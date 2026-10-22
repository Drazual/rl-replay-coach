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


# ---------------------------------------------------------------- ball mechanics

def bframe(t, *, loc, vel=(0, 0, 0), rot=(0.0, 0.0, 0.0), ground=False, ball_loc=(0, 0, 93), ball_vel=(0, 0, 0),
           touches=0, hasflip=True, jump=0, pitch_in=0.0, boost=1, boost_amt=80, wheels=None, dodging=False):
    car = {"loc": list(loc), "vel": list(vel), "rot": list(rot), "boost": boost_amt, "ground": ground,
           "dodging": dodging, "hasflip": hasflip, "wheels": 4 if ground else 0, "team": 0, "touches": touches}
    if wheels is not None:
        car["wheels"] = wheels
    return {"t": t, "car": car,
            "in": {"thr": 1, "steer": 0, "pitch": pitch_in, "yaw": 0, "roll": 0, "jump": jump, "boost": boost, "hb": 0},
            "ball": {"loc": list(ball_loc), "vel": list(ball_vel)}}


def flip_reset_frames(*, reset=True, use=True, upside_down=True):
    """Car flies up under the ball upside down, touches it with its wheels, then flips into it toward the blue attacking goal."""
    fs, t, dt, n = [], 0.0, 1 / 60, 0
    fs.append(bframe(t, loc=(0, 0, 17), ground=True)); t += dt
    fs.append(bframe(t, loc=(0, 0, 17), ground=True, jump=1)); t += dt
    flipped = False
    roll = math.pi if upside_down else 0.0
    for i in range(80):
        z = 100 + i * 12
        hasflip = not flipped
        jump = 0
        ball = (0, 400 + i * 2, z + 150)  # just above and ahead
        bvel = (0, 120, 0)
        if i == 20:  # first jump's flip gets used for height (double jump), so the flip is gone
            jump, flipped = 1, True
            hasflip = False
        if i == 40:  # wheels touch the ball
            n += 1
            if reset:
                flipped = False
                hasflip = True
        if use and reset and i == 55:
            jump = 1
            hasflip = False
            flipped = True
        if use and reset and i == 58:  # the flip hits the ball toward the goal
            n += 1
            bvel = (0, 2600, 100)
        if i > 58 and use and reset:
            bvel = (0, 2600, 100)
            ball = (0, 1000 + (i - 58) * 40, 900)
        # the ball is right above the car: upside down, the wheels face it; the right way up, the roof does
        fs.append(bframe(t, loc=(0, 400 + i * 2, z), vel=(0, 300, 600), rot=(0.0, math.pi / 2, roll),
                         ball_loc=ball, ball_vel=bvel, touches=n, hasflip=hasflip, jump=jump,
                         pitch_in=-1.0 if jump and i == 55 else 0.0))
        t += dt
    fs.append(bframe(t, loc=(0, 600, 17), ground=True, touches=n))
    return fs


def test_flip_reset_used_into_shot():
    atts = run(flip_reset_frames())
    assert atts and atts[0].kind == "flip_reset", atts
    a = atts[0]
    assert a.details["reset"] and a.details["confirmed_by_game"] and a.details["shot_speed"] >= 2000
    assert a.details["on_target"] and a.score >= 80, a


def test_flip_reset_not_used():
    a = run(flip_reset_frames(use=False))[0]
    assert a.kind == "flip_reset" and a.details["reset"] and "didn't use it" in a.tip


def test_flip_reset_missed():
    a = run(flip_reset_frames(reset=False))[0]
    assert a.kind == "flip_reset" and not a.details["reset"] and a.tip.startswith("No reset")


def test_roof_touch_is_not_a_reset_try():
    atts = run(flip_reset_frames(reset=False, upside_down=False))
    assert not any(a.kind == "flip_reset" for a in atts)


def air_dribble_frames(touch_every=12, n_touches=5, gap=150.0, finish=2400):
    fs, t, dt, n = [], 0.0, 1 / 60, 0
    fs.append(bframe(t, loc=(0, 0, 17), ground=True)); t += dt
    fs.append(bframe(t, loc=(0, 0, 17), ground=True, jump=1)); t += dt
    frames = touch_every * n_touches + 20
    for i in range(frames):
        y, z = 100 + i * 15, 300 + i * 8
        if i % touch_every == 0 and n < n_touches:
            n += 1
        last = n == n_touches and i >= touch_every * (n_touches - 1)
        bvel = (0, finish, 0) if last else (0, 900, 300)
        fs.append(bframe(t, loc=(0, y, z), vel=(0, 900, 300), rot=(0.6, math.pi / 2, 0.0),
                         ball_loc=(0, y + gap * 0.7, z + gap * 0.7), ball_vel=bvel, touches=n))
        t += dt
    fs.append(bframe(t, loc=(0, 2000, 17), ground=True, touches=n))
    return fs


