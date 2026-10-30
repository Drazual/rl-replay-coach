// RL Coach front end. Plain JavaScript, no build step, works offline.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
};

const state = {
  replays: [], you: null, folders: [],
  current: null, data: null, tab: "review", player: null,
  heat: { subject: "player", half: "all" }, shots: { who: "all" },
  trendMetric: "avg_boost",
};

// Field geometry in unreal units (1 uu = 1 cm).
const FIELD = { x: 4096, y: 5120, goalW: 893, goalD: 880, corner: 1152 };
const TEAM_COLOR = ["#3b82f6", "#f97316"];

// ------------------------------------------------------------------ helpers
function fmtDate(d) {
  // replay dates look like "2026-10-18 11-48-01"
  const m = /^(\d{4})-(\d\d)-(\d\d) (\d\d)-(\d\d)/.exec(d || "");
  if (!m) return d || "";
  const dt = new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5]);
  const days = Math.floor((Date.now() - dt) / 864e5);
  const time = dt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (days < 1 && new Date().getDate() === dt.getDate()) return `Today ${time}`;
  if (days < 2) return `Yesterday ${time}`;
  return dt.toLocaleDateString([], { month: "short", day: "numeric", year: dt.getFullYear() === new Date().getFullYear() ? undefined : "numeric" }) + ` ${time}`;
}
const fmtLen = (s) => (s ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "");
function youTeam(r) {
  const you = (state.you || "").toLowerCase();
  const p = (r.players || []).find((p) => (p.name || "").toLowerCase() === you);
  return p ? p.team : null;
}
function resultOf(r) {
  const t = youTeam(r);
  if (t === null || !r.score) return null;
  const a = r.score[t], b = r.score[1 - t];
  return a > b ? "win" : a < b ? "loss" : "draw";
}
function fmtVal(v, def) {
  if (v === null || v === undefined) return "–";
  const d = /\.(\d)f/.exec(def.fmt);
  return Number(v).toFixed(d ? +d[1] : 0) + (def.unit || "");
}
function toast(msg, ms = 2500) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; document.body.append(t);
  setTimeout(() => t.remove(), ms);
}

// ------------------------------------------------------------------ replay list
async function loadList() {
  const res = await api("/api/replays");
  state.replays = res.replays; state.you = res.you; state.folders = res.folders;
  const modes = [...new Set(state.replays.map((r) => r.playlist).filter(Boolean))].sort();
  const sel = $("#filter-mode"), cur = sel.value;
  sel.innerHTML = `<option value="">All modes</option>` + modes.map((m) => `<option ${m === cur ? "selected" : ""}>${esc(m)}</option>`).join("");
  renderList();
  $("#empty").hidden = !!state.current || !$("#view").hidden;  // keep it off the Live and trends pages
}

function displayName(r) {
  return r.label || r.title || `${r.playlist || "Match"} · ${r.map || ""}`;
}

function renderList() {
  const q = $("#search").value.toLowerCase().trim();
  const mode = $("#filter-mode").value, res = $("#filter-result").value;
  const rows = state.replays.filter((r) => {
    if (mode && r.playlist !== mode) return false;
    if (res && resultOf(r) !== res) return false;
    if (!q) return true;
    const hay = [displayName(r), r.file, r.map, r.playlist, ...(r.players || []).map((p) => p.name)].join(" ").toLowerCase();
    return hay.includes(q);
  });
  $("#list-count").textContent = `${rows.length} of ${state.replays.length} replays` + (state.you ? ` · you: ${state.you}` : "");
  $("#replay-list").innerHTML = rows.map((r) => {
    if (r.error) return `<li class="${r.id === state.current ? "active" : ""}" data-id="${r.id}"><div class="name muted">${esc(r.file)}</div><div class="sub">Could not read this replay</div></li>`;
    const result = resultOf(r);
    const t = youTeam(r);
    const score = r.score ? (t === 1 ? `${r.score[1]}–${r.score[0]}` : `${r.score[0]}–${r.score[1]}`) : "";
    const others = (r.players || []).map((p) => p.name).join(", ");
    return `<li class="${r.id === state.current ? "active" : ""}" data-id="${r.id}">
      <div class="name"><span class="dot ${r.analysed ? "on" : ""}" title="${r.analysed ? "Analysed" : "Not analysed yet"}"></span>${esc(displayName(r))}</div>
      <span class="score-pill ${result || "neutral"}">${result ? result[0].toUpperCase() + " " : ""}${score}</span>
      <div class="sub">${esc(fmtDate(r.date))} · ${r.team_size}v${r.team_size} · ${esc(r.map)}</div>
      <div class="sub">${esc(others)}</div>
    </li>`;
  }).join("");
}

$("#replay-list").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) openReplay(li.dataset.id);
});
["input", "change"].forEach((ev) => {
  $("#search").addEventListener(ev, renderList);
  $("#filter-mode").addEventListener(ev, renderList);
  $("#filter-result").addEventListener(ev, renderList);
});

// ------------------------------------------------------------------ open replay
let pollTimer = null;
async function openReplay(id) {
  state.liveView = false;
  state.current = id; state.data = null;
  clearTimeout(pollTimer);
  renderList();
  $("#empty").hidden = true;
  const view = $("#view"); view.hidden = false;
  const r = state.replays.find((x) => x.id === id) || {};
  view.innerHTML = `<div class="loading"><div class="spinner"></div>Analysing ${esc(displayName(r))}… this takes 5–20 seconds the first time, then it is instant.</div>`;
  const poll = async () => {
    if (state.current !== id) return;
    const d = await api(`/api/replay?id=${id}`);
    if (state.current !== id) return;
    if (d.status === "done") {
      state.data = d;
      state.player = d.you || d.players[0]?.key;
      if (r && !r.analysed) { r.analysed = true; renderList(); }
      renderReplay();
    } else if ((d.status || "").startsWith("error")) {
      view.innerHTML = `<div class="card"><h3>Could not analyse this replay</h3><div class="muted">${esc(d.status)}</div>
        <p><button id="retry">Try again</button></p></div>`;
      $("#retry").onclick = async () => { await api("/api/retry", { id }); openReplay(id); };
    } else {
      pollTimer = setTimeout(poll, 800);
    }
  };
  poll();
}

function playerByKey(k) { return state.data.players.find((p) => p.key === k); }

