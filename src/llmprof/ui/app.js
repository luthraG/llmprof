const CAT = {
  "system prompt": "#7c84ff",
  "user input": "#3ddc97",
  "history (assistant)": "#4aa8ff",
  "tool schemas": "#f7a13b",
  "tool calls": "#c879ff",
  "tool results": "#2bd4c0",
};
const TOOL_SHADES = ["#f7a13b", "#f4b860", "#e8954f", "#f9c07e", "#dd8a3c", "#ffcf94"];

const $ = (s) => document.querySelector(s);
const fmt = (n) => n >= 1e6 ? (n/1e6).toFixed(2)+"M" : n >= 1e3 ? (n/1e3).toFixed(1)+"k" : String(n);
const money = (c) => c == null ? "$?" : c < 0.01 ? "$"+c.toFixed(4) : "$"+c.toFixed(2);
const ago = (ts) => {
  const s = Math.max(0, Date.now()/1000 - ts);
  if (s < 60) return Math.floor(s)+"s ago";
  if (s < 3600) return Math.floor(s/60)+"m ago";
  if (s < 86400) return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago";
};

let TRACES = [];
let selectedId = null;
let focusPath = [];   // array of nodes from root to current focus
let CURRENT_TRACE = null;
let sortBy = "recent";
let modelFilter = "";
let view = "calls";  // "calls" | "trends" | "timeline"
let selectedSession = null;

