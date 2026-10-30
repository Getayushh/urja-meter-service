// The dashboard talks ONLY to our FastAPI service, never to the original portal.
// Same origin: the API serves this page at "/". For a separate dev server, set API to
// "http://127.0.0.1:8000" and allow that origin in CORS (app/main.py).
const API = "";
const $ = (id) => document.getElementById(id);
const S = { page: 1, q: "", results: [], active: -1, selected: null, readings: [], rangeH: 24, metric: "kwh",
            busy: false, token: 0, dtPage: 1, dtMap: null, debounce: null };

// ---------- small helpers (API data is only ever written with textContent) ----------
function h(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = text; return n; }
const fmt = (n, d = 2) => (n === null || n === undefined ? "—" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const when = (iso) => iso.replace("T", " ").slice(0, 16);
function stamp() { $("lastLoaded").textContent = "Last load " + new Date().toLocaleTimeString(); }
function setState(id, msg, err = false) { const el = $(id); el.textContent = msg; el.className = "state" + (err ? " error" : "") + (id === "energyState" ? " inline" : ""); }

async function api(path) {
  let res;
  try { res = await fetch(API + path); }
  catch { throw new Error("Cannot reach the API. Is uvicorn running on port 8000?"); }
  let body = null;
  try { body = await res.json(); } catch { /* not JSON */ }
  if (res.status === 429) {
    const wait = res.headers.get("Retry-After");
    $("banner").hidden = false;
    $("banner").textContent = "The upstream portal is rate-limiting requests. Please wait" + (wait ? ` about ${wait}s` : "") + " and try again.";
    throw new Error("Rate-limited by the upstream portal.");
  }
  if (!res.ok) throw new Error(body?.message || `Request failed (HTTP ${res.status})`);
  $("banner").hidden = true;
  return body;
}

async function checkHealth() {
  const el = $("apiStatus");
  try { await api("/health"); el.textContent = "API online (portal not checked)"; el.className = "pill ok"; }
  catch { el.textContent = "API offline"; el.className = "pill bad"; }
}

// ---------- navigation ----------
document.querySelectorAll(".nav").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".nav").forEach((x) => x.classList.toggle("active", x === b));
  for (const v of ["overview", "transformers"]) $("view-" + v).hidden = v !== b.dataset.view;
  $("crumb").textContent = "Workspace / " + b.textContent;
  if (b.dataset.view === "transformers" && !$("dtTable").querySelector("tbody").children.length) loadTransformers();
});

// ======================= SEARCH + RESULTS =======================
async function loadMeters() {
  const token = ++S.token;               // ignore answers that arrive after a newer search
  setState("meterState", "Loading…");
  try {
    const r = await api(`/api/v1/meters?q=${encodeURIComponent(S.q)}&page=${S.page}`);
    if (token !== S.token) return;
    S.results = r.data; S.active = -1;
    renderResults();
    const pages = r.total && r.pageSize ? Math.ceil(r.total / r.pageSize) : null;
    $("resultsMeta").textContent = r.data.length
      ? (r.total != null ? `${fmt(r.total, 0)} meter${r.total === 1 ? "" : "s"}` : `${r.data.length} meters`) + (S.q ? ` matching “${S.q}”` : "")
      : "No meters found" + (S.q ? ` for “${S.q}”` : "");
    $("pageInfo").textContent = r.data.length ? `Page ${r.page}` + (pages ? ` of ${pages}` : "") : "";
    $("prevBtn").disabled = S.page <= 1;
    $("nextBtn").disabled = pages ? S.page >= pages : r.data.length === 0;
    setState("meterState", "");
    stamp();
  } catch (e) {
    if (token !== S.token) return;
    S.results = []; renderResults(); $("resultsMeta").textContent = "Search failed"; setState("meterState", e.message, true);
  }
}

