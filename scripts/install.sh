#!/usr/bin/env bash
# Primary install of xttp-stack on a clean Ubuntu/Debian host (amd64/arm64).
# Does NOT invent secrets. Does NOT start services — fill configs first.
# Downloads mihomo + xray into /usr/local/bin/ when missing (or XTTP_FORCE_BINARIES=1).
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_ROOT="${XTTP_INSTALL_ROOT:-${XTTP_GIT_REPO:-/opt/xttp-stack}}"
BIN_DIR="${XTTP_BIN_DIR:-/usr/local/bin}"
# Services and native validators intentionally share these fixed binary paths.
# Reject unsupported overrides before packages, files, or services are changed.
if [[ "$BIN_DIR" != "/usr/local/bin" ]]; then
  echo "ERROR: XTTP_BIN_DIR must be /usr/local/bin (service and panel binary paths)." >&2
  exit 1
fi
if [[ "$INSTALL_ROOT" != /* || "$INSTALL_ROOT" == "/" || "$INSTALL_ROOT" == *$'\n'* || "$INSTALL_ROOT" == *$'\r'* ]]; then
  echo "ERROR: XTTP_INSTALL_ROOT must be an absolute source directory, not /." >&2
  exit 1
fi
if [[ -d "$INSTALL_ROOT" ]]; then
  INSTALL_ROOT="$(cd "$INSTALL_ROOT" && pwd -P)"
fi
if [[ "$REPO_ROOT" != "$INSTALL_ROOT" && -d "$INSTALL_ROOT" && ! -e "$INSTALL_ROOT/.git" ]] \
    && [[ -n "$(find "$INSTALL_ROOT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "ERROR: $INSTALL_ROOT is an existing non-Git tree; it was not modified." >&2
  echo "       Run its own installer or choose an empty XTTP_INSTALL_ROOT; preserve local files." >&2
  exit 1
fi
# 0 = install binaries if missing; 1 = always re-download; skip entirely with XTTP_SKIP_BINARIES=1
FORCE_BINARIES="${XTTP_FORCE_BINARIES:-0}"
SKIP_BINARIES="${XTTP_SKIP_BINARIES:-0}"

# Installation can replace native binaries too; serialize it with UI, timer and
# manual updates before touching packages, source, device data or services.
UPDATE_LOCK="${XTTP_UPDATE_LOCK:-/run/xttp-update.lock}"
if [[ -n "${XTTP_UPDATE_LOCK_FD:-}" ]]; then
  UPDATE_LOCK_FD="$XTTP_UPDATE_LOCK_FD"
  if [[ ! "$UPDATE_LOCK_FD" =~ ^[0-9]+$ ]] || [[ ! "/dev/fd/$UPDATE_LOCK_FD" -ef "$UPDATE_LOCK" ]]; then
    echo "ERROR: invalid inherited update lock descriptor." >&2
    exit 1
  fi
else
  mkdir -p "$(dirname "$UPDATE_LOCK")"
  exec {UPDATE_LOCK_FD}>"$UPDATE_LOCK"
fi
flock -n "$UPDATE_LOCK_FD" || { echo "ERROR: another update owns $UPDATE_LOCK." >&2; exit 1; }
export XTTP_UPDATE_LOCK="$UPDATE_LOCK" XTTP_UPDATE_LOCK_FD="$UPDATE_LOCK_FD"

echo "==> xttp-stack install from: $REPO_ROOT"

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
if command -v apt-get >/dev/null 2>&1; then
  if ! apt-get update -qq; then
    echo "    WARNING: apt-get update failed (DNS/proxy/mirror). Continuing if tools exist…"
  fi
  apt-get install -y -qq python3 curl ca-certificates tar gzip unzip git \
    || echo "    WARNING: apt-get install had errors — need: python3 curl ca-certificates gzip unzip git"
fi
for cmd in python3 curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command missing: $cmd"
    exit 1
  fi
done
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ── helpers: download core binaries ──────────────────────────
detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *)
      echo "ERROR: unsupported arch $(uname -m) (need amd64 or arm64)" >&2
      return 1
      ;;
  esac
}

install_xray() {
  local arch="$1"
  local zipname dest tmp
  dest="${BIN_DIR}/xray"
  if [[ -x "$dest" && "$FORCE_BINARIES" != "1" ]]; then
    echo "    xray already present: $dest ($("$dest" version 2>/dev/null | head -1 || echo ok))"
    return 0
  fi
  case "$arch" in
    amd64) zipname="Xray-linux-64.zip" ;;
    arm64) zipname="Xray-linux-arm64-v8a.zip" ;;
  esac
  tmp="$(mktemp -d "$WORK/xray.XXXXXX")"
  echo "    downloading Xray $XRAY_VERSION ($zipname)…"
  curl -fsSL --connect-timeout 20 --retry 3 \
    -o "$tmp/xray.zip" \
    "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${zipname}"
  unzip -qo "$tmp/xray.zip" xray -d "$tmp"
  install -m 755 "$tmp/xray" "$dest"
  rm -rf "$tmp"
  echo "    installed $dest"
  "$dest" version 2>/dev/null | head -2 || true
}

install_mihomo() {
  local arch="$1"
  local dest tmp url
  dest="${BIN_DIR}/mihomo"
  if [[ -x "$dest" && "$FORCE_BINARIES" != "1" ]]; then
    echo "    mihomo already present: $dest ($("$dest" -v 2>/dev/null | head -1 || echo ok))"
    return 0
  fi
  tmp="$(mktemp -d "$WORK/mihomo.XXXXXX")"
  echo "    resolving pinned mihomo $MIHOMO_VERSION asset for $arch…"
  # Prefer compatible amd64 build; plain arm64 otherwise
  url="$(
    curl -fsSL --connect-timeout 20 --retry 3 \
      -H "User-Agent: xttp-stack-install/1.0" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/${MIHOMO_VERSION}" \
    | XTTP_ARCH="$arch" python3 -c '
import json, os, re, sys
arch = os.environ.get("XTTP_ARCH", "amd64")
d = json.load(sys.stdin)
assets = d.get("assets") or []

def pick(pred):
    for a in assets:
        n = a.get("name") or ""
        if pred(n) and n.endswith(".gz"):
            return a.get("browser_download_url") or ""
    return ""

if arch == "amd64":
    url = pick(lambda n: "linux-amd64" in n and "compatible" in n and "go" not in n)
    if not url:
        url = pick(lambda n: bool(re.search(r"mihomo-linux-amd64-v?\d", n)) and "compatible" not in n and "go" not in n and "cgo" not in n)
else:
    url = pick(lambda n: "linux-arm64" in n and "go" not in n and "cgo" not in n)
print(url or "")
'
  )"
  if [[ -z "$url" ]]; then
    echo "ERROR: could not find mihomo release asset for $arch (GitHub API / network)"
    echo "       set XTTP_SKIP_BINARIES=1 and install manually, or retry later"
    rm -rf "$tmp"
    return 1
  fi
  echo "    downloading mihomo…"
  echo "      $url"
  curl -fsSL --connect-timeout 20 --retry 3 -o "$tmp/mihomo.gz" "$url"
  gunzip -c "$tmp/mihomo.gz" > "$tmp/mihomo"
  install -m 755 "$tmp/mihomo" "$dest"
  rm -rf "$tmp"
  echo "    installed $dest"
  "$dest" -v 2>/dev/null | head -2 || true
}

echo "==> persistent source tree: $INSTALL_ROOT"
if [[ "$REPO_ROOT" != "$INSTALL_ROOT" ]]; then
  if [[ -e "$INSTALL_ROOT/.git" ]]; then
    echo "    keeping existing checkout (no reset, copy, or implicit code update)"
  elif [[ -e "$REPO_ROOT/.git" ]]; then
    git clone --no-hardlinks "$REPO_ROOT" "$INSTALL_ROOT"
    # A local clone must retain the upstream, not point fleet updates at a
    # temporary installer checkout that may subsequently be removed.
    if origin="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null)"; then
      git -C "$INSTALL_ROOT" remote set-url origin "$origin"
    else
      git -C "$INSTALL_ROOT" remote remove origin
    fi
  else
    mkdir -p "$INSTALL_ROOT"
    for item in panel groups scripts systemd configs version-manifest.json README.md CHANGELOG.md LICENSE; do
      if [[ -e "$REPO_ROOT/$item" ]]; then
        cp -a "$REPO_ROOT/$item" "$INSTALL_ROOT/"
      fi
    done
    echo "    local-only source (no Git upstream; remote panel updates unavailable)"
  fi
fi
for required in panel/app.py panel/templates/panel.html panel/templates/login.html \
    panel/static/panel.css panel/static/app.js panel/static/rules.js \
    panel/static/monitoring.js panel/static/system.js \
    scripts/install.sh scripts/update.sh scripts/check-and-update.sh version-manifest.json \
    systemd/mihomo-lists.service systemd/xttp-update.service systemd/xttp-update.timer \
    systemd/mihomo.service systemd/xray.service; do
  if [[ ! -f "$INSTALL_ROOT/$required" ]]; then
    echo "ERROR: incomplete source tree: $INSTALL_ROOT/$required" >&2
    exit 1
  fi
done
export XTTP_GIT_REPO="$INSTALL_ROOT"

# The installed package manifest is the single source of native release pins.
# Read both once, reject malformed tags, and never fall back to /latest.
CORE_VERSIONS="$(python3 - "$INSTALL_ROOT/version-manifest.json" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as source:
    manifest = json.load(source)
if not isinstance(manifest, dict):
    raise SystemExit("ERROR: version-manifest.json must contain an object")
targets = []
for component in ("mihomo", "xray"):
    target = manifest.get(component)
    if not isinstance(target, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", target):
        raise SystemExit("ERROR: invalid pinned release tag for " + component)
    targets.append(target)
print("\t".join(targets))
PY
)"
IFS=$'\t' read -r MIHOMO_VERSION XRAY_VERSION <<< "$CORE_VERSIONS"

echo "==> core binaries → $BIN_DIR"
install -d -m 755 "$BIN_DIR"
if [[ "$SKIP_BINARIES" == "1" ]]; then
  echo "    skip binaries (XTTP_SKIP_BINARIES=1)"
  for b in mihomo xray; do
    if [[ ! -x "${BIN_DIR}/$b" ]]; then
      echo "    WARNING: missing ${BIN_DIR}/$b — services will fail with 203/EXEC until installed"
    fi
  done
else
  ARCH="$(detect_arch)"
  echo "    arch=$ARCH"
  install_xray "$ARCH"
  install_mihomo "$ARCH"
fi

echo "==> panel data directory"
install -d -m 755 /etc/mihomo-lists

echo "==> config directories"
install -d -m 755 /etc/mihomo /etc/xray /etc/mihomo/rule-providers/groups

MIHOMO_CFG=/etc/mihomo/config.yaml
XRAY_CFG=/etc/xray/config.json
EXAMPLE_M="$INSTALL_ROOT/configs/examples/mihomo.config.example.yaml"
EXAMPLE_X="$INSTALL_ROOT/configs/examples/xray.config.example.json"
PREPARE_STATE="${MIHOMO_CFG}.xttp-prepare.sha256"

if [[ ! -e "$MIHOMO_CFG" && ! -L "$MIHOMO_CFG" ]]; then
  if [[ -f "$EXAMPLE_M" ]]; then
    # Persist intent before publishing the config so interrupted installs can resume.
    seed_state="$(mktemp "${PREPARE_STATE}.XXXXXX")"
    chmod 600 "$seed_state"
    sha256sum "$EXAMPLE_M" | cut -d' ' -f1 > "$seed_state"
    mv -f "$seed_state" "$PREPARE_STATE"
    install -m 644 "$EXAMPLE_M" "$MIHOMO_CFG"
    echo "    created $MIHOMO_CFG from example"
  fi
else
  echo "    keep existing $MIHOMO_CFG"
fi

if [[ ! -e "$XRAY_CFG" && ! -L "$XRAY_CFG" ]]; then
  if [[ -f "$EXAMPLE_X" ]]; then
    install -m 600 "$EXAMPLE_X" "$XRAY_CFG"
    echo "    created $XRAY_CFG from example"
  fi
else
  echo "    keep existing $XRAY_CFG"
fi

echo "==> device-local groups.json (bundled rules bootstrap only)"
GROUPS_LIVE=/etc/mihomo/rule-providers/groups.json
GROUPS_REPO="$INSTALL_ROOT/groups/groups.json"
if [[ -f "$GROUPS_REPO" ]]; then
  if [[ ! -e "$GROUPS_LIVE" && ! -L "$GROUPS_LIVE" ]]; then
    install -m 644 "$GROUPS_REPO" "$GROUPS_LIVE"
    echo "    installed groups from repo → $GROUPS_LIVE"
  else
    echo "    keep existing $GROUPS_LIVE (not overwritten)"
  fi
fi

# The backend owns seed authorization and the durable publication journal.
# In particular a completed publication is not a user edit just because its
# bytes differ from the original example hash after an interrupted install.
echo "==> prepare initial config if authorized (never starts services)"
python3 "$INSTALL_ROOT/panel/app.py" --prepare-config

echo "==> hostname → /etc/hosts (avoid sudo: unable to resolve host)"
HN="$(hostname -s 2>/dev/null || hostname || true)"
if [[ -n "$HN" && "$HN" != "localhost" ]]; then
  if ! grep -qE "[[:space:]]${HN}([[:space:]]|$)" /etc/hosts 2>/dev/null; then
    printf '\n127.0.0.1\t%s\n::1\t%s\n' "$HN" "$HN" >> /etc/hosts
    echo "    added $HN to /etc/hosts"
  else
    echo "    $HN already in /etc/hosts"
  fi
fi

echo "==> systemd units (local drop-ins are preserved)"
HAD_TIMER=0
if systemctl cat xttp-update.timer >/dev/null 2>&1; then
  HAD_TIMER=1
fi
for unit in mihomo.service xray.service mihomo-lists.service xttp-update.service xttp-update.timer; do
  # Quote the environment assignment for spaces/backslashes and escape systemd
  # specifiers. ExecStart expands ${XTTP_GIT_REPO} as one argument.
  python3 - "$INSTALL_ROOT/systemd/$unit" "$WORK/$unit" "$INSTALL_ROOT" <<'PY'
import pathlib, sys
source, target, repo = sys.argv[1:]
escaped = repo.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
text = pathlib.Path(source).read_text()
lines = ['Environment="XTTP_GIT_REPO=' + escaped + '"' if line.startswith("Environment=XTTP_GIT_REPO=") else line
         for line in text.splitlines()]
pathlib.Path(target).write_text("\n".join(lines) + "\n")
PY
  install -m 644 "$WORK/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
# Only retire the obsolete installed copy, never its parent or an old checkout.
rm -f /opt/mihomo-lists/app.py
systemctl enable mihomo xray mihomo-lists
if [[ "$HAD_TIMER" == "0" ]]; then
  systemctl enable xttp-update.timer
  echo "    new xttp-update.timer enabled, not started"
else
  echo "    existing xttp-update.timer enabled/disabled and running policy unchanged"
fi

# final binary sanity (fail install if still missing — catches 203/EXEC early)
echo "==> verify binaries"
MISSING=0
for b in mihomo xray; do
  if [[ -x "${BIN_DIR}/$b" ]]; then
    echo "    OK ${BIN_DIR}/$b"
  else
    echo "    MISSING ${BIN_DIR}/$b"
    MISSING=1
  fi
done
if [[ "$MISSING" -ne 0 && "$SKIP_BINARIES" != "1" ]]; then
  echo "ERROR: core binaries missing — fix network to GitHub and re-run, or install manually"
  exit 1
fi

echo "=============================================="
echo "  Binaries:  ${BIN_DIR}/mihomo  ${BIN_DIR}/xray"
echo "  Fill secrets BEFORE starting services:"
echo "    $MIHOMO_CFG"
echo "    $XRAY_CFG"
echo "  Or paste vless:// in panel after start (Система → Подключение)."
echo "  Then:  systemctl start mihomo xray mihomo-lists"
echo "  Fleet: systemctl enable --now xttp-update.timer"
echo "  UI:    ssh -N -L 9080:127.0.0.1:9080 <user>@<host>, then http://127.0.0.1:9080"
echo "  Login: admin; initial password: sudo cat /etc/mihomo-lists/bootstrap.password"
echo "         (created on first panel start; change it after login)"
echo "=============================================="
echo "Core/panel services were ENABLED but NOT started; existing timer policy was preserved."
echo "Env: XTTP_SKIP_BINARIES=1  XTTP_FORCE_BINARIES=1  XTTP_INSTALL_ROOT=…"
