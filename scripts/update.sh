#!/usr/bin/env bash
# Deploy panel code only; preserve device data, core binaries and timer policy.
set -euo pipefail

validate_package() {
  python3 - "$1" "${2:-}" <<'PY'
import json, pathlib, subprocess, sys
repo, revision = sys.argv[1:]
required = ("panel/app.py", "panel/templates/panel.html", "panel/templates/login.html",
            "panel/static/panel.css", "panel/static/app.js", "panel/static/rules.js",
            "panel/static/monitoring.js", "panel/static/system.js",
            "systemd/mihomo-lists.service", "systemd/xttp-update.service", "systemd/xttp-update.timer",
            "scripts/install.sh", "scripts/update.sh", "scripts/check-and-update.sh",
            "version-manifest.json")
for name in required:
    data = (subprocess.check_output(["git", "-C", repo, "show", revision + ":" + name])
            if revision else pathlib.Path(repo, name).read_bytes())
    if not data.strip():
        raise SystemExit("ERROR: empty package file: " + name)
    if name.endswith(".py"):
        compile(data, name, "exec")
    elif name.endswith(".json"):
        if not isinstance(json.loads(data), dict):
            raise SystemExit("ERROR: invalid version manifest object")
PY
}

panel_ready() {
  python3 - <<'PY'
import ast, os, pathlib, re, subprocess, time, urllib.request
source = pathlib.Path(os.environ["XTTP_GIT_REPO"], "panel/app.py").read_text()
expected = next((node.value.value for node in ast.parse(source).body
                 if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                 and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)), "")
end = time.monotonic() + 15
last = "panel did not become ready"
while time.monotonic() < end:
    try:
        subprocess.run(["systemctl", "is-active", "--quiet", "mihomo-lists"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        result = subprocess.run(["systemctl", "show", "mihomo-lists", "--property=MainPID", "--value"],
                                capture_output=True, text=True, timeout=3)
        pid = result.stdout.strip()
        env = dict(os.environ)
        if pid.isdecimal() and int(pid) > 0:
            raw = pathlib.Path("/proc", pid, "environ").read_bytes()
            env.update(item.decode().split("=", 1) for item in raw.split(b"\0") if b"=" in item)
        host = env.get("XTTP_HOST", "127.0.0.1")
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
        if ":" in host:
            host = "[" + host + "]"
        # /login also exists on the pre-migration embedded frontend, unlike
        # /api/session, so restoration can prove actual legacy readiness.
        base = "http://" + host + ":" + env.get("XTTP_PORT", "9080")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + "/login", timeout=2) as response:
            header = response.headers.get("X-XTTP-Version", "")
            correct = header == expected
            if not header:
                text = response.read(512 * 1024).decode("utf-8", "replace")
                correct = bool(expected and re.search(r"(?<![0-9.])" + re.escape(expected) + r"(?![0-9.])", text))
            if response.status != 200 or not correct:
                raise RuntimeError("unexpected panel status/version")
            if header:
                for name in ("panel.css", "app.js", "rules.js", "monitoring.js", "system.js"):
                    with opener.open(base + "/static/" + name, timeout=2) as asset:
                        if asset.status != 200 or not asset.read(1):
                            raise RuntimeError("required static asset unavailable: " + name)
            break
    except Exception as error:
        last = str(error)
    time.sleep(0.25)
else:
    raise SystemExit("ERROR: panel readiness failed: " + last)
PY
}

