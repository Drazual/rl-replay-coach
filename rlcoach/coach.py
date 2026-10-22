"""Turn a player's numbers into prioritised, plain-language coaching.

Each rule looks at the player's numbers, the rest of the lobby (players in the
same match are roughly the same skill, which makes them the fairest yardstick),
and a few absolute guardrails. A rule that fires produces a Finding with a
priority score; the highest scores become the player's focus areas.

The thresholds are coaching heuristics, not rank data. They are kept in one
place (THRESHOLDS) so they are easy to tune.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from .metrics import METRIC_BY_KEY, METRICS, lobby_metrics
from .parse import Replay, pid_key

THRESHOLDS = {
    "zero_boost_pct": 15,
    "full_boost_pct": 15,
    "overfill_per_big": 30,
    "small_pad_share": 55,
    "supersonic_boost_pct": 15,
    "behind_ball_pct": 55,
    "role_skew": 12,  # % points away from an even share of first-man time
    "whiffs_p5": 2.5,
    "beaten_p5": 6,
    "fifty_win_pct": 35,
    "kickoff_win_pct": 34,
    "shooting_pct": 20,
    "demos_taken": 3,
}


@dataclass
class Finding:
    key: str
    area: str
    title: str
    evidence: str
    why: str
    fix: list[str]
    score: float  # higher = more important


@dataclass
class Moment:
    time: float
    clock: str
    kind: str  # "conceded", "whiff", "beaten", "fifty", "goal"
    text: str
    good: bool = False


@dataclass
class Report:
    replay: Replay
    player_key: str
    metrics: dict
    lobby: dict  # key -> metrics for every player
    findings: list[Finding]
    strengths: list[str]
    moments: list[Moment] = field(default_factory=list)

    @property
    def player(self):
        return self.replay.players[self.player_key]

    @property
    def won(self) -> bool:
        s = self.replay.score
        return s[self.player.team] > s[1 - self.player.team]


def _avg_others(lobby: dict, me: str, key: str) -> float | None:
    vals = [m[key] for k, m in lobby.items() if k != me and m.get(key) is not None]
    return mean(vals) if vals else None


def _f(v, spec=".0f"):
    return "n/a" if v is None else format(v, spec)


def _goals_against(replay: Replay, me: str, team: int) -> list[dict]:
    """Where the player was at the last opponent touch before each goal against."""
    fwd = 1 if team == 0 else -1  # team 0 (blue) attacks +y
    out = []
    for g in replay.modules.get("core", {}).get("goal_context", []) or []:
        if g.get("scoring_team_is_team_0") == (team == 0):
            continue
        last = g.get("scorer_last_touch") or {}
        ball_y = (last.get("ball_position") or {}).get("y")
        for p in last.get("players") or g.get("players") or []:
            if pid_key(p.get("player")) != me:
                continue
            pos = p.get("position")
            out.append({
                "time": g.get("time", 0),
                "demoed": pos is None,
                "upfield_m": None if pos is None or ball_y is None else (pos["y"] - ball_y) * fwd / 100,
                "boost": (p.get("boost_amount") or 0) / 2.55,
                "last_back": bool(p.get("is_most_back")),
            })
    return out


def _rules(replay: Replay, me: str, m: dict, lobby: dict) -> list[Finding]:
    T = THRESHOLDS
    out: list[Finding] = []
    avg = lambda k: _avg_others(lobby, me, k)  # noqa: E731
    n = replay.team_size

    # --- Boost ---------------------------------------------------------------
    z, z_l = m["zero_boost_pct"], avg("zero_boost_pct")
    if z is not None and (z >= T["zero_boost_pct"] or (z_l is not None and z >= 10 and z >= z_l + 5)):
        out.append(Finding(
            "boost_starved", "Boost", "You are running out of boost too often",
            f"{z:.0f}% of the game at 0 boost (lobby average {_f(z_l)}%). "
            f"You spent {_f(m['low_boost_pct'])}% of the game under 25 boost.",
            "With no boost you cannot challenge, save, or rotate back in time, so every empty tank is a window "
            "where your team is effectively a player down.",
            [
                "Route your rotations through small pads. A small pad gives 12 boost and they sit along every "
                "natural path back to goal.",
                "Stop boosting on the ground when you are already near top speed. Feather it in short taps instead.",
                "Drill: 5 minutes of free play where you never let the meter hit 0. Collect only small pads.",
            ],
            1.5 + (z - 10) / 5,
        ))

    full, a = m["full_boost_pct"], m["avg_boost"]
    if full is not None and a is not None and full >= T["full_boost_pct"] and a >= 55:
        out.append(Finding(
            "boost_hoard", "Boost", "You are sitting on full boost instead of using it",
            f"{full:.0f}% of the game at 100 boost, average boost {a:.0f}.",
            "Boost at 100 cannot go any higher, so every pad you touch is wasted and you are probably playing "
            "slower than you could.",
            [
                "When you have 80+, spend it: go fast to the next play, take a shot, or challenge.",
                "Leave big pads for teammates who are low, especially the corner pads during rotation.",
            ],
            1.0 + (full - T["full_boost_pct"]) / 5,
        ))

    of = m["overfill_per_big"]
    if of is not None and of >= T["overfill_per_big"]:
        out.append(Finding(
            "overfill", "Boost", "You take big pads when you are already fairly full",
            f"About {of:.0f} boost wasted every time you grab a 100 pad.",
            "A big pad only gives you what fits in the tank. Grabbing it at 60 boost wastes 60 and often pulls "
            "you out of position.",
            ["Only detour for a big pad below ~40 boost. Otherwise take the small pads on your path."],
            0.8 + (of - T["overfill_per_big"]) / 15,
        ))

    sps = m["small_pad_share"]
    if sps is not None and sps < T["small_pad_share"]:
        out.append(Finding(
            "big_pad_reliance", "Boost", "You rely on big pads instead of small ones",
            f"Only {sps:.0f}% of your pickups were small pads.",
            "Detours to the corner pads take you away from the play. Players who rotate well live on small pads.",
            ["Learn the small pad lines from the back post to the midfield; drive over them on every rotation."],
            0.7 + (T["small_pad_share"] - sps) / 15,
        ))

    ssb = m["supersonic_boost_pct"]
    if ssb is not None and ssb >= T["supersonic_boost_pct"]:
        out.append(Finding(
            "supersonic_waste", "Boost", "You hold boost after you are already at max speed",
            f"{ssb:.0f}% of your boost was used while already supersonic.",
            "Once you are supersonic, boosting more on the ground barely makes you faster. That boost is gone.",
            ["Let go of boost once the trail appears behind your car, and tap it only to keep supersonic."],
            0.6 + (ssb - T["supersonic_boost_pct"]) / 10,
        ))

    # --- Speed ---------------------------------------------------------------
    sp, sp_l = m["avg_speed_kmh"], avg("avg_speed_kmh")
    slow, slow_l = m["slow_pct"], avg("slow_pct")
    if sp is not None and sp_l and (sp <= sp_l * 0.93 or (slow is not None and slow_l is not None and slow >= slow_l + 6)):
        out.append(Finding(
            "slow", "Speed", "You play slower than the rest of the lobby",
            f"Average speed {sp:.0f} km/h vs lobby {sp_l:.0f} km/h; {_f(slow)}% of the game at slow speed "
            f"(lobby {_f(slow_l)}%).",
            "Speed is time. Faster players reach the ball first, rotate back sooner, and get more touches.",
            [
                "Avoid stopping: powerslide to turn, and land wheels-down after aerials so you keep momentum.",
                "Use flips and wavedashes for free speed instead of boost when you are low.",
                "Drill: free play laps around the field, staying above about 50 km/h without boost.",
            ],
            1.0 + (sp_l - sp) / sp_l * 10,
        ))

    # --- Positioning ---------------------------------------------------------
    bb, bb_l = m["behind_ball_pct"], avg("behind_ball_pct")
    if bb is not None and (bb < T["behind_ball_pct"] or (bb_l is not None and bb < bb_l - 8)):
        out.append(Finding(
            "ahead_of_ball", "Positioning", "You spend too long on the wrong side of the ball",
            f"Goal-side of the ball only {bb:.0f}% of the game (lobby {_f(bb_l)}%).",
            "When you are ahead of the ball you cannot defend or challenge it. Any clear goes past you and "
            "your team plays a man down.",
            [
                "After a shot or a challenge, rotate back toward your own back post before you look for the ball.",
                "Before you commit to a challenge, check that a teammate is behind you.",
            ],
            1.4 + ((bb_l or T["behind_ball_pct"]) - bb) / 6,
        ))

    if n > 1:
        fair = 100 / n
        fm, fm_l = m["first_man_pct"], avg("first_man_pct")
        touches, touches_l = m["touches_p5"], avg("touches_p5")
        if fm is not None and fm >= fair + T["role_skew"]:
            out.append(Finding(
                "ball_chase", "Rotation", "You go for the ball too often (ball chasing)",
                f"First man {fm:.0f}% of the time, where an even share in {n}v{n} is about {fair:.0f}%.",
                "If you are always the player closest to the ball, you keep cutting off teammates' rotations and "
                "leave the net open behind you.",
                [
                    "Before you go, ask: is a teammate closer or already going? If yes, fill the space behind them.",
                    "After your touch, rotate out along the side wall instead of chasing the next touch.",
                ],
                1.3 + (fm - fair - T["role_skew"]) / 5,
            ))
        elif fm is not None and fm <= fair - T["role_skew"] and touches is not None and touches_l and touches < touches_l * 0.8:
            out.append(Finding(
                "passive", "Rotation", "You hang back and let the game come to you",
                f"First man only {fm:.0f}% of the time and {touches:.0f} touches per 5 min (lobby {touches_l:.0f}).",
                "Staying back feels safe, but it leaves your teammates outnumbered and lets opponents keep pressure.",
                [
                    "When the ball comes to your side and nobody on your team is closer, go for it with intent.",
                    "Push up to midfield when your team has the ball instead of waiting in net.",
                ],
                1.1 + (fair - fm - T["role_skew"]) / 5,
            ))

    lm = m["conceded_last_man"] or 0
    ga = _goals_against(replay, me, replay.players[me].team)
    ca = max(m["caught_ahead"] or 0, sum(1 for g in ga if (g["upfield_m"] or 0) > 10))
    if lm >= 2 or ca >= 2 or (ga and ca == len(ga)):
        out.append(Finding(
            "last_man", "Defense", "Goals are going in while you are the last defender or caught upfield",
            f"{len(ga)} goals against: you were last back for {lm} and more than 10 m upfield of the ball for {ca}.",
            "The last player back has one job: do not get beaten. Committing to a challenge you cannot win as "
            "last man is the most expensive mistake in the game.",
            [
                "As last back, shadow: drive backwards toward your net matching the attacker's pace, and only "
                "challenge when they make a heavy touch.",
                "Check the 'Moments to rewatch' list below and look at what you were doing two seconds before each goal.",
            ],
            1.2 + 0.6 * max(lm, ca),
        ))

    # --- Ball play -----------------------------------------------------------
    wh = m["whiffs_p5"]
    if wh is not None and (wh >= T["whiffs_p5"] or m["_aerial_whiffs"] >= 3):
        out.append(Finding(
            "whiffs", "Mechanics", "You are missing the ball when you go for it",
            f"{m['_whiffs']} whiffs ({m['_aerial_whiffs']} in the air), {wh:.1f} per 5 minutes.",
            "A whiff is worse than not going: you spend the time and boost, take yourself out of the play, and "
            "the opponent gets a free touch.",
            [
                "Go a little slower into touches you are unsure of; it is easier to hit a ball you arrive at under control.",
                "Drill: custom training, search for 'aerial' or 'ball reading' packs and run them until you hit 7/10.",
            ],
            1.0 + (wh - T["whiffs_p5"]) / 2,
        ))

    bt, bt_l = m["beaten_p5"], avg("beaten_p5")
    if bt is not None and bt >= T["beaten_p5"] and (bt_l is None or bt >= bt_l + 2):
        out.append(Finding(
            "beaten", "Challenges", "Opponents keep getting to the ball before you",
            f"Beaten to the ball {m['_beaten']} times ({bt:.1f} per 5 min, lobby {_f(bt_l, '.1f')}).",
            "If you commit and arrive second, you are out of the play. This usually comes from reacting late or "
            "taking a curved path to the ball.",
            [
                "Start moving as the opponent is about to touch the ball, not after.",
                "If you are clearly going to be second, do not commit: turn and cover instead.",
            ],
            0.9 + (bt - T["beaten_p5"]) / 3,
        ))

    fw = m["fifty_win_pct"]
    if fw is not None and m["_open_fifties"] >= 4 and fw <= T["fifty_win_pct"]:
        out.append(Finding(
            "fifties", "Challenges", "You are losing most 50/50s",
            f"Won {fw:.0f}% of {m['_open_fifties']} open-play 50/50s.",
            "Lost 50/50s send the ball past you toward your own net.",
            [
                "Flip into the ball at the last moment and hit it with the front corner of your car.",
                "Hitting the ball slightly low lifts it over the opponent instead of into them.",
            ],
            0.9 + (T["fifty_win_pct"] - fw) / 20,
        ))

    kw = m["kickoff_win_pct"]
    if kw is not None and m["_kickoffs"] >= 3 and kw <= T["kickoff_win_pct"]:
        appr = m["_kickoff_approaches"]
        used = ", ".join(f"{k.replace('_', ' ')} x{int(v)}" for k, v in appr.items() if v) or "unknown"
        tips = ["Drill: practise the speed flip kickoff in free play from each spawn until you reach the ball "
                "in under 2.4 seconds."]
        if not appr.get("speed_flip"):
            tips.insert(0, "You are not speed flipping on kickoffs. It is the fastest way to the ball and worth learning.")
        out.append(Finding(
            "kickoffs", "Kickoffs", "You are losing kickoffs",
            f"Won {kw:.0f}% of {m['_kickoffs']} kickoffs you took. Approaches seen: {used}.",
            "Every lost kickoff gives the opponent an attack straight away.",
            tips,
            0.8 + (T["kickoff_win_pct"] - kw) / 25,
        ))

    sh = m["shooting_pct"]
    if sh is not None and (m["shots"] or 0) >= 3 and sh < T["shooting_pct"]:
        out.append(Finding(
            "shooting", "Shooting", "Your shots are not going in",
            f"{m['goals']} goals from {m['shots']} shots ({sh:.0f}%).",
            "Shots straight at the keeper rarely score. Placement beats power.",
            [
                "Aim for the far corner, away from the defender.",
                "Drill: custom training, search 'shooting' and work through a pack, focusing on placement.",
            ],
            0.7 + (T["shooting_pct"] - sh) / 20,
        ))

    hh, hh_l = m["hard_hit_pct"], avg("hard_hit_pct")
    if hh is not None and hh_l is not None and m["_touches"] >= 15 and hh < hh_l - 10:
        out.append(Finding(
            "weak_touches", "Ball play", "Your touches are soft",
            f"{hh:.0f}% of your touches were hard hits (lobby {hh_l:.0f}%).",
            "Soft touches give the ball back to the other team. Hard, aimed touches clear danger and create chances.",
            ["Arrive at the ball with speed and flip into it when you want power."],
            0.6 + (hh_l - hh) / 15,
        ))

    dt = m["demos_taken"] or 0
    if dt >= T["demos_taken"]:
        out.append(Finding(
            "demos_taken", "Awareness", "You are getting demolished a lot",
            f"Demolished {dt} times.",
            "Each demo removes you from the play for 3 seconds.",
            ["Check your surroundings when you are slow near an opponent who is supersonic, and jump or turn away."],
            0.6 + (dt - T["demos_taken"]) / 2,
        ))

    if (m["_minutes"] or 0) >= 4 and m["half_flips"] == 0 and m["wavedashes"] == 0:
        out.append(Finding(
            "movement_mechanics", "Mechanics", "Learn the half flip and wavedash",
            "No half flips or wavedashes were detected this game.",
            "Both give you speed without boost and make recoveries much quicker.",
            ["Drill: in free play, drive backwards, jump, backflip, cancel the flip, then air roll to land. "
             "Ten minutes a day for a week."],
            0.4,
        ))
    return out


# Metrics each finding is about, so a weakness is never also listed as a strength.
FINDING_METRICS = {
    "boost_starved": {"zero_boost_pct", "low_boost_pct"},
    "boost_hoard": {"full_boost_pct"},
    "overfill": {"overfill_per_big"},
    "big_pad_reliance": {"small_pad_share"},
    "supersonic_waste": {"supersonic_boost_pct"},
    "slow": {"avg_speed_kmh", "slow_pct", "supersonic_pct"},
    "ahead_of_ball": {"behind_ball_pct"},
    "passive": {"touches_p5"},
    "whiffs": {"whiffs_p5"},
    "beaten": {"beaten_p5"},
    "fifties": {"fifty_win_pct"},
    "kickoffs": {"kickoff_win_pct"},
    "weak_touches": {"hard_hit_pct"},
}


def _strengths(m: dict, lobby: dict, me: str, findings: list[Finding]) -> list[str]:
    flagged = set().union(*(FINDING_METRICS.get(f.key, set()) for f in findings))
    scored = []
    for metric in METRICS:
        if metric.better is None or metric.group == "Results" or metric.key in flagged:
            continue
        v, others = m.get(metric.key), [x[metric.key] for k, x in lobby.items() if k != me and x.get(metric.key) is not None]
        if v is None or not others:
            continue
        o = mean(others)
        if not o:
            continue
        rel = (v - o) / abs(o) if metric.better == "high" else (o - v) / abs(o)
        best = v >= max(others) if metric.better == "high" else v <= min(others)
        if best and rel >= 0.1:
            scored.append((rel, f"{metric.label}: {format(v, metric.fmt)}{metric.unit} vs lobby {format(o, metric.fmt)}{metric.unit}"))
    scored.sort(reverse=True)
    return [s for _, s in scored[:4]]


def _moments(replay: Replay, me: str, team: int) -> list[Moment]:
    out: list[Moment] = []
    clock = replay.clock
    # Team 0 (blue) defends negative y; its own half is y < 0.
    own = (lambda y: y < 0) if team == 0 else (lambda y: y > 0)

    for g in replay.modules.get("core", {}).get("goal_context", []) or []:
        if g.get("scoring_team_is_team_0") == (team == 0) and pid_key(g.get("scorer")) == me:
            t = g.get("time", 0)
            out.append(Moment(t, clock.label(t), "goal", "You scored. Rewatch what set it up so you can repeat it.", True))
    for g in _goals_against(replay, me, team):
        bits = []
        if g["demoed"]:
            bits.append("you were demolished or respawning")
        elif g["upfield_m"] is not None:
            u = g["upfield_m"]
            bits.append(f"you were {abs(u):.0f} m {'upfield of' if u > 0 else 'behind'} the ball")
        bits.append(f"{g['boost']:.0f} boost")
        if g["last_back"]:
            bits.append("last back")
        out.append(Moment(g["time"], clock.label(g["time"]), "conceded", "Goal against: " + ", ".join(bits) + "."))

    for e in replay.events("whiff"):
        if e.get("kind") == "whiff" and pid_key(e.get("player")) == me:
            t = e.get("time", 0)
            where = "in the air" if e.get("aerial") else "on the ground"
            out.append(Moment(t, clock.label(t), "whiff", f"Whiff {where}, missed by {e.get('closest_approach_distance', 0) / 100:.1f} m."))

    beaten = [e for e in replay.events("beaten_to_ball") if pid_key(e.get("player")) == me]
    beaten = [e for e in beaten if own((e.get("player_position") or [0, 0])[1])]
    for e in sorted(beaten, key=lambda e: -(e.get("margin_seconds") or 0))[:4]:
        t = e.get("time", 0)
        out.append(Moment(t, clock.label(t), "beaten",
                          f"Beaten to the ball in your half by {e.get('margin_seconds', 0):.1f}s."))

    side = "team_zero" if team == 0 else "team_one"
    for e in replay.events("fifty_fifty"):
        if e.get("is_kickoff") or pid_key(e.get(f"{side}_player")) != me:
            continue
        won = e.get("winning_team_is_team_0")
        if won is None or won == (team == 0):
            continue
        y = (e.get("midpoint") or [0, 0])[1]
        if own(y):
            t = e.get("start_time", 0)
            out.append(Moment(t, clock.label(t), "fifty", "Lost a 50/50 in your own half."))

    out.sort(key=lambda mo: mo.time)
    return out


# Short advice for when a stat is simply the weakest in the lobby without
# breaking any absolute threshold.
GAP_TIPS = {
    "zero_boost_pct": "Pick up small pads on the way back so you are never empty.",
    "low_boost_pct": "Pick up small pads on the way back so you rarely drop under 25.",
    "small_pad_share": "Drive over small pads on your rotation path instead of detouring for big ones.",
    "overfill_per_big": "Skip big pads when you already have 50+.",
    "supersonic_boost_pct": "Let go of boost once you are supersonic.",
    "stolen_pads": "When you are in the opponent's half, grab their pads to starve them.",
    "avg_speed_kmh": "Keep moving: powerslide turns and flip for speed instead of stopping.",
    "supersonic_pct": "Use boost to reach supersonic on long rotations, then hold it with taps.",
    "slow_pct": "Avoid stopping and turning in place; powerslide and keep momentum.",
    "behind_ball_pct": "Rotate back toward your back post after each touch.",
    "touches_p5": "Look for more chances to play the ball when you are the closest player.",
    "hard_hit_pct": "Flip into the ball when you want power.",
    "whiffs_p5": "Approach uncertain touches a little slower and under control.",
    "beaten_p5": "Read the play earlier; if you will be second, cover instead of committing.",
    "fifty_win_pct": "Flip into 50/50s at the last moment with the front corner of your car.",
    "kickoff_win_pct": "Practise a speed flip kickoff in free play.",
}


def _lobby_gaps(m: dict, lobby: dict, me: str, taken: set[str], limit: int) -> list[Finding]:
    gaps = []
    for metric in METRICS:
        if metric.key not in GAP_TIPS or metric.key in taken:
            continue
        v = m.get(metric.key)
        others = [x[metric.key] for k, x in lobby.items() if k != me and x.get(metric.key) is not None]
        if v is None or not others or not mean(others):
            continue
        o = mean(others)
        rel = (o - v) / abs(o) if metric.better == "high" else (v - o) / abs(o)
        floor = 5 if metric.unit == "%" else 1
        if rel >= 0.2 and abs(v - o) >= floor:
            gaps.append((rel, metric, v, o))
    gaps.sort(key=lambda g: -g[0])
    return [
        Finding(
            f"gap_{metric.key}", metric.group, f"{metric.label} was your weakest stat vs the lobby",
            f"{format(v, metric.fmt)}{metric.unit} vs a lobby average of {format(o, metric.fmt)}{metric.unit}.",
            "Not a big problem on its own, but it is where the other players in this match did better than you.",
            [GAP_TIPS[metric.key]],
            0.3 + min(rel, 1.0) * 0.3,
        )
        for rel, metric, v, o in gaps[:limit]
    ]


def coach(replay: Replay, player_query: str | None = None) -> Report:
    player = replay.find_player(player_query)
    if player is None:
        names = ", ".join(p.name for p in replay.players.values())
        raise LookupError(f"Could not find player {player_query!r} in {replay.path.name}. Players: {names}")
    lobby = lobby_metrics(replay)
    m = lobby[player.key]
    findings = sorted(_rules(replay, player.key, m, lobby), key=lambda f: -f.score)
    if len(findings) < 3:
        taken = set().union(*(FINDING_METRICS.get(f.key, set()) for f in findings))
        findings += _lobby_gaps(m, lobby, player.key, taken, 3 - len(findings))
    return Report(
        replay=replay,
        player_key=player.key,
        metrics=m,
        lobby=lobby,
        findings=findings,
        strengths=_strengths(m, lobby, player.key, findings),
        moments=_moments(replay, player.key, player.team),
    )


__all__ = ["coach", "Report", "Finding", "Moment", "THRESHOLDS", "METRIC_BY_KEY"]
