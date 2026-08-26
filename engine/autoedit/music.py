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
        return 60.0 / self.bpm if self.bpm else 0.0

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
    total_energy = float(envelope.sum()) or 1.0

    peak = float(envelope.max()) or 1.0

    def best_phase_for(bpm: float) -> tuple[float, float]:
        """Best phase for this tempo, scored by how well the grid explains the onsets.

        Recall alone (energy captured / total) cannot separate octaves: a
        double-tempo grid still captures every onset, it just also lands on the
        silence between them. Precision -- mean energy per beat position -- catches
        exactly that, scoring 1.0 at the true tempo and 0.5 at the double. Their
        harmonic mean settles both directions, which neither does alone.
        """
        step = (60.0 / bpm) * frame_rate
        if step < 2:
            return -1.0, 0.0
        best = (-1.0, 0.0)
        for phase in np.arange(0.0, step, max(0.1, step / 256.0)):
            idx = np.round(np.arange(phase, envelope.size - 1, step)).astype(int)
            idx = idx[(idx >= 0) & (idx < envelope.size)]
            if not idx.size:
                continue
            captured = float(envelope[idx].sum())
            recall = captured / total_energy
            precision = (captured / idx.size) / peak
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
