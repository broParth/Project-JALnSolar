/* Project JAL — front-end logic for dashboard, simulation, history and settings pages.
   Talks only to the JSON endpoints defined in app.py. */
(function () {
  "use strict";

  // ---------- Helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const nf0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
  const L = (v) => nf0.format(Number(v) || 0);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const todayISO = () => {
    const d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  };
  const nowHM = () => new Date().toTimeString().slice(0, 5);

  async function api(url, options = {}) {
    const csrf = document.querySelector('meta[name="csrf-token"]');
    const opts = { credentials: "same-origin", ...options,
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf ? csrf.content : "", ...(options.headers || {}) } };
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      throw new Error("Could not reach the JAL server. Is app.py running?");
    }
    if ((res.redirected && res.url.includes("/login")) || res.status === 401) { window.location = "/login"; throw new Error("Session expired."); }
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok || data.ok === false) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  }

  // Build a JSON payload from a form, skipping blank fields so the server defaults apply.
  function formPayload(form) {
    const out = {};
    new FormData(form).forEach((v, k) => { if (String(v).trim() !== "") out[k] = String(v).trim(); });
    return out;
  }

  let toastTimer;
  function toast(msg, isErr = false) {
    let el = $("#toast");
    if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; el.setAttribute("role", "status"); document.body.appendChild(el); }
    el.textContent = msg;
    el.classList.toggle("err", isErr);
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 3500);
  }

  function showResult(el, kind, html) {
    if (!el) return;
    el.className = `result show ${kind}`;
    el.innerHTML = html;
  }

  async function withBusy(btn, fn) {
    const label = btn ? btn.innerHTML : "";
    if (btn) { btn.disabled = true; btn.textContent = "Working…"; }
    try { return await fn(); } finally { if (btn) { btn.disabled = false; btn.innerHTML = label; } }
  }

  const ICONS = {
    rainfall: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.7s-6 6.6-6 11.1a6 6 0 0 0 12 0C18 9.3 12 2.7 12 2.7z"/></svg>',
    usage: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10h9a3 3 0 0 1 3 3v1"/><path d="M4 7v6"/><path d="M16 18v.01M16 21v.01"/></svg>',
    solar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    weather_import: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 0 1 0 9z"/></svg>',
    tank_adjust: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg>',
  };
  ICONS.debris_clean = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18l-7 8v6l-4-2v-4z"/></svg>';
  ICONS.solar_use ='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>';
  const TYPE_LABEL = { rainfall: "Rainfall", solar: "Solar generation", solar_use: "Solar energy use", usage: "Water use", weather_import: "Weather estimate", tank_adjust: "Tank adjustment", debris_clean: "Catcher cleaned" };
  const TYPE_CLASS = { rainfall: "water", solar: "solar", solar_use: "solar", usage: "danger", weather_import: "leaf", tank_adjust: "water", debris_clean: "leaf" };

  function eventSummary(e) {
    if (e.event_type === "solar") return `${nf2.format(e.solar_kwh || 0)} kWh`;
    if (e.event_type === "solar_use") return `−${nf2.format(e.energy_kwh || 0)} kWh${Number(e.grid_kwh) > 0.005 ? ` · ${nf2.format(e.grid_kwh)} from grid` : ""}`;
    if (e.event_type === "rainfall") return `+${L(e.collected_l)} L · ${nf1.format(e.rainfall_mm || 0)} mm`;
    if (e.event_type === "usage") return `−${L(e.used_l)} L${Number(e.unmet_l) > 0.5 ? ` · ${L(e.unmet_l)} L short` : ""}`;
    if (e.event_type === "tank_adjust") return "levels set";
    if (e.event_type === "debris_clean") return "load reset";
    return `${L(e.collected_l)} L est. · ${nf1.format(e.rainfall_mm || 0)} mm`;
  }

  // ---------- Shared renderers ----------
  function renderTanks(container, tanks) {
    if (!container) return;
    container.innerHTML = tanks.map((t) => {
      const pct = t.capacity_l > 0 ? Math.min(100, (t.level_l / t.capacity_l) * 100) : 0;
      return `<div class="tank">
        <div class="tank-body" role="img" aria-label="Tank ${t.tank_number} is ${Math.round(pct)}% full">
          <div class="tank-fill${pct >= 99.5 ? " full" : ""}" data-pct="${pct}"></div>
          <div class="tank-pct num">${Math.round(pct)}%</div>
        </div>
        <div class="tank-name">Tank ${t.tank_number}</div>
        <div class="tank-meta num"><b>${L(t.level_l)} L</b><br>of ${L(t.capacity_l)}</div>
      </div>`;
    }).join("");
    requestAnimationFrame(() => $$(".tank-fill", container).forEach((f) => { f.style.height = f.dataset.pct + "%"; }));
  }

  function renderEventList(container, events) {
    if (!container) return;
    if (!events.length) { container.innerHTML = '<div class="empty">No events yet. Run a rainfall simulation to get started.</div>'; return; }
    container.innerHTML = `<ul class="event-list">${events.map((e) => `
      <li>
        <span class="event-dot stat-icon ${TYPE_CLASS[e.event_type] || "water"}">${ICONS[e.event_type] || ICONS.rainfall}</span>
        <div class="event-body">
          <div class="event-title"><span>${esc(TYPE_LABEL[e.event_type] || e.event_type)}</span><span class="num">${eventSummary(e)}</span></div>
          <div class="event-sub">${esc(e.event_date)} ${esc(e.event_time)}${e.village ? " · " + esc(e.village) : ""}</div>
          ${e.details ? `<div class="event-sub">${esc(e.details)}</div>` : ""}
        </div>
      </li>`).join("")}</ul>`;
  }

  // Count a number up (or down) smoothly from its current value.
  function animateNumber(el, to, fmt = nf1) {
    if (!el) return;
    const from = parseFloat(el.dataset.v || "0") || 0;
    el.dataset.v = to;
    const t0 = performance.now(), dur = 900;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt.format(from + (to - from) * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // Live solar output card on the simulation page.
  function renderSolarLive(state, burstKwh) {
    const scene = $("#solar-scene");
    if (!scene) return;
    const events = (state.events || []).filter((e) => Number(e.solar_kwh) > 0);
    const today = todayISO();
    const add = (list, key) => list.reduce((a, e) => a + (Number(e[key]) || 0), 0);
    const todays = events.filter((e) => e.event_date === today);
    const todayKwh = add(todays, "solar_kwh");
    const total = add(events, "solar_kwh"), homes = add(events, "village_energy_kwh"), sensors = add(events, "system_energy_kwh");

    // More energy today → brighter panel and faster flow (capped at 20 kWh/day).
    const intensity = todayKwh > 0 ? 0.4 + 0.6 * Math.min(1, todayKwh / 20) : 0;
    scene.classList.toggle("idle", intensity === 0);
    scene.style.setProperty("--glow", intensity.toFixed(2));
    scene.style.setProperty("--flow-speed", `${(1.8 - intensity * 1.2).toFixed(2)}s`);

    animateNumber($("#solar-today"), todayKwh);
    animateNumber($("#solar-total"), total);
    const base = Math.max(total, 0.0001);
    $("#live-homes").style.width = `${(homes / base) * 100}%`;
    $("#live-sensors").style.width = `${(sensors / base) * 100}%`;
    setText("live-split-text", total > 0
      ? `${nf1.format(homes)} kWh to homes · ${nf1.format(sensors)} kWh to sensors · ${nf1.format(Math.max(0, total - homes - sensors))} kWh to battery or spare`
      : "No solar generation recorded yet.");
    const ins = state.insights || {};
    if (ins.battery_capacity_kwh !== undefined) {
      const pct = ins.battery_capacity_kwh > 0 ? (ins.battery_level_kwh / ins.battery_capacity_kwh) * 100 : 0;
      const bar = $("#live-battery");
      if (bar) bar.style.width = `${Math.min(100, pct)}%`;
      setText("live-battery-text", `${nf1.format(ins.battery_level_kwh)} / ${nf1.format(ins.battery_capacity_kwh)} kWh`);
      let text = `Pumping has used ${nf1.format(ins.pump_kwh_total)} kWh: ${nf1.format(ins.battery_used_kwh_total)} from the battery, ${nf1.format(ins.grid_kwh_total)} from the grid.`;
      if (ins.solar_use_kwh_total > 0) {
        text += ` Sensors/system have used ${nf1.format(ins.solar_use_kwh_total)} kWh: ${nf1.format(ins.solar_use_battery_kwh_total)} from the battery, ${nf1.format(ins.solar_use_grid_kwh_total)} from the grid.`;
      }
      setText("live-pump-text", text);
    }
    setText("solar-live-sub", todayKwh > 0
      ? `${todays.length} solar reading${todays.length === 1 ? "" : "s"} today: panels are powering homes and sensors`
      : "No solar recorded today: record generation to light it up");

    if (burstKwh) {
      const b = $("#solar-burst");
      b.textContent = `+${nf2.format(burstKwh)} kWh`;
      b.classList.remove("go"); void b.offsetWidth; b.classList.add("go");
    }
  }

  function setText(id, value) { const el = document.getElementById(id); if (el) el.textContent = value; }

  function renderStats(state) {
    const events = state.events || [];
    const sum = (type, key) => events.filter((e) => e.event_type === type).reduce((a, e) => a + (Number(e[key]) || 0), 0);
    const pct = state.total_capacity_l ? (state.total_level_l / state.total_capacity_l) * 100 : 0;
    setText("stat-stored", L(state.total_level_l));
    setText("stat-stored-pct", `${nf1.format(pct)}% of ${L(state.total_capacity_l)} L capacity`);
    setText("stat-space", L(state.total_remaining_space_l));
    setText("stat-collected", L(sum("rainfall", "collected_l")));
    setText("stat-used", `${L(sum("usage", "used_l"))} L used to date`);
    // Solar can come from standalone solar events or from older rainfall events that carried solar data.
    const solarSum = (key) => events.reduce((a, e) => a + (Number(e[key]) || 0), 0);
    setText("stat-solar", nf1.format(solarSum("solar_kwh")));
    setText("stat-solar-split", `${nf1.format(solarSum("village_energy_kwh"))} kWh to village homes`);
    setText("stat-events", L(events.length));

    const ins = state.insights || {};
    if (ins.days_left === null || ins.days_left === undefined) {
      setText("stat-days", "–");
      setText("stat-days-sub", "Record water use to estimate this");
    } else {
      setText("stat-days", ins.days_left >= 100 ? nf0.format(ins.days_left) : nf1.format(ins.days_left));
      setText("stat-days-sub", `At ${L(ins.avg_daily_demand_l)} L/day (last ${ins.demand_window_days} day${ins.demand_window_days === 1 ? "" : "s"} of use), no more rain`);
    }
    const daysCard = $("#card-days");
    if (daysCard) daysCard.classList.toggle("alert", ins.days_left !== null && ins.days_left !== undefined && ins.days_left < 7);
    setText("stat-unmet", L(ins.unmet_total_l));
    setText("stat-unmet-sub", ins.unmet_total_l > 0.5
      ? `${L(ins.window_unmet_l)} L in the recent window · tanks ran short`
      : "All recorded demand was met");
    const batPct = ins.battery_capacity_kwh > 0 ? (ins.battery_level_kwh / ins.battery_capacity_kwh) * 100 : 0;
    setText("stat-battery", nf1.format(ins.battery_level_kwh || 0));
    setText("stat-battery-sub", `${nf0.format(batPct)}% of ${nf1.format(ins.battery_capacity_kwh || 0)} kWh · charged by spare solar`);
    setText("stat-pump", nf1.format(ins.pump_kwh_total || 0));
    setText("stat-pump-sub", ins.pump_kwh_total > 0
      ? `${nf0.format(((ins.pump_kwh_total - ins.grid_kwh_total) / ins.pump_kwh_total) * 100)}% from solar battery, ${nf1.format(ins.grid_kwh_total)} kWh from grid`
      : `${ins.pump_kwh_per_kl ?? "–"} kWh per 1,000 L pumped`);
  }

  // ---------- Charts ----------
  const charts = {};
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function makeChart(id, config) {
    const canvas = document.getElementById(id);
    if (!canvas || typeof Chart === "undefined") return;
    if (charts[id]) charts[id].destroy();
    Chart.defaults.font.family = '"Plus Jakarta Sans", system-ui, sans-serif';
    Chart.defaults.color = css("--ink-2");
    charts[id] = new Chart(canvas, config);
  }

  function dailySeries(events) {
    const days = {};
    events.forEach((e) => {
      const d = (days[e.event_date] ||= { collected: 0, used: 0, rain: 0, solar: 0 });
      if (e.event_type === "rainfall") { d.collected += Number(e.collected_l) || 0; d.rain += Number(e.rainfall_mm) || 0; }
      d.solar += Number(e.solar_kwh) || 0;
      if (e.event_type === "usage") d.used += Number(e.used_l) || 0;
    });
    const labels = Object.keys(days).sort();
    return { labels, days };
  }

  function waterChart(id, events) {
    const { labels, days } = dailySeries(events);
    makeChart(id, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Collected (L)", data: labels.map((d) => Math.round(days[d].collected)), backgroundColor: css("--water"), borderRadius: 6, maxBarThickness: 28 },
          { label: "Used (L)", data: labels.map((d) => Math.round(days[d].used)), backgroundColor: css("--danger"), borderRadius: 6, maxBarThickness: 28 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 3 } } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: css("--line") }, ticks: { callback: (v) => nf0.format(v) } } },
      },
    });
  }

  function solarChart(id, events) {
    const rain = events.filter((e) => Number(e.solar_kwh) > 0);
    const solar = rain.reduce((a, e) => a + (Number(e.solar_kwh) || 0), 0);
    const village = rain.reduce((a, e) => a + (Number(e.village_energy_kwh) || 0), 0);
    const system = rain.reduce((a, e) => a + (Number(e.system_energy_kwh) || 0), 0);
    const other = Math.max(0, solar - village - system);
    makeChart(id, {
      type: "doughnut",
      data: {
        labels: ["Village homes", "Sensors / IoT", "Battery / spare"],
        datasets: [{ data: [village, system, other].map((v) => +v.toFixed(2)), backgroundColor: [css("--solar"), css("--leaf"), "#c7d6df"], borderWidth: 0 }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "68%",
        plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10 } }, tooltip: { callbacks: { label: (c) => ` ${c.label}: ${nf2.format(c.raw)} kWh` } } },
      },
    });
  }

  function rainLineChart(id, events) {
    const { labels, days } = dailySeries(events);
    makeChart(id, {
      type: "line",
      data: { labels, datasets: [{ label: "Rainfall (mm)", data: labels.map((d) => +days[d].rain.toFixed(1)), borderColor: css("--water-deep"), backgroundColor: "rgba(28,143,214,.12)", fill: true, tension: .3, pointRadius: 3 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: css("--line") } } } },
    });
  }

  // ---------- Pages ----------
  async function initDashboard() {
    try {
      const state = await api("/api/state");
      renderStats(state);
      renderTanks($("#tanks"), state.tanks);
      renderEventList($("#recent"), state.recent);
      waterChart("chart-water", state.events);
      solarChart("chart-solar", state.events);
      const rainEvents = state.events.filter((e) => e.event_type === "rainfall");
      document.body.classList.toggle("is-raining", rainEvents.length > 0);
    } catch (e) { toast(e.message, true); }
  }

  async function initSimulation() {
    const rainForm = $("#rain-form");
    const useForm = $("#usage-form");
    $$('input[name="event_date"]').forEach((i) => { i.value = todayISO(); });
    $$('input[name="event_time"]').forEach((i) => { i.value = nowHM(); });

    let state;
    async function refresh(burstKwh) {
      state = await api("/api/state");
      renderTanks($("#tanks"), state.tanks);
      renderSolarLive(state, burstKwh);
      renderDebris(state);
      setText("sim-stored", `${L(state.total_level_l)} L stored · ${L(state.total_remaining_space_l)} L free`);
      updatePreview();
      updateDemand();
    }

    function renderDebris(st) {
      const d = (st.insights || {}).debris;
      if (!d) return;
      setText("debris-kg", `${nf2.format(d.kg)} kg`);
      setText("debris-pct", d.avg_capture_percent === null ? "–" : `${nf1.format(d.avg_capture_percent)}%`);
      setText("debris-status", d.clean_due ? "Clean soon" : "OK");
      $("#debris-card").classList.toggle("due", d.clean_due);
      $("#debris-bar").style.width = `${Math.min(100, (d.kg / d.alert_kg) * 100)}%`;
      $("#debris-bar").style.background = d.clean_due ? "var(--danger)" : "var(--solar)";
      setText("debris-sub", `${d.since ? `Since last cleaned (${d.since})` : "Since records began"}: ${d.rain_entries} rain entr${d.rain_entries === 1 ? "y" : "ies"}. Cleaning reminder at ${nf1.format(d.alert_kg)} kg.`);
    }

    $("#debris-clean").addEventListener("click", (ev) => withBusy(ev.currentTarget, async () => {
      if (!confirm("Record that the debris catcher was cleaned now?")) return;
      try {
        const r = await api("/api/debris/clean", { method: "POST", body: { event_date: todayISO(), event_time: nowHM() } });
        toast(r.message);
        await refresh();
      } catch (e) { toast(e.message, true); }
    }));

    // Village water-demand calculator (nothing is saved).
    const demandForm = $("#demand-form");
    let dailyDemand = 0;
    function updateDemand() {
      const f = new FormData(demandForm);
      const pop = Math.max(0, parseFloat(f.get("population")) || 0);
      const lpcd = Math.max(0, parseFloat(f.get("lpcd")) || 0);
      const cropM3 = Math.max(0, parseFloat(f.get("crop_m3")) || 0);
      dailyDemand = pop * lpcd + (cropM3 * 1000) / 365;
      setText("d-daily", `${L(dailyDemand)} L`);
      const yearly = dailyDemand * 365;
      setText("d-yearly", yearly >= 1e6 ? `${nf2.format(yearly / 1e6)} million L` : `${L(yearly)} L`);
      const cap = state ? state.total_capacity_l : 0;
      setText("d-days", dailyDemand > 0 && cap ? `${nf1.format(cap / dailyDemand)} days` : "–");
      const ins = state ? state.insights || {} : {};
      setText("d-compare", !state ? "" : `Full storage is ${L(cap)} L; ${L(state.total_level_l)} L is stored now (${dailyDemand > 0 ? nf1.format(state.total_level_l / dailyDemand) : "–"} days at this demand).`
        + (ins.avg_daily_demand_l ? ` Recorded use at this site averages ${L(ins.avg_daily_demand_l)} L/day.` : ""));
    }
    demandForm.addEventListener("input", updateDemand);
    $("#d-use").addEventListener("click", () => {
      useForm.used_l.value = Math.round(dailyDemand);
      useForm.scrollIntoView({ behavior: "smooth", block: "center" });
      toast(`"Water needed" set to ${L(dailyDemand)} L`);
    });

    function updatePreview() {
      const f = new FormData(rainForm);
      const rain = Math.max(0, parseFloat(f.get("rainfall_mm")) || 0);
      const area = Math.max(1, parseFloat(f.get("canopy_area_m2")) || 100);
      const eff = Math.min(100, Math.max(0, parseFloat(f.get("collection_efficiency")) || 0)) / 100;
      const litres = rain * area * eff;
      setText("preview-litres", `${L(litres)} L (${L(litres / 5)} L per collector)`);
      if (!state) { setText("preview-note", "–"); return; }
      // Same cascade as the server: equal share per tank, a full tank passes its extra on to the next.
      let carry = 0;
      state.tanks.forEach((t) => { const incoming = litres / state.tanks.length + carry; carry = Math.max(0, incoming - Math.max(0, t.capacity_l - t.level_l)); });
      setText("preview-note", carry > 0.5 ? `About ${L(carry)} L would overflow from Tank 5` : "Fits in available storage");
    }
    rainForm.addEventListener("input", updatePreview);

    const solarForm = $("#solar-form");
    function updateSolarSplit() {
      const f = new FormData(solarForm);
      const solar = parseFloat(f.get("solar_kwh")) || 0;
      const vRaw = f.get("village_energy_kwh"), sRaw = f.get("system_energy_kwh");
      const village = vRaw === "" ? solar * 0.75 : parseFloat(vRaw) || 0;
      const system = sRaw === "" ? solar * 0.10 : parseFloat(sRaw) || 0;
      const total = Math.max(solar, village + system, 0.0001);
      $("#split-village").style.width = `${(village / total) * 100}%`;
      $("#split-system").style.width = `${(system / total) * 100}%`;
      setText("split-text", village + system > solar + 1e-9
        ? "Homes + sensors is more than the energy generated. Reduce the allocation."
        : `${nf2.format(village)} kWh homes · ${nf2.format(system)} kWh sensors · ${nf2.format(Math.max(0, solar - village - system))} kWh to battery`);
    }
    solarForm.addEventListener("input", updateSolarSplit);
    updateSolarSplit();

    solarForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", solarForm), async () => {
        try {
          const r = await api("/api/solar", { method: "POST", body: formPayload(solarForm) });
          showResult($("#solar-result"), "ok", `<strong>${nf2.format(r.solar_kwh)} kWh recorded.</strong> Battery now ${nf1.format(r.battery_level_kwh)} / ${nf1.format(r.battery_capacity_kwh)} kWh.<br><span class="small">${esc(r.message)}</span>`);
          toast("Solar generation recorded");
          await refresh(r.solar_kwh);
        } catch (e) { showResult($("#solar-result"), "err", esc(e.message)); }
      });
    });

    rainForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", rainForm), async () => {
        try {
          const r = await api("/api/rainfall", { method: "POST", body: formPayload(rainForm) });
          const kind = r.overflow_l > 0 ? "warn" : "ok";
          showResult($("#rain-result"), kind,
            `<strong>${L(r.collected_l)} L collected</strong>, ${L(r.stored_l)} L stored${r.overflow_l > 0 ? `, <strong>${L(r.overflow_l)} L overflow</strong> needs recharge/overflow handling` : ""}.<br><span class="small">${esc(r.message)}</span><br><span class="small muted">Debris catcher (estimate): about ${nf2.format(r.debris_kg)} kg caught, ${nf1.format(r.debris_percent)}% capture.</span>`);
          toast("Rainfall event recorded");
          await refresh();
        } catch (e) { showResult($("#rain-result"), "err", esc(e.message)); }
      });
    });

    useForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", useForm), async () => {
        try {
          const r = await api("/api/usage", { method: "POST", body: formPayload(useForm) });
          const short = Number(r.unmet_l) > 0.5;
          showResult($("#usage-result"), short ? "warn" : "ok",
            `<strong>${L(r.used_l)} L supplied${short ? `, ${L(r.unmet_l)} L of the ${L(r.demand_l)} L needed could not be met` : ""}.</strong><br><span class="small">${esc(r.message)}</span>`);
          toast(short ? "Recorded: tanks ran short" : "Water usage recorded", short);
          await refresh();
        } catch (e) { showResult($("#usage-result"), "err", esc(e.message)); }
      });
    });

    const solarUseForm = $("#solar-use-form");
    solarUseForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", solarUseForm), async () => {
        try {
          const r = await api("/api/solar/use", { method: "POST", body: formPayload(solarUseForm) });
          const short = Number(r.grid_kwh) > 0.005;
          showResult($("#solar-use-result"), short ? "warn" : "ok",
            `<strong>${nf2.format(r.energy_kwh)} kWh used</strong>: ${nf2.format(r.battery_out_kwh)} from the battery${short ? `, <strong>${nf2.format(r.grid_kwh)} from the grid</strong> (battery ran short)` : ""}. Battery now ${nf1.format(r.battery_level_kwh)} / ${nf1.format(r.battery_capacity_kwh)} kWh.<br><span class="small">${esc(r.message)}</span>`);
          toast(short ? "Recorded: battery ran short" : "Solar energy use recorded", short);
          await refresh();
        } catch (e) { showResult($("#solar-use-result"), "err", esc(e.message)); }
      });
    });

    $$("[data-preset]").forEach((b) => b.addEventListener("click", () => {
      const [mm, minutes] = b.dataset.preset.split(",");
      rainForm.rainfall_mm.value = mm; rainForm.duration_minutes.value = minutes;
      updatePreview();
    }));

    try { await refresh(); } catch (e) { toast(e.message, true); }
  }

  async function initHistory() {
    let events = [];
    const tbody = $("#history-body");
    const filter = $("#filter-type");

    function render() {
      const type = filter.value;
      const rows = type ? events.filter((e) => e.event_type === type) : events;
      setText("history-count", `${rows.length} event${rows.length === 1 ? "" : "s"}`);
      if (!rows.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">No events to show.</td></tr>'; return; }
      tbody.innerHTML = rows.map((e) => `
        <tr>
          <td class="num" style="white-space:nowrap">${esc(e.event_date)}<div class="muted small">${esc(e.event_time)}</div></td>
          <td><span class="badge ${esc(e.event_type)}">${esc(TYPE_LABEL[e.event_type] || e.event_type)}</span></td>
          <td>${esc(e.village || "—")}</td>
          <td class="r num">${e.rainfall_mm ? nf1.format(e.rainfall_mm) : "—"}</td>
          <td class="r num">${e.collected_l ? L(e.collected_l) : "—"}</td>
          <td class="r num">${e.used_l ? L(e.used_l) : "—"}</td>
          <td class="r num">${Number(e.unmet_l) > 0.5 ? L(e.unmet_l) : "—"}</td>
          <td class="r num">${e.solar_kwh ? nf2.format(e.solar_kwh) : "—"}</td>
          <td class="details">${esc(e.details || "")}</td>
          <td><button class="icon-btn" data-del="${e.id}" title="Delete event" aria-label="Delete event">${ICONS.trash}</button></td>
        </tr>`).join("");
    }

    tbody.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-del]");
      if (!btn) return;
      if (!confirm("Delete this entry? Tank levels and battery will be recalculated as if it never happened.")) return;
      try {
        await api(`/api/history/${btn.dataset.del}`, { method: "DELETE" });
        events = (await api("/api/history")).events;  // later entries may have changed
        render(); drawCharts(); toast("Entry deleted and tanks recalculated");
      } catch (e) { toast(e.message, true); }
    });
    filter.addEventListener("change", render);

    $("#export-csv").addEventListener("click", () => {
      const cols = ["event_date", "event_time", "event_type", "village", "rainfall_mm", "duration_hours", "collected_l", "overflow_l", "demand_l", "used_l", "unmet_l", "solar_kwh", "village_energy_kwh", "system_energy_kwh", "battery_in_kwh", "energy_kwh", "debris_kg", "debris_percent", "pump_kwh", "battery_out_kwh", "grid_kwh", "details"];
      const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const csv = [cols.join(",")].concat(events.map((e) => cols.map((c) => q(e[c])).join(","))).join("\n");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      a.download = `jal-history-${todayISO()}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
    });

    function drawCharts() {
      const asc = [...events].reverse();
      waterChart("chart-water", asc);
      rainLineChart("chart-rain", asc);
    }

    try {
      const r = await api("/api/history");
      events = r.events;
      render(); drawCharts();
    } catch (e) { toast(e.message, true); }
  }

  async function initSettings() {
    const sForm = $("#settings-form");
    const tForm = $("#tanks-form");
    const wForm = $("#weather-form");
    let lastWeather = null;

    async function loadTanks() {
      const state = await api("/api/state");
      $("#tank-fields").innerHTML = state.tanks.map((t, i) => `
        <div class="card" style="padding:16px;box-shadow:none;background:var(--surface-2)">
          <h3 style="margin-bottom:10px">Tank ${t.tank_number}</h3>
          <div class="field" style="margin-bottom:10px"><label for="cap${i}">Capacity (L)</label><input id="cap${i}" type="number" min="1" step="any" name="capacity_l" value="${+t.capacity_l}" required></div>
          <div class="field"><label for="lvl${i}">Current level (L)</label><input id="lvl${i}" type="number" min="0" step="any" name="level_l" value="${+(+t.level_l).toFixed(2)}" required></div>
        </div>`).join("");
    }

    sForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", sForm), async () => {
        try {
          const f = new FormData(sForm);
          const body = Object.fromEntries(f.entries());
          await api("/api/settings", { method: "POST", body });
          toast("Settings saved");
          setTimeout(() => location.reload(), 600);  // refresh site name in the sidebar
        } catch (e) { toast(e.message, true); }
      });
    });

    tForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const caps = $$('input[name="capacity_l"]', tForm), lvls = $$('input[name="level_l"]', tForm);
      const tanks = caps.map((c, i) => ({ capacity_l: c.value, level_l: lvls[i].value }));
      withBusy($("button[type=submit]", tForm), async () => {
        try { await api("/api/tanks", { method: "POST", body: { tanks } }); toast("Tanks updated"); await loadTanks(); }
        catch (e) { toast(e.message, true); }
      });
    });

    $("#reset-btn").addEventListener("click", async () => {
      if (!confirm("Empty all tanks and the battery for this site and delete ALL of its history? This cannot be undone.")) return;
      try { const r = await api("/api/reset", { method: "POST" }); toast(r.message); await loadTanks(); }
      catch (e) { toast(e.message, true); }
    });

    // Sites
    async function loadSites() {
      const r = await api("/api/sites");
      $("#site-list").innerHTML = r.sites.map((s) => `
        <div class="site-chip${s.id === r.current ? " current" : ""}">
          <div><b>${esc(s.name)}</b><span>${s.id === r.current ? "Current site" : esc(s.village || "")}</span></div>
          ${s.id === r.current ? "" : `<button type="button" class="btn btn-ghost btn-sm" data-site-open="${s.id}">Open</button>`}
          ${r.sites.length > 1 ? `<button type="button" class="icon-btn" data-site-del="${s.id}" data-name="${esc(s.name)}" title="Delete site" aria-label="Delete site ${esc(s.name)}">${ICONS.trash}</button>` : ""}
        </div>`).join("");
    }
    $("#site-list").addEventListener("click", async (ev) => {
      const open = ev.target.closest("[data-site-open]");
      const del = ev.target.closest("[data-site-del]");
      try {
        if (open) { await api(`/api/sites/${open.dataset.siteOpen}/select`, { method: "POST" }); location.reload(); }
        if (del) {
          if (!confirm(`Delete the site "${del.dataset.name}" with its tanks and all of its history? This cannot be undone.`)) return;
          await api(`/api/sites/${del.dataset.siteDel}`, { method: "DELETE" });
          location.reload();
        }
      } catch (e) { toast(e.message, true); }
    });
    $("#new-site-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const form = ev.currentTarget;
      withBusy($("button[type=submit]", form), async () => {
        try { await api("/api/sites", { method: "POST", body: formPayload(form) }); location.reload(); }
        catch (e) { toast(e.message, true); }
      });
    });
    loadSites().catch((e) => toast(e.message, true));

    $("#use-saved-location").addEventListener("click", () => {
      wForm.place.value = sForm.village.value;
      wForm.latitude.value = sForm.latitude.value;
      wForm.longitude.value = sForm.longitude.value;
    });

    wForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const params = new URLSearchParams(formPayload(wForm));
      const out = $("#weather-result");
      withBusy($("button[type=submit]", wForm), async () => {
        try {
          const r = await api(`/api/weather?${params}`);
          lastWeather = r;
          out.innerHTML = `
            <div class="preview" style="background:var(--water-soft)">
              <div><div class="small">${esc(r.place)} · ${esc(r.start)} to ${esc(r.end)}</div><strong class="num">${nf1.format(r.total_rainfall_mm)} mm</strong> total rainfall</div>
              <div style="text-align:right"><div class="small">Estimated collectable</div><strong class="num">${L(r.estimated_collectable_litres)} L</strong></div>
            </div>
            <p class="small muted" style="margin:10px 0">Rainfall: ${esc(r.source)} · location: ${esc(r.location_source)} (${nf2.format(r.latitude)}, ${nf2.format(r.longitude)}) · ${nf1.format(r.canopy_area_m2)} m² canopy at ${nf1.format(r.collection_efficiency_percent)}% efficiency. ${esc(r.note)}</p>
            <p class="small muted credits">${(r.credits || []).map((c) => `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)}</a>`).join(" · ")}</p>
            <div class="chart-box sm"><canvas id="chart-weather" aria-label="Daily rainfall"></canvas></div>
            <div class="form-actions"><button type="button" class="btn btn-solar" id="weather-save">Save estimate to history</button><span class="small muted">Saved for analysis only, not added to tank levels.</span></div>`;
          makeChart("chart-weather", {
            type: "bar",
            data: { labels: r.rows.map((x) => x.date), datasets: [{ label: "Rainfall (mm)", data: r.rows.map((x) => x.rainfall_mm), backgroundColor: css("--water"), borderRadius: 4, maxBarThickness: 22 }] },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: css("--line") } } } },
          });
          $("#weather-save").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
            try {
              const s = await api("/api/weather/use", { method: "POST", body: { total_rainfall_mm: lastWeather.total_rainfall_mm, estimated_collectable_litres: lastWeather.estimated_collectable_litres, place: lastWeather.place, event_date: lastWeather.end } });
              toast(s.message);
            } catch (err) { toast(err.message, true); }
          }));
        } catch (e) { out.innerHTML = `<div class="result show err">${esc(e.message)}</div>`; }
      });
    });

    // Default weather window: last 7 complete days.
    const end = new Date(); end.setDate(end.getDate() - 1);
    const start = new Date(end); start.setDate(start.getDate() - 6);
    const iso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    wForm.start.value = iso(start); wForm.end.value = iso(end);

    try { await loadTanks(); } catch (e) { toast(e.message, true); }
  }

  // Sidebar site switcher (all signed-in pages).
  function initSiteSwitch() {
    const sel = $("#site-select");
    if (!sel) return;
    const current = sel.value;
    sel.addEventListener("change", async () => {
      if (sel.value === "__manage") { window.location = "/settings#sites"; return; }
      try { await api(`/api/sites/${sel.value}/select`, { method: "POST" }); location.reload(); }
      catch (e) { sel.value = current; toast(e.message, true); }
    });
  }

  function initProfile() {
    const pForm = $("#profile-form");
    const pwForm = $("#password-form");
    pForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", pForm), async () => {
        try {
          const r = await api("/api/profile", { method: "POST", body: Object.fromEntries(new FormData(pForm).entries()) });
          showResult($("#profile-result"), "ok", "Details saved.");
          setText("profile-name", r.display_name);
          setText("profile-identifier", r.identifier);
          toast("Profile updated");
          setTimeout(() => location.reload(), 800);  // refresh name and initials in the sidebar
        } catch (e) { showResult($("#profile-result"), "err", esc(e.message)); }
      });
    });
    pwForm.addEventListener("submit", (ev) => {
      ev.preventDefault();
      withBusy($("button[type=submit]", pwForm), async () => {
        try {
          const r = await api("/api/profile/password", { method: "POST", body: Object.fromEntries(new FormData(pwForm).entries()) });
          pwForm.reset();
          showResult($("#password-result"), "ok", esc(r.message) + " Use it the next time you sign in.");
          toast("Password changed");
        } catch (e) { showResult($("#password-result"), "err", esc(e.message)); }
      });
    });
  }

  const PAGES = { dashboard: initDashboard, simulation: initSimulation, history: initHistory, settings: initSettings, profile: initProfile };
  document.addEventListener("DOMContentLoaded", () => {
    initSiteSwitch();
    const page = document.body.dataset.page;
    if (PAGES[page]) PAGES[page]();
  });
})();