finish_update() {
  local rc=$? recovery=0 unit
  trap - EXIT
  set +e
  if [[ "$rc" -ne 0 ]]; then
    echo "ERROR: panel deployment failed (rc=$rc); recovering prior package." >&2
    if [[ "$CODE_CHANGED" == "1" ]]; then
      if [[ "$(git -C "$REPO" rev-parse HEAD)" == "$OLD_COMMIT" ]]; then
        CODE_CHANGED=0
      elif [[ "$(git -C "$REPO" rev-parse HEAD)" == "$NEW_COMMIT" ]] \
          && git -C "$REPO" diff --quiet && git -C "$REPO" diff --cached --quiet; then
        # Never reset a user branch or discard edits. Its new tip is retained;
        # only this clean runtime worktree returns to the known prior commit.
        git -C "$REPO" switch --detach "$OLD_COMMIT" || recovery=1
      else
        echo "ERROR: runtime checkout changed concurrently; preserving edits, recovery failed." >&2
        recovery=1
      fi
    fi
    if [[ "$UNITS_CHANGED" == "1" ]]; then
      for unit in mihomo-lists.service xttp-update.service xttp-update.timer; do
        if [[ -e "$WORK/old-$unit" || -L "$WORK/old-$unit" ]]; then
          cp -a --remove-destination "$WORK/old-$unit" "/etc/systemd/system/$unit" || recovery=1
        else
          rm -f "/etc/systemd/system/$unit" || recovery=1
        fi
      done
      systemctl daemon-reload || recovery=1
      if [[ "${XTTP_NO_RESTART:-0}" != "1" ]]; then
        systemctl restart mihomo-lists && panel_ready || recovery=1
      fi
    fi
    if [[ "$recovery" == "0" && "$CODE_CHANGED" == "0" && "$UNITS_CHANGED" == "0" ]]; then
      echo "==> deployment rejected before publication; prior package unchanged" >&2
    elif [[ "$recovery" == "0" ]]; then
      echo "==> previous complete package restored (original deployment still failed)" >&2
    else
      echo "ERROR: previous package recovery failed; manual repair required, no successful rollback claimed." >&2
      echo "ERROR: private recovery material retained at $WORK" >&2
    fi
  fi
  if [[ "$recovery" == "0" && -n "$WORK" ]]; then
    rm -rf "$WORK"
  fi
  exit "$rc"
}

