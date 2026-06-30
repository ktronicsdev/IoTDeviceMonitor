/* Generic PH1800 (all-inverter) dashboard — pure pass-through of ShineMonitor.
   The per-plant page sets window.PH1800 = {username, label}. The user types only their
   password; the data file is sha256(username:password).json on the ph1800-live branch, so a
   plant's data isn't reachable without its credentials. NO calculations — everything shown is
   exactly what ShineMonitor returned. */
(function () {
  const CFG = window.PH1800 || {};
  const REPO = "ktronicsdev/IoTDeviceMonitor", BRANCH = "ph1800-live";
  const PALETTE = ["#22c55e", "#3b82f6", "#eab308", "#ec4899", "#22d3ee", "#a78bfa",
                   "#f59e0b", "#10b981", "#f87171", "#94a3b8"];
  let DATA = null, plantIdx = 0, curTab = "live", chart = null, dayIdx = null, sel = [];
  const $ = (id) => document.getElementById(id);

  async function sha256hex(s) {
    const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
  }
  async function fetchPlant(file) {
    const api = `https://api.github.com/repos/${REPO}/contents/${file}?ref=${BRANCH}`;
    try {
      const r = await fetch(api, { cache: "no-store", headers: { Accept: "application/vnd.github.raw" } });
      if (r.ok) return await r.json();
    } catch (e) {}
    try {
      const r2 = await fetch(`https://raw.githubusercontent.com/${REPO}/${BRANCH}/${file}?t=` + Date.now(), { cache: "no-store" });
      if (r2.ok) return await r2.json();
    } catch (e) {}
    return null;
  }

  // ---------- login ----------
  function showGate(err) {
    $("root").innerHTML =
      `<div class="gate"><div class="gatebox">
         <h1>${CFG.label || "Plant"}</h1>
         <div class="sub">Solar monitoring — sign in</div>
         <input id="gp" type="password" placeholder="Password" autofocus>
         <button class="go" id="ggo">View dashboard</button>
         <div class="err">${err || ""}</div>
       </div></div>`;
    const go = () => doLogin($("gp").value);
    $("ggo").onclick = go;
    $("gp").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }
  async function doLogin(pw) {
    if (!pw) return;
    $("ggo").textContent = "…";
    const file = (await sha256hex(`${CFG.username}:${pw}`)) + ".json";
    const data = await fetchPlant(file);
    if (!data) { showGate("Wrong password, or no data published yet."); return; }
    sessionStorage.setItem("ph1800_pw", pw);
    start(data);
  }
  function start(data) {
    DATA = data;
    buildShell();
    render();
    setInterval(refresh, 300000);
  }
  async function refresh() {
    const pw = sessionStorage.getItem("ph1800_pw"); if (!pw) return;
    const d = await fetchPlant((await sha256hex(`${CFG.username}:${pw}`)) + ".json");
    if (d) { DATA = d; render(); }
  }

  // ---------- shell ----------
  function buildShell() {
    $("root").innerHTML =
      `<div class="topbar">
         <h1 id="hname"></h1><span id="hstatus" class="muted"></span>
         <select class="d" id="plantsel" style="display:none"></select>
         <span class="spacer"></span>
         <span class="muted" id="hupd"></span>
       </div>
       <div class="wrap">
         <div class="tabs">
           <button data-t="live" class="active">Live</button>
           <button data-t="history">History</button>
           <button data-t="info">Info</button>
         </div>
         <div id="pane"></div>
         <div class="foot">© Ktronics (Pvt) Ltd · data via ShineMonitor (shown as-is)</div>
       </div>`;
    $("root").querySelectorAll(".tabs button").forEach((b) =>
      (b.onclick = () => { curTab = b.dataset.t; $("root").querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("active", x === b)); render(); }));
    const ps = $("plantsel");
    if (DATA.plants.length > 1) {
      ps.style.display = "";
      ps.innerHTML = DATA.plants.map((p, i) => `<option value="${i}">${p.plant.name}</option>`).join("");
      ps.onchange = () => { plantIdx = +ps.value; dayIdx = null; sel = []; render(); };
    }
  }
  const plant = () => DATA.plants[plantIdx] || {};

  // ---------- render dispatch ----------
  function render() {
    if (!DATA) return;
    const p = plant(), dev = p.device || {};
    $("hname").textContent = (p.plant && p.plant.name) || DATA.account || "Plant";
    const cls = dev.online ? "on" : "off";
    $("hstatus").innerHTML = `<span class="dot ${cls}"></span>${dev.online ? "Online" : "Offline"}` +
      (dev.alias ? ` · ${dev.alias}` : "");
    $("hupd").textContent = "Last update " + (dev.last_update || "—");
    if (curTab === "live") renderLive();
    else if (curTab === "history") renderHistory();
    else renderInfo();
  }

  // ---------- Live: card grid of every field ----------
  function renderLive() {
    const f = plant().latest || {};
    const cards = Object.keys(f).map((k) => {
      const v = f[k];
      return `<div class="m"><div class="l">${v.label || k}</div>
        <div class="v">${fmt(v.value)}${v.unit ? `<span class="u">${v.unit}</span>` : ""}</div></div>`;
    }).join("");
    $("pane").innerHTML = `<div class="card"><div class="ch"><h2>Live readings</h2>
      <span class="muted">straight from ShineMonitor</span></div>
      <div class="grid">${cards || '<div class="muted">No live fields.</div>'}</div></div>`;
  }
  const fmt = (v) => (v === null || v === undefined || v === "") ? "—"
    : (typeof v === "number" ? (Math.round(v * 100) / 100).toLocaleString() : String(v));

  // ---------- History: pick fields, navigate days, line chart ----------
  const minOfDay = (t) => { const m = String(t).slice(11, 16).split(":").map(Number); return (m[0] || 0) * 60 + (m[1] || 0); };
  const hhmm = (v) => { v = Math.round(v); return String(Math.floor(v / 60)).padStart(2, "0") + ":" + String(v % 60).padStart(2, "0"); };
  function dayList() {
    const s = plant().series || [];
    return [...new Set(s.map((r) => String(r.timestamp).slice(0, 10)))].sort();
  }
  function numericFields() {
    const s = plant().series || [], f = plant().series_fields || [];
    return f.filter((c) => s.some((r) => typeof r[c.key] === "number"));
  }
  function renderHistory() {
    const fields = numericFields(), days = dayList();
    if (!fields.length || !days.length) {
      $("pane").innerHTML = `<div class="card"><div class="muted">No numeric history yet.</div></div>`;
      return;
    }
    if (!sel.length) sel = fields.slice(0, 2).map((c) => c.key);
    if (dayIdx === null || dayIdx >= days.length) dayIdx = days.length - 1;
    const day = days[dayIdx];
    const chips = fields.map((c, i) => {
      const on = sel.includes(c.key);
      return `<span class="chip ${on ? "" : "off"}" data-k="${c.key}">
        <i style="background:${PALETTE[i % PALETTE.length]}"></i>${c.label}</span>`;
    }).join("");
    $("pane").innerHTML = `<div class="card">
      <div class="ch"><h2>History</h2>
        <div class="muted" style="display:flex;gap:8px;align-items:center">
          <button class="btn" id="dprev">‹</button><select class="d" id="dsel"></select><button class="btn" id="dnext">›</button></div></div>
      <div class="chips" id="chips">${chips}</div>
      <canvas id="hcanvas"></canvas></div>`;
    const dsel = $("dsel");
    dsel.innerHTML = days.map((d) => `<option value="${d}">${d}</option>`).join("");
    dsel.value = day;
    dsel.onchange = () => { dayIdx = days.indexOf(dsel.value); renderHistory(); };
    $("dprev").onclick = () => { if (dayIdx > 0) { dayIdx--; renderHistory(); } };
    $("dnext").onclick = () => { if (dayIdx < days.length - 1) { dayIdx++; renderHistory(); } };
    $("chips").querySelectorAll(".chip").forEach((c) => (c.onclick = () => {
      const k = c.dataset.k;
      sel = sel.includes(k) ? sel.filter((x) => x !== k) : [...sel, k];
      renderHistory();
    }));
    drawHistory(day, fields);
  }
  function drawHistory(day, fields) {
    const s = (plant().series || []).filter((r) => String(r.timestamp).slice(0, 10) === day)
      .sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1));
    const datasets = sel.map((k) => {
      const i = fields.findIndex((c) => c.key === k), col = PALETTE[i % PALETTE.length];
      return { label: (fields[i] || {}).label || k, borderColor: col, backgroundColor: col,
        borderWidth: 1.5, pointRadius: 0, tension: 0.25, spanGaps: true,
        data: s.map((r) => ({ x: minOfDay(r.timestamp), y: typeof r[k] === "number" ? r[k] : null })) };
    });
    const cfg = { type: "line", data: { datasets },
      options: { responsive: true, interaction: { mode: "index", intersect: false },
        scales: { x: { type: "linear", min: 0, max: 1440, ticks: { color: "#8a94a6", stepSize: 120, callback: hhmm }, grid: { color: "#1f2937" } },
          y: { ticks: { color: "#8a94a6" }, grid: { color: "#1f2937" } } },
        plugins: { legend: { labels: { color: "#aeb9cb", usePointStyle: false, boxWidth: 9, boxHeight: 9, font: { size: 11 } } },
          tooltip: { callbacks: { title: (i) => (i.length ? hhmm(i[0].parsed.x) : "") } } } } };
    if (chart) chart.destroy();
    chart = new Chart($("hcanvas"), cfg);
  }

  // ---------- Info ----------
  function renderInfo() {
    const p = plant().plant || {}, dev = plant().device || {};
    const row = (l, v) => v ? `<span>${l} <b>${v}</b></span>` : "";
    $("pane").innerHTML = `<div class="card"><div class="ch"><h2>Plant info</h2></div>
      <div class="info">
        ${row("Name", p.name)}${row("Capacity", p.nominal_power_kw ? p.nominal_power_kw + " kW" : "")}
        ${row("Installed", (p.install || "").slice(0, 10))}${row("Location", p.country)}
        ${row("Device", dev.alias)}${row("Device code", dev.devcode)}
        ${row("Serial", dev.sn)}${row("Status", dev.online ? "Online" : "Offline")}
      </div>
      <div class="muted" style="margin-top:10px">All values are passed through unchanged from ShineMonitor — no estimates or calculations.</div>
    </div>`;
  }

  // ---------- boot ----------
  if (!CFG.username) { $("root").innerHTML = `<div class="gate"><div class="gatebox"><h1>Not configured</h1><div class="sub">This page is missing its plant config.</div></div></div>`; return; }
  const saved = sessionStorage.getItem("ph1800_pw");
  if (saved) doLogin(saved); else showGate("");
})();
