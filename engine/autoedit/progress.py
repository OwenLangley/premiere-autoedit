"""How far through a run we are, said out loud so the panel can draw it.

An editor pressing Create on a 28-minute reference waits minutes with the
button greyed out. Nothing in the chain told them whether the tool was working
or had died, which are the two possibilities they care about and the only two
they could not distinguish.

The chain already existed and was severed in one place. `cmd_plan` narrates
itself to stderr; the helper captured that stderr into a `StringIO` and read it
only AFTER the engine returned, so every line arrived at once, at the end, when
nobody needed it any more. This module gives those lines a machine-readable
sibling and the helper streams them as they are written.

**Weights come from the material, not from a guess.** Every expensive thing here
is decode-bound -- ffmpeg reading frames -- so cost runs with the number of
video seconds to read. A unit is therefore weighted by its duration, which is
known from the probe that already happens. The alternative, hand-picked stage
percentages ("the reference is 35% of a run"), would be a number with nothing
behind it drawn to three significant figures.

**It is approximate, and in a knowable direction.** Transcription is also
monotonic in duration but far slower per second than a scan, so a run with
`--subtitles` moves faster early and slower late. A bar is a claim about
position, not about time remaining, which is why nothing here estimates an ETA:
the data does not support one.

**The progress is coarse, because a measurement said so.** The obvious finer
grain is ffmpeg's own `-progress`, reporting position within a single pass. It
does not work for these filter chains. Measured on 7.1.1 against the real
`detect_structure` chain over a 600-second source:

    blocks written: 1          out_time=N/A

`-progress` reports the OUTPUT stream, and the chain ends in
`select='gt(scene,N)'`, which is there precisely to discard almost every frame.
No output frames means no periodic blocks -- one block, at exit, saying nothing.
So a pass is atomic to us, and the bar moves between passes rather than through
them. On the reference that is roughly every thirty to sixty seconds.

**The reporter is module-level state**, the way `logging` is, rather than a
callback threaded through `measure` -> `detect_structure` and every cached path
around them. That buys one obligation: the helper calls `engine_main` IN-PROCESS
and reuses the interpreter across jobs, so a run that ends must not leave its
weights behind for the next one. `begin` replaces the whole run and `finish`
clears it; neither ever accumulates. The helper is single-threaded, so there is
no second run to race with.
"""

from __future__ import annotations

import sys

# Prefixes a progress line on stderr. The helper imports this rather than
# matching a copy of the string: both are Python and `watch.py` already imports
# from `autoedit`, so there is no reason to let the two drift.
PROGRESS_PREFIX = "PROGRESS"


def _emit(fraction: float, key: str, detail: str) -> None:
    print(f"{PROGRESS_PREFIX} {fraction:.4f} {key} {detail}".rstrip(),
          file=sys.stderr, flush=True)


def parse(line: str) -> tuple[float, str, str] | None:
    """Read one progress line back. None for anything else, including prose.

    The engine writes progress and human narration to the same stream, and the
    helper has to keep them apart -- the narration becomes the failure message
    and the "ready" summary, and a progress line in there would be noise at the
    exact moment an editor is reading for a reason.
    """
    if not line.startswith(PROGRESS_PREFIX + " "):
        return None
    number, _, rest = line[len(PROGRESS_PREFIX) + 1:].partition(" ")
    try:
        fraction = float(number)
    except ValueError:
        return None
    if not 0.0 <= fraction <= 1.0:
        return None
    key, _, detail = rest.partition(" ")
    return fraction, key.strip(), detail.strip()


class Run:
    """A sequence of weighted units, reported as one fraction of the whole.

    Not a counter of units done: units differ in size by more than an order of
    magnitude -- a 4-second cutaway beside a 28-minute reference -- and counting
    them would make the bar jump almost to the end and then sit still, which is
    the failure this is meant to remove.
    """

    def __init__(self, units: list[tuple[str, float]]):
        # A zero-weight unit still has to be reachable, or a run of silent stills
        # would divide by zero and report nothing at all.
        self.units = [(key, max(float(weight), 0.0)) for key, weight in units]
        self.total = sum(w for _, w in self.units) or 1.0
        self.index = 0
        self.reported = 0.0

    def _before(self, index: int) -> float:
        return sum(w for _, w in self.units[:index])

    def unit(self, key: str) -> None:
        """Move to this unit. Anything named before it is finished by definition.

        Matching by key rather than stepping by one, because a run skips units:
        no reference, a file with no audio, subtitles switched off. Stepping
        would silently misattribute every unit after the first skip.
        """
        for i, (name, _) in enumerate(self.units):
            if name == key:
                self.index = max(self.index, i)
                return
        # An unknown key is a bug in the caller, not a reason to stop the run.

    def step(self, key: str, detail: str = "", within: float = 0.0) -> None:
        """Report a position inside the current unit, as 0..1 of that unit.

        `key` is a translation key, not a sentence. The panel is fully
        translated and its editor reads Japanese; prose written here would be
        the one part of the window that stayed in English, and it would be the
        part they look at while waiting. `detail` is a file name or a count --
        something that reads the same in either language.
        """
        start = self._before(self.index)
        weight = self.units[self.index][1] if self.index < len(self.units) else 0.0
        fraction = (start + weight * min(max(within, 0.0), 1.0)) / self.total
        # Never backwards. A bar that retreats reads as a fault even when the
        # work is fine, and two units can legitimately report out of order when
        # one of them was skipped.
        self.reported = min(max(fraction, self.reported), 0.999)
        _emit(self.reported, key, detail)


# ----------------------------------------------------------------- the current run

_run: Run | None = None


def begin(units: list[tuple[str, float]]) -> None:
    global _run
    _run = Run(units)


def finish() -> None:
    global _run
    _run = None


def unit(key: str) -> None:
    if _run is not None:
        _run.unit(key)


def step(key: str, detail: str = "", within: float = 0.0) -> None:
    """Say where we are. Does nothing when no run is in progress.

    Silence rather than an error, because `measure()` and the modules under it
    are also called by `report.sh`, by the tests, and by the library indexer,
    none of which have a run to report against.
    """
    if _run is not None:
        _run.step(key, detail, within)