# Read the complete function before Git replaces this script in its checkout.
main() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    return 1
  fi
  if [[ "${XTTP_BIN_DIR:-/usr/local/bin}" != "/usr/local/bin" ]]; then
    echo "ERROR: XTTP_BIN_DIR must be /usr/local/bin (service and panel binary paths)." >&2
    return 1
  fi
  REPO="${XTTP_GIT_REPO:-$(cd "$(dirname "$0")/.." && pwd -P)}"
  REPO="$(cd "$REPO" && pwd -P)"
  if [[ "$REPO" == *$'\n'* || "$REPO" == *$'\r'* ]]; then
    echo "ERROR: invalid source directory." >&2
    return 1
  fi
  export XTTP_GIT_REPO="$REPO"
  local lock="${XTTP_UPDATE_LOCK:-/run/xttp-update.lock}" fd branch unit
  if [[ -n "${XTTP_UPDATE_LOCK_FD:-}" ]]; then
    fd="$XTTP_UPDATE_LOCK_FD"
    # Backend passes the held open file description through pass_fds. Verify
    # its inode and really flock it; an environment flag never bypasses locking.
    if [[ ! "$fd" =~ ^[0-9]+$ ]] || [[ ! "/dev/fd/$fd" -ef "$lock" ]]; then
      echo "ERROR: invalid inherited update lock descriptor." >&2
      return 1
    fi
  else
    mkdir -p "$(dirname "$lock")"
    exec {fd}>"$lock"
  fi
  flock -n "$fd" || { echo "ERROR: another update owns $lock." >&2; return 1; }

  install -d -m 700 /etc/mihomo-lists/panel-backups
  WORK="$(mktemp -d /etc/mihomo-lists/panel-backups/manual.XXXXXX)"
  OLD_COMMIT="" NEW_COMMIT="" CODE_CHANGED=0 UNITS_CHANGED=0
  trap finish_update EXIT
  for unit in mihomo-lists.service xttp-update.service xttp-update.timer; do
    if [[ -e "/etc/systemd/system/$unit" || -L "/etc/systemd/system/$unit" ]]; then
      cp -a "/etc/systemd/system/$unit" "$WORK/old-$unit"
    fi
  done

  echo "==> code source: $REPO"
  if [[ -e "$REPO/.git" ]]; then
    if [[ "${XTTP_GIT_SYNCED:-0}" == "1" ]]; then
      echo "    Git synchronized and candidate validated by durable worker"
    else
      # Clean tracked bytes make complete, non-destructive recovery possible.
      if ! git -C "$REPO" diff --quiet || ! git -C "$REPO" diff --cached --quiet; then
        echo "ERROR: tracked source edits exist; preserve/reconcile them before updating." >&2
        return 1
      fi
      OLD_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
      printf 'repo=%s\nold_commit=%s\n' "$REPO" "$OLD_COMMIT" > "$WORK/recovery.txt"
      branch="${XTTP_GITHUB_BRANCH:-main}"
      git -C "$REPO" fetch origin "$branch"
      # FETCH_HEAD is mutable even while this update owns the runtime lock:
      # the timer may fetch metadata without changing the checkout.
      NEW_COMMIT="$(git -C "$REPO" rev-parse FETCH_HEAD)"
      validate_package "$REPO" "$NEW_COMMIT"
      # Set intent before the fast-forward: signals/errors during merge still
      # attempt recovery only if HEAD reached this exact candidate commit.
      printf 'candidate_commit=%s\n' "$NEW_COMMIT" >> "$WORK/recovery.txt"
      if [[ "$OLD_COMMIT" != "$NEW_COMMIT" ]]; then
        CODE_CHANGED=1
      fi
      git -C "$REPO" merge --ff-only "$NEW_COMMIT"
      NEW_COMMIT="$(git -C "$REPO" rev-parse HEAD)"
      if [[ "$NEW_COMMIT" == "$OLD_COMMIT" ]]; then
        CODE_CHANGED=0
      fi
    fi
  else
    echo "    LOCAL deployment only: no remote upgrade was performed"
  fi
  # XTTP_GIT_SYNCED is not permission to skip current package validation.
  validate_package "$REPO"

  echo "==> panel/fleet units (drop-ins and timer policy preserved)"
  for unit in mihomo-lists.service xttp-update.service xttp-update.timer; do
    python3 - "$REPO/systemd/$unit" "$WORK/$unit" "$REPO" <<'PY'
import pathlib, sys
source, target, repo = sys.argv[1:]
escaped = repo.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
lines = ['Environment="XTTP_GIT_REPO=' + escaped + '"' if line.startswith("Environment=XTTP_GIT_REPO=") else line
         for line in pathlib.Path(source).read_text().splitlines()]
pathlib.Path(target).write_text("\n".join(lines) + "\n")
PY
    UNITS_CHANGED=1
    install -m 644 "$WORK/$unit" "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
  if [[ -f "$REPO/groups/groups.json" && ! -e /etc/mihomo/rule-providers/groups.json \
      && ! -L /etc/mihomo/rule-providers/groups.json ]]; then
    install -d -m 755 /etc/mihomo/rule-providers/groups
    install -m 644 "$REPO/groups/groups.json" /etc/mihomo/rule-providers/groups.json
  fi
  if [[ "${XTTP_NO_RESTART:-0}" == "1" ]]; then
    echo "==> readiness/recovery delegated to durable worker"
  else
    systemctl restart mihomo-lists
    panel_ready
  fi
  # Keep the legacy entry point until successful activation, so old units can
  # actually recover if a migration fails before the worker takes ownership.
  if [[ "${XTTP_NO_RESTART:-0}" != "1" ]]; then
    rm -f /opt/mihomo-lists/app.py
  fi
  # Activation is committed; cleanup failure must not undo a healthy package.
  trap - EXIT
  rm -rf "$WORK" || echo "WARNING: could not remove private recovery material: $WORK" >&2
  WORK=""
  echo "==> panel deployment complete; device data, cores and timer policy preserved"
}
main "$@"
