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
  current: null, data: null, tab: "coach", player: null,
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
  $("#empty").hidden = !!state.current;
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
  const tabs = [["coach", "Coach"], ["heat", "Heat maps"], ["shots", "Shots"], ["stats", "Stats"], ["boost", "Boost"]];
  $("#view").innerHTML = `
    <div class="head">
      <div>
        <div class="title-row"><h1>${esc(displayName({ ...d, label: r.label }))}</h1><button id="rename" title="Give this replay a name">✎ Rename</button><button id="reveal">Show file</button></div>
        <div class="muted">${esc(d.playlist)} · ${d.team_size}v${d.team_size} · ${esc(d.map)} · ${esc(fmtDate(d.date))}${d.overtime ? " · overtime" : ""} · <span class="small">${esc(d.file)}</span></div>
      </div>
    </div>
    <div class="scoreboard">
      <div class="team">${team(0)}</div>
      <div class="big"><span class="t0">${d.score[0]}</span> – <span class="t1">${d.score[1]}</span></div>
      <div class="team right">${team(1)}</div>
    </div>
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
  ({ coach: renderCoach, heat: renderHeat, shots: renderShots, stats: renderStats, boost: renderBoost })[state.tab]();
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
      <div class="card"><h3>Moments to rewatch</h3><div class="muted small">In-game clock. Open the replay in Rocket League, scrub to the time, and watch the 5 seconds before it.</div>
        ${rep.moments.map((m) => `<div class="moment ${m.good ? "good" : ""}"><span class="ic">${ICON[m.kind] || "•"}</span><span class="clock">${esc(m.clock)}</span><span>${esc(m.text)}</span></div>`).join("") || `<div class="muted">Nothing flagged.</div>`}
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
        <div class="card"><h3>Shot list</h3>${shots.map((s) => `<div class="moment"><span class="clock">${esc(s.clock)}</span><span class="t${s.team}">${esc(playerByKey(s.player)?.name || "?")}</span><span class="muted">${s.outcome}${s.z > 300 ? " · aerial" : ""}</span></div>`).join("") || `<div class="muted">No shots.</div>`}</div>
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

// Tell the user when a newer release is on GitHub.
api("/api/update").then((u) => {
  if (!u.available) return;
  const bar = document.createElement("div");
  bar.className = "update-bar";
  bar.innerHTML = `RL Coach ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="primary">Download update</button> <button class="ghost">Later</button>`;
  const [get, later] = bar.querySelectorAll("button");
  get.onclick = () => api("/api/open-url", { url: u.exe || u.url });
  later.onclick = () => bar.remove();
  document.body.prepend(bar);
}).catch(() => {});
  });
}

$("#btn-settings").onclick = () => { renderSettings(); $("#settings").showModal(); };
$("#settings").addEventListener("close", async () => {
  const name = $("#set-player").value.trim();
  if (name) { await api("/api/settings", { player: name }); }
  loadList();

// Tell the user when a newer release is on GitHub.
api("/api/update").then((u) => {
  if (!u.available) return;
  const bar = document.createElement("div");
  bar.className = "update-bar";
  bar.innerHTML = `RL Coach ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="primary">Download update</button> <button class="ghost">Later</button>`;
  const [get, later] = bar.querySelectorAll("button");
  get.onclick = () => api("/api/open-url", { url: u.exe || u.url });
  later.onclick = () => bar.remove();
  document.body.prepend(bar);
}).catch(() => {});
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

// Tell the user when a newer release is on GitHub.
api("/api/update").then((u) => {
  if (!u.available) return;
  const bar = document.createElement("div");
  bar.className = "update-bar";
  bar.innerHTML = `RL Coach ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="primary">Download update</button> <button class="ghost">Later</button>`;
  const [get, later] = bar.querySelectorAll("button");
  get.onclick = () => api("/api/open-url", { url: u.exe || u.url });
  later.onclick = () => bar.remove();
  document.body.prepend(bar);
}).catch(() => {});
};
$("#btn-trends").onclick = renderTrends;
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

// Tell the user when a newer release is on GitHub.
api("/api/update").then((u) => {
  if (!u.available) return;
  const bar = document.createElement("div");
  bar.className = "update-bar";
  bar.innerHTML = `RL Coach ${esc(u.latest)} is available (you have ${esc(u.current)}). <button class="primary">Download update</button> <button class="ghost">Later</button>`;
  const [get, later] = bar.querySelectorAll("button");
  get.onclick = () => api("/api/open-url", { url: u.exe || u.url });
  later.onclick = () => bar.remove();
  document.body.prepend(bar);
}).catch(() => {});
