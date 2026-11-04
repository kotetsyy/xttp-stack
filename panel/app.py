#!/usr/bin/env python3
"""xttp panel — groups routing + users + xttp connection settings"""
from __future__ import annotations

import hashlib
import ast
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import sys
import shutil
import threading
import copy
import socket
import subprocess
import time
import tempfile
import errno
import urllib.parse
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = os.environ.get("XTTP_HOST", "127.0.0.1").strip() or "127.0.0.1", 9080
GROUPS_FILE = "/etc/mihomo/rule-providers/groups.json"


# Code checkout only; device-local groups are never committed or synchronized.
XTTP_GIT_REPO = os.environ.get("XTTP_GIT_REPO", "/opt/xttp-stack").rstrip("/")
XTTP_GITHUB_REPO = os.environ.get("XTTP_GITHUB_REPO", "kotetsyy/xttp-stack").strip()
XTTP_GITHUB_BRANCH = os.environ.get("XTTP_GITHUB_BRANCH", "main").strip() or "main"


REMOTES_FILE = "/etc/mihomo/rule-providers/remotes.json"
CONFIG_FILE = "/etc/mihomo/config.yaml"
GROUPS_DIR = "/etc/mihomo/rule-providers/groups"
GROUPS_BACKUP_DIR = "/etc/mihomo-lists/groups-backups"
GROUPS_YAML_QUARANTINE = "/etc/mihomo-lists/groups-yaml-quarantine"
GROUPS_BACKUP_MAX = 40
GROUPS_YAML_QUARANTINE_MAX = 80
_groups_lock = threading.RLock()
DATA_DIR = "/etc/mihomo-lists"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
BOOTSTRAP_PASSWORD_FILE = os.path.join(DATA_DIR, "bootstrap.password")
_sessions_lock = threading.RLock()
_users_lock = threading.RLock()
SESSION_COOKIE_SECURE = os.environ.get("XTTP_COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
PREFS_FILE = os.path.join(DATA_DIR, "prefs.json")
TRAFFIC_HISTORY_FILE = os.path.join(DATA_DIR, "traffic_history.json")
TRAFFIC_HISTORY_MAX = 3700  # ~1h @ 1 sample/sec
TRAFFIC_HISTORY_SAVE_EVERY = 30  # persist to disk every N samples
XRAY_CONFIGS = ["/etc/xray/config.json", "/usr/local/etc/xray/config.json"]
XRAY_BACKUP_DIR = os.path.join(DATA_DIR, "xray-backups")
SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 11090
BOOTSTRAP_USER = "admin"
RESTART_CMD = ["systemctl", "restart", "mihomo"]
RESTART_XRAY_CMD = ["systemctl", "restart", "xray"]
ACTIVITY_MAX = 100
VERSION = "0.19.0"
MIHOMO_API = "http://127.0.0.1:9090"


SESSION_COOKIE = "lists_session"
SESSION_TTL = 7 * 24 * 3600  # 7 days

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]+(?:\.(?!-)[a-z0-9-]+)+\.?$",
    re.I,
)



def esc(s):
    return html.escape(str(s))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProcessLock:
    """Reentrant within a thread; a real advisory lock across processes."""
    def __init__(self, path):
        self.path = path
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.fd = None

    def acquire(self, blocking=True):
        if not self.thread_lock.acquire(blocking=blocking):
            return False
        if self.depth:
            self.depth += 1
            return True
        try:
            path = self.path() if callable(self.path) else self.path
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"\0")
                while True:
                    try:
                        os.lseek(self.fd, 0, os.SEEK_SET)
                        msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if not blocking:
                            raise
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            self.depth = 1
            return True
        except OSError as ex:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.thread_lock.release()
            if ex.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise

    def release(self):
        self.depth -= 1
        if not self.depth:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(self.fd, 0, os.SEEK_SET)
                    msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        self.thread_lock.release()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("lock unavailable")
        return self

    def __exit__(self, *args):
        self.release()


def update_lock_path():
    default = os.path.join(tempfile.gettempdir(), "xttp-update.lock") if os.name == "nt" else "/run/xttp-update.lock"
    return os.environ.get("XTTP_UPDATE_LOCK", default)


def sync_directory(directory: str) -> None:
    if os.name != "nt":
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_bytes(path: str, data: bytes, mode: int = 0o600) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=directory)
    try:
        os.chmod(tmp, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        sync_directory(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path: str, data) -> None:
    atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


_activity_lock = ProcessLock(lambda: ACTIVITY_FILE + ".lock")
_config_lock = ProcessLock(lambda: os.path.join(DATA_DIR, "config.lock"))
_xray_lock = ProcessLock(lambda: os.path.join(DATA_DIR, "xray.lock"))
_operation_lock = ProcessLock(lambda: os.path.join(DATA_DIR, "operations.lock"))


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(XRAY_BACKUP_DIR, exist_ok=True)
    os.chmod(XRAY_BACKUP_DIR, 0o700)
    os.makedirs(GROUPS_BACKUP_DIR, exist_ok=True)
    os.makedirs(GROUPS_YAML_QUARANTINE, exist_ok=True)


def harden_xray_credentials() -> None:
    with _xray_lock:
        ensure_data_dir()
        # Existing 0.18.x files need the same migration as future atomic writes.
        for path in XRAY_CONFIGS:
            if os.path.isfile(path) and not os.path.islink(path):
                os.chmod(path, 0o600)
        for entry in os.scandir(XRAY_BACKUP_DIR):
            if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False):
                os.chmod(entry.path, 0o600)


def load_prefs() -> dict:
    ensure_data_dir()
    defaults = {"auto_ping": False, "auto_ping_sec": 45}
    if not os.path.exists(PREFS_FILE):
        return dict(defaults)
    try:
        d = json.load(open(PREFS_FILE, encoding="utf-8"))
        if not isinstance(d, dict):
            return dict(defaults)
        out = dict(defaults)
        out.update(d)
        return out
    except Exception:
        return dict(defaults)


def save_prefs(prefs: dict) -> None:
    ensure_data_dir()
    atomic_json(PREFS_FILE, prefs)


