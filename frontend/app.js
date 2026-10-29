// The dashboard talks ONLY to our FastAPI service, never to the original portal.
// Same origin: the API serves this dashboard at "/". For a separate dev server,
// set API to "http://127.0.0.1:8000" (and allow that origin in CORS).
const API = "";
const $ = (id) => document.getElementById(id);
let meterPage = 1, dtPage = 1, selectedId = null, busy = false, readings = [], rangeH = 24;

// Show text safely: API data is only ever written with textContent.
const fmt = (n, d = 2) => (n === null || n === undefined ? "—" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
function cell(text) { const td = document.createElement("td"); td.textContent = text ?? "—"; return td; }
function stamp() { $("lastLoaded").textContent = "Last load " + new Date().toLocaleTimeString(); }
function setState(id, msg, err = false) { const el = $(id); el.textContent = msg; el.className = "state" + (err ? " error" : ""); }

async function api(path) {
  let res;
  try { res = await fetch(API + path); }
  catch { throw new Error("Cannot reach the API. Is uvicorn running on port 8000?"); }
  let body = null;
  try { body = await res.json(); } catch { /* ignore */ }
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

// ---------- meters ----------
async function loadMeters() {
  setState("meterState", "Loading…"); $("meterTable").hidden = true;
  try {
    const q = encodeURIComponent($("searchInput").value.trim());
    const r = await api(`/api/v1/meters?q=${q}&page=${meterPage}`);
    const tbody = $("meterTable").querySelector("tbody"); tbody.textContent = "";
    setState("meterState", r.data.length ? "" : "No meters found.");
    $("meterTable").hidden = !r.data.length;
    for (const m of r.data) {
      const tr = document.createElement("tr"); tr.className = "clickable";
      [m.meterId, m.serialNo, m.make, m.phaseType, m.installationStatus, m.dtCode].forEach((v) => tr.appendChild(cell(v)));
      if (m.meterId === selectedId) tr.classList.add("selected");
      tr.onclick = () => selectMeter(m, tr);
      tbody.appendChild(tr);
    }
    const count = (s) => r.data.filter((m) => m.installationStatus === s).length;
    $("kTotal").textContent = r.total != null ? fmt(r.total, 0) : "—";
    $("kInstalled").textContent = fmt(count("Installed"), 0);
    $("kFlagged").textContent = fmt(count("Faulty") + count("Decommissioned"), 0);
    const pages = r.total && r.pageSize ? Math.ceil(r.total / r.pageSize) : null;
    $("pageInfo").textContent = `Page ${r.page}` + (pages ? ` of ${pages}` : "");
    $("prevBtn").disabled = meterPage <= 1;
    $("nextBtn").disabled = pages ? meterPage >= pages : r.data.length === 0;
    stamp();
  } catch (e) { setState("meterState", e.message, true); }
}

async function selectMeter(m, row) {
  document.querySelectorAll("#meterTable tr.selected").forEach((t) => t.classList.remove("selected"));
  row.classList.add("selected");
  selectedId = m.meterId; readings = [];
  $("energyBtn").disabled = false; drawChart(); setState("energyState", "");
  $("kLatest").textContent = "—"; $("kLatestSub").textContent = "load readings for this meter";
  $("aTitle").textContent = m.meterId; $("aSub").textContent = `${m.make ?? "Unknown make"} · serial ${m.serialNo ?? "—"}`;
  const chip = $("aStatus"); chip.hidden = !m.installationStatus; chip.textContent = m.installationStatus ?? "";
  chip.className = "chip " + (m.installationStatus ?? "");
  const dl = $("aDetail"); dl.textContent = "";
  for (const [k, v] of [["Phase", m.phaseType], ["Transformer", m.dtCode], ["Installation type", m.installationType]]) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v ?? "—"; dl.append(dt, dd);
  }
  $("hierarchy").textContent = Array.isArray(m.hierarchy) && m.hierarchy.length
    ? "Hierarchy: " + m.hierarchy.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" → ")
    : "Network hierarchy is not provided by the portal for this meter.";
  setState("geo", "Loading…");
  try {
    const g = (await api(`/api/v1/meters/${encodeURIComponent(m.meterId)}/geo`)).data;
    const el = $("geo"); el.className = "state"; el.textContent = `${g.latitude.toFixed(5)}, ${g.longitude.toFixed(5)} · `;
    const a = document.createElement("a");
    a.href = `https://www.openstreetmap.org/?mlat=${g.latitude}&mlon=${g.longitude}#map=17/${g.latitude}/${g.longitude}`;
    a.target = "_blank"; a.rel = "noopener"; a.textContent = "Open in OpenStreetMap"; el.appendChild(a);
  } catch (e) { setState("geo", e.message); }
}

