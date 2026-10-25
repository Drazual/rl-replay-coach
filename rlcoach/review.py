"""Decision review: what you did at the moments that mattered, and what you should have done.

The replay is rebuilt 15 times a second (every car, the ball, boost) and lined up
with the parser's touch, whiff, demo, shot and boost-pickup events. For every goal
against we walk back to where the attack started and check, in order, what each
of your decisions contributed: did you lose the challenge, were you the cover and
caught upfield, did you double commit, whiff, get demoed, come back on the ball
side, or go for boost while the attack was coming? The biggest one is the root
cause. If none of it is on you, the review says so and names what did go wrong.

Outside goals it also flags habits that did not cost a goal this time but will:
risky last-man challenges, double commits, boost grabs while last back, shots
straight at the keeper and missed open nets. Goals and saves are listed as
good decisions.

Field: team 0 (blue) defends -y, team 1 (orange) defends +y. 1 unit = 1 cm.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import subtr_actor

from .parse import Replay, pid_key

FPS = 15
GOAL_Y = 5120.0
POST_X = 893.0

KIND_LABELS = {
    "last_man_challenge": "Lost challenge as last back",
    "lost_challenge": "Lost the challenge that started the attack",
    "bad_clear": "Clear went to the middle",
    "soft_touch": "Soft touch gave the ball away",
    "own_goal": "Touch went into your own net",
    "double_commit": "Double commit",
    "no_cover": "Caught upfield when you were the cover",
    "whiff": "Missed the ball",
    "beaten": "Beaten to the ball",
    "demoed": "Demolished during the attack",
    "late_back": "Not back in time",
    "too_far_out": "Too far out of your net",
    "beaten_back": "The ball beat you back to your net",
    "no_boost": "No boost for the save",
    "ball_side": "Rotated back on the ball side",
    "boost_grab": "Went for boost while last back",
    "open_net_miss": "Missed an open net",
    "shot_at_keeper": "Shot straight at the keeper",
    "not_on_you": "Not on you",
    "goal": "Goal",
    "save": "Save",
}


@dataclass
class Step:
    time: float
    clock: str
    kind: str
    weight: float  # how much it decided the outcome; the largest is the root cause
    happened: str
    should: str
    why: str


@dataclass
class Decision:
    time: float
    clock: str
    kind: str
    verdict: str  # "mistake" | "ok" | "good"
    title: str
    happened: str
    should: str
    why: str
    weight: float
    context: str = ""  # e.g. "Goal against at 2:31"
    steps: list = field(default_factory=list)  # the chain for a goal against, in time order
    snapshot: dict = field(default_factory=dict)  # positions at the decision, for the diagram


# ---------------------------------------------------------------------------- game state


class State:
    """Every car and the ball, sampled at FPS, plus the touch list."""

    def __init__(self, path: str, replay: Replay):
        meta, arr = subtr_actor.get_ndarray_with_info_from_replay_filepath(
            str(path),
            global_feature_adders=["CurrentTime", "BallRigidBody"],
            player_feature_adders=["PlayerRigidBody", "PlayerBoost"],
            fps=FPS,
        )
        g = len(meta["column_headers"]["global_headers"])
        p = len(meta["column_headers"]["player_headers"])
        rm = meta["replay_meta"]
        self.replay = replay
        self.t = arr[:, 0].astype(float)
        self.ball = arr[:, 1:4].astype(float)
        self.bvel = arr[:, 7:10].astype(float)
        self.pos, self.vel, self.boost, self.alive = {}, {}, {}, {}
        for i, x in enumerate(rm["team_zero"] + rm["team_one"]):
            key, base = pid_key(x["remote_id"]), g + i * p
            pos = arr[:, base:base + 3].astype(float)
            self.pos[key] = pos
            self.vel[key] = arr[:, base + 6:base + 9].astype(float)
            self.boost[key] = arr[:, base + 12].astype(float) / 2.55
            # A demolished (or departed) car sits at the origin.
            self.alive[key] = ~(np.abs(pos).sum(axis=1) < 3)
        self.team = {k: pl.team for k, pl in replay.players.items() if k in self.pos}
        self.name = {k: pl.name for k, pl in replay.players.items()}

        self.touches = []
        for e in replay.events("touch"):
            key = pid_key(e.get("player"))
            if key not in self.team:
                continue
            tags = {f"{tg.get('group')}:{tg.get('value')}" for tg in e.get("tags", [])}
            self.touches.append({
                "t": float(e.get("time", 0)), "key": key, "team": self.team[key],
                "ball": e.get("ball_position") or [0, 0, 0],
                "power": float(e.get("ball_speed_change") or 0), "tags": tags,
            })
        self.touches.sort(key=lambda x: x["t"])

        starts = []
        for k in replay.events("kickoff"):
            starts.append(float(k.get("start_time") or k.get("live_action_start_time") or 0))
        self.kickoffs = sorted(starts) or [float(self.t[0]) if len(self.t) else 0.0]

    # ---- lookups
    def i(self, t: float) -> int:
        j = int(np.clip(np.searchsorted(self.t, t), 0, len(self.t) - 1))
        return j - 1 if j > 0 and abs(self.t[j - 1] - t) < abs(self.t[j] - t) else j

    def segment_start(self, t: float) -> float:
        return max([k for k in self.kickoffs if k <= t] or [self.kickoffs[0]])

    def teammates(self, key: str) -> list[str]:
        return [k for k in self.team if self.team[k] == self.team[key] and k != key]

    def opponents(self, key: str) -> list[str]:
        return [k for k in self.team if self.team[k] != self.team[key]]

    # ---- geometry
    @staticmethod
    def fwd(team: int) -> int:
        return 1 if team == 0 else -1  # blue attacks +y

    def depth(self, team: int, y: float) -> float:
        """How far upfield a y position is for this team (own goal line = -5120)."""
        return y * self.fwd(team)

    def own_goal(self, team: int) -> tuple[float, float]:
        return 0.0, -GOAL_Y * self.fwd(team)

    def dist_ball(self, key: str, i: int) -> float:
        return float(np.linalg.norm(self.ball[i] - self.pos[key][i]))

    def toward_ball(self, key: str, i: int) -> float:
        """Speed toward the ball (negative = driving away)."""
        d = self.ball[i] - self.pos[key][i]
        n = np.linalg.norm(d) or 1.0
        return float(np.dot(self.vel[key][i], d / n))

    def eta(self, key: str, i: int) -> float:
        """Rough seconds to reach the ball: distance over a speed that grows toward ~2300 uu/s."""
        if not self.alive[key][i]:
            return 99.0
        d, toward = self.dist_ball(key, i), self.toward_ball(key, i)
        t = d / max(1200.0, (max(toward, 0.0) + 2300.0) / 2)
        return t + (0.6 if toward < -200 else 0.0)

    def last_back(self, team: int, i: int) -> str | None:
        """The alive player on this team closest to their own goal line."""
        cars = [k for k, tm in self.team.items() if tm == team and self.alive[k][i]]
        return min(cars, key=lambda k: self.depth(team, self.pos[k][i][1]), default=None)

    def goalside(self, key: str, i: int, margin: float = 200.0) -> bool:
        tm = self.team[key]
        return self.depth(tm, self.pos[key][i][1]) < self.depth(tm, self.ball[i][1]) - margin

    def upfield_of_ball(self, key: str, i: int) -> float:
        tm = self.team[key]
        return self.depth(tm, self.pos[key][i][1]) - self.depth(tm, self.ball[i][1])

    def dist_own_goal(self, key: str, i: int) -> float:
        gx, gy = self.own_goal(self.team[key])
        x, y = self.pos[key][i][:2]
        return math.hypot(x - gx, y - gy)

    def snapshot(self, i: int, me: str, suggest: tuple | None = None, label: str = "") -> dict:
        def r(v):
            return int(round(float(v)))
        snap = {
            "ball": {"x": r(self.ball[i][0]), "y": r(self.ball[i][1]), "z": r(self.ball[i][2]),
                     "vx": r(self.bvel[i][0]), "vy": r(self.bvel[i][1])},
            "players": [
                {"key": k, "team": self.team[k], "you": k == me, "alive": bool(self.alive[k][i]),
                 "x": r(self.pos[k][i][0]), "y": r(self.pos[k][i][1]),
                 "vx": r(self.vel[k][i][0]), "vy": r(self.vel[k][i][1]), "boost": r(self.boost[k][i])}
                for k in self.team
            ],
        }
        if suggest is not None:
            snap["suggest"] = {"x": r(suggest[0]), "y": r(suggest[1]), "label": label}
        return snap


def _m(uu: float) -> str:
    return f"{abs(uu) / 100:.0f} m"


def _where(st: State, team: int, x: float, y: float) -> str:
    d = st.depth(team, y)
    third = "in your defensive third" if d < -1700 else "in midfield" if d < 1700 else "in their third"
    side = "" if abs(x) < 1300 else (" on the left" if (x * st.fwd(team)) < 0 else " on the right")
    return third + side


def _back_post(st: State, team: int, ball_x: float) -> tuple[float, float]:
    gx, gy = st.own_goal(team)
    side = -1.0 if ball_x > 0 else 1.0
    return side * (POST_X - 100), gy + 250 * st.fwd(team)


def _cover_spot(st: State, team: int, i: int) -> tuple[float, float]:
    """Between the ball and your net, a little to the far side: where the second defender should be."""
    gx, gy = st.own_goal(team)
    bx, by = st.ball[i][:2]
    x = bx * 0.35 + (-np.sign(bx) * 600 if abs(bx) > 300 else 0)
    return float(x), float(gy + (by - gy) * 0.45)


def _shadow_spot(st: State, team: int, i: int) -> tuple[float, float]:
    gx, gy = st.own_goal(team)
    bx, by = st.ball[i][:2]
    d = math.hypot(bx - gx, by - gy) or 1.0
    k = min(1.0, 1500.0 / d)
    return float(bx + (gx - bx) * k), float(by + (gy - by) * k)


# ---------------------------------------------------------------------------- goals against


def _goal_against(st: State, g: dict, me: str) -> Decision:
    rp = st.replay
    team, opp = st.team[me], 1 - st.team[me]
    tg = float(g.get("time", 0))
    seg0 = st.segment_start(tg - 0.5)
    tl = [x for x in st.touches if seg0 <= x["t"] <= tg + 0.05]
    clock = rp.clock.label

    j = len(tl) - 1
    while j >= 0 and tl[j]["team"] == opp:
        j -= 1
    lost = tl[j] if j >= 0 else None  # our last touch
    poss = tl[j + 1] if j + 1 < len(tl) else None  # first touch of the scoring chain
    shot = tl[-1] if tl and tl[-1]["team"] == opp else None
    t_poss = poss["t"] if poss else (lost["t"] if lost else tg - 3)
    t_shot = shot["t"] if shot else tg - 1
    win0 = min(lost["t"] if lost else t_poss, t_poss) - 1.5
    steps: list[Step] = []
    mates = st.teammates(me)

    def add(t, kind, w, happened, should, why):
        steps.append(Step(round(t, 2), clock(t), kind, w, happened, should, why))

    # Own goal: our touch was the last one.
    if tl and tl[-1]["team"] == team:
        last = tl[-1]
        shot_before = [x for x in tl if x["team"] == opp and 0 < last["t"] - x["t"] <= 2.0]
        if last["key"] == me and shot_before:
            add(last["t"], "not_on_you", 0.5,
                f"You got a touch on {st.name[shot_before[-1]['key']]}'s shot but it still went in.", "", "")
        elif last["key"] == me:
            add(last["t"], "own_goal", 2.0, "Your touch sent the ball into your own net.",
                "When the ball is coming across your goal, hit it toward the side wall or corner, not back across "
                "the goal mouth. If you can't get a clean touch, let it hit the wall or backboard.",
                "Touches across your own goal mouth are the most common source of own goals.")
        else:
            add(last["t"], "not_on_you", 0.5, f"The ball went in off {st.name.get(last['key'], 'a teammate')}.", "", "")

    # 1. Our last touch before they took over.
    if lost is not None and (not tl or tl[-1]["team"] == opp):
        i = st.i(lost["t"])
        bd = st.depth(team, lost["ball"][1])
        contested = poss is not None and poss["t"] - lost["t"] < 0.8
        if lost["key"] == me:
            lb = st.last_back(team, i) == me
            mates_up = [f"{st.name[k]} {_m(st.upfield_of_ball(k, i))} upfield" for k in mates
                        if st.alive[k][i] and st.upfield_of_ball(k, i) > 0]
            if contested and lb and bd < 2000:
                add(lost["t"], "last_man_challenge", 3.0,
                    f"You challenged {st.name[poss['key']]} {_where(st, team, *lost['ball'][:2])} as the last player "
                    f"back and they won it" + (f" ({', '.join(mates_up)})." if mates_up else "."),
                    "Shadow instead: drive back toward your net facing the ball, stay between it and the goal, and "
                    "only challenge when they make a heavy touch or a teammate is back to cover you.",
                    "When the last defender loses a challenge there is nobody left between the ball and your net.")
            elif contested:
                add(lost["t"], "lost_challenge", 1.5 if bd < -1700 else 1.1 if bd < 1700 else 0.7,
                    f"You lost the challenge to {st.name[poss['key']]} {_where(st, team, *lost['ball'][:2])} and that "
                    "started their attack.",
                    "Only challenge when you'll get there first or with a hard, aimed flip. If you'll be second, "
                    "slow down, fake, and cover instead.",
                    "A lost challenge sends the ball past you; arriving late is worse than not going.")
            elif poss is not None:
                pi = st.i(poss["t"])
                pb = st.ball[pi]
                if st.depth(team, pb[1]) < 0 and abs(pb[0]) < 2500 and poss["t"] - lost["t"] < 3:
                    add(lost["t"], "bad_clear", 2.0,
                        f"Your touch put the ball in the middle of your half and {st.name[poss['key']]} picked it up.",
                        "Clear toward the side walls or corners, or high and hard into their half. When you can't get "
                        "a strong touch, a soft touch into the corner is better than one into the middle.",
                        "The middle of your half is the most dangerous place to give the ball away: every opponent "
                        "has a straight line to your net.")
                elif lost["power"] < 900 and poss["t"] - lost["t"] < 2.5:
                    add(lost["t"], "soft_touch", 1.3,
                        f"Your soft touch went straight to {st.name[poss['key']]}.",
                        "Touch it with a purpose: either keep control of it or hit it hard toward the corner or a teammate.",
                        "A soft touch gives the opponent a free first touch.")
        else:
            # A teammate lost it. Were you a second player on the same ball, or the missing cover?
            ib = st.i(lost["t"] - 0.35)
            if (st.alive[me][ib] and st.dist_ball(me, ib) < 1000 and st.toward_ball(me, ib) > 500
                    and st.dist_ball(lost["key"], ib) < 1200 and _was_second(st, me, lost["key"], lost["t"])):
                add(lost["t"], "double_commit", 2.5,
                    f"You and {st.name[lost['key']]} both went for the same ball; {st.name[lost['key']]} touched it and "
                    f"{st.name[poss['key']] if poss else 'they'} took it past you both.",
                    "If a teammate is closer or already going, peel off: turn toward your back post or the space "
                    "between the ball and your net, and be ready for what comes out of their challenge.",
                    "Two players on one ball means nobody is covering; any bounce past you is an open run at your net.")
            elif contested and st.alive[me][i]:
                behind = [k for k in mates if st.alive[k][i] and st.goalside(k, i) and k != lost["key"]]
                up = st.upfield_of_ball(me, i)
                if not behind and up > 500 and team_size(st) > 1:
                    add(lost["t"], "no_cover", 2.3,
                        f"{st.name[lost['key']]} challenged and nobody was behind them: you were {_m(up)} upfield of the ball.",
                        "When a teammate goes for a challenge, be behind it: roughly halfway between the ball and "
                        "your net, ready for what comes out.",
                        "Challenges are 50/50s. Somebody has to cover the half where it goes wrong, and you were the "
                        "closest one to doing it.")

    # 2. Your whiffs, beaten-to-balls and demos during the attack.
    for e in rp.events("whiff"):
        if e.get("kind") == "whiff" and pid_key(e.get("player")) == me and win0 <= e.get("time", 0) <= t_shot + 0.2:
            t = float(e["time"])
            lb = st.last_back(team, st.i(t)) == me
            add(t, "whiff", 2.8 if lb else 2.0,
                f"You went for the ball {'in the air ' if e.get('aerial') else ''}and missed it by "
                f"{e.get('closest_approach_distance', 0) / 100:.1f} m" + (" as the last player back." if lb else "."),
                "Go into uncertain touches a little slower and under control. If you're not sure you'll hit it, "
                "stay goal-side and let it come to you.",
                "A whiff costs the time, the boost and your position, and gives them a free touch.")
    for e in rp.events("beaten_to_ball"):
        t = float(e.get("time", 0))
        if pid_key(e.get("player")) == me and win0 <= t <= t_shot and (e.get("margin_seconds") or 0) > 0.15:
            i = st.i(t)
            bd = st.depth(team, st.ball[i][1])
            lb = st.last_back(team, i) == me and team_size(st) > 1
            w = 2.6 if lb and bd < 2000 else 1.2 if bd < 0 else 0.6
            add(t, "beaten", w,
                f"You committed to the ball {_where(st, team, *st.ball[i][:2])}"
                + (" as the last player back" if lb else "")
                + f" but {st.name.get(pid_key(e.get('winner')), 'they')} got there "
                f"{e.get('margin_seconds', 0):.1f}s before you.",
                "If you can see you'll be second, don't commit: turn back and cover the space behind the play.",
                "Committing late takes you out of the play without winning the ball.")
    for d in rp.modules.get("demo", {}).get("demolitions", []) or []:
        t = float(d.get("time", 0))
        if pid_key(d.get("victim")) == me and win0 - 1.5 <= t <= tg:
            add(t, "demoed", 1.8,
                f"You were demolished by {st.name.get(pid_key(d.get('attacker')), 'an opponent')} "
                f"{tg - t:.1f}s before the goal.",
                "When you're defending, keep moving and watch for an opponent driving at you at speed; jump or "
                "dodge sideways out of the way.",
                "A demo removes you for 3 seconds, which is the whole attack.")

    # 3. Big boost while the attack was coming.
    for e in rp.modules.get("boost", {}).get("pickup_events", []) or []:
        t = float(e.get("time", 0))
        if pid_key(e.get("player_id")) != me or e.get("pad_type") != "big" or not (t_poss - 0.5 <= t <= t_shot):
            continue
        i = st.i(t)
        if (st.last_back(team, i) == me and team_size(st) > 1 and st.depth(team, st.ball[i][1]) < 1000
                and _boost_left_net(st, me, i)):
            add(t, "boost_grab", 1.8,
                f"You went for the big boost {_where(st, team, *st.pos[me][i][:2])} as the last player back while "
                "they were attacking.",
                "When your team doesn't have the ball and you're last back, stay in net and take the small pads on "
                "the way. Get the big boost once the ball is safely in their half.",
                "The corner boost is the furthest point from the play; going there as last man leaves the net empty.")
            break

    # 4. Defending the shot.
    i_s = st.i(t_shot)
    if st.alive[me][i_s] and not any(s.kind == "demoed" for s in steps):
        lb = st.last_back(team, i_s) == me
        in_net = st.dist_own_goal(me, i_s) < 1600 and st.goalside(me, i_s, 0)
        if (lb and st.upfield_of_ball(me, i_s) > 500
                and not any(s.kind in ("no_cover", "double_commit", "beaten", "last_man_challenge") for s in steps)):
            add(t_shot, "late_back", 1.5,
                f"When they shot you were the last one back but still {_m(st.upfield_of_ball(me, i_s))} upfield of the ball.",
                "As soon as they get the ball in your half, turn and drive back to your net (boost if you need to) "
                "rather than trying to catch the ball from behind.",
                "From behind the ball you can't block the shot; only someone between the ball and the net can.")
        elif lb and st.goalside(me, i_s, 0) and _ball_goal_dist(st, team, i_s) > 3500 and (
                st.dist_own_goal(me, st.i(tg - 0.2)) > 1000 or not st.goalside(me, st.i(tg - 0.2), 0)):
            d0 = st.dist_own_goal(me, i_s)
            spd = float(np.linalg.norm(st.vel[me][i_s][:2])) * 0.036
            high = st.depth(team, st.pos[me][i_s][1]) > 0
            add(t_shot, "beaten_back", 1.8,
                f"The ball was sent toward your net from {_m(_ball_goal_dist(st, team, i_s))} out. You were the last one "
                f"back, {_m(d0)} from goal{' and already in their half' if high else ''}, going {spd:.0f} km/h, and the "
                "ball got there first.",
                "As last back, stay in your own half while your teammate is deep in theirs, and the moment the ball "
                "is cleared toward your net, turn and race back: boost and flip so you beat the ball to the goal.",
                "A long clear takes about 3-4 seconds to reach your goal; that is enough time only if you react at once.")
        elif (lb and not in_net and st.dist_own_goal(me, i_s) > 2200 and st.goalside(me, i_s)
              and _ball_goal_dist(st, team, i_s) < 4000 and st.dist_ball(me, i_s) > 800):
            add(t_shot, "too_far_out", 1.2,
                f"You were {_m(st.dist_own_goal(me, i_s))} out from your goal when the shot was taken.",
                "When the attacker has the ball under control, back up into your net and make them beat you with a "
                "good shot, rather than meeting them at the top of the box.",
                "Far from the net you cover only a small part of the goal; a simple shot past you goes in.")
        bz = float(g.get("ball_position", {}).get("z", 0) or 0)
        if lb and st.boost[me][i_s] < 12 and bz > 300 and st.dist_own_goal(me, i_s) < 3000:
            add(t_shot, "no_boost", 1.0,
                f"You had {st.boost[me][i_s]:.0f} boost when they shot and the ball went in {bz / 100:.1f} m high.",
                "Pick up small pads on your way back so you have at least 30 for a save.",
                "With an empty tank you can't reach anything above the crossbar height of your car.")
        if not lb or not in_net:
            # Coming back on the ball side?
            i0, i1 = st.i(t_poss), i_s
            if i1 > i0:
                gx, gy = st.own_goal(team)
                def retreating(k):
                    to_goal = np.array([gx, gy]) - st.pos[me][k][:2]
                    return np.dot(st.vel[me][k][:2], to_goal / (np.linalg.norm(to_goal) or 1.0)) > 700

                frames = [k for k in range(i0, i1 + 1)
                          if st.alive[me][k] and abs(st.ball[k][0]) > 900 and retreating(k)]
                ballside = [k for k in frames if np.sign(st.pos[me][k][0]) == np.sign(st.ball[k][0])
                            and abs(st.pos[me][k][0]) > 700
                            and any(st.alive[m][k] and st.dist_ball(m, k) < st.dist_ball(me, k) for m in mates)]
                if len(frames) >= 8 and len(ballside) >= 0.6 * len(frames):
                    k = ballside[len(ballside) // 2]
                    add(float(st.t[k]), "ball_side", 1.0,
                        "You rotated back on the same side of the field as the ball.",
                        "Come back toward the far (back) post, away from the ball. From there you see the whole play, "
                        "can cover the whole goal, and don't block your teammates' path.",
                        "Ball-side rotations put you in front of the attacker's touch and leave the far post open.")

    steps.sort(key=lambda s: s.time)
    mine = [s for s in steps if s.kind != "not_on_you"]
    who = st.name.get(g_scorer(g), "they")
    ctx = f"Goal against at {clock(tg)}" + (f" ({who})" if who else "")
    if mine and max(s.weight for s in mine) >= 1.2:
        root = max(mine, key=lambda s: (s.weight, -s.time))
        i = st.i(root.time)
        sug = {
            "last_man_challenge": (_shadow_spot(st, team, i), "shadow here"),
            "double_commit": (_cover_spot(st, team, i), "cover here"),
            "no_cover": (_cover_spot(st, team, i), "cover here"),
            "late_back": (_back_post(st, team, st.ball[i][0]), "back post"),
            "ball_side": (_back_post(st, team, st.ball[i][0]), "back post"),
            "boost_grab": (_back_post(st, team, st.ball[i][0]), "stay in net"),
            "too_far_out": (_shadow_spot(st, team, i), "back up"),
            "beaten_back": (_back_post(st, team, st.ball[i][0]), "be here"),
        }.get(root.kind, (None, ""))
        return Decision(root.time, root.clock, root.kind, "mistake", KIND_LABELS[root.kind], root.happened,
                        root.should, root.why, 3.0 + root.weight, ctx, [asdict(s) for s in steps],
                        st.snapshot(i, me, *sug) if sug[0] is not None else st.snapshot(i, me))

    # Not on you: explain what did go wrong.
    what = "they scored"
    if tl and tl[-1]["team"] == team:
        what = "the ball went in off " + ("you after their shot" if tl[-1]["key"] == me
                                          else st.name.get(tl[-1]["key"], "a teammate"))
    elif lost is not None and lost["key"] == me and poss is not None:
        what = f"you lost the ball to {st.name[poss['key']]} {_where(st, team, *lost['ball'][:2])} at {clock(lost['t'])}"
    elif lost is not None and poss is not None:
        what = (f"{st.name[lost['key']]} lost the ball to {st.name[poss['key']]} "
                f"{_where(st, team, *lost['ball'][:2])} at {clock(lost['t'])}")
    elif poss is not None:
        what = f"{st.name[poss['key']]} won possession at {clock(poss['t'])}"
    i_s2 = st.i(t_shot)
    you = ("you were in net" if st.alive[me][i_s2] and st.dist_own_goal(me, i_s2) < 1600
           else "you were goal-side of the ball" if st.alive[me][i_s2] and st.goalside(me, i_s2)
           else "you were covering further up")
    return Decision(tg, clock(tg), "not_on_you", "ok", "Not on you",
                    f"The attack started when {what}; {you}.",
                    "Nothing big to change on your side. Rewatch it from your teammate's view to see how your team "
                    "could have covered it together.",
                    "Not every goal is a mistake by you; it helps to know which ones were.",
                    1.0, ctx, [asdict(s) for s in steps], st.snapshot(i_s2, me))


def _ball_goal_dist(st: State, team: int, i: int) -> float:
    gx, gy = st.own_goal(team)
    return math.hypot(st.ball[i][0] - gx, st.ball[i][1] - gy)


def _boost_left_net(st: State, me: str, i: int) -> bool:
    """Did this pickup take you off the line between the ball and your net?

    A corner pad next to your back post on the way back is fine; a midfield pad, or
    one that puts you behind the ball or far to the side of the shooting lane, is not.
    """
    team = st.team[me]
    x, y = st.pos[me][i][:2]
    if st.depth(team, y) > -1500:  # midfield or upfield pads
        return True
    if not st.goalside(me, i, 0):
        return True
    gx, gy = st.own_goal(team)
    bx, by = st.ball[i][:2]
    # distance from you to the ball-to-goal line
    vx, vy = gx - bx, gy - by
    n = math.hypot(vx, vy) or 1.0
    off_line = abs((x - bx) * vy - (y - by) * vx) / n
    return off_line > 2200


def _was_second(st: State, me: str, mate: str, t: float) -> bool:
    """Was the teammate already the better-placed player a second before the touch?"""
    i = st.i(t - 1.0)
    return st.eta(mate, i) <= st.eta(me, i) + 0.1


def g_scorer(g: dict) -> str:
    return pid_key(g.get("scorer"))


def team_size(st: State) -> int:
    return max(1, len(st.team) // 2)


# ---------------------------------------------------------------------------- open play


def _open_play(st: State, me: str, goal_windows: list[tuple[float, float]]) -> list[Decision]:
    rp = st.replay
    team, opp = st.team[me], 1 - st.team[me]
    clock = rp.clock.label
    out: list[Decision] = []
    opp_shots = [float(e.get("time", 0)) for e in rp.modules.get("core", {}).get("timeline", []) or []
                 if e.get("kind") == "Shot" and e.get("is_team_0") == (opp == 0)]

    def in_goal_window(t):
        return any(a <= t <= b for a, b in goal_windows)

    def led_to_shot(t):
        return any(0 < s - t <= 6 for s in opp_shots)

    tl = st.touches
    for n, x in enumerate(tl):
        if in_goal_window(x["t"]):
            continue
        nxt = tl[n + 1] if n + 1 < len(tl) else None
        i = st.i(x["t"])
        # Your challenges as last back.
        if x["key"] == me and nxt and nxt["team"] == opp and nxt["t"] - x["t"] < 0.8:
            bd = st.depth(team, x["ball"][1])
            if st.last_back(team, i) == me and bd < 1500 and team_size(st) > 1:
                shot = led_to_shot(x["t"])
                out.append(Decision(
                    x["t"], clock(x["t"]), "last_man_challenge", "mistake", KIND_LABELS["last_man_challenge"],
                    f"You challenged {st.name[nxt['key']]} {_where(st, team, *x['ball'][:2])} as the last player back "
                    f"and lost it" + (", and they got a shot off." if shot else "."),
                    "Shadow back toward your net instead, and only challenge with a teammate behind you or when they "
                    "make a heavy touch.",
                    "As last man, losing the challenge gives them an open run at your net.",
                    1.8 if shot else 1.1, "Open play", [], st.snapshot(i, me, _shadow_spot(st, team, i), "shadow here")))
        # Double commits on a teammate's touch that lost the ball.
        if x["team"] == team and x["key"] != me and nxt and nxt["team"] == opp and nxt["t"] - x["t"] < 1.5:
            ib = st.i(x["t"] - 0.35)
            if (st.alive[me][ib] and st.dist_ball(me, ib) < 1000 and st.toward_ball(me, ib) > 500
                    and st.depth(team, x["ball"][1]) < 2500 and _was_second(st, me, x["key"], x["t"])):
                shot = led_to_shot(x["t"])
                out.append(Decision(
                    x["t"], clock(x["t"]), "double_commit", "mistake", KIND_LABELS["double_commit"],
                    f"You and {st.name[x['key']]} both went for the same ball and {st.name[nxt['key']]} took it"
                    + (" and got a shot off." if shot else "."),
                    "When a teammate is closer or already going, peel off and cover behind them.",
                    "Two players on one ball means nobody is covering.",
                    1.6 if shot else 1.0, "Open play", [], st.snapshot(ib, me, _cover_spot(st, team, ib), "cover here")))

    # Boost grabs as last back while they had the ball in your half.
    last_grab = -99.0
    for e in rp.modules.get("boost", {}).get("pickup_events", []) or []:
        t = float(e.get("time", 0))
        if pid_key(e.get("player_id")) != me or e.get("pad_type") != "big" or in_goal_window(t) or t - last_grab < 5:
            continue
        i = st.i(t)
        prev = [x for x in tl if x["t"] <= t]
        if (prev and prev[-1]["team"] == opp and st.last_back(team, i) == me and team_size(st) > 1
                and st.depth(team, st.ball[i][1]) < 0 and _boost_left_net(st, me, i)):
            last_grab = t
            shot = led_to_shot(t)
            out.append(Decision(
                t, clock(t), "boost_grab", "mistake", KIND_LABELS["boost_grab"],
                "You took the big boost as the last player back while they had the ball in your half"
                + (" and they got a shot off." if shot else "."),
                "Stay between the ball and your net and collect small pads on the way; get the corner boost once the "
                "ball is in their half.",
                "The corner pad is the furthest point from the play.",
                1.5 if shot else 0.9, "Open play", [], st.snapshot(i, me, _back_post(st, team, st.ball[i][0]), "stay in net")))

    # Your shots.
    saves = [e for e in rp.modules.get("core", {}).get("timeline", []) or [] if e.get("kind") == "Save"]
    goals = [float(g.get("time", 0)) for g in rp.modules.get("core", {}).get("goal_context", []) or []]
    for x in tl:
        if x["key"] != me or "action:shot" not in x["tags"] or any(0 <= gt - x["t"] <= 4 for gt in goals):
            continue
        if math.hypot(x["ball"][0], x["ball"][1] - GOAL_Y * st.fwd(team)) > 3500:
            continue
        i = st.i(x["t"])
        j = min(len(st.t) - 1, i + 2)
        gx, gy = 0.0, GOAL_Y * st.fwd(team)  # their goal
        defenders = [k for k in st.opponents(me) if st.alive[k][i]
                     and math.hypot(st.pos[k][i][0] - gx, st.pos[k][i][1] - gy) < 2500
                     and st.depth(team, st.pos[k][i][1]) > st.depth(team, x["ball"][1])]
        anyone_back = [k for k in st.opponents(me) if st.alive[k][i]
                       and st.depth(team, st.pos[k][i][1]) > st.depth(team, x["ball"][1]) - 300]
        saved = any(s.get("is_team_0") == (opp == 0) and 0 <= s.get("time", 0) - x["t"] <= 4 for s in saves)
        if not anyone_back and not saved:
            out.append(Decision(
                x["t"], clock(x["t"]), "open_net_miss", "mistake", KIND_LABELS["open_net_miss"],
                "Nobody was in their net and the shot missed.",
                "With an open net, slow down and place it: a controlled touch at 70% power toward the middle of the "
                "goal is enough.",
                "Open nets are the cheapest goals in the game; power is what makes them miss.",
                1.2, "Open play", [], st.snapshot(i, me)))
        elif saved and defenders:
            vy = st.bvel[j][1]
            if abs(vy) > 300:
                tt = (gy - st.ball[j][1]) / vy
                px = st.ball[j][0] + st.bvel[j][0] * tt if tt > 0 else st.ball[j][0]
                keeper = min(defenders, key=lambda k: abs(st.pos[k][i][1] - gy))
                if abs(px - st.pos[keeper][i][0]) < 600:
                    out.append(Decision(
                        x["t"], clock(x["t"]), "shot_at_keeper", "mistake", KIND_LABELS["shot_at_keeper"],
                        f"Your shot went almost straight at {st.name[keeper]}, who saved it.",
                        "Aim for the corner away from the defender, or pass or fake when they're set in net.",
                        "A defender sitting in net saves anything aimed at them; placement beats power.",
                        0.9, "Open play", [], st.snapshot(i, me)))

    # Good decisions: your goals and saves.
    for g in rp.modules.get("core", {}).get("goal_context", []) or []:
        if g_scorer(g) == me and g.get("scoring_team_is_team_0") == (team == 0):
            t = float(g.get("time", 0))
            out.append(Decision(t, clock(t), "goal", "good", "You scored",
                                "Rewatch the touch before it: that's the setup worth repeating.", "", "", 0.5,
                                "Good play", [], st.snapshot(st.i(t - 0.5), me)))
    for s in saves:
        if pid_key(s.get("player_id")) == me:
            t = float(s.get("time", 0))
            out.append(Decision(t, clock(t), "save", "good", "You made a save",
                                "You were in position for it.", "", "", 0.4, "Good play", [], st.snapshot(st.i(t), me)))
    return out


# ---------------------------------------------------------------------------- entry point


def _assign_blame(st: State, per_player: dict[str, Decision]) -> None:
    """One goal, every defender's chain: the biggest mistake on the team is the root cause.

    Other defenders keep a mistake only if theirs was nearly as big; otherwise their card
    says the goal was not on them and names what was.
    """
    scored = {k: max((s["weight"] for s in d.steps if s["kind"] != "not_on_you"), default=0.0)
              for k, d in per_player.items() if d.verdict == "mistake"}
    if not scored:
        return
    ranked = sorted(scored, key=lambda k: (-scored[k], per_player[k].time))
    owner = ranked[0]
    od = per_player[owner]
    # At most one other player shares it, and only with a mistake about as big as the owner's.
    shared = ranked[1] if len(ranked) > 1 and scored[ranked[1]] >= 2.4 and scored[ranked[1]] >= scored[owner] - 0.3 else None
    for k, d in per_player.items():
        if k == owner or d.verdict != "mistake":
            if k != owner and d.verdict == "ok":
                d.happened += f" The root cause was {st.name[owner]}: {od.title.lower()} at {od.clock}."
            continue
        if k == shared:
            d.context += f" (shared with {st.name[owner]})"
            continue
        d.verdict, d.weight = "ok", 1.0
        d.should = (f"Smaller thing to tidy up: {d.should[0].lower() + d.should[1:]}" if d.should else d.should)
        d.happened = (f"The root cause was {st.name[owner]}: {od.title.lower()} at {od.clock}. "
                      f"On your side: {d.happened[0].lower() + d.happened[1:]}")
        d.kind, d.title = "not_on_you", "Mostly not on you"


def review_player(st: State, me: str, goal_cards: dict | None = None) -> dict:
    team = st.team[me]
    against = [g for g in _goals(st.replay) if g.get("scoring_team_is_team_0") != (team == 0)]
    decisions = [goal_cards[(id(g), me)] if goal_cards else _goal_against(st, g, me) for g in against]
    windows = [(st.segment_start(float(g.get("time", 0)) - 0.5), float(g.get("time", 0))) for g in against]
    rest = _open_play(st, me, windows)
    mistakes = sorted([d for d in rest if d.verdict == "mistake"], key=lambda d: -d.weight)[:10]
    goods = [d for d in rest if d.verdict == "good"][:4]
    decisions += mistakes + goods
    decisions.sort(key=lambda d: d.time)

    # Habits: the mistakes that were yours, counting each goal's root cause and open-play flags.
    habits: dict[str, dict] = {}
    for d in decisions:
        if d.verdict != "mistake":
            continue
        h = habits.setdefault(d.kind, {"kind": d.kind, "label": KIND_LABELS[d.kind], "count": 0, "goals": 0,
                                       "weight": 0.0, "should": d.should})
        h["count"] += 1
        h["weight"] = round(h["weight"] + d.weight, 2)
        h["goals"] += 1 if d.context.startswith("Goal against") else 0
    habit_list = sorted(habits.values(), key=lambda h: (-h["goals"], -h["weight"]))
    on_you = sum(1 for d in decisions if d.context.startswith("Goal against") and d.verdict == "mistake")
    return {
        "decisions": [asdict(d) for d in decisions],
        "habits": habit_list,
        "goals_against": len(against),
        "goals_on_you": on_you,
    }


def _goals(replay: Replay) -> list[dict]:
    """Goal entries, dropping the occasional duplicate the parser reports a moment after a goal."""
    out = []
    for g in sorted(replay.modules.get("core", {}).get("goal_context", []) or [], key=lambda g: g.get("time", 0)):
        if out and g.get("time", 0) - out[-1].get("time", 0) < 3:
            continue
        out.append(g)
    return out


def review(path: str, replay: Replay) -> dict:
    """Decision review for every human player in the replay, keyed like replay.players."""
    st = State(path, replay)
    humans = [k for k, p in replay.players.items() if not p.is_bot and k in st.team]
    cards: dict = {}
    for g in _goals(replay):
        defending = 1 if g.get("scoring_team_is_team_0") else 0
        per = {}
        for k in st.team:
            if st.team[k] == defending:
                try:
                    per[k] = _goal_against(st, g, k)
                except Exception:
                    continue
        _assign_blame(st, per)
        for k, d in per.items():
            cards[(id(g), k)] = d
    out = {}
    for key, p in replay.players.items():
        if key not in humans:
            continue
        try:
            out[key] = review_player(st, key, cards)
        except Exception as e:  # one odd player should not sink the whole replay
            out[key] = {"decisions": [], "habits": [], "goals_against": 0, "goals_on_you": 0, "error": str(e)}
    return out