def load_activity() -> list[dict]:
    ensure_data_dir()
    if not os.path.exists(ACTIVITY_FILE):
        return []
    try:
        with open(ACTIVITY_FILE, encoding="utf-8") as source:
            d = json.load(source)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def safe_diagnostic(value):
    if isinstance(value, str):
        value = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", value)
        return re.sub(r"vless://[^\s]+", "vless://[redacted]", value)
    if isinstance(value, dict):
        return {key: safe_diagnostic(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_diagnostic(item) for item in value]
    return value


def log_activity(user: str, action: str, detail: str = "", ok: bool = True, extra: dict | None = None) -> None:
    """Best-effort journal: logging must never undo a committed action's result."""
    try:
        with _activity_lock:
            items = load_activity()
            entry = {
                "id": uuid.uuid4().hex,
                "ts": utc_now(), "user": user or "system", "action": action,
                "detail": safe_diagnostic(detail or "")[:400], "ok": bool(ok),
            }
            if extra:
                entry["extra"] = safe_diagnostic(extra)
            atomic_json(ACTIVITY_FILE, [entry] + items[:ACTIVITY_MAX - 1])
    except Exception as ex:
        print(f"activity journal: {ex}", file=sys.stderr)



def mihomo_get(path: str, timeout: float = 4.0):
    """GET JSON from mihomo external-controller."""
    req = urllib.request.Request(MIHOMO_API + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def mihomo_patch(path: str, data: dict, timeout: float = 6.0) -> dict:
    """PATCH JSON to mihomo external-controller (runtime configs)."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        MIHOMO_API + path,
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_b = resp.read()
            if not raw_b:
                return {"ok": True}
            try:
                d = json.loads(raw_b.decode("utf-8", errors="replace"))
                if isinstance(d, dict):
                    d.setdefault("ok", True)
                    return d
                return {"ok": True, "data": d}
            except Exception:
                return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


_CORE_KEYS = (
    "mode",
    "log-level",
    "allow-lan",
    "ipv6",
    "unified-delay",
    "tcp-concurrent",
    "find-process-mode",
)



def mihomo_post(path: str, data: dict | None = None, timeout: float = 60.0) -> dict:
    """POST to mihomo external-controller (e.g. /configs/geo force update)."""
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        MIHOMO_API + path,
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_b = resp.read()
            if not raw_b:
                return {"ok": True, "status": getattr(resp, "status", 204)}
            try:
                d = json.loads(raw_b.decode("utf-8", errors="replace"))
                if isinstance(d, dict):
                    d.setdefault("ok", True)
                    return d
                return {"ok": True, "data": d}
            except Exception:
                return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def core_geo_update_now() -> dict:
    """Force geoip/geosite refresh via mihomo POST /configs/geo."""
    r = mihomo_post("/configs/geo", None, timeout=120.0)
    ok = r.get("ok") is not False
    try:
        log_activity(
            "system",
            "core",
            "geo: обновить сейчас",
            ok=ok,
        )
    except Exception:
        pass
    if not ok:
        return r
    return {"ok": True, "result": r}


_VERSIONS_CACHE: dict | None = None
_VERSIONS_CACHE_TS: float = 0.0
_VERSIONS_CACHE_TTL = 480.0  # 8 minutes
_versions_lock = threading.Lock()
XRAY_BIN = "/usr/local/bin/xray"
MIHOMO_BIN = "/usr/local/bin/mihomo"


def _parse_version_token(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    if s.lower().startswith("v") and len(s) > 1 and s[1].isdigit():
        s = s[1:]
    return s


def _version_mihomo() -> dict:
    try:
        d = mihomo_get("/version", timeout=3.0)
        ver = ""
        if isinstance(d, dict):
            ver = str(d.get("version") or d.get("Version") or "").strip()
        if not ver:
            try:
                cfg = mihomo_get("/configs", timeout=3.0)
                ua = str((cfg or {}).get("global-ua") or "")
                m = re.search(r"(?:clash\.meta|mihomo)/v?([\d.]+)", ua, re.I)
                if m:
                    ver = m.group(1)
            except Exception:
                pass
        ver = _parse_version_token(ver)
        if not ver:
            return {"ok": False, "label": "mihomo", "version": None, "display": "mihomo н/д"}
        return {
            "ok": True,
            "label": "mihomo",
            "version": ver,
            "display": f"mihomo v{ver}",
            "meta": bool(isinstance(d, dict) and d.get("meta")),
        }
    except Exception as ex:
        return {
            "ok": False,
            "label": "mihomo",
            "version": None,
            "display": "mihomo н/д",
            "error": str(ex)[:120],
        }


def _version_xray() -> dict:
    """Fixed binary path only — same safety model as mihomo -t / systemctl."""
    bin_path = XRAY_BIN if os.path.isfile(XRAY_BIN) else "xray"
    try:
        r = subprocess.run(
            [bin_path, "version"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        m = re.search(r"Xray\s+([vV]?[\d.]+\w*)", out)
        if not m:
            m = re.search(r"version[:\s]+([vV]?[\d.]+)", out, re.I)
        ver = _parse_version_token(m.group(1) if m else "")
        if not ver:
            return {"ok": False, "label": "xray", "version": None, "display": "xray н/д"}
        return {"ok": True, "label": "xray", "version": ver, "display": f"xray v{ver}"}
    except Exception as ex:
        return {
            "ok": False,
            "label": "xray",
            "version": None,
            "display": "xray н/д",
            "error": str(ex)[:120],
        }


def _version_os() -> dict:
    kernel = ""
    distro = ""
    try:
        r = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=2)
        kernel = (r.stdout or "").strip()
    except Exception:
        pass
    try:
        text = open("/etc/os-release", encoding="utf-8").read()
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                distro = line.split("=", 1)[1].strip().strip('"')
                break
    except Exception:
        pass
    parts = []
    if distro:
        parts.append(distro)
    if kernel:
        parts.append(kernel)
    if not parts:
        return {"ok": False, "label": "os", "version": None, "display": "os н/д"}
    return {
        "ok": True,
        "label": "os",
        "version": kernel or distro,
        "display": " · ".join(parts),
        "kernel": kernel,
        "distro": distro,
    }


def components_versions(*, force: bool = False) -> dict:
    """Cached component versions for the updates page (TTL ~8 min)."""
    global _VERSIONS_CACHE, _VERSIONS_CACHE_TS
    now = time.time()
    with _versions_lock:
        if (
            not force
            and _VERSIONS_CACHE is not None
            and (now - _VERSIONS_CACHE_TS) < _VERSIONS_CACHE_TTL
        ):
            out = dict(_VERSIONS_CACHE)
            out["cached"] = True
            out["age_sec"] = int(now - _VERSIONS_CACHE_TS)
            return out

    mihomo = _version_mihomo()
    xray = _version_xray()
    panel = {
        "ok": True,
        "label": "panel",
        "version": VERSION,
        "display": f"проверка обновления панели · v{VERSION}",
    }
    payload = {
        "ok": True,
        "items": [mihomo, xray, panel],
        "mihomo": mihomo,
        "xray": xray,
        "panel": panel,
        "cached": False,
        "ttl_sec": int(_VERSIONS_CACHE_TTL),
        "ts": int(now),
    }
    with _versions_lock:
        _VERSIONS_CACHE = dict(payload)
        _VERSIONS_CACHE_TS = now
    return payload



# ─── version check / binary update (mihomo, xray) ─────────────
BIN_BACKUP_DIR = os.path.join(DATA_DIR, "bin-backups")
BIN_BACKUP_MAX = 8
_UPDATE_CHECK_CACHE: dict = {}
_UPDATE_CHECK_TTL = 2700.0  # 45 minutes
_update_lock = ProcessLock(update_lock_path)
MIHOMO_BIN = "/usr/local/bin/mihomo"
GITHUB_UA = f"xttp-panel-updater/{VERSION}"

# ─── changelog (GitHub CHANGELOG.md) ─────────────────────────
_CHANGELOG_CACHE: dict | None = None
_CHANGELOG_CACHE_TS: float = 0.0
_CHANGELOG_CACHE_TTL = 300.0  # 5 min


def parse_changelog_md(text: str, current: str | None = None) -> list:
    """Parse Keep-a-Changelog-ish markdown into UI items (newest first)."""
    current = _parse_version_token(current or VERSION)
    items = []
    parts = re.split(r"(?m)^##\s+\[", text or "")
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        hm = re.match(
            r"v?([\d.]+)\]\s*(?:[-–—]\s*(\d{4}-\d{2}-\d{2}))?",
            head.strip(),
        )
        if not hm:
            continue
        ver = hm.group(1)
        date = hm.group(2) or ""
        sections = []
        sec_title = ""
        sec_items = []
        is_fix = False

        def flush():
            nonlocal sec_title, sec_items, is_fix
            if sec_title and sec_items:
                sections.append(
                    {"title": sec_title, "fix": is_fix, "items": list(sec_items)}
                )
            sec_title = ""
            sec_items = []
            is_fix = False

        for ln in body.splitlines():
            s = ln.rstrip()
            if s.startswith("### "):
                flush()
                sec_title = s[4:].strip()
                low = sec_title.lower()
                is_fix = ("fix" in low) or ("bug" in low)
                continue
            if s.startswith("## "):
                break
            mli = re.match(r"^[-*]\s+(.+)$", s.strip())
            if mli and sec_title:
                item = mli.group(1).strip()
                item = re.sub(r"\*\*(.+?)\*\*", r"\1", item)
                item = re.sub(r"`([^`]+)`", r"\1", item)
                sec_items.append(item)
        flush()
        items.append(
            {
                "version": ver,
                "display": f"v{ver}",
                "date": date or ver,
                "current": _parse_version_token(ver) == current,
                "sections": sections,
            }
        )
    seen = False
    for it in items:
        if it["current"] and not seen:
            seen = True
        elif it["current"] and seen:
            it["current"] = False
    return items


def _load_changelog_text():
    """Return (markdown, source). Prefer GitHub, fallback local repo file."""
    repo = (XTTP_GITHUB_REPO or "kotetsyy/xttp-stack").strip()
    branch = (XTTP_GITHUB_BRANCH or "main").strip() or "main"
    bust = str(int(time.time() // 60))

    try:
        api_url = (
            f"https://api.github.com/repos/{repo}/contents/CHANGELOG.md"
            f"?ref={urllib.parse.quote(branch)}"
        )
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": GITHUB_UA,
                "Accept": "application/vnd.github.raw+json",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return resp.read().decode("utf-8", errors="replace"), "github-api"
    except Exception:
        pass

    try:
        raw_url = (
            f"https://raw.githubusercontent.com/{repo}/{branch}/CHANGELOG.md?t={bust}"
        )
        req = urllib.request.Request(
            raw_url,
            headers={"User-Agent": GITHUB_UA, "Cache-Control": "no-cache"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return resp.read().decode("utf-8", errors="replace"), "github-raw"
    except Exception:
        pass

    for base in (
        os.environ.get("XTTP_GIT_REPO", XTTP_GIT_REPO),
        "/opt/xttp-stack",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ):
        if not base:
            continue
        path = os.path.join(str(base), "CHANGELOG.md")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read(), f"local:{path}"
    raise RuntimeError("CHANGELOG.md not found on GitHub or local repo")


def changelog_snapshot(*, force: bool = False) -> dict:
    global _CHANGELOG_CACHE, _CHANGELOG_CACHE_TS
    now = time.time()
    if (
        not force
        and _CHANGELOG_CACHE is not None
        and (now - _CHANGELOG_CACHE_TS) < _CHANGELOG_CACHE_TTL
    ):
        out = dict(_CHANGELOG_CACHE)
        out["cached"] = True
        out["age_sec"] = int(now - _CHANGELOG_CACHE_TS)
        return out
    try:
        text, source = _load_changelog_text()
        items = parse_changelog_md(text, current=VERSION)
        payload = {
            "ok": True,
            "current": VERSION,
            "items": items,
            "source": source,
            "cached": False,
            "ttl_sec": int(_CHANGELOG_CACHE_TTL),
            "html_url": (
                f"https://github.com/{XTTP_GITHUB_REPO}/blob/"
                f"{XTTP_GITHUB_BRANCH}/CHANGELOG.md"
            ),
        }
    except Exception as ex:
        payload = {
            "ok": False,
            "current": VERSION,
            "items": [
                {
                    "version": VERSION,
                    "display": f"v{VERSION}",
                    "date": VERSION,
                    "current": True,
                    "sections": [
                        {
                            "title": "Info",
                            "fix": False,
                            "items": [
                                f"Не удалось загрузить CHANGELOG.md: {str(ex)[:160]}",
                                "Файл должен лежать в корне репозитория на GitHub.",
                            ],
                        }
                    ],
                }
            ],
            "source": "error",
            "error": str(ex)[:200],
            "cached": False,
        }
    with _versions_lock:
        _CHANGELOG_CACHE = dict(payload)
        _CHANGELOG_CACHE_TS = now
    return payload




def _semver_key(v: str) -> tuple:
    v = (v or "").strip()
    if v.lower().startswith("v"):
        v = v[1:]
    parts = re.split(r"[^0-9]+", v)
    nums = []
    for p in parts:
        if p.isdigit():
            nums.append(int(p))
        if len(nums) >= 4:
            break
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _is_remote_newer(remote: str, local: str) -> bool:
    if not remote or not local:
        return False
    return _semver_key(remote) > _semver_key(local)


def _http_json(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": GITHUB_UA,
            "Accept": "application/vnd.github+json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_download(url: str, dest: str, timeout: float = 180.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": GITHUB_UA}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    with open(dest, "wb") as f:
        f.write(data)


def _github_latest(repo: str, target_version: str | None = None) -> dict:
    """Read latest, or exactly the requested version's release tag."""
    tag_wanted = None
    endpoint = "latest"
    if target_version is not None:
        version = _parse_version_token(str(target_version))
        if not version:
            raise ValueError("empty target version")
        tag_wanted = f"v{version}"
        endpoint = "tags/" + urllib.parse.quote(tag_wanted, safe="")
    d = _http_json(f"https://api.github.com/repos/{repo}/releases/{endpoint}", timeout=25.0)
    tag = str(d.get("tag_name") or "").strip()
    if not tag or (tag_wanted is not None and tag != tag_wanted):
        raise ValueError(f"release tag mismatch: wanted {tag_wanted or 'latest'}, got {tag or '(missing)'}")
    body = str(d.get("body") or "")
    # first non-empty lines of notes
    lines = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("<!--"):
            continue
        # strip markdown headers lightly
        s = re.sub(r"^#+\s*", "", s)
        lines.append(s)
        if len(lines) >= 3:
            break
    notes = " ".join(lines)[:400]
    assets = d.get("assets") or []
    return {
        "tag": tag,
        "version": _parse_version_token(tag),
        "html_url": str(d.get("html_url") or f"https://github.com/{repo}/releases/{endpoint}"),
        "notes": notes,
        "assets": assets,
        "name": str(d.get("name") or tag),
    }


def _binary_arch() -> str:
    import platform

    machine = platform.machine().strip().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not arch:
        raise ValueError(f"unsupported architecture: {machine} (need amd64 or arm64)")
    return arch


def _pick_mihomo_asset(assets: list, version: str) -> dict | None:
    arch = _binary_arch()
    ver = _parse_version_token(version)
    names = {
        str(a.get("name") or ""): a
        for a in assets
        if isinstance(a, dict) and a.get("browser_download_url")
    }
    # Compatible amd64 runs on baseline CPUs; never substitute v2/v3 or Go variants.
    candidates = (
        (f"mihomo-linux-amd64-compatible-v{ver}.gz", f"mihomo-linux-amd64-compatible-{ver}.gz")
        if arch == "amd64" else ()
    )
    candidates += (
        f"mihomo-linux-{arch}-v{ver}.gz",
        f"mihomo-linux-{arch}-{ver}.gz",
    )
    for name in candidates:
        if name in names:
            return names[name]
    return None


def _pick_xray_asset(assets: list) -> dict | None:
    arch = _binary_arch()
    name = "Xray-linux-64.zip" if arch == "amd64" else "Xray-linux-arm64-v8a.zip"
    for asset in assets:
        if isinstance(asset, dict) and asset.get("name") == name and asset.get("browser_download_url"):
            return asset
    return None


def check_component_update(
    component: str, *, force: bool = False, target_version: str | None = None
) -> dict:
    """Check latest (cached) or an exact target release (never cached as latest)."""
    component = (component or "").strip().lower()
    if component not in ("mihomo", "xray", "panel"):
        return {"ok": False, "error": "unknown component"}
    try:
        arch = _binary_arch() if component in ("mihomo", "xray") else None
    except ValueError as ex:
        return {"ok": False, "component": component, "error": str(ex), "update_available": False}

    now = time.time()
    with _versions_lock:
        cached = _UPDATE_CHECK_CACHE.get(component) if target_version is None else None
        if arch and cached and cached.get("architecture") != arch:
            cached = None
        # panel: always re-fetch on UI click (force) — raw CDN can lag; user expects live GitHub
        if (
            not force
            and component != "panel"
            and cached
            and (now - cached.get("_ts", 0)) < _UPDATE_CHECK_TTL
        ):
            out = dict(cached)
            out["cached"] = True
            return out
        if (
            not force
            and component == "panel"
            and cached
            and (now - cached.get("_ts", 0)) < 30.0
        ):
            # short cache only (30s) so spam-click doesn't hammer API
            out = dict(cached)
            out["cached"] = True
            return out

    # current local
    if component == "mihomo":
        cur = _version_mihomo()
        local_ver = cur.get("version") or ""
    elif component == "xray":
        cur = _version_xray()
        local_ver = cur.get("version") or ""
    else:
        cur = {"ok": True, "version": VERSION, "display": f"panel v{VERSION}"}
        local_ver = VERSION

    try:
        if component == "panel":
            gh = _github_panel_remote()
            remote_ver = _parse_version_token(str(gh.get("version") or ""))
            update = _is_remote_newer(remote_ver, local_ver)
            result = {
                "ok": True,
                "component": "panel",
                "current": local_ver,
                "latest": remote_ver or local_ver,
                "update_available": update,
                "up_to_date": (not update) and bool(local_ver and remote_ver),
                "notes": gh.get("notes") or "Источник: GitHub version-manifest.json",
                "html_url": gh.get("html_url") or "",
                "manual_only": False,
                "source": gh.get("source") or "github",
                "manifest": gh.get("manifest"),
                "cached": False,
            }
            result["manual_only"] = not source_capabilities()["remote_panel_update"]
            if target_version and _parse_version_token(target_version) != result["latest"]:
                return {"ok": False, "error": "requested panel version differs from current release"}
        elif component == "mihomo":
            rel = _github_latest("MetaCubeX/mihomo", target_version=target_version)
            remote_ver = rel["version"]
            asset = _pick_mihomo_asset(rel.get("assets") or [], remote_ver)
            update = _is_remote_newer(remote_ver, local_ver)
            result = {
                "ok": True,
                "component": "mihomo",
                "current": local_ver,
                "latest": remote_ver,
                "update_available": update,
                "up_to_date": (not update) and bool(local_ver and remote_ver),
                "notes": rel.get("notes") or "",
                "html_url": rel.get("html_url") or "",
                "asset_name": (asset or {}).get("name"),
                "asset_url": (asset or {}).get("browser_download_url"),
                "manual_only": False,
                "cached": False,
            }
        else:  # xray
            rel = _github_latest("XTLS/Xray-core", target_version=target_version)
            remote_ver = rel["version"]
            asset = _pick_xray_asset(rel.get("assets") or [])
            update = _is_remote_newer(remote_ver, local_ver)
            result = {
                "ok": True,
                "component": "xray",
                "current": local_ver,
                "latest": remote_ver,
                "update_available": update,
                "up_to_date": (not update) and bool(local_ver and remote_ver),
                "notes": rel.get("notes") or "",
                "html_url": rel.get("html_url") or "",
                "asset_name": (asset or {}).get("name"),
                "asset_url": (asset or {}).get("browser_download_url"),
                "manual_only": False,
                "cached": False,
            }
    except Exception as ex:
        result = {
            "ok": False,
            "component": component,
            "current": local_ver,
            "error": str(ex)[:200],
            "update_available": False,
            "up_to_date": False,
            "cached": False,
        }

    result["_ts"] = now
    if arch:
        result["architecture"] = arch
    if target_version is None:
        with _versions_lock:
            _UPDATE_CHECK_CACHE[component] = dict(result)
    return result


def _ensure_bin_backup_dir() -> None:
    ensure_data_dir()
    os.makedirs(BIN_BACKUP_DIR, exist_ok=True)


def _backup_binary(bin_path: str, label: str) -> str:
    _ensure_bin_backup_dir()
    ts = time.strftime("%Y%m%d%H%M%S")
    dest = os.path.join(BIN_BACKUP_DIR, f"{label}.bak.{ts}.{secrets.token_hex(4)}")
    shutil.copy2(bin_path, dest)
    # prune
    try:
        files = sorted(
            [f for f in os.listdir(BIN_BACKUP_DIR) if f.startswith(label + ".bak.")],
            reverse=True,
        )
        for old in files[BIN_BACKUP_MAX:]:
            try:
                os.remove(os.path.join(BIN_BACKUP_DIR, old))
            except OSError:
                pass
    except OSError:
        pass
    return dest


def _extract_mihomo_gz(gz_path: str, out_bin: str) -> None:
    import gzip

    with gzip.open(gz_path, "rb") as src, open(out_bin, "wb") as dst:
        shutil.copyfileobj(src, dst)
    os.chmod(out_bin, 0o755)


def _extract_xray_zip(zip_path: str, out_bin: str) -> None:
    import zipfile
    import tempfile

    with tempfile.TemporaryDirectory(prefix="xray-upd-") as td:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(td)
        # find xray binary
        cand = None
        for root, _dirs, files in os.walk(td):
            for f in files:
                if f == "xray" or f == "xray.exe":
                    cand = os.path.join(root, f)
                    break
            if cand:
                break
        if not cand:
            raise RuntimeError("xray binary not found in archive")
        shutil.copy2(cand, out_bin)
        os.chmod(out_bin, 0o755)


def _probe_timeout(deadline: float, limit: float = 3.0) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("readiness deadline expired")
    return min(limit, remaining)


def _wait_ready(probe, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if probe(timeout=_probe_timeout(deadline, 4.0)) and time.monotonic() < deadline:
                return True
        except Exception:
            pass
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))
    return False


def _health_mihomo(*, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    try:
        if not service_active("mihomo", timeout=_probe_timeout(deadline)):
            return False
        d = mihomo_get("/version", timeout=_probe_timeout(deadline))
        return isinstance(d, dict) and bool(d.get("version"))
    except Exception:
        return False


def _health_xray(*, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    try:
        if not service_active("xray", timeout=_probe_timeout(deadline)):
            return False
        with socket.create_connection((SOCKS_HOST, int(SOCKS_PORT)), timeout=_probe_timeout(deadline)):
            return True
    except Exception:
        return False



def _github_panel_remote() -> dict:
    """Latest panel version from GitHub (version-manifest.json, fallback: latest tag)."""
    repo = (XTTP_GITHUB_REPO or "kotetsyy/xttp-stack").strip()
    branch = (XTTP_GITHUB_BRANCH or "main").strip() or "main"
    html_url = f"https://github.com/{repo}"
    manifest_err = ""
    bust = str(int(time.time()))

    def _parse_manifest(manifest: dict, source: str) -> dict | None:
        ver = _parse_version_token(str(manifest.get("panel") or ""))
        if not ver:
            return None
        notes = str(manifest.get("notes") or "").strip()
        if not notes:
            notes = (
                f"GitHub {repo}@{branch} · version-manifest.json"
                + (f" · updated_at {manifest.get('updated_at')}" if manifest.get("updated_at") else "")
            )
        return {
            "version": ver,
            "notes": notes,
            "html_url": f"{html_url}/blob/{branch}/version-manifest.json",
            "source": source,
            "manifest": manifest,
        }

    # 1) GitHub Contents API (fresher than raw CDN)
    try:
        api_url = (
            f"https://api.github.com/repos/{repo}/contents/version-manifest.json"
            f"?ref={urllib.parse.quote(branch)}"
        )
        req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": GITHUB_UA,
                "Accept": "application/vnd.github.raw+json",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = _parse_manifest(json.loads(raw), "version-manifest-api")
        if parsed:
            return parsed
    except Exception as ex:
        manifest_err = str(ex)[:160]

    # 2) raw.githubusercontent with cache-buster
    try:
        raw_url = (
            f"https://raw.githubusercontent.com/{repo}/{branch}/version-manifest.json"
            f"?t={bust}"
        )
        req = urllib.request.Request(
            raw_url,
            headers={
                "User-Agent": GITHUB_UA,
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = _parse_manifest(json.loads(raw), "version-manifest")
        if parsed:
            return parsed
    except Exception as ex:
        manifest_err = (manifest_err + "; " if manifest_err else "") + str(ex)[:120]

    # 2) fallback: latest release / tag via API
    try:
        try:
            rel = _github_latest(repo)
            ver = rel.get("version") or ""
            if ver:
                return {
                    "version": ver,
                    "notes": rel.get("notes") or f"GitHub release {rel.get('tag') or ver}",
                    "html_url": rel.get("html_url") or f"{html_url}/releases",
                    "source": "release",
                    "manifest": None,
                }
        except Exception:
            pass
        tags = _http_json(f"https://api.github.com/repos/{repo}/tags?per_page=5", timeout=20.0)
        if isinstance(tags, list) and tags:
            tag = str(tags[0].get("name") or "")
            ver = _parse_version_token(tag)
            if ver:
                return {
                    "version": ver,
                    "notes": f"GitHub tag {tag}" + (f" (manifest: {manifest_err})" if manifest_err else ""),
                    "html_url": f"{html_url}/releases/tag/{tag}",
                    "source": "tag",
                    "manifest": None,
                }
    except Exception as ex:
        raise RuntimeError(
            f"не удалось получить версию панели с GitHub: {ex}"
            + (f"; manifest: {manifest_err}" if manifest_err else "")
        ) from ex
    raise RuntimeError(
        "не удалось определить версию панели на GitHub"
        + (f" ({manifest_err})" if manifest_err else "")
    )


PANEL_FILES = ("panel/app.py", "panel/templates/panel.html", "panel/templates/login.html",
               "panel/static/panel.css", "panel/static/app.js", "panel/static/rules.js",
               "panel/static/monitoring.js", "panel/static/system.js")
RUNTIME_FILES = PANEL_FILES + ("scripts/install.sh", "scripts/update.sh", "scripts/check-and-update.sh",
                               "version-manifest.json", "systemd/mihomo-lists.service",
                               "systemd/xttp-update.service", "systemd/xttp-update.timer")


def _git_output(repo: str, *args, timeout=30) -> str:
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("git " + args[0] + ": " + (result.stderr or result.stdout or "")[-400:])
    return result.stdout or ""


def _validate_panel_revision(repo: str, revision: str, expected_version: str = "") -> None:
    for path in RUNTIME_FILES:
        source = _git_output(repo, "show", revision + ":" + path)
        if not source.strip():
            raise RuntimeError("empty required panel file: " + path)
        if path.endswith(".py"):
            tree = ast.parse(source, filename=path)
            version = next((node.value.value for node in tree.body if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
                            and isinstance(node.value, ast.Constant)), None)
            if expected_version and _parse_version_token(str(version or "")) != _parse_version_token(expected_version):
                raise RuntimeError("fetched panel version differs from offered release")
        elif path.endswith(".sh"):
            checked = subprocess.run(["/bin/bash", "-n"], input=source, capture_output=True, text=True, timeout=15)
            if checked.returncode:
                raise RuntimeError("fetched shell syntax invalid: " + path)
        elif path == "version-manifest.json":
            manifest = json.loads(source)
            if not isinstance(manifest, dict) or (expected_version and _parse_version_token(str(manifest.get("panel") or "")) != _parse_version_token(expected_version)):
                raise RuntimeError("fetched manifest differs from offered panel release")


def source_capabilities() -> dict:
    repo = os.environ.get("XTTP_GIT_REPO", XTTP_GIT_REPO)
    git = os.path.exists(os.path.join(repo, ".git"))
    remote = False
    if git:
        try:
            result = subprocess.run(["git", "-C", repo, "config", "--get", "remote.origin.url"],
                                    capture_output=True, text=True, timeout=5)
            remote = result.returncode == 0 and bool((result.stdout or "").strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {"remote_panel_update": remote, "source_mode": "git" if git else "local"}


def _panel_ready(version: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def probe(*, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        if not service_active("mihomo-lists", timeout=_probe_timeout(deadline)):
            return False
        result = subprocess.run(
            ["systemctl", "show", "mihomo-lists", "--property=MainPID", "--value"],
            capture_output=True, text=True, timeout=_probe_timeout(deadline))
        pid = (result.stdout or "").strip()
        if result.returncode or not pid.isdecimal() or int(pid) <= 0:
            return False
        # The timer worker does not inherit the panel service's Environment=.
        with open(os.path.join("/proc", pid, "environ"), "rb") as stream:
            environment = dict(os.fsdecode(item).split("=", 1)
                               for item in stream.read().split(b"\0") if b"=" in item)
        host = environment.get("XTTP_HOST", "").strip() or "127.0.0.1"
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
        if ":" in host:
            host = "[" + str(ipaddress.IPv6Address(host)) + "]"
        elif not re.fullmatch(r"[A-Za-z0-9._-]+", host):
            return False
        base = f"http://{host}:{PORT}"
        with opener.open(base + "/login", timeout=_probe_timeout(deadline, 2.0)) as response:
            header = response.headers.get("X-XTTP-Version", "")
            legacy = _semver_key(version) < _semver_key("0.19.0")
            correct = _parse_version_token(header) == _parse_version_token(version)
            if not header and legacy:
                text = response.read(512 * 1024).decode("utf-8", "replace")
                correct = bool(re.search(r"(?<![0-9.])" + re.escape(_parse_version_token(version)) + r"(?![0-9.])", text))
            if response.status != 200 or not correct:
                return False
        if not legacy:
            for path in PANEL_FILES[3:]:
                with opener.open(base + "/static/" + os.path.basename(path),
                                 timeout=_probe_timeout(deadline, 2.0)) as asset:
                    if asset.status != 200 or not asset.read(1):
                        return False
        return True

    return _wait_ready(probe)


def _panel_backup(repo: str) -> tuple[str, list[dict]]:
    directory = os.path.join(DATA_DIR, "panel-backups", uuid.uuid4().hex)
    os.makedirs(directory, mode=0o700)
    paths = []
    for root, dirs, files in os.walk(os.path.join(repo, "panel")):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        paths.extend(os.path.join(root, name) for name in files if not name.endswith(".pyc"))
    paths.extend(["/opt/mihomo-lists/app.py"] + [
        "/etc/systemd/system/" + name for name in
        ("mihomo-lists.service", "xttp-update.service", "xttp-update.timer")])
    entries = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        saved = os.path.join(directory, str(len(entries)))
        with open(path, "rb") as stream:
            atomic_bytes(saved, stream.read())
        entries.append({"path": path, "backup": saved, "mode": os.stat(path).st_mode & 0o777})
    atomic_json(os.path.join(directory, "manifest.json"), entries)
    return directory, entries


def update_component_panel(user: str = "admin", *, target_version: str | None = None) -> dict:
    """The durable external worker owns installation and complete checkout recovery."""
    if not _update_lock.acquire(blocking=False):
        return {"ok": False, "error": "обновление уже выполняется"}
    try:
        if not source_capabilities()["remote_panel_update"]:
            return {"ok": False, "error": "local-only source: remote panel update unavailable"}
        check = check_component_update("panel", target_version=target_version)
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error") or "check failed"}
        if not check.get("update_available"):
            return {"ok": True, "component": "panel", "unchanged": True, "check": check}
        repo = os.environ.get("XTTP_GIT_REPO", XTTP_GIT_REPO)
        old_ver, new_ver = check.get("current") or VERSION, check.get("latest") or ""
        if _git_output(repo, "status", "--porcelain", "--untracked-files=no").strip():
            raise RuntimeError("tracked source edits must be reconciled before panel update")
        old_commit = _git_output(repo, "rev-parse", "HEAD").strip()
        operation_stage("fetching-panel")
        _git_output(repo, "fetch", "origin", XTTP_GITHUB_BRANCH, timeout=120)
        candidate_commit = _git_output(repo, "rev-parse", "FETCH_HEAD").strip()
        _validate_panel_revision(repo, candidate_commit, new_ver)
        backup, entries = _panel_backup(repo)
        try:
            # Any timeout after this point may have changed the worktree: recover too.
            operation_stage("publishing-panel")
            _git_output(repo, "merge", "--ff-only", candidate_commit, timeout=60)
            if (_git_output(repo, "rev-parse", "HEAD").strip() != candidate_commit
                    or _git_output(repo, "status", "--porcelain", "--untracked-files=no").strip()):
                raise RuntimeError("checkout changed concurrently before installation")
            env = os.environ.copy()
            env.update({"XTTP_GIT_REPO": repo, "XTTP_GIT_SYNCED": "1", "XTTP_NO_RESTART": "1",
                        "XTTP_GITHUB_BRANCH": XTTP_GITHUB_BRANCH})
            pass_options = {}
            if os.name != "nt":
                env["XTTP_UPDATE_LOCK_FD"] = str(_update_lock.fd)
                pass_options["pass_fds"] = (_update_lock.fd,)
            operation_stage("installing-panel")
            installed = subprocess.run(["/bin/bash", os.path.join(repo, "scripts", "update.sh")],
                capture_output=True, text=True, timeout=180, env=env, cwd=repo, **pass_options)
            if installed.returncode:
                raise RuntimeError("update.sh: " + (installed.stderr or installed.stdout or "")[-500:])
            operation_stage("restarting-panel")
            _service_command("restart", "mihomo-lists")
            if not _panel_ready(new_ver):
                raise RuntimeError("panel readiness/version check failed")
            legacy_app = "/opt/mihomo-lists/app.py"
            if os.path.isfile(legacy_app) and os.path.realpath(legacy_app) != os.path.realpath(os.path.join(repo, "panel", "app.py")):
                os.unlink(legacy_app)
            log_activity(user, "update", f"panel: {old_ver} → {new_ver} OK")
            return {"ok": True, "component": "panel", "old": old_ver, "new": new_ver, "backup": backup}
        except Exception as ex:
            operation_stage("recovering-panel")
            try:
                if _git_output(repo, "status", "--porcelain", "--untracked-files=no").strip():
                    raise RuntimeError("recovery refused to overwrite concurrent tracked user edits; complete backup: " + backup)
                if _git_output(repo, "rev-parse", "HEAD").strip() not in (old_commit, candidate_commit):
                    raise RuntimeError("recovery refused: checkout moved concurrently")
                # Retain both commits/branches. A detached checkout is not a force reset.
                _git_output(repo, "switch", "--detach", old_commit, timeout=60)
                for item in entries:
                    if not os.path.commonpath([os.path.abspath(item["path"]), os.path.abspath(repo)]) == os.path.abspath(repo):
                        with open(item["backup"], "rb") as stream:
                            atomic_bytes(item["path"], stream.read(), item["mode"])
                    if _file_hash(item["path"]) != _file_hash(item["backup"]):
                        raise RuntimeError("complete panel restore verification failed")
                reloaded = subprocess.run(["systemctl", "daemon-reload"], capture_output=True, text=True, timeout=30)
                if reloaded.returncode:
                    raise RuntimeError("daemon-reload failed after restore")
                _service_command("restart", "mihomo-lists")
                if not _panel_ready(old_ver):
                    raise RuntimeError("restored panel is not ready")
                rollback = {"status": "succeeded", "commit": old_commit}
            except Exception as restore:
                rollback = {"status": "failed", "error": str(restore)}
            return {"ok": False, "error": str(ex), "rollback": rollback,
                    "rolled_back": rollback["status"] == "succeeded", "backup": backup}
    except Exception as ex:
        log_activity(user, "update", f"panel: {ex}", ok=False)
        return {"ok": False, "error": str(ex)[:500]}
    finally:
        _update_lock.release()



def _service_command(action: str, service: str) -> None:
    result = subprocess.run(["systemctl", action, service], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"systemctl {action} {service}: " + (result.stderr or result.stdout or "")[-400:])


def update_component_binary(
    component: str, user: str = "admin", *, target_version: str | None = None
) -> dict:
    """All post-stop failures enter verified recovery under the common OS lock."""
    if component not in ("mihomo", "xray"):
        return {"ok": False, "error": "only mihomo/xray support auto-update"}
    if not _update_lock.acquire(blocking=False):
        return {"ok": False, "error": "обновление уже выполняется"}
    transaction_lock = _config_lock if component == "mihomo" else _xray_lock
    transaction_held = False
    try:
        transaction_held = transaction_lock.acquire(blocking=False)
        if not transaction_held:
            return {"ok": False, "error": "конфигурация компонента сейчас изменяется"}
        check = check_component_update(component, target_version=target_version)
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error") or "check failed"}
        if not check.get("update_available"):
            if not check.get("current"):
                return {"ok": False, "error": "current component version unavailable", "check": check}
            return {"ok": True, "component": component, "unchanged": True, "check": check}
        url = check.get("asset_url")
        if not url:
            return {"ok": False, "error": "нет native asset для скачивания"}
        bin_path = MIHOMO_BIN if component == "mihomo" else XRAY_BIN
        if not os.path.isfile(bin_path):
            return {"ok": False, "error": f"бинарник не найден: {bin_path}"}
        health = _health_mihomo if component == "mihomo" else _health_xray
        old_ver, new_ver = check.get("current") or "?", check.get("latest") or "?"
        with tempfile.TemporaryDirectory(prefix=f"upd-{component}-") as td:
            asset_name = os.path.basename(check.get("asset_name") or urllib.parse.urlparse(url).path)
            dl_path, new_bin = os.path.join(td, asset_name), os.path.join(td, "new-bin")
            operation_stage("downloading-" + component)
            _http_download(url, dl_path, timeout=240)
            if component == "mihomo" and asset_name.endswith(".gz"):
                _extract_mihomo_gz(dl_path, new_bin)
            elif component == "xray" and asset_name.endswith(".zip"):
                _extract_xray_zip(dl_path, new_bin)
            else:
                raise RuntimeError("неожиданный формат native asset")
            old_hash = _file_hash(bin_path)
            backup = _backup_binary(bin_path, component)
            if _file_hash(backup) != old_hash:
                raise RuntimeError("binary backup verification failed")
            try:
                operation_stage("activating-" + component)
                _service_command("stop", component)
                shutil.copy2(new_bin, bin_path)
                os.chmod(bin_path, 0o755)
                _service_command("start", component)
                if not _wait_ready(health):
                    raise RuntimeError("service readiness failed")
            except Exception as ex:
                operation_stage("recovering-" + component)
                restore_errors = []
                try:
                    _service_command("stop", component)
                except Exception as stop:
                    restore_errors.append(str(stop))
                try:
                    shutil.copy2(backup, bin_path)
                    os.chmod(bin_path, 0o755)
                    if _file_hash(bin_path) != old_hash:
                        raise RuntimeError("restored binary bytes differ")
                    _service_command("start", component)
                    if not _wait_ready(health):
                        raise RuntimeError("restored service is not ready")
                    rollback = {"status": "succeeded"}
                except Exception as restore:
                    rollback = {"status": "failed", "error": str(restore)}
                if restore_errors:
                    rollback["diagnostic"] = "; ".join(restore_errors)
                log_activity(user, "update", f"{component}: {old_ver} → {new_ver} failed; rollback={rollback['status']}", ok=False)
                return {"ok": False, "error": str(ex), "rolled_back": rollback["status"] == "succeeded",
                        "rollback": rollback, "backup": backup, "old": old_ver, "new": new_ver}
            global _VERSIONS_CACHE, _VERSIONS_CACHE_TS
            with _versions_lock:
                _VERSIONS_CACHE, _VERSIONS_CACHE_TS = None, 0
                _UPDATE_CHECK_CACHE.pop(component, None)
            log_activity(user, "update", f"{component}: {old_ver} → {new_ver} OK", extra={"backup": backup})
            return {"ok": True, "component": component, "old": old_ver, "new": new_ver, "backup": backup}
    except Exception as ex:
        log_activity(user, "update", f"{component}: {ex}", ok=False)
        return {"ok": False, "error": str(ex)[:400]}
    finally:
        if transaction_held:
            transaction_lock.release()
        _update_lock.release()


def core_configs_snapshot() -> dict:
    """Runtime mihomo /configs for the core settings page."""
    try:
        d = mihomo_get("/configs", timeout=4.0)
        if not isinstance(d, dict):
            return {"ok": False, "error": "bad configs payload", "configs": {}}
        # ports are often read-only in UI; still surface them
        ports = {
            "port": d.get("port"),
            "socks-port": d.get("socks-port"),
            "mixed-port": d.get("mixed-port"),
            "redir-port": d.get("redir-port"),
            "tproxy-port": d.get("tproxy-port"),
        }
        tun = d.get("tun") if isinstance(d.get("tun"), dict) else {}
        geox = d.get("geox-url") if isinstance(d.get("geox-url"), dict) else {}
        return {
            "ok": True,
            "configs": d,
            "ports": ports,
            "tun": tun,
            "sniffing": bool(d.get("sniffing")),
            "geo_auto_update": bool(d.get("geo-auto-update")),
            "geo_update_interval": d.get("geo-update-interval"),
            "geox_url": geox,
            "geo_update_api": True,  # POST /configs/geo available
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex), "configs": {}, "ports": {}}


def core_configs_apply(form: dict) -> dict:
    """Apply runtime core settings via PATCH /configs."""
    patch: dict = {}
    if "mode" in form and form.get("mode") not in (None, ""):
        mode = str(form.get("mode") or "").strip().lower()
        if mode not in ("rule", "global", "direct"):
            return {"ok": False, "error": f"bad mode: {mode}"}
        patch["mode"] = mode
    if "log-level" in form and form.get("log-level") not in (None, ""):
        lvl = str(form.get("log-level") or "").strip().lower()
        if lvl not in ("silent", "error", "warning", "info", "debug"):
            return {"ok": False, "error": f"bad log-level: {lvl}"}
        patch["log-level"] = lvl
    for bool_key in ("allow-lan", "ipv6", "unified-delay", "tcp-concurrent", "sniffing", "geo-auto-update"):
        if bool_key in form and form.get(bool_key) not in (None, ""):
            v = form.get(bool_key)
            if isinstance(v, bool):
                patch[bool_key] = v
            else:
                s = str(v).strip().lower()
                patch[bool_key] = s in ("1", "true", "yes", "on")
    if "find-process-mode" in form and form.get("find-process-mode") not in (None, ""):
        fpm = str(form.get("find-process-mode") or "").strip().lower()
        if fpm not in ("always", "strict", "off"):
            return {"ok": False, "error": f"bad find-process-mode: {fpm}"}
        patch["find-process-mode"] = fpm
    if "geo-update-interval" in form and form.get("geo-update-interval") not in (None, ""):
        try:
            hours = int(form.get("geo-update-interval"))
        except Exception:
            return {"ok": False, "error": "bad geo-update-interval"}
        if hours < 1 or hours > 168:
            return {"ok": False, "error": "geo-update-interval: 1–168 часов"}
        patch["geo-update-interval"] = hours
    if not patch:
        return {"ok": False, "error": "nothing to patch"}
    r = mihomo_patch("/configs", patch)
    if r.get("ok") is False:
        return r
    # re-read
    snap = core_configs_snapshot()
    snap["patched"] = patch
    try:
        log_activity(
            "system",
            "core",
            "ядро: " + ", ".join(f"{k}={v}" for k, v in patch.items()),
            ok=True,
        )
    except Exception:
        pass
    return snap


def rules_overview_snapshot(q: str = "") -> dict:
    """Flat list of all group rules for Rules overview modal."""
    q = (q or "").strip().lower()
    groups = load_groups()
    items = []
    for gi, g in enumerate(groups):
        gid = g.get("id") or ""
        gname = g.get("name") or ""
        gen = bool(g.get("enabled", True))
        for ei, ent in enumerate(g.get("entries") or []):
            ensure_entry_id(ent)
            row = {
                "group_id": gid,
                "group_name": gname,
                "group_enabled": gen,
                "group_index": gi,
                "entry_id": ent.get("id") or "",
                "entry_index": ei,
                "type": ent.get("type") or "raw",
                "value": ent.get("value") or "",
                "rule": ent.get("rule") or "",
                "name": ent.get("name") or "",
                "enabled": bool(ent.get("enabled", True)),
            }
            if q:
                hay = " ".join(
                    str(row.get(k) or "")
                    for k in ("group_name", "type", "value", "rule", "name")
                ).lower()
                if q not in hay:
                    continue
            items.append(row)
    return {
        "ok": True,
        "count": len(items),
        "groups": len(groups),
        "items": items,
    }


def mihomo_delete(path: str, timeout: float = 4.0) -> dict:
    """DELETE against mihomo external-controller (connections kill)."""
    req = urllib.request.Request(MIHOMO_API + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {"ok": True}
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
                if isinstance(d, dict):
                    d.setdefault("ok", True)
                    return d
                return {"ok": True, "data": d}
            except Exception:
                return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


# ── Live log ring (from mihomo GET /logs stream) ─────────────────────────────
_LOG_LOCK = threading.Lock()
_LOG_BUF: deque = deque(maxlen=800)
_LOG_SEQ = 0
_LOG_EPOCH = uuid.uuid4().hex
_LOG_ERROR = ""
_log_thread: threading.Thread | None = None
_log_stop = threading.Event()
_log_level_wanted = "info"
_log_stream_socket = None


def _log_push(level: str, payload: str) -> None:
    global _LOG_SEQ
    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LOG_BUF.append({
            "seq": _LOG_SEQ,
            "ts": time.time(),
            "type": (level or "info").lower(),
            "payload": (payload or "")[:8192],
        })


def start_log_sampler(level: str = "info") -> None:
    """Daemon: keep a local ring of mihomo log lines (panel polls /api/logs)."""
    global _log_thread, _log_level_wanted
    _log_level_wanted = (level or "info").lower()
    if _log_thread and _log_thread.is_alive():
        return

    def loop() -> None:
        global _log_level_wanted, _LOG_ERROR, _log_stream_socket
        while not _log_stop.is_set():
            lvl = _log_level_wanted or "info"
            url = MIHOMO_API + "/logs?level=" + urllib.parse.quote(lvl)
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=60) as resp:
                    _LOG_ERROR = ""
                    # urllib's HTTPResponse wraps this socket in a BufferedReader.
                    # Keep it so a level change can interrupt an idle readline.
                    with _LOG_LOCK:
                        _log_stream_socket = getattr(getattr(resp.fp, "raw", None), "_sock", None)
                    while not _log_stop.is_set():
                        if (_log_level_wanted or "info") != lvl:
                            break  # reopen with new level
                        line = resp.readline()
                        if not line:
                            break
                        try:
                            d = json.loads(line.decode("utf-8", errors="replace"))
                            _log_push(str(d.get("type") or lvl), str(d.get("payload") or ""))
                        except Exception:
                            text = line.decode("utf-8", errors="replace").strip()
                            if text:
                                _log_push(lvl, text)
            except Exception as ex:
                if _log_level_wanted == lvl:
                    _LOG_ERROR = str(ex)[:200]
                    time.sleep(1.0)
            finally:
                with _LOG_LOCK:
                    _log_stream_socket = None
            time.sleep(0.3)

    _log_stop.clear()
    _log_thread = threading.Thread(target=loop, name="log-sampler", daemon=True)
    _log_thread.start()


def set_log_level(level: str) -> None:
    global _log_level_wanted
    if level not in ("debug", "info", "warning", "error"):
        raise ValueError("Неверный уровень журнала")
    prefs = load_prefs()
    prefs["log_level"] = level
    save_prefs(prefs)
    previous = _log_level_wanted
    _log_level_wanted = level
    with _LOG_LOCK:
        stream_socket = _log_stream_socket
    if previous != level and stream_socket is not None:
        try:
            stream_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # The old stream already closed; the sampler will reopen it.
    start_log_sampler(level)


def logs_snapshot(level: str = "all", since: int = 0, limit: int = 200, mode: str = "tail", epoch: str = "") -> dict:
    """Return buffered log lines. mode=tail → last N; mode=live → only seq>since."""
    start_log_sampler(_log_level_wanted)
    level = (level or "all").lower()
    try:
        since = int(since or 0)
    except Exception:
        since = 0
    try:
        limit = max(20, min(500, int(limit or 200)))
    except Exception:
        limit = 200
    with _LOG_LOCK:
        items = list(_LOG_BUF)
        cur = _LOG_SEQ
    if mode == "live" and since > 0 and epoch == _LOG_EPOCH and since <= cur:
        items = [x for x in items if int(x.get("seq") or 0) > since]
    if level and level not in ("all", ""):
        if level == "warning":
            items = [x for x in items if (x.get("type") or "") in ("warning", "error")]
        else:
            items = [x for x in items if (x.get("type") or "") == level]
    if mode != "live":
        items = items[-limit:]
    else:
        items = items[-limit:]
    return {
        "ok": not bool(_LOG_ERROR),
        "error": _LOG_ERROR,
        "epoch": _LOG_EPOCH,
        "sampler_error": _LOG_ERROR,
        "level": _log_level_wanted,
        "seq": cur,
        "items": items,
        "count": len(items),
    }


def connections_snapshot() -> dict:
    """Simplify mihomo /connections for the panel table."""
    try:
        d = mihomo_get("/connections", timeout=4.0)
    except Exception as ex:
        return {"ok": False, "error": str(ex), "connections": []}
    out = []
    now = time.time()
    for c in d.get("connections") or []:
        md = c.get("metadata") or {}
        host = md.get("host") or md.get("sniffHost") or md.get("destinationIP") or md.get("remoteDestination") or "—"
        start = c.get("start") or ""
        dur = ""
        try:
            # RFC3339-ish
            if start:
                # strip fractional for fromisoformat fallback
                s = start.replace("Z", "+00:00")
                if "." in s:
                    head, rest = s.split(".", 1)
                    # keep timezone part
                    tz = ""
                    if "+" in rest:
                        frac, tz = rest.split("+", 1)
                        tz = "+" + tz
                    elif "-" in rest[1:]:
                        pass
                    s2 = head + (tz if tz else "+00:00")
                    try:
                        from datetime import datetime as _dt
                        t0 = _dt.fromisoformat(s.replace("Z", "+00:00"))
                        dur_s = max(0, int(now - t0.timestamp()))
                    except Exception:
                        dur_s = 0
                else:
                    from datetime import datetime as _dt
                    t0 = _dt.fromisoformat(s)
                    dur_s = max(0, int(now - t0.timestamp()))
                if dur_s < 60:
                    dur = f"{dur_s}s"
                elif dur_s < 3600:
                    dur = f"{dur_s // 60}m {dur_s % 60}s"
                else:
                    dur = f"{dur_s // 3600}h {(dur_s % 3600) // 60}m"
        except Exception:
            dur = "—"
        chains = c.get("chains") or []
        chain = " → ".join(str(x) for x in chains) if chains else "—"
        rule = c.get("rule") or "—"
        payload = c.get("rulePayload") or ""
        if payload:
            rule_disp = f"{rule}({payload})" if rule != "RuleSet" else payload
        else:
            rule_disp = rule
        out.append({
            "id": c.get("id") or "",
            "host": host,
            "network": md.get("network") or "",
            "type": md.get("type") or "",
            "source": f"{md.get('sourceIP') or ''}:{md.get('sourcePort') or ''}".strip(":"),
            "dest": f"{md.get('destinationIP') or md.get('remoteDestination') or ''}:{md.get('destinationPort') or ''}".strip(":"),
            "rule": rule_disp,
            "ruleType": rule,
            "rulePayload": payload,
            "chain": chain,
            "upload": int(c.get("upload") or 0),
            "download": int(c.get("download") or 0),
            "start": start,
            "duration": dur or "—",
        })
    # newest first roughly by start string
    out.sort(key=lambda x: x.get("start") or "", reverse=True)
    return {
        "ok": True,
        "connections": out,
        "downloadTotal": d.get("downloadTotal"),
        "uploadTotal": d.get("uploadTotal"),
        "memory": d.get("memory"),
        "count": len(out),
    }




# ── Traffic rate history (server-side; survives browser F5) ──────────────────
_traffic_lock = threading.Lock()
_traffic_history: deque = deque(maxlen=TRAFFIC_HISTORY_MAX)
_traffic_last: dict | None = None  # {up, down, t}
_traffic_samples_since_save = 0
_traffic_sampler_started = False
_TRAFFIC_EPOCH = uuid.uuid4().hex


def _traffic_history_load() -> None:
    """Load ring buffer from disk (best-effort)."""
    global _traffic_last
    try:
        if not os.path.exists(TRAFFIC_HISTORY_FILE):
            return
        with open(TRAFFIC_HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        pts = data.get("points") or []
        with _traffic_lock:
            _traffic_history.clear()
            for p in pts[-TRAFFIC_HISTORY_MAX:]:
                try:
                    _traffic_history.append({
                        "t": float(p["t"]),
                        "upRate": float(p.get("upRate") or 0),
                        "downRate": float(p.get("downRate") or 0),
                    })
                except Exception:
                    continue
            if data.get("last"):
                _traffic_last = data["last"]
    except Exception:
        pass


def _traffic_history_save() -> None:
    try:
        ensure_data_dir()
        with _traffic_lock:
            payload = {
                "points": list(_traffic_history),
                "last": _traffic_last,
                "saved_at": time.time(),
            }
        tmp = TRAFFIC_HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, TRAFFIC_HISTORY_FILE)
    except Exception:
        pass


def traffic_history_sample_once() -> None:
    """Pull mihomo totals, compute B/s, append to ring buffer."""
    global _traffic_last, _traffic_samples_since_save, _TRAFFIC_EPOCH
    try:
        conns = mihomo_get("/connections", timeout=2.0)
    except Exception:
        return
    now = time.time()
    up = int(conns.get("uploadTotal") or 0)
    down = int(conns.get("downloadTotal") or 0)
    up_rate = 0.0
    down_rate = 0.0
    with _traffic_lock:
        prev = _traffic_last
        if prev and (up < int(prev.get("up", 0)) or down < int(prev.get("down", 0))
                     or now <= float(prev.get("t", 0))):
            _TRAFFIC_EPOCH = uuid.uuid4().hex
            _traffic_history.clear()
            prev = None
        if prev and now > float(prev.get("t") or 0):
            dt = max(0.5, now - float(prev["t"]))
            # totals can reset on mihomo restart → clamp at 0
            up_rate = max(0.0, (up - int(prev.get("up") or 0)) / dt)
            down_rate = max(0.0, (down - int(prev.get("down") or 0)) / dt)
            # if gap too large (sleep/suspend), skip absurd spike
            if dt > 10:
                up_rate = 0.0
                down_rate = 0.0
        _traffic_last = {"up": up, "down": down, "t": now}
        if prev is not None:
            # only append rate points after we have a baseline
            _traffic_history.append({
                "t": now,
                "upRate": up_rate,
                "downRate": down_rate,
            })
            _traffic_samples_since_save += 1
            do_save = _traffic_samples_since_save >= TRAFFIC_HISTORY_SAVE_EVERY
            if do_save:
                _traffic_samples_since_save = 0
        else:
            do_save = False
    if do_save:
        _traffic_history_save()


def traffic_history_list(since: float | None = None) -> list[dict]:
    with _traffic_lock:
        pts = list(_traffic_history)
    if since is not None:
        try:
            s = float(since)
            pts = [p for p in pts if p["t"] > s]
        except Exception:
            pass
    return pts


def start_traffic_sampler() -> None:
    """Daemon thread: 1 Hz sampling independent of open browser tabs."""
    global _traffic_sampler_started
    if _traffic_sampler_started:
        return
    _traffic_sampler_started = True
    _traffic_history_load()

    def loop() -> None:
        # first sample establishes baseline without a zero point
        while True:
            try:
                traffic_history_sample_once()
            except Exception:
                pass
            time.sleep(1.0)

    th = threading.Thread(target=loop, name="traffic-sampler", daemon=True)
    th.start()


def stats_snapshot(since: float | None = None, epoch: str = "") -> dict:
    """Live snapshot for Statistics tab: totals + per-group breakdown."""
    try:
        conns = mihomo_get("/connections")
    except Exception as ex:
        return {"ok": False, "error": f"mihomo connections: {ex}"}
    try:
        rules = mihomo_get("/rules")
    except Exception:
        rules = {"rules": []}

    # provider name (g_Telegram) → display name from groups.json
    groups = load_groups()
    used: set[str] = set()
    name_by_provider: dict[str, str] = {}
    for g in groups:
        if not g.get("enabled", True):
            continue
        active = [e for e in (g.get("entries") or []) if e.get("enabled", True) and e.get("rule")]
        if not active:
            continue
        try:
            n = group_provider_name(g, used)
        except Exception:
            n = "g_" + group_slug(g.get("name") or g.get("id") or "group")
            used.add(n)
        name_by_provider[n] = g.get("name") or n

    by_group: dict[str, dict] = {}
    for r in rules.get("rules") or []:
        if r.get("type") != "RuleSet":
            continue
        payload = r.get("payload") or ""
        if not str(payload).startswith("g_"):
            continue
        extra = r.get("extra") or {}
        display = name_by_provider.get(payload) or str(payload)[2:].replace("_", " ")
        by_group[payload] = {
            "id": payload,
            "name": display,
            "hits": int(extra.get("hitCount") or 0),
            "proxy": r.get("proxy") or "",
            "up": 0,
            "down": 0,
            "conns": 0,
        }

    # live connection bytes by ruleset
    for c in conns.get("connections") or []:
        if c.get("rule") != "RuleSet":
            continue
        payload = c.get("rulePayload") or ""
        if payload not in by_group:
            continue
        by_group[payload]["up"] += int(c.get("upload") or 0)
        by_group[payload]["down"] += int(c.get("download") or 0)
        by_group[payload]["conns"] += 1

    groups_out = sorted(
        by_group.values(),
        key=lambda x: (-(x["up"] + x["down"]), -x["hits"], x["name"].lower()),
    )
    proxy_conns = 0
    for c in conns.get("connections") or []:
        chains = c.get("chains") or []
        if any(x in ("PROXY", "xttp") for x in chains):
            proxy_conns += 1

    with _traffic_lock:
        history_epoch, history = _TRAFFIC_EPOCH, list(_traffic_history)
    if since is not None and epoch == history_epoch:
        history = [point for point in history if point["t"] > float(since)]
    now_ts = time.time()
    return {
        "ok": True,
        "uploadTotal": int(conns.get("uploadTotal") or 0),
        "downloadTotal": int(conns.get("downloadTotal") or 0),
        "connections": len(conns.get("connections") or []),
        "proxyConnections": proxy_conns,
        "memory": int(conns.get("memory") or 0),
        "groups": groups_out,
        "ts": now_ts,
        # ring buffer collected by background sampler (1 Hz, ~1h)
        "history": history,
        "epoch": history_epoch,
        "historyMax": TRAFFIC_HISTORY_MAX,
    }


def services_status() -> dict:
    return {
        "ok": True,
        "xray": service_active("xray"),
        "mihomo": service_active("mihomo"),
        "lists": True,  # if we answer, lists UI is up
    }


def restart_service(name: str) -> None:
    name = (name or "").strip().lower()
    if name not in ("xray", "mihomo"):
        raise ValueError("unknown service: " + name)
    lock = _xray_lock if name == "xray" else _config_lock
    with lock:
        (restart_xray if name == "xray" else restart_mihomo)()
        if not (_health_xray if name == "xray" else _health_mihomo)():
            raise RuntimeError("сервис не готов после перезапуска: " + name)


def _new_bootstrap_password() -> str:
    ensure_data_dir()
    password = secrets.token_urlsafe(24)
    tmp = BOOTSTRAP_PASSWORD_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(password + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, BOOTSTRAP_PASSWORD_FILE)
    return password


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def load_users() -> list[dict]:
    ensure_data_dir()
    if not os.path.exists(USERS_FILE):
        users = [
            {
                "id": str(uuid.uuid4())[:8],
                "username": BOOTSTRAP_USER,
                "password_hash": hash_password(_new_bootstrap_password()),
                "enabled": True,
                "created_at": utc_now(),
                "last_login": "",
            }
        ]
        save_users(users)
        return users
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_users(users: list[dict]) -> None:
    ensure_data_dir()
    tmp = USERS_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, USERS_FILE)


def initialize_users() -> None:
    users = load_users()
    for user in users:
        if user.get("username") == BOOTSTRAP_USER and verify_password("changeme", user.get("password_hash", "")):
            user["password_hash"] = hash_password(_new_bootstrap_password())
            user["session_version"] = secrets.token_hex(16)
            save_users(users)
            break
    if os.path.exists(BOOTSTRAP_PASSWORD_FILE):
        print(f"Initial admin password: {BOOTSTRAP_PASSWORD_FILE} (read locally; change after login)", flush=True)


def find_user(username: str) -> dict | None:
    for u in load_users():
        if u.get("username") == username:
            return u
    return None


def find_user_by_id(uid: str) -> dict | None:
    for u in load_users():
        if u.get("id") == uid:
            return u
    return None


def public_user(u: dict) -> dict:
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "enabled": u.get("enabled", True),
        "created_at": u.get("created_at", ""),
        "last_login": u.get("last_login", ""),
    }


def _load_sessions_unlocked() -> dict:
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            sessions = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        sessions = None
    if not isinstance(sessions, dict):
        quarantine = SESSIONS_FILE + f".invalid.{int(time.time())}.{secrets.token_hex(4)}"
        os.chmod(SESSIONS_FILE, 0o600)
        os.replace(SESSIONS_FILE, quarantine)
        print(f"Invalid session store quarantined at {quarantine}; all prior sessions revoked.", file=sys.stderr)
        return {}
    now = time.time()
    return {
        key: value for key, value in sessions.items()
        if isinstance(value, dict) and isinstance(value.get("expires"), int) and value["expires"] > now
    }


def _save_sessions_unlocked(sessions: dict) -> None:
    ensure_data_dir()
    tmp = SESSIONS_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sessions, f, separators=(",", ":"))
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, SESSIONS_FILE)


def make_session_token(user: str) -> str:
    record = find_user(user)
    if not record or not record.get("enabled", True):
        raise RuntimeError("user is not enabled")
    token = secrets.token_urlsafe(32)
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _sessions_lock:
        sessions = _load_sessions_unlocked()
        sessions[key] = {
            "user_id": record["id"],
            "version": record.get("session_version", ""),
            "expires": int(time.time()) + SESSION_TTL,
        }
        _save_sessions_unlocked(sessions)
    return token


def verify_session_token(token: str) -> str | None:
    if not token:
        return None
    try:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with _sessions_lock:
            record = _load_sessions_unlocked().get(key)
        if not record:
            return None
        user = find_user_by_id(record.get("user_id", ""))
        if not user or not user.get("enabled", True):
            return None
        if record.get("version") != user.get("session_version", ""):
            return None
        return user["username"]
    except Exception:
        return None


def revoke_session_token(token: str) -> None:
    if not token:
        return
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _sessions_lock:
        sessions = _load_sessions_unlocked()
        if sessions.pop(key, None) is not None:
            _save_sessions_unlocked(sessions)


def parse_cookies(header: str) -> dict:
    out = {}
    if not header:
        return out
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = urllib.parse.unquote(v.strip())
    return out


_UI_BUNDLE = None
_UI_LOCK = threading.Lock()
STATIC_TYPES = {"panel.css": "text/css; charset=utf-8",
                **{name + ".js": "text/javascript; charset=utf-8" for name in ("app", "rules", "monitoring", "system")}}


def load_ui_bundle() -> dict:
    """One immutable bundle per process; checkout publication cannot mix releases."""
    global _UI_BUNDLE
    with _UI_LOCK:
        if _UI_BUNDLE is not None:
            return _UI_BUNDLE
        root = os.path.dirname(os.path.abspath(__file__))
        bundle = {"templates": {}, "static": {}}
        for name in ("panel.html", "login.html"):
            with open(os.path.join(root, "templates", name), encoding="utf-8") as stream:
                source = stream.read()
            if not source.strip():
                raise RuntimeError("required template is empty: " + name)
            bundle["templates"][name] = source
        for name in STATIC_TYPES:
            with open(os.path.join(root, "static", name), "rb") as stream:
                body = stream.read()
            if not body:
                raise RuntimeError("required static file is empty: " + name)
            bundle["static"][name] = (body, '"' + hashlib.sha256(body).hexdigest() + '"')
        _UI_BUNDLE = bundle
        return bundle


def render_template(name: str, username: str = "", content: str = "") -> bytes:
    if name not in ("panel.html", "login.html"):
        raise ValueError("unknown template")
    template = load_ui_bundle()["templates"][name]
    values = {"@@VERSION@@": VERSION, "@@USERNAME@@": username, "@@CONTENT@@": content}
    return re.sub(r"@@(?:VERSION|USERNAME|CONTENT)@@", lambda match: esc(values[match.group()]),
                  template).encode("utf-8")


def login_page(err: str = "") -> bytes:
    return render_template("login.html", content=err)


# ─── XTTP / xray ───────────────────────────────────────────────


def _read_xray_config() -> dict | None:
    for p in XRAY_CONFIGS:
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
    return None


def parse_xray_outbound(cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else _read_xray_config()
    empty = {
        "ok": False,
        "tag": "",
        "protocol": "",
        "address": "",
        "port": 0,
        "uuid": "",
        "uuid_short": "",
        "network": "",
        "security": "",
        "sni": "",
        "fingerprint": "",
        "public_key": "",
        "public_key_short": "",
        "short_id": "",
        "spider_x": "",
        "path": "",
        "mode": "",
        "encryption": "none",
        "flow": "",
    }
    if not cfg:
        return empty
    for o in cfg.get("outbounds") or []:
        if o.get("protocol") != "vless":
            continue
        s = o.get("settings") or {}
        vnext = (s.get("vnext") or [{}])[0]
        user = (vnext.get("users") or [{}])[0]
        st = o.get("streamSettings") or {}
        rs = st.get("realitySettings") or {}
        xs = st.get("xhttpSettings") or st.get("wsSettings") or {}
        uid = user.get("id") or ""
        pbk = rs.get("publicKey") or ""
        return {
            "ok": True,
            "tag": o.get("tag") or "xttp",
            "protocol": "vless",
            "address": vnext.get("address") or "",
            "port": int(vnext.get("port") or 0),
            "uuid": uid,
            "uuid_short": (uid[:8] + "…") if len(uid) > 8 else uid,
            "network": st.get("network") or "",
            "security": st.get("security") or "",
            "sni": rs.get("serverName") or "",
            "fingerprint": rs.get("fingerprint") or "",
            "public_key": pbk,
            "public_key_short": (pbk[:14] + "…") if len(pbk) > 14 else pbk,
            "short_id": rs.get("shortId") or "",
            "spider_x": rs.get("spiderX") or "",
            "path": xs.get("path") or "",
            "mode": (xs.get("mode") or (xs.get("extra") or {}).get("mode") or ""),
            "encryption": user.get("encryption") or "none",
            "flow": user.get("flow") or "",
        }
    return empty


def service_active(name: str, *, timeout: float = 5.0) -> bool:
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "").strip() == "active"


def tcp_probe(host: str, port: int, timeout: float = 3.0) -> dict:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            ms = (time.perf_counter() - t0) * 1000
            return {"ok": True, "latency_ms": round(ms, 1), "error": ""}
    except Exception as ex:
        return {"ok": False, "latency_ms": None, "error": str(ex)}


def socks_http_probe(timeout: float = 12.0) -> dict:
    """Full-path probe through local socks → xray → node → public URL.

    Not the same as MetaCube proxy delay (often lighter / cached).
    We warm up once, then measure a second request (closer to steady latency).
    """
    proxy = f"socks5h://{SOCKS_HOST}:{SOCKS_PORT}"
    url = "https://www.gstatic.com/generate_204"
    base = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "--connect-timeout",
        "5",
        "--max-time",
        str(int(timeout)),
        "-x",
        proxy,
        url,
    ]
    try:
        # warm-up (TLS + Reality path) — not counted
        subprocess.run(base, capture_output=True, text=True, timeout=timeout + 2)
        cmd = base[:4] + ["-w", "%{http_code} %{time_total}"] + base[4:]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        out = (r.stdout or "").strip().split()
        code = out[0] if out else "0"
        elapsed = float(out[1]) if len(out) > 1 else 0.0
        ok = code in ("204", "200")
        return {
            "ok": ok,
            "http_code": code,
            "latency_ms": round(elapsed * 1000, 1) if ok else None,
            "error": "" if ok else ((r.stderr or "").strip() or f"HTTP {code}"),
            "note": "full tunnel HTTPS (warmed)",
        }
    except Exception as ex:
        return {"ok": False, "http_code": "", "latency_ms": None, "error": str(ex), "note": ""}


def socks_speedtest(bytes_n: int = 5_000_000, timeout: float = 45.0) -> dict:
    url = f"https://speed.cloudflare.com/__down?bytes={int(bytes_n)}"
    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code} %{size_download} %{speed_download} %{time_total}",
        "--connect-timeout",
        "8",
        "--max-time",
        str(int(timeout)),
        "-x",
        f"socks5h://{SOCKS_HOST}:{SOCKS_PORT}",
        url,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
        parts = (r.stdout or "").strip().split()
        if len(parts) < 4:
            return {"ok": False, "error": (r.stderr or "speedtest failed").strip(), "mbps": None}
        code, size_s, speed_s, time_s = parts[0], parts[1], parts[2], parts[3]
        size = float(size_s)
        speed = float(speed_s)  # bytes/sec
        mbps = round(speed * 8 / 1_000_000, 2)
        ok = code in ("200", "206") and size > 0
        return {
            "ok": ok,
            "http_code": code,
            "bytes": int(size),
            "seconds": round(float(time_s), 2),
            "mbps": mbps if ok else None,
            "error": "" if ok else ((r.stderr or "").strip() or f"HTTP {code}"),
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex), "mbps": None}


