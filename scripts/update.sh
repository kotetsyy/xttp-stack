#!/usr/bin/env bash
# Update panel code + groups from git; restart panel only.
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
  git pull --ff-only || git pull
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

# Sync groups.json from repo → live path if repo file is newer or live missing
GROUPS_REPO="$REPO_ROOT/groups/groups.json"
GROUPS_LIVE=/etc/mihomo/rule-providers/groups.json
if [[ -f "$GROUPS_REPO" ]]; then
  install -d -m 755 /etc/mihomo/rule-providers/groups
  if [[ ! -f "$GROUPS_LIVE" ]] || [[ "$GROUPS_REPO" -nt "$GROUPS_LIVE" ]]; then
    # only auto-copy if env XTTP_SYNC_GROUPS=1 (default on for git-based fleet)
    if [[ "${XTTP_SYNC_GROUPS:-1}" != "0" ]]; then
      install -m 644 "$GROUPS_REPO" "$GROUPS_LIVE"
      echo "==> groups.json synced → $GROUPS_LIVE"
    fi
  else
    echo "==> groups.json live is newer or same — left unchanged"
  fi
fi

echo "==> restart panel (mihomo/xray not restarted)"
systemctl restart mihomo-lists
sleep 1
systemctl is-active mihomo-lists
curl -sS -o /dev/null -w "panel http=%{http_code}\n" http://127.0.0.1:9080/ || true

if [[ "${XTTP_UPDATE_BINARIES:-0}" == "1" ]]; then
  echo "==> optional binary version check (panel API, if running)"
  # informational only — real upgrade still via UI or separate tooling
  curl -sS "http://127.0.0.1:9080/api/core/versions" 2>/dev/null | head -c 400 || true
  echo
fi

echo "==> done"
echo "Secret configs untouched: /etc/mihomo/config.yaml /etc/xray/config.json"
echo "Binary updates: panel → Settings → Core → version chips"
