"""Which track from the library is playing under this video.

An editor cutting to a reference usually wants the reference's own music, and
they already have the file -- it is sitting in their music folder. Asking them
to find it again in a hundred-item dropdown is asking them to do a lookup the
machine can do.

**Landmark hashing, not correlation.** Pairs of peaks in the spectrogram become
hashes; a match is a cluster of hashes that agree on the SAME time offset
between query and track. That last part is what makes this work under a
voiceover: noise produces hash collisions too, but they land at scattered
offsets and never pile up, while a real match puts thousands of hits on one
offset. Cross-correlation or chroma similarity would instead be dragged around
by whatever else is in the mix.

**Measured against the real 103-track library on this machine**, matching a
30-second excerpt of "Back on 74" re-encoded as AAC:

    query                               winner   runner-up
    clean                                 4258           7
    speech mixed over it, x1              4189           5
    speech mixed over it, x4              3902           5
    heavy pink noise                      3995           6
    only 10 seconds of music              1470           6

and, holding out ten tracks and matching each against a library that does NOT
contain it -- every score below is therefore a FALSE match:

    highest false match across all ten:      8

So the two populations are separated by a factor of about 180, and MATCH_FLOOR
sits in the gap rather than near either edge. This is not a close call, which is
the only reason a feature that picks a track by itself is acceptable at all.

**numpy only**, for the reason music.py already gives: the whole job is an FFT,
a local-maximum search and a histogram, against a dependency that drags in
scipy, numba and a compiler toolchain.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Music, not speech, so the top octave carries little and 22.05kHz is plenty.
# Same rate as music.py, which means the same decode can serve both one day.
SAMPLE_RATE = 22050
WINDOW = 2048        # 93ms: long enough to resolve a bass note
HOP = 512            # 43 frames a second

# Peaks are found as maxima of a neighbourhood this size. Bigger means fewer,
# stronger landmarks: more robust to noise, less able to match a short excerpt.
NEIGHBOURHOOD_F = 12
NEIGHBOURHOOD_T = 12

# How many peaks a second to keep, strongest first. A cap rather than a
# threshold, so a quiet passage and a loud one contribute alike.
PEAKS_PER_SECOND = 40

# Each peak is paired with up to this many later peaks, within this window of
# frames. The pair is the landmark; a lone peak carries almost no information.
FAN_OUT = 8
MIN_GAP_FRAMES = 1
MAX_GAP_FRAMES = 80

# The score a match must reach to be acted on. Measured: true matches scored
# 1470 and up even through heavy noise, and the best FALSE match across ten
# held-out tracks scored 8. A hundred is an order of magnitude above anything a
# wrong answer produced and an order of magnitude below the weakest right one.
MATCH_FLOOR = 100

# ...and it must beat the runner-up by this much. The floor alone would be
# enough on the measurements above, but a library holding two copies of one song
# is exactly the case where a single strong score is not evidence of WHICH file
# the editor means. Ambiguity is reported rather than guessed, the same rule
# `_find_music_bed` follows when it finds two candidate beds.
MATCH_MARGIN = 4.0

# Bumped when anything above changes the hashes. Cached fingerprints are keyed
# on it, so an old cache is ignored rather than silently mixed with a new one.
FINGERPRINT_VERSION = 1

# How much of one track to index. A bed is picked from somewhere in the track,
# not always the top, so this cannot be thirty seconds -- but an hour-long mix
# in the library should not cost an hour of indexing either. Ten minutes covers
# every track in the library measured here bar one.
INDEX_SECONDS = 600

# How much of the reference to listen to. The music has to be found, not
# transcribed, and 1470 was the score from ten seconds of it -- fifteen times
# the floor. Ninety seconds is generous and still under two seconds of work.
QUERY_SECONDS = 90


class FingerprintError(RuntimeError):
    pass


@dataclass
class Fingerprint:
    """One track's landmarks: parallel arrays, sorted by hash.

    Arrays rather than a dict because a hundred tracks is about 2.7 million
    landmarks; as numpy that is 32MB and a searchsorted away from being usable,
    and as Python dicts it is neither.
    """

    hashes: np.ndarray      # int64, ascending
    times: np.ndarray       # int32, frame index of the anchor peak
    frames: int = 0         # how long the fingerprinted audio was, in frames

    def __len__(self) -> int:
        return int(self.hashes.size)

    @property
    def seconds(self) -> float:
        return self.frames * HOP / SAMPLE_RATE


@dataclass
class Match:
    name: str
    score: int
    runner_up: int
    offset_seconds: float

    @property
    def decisive(self) -> bool:
        return (self.score >= MATCH_FLOOR
                and self.score >= MATCH_MARGIN * max(self.runner_up, 1))


# ------------------------------------------------------------------ analysis


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FingerprintError("ffmpeg not found on PATH (brew install ffmpeg)")
    return exe


def decode_mono(path: str | Path, seconds: float | None = None,
                start: float | None = None) -> np.ndarray:
    """Raw mono float samples, straight out of ffmpeg.

    To a pipe rather than a temp file: this is read once and thrown away, and
    the library pass does it a hundred times.
    """
    cmd = [_ffmpeg(), "-v", "error"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(path)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=900)
    samples = np.frombuffer(out.stdout, dtype=np.float32)
    if samples.size == 0:
        raise FingerprintError(f"no audio could be read from {Path(path).name}")
    return samples


def spectrogram(samples: np.ndarray) -> np.ndarray:
    if samples.size < WINDOW:
        return np.zeros((WINDOW // 2 + 1, 0), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP]
    window = np.hanning(WINDOW).astype(np.float32)
    magnitude = np.abs(np.fft.rfft(frames * window, axis=1)).T
    # log1p rather than dB: it needs no floor to avoid log(0), and the peak
    # PICKING only cares about ordering, which any monotone map preserves.
    return np.log1p(magnitude).astype(np.float32)


def peaks(spec: np.ndarray, cap: int | None = None) -> np.ndarray:
    """The constellation map: local maxima, strongest first if capped."""
    if spec.size == 0:
        return np.zeros((0, 2), np.int32)
    padded = np.pad(spec, ((NEIGHBOURHOOD_F, NEIGHBOURHOOD_F),
                           (NEIGHBOURHOOD_T, NEIGHBOURHOOD_T)),
                    constant_values=-np.inf)
    windows = np.lib.stride_tricks.sliding_window_view(
        padded, (2 * NEIGHBOURHOOD_F + 1, 2 * NEIGHBOURHOOD_T + 1))
    hits = (spec == windows.max(axis=(2, 3))) & (spec > spec.mean())
    freq, time = np.nonzero(hits)
    if cap and freq.size > cap:
        strongest = np.argsort(-spec[freq, time])[:cap]
        freq, time = freq[strongest], time[strongest]
    return np.stack([freq, time], 1).astype(np.int32)


def landmarks(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pairs of peaks as (hash, anchor time), sorted by hash."""
    if points.size == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int32)
    points = points[np.argsort(points[:, 1], kind="stable")]
    freqs, times = points[:, 0].astype(np.int64), points[:, 1].astype(np.int64)

    hashes: list[np.ndarray] = []
    anchors: list[np.ndarray] = []
    # Vectorised over the fan: for each k, pair every peak with the peak k
    # places later, and keep the pairs whose gap is inside the target zone.
    for k in range(1, FAN_OUT + 1):
        if k >= len(points):
            break
        gap = times[k:] - times[:-k]
        usable = (gap >= MIN_GAP_FRAMES) & (gap <= MAX_GAP_FRAMES)
        if not usable.any():
            continue
        f1 = freqs[:-k][usable]
        f2 = freqs[k:][usable]
        # Frequencies are halved before hashing: one bin of drift from a
        # re-encode should not change the landmark.
        hashes.append(((f1 >> 1) << 20) | ((f2 >> 1) << 8) | np.minimum(gap[usable], 255))
        anchors.append(times[:-k][usable])

    if not hashes:
        return np.zeros(0, np.int64), np.zeros(0, np.int32)
    flat = np.concatenate(hashes)
    anchor = np.concatenate(anchors).astype(np.int32)
    order = np.argsort(flat, kind="stable")
    return flat[order], anchor[order]


