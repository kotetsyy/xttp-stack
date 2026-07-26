#!/usr/bin/env bash
# Fleet auto-update: fetch manifest, health-check, update binaries via panel code, panel via update.sh.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root"
  exit 1
fi

REPO="${XTTP_GIT_REPO:-/opt/xttp-stack}"
APP="${XTTP_PANEL_APP:-/opt/mihomo-lists/app.py}"
LOCK="${XTTP_UPDATE_LOCK:-/run/xttp-update.lock}"
INITIATOR="${XTTP_FLEET_INITIATOR:-автообновление (timer)}"
export XTTP_FLEET_INITIATOR="$INITIATOR"

log() { echo "$(date -Is) xttp-update: $*"; }

mkdir -p "$(dirname "$LOCK")" 2>/dev/null || true
exec 9>"$LOCK"
if ! flock -n 9; then
  log "already running — skip"
  exit 0
fi

if [[ ! -f "$APP" ]]; then
  log "panel app missing: $APP"
  exit 1
fi
if [[ ! -d "$REPO" ]]; then
  log "repo missing: $REPO"
  exit 1
fi

log "start (initiator=$INITIATOR)"

if ! python3 "$APP" --fleet-cmd health; then
  log "пропущено: система нездорова, требуется ручная проверка"
  python3 "$APP" --fleet-cmd log-skip --reason "система нездорова, требуется ручная проверка" || true
  exit 0
fi

cd "$REPO"
if [[ -d .git ]]; then
  log "git fetch origin"
  git fetch origin 2>&1 | while IFS= read -r line; do log "git: $line"; done || true
  if ! git show origin/main:version-manifest.json > /tmp/xttp-version-manifest.json 2>/dev/null; then
    if [[ -f "$REPO/version-manifest.json" ]]; then
      cp -f "$REPO/version-manifest.json" /tmp/xttp-version-manifest.json
      log "using local version-manifest.json"
    else
      log "version-manifest.json not found"
      exit 1
    fi
  fi
else
  if [[ -f "$REPO/version-manifest.json" ]]; then
    cp -f "$REPO/version-manifest.json" /tmp/xttp-version-manifest.json
  else
    log "not a git repo and no manifest"
    exit 1
  fi
fi

set +e
python3 "$APP" --fleet-cmd apply \
  --manifest /tmp/xttp-version-manifest.json \
  --repo "$REPO"
rc=$?
set -e

if [[ $rc -eq 10 ]]; then
  log "panel outdated — running update.sh"
  bash "$REPO/scripts/update.sh"
  python3 "$APP" --fleet-cmd log-panel --manifest /tmp/xttp-version-manifest.json || true
  log "panel update finished"
elif [[ $rc -eq 0 ]]; then
  log "done (panel up-to-date or only binaries updated)"
else
  log "fleet apply failed rc=$rc"
  exit "$rc"
fi

log "done"
exit 0
