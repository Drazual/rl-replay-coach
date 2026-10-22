"""Turn a .replay file into a plain Python structure we can coach from.

All parsing is done offline by subtr-actor (a Rust replay parser with Python
bindings). Nothing here touches the running game.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import subtr_actor

# Only the stat modules the coach actually reads. Parsing every module is
# roughly 2x slower.
MODULES = [
    "core",
    "boost",
    "movement",
    "positioning",
    "rotation",
    "touch",
    "whiff",
    "beaten_to_ball",
    "kickoff",
    "fifty_fifty",
    "demo",
    "powerslide",
    "wavedash",
    "half_flip",
]


def pid_key(remote_id) -> str:
    """Stable string key for a player's remote id dict."""
    return json.dumps(remote_id, sort_keys=True)


@dataclass
class Player:
    key: str
    name: str
    team: int  # 0 = blue, 1 = orange
    is_bot: bool
    car: str | None
    header_stats: dict = field(default_factory=dict)


class Clock:
    """Maps replay time (seconds since recording began) to the in-game clock."""

    def __init__(self, replay_time: np.ndarray, seconds_remaining: np.ndarray):
        self.t = replay_time
        self.sr = seconds_remaining
        zero = np.nonzero(seconds_remaining <= 0)[0]
        self.ot_start = float(replay_time[zero[0]]) if len(zero) else None
        self.overtime = False  # set by load() once we know the score at 0:00

    def label(self, t: float) -> str:
        if len(self.t) == 0:
            return f"replay {int(t) // 60}:{int(t) % 60:02d}"
        i = int(np.clip(np.searchsorted(self.t, t), 0, len(self.t) - 1))
        sr = int(round(float(self.sr[i])))
        if sr <= 0 and self.overtime and t > self.ot_start + 4:
            over = int(t - self.ot_start)
            return f"OT +{over // 60}:{over % 60:02d}"
        return f"{sr // 60}:{sr % 60:02d}"


@dataclass
class Replay:
    path: Path
    headers: dict
    players: dict[str, Player]
    modules: dict
    clock: Clock
    game_type: dict = field(default_factory=dict)

    @property
    def team_size(self) -> int:
        n = self.headers.get("TeamSize")
        if n:
            return int(n)
        return max(1, len(self.players) // 2)

    @property
    def score(self) -> tuple[int, int]:
        return int(self.headers.get("Team0Score", 0) or 0), int(self.headers.get("Team1Score", 0) or 0)

    @property
    def recorder(self) -> str | None:
        return self.headers.get("PlayerName")

    @property
    def date(self) -> str:
        return str(self.headers.get("Date", ""))

    @property
    def map_name(self) -> str:
        return str(self.headers.get("MapName", ""))

    def player_stats(self, module: str, key: str) -> dict:
        """The summed per-player stats for one module, or {} if absent."""
        for entry in self.modules.get(module, {}).get("player_stats", []) or []:
            if pid_key(entry.get("player_id")) == key:
                return entry.get("stats", {}) or {}
        return {}

    def events(self, module: str, name: str = "events") -> list:
        return self.modules.get(module, {}).get(name, []) or []

    def find_player(self, query: str | None) -> Player | None:
        """Find a player by (case-insensitive) name; falls back to the recorder."""
        humans = [p for p in self.players.values() if not p.is_bot]
        for q in [query, self.recorder]:
            if not q:
                continue
            ql = q.lower()
            exact = [p for p in self.players.values() if p.name.lower() == ql]
            if exact:
                return exact[0]
            partial = [p for p in self.players.values() if ql in p.name.lower()]
            if partial:
                return partial[0]
            if q is query:
                return None  # an explicit name that is not in this replay
        return humans[0] if len(humans) == 1 else None


def load(path: str | Path) -> Replay:
    path = Path(path)
    summed = subtr_actor.get_summed_stats(str(path), MODULES)
    meta = summed["replay_meta"]
    headers = dict(meta.get("all_headers", []))

    header_stats = {p.get("Name"): p for p in headers.get("PlayerStats", []) or []}
    players: dict[str, Player] = {}
    for team, side in ((0, "team_zero"), (1, "team_one")):
        for p in meta.get(side, []) or []:
            key = pid_key(p.get("remote_id"))
            hs = header_stats.get(p.get("name"), {})
            players[key] = Player(
                key=key,
                name=p.get("name", "?"),
                team=team,
                is_bot=bool(hs.get("bBot")),
                car=p.get("car_hitbox_family"),
                header_stats=hs,
            )

    try:
        _, arr = subtr_actor.get_ndarray_with_info_from_replay_filepath(
            str(path), global_feature_adders=["CurrentTime", "SecondsRemaining"], player_feature_adders=[]
        )
        clock = Clock(arr[:, 0].astype(float), arr[:, 1].astype(float))
    except Exception:
        clock = Clock(np.array([]), np.array([]))

    if clock.ot_start is not None:
        goals = summed["modules"].get("core", {}).get("goal_context", []) or []
        before = [g.get("scoring_team_is_team_0") for g in goals if g.get("time", 0) < clock.ot_start]
        clock.overtime = before.count(True) == before.count(False) and len(goals) > len(before)

    return Replay(path=path, headers=headers, players=players, modules=summed["modules"], clock=clock,
                  game_type=meta.get("game_type") or {})