def fingerprint(path: str | Path, seconds: float | None = None,
                start: float | None = None) -> Fingerprint:
    spec = spectrogram(decode_mono(path, seconds, start))
    cap = int(PEAKS_PER_SECOND * spec.shape[1] * HOP / SAMPLE_RATE) or None
    h, t = landmarks(peaks(spec, cap))
    return Fingerprint(h, t, spec.shape[1])


# ------------------------------------------------------------------ matching


def score_against(query: Fingerprint, track: Fingerprint) -> tuple[int, int]:
    """(size of the largest aligned cluster, its offset in frames).

    The alignment is the whole trick. Every shared hash votes for one offset --
    `track time - query time` -- and only a genuine match makes those votes
    agree. Under a voiceover the spurious hashes still arrive, they simply vote
    for offsets scattered across the track and never out-vote the real one.
    """
    if len(query) == 0 or len(track) == 0:
        return 0, 0

    left = np.searchsorted(track.hashes, query.hashes, "left")
    right = np.searchsorted(track.hashes, query.hashes, "right")
    counts = right - left
    total = int(counts.sum())
    if total == 0:
        return 0, 0

    # Expand to one row per (query landmark, track landmark) pair without a
    # Python loop: repeat each query time by how many track landmarks share its
    # hash, and gather the matching track times alongside.
    query_times = np.repeat(query.times.astype(np.int64), counts)
    picked = np.repeat(left, counts) + (
        np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts))
    deltas = track.times[picked].astype(np.int64) - query_times

    shifted = deltas - deltas.min()
    votes = np.bincount(shifted)
    best = int(votes.argmax())
    return int(votes[best]), int(best + deltas.min())


