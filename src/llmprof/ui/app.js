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

async function load() {
  const r = await fetch("/llmprof/api/traces?limit=100");
  const data = await r.json();
  TRACES = data.traces || [];
  $("#upstream").textContent = (data.upstream || "").replace(/^https?:\/\//, "");
  renderKpis();
  renderCalls();
  if (selectedId == null && TRACES.length) select(TRACES[0].id);
  else if (selectedId != null && !TRACES.find(t => t.id === selectedId) && TRACES.length) select(TRACES[0].id);
  else if (!TRACES.length) renderEmpty();
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

function renderSuggestions(tree, t) {
  const el = $("#suggestions"); if (!el) return;
  const inP = t.input_per_1k;
  const total = tree.tokens || 1;
  const comp = {};
  for (const c of (tree.children || [])) comp[c.name] = c.tokens;
  const save = (tok) => inP != null ? ` <span class="save">~${money(tok/1000*inP)}/call</span>` : "";
  const out = [];
  const ts = comp["tool schemas"] || 0;
  if (ts / total >= 0.35)
    out.push(["warn", `<b>Tool schemas are ${(ts/total*100).toFixed(0)}% of the context</b> (${ts.toLocaleString()} tok).${save(ts)} Trim descriptions, drop unused tools, or load schemas lazily.`]);
  if (t.cached_tokens) {
    const pctCached = (t.cached_tokens / (t.prompt_tokens || 1) * 100).toFixed(0);
    out.push(["ok", `Prompt caching is active: <b>${t.cached_tokens.toLocaleString()} tokens</b> (${pctCached}% of the prompt) were served from cache on this call.`]);
  }
  const prefix = (comp["system prompt"] || 0) + ts;
  if (prefix >= 1024 && !t.cached_tokens)  // suggest only if not already caching (min ~1k tokens)
    out.push(["tip", `Your stable prefix (system prompt + tool schemas) is <b>${prefix.toLocaleString()} tokens</b> and repeats on every call. <b>Prompt caching</b> can cut ~90% off it on cache hits${inP != null ? `, saving <span class="save">~${money(prefix*0.9/1000*inP)}/call</span> after the first` : ""}.`]);
  const hist = (comp["history (assistant)"] || 0) + (comp["tool results"] || 0);
  if (hist / total >= 0.4)
    out.push(["warn", `<b>History and tool results are ${(hist/total*100).toFixed(0)}% of the context</b> (${hist.toLocaleString()} tok).${save(hist)} Summarize or truncate older turns.`]);
  if ((comp["system prompt"] || 0) >= 1500)
    out.push(["tip", `System prompt is <b>${comp["system prompt"].toLocaleString()} tokens</b> of fixed overhead on every call.`]);
  // tools defined but not called on this request
  const tsNode = (tree.children || []).find(c => c.name === "tool schemas");
  const called = new Set(t.called_tools || []);
  if (tsNode && tsNode.children && called.size > 0) {
    const unused = tsNode.children.filter(c => !called.has(c.name));
    if (unused.length) {
      const wasted = unused.reduce((a, c) => a + c.tokens, 0);
      const names = unused.map(c => esc(c.name)).slice(0, 6).join(", ") + (unused.length > 6 ? ", ..." : "");
      out.push(["warn", `<b>${unused.length} of ${tsNode.children.length} tools were not called</b> on this request (${names}): ${wasted.toLocaleString()} tok.${save(wasted)} If they are not needed here, drop them from the call.`]);
    }
  }
  if (!out.length) out.push(["ok", "No obvious waste detected. This context looks lean."]);

  const ICON = {
    warn: '<svg width="16" height="16" viewBox="0 0 24 24" fill="#f7a13b"><path d="M12 4l9 16H3z"/></svg>',
    tip: '<svg width="16" height="16" viewBox="0 0 24 24" fill="#7c84ff"><path d="M9 21h6v-1H9zM12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"/></svg>',
    ok: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3ddc97" stroke-width="2.4"><path d="M20 6L9 17l-5-5"/></svg>',
  };
  const count = out.filter(o => o[0] !== "ok").length;
  $("#opt-count").textContent = count ? `${count} found` : "clean";
  el.innerHTML = out.map(([sev, text]) =>
    `<div class="sugg ${sev}"><span class="si">${ICON[sev]}</span><span class="stext">${text}</span></div>`).join("");
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

load();
setInterval(load, 4000);
