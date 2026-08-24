"""Both implementations of the timebase must agree, forever.

The panel re-implements this maths in JavaScript. If the two drift apart, clips
land on the wrong frame and no other test in either suite would notice.
"""

import json
from pathlib import Path

import pytest

from autoedit.timebase import Timebase

VECTORS = Path(__file__).resolve().parents[2] / "schema" / "timebase-vectors.json"


def cases():
    return json.loads(VECTORS.read_text())["cases"]


@pytest.mark.parametrize("case", cases(), ids=lambda c: f"{c['timebase']['fpsNum']}/{c['timebase']['fpsDen']}")
def test_python_matches_the_shared_vectors(case):
    tb = Timebase.from_dict(case["timebase"])
    for v in case["toFrames"]:
        assert tb.to_frames(v["seconds"]) == v["frames"]
    for v in case["snap"]:
        assert tb.snap(v["seconds"]) == pytest.approx(v["snapped"], abs=1e-9)
    for v in case["toSeconds"]:
        assert tb.to_seconds(v["frames"]) == pytest.approx(v["seconds"], abs=1e-9)
    for v in case["timecode"]:
        assert tb.timecode(v["frames"]) == v["timecode"]