// ---------- energy ----------
async function loadEnergy() {
  if (!selectedId || busy) return;
  busy = true; $("energyBtn").disabled = true; setState("energyState", "Loading readings…");
  try {
    const r = await api(`/api/v1/meters/${encodeURIComponent(selectedId)}/energy`);
    readings = r.data.map((x) => ({ ...x, t: new Date(x.timestamp).getTime() }));
    const w = r.warnings || {}, notes = [];
    if (w.skippedInvalidTimestamp) notes.push(`${w.skippedInvalidTimestamp} rows skipped (bad timestamp)`);
    if (w.duplicateTimestamps) notes.push(`${w.duplicateTimestamps} duplicate timestamps`);
    if (w.registerDecreases) notes.push(`${w.registerDecreases} register decreases`);
    setState("energyState", readings.length ? `${r.count} readings` + (notes.length ? " · " + notes.join("; ") : "") : "No readings returned.");
    const last = readings[readings.length - 1];
    if (last) { $("kLatest").textContent = fmt(last.kwh); $("kLatestSub").textContent = "cumulative kWh · " + last.timestamp.replace("T", " "); }
    drawChart(); stamp();
  } catch (e) { setState("energyState", e.message, true); }
  finally { busy = false; $("energyBtn").disabled = false; }
}

function windowed() {
  if (!readings.length || !rangeH) return readings;
  const cutoff = readings[readings.length - 1].t - rangeH * 3600e3;
  return readings.filter((x) => x.t >= cutoff);
}

const NS = "http://www.w3.org/2000/svg";
function el(name, attrs, text) { const n = document.createElementNS(NS, name); for (const k in attrs) n.setAttribute(k, attrs[k]); if (text) n.textContent = text; return n; }