function renderReplay() {
  const d = state.data, r = state.replays.find((x) => x.id === state.current) || {};
  const metrics = d.metrics;
  const team = (t) => d.players.filter((p) => p.team === t).sort((a, b) => b.score - a.score).map((p) => {
    const m = metrics[p.key] || {};
    return `<div class="p ${p.key === state.player ? "sel" : ""}" data-key="${p.key}">
      <b class="t${p.team}">${esc(p.name)}${p.is_bot ? " 🤖" : ""}${p.key === d.you ? " (you)" : ""}</b>
      <span class="st">${p.score} pts · ${m.goals ?? 0}G ${m.shots ?? 0}Sh ${m.saves ?? 0}Sv ${m.assists ?? 0}A</span></div>`;
  }).join("");
  const tabs = [["review", "Review"], ["coach", "Coach"], ["heat", "Heat maps"], ["shots", "Shots"], ["stats", "Stats"], ["boost", "Boost"]];
  $("#view").innerHTML = `
    <div class="head">
      <div>
        <div class="title-row"><h1>${esc(displayName({ ...d, label: r.label }))}</h1><button id="rename" title="Give this replay a name">✎ Rename</button><button id="reveal">Show file</button><button id="watch-in-game" title="Open this replay in Rocket League's replay viewer and follow along here">▶ Watch in game</button></div>
        <div class="muted">${esc(d.playlist)} · ${d.team_size}v${d.team_size} · ${esc(d.map)} · ${esc(fmtDate(d.date))}${d.overtime ? " · overtime" : ""} · <span class="small">${esc(d.file)}</span></div>
      </div>
    </div>
    <div class="scoreboard">
      <div class="team">${team(0)}</div>
      <div class="big"><span class="t0">${d.score[0]}</span> – <span class="t1">${d.score[1]}</span></div>
      <div class="team right">${team(1)}</div>
    </div>
    <div id="watch-bar"></div>
    <div class="muted small">Click a player above to see their coaching, heat map and shots.</div>
    <nav class="tabs">${tabs.map(([k, l]) => `<button data-tab="${k}" class="${state.tab === k ? "on" : ""}">${l}</button>`).join("")}</nav>
    <section id="tab"></section>`;
  $("#view").querySelectorAll(".team .p").forEach((el) => el.onclick = () => { state.player = el.dataset.key; renderReplay(); });
  $("#view").querySelectorAll(".tabs button").forEach((el) => el.onclick = () => { state.tab = el.dataset.tab; renderReplay(); });
  $("#rename").onclick = async () => {
    const label = prompt("Name this replay (leave empty to reset):", r.label || d.title || "");
    if (label === null) return;
    await api("/api/label", { id: state.current, label });
    r.label = label.trim(); renderList(); renderReplay();
  };
  $("#reveal").onclick = () => api("/api/reveal", { id: state.current });
  $("#watch-in-game").onclick = watchInGame;
  ({ review: renderReview, coach: renderCoach, heat: renderHeat, shots: renderShots, stats: renderStats, boost: renderBoost })[state.tab]();
}

// ------------------------------------------------------------------ review tab
// Top-down field for one moment, your goal at the bottom. Arrows show where each car was heading.
function momentSvg(snap, team) {
  if (!snap || !snap.ball) return "";
  const f = team === 0 ? 1 : -1;
  const X = (x) => x * f, Y = (y) => -y * f;
  const { x: W, y: H, goalW: GW, goalD: GD, corner: C } = FIELD;
  const outline = `M ${-W + C} ${-H} L ${W - C} ${-H} L ${W} ${-H + C} L ${W} ${H - C} L ${W - C} ${H} L ${-W + C} ${H} L ${-W} ${H - C} L ${-W} ${-H + C} Z`;
  const arrow = (x, y, vx, vy, color, k = 0.55) => {
    const sp = Math.hypot(vx, vy); if (sp < 250) return "";
    return `<line x1="${X(x)}" y1="${Y(y)}" x2="${X(x + vx * k)}" y2="${Y(y + vy * k)}" stroke="${color}" stroke-width="90" stroke-linecap="round" opacity=".8" marker-end="url(#ah)"/>`;
  };
  const cars = snap.players.filter((p) => p.alive).map((p) => {
    const c = TEAM_COLOR[p.team];
    return arrow(p.x, p.y, p.vx, p.vy, c) +
      `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.you ? 330 : 260}" fill="${c}" stroke="${p.you ? "#fff" : "#0008"}" stroke-width="${p.you ? 120 : 50}"/>` +
      (p.you ? `<text x="${X(p.x)}" y="${Y(p.y) - 470}" text-anchor="middle">you</text>` : "");
  }).join("");
  const b = snap.ball;
  const sug = snap.suggest ? (() => {
    const me = snap.players.find((p) => p.you);
    const line = me && me.alive ? `<line x1="${X(me.x)}" y1="${Y(me.y)}" x2="${X(snap.suggest.x)}" y2="${Y(snap.suggest.y)}" stroke="#3fb950" stroke-width="90" stroke-dasharray="200 140"/>` : "";
    return line + `<circle cx="${X(snap.suggest.x)}" cy="${Y(snap.suggest.y)}" r="420" fill="none" stroke="#3fb950" stroke-width="110" stroke-dasharray="180 110"/>
      <text class="sug" x="${X(snap.suggest.x)}" y="${Y(snap.suggest.y) - 560}" text-anchor="middle">${esc(snap.suggest.label)}</text>`;
  })() : "";
  return `<svg class="moment-svg" viewBox="${-W - 250} ${-H - GD - 150} ${2 * W + 500} ${2 * H + 2 * GD + 300}" role="img" aria-label="Positions at this moment">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#cbd5e1"/></marker></defs>
    <path d="${outline}" fill="var(--field)" stroke="var(--field-line)" stroke-width="40"/>
    <line x1="${-W}" y1="0" x2="${W}" y2="0" stroke="var(--field-line)" stroke-width="30"/>
    <circle cx="0" cy="0" r="900" fill="none" stroke="var(--field-line)" stroke-width="30"/>
    <rect x="${-GW}" y="${H}" width="${2 * GW}" height="${GD}" fill="${TEAM_COLOR[team]}55" stroke="var(--field-line)" stroke-width="30"/>
    <rect x="${-GW}" y="${-H - GD}" width="${2 * GW}" height="${GD}" fill="${TEAM_COLOR[1 - team]}55" stroke="var(--field-line)" stroke-width="30"/>
    ${sug}${cars}
    ${arrow(b.x, b.y, b.vx, b.vy, "#e5e7eb", 0.45)}
    <circle cx="${X(b.x)}" cy="${Y(b.y)}" r="${200 + Math.min(200, b.z / 8)}" fill="#e5e7eb" stroke="#111" stroke-width="50"/>
  </svg>`;
}

const VERDICT = { mistake: ["✕", "bad"], ok: ["✓", "ok"], good: ["★", "good"] };
function renderReview() {
  const d = state.data, p = playerByKey(state.player);
  const rv = (d.review || {})[state.player];
  if (!rv) {
    $("#tab").innerHTML = `<div class="card">${d.review ? "No review for this player (bots aren't reviewed)." : "Open this replay again to build the review (it needs a fresh analysis)."}</div>`;
    return;
  }
  const top = rv.habits[0];
  const head = `<div class="card focus">
    <h3>${esc(p.name)}: ${rv.goals_against ? `${rv.goals_on_you} of ${rv.goals_against} goals against came from your decisions` : "no goals against"}</h3>
    ${top ? `<div>Biggest thing to change: <b>${esc(top.label)}</b> (${top.count}×${top.goals ? `, cost ${top.goals} goal${top.goals > 1 ? "s" : ""}` : ""}).${top.should ? ` ${esc(top.should)}` : ""}</div>` : `<div>No decision mistakes stood out this game.</div>`}
    ${rv.habits.length > 1 ? `<div style="margin-top:6px">${rv.habits.slice(1).map((h) => `<span class="chip">${esc(h.label)} ${h.count}×${h.goals ? ` · ${h.goals} goal${h.goals > 1 ? "s" : ""}` : ""}</span>`).join("")}</div>` : ""}
    <div class="muted small" style="margin-top:6px">Every goal against is traced back to where the attack started; the biggest mistake on your team is the root cause. Click a clock to jump the in-game replay there.</div></div>`;
  const cards = rv.decisions.map((x) => {
    const [ic, cls] = VERDICT[x.verdict] || ["•", ""];
    const chain = x.steps && x.steps.length > 1 ? `<details><summary class="small">Everything you did in this attack (${x.steps.length})</summary>
      ${x.steps.map((s) => `<div class="moment seekable" data-time="${s.time}"><span class="clock">${esc(s.clock)}</span><span>${esc(s.happened)}${s.should ? `<br><span class="muted small">${esc(s.should)}</span>` : ""}</span></div>`).join("")}</details>` : "";
    return `<div class="card decision ${cls}">
      <div class="dec-grid">
        <div>${momentSvg(x.snapshot, p.team)}<div class="muted small" style="text-align:center">Your goal at the bottom</div></div>
        <div>
          <div class="dec-head"><span class="verdict ${cls}">${ic}</span><b>${esc(x.title)}</b>
            <span class="clock seekable" data-time="${x.time}" title="Jump the in-game replay here">${esc(x.clock)}</span><span class="chip">${esc(x.context)}</span></div>
          <p>${esc(x.happened)}</p>
          ${x.should ? `<p class="should"><b>${x.verdict === "mistake" ? "What to do instead" : "Next time"}:</b> ${esc(x.should)}</p>` : ""}
          ${x.why ? `<p class="muted small">${esc(x.why)}</p>` : ""}
          ${chain}
        </div>
      </div></div>`;
  }).join("");
  const filters = `<div class="seg" id="rv-filter" style="margin-bottom:10px">${[["all", "Everything"], ["mistake", "Mistakes"], ["goals", "Goals against"], ["good", "Good plays"]].map(([k, l]) => `<button data-k="${k}" class="${(state.rvFilter || "all") === k ? "on" : ""}">${l}</button>`).join("")}</div>`;
  $("#tab").innerHTML = head + filters + `<div id="rv-cards">${cards || `<div class="card">Nothing to review for this player.</div>`}</div>`;
  const apply = () => {
    const k = state.rvFilter || "all";
    $("#rv-cards").querySelectorAll(".decision").forEach((el, i) => {
      const x = rv.decisions[i];
      el.hidden = !(k === "all" || (k === "mistake" && x.verdict === "mistake") || (k === "good" && x.verdict === "good") || (k === "goals" && x.context.startsWith("Goal against")));
    });
  };
  $("#rv-filter").querySelectorAll("button").forEach((b) => b.onclick = () => { state.rvFilter = b.dataset.k; renderReview(); });
  apply();
}

