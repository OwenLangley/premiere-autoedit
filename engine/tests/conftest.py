import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from autoedit.transcript import Transcript, Word


@pytest.fixture
def mkword():
    def _mk(text: str, start: float, dur: float = 0.30, conf: float = 0.99, speaker=None) -> Word:
        return Word(text, start, start + dur, conf, speaker)
    return _mk


@pytest.fixture
def mktranscript(mkword):
    def _mk(spec: list[tuple], media_id: str = "A001") -> Transcript:
        """spec entries are (text, start) or (text, start, dur) or (text, start, dur, conf)."""
        return Transcript(media_id, [mkword(*s) for s in spec])
    return _mk
