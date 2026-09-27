import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Pure ffmpeg output, 19 MB of it, regenerated in about two seconds -- which is
# why it is not committed to a repository that is otherwise 2 MB.
MEDIA_FIXTURES = ("sample_25fps_1080p.mp4", "sample_2997_vertical_silent.mp4",
                  "sample_audio_only.m4a", "sample_music.wav")


def pytest_sessionstart(session):
    """Make the media fixtures before anything is collected.

    The first thing anyone does with a fresh clone is run the tests, and what
    they used to get was two FileNotFoundErrors raised inside `shutil.copy` --
    which reads like a broken project rather than a missing build step.
    `generate.sh` was one line away in .gitignore and nowhere a newcomer looks.

    Before collection, not in a fixture: the `skipif` marks that guard the rest
    of these tests are evaluated while the module is imported, so anything
    generated later would arrive after the decision to skip.

    ffmpeg is a hard requirement of the project itself, so needing it here costs
    nobody anything. Without it the marks take over and name the script.
    """
    if all((FIXTURES / name).exists() for name in MEDIA_FIXTURES):
        return
    if shutil.which("ffmpeg") is None:
        return
    subprocess.run(["bash", str(FIXTURES / "generate.sh")],
                   capture_output=True, text=True, timeout=120)

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
