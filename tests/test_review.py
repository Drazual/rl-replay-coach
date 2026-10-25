"""Decision review on small hand-built game states, plus an end-to-end run on a real replay if one is given."""

import os

import numpy as np
import pytest

from rlcoach import review as rv
from rlcoach.parse import pid_key

REPLAY = os.environ.get("RLCOACH_TEST_REPLAY")


class FakeClock:
    def label(self, t):
        return f"t{t:.1f}"


class FakeReplay:
    def __init__(self, modules):
        self.modules = modules
        self.clock = FakeClock()

    def events(self, module, name="events"):
        return self.modules.get(module, {}).get(name, []) or []


def make_state(cars, ball, touches, modules=None, t_end=14.0):
    """cars: key -> (team, fn(t) -> (x, y)); ball: fn(t) -> (x, y, z). Everything moves on the ground."""
    st = rv.State.__new__(rv.State)
    st.t = np.arange(0, t_end, 1 / rv.FPS)
    st.ball = np.array([ball(t) for t in st.t], dtype=float)
    st.bvel = np.gradient(st.ball, st.t, axis=0)
    st.pos, st.vel, st.boost, st.alive, st.team, st.name = {}, {}, {}, {}, {}, {}
    for key, (team, fn) in cars.items():
        pos = np.array([(*fn(t), 17.0) for t in st.t], dtype=float)
        st.pos[key], st.vel[key] = pos, np.gradient(pos, st.t, axis=0)
        st.boost[key] = np.full(len(st.t), 50.0)
        st.alive[key] = np.ones(len(st.t), dtype=bool)
        st.team[key], st.name[key] = team, key
    st.touches = [{"t": t, "key": k, "team": st.team[k], "ball": list(ball(t)), "power": p, "tags": set(tags)}
                  for t, k, p, tags in touches]
    st.kickoffs = [0.0]
    st.replay = FakeReplay(modules or {})
    return st


def lerp(a, b, t0, t1):
    def f(t):
        k = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
    return f


def goal(t, scorer):
    return {"time": t, "scoring_team_is_team_0": False, "scorer": {"Epic": scorer}, "ball_position": {"z": 100}}


# Keys must match pid_key of the scorer dicts.
A, B, C, D = (pid_key({"Epic": n}) for n in "ABCD")


def last_man_scenario():
    # Blue (team 0) defends -y. A is last back and drives up to challenge C at -2000; B is upfield.
    cars = {
        A: (0, lerp((0, -4000), (0, -2100), 6, 10)),
        B: (0, lambda t: (1500, 1500)),
        C: (1, lerp((0, 0), (0, -1900), 6, 10.3)),
        D: (1, lambda t: (-2000, 2000)),
    }
    ball = lambda t: (0, -2000, 93) if t < 10.3 else (0, -2000 - (t - 10.3) * 1500, 93)  # noqa: E731
    touches = [(9.0, B, 1500, []), (10.0, A, 800, ["contested:contested"]), (10.3, C, 1200, []),
               (11.0, C, 2000, ["action:shot"])]
    return make_state(cars, ball, touches), goal(12.0, "C")


def test_last_man_challenge_is_the_root_cause():
    st, g = last_man_scenario()
    cards = {k: rv._goal_against(st, g, k) for k in (A, B)}
    rv._assign_blame(st, cards)
    assert cards[A].verdict == "mistake" and cards[A].kind == "last_man_challenge"
    assert "shadow" in cards[A].should.lower()
    assert cards[A].snapshot["suggest"]["label"] == "shadow here"
    assert cards[B].verdict == "ok" and "root cause was" in cards[B].happened.lower()


def test_double_commit_blames_the_second_player():
    # A and B both drive at the same ball in their own half; B gets there first and loses it.
    cars = {
        A: (0, lerp((600, -3500), (100, -2150), 8, 10)),
        B: (0, lerp((0, -3200), (0, -2100), 8, 10)),
        C: (1, lerp((0, 0), (0, -1900), 8, 10.3)),
        D: (1, lambda t: (2000, 2000)),
    }
    ball = lambda t: (0, -2000, 93) if t < 10.3 else (0, -2000 - (t - 10.3) * 1500, 93)  # noqa: E731
    touches = [(10.0, B, 900, []), (10.3, C, 1500, []), (11.0, C, 2000, ["action:shot"])]
    st = make_state(cars, ball, touches)
    g = goal(12.0, "C")
    cards = {k: rv._goal_against(st, g, k) for k in (A, B)}
    rv._assign_blame(st, cards)
    assert cards[A].kind == "double_commit" and cards[A].verdict == "mistake"


def test_boost_on_the_way_back_is_fine_but_far_or_midfield_pads_are_not():
    ball = lambda t: (2600, -1500, 93)  # noqa: E731  coming down your right wing
    others = {B: (0, lambda t: (0, 3000)), C: (1, lambda t: (2500, -1000)), D: (1, lambda t: (0, 0))}
    for spot, flagged in (((3072, -4096), False),  # the corner pad on that wing: still between ball and net
                          ((-3072, -4096), True),  # the far corner: off the shooting lane
                          ((-3584, 0), True)):  # a midfield pad
        st = make_state({A: (0, lambda t, s=spot: s), **others}, ball, [])
        assert rv._boost_left_net(st, A, 10) == flagged, spot


def test_duplicate_goal_entries_are_merged():
    rp = FakeReplay({"core": {"goal_context": [{"time": 100.0}, {"time": 100.4}, {"time": 150.0}]}})
    assert [g["time"] for g in rv._goals(rp)] == [100.0, 150.0]


def test_not_on_you_when_teammate_lost_it_and_you_were_in_net():
    cars = {
        A: (0, lambda t: (0, -4900)),  # in net all along
        B: (0, lerp((0, -1000), (0, 400), 6, 10)),  # challenges upfield as last-but-one
        C: (1, lerp((0, 1500), (0, 600), 6, 10.3)),
        D: (1, lambda t: (2500, 2500)),
    }
    ball = lambda t: (0, 500, 93) if t < 10.3 else (0, 500 - (t - 10.3) * 2000, 93)  # noqa: E731
    touches = [(10.0, B, 800, ["contested:contested"]), (10.3, C, 1800, []), (12.5, C, 2000, ["action:shot"])]
    st = make_state(cars, ball, touches, t_end=15)
    g = goal(13.5, "C")
    cards = {k: rv._goal_against(st, g, k) for k in (A, B)}
    rv._assign_blame(st, cards)
    assert cards[A].verdict == "ok"
    assert "in net" in cards[A].happened


@pytest.mark.skipif(not REPLAY, reason="set RLCOACH_TEST_REPLAY to a .replay file")
def test_review_real_replay():
    from rlcoach import parse

    replay = parse.load(REPLAY)
    out = rv.review(REPLAY, replay)
    assert out
    for key, r in out.items():
        assert "error" not in r, r.get("error")
        assert r["goals_on_you"] <= r["goals_against"]
        for d in r["decisions"]:
            assert d["verdict"] in ("mistake", "ok", "good")
            assert d["snapshot"]["players"]
    # Blame goes to the root cause: in 3v3 never the whole team; in 2v2 both only when it was shared.
    for g in rv._goals(replay):
        defending = 1 if g.get("scoring_team_is_team_0") else 0
        ctx = f"Goal against at {replay.clock.label(g['time'])}"
        blamed = {k for k, r in out.items() if replay.players[k].team == defending
                  for d in r["decisions"] if d["context"].startswith(ctx) and d["verdict"] == "mistake"}
        assert len(blamed) <= max(2, replay.team_size - 1)