// ------------------------------------------------------------------ coach tab
const ICON = { conceded: "⚠", whiff: "✕", beaten: "⏱", fifty: "⚔", goal: "★" };
function renderCoach() {
  const d = state.data, p = playerByKey(state.player), rep = d.reports[state.player];
  if (!rep) { $("#tab").innerHTML = `<div class="card">No coaching for this player.</div>`; return; }
  const focus = rep.findings.slice(0, 3).map((f, i) => `
    <div class="card focus"><h3><span class="n">${i + 1}</span>${esc(f.title)}<span class="chip">${esc(f.area)}</span></h3>
      <div><b>${esc(f.evidence)}</b></div><div class="muted">${esc(f.why)}</div>
      <ul class="tight">${f.fix.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")
    || `<div class="card">Nothing stood out as a clear weakness this game.</div>`;
  const more = rep.findings.slice(3);
  $("#tab").innerHTML = `
    <h2 style="margin:0 0 10px">${esc(p.name)}: focus on these</h2>${focus}
    ${more.length ? `<details class="card"><summary>${more.length} smaller things</summary><ul class="tight">${more.map((f) => `<li><b>${esc(f.title)}</b>: ${esc(f.evidence)} ${esc(f.fix[0])}</li>`).join("")}</ul></details>` : ""}
    <div class="grid2">
      <div class="card"><h3>What went well</h3>${rep.strengths.length ? `<ul class="tight">${rep.strengths.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : `<div class="muted">No stat clearly above the rest of the lobby this game.</div>`}</div>
      <div class="card"><h3>Moments to rewatch</h3><div class="muted small">In-game clock. Watching in game (▶ Watch in game)? Click a moment to jump the replay there.</div>
        ${rep.moments.map((m) => `<div class="moment seekable ${m.good ? "good" : ""}" data-time="${m.time}" title="Click to jump the in-game replay here"><span class="ic">${ICON[m.kind] || "•"}</span><span class="clock">${esc(m.clock)}</span><span>${esc(m.text)}</span></div>`).join("") || `<div class="muted">Nothing flagged.</div>`}
      </div>
    </div>`;
}

// ------------------------------------------------------------------ field drawing
// Draws the pitch with the viewer's own goal at the bottom. Returns a projector (x, y) -> canvas px.
function drawField(canvas, flip) {
  const W = canvas.width, H = canvas.height, ctx = canvas.getContext("2d");
  const pad = 14, sy = (H - 2 * pad) / (2 * (FIELD.y + FIELD.goalD)), sx = (W - 2 * pad) / (2 * FIELD.x), s = Math.min(sx, sy);
  const P = (x, y) => { if (flip) { x = -x; y = -y; } return [W / 2 + x * s, H / 2 - y * s]; };
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "#0f2a1c"; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 2;
  const c = FIELD.corner, X = FIELD.x, Y = FIELD.y;
  const outline = [[-X + c, Y], [X - c, Y], [X, Y - c], [X, -Y + c], [X - c, -Y], [-X + c, -Y], [-X, -Y + c], [-X, Y - c]];
  ctx.beginPath(); outline.forEach(([x, y], i) => { const [a, b] = P(x, y); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); }); ctx.closePath();
  ctx.fillStyle = "#123522"; ctx.fill(); ctx.stroke();
  const clipToField = () => { ctx.beginPath(); outline.forEach(([x, y], i) => { const [a, b] = P(x, y); i ? ctx.lineTo(a, b) : ctx.moveTo(a, b); }); ctx.closePath();
    for (const gy of [-Y, Y]) { const [x1, y1] = P(-FIELD.goalW, gy), [x2, y2] = P(FIELD.goalW, gy + Math.sign(gy) * FIELD.goalD); ctx.rect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1)); }
    ctx.clip("nonzero"); };
  // halfway line, centre circle, thirds
  ctx.beginPath(); let [a, b] = P(-X, 0); ctx.moveTo(a, b); [a, b] = P(X, 0); ctx.lineTo(a, b); ctx.stroke();
  ctx.beginPath(); [a, b] = P(0, 0); ctx.arc(a, b, 1000 * s, 0, Math.PI * 2); ctx.stroke();
  ctx.setLineDash([6, 6]); ctx.strokeStyle = "rgba(255,255,255,.15)";
  for (const ty of [-Y / 3, Y / 3]) { ctx.beginPath(); [a, b] = P(-X, ty); ctx.moveTo(a, b); [a, b] = P(X, ty); ctx.lineTo(a, b); ctx.stroke(); }
  ctx.setLineDash([]);
  // goals: the blue goal is at -y. Colour each by its team.
  for (const [gy, col] of [[-Y, TEAM_COLOR[0]], [Y, TEAM_COLOR[1]]]) {
    const d = gy < 0 ? -FIELD.goalD : FIELD.goalD;
    const [x1, y1] = P(-FIELD.goalW, gy), [x2, y2] = P(FIELD.goalW, gy + d);
    ctx.fillStyle = col + "55"; ctx.strokeStyle = col; ctx.lineWidth = 2;
    ctx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
    ctx.strokeRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
  }
  return { P, s, ctx, clipToField };
}

function heatColor(t) {
  // transparent -> blue -> cyan -> yellow -> red
  const stops = [[0, [30, 60, 255, 0]], [0.2, [30, 90, 255, 150]], [0.45, [0, 220, 220, 190]], [0.7, [250, 230, 40, 215]], [1, [255, 40, 30, 235]]];
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i], k = (t - t0) / (t1 - t0);
      return c0.map((v, j) => Math.round(v + (c1[j] - v) * k));
    }
  }
  return stops[stops.length - 1][1];
}