def xttp_status() -> dict:
    info = parse_xray_outbound()
    xray_on = service_active("xray")
    mihomo_on = service_active("mihomo")
    return {
        "ok": True,
        "xray_active": xray_on,
        "mihomo_active": mihomo_on,
        "socks": f"{SOCKS_HOST}:{SOCKS_PORT}",
        "mihomo_proxy": "xttp → socks5",
        "outbound": info,
    }


def normalize_hostname(value: str) -> str:
    try:
        value = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Неверное доменное имя") from None
    if not value or len(value) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")):
        raise ValueError("Неверное доменное имя")
    return value


def parse_vless_link(link: str) -> dict:
    s = (link or "").strip()
    if s.startswith("vless://"):
        s = s[8:]
    else:
        raise ValueError("Ссылка должна начинаться с vless://")
    if "#" in s:
        s, frag = s.split("#", 1)
        name = urllib.parse.unquote(frag)
    else:
        name = "xttp"
    if "?" in s:
        main, qs = s.split("?", 1)
        params = urllib.parse.parse_qs(qs, keep_blank_values=True)
        p = {k: (v[0] if v else "") for k, v in params.items()}
    else:
        main, p = s, {}
    if "@" not in main:
        raise ValueError("Неверный формат: нет uuid@host")
    uid, hostport = main.split("@", 1)
    if ":" not in hostport:
        raise ValueError("Неверный формат: нет host:port")
    host, port_s = hostport.rsplit(":", 1)
    if not re.fullmatch(r"[0-9]+", port_s):
        raise ValueError("Неверный порт сервера")
    port = int(port_s)
    net = p.get("type") or p.get("network") or "tcp"
    security = p.get("security") or "none"
    try:
        uid = str(uuid.UUID(urllib.parse.unquote(uid)))
    except (ValueError, AttributeError):
        raise ValueError("Неверный VLESS UUID") from None
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        host = normalize_hostname(host)
    if not 1 <= port <= 65535:
        raise ValueError("Порт: 1–65535")
    if net not in ("tcp", "raw", "ws", "grpc", "xhttp", "splithttp", "httpupgrade", "kcp"):
        raise ValueError("Неподдерживаемый транспорт")
    if security not in ("none", "tls", "reality"):
        raise ValueError("Неподдерживаемая защита транспорта")
    if p.get("encryption", "none") != "none":
        raise ValueError("VLESS encryption должен быть none")
    if security == "reality":
        if not (p.get("pbk") or p.get("publicKey")) or not (p.get("sni") or p.get("serverName")):
            raise ValueError("Reality требует public key и SNI")
        short_id = p.get("sid") or p.get("shortId") or ""
        if not re.fullmatch(r"[0-9a-fA-F]{0,16}", short_id) or len(short_id) % 2:
            raise ValueError("Неверный Reality short ID")
    if (p.get("mode") or "auto") not in ("auto", "packet-up", "stream-up", "stream-one"):
        raise ValueError("Неверный xhttp mode")
    return {
        "name": name,
        "uuid": uid,
        "address": host,
        "port": port,
        "encryption": p.get("encryption") or "none",
        "flow": p.get("flow") or "",
        "network": net,
        "security": security,
        "sni": p.get("sni") or p.get("serverName") or "",
        "fingerprint": p.get("fp") or p.get("fingerprint") or "chrome",
        "public_key": p.get("pbk") or p.get("publicKey") or "",
        "short_id": p.get("sid") or p.get("shortId") or "",
        "spider_x": p.get("spx") or p.get("spiderX") or "",
        "path": p.get("path") or "/",
        "mode": p.get("mode") or "auto",
        "host_header": p.get("host") or "",
        "service_name": p.get("serviceName") or "",
        "alpn": p.get("alpn") or "",
    }


