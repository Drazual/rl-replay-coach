"""Per-player numbers, normalised so games of different lengths compare fairly."""

from __future__ import annotations

from dataclasses import dataclass

from .parse import Replay

RAW_BOOST = 255 / 100  # replay stores boost as 0-255; players think in 0-100
UU_TO_KMH = 0.036  # 1 unreal unit = 1 cm


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str
    fmt: str  # python format spec applied to the value
    unit: str = ""
    better: str | None = None  # "high", "low" or None when neither is better


METRICS = [
    # Boost
    Metric("avg_boost", "Average boost", "Boost", ".0f", "", None),
    Metric("bpm", "Boost used per minute", "Boost", ".0f", "", None),
    Metric("zero_boost_pct", "Time at 0 boost", "Boost", ".0f", "%", "low"),
    Metric("low_boost_pct", "Time under 25 boost", "Boost", ".0f", "%", "low"),
    Metric("full_boost_pct", "Time at 100 boost", "Boost", ".0f", "%", None),
    Metric("small_pad_share", "Small pads share of pickups", "Boost", ".0f", "%", "high"),
    Metric("overfill_per_big", "Boost wasted per big pad", "Boost", ".0f", "", "low"),
    Metric("supersonic_boost_pct", "Boost spent while already supersonic", "Boost", ".0f", "%", "low"),
    Metric("stolen_pads", "Opponent pads stolen", "Boost", ".0f", "", "high"),
    # Speed & movement
    Metric("avg_speed_kmh", "Average speed", "Speed", ".0f", " km/h", "high"),
    Metric("supersonic_pct", "Time supersonic", "Speed", ".0f", "%", "high"),
    Metric("slow_pct", "Time at slow speed", "Speed", ".0f", "%", "low"),
    Metric("ground_pct", "Time on ground", "Speed", ".0f", "%", None),
    Metric("high_air_pct", "Time high in the air", "Speed", ".0f", "%", None),
    Metric("powerslides_pm", "Powerslides per minute", "Speed", ".1f", "", None),
    # Positioning & rotation
    Metric("behind_ball_pct", "Time goal-side of the ball", "Positioning", ".0f", "%", "high"),
    Metric("def_third_pct", "Time in defensive third", "Positioning", ".0f", "%", None),
    Metric("off_third_pct", "Time in offensive third", "Positioning", ".0f", "%", None),
    Metric("most_back_pct", "Time as last back", "Positioning", ".0f", "%", None),
    Metric("first_man_pct", "Time as first man (closest to ball)", "Positioning", ".0f", "%", None),
    Metric("third_man_pct", "Time as last man in rotation", "Positioning", ".0f", "%", None),
    Metric("longest_first_man", "Longest first-man stint", "Positioning", ".1f", " s", None),
    # Ball play
    Metric("touches_p5", "Touches per 5 min", "Ball play", ".0f", "", "high"),
    Metric("hard_hit_pct", "Touches that were hard hits", "Ball play", ".0f", "%", "high"),
    Metric("aerial_touches_p5", "Aerial touches per 5 min", "Ball play", ".1f", "", None),
    Metric("whiffs_p5", "Whiffs per 5 min", "Ball play", ".1f", "", "low"),
    Metric("beaten_p5", "Beaten to the ball per 5 min", "Ball play", ".1f", "", "low"),
    Metric("fifty_win_pct", "50/50s won", "Ball play", ".0f", "%", "high"),
    Metric("kickoff_win_pct", "Kickoffs won", "Ball play", ".0f", "%", "high"),
    # Results
    Metric("goals", "Goals", "Results", ".0f", "", "high"),
    Metric("shots", "Shots", "Results", ".0f", "", "high"),
    Metric("saves", "Saves", "Results", ".0f", "", "high"),
    Metric("assists", "Assists", "Results", ".0f", "", "high"),
    Metric("shooting_pct", "Shooting %", "Results", ".0f", "%", "high"),
    Metric("conceded_last_man", "Goals conceded as last defender", "Results", ".0f", "", "low"),
    Metric("caught_ahead", "Caught upfield on goals against", "Results", ".0f", "", "low"),
    Metric("demos_inflicted", "Demos inflicted", "Results", ".0f", "", None),
    Metric("demos_taken", "Times demolished", "Results", ".0f", "", "low"),
    # Mechanics seen
    Metric("wavedashes", "Wavedashes", "Mechanics", ".0f", "", None),
    Metric("half_flips", "Half flips", "Mechanics", ".0f", "", None),
]
METRIC_BY_KEY = {m.key: m for m in METRICS}


def _pct(part, whole):
    return 100.0 * part / whole if whole else None


def _labeled(entries: dict | None, **want) -> float:
    """Sum the count/value of labeled entries that match all the given labels."""
    total = 0.0
    for e in (entries or {}).get("entries", []) or []:
        labels = {l["key"]: l["value"] for l in e.get("labels", [])}
        if all(labels.get(k) == v for k, v in want.items()):
            total += e.get("count", e.get("value", 0)) or 0
    return total


