"""Grade mechanics from a live stream of car physics and controller inputs.

Frames come from the BakkesMod plugin (offline / free play only), roughly 60-120
per second:

    {"t": 12.34,                       # game time in seconds
     "car": {"loc": [x, y, z], "vel": [x, y, z], "rot": [pitch, yaw, roll],  # radians
             "boost": 0-100, "ground": bool, "dodging": bool,
             "hasflip": bool, "wheels": 0-4, "team": 0/1, "touches": n},   # newer plugins only
     "in": {"thr", "steer", "pitch", "yaw", "roll": -1..1, "jump", "boost", "hb": 0/1},
     "ball": {"loc": [x, y, z], "vel": [x, y, z]} or null}

Every time the car leaves a surface and lands again we look at what happened
in the air (jumps, dodges, flip cancels, air roll, ball touches) and decide
whether it was a half flip, speed flip, wavedash, zap dash, fast aerial, flip
reset, air dribble or ceiling shot attempt. Each attempt gets a 0-100 score, a
letter, and the single most useful tip.

"touches" is a running count of the car's ball hits. Older plugins don't send
it, so a touch is then inferred from the ball's velocity jumping while the
car is next to it. "hasflip" is how a flip reset is confirmed; without it a
reset is assumed when all four wheels face the ball at the touch.

Directions are worked out from the car's own velocity change and from the
stick being reversed, not from input sign conventions, so the grading does not
depend on how a controller is bound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SUPERSONIC = 2200.0
CEILING_Z = 1900.0  # the ceiling is at 2044; a car driving on it sits a little lower
GOAL_Y = 5120.0
GOAL_HALF_WIDTH = 893.0


def _speed(v) -> float:
    return math.hypot(v[0], v[1], v[2])


def _flat_speed(v) -> float:
    return math.hypot(v[0], v[1])


def _axes(rot):
    """Forward, right and up unit vectors from (pitch, yaw, roll) in radians (Unreal convention)."""
    p, y, r = rot
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    fwd = (cp * cy, cp * sy, sp)
    right = (sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp)
    up = (-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp)
    return fwd, right, up


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unit(v):
    n = math.sqrt(_dot(v, v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _on_target(ball_vel, ball_loc, team: int) -> bool:
    """Is the ball heading at the opponent's goal mouth? (Straight line, ignoring gravity and bounces.)"""
    sign = 1 if team == 0 else -1  # blue attacks +y
    vy = ball_vel[1] * sign
    if vy <= 300:
        return False
    t = (GOAL_Y - ball_loc[1] * sign) / vy
    return t >= 0 and abs(ball_loc[0] + ball_vel[0] * t) < GOAL_HALF_WIDTH + 100


def _yaw_diff(a: float, b: float) -> float:
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return abs(math.degrees(d))


def _grade(score: float) -> str:
    return "S" if score >= 92 else "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 45 else "D"


@dataclass
class Attempt:
    kind: str  # "half_flip", "speed_flip", "wavedash", "fast_aerial", "landing"
    time: float
    score: int
    grade: str
    tip: str
    details: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


KIND_LABELS = {
    "half_flip": "Half flip",
    "speed_flip": "Speed flip",
    "wavedash": "Wavedash",
    "zap_dash": "Zap dash",
    "fast_aerial": "Fast aerial",
    "flip_reset": "Flip reset",
    "air_dribble": "Air dribble",
    "ceiling_shot": "Ceiling shot",
    "landing": "Landing",
}


class _Penalties:
    """Collects score deductions; the biggest one becomes the tip."""

    def __init__(self):
        self.items: list[tuple[float, str]] = []

    def add(self, points: float, tip: str):
        if points > 0:
            self.items.append((points, tip))

    def result(self, good_tip: str) -> tuple[int, str]:
        score = max(0, round(100 - sum(p for p, _ in self.items)))
        tip = max(self.items)[1] if self.items else good_tip
        return score, tip