function drawHeat(canvas, points, flip) {
  const { P, ctx, clipToField } = drawField(canvas, flip);
  if (!points.length) return;
  const W = canvas.width, H = canvas.height, cell = 6;
  const gw = Math.ceil(W / cell), gh = Math.ceil(H / cell), grid = new Float32Array(gw * gh);
  for (const [x, y] of points) {
    const [px, py] = P(x, y);
    const gx = Math.floor(px / cell), gy = Math.floor(py / cell);
    if (gx >= 0 && gy >= 0 && gx < gw && gy < gh) grid[gy * gw + gx] += 1;
  }
  // separable gaussian blur
  const r = 4, k = []; let ks = 0;
  for (let i = -r; i <= r; i++) { const v = Math.exp(-(i * i) / (2 * 2.2 * 2.2)); k.push(v); ks += v; }
  const tmp = new Float32Array(gw * gh), out = new Float32Array(gw * gh);
  for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) { let s = 0; for (let i = -r; i <= r; i++) { const xx = x + i; if (xx >= 0 && xx < gw) s += grid[y * gw + xx] * k[i + r]; } tmp[y * gw + x] = s / ks; }
  for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) { let s = 0; for (let i = -r; i <= r; i++) { const yy = y + i; if (yy >= 0 && yy < gh) s += tmp[yy * gw + x] * k[i + r]; } out[y * gw + x] = s / ks; }
  let max = 0; for (const v of out) max = Math.max(max, v);
  const img = ctx.createImageData(gw, gh);
  for (let i = 0; i < out.length; i++) {
    const t = Math.pow(out[i] / max, 0.6);  // lift quieter areas a little so they still show
    const [R, G, B, A] = t < 0.1 ? [0, 0, 0, 0] : heatColor(t);
    img.data.set([R, G, B, A], i * 4);
  }
  const off = document.createElement("canvas"); off.width = gw; off.height = gh; off.getContext("2d").putImageData(img, 0, 0);
  ctx.save(); clipToField(); ctx.imageSmoothingEnabled = true; ctx.globalAlpha = 0.85; ctx.drawImage(off, 0, 0, gw * cell, gh * cell); ctx.restore();
}

function zoneShares(points, team) {
  // fractions of time in own/neutral/opponent third and own/opponent half for a team
  const dir = team === 0 ? 1 : -1, third = FIELD.y / 3;
  let own = 0, mid = 0, opp = 0, ownHalf = 0;
  for (const [, y] of points) {
    const yy = y * dir;
    if (yy < -third) own++; else if (yy > third) opp++; else mid++;
    if (yy < 0) ownHalf++;
  }
  const n = points.length || 1;
  return { own: own / n, mid: mid / n, opp: opp / n, ownHalf: ownHalf / n };
}

function bars(rows) {
  return `<div class="bars">${rows.map(([l, v]) => `<div class="bar"><span>${esc(l)}</span><div class="track"><div class="fill" style="width:${(v * 100).toFixed(0)}%"></div></div><span>${(v * 100).toFixed(0)}%</span></div>`).join("")}</div>`;
}

// ------------------------------------------------------------------ heat map tab
function renderHeat() {
  const d = state.data, p = playerByKey(state.player), hs = state.heat;
  const viewTeam = p.team, flip = viewTeam === 1;
  let pts, label, team = viewTeam;
  if (hs.subject === "ball") { pts = d.tracks.ball; label = "Ball"; }
  else if (hs.subject === "team") { pts = d.players.filter((q) => q.team === viewTeam).flatMap((q) => d.tracks.players[q.key] || []); label = `${viewTeam ? "Orange" : "Blue"} team`; }
  else if (hs.subject === "opponents") { pts = d.players.filter((q) => q.team !== viewTeam).flatMap((q) => d.tracks.players[q.key] || []); label = "Opponents"; }
  else { pts = d.tracks.players[p.key] || []; label = p.name; }
  const z = zoneShares(pts, team);
  $("#tab").innerHTML = `
    <div class="field-wrap">
      <canvas id="field" width="520" height="700"></canvas>
      <div class="controls">
        <div class="card"><h3>Show</h3>
          <div class="seg" id="heat-subject">
            ${[["player", p.name], ["team", "My team"], ["opponents", "Opponents"], ["ball", "Ball"]].map(([k, l]) => `<button data-k="${k}" class="${hs.subject === k ? "on" : ""}">${esc(l)}</button>`).join("")}
          </div>
          <div class="muted small" style="margin-top:8px">${esc(p.name)}'s goal is at the bottom. Hotter colours mean more time spent there.</div>
        </div>
        <div class="card"><h3>${esc(label)}: where the time went</h3>
          ${bars([["Defensive third", z.own], ["Middle third", z.mid], ["Attacking third", z.opp]])}
          <div style="margin-top:10px">${bars([["Own half", z.ownHalf], ["Their half", 1 - z.ownHalf]])}</div>
        </div>
        ${hs.subject === "player" ? positionCard(p) : ""}
      </div>
    </div>`;
  $("#heat-subject").querySelectorAll("button").forEach((b) => b.onclick = () => { hs.subject = b.dataset.k; renderHeat(); });
  drawHeat($("#field"), pts, flip);
}

function positionCard(p) {
  const m = state.data.metrics[p.key] || {}, defs = Object.fromEntries(state.data.metric_defs.map((x) => [x.key, x]));
  const rows = ["behind_ball_pct", "most_back_pct", "first_man_pct", "third_man_pct", "avg_speed_kmh", "supersonic_pct", "high_air_pct"]
    .filter((k) => m[k] !== null && m[k] !== undefined)
    .map((k) => `<tr><td>${esc(defs[k].label)}</td><td>${fmtVal(m[k], defs[k])}</td></tr>`).join("");
  return `<div class="card"><h3>Positioning</h3><table>${rows}</table></div>`;
}

// ------------------------------------------------------------------ shots tab
function renderShots() {
  const d = state.data, p = playerByKey(state.player), flip = p.team === 1, who = state.shots.who;
  const shots = d.shots.filter((s) => who === "all" ? true : who === "team" ? s.team === p.team : who === "opp" ? s.team !== p.team : s.player === p.key);
  const counts = { goal: 0, saved: 0, missed: 0 }; shots.forEach((s) => counts[s.outcome]++);
  const tbl = d.players.slice().sort((a, b) => a.team - b.team || b.score - a.score).map((q) => {
    const m = d.metrics[q.key] || {};
    const pct = m.shots ? Math.round(100 * m.goals / m.shots) + "%" : "–";
    return `<tr class="${q.key === p.key ? "me" : ""}"><td class="t${q.team}">${esc(q.name)}</td><td>${m.goals ?? 0}</td><td>${m.shots ?? 0}</td><td>${pct}</td><td>${m.saves ?? 0}</td><td>${m.assists ?? 0}</td></tr>`;
  }).join("");
  const teamTot = [0, 1].map((t) => {
    const ps = d.players.filter((q) => q.team === t), g = ps.reduce((a, q) => a + (d.metrics[q.key]?.goals || 0), 0), s = ps.reduce((a, q) => a + (d.metrics[q.key]?.shots || 0), 0);
    return `<tr><td class="t${t}"><b>${t ? "Orange" : "Blue"} team</b></td><td><b>${g}</b></td><td><b>${s}</b></td><td><b>${s ? Math.round(100 * g / s) + "%" : "–"}</b></td><td></td><td></td></tr>`;
  }).join("");
  $("#tab").innerHTML = `
    <div class="field-wrap">
      <canvas id="field" width="520" height="700"></canvas>
      <div class="controls">
        <div class="card"><h3>Shots shown</h3>
          <div class="seg" id="shot-who">${[["player", p.name], ["team", "My team"], ["opp", "Opponents"], ["all", "Everyone"]].map(([k, l]) => `<button data-k="${k}" class="${who === k ? "on" : ""}">${esc(l)}</button>`).join("")}</div>
          <div class="legend" style="margin-top:10px"><span style="--c:#3fb950">Goal (${counts.goal})</span><span style="--c:#e3b341">Saved (${counts.saved})</span><span style="--c:#8b98a7">Missed / blocked (${counts.missed})</span></div>
          <div class="muted small" style="margin-top:6px">Dots mark where the ball was when the shot was hit. Hover for details.</div>
        </div>
        <div class="card"><h3>Shooting %</h3><div class="scroll"><table><tr><th>Player</th><th>Goals</th><th>Shots</th><th>Shot %</th><th>Saves</th><th>Assists</th></tr>${tbl}${teamTot}</table></div>
          <div class="muted small" style="margin-top:6px">Goals and shots are the official in-game counts.</div></div>
        <div class="card"><h3>Shot list</h3>${shots.map((s) => `<div class="moment seekable" data-time="${s.time}" title="Click to jump the in-game replay here"><span class="clock">${esc(s.clock)}</span><span class="t${s.team}">${esc(playerByKey(s.player)?.name || "?")}</span><span class="muted">${s.outcome}${s.z > 300 ? " · aerial" : ""}</span></div>`).join("") || `<div class="muted">No shots.</div>`}</div>
      </div>
    </div>`;
  $("#shot-who").querySelectorAll("button").forEach((b) => b.onclick = () => { state.shots.who = b.dataset.k; renderShots(); });
  const canvas = $("#field"), { P, ctx } = drawField(canvas, flip);
  const col = { goal: "#3fb950", saved: "#e3b341", missed: "#8b98a7" };
  const placed = [];
  for (const s of shots) {
    const [x, y] = P(s.x, s.y);
    placed.push({ x, y, s });
    ctx.beginPath(); ctx.arc(x, y, s.outcome === "goal" ? 9 : 7, 0, Math.PI * 2);
    ctx.fillStyle = col[s.outcome]; ctx.globalAlpha = 0.9; ctx.fill(); ctx.globalAlpha = 1;
    ctx.lineWidth = 2.5; ctx.strokeStyle = TEAM_COLOR[s.team]; ctx.stroke();
  }
  canvas.title = "";
  canvas.onmousemove = (e) => {
    const r = canvas.getBoundingClientRect(), mx = (e.clientX - r.left) * canvas.width / r.width, my = (e.clientY - r.top) * canvas.height / r.height;
    const hit = placed.find((q) => Math.hypot(q.x - mx, q.y - my) < 10);
    canvas.title = hit ? `${playerByKey(hit.s.player)?.name} at ${hit.s.clock}: ${hit.s.outcome}${hit.s.z > 300 ? " (aerial)" : ""}` : "";
  };
}