def build_xray_config_from_vless(parsed: dict, base: dict | None = None) -> dict:
    base = base or _read_xray_config() or {}
    inbounds = base.get("inbounds") or [
        {
            "tag": "socks-in",
            "port": SOCKS_PORT,
            "listen": SOCKS_HOST,
            "protocol": "socks",
            "settings": {"udp": True, "auth": "noauth"},
        },
        {
            "tag": "http-in",
            "port": SOCKS_PORT + 1,
            "listen": SOCKS_HOST,
            "protocol": "http",
        },
    ]
    user = {"id": parsed["uuid"], "encryption": parsed.get("encryption") or "none"}
    if parsed.get("flow"):
        user["flow"] = parsed["flow"]
    stream: dict = {
        "network": parsed.get("network") or "tcp",
        "security": parsed.get("security") or "none",
    }
    net = stream["network"]
    if net in ("xhttp", "splithttp"):
        stream["xhttpSettings"] = {
            "path": parsed.get("path") or "/",
            "mode": parsed.get("mode") or "auto",
            "extra": {"mode": parsed.get("mode") or "auto", "xPaddingBytes": "100-1000"},
        }
    elif net == "ws":
        stream["wsSettings"] = {"path": parsed.get("path") or "/", "headers": {}}
        if parsed.get("host_header"):
            stream["wsSettings"]["headers"]["Host"] = parsed["host_header"]
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": parsed.get("service_name") or ""}
    if stream["security"] == "reality":
        stream["realitySettings"] = {
            "serverName": parsed.get("sni") or "",
            "fingerprint": parsed.get("fingerprint") or "chrome",
            "show": False,
            "publicKey": parsed.get("public_key") or "",
            "shortId": parsed.get("short_id") or "",
            "spiderX": parsed.get("spider_x") or "",
        }
    elif stream["security"] == "tls":
        stream["tlsSettings"] = {
            "serverName": parsed.get("sni") or "",
            "fingerprint": parsed.get("fingerprint") or "chrome",
            "allowInsecure": False,
        }
        if parsed.get("alpn"):
            stream["tlsSettings"]["alpn"] = [a.strip() for a in parsed["alpn"].split(",") if a.strip()]

    outbound = {
        "tag": "xttp",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": parsed["address"],
                    "port": int(parsed["port"]),
                    "users": [user],
                }
            ]
        },
        "streamSettings": stream,
    }
    return {
        "log": base.get("log") or {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": base.get("routing")
        or {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["socks-in", "http-in"],
                    "outboundTag": "xttp",
                }
            ],
        },
    }