def identify(query: Fingerprint, library: dict[str, Fingerprint]) -> Match | None:
    """The track this audio contains, or None when nothing is decisive.

    None is an ordinary answer: the reference may use music that is not in the
    library at all, and saying so is right. Scoring a cut against the wrong
    track would be worse than leaving the choice alone -- the same judgement
    `_find_music_bed` makes when it refuses to pick between two beds.
    """
    ranked = sorted(
        ((score_against(query, fp)[0], name) for name, fp in library.items()),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not ranked:
        return None
    score, name = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    offset = score_against(query, library[name])[1]
    return Match(name, score, runner_up, offset * HOP / SAMPLE_RATE)


# ------------------------------------------------------------------- storage


def save(path: Path, fp: Fingerprint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, hashes=fp.hashes, times=fp.times,
                        frames=np.int64(fp.frames), version=np.int64(FINGERPRINT_VERSION))


def load(path: Path) -> Fingerprint | None:
    try:
        with np.load(path) as data:
            if int(data["version"]) != FINGERPRINT_VERSION:
                return None
            return Fingerprint(data["hashes"], data["times"], int(data["frames"]))
    except (OSError, ValueError, KeyError):
        return None      # a missing or damaged cache is rebuilt, never fatal


def cache_path(work_dir: Path, content_hash: str) -> Path:
    """Where one track's landmarks live.

    Keyed on the file's content hash and on everything that shapes the hashes,
    so a track that is re-exported, or a change to the analysis, produces a
    different path rather than a stale hit. The helper writes these in the
    background and the engine only ever reads them; both have to agree on this
    name, which is why it is here and not in either of them.
    """
    import hashlib
    key = hashlib.sha256(
        f"{content_hash}|v{FINGERPRINT_VERSION}|{INDEX_SECONDS}".encode()
    ).hexdigest()[:24]
    return work_dir / "fingerprints" / f"{key}.npz"