function drawChart() {
  const svg = $("chart"); svg.textContent = ""; $("readout").textContent = "Hover the chart for a reading.";
  const pts = windowed().filter((x) => x.kwh !== null);
  $("sKwh").textContent = pts.length ? fmt(pts[pts.length - 1].kwh) : "—";
  const volts = pts.map((x) => x.voltR).filter((v) => v !== null);
  $("sVolt").textContent = volts.length ? fmt(volts.reduce((a, b) => a + b, 0) / volts.length, 1) : "—";
  $("sSpan").textContent = pts.length > 1 ? fmt(pts[pts.length - 1].kwh - pts[0].kwh) : "—";
  $("chartSub").textContent = selectedId ? `${selectedId} · ${pts.length} readings in window` : "No meter selected";
  const W = 800, H = 260, L = 64, R = 16, T = 16, B = 28;
  if (pts.length < 2) { svg.appendChild(el("text", { x: L, y: H / 2, fill: "#8d9bb8", "font-size": 14 }, selectedId ? "Load readings to draw the chart." : "Select a meter to begin.")); return; }
  const min = Math.min(...pts.map((p) => p.kwh)), max = Math.max(...pts.map((p) => p.kwh)), span = max - min || 1;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const x = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R), y = (v) => T + (1 - (v - min) / span) * (H - T - B);
  for (const f of [0, 0.5, 1]) {
    const v = min + span * f;
    svg.append(el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#1d2a47", "stroke-dasharray": "4 4" }), el("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end", fill: "#8d9bb8", "font-size": 11 }, fmt(v)));
  }
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.kwh).toFixed(1)}`).join("");
  svg.append(el("path", { d: `${line}L${x(t1)},${H - B}L${x(t0)},${H - B}Z`, fill: "rgba(45,212,191,.14)" }), el("path", { d: line, fill: "none", stroke: "#2dd4bf", "stroke-width": 2.2 }));
  svg.append(el("text", { x: L, y: H - 8, fill: "#8d9bb8", "font-size": 11 }, pts[0].timestamp.replace("T", " ")), el("text", { x: W - R, y: H - 8, "text-anchor": "end", fill: "#8d9bb8", "font-size": 11 }, pts[pts.length - 1].timestamp.replace("T", " ")));
  const dot = el("circle", { r: 5, fill: "#2dd4bf", visibility: "hidden" }); svg.appendChild(dot);
  svg.onmousemove = (e) => {
    const box = svg.getBoundingClientRect(), t = t0 + (((e.clientX - box.left) / box.width) * W - L) / (W - L - R) * (t1 - t0);
    const p = pts.reduce((a, b) => (Math.abs(b.t - t) < Math.abs(a.t - t) ? b : a));
    dot.setAttribute("cx", x(p.t)); dot.setAttribute("cy", y(p.kwh)); dot.setAttribute("visibility", "visible");
    $("readout").textContent = `${p.timestamp.replace("T", " ")} · ${fmt(p.kwh)} kWh · voltR ${fmt(p.voltR, 0)} · interval ${fmt(p.intervalKwh, 3)} kWh`;
  };
  svg.onmouseleave = () => dot.setAttribute("visibility", "hidden");
}

// ---------- transformers ----------
async function loadTransformers() {
  setState("dtState", "Loading…"); $("dtTable").hidden = true;
  try {
    const r = await api(`/api/v1/distribution-transformers?page=${dtPage}`);
    const tbody = $("dtTable").querySelector("tbody"); tbody.textContent = "";
    if (!r.data.length) { setState("dtState", "No transformers on this page."); return; }
    setState("dtState", ""); $("dtTable").hidden = false;
    const maxCap = Math.max(...r.data.map((t) => t.capacityKva || 0), 1);
    for (const t of r.data) {
      const tr = document.createElement("tr");
      [t.code, t.name, t.feederCode, t.capacityKva].forEach((v) => tr.appendChild(cell(v)));
      const td = document.createElement("td"), bar = document.createElement("div");
      bar.className = "bar"; bar.style.width = ((t.capacityKva || 0) / maxCap) * 100 + "%";
      td.appendChild(bar); tr.appendChild(td); tbody.appendChild(tr);
    }
    const pages = r.total && r.pageSize ? Math.ceil(r.total / r.pageSize) : null;
    $("dtPageInfo").textContent = `Page ${r.page}` + (pages ? ` of ${pages}` : "") + (r.total != null ? ` · ${r.total} transformers` : "");
    $("dtPrev").disabled = dtPage <= 1; $("dtNext").disabled = pages ? dtPage >= pages : false;
    stamp();
  } catch (e) { setState("dtState", e.message, true); }
}

// ---------- wiring ----------
$("searchBtn").onclick = () => { meterPage = 1; loadMeters(); };
$("searchInput").onkeydown = (e) => { if (e.key === "Enter") $("searchBtn").click(); };
$("prevBtn").onclick = () => { meterPage--; loadMeters(); };
$("nextBtn").onclick = () => { meterPage++; loadMeters(); };
$("dtPrev").onclick = () => { dtPage--; loadTransformers(); };
$("dtNext").onclick = () => { dtPage++; loadTransformers(); };
$("energyBtn").onclick = loadEnergy;
document.querySelectorAll("#range button").forEach((b) => b.onclick = () => {
  rangeH = Number(b.dataset.h);
  document.querySelectorAll("#range button").forEach((x) => x.classList.toggle("on", x === b));
  drawChart();
});
checkHealth(); loadMeters(); drawChart();