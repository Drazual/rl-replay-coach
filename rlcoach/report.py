"""Render coaching reports as a terminal summary or a self-contained HTML page."""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import datetime
from statistics import mean

from .coach import Report
from .metrics import METRICS

ICON = {"conceded": "⚠", "whiff": "✕", "beaten": "⏱", "fifty": "⚔", "goal": "★"}


def _fmt(v, metric) -> str:
    return "–" if v is None else f"{format(v, metric.fmt)}{metric.unit}"


def _title(r: Report) -> str:
    s = r.replay.score
    me = r.player.team
    result = "Win" if r.won else ("Loss" if s[me] < s[1 - me] else "Draw")
    n = r.replay.team_size
    return f"{result} {s[me]}–{s[1 - me]} · {n}v{n} · {r.replay.map_name} · {r.replay.date}"


# --------------------------------------------------------------------------- text


def to_text(reports: list[Report]) -> str:
    lines: list[str] = []
    if len(reports) > 1:
        lines += _text_recurring(reports) + [""]
    for r in reports:
        lines.append(f"=== {r.player.name}: {_title(r)} ({r.replay.path.name})")
        if r.findings:
            lines.append("Focus on:")
            for i, f in enumerate(r.findings[:3], 1):
                lines.append(f"  {i}. {f.title}")
                lines.append(f"     {f.evidence}")
                lines.append(f"     Try: {f.fix[0]}")
        else:
            lines.append("No clear weaknesses this game. Nice.")
        if r.strengths:
            lines.append("Strengths: " + "; ".join(r.strengths))
        if r.moments:
            lines.append("Rewatch:")
            for mo in r.moments[:8]:
                lines.append(f"  {mo.clock:>8}  {mo.text}")
        lines.append("")
    return "\n".join(lines)


def _recurring(reports: list[Report]):
    seen = defaultdict(list)
    for r in reports:
        for f in r.findings:
            seen[f.key].append(f)
    rows = [(len(fs), mean(f.score for f in fs), fs[0]) for fs in seen.values()]
    rows.sort(key=lambda x: (-x[0], -x[1]))
    return rows


def _text_recurring(reports: list[Report]) -> list[str]:
    lines = [f"=== Across {len(reports)} games"]
    for count, _, f in _recurring(reports)[:5]:
        lines.append(f"  {count}/{len(reports)} games: {f.title}")
    return lines