def player_metrics(replay: Replay, key: str) -> dict[str, float | None]:
    core = replay.player_stats("core", key)
    boost = replay.player_stats("boost", key)
    move = replay.player_stats("movement", key)
    pos = replay.player_stats("positioning", key)
    rot = replay.player_stats("rotation", key)
    touch = replay.player_stats("touch", key)
    whiff = replay.player_stats("whiff", key)
    fifty = replay.player_stats("fifty_fifty", key)
    slide = replay.player_stats("powerslide", key)

    t_boost = boost.get("tracked_time") or 0
    t_move = move.get("tracked_time") or 0
    t_pos = pos.get("tracked_time") or 0
    t_rot = rot.get("active_game_time") or 0
    minutes = (t_pos or t_move or t_boost) / 60 or None
    per5 = (lambda n: n / minutes * 5) if minutes else (lambda n: None)

    m: dict[str, float | None] = {}

    # Boost
    m["avg_boost"] = boost.get("boost_integral", 0) / t_boost / RAW_BOOST if t_boost else None
    m["bpm"] = boost.get("amount_used", 0) / RAW_BOOST / (t_boost / 60) if t_boost else None
    m["zero_boost_pct"] = _pct(boost.get("time_zero_boost", 0), t_boost)
    m["low_boost_pct"] = _pct(boost.get("time_boost_0_25", 0), t_boost)
    m["full_boost_pct"] = _pct(boost.get("time_hundred_boost", 0), t_boost)
    small, big = boost.get("small_pads_collected", 0), boost.get("big_pads_collected", 0)
    m["small_pad_share"] = _pct(small, small + big)
    m["overfill_per_big"] = boost.get("overfill_total", 0) / RAW_BOOST / big if big else None
    m["supersonic_boost_pct"] = _pct(boost.get("amount_used_while_supersonic", 0), boost.get("amount_used", 0))
    m["stolen_pads"] = boost.get("big_pads_stolen", 0) + boost.get("small_pads_stolen", 0)

    # Speed
    m["avg_speed_kmh"] = move.get("speed_integral", 0) / t_move * UU_TO_KMH if t_move else None
    m["supersonic_pct"] = _pct(move.get("time_supersonic_speed", 0), t_move)
    m["slow_pct"] = _pct(move.get("time_slow_speed", 0), t_move)
    m["ground_pct"] = _pct(move.get("time_on_ground", 0), t_move)
    m["high_air_pct"] = _pct(move.get("time_high_air", 0), t_move)
    m["powerslides_pm"] = slide.get("press_count", 0) / minutes if minutes else None

    # Positioning
    m["behind_ball_pct"] = _pct(pos.get("time_behind_ball", 0), t_pos)
    m["def_third_pct"] = _pct(pos.get("time_defensive_third", 0), t_pos)
    m["off_third_pct"] = _pct(pos.get("time_offensive_third", 0), t_pos)
    m["most_back_pct"] = _pct(pos.get("time_most_back", 0), t_pos) if replay.team_size > 1 else None
    if replay.team_size > 1 and t_rot:
        m["first_man_pct"] = _pct(rot.get("time_first_man", 0), t_rot)
        last_role = "time_third_man" if replay.team_size >= 3 else "time_second_man"
        m["third_man_pct"] = _pct(rot.get(last_role, 0), t_rot)
        m["longest_first_man"] = rot.get("longest_first_man_stint_time")
    else:
        m["first_man_pct"] = m["third_man_pct"] = m["longest_first_man"] = None

    # Ball play
    touches = touch.get("touch_count", 0)
    m["touches_p5"] = per5(touches)
    m["hard_hit_pct"] = _pct(touch.get("hard_hit_count", 0), touches)
    m["aerial_touches_p5"] = per5(touch.get("aerial_touch_count", 0))
    m["whiffs_p5"] = per5(whiff.get("whiff_count", 0))
    beaten = sum(1 for e in replay.events("beaten_to_ball") if _k(e.get("player")) == key)
    m["beaten_p5"] = per5(beaten)
    open_wins = fifty.get("wins", 0) - fifty.get("kickoff_wins", 0)
    open_losses = fifty.get("losses", 0) - fifty.get("kickoff_losses", 0)
    m["fifty_win_pct"] = _pct(open_wins, open_wins + open_losses)
    kw, kl = fifty.get("kickoff_wins", 0), fifty.get("kickoff_losses", 0)
    m["kickoff_win_pct"] = _pct(kw, kw + kl)

    # Results
    for k in ("goals", "shots", "saves", "assists"):
        m[k] = core.get(k, 0)
    m["shooting_pct"] = _pct(core.get("goals", 0), core.get("shots", 0))
    m["conceded_last_man"] = core.get("goals_conceded_while_last_defender", 0) if replay.team_size > 1 else None
    m["caught_ahead"] = core.get("caught_ahead_of_play_on_conceded_goals", 0)
    demos = replay.modules.get("demo", {}).get("demolitions", []) or []
    m["demos_inflicted"] = sum(1 for d in demos if _k(d.get("attacker")) == key)
    m["demos_taken"] = sum(1 for d in demos if _k(d.get("victim")) == key)

    # Mechanics
    m["wavedashes"] = replay.player_stats("wavedash", key).get("count", 0)
    m["half_flips"] = replay.player_stats("half_flip", key).get("count", 0)

    # Extra raw counts the coach needs but that are not shown in the table.
    m["_minutes"] = minutes
    m["_touches"] = touches
    m["_kickoffs"] = kw + kl
    m["_open_fifties"] = open_wins + open_losses
    m["_whiffs"] = whiff.get("whiff_count", 0)
    m["_aerial_whiffs"] = whiff.get("aerial_whiff_count", 0)
    m["_beaten"] = beaten
    m["_kickoff_approaches"] = {
        a: _labeled(replay.player_stats("kickoff", key).get("labeled_event_counts"), kickoff_approach=a)
        for a in ("front_flip", "speed_flip", "diagonal_flip", "drive", "delay", "fake", "boost_only")
    }
    return m


def _k(remote_id) -> str | None:
    from .parse import pid_key

    return pid_key(remote_id) if remote_id is not None else None


def lobby_metrics(replay: Replay) -> dict[str, dict[str, float | None]]:
    return {key: player_metrics(replay, key) for key in replay.players}