function renderResults() {
  const ul = $("results"); ul.textContent = "";
  S.results.forEach((m, i) => {
    const li = h("li", "opt"); li.setAttribute("role", "option"); li.dataset.i = i;
    if (m.meterId === S.selected) li.classList.add("selected");
    li.append(h("b", "", m.meterId ?? "—"),
              h("span", "meta", [m.serialNo, m.make, m.phaseType && m.phaseType + " phase"].filter(Boolean).join(" · ") || "—"),
              h("span", "dt", m.dtCode ?? ""),
              h("span", "chip " + (m.installationStatus ?? ""), m.installationStatus ?? "—"));
    li.onclick = () => selectMeter(m);
    ul.appendChild(li);
  });
}

function setActive(i) {
  const items = $("results").children;
  if (!items.length) return;
  S.active = (i + items.length) % items.length;
  [...items].forEach((li, k) => li.classList.toggle("active", k === S.active));
  items[S.active].scrollIntoView({ block: "nearest" });
}

function runSearch() { clearTimeout(S.debounce); S.q = $("searchInput").value.trim(); S.page = 1; loadMeters(); }

$("searchBtn").onclick = runSearch;
$("searchInput").oninput = () => {      // gentle search-as-you-type: waits for a pause, only if the text changed
  clearTimeout(S.debounce);
  S.debounce = setTimeout(() => { if ($("searchInput").value.trim() !== S.q) runSearch(); }, 700);
};
$("searchInput").onkeydown = (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); setActive(S.active + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); setActive(S.active - 1); }
  else if (e.key === "Escape") { S.active = -1; [...$("results").children].forEach((li) => li.classList.remove("active")); }
  else if (e.key === "Enter") {
    if (S.active >= 0 && S.results[S.active]) selectMeter(S.results[S.active]);
    else runSearch();
  }
};
$("prevBtn").onclick = () => { S.page--; loadMeters(); };
$("nextBtn").onclick = () => { S.page++; loadMeters(); };
$("backBtn").onclick = () => { window.scrollTo({ top: 0, behavior: "smooth" }); $("searchInput").focus(); };

