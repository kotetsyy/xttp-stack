#!/usr/bin/env bash
# Timer/CLI entrypoint. The durable Python worker owns the one shared OS lock.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi
if [[ "${XTTP_BIN_DIR:-/usr/local/bin}" != "/usr/local/bin" ]]; then
  echo "ERROR: XTTP_BIN_DIR must be /usr/local/bin (service and panel binary paths)." >&2
  exit 1
fi

REPO="${XTTP_GIT_REPO:-$(cd "$(dirname "$0")/.." && pwd -P)}"
APP="$REPO/panel/app.py"
export XTTP_GIT_REPO="$REPO"
export XTTP_UPDATE_LOCK="${XTTP_UPDATE_LOCK:-/run/xttp-update.lock}"
export XTTP_FLEET_INITIATOR="${XTTP_FLEET_INITIATOR:-автообновление (timer)}"
# Never inherit a different process's shell descriptor into a new lock owner.
unset XTTP_UPDATE_LOCK_FD

log() { echo "$(date -Is) xttp-update: $*"; }
MANIFEST=""
STAGE="source validation"
WORKER_STARTED=0
finish() {
  local rc=$?
  trap - EXIT
  if [[ -n "$MANIFEST" ]]; then
    rm -f "$MANIFEST"
  fi
  if [[ "$rc" -ne 0 ]]; then
    log "FAILED stage=$STAGE rc=$rc"
    if [[ "$WORKER_STARTED" == "0" && -f "$APP" ]]; then
      python3 "$APP" --fleet-cmd log-skip --reason "fleet failed: $STAGE (rc=$rc)" || true
    fi
  fi
  exit "$rc"
}
trap finish EXIT

if [[ ! -f "$APP" || ! -d "$REPO" ]]; then
  log "canonical source app missing: $APP"
  exit 1
fi
log "start (initiator=$XTTP_FLEET_INITIATOR, source=$REPO)"
umask 077
MANIFEST="$(mktemp "${TMPDIR:-/tmp}/xttp-version-manifest.XXXXXX")"
STAGE="manifest fetch"
if [[ -e "$REPO/.git" ]]; then
  BRANCH="${XTTP_GITHUB_BRANCH:-main}"
  # Fetching never changes working files. Failed fetch must not silently apply
  # a stale manifest; checkout mutation is fast-forward-only inside the worker.
  git -C "$REPO" fetch origin "$BRANCH"
  git -C "$REPO" show FETCH_HEAD:version-manifest.json > "$MANIFEST"
else
  log "local-only source; remote panel updates unavailable"
  cp "$REPO/version-manifest.json" "$MANIFEST"
fi

STAGE="durable fleet operation"
WORKER_STARTED=1
# run accepts a durable operation and executes the same --operation-run path as
# UI jobs. No shell flock: nested independent descriptors would deadlock.
python3 "$APP" --fleet-cmd run --manifest "$MANIFEST" --repo "$REPO"
log "done (all requested components succeeded; see durable operation result)"
