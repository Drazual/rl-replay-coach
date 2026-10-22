"""Synthetic input sequences for the live mechanics grader."""

import math

from rlcoach.live.mechanics import MechanicsTracker


def frame(t, *, z=17.0, vel=(0, 0, 0), yaw=0.0, roll=0.0, ground=True, pitch_in=0.0, roll_in=0.0,
          yaw_in=0.0, jump=0, boost=0, dodging=False):
    return {"t": t, "car": {"loc": [0, 0, z], "vel": list(vel), "rot": [0.0, yaw, roll], "boost": 50,
                            "ground": ground, "dodging": dodging},
            "in": {"thr": 0, "steer": 0, "pitch": pitch_in, "yaw": yaw_in, "roll": roll_in, "jump": jump,
                   "boost": boost, "hb": 0}}


def run(frames):
    tr = MechanicsTracker()
    out = []
    for f in frames:
        out += tr.feed(f)
    return out


def half_flip(cancel_at=0.08, turn=math.pi, roll_in=1.0):
    fs, t, dt = [], 0.0, 1 / 60
    for _ in range(10):  # reversing
        fs.append(frame(t, vel=(-800, 0, 0))); t += dt
    fs.append(frame(t, vel=(-800, 0, 0), jump=1)); t += dt  # jump on the ground
    fs.append(frame(t, z=40, vel=(-800, 0, 200), ground=False, jump=1)); t += dt
    fs.append(frame(t, z=50, vel=(-800, 0, 150), ground=False, jump=0)); t += dt
    t_dodge = t
    fs.append(frame(t, z=55, vel=(-800, 0, 100), ground=False, jump=1, pitch_in=1.0, dodging=True)); t += dt
    n = 50
    for i in range(n):
        el = t - t_dodge
        p_in = -1.0 if el >= cancel_at else 1.0
        fs.append(frame(t, z=60, vel=(-1300, 0, 0), yaw=turn * (i + 1) / n, ground=False,
                        pitch_in=p_in, roll_in=roll_in if el > cancel_at else 0, dodging=True))
        t += dt
    fs.append(frame(t, vel=(1200, 0, 0), yaw=turn))  # land facing the other way
    return fs


def test_good_half_flip_scores_high():
    atts = [a for a in run(half_flip()) if a.kind == "half_flip"]
    assert len(atts) == 1 and atts[0].score >= 80, atts


def test_late_cancel_is_flagged():
    att = [a for a in run(half_flip(cancel_at=0.35)) if a.kind == "half_flip"][0]
    assert att.score < 80 and "Cancel sooner" in att.tip


def test_no_turn_tells_you_to_air_roll():
    att = [a for a in run(half_flip(turn=math.radians(60), roll_in=0)) if a.kind == "half_flip"][0]
    assert "turned" in att.tip or "Air roll" in att.tip


def test_wavedash_speed_gain():
    fs, t, dt = [], 0.0, 1 / 60
    fs.append(frame(t, vel=(1000, 0, 0))); t += dt
    for i in range(30):  # falling from a jump
        fs.append(frame(t, z=200 - i * 6, vel=(1000, 0, -300), ground=False)); t += dt
    fs.append(frame(t, z=25, vel=(1000, 0, -300), ground=False, jump=1, pitch_in=-1.0, dodging=True)); t += dt
    fs.append(frame(t, z=20, vel=(1100, 0, -300), ground=False, pitch_in=-1.0, dodging=True)); t += dt
    fs.append(frame(t, vel=(1550, 0, 0)))
    atts = run(fs)
    assert atts and atts[0].kind == "wavedash" and atts[0].details["speed_gain"] >= 400


def test_roof_landing():
    fs, t, dt = [], 0.0, 1 / 60
    fs.append(frame(t)); t += dt
    for i in range(70):
        fs.append(frame(t, z=300, ground=False)); t += dt
    fs.append(frame(t, roll=math.pi))  # upside down
    atts = run(fs)
    assert atts and atts[0].kind == "landing" and atts[0].score < 50