// ======================= SELECTED METER =======================
function selectMeter(m) {
  S.selected = m.meterId; S.readings = [];
  document.querySelectorAll("#results .opt").forEach((li, i) => li.classList.toggle("selected", S.results[i]?.meterId === m.meterId));
  $("empty").hidden = true; $("workspace").hidden = false;
  $("aTitle").textContent = m.meterId;
  $("aSub").textContent = `${m.make ?? "Unknown make"} · serial ${m.serialNo ?? "—"}`;
  const chip = $("aStatus"); chip.hidden = !m.installationStatus; chip.textContent = m.installationStatus ?? ""; chip.className = "chip " + (m.installationStatus ?? "");

  const dl = $("aDetail"); dl.textContent = "";
  const typeDd = h("dd", "", "…");
  const rows = [["Meter ID", h("dd", "", m.meterId)], ["Serial", h("dd", "", m.serialNo ?? "—")], ["Make", h("dd", "", m.make ?? "—")],
                ["Phase", h("dd", "", m.phaseType ?? "—")], ["Transformer code", h("dd", "", m.dtCode ?? "—")], ["Installation type", typeDd]];
  for (const [k, dd] of rows) dl.append(h("dt", "", k), dd);

  // energy is NEVER fetched automatically; only the button does that
  $("energyBtn").disabled = false; setState("energyState", ""); $("readout").textContent = "Hover the chart for a reading.";
  drawChart();
  loadProfile(m.meterId, typeDd);
  loadGeo(m.meterId);
  if (window.innerWidth < 1000) $("workspace").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadGeo(id) {
  setState("geo", "Loading…");
  try {
    const g = (await api(`/api/v1/meters/${encodeURIComponent(id)}/geo`)).data;
    if (S.selected !== id) return;      // user already picked another meter
    const el = $("geo"); el.className = "state"; el.textContent = `${g.latitude.toFixed(5)}, ${g.longitude.toFixed(5)} · `;
    const a = h("a", "", "Open in OpenStreetMap");
    a.href = `https://www.openstreetmap.org/?mlat=${g.latitude}&mlon=${g.longitude}#map=17/${g.latitude}/${g.longitude}`;
    a.target = "_blank"; a.rel = "noopener"; el.appendChild(a);
  } catch (e) { if (S.selected === id) setState("geo", e.message); }
}

// Installation type + network hierarchy come from a separate endpoint (search results lack them).
async function loadProfile(id, typeDd) {
  setState("hierarchy", "Loading…");
  try {
    const p = await api(`/api/v1/meters/${encodeURIComponent(id)}/hierarchy`);
    if (S.selected !== id) return;
    typeDd.textContent = p.installationType ?? "—";
    renderHierarchy(p);
    stamp();
  } catch (e) {
    if (S.selected !== id) return;
    typeDd.textContent = "—"; setState("hierarchy", e.message, true);
  }
}

function renderHierarchy(p) {
  const box = $("hierarchy"); box.className = "state"; box.textContent = "";
  if (!p.hierarchy.length) { box.textContent = "The portal returned no hierarchy for this meter."; return; }
  const ol = h("ol", "tree");
  p.hierarchy.forEach((l, i) => {
    const li = h("li", "step" + (i === p.hierarchy.length - 1 && l.level === "transformer" ? " leaf" : ""));
    const nm = h("span", "nm"); nm.append(h("span", "", l.name ?? "—"));
    if (l.code) nm.append(h("span", "code", l.code));
    li.append(h("span", "lv", l.level), nm);
    if (l.level === "transformer" && l.code) addCapacity(li, l.code);
    ol.appendChild(li);
  });
  box.appendChild(ol);
  if (!p.hierarchyComplete) box.appendChild(h("p", "note", "Some levels were missing in the portal data."));
}

// Rated capacity of the transformer: the 2 transformer pages are fetched once, then kept in memory.
function getDtMap() {
  if (!S.dtMap) {
    S.dtMap = (async () => {
      const map = new Map(); let page = 1, pages = 1;
      do {
        const r = await api(`/api/v1/distribution-transformers?page=${page}`);
        r.data.forEach((t) => t.code && map.set(t.code, t));
        pages = r.total && r.pageSize ? Math.ceil(r.total / r.pageSize) : 1; page++;
      } while (page <= pages && page <= 5);
      return map;
    })();
    S.dtMap.catch(() => { S.dtMap = null; });   // allow a retry next time
  }
  return S.dtMap;
}
async function addCapacity(li, code) {
  try {
    const t = (await getDtMap()).get(code);
    if (t && t.capacityKva != null) li.appendChild(h("span", "cap", `Rated capacity ${fmt(t.capacityKva, 0)} kVA (a rating, not the current load)`));
  } catch { /* capacity is a bonus; ignore failures */ }
}

// ======================= ENERGY =======================
async function loadEnergy() {
  if (!S.selected || S.busy) return;     // one request at a time, no double clicks
  const id = S.selected;
  S.busy = true; $("energyBtn").disabled = true; setState("energyState", "Loading readings…");
  try {
    const r = await api(`/api/v1/meters/${encodeURIComponent(id)}/energy`);
    if (S.selected !== id) return;
    S.readings = r.data.map((x) => ({ ...x, t: new Date(x.timestamp).getTime() }));  // ISO string from our API
    const w = r.warnings || {}, notes = [];
    if (w.skippedInvalidTimestamp) notes.push(`${w.skippedInvalidTimestamp} rows skipped (bad timestamp)`);
    if (w.duplicateTimestamps) notes.push(`${w.duplicateTimestamps} duplicate timestamps`);
    if (w.registerDecreases) notes.push(`${w.registerDecreases} register decreases`);
    setState("energyState", S.readings.length ? `${r.count} readings` + (notes.length ? " · " + notes.join("; ") : "") : "No readings returned for this meter.");
    drawChart(); stamp();
  } catch (e) { setState("energyState", e.message, true); }
  finally { S.busy = false; $("energyBtn").disabled = !S.selected; }
}
$("energyBtn").onclick = loadEnergy;

function windowed() {
  if (!S.readings.length || !S.rangeH) return S.readings;
  const cutoff = S.readings[S.readings.length - 1].t - S.rangeH * 3600e3;   // measured back from the last reading, not "now"
  return S.readings.filter((x) => x.t >= cutoff);
}

document.querySelectorAll("#range button").forEach((b) => b.onclick = () => {
  S.rangeH = Number(b.dataset.h);
  document.querySelectorAll("#range button").forEach((x) => x.classList.toggle("on", x === b)); drawChart();
});
document.querySelectorAll("#metric button").forEach((b) => b.onclick = () => {
  S.metric = b.dataset.m;
  document.querySelectorAll("#metric button").forEach((x) => x.classList.toggle("on", x === b)); drawChart();
});

const NS = "http://www.w3.org/2000/svg";
function sv(name, attrs, text) { const n = document.createElementNS(NS, name); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text) n.textContent = text; return n; }
const FIELD = { kwh: "kwh", interval: "intervalKwh", volt: "voltR" };
const LABEL = { kwh: "kWh", interval: "kWh per interval", volt: "voltR" };