def test_air_dribble_good():
    a = run(air_dribble_frames())[0]
    assert a.kind == "air_dribble" and a.details["touches"] == 5 and a.score >= 80, a


def test_air_dribble_loose_control():
    a = run(air_dribble_frames(n_touches=2, gap=600, finish=800))[0]
    assert a.kind == "air_dribble" and a.score < 60


def ceiling_frames(jump_off=False, flip=True, hit=True):
    fs, t, dt, n = [], 0.0, 1 / 60, 0
    for i in range(10):  # driving on the ceiling, upside down
        fs.append(bframe(t, loc=(0, -1000 + i * 20, 2000), vel=(0, 1200, 0), rot=(0.0, math.pi / 2, math.pi),
                         ground=True, ball_loc=(0, 0, 600)))
        t += dt
    if jump_off:
        fs.append(bframe(t, loc=(0, -800, 2000), vel=(0, 1200, 0), rot=(0.0, math.pi / 2, math.pi), ground=True,
                         jump=1, ball_loc=(0, 0, 600))); t += dt
    for i in range(60):
        z = 1950 - i * 25
        jump = 1 if flip and i == 45 else 0
        bvel = (0, 0, 0)
        if hit and i == 48:
            n += 1
        if hit and i >= 48:
            bvel = (0, 2800, -200)
        fs.append(bframe(t, loc=(0, -780 + i * 18, z), vel=(0, 1100, -1200), rot=(0.0, math.pi / 2, 0.0),
                         ball_loc=(0, 100, z - 120) if i < 48 else (0, 100 + (i - 48) * 45, 800),
                         ball_vel=bvel, touches=n, jump=jump, pitch_in=-1.0 if jump else 0.0, dodging=i >= 45 and flip))
        t += dt
    fs.append(bframe(t, loc=(0, 300, 17), ground=True, touches=n))
    return fs


def test_ceiling_shot_clean():
    a = run(ceiling_frames())[0]
    assert a.kind == "ceiling_shot" and not a.details["jumped_off"] and a.details["flip_used"], a
    assert a.details["on_target"] and a.score >= 80, a


def test_ceiling_shot_jumped_off_and_no_flip():
    a = run(ceiling_frames(jump_off=True, flip=False))[0]
    assert a.kind == "ceiling_shot" and a.details["jumped_off"] and not a.details["flip_used"] and a.score < 70


def zap_dash_frames(gain=650, air=0.18):
    fs, t, dt = [], 0.0, 1 / 120
    fs.append(bframe(t, loc=(0, 0, 17), vel=(1000, 0, 0), ground=True, boost=0)); t += dt
    fs.append(bframe(t, loc=(0, 0, 17), vel=(1000, 0, 0), ground=True, jump=1, boost=0)); t += dt
    steps = int(air / dt)
    for i in range(steps):
        jump = 1 if i == steps - 6 else 0
        dodging = i >= steps - 6
        fs.append(bframe(t, loc=(0, 0, 30 + min(i, steps - i)), vel=(1000 + (gain * 0.3 if dodging else 0), 0, 50),
                         rot=(0.1, 0.0, 0.0), jump=jump, pitch_in=1.0 if dodging else 0.0, dodging=dodging, boost=0))
        t += dt
    fs.append(bframe(t, loc=(0, 0, 17), vel=(1000 + gain, 0, 0), ground=True, boost=0))
    return fs


def test_zap_dash_good():
    a = run(zap_dash_frames())[0]
    assert a.kind == "zap_dash" and a.details["speed_gain"] >= 500 and a.score >= 80, a


def test_zap_dash_slow_and_weak():
    a = run(zap_dash_frames(gain=150, air=0.4))[0]
    assert a.kind == "zap_dash" and a.score < 60 and "speed" in a.tip


def test_old_plugin_without_new_fields_still_works():
    """Frames from the v0.3 plugin have no touches/hasflip/wheels: half flips must still grade."""
    atts = [a for a in run(half_flip()) if a.kind == "half_flip"]
    assert atts and atts[0].score >= 80


def test_touches_inferred_without_new_plugin_fields():
    fs = ceiling_frames()
    for f in fs:
        for k in ("touches", "hasflip", "wheels"):
            f["car"].pop(k)
    a = run(fs)[0]
    assert a.kind == "ceiling_shot" and a.details["shot_speed"] and a.details["on_target"], a
