"""Everything the app shows for one replay, as plain JSON-ready data."""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import subtr_actor

from . import parse
from .coach import coach
from .metrics import METRICS, lobby_metrics
from .names import map_name, playlist_name
from .parse import pid_key
from .review import review

ANALYSIS_VERSION = 3  # bump when the payload shape changes so caches are rebuilt
HEATMAP_FPS = 10


def _tracks(path: str, players: list[str]) -> dict:
    """Positions (x, y) of every player and the ball, plus boost, sampled at HEATMAP_FPS."""
    meta, arr = subtr_actor.get_ndarray_with_info_from_replay_filepath(
        path,
        global_feature_adders=["CurrentTime", "BallRigidBodyNoVelocities"],
        player_feature_adders=["PlayerRigidBodyNoVelocities", "PlayerBoost"],
        fps=HEATMAP_FPS,
    )
    g = len(meta["column_headers"]["global_headers"])
    p = len(meta["column_headers"]["player_headers"])
    rm = meta["replay_meta"]
    order = [pid_key(x["remote_id"]) for x in rm["team_zero"] + rm["team_one"]]

    t = arr[:, 0]
    out = {
        "time": np.round(t, 1).tolist(),
        "ball": np.round(arr[:, 1:3]).astype(int).tolist(),
        "players": {},
        "boost": {},
    }
    for i, key in enumerate(order):
        base = g + i * p
        xyz = arr[:, base:base + 3]
        # A player who is demolished or has left sits at the origin; drop those samples.
        alive = ~((np.abs(xyz[:, 0]) < 1) & (np.abs(xyz[:, 1]) < 1) & (np.abs(xyz[:, 2]) < 1))
        pts = np.round(xyz[alive, :2]).astype(int)
        out["players"][key] = pts.tolist()
        boost = arr[:, base + p - 1] / 2.55
        out["boost"][key] = np.round(boost[:: HEATMAP_FPS // 2]).astype(int).tolist()  # 2 samples per second
    out["boost_time"] = np.round(t[:: HEATMAP_FPS // 2], 1).tolist()
    return out


def _shots(replay: parse.Replay) -> list[dict]:
    """Every shot or goal touch with where the ball was and how it ended."""
    goal_touches = {}
    for g in replay.modules.get("core", {}).get("goal_context", []) or []:
        last = g.get("scorer_last_touch") or {}
        if last.get("touch_id") is not None:
            goal_touches[last["touch_id"]] = g
    saves = [e for e in replay.modules.get("core", {}).get("timeline", []) or [] if e.get("kind") == "Save"]

    shots = []
    for e in replay.events("touch"):
        action = next((tg["value"] for tg in e.get("tags", []) if tg.get("group") == "action"), None)
        tid = e.get("touch_id")
        if action != "shot" and tid not in goal_touches:
            continue
        bx, by, bz = e.get("ball_position") or (0, 0, 0)
        t = e.get("time", 0)
        team = 0 if e.get("is_team_0") else 1
        if tid in goal_touches:
            outcome = "goal"
        elif any(s.get("is_team_0") != e.get("is_team_0") and 0 <= s.get("time", 0) - t <= 4 for s in saves):
            outcome = "saved"
        else:
            outcome = "missed"
        shots.append({
            "player": pid_key(e.get("player")),
            "team": team,
            "x": round(bx), "y": round(by), "z": round(bz),
            "time": t,
            "clock": replay.clock.label(t),
            "outcome": outcome,
            "speed_kmh": round((e.get("ball_speed_change") or 0) * 0.036),
        })
    return shots


def _frame_times(replay: parse.Replay) -> list[list[float]]:
    """(replay frame, replay time) pairs, so the app can follow along with the in-game replay viewer."""
    pairs = {}
    for module in ("touch", "whiff", "beaten_to_ball"):
        for e in replay.events(module):
            if e.get("frame") is not None and e.get("time") is not None:
                pairs[int(e["frame"])] = round(float(e["time"]), 3)
    return [[f, t] for f, t in sorted(pairs.items())]


def analyse(path: str, you: str | None = None) -> dict:
    replay = parse.load(path)
    you_player = replay.find_player(you)
    tracks = _tracks(str(path), list(replay.players))


    reports = {}
    for key, p in replay.players.items():
        try:
            r = coach(replay, p.name)
        except LookupError:
            continue
        reports[key] = {
            "findings": [asdict(f) for f in r.findings],
            "strengths": r.strengths,
            "moments": [asdict(m) for m in r.moments],
            "won": r.won,
        }
    lobby = lobby_metrics(replay)
    try:
        decisions = review(str(path), replay)
    except Exception as e:  # the rest of the analysis is still useful without it
        decisions = {"_error": str(e)}

    goals = []
    for g in replay.modules.get("core", {}).get("goal_context", []) or []:
        goals.append({
            "time": g.get("time", 0),
            "clock": replay.clock.label(g.get("time", 0)),
            "team": 0 if g.get("scoring_team_is_team_0") else 1,
            "scorer": pid_key(g.get("scorer")),
            "x": round((g.get("ball_position") or {}).get("x", 0)),
            "y": round((g.get("ball_position") or {}).get("y", 0)),
        })

    s0, s1 = replay.score
    return {
        "version": ANALYSIS_VERSION,
        "file": replay.path.name,
        "title": replay.headers.get("ReplayName") or "",
        "date": replay.date,
        "map": map_name(replay.map_name),
        "playlist": playlist_name(replay),
        "team_size": replay.team_size,
        "score": [s0, s1],
        "overtime": replay.clock.overtime,
        "you": you_player.key if you_player else None,
        "players": [
            {"key": p.key, "name": p.name, "team": p.team, "is_bot": p.is_bot, "car": p.car,
             "score": p.header_stats.get("Score", 0)}
            for p in replay.players.values()
        ],
        "metric_defs": [asdict(m) for m in METRICS],
        "metrics": {k: {mk: mv for mk, mv in m.items() if not mk.startswith("_")} for k, m in lobby.items()},
        "reports": reports,
        "review": decisions,
        "tracks": tracks,
        "shots": _shots(replay),
        "frame_times": _frame_times(replay),
        "goals": goals,
    }
