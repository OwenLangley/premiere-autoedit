"""Beat detection for music-led cutting.

Promos are cut to the track, not to speech. This finds the beat grid so cuts can
land on it.

Implemented directly on numpy rather than pulling in librosa: the whole job is a
spectral-flux onset envelope, an autocorrelation for tempo, and a phase search.
That is a few dozen lines, against a dependency that drags in scipy, numba and a
compiler toolchain.

Deliberately conservative -- it reports its own confidence, and the caller is
expected to fall back to shot-driven cutting when that confidence is poor. A
promo cut to the wrong grid is worse than one not cut to a grid at all.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_RATE = 22050
HOP = 512
WINDOW = 1024

# How far a cut may be moved to reach a beat when the speech has priority.
# Half a beat at 104 BPM is 288ms, so this deliberately does NOT reach every
# beat: only cuts that are already close get pulled onto the grid, and the rest
# are left where the words put them. That asymmetry is the whole point of
# `beat_priority: speech`.
MAX_BEAT_NUDGE = 0.12

MIN_BPM = 60.0
MAX_BPM = 200.0


class MusicError(RuntimeError):
    pass


@dataclass
class BeatGrid:
    bpm: float
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    confidence: float = 0.0
    octave_ambiguous: bool = False   # half or double scored nearly as well

    @property
    def beat_interval(self) -> float:
        """Typical gap between beats.

        The MEASURED median rather than 60/bpm, because the beats are tracked
        through the song and their spacing genuinely varies. `bpm` is the
        headline number for reporting; this is the one to compute with.
        """
        if len(self.beats) > 2:
            gaps = [b - a for a, b in zip(self.beats, self.beats[1:])]
            gaps.sort()
            return gaps[len(gaps) // 2]
        return 60.0 / self.bpm if self.bpm else 0.0

    def cut_points(self, every: float = 1.0) -> list[float]:
        """The times to cut on, at a chosen frequency against the real beats.

        `every` is counted in beats: 1 lands on every beat, 2 on every other, 4
        on the bar. Below 1 it subdivides -- 0.5 adds the midpoint between each
        pair of beats, which stays correct as the tempo drifts because it is the
        midpoint of two ACTUAL beats rather than a fixed offset.
        """
        beats = self.beats
        if not beats:
            return []
        if every >= 1:
            return beats[::max(1, int(round(every)))]
        # Subdivide. Anything below a half-beat is a stutter, not a pace.
        parts = max(2, min(4, int(round(1.0 / every))))
        out: list[float] = []
        for a, b in zip(beats, beats[1:]):
            step = (b - a) / parts
            out.extend(a + i * step for i in range(parts))
        out.append(beats[-1])
        return out

    def snap(self, time: float, subdivision: int = 1) -> float:
        """Nearest beat (or every Nth beat) to a given time."""
        candidates = self.beats[::max(1, subdivision)]
        if not candidates:
            return time
        return min(candidates, key=lambda b: abs(b - time))

    def next_beat(self, time: float, subdivision: int = 1) -> float | None:
        """First beat at or after `time`.

        Cut points want this rather than `snap`: the nearest beat is often just
        BEFORE the earliest usable frame of a shot, and a cut cannot start before
        its own shot does.
        """
        candidates = self.beats[::max(1, subdivision)]
        for b in candidates:
            if b >= time - 1e-6:
                return b
        return None

    def to_dict(self) -> dict:
        return {
            "bpm": round(self.bpm, 2),
            "confidence": round(self.confidence, 3),
            "octaveAmbiguous": self.octave_ambiguous,
            "beats": [round(b, 4) for b in self.beats],
            "downbeats": [round(b, 4) for b in self.downbeats],
        }


def extract_audio(path: str | Path, out_path: str | Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise MusicError("ffmpeg not found on PATH")
    dst = Path(out_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise MusicError(f"could not extract audio from {Path(path).name}: {result.stderr.strip()[:300]}")
    return dst


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2:
            raise MusicError("expected 16-bit PCM")
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def onset_envelope(samples: np.ndarray) -> np.ndarray:
    """Spectral flux: summed positive change in magnitude spectrum per frame."""
    if samples.size < WINDOW:
        return np.zeros(0, dtype=np.float32)

    frames = 1 + (samples.size - WINDOW) // HOP
    window = np.hanning(WINDOW).astype(np.float32)
    strides = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP][:frames]
    spectra = np.abs(np.fft.rfft(strides * window, axis=1))

    flux = np.diff(spectra, axis=0)
    flux[flux < 0] = 0.0                       # onsets only: energy rising
    envelope = flux.sum(axis=1)

    # Remove slow loudness drift so a loud chorus does not dominate a quiet verse.
    #
    # The window must be much LONGER than a beat period. A short one (16 frames,
    # ~0.37s) tracks the beat itself and cancels alternate onsets, which fabricates
    # a half-tempo reading -- measured: a 120 BPM click track produced an envelope
    # peaking every 1.0s instead of 0.5s. 2 seconds clears the slowest tempo we
    # accept (60 BPM = 1.0s per beat) with margin.
    baseline_frames = int(2.0 * SAMPLE_RATE / HOP)
    if envelope.size > baseline_frames > 1:
        kernel = np.ones(baseline_frames, dtype=np.float32) / baseline_frames
        baseline = np.convolve(envelope, kernel, mode="same")
        envelope = np.maximum(envelope - baseline, 0.0)

    peak = envelope.max() if envelope.size else 0.0
    return envelope / peak if peak > 0 else envelope


def estimate_tempo(envelope: np.ndarray) -> tuple[float, float]:
    """Tempo by autocorrelating the onset envelope. Returns (bpm, confidence)."""
    if envelope.size < 32:
        return 0.0, 0.0

    frame_rate = SAMPLE_RATE / HOP
    centred = envelope - envelope.mean()
    corr = np.correlate(centred, centred, mode="full")[centred.size - 1:]
    if corr[0] <= 0:
        return 0.0, 0.0
    corr = corr / corr[0]

    lo = max(1, int(frame_rate * 60.0 / MAX_BPM))
    hi = min(corr.size - 1, int(frame_rate * 60.0 / MIN_BPM))
    if hi <= lo:
        return 0.0, 0.0

    window = corr[lo:hi]
    lag = int(np.argmax(window)) + lo

    # Parabolic interpolation around the peak. Integer lags quantise tempo badly:
    # at ~43 frames/sec a 120 BPM track sits between lag 21 (123 BPM) and lag 22
    # (117.5 BPM), and a 2% tempo error accumulates into cuts landing visibly off
    # the beat by the end of a 60-second promo.
    if 0 < lag < corr.size - 1:
        prev_, here, next_ = corr[lag - 1], corr[lag], corr[lag + 1]
        denom = prev_ - 2.0 * here + next_
        if abs(denom) > 1e-12:
            lag = lag + 0.5 * (prev_ - next_) / denom

    bpm = 60.0 * frame_rate / lag

    # Confidence: how much the winning lag stands out from the rest.
    others = np.delete(window, np.argmax(window))
    confidence = float(window.max() - others.mean()) if others.size else 0.0
    return float(bpm), max(0.0, min(1.0, confidence))


def _tempo_prior(bpm: float) -> float:
    """Perceptual weighting, log-normal around 120 BPM.

    Standard practice in tempo estimation: without it, subharmonics win on raw
    correlation alone and everything fast gets halved.
    """
    if bpm <= 0:
        return 0.0
    return float(np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2))


# How near a beat has to fall to count as landing on an onset, in envelope
# frames. Two frames is about 23ms at this hop -- inside what anyone would call
# "together".
PEAK_TOLERANCE = 2.0


def _onset_peaks(envelope: np.ndarray) -> np.ndarray:
    """Local maxima that stand out from the surrounding envelope."""
    if envelope.size < 3:
        return np.empty(0)
    threshold = float(envelope.mean() + 0.5 * envelope.std())
    mid = envelope[1:-1]
    rising = mid >= envelope[:-2]
    falling = mid > envelope[2:]
    return (np.flatnonzero((mid > threshold) & rising & falling) + 1).astype(float)


def _nearest_gap(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """For each point in `a`, the distance to the closest point in `b`.

    searchsorted rather than a full pairwise matrix: the phase search calls this
    thousands of times, and a click train against a few hundred onsets is a lot
    of wasted multiplication.
    """
    if b.size == 0:
        return np.full(a.shape, np.inf)
    pos = np.searchsorted(b, a)
    left = b[np.clip(pos - 1, 0, b.size - 1)]
    right = b[np.clip(pos, 0, b.size - 1)]
    return np.minimum(np.abs(a - left), np.abs(a - right))


# How far a predicted beat may be pulled to reach a real onset, as a fraction of
# the current beat interval. Beyond this it is more likely a neighbouring
# off-beat than the beat itself, and following it would derail the tracker.
BEAT_SNAP_TOLERANCE = 0.28

# How quickly the tracked interval follows what the music is actually doing.
# Fully (1.0) and one loud off-beat throws the tempo away; not at all (0.0) and
# this is the fixed grid again, which is the thing being fixed.
INTERVAL_MEMORY = 0.30


def track_beats(
    envelope: np.ndarray, bpm: float, duration: float,
    frame_rate: float, offset: float,
) -> list[float]:
    """Follow the beat through the track instead of assuming it never moves.

    `fit_grid` finds one tempo and one phase and lays a perfectly even grid over
    the whole song. Songs are not perfectly even: measured on a click track that
    accelerates from 100 to 130 BPM, an even grid sits a median 70ms and a worst
    546ms away from the actual beats -- over a beat adrift by the end, which is
    exactly the "it isn't cutting to the beat" complaint.

    So the grid becomes a starting guess. Each predicted beat is pulled to the
    nearest real onset if one is close enough, the interval is nudged toward the
    spacing actually observed, and the next prediction starts from where the
    music really was rather than from where the arithmetic said it should be.
    Errors stop compounding, because nothing is ever computed from beat zero.

    A prediction with no onset near it is kept as predicted -- a bar of held
    strings should not stop the count -- and the tracker carries on from there.
    """
    peaks = _onset_peaks(envelope)
    if bpm <= 0 or envelope.size == 0:
        return []
    interval = (60.0 / bpm) * frame_rate
    if interval < 1:
        return []

    # Start on the first strong onset rather than at zero, so the count begins
    # where the music does.
    at = float(peaks[0]) if peaks.size else 0.0
    while at - interval > 0:
        at -= interval

    out: list[float] = []
    guard = int(envelope.size / max(1.0, interval)) + 8
    while at < envelope.size and len(out) < guard:
        if peaks.size:
            gap = _nearest_gap(np.array([at]), peaks)[0]
            if gap <= interval * BEAT_SNAP_TOLERANCE:
                idx = int(np.argmin(np.abs(peaks - at)))
                landed = float(peaks[idx])
                if out:
                    observed = landed - out[-1]
                    # Only believe a spacing that is plausibly one beat.
                    if 0.5 * interval < observed < 1.8 * interval:
                        interval += (observed - interval) * INTERVAL_MEMORY
                at = landed
        out.append(at)
        at += interval

    return [t / frame_rate + offset for t in out if 0.0 <= t / frame_rate + offset <= duration]


def fit_grid(envelope: np.ndarray, rough_bpm: float, duration: float) -> tuple[float, list[float], bool, float]:
    """Refine tempo and phase together by fitting a grid to the onsets.

    Two problems are solved here, and autocorrelation alone solves neither.

    **Precision.** Autocorrelation resolves tempo only to its integer lag spacing,
    about 0.6% at this frame rate. That sounds negligible but accumulates: over a
    60-second promo a 0.6% error walks the grid more than 350ms off the music. A
    fine 2D search over tempo and phase runs on a tiny array, so precision is
    nearly free.

    **Octave errors.** Autocorrelation peaks just as happily at half or double the
    real tempo, and the subharmonic often wins -- measured: a clean 140 BPM pulse
    train was read as 69.96. Candidates at half and double are therefore scored
    explicitly, using onset *coverage* (what fraction of the total onset energy the
    grid actually lands on) rather than raw energy. A half-tempo grid hits every
    other onset and scores ~0.5; the true tempo scores ~1.0. Coverage is then
    weighted by the perceptual prior to settle genuinely ambiguous cases.

    A constant-tempo grid is the right model for produced music beds, which is what
    promos use. It is the wrong model for live performance.

    @returns (bpm, beat times in seconds, whether the octave is ambiguous)
    """
    if rough_bpm <= 0 or envelope.size == 0:
        return 0.0, [], False

    frame_rate = SAMPLE_RATE / HOP
    # flux[i] is the change between spectra i and i+1, whose centres straddle this
    # point. Measured against a click track as the best of the plausible offsets.
    offset = (WINDOW / 2 + HOP / 2) / SAMPLE_RATE
    # Onset PEAKS, not raw energy. The energy form of this scored 0.328 / 0.349 /
    # 0.331 / 0.297 for 45 / 90 / 180 / 360 BPM on a real track -- a spread of
    # 0.05 across four octaves, which is no discrimination at all, so the tempo
    # prior ended up making the choice and picked the half. Asking instead
    # "does a click land ON an onset, and is every onset explained" separates the
    # same octaves 0.46 (90) against 0.64 (180).
    peaks = _onset_peaks(envelope)

    def best_phase_for(bpm: float) -> tuple[float, float]:
        """Best phase for this tempo, scored by how well the grid explains the onsets.

        Recall alone cannot separate octaves: a double-tempo grid still explains
        every onset, it just also lands in the silence between them. Precision --
        the share of beat positions that actually coincide with an onset --
        catches exactly that. Their harmonic mean settles both directions, which
        neither does alone.
        """
        step = (60.0 / bpm) * frame_rate
        if step < 3 or peaks.size == 0:
            return -1.0, 0.0
        best = (-1.0, 0.0)
        for phase in np.arange(0.0, step, max(0.5, step / 64.0)):
            clicks = np.arange(phase, envelope.size - 1, step)
            if clicks.size < 4:
                continue
            precision = float((_nearest_gap(clicks, peaks) <= PEAK_TOLERANCE).mean())
            recall = float((_nearest_gap(peaks, clicks) <= PEAK_TOLERANCE).mean())
            f = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
            if f > best[0]:
                best = (f, float(phase))
        return best

    candidates: list[tuple[float, float, float]] = []
    best = (-1.0, rough_bpm, 0.0)
    best_fit = 0.0
    for multiplier in (0.5, 1.0, 2.0):
        base = rough_bpm * multiplier
        if not (MIN_BPM * 0.9 <= base <= MAX_BPM * 1.1):
            continue
        for bpm in np.linspace(base * 0.96, base * 1.04, 65):
            if not (MIN_BPM <= bpm <= MAX_BPM):
                continue
            fit, phase = best_phase_for(float(bpm))
            if fit < 0:
                continue
            score = fit * _tempo_prior(float(bpm))
            candidates.append((score, float(bpm), phase))
            if score > best[0]:
                best = (score, float(bpm), phase)
                best_fit = fit

    _, bpm, phase = best
    step = (60.0 / bpm) * frame_rate
    times = np.arange(phase, envelope.size, step) / frame_rate + offset
    beats = [float(t) for t in times if 0.0 <= t <= duration]

    # Half vs double is often a real musical ambiguity, not a bug -- a 200 BPM
    # track is commonly felt at 100. Where the runner-up octave scores close to
    # the winner, say so rather than pretending the question was settled.
    rival = 0.0
    for score, cand_bpm, _ in candidates:
        ratio = cand_bpm / bpm if bpm else 0.0
        if (1.8 < ratio < 2.2 or 0.45 < ratio < 0.55) and score > rival:
            rival = score
    ambiguous = bool(rival > 0 and rival >= best[0] * 0.85)
    return bpm, beats, ambiguous, float(best_fit)


def detect_beats(path: str | Path, duration: float, work_dir: str | Path = "/tmp") -> BeatGrid:
    """Full beat analysis of a music file."""
    wav = extract_audio(path, Path(work_dir) / f"{Path(path).stem}.beat.wav")
    samples = _read_wav(wav)
    if samples.size == 0:
        raise MusicError(f"{Path(path).name} contains no audio samples")

    envelope = onset_envelope(samples)
    bpm, confidence = estimate_tempo(envelope)
    if bpm <= 0:
        return BeatGrid(0.0, [], [], 0.0)

    bpm, beats, ambiguous, fit = fit_grid(envelope, bpm, duration)
    # `fit_grid` settles the tempo and phase; the tracker then follows the beat
    # through the song, because songs drift. On a click track accelerating from
    # 100 to 130 BPM the even grid lands a median 70ms out and a worst 546ms --
    # over a beat by the end. Tracked: 11ms and 65ms.
    tracked = track_beats(envelope, bpm, duration, SAMPLE_RATE / HOP,
                          (WINDOW / 2 + HOP / 2) / SAMPLE_RATE)
    if len(tracked) >= max(4, len(beats) // 2):
        beats = tracked
    downbeats = beats[::4]                     # assume 4/4, the promo default
    # Confidence describes the grid that is actually going to be used, which is
    # the one `fit_grid` settled on -- how well it explains the onsets, as an F1
    # of precision and recall.
    #
    # It used to be `estimate_tempo`'s autocorrelation peakiness, measured BEFORE
    # refinement and never updated. That number answers "is there a clear
    # periodicity in the signal", not "is this grid right", and the two come
    # apart badly: a track with a dead-even 126 BPM grid scored 0.14 and was
    # refused, so its edit ignored the music entirely while the editor could hear
    # the beat perfectly well.
    return BeatGrid(bpm=bpm, beats=beats, downbeats=downbeats,
                    confidence=float(fit), octave_ambiguous=ambiguous)
