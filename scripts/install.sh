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
INSTALL_ROOT="${XTTP_INSTALL_ROOT:-/opt/xttp-stack}"
BIN_DIR="${XTTP_BIN_DIR:-/usr/local/bin}"
# 0 = install binaries if missing; 1 = always re-download; skip entirely with XTTP_SKIP_BINARIES=1
FORCE_BINARIES="${XTTP_FORCE_BINARIES:-0}"
SKIP_BINARIES="${XTTP_SKIP_BINARIES:-0}"

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
  tmp="$(mktemp -d)"
  echo "    downloading Xray ($zipname)…"
  curl -fsSL --connect-timeout 20 --retry 3 \
    -o "$tmp/xray.zip" \
    "https://github.com/XTLS/Xray-core/releases/latest/download/${zipname}"
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
  tmp="$(mktemp -d)"
  echo "    resolving latest mihomo asset for $arch…"
  # Prefer compatible amd64 build; plain arm64 otherwise
  url="$(
    curl -fsSL --connect-timeout 20 --retry 3 \
      -H "User-Agent: xttp-stack-install/1.0" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest" \
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
        url = pick(lambda n: bool(re.search(r"mihomo-linux-amd64-v?\\d", n)) and "compatible" not in n and "go" not in n and "cgo" not in n)
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

echo "==> install tree to $INSTALL_ROOT"
mkdir -p "$INSTALL_ROOT"
# Prefer keeping a git clone at /opt/xttp-stack; if running from elsewhere, rsync/copy
if [[ "$REPO_ROOT" != "$INSTALL_ROOT" ]]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git' \
      "$REPO_ROOT"/panel \
      "$REPO_ROOT"/groups \
      "$REPO_ROOT"/scripts \
      "$REPO_ROOT"/systemd \
      "$REPO_ROOT"/configs \
      "$REPO_ROOT"/README.md \
      "$REPO_ROOT"/LICENSE \
      "$INSTALL_ROOT"/ 2>/dev/null || true
  fi
  if [[ ! -f "$INSTALL_ROOT/panel/app.py" ]]; then
    mkdir -p "$INSTALL_ROOT"/{panel,groups,scripts,systemd,configs/examples}
    cp -a "$REPO_ROOT/panel/." "$INSTALL_ROOT/panel/"
    cp -a "$REPO_ROOT/groups/." "$INSTALL_ROOT/groups/"
    cp -a "$REPO_ROOT/scripts/." "$INSTALL_ROOT/scripts/"
    cp -a "$REPO_ROOT/systemd/." "$INSTALL_ROOT/systemd/"
    cp -a "$REPO_ROOT/configs/." "$INSTALL_ROOT/configs/"
    cp -a "$REPO_ROOT/README.md" "$INSTALL_ROOT/" 2>/dev/null || true
    cp -a "$REPO_ROOT/LICENSE" "$INSTALL_ROOT/" 2>/dev/null || true
  fi
  # fleet / changelog files
  for f in version-manifest.json CHANGELOG.md; do
    if [[ -f "$REPO_ROOT/$f" ]]; then
      cp -a "$REPO_ROOT/$f" "$INSTALL_ROOT/$f"
    fi
  done
else
  # already at INSTALL_ROOT (git clone)
  :
fi

# If this is already the git clone, INSTALL_ROOT == REPO_ROOT
if [[ -d "$REPO_ROOT/.git" && "$REPO_ROOT" != "$INSTALL_ROOT" ]]; then
  echo "    tip: for git-based updates, clone the repo to $INSTALL_ROOT and re-run"
fi

echo "==> panel binary path"
install -d -m 755 /opt/mihomo-lists
install -m 755 "$INSTALL_ROOT/panel/app.py" /opt/mihomo-lists/app.py
install -d -m 755 /etc/mihomo-lists

echo "==> config directories"
install -d -m 755 /etc/mihomo /etc/xray /etc/mihomo/rule-providers/groups

MIHOMO_CFG=/etc/mihomo/config.yaml
XRAY_CFG=/etc/xray/config.json
EXAMPLE_M="$INSTALL_ROOT/configs/examples/mihomo.config.example.yaml"
EXAMPLE_X="$INSTALL_ROOT/configs/examples/xray.config.example.json"

if [[ ! -f "$MIHOMO_CFG" ]]; then
  if [[ -f "$EXAMPLE_M" ]]; then
    install -m 644 "$EXAMPLE_M" "$MIHOMO_CFG"
    echo "    created $MIHOMO_CFG from example"
  fi
else
  echo "    keep existing $MIHOMO_CFG"
fi

if [[ ! -f "$XRAY_CFG" ]]; then
  if [[ -f "$EXAMPLE_X" ]]; then
    install -m 600 "$EXAMPLE_X" "$XRAY_CFG"
    echo "    created $XRAY_CFG from example"
  fi
else
  echo "    keep existing $XRAY_CFG"
fi

echo "==> groups.json"
GROUPS_LIVE=/etc/mihomo/rule-providers/groups.json
GROUPS_REPO="$INSTALL_ROOT/groups/groups.json"
if [[ -f "$GROUPS_REPO" ]]; then
  if [[ ! -f "$GROUPS_LIVE" ]]; then
    install -m 644 "$GROUPS_REPO" "$GROUPS_LIVE"
    echo "    installed groups from repo → $GROUPS_LIVE"
  else
    echo "    keep existing $GROUPS_LIVE (not overwritten)"
  fi
fi

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

echo "==> systemd units"
install -m 644 "$INSTALL_ROOT/systemd/mihomo.service" /etc/systemd/system/mihomo.service
install -m 644 "$INSTALL_ROOT/systemd/xray.service" /etc/systemd/system/xray.service
install -m 644 "$INSTALL_ROOT/systemd/mihomo-lists.service" /etc/systemd/system/mihomo-lists.service
# point git repo for optional commits
sed -i "s|^Environment=XTTP_GIT_REPO=.*|Environment=XTTP_GIT_REPO=$INSTALL_ROOT|" \
  /etc/systemd/system/mihomo-lists.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable mihomo xray mihomo-lists

echo

echo "==> fleet auto-update timer (enabled, not started)"
install -m 644 "$INSTALL_ROOT/systemd/xttp-update.service" /etc/systemd/system/xttp-update.service
install -m 644 "$INSTALL_ROOT/systemd/xttp-update.timer" /etc/systemd/system/xttp-update.timer
# point repo path in service
sed -i "s|Environment=XTTP_GIT_REPO=.*|Environment=XTTP_GIT_REPO=$INSTALL_ROOT|" \
  /etc/systemd/system/xttp-update.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable xttp-update.timer
# do not start timer until first manual run — same spirit as not auto-starting mihomo
echo "    enabled xttp-update.timer (start later: systemctl start xttp-update.timer)"

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
echo "  Or paste vless:// in panel after start (Settings → Connection)."
echo "  Then:  systemctl start mihomo xray mihomo-lists"
echo "  Fleet: systemctl enable --now xttp-update.timer"
echo "  UI:    http://<host>:9080  (admin / changeme — change it)"
echo "=============================================="
echo "Services were ENABLED but NOT started."
echo "Env: XTTP_SKIP_BINARIES=1  XTTP_FORCE_BINARIES=1  XTTP_INSTALL_ROOT=…"
