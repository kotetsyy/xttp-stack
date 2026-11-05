import * as transport from "./system.js";
import * as rulesAPI from "./rules.js";
import * as monitoringAPI from "./monitoring.js";

(function(){
  const fetch = transport.panelFetch;
  // ensure no stuck full-screen overlay from prior session/DOM
  try {
    document.getElementById("verBackdrop")?.classList.remove("is-open");
    document.body.classList.remove("ver-changelog-open");
  } catch (e) {}
  // clean ?msg= / ?err= from address bar after toast is shown
  try {
    if (location.search && (location.search.includes("msg=") || location.search.includes("err="))) {
      history.replaceState(null, "", location.pathname || "/");
    }
  } catch (e) {}

  // auto-hide server-rendered toasts (e.g. «Добро пожаловать»)
  document.querySelectorAll(".toast").forEach(el => {
    const ms = el.classList.contains("toast-err") ? 5000 : 2800;
    setTimeout(() => {
      el.classList.add("toast-out");
      setTimeout(() => el.remove(), 400);
    }, ms);
  });

  // version changelog — robust for iOS (portal + is-open + global hook)
  
  // ── Connections view ───────────────────────────────────────────
  const connsTimer = monitoringAPI.createPoller(refreshConns, 2000);
  let connsCache = [];

  function stopConnsPolling(){
    connsTimer.stop();
  }
  function startConnsPolling(){
    if (monitoringVisible("conns")) connsTimer.start();
  }
  function fmtShortBytes(n){
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n/1024).toFixed(1) + " K";
    if (n < 1073741824) return (n/1048576).toFixed(1) + " M";
    return (n/1073741824).toFixed(2) + " G";
  }
  async function refreshConns(signal){
    if (!signal) return connsTimer.refresh();
    if (signal.aborted || !monitoringVisible("conns")) return;
    try {
      const d = await monitoringAPI.readSnapshot("/api/connections", signal);
      if (signal.aborted || !monitoringVisible("conns")) return;
      connsCache = d.connections || [];
      renderConnsTable();
      const meta = document.getElementById("connMeta");
      if (meta) {
        meta.textContent = "активных: " + (d.count ?? connsCache.length)
          + " · ↓ " + fmtShortBytes(d.downloadTotal || 0)
          + " · ↑ " + fmtShortBytes(d.uploadTotal || 0)
          + " · " + new Date().toLocaleTimeString();
      }
    } catch (e) {
      if (signal.aborted || !monitoringVisible("conns")) return;
      const body = document.getElementById("connsBody");
      if (body) body.innerHTML = `<tr><td colspan="7" class="stats-empty">${escHtml(e.message || String(e))}</td></tr>`;
      const meta = document.getElementById("connMeta");
      if (meta) meta.textContent = e.message || String(e);
    }
  }
  function renderConnsTable(){
    const body = document.getElementById("connsBody");
    if (!body) return;
    const q = ((searchInput && searchInput.value) || "").trim().toLowerCase();
    let rows = connsCache;
    if (q) {
      rows = rows.filter(c => {
        const hay = [c.host, c.rule, c.chain, c.source, c.dest, c.network, c.type].join(" ").toLowerCase();
        return hay.includes(q);
      });
    }
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="7" class="stats-empty">${q ? "Нет совпадений" : "Нет активных соединений"}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(c => {
      const proto = [c.network, c.type].filter(Boolean).join(" · ");
      return `<tr data-conn-id="${escHtml(c.id)}">
        <td><div class="conn-host">${escHtml(c.host || "—")}</div>
          <div class="meta" style="font-size:11px">${escHtml(c.source || "")} → ${escHtml(c.dest || "")}</div></td>
        <td>${escHtml(c.rule || "—")}</td>
        <td class="conn-chain">${escHtml(c.chain || "—")}</td>
        <td>${escHtml(proto || "—")}</td>
        <td class="conn-bytes">↓ ${fmtShortBytes(c.download)} · ↑ ${fmtShortBytes(c.upload)}</td>
        <td>${escHtml(c.duration || "—")}</td>
        <td><button type="button" class="btn btn-ghost conn-kill" data-kill-conn="${escHtml(c.id)}" title="Разорвать">✕</button></td>
      </tr>`;
    }).join("");
  }
  document.getElementById("btnRefreshConns")?.addEventListener("click", () => refreshConns());
  document.getElementById("btnKillAllConns")?.addEventListener("click", async () => {
    if (!confirm("Разорвать ВСЕ активные соединения?")) return;
    try {
      const res = await fetch("/api/connections", { method: "DELETE", credentials: "same-origin" });
      const d = await res.json();
      const ok = res.ok && d.ok === true;
      toast(ok ? "Все соединения разорваны" : (d.error || "fail"), ok);
      refreshConns();
    } catch (e) { toast(String(e), false); }
  });
  document.getElementById("panel-conns")?.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-kill-conn]");
    if (!b) return;
    const id = b.getAttribute("data-kill-conn");
    if (!id || !confirm("Разорвать это соединение?")) return;
    try {
      const res = await fetch("/api/connections/" + encodeURIComponent(id), { method: "DELETE", credentials: "same-origin" });
      const d = await res.json();
      const ok = res.ok && d.ok === true;
      toast(ok ? "Соединение разорвано" : (d.error || "fail"), ok);
      refreshConns();
    } catch (err) { toast(String(err), false); }
  });

  // ── Logs view ──────────────────────────────────────────────────
  const logsTimer = monitoringAPI.createPoller(refreshLogs, 1000);
  const logsCursor = monitoringAPI.createLogCursor();
  let logsLevel = "info";
  let logsMode = "tail"; // tail | live
  let logsLines = []; // {seq,type,payload,ts}
  let logsAutoScroll = true;

  function stopLogsPolling(){
    logsTimer.stop();
  }
  function startLogsPolling(){
    if (monitoringVisible("logs")) logsTimer.start();
  }
  function setLogsLevel(lvl){
    logsLevel = lvl || "info";
    document.querySelectorAll("#logsLevel .stab").forEach(b => {
      b.classList.toggle("active", b.getAttribute("data-log-level") === logsLevel);
    });
    // tell server preferred stream level (use debug if filter is debug, else info for volume)
    const streamLvl = logsLevel === "debug" ? "debug" : (logsLevel === "error" ? "warning" : "info");
    api("/api/logs/level", { level: streamLvl }).then(d => {
      if (d.ok !== true) throw new Error(d.error || "Не удалось изменить уровень логов");
    }).catch(e => {
      const meta = document.getElementById("logsMeta");
      if (meta && monitoringVisible("logs")) meta.textContent = e.message || String(e);
      toast(e.message || String(e), false);
    });
    renderLogsView();
    refreshLogs();
  }
  function setLogsMode(mode){
    logsMode = mode === "live" ? "live" : "tail";
    document.querySelectorAll("#logsMode .stab").forEach(b => {
      b.classList.toggle("active", b.getAttribute("data-log-mode") === logsMode);
    });
    stopLogsPolling();
    logsCursor.setMode(logsMode);
    logsLines = [];
    renderLogsView();
    startLogsPolling();
  }
  async function refreshLogs(signal){
    if (!signal) return logsTimer.refresh();
    if (signal.aborted || !monitoringVisible("logs")) return;
    try {
      const d = await monitoringAPI.readSnapshot(logsCursor.query(), signal);
      if (signal.aborted || !monitoringVisible("logs")) return;
      const state = logsCursor.consume(d);
      logsLines = state.items;
      renderLogsView();
      const meta = document.getElementById("logsMeta");
      if (meta) {
        meta.textContent = "буфер seq " + (d.seq || 0)
          + " · показано " + document.querySelectorAll("#logsView .log-line").length
          + " · уровень " + logsLevel
          + " · " + (logsMode === "live" ? "live" : "последние 200");
      }
    } catch (e) {
      if (signal.aborted || !monitoringVisible("logs")) return;
      const meta = document.getElementById("logsMeta");
      if (meta) meta.textContent = e.message || String(e);
    }
  }
  function renderLogsView(){
    const el = document.getElementById("logsView");
    if (!el) return;
    const q = ((searchInput && searchInput.value) || "").trim().toLowerCase();
    let rows = logsLines;
    if (logsLevel && logsLevel !== "all") {
      if (logsLevel === "warning") {
        rows = rows.filter(r => (r.type || "") === "warning" || (r.type || "") === "error");
      } else {
        rows = rows.filter(r => (r.type || "") === logsLevel);
      }
    }
    if (q) rows = rows.filter(r => String(r.payload || "").toLowerCase().includes(q));
    // newest first (time top→bottom was wrong; Meta-like reverse chrono)
    rows = rows.slice().reverse();
    const nearTop = el.scrollTop < 48;
    el.innerHTML = rows.map(r => {
      const lvl = (r.type || "info").toLowerCase();
      let ts = "";
      try {
        if (r.ts) ts = new Date(r.ts * 1000).toLocaleTimeString();
      } catch (_) {}
      return `<span class="log-line lvl-${escHtml(lvl)}"><span class="log-ts">${escHtml(ts)}</span>[${escHtml(lvl)}] ${escHtml(r.payload || "")}</span>`;
    }).join("") || '<span class="log-line lvl-info">Нет записей…</span>';
    if (logsAutoScroll && nearTop) el.scrollTop = 0;
  }
  document.getElementById("logsLevel")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-log-level]");
    if (b) setLogsLevel(b.getAttribute("data-log-level"));
  });
  document.getElementById("logsMode")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-log-mode]");
    if (b) setLogsMode(b.getAttribute("data-log-mode"));
  });
  document.getElementById("btnClearLogs")?.addEventListener("click", () => {
    logsLines = logsCursor.clear();
    renderLogsView();
  });
  document.getElementById("logsView")?.addEventListener("scroll", () => {
    const el = document.getElementById("logsView");
    if (!el) return;
    logsAutoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  });

  // search input also filters conns/logs (use getElementById — avoid TDZ before const searchInput)
  document.getElementById("searchInput")?.addEventListener("input", () => {
    if (currentView === "conns") renderConnsTable();
    if (currentView === "logs") renderLogsView();
  });


  (function setupVerChangelog(){
    const verWrap = document.querySelector(".ver-wrap");
    const verChip = document.getElementById("verChip");
    let verDd = document.getElementById("verDd");
    if (!verChip || !verDd || !verWrap) return;

    let open = false;
    let ignoreOutsideUntil = 0;
    let backdrop = document.getElementById("verBackdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "verBackdrop";
      backdrop.className = "ver-backdrop";
      backdrop.setAttribute("aria-hidden", "true");
      document.body.appendChild(backdrop);
    }
    const homeParent = verWrap;
    const homeNext = verDd.nextSibling;

    function isMobile(){
      return !!(window.matchMedia && window.matchMedia("(max-width:720px)").matches);
    }

    /** Position as fixed portal + real scroll metrics (desktop & mobile). */
    function placePanel(){
      const r = verChip.getBoundingClientRect();
      const mobile = isMobile();
      const gap = 8;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let top = Math.round(r.bottom + gap);
      let width = mobile ? Math.max(200, vw - 24) : Math.min(360, vw - 32);
      let left;
      if (mobile) {
        left = 12;
        width = vw - 24;
      } else {
        left = Math.round(r.left);
        if (left + width > vw - 12) left = Math.max(12, vw - 12 - width);
        if (left < 12) left = 12;
      }
      let maxH = Math.max(160, Math.min(420, vh - top - 16));
      // flip above chip if little space below
      let useBottom = false;
      let bottomPx = 0;
      if (maxH < 200 && r.top > vh - r.bottom) {
        useBottom = true;
        bottomPx = Math.round(vh - r.top + gap);
        maxH = Math.max(160, Math.min(420, r.top - gap - 16));
      }
      const parts = [
        "position:fixed",
        mobile ? "left:12px" : ("left:" + left + "px"),
        mobile ? "right:12px" : ("width:" + width + "px"),
        mobile ? "width:auto" : "",
        useBottom ? ("bottom:" + bottomPx + "px") : ("top:" + top + "px"),
        useBottom ? "top:auto" : "bottom:auto",
        "max-height:" + maxH + "px",
        "height:auto",
        "overflow-x:hidden",
        "overflow-y:auto",
        "overscroll-behavior:contain",
        "touch-action:pan-y",
        "-webkit-overflow-scrolling:touch",
        "z-index:10050",
        "opacity:1",
        "visibility:visible",
        "pointer-events:auto",
        "transform:none",
        "display:block",
        "box-sizing:border-box"
      ].filter(Boolean);
      verDd.style.cssText = parts.join(";");
    }

    function clearInline(){
      verDd.style.cssText = "";
    }

    function setOpen(next){
      open = !!next;
      verWrap.classList.toggle("open", open);
      verDd.classList.toggle("is-open", open);
      verChip.setAttribute("aria-expanded", open ? "true" : "false");
      backdrop.classList.toggle("is-open", open);
      backdrop.setAttribute("aria-hidden", open ? "false" : "true");
      document.body.classList.toggle("ver-changelog-open", open);
      if (open) { try { if (typeof loadChangelog === "function") loadChangelog(!window.__changelogLoaded); } catch(_) {} }

      if (open) {
        ignoreOutsideUntil = Date.now() + 400;
        // always portal to body so sticky topbar / overflow ancestors cannot steal wheel/touch
        if (verDd.parentElement !== document.body) {
          document.body.appendChild(verDd);
        }
        placePanel();
        // focus panel for a11y; don't steal if user is mid-gesture
        try { verDd.setAttribute("tabindex", "-1"); } catch (_) {}
      } else {
        clearInline();
        if (verDd.parentElement !== homeParent) {
          if (homeNext && homeNext.parentElement === homeParent) {
            homeParent.insertBefore(verDd, homeNext);
          } else {
            homeParent.appendChild(verDd);
          }
        }
      }
    }

    function toggle(e){
      if (e) {
        try { e.preventDefault(); } catch (_) {}
        try { e.stopPropagation(); } catch (_) {}
      }
      setOpen(!open);
      return false;
    }

    
    async function loadChangelog(force){
    const list = document.getElementById("verChangelogList");
    const loading = document.getElementById("verChangelogLoading");
    const foot = document.getElementById("verChangelogFoot");
    const link = document.getElementById("verChangelogLink");
    if (!list) return;
    if (loading) { loading.hidden = false; loading.textContent = "Загрузка changelog с GitHub…"; }
    try {
      const qs = force ? "?force=1" : "";
      const res = await fetch("/api/changelog" + qs, { credentials: "same-origin", cache: "no-store" });
      const d = await res.json();
      const esc = (typeof escHtml === "function") ? escHtml : function(s){
        return String(s == null ? "" : s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      };
      const items = d.items || [];
      if (!items.length) {
        list.innerHTML = `<div class="ver-item"><span class="ver-dot"></span><div><div class="ver-date">Пусто</div></div></div>`;
      } else {
        list.innerHTML = items.map(function(it){
          const cur = it.current ? " current" : "";
          const badge = it.current ? `<span class="ver-badge">CURRENT</span>` : "";
          const secs = (it.sections || []).map(function(sec){
            const fix = sec.fix ? " fix" : "";
            const lis = (sec.items || []).map(function(x){ return `<li>${esc(x)}</li>`; }).join("");
            return `<div class="ver-sec"><div class="ver-sec-title${fix}">${esc(sec.title || "")}</div><ul class="ver-list">${lis}</ul></div>`;
          }).join("");
          return `<div class="ver-item${cur}"><span class="ver-dot"></span><div>`
            + `<div class="ver-head"><span class="ver-tag">${esc(it.display || ("v" + it.version))}</span>${badge}</div>`
            + `<div class="ver-date">${esc(it.date || it.version || "")}</div>`
            + secs
            + `</div></div>`;
        }).join("");
      }
      if (foot && link && d.html_url) {
        link.href = d.html_url;
        foot.hidden = false;
      } else if (foot) foot.hidden = true;
      if (loading) loading.hidden = true;
      window.__changelogLoaded = true;
      window.__changelogSource = d.source || "";
    } catch (e) {
      if (loading) {
        loading.hidden = false;
        loading.textContent = "Ошибка загрузки changelog: " + (e && e.message ? e.message : e);
      }
    }
  }
    window.loadChangelog = loadChangelog;
  window.__toggleVerChangelog = toggle;
    window.__setVerChangelog = setOpen;

    verChip.addEventListener("click", toggle, true);
    backdrop.addEventListener("click", function(){ setOpen(false); });
    // backdrop must not eat wheel over the panel (panel is higher z-index);
    // block wheel on backdrop so page doesn't scroll under
    backdrop.addEventListener("wheel", function(e){
      e.preventDefault();
    }, { passive: false });
    backdrop.addEventListener("touchmove", function(e){
      e.preventDefault();
    }, { passive: false });

    // Guarantee wheel scrolls the list (not the page). Manual delta if needed.
    verDd.addEventListener("wheel", function(e){
      if (!open) return;
      e.stopPropagation();
      const el = verDd;
      const canScroll = el.scrollHeight > el.clientHeight + 1;
      if (!canScroll) {
        e.preventDefault();
        return;
      }
      const dy = e.deltaY;
      const top = el.scrollTop;
      const max = el.scrollHeight - el.clientHeight;
      const atTop = top <= 0;
      const atBottom = top >= max - 1;
      // always prevent body scroll chaining
      if ((dy < 0 && atTop) || (dy > 0 && atBottom)) {
        e.preventDefault();
        return;
      }
      // force scroll on the panel (fixes cases where browser ignores overflow:auto under sticky/fixed)
      e.preventDefault();
      el.scrollTop = Math.max(0, Math.min(max, top + dy));
    }, { passive: false });

    // touch: allow pan-y on panel; prevent body move when gesture is on panel
    verDd.addEventListener("touchmove", function(e){
      if (!open) return;
      e.stopPropagation();
    }, { passive: true });

    document.addEventListener("click", function(e){
      if (!open) return;
      if (Date.now() < ignoreOutsideUntil) return;
      const t = e.target;
      if (verChip.contains(t) || verDd.contains(t) || (backdrop && backdrop.contains(t))) return;
      setOpen(false);
    }, true);

    window.addEventListener("resize", function(){
      if (open) placePanel();
    }, { passive: true });
    window.addEventListener("orientationchange", function(){
      if (open) setTimeout(placePanel, 100);
    });
    window.addEventListener("scroll", function(){
      if (open) placePanel();
    }, { passive: true, capture: true });
  })();

  // Ask before revoking the session, while drafts can still be saved.
  document.getElementById("btnLogout")?.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirmDiscardChanges()) return;
    try {
      const d = await api("/logout", {});
      if (!d.ok) return toast(d.error || "Не удалось выйти", false);
      leavingPanel = true;
      window.location.replace("/login");
    } catch (error) { toast(String(error), false); }
  });

  const modalGroup = document.getElementById("modalGroup");
  const modalImport = document.getElementById("modalImport");
  const modalExplain = document.getElementById("modalExplain");
  const btnAdd = document.getElementById("btnAdd");
  const btnExplain = document.getElementById("btnExplain");
  const searchWrap = document.getElementById("searchWrap");
  const searchInput = document.getElementById("searchInput");
  const importText = document.getElementById("importText");
  const previewBox = document.getElementById("previewBox");
  const previewStats = document.getElementById("previewStats");
  const importGid = document.getElementById("importGid");

  function openModal(el){
    if (!el) return false;
    if ([modalGroup, modalImport, modalExplain].some(m => m !== el && m?.classList.contains("open")) && !closeAll()) return false;
    el.classList.add("open");
    el.setAttribute("aria-hidden","false");
    syncMonitoringVisibility();
    return true;
  }
  function closeAll(){
    const groupOpen = modalGroup?.classList.contains("open");
    const importOpen = modalImport?.classList.contains("open");
    if (!groupOpen && !importOpen && !modalExplain?.classList.contains("open")) return true;
    if (rulePending || transport.isBusy()) {
      toast("Дождитесь завершения текущего запросa", false);
      return false;
    }
    if ((groupOpen && document.getElementById("gname")?.value.trim()) || (importOpen && importText?.value.trim())) {
      if (!confirmDiscardChanges()) return false;
      if (groupOpen) modalGroup.querySelector("form")?.reset();
      if (importOpen) { importText.value = ""; importPreviewItems = []; }
    }
    [modalGroup, modalImport, modalExplain].forEach(m => {
      if (!m) return;
      m.classList.remove("open");
      m.setAttribute("aria-hidden","true");
    });
    syncMonitoringVisibility();
    return true;
  }
  function toast(msg, ok){
    document.querySelectorAll(".toast").forEach(t => t.remove());
    const el = document.createElement("div");
    el.className = "toast " + (ok ? "toast-ok" : "toast-err");
    el.setAttribute("role", ok ? "status" : "alert");
    el.textContent = msg;
    document.body.appendChild(el);
    const ms = ok ? 2800 : 5000;
    setTimeout(() => {
      el.classList.add("toast-out");
      setTimeout(() => el.remove(), 400);
    }, ms);
  }
  
  
  /** Core "?" tooltips: portal to body + viewport clamp/flip (escape modal containing-block). */
  function placeCoreTip(tip){
    if (!tip) return;
    let pop = tip.querySelector(".core-tip-pop");
    if (!pop && tip.dataset.tipId) {
      pop = document.querySelector('.core-tip-pop[data-for="' + tip.dataset.tipId + '"]');
    }
    if (!pop) return;

    // portal to body so position:fixed is relative to viewport (not settings-modal)
    if (!tip.dataset.tipId) {
      tip.dataset.tipId = "ct" + Math.random().toString(36).slice(2, 9);
    }
    pop.dataset.for = tip.dataset.tipId;
    if (pop.parentElement !== document.body) {
      document.body.appendChild(pop);
    }

    const margin = 10;
    const gap = 8;
    const r = tip.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const width = Math.min(260, Math.max(180, vw - margin * 2));

    tip.classList.add("is-tip-open");
    pop.style.display = "block";
    pop.style.position = "fixed";
    pop.style.width = width + "px";
    pop.style.maxWidth = (vw - margin * 2) + "px";
    pop.style.zIndex = "10060";
    pop.style.transform = "none";
    pop.style.margin = "0";
    pop.style.visibility = "hidden";
    pop.style.left = "0px";
    pop.style.top = "0px";
    pop.style.right = "auto";
    pop.style.bottom = "auto";

    const ph = Math.max(pop.offsetHeight || 0, 48);
    const pw = width;

    let left = Math.round(r.left);
    let top = Math.round(r.bottom + gap);

    // flip left if overflows right
    if (left + pw > vw - margin) {
      left = Math.round(r.right - pw);
    }
    if (left < margin) left = margin;
    if (left + pw > vw - margin) left = Math.max(margin, vw - margin - pw);

    // flip above if overflows bottom
    if (top + ph > vh - margin) {
      const above = Math.round(r.top - gap - ph);
      if (above >= margin) top = above;
      else top = Math.max(margin, Math.min(top, vh - margin - ph));
    }
    if (top < margin) top = margin;

    pop.style.left = left + "px";
    pop.style.top = top + "px";
    pop.style.visibility = "visible";
  }
  function hideCoreTip(tip){
    if (!tip) return;
    tip.classList.remove("is-tip-open");
    let pop = tip.querySelector(".core-tip-pop");
    if (!pop && tip.dataset.tipId) {
      pop = document.querySelector('.core-tip-pop[data-for="' + tip.dataset.tipId + '"]');
    }
    if (!pop) return;
    pop.style.display = "none";
    pop.style.visibility = "";
    // return home under the "?" for next open
    if (pop.parentElement === document.body) {
      tip.appendChild(pop);
    }
  }
  function setupCoreTips(){
    document.querySelectorAll(".core-tip").forEach((tip) => {
      if (tip.dataset.tipBound) return;
      tip.dataset.tipBound = "1";
      tip.addEventListener("mouseenter", () => placeCoreTip(tip));
      tip.addEventListener("focus", () => placeCoreTip(tip));
      tip.addEventListener("mouseleave", () => hideCoreTip(tip));
      tip.addEventListener("blur", () => hideCoreTip(tip));
    });
    if (!window.__coreTipRepoBound) {
      window.__coreTipRepoBound = true;
      const repo = () => {
        const open = document.querySelector(".core-tip.is-tip-open");
        if (open) placeCoreTip(open);
      };
      window.addEventListener("resize", repo);
      document.getElementById("settingsBd")?.addEventListener("scroll", repo, true);
      document.querySelector(".settings-body")?.addEventListener("scroll", repo, true);
    }
  }
  try { setupCoreTips(); } catch (_) {}
  try { window.placeCoreTip = placeCoreTip; window.hideCoreTip = hideCoreTip; } catch (_) {}

  async function loadCoreVersions(force){
    const el = document.getElementById("coreVersions");
    if (!el) return;
    try {
      const qs = force ? "?force=1" : "";
      const res = await fetch("/api/core/versions" + qs, { credentials: "same-origin", cache: "no-store" });
      const d = await res.json();
      if (!d.ok) {
        el.innerHTML = `<span class="core-ver-chip bad"><span class="cv-dot"></span>версии н/д</span>`;
        return;
      }
      const esc = (typeof escHtml === "function") ? escHtml : (s) => String(s ?? "");
      // only mihomo / xray / panel
      const items = (d.items || [d.mihomo, d.xray, d.panel].filter(Boolean))
        .filter(it => it && ["mihomo","xray","panel"].includes(it.label));
      window.__coreVerState = window.__coreVerState || {};
      el.innerHTML = items.map((it) => {
        const lab = it.label || "";
        const st = (window.__coreVerState[lab] || {});
        const ok = !!(it && it.ok);
        let cls = ok ? "ok" : "bad";
        let title = esc(lab);
        let badge = "";
        let dot = `<span class="cv-dot" aria-hidden="true"></span>`;
        if (st.checking) {
          cls = "checking";
          title = "Проверка…";
          dot = `<span class="cv-spin" aria-hidden="true"></span>`;
        } else if (st.error) {
          cls = "bad";
          title = "Не удалось проверить — повторить?";
        } else if (st.update_available) {
          cls = "warn";
          title = "Доступно обновление";
          badge = `<span class="cv-badge">↑ ${esc(st.latest || "")}</span>`;
        } else if (st.up_to_date) {
          cls = "ok";
          title = "Актуальная версия";
        }
        const text = st.checking ? "Проверка…" : esc((it && it.display) || "н/д");
        return `<button type="button" class="core-ver-chip ${cls}" data-comp="${esc(lab)}" title="${title}">${dot}<span class="cv-text">${text}</span>${badge}</button>`;
      }).join("");
    } catch (e) {
      el.innerHTML = `<span class="core-ver-chip bad"><span class="cv-dot"></span>версии н/д</span>`;
    }
  }

  window.__coreVerState = window.__coreVerState || {};
  let __coreUpdPayload = null;
  let updatePoll = null;
  let updateOperation = null;
  let updateGeneration = 0;

  function renderUpdateOperation(operation){
    updateOperation = operation;
    const names = {queued:"Принято · ожидает запуска", running:"Обновление выполняется", succeeded:"Обновление завершено", failed:"Обновление не удалось"};
    const title = document.getElementById("coreUpdTitle");
    const notes = document.getElementById("coreUpdNotes");
    const man = document.getElementById("coreUpdManual");
    const terminal = ["succeeded","failed"].includes(operation.status);
    const rollback = operation.rollback;
    const rollbackText = typeof rollback === "string" ? rollback : rollback ? JSON.stringify(rollback) : "нет данных";
    if (title) title.textContent = names[operation.status] || operation.status;
    if (notes) notes.textContent = [
      "Задание: " + operation.id,
      "Компонент: " + operation.component,
      "Этап: " + (operation.stage || "—"),
      "Восстановление: " + rollbackText,
      operation.error || "",
      operation.result == null ? "" : JSON.stringify(operation.result, null, 2),
    ].filter(Boolean).join("\n");
    if (man) {
      man.hidden = false;
      man.textContent = operation.status === "succeeded" ? "Сервер подтвердил конечный результат."
        : operation.status === "failed" ? "Задание завершилось с ошибкой. Проверьте этап и результат восстановления."
        : "Задание принято, но ещё не завершено. Перезапуск панели не означает успех.";
    }
    const btnDo = document.getElementById("btnCoreUpdDo");
    if (btnDo) btnDo.hidden = true;
    const actions = document.getElementById("coreUpdActions");
    const conf = document.getElementById("coreUpdConfirm");
    if (actions) actions.hidden = false;
    if (conf) conf.hidden = true;
    if (!terminal) return;
    try {
      if (localStorage.getItem("xttp-operation-id") === operation.id) localStorage.removeItem("xttp-operation-id");
    } catch (_) {}
    window.__coreVerState[operation.component] = {};
    loadCoreVersions(true);
    if (operation.component === "panel" && operation.status === "succeeded") {
      if (confirmDiscardChanges()) {
        reloadPanel();
      } else if (man) {
        man.textContent = "Обновление завершено. Открытая страница сохранена; обновите её после сохранения черновиков.";
      }
    }
  }

  function watchUpdateOperation(id, component){
    updatePoll?.stop();
    const generation = ++updateGeneration;
    updateOperation = {id, component, status:"queued", stage:"accepted"};
    renderUpdateOperation(updateOperation);
    updatePoll = transport.watchOperation(id, operation => {
      if (generation !== updateGeneration) return;
      renderUpdateOperation(operation);
    }, error => {
      if (generation !== updateGeneration) return;
      if ([401,404].includes(error.status)) updatePoll = null;
      if (error.status === 404) updateOperation = null;
      const man = document.getElementById("coreUpdManual");
      if (man) {
        man.hidden = false;
        man.textContent = "Задание " + id + ": " + error.message
          + ". Это не результат обновления; идентификатор сохранён."
          + ([401,404].includes(error.status) ? "" : " Ожидаем восстановления API.");
      }
    });
  }

  function resumeUpdateOperation(){
    let id = "";
    try { id = localStorage.getItem("xttp-operation-id") || ""; } catch (_) {}
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) return false;
    updateOperation = null;
    openCoreUpdModal({component:"", notes:"Получение сохранённого задания…"});
    watchUpdateOperation(id, "");
    return true;
  }

  function openCoreUpdModal(payload){
    updatePoll?.stop();
    updatePoll = null;
    updateGeneration++;
    __coreUpdPayload = payload;
    const bd = document.getElementById("modalCoreUpdate");
    if (!bd) return;
    // above settings scrim (z-index) + ensure visible in stacking context
    if (bd.parentElement !== document.body) document.body.appendChild(bd);
    bd.style.zIndex = "10060";
    const title = document.getElementById("coreUpdTitle");
    const vers = document.getElementById("coreUpdVersions");
    const notes = document.getElementById("coreUpdNotes");
    const man = document.getElementById("coreUpdManual");
    const link = document.getElementById("coreUpdLink");
    const linkWrap = document.getElementById("coreUpdLinkWrap");
    const actions = document.getElementById("coreUpdActions");
    const conf = document.getElementById("coreUpdConfirm");
    const btnDo = document.getElementById("btnCoreUpdDo");
    const _isPanel = payload.component === "panel";
    const _compTitle = _isPanel ? "панели (GitHub)" : (payload.component || "");
    const _hasUpd = !!payload.update_available;
    const _upToDate = !!payload.up_to_date && !_hasUpd;
    if (title) {
      title.textContent = _hasUpd
        ? ("Доступно обновление " + _compTitle)
        : (_upToDate ? ("Актуальная версия " + _compTitle) : ("Проверка " + _compTitle));
    }
    if (vers) {
      if (_hasUpd) vers.textContent = "v" + (payload.current || "?") + " → v" + (payload.latest || "?");
      else if (_upToDate) vers.textContent = "установлена v" + (payload.current || payload.latest || "?") + " · GitHub v" + (payload.latest || "?");
      else vers.textContent = "v" + (payload.current || "?") + " / remote v" + (payload.latest || "?");
    }
    if (notes) {
      notes.textContent = payload.notes
        || (_upToDate ? "Обновление не требуется — версия совпадает с GitHub." : "Нет краткого описания релиза.");
    }
    if (payload.manual_only) {
      if (man) { man.hidden = false; man.textContent = payload.manual_hint || "Обновите вручную на сервере."; }
      if (btnDo) btnDo.hidden = true;
    } else if (_hasUpd) {
      if (man) man.hidden = true;
      if (btnDo) { btnDo.hidden = false; btnDo.textContent = "Обновить"; }
    } else {
      if (man) {
        man.hidden = false;
        man.textContent = _isPanel
          ? "Панель совпадает с version-manifest.json на GitHub. Когда выкатите более новую версию в main — здесь появится кнопка «Обновить»."
          : "Уже актуальная версия. Обновление не требуется.";
      }
      if (btnDo) btnDo.hidden = true;
    }
    if (link && payload.html_url) {
      link.href = payload.html_url;
      if (linkWrap) linkWrap.hidden = false;
    } else if (linkWrap) linkWrap.hidden = true;
    const confText = document.getElementById("coreUpdConfirmText");
    if (confText) {
      confText.textContent = _isPanel
        ? "Панель установит точную опубликованную версию и перезапустится. Продолжить?"
        : "Сервис будет остановлен и перезапущен. Продолжить?";
    }
    if (actions) actions.hidden = false;
    if (conf) conf.hidden = true;
    bd.dataset.comp = payload.component || "";
    bd.style.zIndex = "10060";
    bd.style.pointerEvents = "auto";
    // settings scrim must not intercept clicks while update modal is open
    const sbd = document.getElementById("settingsBd");
    if (sbd) {
      if (!bd.classList.contains("open")) sbd.dataset.prevPe = sbd.style.pointerEvents || "";
      sbd.style.pointerEvents = "none";
    }
    if (bd.parentElement !== document.body) document.body.appendChild(bd);
    bd.classList.add("open");
    bd.setAttribute("aria-hidden", "false");
    syncMonitoringVisibility();
    if (updateOperation && !["succeeded","failed"].includes(updateOperation.status)) {
      watchUpdateOperation(updateOperation.id, updateOperation.component);
    }
  }
  function closeCoreUpdModal(){
    const bd = document.getElementById("modalCoreUpdate");
    if (!bd) return;
    updatePoll?.stop();
    updatePoll = null;
    updateGeneration++;
    bd.classList.remove("open");
    bd.setAttribute("aria-hidden", "true");
    // keep last payload.comp on dataset for retry; clear object
    const sbd = document.getElementById("settingsBd");
    if (sbd) {
      sbd.style.pointerEvents = sbd.dataset.prevPe || "";
      delete sbd.dataset.prevPe;
    }
    __coreUpdPayload = null;
    syncMonitoringVisibility();
  }
  async function checkCoreComponent(comp, showResult = true){
    if (!["panel","mihomo","xray"].includes(comp)) return;
    const dbg = (msg) => {
      const d = document.getElementById("coreVerDebug");
      if (!d) return;
      const t = new Date().toLocaleTimeString();
      d.textContent = "[" + t + "] " + msg;
      d.classList.add("has-msg");
      try { console.log("[core-ver]", t, msg); } catch (_) {}
    };
    window.__coreVerState = window.__coreVerState || {};
    window.__coreVerState[comp] = Object.assign({}, window.__coreVerState[comp] || {}, { checking: true, error: false });
    dbg(comp + ": запрос /api/core/versions/check …");
    if (typeof loadCoreVersions === "function") await loadCoreVersions(false);
    const t0 = Date.now();
    try {
      const url = "/api/core/versions/check?component=" + encodeURIComponent(comp) + "&force=1";
      dbg(comp + ": GET " + url);
      const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      const d = await res.json();
      // min spinner visibility ~450ms so user sees "Проверка..."
      const wait = Math.max(0, 450 - (Date.now() - t0));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      if (!d.ok) {
        window.__coreVerState[comp] = { error: true, checking: false };
        dbg(comp + ": ошибка — " + (d.error || res.status));
        if (typeof toast === "function") toast((comp) + ": не удалось проверить", false);
        if (typeof loadCoreVersions === "function") await loadCoreVersions(false);
        return;
      }
      window.__coreVerState[comp] = {
        checking: false,
        error: false,
        update_available: !!d.update_available,
        up_to_date: !!d.up_to_date,
        latest: d.latest,
        current: d.current,
        notes: d.notes,
        html_url: d.html_url,
        manual_only: !!d.manual_only,
        manual_hint: d.manual_hint,
        component: d.component || comp,
      };
      if (typeof loadCoreVersions === "function") await loadCoreVersions(false);
      const _nm = (comp === "panel") ? "панель" : comp;
      if (d.update_available) {
        dbg(_nm + ": есть обновление v" + (d.current || "?") + " → v" + (d.latest || "?"));
        if (typeof toast === "function") toast(_nm + ": доступна v" + (d.latest || ""));
      } else if (d.up_to_date) {
        dbg(_nm + ": актуальная v" + (d.current || d.latest || ""));
        if (typeof toast === "function") toast(_nm + ": актуальная версия");
      } else {
        dbg(_nm + ": ответ без флага update/up_to_date");
      }
      // always show result modal (update OR up-to-date)
      if (showResult && typeof openCoreUpdModal === "function") openCoreUpdModal(window.__coreVerState[comp]);
    } catch (e) {
      const wait = Math.max(0, 450 - (Date.now() - t0));
      if (wait) await new Promise((r) => setTimeout(r, wait));
      window.__coreVerState[comp] = { error: true, checking: false };
      dbg(comp + ": сеть/исключение — " + (e && e.message ? e.message : e));
      if (typeof toast === "function") toast(String(e), false);
      if (typeof loadCoreVersions === "function") await loadCoreVersions(false);
    }
  }
  function setupCoreVersionChips(){
    const el = document.getElementById("coreVersions");
    if (!el || el.dataset.bound) return;
    el.dataset.bound = "1";
    el.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-comp]");
      if (!chip) return;
      const comp = chip.getAttribute("data-comp");
      const st = (window.__coreVerState || {})[comp] || {};
      if (st.checking) return;
      // if already know update — open modal; else re-check (also for error retry)
      if (st.update_available && !st.error) {
        openCoreUpdModal(Object.assign({ component: comp }, st));
        return;
      }
      checkCoreComponent(comp);
    });
  }
  // Robust document-level handlers (modal may be reparented to <body>; settings scrim must not steal clicks)
  if (!window.__coreUpdDelegated) {
    window.__coreUpdDelegated = true;
    document.addEventListener("click", (e) => {
      const t = e.target;
      if (!t || !t.closest) return;
      const bd = document.getElementById("modalCoreUpdate");
      if (!bd || !bd.classList.contains("open")) return;

      // close on backdrop / [data-close]
      if (t.id === "modalCoreUpdate" || t.closest("#modalCoreUpdate [data-close]")) {
        e.preventDefault();
        e.stopPropagation();
        closeCoreUpdModal();
        return;
      }

      const btnDo = t.closest("#btnCoreUpdDo");
      if (btnDo) {
        e.preventDefault();
        e.stopPropagation();
        const actions = document.getElementById("coreUpdActions");
        const conf = document.getElementById("coreUpdConfirm");
        if (actions) actions.hidden = true;
        if (conf) conf.hidden = false;
        if (typeof toast === "function") toast("Подтвердите обновление");
        return;
      }

      const btnCancel = t.closest("#btnCoreUpdCancel");
      if (btnCancel) {
        e.preventDefault();
        e.stopPropagation();
        const actions = document.getElementById("coreUpdActions");
        const conf = document.getElementById("coreUpdConfirm");
        if (actions) actions.hidden = false;
        if (conf) conf.hidden = true;
        return;
      }

      const btnOk = t.closest("#btnCoreUpdConfirm");
      if (btnOk) {
        e.preventDefault();
        e.stopPropagation();
        if (btnOk.dataset.busy === "1") return;
        if (updateOperation && !["succeeded","failed"].includes(updateOperation.status)) {
          return toast("Сначала дождитесь результата текущего задания", false);
        }
        const p = __coreUpdPayload || {};
        const comp = p.component || bd.dataset.comp || "";
        if (!comp) {
          if (typeof toast === "function") toast("Нет данных обновления — закройте и нажмите чип снова", true);
          else alert("Нет данных обновления");
          return;
        }
        (async () => {
          btnOk.dataset.busy = "1";
          btnOk.disabled = true;
          btnOk.textContent = "Обновление…";
          if (typeof toast === "function") toast("Обновление " + (comp === "panel" ? "панели" : comp) + "…");
          try {
            if (!p.update_available || p.manual_only || !p.latest) {
              return toast("Сначала проверьте точную опубликованную версию", false);
            }
            if (comp === "panel" && hasUnsavedChanges()
                && !confirm("Панель перезапустится. После завершения открытая страница с черновиками останется до вашего подтверждения перезагрузки. Продолжить?")) return;
            const generation = updateGeneration;
            const d = await transport.startUpdate(comp, p.latest);
            if (d.operation_id) {
              try { localStorage.setItem("xttp-operation-id", d.operation_id); } catch (_) {}
            }
            if (!d.ok) {
              if (generation === updateGeneration && d.operation_id) watchUpdateOperation(d.operation_id, comp);
              return toast(d.error || "Не удалось запустить обновление", false);
            }
            if (!d.operation_id) return toast("Сервер не вернул идентификатор задания", false);
            updateOperation = {id:d.operation_id, component:comp, status:"queued", stage:"accepted"};
            toast("Задание принято · обновление ещё не завершено", true);
            if (generation === updateGeneration && bd.classList.contains("open")) {
              watchUpdateOperation(d.operation_id, comp);
            }
          } catch (err) {
            if (typeof toast === "function") toast(String(err && err.message ? err.message : err), false);
            else alert(String(err));
          } finally {
            btnOk.dataset.busy = "0";
            btnOk.disabled = false;
            btnOk.textContent = "Да, обновить";
            const actions = document.getElementById("coreUpdActions");
            const conf = document.getElementById("coreUpdConfirm");
            if (actions) actions.hidden = false;
            if (conf) conf.hidden = true;
          }
        })();
      }
    }, true);
  }
  
  document.getElementById("btnFleetUpdateNow")?.addEventListener("click", async () => {
    const btn = document.getElementById("btnFleetUpdateNow");
    if (btn) { btn.disabled = true; btn.textContent = "Проверка…"; }
    try {
      for (const comp of ["panel","mihomo","xray"]) await checkCoreComponent(comp, false);
      const available = ["panel","mihomo","xray"].find(comp => window.__coreVerState[comp]?.update_available);
      if (available) openCoreUpdModal(window.__coreVerState[available]);
      else if (["panel","mihomo","xray"].some(comp => window.__coreVerState[comp]?.error)) {
        toast("Проверка завершилась с ошибкой; обновления не запускались", false);
      } else toast("Проверка завершена; доступных обновлений нет", true);
    } catch (error) { toast(String(error), false); }
    finally {
      if (btn) { btn.disabled = false; btn.textContent = "Проверить обновления сейчас"; }
    }
  });

  try {
    window.checkCoreComponent = checkCoreComponent;
    window.setupCoreVersionChips = setupCoreVersionChips;
    window.openCoreUpdModal = openCoreUpdModal;
    window.loadCoreVersions = loadCoreVersions;
    setupCoreVersionChips();
  } catch (_) {}
  // document-level fallback — works even if #coreVersions was rebound/replaced
  if (!window.__coreVerClickDelegated) {
    window.__coreVerClickDelegated = true;
    document.addEventListener("click", (e) => {
      const chip = e.target && e.target.closest && e.target.closest("#coreVersions [data-comp]");
      if (!chip) return;
      e.preventDefault();
      e.stopPropagation();
      const comp = chip.getAttribute("data-comp");
      if (!comp) return;
      const st = (window.__coreVerState || {})[comp] || {};
      if (st.checking) return;
      if (st.update_available && !st.error && typeof openCoreUpdModal === "function") {
        openCoreUpdModal(Object.assign({ component: comp }, st));
        return;
      }
      if (typeof checkCoreComponent === "function") checkCoreComponent(comp);
      else console.error("[core-ver] checkCoreComponent missing");
    }, true);
  }



  async function loadCoreSettings(discard = false){
    if (discard && hasCoreDrafts() && !confirm("Загрузить настройки из ядра? Несохранённые runtime-значения будут потеряны.")) return;
    const startedValues = JSON.stringify(coreValues());
    if (!coreReady) setCoreLoading(true);
    const meta = document.getElementById("coreMeta");
    const portsEl = document.getElementById("corePorts");
    const showPortsErr = (msg) => {
      if (!portsEl) return;
      const safe = (typeof escHtml === "function") ? escHtml(msg) : String(msg || "ошибка");
      portsEl.innerHTML = `<div class="core-ports-err"><span>Не удалось загрузить порты: ${safe}</span>`
        + `<button type="button" class="btn btn-ghost" id="btnCorePortsRetry" style="padding:6px 12px;font-size:12.5px">Повторить</button></div>`;
      document.getElementById("btnCorePortsRetry")?.addEventListener("click", () => loadCoreSettings());
    };
    const fmtPort = (v) => {
      if (v === 0 || v === "0") return { text: "выключен", off: true };
      if (v == null || v === "") return { text: "—", off: true };
      return { text: String(v), off: false };
    };
    const renderPorts = (ports, c) => {
      if (!portsEl) return;
      const src = ports || {};
      const rows = [
        ["Mixed", src["mixed-port"] ?? c["mixed-port"]],
        ["HTTP", src.port ?? c.port],
        ["SOCKS", src["socks-port"] ?? c["socks-port"]],
        ["Redir", src["redir-port"] ?? c["redir-port"]],
        ["TProxy", src["tproxy-port"] ?? c["tproxy-port"]],
      ];
      const esc = (typeof escHtml === "function") ? escHtml : (s) => String(s ?? "");
      portsEl.innerHTML = `<div class="core-ports-list">` + rows.map(([lab, v]) => {
        const p = fmtPort(v);
        return `<div class="core-port-row"><div class="cp-l">${esc(lab)}</div>`
          + `<div class="cp-n${p.off ? " is-off" : ""}">${esc(p.text)}</div></div>`;
      }).join("") + `</div>`;
    };
    try {
      if (portsEl && !portsEl.dataset.loading) {
        /* keep previous ports if any; only show loading if empty */
        if (!portsEl.querySelector(".core-ports-list") && !portsEl.querySelector(".core-ports-err")) {
          portsEl.innerHTML = `<div class="core-ports-err" style="background:transparent;border-color:var(--line);color:var(--muted)">Загрузка портов…</div>`;
        }
      }
      if (typeof loadCoreVersions === "function") loadCoreVersions(false);
      const res = await fetch("/api/core/configs", { credentials: "same-origin", cache: "no-store" });
      let d = null;
      const ct = (res.headers.get("content-type") || "");
      if (ct.includes("application/json")) {
        d = await res.json();
      } else {
        const t = await res.text();
        throw new Error(res.status === 401 ? "нужен вход" : ("HTTP " + res.status + (t ? ": " + t.slice(0, 80) : "")));
      }
      if (!res.ok || !d || !d.ok) {
        const err = (d && d.error) || ("HTTP " + res.status);
        if (meta) meta.textContent = "ошибка: " + err;
        showPortsErr(err);
        return;
      }
      const c = d.configs || {};
      const ports = d.ports || {};
      const keepDraft = (!discard && hasCoreDrafts()) || JSON.stringify(coreValues()) !== startedValues;
      const setSel = (id, v) => {
        const el = document.getElementById(id);
        if (!keepDraft && el && v != null && v !== "") el.value = String(v);
      };
      const setChk = (id, v) => {
        const el = document.getElementById(id);
        if (!keepDraft && el) el.checked = !!v;
      };
      setSel("coreMode", c.mode || "rule");
      setSel("coreLogLevel", c["log-level"] || "info");
      setSel("coreFindProcess", c["find-process-mode"] || "off");
      setChk("coreAllowLan", c["allow-lan"]);
      setChk("coreIpv6", c.ipv6);
      setChk("coreUnifiedDelay", c["unified-delay"]);
      setChk("coreTcpConcurrent", c["tcp-concurrent"]);
      setChk("coreSniffing", c.sniffing != null ? c.sniffing : d.sniffing);
      setChk("coreGeoAuto", c["geo-auto-update"] != null ? c["geo-auto-update"] : d.geo_auto_update);
      const geoInt = c["geo-update-interval"] != null ? c["geo-update-interval"] : d.geo_update_interval;
      const geoIntEl = document.getElementById("coreGeoInterval");
      if (!keepDraft && geoIntEl && geoInt != null && geoInt !== "") geoIntEl.value = String(geoInt);
      const geoWrap = document.getElementById("coreGeoIntervalWrap");
      if (geoWrap) geoWrap.hidden = !(document.getElementById("coreGeoAuto")?.checked);
      if (!keepDraft) coreBaseline = coreValues();
      // TUN read-only card
      const tunEl = document.getElementById("coreTun");
      if (tunEl) {
        const tun = (d.tun && typeof d.tun === "object") ? d.tun : (c.tun || {});
        const esc = (typeof escHtml === "function") ? escHtml : (s) => String(s ?? "");
        const yn = (v) => (v ? "Да" : "Нет");
        const on = !!tun.enable;
        let addr = tun["inet4-address"];
        if (Array.isArray(addr)) addr = addr.filter(Boolean).join(", ") || "—";
        else if (addr == null || addr === "") addr = "—";
        const cells = [
          ["Статус", `<span class="core-tun-status"><span class="core-tun-dot${on ? " on" : ""}"></span>${on ? "Включён" : "Выключен"}</span>`],
          ["Устройство", esc(tun.device || "—")],
          ["Стек", esc(tun.stack || "—")],
          ["Авто-маршрут", yn(!!tun["auto-route"])],
          ["Авто-редирект", yn(!!tun["auto-redirect"])],
          ["Адрес", esc(String(addr))],
        ];
        tunEl.innerHTML = cells.map(([lab, val]) =>
          `<div class="core-tun-row"><div class="cp-l">${esc(lab)}</div><div class="cp-n">${val}</div></div>`
        ).join("");
      }
      // geo sources short line
      const geoSrc = document.getElementById("coreGeoSources");
      if (geoSrc) {
        const gu = (d.geox_url && typeof d.geox_url === "object") ? d.geox_url : (c["geox-url"] || {});
        const hasMeta = JSON.stringify(gu).includes("MetaCubeX") || JSON.stringify(gu).includes("meta-rules");
        geoSrc.textContent = hasMeta
          ? "geoip.dat, geosite.dat — MetaCubeX/meta-rules-dat"
          : "geoip.dat, geosite.dat — источники из geox-url";
      }
      const btnGeo = document.getElementById("btnGeoUpdateNow");
      if (btnGeo) btnGeo.hidden = (d.geo_update_api === false);
      renderPorts(ports, c);
      if (meta) {
        meta.textContent = "mode " + (c.mode || "—")
          + " · log " + (c["log-level"] || "—")
          + " · tun " + ((c.tun && c.tun.enable) || (d.tun && d.tun.enable) ? "on" : "off")
          + " · " + new Date().toLocaleTimeString();
      }
      coreReady = true;
      setCoreLoading(false);
    } catch (e) {
      const msg = (e && e.message) ? e.message : String(e);
      if (meta) meta.textContent = "ошибка загрузки: " + msg;
      showPortsErr(msg);
    }
  }
  async function saveCoreSettings(){
    if (!coreReady) return toast("Сначала дождитесь загрузки настроек ядра", false);
    const body = {
      mode: document.getElementById("coreMode")?.value || "rule",
      "log-level": document.getElementById("coreLogLevel")?.value || "info",
      "find-process-mode": document.getElementById("coreFindProcess")?.value || "off",
      "allow-lan": document.getElementById("coreAllowLan")?.checked ? "true" : "false",
      ipv6: document.getElementById("coreIpv6")?.checked ? "true" : "false",
      "unified-delay": document.getElementById("coreUnifiedDelay")?.checked ? "true" : "false",
      "tcp-concurrent": document.getElementById("coreTcpConcurrent")?.checked ? "true" : "false",
      sniffing: document.getElementById("coreSniffing")?.checked ? "true" : "false",
      "geo-auto-update": document.getElementById("coreGeoAuto")?.checked ? "true" : "false",
    };
    const geoIv = document.getElementById("coreGeoInterval")?.value;
    if (geoIv != null && String(geoIv).trim() !== "") body["geo-update-interval"] = String(geoIv).trim();
    const submittedValues = coreValues();
    try {
      const d = await api("/api/core/configs", body);
      if (!d.ok) {
        if (typeof toast === "function") toast(d.error || "ошибка ядра", false);
        else alert(d.error || "ошибка ядра");
        return;
      }
      coreBaseline = submittedValues;
      if (typeof toast === "function") toast("Ядро обновлено");
      await loadCoreSettings();
    } catch (e) {
      if (typeof toast === "function") toast(String(e), false);
    }
  }
  document.getElementById("btnCoreSave")?.addEventListener("click", () => saveCoreSettings());
  document.getElementById("btnCoreRefresh")?.addEventListener("click", () => loadCoreSettings(true));
  document.getElementById("coreGeoAuto")?.addEventListener("change", () => {
    const wrap = document.getElementById("coreGeoIntervalWrap");
    if (wrap) wrap.hidden = !document.getElementById("coreGeoAuto")?.checked;
  });
  document.getElementById("btnGeoIntervalSave")?.addEventListener("click", async () => {
    const hours = document.getElementById("coreGeoInterval")?.value || "24";
    const submittedGeo = coreValues();
    try {
      const d = await api("/api/core/configs", {
        "geo-auto-update": document.getElementById("coreGeoAuto")?.checked ? "true" : "false",
        "geo-update-interval": String(hours),
      });
      if (!d.ok) {
        if (typeof toast === "function") toast(d.error || "ошибка geo", false);
        return;
      }
      coreBaseline = {...coreBaseline, coreGeoAuto:submittedGeo.coreGeoAuto, coreGeoInterval:submittedGeo.coreGeoInterval};
      if (typeof toast === "function") toast("Интервал geo сохранён");
      await loadCoreSettings();
    } catch (e) {
      if (typeof toast === "function") toast(String(e), false);
    }
  });
  document.getElementById("btnGeoUpdateNow")?.addEventListener("click", async () => {
    const btn = document.getElementById("btnGeoUpdateNow");
    if (btn) { btn.disabled = true; btn.textContent = "Обновление…"; }
    try {
      const d = await api("/api/core/geo-update", {});
      if (!d.ok) {
        if (typeof toast === "function") toast(d.error || "ошибка geo update", false);
        else alert(d.error || "ошибка geo update");
      } else {
        if (typeof toast === "function") toast("Geo-базы: обновление запущено");
      }
    } catch (e) {
      if (typeof toast === "function") toast(String(e), false);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "Обновить сейчас"; }
    }
  });

  /* Rules overview */
  let rovCache = [];
  function openRulesOverview(){
    const bd = document.getElementById("modalRulesOverview");
    if (!bd) return;
    bd.classList.add("open");
    bd.setAttribute("aria-hidden", "false");
    syncMonitoringVisibility();
    loadRulesOverview();
    setTimeout(() => document.getElementById("rovSearch")?.focus(), 80);
  }
  function closeRulesOverview(){
    const bd = document.getElementById("modalRulesOverview");
    if (!bd) return;
    bd.classList.remove("open");
    bd.setAttribute("aria-hidden", "true");
    syncMonitoringVisibility();
  }
  function renderRulesOverview(items){
    const body = document.getElementById("rovBody");
    const meta = document.getElementById("rovMeta");
    if (!body) return;
    const q = ((document.getElementById("rovSearch") && document.getElementById("rovSearch").value) || "").trim().toLowerCase();
    let rows = items || rovCache;
    if (q) {
      rows = rows.filter(r => {
        const hay = [r.group_name, r.type, r.value, r.rule, r.name].join(" ").toLowerCase();
        return hay.includes(q);
      });
    }
    if (meta) meta.textContent = "показано " + rows.length + " из " + (items || rovCache).length;
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" class="rov-empty">${q ? "Нет совпадений" : "Правил нет"}</td></tr>`;
      return;
    }
    const typeLab = {ipv4:"IPV4",ipv6:"IPV6",namespace:"NS",domain:"DOM",keyword:"KW",raw:"RAW"};
    const typeCls = {ipv4:"badge-v4",ipv6:"badge-v6",namespace:"badge-ns",domain:"badge-dom",keyword:"badge-kw",raw:"badge-raw"};
    body.innerHTML = rows.map(r => {
      const off = (!r.enabled || !r.group_enabled) ? " off" : "";
      const t = r.type || "raw";
      const val = r.value || r.rule || "—";
      return `<tr class="${off}" data-gid="${escHtml(r.group_id || "")}" title="${escHtml(r.rule || "")}">
        <td>${escHtml(r.group_name || "—")}${r.group_enabled ? "" : ' <span class="meta">off</span>'}</td>
        <td><span class="pill ${typeCls[t] || "badge-raw"}">${escHtml(typeLab[t] || t)}</span></td>
        <td class="rov-val">${escHtml(val)}</td>
        <td>${r.enabled ? "✓" : "—"}</td>
      </tr>`;
    }).join("");
  }
  async function loadRulesOverview(){
    const body = document.getElementById("rovBody");
    if (body) body.innerHTML = `<tr><td colspan="4" class="rov-empty">Загрузка…</td></tr>`;
    try {
      const res = await fetch("/api/rules/overview", { credentials: "same-origin" });
      const d = await res.json();
      if (!d.ok) {
        if (body) body.innerHTML = `<tr><td colspan="4" class="rov-empty">${escHtml(d.error || "ошибка")}</td></tr>`;
        return;
      }
      rovCache = d.items || [];
      renderRulesOverview(rovCache);
    } catch (e) {
      if (body) body.innerHTML = `<tr><td colspan="4" class="rov-empty">сеть</td></tr>`;
    }
  }
  function jumpToGroup(gid){
    if (!gid) return;
    closeRulesOverview();
    if (typeof setView === "function") setView("rules");
    const card = document.querySelector(`.gcard[data-gid="${CSS.escape(gid)}"]`);
    if (!card) return;
    card.classList.remove("collapsed");
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("gcard-flash");
    setTimeout(() => card.classList.remove("gcard-flash"), 1200);
  }
  document.getElementById("btnRulesOverview")?.addEventListener("click", openRulesOverview);
  document.getElementById("btnRovRefresh")?.addEventListener("click", loadRulesOverview);
  document.getElementById("rovSearch")?.addEventListener("input", () => renderRulesOverview(rovCache));
  document.getElementById("rovBody")?.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-gid]");
    if (tr) jumpToGroup(tr.getAttribute("data-gid"));
  });
  document.getElementById("modalRulesOverview")?.addEventListener("click", (e) => {
    if (e.target.id === "modalRulesOverview" || e.target.closest("[data-close]")) closeRulesOverview();
  });


  const coreFieldIds = ["coreMode","coreLogLevel","coreFindProcess","coreAllowLan","coreIpv6","coreUnifiedDelay","coreTcpConcurrent","coreSniffing","coreGeoAuto","coreGeoInterval"];
  function coreValues(){
    return Object.fromEntries(coreFieldIds.map(id => {
      const input = document.getElementById(id);
      return [id, input?.type === "checkbox" ? input.checked : input?.value || ""];
    }));
  }
  let coreBaseline = coreValues();
  let coreReady = false;
  function setCoreLoading(loading){
    for (const id of [...coreFieldIds, "btnCoreSave", "btnGeoIntervalSave", "btnGeoUpdateNow"]) {
      const control = document.getElementById(id);
      if (control) control.disabled = loading;
    }
  }
  let leavingPanel = false;
  function hasCoreDrafts(){
    return JSON.stringify(coreValues()) !== JSON.stringify(coreBaseline);
  }
  function hasUnsavedChanges(){
    return transport.isBusy() || hasCoreDrafts()
      || !!document.getElementById("vlessIn")?.value.trim()
      || (typeof hasRuleDrafts === "function" && hasRuleDrafts());
  }
  function confirmDiscardChanges(){
    if (transport.isBusy()) {
      toast("Дождитесь завершения текущего запроса", false);
      return false;
    }
    return !hasUnsavedChanges() || confirm("Несохранённые изменения будут потеряны. Продолжить?");
  }
  function reloadPanel(){
    leavingPanel = true;
    location.reload();
  }
  window.addEventListener("beforeunload", event => {
    if (leavingPanel || !hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("panel-auth-required", () => {
    toast("Сессия завершена. Черновики остаются в этой странице; откройте вход в другой вкладке и вернитесь сюда.", false);
  });
  async function api(path, data){
    try { return await transport.api(path, data); }
    catch (error) { return {ok:false, error:error.message || String(error)}; }
  }

  function renumber(card){
    const rows = card.querySelectorAll(".rules-body .rule-row:not(.removing)");
    rows.forEach((tr, i) => {
      const cell = tr.querySelector(".col-id");
      if (cell) cell.textContent = "#" + (i + 1);
    });
    card.querySelectorAll(".rule-count").forEach(el => { el.textContent = rows.length; });
  }

  function wirePatternDetect(inp){
    if (inp.dataset.detectBound) return;
    inp.dataset.detectBound = "1";
    const row = inp.closest("tr");
    const sel = row.querySelector("select[name=etype]");
    let tmr, serial = 0;
    async function detectOne(){
      const request = ++serial;
      const v = inp.value || "";
      if (!v.trim() || !sel || sel.dataset.manualType || row.classList.contains("saving")) return;
      const selected = sel.value;
      try {
        const data = await rulesAPI.detect(v);
        if (request !== serial || inp.value !== v || sel.value !== selected || sel.dataset.manualType || row.classList.contains("saving")) return;
        const it = data.items[0];
        if (!it || data.invalid) return;
        if (sel.querySelector('option[value="'+it.type+'"]')) {
          sel.value = it.type;
          sel.className = "type-select " + it.type + (row.classList.contains("draft") ? " add-type" : "");
        }
      } catch(e){}
    }
    inp.addEventListener("input", () => { clearTimeout(tmr); serial++; tmr=setTimeout(detectOne, 180); });
  }

  function draftRowHtml(){
    return `<tr class="rule-row draft">
      <td class="col-id">+</td>
      <td class="col-name-hide"><input class="cell-input" name="ename" placeholder="имя правила…"/></td>
      <td class="col-type">
        <select class="type-select namespace add-type" name="etype">
          <option value="auto" selected>Auto</option>
          <option value="namespace">Namespace</option>
          <option value="ipv4">IPv4</option>
          <option value="ipv6">IPv6</option>
          <option value="domain">Domain</option>
          <option value="keyword">Keyword</option>
        </select>
      </td>
      <td class="col-pattern"><div class="pattern-cell">
        <input class="cell-input mono pattern-input" name="value" placeholder="паттерн правила…" autofocus/>
        <span class="rule-conflict-badge pill pill-status pill-conflict" hidden></span>
      </div></td>
      <td class="col-en">
        <label class="switch" title="Включён">
          <input type="checkbox" name="enabled" value="1" checked/>
          <span></span>
        </label>
      </td>
      <td class="col-del">
        <button type="button" class="icon-del" data-del-draft aria-label="Отмена" title="Отмена">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
        </button>
      </td>
    </tr>`;
  }

  /** Shared pattern key — same as import conflict check */
  function normalizePattern(v){
    return String(v || "").trim().toLowerCase().replace(/\.$/, "");
  }

  const CONFLICT_WARN_SVG = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`;

  let conflictFilterOn = false;
  let conflictScanTimer = null;
  /** last scan stats for filter link label */
  let conflictStats = { patterns: 0, groups: 0, totalGroups: 0 };

  function scheduleConflictScan(){
    if (conflictScanTimer) clearTimeout(conflictScanTimer);
    conflictScanTimer = setTimeout(() => {
      conflictScanTimer = null;
      rebuildConflictMarkers();
    }, 80);
  }

  function cardHasConflict(card){
    if (!card) return false;
    if (card.getAttribute("data-has-conflict") === "1") return true;
    if (card.querySelector(".gcard-conflict-warn[data-show='1']")) return true;
    if (card.querySelector(".rule-row.is-conflict")) return true;
    return false;
  }

  function updateConflictFilterLink(){
    const link = document.getElementById("conflictFilter");
    if (!link) return;
    const { patterns, groups, totalGroups } = conflictStats;
    if (patterns <= 0 || groups <= 0) {
      link.hidden = true;
      link.setAttribute("data-show", "0");
      link.textContent = "";
      link.classList.remove("active");
      link.setAttribute("aria-pressed", "false");
      if (conflictFilterOn) {
        conflictFilterOn = false;
        applyListFilter();
      }
      return;
    }
    link.hidden = false;
    link.setAttribute("data-show", "1");
    link.classList.toggle("active", conflictFilterOn);
    link.setAttribute("aria-pressed", conflictFilterOn ? "true" : "false");
    if (conflictFilterOn) {
      link.textContent = "✕ Сбросить · " + groups + " из " + totalGroups + " с конфликтами";
      link.title = "Показать все группы";
    } else {
      const word = patterns === 1 ? "конфликт" : (patterns < 5 ? "конфликта" : "конфликтов");
      link.textContent = patterns + " " + word + " между группами";
      link.title = "Показать только группы с пересекающимися правилами";
    }
  }

  /**
   * Scan all groups for cross-group pattern collisions (frontend, O(rules)).
   * Marks group header icon + rule rows; updates filter link.
   */
  function rebuildConflictMarkers(){
    const index = new Map(); // key -> [{gid,gname,row}]
    document.querySelectorAll(".gcard").forEach(card => {
      const gid = card.getAttribute("data-gid") || "";
      const gname = (card.querySelector(".gcard-name")?.value || "").trim() || gid;
      card.querySelectorAll(".rules-body .rule-row:not(.draft)").forEach(row => {
        const val = row.querySelector("[name=value]")?.value || "";
        const key = normalizePattern(val);
        if (!key) return;
        if (!index.has(key)) index.set(key, []);
        index.get(key).push({ gid, gname, row });
      });
    });

    // clear previous — .is-conflict drives badge visibility (CSS); wipe badge text
    document.querySelectorAll(".rule-row.is-conflict").forEach(row => row.classList.remove("is-conflict"));
    document.querySelectorAll(".rule-conflict-badge").forEach(b => {
      b.hidden = true;
      b.textContent = "";
      b.removeAttribute("title");
    });
    document.querySelectorAll(".gcard").forEach(card => {
      card.removeAttribute("data-has-conflict");
      const w = card.querySelector(".gcard-conflict-warn");
      if (w) {
        w.hidden = true;
        w.setAttribute("data-show", "0");
        w.removeAttribute("title");
        w.innerHTML = "";
      }
      const fc = card.querySelector(".gcard-conflict-count");
      if (fc) {
        fc.hidden = true;
        fc.setAttribute("data-show", "0");
        fc.textContent = "";
      }
    });

    const groupStats = new Map(); // gid -> { count, others: Set }
    let patternConflicts = 0;

    function ensureConflictBadge(row){
      let badge = row.querySelector(".rule-conflict-badge");
      if (badge) return badge;
      const cell = row.querySelector(".pattern-cell");
      if (cell) {
        badge = document.createElement("span");
        badge.className = "rule-conflict-badge pill pill-status pill-conflict";
        badge.hidden = true;
        cell.appendChild(badge);
        return badge;
      }
      const inp = row.querySelector(".pattern-input");
      if (!inp || !inp.parentElement) return null;
      const wrap = document.createElement("div");
      wrap.className = "pattern-cell";
      inp.parentElement.insertBefore(wrap, inp);
      wrap.appendChild(inp);
      badge = document.createElement("span");
      badge.className = "rule-conflict-badge pill pill-status pill-conflict";
      badge.hidden = true;
      wrap.appendChild(badge);
      return badge;
    }

    index.forEach((entries) => {
      const gids = new Set(entries.map(e => e.gid));
      if (gids.size < 2) return;
      patternConflicts++;
      entries.forEach(e => {
        const others = [...new Set(entries.filter(x => x.gid !== e.gid).map(x => x.gname))];
        const badge = ensureConflictBadge(e.row);
        // single condition: only mark row if badge can be shown (badge + left accent stay in lockstep)
        if (!badge) return;
        const label = others.length === 1 ? others[0] : others.slice(0, 2).join(", ") + (others.length > 2 ? "…" : "");
        badge.textContent = "конфликт: " + label;
        badge.title = "Также в: " + others.join(", ");
        badge.hidden = false;
        e.row.classList.add("is-conflict"); // CSS left accent on first cell only
        if (!groupStats.has(e.gid)) groupStats.set(e.gid, { count: 0, others: new Set() });
        const st = groupStats.get(e.gid);
        st.count++;
        others.forEach(n => st.others.add(n));
      });
    });

    groupStats.forEach((st, gid) => {
      const card = document.querySelector(`.gcard[data-gid="${CSS.escape(gid)}"]`);
      if (!card) return;
      card.setAttribute("data-has-conflict", "1");
      let w = card.querySelector(".gcard-conflict-warn");
      if (!w) {
        w = document.createElement("span");
        w.className = "gcard-conflict-warn";
        w.setAttribute("role", "img");
        w.setAttribute("aria-label", "Конфликт правил");
        const form = card.querySelector("form[data-group-action=rename]") || card.querySelector(".gcard-name")?.closest("form");
        if (form) form.after(w);
        else card.querySelector(".gcard-head")?.appendChild(w);
      }
      const tip = st.count + " правил пересекаются с другими группами" +
        (st.others.size ? " (" + [...st.others].slice(0, 4).join(", ") + (st.others.size > 4 ? "…" : "") + ")" : "");
      w.innerHTML = CONFLICT_WARN_SVG;
      w.title = tip;
      w.hidden = false;
      w.setAttribute("data-show", "1");

      // footer: "N правил · K конфликт(ов)" — only when K > 0
      let fc = card.querySelector(".gcard-conflict-count");
      if (!fc) {
        fc = document.createElement("span");
        fc.className = "gcard-conflict-count";
        const countWrap = card.querySelector(".gcard-foot .rule-count-wrap");
        if (countWrap) countWrap.after(fc);
        else card.querySelector(".gcard-foot")?.prepend(fc);
      }
      const k = st.count;
      const confWord = k === 1 ? "конфликт" : (k > 1 && k < 5 ? "конфликта" : "конфликтов");
      fc.textContent = "· " + k + " " + confWord;
      fc.hidden = false;
      fc.setAttribute("data-show", "1");
    });

    const allCards = document.querySelectorAll(".gcard");
    conflictStats = {
      patterns: patternConflicts,
      groups: groupStats.size,
      totalGroups: allCards.length,
    };
    updateConflictFilterLink();
    // re-apply list filter if active (flags were just rebuilt)
    applyListFilter();
  }

  const ruleSnapshots = new WeakMap();
  const groupSnapshots = new WeakMap();
  const ruleSaves = new WeakMap();
  let rulePending = 0;
  let guardedRuleForm = null;

  function rowValues(row){
    const select = row.querySelector("[name=etype]");
    const snapshot = ruleSnapshots.get(row);
    const type = row.dataset.entryType === "raw" && select?.value === (snapshot?.displayType || "namespace")
      ? "raw" : (select?.value || "auto");
    return {
      ...(row.dataset.eid ? {id: row.dataset.eid} : {}),
      type, value: row.querySelector("[name=value]")?.value || "",
      name: row.querySelector("[name=ename]")?.value || "",
      enabled: !!row.querySelector("[name=enabled]")?.checked
    };
  }
  function rowChanged(row){
    const value = rowValues(row);
    if (row.classList.contains("draft")) return !!(value.value.trim() || value.name.trim());
    const saved = ruleSnapshots.get(row);
    return !saved || ["type", "value", "name", "enabled"].some(key => value[key] !== saved[key]);
  }
  function hasRuleDrafts(){
    if (rulePending) return true;
    if ([...document.querySelectorAll(".rules-body .rule-row")].some(rowChanged)) return true;
    for (const card of document.querySelectorAll(".gcard")) {
      const saved = groupSnapshots.get(card);
      const name = card.querySelector(".gcard-name");
      const toggle = card.querySelector("form[data-group-action=toggle] [name=enabled]");
      if (saved && ((name && !guardedRuleForm?.contains(name) && name.value !== saved.name)
        || (toggle && !guardedRuleForm?.contains(toggle) && toggle.checked !== saved.enabled))) return true;
    }
    const name = document.getElementById("gname");
    return !!((name && !guardedRuleForm?.contains(name) && name.value.trim())
      || (importText && !guardedRuleForm?.contains(importText) && importText.value.trim()));
  }
  function clearRuleError(control){
    control.setCustomValidity?.("");
    control.removeAttribute("aria-invalid");
    if (control.dataset.ruleError) { control.removeAttribute("title"); delete control.dataset.ruleError; }
  }
  function markRuleError(control, message){
    if (!control) return;
    control.setCustomValidity(message);
    control.setAttribute("aria-invalid", "true");
    control.dataset.ruleError = "1";
    control.title = message;
  }
  function acceptSavedRow(row, submitted, entry){
    const current = rowValues(row);
    const draft = row.classList.contains("draft") ? row : null;
    if (draft) {
      const wrap = document.createElement("tbody");
      wrap.innerHTML = draftRowHtml();
      row = wrap.firstElementChild;
      row.querySelector("[name=etype]").value = draft.querySelector("[name=etype]").value;
      row.querySelector("[name=etype]").dataset.manualType = draft.querySelector("[name=etype]").dataset.manualType || "";
      row.querySelector("[name=value]").value = current.value;
      row.querySelector("[name=ename]").value = current.name;
      row.querySelector("[name=enabled]").checked = current.enabled;
      row.style.setProperty("--ri", Math.min(draft.closest(".rules-body").querySelectorAll(".rule-row:not(.draft)").length, 12));
    }
    const select = row.querySelector("[name=etype]");
    const displayType = entry.type === "raw" ? "namespace" : entry.type;
    if (current.type === submitted.type) select.value = displayType;
    if (current.value === submitted.value) row.querySelector("[name=value]").value = entry.value;
    if (current.name === submitted.name) row.querySelector("[name=ename]").value = entry.name;
    if (current.enabled === submitted.enabled) row.querySelector("[name=enabled]").checked = entry.enabled;
    row.dataset.eid = entry.id;
    row.dataset.entryType = entry.type;
    row.classList.remove("draft");
    select.className = "type-select " + select.value;
    if (select.value !== "auto") select.querySelector("option[value=auto]")?.remove();
    row.querySelector("[name=ename]")?.classList.add("emptyish");
    row.querySelector("[name=value]")?.classList.add("emptyish");
    row.querySelector("[name=enabled]")?.setAttribute("data-toggle-entry", "");
    const del = row.querySelector("[data-del-draft]");
    if (del) {
      del.removeAttribute("data-del-draft");
      del.setAttribute("data-del-entry", "");
      del.setAttribute("aria-label", "Удалить");
      del.title = "Удалить";
    }
    row.dataset.filter = [entry.name, entry.value, entry.type].join(" ").toLowerCase();
    ruleSnapshots.set(row, {...entry, displayType});
    row.querySelectorAll("input,select").forEach(clearRuleError);
    if (draft) {
      draft.replaceWith(row);
      wirePatternDetect(row.querySelector(".pattern-input"));
    }
  }
  async function saveRows(card, rows){
    if (!card || ruleSaves.has(card)) return false;
    const changed = [...rows].filter(row => !row.classList.contains("removing") && rowChanged(row));
    if (!changed.length) return true;
    const submitted = changed.map(rowValues);
    changed.forEach(row => {
      row.classList.add("saving");
      row.querySelectorAll("input,select").forEach(clearRuleError);
    });
    const button = card.querySelector(".btn-save-group");
    if (button) button.disabled = true;
    rulePending++;
    const pending = rulesAPI.saveGroup(card.dataset.gid, {entries: submitted});
    ruleSaves.set(card, pending);
    try {
      const data = await pending;
      changed.forEach((row, index) => acceptSavedRow(row, submitted[index], data.entries[index]));
      renumber(card);
      scheduleConflictScan();
      return true;
    } catch (error) {
      const errors = error.data?.errors || [];
      let first;
      changed.forEach((row, index) => {
        const issue = errors.find(item => item.index === index || (item.id && item.id === submitted[index].id));
        if (issue || !errors.length) {
          const control = row.querySelector("[name=value]");
          markRuleError(control, issue?.error || error.message);
          if (!first) first = control;
        }
      });
      toast("Не сохранено: " + error.message, false);
      first?.reportValidity();
      return false;
    } finally {
      rulePending--;
      ruleSaves.delete(card);
      changed.forEach(row => row.classList.remove("saving"));
      if (button) button.disabled = false;
    }
  }
  async function commitDraft(row){
    if (!row || row.classList.contains("saving") || !row.classList.contains("draft")) return false;
    if (!(row.querySelector("[name=value]")?.value || "").trim()) return false;
    const value = row.querySelector("[name=value]").value;
    const saved = await saveRows(row.closest(".gcard"), [row]);
    if (saved) toast("Добавлено: " + value, true);
    return saved;
  }

  async function deleteEntry(row){
    const card = row.closest(".gcard");
    const gid = card && card.getAttribute("data-gid");
    const eid = row.getAttribute("data-eid");
    if (!gid || ruleSaves.has(card) || row.classList.contains("removing")) return;
    if (row.classList.contains("draft") || !eid) {
      row.classList.add("removing");
      setTimeout(() => { row.remove(); renumber(card); scheduleConflictScan(); }, 200);
      return;
    }
    row.classList.add("removing");
    rulePending++;
    const pending = rulesAPI.saveGroup(gid, {deleted: [eid]});
    ruleSaves.set(card, pending);
    try {
      await pending;
      setTimeout(() => {
        row.remove();
        renumber(card);
        scheduleConflictScan();
      }, 200);
    } catch (e) {
      row.classList.remove("removing");
      markRuleError(row.querySelector("[name=value]"), e.message);
      toast(e.message, false);
    } finally {
      rulePending--;
      ruleSaves.delete(card);
    }
  }

  async function saveRow(row){
    if (row.classList.contains("draft")) return commitDraft(row);
    return saveRows(row.closest(".gcard"), [row]);
  }

  btnAdd.addEventListener("click", () => openModal(modalGroup));
  btnExplain?.addEventListener("click", () => {
    if (!openModal(modalExplain)) return;
    const box = document.getElementById("explainResult");
    if (box) { box.classList.remove("show"); box.innerHTML = ""; }
    const inp = document.getElementById("explainInput");
    setTimeout(() => inp?.focus(), 80);
  });
  document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", closeAll));
  [modalGroup, modalImport, modalExplain].forEach(m => m?.addEventListener("click", e => { if (e.target === m) closeAll(); }));
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    if (document.querySelector(".modal-backdrop.open")) closeAll();
    else if (settingsBd?.classList.contains("open")) {
      e.preventDefault();
      closeSettings();
    }
  });

  /**
   * Single status → color mapping for Explain route.
   * CSS classes set --explain-status (stripe, status-dot, action text).
   */
  const EXPLAIN_STATUS = {
    proxy: { key: "proxy", label: "PROXY", css: "ind-proxy" },
    direct: { key: "direct", label: "DIRECT", css: "ind-direct" },
    block: { key: "block", label: "BLOCK", css: "ind-block" },
  };
  function resolveExplainStatus(d, action){
    const pol = String((action && action.policy) || "").toUpperCase();
    const ind = String((d && d.indicator) || "").toLowerCase();
    if (ind === "block" || pol === "BLOCK" || pol === "REJECT") return EXPLAIN_STATUS.block;
    if (ind === "proxy" || pol === "PROXY") return EXPLAIN_STATUS.proxy;
    return EXPLAIN_STATUS.direct;
  }

  function renderExplainResult(d){
    const box = document.getElementById("explainResult");
    if (!box) return;
    if (!d || !d.ok) {
      const stErr = EXPLAIN_STATUS.block;
      box.innerHTML = `<div class="explain-card ${stErr.css}"><div class="explain-ind"><span class="explain-ind-dot" aria-hidden="true"></span>${escHtml(d && d.error ? d.error : "Ошибка")}</div></div>`;
      box.classList.add("show");
      return;
    }
    const st = d.steps || {};
    const rule = st.rule || {};
    const group = st.group || {};
    const action = st.action || {};
    const status = resolveExplainStatus(d, action);
    const pol = (action.policy || status.label).toUpperCase();
    const typeCls = (rule.type || "match").toLowerCase();
    const typeLab = rule.type_label || rule.type || "—";
    const pattern = rule.pattern != null ? rule.pattern : "—";
    const idxLab = rule.index_label || (rule.index != null ? "#" + rule.index : "—");
    const ruleHit = d.matched && d.match_source === "group";
    // MATCH: one compact line — final "MATCH → DIRECT" only in Действие
    const ruleBody = ruleHit
      ? `<span class="explain-type-tag ${escHtml(typeCls)}">${escHtml(typeLab)}</span><span class="mono">${escHtml(pattern)}</span>
         <div class="muted">правило ${escHtml(String(idxLab))} · <span class="mono">${escHtml(rule.rule || "")}</span></div>`
      : (d.match_source === "system"
        ? `<span class="explain-type-tag geoip">${escHtml(typeLab)}</span><span class="mono">${escHtml(pattern)}</span>
           <div class="muted"><span class="mono">${escHtml(rule.rule || "")}</span></div>`
        : `<span class="explain-type-tag match">MATCH</span><span class="muted">нет совпадений — применяется дефолт</span>`);
    const groupBody = group.name && group.name !== "—"
      ? `<strong>${escHtml(group.name)}</strong>${group.provider ? ` <span class="muted mono">(${escHtml(group.provider)})</span>` : ""}`
      : `<span class="muted">${escHtml(group.name || "—")}</span>`;
    let actionBody;
    if (status.key === "proxy") {
      actionBody = `<span class="explain-policy">PROXY</span>${action.node ? ` <span class="muted">→ нода <span class="mono">${escHtml(action.node)}</span></span>` : ""}
         <div class="muted">${escHtml(action.detail || "")}</div>`;
    } else if (status.key === "block") {
      actionBody = `<span class="explain-policy">${escHtml(action.detail || pol || "BLOCK")}</span>`;
    } else {
      actionBody = `<span class="explain-policy">${escHtml(action.detail || pol || "DIRECT")}</span>`;
    }

    box.innerHTML = `
      <div class="explain-card ${status.css}">
        <div class="explain-ind"><span class="explain-ind-dot" aria-hidden="true"></span>${escHtml(status.label)}</div>
        <div class="explain-chain">
          <div class="explain-step">
            <span class="explain-step-dot" aria-hidden="true"></span>
            <div>
              <div class="explain-step-label">Запрос</div>
              <div class="explain-step-body"><span class="mono">${escHtml(st.request || d.query || "")}</span>
                <div class="muted">${escHtml(d.query_kind === "ip" ? "IP" : "domain")}</div>
              </div>
            </div>
          </div>
          <div class="explain-step">
            <span class="explain-step-dot" aria-hidden="true"></span>
            <div>
              <div class="explain-step-label">Совпавшее правило</div>
              <div class="explain-step-body">${ruleBody}</div>
            </div>
          </div>
          <div class="explain-step">
            <span class="explain-step-dot" aria-hidden="true"></span>
            <div>
              <div class="explain-step-label">Группа</div>
              <div class="explain-step-body">${groupBody}</div>
            </div>
          </div>
          <div class="explain-step">
            <span class="explain-step-dot" aria-hidden="true"></span>
            <div>
              <div class="explain-step-label">Действие</div>
              <div class="explain-step-body">${actionBody}</div>
            </div>
          </div>
        </div>
      </div>`;
    box.classList.add("show");
  }

  document.getElementById("explainForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const inp = document.getElementById("explainInput");
    const btn = document.getElementById("btnExplainRun");
    const q = (inp && inp.value || "").trim();
    if (!q) return;
    const prev = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "Проверяю..."; }
    try {
      const d = await rulesAPI.explain(q);
      renderExplainResult(d);
    } catch (err) {
      renderExplainResult({ ok: false, error: String(err) });
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = prev || "Проверить маршрут"; }
    }
  });

  document.querySelectorAll("[data-import]").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const gid = b.getAttribute("data-import") || "";
      if (modalImport.classList.contains("open") && importText.value.trim() && !closeAll()) return;
      if (!openModal(modalImport)) return;
      importGid.value = gid;
      importText.value = "";
      importPreviewItems = [];
      previewBox.hidden = true;
      previewStats.innerHTML = "";
      const card = b.closest(".gcard");
      const gname = card?.querySelector(".gcard-name")?.value || card?.querySelector(".gcard-name")?.getAttribute("value") || "—";
      const nameEl = document.getElementById("importGroupName");
      if (nameEl) nameEl.textContent = gname;
      setTimeout(() => importText.focus(), 40);
    });
  });

  document.querySelectorAll("[data-collapse]").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const card = b.closest(".gcard");
      if (card) card.classList.toggle("collapsed");
    });
  });
  document.querySelectorAll(".gcard-head").forEach(head => {
    head.addEventListener("click", (e) => {
      if (e.target.closest("input,button,label,a,select,form button")) return;
      const card = head.closest(".gcard");
      if (card) card.classList.toggle("collapsed");
    });
  });

  // + add rule in group header
  document.querySelectorAll("[data-add-rule]").forEach(b => {
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const card = b.closest(".gcard");
      if (!card) return;
      card.classList.remove("collapsed");
      const body = card.querySelector(".rules-body");
      if (!body) return;
      // only one draft at a time per group
      const existing = body.querySelector(".rule-row.draft");
      if (existing) {
        existing.querySelector(".pattern-input")?.focus();
        return;
      }
      body.insertAdjacentHTML("afterbegin", draftRowHtml());
      const draft = body.querySelector(".rule-row.draft");
      const inp = draft.querySelector(".pattern-input");
      wirePatternDetect(inp);
      setTimeout(() => inp.focus(), 30);
    });
  });

  // delegated events for rows
  document.getElementById("panel-groups").addEventListener("click", (e) => {
    const del = e.target.closest("[data-del-entry], [data-del-draft]");
    if (del) {
      e.preventDefault();
      const row = del.closest(".rule-row");
      if (row) deleteEntry(row);
      return;
    }
    const saveBtn = e.target.closest(".btn-save-group");
    if (saveBtn) {
      const card = saveBtn.closest(".gcard");
      (async () => {
        const rows = card.querySelectorAll(".rule-row");
        if (await saveRows(card, rows)) toast("Сохранено", true);
      })();
    }
  });

  document.getElementById("panel-groups").addEventListener("keydown", (e) => {
    const inp = e.target.closest(".pattern-input, .cell-input");
    if (!inp || e.key !== "Enter") return;
    e.preventDefault();
    const row = inp.closest(".rule-row");
    if (row) saveRow(row);
  });

  document.getElementById("panel-groups").addEventListener("change", (e) => {
    const t = e.target;
    if (t.closest(".rule-row")) clearRuleError(t);
    if (t.matches("[name=enabled]") && t.closest(".rule-row:not(.draft)")) {
      saveRow(t.closest(".rule-row"));
    }
    if (t.matches("select[name=etype]")) {
      t.dataset.manualType = "1";
      t.className = "type-select " + t.value;
      if (t.closest(".rule-row:not(.draft)")) scheduleConflictScan();
    }
  });

  document.getElementById("panel-groups").addEventListener("focusout", (e) => {
    const inp = e.target.closest(".pattern-input");
    if (!inp) return;
    const row = inp.closest(".rule-row.draft");
    if (row && (inp.value || "").trim()) {
      // slight delay so click on delete can win
      setTimeout(() => {
        if (document.body.contains(row) && row.classList.contains("draft")) commitDraft(row);
      }, 120);
    }
  });

  document.querySelectorAll(".pattern-input").forEach(wirePatternDetect);
  document.querySelectorAll(".rules-body .rule-row:not(.draft)").forEach(row => {
    ruleSnapshots.set(row, {...rowValues(row), displayType: row.querySelector("[name=etype]")?.value});
  });
  document.querySelectorAll(".gcard").forEach(card => {
    groupSnapshots.set(card, {
      name: card.querySelector(".gcard-name")?.value || "",
      enabled: !!card.querySelector("form[data-group-action=toggle] [name=enabled]")?.checked
    });
  });
  document.addEventListener("input", e => {
    if (e.target.matches("input,textarea,select") && e.target.closest("#panel-groups,#modalGroup,#modalImport")) {
      clearRuleError(e.target);
    }
  });
  document.addEventListener("change", e => {
    if (!e.target.matches("input,textarea,select") || !e.target.closest("#panel-groups,#modalGroup,#modalImport")) return;
    const form = e.target.closest("form");
    if (form) form.querySelectorAll("input,textarea,select").forEach(clearRuleError);
  }, true);

  // ── Mobile: tap pattern → copy popover ──
  function isMobileLayout(){
    try { return window.matchMedia("(max-width:720px)").matches; } catch(e) { return false; }
  }
  function closePatternCopyToast(){
    document.getElementById("patternCopyToast")?.remove();
  }
  function showPatternCopyToast(val){
    closePatternCopyToast();
    const el = document.createElement("div");
    el.id = "patternCopyToast";
    el.className = "pattern-copy-toast";
    el.setAttribute("role", "dialog");
    el.innerHTML =
      "<code></code>" +
      '<div class="pct-actions">' +
      '<button type="button" class="btn btn-ghost" data-pct-close>Закрыть</button>' +
      '<button type="button" class="btn btn-primary" data-pct-copy>Копировать</button>' +
      "</div>";
    el.querySelector("code").textContent = val;
    el.querySelector("[data-pct-close]").addEventListener("click", closePatternCopyToast);
    el.querySelector("[data-pct-copy]").addEventListener("click", async () => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(val);
        } else {
          const ta = document.createElement("textarea");
          ta.value = val; document.body.appendChild(ta); ta.select();
          document.execCommand("copy"); ta.remove();
        }
        toast("Скопировано", true);
        closePatternCopyToast();
      } catch (err) {
        toast("Не удалось скопировать", false);
      }
    });
    document.body.appendChild(el);
  }
  document.getElementById("panel-groups")?.addEventListener("click", (e) => {
    if (!isMobileLayout()) return;
    if (e.target.closest("button,label,select,a,.switch,.icon-del,.mini-btn")) return;
    const inp = e.target.closest(".pattern-input");
    if (!inp) return;
    const val = (inp.value || "").trim();
    if (!val) return;
    // show when multi-line / long / or always on mobile for easy copy
    if (val.length >= 18 || inp.scrollHeight > inp.clientHeight + 4 || true) {
      e.preventDefault();
      showPatternCopyToast(val);
    }
  });


  function updateCardFilterSlice(card, shown, total){
    let el = card.querySelector(".gcard-filter-slice");
    if (!conflictFilterOn || total === 0) {
      if (el) {
        el.hidden = true;
        el.setAttribute("data-show", "0");
        el.textContent = "";
      }
      return;
    }
    if (!el) {
      el = document.createElement("span");
      el.className = "gcard-filter-slice";
      const foot = card.querySelector(".gcard-foot");
      const save = foot?.querySelector(".btn-save-group");
      if (foot && save) foot.insertBefore(el, save);
      else if (foot) foot.appendChild(el);
    }
    el.hidden = false;
    el.setAttribute("data-show", "1");
    el.textContent = shown + " из " + total + " правил (только конфликты)";
  }

  /**
   * Apply search query AND optional conflict-only filter (intersection).
   * Groups: .is-filtered-out. Rows: .is-row-filtered-out (display-only).
   * When conflict filter is on, only .is-conflict rows stay visible inside a group.
   */
  function applyListFilter(){
    if (typeof currentView !== "undefined" && currentView === "stats") {
      filterStatsBySearch();
      return;
    }
    const q = (searchInput?.value || "").trim().toLowerCase();
    document.querySelectorAll(".gcard").forEach(card => {
      const groupHay = (card.getAttribute("data-filter") || "").toLowerCase();
      const rows = card.querySelectorAll(".rules-body .rule-row");
      const dataRows = card.querySelectorAll(".rules-body .rule-row:not(.draft)");
      const conflictOk = !conflictFilterOn || cardHasConflict(card);
      const total = dataRows.length;

      const title = (card.querySelector(".gcard-name")?.value || "").toLowerCase();
      // only group TITLE counts as "name hit" (shows all rows).
      // do NOT use groupHay here ? it contains all patterns and would unfilter every row.
      let groupNameHit = !q ? true : title.includes(q);

      let anySearchHit = false;
      let anyVisible = false;
      let shownConflicts = 0;

      rows.forEach(r => {
        r.style.display = ""; // class controls hide
        r.classList.remove("search-hit");

        if (r.classList.contains("draft")) {
          // drafts always visible when group is shown (edit in progress)
          r.classList.remove("is-row-filtered-out");
          return;
        }

        const isConf = r.classList.contains("is-conflict");
        const rowHay = (
          (r.getAttribute("data-filter") || "") + " " +
          (r.querySelector("[name=ename]")?.value || "") + " " +
          (r.querySelector("[name=value]")?.value || "") + " " +
          (r.querySelector("[name=etype]")?.value || "")
        ).toLowerCase();
        const searchHit = !q || rowHay.includes(q);
        if (searchHit && q) anySearchHit = true;

        // intersection: conflict-filter requires is-conflict; search requires hit
        // if group name matches search, all conflict rows (or all rows) count as ok for search
        let show = true;
        if (conflictFilterOn) show = isConf;
        if (q) {
          if (groupNameHit) {
            // name match → keep row if it already passed conflict gate
            show = show;
          } else {
            show = show && searchHit;
          }
        }

        r.classList.toggle("is-row-filtered-out", !show);
        r.classList.toggle("search-hit", !!(q && searchHit && show));
        if (show) {
          anyVisible = true;
          if (isConf) shownConflicts++;
        }
      });

      const searchOk = !q || groupNameHit || anySearchHit;
      // if conflict filter: group must have conflicts; if also search, need searchOk
      // when conflict filter + search: prefer groups that have at least one visible row
      let showCard = conflictOk && searchOk;
      if (conflictFilterOn && q && !groupNameHit) {
        showCard = conflictOk && anyVisible;
      } else if (conflictFilterOn && !q) {
        showCard = conflictOk;
      } else if (!conflictFilterOn && q) {
        showCard = searchOk;
      } else {
        showCard = true;
      }

      card.classList.toggle("is-filtered-out", !showCard);
      card.style.display = "";

      if (showCard) {
        if (q && anySearchHit) card.classList.remove("collapsed");
        // conflict-only filter: leave expand/collapse to user (don't force collapse)
        if (!q && !conflictFilterOn) card.classList.add("collapsed");
      }

      // footer slice label only when conflict filter on and card visible
      if (showCard && conflictFilterOn) {
        updateCardFilterSlice(card, shownConflicts, total);
      } else {
        updateCardFilterSlice(card, 0, 0);
      }
    });
  }
  // back-compat alias
  function filter(q){ applyListFilter(); }


  // ── View: Правила / Статистика ──
  const VIEW_KEY = "xttp_panel_view";
  let viewGen = 0;
  let currentView = "rules";
  const statsTimer = monitoringAPI.createPoller(refreshStats, 1000);
  const statsCursor = monitoringAPI.createStatsCursor();
  let trafficHistory = []; // {t, upRate, downRate}
  let lastStatsData = null;
  let statsSessionStart = null;
  let statsPeriod = "live";
  let statsLines = { down: true, up: true };
  let statsHoverIdx = -1;
  let statsChartLayout = null;
  let statsRafId = 0;
  let statsSmoothMax = 1;
  let statsChartGeom = { w: 0, h: 0, dpr: 0 };
  let statsLastFrameT = 0;
  let statsReduceMotion = false;
  /* continuous display clock + lerped tip rates (smooth live scroll, no 1Hz snap) */
  let statsVis = { up: 0, down: 0, primed: false };
  const PERIOD_KEY = "xttp_stats_period";
  const LINES_KEY = "xttp_stats_lines";
  const PERIOD_SEC = { live: 90, "5m": 300, "1h": 3600, session: Infinity };
  const PERIOD_LABEL = { live: "live", "5m": "5 мин", "1h": "1 час", session: "сессия" };
  const statsSearchPlaceholder = "Фильтр по сервису…";
  const rulesSearchPlaceholder = "Поиск сервисов…";

  function monitoringVisible(view){
    return currentView === view && !document.hidden
      && !document.querySelector(".modal-backdrop.open, #settingsBd.open, #verBackdrop.is-open");
  }
  function syncMonitoringVisibility(){
    if (monitoringVisible("stats")) startStatsPolling();
    else stopStatsPolling();
    if (monitoringVisible("conns")) startConnsPolling();
    else stopConnsPolling();
    if (monitoringVisible("logs")) startLogsPolling();
    else stopLogsPolling();
  }
  document.addEventListener("visibilitychange", syncMonitoringVisibility);
  window.addEventListener("pagehide", () => {
    stopStatsPolling(); stopConnsPolling(); stopLogsPolling();
  });
  window.addEventListener("pageshow", syncMonitoringVisibility);
  const monitoringModalObserver = new MutationObserver(syncMonitoringVisibility);
  document.querySelectorAll(".modal-backdrop, #settingsBd, #verBackdrop").forEach(modal => {
    monitoringModalObserver.observe(modal, {attributes: true, attributeFilter: ["class"]});
  });

  function moveViewPill(){
    const wrap = document.getElementById("viewSwitch");
    const pill = document.getElementById("viewPill");
    const btn = wrap?.querySelector(".stab.active");
    if (!pill || !wrap || !btn) return;
    const wr = wrap.getBoundingClientRect();
    const br = btn.getBoundingClientRect();
    pill.style.width = br.width + "px";
    pill.style.transform = "translate3d(" + (br.left - wr.left - 4) + "px,0,0)";
  }

  function fmtBytes(n){
    n = Number(n) || 0;
    const u = ["B","KB","MB","GB","TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i === 0 ? String(Math.round(n)) : n.toFixed(n >= 10 ? 1 : 2)) + " " + u[i];
  }
  function fmtRate(bps){
    return fmtBytes(bps) + "/s";
  }

  function setView(view, opts){
    opts = opts || {};
    const allowed = {rules:1, stats:1, conns:1, logs:1};
    const next = allowed[view] ? view : "rules";
    currentView = next;
    viewGen++;

    document.querySelectorAll("#viewSwitch .stab").forEach(b => {
      const on = b.getAttribute("data-view") === next;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    try { moveViewPill(); } catch (_) {}

    const rulesPanel = document.getElementById("panel-groups");
    const statsPanel = document.getElementById("panel-stats");
    const connsPanel = document.getElementById("panel-conns");
    const logsPanel = document.getElementById("panel-logs");
    const actions = document.getElementById("toolbarRulesActions");

    function activatePanel(panel, on){
      if (!panel) return;
      panel.classList.remove("is-hiding");
      panel.style.opacity = "";
      panel.style.transform = "";
      panel.style.pointerEvents = "";
      panel.style.position = "";
      panel.style.width = "";
      panel.style.inset = "";
      panel.style.visibility = "";
      panel.style.display = "";
      panel.hidden = !on;
      if (on) {
        panel.hidden = false;
        panel.style.setProperty("opacity", "1", "important");
        panel.style.setProperty("visibility", "visible", "important");
        panel.style.setProperty("display", "block", "important");
        panel.style.setProperty("transform", "none", "important");
        panel.style.setProperty("pointer-events", "auto", "important");
        panel.style.setProperty("position", "relative", "important");
      }
    }

    activatePanel(rulesPanel, next === "rules");
    activatePanel(statsPanel, next === "stats");
    activatePanel(connsPanel, next === "conns");
    activatePanel(logsPanel, next === "logs");

    if (actions) actions.hidden = next !== "rules";

    if (searchInput) {
      const ph = {
        rules: rulesSearchPlaceholder,
        stats: statsSearchPlaceholder,
        conns: "Фильтр по домену / IP…",
        logs: "Фильтр по тексту лога…",
      };
      searchInput.placeholder = ph[next] || rulesSearchPlaceholder;
      searchInput.setAttribute("aria-label", searchInput.placeholder);
    }

    if (opts.persist !== false) {
      try { localStorage.setItem(VIEW_KEY, next); } catch (e) {}
    }

    syncMonitoringVisibility();

    if (next === "stats") {
      try { filterStatsBySearch(); } catch (_) {}
    } else if (next === "rules") {
      try { reviveRulesCards(); } catch (_) {}
      try { applyListFilter(); } catch (_) {}
      try { reviveRulesCards(); } catch (_) {}
    }
  }

  function reviveRulesCards(){
    const panel = document.getElementById("panel-groups");
    if (!panel || currentView !== "rules") return;
    panel.hidden = false;
    panel.classList.remove("is-hiding");
    panel.style.setProperty("opacity", "1", "important");
    panel.style.setProperty("display", "block", "important");
    panel.style.setProperty("visibility", "visible", "important");

    const cards = panel.querySelectorAll(".gcard");
    cards.forEach(c => {
      c.style.setProperty("opacity", "1", "important");
      c.style.setProperty("transform", "none", "important");
      c.style.setProperty("animation", "none", "important");
      c.style.setProperty("visibility", "visible", "important");
      // only clear display if we didn't intentionally filter
      if (!c.classList.contains("is-filtered-out")) {
        c.style.removeProperty("display");
        if (c.style.display === "none") c.style.display = "";
      }
    });

    // empty-state only if truly no cards in DOM
    let empty = document.getElementById("rulesEmptyFallback");
    if (!cards.length) {
      if (!empty) {
        empty = document.createElement("div");
        empty.id = "rulesEmptyFallback";
        empty.className = "empty-wrap";
        empty.innerHTML =
          '<div class="empty-card"><div><h3>Список правил пуст</h3>' +
          '<p class="meta">Карточки групп не найдены. Нажмите «Обновить».</p>' +
          '<button type="button" class="btn btn-primary" id="rulesEmptyReload" style="margin-top:10px">Обновить</button></div></div>';
        panel.appendChild(empty);
        empty.querySelector("#rulesEmptyReload")?.addEventListener("click", () => location.reload());
      }
      empty.hidden = false;
    } else if (empty) {
      empty.hidden = true;
    }

    // if every card filtered out with no query — unstick
    const q = (searchInput && searchInput.value || "").trim();
    if (cards.length && !q) {
      const anyVisible = [...cards].some(c => !c.classList.contains("is-filtered-out"));
      if (!anyVisible) {
        conflictFilterOn = false;
        cards.forEach(c => {
          c.classList.remove("is-filtered-out");
          c.style.removeProperty("display");
        });
        try {
          if (typeof updateConflictFilterLink === "function") updateConflictFilterLink();
          applyListFilter();
        } catch (_) {}
        cards.forEach(c => {
          c.style.setProperty("opacity", "1", "important");
          c.style.setProperty("animation", "none", "important");
        });
      }
    }
  }

  function filterStatsBySearch(){
    const q = (searchInput?.value || "").trim().toLowerCase();
    const rows = document.querySelectorAll("#statsBody tr[data-name]");
    rows.forEach(tr => {
      const name = (tr.getAttribute("data-name") || "").toLowerCase();
      const id = (tr.getAttribute("data-id") || "").toLowerCase();
      const hit = !q || name.includes(q) || id.includes(q);
      tr.classList.toggle("is-dim", !hit);
      tr.hidden = q ? !hit : false;
    });
    // redraw chart unfiltered totals always; table filters only
  }

  function loadStatsPrefs(){
    try {
      const p = localStorage.getItem(PERIOD_KEY);
      if (p && PERIOD_SEC[p] != null) statsPeriod = p;
      const l = localStorage.getItem(LINES_KEY);
      if (l) {
        const o = JSON.parse(l);
        if (typeof o.down === "boolean") statsLines.down = o.down;
        if (typeof o.up === "boolean") statsLines.up = o.up;
      }
    } catch (e) {}
    if (!statsLines.down && !statsLines.up) statsLines.down = true;
  }
  function saveStatsPrefs(){
    try {
      localStorage.setItem(PERIOD_KEY, statsPeriod);
      localStorage.setItem(LINES_KEY, JSON.stringify(statsLines));
    } catch (e) {}
  }
  function moveStatsPeriodPill(){
    const wrap = document.getElementById("statsPeriod");
    const pill = document.getElementById("statsPeriodPill");
    const btn = wrap && wrap.querySelector(".stab.active");
    if (!pill || !wrap || !btn) return;
    const wr = wrap.getBoundingClientRect();
    const br = btn.getBoundingClientRect();
    pill.style.width = br.width + "px";
    pill.style.transform = "translate3d(" + (br.left - wr.left - 4) + "px,0,0)";
  }
  function applyStatsPrefsToUi(){
    document.querySelectorAll("#statsPeriod .stab").forEach(b => {
      b.classList.toggle("active", b.getAttribute("data-period") === statsPeriod);
    });
    const ld = document.getElementById("lineDown");
    const lu = document.getElementById("lineUp");
    if (ld) ld.checked = !!statsLines.down;
    if (lu) lu.checked = !!statsLines.up;
    const title = document.getElementById("statsChartTitle");
    if (title) title.textContent = "Трафик (" + (PERIOD_LABEL[statsPeriod] || statsPeriod) + ")";
    moveStatsPeriodPill();
  }
  function getPeriodWindow(){
    const now = Date.now() / 1000;
    if (statsPeriod === "session") {
      let t0 = statsSessionStart != null ? statsSessionStart : now - 90;
      if (trafficHistory.length) {
        const first = trafficHistory[0].t;
        if (statsSessionStart == null || first < t0) {
          /* keep session start; still clamp left to first sample if later */
        }
        if (statsSessionStart == null) t0 = first;
      }
      return { t0: t0, t1: now, label: PERIOD_LABEL.session || "сессия" };
    }
    const win = Number(PERIOD_SEC[statsPeriod]) || 90;
    return { t0: now - win, t1: now, label: PERIOD_LABEL[statsPeriod] || statsPeriod };
  }
  function getHistoryForPeriod(){
    if (!trafficHistory.length) return [];
    const { t0, t1 } = getPeriodWindow();
    // include slight slack so the newest sample is never dropped by clock skew
    return trafficHistory.filter(p => p.t >= t0 - 0.25 && p.t <= t1 + 0.75);
  }
  function stopStatsPolling(){
    statsTimer.stop();
    if (statsRafId) { cancelAnimationFrame(statsRafId); statsRafId = 0; }
    hideStatsTooltip();
  }
  function statsAnimLoop(ts){
    statsRafId = 0;
    if (!monitoringVisible("stats")) return;
    const nowMs = ts || performance.now();
    const dt = statsLastFrameT ? Math.min(0.05, (nowMs - statsLastFrameT) / 1000) : 0.016;
    statsLastFrameT = nowMs;
    // lerp tip rates toward latest sample every frame → no hard jump on poll
    if (trafficHistory.length) {
      const last = trafficHistory[trafficHistory.length - 1];
      const tu = Math.max(0, last.upRate || 0);
      const td = Math.max(0, last.downRate || 0);
      if (!statsVis.primed || statsReduceMotion) {
        statsVis.up = tu; statsVis.down = td; statsVis.primed = true;
      } else {
        const k = 1 - Math.exp(-dt * 7);
        statsVis.up += (tu - statsVis.up) * k;
        statsVis.down += (td - statsVis.down) * k;
      }
    }
    drawStatsChart(dt);
    statsRafId = requestAnimationFrame(statsAnimLoop);
  }
  function startStatsAnim(){
    if (statsRafId || !monitoringVisible("stats")) return;
    try {
      statsReduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { statsReduceMotion = false; }
    statsLastFrameT = 0;
    statsRafId = requestAnimationFrame(statsAnimLoop);
  }
  function startStatsPolling(){
    if (!monitoringVisible("stats")) return;
    if (statsRafId) { statsTimer.start(); return; }
    loadStatsPrefs();
    applyStatsPrefsToUi();
    bindStatsChartUi();
    if (statsSessionStart == null) statsSessionStart = Date.now() / 1000;
    statsTimer.start();
    startStatsAnim();
    requestAnimationFrame(() => moveStatsPeriodPill());
  }
  async function refreshStats(signal){
    if (!signal) return statsTimer.refresh();
    if (signal.aborted || !monitoringVisible("stats")) return;
    try {
      const d = await monitoringAPI.readSnapshot(statsCursor.query(), signal);
      if (signal.aborted || !monitoringVisible("stats")) return;
      lastStatsData = d;
      const state = statsCursor.consume(d);
      trafficHistory = state.history;
      if (state.changed) {
        statsVis.primed = false;
        hideStatsTooltip();
      }
      const last = trafficHistory[trafficHistory.length - 1];
      const upRate = last ? last.upRate : 0;
      const downRate = last ? last.downRate : 0;
      const el = (id) => document.getElementById(id);
      if (el("statDownRate")) el("statDownRate").textContent = fmtRate(downRate);
      if (el("statUpRate")) el("statUpRate").textContent = fmtRate(upRate);
      if (el("statDownTotal")) el("statDownTotal").textContent = "всего " + fmtBytes(d.downloadTotal);
      if (el("statUpTotal")) el("statUpTotal").textContent = "всего " + fmtBytes(d.uploadTotal);
      if (el("statConns")) el("statConns").textContent = String(d.connections ?? "—");
      if (el("statConnSub")) {
        const mem = Number(d.memory) || 0;
        const px = d.proxyConnections;
        if (mem > 0) el("statConnSub").textContent = "RAM " + fmtBytes(mem);
        else el("statConnSub").textContent = "через PROXY: " + String(px ?? 0);
      }
      const hitSum = (d.groups || []).reduce((s, g) => s + (g.hits || 0), 0);
      if (el("statHits")) el("statHits").textContent = String(hitSum);
      renderStatsTable(d.groups || []);
      // paint only via rAF loop — avoid 1Hz full redraw flash
      if (!statsRafId && monitoringVisible("stats")) startStatsAnim();
      filterStatsBySearch();
      if (el("statsMeta")) {
        const _pw = getPeriodWindow();
        el("statsMeta").textContent = "обновлено " + new Date().toLocaleTimeString()
          + " · группы " + (d.groups || []).length
          + " · " + (PERIOD_LABEL[statsPeriod] || statsPeriod)
          + " · точек " + getHistoryForPeriod().length
          + "/" + trafficHistory.length;
      }
    } catch (e) {
      if (signal.aborted || !monitoringVisible("stats")) return;
      const body = document.getElementById("statsBody");
      if (body) body.innerHTML = `<tr><td colspan="6" class="stats-empty">${escHtml(e.message || String(e))}</td></tr>`;
      const meta = document.getElementById("statsMeta");
      if (meta) meta.textContent = e.message || String(e);
    }
  }

  function renderStatsTable(groups){
    const body = document.getElementById("statsBody");
    if (!body) return;
    if (!groups.length) {
      body.innerHTML = `<tr><td colspan="6" class="stats-empty">Нет RuleSet-групп (примени правила)</td></tr>`;
      return;
    }
    const maxBytes = Math.max(1, ...groups.map(g => (g.up || 0) + (g.down || 0)));
    const grand = groups.reduce((s, g) => s + (g.up || 0) + (g.down || 0), 0) || 1;
    body.innerHTML = groups.map(g => {
      const up = g.up || 0, down = g.down || 0;
      const total = up + down;
      const pct = Math.round(100 * total / maxBytes);
      const share = Math.round(1000 * total / grand) / 10;
      const tip = fmtBytes(down) + " download, " + fmtBytes(up) + " upload — " + share + "% от суммарного трафика групп";
      return `<tr data-name="${escHtml(g.name || "")}" data-id="${escHtml(g.id || "")}">
        <td><strong>${escHtml(g.name || g.id || "")}</strong></td>
        <td>${g.hits || 0}</td>
        <td>${g.conns || 0}</td>
        <td>${fmtBytes(down)}</td>
        <td>${fmtBytes(up)}</td>
        <td><div class="bar" title="${escHtml(tip)}"><i style="width:${pct}%"></i></div></td>
      </tr>`;
    }).join("");
  }

  
  /** Fixed bucket size (seconds) for a time span — deterministic, no random sampling. */
  function bucketSecForSpan(span){
    span = Math.max(1, Number(span) || 1);
    if (span <= 120) return 1;       // live ~90s: 1s buckets
    if (span <= 400) return 2;       // 5 min
    if (span <= 1200) return 5;
    if (span <= 3600) return 15;     // 1 hour → 240 buckets
    if (span <= 7200) return 30;
    // long session: aim ~240 points
    return Math.max(60, Math.floor(span / 240));
  }

  /**
   * Aggregate rate samples into fixed absolute time buckets:
   *   key = floor(t / bucketSec) * bucketSec
   * Completed buckets (key + bucketSec <= t1) are stable averages.
   * The open bucket containing t1 may update every poll — expected.
   */
  function aggregateHistoryBuckets(hist, t0, t1, bucketSec){
    if (!hist || !hist.length) return [];
    bucketSec = Math.max(1, Number(bucketSec) || 1);
    t0 = Number(t0) || 0;
    t1 = Number(t1) || 0;
    const map = new Map(); // key -> {sumU,sumD,n}
    for (let i = 0; i < hist.length; i++) {
      const p = hist[i];
      const t = Number(p.t) || 0;
      if (t < t0 - bucketSec || t > t1 + bucketSec) continue;
      const key = Math.floor(t / bucketSec) * bucketSec;
      let b = map.get(key);
      if (!b) {
        b = { sumU: 0, sumD: 0, n: 0 };
        map.set(key, b);
      }
      b.sumU += Math.max(0, Number(p.upRate) || 0);
      b.sumD += Math.max(0, Number(p.downRate) || 0);
      b.n += 1;
    }
    const keys = Array.from(map.keys()).sort((a, b) => a - b);
    // open bucket starts at floor(t1 / bucketSec) * bucketSec
    const openKey = Math.floor(t1 / bucketSec) * bucketSec;
    const out = [];
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      // drop buckets entirely left of window
      if (key + bucketSec < t0) continue;
      if (key > t1) continue;
      const b = map.get(key);
      const n = b.n || 1;
      // plot at bucket center — fixed for a given key (shape-stable)
      const tMid = key + bucketSec * 0.5;
      out.push({
        t: tMid,
        upRate: b.sumU / n,
        downRate: b.sumD / n,
        _bucket: key,
        _open: key === openKey,
      });
    }
    return out;
  }


  function drawStatsChart(dt){
    const canvas = document.getElementById("statsChart");
    if (!canvas || !canvas.getContext) return;
    if (canvas.style.visibility === "hidden") return;
    if (typeof dt !== "number" || !(dt > 0)) dt = 0.016;

    const parent = canvas.parentElement;
    const cssW = Math.max(300, parent ? parent.clientWidth - 8 : 900);
    const cssH = 160;
    const dpr = window.devicePixelRatio || 1;

    const geom = statsChartGeom;
    if (geom.w !== cssW || geom.h !== cssH || geom.dpr !== dpr) {
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
      geom.w = cssW; geom.h = cssH; geom.dpr = dpr;
    }

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const win = getPeriodWindow();
    const t0 = win.t0, t1 = win.t1;
    const span = Math.max(1e-3, t1 - t0);
    let hist = getHistoryForPeriod();

    // Deterministic fixed-time buckets (not index stride — stride remaps history every second)
    const bSec = bucketSecForSpan(span);
    hist = aggregateHistoryBuckets(hist, t0, t1, bSec);

    const padL = 8, padR = 8, padT = 30, padB = 18;
    const w = Math.max(1, cssW - padL - padR);
    const h = Math.max(1, cssH - padT - padB);

    let drawHist = hist;
    // Continuous right edge: always pin head to t1 (now) with lerped rates so the
    // chart scrolls smoothly every frame instead of jumping once per poll.
    if (hist.length >= 1) {
      const last = hist[hist.length - 1];
      const tipUp = statsVis.primed ? statsVis.up : (last.upRate || 0);
      const tipDown = statsVis.primed ? statsVis.down : (last.downRate || 0);
      if (t1 > (last.t || 0) + 0.001) {
        drawHist = hist.concat([{ t: t1, upRate: tipUp, downRate: tipDown }]);
      } else if (drawHist.length) {
        // same second as last sample — still show lerped tip on last point for soft peaks
        const copy = drawHist.slice();
        const L = copy[copy.length - 1];
        copy[copy.length - 1] = {
          t: L.t,
          upRate: tipUp,
          downRate: tipDown,
        };
        drawHist = copy;
      }
    }

    // sort + dedupe by time (broken order / duplicate t → bezier loops)
    if (drawHist.length > 1) {
      drawHist = drawHist.slice().sort((a, b) => (a.t || 0) - (b.t || 0));
      const cleaned = [drawHist[0]];
      for (let i = 1; i < drawHist.length; i++) {
        const prev = cleaned[cleaned.length - 1];
        const cur = drawHist[i];
        if ((cur.t || 0) - (prev.t || 0) < 0.05) {
          cleaned[cleaned.length - 1] = cur; // keep newest at same stamp
        } else {
          cleaned.push(cur);
        }
      }
      drawHist = cleaned;
    }
    statsChartLayout = { padL, padR, padT, padB, w, h, cssW, cssH, hist: drawHist, t0, t1, span };

    if (drawHist.length < 2) {
      ctx.fillStyle = "rgba(139,147,167,.55)";
      ctx.font = "12px Inter, sans-serif";
      ctx.fillText(drawHist.length ? "Нужно ещё точек…" : "Сбор точек…", padL, cssH / 2);
      ctx.fillStyle = "rgba(139,147,167,.4)";
      ctx.font = "10px Inter, sans-serif";
      ctx.fillText(formatWindowLabel(t0, t1), padL, cssH - 4);
      return;
    }

    const vals = [];
    drawHist.forEach(p => {
      if (statsLines.down) vals.push(p.downRate || 0);
      if (statsLines.up) vals.push(p.upRate || 0);
    });
    if (!vals.length) {
      ctx.fillStyle = "rgba(139,147,167,.55)";
      ctx.font = "12px Inter, sans-serif";
      ctx.fillText("Включите download или upload", padL, cssH / 2);
      return;
    }

    const targetMax = Math.max(1, ...vals);
    if (statsReduceMotion) {
      statsSmoothMax = targetMax;
    } else {
      const rise = targetMax > statsSmoothMax;
      // slower rise/fall → less vertical "pop" when a new sample arrives
      const k = rise ? (1 - Math.exp(-dt * 6)) : (1 - Math.exp(-dt * 2.2));
      statsSmoothMax += (targetMax - statsSmoothMax) * k;
      if (statsSmoothMax < 1) statsSmoothMax = 1;
    }
    const maxV = statsSmoothMax;

    ctx.strokeStyle = "rgba(255,255,255,.045)";
    ctx.lineWidth = 1;
    for (let g = 0; g < 4; g++) {
      const y = padT + (h * g) / 3;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + w, y); ctx.stroke();
    }

    function xAt(t) {
      return padL + ((t - t0) / span) * w;
    }
    function yAt(v) {
      return padT + h - (Math.min(maxV, Math.max(0, v || 0)) / maxV) * h;
    }

    function series(key, color, fillAlpha) {
      const pts = [];
      for (let i = 0; i < drawHist.length; i++) {
        const x = xAt(drawHist[i].t);
        const y = yAt(drawHist[i][key]);
        // drop non-monotonic X (would loop)
        if (pts.length && x <= pts[pts.length - 1].x + 0.01) {
          pts[pts.length - 1] = { x: Math.max(x, pts[pts.length - 1].x + 0.01), y };
          continue;
        }
        pts.push({ x, y });
      }
      if (pts.length < 2) return;

      // X-monotonic cubic: control points always between p1.x and p2.x
      // (Catmull-Rom freely moves X backward → visible loops)
      function strokeSmooth(startFromBaseline) {
        if (startFromBaseline) {
          ctx.moveTo(pts[0].x, padT + h);
          ctx.lineTo(pts[0].x, pts[0].y);
        } else {
          ctx.moveTo(pts[0].x, pts[0].y);
        }
        for (let i = 0; i < pts.length - 1; i++) {
          const p1 = pts[i];
          const p2 = pts[i + 1];
          const dx = Math.max(0, p2.x - p1.x) / 3;
          ctx.bezierCurveTo(p1.x + dx, p1.y, p2.x - dx, p2.y, p2.x, p2.y);
        }
      }

      if (fillAlpha > 0) {
        ctx.beginPath();
        strokeSmooth(true);
        const last = pts[pts.length - 1];
        ctx.lineTo(last.x, padT + h);
        ctx.closePath();
        const rgba = hexToRgba(color, fillAlpha);
        const rgba0 = hexToRgba(color, 0);
        const g2 = ctx.createLinearGradient(0, padT, 0, padT + h);
        g2.addColorStop(0, rgba);
        g2.addColorStop(1, rgba0);
        ctx.fillStyle = g2;
        ctx.fill();
      }

      ctx.beginPath();
      strokeSmooth(false);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.25;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.stroke();
      ctx.save();
      ctx.globalAlpha = 0.2;
      ctx.lineWidth = 5;
      ctx.stroke();
      ctx.restore();
    }

    if (statsLines.down) series("downRate", "#38bdf8", 0.18);
    if (statsLines.up) series("upRate", "#a78bfa", 0.14);

    if (statsHoverIdx >= 0 && statsHoverIdx < drawHist.length) {
      const p = drawHist[statsHoverIdx];
      const x = xAt(p.t);
      ctx.strokeStyle = "rgba(255,255,255,.22)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + h);
      ctx.stroke();
      function dot(key, color) {
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.arc(x, yAt(p[key]), 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.fillStyle = "rgba(255,255,255,.85)";
        ctx.arc(x, yAt(p[key]), 1.4, 0, Math.PI * 2);
        ctx.fill();
      }
      if (statsLines.down) dot("downRate", "#38bdf8");
      if (statsLines.up) dot("upRate", "#a78bfa");
    }

    ctx.fillStyle = "rgba(139,147,167,.55)";
    ctx.font = "10px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(formatTick(t0), padL, cssH - 4);
    ctx.textAlign = "right";
    ctx.fillText(formatTick(t1) + " · " + formatSpan(span), padL + w, cssH - 4);
    ctx.textAlign = "left";
  }
  function hexToRgba(hex, a){
    const h = (hex || "").replace("#", "");
    if (h.length !== 6) return "rgba(255,255,255," + a + ")";
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  }
  function formatTick(ts){
    try {
      return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch (e) { return ""; }
  }
  function formatSpan(sec){
    if (sec < 120) return Math.round(sec) + "с";
    if (sec < 3600) return Math.round(sec / 60) + "м";
    return (Math.round(sec / 360) / 10) + "ч";
  }
  function formatWindowLabel(t0, t1){
    return formatTick(t0) + " → " + formatTick(t1);
  }
function hideStatsTooltip(){
    statsHoverIdx = -1;
    const tip = document.getElementById("statsTooltip");
    if (tip) tip.hidden = true;
  }
  function showStatsTooltip(clientX, clientY){
    const canvas = document.getElementById("statsChart");
    const tip = document.getElementById("statsTooltip");
    const layout = statsChartLayout;
    if (!canvas || !tip || !layout || !layout.hist || layout.hist.length < 2) {
      hideStatsTooltip();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const { padL, w, hist, t0, span } = layout;
    const tAt = (t0 || 0) + (Math.max(0, Math.min(1, (x - padL) / Math.max(1, w)))) * (span || 1);
    let idx = 0, best = Infinity;
    for (let i = 0; i < hist.length; i++) {
      const d = Math.abs((hist[i].t || 0) - tAt);
      if (d < best) { best = d; idx = i; }
    }
    statsHoverIdx = idx;
    const p = hist[idx];
    const t = new Date((p.t || 0) * 1000);
    let rows = `<div class="tt-time">${escHtml(t.toLocaleTimeString())}</div>`;
    if (statsLines.down) {
      rows += `<div class="tt-row"><span class="tt-dot dn"></span>↓ ${escHtml(fmtRate(p.downRate || 0))}</div>`;
    }
    if (statsLines.up) {
      rows += `<div class="tt-row"><span class="tt-dot up"></span>↑ ${escHtml(fmtRate(p.upRate || 0))}</div>`;
    }
    if (!statsLines.down && !statsLines.up) { hideStatsTooltip(); return; }
    tip.innerHTML = rows;
    tip.hidden = false;
    const body = canvas.parentElement;
    const br = body.getBoundingClientRect();
    let left = clientX - br.left;
    let top = clientY - br.top;
    const tw = tip.offsetWidth || 140;
    left = Math.max(tw / 2 + 4, Math.min(br.width - tw / 2 - 4, left));
    top = Math.max(12, top);
    tip.style.left = left + "px";
    tip.style.top = top + "px";
    drawStatsChart();
  }
  function bindStatsChartUi(){
    const canvas = document.getElementById("statsChart");
    if (canvas && !canvas._statsBound) {
      canvas._statsBound = true;
      canvas.addEventListener("mousemove", (e) => showStatsTooltip(e.clientX, e.clientY));
      canvas.addEventListener("mouseleave", () => { hideStatsTooltip(); drawStatsChart(); });
    }
    const periodWrap = document.getElementById("statsPeriod");
    if (periodWrap && !periodWrap._statsBound) {
      periodWrap._statsBound = true;
      periodWrap.addEventListener("click", (e) => {
        const b = e.target && e.target.closest ? e.target.closest(".stab[data-period]") : null;
        if (!b || !periodWrap.contains(b)) return;
        e.preventDefault();
        const next = b.getAttribute("data-period") || "live";
        if (next === statsPeriod) { drawStatsChart(); return; }
        statsPeriod = next;
        saveStatsPrefs();
        applyStatsPrefsToUi();
        hideStatsTooltip();
        drawStatsChart();
      });
    }
    const ld = document.getElementById("lineDown");
    const lu = document.getElementById("lineUp");
    function onLine(){
      statsLines.down = !!(ld && ld.checked);
      statsLines.up = !!(lu && lu.checked);
      if (!statsLines.down && !statsLines.up) {
        statsLines.down = true;
        if (ld) ld.checked = true;
      }
      saveStatsPrefs();
      hideStatsTooltip();
      drawStatsChart();
    }
    if (ld && !ld._statsBound) { ld._statsBound = true; ld.addEventListener("change", onLine); }
    if (lu && !lu._statsBound) { lu._statsBound = true; lu.addEventListener("change", onLine); }
  }

  document.getElementById("viewRules")?.addEventListener("click", () => setView("rules"));
  document.getElementById("viewStats")?.addEventListener("click", () => setView("stats"));
  document.getElementById("viewConns")?.addEventListener("click", () => setView("conns"));
  document.getElementById("viewLogs")?.addEventListener("click", () => setView("logs"));
  // delegation fallback (if ids missing after hot patch)
  document.getElementById("viewSwitch")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]");
    if (!b || !b.closest("#viewSwitch")) return;
    const v = b.getAttribute("data-view");
    if (v) setView(v);
  });
  window.addEventListener("resize", () => {
    moveViewPill();
    moveStatsPeriodPill();
    if (currentView === "stats") drawStatsChart();
  });

  // restore view: ?view=stats wins, else localStorage
  (function initView(){
    let initial = "rules";
    try {
      const saved = localStorage.getItem(VIEW_KEY);
      if (saved === "stats" || saved === "rules" || saved === "conns" || saved === "logs") initial = saved;
    } catch (e) {}
    // drop legacy ?view= from address bar (state is localStorage only)
    try {
      const url = new URL(location.href);
      if (url.searchParams.has("view")) {
        url.searchParams.delete("view");
        const q = url.searchParams.toString();
        history.replaceState(null, "", url.pathname + (q ? "?" + q : "") + url.hash);
      }
    } catch (e) {}
    setView(initial, { persist: false });
  if (currentView === "rules") reviveRulesCards();

    requestAnimationFrame(() => moveViewPill());
  })();

  searchInput.addEventListener("input", () => applyListFilter());

  document.getElementById("conflictFilter")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    conflictFilterOn = !conflictFilterOn;
    updateConflictFilterLink();
    applyListFilter();
    // drop browser focus ring immediately after toggle
    try { e.currentTarget.blur(); } catch (_) {}
  });

  let tmr;
  let importPreviewItems = [];

  // static type pills (same palette as main board)
  const IMPORT_TYPE_LAB = { ipv4: "IPv4", ipv6: "IPv6", namespace: "Namespace", domain: "Domain", keyword: "Keyword" };
  const IMPORT_TYPE_CLS = { ipv4: "badge-v4", ipv6: "badge-v6", namespace: "badge-ns", domain: "badge-dom", keyword: "badge-kw" };

  function collectExistingRules(){
    const out = [];
    document.querySelectorAll(".gcard").forEach(card => {
      const gid = card.dataset.gid || "";
      const gname = (card.querySelector(".gcard-name")?.value || "").trim() || gid;
      card.querySelectorAll(".rule-row:not(.draft)").forEach(row => {
        const val = (row.querySelector("[name=value]")?.value || "").trim();
        if (!val) return;
        out.push({
          value: normalizePattern(val),
          raw: val,
          gid,
          gname,
        });
      });
    });
    return out;
  }

  function classifyImportItem(it, targetGid, existing){
    const key = normalizePattern(it.value);
    if (!key) return { status: "new", other: null };
    const matches = existing.filter(e => e.value === key);
    const inTarget = matches.find(e => e.gid === targetGid);
    if (inTarget) return { status: "dup", other: inTarget };
    const other = matches.find(e => e.gid !== targetGid);
    if (other) return { status: "conflict", other };
    return { status: "new", other: null };
  }

  function renderImportPreview(){
    if (!previewBox || !previewStats) return;
    const items = importPreviewItems;
    const optWrap = document.getElementById("importOptWrap");
    const skipCb = document.getElementById("importSkipDupConflict");
    const submitBtn = document.getElementById("importSubmitBtn");
    const skipDupConf = !!(skipCb && skipCb.checked);
    if (!items.length) {
      previewBox.hidden = true;
      previewStats.innerHTML = "";
      if (optWrap) optWrap.hidden = true;
      if (submitBtn) submitBtn.textContent = "Импортировать";
      return;
    }
    const targetGid = importGid?.value || "";
    const existing = collectExistingRules();
    const counts = {};
    let nNew = 0, nDup = 0, nConf = 0;
    const classified = items.map(it => {
      counts[it.type] = (counts[it.type] || 0) + 1;
      const c = classifyImportItem(it, targetGid, existing);
      if (c.status === "dup") nDup++;
      else if (c.status === "conflict") nConf++;
      else nNew++;
      return { it, ...c };
    });

    if (optWrap) optWrap.hidden = false;

    const typePills = Object.keys(counts).map(k => {
      const lab = IMPORT_TYPE_LAB[k] || k;
      const cls = IMPORT_TYPE_CLS[k] || "badge-ns";
      return `<span class="pill ${cls}">${lab} ${counts[k]}</span>`;
    }).join("");
    const total = items.length;
    previewStats.innerHTML =
      `<div class="preview-stats-types">${typePills}<span class="preview-stats-summary">· <strong>${total}</strong> правил будет добавлено</span></div>` +
      `<div class="preview-stats-summary"><strong>${nNew}</strong> новых · <strong>${nDup}</strong> дубля · <strong>${nConf}</strong> конфликт${nConf === 1 ? "" : "ов"}</div>`;

    if (submitBtn) {
      submitBtn.textContent = skipDupConf ? (`Импортировать (${nNew})`) : "Импортировать";
    }

    const maxShow = 100;
    previewBox.hidden = false;
    previewBox.innerHTML = classified.slice(0, maxShow).map((row, i) => {
      const it = row.it;
      const typePill = `<span class="pill ${IMPORT_TYPE_CLS[it.type] || "badge-ns"}">${IMPORT_TYPE_LAB[it.type] || it.type || ""}</span>`;
      let statusHtml = "";
      let rowCls = "preview-row";
      if (row.status === "dup") {
        statusHtml = `<span class="pill pill-status pill-dup" title="Уже есть в этой группе">уже есть</span>`;
        rowCls += " is-dup";
      } else if (row.status === "conflict") {
        const on = row.other?.gname || "другая группа";
        statusHtml = `<span class="pill pill-status pill-conflict" title="Совпадает с правилом в другой группе">конфликт: ${escHtml(on)}</span>`;
        rowCls += " is-conflict";
      }
      if (skipDupConf && (row.status === "dup" || row.status === "conflict")) {
        rowCls += " will-skip";
        statusHtml += `<span class="preview-skip-note">— будет пропущено</span>`;
      }
      return `<div class="${rowCls}" style="--pi:${Math.min(i, 12)};animation-delay:${Math.min(i * 12, 200)}ms">
        <span class="preview-val" title="${escHtml(it.value)}">${escHtml(it.value)}</span>
        ${typePill}
        ${statusHtml}
      </div>`;
    }).join("") + (items.length > maxShow
      ? `<div class="preview-row" style="opacity:1;animation:none"><span class="meta">… и ещё ${items.length - maxShow}</span></div>`
      : "");
  }

  let importPreviewSerial = 0;
  async function refreshPreview(){
    const serial = ++importPreviewSerial;
    const text = importText.value || "";
    if (!text.trim()) {
      importPreviewItems = [];
      previewBox.hidden = true;
      previewStats.innerHTML = "";
      return;
    }
    const mode = document.getElementById("detectMode").value;
    try {
      const data = await rulesAPI.detect(text, mode);
      if (serial !== importPreviewSerial || importText.value !== text || document.getElementById("detectMode").value !== mode) return;
      importPreviewItems = data.items;
      renderImportPreview();
      if (data.invalid) {
        markRuleError(importText, "Не распознано строк: " + data.invalid);
        previewStats.append(document.createTextNode(" · Не распознано строк: " + data.invalid));
      } else clearRuleError(importText);
    } catch (error) {
      if (serial !== importPreviewSerial || importText.value !== text) return;
      importPreviewItems = [];
      previewBox.hidden = true;
      previewStats.textContent = error.message;
    }
  }

  if (importText) {
    importText.addEventListener("input", () => { clearTimeout(tmr); tmr = setTimeout(refreshPreview, 250); });
    document.getElementById("detectMode")?.addEventListener("change", refreshPreview);
  }
  document.getElementById("importSkipDupConflict")?.addEventListener("change", () => {
    renderImportPreview();
  });
  function ruleInputStamp(){
    return JSON.stringify([...document.querySelectorAll("input,select,textarea")].map(control => [
      control.name || control.id, control.type === "checkbox" ? control.checked : control.value
    ]));
  }
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    const path = form.getAttribute("action");
    if (!["/api/groups/create", "/api/groups/save", "/api/groups/delete", "/api/groups/import", "/api/rules/apply"].includes(path)) return;
    e.preventDefault();
    if (rulePending || transport.isBusy()) { toast("Дождитесь завершения текущего запроса", false); return; }
    const fields = new FormData(form);
    const gid = fields.get("gid");
    if (path === "/api/rules/apply") {
      if (hasRuleDrafts()) {
        if (!confirm("Сохранить правки перед применением правил?")) return;
        for (const card of document.querySelectorAll(".gcard")) {
          if (!await saveRows(card, card.querySelectorAll(".rule-row"))) return;
        }
        if (hasRuleDrafts()) { toast("Сначала сохраните или отмените изменения группы и текста импорта", false); return; }
      }
      if (!confirm("Применить сохранённые правила и перезапустить mihomo?")) return;
    } else {
      guardedRuleForm = form;
      let allowed;
      try { allowed = confirmDiscardChanges(); } finally { guardedRuleForm = null; }
      if (!allowed) return;
      if (path === "/api/groups/delete" && !confirm("Удалить группу?")) return;
    }
    const text = path === "/api/groups/import" ? importText.value : "";
    const detect = document.getElementById("detectMode")?.value || "auto";
    const skip = !!document.getElementById("importSkipDupConflict")?.checked;
    const stamp = ruleInputStamp();
    const button = e.submitter || form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    rulePending++;
    let data;
    try {
      if (path === "/api/groups/create") data = await rulesAPI.createGroup(fields.get("name"));
      else if (path === "/api/groups/delete") data = await rulesAPI.deleteGroup(gid);
      else if (path === "/api/groups/import") data = await rulesAPI.importGroup(gid, text, detect, skip);
      else if (path === "/api/rules/apply") data = await rulesAPI.applyRules();
      else data = await rulesAPI.saveGroup(gid, form.dataset.groupAction === "toggle"
        ? {enabled: form.querySelector("[name=enabled]").checked ? "1" : "0"} : {name: fields.get("name")});
    } catch (error) {
      const control = path === "/api/groups/import" ? importText : form.querySelector("input:not([type=hidden])");
      markRuleError(control, error.message);
      control?.reportValidity();
      toast(error.message, false);
      return;
    } finally {
      rulePending--;
      if (button) button.disabled = false;
    }
    if (path === "/api/rules/apply") { toast("Правила применены", true); return; }
    if (path === "/api/groups/import" && data.invalid) {
      const message = "Импортировано: " + data.imported + " · пропущено: " + data.skipped + " · не распознано: " + data.invalid;
      markRuleError(importText, message);
      importText.reportValidity();
      toast(message, false);
      return;
    }
    const edited = stamp !== ruleInputStamp();
    if (path === "/api/groups/import" && importText.value === text) importText.value = "";
    if (path === "/api/groups/create" && form.elements.name.value === fields.get("name")) form.reset();
    if (path === "/api/groups/save") {
      const card = form.closest(".gcard");
      const saved = groupSnapshots.get(card);
      if (form.dataset.groupAction === "toggle") {
        saved.enabled = fields.has("enabled");
        card.classList.toggle("disabled", !saved.enabled);
      } else saved.name = fields.get("name");
    }
    if (edited && !confirmDiscardChanges()) {
      toast("Сохранено. Новые изменения оставлены в этой странице.", true);
      return;
    }
    reloadPanel();
  });

  // initial cross-group conflict markers (after DOM ready)
  scheduleConflictScan();

  // ── Settings ──
  const settingsBd = document.getElementById("settingsBd");
  const btnSettings = document.getElementById("btnSettings");
  const xttpDot = document.getElementById("xttpDot");
  let usersMe = "";
  let usersMeId = "";

  function resetXttpHealthUi(){
    const pill = document.getElementById("xttpStatusPill");
    if (pill) {
      pill.className = "status-pill unk";
      pill.textContent = "Не проверено";
    }
    const mTcp = document.getElementById("mTcp");
    const mHttp = document.getElementById("mHttp");
    const mSpeed = document.getElementById("mSpeed");
    function setEmpty(el){
      if (!el) return;
      el.textContent = "не измерено";
      el.classList.add("empty-metric");
    }
    setEmpty(mTcp); setEmpty(mHttp); setEmpty(mSpeed);
  }
  function setMetric(el, text, empty){
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("empty-metric", !!empty);
  }
  function openSettings(){
    try { const c = document.getElementById("statsChart"); if (c) c.style.visibility = "hidden"; } catch (e) {}
    settingsBd.classList.add("open");
    settingsBd.setAttribute("aria-hidden","false");
    document.getElementById("settingsClose")?.focus({preventScroll:true});
    syncMonitoringVisibility();
    try { document.body.classList.add("settings-open"); } catch (_) {}
    resetXttpHealthUi();
    const placeSettingsPill = (instant) => {
      moveStabPill(document.querySelector(".settings-nav .stab[data-stab].active"), instant);
    };
    // layout may not be final until modal is painted + fade finishes (~300ms)
    requestAnimationFrame(() => {
      placeSettingsPill(true);
      requestAnimationFrame(() => placeSettingsPill(true));
    });
    setTimeout(() => placeSettingsPill(true), 50);
    setTimeout(() => placeSettingsPill(false), 320);
    loadUsers();
    loadXttpStatus();
    loadBackups();
    loadOps();
    if (typeof loadCoreSettings === "function") loadCoreSettings();
    try { if (typeof setupCoreVersionChips === "function") setupCoreVersionChips(); } catch(_) {}
    try { if (typeof loadCoreVersions === "function") loadCoreVersions(false); } catch(_) {}
    if (typeof setupCoreTips === "function") setupCoreTips();
    // auto «Проверить связь» when opening settings (quiet — no toast spam)
    if (typeof runXttpCheck === "function") runXttpCheck({ quiet: true });
    if (!updatePoll) resumeUpdateOperation();
  }
  function closeSettings(){
    settingsBd.classList.remove("open");
    settingsBd.setAttribute("aria-hidden","true");
    try { document.body.classList.remove("settings-open"); } catch (_) {}
    try { const c = document.getElementById("statsChart"); if (c) c.style.visibility = ""; } catch (e) {}
    syncMonitoringVisibility();
    setupActivityPoll(false);
    btnSettings?.focus({preventScroll:true});
  }
  btnSettings?.addEventListener("click", openSettings);
  document.getElementById("settingsClose")?.addEventListener("click", closeSettings);
  settingsBd?.addEventListener("click", e => { if (e.target === settingsBd) closeSettings(); });

  function moveStabPill(btn, instant){
    // only the Settings tab pill ? never the main Rules/Stats view-switch
    const pill = document.getElementById("stabPill");
    const wrap = document.querySelector(".settings-nav .stabs");
    if (!btn || !btn.dataset || !btn.dataset.stab) {
      btn = wrap?.querySelector(".stab[data-stab].active") || wrap?.querySelector(".stab[data-stab]");
    }
    if (!pill || !wrap || !btn) return;
    const wr = wrap.getBoundingClientRect();
    const br = btn.getBoundingClientRect();
    // wrap has padding:4px ? pill is left:4px, so offset = btn.left - wrap.left - 4
    const x = br.left - wr.left - 4;
    if (instant) {
      const prev = pill.style.transition;
      pill.style.transition = "none";
      pill.style.width = br.width + "px";
      pill.style.transform = "translateX(" + x + "px)";
      void pill.offsetWidth;
      pill.style.transition = prev || "";
    } else {
      pill.style.width = br.width + "px";
      pill.style.transform = "translateX(" + x + "px)";
    }
  }
  // settings tabs only (data-stab) ? do NOT touch main view-switch .stab[data-view]
  document.querySelectorAll(".stab[data-stab]").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".stab[data-stab]").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".spanel").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    document.getElementById("spanel-" + b.dataset.stab)?.classList.add("active");
    b.scrollIntoView({block:"nearest",inline:"nearest"});
    moveStabPill(b);
    if (b.dataset.stab === "core" && typeof loadCoreSettings === "function") {
      loadCoreSettings();
    }
    if (b.dataset.stab === "xttp" && typeof runXttpCheck === "function") {
      runXttpCheck({ quiet: true });
    }
    if (b.dataset.stab === "ops" && typeof loadActivity === "function") {
      loadActivity({ soft: true });
      if (typeof setupActivityPoll === "function") setupActivityPoll(true);
      if (typeof updatePingAgeUi === "function") updatePingAgeUi();
    }
  }));
  // init pill under active tab
  requestAnimationFrame(() => moveStabPill(document.querySelector(".stab.active")));
  window.addEventListener("resize", () => {
    if (settingsBd?.classList.contains("open")) {
      const active = document.querySelector(".settings-nav .stab[data-stab].active");
      active?.scrollIntoView({block:"nearest",inline:"nearest"});
      moveStabPill(active, true);
    }
  });

  async function loadUsers(){
    const body = document.getElementById("usersBody");
    if (body) {
      body.innerHTML = `<tr><td colspan="4">
        <div class="skel-card"><div class="skel w40"></div><div class="skel w70"></div><div class="skel w90"></div></div>
      </td></tr>`;
    }
    try {
      const res = await fetch("/api/users", {credentials:"same-origin"});
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || "Не удалось загрузить пользователей");
      usersMe = d.me || "";
      usersMeId = (d.users || []).find(user => user.username === usersMe)?.id || "";
      if (!body) return;
      body.innerHTML = (d.users || []).map((u, i) => {
        const on = u.enabled !== false;
        const self = u.username === usersMe;
        return `<tr style="--ri:${Math.min(i,10)}">
          <td><strong>${escHtml(u.username)}</strong>${self ? ' <span class="meta">(вы)</span>' : ''}</td>
          <td>${on ? '<span class="badge-on">ON</span>' : '<span class="badge-off">OFF</span>'}</td>
          <td class="meta">${escHtml(u.last_login || "—")}</td>
          <td style="white-space:nowrap">
            <button type="button" class="mini-btn icon-neutral" data-upw="${escHtml(u.id)}" title="Сменить пароль" aria-label="Сменить пароль">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 17v.01"/><rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>
            </button>
            <button type="button" class="mini-btn icon-neutral" data-utog="${escHtml(u.id)}" title="${on?"Заблокировать":"Разблокировать"}" aria-label="${on?"Заблокировать":"Разблокировать"}" ${self?"disabled":""}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${on
                ? '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0"/>'
                : '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.5-1"/>'}</svg>
            </button>
            <button type="button" class="mini-btn icon-danger-only" data-udel="${escHtml(u.id)}" title="Удалить пользователя" aria-label="Удалить" ${self?"disabled":""}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>
            </button>
          </td>
        </tr>`;
      }).join("") || '<tr><td colspan="4" class="meta">Нет пользователей</td></tr>';
    } catch(e) { toast(String(e), false); }
  }
  function escHtml(s){
    return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");
  }

  document.getElementById("btnUserAdd")?.addEventListener("click", () => {
    document.getElementById("userAddForm").style.display = "block";
  });
  document.getElementById("userAddCancel")?.addEventListener("click", () => {
    document.getElementById("userAddForm").style.display = "none";
  });
  document.getElementById("userAddForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const d = await api("/api/users/add", {
      username: fd.get("username"),
      password: fd.get("password")
    });
    if (!d.ok) return toast(d.error || "Ошибка", false);
    toast("Пользователь создан", true);
    e.target.reset();
    document.getElementById("userAddForm").style.display = "none";
    loadUsers();
  });
  document.getElementById("usersBody")?.addEventListener("click", async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.upw) {
      const pw = prompt("Новый пароль (мин. 6):");
      if (!pw) return;
      if (t.dataset.upw === usersMeId && !confirmDiscardChanges()) return;
      const d = await api("/api/users/password", {id: t.dataset.upw, password: pw});
      toast(d.ok ? "Пароль обновлён" : (d.error || "Ошибка"), !!d.ok);
      if (d.ok && d.relogin) {
        leavingPanel = true;
        location.replace("/login");
        return;
      }
    }
    if (t.dataset.utog) {
      const d = await api("/api/users/toggle", {id: t.dataset.utog});
      if (!d.ok) return toast(d.error || "Ошибка", false);
      loadUsers();
    }
    if (t.dataset.udel) {
      if (!confirm("Удалить пользователя?")) return;
      const d = await api("/api/users/delete", {id: t.dataset.udel});
      if (!d.ok) return toast(d.error || "Ошибка", false);
      toast("Удалён", true);
      loadUsers();
    }
  });

  function renderXttpKv(st){
    const o = st.outbound || {};
    const rows = [
      ["Endpoint", o.address ? `${o.address}:${o.port}` : "—"],
      ["Протокол", o.ok ? `VLESS · ${o.security || "?"} · ${o.network || "?"}` : "—"],
      ["UUID", o.uuid_short || "—"],
      ["SNI", o.sni || "—"],
      ["Public key", o.public_key_short || "—"],
      ["shortId", o.short_id || "—"],
      ["Path / mode", `${o.path || "—"} / ${o.mode || "—"}`],
      ["Local socks", st.socks || "—"],
      ["mihomo", st.mihomo_proxy || "xttp"],
    ];
    document.getElementById("xttpKv").innerHTML = rows.map(([k,v]) =>
      `<dt>${escHtml(k)}</dt><dd>${escHtml(v)}</dd>`).join("");
    document.getElementById("xttpServices").textContent =
      `xray: ${st.xray_active ? "active" : "down"} · mihomo: ${st.mihomo_active ? "active" : "down"}`;
  }

  async function loadXttpStatus(){
    try {
      const res = await fetch("/api/xttp/status", {credentials:"same-origin"});
      const st = await res.json();
      if (!res.ok || st.ok === false) throw new Error(st.error || "Не удалось загрузить состояние");
      renderXttpKv(st);
    } catch(e) { toast(e.message || String(e), false); }
  }
  async function loadBackups(){
    try {
      const res = await fetch("/api/xttp/backups", {credentials:"same-origin"});
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || "Не удалось загрузить резервные копии");
      const el = document.getElementById("backupList");
      const list = d.backups || [];
      if (!list.length) { el.textContent = "пока нет"; return; }
      el.innerHTML = list.map(n =>
        `<div style="display:flex;align-items:center;gap:8px;margin:4px 0">
          <span class="mono">${escHtml(n)}</span>
          <button type="button" class="btn btn-ghost" data-rb="${escHtml(n)}" style="padding:4px 10px;font-size:12px">Откатить</button>
        </div>`).join("");
    } catch(e) { toast(e.message || String(e), false); }
  }
  document.getElementById("backupList")?.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-rb]");
    if (!b) return;
    if (!confirm("Откатить xray на " + b.dataset.rb + "?")) return;
    const d = await api("/api/xttp/rollback", {name: b.dataset.rb, confirm:1});
    if (!d.ok) return toast(d.error || "Ошибка", false);
    toast(d.http?.ok === true ? "Откат выполнен · HTTP OK"
      : "Конфигурация восстановлена; HTTP-проверка не подтверждена", d.http?.ok === true);
    loadXttpStatus();
    refreshXttpDot();
  });

  document.getElementById("btnXttpRefresh")?.addEventListener("click", () => { loadXttpStatus(); loadBackups(); });
  document.getElementById("btnXttpPing")?.addEventListener("click", () => runXttpCheck({ quiet: false }));
  document.getElementById("btnXttpSpeed")?.addEventListener("click", async () => {
    setMetric(document.getElementById("mSpeed"), "…", true);
    toast("Speedtest…", true);
    const d = await api("/api/xttp/speedtest", {});
    if (!d.ok) {
      setMetric(document.getElementById("mSpeed"), "FAIL", true);
      return toast(d.error || "Speedtest fail", false);
    }
    setMetric(document.getElementById("mSpeed"), d.mbps + " Mbps", false);
    toast("Speed: " + d.mbps + " Mbps", true);
  });
  document.getElementById("vlessIn")?.addEventListener("input", () => {
    const preview = document.getElementById("vlessPreview");
    if (preview) preview.textContent = "";
  });
  document.getElementById("btnVlessPreview")?.addEventListener("click", async () => {
    const source = document.getElementById("vlessIn").value;
    const d = await api("/api/xttp/preview", {vless:source});
    if (document.getElementById("vlessIn").value !== source) return;
    const el = document.getElementById("vlessPreview");
    if (!d.ok) { el.textContent = d.error || "ошибка"; return; }
    const p = d.preview || {};
    el.textContent = `${p.address}:${p.port} · ${p.network}/${p.security} · SNI ${p.sni || "—"} · ${p.uuid_short}`;
  });
  document.getElementById("btnVlessApply")?.addEventListener("click", async () => {
    if (!confirm("Применить новый vless:// ? Текущий xray уйдёт в backup.")) return;
    const input = document.getElementById("vlessIn");
    const source = input.value;
    const d = await api("/api/xttp/apply", {vless:source, confirm:1});
    if (!d.ok) return toast(d.error || "Ошибка apply", false);
    toast("Применено · backup " + (d.backup || "") + (d.http_ok ? " · HTTP OK" : " · HTTP fail"), !!d.http_ok);
    if (input.value === source) {
      input.value = "";
      document.getElementById("vlessPreview").textContent = "";
    }
    loadXttpStatus();
    loadBackups();
    refreshXttpDot();
  });

  const svcDot = document.getElementById("svcDot");
  let autoPingTimer = null;
  let lastAutoPingAt = 0;
  let xttpCheckInFlight = null;

  function setXttpDot(up){
    if (!xttpDot) return;
    xttpDot.classList.add("chip-status");
    xttpDot.classList.remove("up","down");
    xttpDot.classList.add(up ? "up" : "down");
    xttpDot.textContent = up ? "xttp · UP" : "xttp · DOWN";
  }

  /** Full link check: metrics + pill + header chip. quiet=true skips toasts (auto). */
  async function runXttpCheck({ quiet = false, soft = false } = {}) {
    if (xttpCheckInFlight) return xttpCheckInFlight;
    xttpCheckInFlight = (async () => {
      const mTcp = document.getElementById("mTcp");
      const mHttp = document.getElementById("mHttp");
      const pill = document.getElementById("xttpStatusPill");
      if (!soft) {
        setMetric(mTcp, "…", true);
        setMetric(mHttp, "…", true);
        if (pill) {
          pill.className = "status-pill unk";
          pill.textContent = "Проверка…";
        }
      }
      try {
        const d = await api("/api/xttp/ping", {});
        if (!d.ok && d.error) {
          if (pill) {
            pill.className = "status-pill down";
            pill.textContent = "DOWN · ошибка";
          }
          if (xttpDot) {
            xttpDot.textContent = "xttp · ?";
            xttpDot.classList.remove("up", "down");
          }
          if (!quiet) toast(d.error, false);
          return d;
        }
        setMetric(mTcp, d.tcp?.ok ? (d.tcp.latency_ms + " ms") : "FAIL", !d.tcp?.ok);
        setMetric(mHttp, d.http?.ok ? (d.http.latency_ms + " ms") : "FAIL", !d.http?.ok);
        if (pill) {
          pill.className = "status-pill " + (d.up ? "up" : "down");
          pill.textContent = d.up ? "UP · нода доступна" : "DOWN · нет связи";
        }
        setXttpDot(!!d.up);
        if (d.services) setSvcDot(!!d.services.xray, !!d.services.mihomo);
        else if (d.xray_active !== undefined) setSvcDot(!!d.xray_active, !!d.mihomo_active);
        lastAutoPingAt = Date.now();
        if (typeof updatePingAgeUi === "function") updatePingAgeUi();
        if (!quiet) {
          if (!d.up) toast(d.http?.error || d.tcp?.error || "Нет связи с нодой", false);
          else toast("Связь OK", true);
        }
        return d;
      } catch (e) {
        if (xttpDot) {
          xttpDot.textContent = "xttp · ?";
          xttpDot.classList.remove("up", "down");
        }
        if (pill) {
          pill.className = "status-pill down";
          pill.textContent = "DOWN · ошибка";
        }
        if (!quiet) toast(String(e), false);
        return { ok: false, error: String(e) };
      } finally {
        xttpCheckInFlight = null;
      }
    })();
    return xttpCheckInFlight;
  }
  /** Status card value + colored dot (ok | down | stale). */
  function setOpsStat(valId, dotId, text, state){
    const el = document.getElementById(valId);
    const dot = document.getElementById(dotId);
    if (el) {
      el.textContent = text;
      el.className = "ops-stat-val" + (state === "stale" && (text === "…" || text === "нет данных") ? " empty-metric" : "");
    }
    if (dot) dot.className = "ops-stat-dot " + (state || "stale");
  }
  function formatRelRu(sec){
    const s = Math.max(0, Math.round(sec));
    if (s < 5) return "только что";
    if (s < 60) return s + " сек назад";
    const m = Math.floor(s / 60);
    if (m < 60) return m + " мин назад";
    const h = Math.floor(m / 60);
    if (h < 48) return h + " ч назад";
    const d = Math.floor(h / 24);
    return d + " дн назад";
  }
  function updatePingAgeUi(){
    const val = document.getElementById("svcPingAge");
    if (!val) return;
    if (!lastAutoPingAt) {
      setOpsStat("svcPingAge", "svcPingDot", "нет данных", "stale");
      return;
    }
    const sec = Math.round((Date.now() - lastAutoPingAt) / 1000);
    const interval = Math.max(15, parseInt(document.getElementById("autoPingSec")?.value, 10) || 45);
    const staleAfter = Math.max(interval * 2, 90);
    const state = sec <= staleAfter ? "ok" : "stale";
    setOpsStat("svcPingAge", "svcPingDot", formatRelRu(sec), state);
  }
  function updateJournalAgeLabel(){
    const el = document.getElementById("journalAge");
    if (!el) return;
    if (!lastActivityFetchAt) { el.textContent = ""; return; }
    const sec = Math.round((Date.now() - lastActivityFetchAt) / 1000);
    if (sec < 3) el.textContent = "обновлено только что";
    else if (sec < 60) el.textContent = "обновлено " + sec + " сек назад";
    else el.textContent = "обновлено " + formatRelRu(sec).replace(/^только что$/, "только что");
  }

  function setSvcDot(xrayOn, mihomoOn){
    if (svcDot) {
      const ok = !!xrayOn && !!mihomoOn;
      svcDot.classList.add("chip-status");
      svcDot.classList.remove("up","down");
      svcDot.classList.add(ok ? "up" : "down");
      if (ok) svcDot.textContent = "svc · OK";
      else {
        const parts = [];
        if (!xrayOn) parts.push("xray↓");
        if (!mihomoOn) parts.push("mihomo↓");
        svcDot.textContent = "svc · " + (parts.join(" ") || "DOWN");
      }
    }
    setOpsStat("svcXray", "svcXrayDot", xrayOn ? "Active" : "Down", xrayOn ? "ok" : "down");
    setOpsStat("svcMihomo", "svcMihomoDot", mihomoOn ? "Active" : "Down", mihomoOn ? "ok" : "down");
  }
  async function refreshServices(){
    try {
      const res = await fetch("/api/services/status", {credentials:"same-origin"});
      const d = await res.json();
      setSvcDot(!!d.xray, !!d.mihomo);
    } catch(e) {
      if (svcDot) { svcDot.textContent = "svc · ?"; svcDot.classList.remove("up","down"); }
      setOpsStat("svcXray", "svcXrayDot", "…", "stale");
      setOpsStat("svcMihomo", "svcMihomoDot", "…", "stale");
    }
  }
  async function refreshXttpDot(){
    // soft: update chip + metrics without flashing «…»
    await runXttpCheck({ quiet: true, soft: true });
  }

  /* Activity: labels + semantic dot kind (constants, not per-row hardcode) */
  const ACTIVITY_LABELS = {
    apply: "apply rules",
    restart: "restart",
    vless_apply: "vless apply",
    vless_rollback: "vless rollback",
    group_add: "group add",
    group_delete: "group delete",
    group_rename: "group rename",
    group_toggle: "group toggle",
    user_add: "user add",
    user_delete: "user delete",
    user_toggle: "user toggle",
    user_password: "user password",
    prefs: "prefs",
    update: "update",
    fleet_check: "fleet check",
    fleet_update: "fleet update",
    fleet_skip: "fleet skip",
    fleet_trigger: "fleet trigger",
  };
  /** action → kind-ok | kind-danger | kind-neutral */
  const ACTIVITY_DOT_KIND = {
    apply: "ok",
    restart: "ok",
    vless_apply: "ok",
    vless_rollback: "ok",
    update: "ok",
    fleet_check: "ok",
    fleet_update: "ok",
    fleet_trigger: "neutral",
    fleet_skip: "danger",
    group_delete: "danger",
    user_delete: "danger",
    user_toggle: "danger",
    group_add: "neutral",
    group_rename: "neutral",
    group_toggle: "neutral",
    user_add: "neutral",
    user_password: "neutral",
    prefs: "neutral",
  };
  function activityDotKind(action, ok){
    if (ok === false) return "danger";
    return ACTIVITY_DOT_KIND[action] || "neutral";
  }
  function activityItemKey(it){
    return [it.ts || "", it.action || "", it.user || "", it.detail || "", String(it.ok)].join("|");
  }
  let activityKnownKeys = [];
  let lastActivityFetchAt = 0;
  let activityPollTimer = null;

  function buildActivityRowHtml(it, isNew){
    const act = ACTIVITY_LABELS[it.action] || it.action || "?";
    const kind = activityDotKind(it.action, it.ok);
    const key = activityItemKey(it);
    return `<div class="activity-row${isNew ? " is-new" : ""}${it.ok === false ? " fail" : ""}" data-act-key="${escHtml(key)}">
      <span class="activity-dot kind-${kind}" aria-hidden="true"></span>
      <div>
        <div class="activity-top">
          <span class="activity-act">${escHtml(act)}</span>
          <span class="activity-user">${escHtml(it.user || "")}</span>
          <span class="activity-ts">${escHtml(it.ts || "")}</span>
        </div>
        <div class="activity-detail">${escHtml(it.detail || "")}</div>
      </div>
    </div>`;
  }
  function renderActivity(items, opts){
    const el = document.getElementById("activityList");
    if (!el) return;
    const soft = !!(opts && opts.soft);
    if (!items || !items.length) {
      el.innerHTML = '<div class="activity-row"><div></div><div class="activity-detail">Пока пусто — действия появятся здесь</div></div>';
      activityKnownKeys = [];
      return;
    }
    const keys = items.map(activityItemKey);
    if (soft && activityKnownKeys.length) {
      if (keys.join("\n") === activityKnownKeys.join("\n")) return; // unchanged
      const firstOld = keys.indexOf(activityKnownKeys[0]);
      const tailMatch = firstOld > 0 &&
        keys.slice(firstOld).join("\n") === activityKnownKeys.join("\n");
      if (tailMatch) {
        const chunk = items.slice(0, firstOld).map(it => buildActivityRowHtml(it, true)).join("");
        el.insertAdjacentHTML("afterbegin", chunk);
        // drop is-new after anim so re-poll doesn't keep class forever
        setTimeout(() => {
          el.querySelectorAll(".activity-row.is-new").forEach(n => n.classList.remove("is-new"));
        }, 250);
        activityKnownKeys = keys;
        return;
      }
    }
    el.innerHTML = items.map(it => buildActivityRowHtml(it, false)).join("");
    activityKnownKeys = keys;
  }
  async function loadActivity(opts){
    const soft = !!(opts && opts.soft);
    try {
      const res = await fetch("/api/activity", {credentials:"same-origin"});
      const d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || "Не удалось загрузить журнал");
      renderActivity(d.items || [], { soft });
      lastActivityFetchAt = Date.now();
      updateJournalAgeLabel();
    } catch(e) {
      if (!soft) {
        const el = document.getElementById("activityList");
        if (el) el.textContent = "не удалось загрузить лог";
      }
    }
  }
  function setupActivityPoll(on){
    if (activityPollTimer) { clearInterval(activityPollTimer); activityPollTimer = null; }
    if (!on) return;
    activityPollTimer = setInterval(() => {
      if (document.hidden || !settingsBd?.classList.contains("open")
          || !document.getElementById("spanel-ops")?.classList.contains("active")) return;
      loadActivity({ soft: true });
      updateJournalAgeLabel();
      updatePingAgeUi();
    }, 8000);
  }
  async function loadPrefs(){
    try {
      const res = await fetch("/api/prefs", {credentials:"same-origin"});
      const d = await res.json();
      const p = d.prefs || {};
      const t = document.getElementById("autoPingToggle");
      const s = document.getElementById("autoPingSec");
      if (t) t.checked = !!p.auto_ping;
      if (s) s.value = p.auto_ping_sec || 45;
      setupAutoPing(!!p.auto_ping, p.auto_ping_sec || 45);
    } catch(e) {}
  }
  function setupAutoPing(on, sec){
    if (autoPingTimer) { clearInterval(autoPingTimer); autoPingTimer = null; }
    if (!on) return;
    const ms = Math.max(15, Math.min(300, parseInt(sec, 10) || 45)) * 1000;
    autoPingTimer = setInterval(() => {
      if (!document.hidden && settingsBd?.classList.contains("open")
          && document.getElementById("spanel-xttp")?.classList.contains("active")) refreshXttpDot();
    }, ms);
  }
  async function loadOps(){
    await refreshServices();
    await loadActivity({ soft: false });
    await loadPrefs();
    updatePingAgeUi();
    setupActivityPoll(true);
  }
  document.getElementById("btnRefreshOps")?.addEventListener("click", () => loadOps());
  document.getElementById("btnRefreshLog")?.addEventListener("click", () => loadActivity({ soft: false }));
  document.getElementById("btnRestartXray")?.addEventListener("click", async () => {
    if (!confirm("Restart xray?")) return;
    const d = await api("/api/services/restart", {service:"xray", confirm:1});
    toast(d.ok ? "xray restarted" : (d.error || "fail"), !!d.ok);
    loadOps();
    refreshXttpDot();
  });
  document.getElementById("btnRestartMihomo")?.addEventListener("click", async () => {
    if (!confirm("Restart mihomo?")) return;
    const d = await api("/api/services/restart", {service:"mihomo", confirm:1});
    toast(d.ok ? "mihomo restarted" : (d.error || "fail"), !!d.ok);
    loadOps();
  });
  document.getElementById("btnSavePrefs")?.addEventListener("click", async () => {
    const t = document.getElementById("autoPingToggle");
    const s = document.getElementById("autoPingSec");
    const d = await api("/api/prefs/set", {
      auto_ping: t && t.checked ? "1" : "0",
      auto_ping_sec: s ? s.value : "45",
    });
    if (!d.ok) return toast(d.error || "fail", false);
    toast("Сохранено", true);
    setupAutoPing(!!(d.prefs && d.prefs.auto_ping), d.prefs && d.prefs.auto_ping_sec);
    loadActivity({ soft: false });
  });

  // light status on load + auto check link (no toast)
  setTimeout(() => {
    refreshServices();
    loadPrefs();
    runXttpCheck({ quiet: true });
    updatePingAgeUi();
    if (!resumeUpdateOperation()) {
      const offerGeneration = updateGeneration;
      checkCoreComponent("panel", false).then(() => {
        const panel = window.__coreVerState.panel;
        if (offerGeneration === updateGeneration && panel?.update_available
            && !document.querySelector(".modal-backdrop.open, #settingsBd.open, #verBackdrop.is-open")) {
          openCoreUpdModal(panel);
        }
      });
    }
  }, 400);
  setInterval(refreshServices, 30000);
  window.addEventListener("pagehide", () => {
    updatePoll?.stop();
    updatePoll = null;
    updateGeneration++;
  });
  // pause ambient mesh when tab hidden → free GPU for 60fps elsewhere
  (function setupMotionBudget(){
    const fx = document.querySelector(".bg-fx");
    function sync(){
      if (!fx) return;
      fx.classList.toggle("is-paused", !!document.hidden);
    }
    document.addEventListener("visibilitychange", sync);
    sync();
  })();
  // tick relative ages (ping + journal label) without refetch
  setInterval(() => {
    updatePingAgeUi();
    updateJournalAgeLabel();
  }, 5000);
})();