async function load() {
  const r = await fetch("/llmprof/api/traces?limit=100");
  const data = await r.json();
  TRACES = data.traces || [];
  const hosts = [...new Set(Object.values(data.upstreams || { x: data.upstream || "" })
    .map(u => (u || "").replace(/^https?:\/\//, "")).filter(Boolean))];
  $("#upstream").textContent = hosts.join(" + ");
  renderKpis();
  renderCalls();
  if (view === "trends") { renderTrends(); return; }
  if (view === "timeline") { renderTimeline(); return; }
  if (selectedId == null && TRACES.length) select(TRACES[0].id);
  else if (selectedId != null && !TRACES.find(t => t.id === selectedId) && TRACES.length) select(TRACES[0].id);
  else if (!TRACES.length) renderEmpty();
}

let _trendsSig = null;
function trendsSig() {
  // cheap fingerprint of the data: count + newest call. Changes only when a
  // new call lands, so the 4s poll does not rebuild (and flash) the panel.
  const head = TRACES[0];
  return TRACES.length + ":" + (head ? head.id + ":" + (head.ts || 0) : "0");
}

async function renderTrends(force) {
  if (!force && _trendsSig === trendsSig() && $("#main .trends-cards")) return;
  _trendsSig = trendsSig();
  const main = $("#main");
  let s;
  try { s = await (await fetch("/llmprof/api/summary")).json(); }
  catch (e) { return; }
  const days = s.days || [], models = s.models || [], routes = s.routes || [];
  const rec = s.reclaimable || {};
  if (!days.length) {
    main.innerHTML = `<div class="empty"><h2>No data yet</h2><div>Capture a few calls to see daily trends.</div></div>`;
    return;
  }
  const today = days[days.length - 1], yest = days[days.length - 2] || { cost: 0, calls: 0, tokens: 0 };
  const delta = (cur, prev) => {
    if (!prev) return cur ? `<span class="delta up">new today</span>` : `<span class="delta flat">no prior day</span>`;
    const pct = (cur - prev) / prev * 100;
    const cls = Math.abs(pct) < 1 ? "flat" : pct > 0 ? "up" : "down";
    const arrow = pct > 0 ? "&#9650;" : pct < 0 ? "&#9660;" : "&middot;";
    return `<span class="delta ${cls}">${arrow} ${Math.abs(pct).toFixed(0)}% vs yesterday</span>`;
  };
  const maxCost = Math.max(...days.map(d => d.cost), 1e-9);
  const show = days.slice(-14);
  const bars = show.map(d => {
    const h = Math.max(3, Math.round(d.cost / maxCost * 140));
    const label = d.day.slice(5);  // MM-DD
    return `<div class="col" title="${d.day}: ${money(d.cost)} &middot; ${fmt(d.tokens)} tok &middot; ${d.calls} calls">`+
           `<div class="bar" style="height:${h}px"></div><div class="day">${label}</div></div>`;
  }).join("");
  const modelRows = models.map(m =>
    `<div class="leg"><span class="sw" style="background:${CAT['tool schemas']||'#7c84ff'}"></span>`+
    `<span class="lname">${esc(m.model || 'unknown')}</span>`+
    `<span class="ltok num">${fmt(m.tokens)} tok</span>`+
    `<span class="lpct">${m.calls}&times;</span>`+
    `<span class="lcost">${money(m.cost)}</span></div>`).join("");
  const maxRoute = Math.max(...routes.map(r => r.cost), 1e-9);
  const routeRows = routes.map(r => {
    const w = Math.max(2, Math.round(r.cost / maxRoute * 100));
    return `<div class="route"><div class="rtop"><span class="rname">${esc(r.route || 'unknown')}</span>`+
      `<span class="rcost">${money(r.cost)}</span></div>`+
      `<div class="rbar"><i style="width:${w}%"></i></div>`+
      `<div class="rmeta">${r.calls}&times; &middot; ${fmt(Math.round(r.avg_tokens))} tok/call avg &middot; ${esc(r.model || '')}</div></div>`;
  }).join("");
  const routePanel = routes.length
    ? `<div class="panel"><div class="panel-title"><span>most expensive prompts</span><span class="pill">${routes.length}</span></div>`+
      `<div class="routes">${routeRows}</div></div>`
    : "";
  const flameIcon = '<svg width="34" height="34" viewBox="0 0 24 24" fill="#f7a13b"><path d="M13 2c.9 3.2-2.2 4.3-2.2 7.4a2.8 2.8 0 005.6.2c0-1-.4-1.9-1-2.8 2.2 1 4 3.2 4 6.1A7.4 7.4 0 015 13.2C5 8.6 9.4 6.6 13 2z"/></svg>';
  // the headline number alone ("X% of spend") is not actionable; pair it with
  // the ranked fixes aggregated from the per-call findings.
  const actionRows = (rec.actions || []).map(a => {
    const dollars = a.save_usd ? ` &middot; <b class="save">~${money(a.save_usd)}</b>` : "";
    const toks = a.tokens ? ` &middot; ${fmt(a.tokens)} tok` : "";
    return `<li class="rb-act"><span class="rb-act-do">${esc(a.action)}</span>`+
      `<span class="rb-act-meta">${fmt(a.calls)} calls${toks}${dollars}</span></li>`;
  }).join("");
  const actionList = actionRows
    ? `<div class="rb-actions"><div class="rb-actions-h">how to reclaim it</div>`+
      `<ul class="rb-act-list">${actionRows}</ul></div>`
    : "";
  let recBanner = "";
  if (rec.reclaimable_usd > 0 && rec.projectable) {
    recBanner = `<div class="reclaim-banner"><div class="rb-top"><span class="rb-ic">${flameIcon}</span>`+
      `<div class="rb-main"><div class="rb-label">reclaimable / mo</div>`+
      `<div class="rb-val">${money(rec.monthly_reclaimable_usd)}</div></div>`+
      `<div class="rb-meta"><span>~${rec.pct}% of spend</span>`+
      `<span>projected from ${fmt(rec.monthly_calls)} calls/mo</span>`+
      `<span>${fmt(rec.calls)} calls analyzed</span></div></div>${actionList}</div>`;
  } else if (rec.reclaimable_usd > 0) {
    // not enough data to project a month yet; show the trustworthy numbers
    recBanner = `<div class="reclaim-banner"><div class="rb-top"><span class="rb-ic">${flameIcon}</span>`+
      `<div class="rb-main"><div class="rb-label">reclaimable</div>`+
      `<div class="rb-val">~${rec.pct}% of spend</div></div>`+
      `<div class="rb-meta"><span>${money(rec.reclaimable_usd)} across ${fmt(rec.calls)} calls so far</span>`+
      `<span>capture ~a day of usage for a /mo estimate</span></div></div>${actionList}</div>`;
  }
  main.innerHTML =
    `<div class="detail-head"><div class="dh-title"><h1>Trends</h1>`+
    `<div class="meta">daily usage across all captured calls</div></div></div>`+
    recBanner+
    `<div class="trends-cards">`+
    `<div class="tcard cost"><div class="tlabel">today's cost</div><div class="tval">${money(today.cost)}</div>${delta(today.cost, yest.cost)}</div>`+
    `<div class="tcard"><div class="tlabel">today's calls</div><div class="tval">${today.calls}</div>${delta(today.calls, yest.calls)}</div>`+
    `<div class="tcard"><div class="tlabel">today's tokens</div><div class="tval">${fmt(today.tokens)}</div>${delta(today.tokens, yest.tokens)}</div>`+
    `</div>`+
    `<div class="panel"><div class="panel-title">cost per day (last ${show.length})</div><div class="barchart">${bars}</div></div>`+
    `<div class="panel"><div class="panel-title"><span>by model</span><span class="pill">${models.length}</span></div><div class="legend" style="grid-template-columns:1fr">${modelRows}</div></div>`+
    routePanel;
}

function setView(v) {
  view = v;
  document.querySelectorAll("#viewToggle button").forEach(b => b.classList.toggle("seg-on", b.dataset.view === v));
  if (v === "trends") renderTrends(true);
  else if (v === "timeline") renderTimeline(true);
  else if (selectedId != null) select(selectedId);
  else if (TRACES.length) select(TRACES[0].id);
  else renderEmpty();
}

// stack components bottom-to-top in a stable order so colors stay put per turn
const STACK_ORDER = ["system prompt", "tool schemas", "user input",
  "history (assistant)", "tool calls", "tool results"];

let _timelineSig = null;
async function renderTimeline(force) {
  const main = $("#main");
  let data;
  try { data = await (await fetch("/llmprof/api/sessions")).json(); }
  catch (e) { return; }
  const runs = data.sessions || [];
  if (!runs.length) {
    if (force || !$("#main .timeline-empty")) {
      main.innerHTML = `<div class="empty timeline-empty"><h2>No multi-turn runs yet</h2>`+
        `<div>Point a chat loop or agent at the proxy. Each follow-up call that extends `+
        `the previous one is chained into a run, and you will see its context grow here.</div></div>`;
    }
    _timelineSig = "empty";
    return;
  }
  if (!selectedSession || !runs.find(r => r.session_id === selectedSession)) {
    selectedSession = runs[0].session_id;
  }
  const run = runs.find(r => r.session_id === selectedSession) || runs[0];
  const sig = selectedSession + ":" + run.turns + ":" + run.last + ":" + runs.length;
  if (!force && _timelineSig === sig && $("#main .timeline-wrap")) return;
  _timelineSig = sig;

  let turns = [];
  try { turns = (await (await fetch("/llmprof/api/sessions/" + selectedSession)).json()).turns || []; }
  catch (e) { return; }

  const comps = turns.map(t => t.components || {});
  const totals = comps.map(c => Object.values(c).reduce((a, b) => a + b, 0));
  const maxT = Math.max(...totals, 1);
  const keys = STACK_ORDER.filter(k => comps.some(c => c[k]))
    .concat([...new Set(comps.flatMap(c => Object.keys(c)))].filter(k => !STACK_ORDER.includes(k)));

  const cols = turns.map((t, i) => {
    const segs = keys.filter(k => comps[i][k]).map(k => {
      const h = comps[i][k] / maxT * 200;
      return `<div class="seg2" style="height:${h.toFixed(1)}px;background:${CAT[k] || '#8b98a5'}" `+
             `title="turn ${t.turn} &middot; ${esc(k)}: ${fmt(comps[i][k])} tok"></div>`;
    }).join("");
    return `<div class="tcol" title="turn ${t.turn}: ${fmt(totals[i])} tok &middot; ${money(t.cost_usd)}">`+
           `<div class="tstack">${segs}</div><div class="tnum">${t.turn}</div></div>`;
  }).join("");

  const first = totals[0] || 1, last = totals[totals.length - 1] || 0;
  const growth = (last / first);
  const lastC = comps[comps.length - 1] || {};
  const histLast = (lastC["history (assistant)"] || 0) + (lastC["tool results"] || 0);
  const histPct = last ? histLast / last * 100 : 0;
  const runCost = turns.reduce((a, t) => a + (t.cost_usd || 0), 0);

  const legend = keys.map(k =>
    `<span class="tl-leg"><span class="sw" style="background:${CAT[k] || '#8b98a5'}"></span>${esc(k)}</span>`).join("");

  const picker = runs.map(r => ({
    value: r.session_id,
    label: `${r.model || 'run'} · ${r.turns} turns · ${money(r.cost)}`,
  }));

  main.innerHTML =
    `<div class="detail-head"><div class="dh-title"><h1>Context timeline</h1>`+
    `<div class="meta">how one run's context grows turn over turn</div></div>`+
    `<div class="tl-pick" id="tlPick"></div></div>`+
    `<div class="trends-cards timeline-wrap">`+
    `<div class="tcard"><div class="tlabel">turns</div><div class="tval">${turns.length}</div></div>`+
    `<div class="tcard"><div class="tlabel">context growth</div><div class="tval">${growth.toFixed(1)}&times;</div>`+
    `<span class="delta ${growth >= 1.5 ? 'up' : 'flat'}">turn 1 to ${turns.length}</span></div>`+
    `<div class="tcard"><div class="tlabel">history at last turn</div><div class="tval">${histPct.toFixed(0)}%</div>`+
    `<span class="delta ${histPct >= 40 ? 'up' : 'flat'}">${fmt(histLast)} tok</span></div>`+
    `<div class="tcard cost"><div class="tlabel">run cost</div><div class="tval">${money(runCost)}</div></div>`+
    `</div>`+
    `<div class="panel"><div class="panel-title"><span>prompt tokens per turn</span></div>`+
    `<div class="tlchart">${cols}</div><div class="tl-legend">${legend}</div></div>`;

  const host = $("#tlPick");
  if (host) dropdown(host, picker, selectedSession, (v) => { selectedSession = v; renderTimeline(true); });
}

function renderKpis() {
  const calls = TRACES.length;
  const tokens = TRACES.reduce((a,t) => a + (t.total_tokens||0), 0);
  const cost = TRACES.reduce((a,t) => a + (t.cost_usd||0), 0);
  $("#kpi-calls").textContent = fmt(calls);
  $("#kpi-tokens").textContent = fmt(tokens);
  $("#kpi-cost").textContent = money(cost);
  drawSpark();
}

function drawSpark() {
  const el = $("#spark"); if (!el) return;
  // sum cost by day, oldest -> newest, last 10 days present
  const byDay = {};
  for (const t of TRACES) {
    const d = new Date((t.ts||0) * 1000).toISOString().slice(0, 10);
    byDay[d] = (byDay[d] || 0) + (t.cost_usd || 0);
  }
  const days = Object.keys(byDay).sort().slice(-10);
  if (!days.length) { el.innerHTML = ""; return; }
  const max = Math.max(...days.map(d => byDay[d]), 1e-9);
  el.innerHTML = days.map(d => {
    const h = Math.max(2, Math.round(byDay[d] / max * 22));
    return `<i style="height:${h}px" title="${d}: ${money(byDay[d])}"></i>`;
  }).join("");
}

function hasWaste(t) {
  const c = t.components || {};
  const total = t.prompt_tokens || Object.values(c).reduce((a, b) => a + b, 0) || 1;
  const ts = c["tool schemas"] || 0;
  const hist = (c["history (assistant)"] || 0) + (c["tool results"] || 0);
  const prefix = (c["system prompt"] || 0) + ts;
  return (ts / total >= 0.35) || (hist / total >= 0.4) || (prefix >= 1024 && !t.cached_tokens);
}

function applyListControls() {
  let list = TRACES.filter(t => !modelFilter || t.model === modelFilter);
  if (sortBy === "cost") list.sort((a,b) => (b.cost_usd||0) - (a.cost_usd||0));
  else if (sortBy === "tokens") list.sort((a,b) => (b.total_tokens||0) - (a.total_tokens||0));
  else if (sortBy === "waste") list = list.slice().sort((a,b) => (hasWaste(b)-hasWaste(a)) || ((b.cost_usd||0)-(a.cost_usd||0)));
  // recent = keep API order (already newest-first)
  return list;
}

/* modern custom dropdown (replaces the dated native <select>) */
function dropdown(host, opts, current, onChange) {
  host.innerHTML = "";
  const cur = opts.find(o => o.value === current) || opts[0];
  const wrap = document.createElement("div");
  wrap.className = "dd";
  const caret = '<svg class="dd-caret" width="12" height="12" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  wrap.innerHTML =
    `<button type="button" class="dd-btn"><span>${esc(cur.label)}</span>${caret}</button>` +
    `<div class="dd-menu">` +
    opts.map(o => `<button type="button" class="dd-item${o.value === current ? " sel" : ""}" data-v="${esc(o.value)}">${esc(o.label)}</button>`).join("") +
    `</div>`;
  wrap.querySelector(".dd-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    document.querySelectorAll(".dd.open").forEach(d => { if (d !== wrap) d.classList.remove("open"); });
    wrap.classList.toggle("open");
  });
  wrap.querySelectorAll(".dd-item").forEach(item => {
    item.addEventListener("click", () => { wrap.classList.remove("open"); onChange(item.dataset.v); });
  });
  host.appendChild(wrap);
}

let _modelsKey = null;
function buildControls(force) {
  const host = $("#sideTools"); if (!host) return;
  const models = [...new Set(TRACES.map(t => t.model).filter(Boolean))].sort();
  const key = sortBy + "|" + modelFilter + "|" + models.join(",");
  if (!force && key === _modelsKey) return;  // avoid rebuilding (and closing an open menu) on every poll
  _modelsKey = key;
  host.innerHTML = "";
  const a = document.createElement("div"), b = document.createElement("div");
  a.className = b.className = "dd-slot";
  host.append(a, b);
  dropdown(a, [
    { value: "recent", label: "recent" },
    { value: "cost", label: "most $" },
    { value: "tokens", label: "most tokens" },
    { value: "waste", label: "needs attention" },
  ], sortBy, (v) => { sortBy = v; buildControls(true); renderCalls(); });
  dropdown(b, [{ value: "", label: "all models" }, ...models.map(m => ({ value: m, label: m }))],
    modelFilter, (v) => { modelFilter = v; buildControls(true); renderCalls(); });
}
document.addEventListener("click", () => {
  document.querySelectorAll(".dd.open").forEach(d => d.classList.remove("open"));
});

function topComponent(comps) {
  let best = null;
  for (const [k,v] of Object.entries(comps||{})) if (!best || v > best[1]) best = [k,v];
  return best;
}

function renderCalls() {
  buildControls(false);
  const el = $("#calls");
  el.innerHTML = "";
  for (const t of applyListControls()) {
    const b = document.createElement("button");
    b.className = "call" + (t.id === selectedId ? " active" : "");
    b.onclick = () => select(t.id);
    const top = topComponent(t.components);
    const wdot = hasWaste(t) ? '<span class="wdot" title="optimization findings"></span>' : '';
    b.innerHTML =
      `<div class="call-top"><span class="call-model">${wdot}${esc(t.model || "unknown")}</span>`+
      `<span class="call-cost">${money(t.cost_usd)}</span></div>`+
      `<div class="call-sub"><span><span class="num">${fmt(t.total_tokens||0)}</span> tok`+
      `${t.streamed ? ' &middot; <span class="badge">stream</span>' : ''}</span>`+
      `<span>${ago(t.ts)}</span></div>`;
    el.appendChild(b);
  }
}

async function select(id) {
  selectedId = id;
  if (view !== "calls") {
    view = "calls";
    document.querySelectorAll("#viewToggle button").forEach(b => b.classList.toggle("seg-on", b.dataset.view === "calls"));
  }
  focusPath = [];
  renderCalls();
  document.body.classList.remove("nav-open");  // close the mobile drawer
  const r = await fetch("/llmprof/api/traces/" + id);
  if (!r.ok) return;
  const t = await r.json();
  renderDetail(t);
}

function renderDetail(t) {
  CURRENT_TRACE = t;
  const tree = t.detail || {name:"context", tokens:t.prompt_tokens||0, children:[]};
  focusPath = focusPath.length ? focusPath : [tree];
  const main = $("#main");
  const projection = t.cost_usd != null ? "&asymp; " + money(t.cost_usd * 1000) + " / 1k calls" : "";
  const ra = t.analysis;
  const flameIc = '<svg width="22" height="22" viewBox="0 0 24 24" fill="#f7a13b"><path d="M13 2c.9 3.2-2.2 4.3-2.2 7.4a2.8 2.8 0 005.6.2c0-1-.4-1.9-1-2.8 2.2 1 4 3.2 4 6.1A7.4 7.4 0 015 13.2C5 8.6 9.4 6.6 13 2z"/></svg>';
  const reclaimBanner = (ra && (ra.reclaimable_tokens || ra.reclaimable_usd))
    ? `<div class="reclaim-call"><span class="rc-ic">${flameIc}</span>`+
      `<span class="rc-text">Reclaimable on this call: <b>${fmt(ra.reclaimable_tokens)} tokens</b>`+
      `${ra.reclaimable_usd ? ` &middot; <b class="save">~${money(ra.reclaimable_usd)}</b>` : ""}</span>`+
      `${ra.reclaimable_usd && t.cost_usd ? `<span class="rc-pct">${(ra.reclaimable_usd / t.cost_usd * 100).toFixed(0)}% of this call</span>` : ""}`+
      `</div>`
    : "";
  main.innerHTML =
    `<div class="detail-head">
       <div class="dh-title">
         <h1>${esc(t.model || "unknown")}</h1>
         <div class="meta">${esc(t.provider||"")} &middot; ${ago(t.ts)} ${t.streamed?'&middot; streamed':''}</div>
       </div>
       <div class="dh-stats">
         <div class="stat"><b class="num">${fmt(t.prompt_tokens||0)}</b><span>prompt</span></div>
         <div class="stat"><b class="num">${fmt(t.completion_tokens||0)}</b><span>completion</span></div>
         <div class="stat"><b class="num">${fmt(t.total_tokens||0)}</b><span>total</span></div>
         ${t.cached_tokens ? `<div class="stat"><b class="num" style="color:var(--accent-2)">${fmt(t.cached_tokens)}</b><span>cached</span></div>` : ''}
         <div class="stat cost"><b class="num">${money(t.cost_usd)}</b><span>${projection || 'est. cost'}</span></div>
       </div>
     </div>
     ${reclaimBanner}
     <div id="insight"></div>
     <div id="wgauge"></div>
     <div class="panel">
       <div class="panel-title"><span>context flame graph</span><span class="crumbs" id="crumbs"></span></div>
       <div id="flame"></div>
     </div>
     <div class="panel">
       <div class="panel-title"><span>optimization</span><span class="pill" id="opt-count"></span></div>
       <div id="suggestions"></div>
     </div>
     <div class="panel">
       <div class="panel-title">breakdown</div>
       <div class="legend" id="legend"></div>
     </div>
     <div class="panel" id="tools-panel" style="display:none">
       <div class="panel-title"><span>tools</span><span class="pill" id="tools-count"></span></div>
       <div class="legend" id="tools-list"></div>
     </div>`;
  renderInsight(tree);
  renderWindow(t);
  drawFlame(tree);
  drawLegend(tree);
  renderSuggestions(tree, t);
  renderTools(tree, t);
}

function renderWindow(t) {
  const el = $("#wgauge"); if (!el) return;
  const w = t.context_window;
  if (!w) { el.innerHTML = ""; return; }
  const used = t.total_tokens || 0;
  const pct = Math.min(100, used / w * 100);
  const color = pct >= 90 ? "#e5534b" : pct >= 75 ? "var(--amber)" : "var(--accent)";
  const pctTxt = pct < 0.1 ? "<0.1" : pct.toFixed(1);
  el.innerHTML =
    `<div class="wgauge"><span class="glabel">context window</span>`+
    `<div class="bar"><i style="width:${Math.max(pct,0.6).toFixed(2)}%;background:${color}"></i></div>`+
    `<span>${used.toLocaleString()} / ${fmt(w)} tokens (${pctTxt}%)</span></div>`;
}

function renderTools(tree, t) {
  const panel = $("#tools-panel"), el = $("#tools-list");
  if (!panel || !el) return;
  const tsNode = (tree.children || []).find(c => c.name === "tool schemas");
  const tools = tsNode && tsNode.children ? [...tsNode.children].sort((a,b)=>b.tokens-a.tokens) : [];
  if (!tools.length) { panel.style.display = "none"; return; }
  panel.style.display = "";
  const inP = t.input_per_1k;
  const total = tree.tokens || 1;
  $("#tools-count").textContent =
    `${tools.length} tools, ${tsNode.tokens.toLocaleString()} tok` +
    (inP != null ? ` (${money(tsNode.tokens/1000*inP)}/call)` : "");
  const called = new Set(t.called_tools || []);
  const knowCalls = called.size > 0;
  el.innerHTML = tools.map(n => {
    const cost = inP != null ? money(n.tokens/1000*inP) : "";
    const unused = knowCalls && !called.has(n.name);
    const tag = unused ? ' <span class="pill">unused</span>' : "";
    return `<div class="leg${unused ? ' dim' : ''}"><span class="sw" style="background:${nodeColor(n, tsNode)}"></span>`+
      `<span class="lname">${esc(n.name)}${tag}</span>`+
      `<span class="ltok num">${n.tokens.toLocaleString()}</span>`+
      `<span class="lpct">${(n.tokens/total*100).toFixed(1)}%</span>`+
      `<span class="lcost">${cost}</span></div>`;
  }).join("");
}

const OPT_ICON = {
  warn: '<svg width="16" height="16" viewBox="0 0 24 24" fill="#f7a13b"><path d="M12 4l9 16H3z"/></svg>',
  tip: '<svg width="16" height="16" viewBox="0 0 24 24" fill="#7c84ff"><path d="M9 21h6v-1H9zM12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/></svg>',
  ok: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3ddc97" stroke-width="2.4"><path d="M20 6L9 17l-5-5"/></svg>',
};

// the waste detector runs server-side (analyze.py); here we just render it
function renderSuggestions(tree, t) {
  const el = $("#suggestions"); if (!el) return;
  const a = t.analysis;
  const findings = (a && a.findings) || [{ severity: "ok", title: "No analysis available",
    body: "This call was recorded before the waste detector shipped." }];
  const count = findings.filter(f => f.severity !== "ok").length;
  $("#opt-count").textContent = count ? `${count} found` : "clean";
  el.innerHTML = findings.map(f => {
    const chip = f.save_usd ? ` <span class="save">~${money(f.save_usd)}/call</span>` : "";
    return `<div class="sugg ${f.severity}"><span class="si">${OPT_ICON[f.severity] || OPT_ICON.tip}</span>`+
      `<span class="stext"><b>${esc(f.title)}</b> ${esc(f.body)}${chip}</span></div>`;
  }).join("");
}

function renderInsight(tree) {
  const el = $("#insight"); if (!el) return;
  const kids = tree.children || [];
  if (!kids.length || !tree.tokens) { el.innerHTML = ""; return; }
  const top = kids.reduce((a, b) => b.tokens > a.tokens ? b : a, kids[0]);
  const pct = top.tokens / tree.tokens * 100;
  const heavy = pct >= 45;
  const verb = heavy ? "dominates this context" : "is the largest piece of this context";
  const flameIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="#f7a13b"><path d="M13 2c.9 3.2-2.2 4.3-2.2 7.4a2.8 2.8 0 005.6.2c0-1-.4-1.9-1-2.8 2.2 1 4 3.2 4 6.1A7.4 7.4 0 015 13.2C5 8.6 9.4 6.6 13 2z"/></svg>';
  const barsIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="#7c84ff"><rect x="3" y="11" width="4" height="10" rx="1"/><rect x="10" y="5" width="4" height="16" rx="1"/><rect x="17" y="14" width="4" height="7" rx="1"/></svg>';
  el.innerHTML =
    `<div class="insight">
       <span class="ic">${heavy ? flameIcon : barsIcon}</span>
       <span class="txt"><b>${esc(top.name)}</b> ${verb}: `+
       `<span class="num">${top.tokens.toLocaleString()} tokens (${pct.toFixed(0)}%)</span>`+
       `${top.children&&top.children.length?` across ${top.children.length} items, click it in the graph to drill in`:''}.</span>
     </div>`;
}

function nodeColor(node, parent) {
  if (CAT[node.name]) return CAT[node.name];
  if (parent && parent.name === "tool schemas") {
    const i = parent.children.indexOf(node);
    return TOOL_SHADES[i % TOOL_SHADES.length];
  }
  if (node.name === "context") return "#39414f";
  return "#8b98a5";
}

function drawCrumbs() {
  const el = $("#crumbs");
  if (!el) return;
  el.innerHTML = "";
  focusPath.forEach((n, i) => {
    if (i) { const s = document.createElement("span"); s.className="crumb-sep"; s.textContent="/"; el.appendChild(s); }
    const c = document.createElement("span");
    c.className = "crumb" + (i === focusPath.length-1 ? " cur" : "");
    c.textContent = n.name;
    c.onclick = () => { focusPath = focusPath.slice(0, i+1); redraw(); };
    el.appendChild(c);
  });
}

let CURRENT_TREE = null;
function drawFlame(tree) { CURRENT_TREE = tree; redraw(); }
function redraw() {
  const focus = focusPath[focusPath.length-1] || CURRENT_TREE;
  drawCrumbs();
  const host = $("#flame");
  if (!focus || !(focus.tokens > 0) || !(focus.children && focus.children.length)) {
    host.innerHTML = `<div class="empty-note">No token breakdown was captured for this call.</div>`;
    return;
  }
  const W = host.clientWidth || 900;
  const ROW = 30, GAP = 2, PAD = 0;
  const NS = "http://www.w3.org/2000/svg";
  const depth = maxDepth(focus);
  const H = (depth+1) * (ROW+GAP);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const scale = focus.tokens > 0 ? W / focus.tokens : 0;
  const totalCtx = (CURRENT_TREE.tokens || focus.tokens) || 1;

  function draw(node, parent, dx, depthIdx) {
    const w = Math.max((node.tokens||0) * scale, 0);
    const x = dx, y = depthIdx * (ROW+GAP);
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "frame");
    g.style.cursor = (node.children && node.children.length) ? "pointer" : "default";
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", x.toFixed(2)); rect.setAttribute("y", y);
    rect.setAttribute("width", Math.max(w-1,0.5).toFixed(2)); rect.setAttribute("height", ROW);
    rect.setAttribute("rx", 3);
    rect.setAttribute("fill", nodeColor(node, parent));
    g.appendChild(rect);
    if (w > 46) {
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", x+7); t.setAttribute("y", y + ROW/2 + 4);
      const chars = Math.floor((w-12)/6.4);
      const label = node.name.length > chars ? node.name.slice(0, Math.max(chars-1,1))+"…" : node.name;
      t.textContent = label;
      g.appendChild(t);
    }
    const pct = (node.tokens/totalCtx*100);
    g.addEventListener("mousemove", (e) => showTip(e, node, pct));
    g.addEventListener("mouseleave", hideTip);
    if (node.children && node.children.length) {
      g.addEventListener("click", () => {
        // build path from root to this node
        const p = pathTo(CURRENT_TREE, node);
        if (p) { focusPath = p; redraw(); }
      });
    }
    svg.appendChild(g);
    let cx = x;
    // heaviest-first so the big picture reads left-to-right (Gregg)
    const kids = (node.children||[]).slice().sort((a,b)=>(b.tokens||0)-(a.tokens||0));
    for (const c of kids) { draw(c, node, cx, depthIdx+1); cx += (c.tokens||0)*scale; }
  }
  draw(focus, focusParent(focus), 0, 0);
  host.innerHTML = ""; host.appendChild(svg);
}

function focusParent(focus) {
  const idx = focusPath.indexOf(focus);
  return idx > 0 ? focusPath[idx-1] : null;
}
function maxDepth(node) {
  if (!node.children || !node.children.length) return 0;
  return 1 + Math.max(...node.children.map(maxDepth));
}
function pathTo(root, target, acc=[]) {
  const p = [...acc, root];
  if (root === target) return p;
  for (const c of (root.children||[])) { const r = pathTo(c, target, p); if (r) return r; }
  return null;
}

function showTip(e, node, pct) {
  const tip = $("#tip");
  const inP = CURRENT_TRACE && CURRENT_TRACE.input_per_1k;
  const costRow = inP ? `<div class="t-row"><b>${money(node.tokens/1000*inP)}</b> input cost</div>` : "";
  tip.innerHTML = `<div class="t-name">${esc(node.name)}</div>`+
    `<div class="t-row"><b>${node.tokens.toLocaleString()}</b> tokens &middot; <b>${pct.toFixed(1)}%</b> of context</div>`+
    costRow+
    `${node.children&&node.children.length?`<div class="t-row">${node.children.length} items &middot; click to zoom</div>`:''}`;
  tip.classList.add("on");
  const w = tip.offsetWidth;
  tip.style.left = Math.min(e.clientX+14, innerWidth-w-12) + "px";
  tip.style.top = (e.clientY+16) + "px";
}
function hideTip() { $("#tip").classList.remove("on"); }

function drawLegend(tree) {
  const el = $("#legend"); if (!el) return;
  el.innerHTML = "";
  const total = tree.tokens || 1;
  const inP = CURRENT_TRACE && CURRENT_TRACE.input_per_1k;
  const items = [...(tree.children||[])].sort((a,b)=>b.tokens-a.tokens);
  if (!items.length) {
    el.innerHTML = `<div class="empty-note">No breakdown available for this call.</div>`;
    return;
  }
  for (const n of items) {
    const row = document.createElement("div");
    row.className = "leg";
    const costCell = inP ? `<span class="lcost">${money(n.tokens/1000*inP)}</span>` : `<span class="lcost"></span>`;
    row.innerHTML =
      `<span class="sw" style="background:${nodeColor(n, tree)}"></span>`+
      `<span class="lname">${esc(n.name)}</span>`+
      `<span class="ltok num">${n.tokens.toLocaleString()}</span>`+
      `<span class="lpct">${(n.tokens/total*100).toFixed(1)}%</span>`+
      costCell;
    el.appendChild(row);
  }
}

function renderEmpty() {
  $("#main").innerHTML =
    `<div class="empty">
       <div class="glyph">&#128293;</div>
       <h2>No calls captured yet</h2>
       <div>Point your LLM client's base URL at this proxy:</div>
       <div class="hint"><code>base_url = http://localhost:4000/v1</code></div>
       <div class="hint">Send a request and it will appear here, broken down token by token.</div>
     </div>`;
}

function esc(s) { return String(s).replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

addEventListener("resize", () => { if (CURRENT_TREE) redraw(); });
addEventListener("keydown", (e) => {
  if ((e.key === "Escape" || e.key === "0") && CURRENT_TREE) { focusPath = [CURRENT_TREE]; redraw(); }
});
const menuBtn = $("#menuBtn");
if (menuBtn) menuBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  document.body.classList.toggle("nav-open");
});
const scrim = $("#scrim");
if (scrim) scrim.addEventListener("click", () => document.body.classList.remove("nav-open"));

document.querySelectorAll("#viewToggle button").forEach(b =>
  b.addEventListener("click", () => setView(b.dataset.view)));

load();
setInterval(load, 4000);
