#!/usr/bin/env bash
# Update panel code from git; keep device-local rules and restart panel only.
# Never overwrites secret configs under /etc/mihomo/config.yaml or /etc/xray/config.json.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> git pull ($REPO_ROOT)"
if [[ -d .git ]]; then
  # Skip if caller already synced (panel UI update_component_panel)
  if [[ "${XTTP_GIT_SYNCED:-0}" == "1" ]]; then
    echo "    skip git (XTTP_GIT_SYNCED=1)"
  else
    BRANCH="${XTTP_GITHUB_BRANCH:-main}"
    if git remote get-url origin >/dev/null 2>&1; then
      if ! git fetch origin "$BRANCH"; then
        echo "ERROR: git fetch failed; no files installed. Fix remote/network access and retry." >&2
        exit 1
      fi
      # Never discard local commits or tracked edits, including old panel rule commits.
      if ! git merge --ff-only FETCH_HEAD; then
        echo "ERROR: cannot fast-forward code; no files installed. Preserve and reconcile local commits/edits (including old groups commits), then retry." >&2
        exit 1
      fi
      git branch --set-upstream-to="origin/$BRANCH" HEAD 2>/dev/null || true
    elif ! git pull --ff-only; then
      echo "ERROR: git sync failed; no files installed. Fix the upstream/network or reconcile local commits/edits, then retry." >&2
      exit 1
    fi
  fi
else
  echo "    WARNING: not a git checkout — only local files will be installed"
fi

echo "==> panel"
install -d -m 755 /opt/mihomo-lists
install -m 755 "$REPO_ROOT/panel/app.py" /opt/mihomo-lists/app.py

echo "==> systemd units"
install -m 644 "$REPO_ROOT/systemd/mihomo.service" /etc/systemd/system/mihomo.service
install -m 644 "$REPO_ROOT/systemd/xray.service" /etc/systemd/system/xray.service
install -m 644 "$REPO_ROOT/systemd/mihomo-lists.service" /etc/systemd/system/mihomo-lists.service
sed -i "s|^Environment=XTTP_GIT_REPO=.*|Environment=XTTP_GIT_REPO=$REPO_ROOT|" \
  /etc/systemd/system/mihomo-lists.service 2>/dev/null || true
install -m 644 "$REPO_ROOT/systemd/xttp-update.service" /etc/systemd/system/xttp-update.service 2>/dev/null || true
install -m 644 "$REPO_ROOT/systemd/xttp-update.timer" /etc/systemd/system/xttp-update.timer 2>/dev/null || true
sed -i "s|Environment=XTTP_GIT_REPO=.*|Environment=XTTP_GIT_REPO=$REPO_ROOT|" /etc/systemd/system/xttp-update.service 2>/dev/null || true
systemctl daemon-reload

# Bundled rules only bootstrap a missing live file; device rules are always local.
GROUPS_REPO="$REPO_ROOT/groups/groups.json"
GROUPS_LIVE=/etc/mihomo/rule-providers/groups.json
if [[ -f "$GROUPS_REPO" ]]; then
  install -d -m 755 /etc/mihomo/rule-providers/groups
  if [[ ! -e "$GROUPS_LIVE" && ! -L "$GROUPS_LIVE" ]]; then
    install -m 644 "$GROUPS_REPO" "$GROUPS_LIVE"
    echo "==> groups.json bootstrapped → $GROUPS_LIVE"
  else
    echo "==> groups.json is device-local — left unchanged"
  fi
fi

if [[ "${XTTP_NO_RESTART:-0}" == "1" ]]; then
  echo "==> skip restart (XTTP_NO_RESTART=1) — caller will restart"
else
  echo "==> restart panel (mihomo/xray not restarted)"
  systemctl restart mihomo-lists
  sleep 1
  systemctl is-active mihomo-lists
  curl -sS -o /dev/null -w "panel http=%{http_code}\n" http://127.0.0.1:9080/ || true
fi

if [[ "${XTTP_UPDATE_BINARIES:-0}" == "1" ]]; then
  echo "==> optional binary version check (panel API, if running)"
  # informational only — real upgrade still via UI or separate tooling
  curl -sS "http://127.0.0.1:9080/api/core/versions" 2>/dev/null | head -c 400 || true
  echo
fi

echo "==> done"
echo "Secret configs untouched: /etc/mihomo/config.yaml /etc/xray/config.json"
echo "Binary updates: panel → Settings → Core → version chips"
