import os
from pathlib import Path

import numpy as np
import pytest

from rlcoach.parse import Clock

REPLAY = os.environ.get("RLCOACH_TEST_REPLAY")


def test_clock_labels_regulation_and_overtime():
    t = np.arange(0, 400, 1.0)
    sr = np.clip(300 - t, 0, None)
    c = Clock(t, sr)
    assert c.label(0) == "5:00"
    assert c.label(240) == "1:00"
    assert c.label(310) == "0:00"  # 0:00 but not overtime
    c.overtime = True
    assert c.label(360).startswith("OT +1:")


@pytest.mark.skipif(not REPLAY, reason="set RLCOACH_TEST_REPLAY to a .replay file")
def test_end_to_end(tmp_path: Path):
    from rlcoach import parse
    from rlcoach.coach import coach
    from rlcoach.report import to_html, to_text

    replay = parse.load(REPLAY)
    name = next(iter(replay.players.values())).name
    report = coach(replay, name)
    assert report.metrics["avg_boost"] is not None
    assert 0 <= report.metrics["behind_ball_pct"] <= 100
    assert name in to_text([report])
    assert "<html" in to_html([report, report])


@pytest.mark.skipif(not REPLAY, reason="set RLCOACH_TEST_REPLAY to a .replay file")
def test_app_payload():
    import json

    from rlcoach.analysis import analyse

    d = analyse(REPLAY)
    json.dumps(d)  # must be JSON-serialisable for the app
    assert d["players"] and d["tracks"]["ball"]
    assert all(s["outcome"] in ("goal", "saved", "missed") for s in d["shots"])
    assert sum(s["outcome"] == "goal" for s in d["shots"]) <= sum(d["score"])