function fillStats(win) {
  const kw = win.filter((x) => x.kwh !== null), volts = win.map((x) => x.voltR).filter((v) => v !== null), iv = win.map((x) => x.intervalKwh).filter((v) => v !== null);
  $("sKwh").textContent = kw.length ? fmt(kw[kw.length - 1].kwh) : "—";
  $("sSpan").textContent = kw.length > 1 ? fmt(kw[kw.length - 1].kwh - kw[0].kwh) : "—";
  $("sPeak").textContent = iv.length ? fmt(Math.max(...iv), 3) : "—";
  $("sVolt").textContent = volts.length ? fmt(volts.reduce((a, b) => a + b, 0) / volts.length, 1) : "—";
  $("chartSub").textContent = S.readings.length ? `${S.selected} · ${win.length} readings in window · ${when(win[0].timestamp)} → ${when(win[win.length - 1].timestamp)}` : (S.selected ? `${S.selected} · no readings loaded` : "No readings loaded");
}

function fillTable(win) {
  const tb = $("readTable").querySelector("tbody"); tb.textContent = "";
  const rows = win.slice(-100).reverse();                       // newest first, capped for speed
  for (const x of rows) {
    const tr = h("tr");
    [when(x.timestamp), fmt(x.kwh), fmt(x.kvah), fmt(x.voltR, 0), fmt(x.intervalKwh, 3)].forEach((v) => tr.appendChild(h("td", "", v)));
    tb.appendChild(tr);
  }
  $("tableNote").textContent = win.length > rows.length ? `Showing the latest ${rows.length} of ${win.length} readings in this window.` : "";
}

