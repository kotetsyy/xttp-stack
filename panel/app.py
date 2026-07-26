#!/usr/bin/env python3
"""xttp panel — groups routing + users + xttp connection settings"""
from __future__ import annotations

import hashlib
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
import urllib.parse
import urllib.request
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "0.0.0.0", 9080
GROUPS_FILE = "/etc/mihomo/rule-providers/groups.json"


# Optional git versioning of groups (non-blocking). Repo path e.g. /opt/xttp-stack
XTTP_GIT_REPO = os.environ.get("XTTP_GIT_REPO", "/opt/xttp-stack").rstrip("/")
XTTP_GIT_COMMIT = os.environ.get("XTTP_GIT_COMMIT", "1").strip() not in ("0", "false", "no", "off")
XTTP_GIT_GROUPS_REL = "groups/groups.json"
XTTP_GITHUB_REPO = os.environ.get("XTTP_GITHUB_REPO", "kotetsyy/xttp-stack").strip()
XTTP_GITHUB_BRANCH = os.environ.get("XTTP_GITHUB_BRANCH", "main").strip() or "main"


def _git_commit_groups_async(reason: str = "save", detail: str = "") -> None:
    """Background git commit of groups.json — never fails the save path."""
    if not XTTP_GIT_COMMIT:
        return
    try:
        threading.Thread(
            target=_git_commit_groups,
            args=(reason, detail),
            name="git-groups-commit",
            daemon=True,
        ).start()
    except Exception:
        pass


def _git_commit_groups(reason: str = "save", detail: str = "") -> None:
    try:
        repo = XTTP_GIT_REPO
        if not repo or not os.path.isdir(os.path.join(repo, ".git")):
            return
        dest = os.path.join(repo, XTTP_GIT_GROUPS_REL.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        src = GROUPS_FILE
        if not os.path.isfile(src):
            return
        try:
            if os.path.realpath(src) != os.path.realpath(dest):
                shutil.copy2(src, dest)
        except Exception:
            shutil.copy2(src, dest)

        rel = XTTP_GIT_GROUPS_REL
        msg = (detail or reason or "save").strip()
        msg = re.sub(r"\s+", " ", msg)[:180]
        if not msg.lower().startswith("update rules"):
            msg = f"update rules: {msg}"
        # nothing to commit?
        st = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain", "--", rel],
            capture_output=True, text=True, timeout=15,
        )
        if not (st.stdout or "").strip():
            return
        subprocess.run(
            ["git", "-C", repo, "add", "--", rel],
            capture_output=True, text=True, timeout=15, check=False,
        )
        subprocess.run(
            ["git", "-C", repo, "commit", "-m", msg, "--", rel],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except Exception:
        pass

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
SESSION_SECRET_FILE = os.path.join(DATA_DIR, "session.secret")
ACTIVITY_FILE = os.path.join(DATA_DIR, "activity.json")
PREFS_FILE = os.path.join(DATA_DIR, "prefs.json")
TRAFFIC_HISTORY_FILE = os.path.join(DATA_DIR, "traffic_history.json")
TRAFFIC_HISTORY_MAX = 3700  # ~1h @ 1 sample/sec
TRAFFIC_HISTORY_SAVE_EVERY = 30  # persist to disk every N samples
XRAY_CONFIGS = ["/etc/xray/config.json", "/usr/local/etc/xray/config.json"]
XRAY_BACKUP_DIR = os.path.join(DATA_DIR, "xray-backups")
SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 11090
# bootstrap defaults (migrated into users.json on first run)
BOOTSTRAP_USER, BOOTSTRAP_PASS = "admin", "changeme"  # change after first login
RESTART_CMD = ["systemctl", "restart", "mihomo"]
RESTART_XRAY_CMD = ["systemctl", "restart", "xray"]
ACTIVITY_MAX = 100
DASH_URL = ""  # optional; MetaCube UI not required
VERSION = "0.18.1"
MIHOMO_API = "http://127.0.0.1:9090"
PAGE_HTML = '<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="utf-8"/>\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>\n<title>xttp panel</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"/>\n<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>\n<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>\n<style>\n:root{\n  --bg:#0b0c0f;--bg2:#12141a;--bg3:#181b22;--hover:#232833;\n  --line:rgba(255,255,255,.06);--line2:rgba(255,255,255,.1);\n  --text:#eceef2;--muted:#8b93a7;--muted2:#5c657a;\n  --acc:#3B82F6;--acc-d:rgba(59,130,246,.14);--acc-g:rgba(59,130,246,.28);\n  --ok:#22C55E;--ok-bg:rgba(34,197,94,.14);--err:#EF4444;--err-bg:rgba(239,68,68,.12);\n  /* ops / activity semantic tokens (not inline) */\n  --ops-stat-ok:var(--ok);\n  --ops-stat-err:var(--err);\n  --ops-stat-gray:#64748B;\n  --act-dot-ok:var(--ok);\n  --act-dot-danger:var(--err);\n  --act-dot-neutral:var(--acc);\n  --font:"Inter",ui-sans-serif,system-ui,sans-serif;--mono:"JetBrains Mono",ui-monospace,monospace;\n  /* tag palette (fixed by type) */\n  --tag-v4-fg:#052e16;--tag-v4-bg:#4ADE80;\n  --tag-v6-fg:#2e1065;--tag-v6-bg:#A78BFA;\n  --tag-ns-fg:#0c4a6e;--tag-ns-bg:#38BDF8;\n  --tag-dom-fg:#3b0764;--tag-dom-bg:#D8B4FE;\n  --tag-kw-fg:#431407;--tag-kw-bg:#FDBA74;\n  --tag-raw-fg:#1e3a5f;--tag-raw-bg:rgba(59,130,246,.25);\n  /* left stripe by category */\n  --stripe-default:#64748B;\n  --stripe-messenger:#3B82F6;\n  --stripe-video:#8B5CF6;\n  --stripe-infra:#14B8A6;\n  --stripe-off:rgba(100,116,139,.45);\n  /* empty / secondary */\n  --empty-opacity:.4;\n  --tool-opacity:.55;\n  /* 60fps-friendly motion tokens (transform/opacity first) */\n  --ease-out:cubic-bezier(.22,1,.36,1);\n  --ease-inout:cubic-bezier(.4,0,.2,1);\n  --ease-spring:cubic-bezier(.16,1,.3,1);\n  --dur-micro:120ms;\n  --dur-fast:180ms;\n  --dur-med:260ms;\n  --dur-slow:360ms;\n  --sh:0 0 0 1px var(--line),0 12px 40px rgba(0,0,0,.4);\n  --sh-hover:0 0 0 1px var(--line2),0 14px 36px rgba(0,0,0,.32);\n}\n*{box-sizing:border-box}\nhtml,body{height:100%}\nbody{margin:0;font-family:var(--font);background:var(--bg);color:var(--text);line-height:1.45;-webkit-font-smoothing:antialiased;position:relative}\nbutton,input,select,textarea{font:inherit;color:inherit}\na{color:inherit;text-decoration:none}\n/* premium ambient mesh — body-level, under .app (z-index 0); modals use solid scrim above */\n.bg-fx{\n  position:fixed;inset:0;z-index:0;pointer-events:none;overflow:hidden;\n  /* base depth: not pure flat black */\n  background:\n    radial-gradient(120% 90% at 0% 0%, #0c1a32 0%, transparent 55%),\n    radial-gradient(100% 80% at 100% 100%, #140e28 0%, transparent 50%),\n    radial-gradient(80% 60% at 50% 100%, #0a1820 0%, transparent 45%),\n    var(--bg);\n}\n.bg-fx .mesh{\n  position:absolute;inset:-30%;pointer-events:none;will-change:transform;\n  transform:translate3d(0,0,0);backface-visibility:hidden;\n  /* no paint containment — blurred layers must composite freely */\n  contain:none;\n}\n.bg-fx.is-paused .mesh,.bg-fx.is-paused .grid{animation-play-state:paused !important}\n.bg-fx .mesh-a{\n  background:\n    radial-gradient(ellipse 52% 46% at 10% 18%, rgba(59,130,246,.28) 0%, transparent 60%),\n    radial-gradient(ellipse 46% 42% at 90% 80%, rgba(139,92,246,.22) 0%, transparent 58%),\n    radial-gradient(ellipse 40% 36% at 70% 10%, rgba(20,184,166,.14) 0%, transparent 55%),\n    radial-gradient(ellipse 55% 48% at 35% 95%, rgba(59,130,246,.16) 0%, transparent 58%);\n  filter:blur(32px);\n  opacity:1;\n  animation:meshDriftA 28s var(--ease-inout) infinite alternate;\n}\n.bg-fx .mesh-b{\n  background:\n    radial-gradient(ellipse 44% 40% at 82% 16%, rgba(96,165,250,.18) 0%, transparent 58%),\n    radial-gradient(ellipse 50% 44% at 18% 74%, rgba(167,139,250,.16) 0%, transparent 56%),\n    radial-gradient(ellipse 34% 30% at 55% 45%, rgba(45,212,191,.08) 0%, transparent 52%);\n  filter:blur(40px);\n  mix-blend-mode:screen;\n  opacity:.75;\n  animation:meshDriftB 32s var(--ease-inout) infinite alternate;\n}\n.bg-fx .grid{\n  position:absolute;inset:0;opacity:.34;\n  background-image:\n    linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),\n    linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);\n  background-size:56px 56px;\n  mask-image:radial-gradient(ellipse 90% 75% at 50% 30%,#000 0%,transparent 75%);\n  -webkit-mask-image:radial-gradient(ellipse 90% 75% at 50% 30%,#000 0%,transparent 75%);\n  animation:gridBreathe 18s var(--ease-inout) infinite alternate;\n}\n.bg-fx .noise{\n  position:absolute;inset:0;opacity:.025;pointer-events:none;\n  background-image:url("data:image/svg+xml,%3Csvg viewBox=\'0 0 256 256\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.85\' numOctaves=\'4\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23n)\'/%3E%3C/svg%3E");\n  background-size:180px 180px;\n}\n.bg-fx .vignette{\n  position:absolute;inset:0;pointer-events:none;\n  background:radial-gradient(ellipse 80% 75% at 50% 40%, transparent 35%, rgba(0,0,0,.45) 100%);\n}\n@keyframes meshDriftA{\n  0%{transform:translate3d(0,0,0) scale(1)}\n  50%{transform:translate3d(1.8%,-1.2%,0) scale(1.03)}\n  100%{transform:translate3d(-1.4%,1.6%,0) scale(1.015)}\n}\n@keyframes meshDriftB{\n  0%{transform:translate3d(0,0,0) scale(1.04)}\n  50%{transform:translate3d(-2%,1.4%,0) scale(1)}\n  100%{transform:translate3d(1.5%,-1.8%,0) scale(1.05)}\n}\n@keyframes gridBreathe{from{opacity:.22}to{opacity:.34}}\n@keyframes riseIn{from{opacity:0;transform:translate3d(0,14px,0)}to{opacity:1;transform:translate3d(0,0,0)}}\n@keyframes modalIn{from{opacity:0;transform:translate3d(0,8px,0) scale(.96)}to{opacity:1;transform:translate3d(0,0,0) scale(1)}}\n@keyframes fadeIn{from{opacity:0}to{opacity:1}}\n@keyframes toastIn{from{opacity:0;transform:translate3d(-50%,-8px,0)}to{opacity:1;transform:translate3d(-50%,0,0)}}\n@keyframes shimmer{0%{background-position:100% 0}100%{background-position:-100% 0}}\n@keyframes rowOut{to{opacity:0;transform:translate3d(10px,0,0)}}\n\n.app{position:relative;z-index:1;min-height:100vh;max-width:1100px;margin:0 auto;padding:0 16px 56px;display:flex;flex-direction:column}\n/* no solid bar — only free-floating controls on ambient */\n.topbar{\n  position:sticky;top:0;z-index:30;\n  display:flex;align-items:center;justify-content:space-between;gap:12px;\n  padding:14px 0 10px;\n  background:transparent !important;\n  backdrop-filter:none !important;\n  -webkit-backdrop-filter:none !important;\n  border:0 !important;\n  box-shadow:none !important;\n}\n.brand{display:inline-flex;align-items:center;gap:10px;font-size:15px;font-weight:600;letter-spacing:-.02em;color:var(--text);text-shadow:0 1px 16px rgba(0,0,0,.5)}\n.brand svg{color:var(--acc);filter:drop-shadow(0 0 10px rgba(59,130,246,.25))}\n.top-right{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:500;color:var(--muted2);position:relative;z-index:40;flex-wrap:wrap}\n.top-right > a.chip,.top-right > button.chip,.top-right > #xttpDot{position:relative;z-index:3;pointer-events:auto !important}\n.top-right > button.chip{cursor:pointer !important;opacity:1 !important}\n/* individual pills only — no continuous header plate */\n.topbar .chip{\n  background:rgba(12,14,18,.55);\n  border-color:rgba(255,255,255,.07);\n  box-shadow:0 4px 16px rgba(0,0,0,.2);\n}\n.topbar .chip-status{background:rgba(12,14,18,.4)}\n.topbar .chip-status.up{background:var(--ok-bg)}\n.topbar .chip-status.down{background:var(--err-bg)}\n.topbar .chip-action{background:rgba(12,14,18,.5)}\n.topbar .chip:hover,.topbar button.chip:hover{background:rgba(28,32,40,.75)}\n\n/* interactive press/hover base */\n.chip,.icon-btn,.mini-btn,.btn,.stab,.modal-x,.icon-del{\n  transition:\n    transform var(--dur-micro) var(--ease-out),\n    background var(--dur-fast) var(--ease-inout),\n    color var(--dur-fast) var(--ease-inout),\n    border-color var(--dur-fast) var(--ease-inout),\n    box-shadow var(--dur-fast) var(--ease-inout),\n    opacity var(--dur-fast) var(--ease-inout);\n}\n.chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;border:1px solid var(--line);background:var(--bg2);color:var(--muted);cursor:default;font:inherit}\n.chip-status{opacity:.85;pointer-events:none}\n.chip-status.up{color:var(--ok);border-color:rgba(34,197,94,.25);background:var(--ok-bg)}\n.chip-status.down{color:var(--err);border-color:rgba(239,68,68,.25);background:var(--err-bg)}\na.chip,button.chip{cursor:pointer}\nbutton.chip.chip-action{background:var(--bg2);color:var(--text);border-color:var(--line2)}\nbutton.chip.chip-action:hover{background:var(--bg3);color:var(--text);transform:scale(1.02)}\na.chip:hover,button.chip:hover{background:var(--bg3);color:var(--text);transform:scale(1.02)}\na.chip:active,button.chip:active{transform:scale(.97)}\nbutton.chip{appearance:none}\n/* logout as quiet text link */\na.link-logout{\n  display:inline-flex;align-items:center;padding:6px 4px;margin-left:2px;\n  border:0;background:transparent;color:var(--muted2);font-size:12px;font-weight:500;\n  text-decoration:none;cursor:pointer;\n  transition:color var(--dur-micro) var(--ease-inout),opacity var(--dur-micro) var(--ease-inout);\n}\na.link-logout:hover{color:#f87171;text-decoration:underline;transform:none;background:transparent}\na.link-logout:active{transform:none;opacity:.8}\n\n.ver-wrap{position:relative;z-index:1}\n/* chip-status sets pointer-events:none — version btn must stay tappable */\n.ver-chip,#verChip,button.chip.ver-chip{\n  pointer-events:auto !important;\n  cursor:pointer !important;\n  opacity:1 !important;\n  -webkit-user-select:none;user-select:none;\n  -webkit-tap-highlight-color:rgba(59,130,246,.25);\n  touch-action:manipulation;\n  position:relative;z-index:5;\n}\n.ver-chip:hover,.ver-wrap:hover .ver-chip,.ver-wrap:focus-within .ver-chip,.ver-wrap.open .ver-chip{background:var(--bg3);color:var(--text);border-color:var(--line2)}\n.ver-dd{\n  position:absolute;top:calc(100% + 8px);left:0;right:auto;width:min(360px,calc(100vw - 32px));max-height:min(420px,70vh);\n  overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;touch-action:pan-y;-webkit-overflow-scrolling:touch;z-index:50;padding:10px 0;\n  background:#12141a;border:1px solid var(--line2);border-radius:14px;\n  box-shadow:0 16px 48px rgba(0,0,0,.65),0 0 0 1px rgba(255,255,255,.04) inset;\n  backdrop-filter:none;-webkit-backdrop-filter:none;\n  opacity:0;visibility:hidden;transform:translate3d(0,8px,0) scale(.98);pointer-events:none !important;\n  transition:opacity var(--dur-fast) var(--ease-out),transform var(--dur-fast) var(--ease-out),visibility var(--dur-fast);\n}\n/* open class ON the panel itself — works even when portaled to body */\n.ver-dd.is-open{\n  opacity:1 !important;visibility:visible !important;\n  transform:translate3d(0,0,0) scale(1) !important;\n  pointer-events:auto !important;\n  z-index:10050 !important;\n}\n.ver-backdrop{\n  display:none;position:fixed;inset:0;z-index:10040;\n  background:rgba(0,0,0,.45);-webkit-tap-highlight-color:transparent;\n  pointer-events:none;\n}\n.ver-backdrop.is-open{pointer-events:auto;}\n.ver-backdrop.is-open{display:block;pointer-events:auto;}\n.ver-wrap.open{z-index:300}\n.ver-wrap.open .ver-dd{\n  opacity:1;visibility:visible;transform:translate3d(0,0,0) scale(1);pointer-events:auto !important;\n  z-index:300;\n}\n/* hover-open only for mouse — sticky hover breaks iOS tap */\n@media (hover:hover) and (pointer:fine){\n  .ver-wrap:hover .ver-dd,.ver-wrap:focus-within .ver-dd{\n    opacity:1;visibility:visible;transform:translate3d(0,0,0) scale(1);pointer-events:auto !important;\n  }\n  .ver-wrap:hover,.ver-wrap:focus-within{z-index:300}\n}\n.ver-dd{scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.12) transparent}\n.ver-dd::-webkit-scrollbar{width:6px}\n.ver-dd::-webkit-scrollbar-track{background:transparent}\n.ver-dd::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:999px;border:2px solid transparent;background-clip:padding-box}\n.ver-dd::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.22)}\n.ver-dd::-webkit-scrollbar-corner{background:transparent}\n.ver-item{display:grid;grid-template-columns:14px 1fr;gap:10px;padding:10px 14px;border:0;background:transparent;transition:background var(--dur-micro) var(--ease-inout)}\n.ver-item:hover{background:rgba(255,255,255,.03)}\n.ver-dot{width:8px;height:8px;border-radius:50%;background:var(--muted2);margin-top:5px}\n.ver-item.current .ver-dot{background:var(--acc);box-shadow:0 0 0 3px var(--acc-d)}\n.ver-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px}\n.ver-tag{font-size:13px;font-weight:700;color:var(--text);letter-spacing:-.01em}\n.ver-item.current .ver-tag{color:var(--acc)}\n.ver-badge{font-size:9px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:2px 6px;border-radius:999px;background:var(--acc-d);color:var(--acc)}\n.ver-date{font-size:11px;color:var(--muted2);font-family:var(--mono)}\n.ver-sec{margin-top:6px}\n.ver-sec-title{font-size:11px;font-weight:700;color:#f5a524;margin:0 0 3px}\n.ver-sec-title.fix{color:var(--ok)}\n.ver-list{margin:0;padding:0 0 0 14px;font-size:12px;color:var(--muted);line-height:1.45}\n.ver-list li{margin:2px 0}\n.ver-list code{font-family:var(--mono);font-size:11px;color:#a5b4fc}\n\n#xttpDot.chip-status.up{color:var(--ok);border-color:rgba(34,197,94,.25);background:var(--ok-bg);opacity:1}\n#xttpDot.chip-status.down{color:var(--err);border-color:rgba(239,68,68,.25);background:var(--err-bg);opacity:1}\n\n/* settings + modals */\n.settings-bd,.modal-backdrop{\n  position:fixed;inset:0;z-index:200;display:none;align-items:center;justify-content:center;padding:16px;\n  background:transparent;\n  /* no opacity transition — intermediate alpha shows page through */\n  transition:none;\n  will-change:auto;\n}\n.modal-backdrop{z-index:180;align-items:flex-end}\n@media(min-width:640px){.modal-backdrop{align-items:center}}\n/* FULLY opaque scrim — alpha 1 only, no rgba override after solid */\n.settings-bd.open{\n  display:flex;\n  /* translucent dim — ambient mesh stays visible; page content hidden via body.settings-open */\n  background:rgba(6,8,14,.62);\n  opacity:1;\n  backdrop-filter:none !important;\n  -webkit-backdrop-filter:none !important;\n  animation:none;\n}\n/* hide MAIN CONTENT under Settings — not .app itself (settings may live inside .app;\n   visibility:hidden on parent still paints children with visibility:visible) */\nbody.settings-open .topbar,\nbody.settings-open .toolbar,\nbody.settings-open .view-stage,\nbody.settings-open #viewStage,\nbody.settings-open .foot-row,\nbody.settings-open .toast,\nbody.settings-open .ver-dd,\nbody.settings-open .ver-backdrop{\n  opacity:0 !important;\n  visibility:hidden !important;\n  pointer-events:none !important;\n}\n/* keep ambient mesh under scrim */\nbody.settings-open .bg-fx{\n  visibility:visible !important;\n  opacity:1 !important;\n  z-index:0 !important;\n  pointer-events:none !important;\n}\n/* settings shell + card fully solid/opaque */\n.settings-bd.open{\n  /* already set above — reinforce */\n}\n.settings-bd.open .settings-modal{\n  background:#12141a !important;\n  opacity:1 !important;\n  visibility:visible !important;\n}\n.modal-backdrop.open{\n  display:flex;\n  background:#12141a;\n  opacity:1;\n  backdrop-filter:none !important;\n  -webkit-backdrop-filter:none !important;\n  animation:none;\n}\n.settings-modal,.modal{\n  width:min(720px,100%);max-height:min(90vh,860px);\n  background:#12141a;\n  border:1px solid var(--line2);border-radius:18px;\n  box-shadow:0 24px 64px rgba(0,0,0,.72);\n  transform:translate3d(0,10px,0) scale(.96);opacity:0;\n  display:flex;flex-direction:column;overflow:hidden;\n}\n/* solid card — no contain:paint, no partial opacity when open */\n.settings-modal{\n  transform:none;\n  opacity:1;\n  will-change:auto;\n  isolation:auto;\n  contain:none;\n  background:#12141a;\n  overflow:hidden;\n}\n.settings-head h2{\n  transform:none !important;\n  filter:none !important;\n  text-shadow:none !important;\n  -webkit-font-smoothing:antialiased;\n  backface-visibility:hidden;\n}\n@keyframes settingsFadeIn{from{opacity:0}to{opacity:1}}\n.modal{\n  width:min(560px,100%);max-height:min(88vh,820px);padding:20px;overflow:auto;\n  display:block;\n  scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.12) transparent;\n}\n.settings-bd.open .settings-modal{\n  animation:none;\n  transform:none;\n  opacity:1 !important;\n  background:#12141a !important;\n}\n.modal-backdrop.open .modal{\n  animation:none;\n  transform:none;\n  opacity:1 !important;\n  background:#12141a !important;\n}\n@media (prefers-reduced-motion:reduce){\n  .settings-bd.open .settings-modal,.modal-backdrop.open .modal{animation:none;opacity:1 !important;transform:none}\n}\n.settings-modal h2,.modal h2{margin:0;font-size:1.15rem;font-weight:650;letter-spacing:-.02em}\n.modal h2{margin:0 0 4px;font-size:1.1rem}\n/* settings: title + tabs fixed, only body scrolls */\n.settings-head{\n  display:flex;align-items:center;justify-content:space-between;gap:10px;\n  flex-shrink:0;padding:16px 18px 10px;\n  background:var(--bg2);z-index:2;\n}\n.settings-head .modal-x{float:none;flex-shrink:0}\n.settings-nav{\n  flex-shrink:0;padding:0 18px 12px;\n  background:var(--bg2);border-bottom:1px solid var(--line);z-index:2;\n}\n.settings-head,.settings-nav,.settings-body,.spanels,.spanel{\n  background:#12141a;\n}\n.settings-body{\n  flex:1;min-height:0;overflow:auto;padding:16px 18px 20px;\n  scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.12) transparent;\n}\n.settings-body::-webkit-scrollbar,.modal::-webkit-scrollbar{width:6px}\n.settings-body::-webkit-scrollbar-track,.modal::-webkit-scrollbar-track{background:transparent}\n.settings-body::-webkit-scrollbar-thumb,.modal::-webkit-scrollbar-thumb{\n  background:rgba(255,255,255,.12);border-radius:999px;border:2px solid transparent;background-clip:padding-box;\n}\n.settings-body::-webkit-scrollbar-thumb:hover,.modal::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.22)}\n.settings-body::-webkit-scrollbar-corner,.modal::-webkit-scrollbar-corner{background:transparent}\n\n/* sliding tab pill */\n.stabs{position:relative;display:flex;gap:0;padding:4px;border-radius:999px;background:var(--bg);border:1px solid var(--line);width:fit-content;margin:0}\n/* main view switch (Rules / Stats) */\n.view-switch{flex-shrink:0;margin-left:4px}\n.view-stage{position:relative;min-height:200px}\n.view-panel{transition:opacity var(--dur-fast) var(--ease-inout),transform var(--dur-fast) var(--ease-out);will-change:opacity,transform;backface-visibility:hidden;transform:translate3d(0,0,0)}\n.view-panel.is-hiding{display:none !important}#panel-groups:not([hidden]),#panel-stats:not([hidden]){display:block !important;opacity:1 !important;visibility:visible !important;position:relative !important;transform:none !important;pointer-events:auto !important;}\n.view-panel[hidden]{display:none !important}\n@media (prefers-reduced-motion:reduce){.view-panel{transition:none}}\n.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:4px 0 14px}\n.stat-card{background:var(--bg2);border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--sh)}\n.stat-label{font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px}\n.stat-value{font-size:20px;font-weight:700;letter-spacing:-.02em;color:var(--text);font-variant-numeric:tabular-nums}\n.stat-sub{font-size:11px;color:var(--muted2);margin-top:4px}\n.stats-chart-wrap{background:var(--bg2);border:1px solid var(--line);border-radius:14px;padding:12px 14px 10px;margin-bottom:14px}\n.stats-chart-head{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:8px}\n.stats-chart-title{font-size:13px;font-weight:600;color:var(--text)}\n.stats-period{flex-shrink:0}\n.stats-period .stab{padding:6px 10px;font-size:12px}\n.stats-chart-body{position:relative}\n.stats-chart-legend{position:absolute;top:6px;right:8px;z-index:3;display:flex;gap:8px;font-size:11px;color:var(--muted2);padding:4px 8px;border-radius:999px;background:rgba(12,14,18,.78);border:1px solid var(--line);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);pointer-events:auto}\n.stats-line-toggle{display:inline-flex;align-items:center;gap:5px;cursor:pointer;user-select:none;color:var(--muted2);font-weight:500}\n.stats-line-toggle:hover{color:var(--text)}\n.stats-line-toggle input{appearance:none;-webkit-appearance:none;width:14px;height:14px;margin:0;border-radius:4px;border:1.5px solid var(--line2);background:var(--bg);display:grid;place-content:center;cursor:pointer;flex-shrink:0}\n.stats-line-toggle input:checked{background:var(--acc);border-color:var(--acc)}\n.stats-line-toggle input:checked:before{content:"";width:8px;height:5px;border-left:1.5px solid #fff;border-bottom:1.5px solid #fff;transform:rotate(-45deg) translate(0.5px,-0.5px)}\n.stats-line-toggle i{display:inline-block;width:8px;height:8px;border-radius:50%;flex-shrink:0}\n.stats-line-toggle .dn{background:#38bdf8}.stats-line-toggle .up{background:#a78bfa}\n.stats-line-toggle:has(input:not(:checked)){opacity:.45}\n#statsChart{width:100%;height:160px;display:block;cursor:crosshair}\n.stats-tooltip{position:absolute;z-index:4;pointer-events:none;min-width:128px;padding:8px 10px;border-radius:10px;font-size:12px;line-height:1.4;color:var(--text);background:#0c0e12;border:1px solid var(--line2);box-shadow:0 8px 24px rgba(0,0,0,.45);transform:translate(-50%,-100%);margin-top:-12px;white-space:nowrap}\n.stats-tooltip[hidden]{display:none !important}\n.stats-tooltip .tt-time{font-size:11px;color:var(--muted2);margin-bottom:4px;font-variant-numeric:tabular-nums}\n.stats-tooltip .tt-row{display:flex;align-items:center;gap:6px;font-variant-numeric:tabular-nums}\n.stats-tooltip .tt-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}\n.stats-tooltip .tt-dot.dn{background:#38bdf8}.stats-tooltip .tt-dot.up{background:#a78bfa}\n.stats-table-wrap{background:var(--bg2);border:1px solid var(--line);border-radius:14px;overflow:auto;max-height:min(52vh,480px);position:relative}\n.stats-table-wrap::-webkit-scrollbar{width:8px}\n.stats-table-wrap::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:99px}\n.stats-table thead th{position:sticky;top:0;z-index:3;background:var(--bg2);box-shadow:0 1px 0 var(--line);}\n.stats-table{width:100%;border-collapse:collapse;font-size:13px}\n.stats-table th{text-align:left;font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.03em;padding:10px 12px;border-bottom:1px solid var(--line);background:var(--bg2)}\n.stats-table td{padding:10px 12px;border-bottom:1px solid var(--line);color:var(--text);font-variant-numeric:tabular-nums}\n.stats-table tr:last-child td{border-bottom:0}\n.stats-table tr.is-dim{opacity:.35}\n.stats-table .bar{height:4px;border-radius:99px;background:rgba(255,255,255,.06);overflow:hidden;min-width:60px}\n.stats-table .bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),#a78bfa);border-radius:99px}\n.stats-meta{font-size:12px;color:var(--muted2);margin:10px 2px 0}\n.stats-empty{padding:28px;text-align:center;color:var(--muted2);font-size:13px}\n.stab-pill{\n  position:absolute;top:4px;left:4px;height:calc(100% - 8px);border-radius:999px;\n  background:var(--acc-d);box-shadow:inset 0 0 0 1px rgba(59,158,255,.22);\n  transition:transform var(--dur-med) var(--ease-spring),width var(--dur-med) var(--ease-spring);\n  pointer-events:none;z-index:0;will-change:transform,width;\n  transform:translate3d(0,0,0);backface-visibility:hidden;\n}\n.stab{position:relative;z-index:1;border:0;background:transparent;color:var(--muted);padding:8px 14px;border-radius:999px;font-size:13px;font-weight:600;cursor:pointer}\n.stab.active{color:var(--acc)}\n.stab:hover:not(.active){color:var(--text)}\n/* one panel visible — no stacked bleed */\n.spanels{position:relative}\n.spanel{display:none}\n.spanel.active{display:block;animation:riseIn var(--dur-med) var(--ease-out) both}\n\n.core-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 14px;align-items:start;grid-auto-rows:auto}\n@media(max-width:560px){.core-grid{grid-template-columns:1fr}}\n.core-field{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:12px 14px}\n.core-field label{display:block;font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.04em;margin:0 0 8px}\n.core-field .ct-sub{margin-top:8px;font-size:11.5px;color:var(--muted2);line-height:1.4;font-weight:400;text-transform:none;letter-spacing:0}\n.core-field-lab{display:flex;align-items:center;gap:6px;margin:0 0 8px}\n.core-field-lab label{margin:0;font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.04em}\n.core-field{position:relative;overflow:visible;z-index:1;height:auto;align-self:start}\n.core-field:hover,.core-field:focus-within{z-index:8}\n.core-grid{overflow:visible;position:relative;align-items:start}\n.core-tip{\n  display:inline-flex;align-items:center;justify-content:center;\n  width:16px;height:16px;border-radius:999px;border:1px solid var(--line2);\n  color:var(--muted2);font-size:10px;font-weight:700;cursor:help;\n  position:relative;flex-shrink:0;\n}\n.core-tip:hover,.core-tip:focus,.core-tip.is-tip-open{color:var(--acc);border-color:var(--acc-g);outline:none}\n/* fixed to viewport via JS (placeCoreTip) — avoids modal overflow:hidden clip */\n.core-tip-pop{\n  display:none;\n  position:fixed;\n  left:0;top:0;\n  transform:none;\n  width:260px;\n  min-width:200px;\n  max-width:min(280px, calc(100vw - 24px));\n  padding:8px 10px;border-radius:10px;\n  background:#1a1e28;\n  border:1px solid var(--line2);\n  color:#eceef2;\n  font-size:12px;font-weight:500;line-height:1.45;\n  text-transform:none;letter-spacing:0;text-align:left;\n  white-space:normal;\n  word-wrap:break-word;\n  overflow-wrap:break-word;\n  word-break:break-word;\n  z-index:320;\n  box-shadow:0 12px 32px rgba(0,0,0,.75);\n  pointer-events:none;\n  box-sizing:border-box;\n}\n/* display controlled by JS after portal to body */\n.core-tip-pop.is-shown{display:block}\n\n.core-ports-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin:0 0 8px}\n.core-ports-head .ct-lab{font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.04em}\n.core-ports-head .ct-sub{font-size:11.5px;color:var(--muted2);font-weight:400;text-transform:none;letter-spacing:0}\n.core-ports-err{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--err-bg);background:var(--err-bg);border-radius:12px;color:var(--err);font-size:13px;margin:0 0 14px}\n.core-ports-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:0 0 14px}\n.core-port-row{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px}\n.core-port-row .cp-l{font-size:11px;font-weight:600;color:var(--muted2);margin-bottom:4px}\n.core-port-row .cp-n{font-family:var(--mono);font-size:14px;font-weight:700;color:var(--text)}\n.core-port-row .cp-n.is-off{color:var(--muted2);font-weight:600}\n.core-section{margin:16px 0 0;padding-top:14px;border-top:1px solid var(--line)}\n.core-section-title{font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.04em;margin:0 0 4px}\n.core-section-sub{font-size:11.5px;color:var(--muted2);margin:0 0 10px}\n.core-tun-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:0 0 4px}\n.core-tun-row{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px}\n.core-tun-row .cp-l{font-size:11px;font-weight:600;color:var(--muted2);margin-bottom:4px}\n.core-tun-row .cp-n{font-family:var(--mono);font-size:13px;font-weight:600;color:var(--text);word-break:break-all}\n.core-tun-status{display:inline-flex;align-items:center;gap:6px;font-family:inherit;font-size:13px;font-weight:600}\n.core-tun-dot{width:8px;height:8px;border-radius:999px;background:var(--muted2);flex-shrink:0}\n.core-tun-dot.on{background:var(--ok);box-shadow:0 0 0 3px var(--ok-bg)}\n.core-geo-interval{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px;margin:10px 0 0;padding:12px 14px;background:var(--bg);border:1px solid var(--line);border-radius:12px}\n.core-geo-interval[hidden]{display:none !important}\n.core-geo-interval .meta{font-size:12.5px;color:var(--muted)}\n.core-geo-interval input[type="number"]{width:88px;background:var(--bg2);border:1px solid var(--line2);border-radius:10px;color:var(--text);padding:8px 10px;font-size:13.5px}\n.core-geo-src{font-size:11.5px;color:var(--muted2);margin:8px 0 0;line-height:1.4}\n.core-toggle-actions{display:flex;align-items:center;gap:10px;flex-shrink:0}\n.core-field select,.core-field input[type="text"]{width:100%;background:var(--bg2);border:1px solid var(--line2);border-radius:10px;color:var(--text);padding:9px 11px;font-size:13.5px;font-family:inherit}\n.core-field select:focus,.core-field input:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-d)}\n.core-toggles{display:flex;flex-direction:column;gap:10px;margin:0 0 14px}\n.core-toggle-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;background:var(--bg);border:1px solid var(--line);border-radius:12px}\n.core-toggle-row .ct-lab{font-size:13.5px;font-weight:600}\n.core-toggle-row .ct-sub{font-size:11.5px;color:var(--muted2);margin-top:2px}\n.core-ports{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px;margin:0 0 14px}\n.core-port{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px;text-align:center}\n.core-port .cp-n{font-family:var(--mono);font-size:15px;font-weight:700}\n.core-port .cp-l{font-size:10.5px;color:var(--muted2);margin-top:3px;text-transform:uppercase;letter-spacing:.04em}\n.core-meta{font-size:12px;color:var(--muted);margin:0 0 12px}\n.core-versions{\n  display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 14px;\n}\n.core-ver-chip{\n  display:inline-flex;align-items:center;gap:6px;\n  padding:5px 10px;border-radius:999px;\n  background:var(--bg);border:1px solid var(--line);\n  font-size:12px;font-weight:500;color:var(--muted);font-family:var(--mono);\n  letter-spacing:-.01em;\n  cursor:pointer;user-select:none;\n  transition:background .12s,border-color .12s,color .12s;\n}\n.core-ver-chip:hover{background:var(--hover);border-color:var(--line2);color:var(--text)}\n.core-ver-chip .cv-dot{\n  width:7px;height:7px;border-radius:999px;background:var(--muted2);flex-shrink:0;\n}\n.core-ver-chip.ok .cv-dot{background:var(--ok);box-shadow:0 0 0 2px var(--ok-bg)}\n.core-ver-chip.warn .cv-dot{background:#F59E0B;box-shadow:0 0 0 2px rgba(245,158,11,.2)}\n.core-ver-chip.bad{color:var(--muted2);opacity:.9}\n.core-ver-chip.bad .cv-dot{background:var(--muted2)}\n.core-ver-chip.checking{opacity:.9;cursor:wait}\n.core-ver-chip .cv-spin{\n  width:8px;height:8px;border-radius:999px;flex-shrink:0;\n  border:1.5px solid var(--muted2);border-top-color:var(--acc);\n  animation:coreSpin .7s linear infinite;\n}\n@keyframes coreSpin{to{transform:rotate(360deg)}}\n\n#modalCoreUpdate{z-index:400 !important}\n#modalCoreUpdate.open{display:flex !important}\n.core-ver-debug{font-size:11.5px;color:var(--muted2);margin:0 0 12px;min-height:1.2em;font-family:var(--mono)}\n.core-ver-debug.has-msg{color:var(--muted)}\n.core-ver-chip .cv-badge{\n  font-size:10px;font-weight:700;color:#F59E0B;margin-left:2px;\n}\n.core-upd-modal .core-upd-row{display:flex;gap:8px;align-items:baseline;margin:0 0 10px;font-size:13.5px}\n.core-upd-modal .core-upd-notes{font-size:12.5px;color:var(--muted);line-height:1.45;margin:0 0 12px;max-height:6em;overflow:auto}\n.core-upd-modal .core-upd-manual{font-size:12.5px;color:var(--muted);background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-family:var(--mono);margin:0 0 12px;word-break:break-word}\n\n.rov-toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 12px}\n.rov-toolbar input{flex:1;min-width:140px;background:var(--bg);border:1px solid var(--line);border-radius:10px;color:var(--text);padding:9px 12px;font-size:13.5px}\n.rov-meta{font-size:12px;color:var(--muted2);margin:0 0 10px}\n.rov-table-wrap{max-height:min(60vh,480px);overflow:auto;border:1px solid var(--line);border-radius:12px}\n.rov-table{width:100%;border-collapse:collapse;font-size:12.5px}\n.rov-table th{position:sticky;top:0;background:var(--bg2);text-align:left;padding:8px 10px;color:var(--muted2);font-weight:600;border-bottom:1px solid var(--line);z-index:1}\n.rov-table td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}\n.rov-table tr:hover td{background:var(--hover);cursor:pointer}\n.rov-table .off{opacity:.45}\n.rov-val{font-family:var(--mono);font-size:12px;word-break:break-all}\n.rov-empty{padding:24px;text-align:center;color:var(--muted)}\n.kv{display:grid;grid-template-columns:140px 1fr;gap:8px 12px;font-size:13px;margin:0 0 14px}\n.kv dt{color:var(--muted2);font-weight:600}.kv dd{margin:0;font-family:var(--mono);font-size:12.5px;word-break:break-all}\n.status-pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;transition:background var(--dur-fast) var(--ease-inout),color var(--dur-fast) var(--ease-inout),border-color var(--dur-fast) var(--ease-inout);border:1px solid transparent}\n.status-pill.up{background:var(--ok-bg);color:var(--ok);border-color:rgba(61,214,140,.2)}\n.status-pill.down{background:var(--err-bg);color:var(--err);border-color:rgba(255,107,107,.2)}\n.status-pill.unk{background:rgba(255,255,255,.04);color:var(--muted);border-color:var(--line);font-weight:500}\n.btn-row{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}\n.utable{width:100%;border-collapse:collapse;font-size:13px}\n.utable th{text-align:left;padding:8px 10px;color:var(--muted2);font-size:11px;border-bottom:1px solid var(--line)}\n.utable td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:middle}\n.utable tr{transition:background var(--dur-micro) var(--ease-inout)}\n.utable tr:hover td{background:rgba(255,255,255,.02)}\n.utable tr:last-child td{border-bottom:0}\n.badge-off{color:#431407;background:#fdba74;font-size:10px;font-weight:700;padding:2px 7px;border-radius:999px}\n.badge-on{color:#052e16;background:#3dd68c;font-size:10px;font-weight:700;padding:2px 7px;border-radius:999px}\n.metric{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 12px}\n.metric .box{flex:1;min-width:120px;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px;transition:border-color var(--dur-fast) var(--ease-inout),box-shadow var(--dur-fast) var(--ease-inout),transform var(--dur-micro) var(--ease-out)}\n.metric .box:hover{border-color:var(--line2);transform:scale(1.015);box-shadow:var(--sh-hover)}\n.metric .box .n{font-size:1.15rem;font-weight:700;font-family:var(--mono)}.metric .box .l{font-size:11px;color:var(--muted2);margin-top:2px}\n.settings-modal .field textarea{min-height:88px}\n.settings-modal .sub,.modal .sub{font-size:13px;color:var(--muted);line-height:1.45;margin:0 0 16px}\n.settings-modal code,.modal code{font-family:var(--mono);font-size:12px;color:#a5b4fc}\n\n.toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:6px 0 18px}\n.icon-btn{width:40px;height:40px;border-radius:12px;border:1px solid var(--line);background:var(--bg2);color:var(--muted);display:inline-flex;align-items:center;justify-content:center;cursor:pointer}\n.icon-btn:hover{background:var(--hover);color:var(--text);border-color:var(--line2);transform:scale(1.03);box-shadow:var(--sh-hover)}\n.icon-btn:active{transform:scale(.97);box-shadow:none}\n.icon-btn.primary{background:var(--acc);border-color:transparent;color:#061018;box-shadow:0 4px 18px var(--acc-g)}\n.icon-btn.primary:hover{background:#60a5fa;transform:scale(1.03)}\n.icon-btn.primary:active{transform:scale(.97)}\n.toolbar-right{display:flex;gap:8px;align-items:center}\n/* search always visible */\n.search-wrap{flex:1;max-width:340px;position:relative;display:block}\n.search-wrap input{width:100%;height:40px;border-radius:12px;border:1px solid var(--line2);background:var(--bg2);padding:0 12px 0 38px;outline:none;transition:border-color var(--dur-fast) var(--ease-inout),box-shadow var(--dur-fast) var(--ease-inout)}\n.search-wrap input::placeholder{font-style:italic;opacity:var(--empty-opacity);color:var(--muted2)}\n.search-wrap input:focus{border-color:rgba(59,130,246,.45);box-shadow:0 0 0 3px var(--acc-d)}\n.search-wrap svg{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted2);pointer-events:none;opacity:.7}\n.panel{animation:riseIn var(--dur-med) var(--ease-out) both}\n\n.empty-wrap{min-height:42vh;display:flex;align-items:center;justify-content:center;padding:24px 8px}\n.empty-card{display:flex;align-items:flex-start;gap:14px;max-width:440px;padding:18px 20px;border-radius:16px;background:var(--bg2);border:1px solid var(--line);box-shadow:var(--sh);animation:modalIn var(--dur-slow) var(--ease-out) both}\n.empty-icon{width:36px;height:36px;border-radius:10px;background:var(--bg3);border:1px solid var(--line);display:flex;align-items:center;justify-content:center;color:var(--muted);flex-shrink:0}\n.empty-card h3{margin:0 0 4px;font-size:15px;font-weight:600}.empty-card p{margin:0;font-size:13px;color:var(--muted)}\n.pill{font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;border-radius:999px;padding:3px 8px;display:inline-flex;align-items:center}\n.badge-v4{color:var(--tag-v4-fg);background:var(--tag-v4-bg)}\n.badge-v6{color:var(--tag-v6-fg);background:var(--tag-v6-bg)}\n.badge-ns{color:var(--tag-ns-fg);background:var(--tag-ns-bg)}\n.badge-dom{color:var(--tag-dom-fg);background:var(--tag-dom-bg)}\n.badge-kw{color:var(--tag-kw-fg);background:var(--tag-kw-bg)}\n.badge-raw{color:var(--tag-raw-fg);background:var(--tag-raw-bg)}\n.meta{font-size:12px;color:var(--muted2);font-weight:500}\n.mono{font-family:var(--mono);font-size:12.5px}\n.icon-del{width:34px;height:34px;border:0;border-radius:10px;background:transparent;color:var(--muted2);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;opacity:var(--tool-opacity)}\n.icon-del:hover{background:var(--err-bg);color:var(--err);opacity:1;transform:scale(1.05)}\n.icon-del:active{transform:scale(.96)}\n.rule-count-wrap{display:inline-flex;flex-direction:column;align-items:flex-end;line-height:1.15;cursor:default;min-width:2.2rem}\n.rule-count{font-size:13px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}\n.rule-count-lbl{font-size:10px;color:var(--muted2);font-weight:500}\n\n/* group cards */\n.gcard{\n  border-radius:14px;background:var(--bg2);border:1px solid var(--line);overflow:hidden;margin-bottom:10px;\n  opacity:1;transform:none;\n  /* entry anim only when panel already visible — avoids stuck opacity:0 if parent was [hidden] */\n  transition:border-color var(--dur-micro) var(--ease-inout),box-shadow var(--dur-micro) var(--ease-inout),transform var(--dur-micro) var(--ease-out),opacity var(--dur-fast) var(--ease-inout),background var(--dur-micro) var(--ease-inout);\n}\n/* subtle hover — no “spotlight” on open groups */\n.gcard:hover{\n  border-color:rgba(255,255,255,.09);\n  box-shadow:none;\n  background:var(--bg2);\n  transform:none;\n}\n.gcard-head{display:flex;align-items:center;gap:10px;padding:16px 14px 16px 0;background:var(--bg3);min-height:56px;transition:background var(--dur-micro) var(--ease-inout)}\n.gcard:hover .gcard-head{background:var(--bg3)}\n.gtable tbody tr:hover td{background:rgba(255,255,255,.025)}\n.gcard:not(.collapsed) .gcard-head{border-bottom:1px solid var(--line)}\n.gcard-stripe{width:4px;align-self:stretch;flex-shrink:0;min-height:56px;margin:-16px 0;background:var(--stripe-default);opacity:.85;transition:background var(--dur-fast) var(--ease-inout),opacity var(--dur-fast) var(--ease-inout)}\n.gcard[data-cat="messenger"] .gcard-stripe{background:var(--stripe-messenger)}\n.gcard[data-cat="video"] .gcard-stripe{background:var(--stripe-video)}\n.gcard[data-cat="infra"] .gcard-stripe{background:var(--stripe-infra)}\n.gcard[data-cat="default"] .gcard-stripe{background:var(--stripe-default)}\n.gcard.disabled .gcard-stripe{background:var(--stripe-off);opacity:.7}\n.gcard-drag{color:var(--muted2);display:inline-flex;flex-shrink:0;padding:0 2px;opacity:.5}\n.gcard-name{flex:1;min-width:0;font-weight:600;font-size:14px;letter-spacing:-.01em;border:0;background:transparent;padding:6px 8px;border-radius:8px;outline:none;transition:background var(--dur-fast) var(--ease-inout),box-shadow var(--dur-fast) var(--ease-inout)}\n.gcard-name:focus{background:var(--bg);box-shadow:inset 0 0 0 1px var(--line2)}\n.gcard-name::placeholder,.cell-input::placeholder{font-style:italic;opacity:var(--empty-opacity);color:var(--muted2);font-weight:400}\n/* cross-group rule conflict warn (same orange as import) */\n.gcard-conflict-warn{\n  display:none;flex-shrink:0;align-items:center;justify-content:center;\n  width:22px;height:22px;margin:0 2px 0 0;padding:0;border:0;background:transparent;\n  color:#fb923c;cursor:default;\n}\n.gcard-conflict-warn[data-show="1"]{display:inline-flex}\n.gcard-conflict-warn svg{display:block}\n.pattern-cell{display:flex;align-items:center;gap:8px;min-width:0}\n.pattern-cell .pattern-input{flex:1;min-width:0}\n/* Conflict badge: NEVER show unless parent row is .is-conflict.\n   Empty .pill.pill-conflict was always visible — .pill{display:inline-flex}\n   overrides HTML [hidden], drawing a short orange bar next to the toggle. */\n.rule-conflict-badge{\n  display:none !important;\n  flex-shrink:0;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;\n}\n.rule-row.is-conflict .rule-conflict-badge{\n  display:inline-flex !important;\n}\n/* left accent only on first cell of conflict rows */\n.rule-row.is-conflict{border-radius:0}\n.rule-row.is-conflict>td:first-child{\n  box-shadow:inset 3px 0 0 #fb923c;\n  padding-left:12px;\n}\n.gcard-conflict-count{\n  display:none;font-size:12px;font-weight:600;color:#fb923c;margin-left:4px;white-space:nowrap;\n}\n.gcard-conflict-count[data-show="1"]{display:inline}\n.conflict-filter-link{\n  display:none;border:0;background:transparent;padding:2px 0;margin:0 0 0 8px;\n  font:inherit;font-size:12.5px;font-weight:500;color:#fb923c;cursor:pointer;\n  text-decoration:underline;text-underline-offset:3px;white-space:nowrap;\n  outline:none;box-shadow:none;-webkit-tap-highlight-color:transparent;\n  border-radius:0;\n}\n.conflict-filter-link[data-show="1"]{display:inline}\n.conflict-filter-link:hover{color:#fdba74}\n.conflict-filter-link:focus,\n.conflict-filter-link:focus-visible,\n.conflict-filter-link:active{\n  outline:none !important;box-shadow:none !important;border:0;\n  color:#fdba74;text-decoration-thickness:2px;\n}\n.conflict-filter-link.active{\n  font-weight:700;color:#fdba74;text-decoration-thickness:2px;\n}\n/* list visibility — class beats residual inline styles / animations */\n.gcard.is-filtered-out{display:none !important}\n.rule-row.is-row-filtered-out{display:none !important}\n.gcard-filter-slice{\n  display:none;font-size:12px;font-weight:500;color:#fb923c;margin-left:2px;white-space:nowrap;\n}\n.gcard-filter-slice[data-show="1"]{display:inline}\n.gcard-meta{padding:0 10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}\n.gcard-actions{display:flex;align-items:center;gap:2px;flex-shrink:0;flex-wrap:nowrap}\n.gcard-actions form{display:contents}\n.act-group{display:inline-flex;align-items:center;gap:2px}\n.act-sep{width:1px;height:18px;background:var(--line2);margin:0 6px;flex-shrink:0;opacity:.8}\n.act-tools .mini-btn{opacity:var(--tool-opacity)}\n.act-tools .mini-btn:hover{opacity:1}\n.mini-btn{width:32px;height:32px;border-radius:9px;border:0;background:transparent;color:var(--muted);display:inline-flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0}\n.mini-btn:hover{background:var(--hover);color:var(--text);transform:scale(1.06)}\n.mini-btn:active{transform:scale(.94)}\n.act-danger .mini-btn{opacity:.75}\n.act-danger .mini-btn:hover,.mini-btn.danger:hover{background:var(--err-bg);color:var(--err);opacity:1}\n.mini-btn .chev{transition:transform var(--dur-med) var(--ease-out)}\n.gcard:not(.collapsed) .mini-btn .chev{transform:rotate(180deg)}\n.rule-row{transition:opacity var(--dur-fast) var(--ease-out),transform var(--dur-fast) var(--ease-out),background var(--dur-micro) var(--ease-inout)}\n.rule-row.removing{animation:rowOut var(--dur-fast) var(--ease-out) forwards;pointer-events:none}\n.rule-row.draft{background:rgba(59,130,246,.05)}\n.rule-row.draft .pattern-input{box-shadow:inset 0 -1px 0 rgba(239,68,68,.4)}\n.rule-row.saving{opacity:.55}\n.rule-row.search-hit{background:rgba(59,130,246,.08)}\n.rule-row.search-hit td{box-shadow:inset 0 0 0 1px rgba(59,130,246,.12)}\n.switch{position:relative;width:38px;height:22px;flex-shrink:0;display:inline-block;vertical-align:middle}\n.switch input{opacity:0;width:0;height:0;position:absolute}\n.switch span{position:absolute;inset:0;background:#3a4050;border-radius:999px;cursor:pointer;transition:background var(--dur-fast) var(--ease-inout)}\n.switch span:before{content:"";position:absolute;width:16px;height:16px;left:3px;top:3px;background:#fff;border-radius:50%;transition:transform var(--dur-fast) var(--ease-out)}\n.switch input:checked+span{background:var(--acc)}\n.switch input:checked+span:before{transform:translateX(16px)}\n.gcard-body{display:none}\n.gcard:not(.collapsed) .gcard-body{display:block;animation:riseIn var(--dur-med) var(--ease-out) both}\n.gcard.disabled{opacity:.55}\n.gtable{width:100%;border-collapse:collapse;font-size:13px}\n.gtable th{text-align:left;padding:10px 12px;font-size:11px;font-weight:600;color:var(--muted2);border-bottom:1px solid var(--line);background:rgba(0,0,0,.15)}\n.gtable td{padding:12px 12px;border-bottom:1px solid var(--line);vertical-align:middle}\n.gtable tbody tr{opacity:0;animation:riseIn var(--dur-med) var(--ease-out) both;animation-delay:calc(var(--ri, 0) * 28ms)}\n.gtable tr:last-child td{border-bottom:0}\n.gtable tbody tr:hover td{background:rgba(255,255,255,.03);transition:background var(--dur-micro) var(--ease-inout)}\n.gtable .col-id{width:48px;color:var(--muted2);font-family:var(--mono);font-size:11px}\n.gtable .col-type{width:130px}\n.gtable .col-en{width:70px;text-align:center}\n.gtable .col-del{width:44px}\n.cell-input{width:100%;border:0;background:transparent;padding:7px 8px;border-radius:8px;outline:none;font-size:13px;transition:background var(--dur-fast) var(--ease-inout),box-shadow var(--dur-fast) var(--ease-inout)}\n.cell-input:focus{background:var(--bg);box-shadow:inset 0 0 0 1px var(--line2)}\n.cell-input.mono{font-family:var(--mono);font-size:12.5px}\n/* type select colors = same tokens as pills */\n.type-select{width:100%;border:0;background:transparent;font-weight:600;font-size:13px;padding:6px 4px;border-radius:8px;outline:none;cursor:pointer;color:var(--tag-ns-bg)}\n.type-select.ipv4,.type-select.auto{color:var(--tag-v4-bg)}\n.type-select.ipv4{color:#4ADE80}\n.type-select.ipv6{color:#A78BFA}\n.type-select.namespace{color:#38BDF8}\n.type-select.domain{color:#D8B4FE}\n.type-select.keyword{color:#FDBA74}\n.type-select option{background:var(--bg2);color:var(--text)}\n.add-row td{background:rgba(59,130,246,.04)}\n.gcard-foot{padding:10px 12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;border-top:1px solid var(--line)}\n.metric .box .n.empty-metric{font-size:12px;font-weight:500;color:var(--muted2);font-style:italic;opacity:var(--empty-opacity);font-family:var(--font)}\n.mini-btn.icon-neutral{color:var(--muted2)}\n.mini-btn.icon-neutral:hover{color:var(--text);background:var(--hover)}\n.mini-btn.icon-danger-only{color:var(--muted2)}\n.mini-btn.icon-danger-only:hover{color:var(--err);background:var(--err-bg)}\n/* —— Ops status cards (unified) —— */\n.ops-metric .box.ops-stat{padding:12px 14px}\n.ops-stat-main{display:flex;align-items:center;gap:8px;min-height:22px}\n.ops-stat-dot{\n  width:8px;height:8px;border-radius:50%;flex-shrink:0;\n  background:var(--ops-stat-gray);\n  box-shadow:0 0 0 3px rgba(100,116,139,.14);\n}\n.ops-stat-dot.ok{background:var(--ops-stat-ok);box-shadow:0 0 0 3px rgba(34,197,94,.16)}\n.ops-stat-dot.down{background:var(--ops-stat-err);box-shadow:0 0 0 3px rgba(239,68,68,.16)}\n.ops-stat-dot.stale{background:var(--ops-stat-gray);box-shadow:0 0 0 3px rgba(100,116,139,.14)}\n.ops-stat-val{\n  font-size:13.5px;font-weight:500;font-family:var(--font);\n  color:var(--text);line-height:1.25;text-transform:none;letter-spacing:0;\n}\n.ops-stat-val.empty-metric,\n.ops-metric .box .ops-stat-val.empty-metric{\n  font-size:13.5px;font-weight:500;font-style:normal;opacity:1;\n  color:var(--muted2);font-family:var(--font);\n}\n.ops-metric .box .l{margin-top:6px;font-size:11px;color:var(--muted2)}\n\n/* —— Auto-ping prefs group —— */\n.ops-prefs-card{\n  margin-top:12px;padding:14px 16px;border-radius:8px;\n  border:1px solid rgba(255,255,255,.1);\n  background:rgba(0,0,0,.22);\n}\n.ops-prefs-card .ops-prefs-label{\n  display:flex;align-items:center;gap:10px;cursor:pointer;\n  font-weight:500;font-size:13.5px;color:var(--text);margin:0;\n}\n.ops-prefs-card .ops-prefs-label input{width:auto;margin:0}\n.ops-prefs-row{\n  display:flex;align-items:center;gap:8px;margin-top:12px;flex-wrap:wrap;\n}\n.ops-prefs-row .meta{font-size:12px}\n.ops-prefs-row input[type="number"]{\n  width:80px;border-radius:10px;border:1px solid var(--line2);\n  background:var(--bg);padding:8px 10px;color:var(--text);font:inherit;\n}\n\n/* —— Activity journal —— */\n.journal-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}\n.journal-head-left{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;min-width:0}\n.journal-head-left h3{margin:0;font-size:14px}\n.journal-age{font-size:11.5px;color:var(--muted2);font-weight:400}\n.btn-icon-only{\n  width:32px;height:32px;padding:0;border-radius:8px;\n  display:inline-flex;align-items:center;justify-content:center;\n  border:1px solid var(--line);background:transparent;color:var(--muted);\n  cursor:pointer;flex-shrink:0;\n  transition:background var(--dur-micro) var(--ease-inout),color var(--dur-micro) var(--ease-inout),transform var(--dur-micro) var(--ease-out);\n}\n.btn-icon-only:hover{background:var(--bg3);color:var(--text)}\n.btn-icon-only:active{transform:scale(.95)}\n.btn-icon-only svg{display:block}\n.activity-list{max-height:280px;overflow:auto;border:1px solid var(--line);border-radius:12px;background:var(--bg);padding:6px 0;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.12) transparent}\n.activity-row{display:grid;grid-template-columns:18px 1fr;gap:8px;padding:8px 12px;border-bottom:1px solid var(--line);font-size:12px;line-height:1.35}\n.activity-row:last-child{border-bottom:0}\n.activity-row:hover{background:rgba(255,255,255,.03)}\n.activity-row.is-new{animation:actFadeIn var(--dur-fast) var(--ease-out) both}\n@keyframes actFadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}\n@media (prefers-reduced-motion:reduce){\n  .activity-row.is-new{animation:none}\n}\n.activity-dot{width:8px;height:8px;border-radius:50%;margin-top:5px;background:var(--act-dot-neutral);flex-shrink:0}\n.activity-dot.kind-ok{background:var(--act-dot-ok)}\n.activity-dot.kind-danger{background:var(--act-dot-danger)}\n.activity-dot.kind-neutral{background:var(--act-dot-neutral)}\n.activity-top{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}\n.activity-act{font-weight:700;color:var(--text);font-family:var(--font);font-size:12.5px}\n.activity-user{font-weight:700;color:rgba(236,238,242,.72);font-size:12.5px}\n.activity-ts{color:var(--muted2);font-family:var(--mono);font-size:11px;margin-left:auto;font-variant-numeric:tabular-nums}\n.activity-detail{color:var(--muted2);margin-top:3px;word-break:break-word;font-weight:400;font-size:11.5px}\n#svcDot.up{color:var(--ok);border-color:rgba(34,197,94,.25);background:var(--ok-bg)}\n#svcDot.down{color:var(--err);border-color:rgba(239,68,68,.25);background:var(--err-bg)}\n\n.modal .sub{margin:0 0 16px}\n.modal-x{float:right;width:32px;height:32px;border:0;border-radius:8px;background:transparent;color:var(--muted);cursor:pointer;display:inline-flex;align-items:center;justify-content:center}\n.modal-x:hover{background:var(--hover);color:var(--text);transform:scale(1.05)}\n.modal-x:active{transform:scale(.95)}\n.field{margin-bottom:12px}.field label{display:block;font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px}\n.field input,.field select,.field textarea{width:100%;border-radius:12px;border:1px solid var(--line2);background:var(--bg);padding:11px 12px;outline:none;transition:border-color var(--dur-fast) var(--ease-inout),box-shadow var(--dur-fast) var(--ease-inout)}\n.field textarea{min-height:110px;resize:vertical;font-family:var(--mono);font-size:12.5px;line-height:1.5}\n.field input:focus,.field select:focus,.field textarea:focus{border-color:rgba(59,158,255,.5);box-shadow:0 0 0 3px var(--acc-d)}\n.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:8px;flex-wrap:wrap}\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:0;border-radius:12px;padding:10px 16px;font-size:14px;font-weight:600;cursor:pointer}\n.btn:hover{transform:scale(1.02)}\n.btn:active{transform:scale(.97)}\n.btn-primary{background:var(--acc);color:#061018;box-shadow:0 4px 16px var(--acc-g)}\n.btn-primary:hover{background:#54aaff}\n.btn-ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}\n.btn-ghost:hover{background:var(--bg3);color:var(--text)}\n.toast{position:fixed;top:16px;left:50%;transform:translate3d(-50%,0,0);z-index:90;max-width:min(520px,calc(100% - 24px));padding:12px 16px;border-radius:12px;font-size:13.5px;font-weight:500;box-shadow:var(--sh);animation:toastIn var(--dur-med) var(--ease-spring) both;transition:opacity var(--dur-med) var(--ease-out),transform var(--dur-med) var(--ease-out);will-change:opacity,transform;backface-visibility:hidden}\n.toast.toast-out{opacity:0;transform:translate3d(-50%,-10px,0);pointer-events:none}\n.toast pre{margin:0;white-space:pre-wrap;font-family:var(--mono);font-size:12px;font-weight:400}\n.toast-ok{background:var(--ok-bg);color:var(--ok);border:1px solid rgba(61,214,140,.22)}\n.toast-err{background:var(--err-bg);color:var(--err);border:1px solid rgba(255,107,107,.22)}\n.preview{max-height:300px;overflow:auto;border:1px solid var(--line);border-radius:12px;background:var(--bg);margin:10px 0}\n.preview-row{\n  display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:8px 10px;\n  padding:8px 12px;border-bottom:1px solid var(--line);font-size:12.5px;\n  opacity:0;animation:riseIn var(--dur-fast) var(--ease-out) both;animation-delay:calc(var(--pi, 0) * 20ms)\n}\n.preview-row:last-child{border-bottom:0}\n.preview-row .preview-val{font-family:var(--mono);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.preview-stats{display:flex;flex-direction:column;gap:6px;margin:8px 0 4px}\n.preview-stats-types{display:flex;gap:8px;flex-wrap:wrap;align-items:center}\n.preview-stats-summary{font-size:12px;color:var(--muted);font-weight:500}\n.preview-stats-summary strong{color:var(--text);font-weight:600}\n.import-group-line{font-size:13px;color:var(--muted);margin:0 0 10px}\n.import-group-line strong{color:var(--text)}\n.detect-field{margin:0;min-width:160px;flex:0 1 200px}\n.detect-field label{display:block;font-size:12px;font-weight:600;color:var(--muted);margin-bottom:6px}\n.detect-field .detect-hint{margin:6px 0 0;font-size:11.5px;color:var(--muted2);line-height:1.35;max-width:220px}\n.import-opt-wrap{margin:10px 0 4px;padding:10px 12px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,.02)}\n.import-opt{display:flex;align-items:flex-start;gap:10px;cursor:pointer;font-size:13px;font-weight:600;color:var(--text);line-height:1.35;user-select:none}\n.import-opt input[type=checkbox]{appearance:none;-webkit-appearance:none;width:18px;height:18px;margin:1px 0 0;flex-shrink:0;border-radius:5px;border:1.5px solid var(--line2);background:var(--bg);cursor:pointer;display:grid;place-content:center;transition:border-color var(--dur-fast) var(--ease-inout),background var(--dur-fast) var(--ease-inout),box-shadow var(--dur-fast) var(--ease-inout)}\n.import-opt input[type=checkbox]:checked{background:var(--acc);border-color:var(--acc);box-shadow:0 0 0 3px var(--acc-d)}\n.import-opt input[type=checkbox]:checked:before{content:"";width:10px;height:6px;border-left:2px solid #fff;border-bottom:2px solid #fff;transform:rotate(-45deg) translate(1px,-1px)}\n.import-opt input[type=checkbox]:focus-visible{outline:none;box-shadow:0 0 0 3px var(--acc-g)}\n.import-opt-hint{margin:6px 0 0 28px;font-size:11.5px;color:var(--muted2);line-height:1.35}\n.preview-row.will-skip{opacity:.5}\n.preview-skip-note{font-size:11px;color:var(--muted2);font-weight:500;white-space:nowrap}\n.detect-select{border-radius:12px;border:1px solid var(--line2);background:var(--bg);padding:10px 12px;min-width:100%;width:100%;color:var(--text);font:inherit}\n/* status pills for import (not type palette) */\n.pill-status{text-transform:none;letter-spacing:0;font-weight:600;font-size:11px}\n.pill-dup{color:#854d0e;background:#facc15}\n.pill-conflict{color:#7c2d12;background:#fb923c}\n/* exception rows: badge + left accent only (no full-row fill) */\n.preview-row.is-dup,\n.preview-row.is-conflict{\n  background:transparent;\n  border-radius:0;\n  border-left:3px solid transparent;\n  padding-left:9px; /* 12px - 3px border so content stays aligned */\n}\n.preview-row.is-dup{border-left-color:#facc15}\n.preview-row.is-conflict{border-left-color:#fb923c}\n\n/* skeleton shimmer */\n.skel{position:relative;overflow:hidden;background:rgba(255,255,255,.04);border-radius:10px;height:14px}\n.skel::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.06),transparent);background-size:200% 100%;animation:shimmer 1.4s var(--ease-inout) infinite}\n.skel-card{padding:14px;border-radius:14px;border:1px solid var(--line);background:var(--bg2);margin-bottom:10px}\n.skel-card .skel{margin:8px 0}\n.skel-card .skel.w40{width:40%}.skel-card .skel.w70{width:70%}.skel-card .skel.w90{width:90%}\n\n@media (prefers-reduced-motion:reduce){\n  *,*::before,*::after{\n    animation-duration:.01ms !important;\n    animation-iteration-count:1 !important;\n    transition-duration:.01ms !important;\n    scroll-behavior:auto !important;\n  }\n  .gcard,.gtable tbody tr,.preview-row,.panel,.spanel.active,.empty-card{opacity:1 !important;transform:none !important}\n  .bg-fx .mesh-a,.bg-fx .mesh-b,.bg-fx .grid{animation:none !important}\n  /* static ambient still visible without motion */\n  .bg-fx .mesh-a{opacity:1}\n  .bg-fx .mesh-b{opacity:.7}\n}\n\n.foot-row{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:12px}\n.live-badge{font-size:10px;font-weight:700;padding:3px 8px;border-radius:999px;margin-left:6px;vertical-align:middle}\n@media(max-width:720px){\n  .gtable .col-id{display:none}\n  .col-name-hide{display:none}\n  body{overflow-x:hidden;-webkit-text-size-adjust:100%}\n  .app{\n    padding:0 max(12px, env(safe-area-inset-right, 0px))\n      calc(20px + env(safe-area-inset-bottom, 0px))\n      max(12px, env(safe-area-inset-left, 0px));\n  }\n\n  /* ── Topbar ── */\n  .topbar{\n    display:flex;flex-direction:column;align-items:stretch;\n    gap:8px;\n    position:sticky !important;top:0 !important;\n    z-index:50 !important;\n    /* fully opaque — desktop uses transparent sticky which bleeds content */\n    background:var(--bg) !important;\n    backdrop-filter:none !important;\n    -webkit-backdrop-filter:none !important;\n    border:0 !important;\n    border-bottom:1px solid var(--line) !important;\n    box-shadow:0 10px 28px rgba(0,0,0,.45) !important;\n    /* full-bleed bar within .app horizontal padding */\n    margin-left:calc(-1 * max(12px, env(safe-area-inset-left, 0px))) !important;\n    margin-right:calc(-1 * max(12px, env(safe-area-inset-right, 0px))) !important;\n    padding:max(8px, env(safe-area-inset-top, 0px)) max(12px, env(safe-area-inset-left, 0px)) 10px max(12px, env(safe-area-inset-right, 0px)) !important;\n  }\n  /* keep first content clear of sticky bar chrome */\n  .toolbar{\n    margin-top:4px !important;\n    position:relative;z-index:1;\n  }\n  #panel-groups,.view-stage,.view-panel{\n    position:relative;z-index:1;\n  }\n  .brand{font-size:15px;font-weight:650;gap:8px;width:100%}\n  .brand svg{width:16px;height:16px}\n  .top-right{\n    display:flex;flex-wrap:nowrap;align-items:center;gap:6px;\n    width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;\n    scrollbar-width:none;padding-bottom:2px;\n    justify-content:flex-start;\n  }\n  .top-right::-webkit-scrollbar{display:none;height:0}\n  .topbar .chip{\n    flex:0 0 auto;padding:6px 10px;font-size:12px;gap:4px;\n    white-space:nowrap;\n  }\n  .top-right > a.chip[target="_blank"]{display:none !important}\n  #svcDot,#xttpDot{font-size:11.5px;padding:6px 9px}\n  #btnSettings{\n    flex:0 0 auto;padding:6px 10px;font-size:12px;\n    max-width:42vw;overflow:hidden;text-overflow:ellipsis;\n  }\n  .link-logout{\n    flex:0 0 auto;margin-left:auto;padding:6px 8px;font-size:12px;\n    white-space:nowrap;\n  }\n  .ver-wrap{flex:0 0 auto;position:relative;z-index:5}\n  .ver-wrap.open{z-index:400}\n  .ver-dd{\n    position:fixed !important;\n    left:12px !important;right:12px !important;\n    width:auto !important;\n    top:72px;\n    max-height:min(55vh, 380px);\n    z-index:400 !important;\n    -webkit-overflow-scrolling:touch;\n  }\n  .top-right{isolation:isolate}\n\n  /* ── Toolbar: search → tabs (tight) → compact icon row ── */\n  .toolbar{\n    display:flex;flex-direction:column;align-items:stretch;\n    gap:8px;margin:8px 0 10px;\n    position:relative;z-index:1;\n  }\n  .toolbar > div:first-child{\n    display:flex !important;flex-direction:column !important;\n    align-items:stretch !important;\n    gap:10px !important; /* search ↔ tabs 8–12px */\n    width:100%;flex:none !important;min-width:0;\n  }\n  .search-wrap{flex:none !important;max-width:none !important;width:100%}\n  .search-wrap input{\n    height:44px;font-size:16px;border-radius:12px;\n  }\n  .view-switch{\n    width:100% !important;margin:0 !important;\n    display:flex !important;\n  }\n  .view-switch .stab{\n    flex:1 1 0;text-align:center;padding:10px 8px;font-size:13.5px;\n    min-height:40px;\n  }\n  /* compact action icons — right-aligned, not full-width grid */\n  .toolbar-right{\n    display:flex !important;\n    flex-direction:row !important;\n    justify-content:flex-end !important;\n    align-items:center !important;\n    gap:8px !important;\n    width:100%;\n    grid-template-columns:unset !important;\n  }\n  .toolbar-right form{display:contents}\n  .toolbar-right .icon-btn{\n    width:40px !important;height:40px !important;\n    min-width:40px !important;min-height:40px !important;\n    flex:0 0 40px !important;\n    border-radius:12px;\n    box-sizing:border-box;\n  }\n  .toolbar-right .icon-btn.primary{\n    width:44px !important;height:44px !important;\n    min-width:44px !important;min-height:44px !important;\n    flex:0 0 44px !important;\n  }\n\n  /* ── Group cards: name / meta line ── */\n  .gcard{margin-bottom:10px;border-radius:14px}\n  .gcard-head{\n    display:grid !important;\n    grid-template-columns:3px 18px minmax(0,1fr) auto;\n    grid-template-areas:\n      "stripe drag name actions"\n      "stripe .    meta meta";\n    column-gap:8px;row-gap:6px;\n    align-items:center;\n    padding:12px 12px 12px 0 !important;\n    min-height:0 !important;\n  }\n  .gcard-stripe{\n    grid-area:stripe;width:3px;margin:0 !important;min-height:100% !important;\n    align-self:stretch;border-radius:0 2px 2px 0;\n  }\n  .gcard-drag{grid-area:drag;justify-self:center;opacity:.45}\n  .gcard-head > form{\n    grid-area:name !important;\n    display:block !important;width:100% !important;\n    min-width:0 !important;flex:unset !important;\n  }\n  .gcard-name{\n    display:block !important;width:100% !important;\n    min-width:0 !important;flex:unset !important;\n    font-size:15px !important;font-weight:650;\n    padding:4px 4px !important;\n    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;\n  }\n  .gcard-conflict-warn{\n    grid-area:name;justify-self:end;align-self:center;margin-right:2px;z-index:1;\n  }\n  /* count + tags on ONE line under title */\n  .gcard-meta{\n    grid-area:meta;padding:0 !important;\n    display:flex !important;flex-direction:row !important;flex-wrap:wrap !important;\n    align-items:center !important;justify-content:flex-start !important;\n    gap:6px 8px !important;min-width:0;width:100%;\n  }\n  .rule-count-wrap{\n    display:inline-flex !important;flex-direction:row !important;\n    align-items:baseline !important;gap:4px !important;\n    min-width:auto !important;line-height:1.2 !important;\n  }\n  .rule-count{font-size:12.5px !important;font-weight:600}\n  .rule-count-lbl{font-size:11px !important;color:var(--muted2)}\n  .gcard-meta .count-pills{\n    display:flex !important;flex-wrap:wrap !important;gap:4px !important;\n    flex:1 1 auto;min-width:0;\n  }\n  .gcard-meta .count-pills .pill,\n  .gcard-meta .badge,\n  .count-pills > span{\n    font-size:10px !important;padding:2px 6px !important;\n  }\n  .gcard-actions{\n    grid-area:actions;justify-self:end;\n    flex-wrap:nowrap;gap:0;max-width:100%;\n  }\n  .gcard-actions .act-sep{margin:0 3px;height:16px}\n  .mini-btn{width:40px;height:40px;min-width:40px;min-height:40px;border-radius:10px}\n  /* rule-row toggle: keep desktop proportions (~46×26), not inflated */\n  .gcard .col-en{\n    display:flex !important;align-items:center;justify-content:center;\n    min-width:44px;min-height:44px; /* touch target wrapper */\n    width:auto !important;\n  }\n  .gcard .col-en .switch{\n    position:relative !important;\n    display:inline-block !important;\n    width:46px !important;height:26px !important;\n    min-width:46px !important;min-height:26px !important;\n    max-width:46px !important;max-height:26px !important;\n    flex:0 0 46px !important;\n    transform:none !important;\n    vertical-align:middle;\n  }\n  .gcard .col-en .switch span{\n    inset:0 !important;border-radius:999px !important;\n  }\n  .gcard .col-en .switch span:before{\n    width:20px !important;height:20px !important;\n    left:3px !important;top:3px !important;\n  }\n  .gcard .col-en .switch input:checked+span:before{\n    transform:translateX(20px) !important;\n  }\n  /* header group-enable switch: same scale, not full-row */\n  .gcard-actions .switch{\n    width:46px !important;height:26px !important;\n    min-width:46px !important;min-height:26px !important;\n    display:inline-block !important;transform:none !important;\n  }\n  .gcard-actions .switch span:before{\n    width:20px !important;height:20px !important;left:3px !important;top:3px !important;\n  }\n  .gcard-actions .switch input:checked+span:before{transform:translateX(20px) !important}\n  .gcard .icon-del{\n    width:40px !important;height:40px !important;\n    min-width:40px !important;min-height:40px !important;\n    display:inline-flex !important;align-items:center;justify-content:center;\n  }\n\n  /* ── Expanded rules: compact 2-line cards (not 3-col table) ── */\n  .gcard .gtable{font-size:12.5px;width:100%}\n  .gcard .gtable thead{display:none !important}\n  .gcard .gtable,\n  .gcard .gtable tbody{display:block;width:100%}\n  .gcard .gtable tr.rule-row{\n    display:grid !important;\n    grid-template-columns:minmax(0,1fr) 48px 40px;\n    grid-template-areas:\n      "type en del"\n      "pat  pat pat";\n    column-gap:8px;row-gap:6px;\n    align-items:center;\n    padding:10px 12px !important;\n    margin:0;\n    border-bottom:1px solid var(--line);\n    min-height:50px;\n    box-sizing:border-box;\n    opacity:1 !important;animation:none !important;\n  }\n  .gcard .gtable tr.rule-row:last-child{border-bottom:0}\n  .gcard .gtable td{\n    display:block;padding:0 !important;border:0 !important;\n    vertical-align:middle;background:transparent !important;\n  }\n  .gcard .gtable .col-id,\n  .gcard .gtable .col-name-hide{display:none !important}\n  .gcard .gtable .col-type{grid-area:type;min-width:0}\n  .gcard .gtable .col-en{grid-area:en;justify-self:center}\n  .gcard .gtable .col-del{grid-area:del;justify-self:center}\n  .gcard .gtable .col-pattern{grid-area:pat;min-width:0;width:100%}\n  .gcard .pattern-cell{\n    display:flex;flex-direction:column;align-items:stretch;gap:4px;\n    min-width:0;width:100%;\n  }\n  .gcard .pattern-input{\n    width:100% !important;\n    font-size:13px !important;\n    font-family:var(--mono) !important;\n    line-height:1.35 !important;\n    min-height:0 !important;height:auto !important;\n    padding:2px 0 !important;\n    border:0 !important;border-radius:0 !important;\n    background:transparent !important;\n    box-shadow:none !important;\n    white-space:normal !important;\n    word-break:break-all !important;\n    overflow-wrap:anywhere !important;\n    overflow:visible !important;\n    text-overflow:clip !important;\n    resize:none;\n  }\n  .gcard .pattern-input:focus{\n    background:rgba(255,255,255,.04) !important;\n    border-radius:8px !important;\n    padding:6px 8px !important;\n    box-shadow:inset 0 0 0 1px var(--line2) !important;\n  }\n  .gcard .type-select{\n    width:auto !important;max-width:100%;\n    min-height:40px;padding:6px 8px !important;\n    font-size:12.5px !important;font-weight:700;\n    border-radius:999px !important;\n  }\n  .gcard .rule-conflict-badge{align-self:flex-start}\n  .gcard .gcard-body{padding:0}\n  .gcard-foot{padding:10px 12px;flex-wrap:wrap;gap:8px}\n\n  /* pattern copy toast (mobile) */\n  .pattern-copy-toast{\n    position:fixed;left:12px;right:12px;\n    bottom:max(16px, env(safe-area-inset-bottom, 0px));\n    z-index:90;\n    display:flex;flex-direction:column;gap:10px;\n    padding:12px 14px;border-radius:14px;\n    background:var(--bg2);border:1px solid var(--line2);\n    box-shadow:var(--sh);max-height:40vh;\n  }\n  .pattern-copy-toast code{\n    font-family:var(--mono);font-size:12.5px;line-height:1.4;\n    word-break:break-all;color:var(--text);\n    overflow:auto;max-height:22vh;\n  }\n  .pattern-copy-toast .pct-actions{\n    display:flex;gap:8px;justify-content:flex-end;\n  }\n  .pattern-copy-toast .pct-actions .btn{\n    min-height:40px;padding:8px 14px;font-size:13px;\n  }\n\n  /* ── Stats ── */\n  .stats-grid{grid-template-columns:repeat(2,minmax(0,1fr)) !important;gap:8px}\n  .stat-card{padding:10px 12px;border-radius:12px}\n  .stats-chart-wrap{padding:10px;border-radius:12px}\n  .stats-chart-head{flex-direction:column;align-items:stretch;gap:8px}\n  .stats-period{width:100%;display:flex}\n  .stats-period .stab{flex:1;padding:8px 4px;font-size:11.5px;text-align:center}\n  .stats-chart-legend{\n    position:static !important;margin:0 0 6px auto;width:fit-content;\n  }\n  #statsChart{height:132px !important}\n  .stats-table-wrap{max-height:min(46vh,400px);border-radius:12px}\n\n  /* ── Modals / toast ── */\n  .settings-bd,.modal-backdrop{\n    padding:max(8px, env(safe-area-inset-top, 0px)) 10px\n      max(10px, env(safe-area-inset-bottom, 0px));\n  }\n  .settings-modal,.modal{\n    width:100%;max-width:100%;\n    max-height:min(92vh, calc(100dvh - 20px));\n    border-radius:16px;\n  }\n  .toast{\n    top:max(10px, env(safe-area-inset-top, 0px));\n    width:calc(100% - 24px);max-width:none;font-size:13px;\n  }\n}\n@media(max-width:390px){\n  /* iPhone narrow: drop status chips, keep ver + settings + logout */\n  #svcDot,#xttpDot{display:none !important}\n  #btnSettings{font-size:0;padding:8px 10px;line-height:0}\n  #btnSettings svg, #btnSettings > *{font-size:14px;line-height:1}\n  /* if settings is text-only with leading emoji gear */\n  #btnSettings{font-size:13px;max-width:none}\n  .gcard-actions .act-sep{display:none}\n  .gcard-actions .act-tools .mini-btn[data-add-rule],\n  .gcard-actions .mini-btn[title*="экспорт"],\n  .gcard-actions .mini-btn[title*="Экспорт"]{ }\n}\n@media(min-width:721px) and (max-width:900px){\n  .app{padding-left:14px;padding-right:14px}\n  .search-wrap{max-width:280px}\n  .stats-grid{grid-template-columns:repeat(3,minmax(0,1fr))}\n}\n/* —— Explain route —— */\n/* status → color tokens (single mapping; used by stripe / status-dot / action text) */\n:root{\n  --explain-proxy:var(--ok);\n  --explain-direct:var(--ops-stat-gray,#64748B);\n  --explain-block:var(--err);\n}\n.explain-form{display:flex;gap:8px;align-items:stretch;flex-wrap:wrap;margin-bottom:14px}\n.explain-form .field{flex:1;min-width:180px;margin:0}\n.explain-form .field input{width:100%}\n.explain-form .btn{flex-shrink:0;align-self:flex-end}\n.explain-result{display:none;margin-top:4px}\n.explain-result.show{display:block;animation:riseIn var(--dur-med) var(--ease-out) both}\n.explain-card{\n  position:relative;border:1px solid var(--line);border-radius:12px;\n  background:var(--bg);padding:16px 16px 14px;margin-top:4px;\n  /* default status color (DIRECT); classes override --explain-status */\n  --explain-status:var(--explain-direct);\n}\n.explain-card.ind-proxy{--explain-status:var(--explain-proxy)}\n.explain-card.ind-direct{--explain-status:var(--explain-direct)}\n.explain-card.ind-block{--explain-status:var(--explain-block)}\n/* stripe always present — color only via --explain-status */\n.explain-card::before{\n  content:"";position:absolute;left:0;right:0;top:0;height:3px;border-radius:12px 12px 0 0;\n  background:var(--explain-status);\n}\n.explain-ind{\n  display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:600;\n  color:var(--explain-status);margin-bottom:12px;\n}\n.explain-ind-dot{\n  width:9px;height:9px;border-radius:50%;flex-shrink:0;\n  background:var(--explain-status);\n  box-shadow:0 0 0 3px color-mix(in srgb, var(--explain-status) 22%, transparent);\n}\n.explain-chain{display:flex;flex-direction:column;gap:0;position:relative;padding-left:4px}\n.explain-step{\n  display:grid;grid-template-columns:18px 1fr;gap:10px;padding:10px 0;\n  position:relative;\n}\n.explain-step:not(:last-child)::before{\n  content:"";position:absolute;left:7px;top:26px;bottom:-2px;width:2px;\n  background:var(--line2);\n}\n/* Variant A: all 4 step dots identical — accent outline, no fill */\n.explain-step-dot{\n  width:10px;height:10px;border-radius:50%;margin-top:4px;\n  background:transparent;border:2px solid var(--acc);z-index:1;box-sizing:border-box;\n}\n.explain-step-label{font-size:11px;font-weight:600;color:var(--muted2);text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}\n.explain-step-body{font-size:13.5px;color:var(--text);line-height:1.4;word-break:break-word}\n.explain-step-body .mono{font-family:var(--mono);font-size:12.5px}\n.explain-step-body .muted{color:var(--muted2);font-size:12px;margin-top:2px}\n.explain-type-tag{\n  display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;\n  font-size:11px;font-weight:700;letter-spacing:.02em;margin-right:6px;vertical-align:middle;\n}\n.explain-type-tag.ipv4{background:rgba(74,222,128,.16);color:#4ADE80}\n.explain-type-tag.ipv6{background:rgba(167,139,250,.16);color:#A78BFA}\n.explain-type-tag.namespace{background:rgba(56,189,248,.16);color:#38BDF8}\n.explain-type-tag.domain{background:rgba(216,180,254,.16);color:#D8B4FE}\n.explain-type-tag.keyword{background:rgba(253,186,116,.16);color:#FDBA74}\n.explain-type-tag.geoip,.explain-type-tag.match{background:rgba(100,116,139,.2);color:#94A3B8}\n/* action text inherits status color from card mapping */\n.explain-policy{\n  display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:14px;\n  color:var(--explain-status);\n}\n.btn:disabled{opacity:.55;cursor:not-allowed;transform:none !important}\n@media (prefers-reduced-motion:reduce){\n  .explain-result.show{animation:none}\n}\n/* === 60fps motion system === */\n@media (prefers-reduced-motion:no-preference){\n  .chip,.icon-btn,.mini-btn,.btn,.stab,.modal-x,.icon-del,.gcard,.status-pill,.settings-modal,.modal,.ver-dd{\n    backface-visibility:hidden;-webkit-backface-visibility:hidden;\n  }\n  .gcard{will-change:transform}\n  .settings-bd.open .settings-modal,.modal-backdrop.open .modal{will-change:opacity,transform}\n}\n@media (prefers-reduced-motion:reduce){\n  .bg-fx .mesh-a,.bg-fx .mesh-b,.bg-fx .grid{animation:none !important}\n  .stab-pill{transition:none !important}\n  .toast{animation:none !important;transition:none !important}\n}\n/* pause expensive ambient mesh when page not visible (JS toggles .is-paused) */\nbody.ver-changelog-open{overflow:hidden}/* keep pan-y on changelog panel — body touch-action:none blocked mobile scroll */body.ver-changelog-open .ver-dd,body.ver-changelog-open .ver-dd.is-open{overflow-y:auto !important;overflow-x:hidden !important;overscroll-behavior:contain !important;touch-action:pan-y !important;pointer-events:auto !important;-webkit-overflow-scrolling:touch;}.ver-backdrop{touch-action:none}.ver-dd{overflow-y:auto !important;overflow-x:hidden !important;overscroll-behavior:contain;touch-action:pan-y;-webkit-overflow-scrolling:touch;}\n@media(max-width:720px){\n  .ver-dd.is-open{\n    border-radius:16px !important;\n    box-shadow:0 20px 60px rgba(0,0,0,.65) !important;\n  }\n}\n\n/* ── Connections / Logs views ── */\n.conn-head,.logs-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin:0 0 12px}\n.conn-meta,.logs-foot{font-size:12.5px;color:var(--muted)}\n.conn-actions{display:flex;gap:8px;flex-wrap:wrap}\n.conn-table-wrap{max-height:min(62vh,560px);overflow:auto;border:1px solid var(--line);border-radius:12px;background:var(--bg)}\n.conn-table-wrap .stats-table{width:100%;border-collapse:collapse;font-size:12.5px}\n.conn-table-wrap th{position:sticky;top:0;z-index:2;background:var(--bg2);text-align:left;padding:10px 12px;font-size:11px;color:var(--muted2);border-bottom:1px solid var(--line)}\n.conn-table-wrap td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}\n.conn-table-wrap tr:hover td{background:rgba(255,255,255,.03)}\n.conn-host{font-family:var(--mono);font-size:12px;word-break:break-all}\n.conn-chain{font-size:12px;color:var(--muted)}\n.conn-bytes{font-family:var(--mono);font-size:11.5px;white-space:nowrap}\n.conn-kill{padding:6px 10px !important;font-size:12px !important;min-height:36px}\n.logs-level .stab,.logs-mode .stab{padding:8px 10px;font-size:12.5px}\n.logs-view-wrap{border:1px solid var(--line);border-radius:12px;background:#0a0b0e;max-height:min(58vh,520px);overflow:auto}\n.logs-view{margin:0;padding:12px 14px;font-family:var(--mono);font-size:11.5px;line-height:1.45;white-space:pre-wrap;word-break:break-word;color:#c8cdd8;min-height:200px}\n.logs-view .log-line{display:block;padding:1px 0}\n.logs-view .log-line.lvl-debug{color:#64748b}\n.logs-view .log-line.lvl-info{color:#94a3b8}\n.logs-view .log-line.lvl-warning{color:#fbbf24}\n.logs-view .log-line.lvl-error{color:#f87171}\n.logs-view .log-ts{color:#475569;margin-right:8px}\n@media(max-width:720px){\n  .conn-table-wrap{max-height:min(55vh,480px)}\n  .logs-view-wrap{max-height:min(50vh,420px)}\n  #viewSwitch .stab{font-size:12px;padding:8px 6px}\n}\n\n</style>\n</head>\n<body>\n<div class="bg-fx" aria-hidden="true">\n  <div class="mesh mesh-a"></div>\n  <div class="mesh mesh-b"></div>\n  <div class="grid"></div>\n  <div class="noise"></div>\n  <div class="vignette"></div>\n</div>\n<div class="app">\n  <header class="topbar">\n    <div class="brand">\n      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>\n      xttp panel\n    </div>\n    <div class="top-right">\n      <div class="ver-wrap" tabindex="0">\n        <button type="button" class="chip ver-chip" id="verChip" aria-haspopup="true" aria-expanded="false" aria-controls="verDd" onclick="try{window.__toggleVerChangelog&&window.__toggleVerChangelog(event)}catch(e){}" style="pointer-events:auto;cursor:pointer;opacity:1;-webkit-user-select:none">v@@VERSION@@</button>\n        <div class="ver-dd" id="verDd" role="menu" aria-label="История версий">\n                                                                      <div class="ver-item current">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.18.0</span><span class="ver-badge">CURRENT</span></div>\n              <div class="ver-date">0.18.0</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Ядро: клик по чипам версий — явный спиннер, toast, debug-строка, document-delegation</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n<div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.17.2</span></div>\n              <div class="ver-date">0.17.2</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Ядро: клик по чипам mihomo/xray/panel — проверка обновлений (GitHub / changelog)</li>\n                  <li>Ядро: обновление mihomo/xray с бэкапом, healthcheck и откатом; panel — вручную</li>\n                </ul>\n              </div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Убран чип Ubuntu/kernel из блока версий</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n<div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.17.1</span></div>\n              <div class="ver-date">0.17.1</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Ядро: блок «Версии компонентов» (mihomo / xray / panel / os), кеш 8 мин</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n<div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.17.0</span></div>\n              <div class="ver-date">0.17.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Ядро: TUN-статус (read-only) + sniffing toggle</li>\n                  <li>Ядро: автообновление geo-баз, интервал (часы), «Обновить сейчас»</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n<div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.9</span></div>\n              <div class="ver-date">0.16.9</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Changelog: версии 0.15.x–0.16.x разнесены по отдельным пунктам (не в одном CURRENT)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.8</span></div>\n              <div class="ver-date">0.16.8</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Настройки: mesh за scrim + скрыт topbar/список (без имён сервисов)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.7</span></div>\n              <div class="ver-date">0.16.7</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Настройки: ambient mesh виден за полупрозрачным scrim (первая итерация)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.6</span></div>\n              <div class="ver-date">0.16.6</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Ambient mesh-фон панели восстановлен/усилен (под карточками, z-index 0)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.5</span></div>\n              <div class="ver-date">0.16.5</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Ядро: tooltip «?» fixed + flip/clamp в viewport (портал в body, не обрезается)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.4</span></div>\n              <div class="ver-date">0.16.4</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Ядро: карточки grid по контенту (align-items:start — не тянуть «Уровень логов»)</li>\n                  <li>Ядро: tooltip «?» с читаемым текстом (.core-tip-pop)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.3</span></div>\n              <div class="ver-date">0.16.3</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Настройки: scrim/карточка 100% solid #12141a (без rgba alpha)</li>\n                  <li>Ядро: tooltip «?» не наезжает на соседние карточки</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.2</span></div>\n              <div class="ver-date">0.16.2</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Настройки/модалки: плотный scrim + solid surface (логи не просвечивают)</li>\n                  <li>Tooltip «?» и changelog: непрозрачный фон</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.1</span></div>\n              <div class="ver-date">0.16.1</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Ядро: GET /api/core больше не падает (порты / Failed to fetch)</li>\n                  <li>Ядро: пояснения к mode / log-level / find-process + порты списком</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.16.0</span></div>\n              <div class="ver-date">0.16.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Настройки → <strong>Ядро</strong>: mode / log-level / allow-lan / IPv6 / delay / TCP (mihomo API, без MetaCube)</li>\n                  <li>Обзор всех правил (иконка списка) — фильтр и переход к группе</li>\n                  <li>MetaCube chip убран из topbar</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.15.3</span></div>\n              <div class="ver-date">0.15.3</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Логи: новые записи сверху (обратный хронопорядок)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.15.2</span></div>\n              <div class="ver-date">0.15.2</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Вкладки Соединения/Логи: добавлены click-handlers</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.15.1</span></div>\n              <div class="ver-date">0.15.1</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Hotfix: кнопки снова кликабельны (TDZ searchInput ломал весь JS)</li>\n                  <li>Сегмент: Правила | Статистика | Соединения | Логи</li>\n                  <li>Соединения: live + kill; Логи: stream /logs + фильтр уровня</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n<div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.18</span></div>\n              <div class="ver-date">0.14.18</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Тест: MetaCube UI отключён (external-ui), API :9090 и панель 9080 работают</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.17</span></div>\n              <div class="ver-date">0.14.17</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Правила: список не пропадает после быстрого Rules⇄Stats (без hide-timeout; gcard opacity:1)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.16</span></div>\n              <div class="ver-date">0.14.16</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Статистика: детерминированные time-bucket’ы (1ч/5м) — форма истории не «плывёт» между тиками</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.15</span></div>\n              <div class="ver-date">0.14.15</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Правила/Статистика: нет пустого списка после быстрого переключения вкладок (race hide timeout)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.14</span></div>\n              <div class="ver-date">0.14.14</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Статистика: live-график плавно «течёт» (rAF-окно + lerp tip, без 1Hz мигания)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.13</span></div>\n              <div class="ver-date">0.14.13</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Changelog: wheel/touch скролл списка версий (portal + overflow-y + overscroll)</li>\n                  <li>Страница под дропдауном больше не перехватывает прокрутку</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.12</span></div>\n              <div class="ver-date">0.14.12</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Mobile: toggle правил снова ~46×26 (не раздутый 70px+)</li>\n                  <li>Mobile: sticky topbar непрозрачный, список не просвечивает</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.11</span></div>\n              <div class="ver-date">0.14.11</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Mobile: правила как 2-строчные карточки (тип+toggle / полный паттерн)</li>\n                  <li>Mobile: компактные icon-btn справа (save / explain / +)</li>\n                  <li>Mobile: «N правил» + теги в одну линию под именем группы</li>\n                  <li>Mobile: тап по паттерну → toast «Копировать»</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.10</span></div>\n              <div class="ver-date">0.14.10</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>iPhone: changelog открывается (portal + onclick + backdrop)</li>\n                  <li>iPhone: кнопка changelog кликабельна (pointer-events)</li>\n                  <li>iPhone: имена групп снова видны (grid-карточки)</li>\n                  <li>iPhone: табы и кнопки toolbar на всю ширину</li>\n                </ul>\n              </div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Мобильная вёрстка: компактная шапка, toolbar, карточки, safe-area</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.14.0</span></div>\n              <div class="ver-date">0.14.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Статистика: история графика на сервере (~1 ч, переживает F5)</li>\n                  <li>График: линии без петель (монотонный bezier)</li>\n                  <li>Статистика: плавный график ~60 fps (rAF, bezier, soft-fill)</li>\n                  <li>Статистика: табы периода Live / 5 мин / 1 час / Сессия</li>\n                  <li>Чекбоксы линий download/upload; tooltip ↓/↑ при наведении</li>\n                </ul>\n              </div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Статистика: табы периода реально меняют окно графика (ось по времени)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n<div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.13.4</span></div>\n              <div class="ver-date">0.13.4</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Настройки: pill вкладок не перекашивается при открытии со Статистики</li>\n                  <li>Разделены .stab[data-stab] и view-switch .stab[data-view]</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.13.3</span></div>\n              <div class="ver-date">0.13.3</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Статистика: opaque sticky-заголовок таблицы при скролле</li>\n                  <li>Подпись метрики: «срабатываний правил всего»</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.13.2</span></div>\n              <div class="ver-date">0.13.2</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Настройки: нет RGB-артефакта заголовка при открытии со Статистики</li>\n                  <li>Пауза live-графика и isolation/opacity-only анимация модалки</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.13.1</span></div>\n              <div class="ver-date">0.13.1</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Статистика: легенда на canvas, колонка «Доля», скролл таблицы</li>\n                  <li>Карточки по-русски; подпись вместо RAM 0 B</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.13.0</span></div>\n              <div class="ver-date">0.13.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Режимы Правила / Статистика (segmented control)</li>\n                  <li>Дашборд: скорость, трафик, соединения, hits по группам, live-график</li>\n                  <li>Поиск в Статистике фильтрует таблицу; состояние в localStorage</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.9</span></div>\n              <div class="ver-date">0.12.9</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Поиск: фильтр строк по паттерну не сбрасывается из-за data-filter группы</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.8</span></div>\n              <div class="ver-date">0.12.8</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Импорт: починен SyntaxError в .join(newline) — кнопки снова кликабельны</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.7</span></div>\n              <div class="ver-date">0.12.7</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Toast после действий через flash-cookie, без ?msg= в URL</li>\n                </ul>\n              </div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Чистый URL после импорта / apply / других POST</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.6</span></div>\n              <div class="ver-date">0.12.6</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Импорт: чекбокс «Не импортировать дубли и конфликты»</li>\n                  <li>При включённом — на бэк только «новые», кнопка со счётчиком</li>\n                  <li>Дубли/конфликты в превью: opacity + «будет пропущено»</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.5</span></div>\n              <div class="ver-date">0.12.5</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Защита groups.json: lock на load/mutate/save</li>\n                  <li>Автобэкап groups.json (до 40 копий в groups-backups)</li>\n                  <li>Блокировка обнуления и catastrophic shrink списка</li>\n                  <li>При apply: stale yaml → quarantine, не hard-delete</li>\n                  <li>При битом JSON — restore из последнего бэкапа</li>\n                </ul>\n              </div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>Гонка ThreadingHTTPServer больше не перетирает группы</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.4</span></div>\n              <div class="ver-date">0.12.4</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Фильтр конфликтов: внутри группы только конфликтные строки + «N из M»</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.3</span></div>\n              <div class="ver-date">0.12.3</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>Фильтр конфликтов: скрытие групп, focus, сброс</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.2</span></div>\n              <div class="ver-date">0.12.2</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>Пустой conflict-badge: .pill перебивал [hidden]</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.1</span></div>\n              <div class="ver-date">0.12.1</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>Конфликт: акцент только на 1-й ячейке; сводка в футере</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.12.0</span></div>\n              <div class="ver-date">0.12.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>Конфликты правил: иконка группы, строки, фильтр</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.11.4</span></div>\n              <div class="ver-date">0.11.4</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>Импорт: убран type-select, статичный бейдж типа</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.11.3</span></div>\n              <div class="ver-date">0.11.3</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>Импорт: дубль/конфликт — бейдж + left border, без заливки</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.11.2</span></div>\n              <div class="ver-date">0.11.2</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>Импорт: сводка, дубли/конфликты, правка типа, «Режим типа»</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.11.1</span></div>\n              <div class="ver-date">0.11.1</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>Explain route: полоска/кружки/MATCH, статус BLOCK</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.11.0</span></div>\n              <div class="ver-date">0.11.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>Explain route — домен/IP → правило → группа → PROXY/DIRECT</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.10.3</span></div>\n              <div class="ver-date">0.10.3</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>Операции: статус-карточки, цвет точек журнала, auto-refresh лога</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.10.2</span></div>\n              <div class="ver-date">0.10.2</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>авто «Проверить связь» при запуске и на вкладке Подключение</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.10.1</span></div>\n              <div class="ver-date">0.10.1</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>group_add / rename / toggle в activity log</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.10.0</span></div>\n              <div class="ver-date">0.10.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Операции: activity log, restart xray/mihomo, live svc, auto-ping</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.9.1</span></div>\n              <div class="ver-date">0.9.1</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>soft group hover</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.9.0</span></div>\n              <div class="ver-date">0.9.0</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>topbar без «полосы»</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.9</span></div>\n              <div class="ver-date">0.8.9</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>glass topbar (недостаточно — см. 0.9.0)</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.8</span></div>\n              <div class="ver-date">0.8.8</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>ambient mesh-фон панели</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.7</span></div>\n              <div class="ver-date">0.8.7</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>UX polish: stripe, иконки, теги, поиск, empty-states</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.6</span></div>\n              <div class="ver-date">0.8.6</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>выход: cookie-only, Basic Auth не держит сессию</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.5</span></div>\n              <div class="ver-date">0.8.5</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list">\n                  <li>настройки: шапка + вкладки отдельно от скролла (без наезда контента)</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.4</span></div>\n              <div class="ver-date">0.8.4</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>«Настройки» + × закреплены при скролле модалки</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.3</span></div>\n              <div class="ver-date">0.8.3</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>ping: подписи TCP vs туннель, warm-up для HTTP-probe</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.2</span></div>\n              <div class="ver-date">0.8.2</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>плавные вкладки, прозрачный scroll, статус «Не проверено»</li>\n                  <li>подписи: Подключение / Замена конфигурации</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.1</span></div>\n              <div class="ver-date">0.8.1</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>motion redesign + versioning с <code>0.1.0</code></li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.8.0</span></div>\n              <div class="ver-date">0.8.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list">\n                  <li>Настройки: пользователи (admin), xttp статус / ping / speedtest</li>\n                  <li>замена ноды по <code>vless://</code> + backup/rollback</li>\n                </ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.7.5</span></div>\n              <div class="ver-date">0.7.5</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>прозрачный scrollbar в changelog dropdown</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.7.4</span></div>\n              <div class="ver-date">0.7.4</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>ссылка <strong>MetaCube</strong></li><li>changelog dropdown по версии (как MetaCube)</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.7.3</span></div>\n              <div class="ver-date">0.7.3</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>брендинг <strong>xttp panel</strong></li><li>автоскрытие toast</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.7.2</span></div>\n              <div class="ver-date">0.7.2</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>чистый URL после logout</li><li><code>replaceState</code> на логине</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.7.1</span></div>\n              <div class="ver-date">0.7.1</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>полноэкранный mesh-фон логина</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.7.0</span></div>\n              <div class="ver-date">0.7.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>своя форма входа (вместо Basic Auth)</li><li>cookie-сессия 7 дней</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.6.4</span></div>\n              <div class="ver-date">0.6.4</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>удаление/toggle группы → apply + restart mihomo</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.6.3</span></div>\n              <div class="ver-date">0.6.3</div>\n              <div class="ver-sec"><div class="ver-sec-title fix">Bug Fixes</div>\n                <ul class="ver-list"><li>PRG-редирект: URL не залипает на action-path</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.6.2</span></div>\n              <div class="ver-date">0.6.2</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>поиск по всем группам (имя + паттерны)</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.6.1</span></div>\n              <div class="ver-date">0.6.1</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>RuleSet имена: <code>g_Telegram</code>…</li><li>rules: GEOIP private + groups + MATCH</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.6.0</span></div>\n              <div class="ver-date">0.6.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>«+» в шапке группы, AJAX add/delete</li><li>авто-detect IPV4 / NAMESPACE</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.5.0</span></div>\n              <div class="ver-date">0.5.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>группы свёрнуты по умолчанию</li><li>убран PROXY-бейдж, без RAW-подписок</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.4.0</span></div>\n              <div class="ver-date">0.4.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>карточки групп MagiTrickle-style</li><li>импорт списка с превью типов</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.3.0</span></div>\n              <div class="ver-date">0.3.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>модель групп + <code>groups.json</code></li><li>classical rule-providers</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.2.0</span></div>\n              <div class="ver-date">0.2.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>тёмная тема UI</li><li>ручные правила + remote lists</li></ul>\n              </div>\n            </div>\n          </div>\n          <div class="ver-item">\n            <span class="ver-dot"></span>\n            <div>\n              <div class="ver-head"><span class="ver-tag">v0.1.0</span></div>\n              <div class="ver-date">0.1.0</div>\n              <div class="ver-sec"><div class="ver-sec-title">Features</div>\n                <ul class="ver-list"><li>первый lists UI для mihomo gateway</li><li>apply + restart · listed → PROXY, else DIRECT</li></ul>\n              </div>\n            </div>\n          </div>\n        </div>\n      </div>\n      <span class="chip chip-status" id="svcDot" title="Сервисы xray / mihomo">svc · …</span>\n      <span class="chip chip-status" id="xttpDot" title="Статус xttp">xttp · …</span>\n      <button type="button" class="chip chip-action" id="btnSettings" title="Настройки">⚙ Настройки</button>\n      <a class="link-logout" href="/logout" id="btnLogout" title="Выйти из панели">Выйти</a>\n    </div>\n  </header>\n  @@TOAST@@\n  <div class="toolbar">\n    <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">\n      <div class="search-wrap open" id="searchWrap">\n        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3-3"/></svg>\n        <input id="searchInput" type="search" placeholder="Поиск сервисов…" aria-label="Поиск сервисов"/>\n      </div>\n      <button type="button" class="conflict-filter-link" id="conflictFilter" data-show="0" hidden aria-pressed="false"></button>\n      <div class="view-switch stabs" id="viewSwitch" role="tablist" aria-label="Режим">\n        <span class="stab-pill" id="viewPill" aria-hidden="true"></span>\n        <button type="button" class="stab active" data-view="rules" id="viewRules" role="tab" aria-selected="true">Правила</button>\n        <button type="button" class="stab" data-view="stats" id="viewStats" role="tab" aria-selected="false">Статистика</button>\n        <button type="button" class="stab" data-view="conns" id="viewConns" role="tab" aria-selected="false">Соединения</button>\n        <button type="button" class="stab" data-view="logs" id="viewLogs" role="tab" aria-selected="false">Логи</button>\n      </div>\n    </div>\n    <div class="toolbar-right" id="toolbarRulesActions">\n      <form method="post" action="/apply">\n        <button type="submit" class="icon-btn" aria-label="Применить" title="Записать rule-providers и restart mihomo">\n          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/></svg>\n        </button>\n      </form>\n      <button type="button" class="icon-btn" id="btnRulesOverview" aria-label="Обзор правил" title="Обзор всех правил">\n        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">\n          <line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/>\n          <line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>\n        </svg>\n      </button>\n      <button type="button" class="icon-btn" id="btnExplain" aria-label="Explain route" title="Explain route — куда уйдёт трафик">\n        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">\n          <circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="6" cy="5" r="2"/>\n          <path d="M6 7v10"/><path d="M6 5h8a4 4 0 0 1 0 8H6"/>\n        </svg>\n      </button>\n      <button type="button" class="icon-btn primary" id="btnAdd" aria-label="Новая группа" title="Новая группа">\n        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>\n      </button>\n    </div>\n  </div>\n\n  <div class="view-stage" id="viewStage">\n    <section id="panel-groups" class="panel view-panel" data-view-panel="rules" role="tabpanel">@@GROUPS@@</section>\n    <section id="panel-stats" class="panel view-panel" data-view-panel="stats" role="tabpanel" hidden>\n      <div class="stats-grid" id="statsCards">\n        <div class="stat-card"><div class="stat-label">Скорость ↓</div><div class="stat-value" id="statDownRate">—</div><div class="stat-sub" id="statDownTotal">всего —</div></div>\n        <div class="stat-card"><div class="stat-label">Скорость ↑</div><div class="stat-value" id="statUpRate">—</div><div class="stat-sub" id="statUpTotal">всего —</div></div>\n        <div class="stat-card"><div class="stat-label">Соединения</div><div class="stat-value" id="statConns">—</div><div class="stat-sub" id="statConnSub">—</div></div>\n        <div class="stat-card"><div class="stat-label">Обращения к правилам</div><div class="stat-value" id="statHits">—</div><div class="stat-sub">срабатываний правил всего</div></div>\n      </div>\n      <div class="stats-chart-wrap">\n        <div class="stats-chart-head">\n          <div class="stats-chart-title" id="statsChartTitle">Трафик (live)</div>\n          <div class="stats-period stabs" id="statsPeriod" role="tablist" aria-label="Период">\n            <span class="stab-pill" id="statsPeriodPill" aria-hidden="true"></span>\n            <button type="button" class="stab active" data-period="live">Live</button>\n            <button type="button" class="stab" data-period="5m">5 мин</button>\n            <button type="button" class="stab" data-period="1h">1 час</button>\n            <button type="button" class="stab" data-period="session">Сессия</button>\n          </div>\n        </div>\n        <div class="stats-chart-body">\n          <div class="stats-chart-legend" id="statsLineToggles">\n            <label class="stats-line-toggle"><input type="checkbox" id="lineDown" checked/><i class="dn"></i><span>download</span></label>\n            <label class="stats-line-toggle"><input type="checkbox" id="lineUp" checked/><i class="up"></i><span>upload</span></label>\n          </div>\n          <canvas id="statsChart" width="900" height="160" aria-label="График трафика"></canvas>\n          <div class="stats-tooltip" id="statsTooltip" hidden></div>\n        </div>\n      </div>\n      <div class="stats-table-wrap">\n        <table class="stats-table">\n          <thead><tr><th>Группа</th><th>Hits</th><th>Conn</th><th>↓</th><th>↑</th><th>Доля</th></tr></thead>\n          <tbody id="statsBody"><tr><td colspan="6" class="stats-empty">Загрузка…</td></tr></tbody>\n        </table>\n      </div>\n      <p class="stats-meta" id="statsMeta"></p>\n    </section>\n    <section id="panel-conns" class="panel view-panel" data-view-panel="conns" role="tabpanel" hidden>\n      <div class="conn-head">\n        <div class="conn-meta" id="connMeta">—</div>\n        <div class="conn-actions">\n          <button type="button" class="btn btn-ghost" id="btnRefreshConns" style="padding:8px 12px;font-size:13px">Обновить</button>\n          <button type="button" class="btn btn-ghost" id="btnKillAllConns" style="padding:8px 12px;font-size:13px;color:var(--err)">Разорвать все</button>\n        </div>\n      </div>\n      <div class="stats-table-wrap conn-table-wrap">\n        <table class="stats-table" id="connsTable">\n          <thead>\n            <tr>\n              <th>Домен / цель</th>\n              <th>Правило</th>\n              <th>Цепочка</th>\n              <th>Протокол</th>\n              <th>↓ / ↑</th>\n              <th>Длит.</th>\n              <th></th>\n            </tr>\n          </thead>\n          <tbody id="connsBody">\n            <tr><td colspan="7" class="stats-empty">Загрузка…</td></tr>\n          </tbody>\n        </table>\n      </div>\n    </section>\n    <section id="panel-logs" class="panel view-panel" data-view-panel="logs" role="tabpanel" hidden>\n      <div class="logs-head">\n        <div class="stabs logs-level" id="logsLevel" role="tablist" aria-label="Уровень логов">\n          <button type="button" class="stab" data-log-level="debug">debug</button>\n          <button type="button" class="stab active" data-log-level="info">info</button>\n          <button type="button" class="stab" data-log-level="warning">warning</button>\n          <button type="button" class="stab" data-log-level="error">error</button>\n        </div>\n        <div class="stabs logs-mode" id="logsMode" role="tablist" aria-label="Окно логов">\n          <button type="button" class="stab active" data-log-mode="tail">последние 200</button>\n          <button type="button" class="stab" data-log-mode="live">с текущего момента</button>\n        </div>\n        <button type="button" class="btn btn-ghost" id="btnClearLogs" style="padding:8px 12px;font-size:13px">Очистить вид</button>\n      </div>\n      <div class="logs-view-wrap">\n        <pre class="logs-view" id="logsView" aria-live="polite"></pre>\n      </div>\n      <div class="logs-foot meta" id="logsMeta">—</div>\n    </section>\n\n  </div>\n</div>\n\n<div class="modal-backdrop" id="modalGroup" aria-hidden="true">\n  <div class="modal" role="dialog">\n    <button type="button" class="modal-x" data-close aria-label="Закрыть">×</button>\n    <h2>Новая группа</h2>\n    <p class="sub">Группа → classical rule-set → PROXY. Внутри: IP-CIDR / DOMAIN-SUFFIX. Тип строки определяется автоматически.</p>\n    <form method="post" action="/group-add">\n      <div class="field"><label for="gname">Название</label>\n        <input id="gname" name="name" placeholder="Впишите название группы" required autofocus/></div>\n      <div class="modal-actions">\n        <button type="button" class="btn btn-ghost" data-close>Отмена</button>\n        <button type="submit" class="btn btn-primary">Создать</button>\n      </div>\n    </form>\n  </div>\n</div>\n\n<div class="modal-backdrop" id="modalExplain" aria-hidden="true">\n  <div class="modal" role="dialog" aria-labelledby="explain-title">\n    <button type="button" class="modal-x" data-close aria-label="Закрыть">×</button>\n    <h2 id="explain-title">Explain route</h2>\n    <p class="sub">Проверка, куда уйдёт трафик: домен или IP против правил групп (тот же порядок, что у mihomo).</p>\n    <form id="explainForm" class="explain-form" autocomplete="off">\n      <div class="field">\n        <label for="explainInput">Домен или IP</label>\n        <input id="explainInput" name="q" type="text" placeholder="Введите домен или IP…" required autofocus/>\n      </div>\n      <button type="submit" class="btn btn-primary" id="btnExplainRun">Проверить маршрут</button>\n    </form>\n    <div id="explainResult" class="explain-result" aria-live="polite"></div>\n  </div>\n</div>\n\n<!-- Settings -->\n<div class="settings-bd" id="settingsBd" aria-hidden="true">\n  <div class="settings-modal" role="dialog" aria-labelledby="set-title">\n    <div class="settings-head">\n      <h2 id="set-title">Настройки</h2>\n      <button type="button" class="modal-x" id="settingsClose" aria-label="Закрыть">×</button>\n    </div>\n    <div class="settings-nav">\n      <div class="stabs" role="tablist">\n        <span class="stab-pill" id="stabPill" aria-hidden="true"></span>\n        <button type="button" class="stab active" data-stab="core">Ядро</button>\n        <button type="button" class="stab" data-stab="users">Пользователи</button>\n        <button type="button" class="stab" data-stab="xttp">Подключение</button>\n        <button type="button" class="stab" data-stab="ops">Операции</button>\n      </div>\n    </div>\n    <div class="settings-body">\n    <div class="spanels">\n    <div id="spanel-core" class="spanel active">\n      <p class="sub" style="margin-top:0">Runtime-настройки mihomo. Меняются сразу через API ядра — MetaCube не нужен.</p>\n      <p class="core-meta" id="coreMeta">—</p>\n      <div class="core-versions" id="coreVersions" aria-label="Версии компонентов"></div>\n      <p class="core-ver-debug" id="coreVerDebug" aria-live="polite"></p>\n      <div class="btn-row" style="margin:0 0 14px">\n        <button type="button" class="btn btn-ghost" id="btnFleetUpdateNow" style="padding:8px 12px;font-size:13px" title="Запустить check-and-update (как timer)">Проверить обновления сейчас</button>\n      </div>\n      <div class="core-ports-head">\n        <div>\n          <div class="ct-lab">Порты</div>\n          <div class="ct-sub">Только просмотр: 0 или пусто = выключен</div>\n        </div>\n      </div>\n      <div class="core-ports" id="corePorts" aria-live="polite"></div>\n      <div class="core-grid">\n        <div class="core-field">\n          <div class="core-field-lab">\n            <label for="coreMode">Режим</label>\n            <span class="core-tip" tabindex="0" aria-label="Справка: режим">?<span class="core-tip-pop" role="tooltip">Rule — трафик идёт по вашим правилам (обычный режим). Global — весь трафик через прокси. Direct — весь трафик напрямую, без прокси.</span></span>\n          </div>\n          <select id="coreMode">\n            <option value="rule">rule</option>\n            <option value="global">global</option>\n            <option value="direct">direct</option>\n          </select>\n          <div class="ct-sub">Rule — по правилам. Global — всё в прокси. Direct — всё напрямую.</div>\n        </div>\n        <div class="core-field">\n          <div class="core-field-lab">\n            <label for="coreLogLevel">Уровень логов</label>\n            <span class="core-tip" tabindex="0" aria-label="Справка: уровень логов">?<span class="core-tip-pop" role="tooltip">Silent — логов нет. Error/Warning — только сбои. Info — важные события. Debug — подробный вывод (много данных).</span></span>\n          </div>\n          <select id="coreLogLevel">\n            <option value="silent">silent</option>\n            <option value="error">error</option>\n            <option value="warning">warning</option>\n            <option value="info">info</option>\n            <option value="debug">debug</option>\n          </select>\n          <div class="ct-sub">Silent — без логов. Info — важное. Debug — максимум деталей.</div>\n        </div>\n        <div class="core-field">\n          <div class="core-field-lab">\n            <label for="coreFindProcess">Find process</label>\n            <span class="core-tip" tabindex="0" aria-label="Справка: find process">?<span class="core-tip-pop" role="tooltip">Off — не определять процесс-источник. Strict — точное определение (может замедлить). Always — всегда пытаться определить процесс.</span></span>\n          </div>\n          <select id="coreFindProcess">\n            <option value="off">off</option>\n            <option value="strict">strict</option>\n            <option value="always">always</option>\n          </select>\n          <div class="ct-sub">Off — не искать процесс. Strict — точно (медленнее). Always — всегда пытаться.</div>\n        </div>\n      </div>\n      <div class="core-toggles">\n        <div class="core-toggle-row">\n          <div><div class="ct-lab">Allow LAN</div><div class="ct-sub">Разрешить доступ с устройств в локальной сети</div></div>\n          <label class="switch"><input type="checkbox" id="coreAllowLan"/><span></span></label>\n        </div>\n        <div class="core-toggle-row">\n          <div><div class="ct-lab">IPv6</div><div class="ct-sub">Обрабатывать IPv6-трафик в ядре</div></div>\n          <label class="switch"><input type="checkbox" id="coreIpv6"/><span></span></label>\n        </div>\n        <div class="core-toggle-row">\n          <div><div class="ct-lab">Unified delay</div><div class="ct-sub">Одинаковый способ замера задержки для нод</div></div>\n          <label class="switch"><input type="checkbox" id="coreUnifiedDelay"/><span></span></label>\n        </div>\n        <div class="core-toggle-row">\n          <div><div class="ct-lab">TCP concurrent</div><div class="ct-sub">Параллельные TCP-подключения (быстрее, больше нагрузка)</div></div>\n          <label class="switch"><input type="checkbox" id="coreTcpConcurrent"/><span></span></label>\n        </div>\n      </div>\n\n      <div class="core-section" id="coreTunGeo">\n        <div class="core-section-title">TUN-интерфейс</div>\n        <p class="core-section-sub">Только просмотр — runtime-статус mihomo (вкл/выкл из UI пока недоступен)</p>\n        <div class="core-tun-list" id="coreTun" aria-live="polite"></div>\n\n        <div class="core-toggles" style="margin-top:14px">\n          <div class="core-toggle-row">\n            <div>\n              <div class="ct-lab">Определение протокола (sniffing)</div>\n              <div class="ct-sub">Определяет реальный протокол по содержимому пакета, а не только по порту — повышает точность маршрутизации</div>\n            </div>\n            <label class="switch"><input type="checkbox" id="coreSniffing"/><span></span></label>\n          </div>\n          <div class="core-toggle-row">\n            <div>\n              <div class="ct-lab">Автообновление geo-баз</div>\n              <div class="ct-sub">Периодически подтягивает свежие geoip/geosite базы для точной гео-маршрутизации</div>\n            </div>\n            <div class="core-toggle-actions">\n              <button type="button" class="btn btn-ghost" id="btnGeoUpdateNow" style="padding:7px 11px;font-size:12.5px" title="Принудительное обновление geo-баз">Обновить сейчас</button>\n              <label class="switch"><input type="checkbox" id="coreGeoAuto"/><span></span></label>\n            </div>\n          </div>\n        </div>\n        <div class="core-geo-interval" id="coreGeoIntervalWrap" hidden>\n          <span class="meta">Интервал обновления, часов</span>\n          <input type="number" id="coreGeoInterval" min="1" max="168" value="24"/>\n          <button type="button" class="btn btn-primary" id="btnGeoIntervalSave" style="padding:8px 14px;font-size:13px">Сохранить</button>\n        </div>\n        <p class="core-geo-src" id="coreGeoSources">geoip.dat, geosite.dat — MetaCubeX/meta-rules-dat</p>\n      </div>\n\n      <div class="btn-row">\n        <button type="button" class="btn btn-primary" id="btnCoreSave" style="padding:8px 14px;font-size:13px">Применить</button>\n        <button type="button" class="btn btn-ghost" id="btnCoreRefresh" style="padding:8px 14px;font-size:13px">Обновить</button>\n      </div>\n    </div>\n    <div id="spanel-users" class="spanel">\n      <p class="sub" style="margin-top:0">Все пользователи — admin. Можно блокировать и удалять (кроме себя / последнего).</p>\n      <div class="btn-row">\n        <button type="button" class="btn btn-primary" id="btnUserAdd" style="padding:8px 14px;font-size:13px">+ Пользователь</button>\n      </div>\n      <table class="utable">\n        <thead><tr><th>Логин</th><th>Статус</th><th>Вход</th><th></th></tr></thead>\n        <tbody id="usersBody"></tbody>\n      </table>\n      <form id="userAddForm" style="display:none;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)">\n        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">\n          <div class="field" style="margin:0"><label>Логин</label><input name="username" required pattern="[A-Za-z0-9_.-]{2,32}" placeholder="operator"/></div>\n          <div class="field" style="margin:0"><label>Пароль</label><input name="password" type="password" required minlength="6" placeholder="мин. 6 символов"/></div>\n        </div>\n        <div class="btn-row">\n          <button type="submit" class="btn btn-primary" style="padding:8px 14px;font-size:13px">Создать</button>\n          <button type="button" class="btn btn-ghost" id="userAddCancel" style="padding:8px 14px;font-size:13px">Отмена</button>\n        </div>\n      </form>\n    </div>\n    <div id="spanel-xttp" class="spanel">\n      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">\n        <span class="status-pill unk" id="xttpStatusPill">Не проверено</span>\n        <span class="meta" id="xttpServices"></span>\n      </div>\n      <dl class="kv" id="xttpKv"></dl>\n      <div class="metric">\n        <div class="box"><div class="n empty-metric" id="mTcp">не измерено</div><div class="l">TCP до порта ноды</div></div>\n        <div class="box"><div class="n empty-metric" id="mHttp">не измерено</div><div class="l">Туннель (socks→нода→net)</div></div>\n        <div class="box"><div class="n empty-metric" id="mSpeed">не измерено</div><div class="l">Speedtest</div></div>\n      </div>\n      <p class="sub" id="pingHint" style="margin:0 0 10px;font-size:12px">TCP — только открытие порта. «Туннель» — полный путь socks→xray→нода→net (HTTPS), обычно 100–500&nbsp;ms.</p>\n      <div class="btn-row">\n        <button type="button" class="btn btn-primary" id="btnXttpPing" style="padding:8px 14px;font-size:13px">Проверить связь</button>\n        <button type="button" class="btn btn-ghost" id="btnXttpSpeed" style="padding:8px 14px;font-size:13px">Speedtest (~5 MB)</button>\n        <button type="button" class="btn btn-ghost" id="btnXttpRefresh" style="padding:8px 14px;font-size:13px">Обновить</button>\n      </div>\n      <hr style="border:0;border-top:1px solid var(--line);margin:16px 0"/>\n      <h3 style="margin:0 0 6px;font-size:14px">Замена конфигурации</h3>\n      <p class="sub" style="margin:0 0 10px">Вставьте <code>vless://…</code> — backup, запись xray, restart, проверка.</p>\n      <div class="field"><label for="vlessIn">vless:// ссылка</label>\n        <textarea id="vlessIn" placeholder="vless://uuid@host:port?security=reality&amp;…"></textarea></div>\n      <div id="vlessPreview" class="meta" style="margin-bottom:8px"></div>\n      <div class="btn-row">\n        <button type="button" class="btn btn-ghost" id="btnVlessPreview" style="padding:8px 14px;font-size:13px">Превью</button>\n        <button type="button" class="btn btn-primary" id="btnVlessApply" style="padding:8px 14px;font-size:13px">Применить</button>\n      </div>\n      <div style="margin-top:12px">\n        <div class="meta" style="margin-bottom:6px">Бэкапы xray</div>\n        <div id="backupList" class="meta"></div>\n      </div>\n    </div>\n    <div id="spanel-ops" class="spanel">\n      <p class="sub" style="margin-top:0">Сервисы, auto-ping и журнал действий (apply, restart, vless, users).</p>\n      <div class="metric ops-metric" style="margin-bottom:14px">\n        <div class="box ops-stat">\n          <div class="ops-stat-main">\n            <span class="ops-stat-dot stale" id="svcXrayDot" aria-hidden="true"></span>\n            <span class="ops-stat-val empty-metric" id="svcXray">…</span>\n          </div>\n          <div class="l">xray</div>\n        </div>\n        <div class="box ops-stat">\n          <div class="ops-stat-main">\n            <span class="ops-stat-dot stale" id="svcMihomoDot" aria-hidden="true"></span>\n            <span class="ops-stat-val empty-metric" id="svcMihomo">…</span>\n          </div>\n          <div class="l">mihomo</div>\n        </div>\n        <div class="box ops-stat">\n          <div class="ops-stat-main">\n            <span class="ops-stat-dot stale" id="svcPingDot" aria-hidden="true"></span>\n            <span class="ops-stat-val empty-metric" id="svcPingAge">нет данных</span>\n          </div>\n          <div class="l">auto-ping</div>\n        </div>\n      </div>\n      <div class="btn-row">\n        <button type="button" class="btn btn-ghost" id="btnRestartXray" style="padding:8px 14px;font-size:13px" title="systemctl restart xray">Restart xray</button>\n        <button type="button" class="btn btn-ghost" id="btnRestartMihomo" style="padding:8px 14px;font-size:13px" title="systemctl restart mihomo">Restart mihomo</button>\n        <button type="button" class="btn btn-ghost" id="btnRefreshOps" style="padding:8px 14px;font-size:13px">Обновить</button>\n      </div>\n      <div class="ops-prefs-card">\n        <label class="ops-prefs-label" for="autoPingToggle">\n          <input type="checkbox" id="autoPingToggle"/>\n          Auto-ping ноды (шапка UP/DOWN)\n        </label>\n        <div class="ops-prefs-row">\n          <span class="meta">интервал, сек</span>\n          <input type="number" id="autoPingSec" min="15" max="300" value="45"/>\n          <button type="button" class="btn btn-primary" id="btnSavePrefs" style="padding:8px 14px;font-size:13px">Сохранить</button>\n        </div>\n      </div>\n      <hr style="border:0;border-top:1px solid var(--line);margin:16px 0"/>\n      <div class="journal-head">\n        <div class="journal-head-left">\n          <h3>Журнал</h3>\n          <span class="journal-age" id="journalAge"></span>\n        </div>\n        <button type="button" class="btn-icon-only" id="btnRefreshLog" title="Обновить журнал" aria-label="Обновить журнал">\n          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">\n            <path d="M21 12a9 9 0 1 1-2.64-6.36"/><polyline points="21 3 21 9 15 9"/>\n          </svg>\n        </button>\n      </div>\n      <div id="activityList" class="activity-list meta">Загрузка…</div>\n    </div>\n    </div><!-- /.spanels -->\n    </div><!-- /.settings-body -->\n  </div>\n</div>\n\n\n<div class="modal-backdrop" id="modalRulesOverview" aria-hidden="true">\n  <div class="modal" role="dialog" aria-labelledby="rov-title" style="max-width:820px;width:100%">\n    <button type="button" class="modal-x" data-close aria-label="Закрыть">×</button>\n    <h2 id="rov-title">Обзор правил</h2>\n    <p class="sub">Все записи из групп. Клик по строке — открыть группу.</p>\n    <div class="rov-toolbar">\n      <input type="search" id="rovSearch" placeholder="Фильтр: домен, IP, группа…" autocomplete="off"/>\n      <button type="button" class="btn btn-ghost" id="btnRovRefresh" style="padding:8px 12px;font-size:13px">Обновить</button>\n    </div>\n    <p class="rov-meta" id="rovMeta">—</p>\n    <div class="rov-table-wrap">\n      <table class="rov-table" id="rovTable">\n        <thead>\n          <tr>\n            <th>Группа</th>\n            <th>Тип</th>\n            <th>Значение</th>\n            <th>Вкл</th>\n          </tr>\n        </thead>\n        <tbody id="rovBody">\n          <tr><td colspan="4" class="rov-empty">Загрузка…</td></tr>\n        </tbody>\n      </table>\n    </div>\n  </div>\n</div>\n\n\n<div class="modal-backdrop" id="modalCoreUpdate" aria-hidden="true">\n  <div class="modal core-upd-modal" role="dialog" aria-labelledby="coreUpdTitle" style="max-width:440px">\n    <button type="button" class="modal-x" data-close aria-label="Закрыть">×</button>\n    <h2 id="coreUpdTitle">Обновление</h2>\n    <div class="core-upd-row"><span class="meta">Версии:</span> <strong id="coreUpdVersions">—</strong></div>\n    <p class="core-upd-notes" id="coreUpdNotes"></p>\n    <p class="core-upd-manual" id="coreUpdManual" hidden></p>\n    <p class="sub" id="coreUpdLinkWrap" style="margin:0 0 14px"><a id="coreUpdLink" href="#" target="_blank" rel="noopener">Посмотреть полный changelog</a></p>\n    <div class="btn-row" id="coreUpdActions">\n      <button type="button" class="btn btn-ghost" data-close style="padding:8px 14px;font-size:13px">Закрыть</button>\n      <button type="button" class="btn btn-primary" id="btnCoreUpdDo" style="padding:8px 14px;font-size:13px">Обновить</button>\n    </div>\n    <div class="btn-row" id="coreUpdConfirm" hidden>\n      <p class="sub" style="margin:0 0 10px;width:100%" id="coreUpdConfirmText">Сервис будет остановлен и перезапущен. Продолжить?</p>\n      <button type="button" class="btn btn-ghost" id="btnCoreUpdCancel" style="padding:8px 14px;font-size:13px">Отмена</button>\n      <button type="button" class="btn btn-primary" id="btnCoreUpdConfirm" style="padding:8px 14px;font-size:13px">Да, обновить</button>\n    </div>\n  </div>\n</div>\n\n<div class="modal-backdrop" id="modalImport" aria-hidden="true">\n  <div class="modal" role="dialog">\n    <button type="button" class="modal-x" data-close aria-label="Закрыть">×</button>\n    <h2>Импорт правил</h2>\n    <p class="sub">По одной строке: CIDR → IPv4, домен → Namespace. Превью ниже.</p>\n    <p class="import-group-line">Группа: <strong id="importGroupName">—</strong></p>\n    <form method="post" action="/entry-import" id="importForm">\n      <input type="hidden" name="gid" id="importGid" value=""/>\n      <div class="field"><label for="importText">Список</label>\n        <textarea id="importText" name="text" placeholder="91.108.4.0/22&#10;telegram.com" required></textarea></div>\n      <div class="preview-stats" id="previewStats"></div>\n      <div class="import-opt-wrap" id="importOptWrap" hidden>\n        <label class="import-opt" for="importSkipDupConflict">\n          <input type="checkbox" id="importSkipDupConflict" checked/>\n          <span>Не импортировать дубли и конфликты</span>\n        </label>\n        <p class="import-opt-hint">Строки, помеченные как дубль или конфликт, будут пропущены при импорте</p>\n      </div>\n      <div class="preview" id="previewBox" hidden></div>\n      <div class="foot-row">\n        <div class="detect-field">\n          <label for="detectMode">Режим типа</label>\n          <select name="detect" id="detectMode" class="detect-select">\n            <option value="auto" selected>Auto — по строке</option>\n            <option value="ipv4">Только IP/CIDR</option>\n            <option value="namespace">Только domain</option>\n          </select>\n          <p class="detect-hint">Auto — автоопределение типа каждой строки (IP/CIDR vs domain). Фильтр ниже сужает превью и импорт, не выбирает группу.</p>\n        </div>\n        <div class="modal-actions" style="margin:0">\n          <button type="button" class="btn btn-ghost" data-close>Отмена</button>\n          <button type="submit" class="btn btn-primary" id="importSubmitBtn">Импортировать</button>\n        </div>\n      </div>\n    </form>\n  </div>\n</div>\n\n<script>\n(function(){\n  // ensure no stuck full-screen overlay from prior session/DOM\n  try {\n    document.getElementById("verBackdrop")?.classList.remove("is-open");\n    document.body.classList.remove("ver-changelog-open");\n  } catch (e) {}\n  // clean ?msg= / ?err= from address bar after toast is shown\n  try {\n    if (location.search && (location.search.includes("msg=") || location.search.includes("err="))) {\n      history.replaceState(null, "", location.pathname || "/");\n    }\n  } catch (e) {}\n\n  // auto-hide server-rendered toasts (e.g. «Добро пожаловать»)\n  document.querySelectorAll(".toast").forEach(el => {\n    const ms = el.classList.contains("toast-err") ? 5000 : 2800;\n    setTimeout(() => {\n      el.classList.add("toast-out");\n      setTimeout(() => el.remove(), 400);\n    }, ms);\n  });\n\n  // version changelog — robust for iOS (portal + is-open + global hook)\n  \n  // ── Connections view ───────────────────────────────────────────\n  let connsTimer = null;\n  let connsCache = [];\n\n  function stopConnsPolling(){\n    if (connsTimer) { clearInterval(connsTimer); connsTimer = null; }\n  }\n  function startConnsPolling(){\n    stopConnsPolling();\n    refreshConns();\n    connsTimer = setInterval(refreshConns, 2000);\n  }\n  function fmtShortBytes(n){\n    n = Number(n) || 0;\n    if (n < 1024) return n + " B";\n    if (n < 1048576) return (n/1024).toFixed(1) + " K";\n    if (n < 1073741824) return (n/1048576).toFixed(1) + " M";\n    return (n/1073741824).toFixed(2) + " G";\n  }\n  async function refreshConns(){\n    if (currentView !== "conns") return;\n    try {\n      const res = await fetch("/api/connections", { credentials: "same-origin" });\n      const d = await res.json();\n      if (!d.ok) {\n        const body = document.getElementById("connsBody");\n        if (body) body.innerHTML = `<tr><td colspan="7" class="stats-empty">${escHtml(d.error || "ошибка")}</td></tr>`;\n        return;\n      }\n      connsCache = d.connections || [];\n      renderConnsTable();\n      const meta = document.getElementById("connMeta");\n      if (meta) {\n        meta.textContent = "активных: " + (d.count || connsCache.length)\n          + " · ↓ " + fmtShortBytes(d.downloadTotal || 0)\n          + " · ↑ " + fmtShortBytes(d.uploadTotal || 0)\n          + " · " + new Date().toLocaleTimeString();\n      }\n    } catch (e) {\n      /* ignore */\n    }\n  }\n  function renderConnsTable(){\n    const body = document.getElementById("connsBody");\n    if (!body) return;\n    const q = ((searchInput && searchInput.value) || "").trim().toLowerCase();\n    let rows = connsCache;\n    if (q) {\n      rows = rows.filter(c => {\n        const hay = [c.host, c.rule, c.chain, c.source, c.dest, c.network, c.type].join(" ").toLowerCase();\n        return hay.includes(q);\n      });\n    }\n    if (!rows.length) {\n      body.innerHTML = `<tr><td colspan="7" class="stats-empty">${q ? "Нет совпадений" : "Нет активных соединений"}</td></tr>`;\n      return;\n    }\n    body.innerHTML = rows.map(c => {\n      const proto = [c.network, c.type].filter(Boolean).join(" · ");\n      return `<tr data-conn-id="${escHtml(c.id)}">\n        <td><div class="conn-host">${escHtml(c.host || "—")}</div>\n          <div class="meta" style="font-size:11px">${escHtml(c.source || "")} → ${escHtml(c.dest || "")}</div></td>\n        <td>${escHtml(c.rule || "—")}</td>\n        <td class="conn-chain">${escHtml(c.chain || "—")}</td>\n        <td>${escHtml(proto || "—")}</td>\n        <td class="conn-bytes">↓ ${fmtShortBytes(c.download)} · ↑ ${fmtShortBytes(c.upload)}</td>\n        <td>${escHtml(c.duration || "—")}</td>\n        <td><button type="button" class="btn btn-ghost conn-kill" data-kill-conn="${escHtml(c.id)}" title="Разорвать">✕</button></td>\n      </tr>`;\n    }).join("");\n  }\n  document.getElementById("btnRefreshConns")?.addEventListener("click", () => refreshConns());\n  document.getElementById("btnKillAllConns")?.addEventListener("click", async () => {\n    if (!confirm("Разорвать ВСЕ активные соединения?")) return;\n    try {\n      const res = await fetch("/api/connections", { method: "DELETE", credentials: "same-origin" });\n      const d = await res.json();\n      toast(d.ok !== false ? "Все соединения разорваны" : (d.error || "fail"), d.ok !== false);\n      refreshConns();\n    } catch (e) { toast(String(e), false); }\n  });\n  document.getElementById("panel-conns")?.addEventListener("click", async (e) => {\n    const b = e.target.closest("[data-kill-conn]");\n    if (!b) return;\n    const id = b.getAttribute("data-kill-conn");\n    if (!id || !confirm("Разорвать это соединение?")) return;\n    try {\n      const res = await fetch("/api/connections/" + encodeURIComponent(id), { method: "DELETE", credentials: "same-origin" });\n      const d = await res.json();\n      toast(d.ok !== false ? "Соединение разорвано" : (d.error || "fail"), d.ok !== false);\n      refreshConns();\n    } catch (err) { toast(String(err), false); }\n  });\n\n  // ── Logs view ──────────────────────────────────────────────────\n  let logsTimer = null;\n  let logsLevel = "info";\n  let logsMode = "tail"; // tail | live\n  let logsSinceSeq = 0;\n  let logsLines = []; // {seq,type,payload,ts}\n  let logsAutoScroll = true;\n\n  function stopLogsPolling(){\n    if (logsTimer) { clearInterval(logsTimer); logsTimer = null; }\n  }\n  function startLogsPolling(){\n    stopLogsPolling();\n    if (logsMode === "live") logsSinceSeq = 0; // will set after first empty\n    refreshLogs(true);\n    logsTimer = setInterval(() => refreshLogs(false), 1000);\n  }\n  function setLogsLevel(lvl){\n    logsLevel = lvl || "info";\n    document.querySelectorAll("#logsLevel .stab").forEach(b => {\n      b.classList.toggle("active", b.getAttribute("data-log-level") === logsLevel);\n    });\n    // tell server preferred stream level (use debug if filter is debug, else info for volume)\n    const streamLvl = logsLevel === "debug" ? "debug" : (logsLevel === "error" ? "warning" : "info");\n    api("/api/logs/level", { level: streamLvl }).catch(() => {});\n    if (logsMode === "tail") logsLines = [];\n    refreshLogs(true);\n  }\n  function setLogsMode(mode){\n    logsMode = mode === "live" ? "live" : "tail";\n    document.querySelectorAll("#logsMode .stab").forEach(b => {\n      b.classList.toggle("active", b.getAttribute("data-log-mode") === logsMode);\n    });\n    if (logsMode === "live") {\n      logsLines = [];\n      logsSinceSeq = 0;\n    }\n    refreshLogs(true);\n  }\n  async function refreshLogs(reset){\n    if (currentView !== "logs") return;\n    try {\n      const since = (logsMode === "live") ? logsSinceSeq : 0;\n      const qs = new URLSearchParams({\n        level: logsLevel === "all" ? "all" : logsLevel,\n        mode: logsMode,\n        since: String(since),\n        limit: "200",\n      });\n      // For tail, show all levels matching filter from buffer; stream is info/debug\n      if (logsMode === "tail") qs.set("level", "all");\n      const res = await fetch("/api/logs?" + qs.toString(), { credentials: "same-origin" });\n      const d = await res.json();\n      if (!d.ok) return;\n      const items = d.items || [];\n      if (logsMode === "live") {\n        if (reset && !logsSinceSeq) {\n          logsSinceSeq = d.seq || 0;\n          logsLines = [];\n        }\n        for (const it of items) {\n          if ((it.seq || 0) > logsSinceSeq) {\n            logsLines.push(it);\n            logsSinceSeq = it.seq;\n          }\n        }\n        if (logsLines.length > 500) logsLines = logsLines.slice(-500);\n      } else {\n        logsLines = items;\n        if (d.seq) logsSinceSeq = d.seq;\n      }\n      renderLogsView();\n      const meta = document.getElementById("logsMeta");\n      if (meta) {\n        meta.textContent = "буфер seq " + (d.seq || 0)\n          + " · показано " + document.querySelectorAll("#logsView .log-line").length\n          + " · уровень " + logsLevel\n          + " · " + (logsMode === "live" ? "live" : "последние 200");\n      }\n    } catch (e) {}\n  }\n  function renderLogsView(){\n    const el = document.getElementById("logsView");\n    if (!el) return;\n    const q = ((searchInput && searchInput.value) || "").trim().toLowerCase();\n    let rows = logsLines;\n    if (logsLevel && logsLevel !== "all") {\n      if (logsLevel === "warning") {\n        rows = rows.filter(r => (r.type || "") === "warning" || (r.type || "") === "error");\n      } else {\n        rows = rows.filter(r => (r.type || "") === logsLevel);\n      }\n    }\n    if (q) rows = rows.filter(r => String(r.payload || "").toLowerCase().includes(q));\n    // newest first (time top→bottom was wrong; Meta-like reverse chrono)\n    rows = rows.slice().reverse();\n    const nearTop = el.scrollTop < 48;\n    el.innerHTML = rows.map(r => {\n      const lvl = (r.type || "info").toLowerCase();\n      let ts = "";\n      try {\n        if (r.ts) ts = new Date(r.ts * 1000).toLocaleTimeString();\n      } catch (_) {}\n      return `<span class="log-line lvl-${escHtml(lvl)}"><span class="log-ts">${escHtml(ts)}</span>[${escHtml(lvl)}] ${escHtml(r.payload || "")}</span>`;\n    }).join("") || \'<span class="log-line lvl-info">Нет записей…</span>\';\n    if (logsAutoScroll && nearTop) el.scrollTop = 0;\n  }\n  document.getElementById("logsLevel")?.addEventListener("click", (e) => {\n    const b = e.target.closest("[data-log-level]");\n    if (b) setLogsLevel(b.getAttribute("data-log-level"));\n  });\n  document.getElementById("logsMode")?.addEventListener("click", (e) => {\n    const b = e.target.closest("[data-log-mode]");\n    if (b) setLogsMode(b.getAttribute("data-log-mode"));\n  });\n  document.getElementById("btnClearLogs")?.addEventListener("click", () => {\n    logsLines = [];\n    if (logsMode === "live") logsSinceSeq = logsSinceSeq; // keep\n    renderLogsView();\n  });\n  document.getElementById("logsView")?.addEventListener("scroll", () => {\n    const el = document.getElementById("logsView");\n    if (!el) return;\n    logsAutoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 48;\n  });\n\n  // search input also filters conns/logs (use getElementById — avoid TDZ before const searchInput)\n  document.getElementById("searchInput")?.addEventListener("input", () => {\n    if (currentView === "conns") renderConnsTable();\n    if (currentView === "logs") renderLogsView();\n  });\n\n\n  (function setupVerChangelog(){\n    const verWrap = document.querySelector(".ver-wrap");\n    const verChip = document.getElementById("verChip");\n    let verDd = document.getElementById("verDd");\n    if (!verChip || !verDd || !verWrap) return;\n\n    let open = false;\n    let ignoreOutsideUntil = 0;\n    let backdrop = document.getElementById("verBackdrop");\n    if (!backdrop) {\n      backdrop = document.createElement("div");\n      backdrop.id = "verBackdrop";\n      backdrop.className = "ver-backdrop";\n      backdrop.setAttribute("aria-hidden", "true");\n      document.body.appendChild(backdrop);\n    }\n    const homeParent = verWrap;\n    const homeNext = verDd.nextSibling;\n\n    function isMobile(){\n      return !!(window.matchMedia && window.matchMedia("(max-width:720px)").matches);\n    }\n\n    /** Position as fixed portal + real scroll metrics (desktop & mobile). */\n    function placePanel(){\n      const r = verChip.getBoundingClientRect();\n      const mobile = isMobile();\n      const gap = 8;\n      const vw = window.innerWidth;\n      const vh = window.innerHeight;\n      let top = Math.round(r.bottom + gap);\n      let width = mobile ? Math.max(200, vw - 24) : Math.min(360, vw - 32);\n      let left;\n      if (mobile) {\n        left = 12;\n        width = vw - 24;\n      } else {\n        left = Math.round(r.left);\n        if (left + width > vw - 12) left = Math.max(12, vw - 12 - width);\n        if (left < 12) left = 12;\n      }\n      let maxH = Math.max(160, Math.min(420, vh - top - 16));\n      // flip above chip if little space below\n      let useBottom = false;\n      let bottomPx = 0;\n      if (maxH < 200 && r.top > vh - r.bottom) {\n        useBottom = true;\n        bottomPx = Math.round(vh - r.top + gap);\n        maxH = Math.max(160, Math.min(420, r.top - gap - 16));\n      }\n      const parts = [\n        "position:fixed",\n        mobile ? "left:12px" : ("left:" + left + "px"),\n        mobile ? "right:12px" : ("width:" + width + "px"),\n        mobile ? "width:auto" : "",\n        useBottom ? ("bottom:" + bottomPx + "px") : ("top:" + top + "px"),\n        useBottom ? "top:auto" : "bottom:auto",\n        "max-height:" + maxH + "px",\n        "height:auto",\n        "overflow-x:hidden",\n        "overflow-y:auto",\n        "overscroll-behavior:contain",\n        "touch-action:pan-y",\n        "-webkit-overflow-scrolling:touch",\n        "z-index:10050",\n        "opacity:1",\n        "visibility:visible",\n        "pointer-events:auto",\n        "transform:none",\n        "display:block",\n        "box-sizing:border-box"\n      ].filter(Boolean);\n      verDd.style.cssText = parts.join(";");\n    }\n\n    function clearInline(){\n      verDd.style.cssText = "";\n    }\n\n    function setOpen(next){\n      open = !!next;\n      verWrap.classList.toggle("open", open);\n      verDd.classList.toggle("is-open", open);\n      verChip.setAttribute("aria-expanded", open ? "true" : "false");\n      backdrop.classList.toggle("is-open", open);\n      backdrop.setAttribute("aria-hidden", open ? "false" : "true");\n      document.body.classList.toggle("ver-changelog-open", open);\n\n      if (open) {\n        ignoreOutsideUntil = Date.now() + 400;\n        // always portal to body so sticky topbar / overflow ancestors cannot steal wheel/touch\n        if (verDd.parentElement !== document.body) {\n          document.body.appendChild(verDd);\n        }\n        placePanel();\n        // focus panel for a11y; don\'t steal if user is mid-gesture\n        try { verDd.setAttribute("tabindex", "-1"); } catch (_) {}\n      } else {\n        clearInline();\n        if (verDd.parentElement !== homeParent) {\n          if (homeNext && homeNext.parentElement === homeParent) {\n            homeParent.insertBefore(verDd, homeNext);\n          } else {\n            homeParent.appendChild(verDd);\n          }\n        }\n      }\n    }\n\n    function toggle(e){\n      if (e) {\n        try { e.preventDefault(); } catch (_) {}\n        try { e.stopPropagation(); } catch (_) {}\n      }\n      setOpen(!open);\n      return false;\n    }\n\n    window.__toggleVerChangelog = toggle;\n    window.__setVerChangelog = setOpen;\n\n    verChip.addEventListener("click", toggle, true);\n    backdrop.addEventListener("click", function(){ setOpen(false); });\n    // backdrop must not eat wheel over the panel (panel is higher z-index);\n    // block wheel on backdrop so page doesn\'t scroll under\n    backdrop.addEventListener("wheel", function(e){\n      e.preventDefault();\n    }, { passive: false });\n    backdrop.addEventListener("touchmove", function(e){\n      e.preventDefault();\n    }, { passive: false });\n\n    // Guarantee wheel scrolls the list (not the page). Manual delta if needed.\n    verDd.addEventListener("wheel", function(e){\n      if (!open) return;\n      e.stopPropagation();\n      const el = verDd;\n      const canScroll = el.scrollHeight > el.clientHeight + 1;\n      if (!canScroll) {\n        e.preventDefault();\n        return;\n      }\n      const dy = e.deltaY;\n      const top = el.scrollTop;\n      const max = el.scrollHeight - el.clientHeight;\n      const atTop = top <= 0;\n      const atBottom = top >= max - 1;\n      // always prevent body scroll chaining\n      if ((dy < 0 && atTop) || (dy > 0 && atBottom)) {\n        e.preventDefault();\n        return;\n      }\n      // force scroll on the panel (fixes cases where browser ignores overflow:auto under sticky/fixed)\n      e.preventDefault();\n      el.scrollTop = Math.max(0, Math.min(max, top + dy));\n    }, { passive: false });\n\n    // touch: allow pan-y on panel; prevent body move when gesture is on panel\n    verDd.addEventListener("touchmove", function(e){\n      if (!open) return;\n      e.stopPropagation();\n    }, { passive: true });\n\n    document.addEventListener("click", function(e){\n      if (!open) return;\n      if (Date.now() < ignoreOutsideUntil) return;\n      const t = e.target;\n      if (verChip.contains(t) || verDd.contains(t) || (backdrop && backdrop.contains(t))) return;\n      setOpen(false);\n    }, true);\n\n    window.addEventListener("resize", function(){\n      if (open) placePanel();\n    }, { passive: true });\n    window.addEventListener("orientationchange", function(){\n      if (open) setTimeout(placePanel, 100);\n    });\n    window.addEventListener("scroll", function(){\n      if (open) placePanel();\n    }, { passive: true, capture: true });\n  })();\n\n  // logout — force navigation (don\'t let overlays / Basic cache keep you in)\n  document.getElementById("btnLogout")?.addEventListener("click", (e) => {\n    e.preventDefault();\n    e.stopPropagation();\n    window.location.replace("/logout");\n  });\n\n  const modalGroup = document.getElementById("modalGroup");\n  const modalImport = document.getElementById("modalImport");\n  const modalExplain = document.getElementById("modalExplain");\n  const btnAdd = document.getElementById("btnAdd");\n  const btnExplain = document.getElementById("btnExplain");\n  const searchWrap = document.getElementById("searchWrap");\n  const searchInput = document.getElementById("searchInput");\n  const importText = document.getElementById("importText");\n  const previewBox = document.getElementById("previewBox");\n  const previewStats = document.getElementById("previewStats");\n  const importGid = document.getElementById("importGid");\n\n  function openModal(el){ el.classList.add("open"); el.setAttribute("aria-hidden","false"); }\n  function closeAll(){\n    [modalGroup, modalImport, modalExplain].forEach(m => {\n      if (!m) return;\n      m.classList.remove("open");\n      m.setAttribute("aria-hidden","true");\n    });\n  }\n  function toast(msg, ok){\n    document.querySelectorAll(".toast").forEach(t => t.remove());\n    const el = document.createElement("div");\n    el.className = "toast " + (ok ? "toast-ok" : "toast-err");\n    el.setAttribute("role", ok ? "status" : "alert");\n    el.textContent = msg;\n    document.body.appendChild(el);\n    const ms = ok ? 2800 : 5000;\n    setTimeout(() => {\n      el.classList.add("toast-out");\n      setTimeout(() => el.remove(), 400);\n    }, ms);\n  }\n  \n  \n  /** Core "?" tooltips: portal to body + viewport clamp/flip (escape modal containing-block). */\n  function placeCoreTip(tip){\n    if (!tip) return;\n    let pop = tip.querySelector(".core-tip-pop");\n    if (!pop && tip.dataset.tipId) {\n      pop = document.querySelector(\'.core-tip-pop[data-for="\' + tip.dataset.tipId + \'"]\');\n    }\n    if (!pop) return;\n\n    // portal to body so position:fixed is relative to viewport (not settings-modal)\n    if (!tip.dataset.tipId) {\n      tip.dataset.tipId = "ct" + Math.random().toString(36).slice(2, 9);\n    }\n    pop.dataset.for = tip.dataset.tipId;\n    if (pop.parentElement !== document.body) {\n      document.body.appendChild(pop);\n    }\n\n    const margin = 10;\n    const gap = 8;\n    const r = tip.getBoundingClientRect();\n    const vw = window.innerWidth || document.documentElement.clientWidth;\n    const vh = window.innerHeight || document.documentElement.clientHeight;\n    const width = Math.min(260, Math.max(180, vw - margin * 2));\n\n    tip.classList.add("is-tip-open");\n    pop.style.display = "block";\n    pop.style.position = "fixed";\n    pop.style.width = width + "px";\n    pop.style.maxWidth = (vw - margin * 2) + "px";\n    pop.style.zIndex = "10060";\n    pop.style.transform = "none";\n    pop.style.margin = "0";\n    pop.style.visibility = "hidden";\n    pop.style.left = "0px";\n    pop.style.top = "0px";\n    pop.style.right = "auto";\n    pop.style.bottom = "auto";\n\n    const ph = Math.max(pop.offsetHeight || 0, 48);\n    const pw = width;\n\n    let left = Math.round(r.left);\n    let top = Math.round(r.bottom + gap);\n\n    // flip left if overflows right\n    if (left + pw > vw - margin) {\n      left = Math.round(r.right - pw);\n    }\n    if (left < margin) left = margin;\n    if (left + pw > vw - margin) left = Math.max(margin, vw - margin - pw);\n\n    // flip above if overflows bottom\n    if (top + ph > vh - margin) {\n      const above = Math.round(r.top - gap - ph);\n      if (above >= margin) top = above;\n      else top = Math.max(margin, Math.min(top, vh - margin - ph));\n    }\n    if (top < margin) top = margin;\n\n    pop.style.left = left + "px";\n    pop.style.top = top + "px";\n    pop.style.visibility = "visible";\n  }\n  function hideCoreTip(tip){\n    if (!tip) return;\n    tip.classList.remove("is-tip-open");\n    let pop = tip.querySelector(".core-tip-pop");\n    if (!pop && tip.dataset.tipId) {\n      pop = document.querySelector(\'.core-tip-pop[data-for="\' + tip.dataset.tipId + \'"]\');\n    }\n    if (!pop) return;\n    pop.style.display = "none";\n    pop.style.visibility = "";\n    // return home under the "?" for next open\n    if (pop.parentElement === document.body) {\n      tip.appendChild(pop);\n    }\n  }\n  function setupCoreTips(){\n    document.querySelectorAll(".core-tip").forEach((tip) => {\n      if (tip.dataset.tipBound) return;\n      tip.dataset.tipBound = "1";\n      tip.addEventListener("mouseenter", () => placeCoreTip(tip));\n      tip.addEventListener("focus", () => placeCoreTip(tip));\n      tip.addEventListener("mouseleave", () => hideCoreTip(tip));\n      tip.addEventListener("blur", () => hideCoreTip(tip));\n    });\n    if (!window.__coreTipRepoBound) {\n      window.__coreTipRepoBound = true;\n      const repo = () => {\n        const open = document.querySelector(".core-tip.is-tip-open");\n        if (open) placeCoreTip(open);\n      };\n      window.addEventListener("resize", repo);\n      document.getElementById("settingsBd")?.addEventListener("scroll", repo, true);\n      document.querySelector(".settings-body")?.addEventListener("scroll", repo, true);\n    }\n  }\n  try { setupCoreTips(); } catch (_) {}\n  try { window.placeCoreTip = placeCoreTip; window.hideCoreTip = hideCoreTip; } catch (_) {}\n\n  async function loadCoreVersions(force){\n    const el = document.getElementById("coreVersions");\n    if (!el) return;\n    try {\n      const qs = force ? "?force=1" : "";\n      const res = await fetch("/api/core/versions" + qs, { credentials: "same-origin", cache: "no-store" });\n      const d = await res.json();\n      if (!d.ok) {\n        el.innerHTML = `<span class="core-ver-chip bad"><span class="cv-dot"></span>версии н/д</span>`;\n        return;\n      }\n      const esc = (typeof escHtml === "function") ? escHtml : (s) => String(s ?? "");\n      // only mihomo / xray / panel\n      const items = (d.items || [d.mihomo, d.xray, d.panel].filter(Boolean))\n        .filter(it => it && ["mihomo","xray","panel"].includes(it.label));\n      window.__coreVerState = window.__coreVerState || {};\n      el.innerHTML = items.map((it) => {\n        const lab = it.label || "";\n        const st = (window.__coreVerState[lab] || {});\n        const ok = !!(it && it.ok);\n        let cls = ok ? "ok" : "bad";\n        let title = esc(lab);\n        let badge = "";\n        let dot = `<span class="cv-dot" aria-hidden="true"></span>`;\n        if (st.checking) {\n          cls = "checking";\n          title = "Проверка…";\n          dot = `<span class="cv-spin" aria-hidden="true"></span>`;\n        } else if (st.error) {\n          cls = "bad";\n          title = "Не удалось проверить — повторить?";\n        } else if (st.update_available) {\n          cls = "warn";\n          title = "Доступно обновление";\n          badge = `<span class="cv-badge">↑ ${esc(st.latest || "")}</span>`;\n        } else if (st.up_to_date) {\n          cls = "ok";\n          title = "Актуальная версия";\n        }\n        const text = st.checking ? "Проверка…" : esc((it && it.display) || "н/д");\n        return `<button type="button" class="core-ver-chip ${cls}" data-comp="${esc(lab)}" title="${title}">${dot}<span class="cv-text">${text}</span>${badge}</button>`;\n      }).join("");\n    } catch (e) {\n      el.innerHTML = `<span class="core-ver-chip bad"><span class="cv-dot"></span>версии н/д</span>`;\n    }\n  }\n\n  window.__coreVerState = window.__coreVerState || {};\n  let __coreUpdPayload = null;\n\n  function openCoreUpdModal(payload){\n    __coreUpdPayload = payload;\n    const bd = document.getElementById("modalCoreUpdate");\n    if (!bd) return;\n    // above settings scrim (z-index) + ensure visible in stacking context\n    if (bd.parentElement !== document.body) document.body.appendChild(bd);\n    bd.style.zIndex = "400";\n    const title = document.getElementById("coreUpdTitle");\n    const vers = document.getElementById("coreUpdVersions");\n    const notes = document.getElementById("coreUpdNotes");\n    const man = document.getElementById("coreUpdManual");\n    const link = document.getElementById("coreUpdLink");\n    const linkWrap = document.getElementById("coreUpdLinkWrap");\n    const actions = document.getElementById("coreUpdActions");\n    const conf = document.getElementById("coreUpdConfirm");\n    const btnDo = document.getElementById("btnCoreUpdDo");\n    const _compTitle = (payload.component === "panel") ? "панели (GitHub)" : (payload.component || "");\n    if (title) title.textContent = "Доступно обновление " + _compTitle;\n    if (vers) vers.textContent = "v" + (payload.current || "?") + " → v" + (payload.latest || "?");\n    if (notes) notes.textContent = payload.notes || "Нет краткого описания релиза.";\n    if (payload.manual_only) {\n      if (man) { man.hidden = false; man.textContent = payload.manual_hint || "Обновите панель вручную на сервере."; }\n      if (btnDo) btnDo.hidden = true;\n    } else {\n      if (man) man.hidden = true;\n      if (btnDo) btnDo.hidden = false;\n    }\n    if (link && payload.html_url) {\n      link.href = payload.html_url;\n      if (linkWrap) linkWrap.hidden = false;\n    } else if (linkWrap) linkWrap.hidden = true;\n    const confText = document.getElementById("coreUpdConfirmText");\n    if (confText) {\n      confText.textContent = (payload.component === "panel")\n        ? "Панель скачает код с GitHub (git pull) и перезапустится. Продолжить?"\n        : "Сервис будет остановлен и перезапущен. Продолжить?";\n    }\n    if (actions) actions.hidden = false;\n    if (conf) conf.hidden = true;\n    bd.classList.add("open");\n    bd.setAttribute("aria-hidden", "false");\n  }\n  function closeCoreUpdModal(){\n    const bd = document.getElementById("modalCoreUpdate");\n    if (!bd) return;\n    bd.classList.remove("open");\n    bd.setAttribute("aria-hidden", "true");\n    __coreUpdPayload = null;\n  }\n  async function checkCoreComponent(comp){\n    if (!comp) return;\n    const dbg = (msg) => {\n      const d = document.getElementById("coreVerDebug");\n      if (!d) return;\n      const t = new Date().toLocaleTimeString();\n      d.textContent = "[" + t + "] " + msg;\n      d.classList.add("has-msg");\n      try { console.log("[core-ver]", t, msg); } catch (_) {}\n    };\n    window.__coreVerState = window.__coreVerState || {};\n    window.__coreVerState[comp] = Object.assign({}, window.__coreVerState[comp] || {}, { checking: true, error: false });\n    dbg(comp + ": запрос /api/core/versions/check …");\n    if (typeof loadCoreVersions === "function") await loadCoreVersions(false);\n    const t0 = Date.now();\n    try {\n      const url = "/api/core/versions/check?component=" + encodeURIComponent(comp);\n      dbg(comp + ": GET " + url);\n      const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });\n      const d = await res.json();\n      // min spinner visibility ~450ms so user sees "Проверка..."\n      const wait = Math.max(0, 450 - (Date.now() - t0));\n      if (wait) await new Promise((r) => setTimeout(r, wait));\n      if (!d.ok) {\n        window.__coreVerState[comp] = { error: true, checking: false };\n        dbg(comp + ": ошибка — " + (d.error || res.status));\n        if (typeof toast === "function") toast((comp) + ": не удалось проверить", true);\n        if (typeof loadCoreVersions === "function") await loadCoreVersions(false);\n        return;\n      }\n      window.__coreVerState[comp] = {\n        checking: false,\n        error: false,\n        update_available: !!d.update_available,\n        up_to_date: !!d.up_to_date,\n        latest: d.latest,\n        current: d.current,\n        notes: d.notes,\n        html_url: d.html_url,\n        manual_only: !!d.manual_only,\n        manual_hint: d.manual_hint,\n        component: d.component || comp,\n      };\n      if (typeof loadCoreVersions === "function") await loadCoreVersions(false);\n      if (d.update_available) {\n        dbg(comp + ": есть обновление v" + (d.current || "?") + " → v" + (d.latest || "?"));\n        if (typeof openCoreUpdModal === "function") openCoreUpdModal(window.__coreVerState[comp]);\n        if (typeof toast === "function") toast(comp + ": доступна v" + (d.latest || ""));\n      } else if (d.up_to_date) {\n        dbg(comp + ": актуальная v" + (d.current || d.latest || ""));\n        if (typeof toast === "function") toast(comp + ": актуальная версия");\n      } else {\n        dbg(comp + ": ответ без флага update/up_to_date");\n      }\n    } catch (e) {\n      const wait = Math.max(0, 450 - (Date.now() - t0));\n      if (wait) await new Promise((r) => setTimeout(r, wait));\n      window.__coreVerState[comp] = { error: true, checking: false };\n      dbg(comp + ": сеть/исключение — " + (e && e.message ? e.message : e));\n      if (typeof toast === "function") toast(String(e), true);\n      if (typeof loadCoreVersions === "function") await loadCoreVersions(false);\n    }\n  }\n  function setupCoreVersionChips(){\n    const el = document.getElementById("coreVersions");\n    if (!el || el.dataset.bound) return;\n    el.dataset.bound = "1";\n    el.addEventListener("click", (e) => {\n      const chip = e.target.closest("[data-comp]");\n      if (!chip) return;\n      const comp = chip.getAttribute("data-comp");\n      const st = (window.__coreVerState || {})[comp] || {};\n      if (st.checking) return;\n      // if already know update — open modal; else re-check (also for error retry)\n      if (st.update_available && !st.error) {\n        openCoreUpdModal(Object.assign({ component: comp }, st));\n        return;\n      }\n      checkCoreComponent(comp);\n    });\n  }\n  document.getElementById("modalCoreUpdate")?.addEventListener("click", (e) => {\n    if (e.target.id === "modalCoreUpdate" || e.target.closest("[data-close]")) closeCoreUpdModal();\n  });\n  document.getElementById("btnCoreUpdDo")?.addEventListener("click", () => {\n    const actions = document.getElementById("coreUpdActions");\n    const conf = document.getElementById("coreUpdConfirm");\n    if (actions) actions.hidden = true;\n    if (conf) conf.hidden = false;\n  });\n  document.getElementById("btnCoreUpdCancel")?.addEventListener("click", () => {\n    const actions = document.getElementById("coreUpdActions");\n    const conf = document.getElementById("coreUpdConfirm");\n    if (actions) actions.hidden = false;\n    if (conf) conf.hidden = true;\n  });\n  document.getElementById("btnCoreUpdConfirm")?.addEventListener("click", async () => {\n    const p = __coreUpdPayload;\n    if (!p || !p.component) return;\n    const btn = document.getElementById("btnCoreUpdConfirm");\n    if (btn) { btn.disabled = true; btn.textContent = "Обновление…"; }\n    try {\n      const d = await api("/api/core/versions/update", {\n        component: p.component,\n        confirm: "1",\n      });\n      if (!d.ok) {\n        if (typeof toast === "function") toast(d.error || "ошибка обновления", true);\n        else alert(d.error || "ошибка обновления");\n      } else {\n        const _nm = (p.component === "panel") ? "панель" : p.component;\n        if (typeof toast === "function") toast(_nm + ": " + (d.old || "") + " → " + (d.new || ""));\n        window.__coreVerState[p.component] = { checking: false, up_to_date: true, update_available: false };\n        closeCoreUpdModal();\n        if (typeof loadCoreVersions === "function") await loadCoreVersions(true);\n      }\n    } catch (e) {\n      if (typeof toast === "function") toast(String(e), true);\n    } finally {\n      if (btn) { btn.disabled = false; btn.textContent = "Да, обновить"; }\n      const actions = document.getElementById("coreUpdActions");\n      const conf = document.getElementById("coreUpdConfirm");\n      if (actions) actions.hidden = false;\n      if (conf) conf.hidden = true;\n    }\n  });\n  \n  document.getElementById("btnFleetUpdateNow")?.addEventListener("click", async () => {\n    const btn = document.getElementById("btnFleetUpdateNow");\n    const dbg = document.getElementById("coreVerDebug");\n    if (btn) { btn.disabled = true; btn.textContent = "Запуск…"; }\n    try {\n      const d = await api("/api/core/fleet-update", {});\n      if (!d.ok) {\n        if (typeof toast === "function") toast(d.error || "не удалось запустить", true);\n        if (dbg) dbg.textContent = "fleet: " + (d.error || "error");\n      } else {\n        if (typeof toast === "function") toast("Автообновление запущено (фон)");\n        if (dbg) dbg.textContent = "fleet: check-and-update.sh started";\n        try { console.log("[fleet]", d); } catch (_) {}\n      }\n    } catch (e) {\n      if (typeof toast === "function") toast(String(e), true);\n    } finally {\n      if (btn) { btn.disabled = false; btn.textContent = "Проверить обновления сейчас"; }\n    }\n  });\n\n  try {\n    window.checkCoreComponent = checkCoreComponent;\n    window.setupCoreVersionChips = setupCoreVersionChips;\n    window.openCoreUpdModal = openCoreUpdModal;\n    window.loadCoreVersions = loadCoreVersions;\n    setupCoreVersionChips();\n  } catch (_) {}\n  // document-level fallback — works even if #coreVersions was rebound/replaced\n  if (!window.__coreVerClickDelegated) {\n    window.__coreVerClickDelegated = true;\n    document.addEventListener("click", (e) => {\n      const chip = e.target && e.target.closest && e.target.closest("#coreVersions [data-comp]");\n      if (!chip) return;\n      e.preventDefault();\n      const comp = chip.getAttribute("data-comp");\n      if (!comp) return;\n      const st = (window.__coreVerState || {})[comp] || {};\n      if (st.checking) return;\n      if (st.update_available && !st.error && typeof openCoreUpdModal === "function") {\n        openCoreUpdModal(Object.assign({ component: comp }, st));\n        return;\n      }\n      if (typeof checkCoreComponent === "function") checkCoreComponent(comp);\n      else console.error("[core-ver] checkCoreComponent missing");\n    }, true);\n  }\n\n\n\n  async function loadCoreSettings(){\n    const meta = document.getElementById("coreMeta");\n    const portsEl = document.getElementById("corePorts");\n    const showPortsErr = (msg) => {\n      if (!portsEl) return;\n      const safe = (typeof escHtml === "function") ? escHtml(msg) : String(msg || "ошибка");\n      portsEl.innerHTML = `<div class="core-ports-err"><span>Не удалось загрузить порты: ${safe}</span>`\n        + `<button type="button" class="btn btn-ghost" id="btnCorePortsRetry" style="padding:6px 12px;font-size:12.5px">Повторить</button></div>`;\n      document.getElementById("btnCorePortsRetry")?.addEventListener("click", () => loadCoreSettings());\n    };\n    const fmtPort = (v) => {\n      if (v === 0 || v === "0") return { text: "выключен", off: true };\n      if (v == null || v === "") return { text: "—", off: true };\n      return { text: String(v), off: false };\n    };\n    const renderPorts = (ports, c) => {\n      if (!portsEl) return;\n      const src = ports || {};\n      const rows = [\n        ["Mixed", src["mixed-port"] ?? c["mixed-port"]],\n        ["HTTP", src.port ?? c.port],\n        ["SOCKS", src["socks-port"] ?? c["socks-port"]],\n        ["Redir", src["redir-port"] ?? c["redir-port"]],\n        ["TProxy", src["tproxy-port"] ?? c["tproxy-port"]],\n      ];\n      const esc = (typeof escHtml === "function") ? escHtml : (s) => String(s ?? "");\n      portsEl.innerHTML = `<div class="core-ports-list">` + rows.map(([lab, v]) => {\n        const p = fmtPort(v);\n        return `<div class="core-port-row"><div class="cp-l">${esc(lab)}</div>`\n          + `<div class="cp-n${p.off ? " is-off" : ""}">${esc(p.text)}</div></div>`;\n      }).join("") + `</div>`;\n    };\n    try {\n      if (portsEl && !portsEl.dataset.loading) {\n        /* keep previous ports if any; only show loading if empty */\n        if (!portsEl.querySelector(".core-ports-list") && !portsEl.querySelector(".core-ports-err")) {\n          portsEl.innerHTML = `<div class="core-ports-err" style="background:transparent;border-color:var(--line);color:var(--muted)">Загрузка портов…</div>`;\n        }\n      }\n      if (typeof loadCoreVersions === "function") loadCoreVersions(false);\n      const res = await fetch("/api/core", { credentials: "same-origin", cache: "no-store" });\n      let d = null;\n      const ct = (res.headers.get("content-type") || "");\n      if (ct.includes("application/json")) {\n        d = await res.json();\n      } else {\n        const t = await res.text();\n        throw new Error(res.status === 401 ? "нужен вход" : ("HTTP " + res.status + (t ? ": " + t.slice(0, 80) : "")));\n      }\n      if (!res.ok || !d || !d.ok) {\n        const err = (d && d.error) || ("HTTP " + res.status);\n        if (meta) meta.textContent = "ошибка: " + err;\n        showPortsErr(err);\n        return;\n      }\n      const c = d.configs || {};\n      const ports = d.ports || {};\n      const setSel = (id, v) => {\n        const el = document.getElementById(id);\n        if (el && v != null && v !== "") el.value = String(v);\n      };\n      const setChk = (id, v) => {\n        const el = document.getElementById(id);\n        if (el) el.checked = !!v;\n      };\n      setSel("coreMode", c.mode || "rule");\n      setSel("coreLogLevel", c["log-level"] || "info");\n      setSel("coreFindProcess", c["find-process-mode"] || "off");\n      setChk("coreAllowLan", c["allow-lan"]);\n      setChk("coreIpv6", c.ipv6);\n      setChk("coreUnifiedDelay", c["unified-delay"]);\n      setChk("coreTcpConcurrent", c["tcp-concurrent"]);\n      setChk("coreSniffing", c.sniffing != null ? c.sniffing : d.sniffing);\n      setChk("coreGeoAuto", c["geo-auto-update"] != null ? c["geo-auto-update"] : d.geo_auto_update);\n      const geoInt = c["geo-update-interval"] != null ? c["geo-update-interval"] : d.geo_update_interval;\n      const geoIntEl = document.getElementById("coreGeoInterval");\n      if (geoIntEl && geoInt != null && geoInt !== "") geoIntEl.value = String(geoInt);\n      const geoWrap = document.getElementById("coreGeoIntervalWrap");\n      if (geoWrap) geoWrap.hidden = !(document.getElementById("coreGeoAuto")?.checked);\n      // TUN read-only card\n      const tunEl = document.getElementById("coreTun");\n      if (tunEl) {\n        const tun = (d.tun && typeof d.tun === "object") ? d.tun : (c.tun || {});\n        const esc = (typeof escHtml === "function") ? escHtml : (s) => String(s ?? "");\n        const yn = (v) => (v ? "Да" : "Нет");\n        const on = !!tun.enable;\n        let addr = tun["inet4-address"];\n        if (Array.isArray(addr)) addr = addr.filter(Boolean).join(", ") || "—";\n        else if (addr == null || addr === "") addr = "—";\n        const cells = [\n          ["Статус", `<span class="core-tun-status"><span class="core-tun-dot${on ? " on" : ""}"></span>${on ? "Включён" : "Выключен"}</span>`],\n          ["Устройство", esc(tun.device || "—")],\n          ["Стек", esc(tun.stack || "—")],\n          ["Авто-маршрут", yn(!!tun["auto-route"])],\n          ["Авто-редирект", yn(!!tun["auto-redirect"])],\n          ["Адрес", esc(String(addr))],\n        ];\n        tunEl.innerHTML = cells.map(([lab, val]) =>\n          `<div class="core-tun-row"><div class="cp-l">${esc(lab)}</div><div class="cp-n">${val}</div></div>`\n        ).join("");\n      }\n      // geo sources short line\n      const geoSrc = document.getElementById("coreGeoSources");\n      if (geoSrc) {\n        const gu = (d.geox_url && typeof d.geox_url === "object") ? d.geox_url : (c["geox-url"] || {});\n        const hasMeta = JSON.stringify(gu).includes("MetaCubeX") || JSON.stringify(gu).includes("meta-rules");\n        geoSrc.textContent = hasMeta\n          ? "geoip.dat, geosite.dat — MetaCubeX/meta-rules-dat"\n          : "geoip.dat, geosite.dat — источники из geox-url";\n      }\n      const btnGeo = document.getElementById("btnGeoUpdateNow");\n      if (btnGeo) btnGeo.hidden = (d.geo_update_api === false);\n      renderPorts(ports, c);\n      if (meta) {\n        meta.textContent = "mode " + (c.mode || "—")\n          + " · log " + (c["log-level"] || "—")\n          + " · tun " + ((c.tun && c.tun.enable) || (d.tun && d.tun.enable) ? "on" : "off")\n          + " · " + new Date().toLocaleTimeString();\n      }\n    } catch (e) {\n      const msg = (e && e.message) ? e.message : String(e);\n      if (meta) meta.textContent = "ошибка загрузки: " + msg;\n      showPortsErr(msg);\n    }\n  }\n  async function saveCoreSettings(){\n    const body = {\n      mode: document.getElementById("coreMode")?.value || "rule",\n      "log-level": document.getElementById("coreLogLevel")?.value || "info",\n      "find-process-mode": document.getElementById("coreFindProcess")?.value || "off",\n      "allow-lan": document.getElementById("coreAllowLan")?.checked ? "true" : "false",\n      ipv6: document.getElementById("coreIpv6")?.checked ? "true" : "false",\n      "unified-delay": document.getElementById("coreUnifiedDelay")?.checked ? "true" : "false",\n      "tcp-concurrent": document.getElementById("coreTcpConcurrent")?.checked ? "true" : "false",\n      sniffing: document.getElementById("coreSniffing")?.checked ? "true" : "false",\n      "geo-auto-update": document.getElementById("coreGeoAuto")?.checked ? "true" : "false",\n    };\n    const geoIv = document.getElementById("coreGeoInterval")?.value;\n    if (geoIv != null && String(geoIv).trim() !== "") body["geo-update-interval"] = String(geoIv).trim();\n    try {\n      const d = await api("/api/core", body);\n      if (!d.ok) {\n        if (typeof toast === "function") toast(d.error || "ошибка ядра", true);\n        else alert(d.error || "ошибка ядра");\n        return;\n      }\n      if (typeof toast === "function") toast("Ядро обновлено");\n      await loadCoreSettings();\n    } catch (e) {\n      if (typeof toast === "function") toast(String(e), true);\n    }\n  }\n  document.getElementById("btnCoreSave")?.addEventListener("click", () => saveCoreSettings());\n  document.getElementById("btnCoreRefresh")?.addEventListener("click", () => loadCoreSettings());\n  document.getElementById("coreGeoAuto")?.addEventListener("change", () => {\n    const wrap = document.getElementById("coreGeoIntervalWrap");\n    if (wrap) wrap.hidden = !document.getElementById("coreGeoAuto")?.checked;\n  });\n  document.getElementById("btnGeoIntervalSave")?.addEventListener("click", async () => {\n    const hours = document.getElementById("coreGeoInterval")?.value || "24";\n    try {\n      const d = await api("/api/core", {\n        "geo-auto-update": document.getElementById("coreGeoAuto")?.checked ? "true" : "false",\n        "geo-update-interval": String(hours),\n      });\n      if (!d.ok) {\n        if (typeof toast === "function") toast(d.error || "ошибка geo", true);\n        return;\n      }\n      if (typeof toast === "function") toast("Интервал geo сохранён");\n      await loadCoreSettings();\n    } catch (e) {\n      if (typeof toast === "function") toast(String(e), true);\n    }\n  });\n  document.getElementById("btnGeoUpdateNow")?.addEventListener("click", async () => {\n    const btn = document.getElementById("btnGeoUpdateNow");\n    if (btn) { btn.disabled = true; btn.textContent = "Обновление…"; }\n    try {\n      const d = await api("/api/core/geo-update", {});\n      if (!d.ok) {\n        if (typeof toast === "function") toast(d.error || "ошибка geo update", true);\n        else alert(d.error || "ошибка geo update");\n      } else {\n        if (typeof toast === "function") toast("Geo-базы: обновление запущено");\n      }\n    } catch (e) {\n      if (typeof toast === "function") toast(String(e), true);\n    } finally {\n      if (btn) { btn.disabled = false; btn.textContent = "Обновить сейчас"; }\n    }\n  });\n\n  /* Rules overview */\n  let rovCache = [];\n  function openRulesOverview(){\n    const bd = document.getElementById("modalRulesOverview");\n    if (!bd) return;\n    bd.classList.add("open");\n    bd.setAttribute("aria-hidden", "false");\n    loadRulesOverview();\n    setTimeout(() => document.getElementById("rovSearch")?.focus(), 80);\n  }\n  function closeRulesOverview(){\n    const bd = document.getElementById("modalRulesOverview");\n    if (!bd) return;\n    bd.classList.remove("open");\n    bd.setAttribute("aria-hidden", "true");\n  }\n  function renderRulesOverview(items){\n    const body = document.getElementById("rovBody");\n    const meta = document.getElementById("rovMeta");\n    if (!body) return;\n    const q = ((document.getElementById("rovSearch") && document.getElementById("rovSearch").value) || "").trim().toLowerCase();\n    let rows = items || rovCache;\n    if (q) {\n      rows = rows.filter(r => {\n        const hay = [r.group_name, r.type, r.value, r.rule, r.name].join(" ").toLowerCase();\n        return hay.includes(q);\n      });\n    }\n    if (meta) meta.textContent = "показано " + rows.length + " из " + (items || rovCache).length;\n    if (!rows.length) {\n      body.innerHTML = `<tr><td colspan="4" class="rov-empty">${q ? "Нет совпадений" : "Правил нет"}</td></tr>`;\n      return;\n    }\n    const typeLab = {ipv4:"IPV4",ipv6:"IPV6",namespace:"NS",domain:"DOM",keyword:"KW",raw:"RAW"};\n    const typeCls = {ipv4:"badge-v4",ipv6:"badge-v6",namespace:"badge-ns",domain:"badge-dom",keyword:"badge-kw",raw:"badge-raw"};\n    body.innerHTML = rows.map(r => {\n      const off = (!r.enabled || !r.group_enabled) ? " off" : "";\n      const t = r.type || "raw";\n      const val = r.value || r.rule || "—";\n      return `<tr class="${off}" data-gid="${escHtml(r.group_id || "")}" title="${escHtml(r.rule || "")}">\n        <td>${escHtml(r.group_name || "—")}${r.group_enabled ? "" : \' <span class="meta">off</span>\'}</td>\n        <td><span class="pill ${typeCls[t] || "badge-raw"}">${escHtml(typeLab[t] || t)}</span></td>\n        <td class="rov-val">${escHtml(val)}</td>\n        <td>${r.enabled ? "✓" : "—"}</td>\n      </tr>`;\n    }).join("");\n  }\n  async function loadRulesOverview(){\n    const body = document.getElementById("rovBody");\n    if (body) body.innerHTML = `<tr><td colspan="4" class="rov-empty">Загрузка…</td></tr>`;\n    try {\n      const res = await fetch("/api/rules/overview", { credentials: "same-origin" });\n      const d = await res.json();\n      if (!d.ok) {\n        if (body) body.innerHTML = `<tr><td colspan="4" class="rov-empty">${escHtml(d.error || "ошибка")}</td></tr>`;\n        return;\n      }\n      rovCache = d.items || [];\n      renderRulesOverview(rovCache);\n    } catch (e) {\n      if (body) body.innerHTML = `<tr><td colspan="4" class="rov-empty">сеть</td></tr>`;\n    }\n  }\n  function jumpToGroup(gid){\n    if (!gid) return;\n    closeRulesOverview();\n    if (typeof setView === "function") setView("rules");\n    const card = document.querySelector(`.gcard[data-gid="${CSS.escape(gid)}"]`);\n    if (!card) return;\n    card.classList.remove("collapsed");\n    card.scrollIntoView({ behavior: "smooth", block: "center" });\n    card.classList.add("gcard-flash");\n    setTimeout(() => card.classList.remove("gcard-flash"), 1200);\n  }\n  document.getElementById("btnRulesOverview")?.addEventListener("click", openRulesOverview);\n  document.getElementById("btnRovRefresh")?.addEventListener("click", loadRulesOverview);\n  document.getElementById("rovSearch")?.addEventListener("input", () => renderRulesOverview(rovCache));\n  document.getElementById("rovBody")?.addEventListener("click", (e) => {\n    const tr = e.target.closest("tr[data-gid]");\n    if (tr) jumpToGroup(tr.getAttribute("data-gid"));\n  });\n  document.getElementById("modalRulesOverview")?.addEventListener("click", (e) => {\n    if (e.target.id === "modalRulesOverview" || e.target.closest("[data-close]")) closeRulesOverview();\n  });\n\n\n  async function api(path, data){\n    const body = new URLSearchParams(data || {});\n    const res = await fetch(path, {\n      method: "POST",\n      headers: {"Content-Type":"application/x-www-form-urlencoded"},\n      body: body.toString(),\n      credentials: "same-origin"\n    });\n    const ct = res.headers.get("content-type") || "";\n    if (ct.includes("application/json")) return res.json();\n    return {ok: res.ok};\n  }\n\n  function renumber(card){\n    const rows = card.querySelectorAll(".rules-body .rule-row:not(.removing)");\n    rows.forEach((tr, i) => {\n      const cell = tr.querySelector(".col-id");\n      if (cell) cell.textContent = "#" + (i + 1);\n    });\n    card.querySelectorAll(".rule-count").forEach(el => { el.textContent = rows.length; });\n  }\n\n  function wirePatternDetect(inp){\n    const row = inp.closest("tr");\n    const sel = row.querySelector("select[name=etype]");\n    let tmr;\n    async function detectOne(){\n      const v = (inp.value || "").trim();\n      if (!v || !sel) return;\n      try {\n        const fd = new URLSearchParams();\n        fd.set("text", v);\n        const res = await fetch("/api/detect", {\n          method:"POST",\n          headers:{"Content-Type":"application/x-www-form-urlencoded"},\n          body: fd.toString(),\n          credentials:"same-origin"\n        });\n        const data = await res.json();\n        const it = (data.items||[])[0];\n        if (!it) return;\n        if (sel.querySelector(\'option[value="\'+it.type+\'"]\')) {\n          sel.value = it.type;\n          sel.className = "type-select " + it.type + (row.classList.contains("draft") ? " add-type" : "");\n        }\n      } catch(e){}\n    }\n    inp.addEventListener("input", () => { clearTimeout(tmr); tmr=setTimeout(detectOne, 180); });\n  }\n\n  function draftRowHtml(){\n    return `<tr class="rule-row draft">\n      <td class="col-id">+</td>\n      <td class="col-name-hide"><input class="cell-input" name="ename" placeholder="имя правила…"/></td>\n      <td class="col-type">\n        <select class="type-select namespace add-type" name="etype">\n          <option value="auto" selected>Auto</option>\n          <option value="namespace">Namespace</option>\n          <option value="ipv4">IPv4</option>\n          <option value="ipv6">IPv6</option>\n          <option value="domain">Domain</option>\n          <option value="keyword">Keyword</option>\n        </select>\n      </td>\n      <td class="col-pattern"><div class="pattern-cell">\n        <input class="cell-input mono pattern-input" name="value" placeholder="паттерн правила…" autofocus/>\n        <span class="rule-conflict-badge pill pill-status pill-conflict" hidden></span>\n      </div></td>\n      <td class="col-en">\n        <label class="switch" title="Включён">\n          <input type="checkbox" name="enabled" value="1" checked/>\n          <span></span>\n        </label>\n      </td>\n      <td class="col-del">\n        <button type="button" class="icon-del" data-del-draft aria-label="Отмена" title="Отмена">\n          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>\n        </button>\n      </td>\n    </tr>`;\n  }\n\n  /** Shared pattern key — same as import conflict check */\n  function normalizePattern(v){\n    return String(v || "").trim().toLowerCase().replace(/\\.$/, "");\n  }\n\n  const CONFLICT_WARN_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;\n\n  let conflictFilterOn = false;\n  let conflictScanTimer = null;\n  /** last scan stats for filter link label */\n  let conflictStats = { patterns: 0, groups: 0, totalGroups: 0 };\n\n  function scheduleConflictScan(){\n    if (conflictScanTimer) clearTimeout(conflictScanTimer);\n    conflictScanTimer = setTimeout(() => {\n      conflictScanTimer = null;\n      rebuildConflictMarkers();\n    }, 80);\n  }\n\n  function cardHasConflict(card){\n    if (!card) return false;\n    if (card.getAttribute("data-has-conflict") === "1") return true;\n    if (card.querySelector(".gcard-conflict-warn[data-show=\'1\']")) return true;\n    if (card.querySelector(".rule-row.is-conflict")) return true;\n    return false;\n  }\n\n  function updateConflictFilterLink(){\n    const link = document.getElementById("conflictFilter");\n    if (!link) return;\n    const { patterns, groups, totalGroups } = conflictStats;\n    if (patterns <= 0 || groups <= 0) {\n      link.hidden = true;\n      link.setAttribute("data-show", "0");\n      link.textContent = "";\n      link.classList.remove("active");\n      link.setAttribute("aria-pressed", "false");\n      if (conflictFilterOn) {\n        conflictFilterOn = false;\n        applyListFilter();\n      }\n      return;\n    }\n    link.hidden = false;\n    link.setAttribute("data-show", "1");\n    link.classList.toggle("active", conflictFilterOn);\n    link.setAttribute("aria-pressed", conflictFilterOn ? "true" : "false");\n    if (conflictFilterOn) {\n      link.textContent = "✕ Сбросить · " + groups + " из " + totalGroups + " с конфликтами";\n      link.title = "Показать все группы";\n    } else {\n      const word = patterns === 1 ? "конфликт" : (patterns < 5 ? "конфликта" : "конфликтов");\n      link.textContent = patterns + " " + word + " между группами";\n      link.title = "Показать только группы с пересекающимися правилами";\n    }\n  }\n\n  /**\n   * Scan all groups for cross-group pattern collisions (frontend, O(rules)).\n   * Marks group header icon + rule rows; updates filter link.\n   */\n  function rebuildConflictMarkers(){\n    const index = new Map(); // key -> [{gid,gname,row}]\n    document.querySelectorAll(".gcard").forEach(card => {\n      const gid = card.getAttribute("data-gid") || "";\n      const gname = (card.querySelector(".gcard-name")?.value || "").trim() || gid;\n      card.querySelectorAll(".rules-body .rule-row:not(.draft)").forEach(row => {\n        const val = row.querySelector("[name=value]")?.value || "";\n        const key = normalizePattern(val);\n        if (!key) return;\n        if (!index.has(key)) index.set(key, []);\n        index.get(key).push({ gid, gname, row });\n      });\n    });\n\n    // clear previous — .is-conflict drives badge visibility (CSS); wipe badge text\n    document.querySelectorAll(".rule-row.is-conflict").forEach(row => row.classList.remove("is-conflict"));\n    document.querySelectorAll(".rule-conflict-badge").forEach(b => {\n      b.hidden = true;\n      b.textContent = "";\n      b.removeAttribute("title");\n    });\n    document.querySelectorAll(".gcard").forEach(card => {\n      card.removeAttribute("data-has-conflict");\n      const w = card.querySelector(".gcard-conflict-warn");\n      if (w) {\n        w.hidden = true;\n        w.setAttribute("data-show", "0");\n        w.removeAttribute("title");\n        w.innerHTML = "";\n      }\n      const fc = card.querySelector(".gcard-conflict-count");\n      if (fc) {\n        fc.hidden = true;\n        fc.setAttribute("data-show", "0");\n        fc.textContent = "";\n      }\n    });\n\n    const groupStats = new Map(); // gid -> { count, others: Set }\n    let patternConflicts = 0;\n\n    function ensureConflictBadge(row){\n      let badge = row.querySelector(".rule-conflict-badge");\n      if (badge) return badge;\n      const cell = row.querySelector(".pattern-cell");\n      if (cell) {\n        badge = document.createElement("span");\n        badge.className = "rule-conflict-badge pill pill-status pill-conflict";\n        badge.hidden = true;\n        cell.appendChild(badge);\n        return badge;\n      }\n      const inp = row.querySelector(".pattern-input");\n      if (!inp || !inp.parentElement) return null;\n      const wrap = document.createElement("div");\n      wrap.className = "pattern-cell";\n      inp.parentElement.insertBefore(wrap, inp);\n      wrap.appendChild(inp);\n      badge = document.createElement("span");\n      badge.className = "rule-conflict-badge pill pill-status pill-conflict";\n      badge.hidden = true;\n      wrap.appendChild(badge);\n      return badge;\n    }\n\n    index.forEach((entries) => {\n      const gids = new Set(entries.map(e => e.gid));\n      if (gids.size < 2) return;\n      patternConflicts++;\n      entries.forEach(e => {\n        const others = [...new Set(entries.filter(x => x.gid !== e.gid).map(x => x.gname))];\n        const badge = ensureConflictBadge(e.row);\n        // single condition: only mark row if badge can be shown (badge + left accent stay in lockstep)\n        if (!badge) return;\n        const label = others.length === 1 ? others[0] : others.slice(0, 2).join(", ") + (others.length > 2 ? "…" : "");\n        badge.textContent = "конфликт: " + label;\n        badge.title = "Также в: " + others.join(", ");\n        badge.hidden = false;\n        e.row.classList.add("is-conflict"); // CSS left accent on first cell only\n        if (!groupStats.has(e.gid)) groupStats.set(e.gid, { count: 0, others: new Set() });\n        const st = groupStats.get(e.gid);\n        st.count++;\n        others.forEach(n => st.others.add(n));\n      });\n    });\n\n    groupStats.forEach((st, gid) => {\n      const card = document.querySelector(`.gcard[data-gid="${CSS.escape(gid)}"]`);\n      if (!card) return;\n      card.setAttribute("data-has-conflict", "1");\n      let w = card.querySelector(".gcard-conflict-warn");\n      if (!w) {\n        w = document.createElement("span");\n        w.className = "gcard-conflict-warn";\n        w.setAttribute("role", "img");\n        w.setAttribute("aria-label", "Конфликт правил");\n        const form = card.querySelector("form[action=\'/group-rename\']") || card.querySelector(".gcard-name")?.closest("form");\n        if (form) form.after(w);\n        else card.querySelector(".gcard-head")?.appendChild(w);\n      }\n      const tip = st.count + " правил пересекаются с другими группами" +\n        (st.others.size ? " (" + [...st.others].slice(0, 4).join(", ") + (st.others.size > 4 ? "…" : "") + ")" : "");\n      w.innerHTML = CONFLICT_WARN_SVG;\n      w.title = tip;\n      w.hidden = false;\n      w.setAttribute("data-show", "1");\n\n      // footer: "N правил · K конфликт(ов)" — only when K > 0\n      let fc = card.querySelector(".gcard-conflict-count");\n      if (!fc) {\n        fc = document.createElement("span");\n        fc.className = "gcard-conflict-count";\n        const countWrap = card.querySelector(".gcard-foot .rule-count-wrap");\n        if (countWrap) countWrap.after(fc);\n        else card.querySelector(".gcard-foot")?.prepend(fc);\n      }\n      const k = st.count;\n      const confWord = k === 1 ? "конфликт" : (k > 1 && k < 5 ? "конфликта" : "конфликтов");\n      fc.textContent = "· " + k + " " + confWord;\n      fc.hidden = false;\n      fc.setAttribute("data-show", "1");\n    });\n\n    const allCards = document.querySelectorAll(".gcard");\n    conflictStats = {\n      patterns: patternConflicts,\n      groups: groupStats.size,\n      totalGroups: allCards.length,\n    };\n    updateConflictFilterLink();\n    // re-apply list filter if active (flags were just rebuilt)\n    applyListFilter();\n  }\n\n  async function commitDraft(row){\n    if (!row || row.classList.contains("saving") || !row.classList.contains("draft")) return;\n    const card = row.closest(".gcard");\n    const gid = card && card.getAttribute("data-gid");\n    const val = (row.querySelector("[name=value]")?.value || "").trim();\n    if (!gid || !val) return;\n    row.classList.add("saving");\n    try {\n      const data = await api("/api/entry-add", {\n        gid,\n        value: val,\n        etype: row.querySelector("[name=etype]")?.value || "auto",\n        ename: row.querySelector("[name=ename]")?.value || "",\n        enabled: row.querySelector("[name=enabled]")?.checked ? "1" : "0"\n      });\n      if (!data.ok) {\n        toast(data.error || "Ошибка", false);\n        row.classList.remove("saving");\n        return;\n      }\n      const wrap = document.createElement("tbody");\n      wrap.innerHTML = data.html.trim();\n      const real = wrap.firstElementChild;\n      row.replaceWith(real);\n      renumber(card);\n      scheduleConflictScan();\n      toast("Добавлено: " + (data.entry?.value || val), true);\n    } catch (e) {\n      toast(String(e), false);\n      row.classList.remove("saving");\n    }\n  }\n\n  async function deleteEntry(row){\n    const card = row.closest(".gcard");\n    const gid = card && card.getAttribute("data-gid");\n    const eid = row.getAttribute("data-eid");\n    if (!gid) return;\n    if (row.classList.contains("draft") || !eid) {\n      row.classList.add("removing");\n      setTimeout(() => { row.remove(); renumber(card); scheduleConflictScan(); }, 200);\n      return;\n    }\n    row.classList.add("removing");\n    try {\n      const data = await api("/api/entry-delete", {gid, eid});\n      if (!data.ok) {\n        row.classList.remove("removing");\n        toast(data.error || "Не удалось удалить", false);\n        return;\n      }\n      setTimeout(() => {\n        row.remove();\n        renumber(card);\n        scheduleConflictScan();\n      }, 200);\n    } catch (e) {\n      row.classList.remove("removing");\n      toast(String(e), false);\n    }\n  }\n\n  async function saveRow(row){\n    if (row.classList.contains("draft")) return commitDraft(row);\n    const card = row.closest(".gcard");\n    const gid = card && card.getAttribute("data-gid");\n    const eid = row.getAttribute("data-eid");\n    const val = (row.querySelector("[name=value]")?.value || "").trim();\n    if (!gid || !eid || !val) return;\n    const data = await api("/api/entry-update", {\n      gid, eid,\n      value: val,\n      etype: row.querySelector("[name=etype]")?.value || "auto",\n      ename: row.querySelector("[name=ename]")?.value || "",\n      enabled: row.querySelector("[name=enabled]")?.checked ? "1" : "0"\n    });\n    if (!data.ok) toast(data.error || "Ошибка", false);\n    else scheduleConflictScan();\n  }\n\n  btnAdd.addEventListener("click", () => openModal(modalGroup));\n  btnExplain?.addEventListener("click", () => {\n    openModal(modalExplain);\n    const box = document.getElementById("explainResult");\n    if (box) { box.classList.remove("show"); box.innerHTML = ""; }\n    const inp = document.getElementById("explainInput");\n    setTimeout(() => inp?.focus(), 80);\n  });\n  document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", closeAll));\n  [modalGroup, modalImport, modalExplain].forEach(m => m?.addEventListener("click", e => { if (e.target === m) closeAll(); }));\n  document.addEventListener("keydown", e => { if (e.key === "Escape") closeAll(); });\n\n  /**\n   * Single status → color mapping for Explain route.\n   * CSS classes set --explain-status (stripe, status-dot, action text).\n   */\n  const EXPLAIN_STATUS = {\n    proxy: { key: "proxy", label: "PROXY", css: "ind-proxy" },\n    direct: { key: "direct", label: "DIRECT", css: "ind-direct" },\n    block: { key: "block", label: "BLOCK", css: "ind-block" },\n  };\n  function resolveExplainStatus(d, action){\n    const pol = String((action && action.policy) || "").toUpperCase();\n    const ind = String((d && d.indicator) || "").toLowerCase();\n    if (ind === "block" || pol === "BLOCK" || pol === "REJECT") return EXPLAIN_STATUS.block;\n    if (ind === "proxy" || pol === "PROXY") return EXPLAIN_STATUS.proxy;\n    return EXPLAIN_STATUS.direct;\n  }\n\n  function renderExplainResult(d){\n    const box = document.getElementById("explainResult");\n    if (!box) return;\n    if (!d || !d.ok) {\n      const stErr = EXPLAIN_STATUS.block;\n      box.innerHTML = `<div class="explain-card ${stErr.css}"><div class="explain-ind"><span class="explain-ind-dot" aria-hidden="true"></span>${escHtml(d && d.error ? d.error : "Ошибка")}</div></div>`;\n      box.classList.add("show");\n      return;\n    }\n    const st = d.steps || {};\n    const rule = st.rule || {};\n    const group = st.group || {};\n    const action = st.action || {};\n    const status = resolveExplainStatus(d, action);\n    const pol = (action.policy || status.label).toUpperCase();\n    const typeCls = (rule.type || "match").toLowerCase();\n    const typeLab = rule.type_label || rule.type || "—";\n    const pattern = rule.pattern != null ? rule.pattern : "—";\n    const idxLab = rule.index_label || (rule.index != null ? "#" + rule.index : "—");\n    const ruleHit = d.matched && d.match_source === "group";\n    // MATCH: one compact line — final "MATCH → DIRECT" only in Действие\n    const ruleBody = ruleHit\n      ? `<span class="explain-type-tag ${escHtml(typeCls)}">${escHtml(typeLab)}</span><span class="mono">${escHtml(pattern)}</span>\n         <div class="muted">правило ${escHtml(String(idxLab))} · <span class="mono">${escHtml(rule.rule || "")}</span></div>`\n      : (d.match_source === "system"\n        ? `<span class="explain-type-tag geoip">${escHtml(typeLab)}</span><span class="mono">${escHtml(pattern)}</span>\n           <div class="muted"><span class="mono">${escHtml(rule.rule || "")}</span></div>`\n        : `<span class="explain-type-tag match">MATCH</span><span class="muted">нет совпадений — применяется дефолт</span>`);\n    const groupBody = group.name && group.name !== "—"\n      ? `<strong>${escHtml(group.name)}</strong>${group.provider ? ` <span class="muted mono">(${escHtml(group.provider)})</span>` : ""}`\n      : `<span class="muted">${escHtml(group.name || "—")}</span>`;\n    let actionBody;\n    if (status.key === "proxy") {\n      actionBody = `<span class="explain-policy">PROXY</span>${action.node ? ` <span class="muted">→ нода <span class="mono">${escHtml(action.node)}</span></span>` : ""}\n         <div class="muted">${escHtml(action.detail || "")}</div>`;\n    } else if (status.key === "block") {\n      actionBody = `<span class="explain-policy">${escHtml(action.detail || pol || "BLOCK")}</span>`;\n    } else {\n      actionBody = `<span class="explain-policy">${escHtml(action.detail || pol || "DIRECT")}</span>`;\n    }\n\n    box.innerHTML = `\n      <div class="explain-card ${status.css}">\n        <div class="explain-ind"><span class="explain-ind-dot" aria-hidden="true"></span>${escHtml(status.label)}</div>\n        <div class="explain-chain">\n          <div class="explain-step">\n            <span class="explain-step-dot" aria-hidden="true"></span>\n            <div>\n              <div class="explain-step-label">Запрос</div>\n              <div class="explain-step-body"><span class="mono">${escHtml(st.request || d.query || "")}</span>\n                <div class="muted">${escHtml(d.query_kind === "ip" ? "IP" : "domain")}</div>\n              </div>\n            </div>\n          </div>\n          <div class="explain-step">\n            <span class="explain-step-dot" aria-hidden="true"></span>\n            <div>\n              <div class="explain-step-label">Совпавшее правило</div>\n              <div class="explain-step-body">${ruleBody}</div>\n            </div>\n          </div>\n          <div class="explain-step">\n            <span class="explain-step-dot" aria-hidden="true"></span>\n            <div>\n              <div class="explain-step-label">Группа</div>\n              <div class="explain-step-body">${groupBody}</div>\n            </div>\n          </div>\n          <div class="explain-step">\n            <span class="explain-step-dot" aria-hidden="true"></span>\n            <div>\n              <div class="explain-step-label">Действие</div>\n              <div class="explain-step-body">${actionBody}</div>\n            </div>\n          </div>\n        </div>\n      </div>`;\n    box.classList.add("show");\n  }\n\n  document.getElementById("explainForm")?.addEventListener("submit", async (e) => {\n    e.preventDefault();\n    const inp = document.getElementById("explainInput");\n    const btn = document.getElementById("btnExplainRun");\n    const q = (inp && inp.value || "").trim();\n    if (!q) return;\n    const prev = btn ? btn.textContent : "";\n    if (btn) { btn.disabled = true; btn.textContent = "Проверяю..."; }\n    try {\n      const d = await api("/api/explain-route", { q });\n      renderExplainResult(d);\n    } catch (err) {\n      renderExplainResult({ ok: false, error: String(err) });\n    } finally {\n      if (btn) { btn.disabled = false; btn.textContent = prev || "Проверить маршрут"; }\n    }\n  });\n\n  document.querySelectorAll("[data-import]").forEach(b => {\n    b.addEventListener("click", (e) => {\n      e.stopPropagation();\n      const gid = b.getAttribute("data-import") || "";\n      importGid.value = gid;\n      importText.value = "";\n      importPreviewItems = [];\n      previewBox.hidden = true;\n      previewStats.innerHTML = "";\n      const card = b.closest(".gcard");\n      const gname = card?.querySelector(".gcard-name")?.value || card?.querySelector(".gcard-name")?.getAttribute("value") || "—";\n      const nameEl = document.getElementById("importGroupName");\n      if (nameEl) nameEl.textContent = gname;\n      openModal(modalImport);\n      setTimeout(() => importText.focus(), 40);\n    });\n  });\n\n  document.querySelectorAll("[data-collapse]").forEach(b => {\n    b.addEventListener("click", (e) => {\n      e.stopPropagation();\n      const card = b.closest(".gcard");\n      if (card) card.classList.toggle("collapsed");\n    });\n  });\n  document.querySelectorAll(".gcard-head").forEach(head => {\n    head.addEventListener("click", (e) => {\n      if (e.target.closest("input,button,label,a,select,form button")) return;\n      const card = head.closest(".gcard");\n      if (card) card.classList.toggle("collapsed");\n    });\n  });\n\n  // + add rule in group header\n  document.querySelectorAll("[data-add-rule]").forEach(b => {\n    b.addEventListener("click", (e) => {\n      e.stopPropagation();\n      const card = b.closest(".gcard");\n      if (!card) return;\n      card.classList.remove("collapsed");\n      const body = card.querySelector(".rules-body");\n      if (!body) return;\n      // only one draft at a time per group\n      const existing = body.querySelector(".rule-row.draft");\n      if (existing) {\n        existing.querySelector(".pattern-input")?.focus();\n        return;\n      }\n      body.insertAdjacentHTML("afterbegin", draftRowHtml());\n      const draft = body.querySelector(".rule-row.draft");\n      const inp = draft.querySelector(".pattern-input");\n      wirePatternDetect(inp);\n      setTimeout(() => inp.focus(), 30);\n    });\n  });\n\n  // delegated events for rows\n  document.getElementById("panel-groups").addEventListener("click", (e) => {\n    const del = e.target.closest("[data-del-entry], [data-del-draft]");\n    if (del) {\n      e.preventDefault();\n      const row = del.closest(".rule-row");\n      if (row) deleteEntry(row);\n      return;\n    }\n    const saveBtn = e.target.closest(".btn-save-group");\n    if (saveBtn) {\n      const card = saveBtn.closest(".gcard");\n      (async () => {\n        const rows = card.querySelectorAll(".rule-row:not(.draft)");\n        for (const row of rows) {\n          try { await saveRow(row); } catch(err) {}\n        }\n        scheduleConflictScan();\n        toast("Сохранено", true);\n      })();\n    }\n  });\n\n  document.getElementById("panel-groups").addEventListener("keydown", (e) => {\n    const inp = e.target.closest(".pattern-input, .cell-input");\n    if (!inp || e.key !== "Enter") return;\n    e.preventDefault();\n    const row = inp.closest(".rule-row");\n    if (row) saveRow(row);\n  });\n\n  document.getElementById("panel-groups").addEventListener("change", (e) => {\n    const t = e.target;\n    if (t.matches("[name=enabled]") && t.closest(".rule-row:not(.draft)")) {\n      const row = t.closest(".rule-row");\n      const card = row.closest(".gcard");\n      api("/api/entry-update", {\n        gid: card.getAttribute("data-gid"),\n        eid: row.getAttribute("data-eid"),\n        value: row.querySelector("[name=value]")?.value || "",\n        etype: row.querySelector("[name=etype]")?.value || "auto",\n        ename: row.querySelector("[name=ename]")?.value || "",\n        enabled: t.checked ? "1" : "0"\n      }).then(d => {\n        if (!d.ok) toast(d.error || "Ошибка", false);\n        else scheduleConflictScan();\n      });\n    }\n    if (t.matches("select[name=etype]")) {\n      t.className = "type-select " + t.value;\n      if (t.closest(".rule-row:not(.draft)")) scheduleConflictScan();\n    }\n  });\n\n  document.getElementById("panel-groups").addEventListener("focusout", (e) => {\n    const inp = e.target.closest(".pattern-input");\n    if (!inp) return;\n    const row = inp.closest(".rule-row.draft");\n    if (row && (inp.value || "").trim()) {\n      // slight delay so click on delete can win\n      setTimeout(() => {\n        if (document.body.contains(row) && row.classList.contains("draft")) commitDraft(row);\n      }, 120);\n    }\n  });\n\n  document.querySelectorAll(".pattern-input").forEach(wirePatternDetect);\n\n  // ── Mobile: tap pattern → copy popover ──\n  function isMobileLayout(){\n    try { return window.matchMedia("(max-width:720px)").matches; } catch(e) { return false; }\n  }\n  function closePatternCopyToast(){\n    document.getElementById("patternCopyToast")?.remove();\n  }\n  function showPatternCopyToast(val){\n    closePatternCopyToast();\n    const el = document.createElement("div");\n    el.id = "patternCopyToast";\n    el.className = "pattern-copy-toast";\n    el.setAttribute("role", "dialog");\n    el.innerHTML =\n      "<code></code>" +\n      \'<div class="pct-actions">\' +\n      \'<button type="button" class="btn btn-ghost" data-pct-close>Закрыть</button>\' +\n      \'<button type="button" class="btn btn-primary" data-pct-copy>Копировать</button>\' +\n      "</div>";\n    el.querySelector("code").textContent = val;\n    el.querySelector("[data-pct-close]").addEventListener("click", closePatternCopyToast);\n    el.querySelector("[data-pct-copy]").addEventListener("click", async () => {\n      try {\n        if (navigator.clipboard && navigator.clipboard.writeText) {\n          await navigator.clipboard.writeText(val);\n        } else {\n          const ta = document.createElement("textarea");\n          ta.value = val; document.body.appendChild(ta); ta.select();\n          document.execCommand("copy"); ta.remove();\n        }\n        toast("Скопировано", true);\n        closePatternCopyToast();\n      } catch (err) {\n        toast("Не удалось скопировать", false);\n      }\n    });\n    document.body.appendChild(el);\n  }\n  document.getElementById("panel-groups")?.addEventListener("click", (e) => {\n    if (!isMobileLayout()) return;\n    if (e.target.closest("button,label,select,a,.switch,.icon-del,.mini-btn")) return;\n    const inp = e.target.closest(".pattern-input");\n    if (!inp) return;\n    const val = (inp.value || "").trim();\n    if (!val) return;\n    // show when multi-line / long / or always on mobile for easy copy\n    if (val.length >= 18 || inp.scrollHeight > inp.clientHeight + 4 || true) {\n      e.preventDefault();\n      showPatternCopyToast(val);\n    }\n  });\n\n\n  function updateCardFilterSlice(card, shown, total){\n    let el = card.querySelector(".gcard-filter-slice");\n    if (!conflictFilterOn || total === 0) {\n      if (el) {\n        el.hidden = true;\n        el.setAttribute("data-show", "0");\n        el.textContent = "";\n      }\n      return;\n    }\n    if (!el) {\n      el = document.createElement("span");\n      el.className = "gcard-filter-slice";\n      const foot = card.querySelector(".gcard-foot");\n      const save = foot?.querySelector(".btn-save-group");\n      if (foot && save) foot.insertBefore(el, save);\n      else if (foot) foot.appendChild(el);\n    }\n    el.hidden = false;\n    el.setAttribute("data-show", "1");\n    el.textContent = shown + " из " + total + " правил (только конфликты)";\n  }\n\n  /**\n   * Apply search query AND optional conflict-only filter (intersection).\n   * Groups: .is-filtered-out. Rows: .is-row-filtered-out (display-only).\n   * When conflict filter is on, only .is-conflict rows stay visible inside a group.\n   */\n  function applyListFilter(){\n    if (typeof currentView !== "undefined" && currentView === "stats") {\n      filterStatsBySearch();\n      return;\n    }\n    const q = (searchInput?.value || "").trim().toLowerCase();\n    document.querySelectorAll(".gcard").forEach(card => {\n      const groupHay = (card.getAttribute("data-filter") || "").toLowerCase();\n      const rows = card.querySelectorAll(".rules-body .rule-row");\n      const dataRows = card.querySelectorAll(".rules-body .rule-row:not(.draft)");\n      const conflictOk = !conflictFilterOn || cardHasConflict(card);\n      const total = dataRows.length;\n\n      const title = (card.querySelector(".gcard-name")?.value || "").toLowerCase();\n      // only group TITLE counts as "name hit" (shows all rows).\n      // do NOT use groupHay here ? it contains all patterns and would unfilter every row.\n      let groupNameHit = !q ? true : title.includes(q);\n\n      let anySearchHit = false;\n      let anyVisible = false;\n      let shownConflicts = 0;\n\n      rows.forEach(r => {\n        r.style.display = ""; // class controls hide\n        r.classList.remove("search-hit");\n\n        if (r.classList.contains("draft")) {\n          // drafts always visible when group is shown (edit in progress)\n          r.classList.remove("is-row-filtered-out");\n          return;\n        }\n\n        const isConf = r.classList.contains("is-conflict");\n        const rowHay = (\n          (r.getAttribute("data-filter") || "") + " " +\n          (r.querySelector("[name=ename]")?.value || "") + " " +\n          (r.querySelector("[name=value]")?.value || "") + " " +\n          (r.querySelector("[name=etype]")?.value || "")\n        ).toLowerCase();\n        const searchHit = !q || rowHay.includes(q);\n        if (searchHit && q) anySearchHit = true;\n\n        // intersection: conflict-filter requires is-conflict; search requires hit\n        // if group name matches search, all conflict rows (or all rows) count as ok for search\n        let show = true;\n        if (conflictFilterOn) show = isConf;\n        if (q) {\n          if (groupNameHit) {\n            // name match → keep row if it already passed conflict gate\n            show = show;\n          } else {\n            show = show && searchHit;\n          }\n        }\n\n        r.classList.toggle("is-row-filtered-out", !show);\n        r.classList.toggle("search-hit", !!(q && searchHit && show));\n        if (show) {\n          anyVisible = true;\n          if (isConf) shownConflicts++;\n        }\n      });\n\n      const searchOk = !q || groupNameHit || anySearchHit;\n      // if conflict filter: group must have conflicts; if also search, need searchOk\n      // when conflict filter + search: prefer groups that have at least one visible row\n      let showCard = conflictOk && searchOk;\n      if (conflictFilterOn && q && !groupNameHit) {\n        showCard = conflictOk && anyVisible;\n      } else if (conflictFilterOn && !q) {\n        showCard = conflictOk;\n      } else if (!conflictFilterOn && q) {\n        showCard = searchOk;\n      } else {\n        showCard = true;\n      }\n\n      card.classList.toggle("is-filtered-out", !showCard);\n      card.style.display = "";\n\n      if (showCard) {\n        if (q && anySearchHit) card.classList.remove("collapsed");\n        // conflict-only filter: leave expand/collapse to user (don\'t force collapse)\n        if (!q && !conflictFilterOn) card.classList.add("collapsed");\n      }\n\n      // footer slice label only when conflict filter on and card visible\n      if (showCard && conflictFilterOn) {\n        updateCardFilterSlice(card, shownConflicts, total);\n      } else {\n        updateCardFilterSlice(card, 0, 0);\n      }\n    });\n  }\n  // back-compat alias\n  function filter(q){ applyListFilter(); }\n\n\n  // ── View: Правила / Статистика ──\n  const VIEW_KEY = "xttp_panel_view";\n  let viewGen = 0;\n  let currentView = "rules";\n  let statsTimer = null;\n  let trafficHistory = []; // {t, upRate, downRate}\n  let lastTotals = null; // {up, down, t}\n  let lastStatsData = null;\n  let statsSessionStart = null;\n  let statsPeriod = "live";\n  let statsLines = { down: true, up: true };\n  let statsHoverIdx = -1;\n  let statsChartLayout = null;\n  let statsRafId = 0;\n  let statsSmoothMax = 1;\n  let statsChartGeom = { w: 0, h: 0, dpr: 0 };\n  let statsLastFrameT = 0;\n  let statsReduceMotion = false;\n  /* continuous display clock + lerped tip rates (smooth live scroll, no 1Hz snap) */\n  let statsVis = { up: 0, down: 0, primed: false };\n  const PERIOD_KEY = "xttp_stats_period";\n  const LINES_KEY = "xttp_stats_lines";\n  const PERIOD_SEC = { live: 90, "5m": 300, "1h": 3600, session: Infinity };\n  const PERIOD_LABEL = { live: "live", "5m": "5 мин", "1h": "1 час", session: "сессия" };\n  const MAX_HISTORY = 3700;\n  const statsSearchPlaceholder = "Фильтр по сервису…";\n  const rulesSearchPlaceholder = "Поиск сервисов…";\n\n  function moveViewPill(){\n    const wrap = document.getElementById("viewSwitch");\n    const pill = document.getElementById("viewPill");\n    const btn = wrap?.querySelector(".stab.active");\n    if (!pill || !wrap || !btn) return;\n    const wr = wrap.getBoundingClientRect();\n    const br = btn.getBoundingClientRect();\n    pill.style.width = br.width + "px";\n    pill.style.transform = "translate3d(" + (br.left - wr.left - 4) + "px,0,0)";\n  }\n\n  function fmtBytes(n){\n    n = Number(n) || 0;\n    const u = ["B","KB","MB","GB","TB"];\n    let i = 0;\n    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }\n    return (i === 0 ? String(Math.round(n)) : n.toFixed(n >= 10 ? 1 : 2)) + " " + u[i];\n  }\n  function fmtRate(bps){\n    return fmtBytes(bps) + "/s";\n  }\n\n  function setView(view, opts){\n    opts = opts || {};\n    const allowed = {rules:1, stats:1, conns:1, logs:1};\n    const next = allowed[view] ? view : "rules";\n    currentView = next;\n    viewGen++;\n\n    document.querySelectorAll("#viewSwitch .stab").forEach(b => {\n      const on = b.getAttribute("data-view") === next;\n      b.classList.toggle("active", on);\n      b.setAttribute("aria-selected", on ? "true" : "false");\n    });\n    try { moveViewPill(); } catch (_) {}\n\n    const rulesPanel = document.getElementById("panel-groups");\n    const statsPanel = document.getElementById("panel-stats");\n    const connsPanel = document.getElementById("panel-conns");\n    const logsPanel = document.getElementById("panel-logs");\n    const actions = document.getElementById("toolbarRulesActions");\n\n    function activatePanel(panel, on){\n      if (!panel) return;\n      panel.classList.remove("is-hiding");\n      panel.style.opacity = "";\n      panel.style.transform = "";\n      panel.style.pointerEvents = "";\n      panel.style.position = "";\n      panel.style.width = "";\n      panel.style.inset = "";\n      panel.style.visibility = "";\n      panel.style.display = "";\n      panel.hidden = !on;\n      if (on) {\n        panel.hidden = false;\n        panel.style.setProperty("opacity", "1", "important");\n        panel.style.setProperty("visibility", "visible", "important");\n        panel.style.setProperty("display", "block", "important");\n        panel.style.setProperty("transform", "none", "important");\n        panel.style.setProperty("pointer-events", "auto", "important");\n        panel.style.setProperty("position", "relative", "important");\n      }\n    }\n\n    activatePanel(rulesPanel, next === "rules");\n    activatePanel(statsPanel, next === "stats");\n    activatePanel(connsPanel, next === "conns");\n    activatePanel(logsPanel, next === "logs");\n\n    if (actions) actions.hidden = next !== "rules";\n\n    if (searchInput) {\n      const ph = {\n        rules: rulesSearchPlaceholder,\n        stats: statsSearchPlaceholder,\n        conns: "Фильтр по домену / IP…",\n        logs: "Фильтр по тексту лога…",\n      };\n      searchInput.placeholder = ph[next] || rulesSearchPlaceholder;\n      searchInput.setAttribute("aria-label", searchInput.placeholder);\n    }\n\n    if (opts.persist !== false) {\n      try { localStorage.setItem(VIEW_KEY, next); } catch (e) {}\n    }\n\n    // stop all side pollers then start the active one\n    try { stopStatsPolling(); } catch (_) {}\n    try { stopConnsPolling(); } catch (_) {}\n    try { stopLogsPolling(); } catch (_) {}\n\n    if (next === "stats") {\n      try { startStatsPolling(); } catch (_) {}\n      try { filterStatsBySearch(); } catch (_) {}\n    } else if (next === "rules") {\n      try { reviveRulesCards(); } catch (_) {}\n      try { applyListFilter(); } catch (_) {}\n      try { reviveRulesCards(); } catch (_) {}\n    } else if (next === "conns") {\n      try { startConnsPolling(); } catch (_) {}\n    } else if (next === "logs") {\n      try { startLogsPolling(); } catch (_) {}\n    }\n  }\n\n  function reviveRulesCards(){\n    const panel = document.getElementById("panel-groups");\n    if (!panel || currentView !== "rules") return;\n    panel.hidden = false;\n    panel.classList.remove("is-hiding");\n    panel.style.setProperty("opacity", "1", "important");\n    panel.style.setProperty("display", "block", "important");\n    panel.style.setProperty("visibility", "visible", "important");\n\n    const cards = panel.querySelectorAll(".gcard");\n    cards.forEach(c => {\n      c.style.setProperty("opacity", "1", "important");\n      c.style.setProperty("transform", "none", "important");\n      c.style.setProperty("animation", "none", "important");\n      c.style.setProperty("visibility", "visible", "important");\n      // only clear display if we didn\'t intentionally filter\n      if (!c.classList.contains("is-filtered-out")) {\n        c.style.removeProperty("display");\n        if (c.style.display === "none") c.style.display = "";\n      }\n    });\n\n    // empty-state only if truly no cards in DOM\n    let empty = document.getElementById("rulesEmptyFallback");\n    if (!cards.length) {\n      if (!empty) {\n        empty = document.createElement("div");\n        empty.id = "rulesEmptyFallback";\n        empty.className = "empty-wrap";\n        empty.innerHTML =\n          \'<div class="empty-card"><div><h3>Список правил пуст</h3>\' +\n          \'<p class="meta">Карточки групп не найдены. Нажмите «Обновить».</p>\' +\n          \'<button type="button" class="btn btn-primary" id="rulesEmptyReload" style="margin-top:10px">Обновить</button></div></div>\';\n        panel.appendChild(empty);\n        empty.querySelector("#rulesEmptyReload")?.addEventListener("click", () => location.reload());\n      }\n      empty.hidden = false;\n    } else if (empty) {\n      empty.hidden = true;\n    }\n\n    // if every card filtered out with no query — unstick\n    const q = (searchInput && searchInput.value || "").trim();\n    if (cards.length && !q) {\n      const anyVisible = [...cards].some(c => !c.classList.contains("is-filtered-out"));\n      if (!anyVisible) {\n        conflictFilterOn = false;\n        cards.forEach(c => {\n          c.classList.remove("is-filtered-out");\n          c.style.removeProperty("display");\n        });\n        try {\n          if (typeof updateConflictFilterLink === "function") updateConflictFilterLink();\n          applyListFilter();\n        } catch (_) {}\n        cards.forEach(c => {\n          c.style.setProperty("opacity", "1", "important");\n          c.style.setProperty("animation", "none", "important");\n        });\n      }\n    }\n  }\n\n  function filterStatsBySearch(){\n    const q = (searchInput?.value || "").trim().toLowerCase();\n    const rows = document.querySelectorAll("#statsBody tr[data-name]");\n    rows.forEach(tr => {\n      const name = (tr.getAttribute("data-name") || "").toLowerCase();\n      const id = (tr.getAttribute("data-id") || "").toLowerCase();\n      const hit = !q || name.includes(q) || id.includes(q);\n      tr.classList.toggle("is-dim", !hit);\n      tr.hidden = q ? !hit : false;\n    });\n    // redraw chart unfiltered totals always; table filters only\n  }\n\n  function loadStatsPrefs(){\n    try {\n      const p = localStorage.getItem(PERIOD_KEY);\n      if (p && PERIOD_SEC[p] != null) statsPeriod = p;\n      const l = localStorage.getItem(LINES_KEY);\n      if (l) {\n        const o = JSON.parse(l);\n        if (typeof o.down === "boolean") statsLines.down = o.down;\n        if (typeof o.up === "boolean") statsLines.up = o.up;\n      }\n    } catch (e) {}\n    if (!statsLines.down && !statsLines.up) statsLines.down = true;\n  }\n  function saveStatsPrefs(){\n    try {\n      localStorage.setItem(PERIOD_KEY, statsPeriod);\n      localStorage.setItem(LINES_KEY, JSON.stringify(statsLines));\n    } catch (e) {}\n  }\n  function moveStatsPeriodPill(){\n    const wrap = document.getElementById("statsPeriod");\n    const pill = document.getElementById("statsPeriodPill");\n    const btn = wrap && wrap.querySelector(".stab.active");\n    if (!pill || !wrap || !btn) return;\n    const wr = wrap.getBoundingClientRect();\n    const br = btn.getBoundingClientRect();\n    pill.style.width = br.width + "px";\n    pill.style.transform = "translate3d(" + (br.left - wr.left - 4) + "px,0,0)";\n  }\n  function applyStatsPrefsToUi(){\n    document.querySelectorAll("#statsPeriod .stab").forEach(b => {\n      b.classList.toggle("active", b.getAttribute("data-period") === statsPeriod);\n    });\n    const ld = document.getElementById("lineDown");\n    const lu = document.getElementById("lineUp");\n    if (ld) ld.checked = !!statsLines.down;\n    if (lu) lu.checked = !!statsLines.up;\n    const title = document.getElementById("statsChartTitle");\n    if (title) title.textContent = "Трафик (" + (PERIOD_LABEL[statsPeriod] || statsPeriod) + ")";\n    moveStatsPeriodPill();\n  }\n  function getPeriodWindow(){\n    const now = Date.now() / 1000;\n    if (statsPeriod === "session") {\n      let t0 = statsSessionStart != null ? statsSessionStart : now - 90;\n      if (trafficHistory.length) {\n        const first = trafficHistory[0].t;\n        if (statsSessionStart == null || first < t0) {\n          /* keep session start; still clamp left to first sample if later */\n        }\n        if (statsSessionStart == null) t0 = first;\n      }\n      return { t0: t0, t1: now, label: PERIOD_LABEL.session || "сессия" };\n    }\n    const win = Number(PERIOD_SEC[statsPeriod]) || 90;\n    return { t0: now - win, t1: now, label: PERIOD_LABEL[statsPeriod] || statsPeriod };\n  }\n  function getHistoryForPeriod(){\n    if (!trafficHistory.length) return [];\n    const { t0, t1 } = getPeriodWindow();\n    // include slight slack so the newest sample is never dropped by clock skew\n    return trafficHistory.filter(p => p.t >= t0 - 0.25 && p.t <= t1 + 0.75);\n  }\n  function stopStatsPolling(){\n    if (statsTimer) { clearInterval(statsTimer); statsTimer = null; }\n    if (statsRafId) { cancelAnimationFrame(statsRafId); statsRafId = 0; }\n    hideStatsTooltip();\n  }\n  function statsAnimLoop(ts){\n    statsRafId = 0;\n    if (currentView !== "stats") return;\n    if (typeof document !== "undefined" && document.hidden) {\n      statsRafId = requestAnimationFrame(statsAnimLoop);\n      return;\n    }\n    const nowMs = ts || performance.now();\n    const dt = statsLastFrameT ? Math.min(0.05, (nowMs - statsLastFrameT) / 1000) : 0.016;\n    statsLastFrameT = nowMs;\n    // lerp tip rates toward latest sample every frame → no hard jump on poll\n    if (trafficHistory.length) {\n      const last = trafficHistory[trafficHistory.length - 1];\n      const tu = Math.max(0, last.upRate || 0);\n      const td = Math.max(0, last.downRate || 0);\n      if (!statsVis.primed || statsReduceMotion) {\n        statsVis.up = tu; statsVis.down = td; statsVis.primed = true;\n      } else {\n        const k = 1 - Math.exp(-dt * 7);\n        statsVis.up += (tu - statsVis.up) * k;\n        statsVis.down += (td - statsVis.down) * k;\n      }\n    }\n    drawStatsChart(dt);\n    statsRafId = requestAnimationFrame(statsAnimLoop);\n  }\n  function startStatsAnim(){\n    if (statsRafId) return;\n    try {\n      statsReduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);\n    } catch (e) { statsReduceMotion = false; }\n    statsLastFrameT = 0;\n    statsRafId = requestAnimationFrame(statsAnimLoop);\n  }\n  function startStatsPolling(){\n    stopStatsPolling();\n    loadStatsPrefs();\n    applyStatsPrefsToUi();\n    bindStatsChartUi();\n    if (statsSessionStart == null) statsSessionStart = Date.now() / 1000;\n    refreshStats();\n    statsTimer = setInterval(refreshStats, 1000);\n    startStatsAnim();\n    requestAnimationFrame(() => moveStatsPeriodPill());\n  }\n  async function refreshStats(){\n    try {\n      const res = await fetch("/api/stats/snapshot", { credentials: "same-origin" });\n      const d = await res.json();\n      if (!d.ok) {\n        const body = document.getElementById("statsBody");\n        if (body) body.innerHTML = `<tr><td colspan="6" class="stats-empty">${escHtml(d.error || "ошибка")}</td></tr>`;\n        return;\n      }\n      lastStatsData = d;\n      const now = Date.now() / 1000;\n      let upRate = 0, downRate = 0;\n      // Prefer server ring-buffer history (survives F5). Fallback: client delta.\n      if (Array.isArray(d.history) && d.history.length) {\n        const incoming = d.history.map(p => ({\n          t: Number(p.t) || 0,\n          upRate: Math.max(0, Number(p.upRate) || 0),\n          downRate: Math.max(0, Number(p.downRate) || 0),\n        }));\n        // Prefer append/merge by timestamp so the live window slides instead of full rebuild snap\n        if (!trafficHistory.length) {\n          trafficHistory = incoming;\n        } else {\n          const lastT = trafficHistory[trafficHistory.length - 1].t || 0;\n          let appended = 0;\n          for (let i = 0; i < incoming.length; i++) {\n            const p = incoming[i];\n            if (p.t > lastT + 0.05) {\n              trafficHistory.push(p);\n              appended++;\n            } else if (p.t >= lastT - 0.05) {\n              trafficHistory[trafficHistory.length - 1] = p;\n            }\n          }\n          // if server rewound / first load after gap — take full ring\n          if (!appended && incoming.length >= trafficHistory.length - 2) {\n            const incLast = incoming[incoming.length - 1].t || 0;\n            const curLast = trafficHistory[trafficHistory.length - 1].t || 0;\n            if (Math.abs(incLast - curLast) < 2) {\n              /* keep merged */\n            } else {\n              trafficHistory = incoming;\n            }\n          }\n          if (trafficHistory.length > MAX_HISTORY) {\n            trafficHistory.splice(0, trafficHistory.length - MAX_HISTORY);\n          }\n        }\n        const last = trafficHistory[trafficHistory.length - 1];\n        upRate = last.upRate || 0;\n        downRate = last.downRate || 0;\n        lastTotals = { up: d.uploadTotal, down: d.downloadTotal, t: Number(d.ts) || now };\n      } else {\n        if (lastTotals && now > lastTotals.t) {\n          const dt = Math.max(0.5, now - lastTotals.t);\n          upRate = Math.max(0, (d.uploadTotal - lastTotals.up) / dt);\n          downRate = Math.max(0, (d.downloadTotal - lastTotals.down) / dt);\n        }\n        lastTotals = { up: d.uploadTotal, down: d.downloadTotal, t: now };\n        trafficHistory.push({ t: now, upRate, downRate });\n        if (trafficHistory.length > MAX_HISTORY) {\n          trafficHistory.splice(0, trafficHistory.length - MAX_HISTORY);\n        }\n      }\n      const el = (id) => document.getElementById(id);\n      if (el("statDownRate")) el("statDownRate").textContent = fmtRate(downRate);\n      if (el("statUpRate")) el("statUpRate").textContent = fmtRate(upRate);\n      if (el("statDownTotal")) el("statDownTotal").textContent = "всего " + fmtBytes(d.downloadTotal);\n      if (el("statUpTotal")) el("statUpTotal").textContent = "всего " + fmtBytes(d.uploadTotal);\n      if (el("statConns")) el("statConns").textContent = String(d.connections ?? "—");\n      if (el("statConnSub")) {\n        const mem = Number(d.memory) || 0;\n        const px = d.proxyConnections;\n        if (mem > 0) el("statConnSub").textContent = "RAM " + fmtBytes(mem);\n        else el("statConnSub").textContent = "через PROXY: " + String(px ?? 0);\n      }\n      const hitSum = (d.groups || []).reduce((s, g) => s + (g.hits || 0), 0);\n      if (el("statHits")) el("statHits").textContent = String(hitSum);\n      renderStatsTable(d.groups || []);\n      // paint only via rAF loop — avoid 1Hz full redraw flash\n      if (!statsRafId && currentView === "stats") startStatsAnim();\n      filterStatsBySearch();\n      if (el("statsMeta")) {\n        const _pw = getPeriodWindow();\n        el("statsMeta").textContent = "обновлено " + new Date().toLocaleTimeString()\n          + " · группы " + (d.groups || []).length\n          + " · " + (PERIOD_LABEL[statsPeriod] || statsPeriod)\n          + " · точек " + getHistoryForPeriod().length\n          + "/" + trafficHistory.length;\n      }\n    } catch (e) {\n      /* ignore transient */\n    }\n  }\n\n  function renderStatsTable(groups){\n    const body = document.getElementById("statsBody");\n    if (!body) return;\n    if (!groups.length) {\n      body.innerHTML = `<tr><td colspan="6" class="stats-empty">Нет RuleSet-групп (примени правила)</td></tr>`;\n      return;\n    }\n    const maxBytes = Math.max(1, ...groups.map(g => (g.up || 0) + (g.down || 0)));\n    const grand = groups.reduce((s, g) => s + (g.up || 0) + (g.down || 0), 0) || 1;\n    body.innerHTML = groups.map(g => {\n      const up = g.up || 0, down = g.down || 0;\n      const total = up + down;\n      const pct = Math.round(100 * total / maxBytes);\n      const share = Math.round(1000 * total / grand) / 10;\n      const tip = fmtBytes(down) + " download, " + fmtBytes(up) + " upload — " + share + "% от суммарного трафика групп";\n      return `<tr data-name="${escHtml(g.name || "")}" data-id="${escHtml(g.id || "")}">\n        <td><strong>${escHtml(g.name || g.id || "")}</strong></td>\n        <td>${g.hits || 0}</td>\n        <td>${g.conns || 0}</td>\n        <td>${fmtBytes(down)}</td>\n        <td>${fmtBytes(up)}</td>\n        <td><div class="bar" title="${escHtml(tip)}"><i style="width:${pct}%"></i></div></td>\n      </tr>`;\n    }).join("");\n  }\n\n  \n  /** Fixed bucket size (seconds) for a time span — deterministic, no random sampling. */\n  function bucketSecForSpan(span){\n    span = Math.max(1, Number(span) || 1);\n    if (span <= 120) return 1;       // live ~90s: 1s buckets\n    if (span <= 400) return 2;       // 5 min\n    if (span <= 1200) return 5;\n    if (span <= 3600) return 15;     // 1 hour → 240 buckets\n    if (span <= 7200) return 30;\n    // long session: aim ~240 points\n    return Math.max(60, Math.floor(span / 240));\n  }\n\n  /**\n   * Aggregate rate samples into fixed absolute time buckets:\n   *   key = floor(t / bucketSec) * bucketSec\n   * Completed buckets (key + bucketSec <= t1) are stable averages.\n   * The open bucket containing t1 may update every poll — expected.\n   */\n  function aggregateHistoryBuckets(hist, t0, t1, bucketSec){\n    if (!hist || !hist.length) return [];\n    bucketSec = Math.max(1, Number(bucketSec) || 1);\n    t0 = Number(t0) || 0;\n    t1 = Number(t1) || 0;\n    const map = new Map(); // key -> {sumU,sumD,n}\n    for (let i = 0; i < hist.length; i++) {\n      const p = hist[i];\n      const t = Number(p.t) || 0;\n      if (t < t0 - bucketSec || t > t1 + bucketSec) continue;\n      const key = Math.floor(t / bucketSec) * bucketSec;\n      let b = map.get(key);\n      if (!b) {\n        b = { sumU: 0, sumD: 0, n: 0 };\n        map.set(key, b);\n      }\n      b.sumU += Math.max(0, Number(p.upRate) || 0);\n      b.sumD += Math.max(0, Number(p.downRate) || 0);\n      b.n += 1;\n    }\n    const keys = Array.from(map.keys()).sort((a, b) => a - b);\n    // open bucket starts at floor(t1 / bucketSec) * bucketSec\n    const openKey = Math.floor(t1 / bucketSec) * bucketSec;\n    const out = [];\n    for (let i = 0; i < keys.length; i++) {\n      const key = keys[i];\n      // drop buckets entirely left of window\n      if (key + bucketSec < t0) continue;\n      if (key > t1) continue;\n      const b = map.get(key);\n      const n = b.n || 1;\n      // plot at bucket center — fixed for a given key (shape-stable)\n      const tMid = key + bucketSec * 0.5;\n      out.push({\n        t: tMid,\n        upRate: b.sumU / n,\n        downRate: b.sumD / n,\n        _bucket: key,\n        _open: key === openKey,\n      });\n    }\n    return out;\n  }\n\n\n  function drawStatsChart(dt){\n    const canvas = document.getElementById("statsChart");\n    if (!canvas || !canvas.getContext) return;\n    if (canvas.style.visibility === "hidden") return;\n    if (typeof dt !== "number" || !(dt > 0)) dt = 0.016;\n\n    const parent = canvas.parentElement;\n    const cssW = Math.max(300, parent ? parent.clientWidth - 8 : 900);\n    const cssH = 160;\n    const dpr = window.devicePixelRatio || 1;\n\n    const geom = statsChartGeom;\n    if (geom.w !== cssW || geom.h !== cssH || geom.dpr !== dpr) {\n      canvas.width = Math.floor(cssW * dpr);\n      canvas.height = Math.floor(cssH * dpr);\n      canvas.style.width = cssW + "px";\n      canvas.style.height = cssH + "px";\n      geom.w = cssW; geom.h = cssH; geom.dpr = dpr;\n    }\n\n    const ctx = canvas.getContext("2d");\n    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);\n    ctx.clearRect(0, 0, cssW, cssH);\n\n    const win = getPeriodWindow();\n    const t0 = win.t0, t1 = win.t1;\n    const span = Math.max(1e-3, t1 - t0);\n    let hist = getHistoryForPeriod();\n\n    // Deterministic fixed-time buckets (not index stride — stride remaps history every second)\n    const bSec = bucketSecForSpan(span);\n    hist = aggregateHistoryBuckets(hist, t0, t1, bSec);\n\n    const padL = 8, padR = 8, padT = 30, padB = 18;\n    const w = Math.max(1, cssW - padL - padR);\n    const h = Math.max(1, cssH - padT - padB);\n\n    let drawHist = hist;\n    // Continuous right edge: always pin head to t1 (now) with lerped rates so the\n    // chart scrolls smoothly every frame instead of jumping once per poll.\n    if (hist.length >= 1) {\n      const last = hist[hist.length - 1];\n      const tipUp = statsVis.primed ? statsVis.up : (last.upRate || 0);\n      const tipDown = statsVis.primed ? statsVis.down : (last.downRate || 0);\n      if (t1 > (last.t || 0) + 0.001) {\n        drawHist = hist.concat([{ t: t1, upRate: tipUp, downRate: tipDown }]);\n      } else if (drawHist.length) {\n        // same second as last sample — still show lerped tip on last point for soft peaks\n        const copy = drawHist.slice();\n        const L = copy[copy.length - 1];\n        copy[copy.length - 1] = {\n          t: L.t,\n          upRate: tipUp,\n          downRate: tipDown,\n        };\n        drawHist = copy;\n      }\n    }\n\n    // sort + dedupe by time (broken order / duplicate t → bezier loops)\n    if (drawHist.length > 1) {\n      drawHist = drawHist.slice().sort((a, b) => (a.t || 0) - (b.t || 0));\n      const cleaned = [drawHist[0]];\n      for (let i = 1; i < drawHist.length; i++) {\n        const prev = cleaned[cleaned.length - 1];\n        const cur = drawHist[i];\n        if ((cur.t || 0) - (prev.t || 0) < 0.05) {\n          cleaned[cleaned.length - 1] = cur; // keep newest at same stamp\n        } else {\n          cleaned.push(cur);\n        }\n      }\n      drawHist = cleaned;\n    }\n    statsChartLayout = { padL, padR, padT, padB, w, h, cssW, cssH, hist: drawHist, t0, t1, span };\n\n    if (drawHist.length < 2) {\n      ctx.fillStyle = "rgba(139,147,167,.55)";\n      ctx.font = "12px Inter, sans-serif";\n      ctx.fillText(drawHist.length ? "Нужно ещё точек…" : "Сбор точек…", padL, cssH / 2);\n      ctx.fillStyle = "rgba(139,147,167,.4)";\n      ctx.font = "10px Inter, sans-serif";\n      ctx.fillText(formatWindowLabel(t0, t1), padL, cssH - 4);\n      return;\n    }\n\n    const vals = [];\n    drawHist.forEach(p => {\n      if (statsLines.down) vals.push(p.downRate || 0);\n      if (statsLines.up) vals.push(p.upRate || 0);\n    });\n    if (!vals.length) {\n      ctx.fillStyle = "rgba(139,147,167,.55)";\n      ctx.font = "12px Inter, sans-serif";\n      ctx.fillText("Включите download или upload", padL, cssH / 2);\n      return;\n    }\n\n    const targetMax = Math.max(1, ...vals);\n    if (statsReduceMotion) {\n      statsSmoothMax = targetMax;\n    } else {\n      const rise = targetMax > statsSmoothMax;\n      // slower rise/fall → less vertical "pop" when a new sample arrives\n      const k = rise ? (1 - Math.exp(-dt * 6)) : (1 - Math.exp(-dt * 2.2));\n      statsSmoothMax += (targetMax - statsSmoothMax) * k;\n      if (statsSmoothMax < 1) statsSmoothMax = 1;\n    }\n    const maxV = statsSmoothMax;\n\n    ctx.strokeStyle = "rgba(255,255,255,.045)";\n    ctx.lineWidth = 1;\n    for (let g = 0; g < 4; g++) {\n      const y = padT + (h * g) / 3;\n      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + w, y); ctx.stroke();\n    }\n\n    function xAt(t) {\n      return padL + ((t - t0) / span) * w;\n    }\n    function yAt(v) {\n      return padT + h - (Math.min(maxV, Math.max(0, v || 0)) / maxV) * h;\n    }\n\n    function series(key, color, fillAlpha) {\n      const pts = [];\n      for (let i = 0; i < drawHist.length; i++) {\n        const x = xAt(drawHist[i].t);\n        const y = yAt(drawHist[i][key]);\n        // drop non-monotonic X (would loop)\n        if (pts.length && x <= pts[pts.length - 1].x + 0.01) {\n          pts[pts.length - 1] = { x: Math.max(x, pts[pts.length - 1].x + 0.01), y };\n          continue;\n        }\n        pts.push({ x, y });\n      }\n      if (pts.length < 2) return;\n\n      // X-monotonic cubic: control points always between p1.x and p2.x\n      // (Catmull-Rom freely moves X backward → visible loops)\n      function strokeSmooth(startFromBaseline) {\n        if (startFromBaseline) {\n          ctx.moveTo(pts[0].x, padT + h);\n          ctx.lineTo(pts[0].x, pts[0].y);\n        } else {\n          ctx.moveTo(pts[0].x, pts[0].y);\n        }\n        for (let i = 0; i < pts.length - 1; i++) {\n          const p1 = pts[i];\n          const p2 = pts[i + 1];\n          const dx = Math.max(0, p2.x - p1.x) / 3;\n          ctx.bezierCurveTo(p1.x + dx, p1.y, p2.x - dx, p2.y, p2.x, p2.y);\n        }\n      }\n\n      if (fillAlpha > 0) {\n        ctx.beginPath();\n        strokeSmooth(true);\n        const last = pts[pts.length - 1];\n        ctx.lineTo(last.x, padT + h);\n        ctx.closePath();\n        const rgba = hexToRgba(color, fillAlpha);\n        const rgba0 = hexToRgba(color, 0);\n        const g2 = ctx.createLinearGradient(0, padT, 0, padT + h);\n        g2.addColorStop(0, rgba);\n        g2.addColorStop(1, rgba0);\n        ctx.fillStyle = g2;\n        ctx.fill();\n      }\n\n      ctx.beginPath();\n      strokeSmooth(false);\n      ctx.strokeStyle = color;\n      ctx.lineWidth = 2.25;\n      ctx.lineJoin = "round";\n      ctx.lineCap = "round";\n      ctx.stroke();\n      ctx.save();\n      ctx.globalAlpha = 0.2;\n      ctx.lineWidth = 5;\n      ctx.stroke();\n      ctx.restore();\n    }\n\n    if (statsLines.down) series("downRate", "#38bdf8", 0.18);\n    if (statsLines.up) series("upRate", "#a78bfa", 0.14);\n\n    if (statsHoverIdx >= 0 && statsHoverIdx < drawHist.length) {\n      const p = drawHist[statsHoverIdx];\n      const x = xAt(p.t);\n      ctx.strokeStyle = "rgba(255,255,255,.22)";\n      ctx.lineWidth = 1;\n      ctx.beginPath();\n      ctx.moveTo(x, padT);\n      ctx.lineTo(x, padT + h);\n      ctx.stroke();\n      function dot(key, color) {\n        ctx.beginPath();\n        ctx.fillStyle = color;\n        ctx.arc(x, yAt(p[key]), 3.5, 0, Math.PI * 2);\n        ctx.fill();\n        ctx.beginPath();\n        ctx.fillStyle = "rgba(255,255,255,.85)";\n        ctx.arc(x, yAt(p[key]), 1.4, 0, Math.PI * 2);\n        ctx.fill();\n      }\n      if (statsLines.down) dot("downRate", "#38bdf8");\n      if (statsLines.up) dot("upRate", "#a78bfa");\n    }\n\n    ctx.fillStyle = "rgba(139,147,167,.55)";\n    ctx.font = "10px Inter, sans-serif";\n    ctx.textAlign = "left";\n    ctx.fillText(formatTick(t0), padL, cssH - 4);\n    ctx.textAlign = "right";\n    ctx.fillText(formatTick(t1) + " · " + formatSpan(span), padL + w, cssH - 4);\n    ctx.textAlign = "left";\n  }\n  function hexToRgba(hex, a){\n    const h = (hex || "").replace("#", "");\n    if (h.length !== 6) return "rgba(255,255,255," + a + ")";\n    const r = parseInt(h.slice(0, 2), 16);\n    const g = parseInt(h.slice(2, 4), 16);\n    const b = parseInt(h.slice(4, 6), 16);\n    return "rgba(" + r + "," + g + "," + b + "," + a + ")";\n  }\n  function formatTick(ts){\n    try {\n      return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });\n    } catch (e) { return ""; }\n  }\n  function formatSpan(sec){\n    if (sec < 120) return Math.round(sec) + "с";\n    if (sec < 3600) return Math.round(sec / 60) + "м";\n    return (Math.round(sec / 360) / 10) + "ч";\n  }\n  function formatWindowLabel(t0, t1){\n    return formatTick(t0) + " → " + formatTick(t1);\n  }\nfunction hideStatsTooltip(){\n    statsHoverIdx = -1;\n    const tip = document.getElementById("statsTooltip");\n    if (tip) tip.hidden = true;\n  }\n  function showStatsTooltip(clientX, clientY){\n    const canvas = document.getElementById("statsChart");\n    const tip = document.getElementById("statsTooltip");\n    const layout = statsChartLayout;\n    if (!canvas || !tip || !layout || !layout.hist || layout.hist.length < 2) {\n      hideStatsTooltip();\n      return;\n    }\n    const rect = canvas.getBoundingClientRect();\n    const x = clientX - rect.left;\n    const { padL, w, hist, t0, span } = layout;\n    const tAt = (t0 || 0) + (Math.max(0, Math.min(1, (x - padL) / Math.max(1, w)))) * (span || 1);\n    let idx = 0, best = Infinity;\n    for (let i = 0; i < hist.length; i++) {\n      const d = Math.abs((hist[i].t || 0) - tAt);\n      if (d < best) { best = d; idx = i; }\n    }\n    statsHoverIdx = idx;\n    const p = hist[idx];\n    const t = new Date((p.t || 0) * 1000);\n    let rows = `<div class="tt-time">${escHtml(t.toLocaleTimeString())}</div>`;\n    if (statsLines.down) {\n      rows += `<div class="tt-row"><span class="tt-dot dn"></span>↓ ${escHtml(fmtRate(p.downRate || 0))}</div>`;\n    }\n    if (statsLines.up) {\n      rows += `<div class="tt-row"><span class="tt-dot up"></span>↑ ${escHtml(fmtRate(p.upRate || 0))}</div>`;\n    }\n    if (!statsLines.down && !statsLines.up) { hideStatsTooltip(); return; }\n    tip.innerHTML = rows;\n    tip.hidden = false;\n    const body = canvas.parentElement;\n    const br = body.getBoundingClientRect();\n    let left = clientX - br.left;\n    let top = clientY - br.top;\n    const tw = tip.offsetWidth || 140;\n    left = Math.max(tw / 2 + 4, Math.min(br.width - tw / 2 - 4, left));\n    top = Math.max(12, top);\n    tip.style.left = left + "px";\n    tip.style.top = top + "px";\n    drawStatsChart();\n  }\n  function bindStatsChartUi(){\n    const canvas = document.getElementById("statsChart");\n    if (canvas && !canvas._statsBound) {\n      canvas._statsBound = true;\n      canvas.addEventListener("mousemove", (e) => showStatsTooltip(e.clientX, e.clientY));\n      canvas.addEventListener("mouseleave", () => { hideStatsTooltip(); drawStatsChart(); });\n    }\n    const periodWrap = document.getElementById("statsPeriod");\n    if (periodWrap && !periodWrap._statsBound) {\n      periodWrap._statsBound = true;\n      periodWrap.addEventListener("click", (e) => {\n        const b = e.target && e.target.closest ? e.target.closest(".stab[data-period]") : null;\n        if (!b || !periodWrap.contains(b)) return;\n        e.preventDefault();\n        const next = b.getAttribute("data-period") || "live";\n        if (next === statsPeriod) { drawStatsChart(); return; }\n        statsPeriod = next;\n        saveStatsPrefs();\n        applyStatsPrefsToUi();\n        hideStatsTooltip();\n        drawStatsChart();\n      });\n    }\n    const ld = document.getElementById("lineDown");\n    const lu = document.getElementById("lineUp");\n    function onLine(){\n      statsLines.down = !!(ld && ld.checked);\n      statsLines.up = !!(lu && lu.checked);\n      if (!statsLines.down && !statsLines.up) {\n        statsLines.down = true;\n        if (ld) ld.checked = true;\n      }\n      saveStatsPrefs();\n      hideStatsTooltip();\n      drawStatsChart();\n    }\n    if (ld && !ld._statsBound) { ld._statsBound = true; ld.addEventListener("change", onLine); }\n    if (lu && !lu._statsBound) { lu._statsBound = true; lu.addEventListener("change", onLine); }\n  }\n\n  document.getElementById("viewRules")?.addEventListener("click", () => setView("rules"));\n  document.getElementById("viewStats")?.addEventListener("click", () => setView("stats"));\n  document.getElementById("viewConns")?.addEventListener("click", () => setView("conns"));\n  document.getElementById("viewLogs")?.addEventListener("click", () => setView("logs"));\n  // delegation fallback (if ids missing after hot patch)\n  document.getElementById("viewSwitch")?.addEventListener("click", (e) => {\n    const b = e.target.closest("[data-view]");\n    if (!b || !b.closest("#viewSwitch")) return;\n    const v = b.getAttribute("data-view");\n    if (v) setView(v);\n  });\n  window.addEventListener("resize", () => {\n    moveViewPill();\n    moveStatsPeriodPill();\n    if (currentView === "stats") drawStatsChart();\n  });\n\n  // restore view: ?view=stats wins, else localStorage\n  (function initView(){\n    let initial = "rules";\n    try {\n      const saved = localStorage.getItem(VIEW_KEY);\n      if (saved === "stats" || saved === "rules" || saved === "conns" || saved === "logs") initial = saved;\n    } catch (e) {}\n    // drop legacy ?view= from address bar (state is localStorage only)\n    try {\n      const url = new URL(location.href);\n      if (url.searchParams.has("view")) {\n        url.searchParams.delete("view");\n        const q = url.searchParams.toString();\n        history.replaceState(null, "", url.pathname + (q ? "?" + q : "") + url.hash);\n      }\n    } catch (e) {}\n    setView(initial, { persist: false });\n  if (currentView === "rules") reviveRulesCards();\n\n    requestAnimationFrame(() => moveViewPill());\n  })();\n\n  searchInput.addEventListener("input", () => applyListFilter());\n\n  document.getElementById("conflictFilter")?.addEventListener("click", (e) => {\n    e.preventDefault();\n    e.stopPropagation();\n    conflictFilterOn = !conflictFilterOn;\n    updateConflictFilterLink();\n    applyListFilter();\n    // drop browser focus ring immediately after toggle\n    try { e.currentTarget.blur(); } catch (_) {}\n  });\n\n  let tmr;\n  let importPreviewItems = [];\n\n  // static type pills (same palette as main board)\n  const IMPORT_TYPE_LAB = { ipv4: "IPv4", ipv6: "IPv6", namespace: "Namespace", domain: "Domain", keyword: "Keyword" };\n  const IMPORT_TYPE_CLS = { ipv4: "badge-v4", ipv6: "badge-v6", namespace: "badge-ns", domain: "badge-dom", keyword: "badge-kw" };\n\n  function collectExistingRules(){\n    const out = [];\n    document.querySelectorAll(".gcard").forEach(card => {\n      const gid = card.dataset.gid || "";\n      const gname = (card.querySelector(".gcard-name")?.value || "").trim() || gid;\n      card.querySelectorAll(".rule-row:not(.draft)").forEach(row => {\n        const val = (row.querySelector("[name=value]")?.value || "").trim();\n        if (!val) return;\n        out.push({\n          value: normalizePattern(val),\n          raw: val,\n          gid,\n          gname,\n        });\n      });\n    });\n    return out;\n  }\n\n  function classifyImportItem(it, targetGid, existing){\n    const key = normalizePattern(it.value);\n    if (!key) return { status: "new", other: null };\n    const matches = existing.filter(e => e.value === key);\n    const inTarget = matches.find(e => e.gid === targetGid);\n    if (inTarget) return { status: "dup", other: inTarget };\n    const other = matches.find(e => e.gid !== targetGid);\n    if (other) return { status: "conflict", other };\n    return { status: "new", other: null };\n  }\n\n  function renderImportPreview(){\n    if (!previewBox || !previewStats) return;\n    const items = importPreviewItems;\n    const optWrap = document.getElementById("importOptWrap");\n    const skipCb = document.getElementById("importSkipDupConflict");\n    const submitBtn = document.getElementById("importSubmitBtn");\n    const skipDupConf = !!(skipCb && skipCb.checked);\n    if (!items.length) {\n      previewBox.hidden = true;\n      previewStats.innerHTML = "";\n      if (optWrap) optWrap.hidden = true;\n      if (submitBtn) submitBtn.textContent = "Импортировать";\n      return;\n    }\n    const targetGid = importGid?.value || "";\n    const existing = collectExistingRules();\n    const counts = {};\n    let nNew = 0, nDup = 0, nConf = 0;\n    const classified = items.map(it => {\n      counts[it.type] = (counts[it.type] || 0) + 1;\n      const c = classifyImportItem(it, targetGid, existing);\n      if (c.status === "dup") nDup++;\n      else if (c.status === "conflict") nConf++;\n      else nNew++;\n      return { it, ...c };\n    });\n\n    if (optWrap) optWrap.hidden = false;\n\n    const typePills = Object.keys(counts).map(k => {\n      const lab = IMPORT_TYPE_LAB[k] || k;\n      const cls = IMPORT_TYPE_CLS[k] || "badge-ns";\n      return `<span class="pill ${cls}">${lab} ${counts[k]}</span>`;\n    }).join("");\n    const total = items.length;\n    previewStats.innerHTML =\n      `<div class="preview-stats-types">${typePills}<span class="preview-stats-summary">· <strong>${total}</strong> правил будет добавлено</span></div>` +\n      `<div class="preview-stats-summary"><strong>${nNew}</strong> новых · <strong>${nDup}</strong> дубля · <strong>${nConf}</strong> конфликт${nConf === 1 ? "" : "ов"}</div>`;\n\n    if (submitBtn) {\n      submitBtn.textContent = skipDupConf ? (`Импортировать (${nNew})`) : "Импортировать";\n    }\n\n    const maxShow = 100;\n    previewBox.hidden = false;\n    previewBox.innerHTML = classified.slice(0, maxShow).map((row, i) => {\n      const it = row.it;\n      const typePill = `<span class="pill ${IMPORT_TYPE_CLS[it.type] || "badge-ns"}">${IMPORT_TYPE_LAB[it.type] || it.type || ""}</span>`;\n      let statusHtml = "";\n      let rowCls = "preview-row";\n      if (row.status === "dup") {\n        statusHtml = `<span class="pill pill-status pill-dup" title="Уже есть в этой группе">уже есть</span>`;\n        rowCls += " is-dup";\n      } else if (row.status === "conflict") {\n        const on = row.other?.gname || "другая группа";\n        statusHtml = `<span class="pill pill-status pill-conflict" title="Совпадает с правилом в другой группе">конфликт: ${escHtml(on)}</span>`;\n        rowCls += " is-conflict";\n      }\n      if (skipDupConf && (row.status === "dup" || row.status === "conflict")) {\n        rowCls += " will-skip";\n        statusHtml += `<span class="preview-skip-note">— будет пропущено</span>`;\n      }\n      return `<div class="${rowCls}" style="--pi:${Math.min(i, 12)};animation-delay:${Math.min(i * 12, 200)}ms">\n        <span class="preview-val" title="${escHtml(it.value)}">${escHtml(it.value)}</span>\n        ${typePill}\n        ${statusHtml}\n      </div>`;\n    }).join("") + (items.length > maxShow\n      ? `<div class="preview-row" style="opacity:1;animation:none"><span class="meta">… и ещё ${items.length - maxShow}</span></div>`\n      : "");\n  }\n\n  async function refreshPreview(){\n    const text = importText.value || "";\n    if (!text.trim()) {\n      importPreviewItems = [];\n      previewBox.hidden = true;\n      previewStats.innerHTML = "";\n      return;\n    }\n    const mode = document.getElementById("detectMode").value;\n    const fd = new URLSearchParams();\n    fd.set("text", text);\n    try {\n      const res = await fetch("/api/detect", {\n        method: "POST",\n        headers: { "Content-Type": "application/x-www-form-urlencoded" },\n        body: fd.toString(),\n        credentials: "same-origin",\n      });\n      const data = await res.json();\n      let items = (data.items || []).map(it => ({\n        type: it.type === "raw" ? "namespace" : (it.type || "namespace"),\n        value: it.value,\n        rule: it.rule,\n      }));\n      if (mode === "ipv4") items = items.filter(i => i.type === "ipv4" || i.type === "ipv6");\n      if (mode === "namespace") items = items.filter(i => i.type === "namespace" || i.type === "domain" || i.type === "keyword");\n      importPreviewItems = items;\n      renderImportPreview();\n    } catch (e) {}\n  }\n\n  if (importText) {\n    importText.addEventListener("input", () => { clearTimeout(tmr); tmr = setTimeout(refreshPreview, 250); });\n    document.getElementById("detectMode")?.addEventListener("change", refreshPreview);\n  }\n  document.getElementById("importSkipDupConflict")?.addEventListener("change", () => {\n    renderImportPreview();\n  });\n  document.getElementById("importForm")?.addEventListener("submit", (e) => {\n    const skipCb = document.getElementById("importSkipDupConflict");\n    if (!skipCb || !skipCb.checked) return;\n    const targetGid = importGid?.value || "";\n    const existing = collectExistingRules();\n    const news = [];\n    for (const it of importPreviewItems) {\n      const c = classifyImportItem(it, targetGid, existing);\n      if (c.status === "new") news.push(it);\n    }\n    if (!news.length) {\n      e.preventDefault();\n      if (typeof toast === "function") toast("Нечего импортировать: все строки — дубли или конфликты", false);\n      return;\n    }\n    if (importText) importText.value = news.map(it => it.value).join("\\n");\n  });\n\n  // initial cross-group conflict markers (after DOM ready)\n  scheduleConflictScan();\n\n  // ── Settings ──\n  const settingsBd = document.getElementById("settingsBd");\n  const btnSettings = document.getElementById("btnSettings");\n  const xttpDot = document.getElementById("xttpDot");\n  let usersMe = "";\n\n  function resetXttpHealthUi(){\n    const pill = document.getElementById("xttpStatusPill");\n    if (pill) {\n      pill.className = "status-pill unk";\n      pill.textContent = "Не проверено";\n    }\n    const mTcp = document.getElementById("mTcp");\n    const mHttp = document.getElementById("mHttp");\n    const mSpeed = document.getElementById("mSpeed");\n    function setEmpty(el){\n      if (!el) return;\n      el.textContent = "не измерено";\n      el.classList.add("empty-metric");\n    }\n    setEmpty(mTcp); setEmpty(mHttp); setEmpty(mSpeed);\n  }\n  function setMetric(el, text, empty){\n    if (!el) return;\n    el.textContent = text;\n    el.classList.toggle("empty-metric", !!empty);\n  }\n  function openSettings(){\n    // pause live stats canvas while modal is open (compositor/RGB fringe fix)\n    if (typeof stopStatsPolling === "function") stopStatsPolling();\n    try { const c = document.getElementById("statsChart"); if (c) c.style.visibility = "hidden"; } catch (e) {}\n    settingsBd.classList.add("open");\n    settingsBd.setAttribute("aria-hidden","false");\n    try { document.body.classList.add("settings-open"); } catch (_) {}\n    resetXttpHealthUi();\n    const placeSettingsPill = (instant) => {\n      moveStabPill(document.querySelector(".settings-nav .stab[data-stab].active"), instant);\n    };\n    // layout may not be final until modal is painted + fade finishes (~300ms)\n    requestAnimationFrame(() => {\n      placeSettingsPill(true);\n      requestAnimationFrame(() => placeSettingsPill(true));\n    });\n    setTimeout(() => placeSettingsPill(true), 50);\n    setTimeout(() => placeSettingsPill(false), 320);\n    loadUsers();\n    loadXttpStatus();\n    loadBackups();\n    loadOps();\n    if (typeof loadCoreSettings === "function") loadCoreSettings();\n    try { if (typeof setupCoreVersionChips === "function") setupCoreVersionChips(); } catch(_) {}\n    try { if (typeof loadCoreVersions === "function") loadCoreVersions(false); } catch(_) {}\n    if (typeof setupCoreTips === "function") setupCoreTips();\n    // auto «Проверить связь» when opening settings (quiet — no toast spam)\n    if (typeof runXttpCheck === "function") runXttpCheck({ quiet: true });\n  }\n  function closeSettings(){\n    settingsBd.classList.remove("open");\n    settingsBd.setAttribute("aria-hidden","true");\n    try { document.body.classList.remove("settings-open"); } catch (_) {}\n    try { const c = document.getElementById("statsChart"); if (c) c.style.visibility = ""; } catch (e) {}\n    // resume live stats if still on Statistics tab\n    if (typeof currentView !== "undefined" && currentView === "stats" && typeof startStatsPolling === "function") {\n      startStatsPolling();\n    }\n  }\n  btnSettings?.addEventListener("click", openSettings);\n  document.getElementById("settingsClose")?.addEventListener("click", closeSettings);\n  settingsBd?.addEventListener("click", e => { if (e.target === settingsBd) closeSettings(); });\n\n  function moveStabPill(btn, instant){\n    // only the Settings tab pill ? never the main Rules/Stats view-switch\n    const pill = document.getElementById("stabPill");\n    const wrap = document.querySelector(".settings-nav .stabs");\n    if (!btn || !btn.dataset || !btn.dataset.stab) {\n      btn = wrap?.querySelector(".stab[data-stab].active") || wrap?.querySelector(".stab[data-stab]");\n    }\n    if (!pill || !wrap || !btn) return;\n    const wr = wrap.getBoundingClientRect();\n    const br = btn.getBoundingClientRect();\n    // wrap has padding:4px ? pill is left:4px, so offset = btn.left - wrap.left - 4\n    const x = br.left - wr.left - 4;\n    if (instant) {\n      const prev = pill.style.transition;\n      pill.style.transition = "none";\n      pill.style.width = br.width + "px";\n      pill.style.transform = "translateX(" + x + "px)";\n      void pill.offsetWidth;\n      pill.style.transition = prev || "";\n    } else {\n      pill.style.width = br.width + "px";\n      pill.style.transform = "translateX(" + x + "px)";\n    }\n  }\n  // settings tabs only (data-stab) ? do NOT touch main view-switch .stab[data-view]\n  document.querySelectorAll(".stab[data-stab]").forEach(b => b.addEventListener("click", () => {\n    document.querySelectorAll(".stab[data-stab]").forEach(x => x.classList.remove("active"));\n    document.querySelectorAll(".spanel").forEach(x => x.classList.remove("active"));\n    b.classList.add("active");\n    document.getElementById("spanel-" + b.dataset.stab)?.classList.add("active");\n    moveStabPill(b);\n    if (b.dataset.stab === "core" && typeof loadCoreSettings === "function") {\n      loadCoreSettings();\n    }\n    if (b.dataset.stab === "xttp" && typeof runXttpCheck === "function") {\n      runXttpCheck({ quiet: true });\n    }\n    if (b.dataset.stab === "ops" && typeof loadActivity === "function") {\n      loadActivity({ soft: true });\n      if (typeof setupActivityPoll === "function") setupActivityPoll(true);\n      if (typeof updatePingAgeUi === "function") updatePingAgeUi();\n    }\n  }));\n  // init pill under active tab\n  requestAnimationFrame(() => moveStabPill(document.querySelector(".stab.active")));\n  window.addEventListener("resize", () => {\n    if (settingsBd?.classList.contains("open")) {\n      moveStabPill(document.querySelector(".settings-nav .stab[data-stab].active"), true);\n    }\n  });\n\n  async function loadUsers(){\n    const body = document.getElementById("usersBody");\n    if (body) {\n      body.innerHTML = `<tr><td colspan="4">\n        <div class="skel-card"><div class="skel w40"></div><div class="skel w70"></div><div class="skel w90"></div></div>\n      </td></tr>`;\n    }\n    try {\n      const res = await fetch("/api/users", {credentials:"same-origin"});\n      const d = await res.json();\n      usersMe = d.me || "";\n      if (!body) return;\n      body.innerHTML = (d.users || []).map((u, i) => {\n        const on = u.enabled !== false;\n        const self = u.username === usersMe;\n        return `<tr style="--ri:${Math.min(i,10)}">\n          <td><strong>${escHtml(u.username)}</strong>${self ? \' <span class="meta">(вы)</span>\' : \'\'}</td>\n          <td>${on ? \'<span class="badge-on">ON</span>\' : \'<span class="badge-off">OFF</span>\'}</td>\n          <td class="meta">${escHtml(u.last_login || "—")}</td>\n          <td style="white-space:nowrap">\n            <button type="button" class="mini-btn icon-neutral" data-upw="${escHtml(u.id)}" title="Сменить пароль" aria-label="Сменить пароль">\n              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 17v.01"/><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>\n            </button>\n            <button type="button" class="mini-btn icon-neutral" data-utog="${escHtml(u.id)}" title="${on?"Заблокировать":"Разблокировать"}" aria-label="${on?"Заблокировать":"Разблокировать"}">\n              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${on\n                ? \'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0"/>\'\n                : \'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-1"/>\'}</svg>\n            </button>\n            <button type="button" class="mini-btn icon-danger-only" data-udel="${escHtml(u.id)}" title="Удалить пользователя" aria-label="Удалить" ${self?"disabled":""}>\n              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>\n            </button>\n          </td>\n        </tr>`;\n      }).join("") || \'<tr><td colspan="4" class="meta">Нет пользователей</td></tr>\';\n    } catch(e) { toast(String(e), false); }\n  }\n  function escHtml(s){\n    return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");\n  }\n\n  document.getElementById("btnUserAdd")?.addEventListener("click", () => {\n    document.getElementById("userAddForm").style.display = "block";\n  });\n  document.getElementById("userAddCancel")?.addEventListener("click", () => {\n    document.getElementById("userAddForm").style.display = "none";\n  });\n  document.getElementById("userAddForm")?.addEventListener("submit", async (e) => {\n    e.preventDefault();\n    const fd = new FormData(e.target);\n    const d = await api("/api/users/add", {\n      username: fd.get("username"),\n      password: fd.get("password")\n    });\n    if (!d.ok) return toast(d.error || "Ошибка", false);\n    toast("Пользователь создан", true);\n    e.target.reset();\n    document.getElementById("userAddForm").style.display = "none";\n    loadUsers();\n  });\n  document.getElementById("usersBody")?.addEventListener("click", async (e) => {\n    const t = e.target.closest("button");\n    if (!t) return;\n    if (t.dataset.upw) {\n      const pw = prompt("Новый пароль (мин. 6):");\n      if (!pw) return;\n      const d = await api("/api/users/password", {id: t.dataset.upw, password: pw});\n      toast(d.ok ? "Пароль обновлён" : (d.error || "Ошибка"), !!d.ok);\n    }\n    if (t.dataset.utog) {\n      const d = await api("/api/users/toggle", {id: t.dataset.utog});\n      if (!d.ok) return toast(d.error || "Ошибка", false);\n      loadUsers();\n    }\n    if (t.dataset.udel) {\n      if (!confirm("Удалить пользователя?")) return;\n      const d = await api("/api/users/delete", {id: t.dataset.udel});\n      if (!d.ok) return toast(d.error || "Ошибка", false);\n      toast("Удалён", true);\n      loadUsers();\n    }\n  });\n\n  function renderXttpKv(st){\n    const o = st.outbound || {};\n    const rows = [\n      ["Endpoint", o.address ? `${o.address}:${o.port}` : "—"],\n      ["Протокол", o.ok ? `VLESS · ${o.security || "?"} · ${o.network || "?"}` : "—"],\n      ["UUID", o.uuid_short || "—"],\n      ["SNI", o.sni || "—"],\n      ["Public key", o.public_key_short || "—"],\n      ["shortId", o.short_id || "—"],\n      ["Path / mode", `${o.path || "—"} / ${o.mode || "—"}`],\n      ["Local socks", st.socks || "—"],\n      ["mihomo", st.mihomo_proxy || "xttp"],\n    ];\n    document.getElementById("xttpKv").innerHTML = rows.map(([k,v]) =>\n      `<dt>${escHtml(k)}</dt><dd>${escHtml(v)}</dd>`).join("");\n    document.getElementById("xttpServices").textContent =\n      `xray: ${st.xray_active ? "active" : "down"} · mihomo: ${st.mihomo_active ? "active" : "down"}`;\n  }\n\n  async function loadXttpStatus(){\n    try {\n      const res = await fetch("/api/xttp/status", {credentials:"same-origin"});\n      const st = await res.json();\n      renderXttpKv(st);\n    } catch(e) {}\n  }\n  async function loadBackups(){\n    try {\n      const res = await fetch("/api/xttp/backups", {credentials:"same-origin"});\n      const d = await res.json();\n      const el = document.getElementById("backupList");\n      const list = d.backups || [];\n      if (!list.length) { el.textContent = "пока нет"; return; }\n      el.innerHTML = list.map(n =>\n        `<div style="display:flex;align-items:center;gap:8px;margin:4px 0">\n          <span class="mono">${escHtml(n)}</span>\n          <button type="button" class="btn btn-ghost" data-rb="${escHtml(n)}" style="padding:4px 10px;font-size:12px">Откатить</button>\n        </div>`).join("");\n    } catch(e) {}\n  }\n  document.getElementById("backupList")?.addEventListener("click", async (e) => {\n    const b = e.target.closest("[data-rb]");\n    if (!b) return;\n    if (!confirm("Откатить xray на " + b.dataset.rb + "?")) return;\n    const d = await api("/api/xttp/rollback", {name: b.dataset.rb});\n    if (!d.ok) return toast(d.error || "Ошибка", false);\n    toast("Откат выполнен", true);\n    loadXttpStatus();\n    refreshXttpDot();\n  });\n\n  document.getElementById("btnXttpRefresh")?.addEventListener("click", () => { loadXttpStatus(); loadBackups(); });\n  document.getElementById("btnXttpPing")?.addEventListener("click", () => runXttpCheck({ quiet: false }));\n  document.getElementById("btnXttpSpeed")?.addEventListener("click", async () => {\n    setMetric(document.getElementById("mSpeed"), "…", true);\n    toast("Speedtest…", true);\n    const d = await api("/api/xttp/speedtest", {});\n    if (!d.ok) {\n      setMetric(document.getElementById("mSpeed"), "FAIL", true);\n      return toast(d.error || "Speedtest fail", false);\n    }\n    setMetric(document.getElementById("mSpeed"), d.mbps + " Mbps", false);\n    toast("Speed: " + d.mbps + " Mbps", true);\n  });\n  document.getElementById("btnVlessPreview")?.addEventListener("click", async () => {\n    const d = await api("/api/xttp/preview", {vless: document.getElementById("vlessIn").value});\n    const el = document.getElementById("vlessPreview");\n    if (!d.ok) { el.textContent = d.error || "ошибка"; return; }\n    const p = d.preview || {};\n    el.textContent = `${p.address}:${p.port} · ${p.network}/${p.security} · SNI ${p.sni || "—"} · ${p.uuid_short}`;\n  });\n  document.getElementById("btnVlessApply")?.addEventListener("click", async () => {\n    if (!confirm("Применить новый vless:// ? Текущий xray уйдёт в backup.")) return;\n    const d = await api("/api/xttp/apply", {vless: document.getElementById("vlessIn").value});\n    if (!d.ok) return toast(d.error || "Ошибка apply", false);\n    toast("Применено · backup " + (d.backup || "") + (d.http_ok ? " · HTTP OK" : " · HTTP fail"), !!d.http_ok);\n    loadXttpStatus();\n    loadBackups();\n    refreshXttpDot();\n  });\n\n  const svcDot = document.getElementById("svcDot");\n  let autoPingTimer = null;\n  let lastAutoPingAt = 0;\n  let xttpCheckInFlight = null;\n\n  function setXttpDot(up){\n    if (!xttpDot) return;\n    xttpDot.classList.add("chip-status");\n    xttpDot.classList.remove("up","down");\n    xttpDot.classList.add(up ? "up" : "down");\n    xttpDot.textContent = up ? "xttp · UP" : "xttp · DOWN";\n  }\n\n  /** Full link check: metrics + pill + header chip. quiet=true skips toasts (auto). */\n  async function runXttpCheck({ quiet = false, soft = false } = {}) {\n    if (xttpCheckInFlight) return xttpCheckInFlight;\n    xttpCheckInFlight = (async () => {\n      const mTcp = document.getElementById("mTcp");\n      const mHttp = document.getElementById("mHttp");\n      const pill = document.getElementById("xttpStatusPill");\n      if (!soft) {\n        setMetric(mTcp, "…", true);\n        setMetric(mHttp, "…", true);\n        if (pill) {\n          pill.className = "status-pill unk";\n          pill.textContent = "Проверка…";\n        }\n      }\n      try {\n        const d = await api("/api/xttp/ping", {});\n        if (!d.ok && d.error) {\n          if (pill) {\n            pill.className = "status-pill down";\n            pill.textContent = "DOWN · ошибка";\n          }\n          if (xttpDot) {\n            xttpDot.textContent = "xttp · ?";\n            xttpDot.classList.remove("up", "down");\n          }\n          if (!quiet) toast(d.error, false);\n          return d;\n        }\n        setMetric(mTcp, d.tcp?.ok ? (d.tcp.latency_ms + " ms") : "FAIL", !d.tcp?.ok);\n        setMetric(mHttp, d.http?.ok ? (d.http.latency_ms + " ms") : "FAIL", !d.http?.ok);\n        if (pill) {\n          pill.className = "status-pill " + (d.up ? "up" : "down");\n          pill.textContent = d.up ? "UP · нода доступна" : "DOWN · нет связи";\n        }\n        setXttpDot(!!d.up);\n        if (d.services) setSvcDot(!!d.services.xray, !!d.services.mihomo);\n        else if (d.xray_active !== undefined) setSvcDot(!!d.xray_active, !!d.mihomo_active);\n        lastAutoPingAt = Date.now();\n        if (typeof updatePingAgeUi === "function") updatePingAgeUi();\n        if (!quiet) {\n          if (!d.up) toast(d.http?.error || d.tcp?.error || "Нет связи с нодой", false);\n          else toast("Связь OK", true);\n        }\n        return d;\n      } catch (e) {\n        if (xttpDot) {\n          xttpDot.textContent = "xttp · ?";\n          xttpDot.classList.remove("up", "down");\n        }\n        if (pill) {\n          pill.className = "status-pill down";\n          pill.textContent = "DOWN · ошибка";\n        }\n        if (!quiet) toast(String(e), false);\n        return { ok: false, error: String(e) };\n      } finally {\n        xttpCheckInFlight = null;\n      }\n    })();\n    return xttpCheckInFlight;\n  }\n  /** Status card value + colored dot (ok | down | stale). */\n  function setOpsStat(valId, dotId, text, state){\n    const el = document.getElementById(valId);\n    const dot = document.getElementById(dotId);\n    if (el) {\n      el.textContent = text;\n      el.className = "ops-stat-val" + (state === "stale" && (text === "…" || text === "нет данных") ? " empty-metric" : "");\n    }\n    if (dot) dot.className = "ops-stat-dot " + (state || "stale");\n  }\n  function formatRelRu(sec){\n    const s = Math.max(0, Math.round(sec));\n    if (s < 5) return "только что";\n    if (s < 60) return s + " сек назад";\n    const m = Math.floor(s / 60);\n    if (m < 60) return m + " мин назад";\n    const h = Math.floor(m / 60);\n    if (h < 48) return h + " ч назад";\n    const d = Math.floor(h / 24);\n    return d + " дн назад";\n  }\n  function updatePingAgeUi(){\n    const val = document.getElementById("svcPingAge");\n    if (!val) return;\n    if (!lastAutoPingAt) {\n      setOpsStat("svcPingAge", "svcPingDot", "нет данных", "stale");\n      return;\n    }\n    const sec = Math.round((Date.now() - lastAutoPingAt) / 1000);\n    const interval = Math.max(15, parseInt(document.getElementById("autoPingSec")?.value, 10) || 45);\n    const staleAfter = Math.max(interval * 2, 90);\n    const state = sec <= staleAfter ? "ok" : "stale";\n    setOpsStat("svcPingAge", "svcPingDot", formatRelRu(sec), state);\n  }\n  function updateJournalAgeLabel(){\n    const el = document.getElementById("journalAge");\n    if (!el) return;\n    if (!lastActivityFetchAt) { el.textContent = ""; return; }\n    const sec = Math.round((Date.now() - lastActivityFetchAt) / 1000);\n    if (sec < 3) el.textContent = "обновлено только что";\n    else if (sec < 60) el.textContent = "обновлено " + sec + " сек назад";\n    else el.textContent = "обновлено " + formatRelRu(sec).replace(/^только что$/, "только что");\n  }\n\n  function setSvcDot(xrayOn, mihomoOn){\n    if (svcDot) {\n      const ok = !!xrayOn && !!mihomoOn;\n      svcDot.classList.add("chip-status");\n      svcDot.classList.remove("up","down");\n      svcDot.classList.add(ok ? "up" : "down");\n      if (ok) svcDot.textContent = "svc · OK";\n      else {\n        const parts = [];\n        if (!xrayOn) parts.push("xray↓");\n        if (!mihomoOn) parts.push("mihomo↓");\n        svcDot.textContent = "svc · " + (parts.join(" ") || "DOWN");\n      }\n    }\n    setOpsStat("svcXray", "svcXrayDot", xrayOn ? "Active" : "Down", xrayOn ? "ok" : "down");\n    setOpsStat("svcMihomo", "svcMihomoDot", mihomoOn ? "Active" : "Down", mihomoOn ? "ok" : "down");\n  }\n  async function refreshServices(){\n    try {\n      const res = await fetch("/api/services/status", {credentials:"same-origin"});\n      const d = await res.json();\n      setSvcDot(!!d.xray, !!d.mihomo);\n    } catch(e) {\n      if (svcDot) { svcDot.textContent = "svc · ?"; svcDot.classList.remove("up","down"); }\n      setOpsStat("svcXray", "svcXrayDot", "…", "stale");\n      setOpsStat("svcMihomo", "svcMihomoDot", "…", "stale");\n    }\n  }\n  async function refreshXttpDot(){\n    // soft: update chip + metrics without flashing «…»\n    await runXttpCheck({ quiet: true, soft: true });\n  }\n\n  /* Activity: labels + semantic dot kind (constants, not per-row hardcode) */\n  const ACTIVITY_LABELS = {\n    apply: "apply rules",\n    restart: "restart",\n    vless_apply: "vless apply",\n    vless_rollback: "vless rollback",\n    group_add: "group add",\n    group_delete: "group delete",\n    group_rename: "group rename",\n    group_toggle: "group toggle",\n    user_add: "user add",\n    user_delete: "user delete",\n    user_toggle: "user toggle",\n    user_password: "user password",\n    prefs: "prefs",\n    update: "update",\n    fleet_check: "fleet check",\n    fleet_update: "fleet update",\n    fleet_skip: "fleet skip",\n    fleet_trigger: "fleet trigger",\n  };\n  /** action → kind-ok | kind-danger | kind-neutral */\n  const ACTIVITY_DOT_KIND = {\n    apply: "ok",\n    restart: "ok",\n    vless_apply: "ok",\n    vless_rollback: "ok",\n    update: "ok",\n    fleet_check: "ok",\n    fleet_update: "ok",\n    fleet_trigger: "neutral",\n    fleet_skip: "danger",\n    group_delete: "danger",\n    user_delete: "danger",\n    user_toggle: "danger",\n    group_add: "neutral",\n    group_rename: "neutral",\n    group_toggle: "neutral",\n    user_add: "neutral",\n    user_password: "neutral",\n    prefs: "neutral",\n  };\n  function activityDotKind(action, ok){\n    if (ok === false) return "danger";\n    return ACTIVITY_DOT_KIND[action] || "neutral";\n  }\n  function activityItemKey(it){\n    return [it.ts || "", it.action || "", it.user || "", it.detail || "", String(it.ok)].join("|");\n  }\n  let activityKnownKeys = [];\n  let lastActivityFetchAt = 0;\n  let activityPollTimer = null;\n\n  function buildActivityRowHtml(it, isNew){\n    const act = ACTIVITY_LABELS[it.action] || it.action || "?";\n    const kind = activityDotKind(it.action, it.ok);\n    const key = activityItemKey(it);\n    return `<div class="activity-row${isNew ? " is-new" : ""}${it.ok === false ? " fail" : ""}" data-act-key="${escHtml(key)}">\n      <span class="activity-dot kind-${kind}" aria-hidden="true"></span>\n      <div>\n        <div class="activity-top">\n          <span class="activity-act">${escHtml(act)}</span>\n          <span class="activity-user">${escHtml(it.user || "")}</span>\n          <span class="activity-ts">${escHtml(it.ts || "")}</span>\n        </div>\n        <div class="activity-detail">${escHtml(it.detail || "")}</div>\n      </div>\n    </div>`;\n  }\n  function renderActivity(items, opts){\n    const el = document.getElementById("activityList");\n    if (!el) return;\n    const soft = !!(opts && opts.soft);\n    if (!items || !items.length) {\n      el.innerHTML = \'<div class="activity-row"><div></div><div class="activity-detail">Пока пусто — действия появятся здесь</div></div>\';\n      activityKnownKeys = [];\n      return;\n    }\n    const keys = items.map(activityItemKey);\n    if (soft && activityKnownKeys.length) {\n      if (keys.join("\\n") === activityKnownKeys.join("\\n")) return; // unchanged\n      const firstOld = keys.indexOf(activityKnownKeys[0]);\n      const tailMatch = firstOld > 0 &&\n        keys.slice(firstOld).join("\\n") === activityKnownKeys.join("\\n");\n      if (tailMatch) {\n        const chunk = items.slice(0, firstOld).map(it => buildActivityRowHtml(it, true)).join("");\n        el.insertAdjacentHTML("afterbegin", chunk);\n        // drop is-new after anim so re-poll doesn\'t keep class forever\n        setTimeout(() => {\n          el.querySelectorAll(".activity-row.is-new").forEach(n => n.classList.remove("is-new"));\n        }, 250);\n        activityKnownKeys = keys;\n        return;\n      }\n    }\n    el.innerHTML = items.map(it => buildActivityRowHtml(it, false)).join("");\n    activityKnownKeys = keys;\n  }\n  async function loadActivity(opts){\n    const soft = !!(opts && opts.soft);\n    try {\n      const res = await fetch("/api/activity", {credentials:"same-origin"});\n      const d = await res.json();\n      renderActivity(d.items || [], { soft });\n      lastActivityFetchAt = Date.now();\n      updateJournalAgeLabel();\n    } catch(e) {\n      if (!soft) {\n        const el = document.getElementById("activityList");\n        if (el) el.textContent = "не удалось загрузить лог";\n      }\n    }\n  }\n  function setupActivityPoll(on){\n    if (activityPollTimer) { clearInterval(activityPollTimer); activityPollTimer = null; }\n    if (!on) return;\n    activityPollTimer = setInterval(() => {\n      loadActivity({ soft: true });\n      updateJournalAgeLabel();\n      updatePingAgeUi();\n    }, 8000);\n  }\n  async function loadPrefs(){\n    try {\n      const res = await fetch("/api/prefs", {credentials:"same-origin"});\n      const d = await res.json();\n      const p = d.prefs || {};\n      const t = document.getElementById("autoPingToggle");\n      const s = document.getElementById("autoPingSec");\n      if (t) t.checked = !!p.auto_ping;\n      if (s) s.value = p.auto_ping_sec || 45;\n      setupAutoPing(!!p.auto_ping, p.auto_ping_sec || 45);\n    } catch(e) {}\n  }\n  function setupAutoPing(on, sec){\n    if (autoPingTimer) { clearInterval(autoPingTimer); autoPingTimer = null; }\n    if (!on) return;\n    const ms = Math.max(15, Math.min(300, parseInt(sec, 10) || 45)) * 1000;\n    autoPingTimer = setInterval(() => { refreshXttpDot(); }, ms);\n  }\n  async function loadOps(){\n    await refreshServices();\n    await loadActivity({ soft: false });\n    await loadPrefs();\n    updatePingAgeUi();\n    setupActivityPoll(true);\n  }\n  document.getElementById("btnRefreshOps")?.addEventListener("click", () => loadOps());\n  document.getElementById("btnRefreshLog")?.addEventListener("click", () => loadActivity({ soft: false }));\n  document.getElementById("btnRestartXray")?.addEventListener("click", async () => {\n    if (!confirm("Restart xray?")) return;\n    const d = await api("/api/services/restart", {service: "xray"});\n    toast(d.ok ? "xray restarted" : (d.error || "fail"), !!d.ok);\n    loadOps();\n    refreshXttpDot();\n  });\n  document.getElementById("btnRestartMihomo")?.addEventListener("click", async () => {\n    if (!confirm("Restart mihomo?")) return;\n    const d = await api("/api/services/restart", {service: "mihomo"});\n    toast(d.ok ? "mihomo restarted" : (d.error || "fail"), !!d.ok);\n    loadOps();\n  });\n  document.getElementById("btnSavePrefs")?.addEventListener("click", async () => {\n    const t = document.getElementById("autoPingToggle");\n    const s = document.getElementById("autoPingSec");\n    const d = await api("/api/prefs/set", {\n      auto_ping: t && t.checked ? "1" : "0",\n      auto_ping_sec: s ? s.value : "45",\n    });\n    if (!d.ok) return toast(d.error || "fail", false);\n    toast("Сохранено", true);\n    setupAutoPing(!!(d.prefs && d.prefs.auto_ping), d.prefs && d.prefs.auto_ping_sec);\n    loadActivity({ soft: false });\n  });\n\n  // light status on load + auto check link (no toast)\n  setTimeout(() => {\n    refreshServices();\n    loadPrefs();\n    runXttpCheck({ quiet: true });\n    updatePingAgeUi();\n  }, 400);\n  setInterval(refreshServices, 30000);\n  // pause ambient mesh when tab hidden → free GPU for 60fps elsewhere\n  (function setupMotionBudget(){\n    const fx = document.querySelector(".bg-fx");\n    function sync(){\n      if (!fx) return;\n      fx.classList.toggle("is-paused", !!document.hidden);\n    }\n    document.addEventListener("visibilitychange", sync);\n    sync();\n  })();\n  // tick relative ages (ping + journal label) without refetch\n  setInterval(() => {\n    updatePingAgeUi();\n    updateJournalAgeLabel();\n  }, 5000);\n})();\n</script>\n</body>\n</html>\n'
LOGIN_HTML = '<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="utf-8"/>\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>\n<title>xttp panel — Вход</title>\n<link rel="preconnect" href="https://fonts.googleapis.com"/>\n<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>\n<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet"/>\n<style>\n:root{\n  --acc:#3b9eff;\n  --text:#eceef2;\n  --muted:#8b93a7;\n  --font:"Inter",ui-sans-serif,system-ui,sans-serif;\n  --ease:cubic-bezier(.22,1,.36,1);\n}\n*{box-sizing:border-box}\nhtml,body{height:100%;margin:0}\nbody{\n  font-family:var(--font);\n  color:var(--text);\n  overflow:hidden;\n  -webkit-font-smoothing:antialiased;\n  background:#06070b;\n}\n\n/* full-bleed ambient mesh — no spinning plate */\n.bg{\n  position:fixed;\n  inset:0;\n  z-index:0;\n  overflow:hidden;\n  background:\n    radial-gradient(120% 90% at 10% 0%, #0a1628 0%, transparent 55%),\n    radial-gradient(100% 80% at 100% 100%, #0c0a1a 0%, transparent 50%),\n    #06070b;\n}\n.bg::before,\n.bg::after{\n  content:"";\n  position:absolute;\n  inset:-20%;\n  pointer-events:none;\n}\n/* soft color fields that slowly drift and breathe */\n.bg::before{\n  background:\n    radial-gradient(ellipse 50% 45% at 15% 25%, rgba(45,120,255,.45) 0%, transparent 55%),\n    radial-gradient(ellipse 45% 40% at 85% 75%, rgba(100,70,220,.4) 0%, transparent 55%),\n    radial-gradient(ellipse 40% 35% at 70% 15%, rgba(30,180,140,.22) 0%, transparent 50%),\n    radial-gradient(ellipse 55% 50% at 40% 90%, rgba(40,90,200,.28) 0%, transparent 55%);\n  filter:blur(8px);\n  animation:meshA 22s ease-in-out infinite alternate;\n  opacity:.95;\n}\n.bg::after{\n  background:\n    radial-gradient(ellipse 42% 38% at 80% 20%, rgba(80,160,255,.35) 0%, transparent 55%),\n    radial-gradient(ellipse 48% 42% at 25% 70%, rgba(120,80,255,.32) 0%, transparent 55%),\n    radial-gradient(ellipse 35% 30% at 50% 45%, rgba(50,200,160,.12) 0%, transparent 50%);\n  filter:blur(12px);\n  animation:meshB 28s ease-in-out infinite alternate;\n  mix-blend-mode:screen;\n  opacity:.75;\n}\n.bg-noise{\n  position:absolute;\n  inset:0;\n  opacity:.035;\n  background-image:url("data:image/svg+xml,%3Csvg viewBox=\'0 0 256 256\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.85\' numOctaves=\'4\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23n)\'/%3E%3C/svg%3E");\n  background-size:180px 180px;\n  pointer-events:none;\n}\n.bg-vignette{\n  position:absolute;\n  inset:0;\n  background:radial-gradient(ellipse 75% 70% at 50% 45%, transparent 30%, rgba(0,0,0,.55) 100%);\n  pointer-events:none;\n}\n.bg-grid{\n  position:absolute;\n  inset:0;\n  background-image:\n    linear-gradient(rgba(255,255,255,.035) 1px, transparent 1px),\n    linear-gradient(90deg, rgba(255,255,255,.035) 1px, transparent 1px);\n  background-size:56px 56px;\n  mask-image:radial-gradient(ellipse 70% 60% at 50% 45%, #000 0%, transparent 72%);\n  -webkit-mask-image:radial-gradient(ellipse 70% 60% at 50% 45%, #000 0%, transparent 72%);\n  opacity:.45;\n  pointer-events:none;\n  animation:gridPulse 10s ease-in-out infinite alternate;\n}\n\n@keyframes meshA{\n  0%{transform:translate(0,0) scale(1)}\n  50%{transform:translate(3%,-2%) scale(1.05)}\n  100%{transform:translate(-2%,3%) scale(1.02)}\n}\n@keyframes meshB{\n  0%{transform:translate(0,0) scale(1.05)}\n  50%{transform:translate(-4%,2%) scale(1)}\n  100%{transform:translate(2%,-3%) scale(1.08)}\n}\n@keyframes gridPulse{\n  from{opacity:.3}\n  to{opacity:.5}\n}\n@keyframes pop{\n  from{opacity:0;transform:scale(.96) translateY(14px)}\n  to{opacity:1;transform:none}\n}\n\n.wrap{\n  position:relative;\n  z-index:1;\n  min-height:100%;\n  display:flex;\n  align-items:center;\n  justify-content:center;\n  padding:24px;\n}\n.card{\n  width:min(400px,100%);\n  background:rgba(14,16,22,.78);\n  border:1px solid rgba(255,255,255,.1);\n  border-radius:20px;\n  padding:32px 28px 28px;\n  box-shadow:\n    0 24px 64px rgba(0,0,0,.55),\n    0 0 0 1px rgba(255,255,255,.03) inset,\n    0 1px 0 rgba(255,255,255,.06) inset;\n  backdrop-filter:blur(24px) saturate(1.2);\n  -webkit-backdrop-filter:blur(24px) saturate(1.2);\n  animation:pop .45s var(--ease);\n}\n.logo{text-align:center;margin-bottom:6px}\n.logo-mark{\n  display:inline-flex;\n  align-items:center;\n  justify-content:center;\n  width:48px;\n  height:48px;\n  border-radius:14px;\n  background:linear-gradient(145deg,rgba(59,158,255,.28),rgba(124,106,247,.18));\n  border:1px solid rgba(59,158,255,.35);\n  color:var(--acc);\n  margin-bottom:12px;\n  box-shadow:0 8px 28px rgba(59,158,255,.22);\n}\n.brand{\n  font-size:1.35rem;\n  font-weight:700;\n  letter-spacing:-.03em;\n  background:linear-gradient(100deg,#9fd0ff 0%,#3b9eff 45%,#8b7cf7 100%);\n  -webkit-background-clip:text;\n  background-clip:text;\n  color:transparent;\n}\n.hello{\n  text-align:center;\n  font-size:1.15rem;\n  font-weight:600;\n  margin:10px 0 4px;\n  letter-spacing:-.02em;\n}\n.sub{\n  text-align:center;\n  font-size:13px;\n  color:var(--muted);\n  margin:0 0 22px;\n  line-height:1.45;\n}\n.field{margin-bottom:14px}\n.field label{\n  display:block;\n  font-size:12px;\n  font-weight:600;\n  color:var(--muted);\n  margin-bottom:6px;\n}\n.input-wrap{position:relative}\n.input-wrap svg{\n  position:absolute;\n  left:12px;\n  top:50%;\n  transform:translateY(-50%);\n  color:var(--muted);\n  opacity:.7;\n  pointer-events:none;\n}\n.field input{\n  width:100%;\n  border-radius:12px;\n  border:1px solid rgba(255,255,255,.1);\n  background:rgba(0,0,0,.4);\n  padding:12px 12px 12px 40px;\n  outline:none;\n  color:var(--text);\n  transition:border-color .2s,box-shadow .2s;\n}\n.field input:focus{\n  border-color:rgba(59,158,255,.55);\n  box-shadow:0 0 0 3px rgba(59,158,255,.15);\n}\n.field input::placeholder{color:#5c657a}\n.err{\n  background:rgba(255,107,107,.12);\n  color:#ff6b6b;\n  border:1px solid rgba(255,107,107,.22);\n  border-radius:12px;\n  padding:10px 12px;\n  font-size:13px;\n  margin-bottom:14px;\n}\n.btn{\n  width:100%;\n  border:0;\n  border-radius:12px;\n  padding:12px 16px;\n  font-size:15px;\n  font-weight:600;\n  cursor:pointer;\n  background:linear-gradient(180deg,#54aaff,#2f8ff0);\n  color:#061018;\n  box-shadow:0 6px 22px rgba(59,158,255,.35);\n  transition:transform .15s,filter .15s;\n  margin-top:6px;\n}\n.btn:hover{filter:brightness(1.06)}\n.btn:active{transform:translateY(1px)}\n.foot{\n  text-align:center;\n  margin-top:18px;\n  font-size:12px;\n  color:#5c657a;\n}\n@media (prefers-reduced-motion:reduce){\n  .bg::before,.bg::after,.bg-grid{animation:none}\n}\n</style>\n</head>\n<body>\n<div class="bg" aria-hidden="true">\n  <div class="bg-noise"></div>\n  <div class="bg-grid"></div>\n  <div class="bg-vignette"></div>\n</div>\n<div class="wrap">\n  <div class="card">\n    <div class="logo">\n      <div class="logo-mark">\n        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>\n      </div>\n      <div class="brand">xttp panel</div>\n    </div>\n    <h1 class="hello">Привет!</h1>\n    <p class="sub">Войдите, чтобы управлять группами маршрутизации</p>\n    @@ERR@@\n    <form method="post" action="/login" autocomplete="on">\n      <div class="field">\n        <label for="user">Имя пользователя</label>\n        <div class="input-wrap">\n          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="8" r="4"/></svg>\n          <input id="user" name="user" type="text" placeholder="Имя пользователя" required autofocus autocomplete="username"/>\n        </div>\n      </div>\n      <div class="field">\n        <label for="pass">Пароль</label>\n        <div class="input-wrap">\n          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>\n          <input id="pass" name="pass" type="password" placeholder="Пароль" required autocomplete="current-password"/>\n        </div>\n      </div>\n      <button type="submit" class="btn">Войти</button>\n    </form>\n    <div class="foot">version @@VERSION@@</div>\n  </div>\n</div>\n<script>\n(function(){\n  try {\n    if (location.search) history.replaceState(null, "", location.pathname || "/");\n  } catch (e) {}\n})();\n</script>\n</body>\n</html>\n\n'
SESSION_COOKIE = "lists_session"
FLASH_MSG_COOKIE = "lists_flash_msg"
FLASH_ERR_COOKIE = "lists_flash_err"
FLASH_TTL = 60  # seconds
SESSION_TTL = 7 * 24 * 3600  # 7 days

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]+(?:\.(?!-)[a-z0-9-]+)+\.?$",
    re.I,
)

# category tokens for left stripe (CSS --stripe-*)
STRIPE_CAT_MESSENGER = ("telegram", "discord", "whatsapp", "meta", "instagram", "facebook", "threads", "signal", "viber", "chat")
STRIPE_CAT_VIDEO = ("youtube", "meet", "video", "stream", "zoom", "media", "twitch", "netflix")
STRIPE_CAT_INFRA = ("cloud", "google", "ai", "geo", "block", "cdn", "dns", "cloudflare", "net", "proxy")


def stripe_category(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in STRIPE_CAT_MESSENGER):
        return "messenger"
    if any(k in n for k in STRIPE_CAT_VIDEO):
        return "video"
    if any(k in n for k in STRIPE_CAT_INFRA):
        return "infra"
    return "default"


def esc(s):
    return html.escape(str(s))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(XRAY_BACKUP_DIR, exist_ok=True)
    os.makedirs(GROUPS_BACKUP_DIR, exist_ok=True)
    os.makedirs(GROUPS_YAML_QUARANTINE, exist_ok=True)


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
    tmp = PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, PREFS_FILE)


def load_activity() -> list[dict]:
    ensure_data_dir()
    if not os.path.exists(ACTIVITY_FILE):
        return []
    try:
        d = json.load(open(ACTIVITY_FILE, encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def log_activity(user: str, action: str, detail: str = "", ok: bool = True, extra: dict | None = None) -> None:
    """Append operational event (newest first), keep last ACTIVITY_MAX."""
    ensure_data_dir()
    items = load_activity()
    entry = {
        "id": str(uuid.uuid4())[:8],
        "ts": utc_now(),
        "user": user or "system",
        "action": action,
        "detail": (detail or "")[:400],
        "ok": bool(ok),
    }
    if extra:
        entry["extra"] = extra
    items.insert(0, entry)
    items = items[:ACTIVITY_MAX]
    tmp = ACTIVITY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, ACTIVITY_FILE)



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
    """Cached component versions for Settings → Ядро (TTL ~8 min)."""
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
_update_lock = threading.Lock()
MIHOMO_BIN = "/usr/local/bin/mihomo"
GITHUB_UA = "xttp-panel-updater/0.17.2"


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


def _github_latest(repo: str) -> dict:
    """repo like MetaCubeX/mihomo → tag, body, html_url, assets."""
    d = _http_json(f"https://api.github.com/repos/{repo}/releases/latest", timeout=25.0)
    tag = str(d.get("tag_name") or "").strip()
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
        "html_url": str(d.get("html_url") or f"https://github.com/{repo}/releases/latest"),
        "notes": notes,
        "assets": assets,
        "name": str(d.get("name") or tag),
    }


def _pick_mihomo_asset(assets: list, version: str) -> dict | None:
    ver = _parse_version_token(version)
    names = {a.get("name"): a for a in assets if isinstance(a, dict)}
    for cand in (
        f"mihomo-linux-amd64-v{ver}.gz",
        f"mihomo-linux-amd64-{ver}.gz",
        f"mihomo-linux-amd64-v{ver}",
    ):
        if cand in names:
            return names[cand]
    # prefer plain amd64 .gz without compatible / go / v1- package variants
    scored = []
    for a in assets:
        n = str(a.get("name") or "")
        if "linux-amd64" not in n or not n.endswith(".gz"):
            continue
        if "compatible" in n or "-go" in n or "v1-go" in n:
            continue
        if re.search(r"v1-v[\d]", n):
            continue
        scored.append(a)
    if scored:
        return scored[0]
    for a in assets:
        n = str(a.get("name") or "")
        if "linux-amd64" in n and n.endswith(".gz") and "compatible" not in n:
            return a
    return None


def _pick_xray_asset(assets: list) -> dict | None:
    for a in assets:
        if str(a.get("name") or "") == "Xray-linux-64.zip":
            return a
    for a in assets:
        n = str(a.get("name") or "")
        if "linux-64" in n and n.endswith(".zip") and "dgst" not in n:
            return a
    return None


def check_component_update(component: str) -> dict:
    """Check GitHub (or panel changelog) for newer version. Cached 45 min per component."""
    component = (component or "").strip().lower()
    if component not in ("mihomo", "xray", "panel"):
        return {"ok": False, "error": "unknown component"}

    now = time.time()
    with _versions_lock:
        cached = _UPDATE_CHECK_CACHE.get(component)
        if cached and (now - cached.get("_ts", 0)) < _UPDATE_CHECK_TTL:
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
        elif component == "mihomo":
            rel = _github_latest("MetaCubeX/mihomo")
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
            rel = _github_latest("XTLS/Xray-core")
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
    with _versions_lock:
        _UPDATE_CHECK_CACHE[component] = dict(result)
    return result


def _ensure_bin_backup_dir() -> None:
    ensure_data_dir()
    os.makedirs(BIN_BACKUP_DIR, exist_ok=True)


def _backup_binary(bin_path: str, label: str) -> str:
    _ensure_bin_backup_dir()
    ts = time.strftime("%Y%m%d%H%M%S")
    dest = os.path.join(BIN_BACKUP_DIR, f"{label}.bak.{ts}")
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


def _health_mihomo() -> bool:
    if not service_active("mihomo"):
        return False
    try:
        d = mihomo_get("/version", timeout=4.0)
        return isinstance(d, dict) and bool(d.get("version"))
    except Exception:
        return False


def _health_xray() -> bool:
    if not service_active("xray"):
        return False
    # socks port must accept TCP
    try:
        with socket.create_connection((SOCKS_HOST, int(SOCKS_PORT)), timeout=3.0):
            return True
    except Exception:
        return False



def _github_panel_remote() -> dict:
    """Latest panel version from GitHub (version-manifest.json, fallback: latest tag)."""
    repo = (XTTP_GITHUB_REPO or "kotetsyy/xttp-stack").strip()
    branch = (XTTP_GITHUB_BRANCH or "main").strip() or "main"
    html_url = f"https://github.com/{repo}"
    # 1) version-manifest.json on branch (fleet signal)
    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/version-manifest.json"
    try:
        req = urllib.request.Request(
            raw_url,
            headers={"User-Agent": GITHUB_UA, "Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        manifest = json.loads(raw)
        ver = _parse_version_token(str(manifest.get("panel") or ""))
        notes = str(manifest.get("notes") or "").strip()
        if not notes:
            notes = (
                f"GitHub {repo}@{branch} · version-manifest.json"
                + (f" · updated_at {manifest.get('updated_at')}" if manifest.get("updated_at") else "")
            )
        if ver:
            return {
                "version": ver,
                "notes": notes,
                "html_url": f"{html_url}/blob/{branch}/version-manifest.json",
                "source": "version-manifest",
                "manifest": manifest,
            }
    except Exception as ex:
        manifest_err = str(ex)[:160]
    else:
        manifest_err = ""

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


def update_component_panel(user: str = "admin") -> dict:
    """Pull panel from git (scripts/update.sh) when GitHub version is newer."""
    if not _update_lock.acquire(blocking=False):
        return {"ok": False, "error": "обновление уже выполняется"}
    try:
        check = check_component_update("panel")
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error") or "check failed", "check": check}
        if not check.get("update_available"):
            return {"ok": False, "error": "обновление не требуется", "check": check}

        old_ver = check.get("current") or VERSION
        new_ver = check.get("latest") or "?"
        repo = os.environ.get("XTTP_GIT_REPO", XTTP_GIT_REPO)
        script = os.path.join(str(repo).rstrip("/\\"), "scripts", "update.sh")
        if not os.path.isfile(script):
            script = "/opt/xttp-stack/scripts/update.sh"
        if not os.path.isfile(script):
            return {"ok": False, "error": f"update.sh не найден: {script}"}

        # sync tree from GitHub (fleet node = pull-only)
        gh_repo = (XTTP_GITHUB_REPO or "kotetsyy/xttp-stack").strip()
        branch = (XTTP_GITHUB_BRANCH or "main").strip() or "main"
        git_log = []
        try:
            if os.path.isdir(os.path.join(repo, ".git")):
                rem = subprocess.run(
                    ["git", "-C", repo, "remote"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                remotes = (rem.stdout or "").split()
                url = f"https://github.com/{gh_repo}.git"
                if "origin" not in remotes:
                    subprocess.run(
                        ["git", "-C", repo, "remote", "add", "origin", url],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                else:
                    subprocess.run(
                        ["git", "-C", repo, "remote", "set-url", "origin", url],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )
                fr = subprocess.run(
                    ["git", "-C", repo, "fetch", "origin", branch],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                git_log.append((fr.stdout or "") + (fr.stderr or ""))
                if fr.returncode != 0:
                    return {
                        "ok": False,
                        "error": f"git fetch failed: {(fr.stderr or fr.stdout or '')[:240]}",
                        "old": old_ver,
                        "new": new_ver,
                    }
                pr = subprocess.run(
                    ["git", "-C", repo, "pull", "--ff-only", "origin", branch],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                git_log.append((pr.stdout or "") + (pr.stderr or ""))
                if pr.returncode != 0:
                    # diverged local bootstrap history — hard reset to origin (pull-only device)
                    rr = subprocess.run(
                        ["git", "-C", repo, "reset", "--hard", f"origin/{branch}"],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    git_log.append((rr.stdout or "") + (rr.stderr or ""))
                    if rr.returncode != 0:
                        return {
                            "ok": False,
                            "error": f"git sync failed: {(rr.stderr or pr.stderr or '')[:240]}",
                            "old": old_ver,
                            "new": new_ver,
                        }
        except Exception as ex:
            return {"ok": False, "error": f"git sync: {ex}", "old": old_ver, "new": new_ver}

        env = os.environ.copy()
        env["XTTP_GIT_REPO"] = str(repo)
        r = subprocess.run(
            ["/bin/bash", script],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
            cwd=str(repo) if os.path.isdir(repo) else None,
        )
        out = (
            "\n".join(x for x in git_log if x).strip()
            + "\n"
            + ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
        ).strip()
        ok = r.returncode == 0
        try:
            log_activity(
                user,
                "update",
                f"panel: {old_ver} → {new_ver} {'OK' if ok else 'FAIL'} (GitHub/git pull)",
                ok=ok,
                extra={"rc": r.returncode, "tail": out[-600:] if out else ""},
            )
        except Exception:
            pass
        if not ok:
            return {
                "ok": False,
                "error": f"update.sh rc={r.returncode}",
                "detail": out[-800:] if out else "",
                "old": old_ver,
                "new": new_ver,
            }
        # clear caches — new process after restart will reload VERSION
        global _VERSIONS_CACHE, _VERSIONS_CACHE_TS
        with _versions_lock:
            _VERSIONS_CACHE = None
            _VERSIONS_CACHE_TS = 0.0
            _UPDATE_CHECK_CACHE.pop("panel", None)
        return {
            "ok": True,
            "component": "panel",
            "old": old_ver,
            "new": new_ver,
            "detail": out[-600:] if out else "",
            "check": check,
        }
    except Exception as ex:
        try:
            log_activity(user, "update", f"panel update error: {ex}", ok=False)
        except Exception:
            pass
        return {"ok": False, "error": str(ex)[:300]}
    finally:
        try:
            _update_lock.release()
        except Exception:
            pass


def update_component_binary(component: str, user: str = "admin") -> dict:
    """Download latest binary, backup, replace, restart, healthcheck, rollback on fail."""
    component = (component or "").strip().lower()
    if component not in ("mihomo", "xray"):
        return {"ok": False, "error": "only mihomo/xray support auto-update"}

    if not _update_lock.acquire(blocking=False):
        return {"ok": False, "error": "обновление уже выполняется"}

    try:
        check = check_component_update(component)
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error") or "check failed"}
        if not check.get("update_available"):
            return {"ok": False, "error": "обновление не требуется", "check": check}
        url = check.get("asset_url")
        if not url:
            return {"ok": False, "error": "нет asset для скачивания в релизе"}

        bin_path = MIHOMO_BIN if component == "mihomo" else XRAY_BIN
        if not os.path.isfile(bin_path):
            return {"ok": False, "error": f"бинарник не найден: {bin_path}"}

        old_ver = check.get("current") or "?"
        new_ver = check.get("latest") or "?"
        svc = component  # service name matches

        import tempfile

        with tempfile.TemporaryDirectory(prefix=f"upd-{component}-") as td:
            asset_name = check.get("asset_name") or os.path.basename(url)
            dl_path = os.path.join(td, asset_name)
            new_bin = os.path.join(td, "new-bin")
            _http_download(url, dl_path, timeout=240.0)

            if component == "mihomo":
                if asset_name.endswith(".gz"):
                    _extract_mihomo_gz(dl_path, new_bin)
                else:
                    shutil.copy2(dl_path, new_bin)
                    os.chmod(new_bin, 0o755)
            else:
                if asset_name.endswith(".zip"):
                    _extract_xray_zip(dl_path, new_bin)
                else:
                    shutil.copy2(dl_path, new_bin)
                    os.chmod(new_bin, 0o755)

            backup = _backup_binary(bin_path, component)
            # stop → replace → start
            subprocess.run(["systemctl", "stop", svc], capture_output=True, text=True, timeout=30)
            try:
                shutil.copy2(new_bin, bin_path)
                os.chmod(bin_path, 0o755)
            except Exception as ex:
                # try restore before start
                try:
                    shutil.copy2(backup, bin_path)
                except Exception:
                    pass
                subprocess.run(["systemctl", "start", svc], capture_output=True, text=True, timeout=30)
                raise RuntimeError(f"replace failed: {ex}") from ex

            subprocess.run(["systemctl", "start", svc], capture_output=True, text=True, timeout=30)
            time.sleep(1.2)

            healthy = _health_mihomo() if component == "mihomo" else _health_xray()
            if not healthy:
                # rollback
                try:
                    subprocess.run(["systemctl", "stop", svc], capture_output=True, text=True, timeout=30)
                    shutil.copy2(backup, bin_path)
                    os.chmod(bin_path, 0o755)
                    subprocess.run(["systemctl", "start", svc], capture_output=True, text=True, timeout=30)
                    time.sleep(1.0)
                except Exception:
                    pass
                try:
                    log_activity(
                        user,
                        "update",
                        f"{component}: {old_ver} → {new_ver} FAILED (rollback)",
                        ok=False,
                    )
                except Exception:
                    pass
                return {
                    "ok": False,
                    "error": "сервис не поднялся после обновления — откат на старый бинарник",
                    "rolled_back": True,
                    "backup": backup,
                    "old": old_ver,
                    "new": new_ver,
                }

            # success — clear version caches
            global _VERSIONS_CACHE, _VERSIONS_CACHE_TS
            with _versions_lock:
                _VERSIONS_CACHE = None
                _VERSIONS_CACHE_TS = 0.0
                _UPDATE_CHECK_CACHE.pop(component, None)

            try:
                log_activity(
                    user,
                    "update",
                    f"{component}: {old_ver} → {new_ver} OK",
                    ok=True,
                    extra={"backup": backup},
                )
            except Exception:
                pass

            return {
                "ok": True,
                "component": component,
                "old": old_ver,
                "new": new_ver,
                "backup": backup,
                "check": check_component_update(component),
            }
    except Exception as ex:
        try:
            log_activity(user, "update", f"{component}: error {ex}", ok=False)
        except Exception:
            pass
        return {"ok": False, "error": str(ex)[:300]}
    finally:
        try:
            _update_lock.release()
        except Exception:
            pass


def core_configs_snapshot() -> dict:
    """Runtime mihomo /configs for Settings → Ядро."""
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
_LOG_LEVEL = "info"
_log_thread: threading.Thread | None = None
_log_stop = threading.Event()
_log_level_wanted = "info"


def _log_push(level: str, payload: str) -> None:
    global _LOG_SEQ
    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LOG_BUF.append({
            "seq": _LOG_SEQ,
            "ts": time.time(),
            "type": (level or "info").lower(),
            "payload": payload or "",
        })


def start_log_sampler(level: str = "info") -> None:
    """Daemon: keep a local ring of mihomo log lines (panel polls /api/logs)."""
    global _log_thread, _log_level_wanted
    _log_level_wanted = (level or "info").lower()
    if _log_thread and _log_thread.is_alive():
        return

    def loop() -> None:
        global _log_level_wanted
        while not _log_stop.is_set():
            lvl = _log_level_wanted or "info"
            url = MIHOMO_API + "/logs?level=" + urllib.parse.quote(lvl)
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=60) as resp:
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
            except Exception:
                time.sleep(1.0)
            time.sleep(0.3)

    _log_stop.clear()
    _log_thread = threading.Thread(target=loop, name="log-sampler", daemon=True)
    _log_thread.start()


def set_log_level(level: str) -> None:
    global _log_level_wanted
    _log_level_wanted = (level or "info").lower()


def logs_snapshot(level: str = "all", since: int = 0, limit: int = 200, mode: str = "tail") -> dict:
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
    if mode == "live" and since > 0:
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
        "ok": True,
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
    global _traffic_last, _traffic_samples_since_save
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


def stats_snapshot() -> dict:
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
            # still list empty enabled groups with 0 metrics
            pass
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
        "history": traffic_history_list(),
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
    if name == "xray":
        restart_xray()
        return
    if name == "mihomo":
        restart_mihomo()
        return
    raise RuntimeError("unknown service: " + name)


def session_secret() -> bytes:
    ensure_data_dir()
    if not os.path.exists(SESSION_SECRET_FILE):
        open(SESSION_SECRET_FILE, "w", encoding="utf-8").write(secrets.token_hex(32) + "\n")
        try:
            os.chmod(SESSION_SECRET_FILE, 0o600)
        except OSError:
            pass
    raw = open(SESSION_SECRET_FILE, encoding="utf-8").read().strip()
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return hashlib.sha256(raw.encode()).digest()


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
                "password_hash": hash_password(BOOTSTRAP_PASS),
                "enabled": True,
                "created_at": utc_now(),
                "last_login": "",
            }
        ]
        save_users(users)
        return users
    try:
        d = json.load(open(USERS_FILE, encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def save_users(users: list[dict]) -> None:
    ensure_data_dir()
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, USERS_FILE)
    try:
        os.chmod(USERS_FILE, 0o600)
    except OSError:
        pass


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


def make_session_token(user: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f"{user}:{exp}"
    sig = hmac.new(session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> str | None:
    """Return username if valid enabled user, else None."""
    if not token or token.count(":") < 2:
        return None
    try:
        user, exp_s, sig = token.rsplit(":", 2)
        payload = f"{user}:{exp_s}"
        expect = hmac.new(session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        if int(exp_s) < int(time.time()):
            return None
        u = find_user(user)
        if not u or not u.get("enabled", True):
            return None
        return user
    except Exception:
        return None


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


def login_page(err: str = "") -> bytes:
    html_out = LOGIN_HTML or "<html><body>LOGIN missing</body></html>"
    err_block = ""
    if err:
        err_block = f'<div class="err" role="alert">{esc(err)}</div>'
    html_out = html_out.replace("@@ERR@@", err_block).replace("@@VERSION@@", esc(VERSION))
    return html_out.encode("utf-8")


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


def service_active(name: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
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
    port = int(port_s)
    net = p.get("type") or p.get("network") or "tcp"
    security = p.get("security") or "none"
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
    ensure_data_dir()
    cfg = _read_xray_config()
    if not cfg:
        raise RuntimeError("xray config not found")
    name = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".json"
    path = os.path.join(XRAY_BACKUP_DIR, name)
    open(path, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    # keep last 8
    files = sorted(
        [f for f in os.listdir(XRAY_BACKUP_DIR) if f.endswith(".json")],
        reverse=True,
    )
    for old in files[8:]:
        try:
            os.remove(os.path.join(XRAY_BACKUP_DIR, old))
        except OSError:
            pass
    return name


def write_xray_config(cfg: dict) -> None:
    data = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    for p in XRAY_CONFIGS:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            continue
        if not os.path.exists(p) and p != XRAY_CONFIGS[0]:
            # only write secondary if it already exists
            continue
        tmp = p + ".tmp"
        open(tmp, "w", encoding="utf-8").write(data)
        os.replace(tmp, p)
    # always ensure primary
    p0 = XRAY_CONFIGS[0]
    os.makedirs(os.path.dirname(p0), exist_ok=True)
    tmp = p0 + ".tmp"
    open(tmp, "w", encoding="utf-8").write(data)
    os.replace(tmp, p0)


def restart_xray() -> None:
    r = subprocess.run(RESTART_XRAY_CMD, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "xray restart failed")
    time.sleep(0.8)


def list_xray_backups() -> list[str]:
    if not os.path.isdir(XRAY_BACKUP_DIR):
        return []
    return sorted([f for f in os.listdir(XRAY_BACKUP_DIR) if f.endswith(".json")], reverse=True)[:8]


def rollback_xray(name: str) -> None:
    path = os.path.join(XRAY_BACKUP_DIR, os.path.basename(name))
    if not os.path.exists(path):
        raise RuntimeError("backup not found")
    cfg = json.load(open(path, encoding="utf-8"))
    write_xray_config(cfg)
    restart_xray()


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


def type_badge(t: str) -> tuple[str, str]:
    m = {
        "ipv4": ("IPV4", "badge-v4"),
        "ipv6": ("IPV6", "badge-v6"),
        "namespace": ("NAMESPACE", "badge-ns"),
        "domain": ("DOMAIN", "badge-dom"),
        "keyword": ("KEYWORD", "badge-kw"),
        "raw": ("RAW", "badge-raw"),
    }
    return m.get(t, ("RAW", "badge-raw"))


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
    dest = os.path.join(GROUPS_BACKUP_DIR, f"groups.{ts}.{safe}.json")
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


def _load_groups_unlocked() -> list[dict]:
    """Load groups without acquiring lock (caller must hold _groups_lock)."""
    try:
        d = _read_groups_raw()
    except Exception as ex:
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
        _save_groups_unlocked(groups, reason="bootstrap", force=True)
        return groups

    d, changed = _normalize_groups(d)
    if changed:
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
    tmp = GROUPS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, GROUPS_FILE)
    # optional async git history (never blocks / fails the save)
    try:
        _git_commit_groups_async(reason=reason, detail=reason)
    except Exception:
        pass


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


def write_group_yaml(group: dict, provider_name: str) -> str:
    os.makedirs(GROUPS_DIR, exist_ok=True)
    # file name mirrors provider name for easy debugging
    fid = re.sub(r"[^a-zA-Z0-9_-]+", "_", provider_name)[:48]
    path = os.path.join(GROUPS_DIR, fid + ".yaml")
    lines = ["payload:", f"  # group: {group.get('name','')} id={group.get('id','')}"]
    for ent in group.get("entries") or []:
        if ent.get("enabled", True) is False:
            continue
        rule = ent.get("rule") or ""
        if rule:
            lines.append("  - " + rule)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return "./rule-providers/groups/" + fid + ".yaml", fid + ".yaml"


def apply_config() -> None:
    with _groups_lock:
        groups = copy.deepcopy(_load_groups_unlocked())
    prov = ["rule-providers:"]
    rsets = ["  # groups -> PROXY"]
    used_names: set[str] = set()
    keep_files: set[str] = set()

    for g in groups:
        if not g.get("enabled", True):
            continue
        active = [e for e in (g.get("entries") or []) if e.get("enabled", True) and e.get("rule")]
        if not active:
            continue
        n = group_provider_name(g, used_names)
        rel, fname = write_group_yaml(g, n)
        keep_files.add(fname)
        prov += [
            f"  {n}:",
            "    type: file",
            "    behavior: classical",
            f"    path: {rel}",
        ]
        rsets.append(f"  - RULE-SET,{n},PROXY")

    if len(prov) == 1:
        os.makedirs(GROUPS_DIR, exist_ok=True)
        empty = GROUPS_DIR + "/_empty.yaml"
        open(empty, "w", encoding="utf-8").write("payload: []\n")
        keep_files.add("_empty.yaml")
        prov += [
            "  _empty:",
            "    type: file",
            "    behavior: classical",
            "    path: ./rule-providers/groups/_empty.yaml",
        ]

    # quarantine stale group yaml (do not hard-delete — recoverable)
    try:
        _ensure_groups_dirs()
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for fn in os.listdir(GROUPS_DIR):
            if not fn.endswith(".yaml") or fn in keep_files:
                continue
            src = os.path.join(GROUPS_DIR, fn)
            dst = os.path.join(GROUPS_YAML_QUARANTINE, f"{ts}.{fn}")
            try:
                shutil.move(src, dst)
            except OSError:
                try:
                    os.remove(src)
                except OSError:
                    pass
        qfiles = [
            os.path.join(GROUPS_YAML_QUARANTINE, n)
            for n in os.listdir(GROUPS_YAML_QUARANTINE)
            if n.endswith(".yaml")
        ]
        qfiles.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        _rotate_paths(qfiles, GROUPS_YAML_QUARANTINE_MAX)
    except FileNotFoundError:
        pass

    # only private LAN + groups + default MATCH
    rules_head = [
        "rules:",
        "  - GEOIP,PRIVATE,DIRECT,no-resolve",
        "",
    ]
    cfg = open(CONFIG_FILE, encoding="utf-8").read()
    if "rule-providers:" not in cfg:
        raise RuntimeError("config.yaml: no rule-providers section")
    head = cfg.split("rule-providers:")[0].rstrip() + "\n\n"
    new_cfg = head + "\n".join(prov) + "\n\n" + "\n".join(rules_head + rsets + ["", "  - MATCH,DIRECT", ""])
    tmp = CONFIG_FILE + ".tmp"
    open(tmp, "w", encoding="utf-8").write(new_cfg)
    r = subprocess.run(
        ["/usr/local/bin/mihomo", "-t", "-d", "/etc/mihomo", "-f", tmp],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("mihomo config invalid:\n" + (r.stderr or r.stdout)[-800:])
    os.replace(tmp, CONFIG_FILE)


def restart_mihomo():
    r = subprocess.run(RESTART_CMD, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "restart failed")


def find_group(groups, gid):
    for g in groups:
        if g.get("id") == gid:
            return g
    return None


def type_options(selected: str) -> str:
    opts = [
        ("namespace", "Namespace"),
        ("ipv4", "IPv4"),
        ("ipv6", "IPv6"),
        ("domain", "Domain"),
        ("keyword", "Keyword"),
    ]
    parts = []
    for val, lab in opts:
        sel = " selected" if val == selected else ""
        parts.append(f'<option value="{val}"{sel}>{lab}</option>')
    return "".join(parts)


def ensure_entry_id(ent: dict) -> str:
    if not ent.get("id"):
        ent["id"] = str(uuid.uuid4())[:8]
    return ent["id"]


def render_entry_row(gid: str, j: int, ent: dict) -> str:
    etype = ent.get("type", "namespace")
    if etype == "raw":
        etype = "namespace"
    en_checked = "checked" if ent.get("enabled", True) else ""
    ename = ent.get("name") or ""
    val = ent.get("value") or ent.get("rule") or ""
    eid = ensure_entry_id(ent)
    return f"""<tr class="rule-row" data-eid="{esc(eid)}" data-filter="{esc((ename+' '+val+' '+etype).lower())}" style="--ri:{min(j, 12)}">
      <td class="col-id">#{j+1}</td>
      <td class="col-name-hide"><input class="cell-input emptyish" name="ename" value="{esc(ename)}" placeholder="имя правила…"/></td>
      <td class="col-type">
        <select class="type-select {esc(etype)}" name="etype" onchange="this.className='type-select '+this.value">
          {type_options(etype)}
        </select>
      </td>
      <td class="col-pattern"><div class="pattern-cell">
        <input class="cell-input mono pattern-input emptyish" name="value" value="{esc(val)}" placeholder="паттерн правила…"/>
        <span class="rule-conflict-badge pill pill-status pill-conflict" hidden></span>
      </div></td>
      <td class="col-en">
        <label class="switch" title="Включён">
          <input type="checkbox" name="enabled" value="1" {en_checked} data-toggle-entry/>
          <span></span>
        </label>
      </td>
      <td class="col-del">
        <button type="button" class="icon-del" data-del-entry aria-label="Удалить" title="Удалить">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
        </button>
      </td>
    </tr>"""


def render_group_card(g: dict, idx: int) -> str:
    gid = g.get("id", "")
    name = g.get("name", "")
    cat = stripe_category(name)
    enabled = g.get("enabled", True)
    entries = g.get("entries") or []
    for ent in entries:
        ensure_entry_id(ent)
    disabled_cls = "" if enabled else " disabled"
    checked = "checked" if enabled else ""
    n = len(entries)

    rows = [render_entry_row(gid, j, ent) for j, ent in enumerate(entries)]

    counts = {}
    for e in entries:
        t = e.get("type", "namespace")
        if t == "raw":
            t = "namespace"
        counts[t] = counts.get(t, 0) + 1
    count_pills = " ".join(
        f'<span class="pill {type_badge(t)[1]}">{type_badge(t)[0]} {c}</span>'
        for t, c in sorted(counts.items())
    )
    hay_parts = [name.lower()]
    for e in entries:
        hay_parts.append((e.get("name") or "").lower())
        hay_parts.append((e.get("value") or "").lower())
        hay_parts.append((e.get("rule") or "").lower())
        hay_parts.append((e.get("type") or "").lower())
    hay = " ".join(p for p in hay_parts if p)

    return f"""
    <div class="gcard collapsed{disabled_cls}" data-gid="{esc(gid)}" data-cat="{esc(cat)}" data-filter="{esc(hay)}" style="--i:{min(idx, 12)}">
      <div class="gcard-head">
        <div class="gcard-stripe" aria-hidden="true"></div>
        <span class="gcard-drag" aria-hidden="true" title="Перетащить">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>
        </span>
        <form method="post" action="/group-rename" style="flex:1;min-width:0;display:flex" onclick="event.stopPropagation()">
          <input type="hidden" name="gid" value="{esc(gid)}"/>
          <input class="gcard-name emptyish" name="name" value="{esc(name)}" placeholder="имя группы…" onchange="this.form.submit()"/>
        </form>
        <span class="gcard-conflict-warn" hidden title="" aria-label="Конфликт правил" role="img"></span>
        <div class="gcard-meta">
          <span class="rule-count-wrap" title="{n} активных правил">
            <span class="rule-count">{n}</span>
            <span class="rule-count-lbl">правил</span>
          </span>
          <span class="count-pills">{count_pills}</span>
        </div>
        <div class="gcard-actions" onclick="event.stopPropagation()">
          <div class="act-group act-state">
            <form method="post" action="/group-toggle">
              <input type="hidden" name="gid" value="{esc(gid)}"/>
              <label class="switch" title="Включить / выключить группу">
                <input type="checkbox" name="enabled" value="1" {checked} onchange="this.form.submit()"/>
                <span></span>
              </label>
            </form>
          </div>
          <span class="act-sep" aria-hidden="true"></span>
          <div class="act-group act-danger">
            <form method="post" action="/group-delete" onsubmit="return confirm('Удалить группу?')">
              <input type="hidden" name="gid" value="{esc(gid)}"/>
              <button type="submit" class="mini-btn danger" title="Удалить группу" aria-label="Удалить группу">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
              </button>
            </form>
          </div>
          <span class="act-sep" aria-hidden="true"></span>
          <div class="act-group act-tools">
            <button type="button" class="mini-btn" data-add-rule title="Добавить правило" aria-label="Добавить правило">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M12 5v14M5 12h14"/></svg>
            </button>
            <button type="button" class="mini-btn" data-import="{esc(gid)}" title="Импорт списка" aria-label="Импорт списка">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12"/><path d="M8 11l4 4 4-4"/><path d="M4 19h16"/></svg>
            </button>
            <button type="button" class="mini-btn" data-collapse title="Развернуть / свернуть" aria-label="Развернуть или свернуть">
              <svg class="chev" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>
            </button>
          </div>
        </div>
      </div>
      <div class="gcard-body">
        <table class="gtable">
          <thead>
            <tr>
              <th class="col-id">#</th>
              <th class="col-name-hide">Имя</th>
              <th class="col-type">Тип</th>
              <th>Паттерн</th>
              <th class="col-en">Включён</th>
              <th class="col-del"></th>
            </tr>
          </thead>
          <tbody class="rules-body">
            {''.join(rows)}
          </tbody>
        </table>
        <div class="gcard-foot">
          <span class="rule-count-wrap" title="{n} активных правил">
            <span class="rule-count">{n}</span>
            <span class="rule-count-lbl">правил</span>
          </span>
          <span class="gcard-conflict-count" data-show="0" hidden></span>
          <span class="count-pills">{count_pills}</span>
          <button type="button" class="btn btn-ghost btn-save-group" style="margin-left:auto;padding:6px 12px;font-size:12px">Сохранить правки</button>
        </div>
      </div>
    </div>
    """


def page(msg="", err="", tab="groups", group_id=""):
    groups = load_groups()

    toast = ""
    if msg:
        toast = f'<div class="toast toast-ok" role="status">{esc(msg)}</div>'
    if err:
        toast = f'<div class="toast toast-err" role="alert"><pre>{esc(err)}</pre></div>'

    if groups:
        groups_content = "".join(render_group_card(g, i) for i, g in enumerate(groups))
    else:
        groups_content = (
            '<div class="empty-wrap"><div class="empty-card">'
            '<div class="empty-icon"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg></div>'
            "<div><h3>Групп нет</h3><p>Создайте группу (+). Добавьте IP-CIDR или domain — тип определится автоматически.</p></div>"
            "</div></div>"
        )

    html_out = PAGE_HTML or "<html><body>PAGE_HTML missing</body></html>"
    for k, v in {
        "@@TOAST@@": toast,
        "@@GROUPS@@": groups_content,
        "@@VERSION@@": esc(VERSION),
        "@@DASH@@": esc(DASH_URL),
    }.items():
        html_out = html_out.replace(k, v)
    return html_out.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[lists-ui]", self.address_string(), fmt % args, flush=True)

    def _auth_user(self) -> str | None:
        # Only cookie session — not browser Basic Auth cache (that broke logout)
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        return verify_session_token(cookies.get(SESSION_COOKIE, ""))

    def _auth_ok(self):
        return self._auth_user() is not None

    def _need_auth(self, err: str = ""):
        """Custom login page — no WWW-Authenticate (avoids browser Basic popup)."""
        body = login_page(err)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _set_session_cookie(self, token: str, clear: bool = False):
        # Expire thoroughly so browsers drop the session
        if clear:
            cookie = (
                f"{SESSION_COOKIE}=deleted; Path=/; Max-Age=0; "
                f"Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax"
            )
        else:
            cookie = (
                f"{SESSION_COOKIE}={urllib.parse.quote(token)}; Path=/; "
                f"Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax"
            )
        self.send_header("Set-Cookie", cookie)

    def _ctx(self, form=None):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        gid = (form or {}).get("gid") or (q.get("gid") or [""])[0]
        return "groups", gid

    def _set_flash_cookies(self, msg: str = "", err: str = "", clear: bool = False):
        """One-shot toast cookies so Location stays without ?msg=."""
        exp = "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0" if clear else f"Max-Age={FLASH_TTL}"
        if clear or msg:
            val = "deleted" if clear else urllib.parse.quote(msg or "", safe="")
            self.send_header(
                "Set-Cookie",
                f"{FLASH_MSG_COOKIE}={val}; Path=/; {exp}; HttpOnly; SameSite=Lax",
            )
        if clear or err:
            val = "deleted" if clear else urllib.parse.quote(err or "", safe="")
            self.send_header(
                "Set-Cookie",
                f"{FLASH_ERR_COOKIE}={val}; Path=/; {exp}; HttpOnly; SameSite=Lax",
            )

    def _pop_flash(self) -> tuple[str, str]:
        cookies = parse_cookies(self.headers.get("Cookie", ""))
        msg = (cookies.get(FLASH_MSG_COOKIE) or "").strip()
        err = (cookies.get(FLASH_ERR_COOKIE) or "").strip()
        if msg == "deleted":
            msg = ""
        if err == "deleted":
            err = ""
        return msg, err

    def _html(self, code=200, msg="", err="", tab="groups", group_id="", clear_flash: bool = False):
        body = page(msg=msg, err=err)
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if clear_flash or msg or err:
            self._set_flash_cookies(clear=True)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, msg="", err="", path="/", set_cookie: str | None = None, clear_cookie: bool = False):
        """Post/Redirect/Get ? toast via flash cookie, clean Location."""
        if "?" in path:
            path = path.split("?", 1)[0] or "/"
        self.send_response(303)
        self.send_header("Location", path or "/")
        if set_cookie is not None:
            self._set_session_cookie(set_cookie)
        if clear_cookie:
            self._set_session_cookie("", clear=True)
        if msg or err:
            self._set_flash_cookies(msg=msg or "", err=err or "")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self):
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        q = urllib.parse.parse_qs(raw, keep_blank_values=True)
        return {k: (v[0] if v else "") for k, v in q.items()}

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)

        if path in ("/login", "/login.html"):
            if self._auth_ok():
                return self._redirect()
            return self._need_auth()

        if path == "/logout":
            # clean URL, no ?msg= on login screen
            return self._redirect(clear_cookie=True)

        if not self._auth_ok():
            if path.startswith("/api/"):
                return self._json(401, {"ok": False, "error": "auth required"})
            # if someone landed on /?msg=... while logged out — still login page
            return self._need_auth()

        if path in ("/", "/index.html"):
            fmsg, ferr = self._pop_flash()
            msg = fmsg or (q.get("msg") or [""])[0]
            err = ferr or (q.get("err") or [""])[0]
            return self._html(msg=msg, err=err, clear_flash=True)
        if path == "/api/users":
            me = self._auth_user() or ""
            return self._json(200, {"ok": True, "me": me, "users": [public_user(u) for u in load_users()]})
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
            text = (q.get("text") or [""])[0]
            results = []
            for line in text.splitlines():
                d = detect_entry(line)
                if d:
                    results.append({"type": d["type"], "value": d["value"], "rule": d["rule"]})
            counts = {}
            for d in results:
                counts[d["type"]] = counts.get(d["type"], 0) + 1
            return self._json(200, {"items": results, "counts": counts})
        if path == "/api/explain-route":
            target = (q.get("q") or q.get("target") or q.get("query") or [""])[0]
            return self._json(200, explain_route(target))
        if path == "/api/stats/snapshot":
            return self._json(200, stats_snapshot())
        if path == "/api/connections":
            return self._json(200, connections_snapshot())
        if path == "/api/logs":
            level = (q.get("level") or ["all"])[0]
            since = (q.get("since") or ["0"])[0]
            limit = (q.get("limit") or ["200"])[0]
            mode = (q.get("mode") or ["tail"])[0]
            return self._json(200, logs_snapshot(level=level, since=since, limit=limit, mode=mode))

        if path in ("/api/core", "/api/core/configs"):
            return self._json(200, core_configs_snapshot())
        if path == "/api/core/versions":
            force = (q.get("force") or ["0"])[0] in ("1", "true", "yes")
            return self._json(200, components_versions(force=force))

        if path == "/api/core/versions/check":
            comp = (q.get("component") or q.get("c") or [""])[0]
            return self._json(200, check_component_update(comp))
        if path == "/api/rules/overview":
            qq = (q.get("q") or [""])[0]
            return self._json(200, rules_overview_snapshot(q=qq))

        # old bookmarked POST urls → home
        if path.lstrip("/").split("?")[0] in (
            "group-add",
            "group-delete",
            "group-rename",
            "group-toggle",
            "entry-import",
            "apply",
            "entry-add",
            "entry-delete",
            "entry-update",
        ):
            return self._redirect()
        self.send_error(404)


    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._auth_ok():
            if path.startswith("/api/"):
                return self._json(401, {"ok": False, "error": "auth required"})
            return self._need_auth()
        me = self._auth_user() or "admin"
        # /api/connections/<id> or /api/connections
        if path == "/api/connections":
            d = mihomo_delete("/connections")
            log_activity(me, "conn_kill_all", "DELETE /connections", ok=bool(d.get("ok", True)))
            return self._json(200 if d.get("ok", True) else 500, d if isinstance(d, dict) else {"ok": True})
        if path.startswith("/api/connections/"):
            cid = path[len("/api/connections/"):].strip("/")
            if not cid or "/" in cid:
                return self._json(400, {"ok": False, "error": "bad id"})
            d = mihomo_delete("/connections/" + urllib.parse.quote(cid, safe=""))
            log_activity(me, "conn_kill", cid[:48], ok=bool(d.get("ok", True)))
            return self._json(200 if d.get("ok", True) else 500, d if isinstance(d, dict) else {"ok": True})
        if path == "/api/logs/level":
            return self._json(405, {"ok": False, "error": "use POST"})
        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        form = self._form()

        if path == "/login":
            user = form.get("user", "").strip()
            password = form.get("pass", "")
            rec = find_user(user)
            if (
                rec
                and rec.get("enabled", True)
                and verify_password(password, rec.get("password_hash", ""))
            ):
                users = load_users()
                for u in users:
                    if u.get("id") == rec.get("id"):
                        u["last_login"] = utc_now()
                        break
                save_users(users)
                token = make_session_token(user)
                return self._redirect(msg="Добро пожаловать", set_cookie=token)
            return self._need_auth("Неверный логин или пароль")

        if path == "/logout":
            return self._redirect(clear_cookie=True)

        if not self._auth_ok():
            if path.startswith("/api/"):
                return self._json(401, {"ok": False, "error": "auth required"})
            return self._need_auth()

        tab, gid = self._ctx(form)
        try:
            me = self._auth_user() or ""

            if path == "/api/users":
                return self._json(200, {"ok": True, "me": me, "users": [public_user(u) for u in load_users()]})

            if path == "/api/users/add":
                username = form.get("username", "").strip()
                password = form.get("password", "")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", username or ""):
                    return self._json(400, {"ok": False, "error": "Логин: 2–32 символа [A-Za-z0-9_.-]"})
                if len(password) < 6:
                    return self._json(400, {"ok": False, "error": "Пароль минимум 6 символов"})
                if find_user(username):
                    return self._json(400, {"ok": False, "error": "Логин занят"})
                users = load_users()
                users.append(
                    {
                        "id": str(uuid.uuid4())[:8],
                        "username": username,
                        "password_hash": hash_password(password),
                        "enabled": True,
                        "created_at": utc_now(),
                        "last_login": "",
                    }
                )
                save_users(users)
                log_activity(me, "user_add", f"создан {username}", ok=True)
                return self._json(200, {"ok": True, "users": [public_user(u) for u in users]})

            if path == "/api/users/password":
                uid = form.get("id", "").strip()
                password = form.get("password", "")
                if len(password) < 6:
                    return self._json(400, {"ok": False, "error": "Пароль минимум 6 символов"})
                users = load_users()
                found = False
                uname = ""
                for u in users:
                    if u.get("id") == uid:
                        u["password_hash"] = hash_password(password)
                        found = True
                        uname = u.get("username", "")
                        break
                if not found:
                    return self._json(400, {"ok": False, "error": "Пользователь не найден"})
                save_users(users)
                log_activity(me, "user_password", f"пароль изменён: {uname}", ok=True)
                return self._json(200, {"ok": True})

            if path == "/api/users/toggle":
                uid = form.get("id", "").strip()
                users = load_users()
                target = None
                for u in users:
                    if u.get("id") == uid:
                        target = u
                        break
                if not target:
                    return self._json(400, {"ok": False, "error": "Пользователь не найден"})
                if target.get("username") == me and target.get("enabled", True):
                    return self._json(400, {"ok": False, "error": "Нельзя заблокировать себя"})
                enabled_admins = [u for u in users if u.get("enabled", True)]
                if target.get("enabled", True) and len(enabled_admins) <= 1:
                    return self._json(400, {"ok": False, "error": "Нужен хотя бы один активный admin"})
                target["enabled"] = not target.get("enabled", True)
                save_users(users)
                state = "включён" if target["enabled"] else "заблокирован"
                log_activity(me, "user_toggle", f"{target.get('username')} → {state}", ok=True)
                return self._json(200, {"ok": True, "users": [public_user(u) for u in users]})

            if path == "/api/users/delete":
                uid = form.get("id", "").strip()
                users = load_users()
                target = next((u for u in users if u.get("id") == uid), None)
                if not target:
                    return self._json(400, {"ok": False, "error": "Пользователь не найден"})
                if target.get("username") == me:
                    return self._json(400, {"ok": False, "error": "Нельзя удалить себя"})
                if len(users) <= 1:
                    return self._json(400, {"ok": False, "error": "Нельзя удалить последнего пользователя"})
                uname = target.get("username", "")
                users = [u for u in users if u.get("id") != uid]
                if not any(u.get("enabled", True) for u in users):
                    return self._json(400, {"ok": False, "error": "Должен остаться активный admin"})
                save_users(users)
                log_activity(me, "user_delete", f"удалён {uname}", ok=True)
                return self._json(200, {"ok": True, "users": [public_user(u) for u in users]})

            if path == "/api/activity":
                return self._json(200, {"ok": True, "items": load_activity()[:80]})

            if path == "/api/prefs":
                return self._json(200, {"ok": True, "prefs": load_prefs()})

            if path == "/api/prefs/set":
                prefs = load_prefs()
                if "auto_ping" in form:
                    prefs["auto_ping"] = form.get("auto_ping") in ("1", "true", "yes", "on")
                if form.get("auto_ping_sec"):
                    try:
                        sec = int(form.get("auto_ping_sec") or 45)
                        prefs["auto_ping_sec"] = max(15, min(sec, 300))
                    except ValueError:
                        pass
                save_prefs(prefs)
                log_activity(me, "prefs", f"auto_ping={prefs.get('auto_ping')} sec={prefs.get('auto_ping_sec')}", ok=True)
                return self._json(200, {"ok": True, "prefs": prefs})

            if path == "/api/services/status":
                return self._json(200, services_status())

            if path == "/api/services/restart":
                name = (form.get("service") or "").strip().lower()
                try:
                    restart_service(name)
                    log_activity(me, "restart", f"restart {name}", ok=True)
                    return self._json(200, {"ok": True, "service": name, "status": services_status()})
                except Exception as ex:
                    log_activity(me, "restart", f"restart {name}: {ex}", ok=False)
                    return self._json(400, {"ok": False, "error": str(ex)})

            if path == "/api/xttp/status":
                return self._json(200, xttp_status())

            if path == "/api/xttp/ping":
                st = xttp_status()
                info = st.get("outbound") or {}
                host, port = info.get("address") or "", int(info.get("port") or 0)
                tcp = tcp_probe(host, port) if host and port else {"ok": False, "error": "no endpoint", "latency_ms": None}
                http = socks_http_probe()
                up = bool(st.get("xray_active") and tcp.get("ok") and http.get("ok"))
                return self._json(
                    200,
                    {
                        "ok": True,
                        "up": up,
                        "xray_active": st.get("xray_active"),
                        "mihomo_active": st.get("mihomo_active"),
                        "endpoint": f"{host}:{port}" if host else "",
                        "tcp": tcp,
                        "http": http,
                        "services": services_status(),
                    },
                )

            if path == "/api/xttp/speedtest":
                if not service_active("xray"):
                    return self._json(400, {"ok": False, "error": "xray не запущен"})
                result = socks_speedtest()
                return self._json(200, result)

            if path == "/api/xttp/preview":
                try:
                    parsed = parse_vless_link(form.get("vless", ""))
                    return self._json(
                        200,
                        {
                            "ok": True,
                            "preview": {
                                "name": parsed.get("name"),
                                "address": parsed.get("address"),
                                "port": parsed.get("port"),
                                "uuid_short": (parsed.get("uuid") or "")[:8] + "…",
                                "network": parsed.get("network"),
                                "security": parsed.get("security"),
                                "sni": parsed.get("sni"),
                                "path": parsed.get("path"),
                                "mode": parsed.get("mode"),
                                "public_key_short": ((parsed.get("public_key") or "")[:14] + "…")
                                if parsed.get("public_key")
                                else "",
                            },
                        },
                    )
                except Exception as ex:
                    return self._json(400, {"ok": False, "error": str(ex)})

            if path == "/api/xttp/apply":
                try:
                    parsed = parse_vless_link(form.get("vless", ""))
                    backup = backup_xray()
                    cfg = build_xray_config_from_vless(parsed)
                    write_xray_config(cfg)
                    restart_xray()
                    ping = socks_http_probe()
                    ep = f"{parsed['address']}:{parsed['port']}"
                    log_activity(
                        me,
                        "vless_apply",
                        f"{ep} backup={backup} http={'ok' if ping.get('ok') else 'fail'}",
                        ok=bool(ping.get("ok")),
                        extra={"backup": backup, "endpoint": ep},
                    )
                    return self._json(
                        200,
                        {
                            "ok": True,
                            "backup": backup,
                            "endpoint": ep,
                            "http_ok": ping.get("ok"),
                            "http": ping,
                            "status": xttp_status(),
                        },
                    )
                except Exception as ex:
                    log_activity(me, "vless_apply", str(ex), ok=False)
                    return self._json(400, {"ok": False, "error": str(ex)})

            if path == "/api/xttp/backups":
                return self._json(200, {"ok": True, "backups": list_xray_backups()})

            if path == "/api/xttp/rollback":
                try:
                    name = form.get("name", "").strip()
                    rollback_xray(name)
                    http = socks_http_probe()
                    log_activity(me, "vless_rollback", f"backup={name}", ok=bool(http.get("ok")))
                    return self._json(200, {"ok": True, "status": xttp_status(), "http": http})
                except Exception as ex:
                    log_activity(me, "vless_rollback", str(ex), ok=False)
                    return self._json(400, {"ok": False, "error": str(ex)})

            if path == "/api/detect":
                text = form.get("text", "")
                results = []
                for line in text.splitlines():
                    d = detect_entry(line)
                    if d:
                        results.append({"type": d["type"], "value": d["value"], "rule": d["rule"]})
                counts = {}
                for d in results:
                    counts[d["type"]] = counts.get(d["type"], 0) + 1
                return self._json(200, {"items": results, "counts": counts})

            if path == "/api/core/fleet-update":
                me = self._auth_user() or "admin"
                initiator = f"ручной ({me})"
                return self._json(200, fleet_run_script_async(initiator=initiator))
            if path == "/api/core/versions/update":
                me = self._auth_user() or "admin"
                if form.get("confirm") not in ("1", "true", "yes"):
                    return self._json(400, {"ok": False, "error": "confirm required"})
                comp = (form.get("component") or "").strip().lower()
                if comp == "panel":
                    return self._json(200, update_component_panel(user=me))
                return self._json(200, update_component_binary(comp, user=me))
            if path == "/api/core/geo-update":
                return self._json(200, core_geo_update_now())
            if path in ("/api/core", "/api/core/configs"):
                return self._json(200, core_configs_apply(form))
            if path == "/api/rules/overview":
                return self._json(200, rules_overview_snapshot(q=form.get("q") or ""))

            if path == "/api/explain-route":
                target = form.get("q") or form.get("target") or form.get("query") or ""
                return self._json(200, explain_route(target))

            if path == "/group-add":
                name = form.get("name", "").strip() or "New group"
                gid_new = str(uuid.uuid4())[:8]

                def _mut_add(gs, _name=name, _gid=gid_new):
                    gs.append(
                        {
                            "id": _gid,
                            "name": _name,
                            "enabled": True,
                            "entries": [],
                        }
                    )

                try:
                    update_groups("group_add", _mut_add)
                except Exception as ex:
                    log_activity(me or "admin", "group_add", str(ex), ok=False)
                    return self._redirect(err=str(ex))
                log_activity(me or "admin", "group_add", f"создана «{name}»", ok=True)
                return self._redirect(msg="Группа создана: " + name)

            if path == "/group-delete":
                gid_del = form.get("gid", "")
                box = {"gname": gid_del}

                def _mut_del(gs, _gid=gid_del, _box=box):
                    _box["gname"] = next((g.get("name") for g in gs if g.get("id") == _gid), _gid)
                    gs[:] = [g for g in gs if g.get("id") != _gid]

                try:
                    update_groups("group_delete", _mut_del, allow_shrink=True)
                except Exception as ex:
                    log_activity(me or "admin", "group_delete", str(ex), ok=False)
                    return self._redirect(err=str(ex))
                gname = box["gname"]
                try:
                    apply_config()
                    restart_mihomo()
                    log_activity(me or "admin", "group_delete", f"удалена «{gname}» + apply", ok=True)
                    return self._redirect(msg="Группа удалена, mihomo обновлён")
                except Exception as ex:
                    log_activity(me or "admin", "group_delete", f"«{gname}» UI ok, apply fail: {ex}", ok=False)
                    return self._redirect(
                        msg="Группа удалена из UI",
                        err="mihomo не обновлён: " + str(ex),
                    )

            if path == "/group-rename":
                gid_r = form.get("gid", "")
                new_name = form.get("name", "").strip()
                box = {"old": "", "new": ""}

                def _mut_ren(gs, _gid=gid_r, _nn=new_name, _box=box):
                    g = find_group(gs, _gid)
                    if not g:
                        raise RuntimeError("Группа не найдена")
                    _box["old"] = g.get("name", "")
                    g["name"] = _nn or g.get("name", "")
                    _box["new"] = g["name"]

                try:
                    update_groups("group_rename", _mut_ren)
                except Exception as ex:
                    return self._redirect(err=str(ex))
                log_activity(
                    me or "admin",
                    "group_rename",
                    f"«{box['old']}» → «{box['new']}»",
                    ok=True,
                )
                return self._redirect(msg="Имя группы обновлено (нажми Применить для mihomo)")

            if path == "/group-toggle":
                gid_t = form.get("gid", "")
                en_t = form.get("enabled") == "1"
                box = {"name": "", "enabled": en_t}

                def _mut_tog(gs, _gid=gid_t, _en=en_t, _box=box):
                    g = find_group(gs, _gid)
                    if not g:
                        raise RuntimeError("Группа не найдена")
                    g["enabled"] = _en
                    _box["name"] = g.get("name")
                    _box["enabled"] = g["enabled"]

                try:
                    update_groups("group_toggle", _mut_tog)
                except Exception as ex:
                    return self._redirect(err=str(ex))
                state = "включена" if box["enabled"] else "выключена"
                try:
                    apply_config()
                    restart_mihomo()
                    log_activity(
                        me or "admin",
                        "group_toggle",
                        f"«{box['name']}» {state} + apply",
                        ok=True,
                    )
                    return self._redirect(msg=f"Группа {state}, mihomo обновлён")
                except Exception as ex:
                    log_activity(
                        me or "admin",
                        "group_toggle",
                        f"«{box['name']}» {state}, apply fail: {ex}",
                        ok=False,
                    )
                    return self._redirect(
                        msg=f"Группа {state}",
                        err="mihomo не обновлён: " + str(ex),
                    )

            if path in ("/entry-add", "/api/entry-add"):
                gid_e = form.get("gid", "")
                raw = form.get("value", "").strip()
                etype = form.get("etype", "auto") or form.get("detect", "auto")
                det = force_type(raw, etype)
                if not det:
                    return self._json(400, {"ok": False, "error": "Не удалось распознать: " + raw})
                ename = form.get("ename", "").strip()
                if ename:
                    det["name"] = ename
                if "enabled" in form:
                    det["enabled"] = form.get("enabled") == "1"
                ensure_entry_id(det)
                box = {"err": None, "dup": False, "idx": -1, "count": 0, "gid": gid_e}

                def _mut_ea(gs, _gid=gid_e, _det=det, _box=box):
                    g = find_group(gs, _gid)
                    if not g:
                        _box["err"] = "Группа не найдена"
                        return
                    if any(e.get("rule") == _det["rule"] for e in g.get("entries") or []):
                        _box["dup"] = True
                        _box["err"] = "Уже есть: " + _det["value"]
                        return
                    g.setdefault("entries", []).append(_det)
                    _box["idx"] = len(g["entries"]) - 1
                    _box["count"] = len(g["entries"])
                    _box["gid"] = g["id"]

                try:
                    update_groups("entry_add", _mut_ea)
                except Exception as ex:
                    return self._json(400, {"ok": False, "error": str(ex)})
                if box["dup"]:
                    return self._json(200, {"ok": False, "error": box["err"], "dup": True})
                if box["err"]:
                    return self._json(400, {"ok": False, "error": box["err"]})
                return self._json(
                    200,
                    {
                        "ok": True,
                        "entry": det,
                        "index": box["idx"],
                        "count": box["count"],
                        "html": render_entry_row(box["gid"], box["idx"], det),
                    },
                )

            if path == "/entry-import":
                gid_i = form.get("gid", "")
                text_imp = form.get("text", "")
                mode = form.get("detect", "auto")
                box = {"added": 0, "err": None}

                def _mut_imp(gs, _gid=gid_i, _text=text_imp, _mode=mode, _box=box):
                    g = find_group(gs, _gid)
                    if not g:
                        _box["err"] = "Группа не найдена"
                        return
                    added = 0
                    for line in _text.splitlines():
                        det = detect_entry(line)
                        if not det:
                            continue
                        if _mode == "ipv4" and det["type"] not in ("ipv4", "ipv6"):
                            continue
                        if _mode == "namespace" and det["type"] not in ("namespace", "domain", "keyword"):
                            continue
                        if any(e.get("rule") == det["rule"] for e in g.get("entries") or []):
                            continue
                        ensure_entry_id(det)
                        g.setdefault("entries", []).append(det)
                        added += 1
                    _box["added"] = added

                try:
                    update_groups("entry_import", _mut_imp)
                except Exception as ex:
                    return self._redirect(err=str(ex))
                if box["err"]:
                    return self._redirect(err=box["err"])
                return self._redirect(msg=f"Импортировано: {box['added']}")

            if path in ("/entry-delete", "/api/entry-delete"):
                gid_d = form.get("gid", "")
                eid = form.get("eid", "").strip()
                i_raw = form.get("i", "-1")
                box = {"err": None, "removed": None, "count": 0}

                def _mut_ed(gs, _gid=gid_d, _eid=eid, _i_raw=i_raw, _box=box):
                    g = find_group(gs, _gid)
                    if not g:
                        _box["err"] = "Группа не найдена"
                        return
                    ents = g.get("entries") or []
                    removed = None
                    if _eid:
                        for i, e in enumerate(ents):
                            if e.get("id") == _eid:
                                removed = ents.pop(i)
                                break
                    else:
                        i = int(_i_raw)
                        if 0 <= i < len(ents):
                            removed = ents.pop(i)
                    if not removed:
                        _box["err"] = "Правило не найдено"
                        return
                    _box["removed"] = removed
                    _box["count"] = len(ents)

                try:
                    update_groups("entry_delete", _mut_ed, allow_shrink=True)
                except Exception as ex:
                    return self._json(400, {"ok": False, "error": str(ex)})
                if box["err"]:
                    return self._json(400, {"ok": False, "error": box["err"]})
                return self._json(
                    200,
                    {
                        "ok": True,
                        "removed": (box["removed"] or {}).get("value", ""),
                        "count": box["count"],
                    },
                )

            if path in ("/entry-update", "/api/entry-update"):
                gid_u = form.get("gid", "")
                eid = form.get("eid", "").strip()
                i_raw = form.get("i", "-1")
                raw = form.get("value", "").strip()
                etype = form.get("etype", "auto")
                ename = form.get("ename", "").strip()
                has_en = "enabled" in form
                en_val = form.get("enabled") == "1"
                box = {"err": None, "entry": None, "idx": -1}

                def _mut_eu(
                    gs,
                    _gid=gid_u,
                    _eid=eid,
                    _i_raw=i_raw,
                    _raw=raw,
                    _etype=etype,
                    _ename=ename,
                    _has_en=has_en,
                    _en_val=en_val,
                    _box=box,
                ):
                    g = find_group(gs, _gid)
                    if not g:
                        _box["err"] = "Группа не найдена"
                        return
                    ents = g.get("entries") or []
                    target = None
                    idx = -1
                    if _eid:
                        for i, e in enumerate(ents):
                            if e.get("id") == _eid:
                                target = e
                                idx = i
                                break
                    else:
                        i = int(_i_raw)
                        if 0 <= i < len(ents):
                            target = ents[i]
                            idx = i
                    if target is None:
                        _box["err"] = "Правило не найдено"
                        return
                    if not _raw:
                        if _has_en:
                            target["enabled"] = _en_val
                            _box["entry"] = target
                            _box["idx"] = idx
                            return
                        _box["err"] = "Пустой паттерн"
                        return
                    det = force_type(_raw, _etype)
                    if not det:
                        _box["err"] = "Не удалось распознать: " + _raw
                        return
                    det["id"] = target.get("id") or ensure_entry_id(det)
                    det["name"] = _ename
                    if _has_en:
                        det["enabled"] = _en_val
                    else:
                        det["enabled"] = target.get("enabled", True)
                    ents[idx] = det
                    _box["entry"] = det
                    _box["idx"] = idx

                try:
                    update_groups("entry_update", _mut_eu)
                except Exception as ex:
                    return self._json(400, {"ok": False, "error": str(ex)})
                if box["err"]:
                    return self._json(400, {"ok": False, "error": box["err"]})
                return self._json(200, {"ok": True, "entry": box["entry"], "index": box["idx"]})

            if path == "/apply":
                try:
                    apply_config()
                    restart_mihomo()
                    log_activity(me or "admin", "apply", "rule-providers + restart mihomo", ok=True)
                    return self._redirect(msg="Применено, mihomo перезапущен.")
                except Exception as ex:
                    log_activity(me or "admin", "apply", str(ex), ok=False)
                    return self._redirect(err=str(ex))

            self.send_error(404)
        except Exception as ex:
            return self._redirect(err=str(ex))



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


def fleet_apply_from_manifest(
    manifest: dict,
    repo: str,
    initiator: str = "автообновление (timer)",
) -> dict:
    """Compare to version-manifest; update mihomo/xray via update_component_binary; flag panel."""
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
        if want and have and _is_remote_newer(want, have):
            r = update_component_binary(comp, user=initiator)
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
    return report


def fleet_run_script_async(initiator: str = "ручной (UI)") -> dict:
    repo = os.environ.get("XTTP_GIT_REPO", XTTP_GIT_REPO)
    script = os.path.join(str(repo).rstrip("/\\"), "scripts", "check-and-update.sh")
    if not os.path.isfile(script):
        script = "/opt/xttp-stack/scripts/check-and-update.sh"
    if not os.path.isfile(script):
        return {"ok": False, "error": f"script not found: {script}"}
    env = os.environ.copy()
    env["XTTP_FLEET_INITIATOR"] = initiator
    env["XTTP_GIT_REPO"] = str(repo)
    try:
        subprocess.Popen(
            ["/bin/bash", script],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            log_activity(initiator, "fleet_trigger", "запущен check-and-update.sh", ok=True)
        except Exception:
            pass
        return {"ok": True, "started": True, "script": script}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


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
            if report.get("skipped"):
                return 0
            if report.get("panel_update"):
                return 10
            if not report.get("ok"):
                return 1
            return 0

        print(json.dumps({"ok": False, "error": f"unknown fleet cmd: {cmd}"}, ensure_ascii=False))
        return 1
    except Exception as ex:
        print(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False))
        return 1


def main():
    os.makedirs(os.path.dirname(GROUPS_FILE), exist_ok=True)
    os.makedirs(GROUPS_DIR, exist_ok=True)
    ensure_data_dir()
    start_traffic_sampler()  # 1 Hz traffic rates for stats chart
    start_log_sampler("info")  # mihomo /logs ring for Logs tab
    load_users()  # bootstrap admin
    session_secret()
    if not os.path.exists(GROUPS_FILE):
        load_groups()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("xttp panel http://%s:%s" % (HOST, PORT), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fleet-cmd":
        raise SystemExit(fleet_cli(sys.argv[1:]))
    main()