@dataclass
class _Air:
    """Everything that happened during one stretch in the air."""

    takeoff: dict
    frames: list = field(default_factory=list)
    jump_presses: list = field(default_factory=list)  # (time, frame) of each rising edge of jump
    jump_release: float | None = None  # first release after the first press
    jumped: bool = False  # left the ground with a jump (rather than driving off a ramp or wall)
    touches: list = field(default_factory=list)  # frames where the car hit the ball
    resets: list = field(default_factory=list)  # times the flip came back mid-air (hasflip false -> true)


class MechanicsTracker:
    def __init__(self):
        self.prev: dict | None = None
        self.air: _Air | None = None
        self.last_ground: dict | None = None
        self.history: list[Attempt] = []

    # -------------------------------------------------------------- input
    def feed(self, f: dict) -> list[Attempt]:
        out: list[Attempt] = []
        prev = self.prev
        car, inp = f["car"], f["in"]
        if prev is not None and f["t"] < prev["t"]:  # time went backwards: reset (e.g. reset shot)
            self.air, prev = None, None
        jump_edge = bool(inp.get("jump")) and not (prev and prev["in"].get("jump"))
        touched = prev is not None and _touched(prev, f)

        if _on_surface(car):
            if self.air is not None:
                a = self.air
                a.frames.append(f)
                if touched:
                    a.touches.append(f)
                out += self._finish(a, f)
                self.air = None
            self.last_ground = f
            if jump_edge:  # the jump press that will lift us off
                self._pending_press = f
        else:
            if self.air is None:
                self.air = _Air(takeoff=self.last_ground or f)
                pending = getattr(self, "_pending_press", None)
                if pending is not None and f["t"] - pending["t"] < 0.25:
                    self.air.jump_presses.append(pending)
                    self.air.jumped = True
                self._pending_press = None
            a = self.air
            if touched:
                a.touches.append(f)
            if car.get("hasflip") and len(a.frames) and a.frames[-1]["car"].get("hasflip") is False:
                a.resets.append(f["t"])
            a.frames.append(f)
            if jump_edge:
                a.jump_presses.append(f)
            if a.jump_presses and a.jump_release is None and not inp.get("jump") and len(a.jump_presses) == 1:
                a.jump_release = f["t"]
        self.prev = f
        self.history += out
        return out

    # -------------------------------------------------------------- analysis of one air stretch
    def _finish(self, a: _Air, land: dict) -> list[Attempt]:
        t0 = a.takeoff["t"]
        airtime = land["t"] - t0
        if airtime < 0.05 or not a.frames:
            return []
        dodge = self._find_dodge(a)
        up_z = _axes(land["car"]["rot"])[2][2]
        clean = up_z > 0.7

        # Ball mechanics first: they can contain dodges that would otherwise look like flips.
        if self._from_ceiling(a) and (a.touches or dodge is not None):
            return [self._ceiling_shot(a, land, dodge)]
        reset_try = self._reset_touch(a)
        # A flip only counts as reset by the ball if a touch is right next to it (a wall brush also gives it back).
        a.resets = [t for t in a.resets if any(abs(f["t"] - t) <= 0.2 for f in a.touches)]
        if a.resets or reset_try is not None:
            return [self._flip_reset(a, land, reset_try)]
        air_touches = [f for f in a.touches if not _on_surface(f["car"]) and f["ball"]["loc"][2] > 200]
        if len(air_touches) >= 2:
            return [self._air_dribble(a, land, air_touches)]

        if dodge is not None:
            if (a.jumped and airtime < 0.45 and land["t"] - dodge["t"] <= 0.15 and dodge["kind"] != "back"
                    and max(f["car"]["loc"][2] for f in a.frames) < 130):
                return [self._zap_dash(a, land, dodge, clean)]
            kind = dodge["kind"]
            if kind == "back" and (dodge["cancel"] is not None or self._moving_backwards(a.takeoff)):
                return [self._half_flip(a, land, dodge, clean)]
            if kind == "diagonal" and (dodge["boost_frac"] > 0.6 or dodge["cancel"] is not None):
                return [self._speed_flip(a, land, dodge, clean)]
            if land["t"] - dodge["t"] <= 0.25 and dodge["z"] < 160:
                return [self._wavedash(a, land, dodge, clean)]
        elif a.jumped and len(a.jump_presses) >= 2 and self._fast_aerial_like(a):
            return [self._fast_aerial(a, land, clean)]

        if airtime > 0.8 and not clean:
            return [Attempt("landing", land["t"], 30, "D",
                            "Landed on your side or roof. Use air roll to get your wheels down before you land.",
                            {"airtime": round(airtime, 2), "up": round(up_z, 2)})]
        if airtime > 0.8:
            return [Attempt("landing", land["t"], 100, "A", "Clean landing on all four wheels.",
                            {"airtime": round(airtime, 2), "up": round(up_z, 2)})]
        return []

    @staticmethod
    def _moving_backwards(f: dict) -> bool:
        fwd = _axes(f["car"]["rot"])[0]
        return _dot(fwd, f["car"]["vel"]) < -300

    def _find_dodge(self, a: _Air) -> dict | None:
        """The second jump press in the air, if it was a dodge (stick held) rather than a double jump."""
        presses = a.jump_presses[1:] if a.jumped else a.jump_presses
        if not presses:
            return None
        press = presses[0]
        sp, ss = press["in"].get("pitch", 0), max(press["in"].get("yaw", 0), press["in"].get("steer", 0), key=abs)
        stick = math.hypot(sp, ss)
        if stick < 0.5 and not press["car"].get("dodging"):
            return None
        # Which way did the dodge push the car? Compare velocity 0.15 s later in the car's frame.
        later = next((f for f in a.frames if f["t"] >= press["t"] + 0.15), a.frames[-1])
        fwd, right, _ = _axes(press["car"]["rot"])
        fwd_flat = (fwd[0], fwd[1], 0.0)
        dv = [later["car"]["vel"][i] - press["car"]["vel"][i] for i in range(3)]
        dv_f, dv_r = _dot(dv, fwd_flat), _dot(dv, right)
        if abs(sp) > 0.3 and abs(ss) > 0.3:
            kind = "diagonal"
        elif abs(dv_f) >= abs(dv_r):
            kind = "front" if dv_f > 0 else "back"
        else:
            kind = "side"
        # Flip cancel: stick pitch pushed the other way soon after the dodge.
        cancel = None
        if abs(sp) > 0.3:
            for f in a.frames:
                if press["t"] < f["t"] <= press["t"] + 0.6 and f["in"].get("pitch", 0) * sp < -0.25:
                    cancel = f["t"]
                    break
        after = [f for f in a.frames if f["t"] >= press["t"]]
        rolled = max((abs(f["in"].get("roll", 0)) for f in after), default=0) > 0.5 or \
            max((abs(f["in"].get("yaw", 0)) for f in after if abs(f["in"].get("pitch", 0)) < 0.5), default=0) > 0.7
        frames_air = [f for f in a.frames if f["t"] <= press["t"]] or a.frames
        boost_frac = sum(1 for f in a.frames if f["in"].get("boost")) / len(a.frames)
        return {
            "t": press["t"], "kind": kind, "cancel": cancel, "rolled": rolled, "z": press["car"]["loc"][2],
            "speed_before": _flat_speed(frames_air[-1]["car"]["vel"]), "boost_frac": boost_frac,
            "first_to_dodge": press["t"] - a.jump_presses[0]["t"] if a.jumped else 0.0,
        }

    def _fast_aerial_like(self, a: _Air) -> bool:
        first, second = a.jump_presses[0], a.jump_presses[1]
        boosting = sum(1 for f in a.frames if f["in"].get("boost")) / len(a.frames) > 0.6
        return boosting and second["t"] - first["t"] < 0.6 and max(f["car"]["loc"][2] for f in a.frames) > 300

    # -------------------------------------------------------------- graders
    def _half_flip(self, a, land, d, clean) -> Attempt:
        pen = _Penalties()
        turn = _yaw_diff(a.takeoff["car"]["rot"][1], land["car"]["rot"][1])
        if d["cancel"] is None:
            pen.add(40, "You did not cancel the flip. Right after you flip backwards, push the stick forward and hold it.")
            cancel_delay = None
        else:
            cancel_delay = d["cancel"] - d["t"]
            if cancel_delay > 0.12:
                pen.add(min(35, (cancel_delay - 0.12) * 150),
                        f"Cancel sooner: you pushed forward {cancel_delay:.2f}s after the flip; aim for under 0.12s.")
        if turn < 150:
            pen.add(min(35, (150 - turn) / 3),
                    f"You only turned {turn:.0f}°. Hold air roll (or steer) longer after the cancel to finish the 180.")
        if not d["rolled"]:
            pen.add(15, "Air roll after the cancel so you land wheels-down facing the other way.")
        if not clean:
            pen.add(25, "You landed on your side or roof. Keep air rolling until your wheels are flat before you land.")
        dur = land["t"] - d["t"]
        if dur > 1.1:
            pen.add(min(15, (dur - 1.1) * 30), f"The half flip took {dur:.2f}s. Jump lower: tap jump instead of holding it.")
        exit_speed = _flat_speed(land["car"]["vel"])
        if exit_speed < 900:
            pen.add(10, "Land with more speed: boost or keep the stick forward through the flip.")
        score, tip = pen.result("Clean half flip. Now try it straight into boost to build speed.")
        return Attempt("half_flip", land["t"], score, _grade(score), tip, {
            "turn_deg": round(turn), "cancel_s": None if cancel_delay is None else round(cancel_delay, 3),
            "air_roll": d["rolled"], "clean_landing": clean, "time_s": round(dur, 2), "exit_speed": round(exit_speed),
        })

    def _speed_flip(self, a, land, d, clean) -> Attempt:
        pen = _Penalties()
        exit_speed = _flat_speed(land["car"]["vel"])
        if d["cancel"] is None:
            pen.add(35, "Cancel the diagonal flip: pull the stick straight back right after you dodge.")
            cancel_delay = None
        else:
            cancel_delay = d["cancel"] - d["t"]
            if cancel_delay > 0.08:
                pen.add(min(30, (cancel_delay - 0.08) * 200),
                        f"Cancel faster: {cancel_delay:.2f}s after the dodge; the best speed flips cancel almost instantly.")
        if d["first_to_dodge"] > 0.15:
            pen.add(min(15, (d["first_to_dodge"] - 0.15) * 60),
                    f"Dodge sooner after the first jump ({d['first_to_dodge']:.2f}s); a quick tap-tap keeps you low.")
        if d["boost_frac"] < 0.9:
            pen.add(15, "Hold boost the whole time, from before the jump until you land.")
        if not clean:
            pen.add(25, "You landed tilted. Air roll the other way during the cancel so you land flat.")
        if exit_speed < 1800:
            pen.add(min(25, (1800 - exit_speed) / 30),
                    f"Landed at {exit_speed:.0f} speed; a clean speed flip lands near supersonic (2000+).")
        score, tip = pen.result("Clean speed flip. Try it from each kickoff spawn.")
        return Attempt("speed_flip", land["t"], score, _grade(score), tip, {
            "exit_speed": round(exit_speed), "cancel_s": None if cancel_delay is None else round(cancel_delay, 3),
            "jump_to_dodge_s": round(d["first_to_dodge"], 3), "boost_held": round(d["boost_frac"] * 100),
            "clean_landing": clean,
        })

    def _wavedash(self, a, land, d, clean) -> Attempt:
        pen = _Penalties()
        before = d["speed_before"]
        after = _flat_speed(land["car"]["vel"])
        gain = after - before
        gap = land["t"] - d["t"]
        if gap > 0.1:
            pen.add(min(35, (gap - 0.1) * 250), f"Dodge later: you dodged {gap:.2f}s before landing; aim for the last 0.1s.")
        if gain < 400:
            pen.add(min(40, (400 - gain) / 8),
                    f"Only +{max(gain, 0):.0f} speed. Tilt the nose up slightly so the back wheels touch first, then dodge.")
        if not clean:
            pen.add(25, "You landed tilted. Keep the car flat apart from a slight nose-up tilt.")
        score, tip = pen.result("Good wavedash. Chain it after landing from a jump to keep momentum.")
        return Attempt("wavedash", land["t"], score, _grade(score), tip, {
            "speed_gain": round(gain), "dodge_before_landing_s": round(gap, 3), "clean_landing": clean,
        })

    def _fast_aerial(self, a, land, clean) -> Attempt:
        pen = _Penalties()
        first, second = a.jump_presses[0], a.jump_presses[1]
        hold = (a.jump_release or second["t"]) - first["t"]
        gap = second["t"] - (a.jump_release or second["t"])
        boost_start = next((f["t"] for f in [a.takeoff] + a.frames if f["in"].get("boost")), None)
        boost_delay = None if boost_start is None else max(0.0, boost_start - first["t"])
        t1 = first["t"] + 1.0
        h1 = next((f["car"]["loc"][2] for f in a.frames if f["t"] >= t1), max(f["car"]["loc"][2] for f in a.frames))
        if hold < 0.12:
            pen.add(min(25, (0.12 - hold) * 250), f"Hold the first jump longer ({hold:.2f}s); about 0.15–0.2s gives the most height.")
        if gap > 0.1:
            pen.add(min(30, (gap - 0.1) * 200), f"Double jump sooner after releasing ({gap:.2f}s gap); it should feel like one motion.")
        if boost_delay is None or boost_delay > 0.1:
            pen.add(20, "Start boosting as you jump, not after.")
        if h1 < 800:
            pen.add(min(25, (800 - h1) / 20), f"You reached {h1:.0f} height after 1s. Tilt back further during the first jump.")
        score, tip = pen.result("Fast aerial looks clean. Now aim it at a ball in training.")
        return Attempt("fast_aerial", land["t"], score, _grade(score), tip, {
            "first_jump_hold_s": round(hold, 3), "double_jump_gap_s": round(gap, 3),
            "boost_delay_s": None if boost_delay is None else round(boost_delay, 3), "height_after_1s": round(h1),
        })

    # -------------------------------------------------------------- ball mechanics
    @staticmethod
    def _from_ceiling(a: _Air) -> bool:
        c = a.takeoff["car"]
        return c["loc"][2] > CEILING_Z and _axes(c["rot"])[2][2] < -0.5

    @staticmethod
    def _reset_touch(a: _Air) -> dict | None:
        """The airborne touch where the wheels faced the ball most squarely, if any came close to a reset."""
        best, best_dot = None, 0.75  # below this it's a nose or roof touch, not a reset try
        for f in a.touches:
            c, b = f["car"], f["ball"]
            if c["loc"][2] < 250 or b["loc"][2] < 250 or _on_surface(c):
                continue
            up = _axes(c["rot"])[2]
            d = -_dot(up, _unit(_sub(b["loc"], c["loc"])))  # the wheels point along -up
            if d > best_dot:
                best, best_dot = {"frame": f, "facing": d}, d
        return best

    def _shot(self, a: _Air, land: dict, after: float) -> dict:
        """What the last touch at or after `after` did to the ball."""
        hits = [f for f in a.touches if f["t"] >= after]
        if not hits:
            return {"hit": False, "speed": 0, "on_target": False}
        last = hits[-1]
        later = next((f for f in a.frames if f["t"] >= last["t"] + 0.05 and f.get("ball")), last)
        b = later.get("ball")
        if not b:
            return {"hit": True, "speed": 0, "on_target": False}
        return {"hit": True, "speed": _speed(b["vel"]),
                "on_target": _on_target(b["vel"], b["loc"], int(last["car"].get("team", 0)))}

    def _dodges_after(self, a: _Air, t: float) -> list[dict]:
        return [f for f in a.jump_presses if f["t"] > t]

    def _flip_reset(self, a, land, reset_try) -> Attempt:
        pen = _Penalties()
        has_flip_data = any("hasflip" in f["car"] for f in a.frames)
        got = bool(a.resets) or (not has_flip_data and reset_try is not None and reset_try["facing"] > 0.9)
        facing = reset_try["facing"] if reset_try else None
        t_reset = a.resets[0] if a.resets else (reset_try["frame"]["t"] if reset_try else a.frames[0]["t"])
        if not got:
            off = math.degrees(math.acos(max(-1.0, min(1.0, facing)))) if facing is not None else None
            pen.add(60, "No reset: all four wheels have to touch the ball. Point your wheels straight at the "
                        f"centre of the ball{f' (you were {off:.0f}° off)' if off is not None else ''} and touch it softly.")
            used = shot = None
        else:
            dodges = self._dodges_after(a, t_reset + 0.02)
            used = dodges[0]["t"] - t_reset if dodges else None
            if used is None:
                pen.add(35, "You got the reset but didn't use it. Flip into the ball before you land.")
                shot = self._shot(a, land, t_reset + 0.05)
            else:
                shot = self._shot(a, land, dodges[0]["t"])
                if not shot["hit"]:
                    pen.add(30, "You used the reset flip but missed the ball. Line up under the ball and flip when it is "
                                "right in front of your nose.")
                elif shot["speed"] < 1500:
                    pen.add(min(20, (1500 - shot["speed"]) / 40),
                            f"Weak finish ({shot['speed']:.0f} ball speed). Boost into the ball and flip as you reach it.")
                if shot["hit"] and not shot["on_target"]:
                    pen.add(15, "The shot wasn't on target. Turn the car to face the goal before you flip.")
        score, tip = pen.result("Clean flip reset and shot. Try it from a higher setup next.")
        return Attempt("flip_reset", land["t"], score, _grade(score), tip, {
            "reset": got, "wheels_facing_ball": None if facing is None else round(facing, 2),
            "used_flip_after_s": None if not got or used is None else round(used, 2),
            "shot_speed": round(shot["speed"]) if shot and shot["hit"] else None,
            "on_target": shot["on_target"] if shot and shot["hit"] else None,
            "confirmed_by_game": bool(a.resets),
        })

    def _air_dribble(self, a, land, touches) -> Attempt:
        pen = _Penalties()
        n = len(touches)
        span = touches[-1]["t"] - touches[0]["t"]
        between = [f for f in a.frames if touches[0]["t"] <= f["t"] <= touches[-1]["t"] and f.get("ball")]
        gap = sum(_speed(_sub(f["ball"]["loc"], f["car"]["loc"])) for f in between) / max(1, len(between))
        ran_out = any(f["car"].get("boost", 100) <= 0 for f in between)
        if n < 3:
            pen.add(15 * (3 - n), "Only two touches. Keep the ball on your nose with short boost taps instead of "
                                  "holding boost.")
        if gap > 280:
            pen.add(min(30, (gap - 280) / 6),
                    f"The ball got away from you (average {gap:.0f} away). Touch it softer and stay under it.")
        if span < 1.0:
            pen.add(min(20, (1.0 - span) * 25), f"You only carried it for {span:.1f}s. Feather boost so you match "
                                                "the ball's speed rather than hitting it.")
        if ran_out:
            pen.add(15, "You ran out of boost mid-dribble. Start with 60+ boost and feather it.")
        shot = self._shot(a, land, touches[-1]["t"])
        if shot["speed"] < 1800:
            pen.add(min(15, (1800 - shot["speed"]) / 60),
                    "Finish harder: flip or boost into the last touch.")
        if not shot["on_target"]:
            pen.add(15, "The last touch wasn't heading at the goal. Steer the car toward the net while you carry it.")
        score, tip = pen.result("Controlled air dribble. Try finishing it with a flip reset.")
        return Attempt("air_dribble", land["t"], score, _grade(score), tip, {
            "touches": n, "carry_s": round(span, 2), "avg_ball_distance": round(gap),
            "finish_speed": round(shot["speed"]), "on_target": shot["on_target"],
        })

    def _ceiling_shot(self, a, land, dodge) -> Attempt:
        pen = _Penalties()
        t0 = a.frames[0]["t"]
        if a.jumped:
            pen.add(10, "Drop off the ceiling instead of jumping: falling off keeps your flip with no time limit.")
        shot = self._shot(a, land, t0)
        first_touch = a.touches[0]["t"] if a.touches else None
        timing = None
        if dodge is None:
            pen.add(30, "You kept your flip but didn't use it. Flip into the ball as you reach it.")
        elif first_touch is None:
            pen.add(45, "You flipped but missed the ball. Leave the ceiling a little later so you meet the ball "
                        "on its way down.")
        else:
            timing = first_touch - dodge["t"]
            if timing < -0.05:
                pen.add(25, "You touched the ball before flipping. Flip a moment earlier so the flip hits the ball.")
            elif timing > 0.35:
                pen.add(min(25, (timing - 0.35) * 60),
                        f"You flipped {timing:.2f}s before reaching the ball. Wait until it's right in front of you.")
        if shot["hit"]:
            if shot["speed"] < 1800:
                pen.add(min(20, (1800 - shot["speed"]) / 50),
                        f"Weak shot ({shot['speed']:.0f}). Boost as you fall and flip straight through the ball.")
            if not shot["on_target"]:
                pen.add(15, "The shot wasn't on target. Face the goal before you drop off the ceiling.")
        score, tip = pen.result("Clean ceiling shot. Now try it off a pass or a wall read.")
        return Attempt("ceiling_shot", land["t"], score, _grade(score), tip, {
            "jumped_off": a.jumped, "flip_used": dodge is not None,
            "flip_to_touch_s": None if timing is None else round(timing, 2),
            "shot_speed": round(shot["speed"]) if shot["hit"] else None,
            "on_target": shot["on_target"] if shot["hit"] else None,
        })

    def _zap_dash(self, a, land, d, clean) -> Attempt:
        pen = _Penalties()
        before = _flat_speed(a.takeoff["car"]["vel"])
        after = _flat_speed(land["car"]["vel"])
        gain = after - before
        airtime = land["t"] - a.frames[0]["t"]
        if gain < 500:
            pen.add(min(40, (500 - gain) / 8),
                    f"Only +{max(gain, 0):.0f} speed. Tilt the nose up right after the jump so the back wheels "
                    "touch as you dodge.")
        if airtime > 0.3:
            pen.add(min(25, (airtime - 0.3) * 150),
                    f"Took {airtime:.2f}s in the air. Tap jump and dodge almost straight away; a zap dash is about 0.2s.")
        if not clean:
            pen.add(25, "You landed tilted. Keep the car flat apart from a slight nose-up tilt.")
        score, tip = pen.result("Clean zap dash. Chain a few in a row to stay at top speed.")
        return Attempt("zap_dash", land["t"], score, _grade(score), tip, {
            "speed_gain": round(gain), "air_time_s": round(airtime, 3), "clean_landing": clean,
        })


def _on_surface(car: dict) -> bool:
    """Wheels down on the floor, a wall or the ceiling."""
    return bool(car.get("ground")) or car.get("wheels", 0) >= 3


def _touched(prev: dict, f: dict) -> bool:
    if "touches" in f["car"] and "touches" in prev["car"]:
        return f["car"]["touches"] > prev["car"]["touches"]
    b0, b1 = prev.get("ball"), f.get("ball")
    if not b0 or not b1:
        return False
    close = _speed(_sub(b1["loc"], f["car"]["loc"])) < 260
    return close and _speed(_sub(b1["vel"], b0["vel"])) > 300


def summary(history: list[Attempt]) -> dict:
    """Per-mechanic counts, averages and the latest tip."""
    out: dict = {}
    for a in history:
        s = out.setdefault(a.kind, {"label": a.label, "count": 0, "total": 0, "best": 0, "recent": []})
        s["count"] += 1
        s["total"] += a.score
        s["best"] = max(s["best"], a.score)
        s["recent"] = (s["recent"] + [a.score])[-20:]
    for s in out.values():
        s["avg"] = round(s.pop("total") / s["count"])
    return out