function drawChart() {
  const svg = $("chart"); svg.textContent = "";
  const win = windowed();
  fillStats(win.length ? win : []); fillTable(win);
  const W = 800, H = 280, L = 70, R = 16, T = 16, B = 30;
  const pts = win.filter((x) => x[FIELD[S.metric]] !== null && x[FIELD[S.metric]] !== undefined);
  svg.onmousemove = svg.onmouseleave = null;
  if (pts.length < 2) {
    const msg = !S.selected ? "Select a meter to begin." : S.readings.length ? "Not enough data for this view." : "Click “Load energy readings”.";
    svg.appendChild(sv("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: "#8d9bb8", "font-size": 15 }, msg)); return;
  }
  const key = FIELD[S.metric], vals = pts.map((p) => p[key]);
  let min = Math.min(...vals), max = Math.max(...vals);
  if (S.metric === "interval") min = Math.min(0, min);          // bars start at zero
  const span = max - min || 1, t0 = pts[0].t, t1 = pts[pts.length - 1].t, dt = t1 - t0 || 1;
  const x = (t) => L + ((t - t0) / dt) * (W - L - R), y = (v) => T + (1 - (v - min) / span) * (H - T - B);
  const dec = S.metric === "interval" ? 2 : S.metric === "volt" ? 0 : 1;

  for (const f of [0, 0.5, 1]) {
    const v = min + span * f;
    svg.append(sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#1d2a47", "stroke-dasharray": "4 4" }),
               sv("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end", fill: "#8d9bb8", "font-size": 12 }, fmt(v, dec)));
  }
  if (S.metric === "interval") {
    const bw = Math.max(1.5, ((W - L - R) / pts.length) * 0.7);
    pts.forEach((p) => svg.appendChild(sv("rect", { x: x(p.t) - bw / 2, y: y(p[key]), width: bw, height: Math.max(0, y(0) - y(p[key])), fill: "#38bdf8", opacity: 0.85 })));
  } else {
    const color = S.metric === "volt" ? "#a78bfa" : "#2dd4bf";
    const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
    svg.append(sv("path", { d: `${line}L${x(t1)},${H - B}L${x(t0)},${H - B}Z`, fill: color, opacity: 0.12 }),
               sv("path", { d: line, fill: "none", stroke: color, "stroke-width": 2.2, "stroke-linejoin": "round" }));
  }
  svg.append(sv("text", { x: L, y: H - 8, fill: "#8d9bb8", "font-size": 12 }, when(pts[0].timestamp)),
             sv("text", { x: W - R, y: H - 8, "text-anchor": "end", fill: "#8d9bb8", "font-size": 12 }, when(pts[pts.length - 1].timestamp)));

  const dot = sv("circle", { r: 5, fill: "#fff", visibility: "hidden" }); svg.appendChild(dot);
  svg.onmousemove = (e) => {
    const box = svg.getBoundingClientRect(), vx = ((e.clientX - box.left) / box.width) * W;
    const t = t0 + ((vx - L) / (W - L - R)) * dt;
    const p = pts.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
    dot.setAttribute("cx", x(p.t)); dot.setAttribute("cy", y(p[key])); dot.setAttribute("visibility", "visible");
    $("readout").textContent = `${when(p.timestamp)} · ${fmt(p.kwh)} kWh (cumulative) · interval ${fmt(p.intervalKwh, 3)} kWh · voltR ${fmt(p.voltR, 0)}`;
  };
  svg.onmouseleave = () => dot.setAttribute("visibility", "hidden");
}

// ======================= TRANSFORMERS VIEW =======================
async function loadTransformers() {
  setState("dtState", "Loading…"); $("dtTable").hidden = true;
  try {
    const r = await api(`/api/v1/distribution-transformers?page=${S.dtPage}`);
    const tbody = $("dtTable").querySelector("tbody"); tbody.textContent = "";
    if (!r.data.length) { setState("dtState", "No transformers on this page."); return; }
    setState("dtState", ""); $("dtTable").hidden = false;
    const maxCap = Math.max(...r.data.map((t) => t.capacityKva || 0), 1);
    for (const t of r.data) {
      const tr = h("tr");
      [t.code, t.name, t.feederCode, t.capacityKva].forEach((v) => tr.appendChild(h("td", "", v ?? "—")));
      const td = h("td"), bar = h("div", "bar"); bar.style.width = ((t.capacityKva || 0) / maxCap) * 100 + "%";
      td.appendChild(bar); tr.appendChild(td); tbody.appendChild(tr);
    }
    const pages = r.total && r.pageSize ? Math.ceil(r.total / r.pageSize) : null;
    $("dtPageInfo").textContent = `Page ${r.page}` + (pages ? ` of ${pages}` : "") + (r.total != null ? ` · ${r.total} transformers` : "");
    $("dtPrev").disabled = S.dtPage <= 1; $("dtNext").disabled = pages ? S.dtPage >= pages : false;
    stamp();
  } catch (e) { setState("dtState", e.message, true); }
}
$("dtPrev").onclick = () => { S.dtPage--; loadTransformers(); };
$("dtNext").onclick = () => { S.dtPage++; loadTransformers(); };

// ======================= START =======================
checkHealth(); loadMeters(); drawChart();