// ------------------------------------------------------------------ stats tab
function renderStats() {
  const d = state.data;
  const ps = d.players.slice().sort((a, b) => a.team - b.team || b.score - a.score);
  let group = null, rows = "";
  for (const def of d.metric_defs) {
    const vals = ps.map((p) => d.metrics[p.key]?.[def.key]);
    if (vals.every((v) => v === null || v === undefined)) continue;
    if (def.group !== group) { group = def.group; rows += `<tr class="group"><td colspan="${ps.length + 1}">${esc(group)}</td></tr>`; }
    const nums = vals.filter((v) => v !== null && v !== undefined);
    const best = def.better === "high" ? Math.max(...nums) : def.better === "low" ? Math.min(...nums) : null;
    rows += `<tr><td>${esc(def.label)}</td>${vals.map((v, i) => `<td class="${ps[i].key === state.player ? "me" : ""} ${best !== null && v === best && nums.length > 1 ? "best" : ""}">${fmtVal(v, def)}</td>`).join("")}</tr>`;
  }
  $("#tab").innerHTML = `<div class="card scroll"><table>
    <tr><th></th>${ps.map((p) => `<th class="t${p.team}">${esc(p.name)}${p.is_bot ? " 🤖" : ""}</th>`).join("")}</tr>${rows}</table>
    <div class="muted small" style="margin-top:8px">Green marks the best value in the lobby where higher or lower is clearly better.</div></div>`;
}