def backup_xray() -> str:
    with _xray_lock:
        ensure_data_dir()
        source = next((p for p in XRAY_CONFIGS if os.path.isfile(p)), None)
        if not source:
            raise RuntimeError("xray config not found")
        name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f-") + secrets.token_hex(4) + ".json"
        with open(source, "rb") as stream:
            atomic_bytes(os.path.join(XRAY_BACKUP_DIR, name), stream.read())
        files = sorted([n for n in os.listdir(XRAY_BACKUP_DIR) if n.endswith(".json") and not n.startswith(".")], reverse=True)
        for old in files[8:]:
            os.unlink(os.path.join(XRAY_BACKUP_DIR, old))
        return name


def _xray_paths() -> list[str]:
    return [p for p in XRAY_CONFIGS if p == XRAY_CONFIGS[0] or os.path.isfile(p)]


def write_xray_config(cfg: dict) -> None:
    with _xray_lock:
        data = (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        for path in _xray_paths():
            atomic_bytes(path, data)


def activate_xray(cfg: dict) -> dict:
    with _xray_lock:
        ensure_data_dir()
        fd, candidate = tempfile.mkstemp(prefix=".xray-candidate-", suffix=".json", dir=XRAY_BACKUP_DIR)
        os.close(fd)
        try:
            atomic_json(candidate, cfg)
            validation = subprocess.run([XRAY_BIN, "run", "-test", "-config", candidate],
                                        capture_output=True, text=True, timeout=30)
            if validation.returncode:
                # Native output can echo credentials: do not send it to browsers/journal.
                raise ValueError("Xray отклонил конфигурацию (native validation)")
        finally:
            os.unlink(candidate)
        previous = {}
        for path in _xray_paths():
            if os.path.isfile(path):
                with open(path, "rb") as stream:
                    previous[path] = stream.read()
            else:
                previous[path] = None
        backup = backup_xray() if any(value is not None for value in previous.values()) else None
        try:
            write_xray_config(cfg)
            restart_xray()
            if not _wait_ready(_health_xray):
                raise RuntimeError("Xray не готов после перезапуска")
            probe = socks_http_probe()
            if not probe.get("ok"):
                raise RuntimeError("Проверка соединения через Xray не прошла")
            return {"ok": True, "backup": backup, "http_ok": True, "http": probe}
        except Exception as ex:
            try:
                for path, contents in previous.items():
                    if contents is None:
                        if os.path.exists(path):
                            os.unlink(path)
                    else:
                        atomic_bytes(path, contents)
                restart_xray()
                if not _wait_ready(_health_xray) or not socks_http_probe().get("ok"):
                    raise RuntimeError("восстановленный Xray не готов")
                for path, contents in previous.items():
                    if contents is not None and _file_hash(path) != hashlib.sha256(contents).hexdigest():
                        raise RuntimeError("восстановленные байты Xray отличаются")
                rollback = {"status": "succeeded"}
            except Exception as restore:
                rollback = {"status": "failed", "error": str(restore)}
            return {"ok": False, "error": str(ex), "backup": backup, "rollback": rollback}


def restart_xray() -> None:
    r = subprocess.run(RESTART_XRAY_CMD, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "xray restart failed")


def list_xray_backups() -> list[str]:
    if not os.path.isdir(XRAY_BACKUP_DIR):
        return []
    return sorted([f for f in os.listdir(XRAY_BACKUP_DIR) if f.endswith(".json") and not f.startswith(".")], reverse=True)[:8]


def rollback_xray(name: str) -> dict:
    with _xray_lock:
        if not name or os.path.basename(name) != name or name not in list_xray_backups():
            raise ValueError("backup not found")
        with open(os.path.join(XRAY_BACKUP_DIR, name), encoding="utf-8") as stream:
            cfg = json.load(stream)
        return activate_xray(cfg)


def detect_entry(raw: str) -> dict | None:
    """Auto-detect MagiTrickle-like entry type from a line."""
    s = raw.strip()
    if not s or s.startswith("#"):
        return None
    up = s.upper()
    if up.startswith("IP-CIDR6,"):
        val = s.split(",", 1)[1].split(",")[0].strip()
        rule = s if "no-resolve" in s else f"IP-CIDR6,{val},no-resolve"
        return {"type": "ipv6", "value": val, "rule": rule, "enabled": True, "name": ""}
    if up.startswith("IP-CIDR,"):
        val = s.split(",", 1)[1].split(",")[0].strip()
        rule = s if "no-resolve" in s else f"IP-CIDR,{val},no-resolve"
        return {"type": "ipv4", "value": val, "rule": rule, "enabled": True, "name": ""}
    if up.startswith("DOMAIN-SUFFIX,"):
        val = s.split(",", 1)[1].strip()
        return {"type": "namespace", "value": val, "rule": f"DOMAIN-SUFFIX,{val}", "enabled": True, "name": ""}
    if up.startswith("DOMAIN-KEYWORD,"):
        val = s.split(",", 1)[1].strip()
        return {"type": "keyword", "value": val, "rule": f"DOMAIN-KEYWORD,{val}", "enabled": True, "name": ""}
    if up.startswith("DOMAIN,"):
        val = s.split(",", 1)[1].strip()
        return {"type": "domain", "value": val, "rule": f"DOMAIN,{val}", "enabled": True, "name": ""}
    if up.startswith(("GEOSITE,", "GEOIP,", "PROCESS-NAME,", "RULE-SET,")):
        return {"type": "raw", "value": s, "rule": s, "enabled": True, "name": ""}

    try:
        if "/" in s:
            net = ipaddress.ip_network(s, strict=False)
            if isinstance(net, ipaddress.IPv4Network):
                return {
                    "type": "ipv4",
                    "value": str(net),
                    "rule": f"IP-CIDR,{net},no-resolve",
                    "enabled": True,
                    "name": "",
                }
            return {
                "type": "ipv6",
                "value": str(net),
                "rule": f"IP-CIDR6,{net},no-resolve",
                "enabled": True,
                "name": "",
            }
        ip = ipaddress.ip_address(s)
        if isinstance(ip, ipaddress.IPv4Address):
            return {
                "type": "ipv4",
                "value": f"{ip}/32",
                "rule": f"IP-CIDR,{ip}/32,no-resolve",
                "enabled": True,
                "name": "",
            }
        return {
            "type": "ipv6",
            "value": f"{ip}/128",
            "rule": f"IP-CIDR6,{ip}/128,no-resolve",
            "enabled": True,
            "name": "",
        }
    except ValueError:
        pass

    d = s.lower().removeprefix("https://").removeprefix("http://")
    d = d.split("/")[0].split("?")[0].strip(".")
    if d and (DOMAIN_RE.match(d) or "." in d):
        return {
            "type": "namespace",
            "value": d,
            "rule": f"DOMAIN-SUFFIX,{d}",
            "enabled": True,
            "name": "",
        }
    if d:
        return {
            "type": "keyword",
            "value": d,
            "rule": f"DOMAIN-KEYWORD,{d}",
            "enabled": True,
            "name": "",
        }
    return None


def parse_classical_rule(rule: str) -> tuple[str, str]:
    """Split classical rule line into (KIND, payload)."""
    s = (rule or "").strip()
    if not s:
        return "", ""
    parts = [p.strip() for p in s.split(",")]
    kind = (parts[0] or "").upper()
    payload = parts[1] if len(parts) > 1 else ""
    return kind, payload


def classify_query(raw: str) -> tuple[str, str]:
    """
    Return (kind, normalized) where kind is 'ip' | 'domain' | 'invalid'.
    """
    s = (raw or "").strip()
    if not s:
        return "invalid", ""
    # strip URL / scheme / path / port
    try:
        if "://" in s:
            u = urllib.parse.urlparse(s)
            s = u.hostname or s
        elif "/" in s and not re.match(r"^[\d.:a-fA-F/]+$", s):
            # domain/path without scheme
            s = s.split("/")[0]
    except Exception:
        pass
    host = s.strip()
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    elif host.count(":") == 1 and not host.startswith("["):
        # host:port (not IPv6)
        left, right = host.rsplit(":", 1)
        if right.isdigit():
            host = left
    host = host.strip()
    try:
        ip = ipaddress.ip_address(host)
        return "ip", str(ip)
    except ValueError:
        pass
    # bare IPv4/CIDR host part already handled; domain
    d = host.rstrip(".").lower()
    if d and (DOMAIN_RE.match(d) or ("." in d and " " not in d and len(d) < 254)):
        return "domain", d
    if d and re.match(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", d, re.I):
        return "domain", d.lower()
    return "invalid", s


def is_geoip_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    except ValueError:
        return False


def entry_matches_query(ent: dict, qkind: str, qval: str) -> bool:
    """Match one classical payload entry the same way mihomo would for domain/IP rules."""
    if ent.get("enabled", True) is False:
        return False
    rule = (ent.get("rule") or "").strip()
    if not rule:
        return False
    etype = (ent.get("type") or "").lower()
    if etype == "raw":
        etype = ""
    value = (ent.get("value") or "").strip()
    kind, payload = parse_classical_rule(rule)
    if not value and payload:
        value = payload

    # Resolve effective rule kind from rule line first, then UI type
    if not kind:
        kind = {
            "ipv4": "IP-CIDR",
            "ipv6": "IP-CIDR6",
            "domain": "DOMAIN",
            "namespace": "DOMAIN-SUFFIX",
            "keyword": "DOMAIN-KEYWORD",
        }.get(etype, "")

    # IP family (no-resolve: domains never hit these without DNS)
    if kind in ("IP-CIDR", "IP-CIDR6"):
        if qkind != "ip":
            return False
        try:
            ip = ipaddress.ip_address(qval)
            net_s = (value or payload).split(",")[0].strip()
            net = ipaddress.ip_network(net_s, strict=False)
            return ip in net
        except ValueError:
            return False

    # Domain family
    if kind in ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD", "DOMAIN-REGEX"):
        if qkind != "domain":
            return False
        d = qval.rstrip(".").lower()
        v = (value or payload or "").rstrip(".").lower()
        if not v:
            return False
        if kind == "DOMAIN":
            return d == v
        if kind == "DOMAIN-SUFFIX":
            return d == v or d.endswith("." + v)
        if kind == "DOMAIN-KEYWORD":
            return v in d
        if kind == "DOMAIN-WILDCARD":
            import fnmatch

            return fnmatch.fnmatchcase(d, v)
        if kind == "DOMAIN-REGEX":
            try:
                return re.search(v, d, re.I) is not None
            except re.error:
                return False
    return False


def _entry_type_label(ent: dict) -> tuple[str, str]:
    """Return (ui_type, display_label) for tag colors."""
    etype = (ent.get("type") or "").lower()
    if etype == "raw":
        kind, _ = parse_classical_rule(ent.get("rule") or "")
        if kind == "IP-CIDR":
            etype = "ipv4"
        elif kind == "IP-CIDR6":
            etype = "ipv6"
        elif kind == "DOMAIN-SUFFIX":
            etype = "namespace"
        elif kind == "DOMAIN-KEYWORD":
            etype = "keyword"
        elif kind == "DOMAIN":
            etype = "domain"
        else:
            etype = "namespace"
    labels = {
        "ipv4": "IPv4",
        "ipv6": "IPv6",
        "namespace": "Namespace",
        "domain": "Domain",
        "keyword": "Keyword",
    }
    if etype not in labels:
        etype = "namespace"
    return etype, labels[etype]


def proxy_node_name() -> str:
    """Best-effort: first non-DIRECT member of PROXY group (config default: xttp)."""
    try:
        req = urllib.request.Request(MIHOMO_API + "/proxies/PROXY", method="GET")
        with urllib.request.urlopen(req, timeout=2) as r:
            d = json.loads(r.read().decode())
        # now = selected; all = list
        now = d.get("now") or ""
        if now and now.upper() not in ("DIRECT", "REJECT", "COMPATIBLE"):
            return now
        for name in d.get("all") or []:
            if str(name).upper() not in ("DIRECT", "REJECT", "COMPATIBLE"):
                return str(name)
    except Exception:
        pass
    return "xttp"


def explain_route(raw_query: str) -> dict:
    """
    Explain where traffic goes for domain/IP — same order as apply_config():
      GEOIP PRIVATE → DIRECT
      enabled groups (list order) → each classical entry (payload order) → PROXY
      MATCH → DIRECT
    Read-only; does not change rules.
    """
    qkind, qval = classify_query(raw_query)
    if qkind == "invalid" or not qval:
        return {"ok": False, "error": "Введите домен или IP"}

    node = proxy_node_name()
    base = {
        "ok": True,
        "query": qval,
        "query_kind": qkind,
        "query_raw": (raw_query or "").strip(),
        "engine_order": [
            "GEOIP,PRIVATE,DIRECT,no-resolve",
            "RULE-SET,<enabled groups in UI order>,PROXY",
            "MATCH,DIRECT",
        ],
    }

    # 1) GEOIP PRIVATE (only for IPs)
    if qkind == "ip" and is_geoip_private(qval):
        return {
            **base,
            "matched": True,
            "match_source": "system",
            "indicator": "direct",
            "steps": {
                "request": qval,
                "rule": {
                    "type": "geoip",
                    "type_label": "GeoIP",
                    "pattern": "PRIVATE",
                    "rule": "GEOIP,PRIVATE,DIRECT,no-resolve",
                    "index": 0,
                    "index_label": "system #0",
                },
                "group": {
                    "id": "",
                    "name": "system",
                    "provider": "GEOIP",
                },
                "action": {
                    "policy": "DIRECT",
                    "node": None,
                    "detail": "DIRECT (private / LAN)",
                },
            },
        }

    # 2) groups in same order as apply_config
    groups = load_groups()
    used_names: set[str] = set()
    for g in groups:
        if not g.get("enabled", True):
            continue
        entries = g.get("entries") or []
        active = [e for e in entries if e.get("enabled", True) and e.get("rule")]
        if not active:
            continue
        provider = group_provider_name(g, used_names)
        for j, ent in enumerate(entries):
            if ent.get("enabled", True) is False:
                continue
            if not ent.get("rule"):
                continue
            if not entry_matches_query(ent, qkind, qval):
                continue
            etype, tlabel = _entry_type_label(ent)
            pattern = (ent.get("value") or "").strip() or parse_classical_rule(ent.get("rule") or "")[1]
            return {
                **base,
                "matched": True,
                "match_source": "group",
                "indicator": "proxy",
                "steps": {
                    "request": qval,
                    "rule": {
                        "type": etype,
                        "type_label": tlabel,
                        "pattern": pattern,
                        "rule": ent.get("rule") or "",
                        "index": j + 1,
                        "index_label": f"#{j + 1}",
                        "entry_id": ent.get("id") or "",
                        "entry_name": ent.get("name") or "",
                    },
                    "group": {
                        "id": g.get("id") or "",
                        "name": g.get("name") or "",
                        "provider": provider,
                    },
                    "action": {
                        "policy": "PROXY",
                        "node": node,
                        "detail": f"PROXY → {node}",
                    },
                },
            }

    # 3) MATCH,DIRECT
    return {
        **base,
        "matched": False,
        "match_source": "match",
        "indicator": "direct",
        "steps": {
            "request": qval,
            "rule": {
                "type": "match",
                "type_label": "MATCH",
                "pattern": "—",
                "rule": "MATCH,DIRECT",
                "index": None,
                "index_label": "default",
            },
            "group": {
                "id": "",
                "name": "—",
                "provider": "MATCH",
            },
            "action": {
                "policy": "DIRECT",
                "node": None,
                "detail": "MATCH → DIRECT",
            },
        },
    }


def force_type(raw: str, typ: str) -> dict | None:
    """Build entry with forced type (override auto-detect)."""
    s = raw.strip()
    if not s:
        return None
    typ = (typ or "auto").lower()
    if typ in ("", "auto"):
        return detect_entry(s)
    if typ == "ipv4":
        try:
            if "/" in s:
                net = ipaddress.ip_network(s, strict=False)
            else:
                ip = ipaddress.ip_address(s)
                net = ipaddress.ip_network(f"{ip}/32" if isinstance(ip, ipaddress.IPv4Address) else f"{ip}/128", strict=False)
            if isinstance(net, ipaddress.IPv6Network):
                return {
                    "type": "ipv6",
                    "value": str(net),
                    "rule": f"IP-CIDR6,{net},no-resolve",
                    "enabled": True,
                    "name": "",
                }
            return {
                "type": "ipv4",
                "value": str(net),
                "rule": f"IP-CIDR,{net},no-resolve",
                "enabled": True,
                "name": "",
            }
        except Exception:
            return None
    if typ == "ipv6":
        try:
            net = ipaddress.ip_network(s if "/" in s else s + "/128", strict=False)
            return {
                "type": "ipv6",
                "value": str(net),
                "rule": f"IP-CIDR6,{net},no-resolve",
                "enabled": True,
                "name": "",
            }
        except Exception:
            return None
    if typ == "namespace":
        d = s.lower().removeprefix("https://").removeprefix("http://").split("/")[0].strip(".")
        return {"type": "namespace", "value": d, "rule": f"DOMAIN-SUFFIX,{d}", "enabled": True, "name": ""}
    if typ == "domain":
        d = s.lower().removeprefix("https://").removeprefix("http://").split("/")[0].strip(".")
        return {"type": "domain", "value": d, "rule": f"DOMAIN,{d}", "enabled": True, "name": ""}
    if typ == "keyword":
        return {"type": "keyword", "value": s, "rule": f"DOMAIN-KEYWORD,{s}", "enabled": True, "name": ""}
    if typ == "raw":
        return {"type": "raw", "value": s, "rule": s, "enabled": True, "name": ""}
    return detect_entry(s)





def _groups_entry_count(groups: list[dict]) -> int:
    return sum(len(g.get("entries") or []) for g in (groups or []))


def _ensure_groups_dirs() -> None:
    ensure_data_dir()
    os.makedirs(GROUPS_BACKUP_DIR, exist_ok=True)
    os.makedirs(GROUPS_YAML_QUARANTINE, exist_ok=True)
    os.makedirs(os.path.dirname(GROUPS_FILE), exist_ok=True)
    os.makedirs(GROUPS_DIR, exist_ok=True)


def _list_group_backups() -> list[str]:
    if not os.path.isdir(GROUPS_BACKUP_DIR):
        return []
    files = [
        os.path.join(GROUPS_BACKUP_DIR, n)
        for n in os.listdir(GROUPS_BACKUP_DIR)
        if n.startswith("groups.") and n.endswith(".json")
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


def _rotate_paths(paths: list[str], keep: int) -> None:
    for old in paths[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def _backup_groups_file(reason: str = "save") -> str | None:
    """Copy current groups.json to rotating backup dir. Returns path or None."""
    if not os.path.exists(GROUPS_FILE):
        return None
    _ensure_groups_dirs()
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", (reason or "save"))[:40] or "save"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(GROUPS_BACKUP_DIR, f"groups.{ts}.{safe}.{secrets.token_hex(4)}.json")
    shutil.copy2(GROUPS_FILE, dest)
    _rotate_paths(_list_group_backups(), GROUPS_BACKUP_MAX)
    return dest


def _read_groups_raw() -> list[dict] | None:
    """Read groups.json; None if missing. Raises on corrupt content."""
    if not os.path.exists(GROUPS_FILE):
        return None
    with open(GROUPS_FILE, encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        raise RuntimeError("groups.json is empty")
    d = json.loads(raw)
    if not isinstance(d, list):
        raise RuntimeError("groups.json: root must be a list")
    return d


def _try_restore_groups_from_backup() -> list[dict] | None:
    for path in _list_group_backups():
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, list) and d:
                tmp = GROUPS_FILE + ".restore.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                os.replace(tmp, GROUPS_FILE)
                try:
                    log_activity(
                        "system",
                        "groups_restore",
                        f"restored {len(d)} groups from {os.path.basename(path)}",
                        ok=True,
                    )
                except Exception:
                    pass
                return d
        except Exception:
            continue
    return None


def _normalize_groups(d: list[dict]) -> tuple[list[dict], bool]:
    changed = False
    for g in d:
        if not isinstance(g, dict):
            continue
        g.setdefault("enabled", True)
        g.setdefault("entries", [])
        if not isinstance(g["entries"], list):
            g["entries"] = []
            changed = True
        for ent in g["entries"]:
            if not isinstance(ent, dict):
                continue
            ent.setdefault("enabled", True)
            ent.setdefault("name", "")
            if not ent.get("id"):
                ent["id"] = str(uuid.uuid4())[:8]
                changed = True
            if "rule" not in ent and ent.get("value"):
                redet = detect_entry(ent["value"])
                if redet:
                    ent["rule"] = redet["rule"]
                    ent.setdefault("type", redet["type"])
                    changed = True
    return d, changed


def _load_groups_unlocked(*, normalize_write: bool = True) -> list[dict]:
    """Load groups without acquiring lock (caller must hold _groups_lock)."""
    try:
        d = _read_groups_raw()
    except Exception as ex:
        if not normalize_write:
            raise RuntimeError(f"groups.json unreadable: {ex}") from ex
        restored = _try_restore_groups_from_backup()
        if restored is not None:
            d, changed = _normalize_groups(restored)
            if changed:
                _save_groups_unlocked(d, reason="normalize_after_restore", force=True)
            return d
        try:
            log_activity("system", "groups_load_error", str(ex)[:300], ok=False)
        except Exception:
            pass
        raise RuntimeError(f"groups.json unreadable and no backup: {ex}") from ex

    if d is None:
        groups: list[dict] = []
        old: list[dict] = []
        manual = "/etc/mihomo/rule-providers/manual.yaml"
        if os.path.exists(manual):
            for line in open(manual, encoding="utf-8"):
                s = line.strip()
                if s.startswith("- "):
                    det = detect_entry(s[2:].strip())
                    if det:
                        old.append(det)
        if old:
            groups.append(
                {
                    "id": "manual",
                    "name": "Manual",
                    "enabled": True,
                    "entries": old,
                }
            )
        groups, _ = _normalize_groups(groups)
        if normalize_write:
            _save_groups_unlocked(groups, reason="bootstrap", force=True)
        return groups

    d, changed = _normalize_groups(d)
    if changed and normalize_write:
        _save_groups_unlocked(d, reason="normalize", force=True)
    return d


def load_groups() -> list[dict]:
    with _groups_lock:
        return copy.deepcopy(_load_groups_unlocked())


def _save_groups_unlocked(
    groups: list[dict],
    *,
    reason: str = "save",
    force: bool = False,
    allow_shrink: bool = False,
) -> None:
    """Atomic save with backup + anti-wipe guards. Caller holds lock."""
    if not isinstance(groups, list):
        raise RuntimeError("save_groups: groups must be a list")

    old: list[dict] | None = None
    try:
        old = _read_groups_raw()
    except Exception:
        old = None

    if old is not None and not force:
        old_n, new_n = len(old), len(groups)
        old_e, new_e = _groups_entry_count(old), _groups_entry_count(groups)

        if old_n > 0 and new_n == 0 and not allow_shrink:
            raise RuntimeError(
                f"refuse empty groups.json overwrite ({old_n} groups / {old_e} entries). "
                "Delete groups one-by-one or pass allow_shrink."
            )

        if (
            not allow_shrink
            and old_e >= 20
            and new_e < max(5, int(old_e * 0.15))
            and new_n < old_n
        ):
            _backup_groups_file("blocked_shrink")
            raise RuntimeError(
                f"refuse catastrophic groups shrink: {old_n}→{new_n} groups, "
                f"{old_e}→{new_e} entries (reason={reason}). "
                f"Backups in {GROUPS_BACKUP_DIR}"
            )

    if old is not None:
        _backup_groups_file(reason)

    _ensure_groups_dirs()
    atomic_json(GROUPS_FILE, groups)


def save_groups(
    groups: list[dict],
    *,
    reason: str = "save",
    force: bool = False,
    allow_shrink: bool = False,
) -> None:
    with _groups_lock:
        _save_groups_unlocked(
            groups, reason=reason, force=force, allow_shrink=allow_shrink
        )


def update_groups(
    reason: str,
    mutator,
    *,
    allow_shrink: bool = False,
    force: bool = False,
) -> list[dict]:
    """Load → mutator(groups) → save under one lock (prevents race overwrites)."""
    with _groups_lock:
        groups = _load_groups_unlocked()
        mutator(groups)
        _save_groups_unlocked(
            groups, reason=reason, force=force, allow_shrink=allow_shrink
        )
        return copy.deepcopy(groups)


def load_remotes() -> list[dict]:
    if not os.path.exists(REMOTES_FILE):
        return []
    try:
        d = json.load(open(REMOTES_FILE, encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_remotes(items: list[dict]) -> None:
    tmp = REMOTES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, REMOTES_FILE)


def safe_name(name: str) -> str:
    name = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-_") or "list"
    if name in ("manual", "groups"):
        name = name + "-remote"
    return name[:48]


def group_file_id(gid: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", gid.lower())[:48]


def group_slug(name: str) -> str:
    """Human-readable slug for rule-provider name (keeps letters/digits/underscore)."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip())
    s = re.sub(r"_+", "_", s).strip("_") or "group"
    if s[0].isdigit():
        s = "G_" + s
    return s[:40]


def group_provider_name(group: dict, used: set[str]) -> str:
    """e.g. Youtube -> g_Youtube; duplicates get _2, _3…"""
    base = "g_" + group_slug(group.get("name") or group.get("id") or "group")
    # mihomo key must be unique; reserved names
    if base in ("g_GEOIP", "g_MATCH", "g_DIRECT", "g_PROXY"):
        base = base + "_grp"
    name = base
    n = 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def _file_hash(path: str) -> str:
    with open(path, "rb") as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def _groups_revision(groups: list[dict]) -> str:
    return hashlib.sha256(json.dumps(groups, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def rules_state(groups=None) -> dict:
    groups = load_groups() if groups is None else groups
    saved = _groups_revision(groups)
    try:
        with open(os.path.join(DATA_DIR, "rules-state.json"), encoding="utf-8") as stream:
            state = json.load(stream)
        applied = state.get("applied_revision")
        if state.get("config_hash") != _file_hash(CONFIG_FILE):
            applied = None
    except (OSError, ValueError, AttributeError):
        applied = None
    return {"saved_revision": saved, "applied_revision": applied,
            "status": "unknown" if not applied else "applied" if applied == saved else "pending"}


def _replace_routing_sections(config: str, providers: str, rules: str) -> str:
    """Replace owned top-level sections, preserving every other YAML byte."""
    keys = list(re.finditer(r"""(?m)^([A-Za-z_][A-Za-z0-9_-]*|"[^"\n]+"|'[^'\n]+')\s*:""", config))
    replacements = {"rule-providers": providers.rstrip() + "\n\n", "rules": rules.rstrip() + "\n\n"}
    seen, chunks = set(), [config[:keys[0].start()] if keys else config]
    for index, match in enumerate(keys):
        key = match.group(1).strip("'\"")
        end = keys[index + 1].start() if index + 1 < len(keys) else len(config)
        if key in replacements:
            if key in seen:
                raise RuntimeError("config.yaml: duplicate managed section: " + key)
            seen.add(key)
            chunks.append(replacements[key])
        else:
            chunks.append(config[match.start():end])
    if "rule-providers" not in seen:
        raise RuntimeError("config.yaml: no rule-providers section")
    if "rules" not in seen:
        chunks.append("\n" + replacements["rules"])
    return "".join(chunks)


def apply_config(*, validate: bool = True, preparation: bool = False) -> str:
    """Publish one immutable provider generation only after native validation."""
    with _config_lock, _groups_lock:
        groups = copy.deepcopy(_load_groups_unlocked())
        with open(CONFIG_FILE, "rb") as stream:
            old = stream.read()
        cfg = old.decode("utf-8")
        generation = tempfile.mkdtemp(prefix="generation-", dir=_provider_directory())
        candidate = None
        published = False
        try:
            prov, rules, used = ["rule-providers:"], [], set()
            for group in groups:
                active = [e for e in group.get("entries", []) if e.get("enabled", True) and e.get("rule")]
                if not group.get("enabled", True) or not active:
                    continue
                name = group_provider_name(group, used)
                provider = os.path.join(generation, name + ".yaml")
                # JSON strings are valid YAML scalars: names/rules cannot inject structure.
                payload = "payload:\n" + "".join("  - " + json.dumps(e["rule"], ensure_ascii=False) + "\n" for e in active)
                atomic_bytes(provider, payload.encode("utf-8"), 0o600)
                prov += [f"  {name}:", "    type: file", "    behavior: classical",
                         "    path: " + json.dumps(os.path.abspath(provider).replace("\\", "/"))]
                rules.append(f"  - RULE-SET,{name},PROXY")
            if not rules:
                provider = os.path.join(generation, "_empty.yaml")
                atomic_bytes(provider, b"payload: []\n")
                prov += ["  _empty:", "    type: file", "    behavior: classical",
                         "    path: " + json.dumps(os.path.abspath(provider).replace("\\", "/"))]
            rule_section = ("rules:\n  - GEOIP,PRIVATE,DIRECT,no-resolve\n"
                            + "\n".join(rules) + "\n  - MATCH,DIRECT\n")
            new = _replace_routing_sections(cfg, "\n".join(prov), rule_section).encode("utf-8")
            fd, candidate = tempfile.mkstemp(prefix=".config-candidate-", suffix=".yaml",
                                            dir=os.path.dirname(os.path.abspath(CONFIG_FILE)))
            os.close(fd)
            atomic_bytes(candidate, new)
            if validate:
                result = subprocess.run(
                    [MIHOMO_BIN, "-t", "-d", os.path.dirname(os.path.abspath(CONFIG_FILE)), "-f", candidate],
                    capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    raise RuntimeError("mihomo config invalid:\n" + (result.stderr or result.stdout or "")[-800:])
            sync_directory(GROUPS_DIR)
            if preparation:
                atomic_json(os.path.join(DATA_DIR, "prepare-config.json"), {
                    "stage": "publishing", "old_hash": hashlib.sha256(old).hexdigest(),
                    "candidate_hash": hashlib.sha256(new).hexdigest(), "generation": generation})
            os.replace(candidate, CONFIG_FILE)
            candidate = None
            published = True
            try:
                sync_directory(os.path.dirname(os.path.abspath(CONFIG_FILE)))
            except Exception:
                atomic_bytes(CONFIG_FILE, old)
                published = False
                raise
            return generation
        finally:
            if candidate and os.path.exists(candidate):
                os.unlink(candidate)
            if not published:
                shutil.rmtree(generation)


def _provider_directory() -> str:
    os.makedirs(GROUPS_DIR, exist_ok=True)
    sync_directory(os.path.dirname(os.path.abspath(GROUPS_DIR)))
    return GROUPS_DIR


def _cleanup_provider_generations(*configs: bytes) -> None:
    try:
        retained = set()
        for config in configs:
            for value in re.findall(r"(?m)^    path: (.+)$", config.decode("utf-8")):
                if value.startswith('"'):
                    retained.add(os.path.abspath(os.path.dirname(json.loads(value))))
        for entry in os.scandir(GROUPS_DIR):
            if (entry.name.startswith("generation-") and entry.is_dir(follow_symlinks=False)
                    and os.path.abspath(entry.path) not in retained):
                shutil.rmtree(entry.path)
    except (OSError, ValueError) as ex:
        log_activity("system", "provider_cleanup", str(ex), ok=False)


def activate_rules() -> dict:
    with _config_lock, _groups_lock:
        with open(CONFIG_FILE, "rb") as stream:
            old = stream.read()
        published = False
        generation = None
        try:
            generation = apply_config()
            published = True
            restart_mihomo()
            if not _wait_ready(_health_mihomo):
                raise RuntimeError("mihomo readiness failed")
            atomic_json(os.path.join(DATA_DIR, "rules-state.json"), {
                "applied_revision": _groups_revision(_load_groups_unlocked()),
                "config_hash": _file_hash(CONFIG_FILE)})
            with open(CONFIG_FILE, "rb") as stream:
                _cleanup_provider_generations(old, stream.read())
            return {"ok": True, "state": rules_state()}
        except Exception as ex:
            try:
                published = published or _file_hash(CONFIG_FILE) != hashlib.sha256(old).hexdigest()
            except OSError:
                published = True
            rollback = {"status": "not_attempted"}
            if published:
                try:
                    atomic_bytes(CONFIG_FILE, old)
                    restart_mihomo()
                    if not _wait_ready(_health_mihomo):
                        raise RuntimeError("restored mihomo is not ready")
                    if _file_hash(CONFIG_FILE) != hashlib.sha256(old).hexdigest():
                        raise RuntimeError("restored configuration differs")
                    rollback = {"status": "succeeded"}
                    if generation:
                        try:
                            shutil.rmtree(generation)
                        except OSError as cleanup:
                            log_activity("system", "provider_cleanup", str(cleanup), ok=False)
                except Exception as restore:
                    rollback = {"status": "failed", "error": str(restore)}
            return {"ok": False, "error": str(ex), "rollback": rollback, "state": rules_state()}


def prepare_config() -> None:
    """Resume authorized seed publication, without starting or assuming readiness."""
    marker = CONFIG_FILE + ".xttp-prepare.sha256"
    journal = os.path.join(DATA_DIR, "prepare-config.json")
    with _config_lock:
        current = _file_hash(CONFIG_FILE)
        try:
            with open(journal, encoding="utf-8") as stream:
                state = json.load(stream)
        except FileNotFoundError:
            state = {}
        if current == state.get("candidate_hash"):
            state["stage"] = "completed"
            atomic_json(journal, state)
            if os.path.exists(marker):
                os.unlink(marker)
            return
        try:
            with open(marker, encoding="ascii") as stream:
                seed = stream.read().strip()
        except FileNotFoundError:
            if state.get("stage") == "publishing":
                raise RuntimeError("interrupted preparation: config differs from seed and candidate")
            return  # existing device configuration is not ours to regenerate
        if current != seed and current != state.get("old_hash"):
            raise RuntimeError("preparation refused: configuration was edited after seed installation")
        apply_config(validate=os.path.isfile(MIHOMO_BIN), preparation=True)
        with open(journal, encoding="utf-8") as stream:
            state = json.load(stream)
        state["stage"] = "completed"
        atomic_json(journal, state)
        os.unlink(marker)


def restart_mihomo():
    r = subprocess.run(RESTART_CMD, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "restart failed")


def find_group(groups, gid):
    for g in groups:
        if g.get("id") == gid:
            return g
    return None




def ensure_entry_id(ent: dict) -> str:
    if not ent.get("id"):
        ent["id"] = str(uuid.uuid4())[:8]
    return ent["id"]








def session_csrf(token: str) -> str | None:
    if not verify_session_token(token):
        return None
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _sessions_lock:
        sessions = _load_sessions_unlocked()
        record = sessions.get(key)
        if record is None:
            return None
        if not record.get("csrf"):
            record["csrf"] = secrets.token_urlsafe(32)
            _save_sessions_unlocked(sessions)
        return record["csrf"]


def _boolean(value) -> bool:
    if value in (True, "1", "true", "yes", "on"):
        return True
    if value in (False, "0", "false", "no", "off"):
        return False
    raise ValueError("Ожидалось логическое значение")


def _label(value, maximum=160) -> str:
    if not isinstance(value, str):
        raise ValueError("Ожидался текст")
    value = value.strip()
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"Название: 1–{maximum} символов без переводов строки")
    return value


def validated_entry(data: dict, previous: dict | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Правило должно быть объектом")
    previous = previous or {}
    kind = data.get("type", previous.get("type", "auto"))
    raw = data.get("value", previous.get("value", ""))
    if kind not in ("auto", "namespace", "domain", "keyword", "ipv4", "ipv6", "raw"):
        raise ValueError("Неверный тип правила")
    if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 32 for c in raw):
        raise ValueError("Неверное значение правила")
    detected = force_type(raw, kind)
    if not detected:
        raise ValueError("Не удалось распознать правило: " + raw[:100])
    kind, value = detected["type"], detected["value"]
    if kind in ("domain", "namespace"):
        value = normalize_hostname(value)
        detected["value"] = value
        detected["rule"] = ("DOMAIN," if kind == "domain" else "DOMAIN-SUFFIX,") + value
    if kind == "keyword" and (not value or "," in value or any(c.isspace() for c in value)):
        raise ValueError("Неверное ключевое слово")
    if kind in ("ipv4", "ipv6"):
        network = ipaddress.ip_network(value, strict=False)
        if (kind == "ipv4") != isinstance(network, ipaddress.IPv4Network):
            raise ValueError("Семейство IP не соответствует типу")
        detected["value"] = str(network)
        detected["rule"] = ("IP-CIDR," if kind == "ipv4" else "IP-CIDR6,") + str(network) + ",no-resolve"
    if kind == "raw":
        if "," not in value or not all(part.strip() for part in value.split(",")):
            raise ValueError("Неверное classical правило")
    detected["id"] = previous.get("id") or uuid.uuid4().hex
    name = data.get("name", previous.get("name", ""))
    if not isinstance(name, str) or len(name) > 256 or any(ord(c) < 32 for c in name):
        raise ValueError("Неверное название правила")
    detected["name"] = name
    detected["enabled"] = _boolean(data.get("enabled", previous.get("enabled", True)))
    return {**previous, **detected} if previous else detected


def group_summary(group: dict) -> dict:
    counts = {}
    for entry in group.get("entries", []):
        kind = entry.get("type", "raw")
        counts[kind] = counts.get(kind, 0) + 1
    return {"id": group["id"], "name": group["name"], "enabled": group.get("enabled", True),
            "count": len(group.get("entries", [])), "counts": counts}


def group_detail(gid: str, offset=0, limit=50, q="") -> dict:
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("offset >= 0; limit: 1–200")
    with _groups_lock:
        groups = _load_groups_unlocked()
        group = find_group(groups, gid)
        if not group:
            raise ValueError("Группа не найдена")
        query = q.strip().casefold()
        entries = [entry for entry in group.get("entries", [])
                   if not query or query in " ".join(str(entry.get(k, "")) for k in ("value", "name", "rule", "type")).casefold()]
        return {"ok": True, "group": {key: group[key] for key in ("id", "name", "enabled")},
                "entries": [{key: row.get(key, True if key == "enabled" else "") for key in
                             ("id", "type", "value", "rule", "name", "enabled")}
                            for row in entries[offset:offset + limit]],
                "total": len(entries), "offset": offset, "limit": limit, "state": rules_state(groups)}


def detect_snapshot(text: str, mode: str = "auto") -> dict:
    items, invalid = [], 0
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = validated_entry({"value": line, "type": mode})
            items.append({key: row[key] for key in ("type", "value", "rule")})
        except ValueError:
            invalid += 1
    counts = {}
    for row in items:
        counts[row["type"]] = counts.get(row["type"], 0) + 1
    return {"ok": True, "items": items, "counts": counts, "invalid": invalid}


def mutate_groups(action: str, form: dict) -> dict:
    """One lock, complete validation, one backup and one durable publication."""
    with _groups_lock:
        groups = copy.deepcopy(_load_groups_unlocked(normalize_write=False))
        gid = form.get("gid", "")
        group = find_group(groups, gid)
        result = {"ok": True}
        if action == "create":
            group = {"id": uuid.uuid4().hex, "name": _label(form.get("name", "")),
                     "enabled": True, "entries": []}
            groups.append(group)
        elif action == "reorder":
            ids = json.loads(form.get("ids", "[]"))
            if (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
                    or len(set(ids)) != len(ids) or set(ids) != {g["id"] for g in groups}):
                raise ValueError("Порядок должен содержать каждую группу ровно один раз")
            by_id = {g["id"]: g for g in groups}
            groups = [by_id[key] for key in ids]
        elif not group:
            raise ValueError("Группа не найдена")
        elif action == "delete":
            if form.get("confirm") != "1":
                raise ValueError("confirm required")
            groups.remove(group)
        elif action == "save":
            if "name" in form:
                group["name"] = _label(form["name"])
            if "enabled" in form:
                group["enabled"] = _boolean(form["enabled"])
            dirty = json.loads(form.get("entries", "[]"))
            deleted = json.loads(form.get("deleted", "[]"))
            if not isinstance(dirty, list) or not isinstance(deleted, list) or not all(isinstance(i, str) for i in deleted):
                raise ValueError("entries/deleted должны быть массивами")
            entries = group.setdefault("entries", [])
            by_id = {e["id"]: e for e in entries}
            if len(set(deleted)) != len(deleted) or any(key not in by_id for key in deleted):
                raise ValueError("Удаляемое правило не найдено")
            seen, updates, additions, errors, submitted_index = set(), {}, [], [], {}
            for index, row in enumerate(dirty):
                try:
                    if not isinstance(row, dict):
                        raise ValueError("Неверное правило")
                    key = row.get("id")
                    if key is not None and key != "" and not isinstance(key, str):
                        raise ValueError("Неверный ID правила")
                    if key:
                        if not isinstance(key, str) or key not in by_id or key in seen or key in deleted:
                            raise ValueError("Изменяемое правило не найдено или повторено")
                        seen.add(key)
                        entry = updates[key] = validated_entry(row, by_id[key])
                    else:
                        entry = validated_entry(row)
                        additions.append(entry)
                    submitted_index[entry["id"]] = index
                except ValueError as ex:
                    errors.append({"index": index, "id": row.get("id") if isinstance(row, dict) else None,
                                   "error": str(ex)})
            if errors:
                raise RequestError(400, "Ошибки в правилах", errors)
            final = [updates.get(e["id"], e) for e in entries if e["id"] not in deleted] + additions
            counts = {}
            for entry in final:
                counts[entry.get("rule")] = counts.get(entry.get("rule"), 0) + 1
            # Existing legacy duplicates are preserved; new/changed duplicates are rejected.
            errors = [{"id": entry["id"], "index": submitted_index[entry["id"]],
                       "error": "Повторяющееся правило: " + entry["value"]}
                      for entry in list(updates.values()) + additions if counts[entry["rule"]] > 1]
            if errors:
                raise RequestError(400, "Повторяющиеся правила", errors)
            group["entries"] = final
        elif action == "import":
            preview = detect_snapshot(form.get("text", ""), form.get("detect") or "auto")
            existing = {e.get("rule") for e in group.get("entries", [])}
            foreign = {e.get("rule") for g in groups if g["id"] != gid for e in g.get("entries", [])}
            imported, skipped = 0, 0
            skip_conflicts = _boolean(form.get("skip_conflicts", "0"))
            for row in preview["items"]:
                if row["rule"] in existing:
                    skipped += 1
                    continue
                if skip_conflicts and row["rule"] in foreign:
                    skipped += 1
                    continue
                group.setdefault("entries", []).append(validated_entry(row))
                existing.add(row["rule"])
                imported += 1
            result.update({"imported": imported, "skipped": skipped, "invalid": preview["invalid"]})
        else:
            raise ValueError("unknown group action")
        _save_groups_unlocked(groups, reason="group_" + action, allow_shrink=action in ("delete", "save"))
        if action in ("create", "save"):
            result["group"] = group_summary(group)
        result["state"] = rules_state(groups)
        return result


def fleet_timer_status() -> dict:
    try:
        enabled = subprocess.run(["systemctl", "is-enabled", "xttp-update.timer"],
                                 capture_output=True, text=True, timeout=5)
        active = subprocess.run(["systemctl", "is-active", "xttp-update.timer"],
                                capture_output=True, text=True, timeout=5)
        en, ac = (enabled.stdout or "").strip(), (active.stdout or "").strip()
        if en not in ("enabled", "disabled", "static", "masked", "enabled-runtime") or ac not in ("active", "inactive", "failed"):
            raise RuntimeError("timer policy unavailable")
        return {"enabled": en in ("enabled", "enabled-runtime"), "active": ac == "active"}
    except Exception as ex:
        return {"enabled": None, "active": None, "error": str(ex)[:160]}


def page(username: str = "") -> bytes:
    return render_template("panel.html", username=username)


class RequestError(ValueError):
    def __init__(self, code: int, message: str, errors=None):
        super().__init__(message)
        self.code = code
        self.errors = errors


def user_action(action: str, form: dict, me: str) -> dict:
    users = load_users()
    uid = form.get("id", "")
    target = next((user for user in users if user.get("id") == uid), None)
    relogin = False
    if action == "add":
        username, password = form.get("username", "").strip(), form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", username):
            raise ValueError("Логин: 2–32 символа [A-Za-z0-9_.-]")
        if len(password) < 6:
            raise ValueError("Пароль минимум 6 символов")
        if any(user.get("username") == username for user in users):
            raise ValueError("Логин занят")
        users.append({"id": uuid.uuid4().hex, "username": username,
                      "password_hash": hash_password(password), "enabled": True,
                      "created_at": utc_now(), "last_login": ""})
    elif action in ("password", "toggle", "delete"):
        if not target:
            raise ValueError("Пользователь не найден")
        if action == "password":
            password = form.get("password", "")
            if len(password) < 6:
                raise ValueError("Пароль минимум 6 символов")
            target["password_hash"] = hash_password(password)
            target["session_version"] = secrets.token_hex(16)
            relogin = target.get("username") == me
        elif action == "toggle":
            if target.get("username") == me and target.get("enabled", True):
                raise ValueError("Нельзя заблокировать себя")
            if target.get("enabled", True) and sum(bool(user.get("enabled", True)) for user in users) <= 1:
                raise ValueError("Нужен хотя бы один активный admin")
            target["enabled"] = not target.get("enabled", True)
            target["session_version"] = secrets.token_hex(16)
        else:
            if target.get("username") == me:
                raise ValueError("Нельзя удалить себя")
            users = [user for user in users if user["id"] != uid]
            if not any(user.get("enabled", True) for user in users):
                raise ValueError("Должен остаться активный admin")
    else:
        raise RequestError(404, "not found")
    save_users(users)
    if action == "password" and target.get("username") == BOOTSTRAP_USER:
        try:
            os.unlink(BOOTSTRAP_PASSWORD_FILE)
        except FileNotFoundError:
            pass
    log_activity(me, "user_" + action, target.get("username", "") if target else form.get("username", ""))
    return {"ok": True, "users": [public_user(user) for user in users], "relogin": relogin}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[lists-ui]", self.address_string(), fmt % args, flush=True)

    def _token(self):
        return parse_cookies(self.headers.get("Cookie", "")).get(SESSION_COOKIE, "")

    def _auth_user(self):
        return verify_session_token(self._token())

    def _send(self, code, body=b"", content_type="application/json; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-XTTP-Version", VERSION)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), headers=headers)

    def _cookie(self, token="", clear=False):
        value = "deleted" if clear else urllib.parse.quote(token)
        expiry = "; Expires=Thu, 01 Jan 1970 00:00:00 GMT" if clear else ""
        return (f"{SESSION_COOKIE}={value}; Path=/; Max-Age={0 if clear else SESSION_TTL}; "
                "HttpOnly; SameSite=Lax" + expiry + ("; Secure" if SESSION_COOKIE_SECURE else ""))

    def _redirect(self, path="/rules", token=None, clear=False):
        headers = {"Location": path}
        if token is not None or clear:
            headers["Set-Cookie"] = self._cookie(token or "", clear)
        self._send(303, headers=headers)

    def _need_auth(self, err=""):
        self._send(200, login_page(err), "text/html; charset=utf-8")

    def _form(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) > 1:
            raise RequestError(400, "ambiguous request body length")
        length = lengths[0] if lengths else "0"
        if not re.fullmatch(r"[0-9]+", length):
            raise RequestError(400, "invalid Content-Length")
        normalized_length = length.lstrip("0") or "0"
        if len(normalized_length) > 7:
            raise RequestError(413, "request body exceeds 2 MiB")
        remaining = int(normalized_length)
        if remaining > 2 * 1024 * 1024:
            raise RequestError(413, "request body exceeds 2 MiB")
        deadline = time.monotonic() + 10
        chunks = []
        old_timeout = self.connection.gettimeout()
        try:
            while remaining:
                available = deadline - time.monotonic()
                if available <= 0:
                    raise RequestError(408, "request body deadline exceeded")
                self.connection.settimeout(available)
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    raise RequestError(400, "incomplete request body")
                remaining -= len(chunk)
                chunks.append(chunk)
            raw = b"".join(chunks).decode("utf-8")
        except (socket.timeout, TimeoutError):
            raise RequestError(408, "request body deadline exceeded") from None
        except UnicodeDecodeError:
            raise RequestError(400, "request body must be UTF-8") from None
        finally:
            self.connection.settimeout(old_timeout)
        content_type = self.headers.get("Content-Type", "application/x-www-form-urlencoded").split(";", 1)[0].strip()
        if raw and content_type != "application/x-www-form-urlencoded":
            raise RequestError(415, "use application/x-www-form-urlencoded")
        if re.search(r"%(?![0-9a-fA-F]{2})", raw):
            raise RequestError(400, "invalid form encoding")
        try:
            fields = urllib.parse.parse_qs(raw, keep_blank_values=True, max_num_fields=100, errors="strict")
        except ValueError:
            raise RequestError(400, "invalid form encoding or too many fields") from None
        if any(len(values) != 1 for values in fields.values()):
            raise RequestError(400, "duplicate form field")
        return {key: values[0] for key, values in fields.items()}

    def _trusted_origin(self):
        origins = self.headers.get_all("Origin", [])
        hosts = self.headers.get_all("Host", [])
        if len(origins) > 1 or len(hosts) > 1:
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        if not origins:
            return True  # non-browser clients still need the session-bound CSRF token
        origin = origins[0]
        expected = os.environ.get("XTTP_ORIGIN") or (("https" if SESSION_COOKIE_SECURE else "http") + "://" + (hosts[0] if hosts else ""))
        def normalized(value):
            parsed = urllib.parse.urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return None
            return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            return normalized(origin) is not None and normalized(origin) == normalized(expected)
        except ValueError:
            return False

    def _mutation_user(self):
        user = self._auth_user()
        if not user:
            raise RequestError(401, "auth required")
        csrf = session_csrf(self._token())
        provided = self.headers.get("X-CSRF-Token", "")
        if not self._trusted_origin() or not csrf or not hmac.compare_digest(csrf, provided):
            raise RequestError(403, "same-origin CSRF token required")
        return user

    def _error(self, ex):
        if isinstance(ex, RequestError):
            code = ex.code
        elif isinstance(ex, (ValueError, TypeError, KeyError)):
            code = 400
        else:
            code = 503
        if isinstance(ex, RequestError) and code in (400, 408, 413, 415):
            self.close_connection = True
        result = {"ok": False, "error": safe_diagnostic(str(ex))[:500]}
        if getattr(ex, "errors", None):
            result["errors"] = ex.errors
        self._json(code, result)

    def do_GET(self):
        try:
            self._get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            self._error(ex)

    do_HEAD = do_GET

    def _static(self, path):
        name = path.removeprefix("/static/")
        if name not in STATIC_TYPES:
            raise RequestError(404, "static file not found")
        body, etag = load_ui_bundle()["static"][name]
        validators = [part.strip() for part in self.headers.get("If-None-Match", "").split(",")]
        unchanged = "*" in validators or any(value.removeprefix("W/") == etag for value in validators)
        self.send_response(304 if unchanged else 200)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", STATIC_TYPES[name])
        self.send_header("X-XTTP-Version", VERSION)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not unchanged and self.command != "HEAD":
            self.wfile.write(body)

    def _get(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query, max_num_fields=20)
        q = lambda name, default="": query.get(name, [default])[0]
        if path.startswith("/static/"):
            return self._static(path)
        user = self._auth_user()
        if path == "/login":
            return self._redirect() if user else self._need_auth()
        if not user:
            if path.startswith("/api/"):
                raise RequestError(401, "auth required")
            return self._redirect("/login")
        if path == "/":
            return self._redirect()
        pages = {"/rules", "/monitoring/traffic", "/monitoring/connections", "/monitoring/logs",
                 "/system/core", "/system/connection", "/system/updates", "/system/users", "/system/activity"}
        if path in pages or re.fullmatch(r"/rules/[A-Za-z0-9_-]{1,80}", path):
            return self._send(200, page(user), "text/html; charset=utf-8")
        if path == "/api/session":
            return self._json(200, {"ok": True, "user": public_user(find_user(user)),
                                    "csrf": session_csrf(self._token()), "version": VERSION,
                                    "capabilities": source_capabilities()})
        if path == "/api/groups":
            with _groups_lock:
                groups = _load_groups_unlocked()
                return self._json(200, {"ok": True, "groups": [group_summary(group) for group in groups], "state": rules_state(groups)})
        if path.startswith("/api/groups/"):
            return self._json(200, group_detail(urllib.parse.unquote(path[len("/api/groups/"):]), q("offset", "0"), q("limit", "50"), q("q")))
        if path == "/api/rules/state":
            return self._json(200, {"ok": True, **rules_state()})
        if path.startswith("/api/operations/"):
            return self._json(200, {"ok": True, "operation": operation_snapshot(path[len("/api/operations/") :])})
        if path == "/api/users":
            return self._json(200, {"ok": True, "me": user, "users": [public_user(u) for u in load_users()]})
        if path == "/api/activity":
            return self._json(200, {"ok": True, "items": load_activity()[:80]})
        if path == "/api/prefs":
            return self._json(200, {"ok": True, "prefs": load_prefs()})
        if path == "/api/services/status":
            return self._json(200, services_status())
        if path == "/api/xttp/status":
            return self._json(200, xttp_status())
        if path == "/api/xttp/backups":
            return self._json(200, {"ok": True, "backups": list_xray_backups()})
        if path == "/api/detect":
            return self._json(200, detect_snapshot(q("text"), q("detect", "auto")))
        if path == "/api/explain-route":
            return self._json(200, explain_route(q("q") or q("target") or q("query")))
        if path == "/api/stats/snapshot":
            return self._json(200, stats_snapshot(float(q("since")) if q("since") else None, q("epoch")))
        if path == "/api/connections":
            return self._json(200, connections_snapshot())
        if path == "/api/logs":
            return self._json(200, logs_snapshot(q("level", "all"), int(q("since", "0")), int(q("limit", "200")), q("mode", "tail"), q("epoch")))
        if path == "/api/core/configs":
            return self._json(200, core_configs_snapshot())
        if path == "/api/core/versions":
            return self._json(200, {**components_versions(force=q("force") == "1"), "fleet_timer": fleet_timer_status()})
        if path == "/api/core/versions/check":
            return self._json(200, check_component_update(q("component"), force=q("force") == "1"))
        if path == "/api/changelog":
            return self._json(200, changelog_snapshot(force=q("force") == "1"))
        if path == "/api/rules/overview":
            return self._json(200, rules_overview_snapshot(q("q")))
        raise RequestError(404, "not found")

    def do_DELETE(self):
        try:
            self._form()  # validate body framing for every mutating method
            user = self._mutation_user()
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/connections":
                result = mihomo_delete("/connections")
            elif path.startswith("/api/connections/"):
                cid = urllib.parse.unquote(path[len("/api/connections/"):])
                if not cid or "/" in cid:
                    raise ValueError("bad connection id")
                result = mihomo_delete("/connections/" + urllib.parse.quote(cid, safe=""))
            else:
                raise RequestError(404, "not found")
            log_activity(user, "conn_kill", path[:100], ok=result.get("ok", True))
            self._json(200 if result.get("ok", True) else 503, result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            self._error(ex)

    def do_POST(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            form = self._form()
            if path == "/login":
                if not self._trusted_origin():
                    raise RequestError(403, "trusted Origin required")
                with _users_lock:
                    username = form.get("user", "").strip()
                    password = form.get("pass", "")
                    record = find_user(username)
                    if not record or not record.get("enabled", True) or not verify_password(password, record.get("password_hash", "")):
                        return self._need_auth("Неверный логин или пароль")
                    users = load_users()
                    for user in users:
                        if user["id"] == record["id"]:
                            user["last_login"] = utc_now()
                    save_users(users)
                    return self._redirect(token=make_session_token(username))
            user = self._mutation_user()
            if path == "/logout":
                revoke_session_token(self._token())
                if "application/json" in self.headers.get("Accept", ""):
                    return self._json(200, {"ok": True}, {"Set-Cookie": self._cookie(clear=True)})
                return self._redirect("/login", clear=True)
            if path.startswith("/api/users/"):
                with _users_lock:
                    result = user_action(path.removeprefix("/api/users/"), form, user)
                return self._json(200, result)
            if path.startswith("/api/groups/"):
                result = mutate_groups(path.removeprefix("/api/groups/"), form)
                log_activity(user, "groups", path.rsplit("/", 1)[-1])
                return self._json(200, result)
            if path == "/api/rules/apply":
                if form.get("confirm") != "1":
                    raise ValueError("confirm required")
                result = activate_rules()
                log_activity(user, "apply", "rule-providers activation", ok=result["ok"])
                return self._json(200 if result["ok"] else 400, result)
            if path == "/api/updates/start":
                if form.get("confirm") != "1":
                    raise ValueError("confirm required")
                result = start_operation(form.get("component", ""), user, form.get("target_version") or None)
                return self._json(202 if result["ok"] else 503, result)
            if path == "/api/logs/level":
                set_log_level(form.get("level", ""))
                return self._json(200, {"ok": True, "level": _log_level_wanted})
            if path == "/api/detect":
                return self._json(200, detect_snapshot(form.get("text", ""), form.get("detect") or "auto"))
            if path == "/api/prefs/set":
                prefs = load_prefs()
                if "auto_ping" in form:
                    prefs["auto_ping"] = _boolean(form["auto_ping"])
                if "auto_ping_sec" in form:
                    seconds = int(form["auto_ping_sec"])
                    if not 15 <= seconds <= 300:
                        raise ValueError("auto_ping_sec: 15–300")
                    prefs["auto_ping_sec"] = seconds
                save_prefs(prefs)
                log_activity(user, "prefs", "настройки сохранены")
                return self._json(200, {"ok": True, "prefs": prefs})
            if path == "/api/services/restart":
                name = form.get("service", "")
                restart_service(name)
                log_activity(user, "restart", name)
                return self._json(200, {"ok": True, "service": name, "status": services_status()})
            if path == "/api/core/configs":
                return self._json(200, core_configs_apply(form))
            if path == "/api/core/geo-update":
                return self._json(200, core_geo_update_now())
            if path == "/api/xttp/ping":
                status = xttp_status()
                info = status.get("outbound") or {}
                host, port = info.get("address") or "", int(info.get("port") or 0)
                tcp = tcp_probe(host, port) if host and port else {"ok": False, "error": "no endpoint", "latency_ms": None}
                http = socks_http_probe()
                return self._json(200, {"ok": True, "up": bool(status.get("xray_active") and tcp.get("ok") and http.get("ok")),
                    "xray_active": status.get("xray_active"), "mihomo_active": status.get("mihomo_active"),
                    "endpoint": f"{host}:{port}" if host else "", "tcp": tcp, "http": http, "services": services_status()})
            if path == "/api/xttp/speedtest":
                if not service_active("xray"):
                    raise ValueError("Xray не запущен")
                return self._json(200, socks_speedtest())
            if path == "/api/xttp/preview":
                parsed = parse_vless_link(form.get("vless", ""))
                preview = {key: parsed[key] for key in ("name", "address", "port", "network", "security", "sni", "path", "mode")}
                preview.update({"uuid_short": parsed["uuid"][:8] + "…", "public_key_short": parsed["public_key"][:14] + "…" if parsed["public_key"] else ""})
                return self._json(200, {"ok": True, "preview": preview})
            if path == "/api/xttp/apply":
                with _xray_lock:
                    parsed = parse_vless_link(form.get("vless", ""))
                    result = activate_xray(build_xray_config_from_vless(parsed))
                if result["ok"]:
                    result.update({"endpoint": f"{parsed['address']}:{parsed['port']}", "status": xttp_status()})
                log_activity(user, "vless_apply", "Xray activation", ok=result["ok"])
                return self._json(200 if result["ok"] else 400, result)
            if path == "/api/xttp/rollback":
                result = rollback_xray(form.get("name", ""))
                if result["ok"]:
                    result["status"] = xttp_status()
                log_activity(user, "vless_rollback", "Xray restore", ok=result["ok"])
                return self._json(200 if result["ok"] else 400, result)
            raise RequestError(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as ex:
            self._error(ex)




def fleet_health_ok() -> tuple[bool, str]:
    """Topbar-style health: mihomo + xray + API/socks."""
    if not service_active("mihomo"):
        return False, "mihomo inactive"
    if not service_active("xray"):
        return False, "xray inactive"
    try:
        mihomo_get("/version", timeout=3.0)
    except Exception as ex:
        return False, f"mihomo api: {ex}"
    try:
        with socket.create_connection((SOCKS_HOST, int(SOCKS_PORT)), timeout=2.5):
            pass
    except Exception as ex:
        return False, f"xray socks: {ex}"
    return True, "ok"


def fleet_apply_from_manifest(manifest: dict, repo: str, initiator: str = "автообновление (timer)") -> dict:
    if not _update_lock.acquire(blocking=False):
        return {"ok": False, "error": "обновление уже выполняется", "actions": [], "panel_update": False}
    try:
        return _fleet_apply_from_manifest(manifest, repo, initiator)
    finally:
        _update_lock.release()


def _fleet_apply_from_manifest(
    manifest: dict,
    repo: str,
    initiator: str = "автообновление (timer)",
) -> dict:
    """Compare to version-manifest; update mihomo/xray via update_component_binary; flag panel."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    for component in ("panel", "mihomo", "xray"):
        if manifest.get(component) and not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?", str(manifest[component])):
            raise ValueError("invalid manifest version: " + component)
    report: dict = {
        "ok": True,
        "skipped": False,
        "panel_update": False,
        "actions": [],
        "initiator": initiator,
    }
    ok, reason = fleet_health_ok()
    if not ok:
        try:
            log_activity(
                initiator,
                "fleet_skip",
                f"пропущено: система нездорова, требуется ручная проверка ({reason})",
                ok=False,
            )
        except Exception:
            pass
        report["ok"] = False
        report["skipped"] = True
        report["reason"] = reason
        return report

    vers = components_versions(force=True)
    any_bin_change = False

    for comp in ("mihomo", "xray"):
        want = _parse_version_token(str(manifest.get(comp) or ""))
        have = _parse_version_token(str((vers.get(comp) or {}).get("version") or ""))
        if want and not have:
            report["ok"] = False
            report["actions"].append({"component": comp, "want": want, "have": have,
                                      "result": {"ok": False, "error": "current version unavailable"}})
            continue
        if want and have and _is_remote_newer(want, have):
            r = update_component_binary(
                comp, user=initiator, target_version=str(manifest.get(comp))
            )
            if not r.get("ok"):
                report["ok"] = False
            else:
                any_bin_change = True
            report["actions"].append(
                {"component": comp, "want": want, "have": have, "result": r}
            )
        else:
            report["actions"].append(
                {"component": comp, "want": want, "have": have, "result": "up_to_date"}
            )

    # Panel signal = version-manifest "panel" vs VERSION (semver).
    # Do not use git behind alone: diverged origin history would false-trigger update.sh.
    want_panel = _parse_version_token(str(manifest.get("panel") or ""))
    have_panel = _parse_version_token(VERSION)
    behind = 0
    try:
        r = subprocess.run(
            ["git", "-C", repo, "rev-list", "--count", "HEAD..origin/main"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        behind = int((r.stdout or "0").strip() or "0")
    except Exception:
        behind = 0

    if want_panel and _is_remote_newer(want_panel, have_panel):
        report["panel_update"] = True
        report["actions"].append(
            {
                "component": "panel",
                "want": want_panel,
                "have": have_panel,
                "behind": behind,
                "result": "needs_update",
            }
        )
    else:
        report["actions"].append(
            {
                "component": "panel",
                "want": want_panel,
                "have": have_panel,
                "behind": behind,
                "result": "up_to_date",
            }
        )

    if not report["panel_update"] and not any_bin_change and report["ok"]:
        try:
            log_activity(initiator, "fleet_check", "версии актуальны", ok=True)
        except Exception:
            pass
    if not report["ok"]:
        report["error"] = "one or more fleet components failed"
    return report


_operation_context = threading.local()


def _operation_path(operation_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise RequestError(404, "operation not found")
    return os.path.join(DATA_DIR, "operations", operation_id + ".json")


def _load_operation(operation_id: str) -> dict:
    try:
        with open(_operation_path(operation_id), encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        raise RequestError(404, "operation not found") from None


def _save_operation(record: dict) -> None:
    record["updated_at"], record["_updated_ts"] = utc_now(), time.time()
    for key in ("result", "error", "rollback"):
        record[key] = safe_diagnostic(record.get(key))
    atomic_json(_operation_path(record["id"]), record)


def _create_operation(component: str, user: str, target_version=None, manifest=None, repo=None) -> dict:
    if component not in ("panel", "mihomo", "xray", "fleet"):
        raise ValueError("unknown component")
    if target_version and (not isinstance(target_version, str) or not re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?", target_version)):
        raise ValueError("invalid target version")
    if component == "fleet" and target_version:
        raise ValueError("fleet uses version-manifest, not one target version")
    operation_id = uuid.uuid4().hex
    record = {"id": operation_id, "component": component, "status": "queued", "stage": "accepted",
              "created_at": utc_now(), "updated_at": utc_now(), "result": None, "error": "",
              "rollback": {"status": "not_attempted"}, "_created_ts": time.time(),
              "_user": user, "_target_version": target_version, "_manifest": manifest,
              "_repo": repo or os.environ.get("XTTP_GIT_REPO", XTTP_GIT_REPO),
              "_unit": "xttp-operation-" + operation_id + ".service"}
    with _operation_lock:
        _save_operation(record)
    return record


def operation_stage(stage: str) -> None:
    operation_id = getattr(_operation_context, "id", None)
    if operation_id:
        try:
            with _operation_lock:
                record = _load_operation(operation_id)
                record["stage"] = stage
                _save_operation(record)
        except Exception as ex:
            # Recovery must still run when a progress/journal write fails.
            print(f"operation stage: {ex}", file=sys.stderr)


def start_operation(component: str, user: str = "admin", target_version=None) -> dict:
    if component == "panel" and not source_capabilities()["remote_panel_update"]:
        raise ValueError("local-only source: remote panel update unavailable")
    record = _create_operation(component, user, target_version)
    operation_id = record["id"]
    env_names = ("XTTP_GIT_REPO", "XTTP_GITHUB_REPO", "XTTP_GITHUB_BRANCH", "XTTP_UPDATE_LOCK",
                 "XTTP_HOST", "XTTP_COOKIE_SECURE", "XTTP_ORIGIN")
    command = ["systemd-run", "--unit=" + record["_unit"], "--collect", "--property=Type=exec",
               "--property=KillMode=control-group"]
    for name in env_names:
        if name in os.environ:
            command.append("--setenv=" + name + "=" + os.environ[name])
    command += [sys.executable, os.path.abspath(__file__), "--operation-run", operation_id]
    try:
        launched = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if launched.returncode:
            raise RuntimeError((launched.stderr or launched.stdout or "systemd worker launch failed")[-500:])
    except Exception as ex:
        with _operation_lock:
            current = _load_operation(operation_id)
            if current["status"] != "queued":
                return {"ok": True, "operation_id": operation_id}
            if current["status"] == "queued":
                current.update({"status": "failed", "stage": "launch-failed", "error": str(ex),
                                "result": {"ok": False, "error": str(ex)}})
                _save_operation(current)
        return {"ok": False, "operation_id": operation_id, "error": str(ex)}
    return {"ok": True, "operation_id": operation_id}


def _worker_alive(record: dict) -> bool:
    pid = record.get("_pid")
    if pid:
        try:
            os.kill(int(pid), 0)
            if os.name != "nt" and record.get("_process_start"):
                with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
                    if stream.read().rsplit(")", 1)[1].split()[19] != record["_process_start"]:
                        return False
            return True
        except (OSError, ValueError):
            return False
    try:
        result = subprocess.run(["systemctl", "is-active", record["_unit"]],
                                capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and (result.stdout or "").strip() in ("active", "activating")
    except (OSError, subprocess.TimeoutExpired):
        return False


def operation_snapshot(operation_id: str) -> dict:
    with _operation_lock:
        record = _load_operation(operation_id)
        if record["status"] in ("queued", "running"):
            age = time.time() - record.get("_created_ts", 0)
            if (record["status"] == "running" or age > 60) and not _worker_alive(record):
                attempted = record.get("stage", "").startswith(("activating-", "recovering-", "publishing-", "installing-", "restarting-"))
                if attempted:
                    record["rollback"] = {"status": "failed", "error": "worker interrupted before recovery verification"}
                record.update({"status": "failed", "stage": "interrupted",
                               "error": "worker interrupted before a verified terminal result",
                               "result": {"ok": False, "error": "worker interrupted"}})
                _save_operation(record)
        return {key: record.get(key) for key in
                ("id", "component", "status", "stage", "created_at", "updated_at", "result", "error", "rollback")}


def run_operation(operation_id: str) -> int:
    with _operation_lock:
        record = _load_operation(operation_id)
        if record["status"] in ("succeeded", "failed"):
            return 0 if record["status"] == "succeeded" else 1
        if record["status"] == "running" and _worker_alive(record):
            return 1
        record.update({"status": "running", "stage": "waiting-lock", "_pid": os.getpid()})
        if os.name != "nt":
            with open(f"/proc/{os.getpid()}/stat", encoding="ascii") as stream:
                record["_process_start"] = stream.read().rsplit(")", 1)[1].split()[19]
        _save_operation(record)
    _operation_context.id = operation_id
    held = False
    previous_repo = os.environ.get("XTTP_GIT_REPO")
    os.environ["XTTP_GIT_REPO"] = record["_repo"]
    try:
        held = _update_lock.acquire(blocking=False)
        if not held:
            result = {"ok": False, "error": "обновление уже выполняется"}
        else:
            component, user = record["component"], record["_user"]
            if component == "panel":
                result = update_component_panel(user, target_version=record.get("_target_version"))
            elif component in ("mihomo", "xray"):
                result = update_component_binary(component, user, target_version=record.get("_target_version"))
            else:
                operation_stage("checking-manifest")
                manifest = record.get("_manifest")
                if manifest is None:
                    manifest = _github_panel_remote().get("manifest")
                if not isinstance(manifest, dict):
                    raise RuntimeError("version-manifest unavailable")
                result = fleet_apply_from_manifest(manifest, record["_repo"], user)
                if result.get("panel_update"):
                    panel = update_component_panel(user, target_version=manifest.get("panel") or None)
                    result["actions"].append({"component": "panel", "result": panel})
                    result["ok"] = bool(result.get("ok") and panel.get("ok"))
                    if not panel.get("ok"):
                        result["error"] = panel.get("error") or "panel update failed"
                if not result.get("ok") and not result.get("error"):
                    result["error"] = result.get("reason") or "one or more fleet components failed"
    except Exception as ex:
        result = {"ok": False, "error": str(ex)[:500]}
    finally:
        if held:
            _update_lock.release()
        _operation_context.id = None
        if previous_repo is None:
            os.environ.pop("XTTP_GIT_REPO", None)
        else:
            os.environ["XTTP_GIT_REPO"] = previous_repo
    with _operation_lock:
        record = _load_operation(operation_id)
        rollback = result.get("rollback") or {"status": "not_attempted"}
        if record["component"] == "fleet":
            rollbacks = [action["result"].get("rollback") for action in result.get("actions", [])
                         if isinstance(action.get("result"), dict) and action["result"].get("rollback")]
            if rollbacks:
                rollback = {"status": "failed" if any(r.get("status") == "failed" for r in rollbacks) else "succeeded",
                            "components": rollbacks}
        record.update({"status": "succeeded" if result.get("ok") else "failed", "stage": "completed",
                       "result": result, "error": result.get("error") or "", "rollback": rollback})
        _save_operation(record)
    log_activity(record["_user"], "operation", f"{record['component']}: {record['status']}", ok=bool(result.get("ok")))
    return 0 if result.get("ok") else 1


def fleet_cli(argv: list[str]) -> int:
    try:
        cmd = ""
        manifest_path = ""
        repo = os.environ.get("XTTP_GIT_REPO", "/opt/xttp-stack")
        reason = ""
        i = 0
        while i < len(argv):
            a = argv[i]
            if a == "--fleet-cmd" and i + 1 < len(argv):
                cmd = argv[i + 1]
                i += 2
                continue
            if a == "--manifest" and i + 1 < len(argv):
                manifest_path = argv[i + 1]
                i += 2
                continue
            if a == "--repo" and i + 1 < len(argv):
                repo = argv[i + 1]
                i += 2
                continue
            if a == "--reason" and i + 1 < len(argv):
                reason = argv[i + 1]
                i += 2
                continue
            i += 1

        initiator = os.environ.get("XTTP_FLEET_INITIATOR", "автообновление (timer)")

        if cmd == "health":
            ok, msg = fleet_health_ok()
            print(msg)
            return 0 if ok else 2

        if cmd == "log-skip":
            try:
                log_activity(
                    initiator,
                    "fleet_skip",
                    f"пропущено: {reason or 'система нездорова, требуется ручная проверка'}",
                    ok=False,
                )
            except Exception:
                pass
            return 0

        if cmd == "log-panel":
            want = ""
            if manifest_path and os.path.isfile(manifest_path):
                try:
                    m = json.load(open(manifest_path, encoding="utf-8"))
                    want = str(m.get("panel") or "")
                except Exception:
                    pass
            try:
                log_activity(
                    initiator,
                    "fleet_update",
                    f"panel обновлена (update.sh) → {want or VERSION}",
                    ok=True,
                )
            except Exception:
                pass
            return 0

        if cmd == "run":
            if not manifest_path:
                raise ValueError("manifest missing")
            with open(manifest_path, encoding="utf-8") as stream:
                manifest = json.load(stream)
            record = _create_operation("fleet", initiator, manifest=manifest, repo=repo)
            code = run_operation(record["id"])
            print(json.dumps({"ok": code == 0, "operation_id": record["id"],
                              "operation": operation_snapshot(record["id"])}, ensure_ascii=False))
            return code

        if cmd == "apply":
            if not manifest_path or not os.path.isfile(manifest_path):
                print(json.dumps({"ok": False, "error": "manifest missing"}, ensure_ascii=False))
                return 1
            try:
                manifest = json.load(open(manifest_path, encoding="utf-8"))
            except Exception as ex:
                print(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False))
                return 1
            report = fleet_apply_from_manifest(manifest, repo=repo, initiator=initiator)
            print(json.dumps(report, ensure_ascii=False))
            if not report.get("ok"):
                return 1
            if report.get("panel_update"):
                return 10
            return 0

        print(json.dumps({"ok": False, "error": f"unknown fleet cmd: {cmd}"}, ensure_ascii=False))
        return 1
    except Exception as ex:
        print(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False))
        return 1


def main():
    load_ui_bundle()
    os.makedirs(os.path.dirname(GROUPS_FILE), exist_ok=True)
    os.makedirs(GROUPS_DIR, exist_ok=True)
    harden_xray_credentials()
    start_traffic_sampler()  # 1 Hz traffic rates for stats chart
    start_log_sampler(load_prefs().get("log_level", "info"))
    initialize_users()
    if not os.path.exists(GROUPS_FILE):
        load_groups()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("xttp panel http://%s:%s" % (HOST, PORT), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--operation-run":
        try:
            raise SystemExit(run_operation(sys.argv[2]))
        except Exception as ex:
            print(f"operation failed: {ex}", file=sys.stderr)
            raise SystemExit(1)
    if len(sys.argv) > 1 and sys.argv[1] == "--fleet-cmd":
        raise SystemExit(fleet_cli(sys.argv[1:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--prepare-config":
        try:
            prepare_config()
        except Exception as ex:
            print(f"prepare-config failed: {ex}", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)
    main()