# --------------------------------------------------------------------------- html

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#5d6670;--line:#e3e6ea;--accent:#2563eb;--good:#15803d;--bad:#b91c1c;--warn:#b45309;--chip:#eef2f7}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e8ebef;--muted:#9aa4af;--line:#2a3038;--accent:#60a5fa;--good:#4ade80;--bad:#f87171;--warn:#fbbf24;--chip:#222831}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}h3{font-size:16px;margin:0 0 6px}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:10px 0}
.focus{border-left:4px solid var(--accent)}.focus .n{display:inline-block;width:24px;height:24px;border-radius:50%;background:var(--accent);color:#fff;text-align:center;font-weight:600;font-size:13px;line-height:24px;margin-right:8px}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 10px;font-size:12px;color:var(--muted);margin-left:6px}
ul{margin:6px 0 0;padding-left:20px}li{margin:3px 0}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:500}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}.good{color:var(--good)}.bad{color:var(--bad)}
.moment{display:flex;gap:12px;padding:6px 0;border-bottom:1px solid var(--line)}.moment:last-child{border:0}.clock{font-variant-numeric:tabular-nums;font-weight:600;min-width:64px}
.moment.g .ic{color:var(--good)}.moment .ic{color:var(--warn);width:16px}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;padding:8px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
.scroll{overflow-x:auto}
"""


def _cmp_class(v, others, metric) -> str:
    if v is None or not others or metric.better is None:
        return ""
    o = mean(others)
    if not o:
        return ""
    rel = (v - o) / abs(o)
    if metric.better == "low":
        rel = -rel
    return "good" if rel > 0.1 else "bad" if rel < -0.1 else ""


def _game_html(r: Report, heading: bool = True) -> str:
    e = html.escape
    out = []
    if heading:
        out.append(f"<h1>{e(r.player.name)}</h1><div class='muted'>{e(_title(r))} · {e(r.replay.path.name)}</div>")

    out.append("<h2>Focus on these</h2>")
    if not r.findings:
        out.append("<div class='card'>Nothing stood out as a clear weakness this game.</div>")
    for i, f in enumerate(r.findings[:3], 1):
        fixes = "".join(f"<li>{e(x)}</li>" for x in f.fix)
        out.append(
            f"<div class='card focus'><h3><span class='n'>{i}</span>{e(f.title)}<span class='chip'>{e(f.area)}</span></h3>"
            f"<div><b>{e(f.evidence)}</b></div><div class='muted'>{e(f.why)}</div><ul>{fixes}</ul></div>"
        )
    if len(r.findings) > 3:
        more = "".join(f"<li><b>{e(f.title)}</b>: {e(f.evidence)} {e(f.fix[0])}</li>" for f in r.findings[3:])
        out.append(f"<details><summary>{len(r.findings) - 3} smaller things</summary><div class='card'><ul>{more}</ul></div></details>")

    if r.strengths:
        out.append("<h2>What you did well</h2><div class='card'><ul>"
                   + "".join(f"<li>{e(s)}</li>" for s in r.strengths) + "</ul></div>")

    if r.moments:
        out.append("<h2>Moments to rewatch</h2><div class='card'><div class='muted' style='margin-bottom:6px'>"
                   "Times are the in-game clock. Open the replay, scrub to that time, and watch the 5 seconds before it.</div>")
        for mo in r.moments:
            out.append(f"<div class='moment {'g' if mo.good else ''}'><span class='ic'>{ICON.get(mo.kind, '•')}</span>"
                       f"<span class='clock'>{e(mo.clock)}</span><span>{e(mo.text)}</span></div>")
        out.append("</div>")

    # Full stat table: you vs everyone else in the match.
    others = [p for k, p in r.replay.players.items() if k != r.player_key]
    head = "".join(f"<th class='num'>{e(p.name)}{' 🤖' if p.is_bot else ''}</th>" for p in others)
    rows, group = [], None
    for metric in METRICS:
        v = r.metrics.get(metric.key)
        ovals = [r.lobby[p.key].get(metric.key) for p in others]
        if v is None and all(x is None for x in ovals):
            continue
        if metric.group != group:
            group = metric.group
            rows.append(f"<tr><th colspan='{len(others) + 2}'>{e(group)}</th></tr>")
        cls = _cmp_class(v, [x for x in ovals if x is not None], metric)
        cells = "".join(f"<td class='num muted'>{_fmt(x, metric)}</td>" for x in ovals)
        rows.append(f"<tr><td>{e(metric.label)}</td><td class='num {cls}'><b>{_fmt(v, metric)}</b></td>{cells}</tr>")
    out.append("<details><summary>All stats vs the lobby</summary><div class='card scroll'><table>"
               f"<tr><th></th><th class='num'>You</th>{head}</tr>{''.join(rows)}</table></div></details>")
    return "\n".join(out)


def _trend_html(reports: list[Report]) -> str:
    e = html.escape
    out = [f"<h1>{e(reports[-1].player.name)}</h1><div class='muted'>{len(reports)} games analysed</div>"]
    wins = sum(r.won for r in reports)
    out.append(f"<h2>Across {len(reports)} games</h2><div class='muted'>{wins} wins, {len(reports) - wins} losses or draws.</div>")
    for count, _, f in _recurring(reports)[:4]:
        fixes = "".join(f"<li>{e(x)}</li>" for x in f.fix)
        out.append(f"<div class='card focus'><h3>{e(f.title)}<span class='chip'>{count} of {len(reports)} games</span></h3>"
                   f"<div class='muted'>{e(f.why)}</div><ul>{fixes}</ul></div>")

    keys = ["avg_boost", "zero_boost_pct", "avg_speed_kmh", "behind_ball_pct", "first_man_pct", "touches_p5",
            "whiffs_p5", "fifty_win_pct", "goals", "saves"]
    ms = [m for m in METRICS if m.key in keys]
    head = "".join(f"<th class='num'>{e(m.label)}</th>" for m in ms)
    body = []
    for r in reports:
        res = "W" if r.won else "L"
        cells = "".join(f"<td class='num'>{_fmt(r.metrics.get(m.key), m)}</td>" for m in ms)
        body.append(f"<tr><td>{e(r.replay.date[:10])} <b>{res}</b></td>{cells}</tr>")
    avg_cells = ""
    for m in ms:
        vals = [r.metrics.get(m.key) for r in reports if r.metrics.get(m.key) is not None]
        avg_cells += f"<td class='num'><b>{_fmt(mean(vals) if vals else None, m)}</b></td>"
    body.append(f"<tr><td><b>Average</b></td>{avg_cells}</tr>")
    out.append(f"<h2>Game by game</h2><div class='card scroll'><table><tr><th>Game</th>{head}</tr>{''.join(body)}</table></div>")
    return "\n".join(out)


def to_html(reports: list[Report]) -> str:
    if len(reports) == 1:
        body = _game_html(reports[0])
        title = f"Replay coach · {reports[0].player.name}"
    else:
        parts = [_trend_html(reports), "<h2>Each game</h2>"]
        for r in reversed(reports):
            parts.append(f"<details><summary>{html.escape(_title(r))}</summary>{_game_html(r, heading=False)}</details>")
        body = "\n".join(parts)
        title = f"Replay coach · {reports[-1].player.name} · {len(reports)} games"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}"
            f"<p class='muted' style='margin-top:40px;font-size:12px'>Generated {stamp} by rlcoach from replay files only. "
            "Thresholds are coaching heuristics; the lobby columns are the fairest comparison.</p></main></body></html>")
