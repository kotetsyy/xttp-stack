import {panelFetch} from "./system.js";

// One request at a time, including across a stop/start while an abort settles.
export function createPoller(refresh, interval){
  let active = false, timer = null, pending = null, generation = 0;
  function schedule(delay){
    if (!active || timer !== null || pending) return;
    timer = setTimeout(() => { timer = null; run(); }, delay);
  }
  async function run(){
    if (!active || pending) return;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    const controller = new AbortController(), started = generation;
    pending = controller;
    try { await refresh(controller.signal); }
    finally {
      pending = null;
      schedule(started === generation ? interval : 0);
    }
  }
  return {
    start(){
      if (active) return;
      active = true;
      generation++;
      run();
    },
    stop(){
      if (!active) return;
      active = false;
      generation++;
      if (timer !== null) { clearTimeout(timer); timer = null; }
      pending?.abort();
    },
    refresh(){ return run(); },
  };
}

export async function readSnapshot(path, signal){
  const response = await panelFetch(path, {credentials: "same-origin", signal});
  const data = await response.json();
  if (!response.ok || data.ok !== true) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

export function createStatsCursor(){
  let epoch = "", cursor = 0;
  const history = [];
  return {
    query(){ return "/api/stats/snapshot?" + new URLSearchParams({since: String(cursor), epoch}); },
    consume(data){
      const changed = epoch !== data.epoch;
      if (changed) { history.length = 0; cursor = 0; }
      epoch = data.epoch;
      for (const point of data.history || []) {
        const t = Number(point.t);
        if (!Number.isFinite(t) || t <= cursor) continue;
        history.push({t, upRate: Math.max(0, Number(point.upRate) || 0), downRate: Math.max(0, Number(point.downRate) || 0)});
        cursor = t;
      }
      const max = Number(data.historyMax) || 3700;
      if (history.length > max) history.splice(0, history.length - max);
      return {history, changed};
    },
  };
}

export function createLogCursor(){
  let epoch = "", seq = 0, clearedAt = 0, mode = "tail", initialized = false, baseline = false;
  const items = [];
  return {
    query(){
      // Both UI modes consume deltas. Tail is the local rolling 200-line window.
      return "/api/logs?" + new URLSearchParams({mode: "live", level: "all", limit: "200", since: String(seq), epoch});
    },
    setMode(value){
      mode = value === "live" ? "live" : "tail";
      epoch = ""; seq = 0; clearedAt = 0; initialized = false;
      baseline = mode === "live";
      items.length = 0;
    },
    clear(){
      items.length = 0; clearedAt = seq;
      if (!initialized) baseline = true;
      return items;
    },
    consume(data){
      const nextSeq = Number(data.seq) || 0;
      const changed = initialized && (epoch !== data.epoch || nextSeq < seq);
      if (changed) { items.length = 0; seq = 0; clearedAt = 0; }
      epoch = data.epoch;
      if (baseline && !initialized) seq = nextSeq;
      for (const item of data.items || []) {
        const itemSeq = Number(item.seq) || 0;
        if (itemSeq <= seq || itemSeq <= clearedAt) continue;
        items.push(item);
        seq = itemSeq;
      }
      seq = Math.max(seq, nextSeq);
      initialized = true;
      const max = mode === "live" ? 500 : 200;
      if (items.length > max) items.splice(0, items.length - max);
      return {items, seq, changed};
    },
  };
}
