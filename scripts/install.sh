#!/usr/bin/env bash
# Primary install of xttp-stack on a clean Ubuntu/Debian host (amd64/arm64).
# Does NOT invent secrets. Does NOT start services — fill configs first.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_ROOT="${XTTP_INSTALL_ROOT:-/opt/xttp-stack}"

echo "==> xttp-stack install from: $REPO_ROOT"

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 curl ca-certificates tar gzip unzip git

echo "==> install tree to $INSTALL_ROOT"
mkdir -p "$INSTALL_ROOT"
# Prefer keeping a git clone at /opt/xttp-stack; if running from elsewhere, rsync/copy
if [[ "$REPO_ROOT" != "$INSTALL_ROOT" ]]; then
  rsync -a --delete \
    --exclude '.git' \
    "$REPO_ROOT"/panel \
    "$REPO_ROOT"/groups \
    "$REPO_ROOT"/scripts \
    "$REPO_ROOT"/systemd \
    "$REPO_ROOT"/configs \
    "$REPO_ROOT"/README.md \
    "$REPO_ROOT"/LICENSE \
    "$INSTALL_ROOT"/ 2>/dev/null || {
    mkdir -p "$INSTALL_ROOT"/{panel,groups,scripts,systemd,configs/examples}
    cp -a "$REPO_ROOT/panel/." "$INSTALL_ROOT/panel/"
    cp -a "$REPO_ROOT/groups/." "$INSTALL_ROOT/groups/"
    cp -a "$REPO_ROOT/scripts/." "$INSTALL_ROOT/scripts/"
    cp -a "$REPO_ROOT/systemd/." "$INSTALL_ROOT/systemd/"
    cp -a "$REPO_ROOT/configs/." "$INSTALL_ROOT/configs/"
    cp -a "$REPO_ROOT/README.md" "$INSTALL_ROOT/" 2>/dev/null || true
    cp -a "$REPO_ROOT/LICENSE" "$INSTALL_ROOT/" 2>/dev/null || true
  }
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

echo "=============================================="
echo "  Fill secrets BEFORE starting services:"
echo "    $MIHOMO_CFG"
echo "    $XRAY_CFG"
echo "  Put mihomo/xray binaries in /usr/local/bin/ if missing."
echo "  Then:  systemctl start mihomo xray mihomo-lists"
echo "  UI:    http://<host>:9080  (admin / changeme — change it)"
echo "=============================================="
echo "Services were ENABLED but NOT started."