// ------------------------------------------------------------------ boost tab
function lineChart({ series, xs, yMax, marks = [], height = 260, yLabel = "" }) {
  const W = 900, H = height, l = 40, r = 10, t = 10, b = 26;
  const x0 = xs[0], x1 = xs[xs.length - 1] || 1;
  const X = (x) => l + (x - x0) / (x1 - x0 || 1) * (W - l - r), Y = (y) => t + (1 - y / yMax) * (H - t - b);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px">`;
  for (const g of [0, 0.25, 0.5, 0.75, 1]) svg += `<line x1="${l}" x2="${W - r}" y1="${Y(g * yMax)}" y2="${Y(g * yMax)}" stroke="#263140"/><text x="${l - 6}" y="${Y(g * yMax) + 4}" text-anchor="end">${Math.round(g * yMax)}</text>`;
  for (const m of marks) svg += `<line x1="${X(m.x)}" x2="${X(m.x)}" y1="${t}" y2="${H - b}" stroke="${m.color}" stroke-dasharray="4 4"/><text x="${X(m.x) + 3}" y="${t + 10}" style="fill:${m.color}">${esc(m.label)}</text>`;
  for (const s of series) {
    const pts = s.ys.map((y, i) => `${X(xs[i]).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");
    svg += `<polyline points="${pts}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.6}" opacity="${s.opacity || 1}"/>`;
  }
  svg += `<text x="${l}" y="${H - 6}">${esc(yLabel)}</text></svg>`;
  return svg;
}

function renderBoost() {
  const d = state.data, p = playerByKey(state.player), xs = d.tracks.boost_time;
  const ys = d.tracks.boost[p.key] || [];
  const marks = d.goals.map((g) => ({ x: g.time, color: TEAM_COLOR[g.team], label: `⚽ ${g.clock}` }));
  const m = d.metrics[p.key] || {}, defs = Object.fromEntries(d.metric_defs.map((x) => [x.key, x]));
  const rows = d.metric_defs.filter((x) => x.group === "Boost").map((def) => `<tr><td>${esc(def.label)}</td>${d.players.map((q) => `<td class="${q.key === p.key ? "me" : ""}">${fmtVal(d.metrics[q.key]?.[def.key], def)}</td>`).join("")}</tr>`).join("");
  $("#tab").innerHTML = `
    <div class="card"><h3>${esc(p.name)}'s boost over the match</h3>
      ${lineChart({ series: [{ ys, color: TEAM_COLOR[p.team], width: 1.8 }], xs, yMax: 100, marks, yLabel: "boost (0–100), dashed lines are goals" })}
      <div class="muted small">Average ${fmtVal(m.avg_boost, defs.avg_boost)}, at 0 boost ${fmtVal(m.zero_boost_pct, defs.zero_boost_pct)} of the time.</div></div>
    <div class="card scroll"><h3>Boost stats</h3><table><tr><th></th>${d.players.map((q) => `<th class="t${q.team}">${esc(q.name)}</th>`).join("")}</tr>${rows}</table></div>`;
}

// ------------------------------------------------------------------ trends
async function renderTrends() {
  state.liveView = false;
  state.current = null; renderList();
  $("#empty").hidden = true; const view = $("#view"); view.hidden = false;
  view.innerHTML = `<div class="loading"><div class="spinner"></div>Loading your games…</div>`;
  const t = await api("/api/trends");
  const games = t.games;
  if (!t.you) { view.innerHTML = `<div class="card"><h3>Who are you?</h3><p>Set your in-game name in Settings so trends can find you in each replay.</p></div>`; return; }
  const header = `<h1 style="margin:0">${esc(t.you)}: trends</h1><div class="muted">${games.length} analysed games with you in them (${t.total} replays in your library${t.unanalysed ? `, ${t.unanalysed} not analysed yet` : ""}).
     ${t.unanalysed ? `<button id="trend-analyse" style="margin-left:8px">Analyse the rest</button>` : ""}</div>`;
  if (games.length < 2) {
    view.innerHTML = header + `<div class="card" style="margin-top:12px">Open a few replays or press <b>Analyse the rest</b> to build up your trends.</div>`;
    $("#trend-analyse")?.addEventListener("click", analyseAll); return;
  }
  const wins = games.filter((g) => g.won).length;
  const counts = {}, titles = {};
  games.forEach((g) => g.findings.forEach((k) => { counts[k] = (counts[k] || 0) + 1; titles[k] = g.finding_titles[k]; }));
  const recurring = Object.entries(counts).filter(([k]) => !k.startsWith("gap_")).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const hab = {};
  let ga = 0, onYou = 0, reviewed = 0;
  games.forEach((g) => {
    if (g.goals_against === null || g.goals_against === undefined) return;
    reviewed++; ga += g.goals_against; onYou += g.goals_on_you || 0;
    (g.habits || []).forEach((h) => {
      const x = hab[h.kind] = hab[h.kind] || { label: h.label, count: 0, goals: 0, games: 0, should: h.should };
      x.count += h.count; x.goals += h.goals; x.games++;
    });
  });
  const habits = Object.values(hab).sort((a, b) => b.goals - a.goals || b.count - a.count).slice(0, 6);
  const habitCard = reviewed ? `<div class="card"><h3>Your decision habits</h3>
      <div class="muted small">From the play-by-play review of ${reviewed} games: ${onYou} of ${ga} goals against came from your decisions.</div>
      ${habits.length ? habits.map((h, i) => `<div class="habit"><span class="n">${i + 1}</span><span>${esc(h.label)}${i === 0 && h.should ? `<br><span class="muted small">${esc(h.should)}</span>` : ""}</span><b>${h.count}× in ${h.games} game${h.games > 1 ? "s" : ""}${h.goals ? ` · cost ${h.goals} goal${h.goals > 1 ? "s" : ""}` : ""}</b></div>`).join("")
        : `<div class="muted">No repeated decision mistakes.</div>`}</div>` : "";
  const defs = (await api(`/api/replay?id=${games[games.length - 1].id}`)).metric_defs || [];
  const def = defs.find((x) => x.key === state.trendMetric) || defs[0];
  const ys = games.map((g) => g.metrics[def.key] ?? 0);
  const yMax = Math.max(1, ...ys) * 1.15;
  const avg = (arr) => arr.reduce((a, b) => a + b, 0) / (arr.length || 1);
  const half = Math.floor(games.length / 2), early = avg(ys.slice(0, half)), late = avg(ys.slice(half));
  const better = def.better === "high" ? late > early : def.better === "low" ? late < early : null;
  // rolling average over 5 games
  const roll = ys.map((_, i) => avg(ys.slice(Math.max(0, i - 4), i + 1)));
  const xs = games.map((_, i) => i);
  let chart = lineChart({ series: [{ ys, color: "#4f9dff", opacity: 0.45 }, { ys: roll, color: "#4f9dff", width: 3 }], xs, yMax, yLabel: `${def.label} per game (thick line: 5-game average)` });
  view.innerHTML = header + `
    <div class="grid2" style="margin-top:12px">
      <div class="card"><h3>Record</h3><div style="font-size:28px;font-weight:800">${wins}W – ${games.length - wins}L</div><div class="muted">${Math.round(100 * wins / games.length)}% win rate</div></div>
      <div class="card"><h3>What keeps coming up</h3>${recurring.length ? recurring.map(([k, n]) => `<div class="bar" style="display:flex;justify-content:space-between"><span>${esc(titles[k])}</span><b>${n} / ${games.length}</b></div>`).join("") : `<div class="muted">Nothing repeats. Nice.</div>`}</div>
    </div>
    ${habitCard}
    <div class="card"><h3>Stat over time</h3>
      <select id="trend-metric">${defs.map((x) => `<option value="${x.key}" ${x.key === def.key ? "selected" : ""}>${esc(x.group)}: ${esc(x.label)}</option>`).join("")}</select>
      <span class="muted" style="margin-left:10px">First half of your games ${fmtVal(early, def)} → recent half ${fmtVal(late, def)}
        ${better === null ? "" : better ? `<b style="color:var(--good)">improving</b>` : `<b style="color:var(--bad)">getting worse</b>`}</span>
      <div style="margin-top:8px">${chart}</div></div>
    <div class="card scroll"><h3>Game by game</h3><table><tr><th>Game</th><th>Result</th>${["avg_boost", "zero_boost_pct", "avg_speed_kmh", "behind_ball_pct", "touches_p5", "whiffs_p5", "shooting_pct", "goals", "saves"].map((k) => `<th>${esc(defs.find((x) => x.key === k)?.label || k)}</th>`).join("")}</tr>
      ${games.slice().reverse().map((g) => `<tr data-id="${g.id}" style="cursor:pointer"><td>${esc(fmtDate(g.date))} · ${esc(g.playlist)}</td><td style="color:var(${g.won ? "--good" : "--bad"})">${g.won ? "Win" : "Loss"}</td>${["avg_boost", "zero_boost_pct", "avg_speed_kmh", "behind_ball_pct", "touches_p5", "whiffs_p5", "shooting_pct", "goals", "saves"].map((k) => `<td>${fmtVal(g.metrics[k], defs.find((x) => x.key === k) || { fmt: ".0f" })}</td>`).join("")}</tr>`).join("")}
    </table></div>`;
  $("#trend-metric").onchange = (e) => { state.trendMetric = e.target.value; renderTrends(); };
  $("#trend-analyse")?.addEventListener("click", analyseAll);
  view.querySelectorAll("tr[data-id]").forEach((tr) => tr.onclick = () => openReplay(tr.dataset.id));
}

// ------------------------------------------------------------------ settings & actions
async function analyseAll() {
  const r = await api("/api/analyse-all", {});
  toast(r.queued ? `Analysing ${r.queued} replays in the background…` : "Everything is already analysed.");
}

function renderSettings() {
  $("#set-player").value = "";
  $("#set-you").textContent = state.you || "unknown";
  $("#set-folders").innerHTML = state.folders.map((f, i) => `<li><span>${esc(f)}</span><button type="button" data-i="${i}">Remove</button></li>`).join("") || `<li class="muted">No folders yet.</li>`;
  $("#set-folders").querySelectorAll("button").forEach((b) => b.onclick = async () => {
    const folders = state.folders.filter((_, i) => i !== +b.dataset.i);
    const r = await api("/api/settings", { folders }); state.folders = r.folders; renderSettings(); loadList();
  });
}

$("#btn-settings").onclick = async () => {
  renderSettings();
  const u = await api("/api/update");
  $("#set-auto-update").checked = u.auto;
  $("#set-version").textContent = u.current + (u.latest && u.latest !== "v" + u.current ? ` (latest ${u.latest})` : "");
  $("#settings").showModal();
  const sc = await api("/api/shortcut");
  $("#set-shortcut-row").hidden = !sc.supported;
  $("#set-shortcut").checked = sc.exists;
};
$("#set-shortcut").onchange = async (e) => {
  const r = await api("/api/shortcut", { create: e.target.checked });
  if (!r.ok) toast(r.error, 4000);
  else toast(r.exists ? "Added RL Coach to your desktop." : "Removed the desktop shortcut.");
  e.target.checked = r.exists;
  $("#shortcut-bar")?.remove();
};
$("#set-auto-update").onchange = (e) => api("/api/settings", { auto_update: e.target.checked });
$("#check-now").onclick = async (e) => {
  e.preventDefault();
  await api("/api/update/check", {});
  toast("Checking for updates…");
  updateDismissed = false;
  setTimeout(pollUpdate, 3000);
};
$("#settings").addEventListener("close", async () => {
  const name = $("#set-player").value.trim();
  if (name) { await api("/api/settings", { player: name }); }
  loadList();
});
async function addFolder() {
  const r = await api("/api/pick-folder", {});
  if (r.folder) { state.folders = r.folders; renderSettings(); loadList(); }
}
$("#btn-add-folder").onclick = addFolder;
$("#btn-add-folder-empty").onclick = addFolder;
$("#btn-add-folder-text").onclick = async () => {
  const f = $("#set-folder-text").value.trim(); if (!f) return;
  const r = await api("/api/settings", { folders: [...state.folders, f] });
  state.folders = r.folders; $("#set-folder-text").value = ""; renderSettings(); loadList();
};
$("#btn-trends").onclick = renderTrends;
$("#btn-live").onclick = renderLive;
$("#btn-analyse-all").onclick = analyseAll;

// Keep the list fresh (new replays appear after each match) and let the app know the window is open.
let lastCount = -1;
setInterval(async () => {
  const s = await api("/api/status");
  const running = Object.values(s.status).filter((v) => v === "running" || v === "queued").length;
  $("#btn-analyse-all").textContent = running ? `⏳ Analysing… (${running} left)` : "▶ Analyse all";
  if (running !== lastCount) { lastCount = running; loadList(); }
}, 4000);
setInterval(loadList, 30000);

loadList();

// ------------------------------------------------------------------ live: shared
const LIVE = { status: null, lastSaid: new Set(), timer: null };
const MECH = [["", "Everything"], ["half_flip", "Half flip"], ["speed_flip", "Speed flip"], ["wavedash", "Wavedash"], ["zap_dash", "Zap dash"], ["fast_aerial", "Fast aerial"], ["flip_reset", "Flip reset"], ["air_dribble", "Air dribble"], ["ceiling_shot", "Ceiling shot"]];

// replay frame <-> replay time, from the pairs the analysis saved
function frameToTime(d, frame) {
  const ft = d.frame_times || [];
  if (!ft.length || frame === null || frame === undefined) return null;
  let i = ft.findIndex(([f]) => f >= frame);
  if (i === -1) i = ft.length - 1;
  if (i === 0) i = 1;
  const [f0, t0] = ft[i - 1], [f1, t1] = ft[i];
  return t0 + (frame - f0) * (t1 - t0) / ((f1 - f0) || 1);
}
function timeToFrame(d, time) {
  const ft = d.frame_times || [];
  if (ft.length < 2) return null;
  let i = ft.findIndex(([, t]) => t >= time);
  if (i === -1) i = ft.length - 1;
  if (i === 0) i = 1;
  const [f0, t0] = ft[i - 1], [f1, t1] = ft[i];
  return Math.max(0, Math.round(f0 + (time - t0) * (f1 - f0) / ((t1 - t0) || 1)));
}

async function seekTo(time) {
  const d = state.data; if (!d) return;
  const lead = 4;  // start a few seconds early so you see the build-up
  const frame = timeToFrame(d, time - lead);
  const r = await api("/api/live/seek", frame !== null ? { frame } : { time: Math.max(0, time - lead) });
  if (!r.ok) toast("Open this replay in Rocket League first (▶ Watch in game), with the Stats API turned on in Live.", 4000);
}
$("#view").addEventListener("click", (e) => {
  const el = e.target.closest(".seekable");
  if (el && state.data && LIVE.status?.stats?.connected) seekTo(+el.dataset.time);
});

async function watchInGame() {
  const r = await api("/api/live/load-replay", { id: state.current });
  toast(r.ok ? "Opening the replay in Rocket League…" : "Rocket League isn't connected. Open the Live page to turn on the Stats API, then restart the game.", 4500);
}

async function pollLive() {
  clearTimeout(LIVE.timer);
  try { LIVE.status = await api("/api/live"); } catch { LIVE.status = null; }
  if (LIVE.status) {
    if (state.liveView) drawLive();
    else if (state.data) drawWatchBar();
  }
  LIVE.timer = setTimeout(pollLive, state.liveView ? 700 : 1000);
}
pollLive();

// Follow-along bar shown on a replay while you watch the same replay in game.
function drawWatchBar() {
  const bar = $("#watch-bar"); if (!bar) return;
  const st = LIVE.status.stats, d = state.data;
  if (!st.connected || !st.in_replay || st.replay_id !== state.current) { bar.innerHTML = ""; return; }
  const now = frameToTime(d, st.frame) ?? st.elapsed;
  const rep = d.reports[state.player] || { moments: [] };
  const rv = (d.review || {})[state.player];
  const pool = rv ? rv.decisions.filter((x) => x.verdict !== "ok").map((x) => ({ time: x.time, good: x.verdict === "good", text: `${x.title}: ${x.should || x.happened}` })) : rep.moments;
  const upcoming = pool.filter((m) => m.time - now > -1 && m.time - now < 10);
  const shots = d.shots.filter((s) => s.time - now > -1 && s.time - now < 6);
  const say = [...upcoming.map((m) => ({ id: "m" + m.time, at: m.time, text: m.text })),
               ...shots.map((s) => ({ id: "s" + s.time, at: s.time, text: `${playerByKey(s.player)?.name} shoots: ${s.outcome}` }))];
  // Push the next callout to the in-game overlay (if the plugin is loaded) a few seconds ahead.
  for (const c of say) {
    if (c.at - now < 4 && c.at - now > 0 && !LIVE.lastSaid.has(c.id)) {
      LIVE.lastSaid.add(c.id);
      api("/api/live/say", { text: `In ${Math.ceil(c.at - now)}s: ${c.text}` });
    }
  }
  bar.innerHTML = `<div class="card watch"><b>🎬 Watching in game</b> · replay at ${esc(st.clock !== null && st.clock !== undefined ? fmtLen(st.clock) : "")}
    <span class="seg" style="display:inline-flex;margin-left:10px">
      <button data-speed="0.25">¼×</button><button data-speed="0.5">½×</button><button data-speed="1">1×</button><button data-pause="1">⏸</button><button data-pause="0">▶</button>
    </span>
    ${upcoming.length ? upcoming.map((m) => `<div class="moment ${m.good ? "good" : ""}"><span class="clock">in ${Math.max(0, Math.round(m.time - now))}s</span><span>${esc(m.text)}</span></div>`).join("")
      : `<div class="muted small">Nothing flagged in the next 10 seconds. Click any moment or shot below to jump the replay there.</div>`}</div>`;
  bar.querySelectorAll("[data-speed]").forEach((b) => b.onclick = () => api("/api/live/speed", { speed: +b.dataset.speed }));
  bar.querySelectorAll("[data-pause]").forEach((b) => b.onclick = () => api("/api/live/pause", { paused: b.dataset.pause === "1" }));
}

// ------------------------------------------------------------------ live page
function renderLive() {
  state.current = null; state.data = null; state.liveView = true; renderList();
  $("#empty").hidden = true; $("#view").hidden = false;
  $("#view").innerHTML = `<h1 style="margin:0 0 4px">Live coaching</h1>
    <div class="muted">Two ways to get coached while you play. Neither touches online matches.</div>
    <div id="live-body"><div class="loading"><div class="spinner"></div>Checking Rocket League…</div></div>`;
  drawLive();
}

function sparkline(scores) {
  if (!scores.length) return "";
  const W = 160, H = 34, n = scores.length;
  const pts = scores.map((v, i) => `${(n === 1 ? W / 2 : i * W / (n - 1)).toFixed(1)},${(H - 2 - v / 100 * (H - 4)).toFixed(1)}`).join(" ");
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><polyline points="${pts}" fill="none" stroke="#4f9dff" stroke-width="2"/></svg>`;
}

function drawLive() {
  const body = $("#live-body"); if (!body || !LIVE.status) return;
  if (document.activeElement && body.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;
  const { stats: st, plugin: pl } = LIVE.status;
  const enabled = st.settings && st.settings.PacketSendRate > 0;
  const statsState = st.connected ? (st.in_replay ? "🟢 Connected · watching a replay" : "🟢 Connected") : enabled ? "🟡 Turned on · start (or restart) Rocket League" : "⚪ Off";
  const replayName = st.replay_id ? displayName(state.replays.find((r) => r.id === st.replay_id) || {}) : null;

  const plState = pl.connected ? `🟢 Connected · ${esc(pl.mode || "")}` : pl.installed ? "🟡 Installed · start Rocket League without Easy Anti-Cheat" : !pl.bakkesmod ? "⚪ BakkesMod not found" : "⚪ Plugin not installed";
  const sum = pl.summary || {};
  const cards = MECH.slice(1).map(([k, l]) => {
    const s = sum[k];
    return `<div class="card mech ${pl.focus === k ? "focus" : ""}"><h3>${l}</h3>
      ${s ? `<div class="big-num">${s.avg}<span class="muted small"> avg · best ${s.best} · ${s.count} tries</span></div>${sparkline(s.recent)}` : `<div class="muted small">No attempts yet.</div>`}</div>`;
  }).join("");
  const landing = sum.landing ? `<span class="chip">Clean landings: ${sum.landing.avg}%</span>` : "";
  const attempts = (pl.attempts || []).slice(0, 25).map((a) => `
    <div class="moment"><span class="grade g${a.grade}">${a.grade}</span><span style="min-width:96px"><b>${esc(a.label)}</b><br><span class="muted small">${a.score}/100</span></span>
      <span>${esc(a.tip)}<br><span class="muted small">${Object.entries(a.details || {}).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ")}</span></span></div>`).join("");

  $("#live-body").innerHTML = `
  <div class="grid2" style="margin-top:14px">
    <div class="card">
      <h3>🎬 Watch replays together</h3>
      <div><b>${statsState}</b></div>
      <p class="muted small">Uses Rocket League's official Stats API, so it works with Easy Anti-Cheat on or off. Open any replay here and press <b>▶ Watch in game</b>.
        RL Coach follows along, calls out what's coming up, and jumps the in-game replay to any moment or shot you click.</p>
      ${!st.install ? `<div class="row"><input id="rl-path" placeholder="Rocket League folder (the one containing TAGame)"><button id="rl-path-save">Save</button></div>` : `<div class="muted small">Game folder: ${esc(st.install)}</div>`}
      ${st.install && !enabled ? `<p><button class="primary" id="enable-stats">Turn on</button> <span class="muted small">Changes one setting in the game's config file (a backup is kept). Restart Rocket League afterwards.</span></p>` : ""}
      ${st.in_replay ? `<p>${replayName ? `Watching <b>${esc(replayName)}</b>. <button id="open-watching">Open it here</button>` : `<span class="muted">Watching a replay that isn't in your replay folders.</span>`}</p>` : ""}
    </div>
    <div class="card">
      <h3>🎮 Mechanics trainer</h3>
      <div><b>${plState}</b></div>
      <p class="muted small">A small BakkesMod plugin that only runs when Rocket League is started <b>without Easy Anti-Cheat</b>, and never sends anything in online games.
        It opens RL Coach automatically, grades every half flip, speed flip, wavedash, zap dash, fast aerial, flip reset, air dribble, ceiling shot and landing in free play or training, and shows a tip on screen.</p>
      ${pl.installed ? "" : pl.bakkesmod ? `<p><button class="primary" id="install-plugin">Install plugin</button></p>` : `<p class="muted small">Install <b>BakkesMod</b> first (bakkesmod.com) and run it once, then come back here.</p>`}
      <details><summary class="small">How to start Rocket League without Easy Anti-Cheat</summary>
        <ul class="tight small"><li><b>Steam:</b> launch the game and pick "Play without Easy Anti-Cheat" in the prompt.</li>
        <li><b>Epic:</b> Library → ⋯ next to Rocket League → "Launch without EAC". You can't play online in this mode; restart normally for ranked.</li>
        <li>Start BakkesMod before the game, then go to Free Play.</li></ul></details>
    </div>
  </div>
  <h2>Mechanics</h2>
  <div class="card"><b>Practise:</b> <span class="seg" id="focus">${MECH.map(([k, l]) => `<button data-k="${k}" class="${(pl.focus || "") === k ? "on" : ""}">${l}</button>`).join("")}</span> ${landing}
    <div class="muted small" style="margin-top:6px">Pick one to only get tips for that mechanic. Each try is scored out of 100 with one thing to fix.</div></div>
  <div class="grid4">${cards}</div>
  <div class="card"><h3>Latest attempts</h3>${attempts || `<div class="muted">Go to Free Play and try a half flip, speed flip, wavedash, zap dash, fast aerial, flip reset, air dribble or ceiling shot.</div>`}</div>`;

  $("#enable-stats")?.addEventListener("click", async () => {
    const r = await api("/api/live/enable-stats", {});
    toast(r.ok ? "Stats API turned on. Restart Rocket League to use it." : r.error, 5000);
  });
  $("#rl-path-save")?.addEventListener("click", async () => { await api("/api/settings/rl-install", { path: $("#rl-path").value }); pollLive(); });
  $("#install-plugin")?.addEventListener("click", async () => {
    const r = await api("/api/live/install-plugin", {});
    toast(r.ok ? "Plugin installed. Start Rocket League without Easy Anti-Cheat to use it." : r.error, 5000);
  });
  $("#open-watching")?.addEventListener("click", () => openReplay(st.replay_id));
  $("#focus").querySelectorAll("button").forEach((b) => b.onclick = async () => { await api("/api/live/focus", { kind: b.dataset.k }); pollLive(); });
}

// ------------------------------------------------------------------ updates
// The app downloads new versions by itself (Settings → Update automatically).
// This bar shows progress and lets you restart into the new version now;
// otherwise it installs the next time you open RL Coach.
let updateDismissed = false;
async function pollUpdate() {
  let u;
  try { u = await api("/api/update"); } catch { return; }
  let bar = $("#update-bar");
  if (!u.available || updateDismissed) { bar?.remove(); return; }
  if (!bar) { bar = document.createElement("div"); bar.id = "update-bar"; bar.className = "update-bar"; document.body.prepend(bar); }
  if (u.state === "downloading") {
    bar.innerHTML = `<span class="spinner small-spin"></span> Downloading RL Coach ${esc(u.latest)}…`;
  } else if (u.state === "ready") {
    bar.innerHTML = `RL Coach ${esc(u.latest)} is ready (you have ${esc(u.current)}). <button class="primary" id="upd-now">Restart to update</button>
      <button class="ghost" id="upd-later">${u.frozen ? "Install next time I open it" : "Later"}</button>`;
    $("#upd-now").onclick = async () => {
      bar.innerHTML = "Updating… RL Coach will reopen in a few seconds.";
      const r = await api("/api/update/apply", {});
      if (!r.ok) bar.innerHTML = "Couldn't update automatically. Download the new version from GitHub Releases.";
    };
    $("#upd-later").onclick = () => { updateDismissed = true; bar.remove(); };
  }
}
pollUpdate();
setInterval(pollUpdate, 15000);

// ------------------------------------------------------------------ desktop shortcut
// Asked once, the first time RL Coach opens. Settings can add or remove it later.
async function offerShortcut() {
  let sc;
  try { sc = await api("/api/shortcut"); } catch { return; }
  if (!sc.offer) return;
  const bar = document.createElement("div");
  bar.id = "shortcut-bar"; bar.className = "update-bar";
  bar.innerHTML = `Put RL Coach on your desktop so it's one double-click away?
    <button class="primary" id="sc-yes">Add desktop shortcut</button> <button class="ghost" id="sc-no">No thanks</button>`;
  document.body.prepend(bar);
  $("#sc-yes").onclick = async () => {
    const r = await api("/api/shortcut", { create: true });
    bar.remove();
    toast(r.ok ? "Added RL Coach to your desktop." : r.error, 4000);
  };
  $("#sc-no").onclick = async () => { bar.remove(); await api("/api/shortcut", { create: false }); };
}
offerShortcut();

if (location.hash === "#live") renderLive();
