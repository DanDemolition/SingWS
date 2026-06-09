"""Phrase-Aligned Song Start — waveform peaks + lightweight start suggestion.

Pure numpy/scipy (no Qt). Two jobs:
  * peaks(): a min/max envelope for drawing the waveform.
  * suggest_start(): a cheap, dependency-free heuristic that finds a likely
    intro-skip / "section or vocal start" from the audio energy and (optionally)
    snaps it to the nearest musical bar.

Deliberately NOT a beat tracker or structural segmenter — reliable Intro→Verse /
Verse→Chorus *labelling* needs ML (librosa et al.), which we keep out to protect
CPU and bundle stability. The result is a single generic "Suggested" point.
"""

from __future__ import annotations

import subprocess
from typing import Dict, List, Optional, Tuple

import numpy as np


def _ffmpeg() -> str:
    try:
        from python_karaoke_transport import _ffmpeg_path
        return _ffmpeg_path("ffmpeg")
    except Exception:
        import shutil
        path = shutil.which("ffmpeg")
        if path:
            return path
        raise RuntimeError("ffmpeg is required to decode audio for the waveform")


def decode_pcm_mono(path: str, sr: int = 8000, max_seconds: float = 720.0) -> np.ndarray:
    """Decode `path` to mono float32 PCM at `sr` Hz via ffmpeg. Low rate keeps it
    cheap (a 4-min song ≈ 2M samples). Returns a 1-D float32 array in [-1, 1]."""
    cmd = [
        _ffmpeg(), "-v", "quiet", "-nostdin",
        "-t", str(float(max_seconds)),
        "-i", str(path),
        "-ac", "1", "-ar", str(int(sr)), "-f", "f32le", "-",
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg decode failed for {path}")
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32, copy=False)


def peaks(pcm: np.ndarray, n_cols: int) -> np.ndarray:
    """min/max envelope per output column for the waveform.
    Returns an (n_cols, 2) array of (min, max) in [-1, 1]."""
    n_cols = max(1, int(n_cols))
    pcm = np.asarray(pcm, dtype=np.float32)
    out = np.zeros((n_cols, 2), dtype=np.float32)
    if pcm.size == 0:
        return out
    # Trim to a whole number of columns so reshape is exact, then min/max per bin.
    bin_size = max(1, pcm.size // n_cols)
    usable = bin_size * n_cols
    if usable <= 0:
        out[:, 0] = pcm.min()
        out[:, 1] = pcm.max()
        return out
    block = pcm[:usable].reshape(n_cols, bin_size)
    out[:, 0] = block.min(axis=1)
    out[:, 1] = block.max(axis=1)
    return out


def rms_envelope(pcm: np.ndarray, sr: int, hop_ms: float = 50.0) -> Tuple[np.ndarray, float]:
    """Short-time RMS energy. Returns (env, hop_seconds)."""
    pcm = np.asarray(pcm, dtype=np.float32)
    hop = max(1, int(sr * hop_ms / 1000.0))
    n = pcm.size // hop
    if n <= 0:
        return np.zeros(0, dtype=np.float32), hop / float(sr)
    block = pcm[: n * hop].reshape(n, hop)
    env = np.sqrt(np.mean(block * block, axis=1) + 1e-12).astype(np.float32)
    return env, hop / float(sr)


def _smooth(x: np.ndarray, w: int) -> np.ndarray:
    if x.size == 0 or w <= 1:
        return x
    k = np.ones(int(w), dtype=np.float32) / float(w)
    return np.convolve(x, k, mode="same").astype(np.float32)


def suggest_start(pcm: np.ndarray, sr: int, bpm: Optional[float] = None,
                  search_fraction: float = 0.35) -> Optional[float]:
    """Heuristic intro-skip point in seconds, or None.

    Idea: smooth the RMS energy, look at its positive rate-of-change (novelty),
    and take the strongest energy *rise* within the first `search_fraction` of
    the track — typically the moment the first full section / vocal kicks in.
    If `bpm` is given, snap the result to the nearest bar so it lands musically.
    Conservative: returns None when the signal is too short or has no clear rise.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size < sr:  # < 1s of audio
        return None
    env, hop_s = rms_envelope(pcm, sr)
    if env.size < 4:
        return None
    env = _smooth(env, max(2, int(0.2 / hop_s)))  # ~200ms smoothing
    novelty = np.diff(env, prepend=env[:1])
    novelty[novelty < 0] = 0.0  # only rises

    search_n = max(2, int(env.size * float(search_fraction)))
    window = novelty[:search_n]
    if window.size == 0 or float(window.max()) <= 0.0:
        return None
    # Require the rise to be meaningfully above the early-track noise floor.
    floor = float(np.median(novelty[:search_n]) + 1e-6)
    idx = int(np.argmax(window))
    if float(window[idx]) < floor * 3.0:
        return None
    seconds = idx * hop_s
    if seconds <= hop_s:  # rise is basically at t=0 → nothing to skip
        return None

    if bpm and bpm > 0:
        seconds_per_bar = 4 * 60.0 / float(bpm)  # 4/4
        if seconds_per_bar > 0:
            seconds = round(seconds / seconds_per_bar) * seconds_per_bar
    return float(max(0.0, seconds))


# ─────────────────── structural section / transition detection ───────────────
# Pure numpy/scipy (NO librosa). Boundaries come from a self-similarity matrix +
# Foote novelty and are reliable; the section *labels* (Intro/Verse/Chorus/
# Instrumental) are best-effort HEURISTIC estimates — real ML would be needed for
# high accuracy — so the host can rename/correct any detected marker.

SECTION_TYPES = ("Intro", "Verse", "Chorus", "Instrumental", "Outro")


def _hz2mel(f):
    return 2595.0 * np.log10(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel2hz(m):
    return 700.0 * (10.0 ** (np.asarray(m, dtype=np.float64) / 2595.0) - 1.0)


def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 32) -> np.ndarray:
    bins = n_fft // 2 + 1
    mels = np.linspace(_hz2mel(0.0), _hz2mel(sr / 2.0), n_mels + 2)
    hz = _mel2hz(mels)
    binf = np.clip(np.floor((n_fft + 1) * hz / sr).astype(int), 0, bins - 1)
    fb = np.zeros((n_mels, bins), dtype=np.float32)
    for m in range(1, n_mels + 1):
        l, c, r = binf[m - 1], binf[m], binf[m + 1]
        c = max(c, l + 1)
        r = max(r, c + 1)
        l = min(l, bins - 2); c = min(c, bins - 1); r = min(r, bins - 1)
        if c > l:
            fb[m - 1, l:c] = (np.arange(l, c) - l) / float(c - l)
        if r > c:
            fb[m - 1, c:r] = (r - np.arange(c, r)) / float(r - c)
    return fb


def _stft_mag(pcm: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size < n_fft:
        pcm = np.pad(pcm, (0, n_fft - pcm.size))
    n_frames = 1 + (pcm.size - n_fft) // hop
    if n_frames < 1:
        return np.zeros((0, n_fft // 2 + 1), dtype=np.float32)
    win = np.hanning(n_fft).astype(np.float32)
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = pcm[idx] * win
    return np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)


def _chroma(mag: np.ndarray, sr: int, n_fft: int) -> np.ndarray:
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    pc = np.full(mag.shape[1], -1, dtype=int)
    nz = freqs > 0
    midi = 69.0 + 12.0 * np.log2((freqs[nz] + 1e-9) / 440.0)
    pc[nz] = np.round(midi).astype(int) % 12
    chroma = np.zeros((mag.shape[0], 12), dtype=np.float32)
    for c in range(12):
        mask = pc == c
        if mask.any():
            chroma[:, c] = mag[:, mask].sum(axis=1)
    return chroma


def _timbre(mag: np.ndarray, melfb: np.ndarray, n_mfcc: int = 12) -> np.ndarray:
    # Drop coefficient 0 (overall log-energy) so timbre describes spectral SHAPE,
    # not loudness — otherwise a quiet→loud jump dwarfs every other boundary.
    from scipy.fft import dct
    logmel = np.log(mag @ melfb.T + 1e-6)
    return dct(logmel, type=2, axis=1, norm="ortho")[:, 1:n_mfcc + 1].astype(np.float32)


def _l2norm_rows(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (x / n).astype(np.float32)


def _checkerboard_kernel(M: int) -> np.ndarray:
    g = np.arange(-M, M)
    gx, gy = np.meshgrid(g, g)
    gauss = np.exp(-0.5 * ((gx / (M / 2.0)) ** 2 + (gy / (M / 2.0)) ** 2))
    return (np.sign(gx * gy) * gauss).astype(np.float32)


def _foote_novelty(ssm: np.ndarray, M: int) -> np.ndarray:
    T = ssm.shape[0]
    if T == 0:
        return np.zeros(0, dtype=np.float32)
    kern = _checkerboard_kernel(M)
    P = np.pad(ssm, M, mode="edge")
    nov = np.empty(T, dtype=np.float32)
    for i in range(T):
        nov[i] = float(np.sum(P[i:i + 2 * M, i:i + 2 * M] * kern))
    nov[nov < 0] = 0.0
    return nov


def _segment_descriptors(mag, sr, n_fft, ssm, bounds):
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / sr)
    vocal_band = (freqs >= 200.0) & (freqs <= 4000.0)
    energy = mag.sum(axis=1) + 1e-9
    vocal = mag[:, vocal_band].sum(axis=1)
    segs = []
    T = ssm.shape[0]
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        b = max(b, a + 1)
        e = float(np.mean(energy[a:b]))
        v = float(np.mean(vocal[a:b]) / np.mean(energy[a:b]))
        mask = np.ones(T, dtype=bool)
        mask[a:b] = False
        rep = float(np.mean(ssm[a:b][:, mask])) if mask.any() else 0.0
        segs.append({"energy": e, "vocal": v, "repetition": rep})
    return segs


def _label_segments(segs: List[Dict]) -> List[str]:
    n = len(segs)
    if n == 0:
        return []
    E = np.array([s["energy"] for s in segs], dtype=np.float64)
    V = np.array([s["vocal"] for s in segs], dtype=np.float64)
    R = np.array([s["repetition"] for s in segs], dtype=np.float64)
    Emax = E.max() if E.max() > 0 else 1.0
    Vmed = float(np.median(V)) if n else 0.0
    vocal = V >= max(1e-6, 0.6 * Vmed)

    types = ["Verse"] * n
    for i in range(n):
        if not vocal[i]:
            types[i] = "Instrumental"

    vidx = [i for i in range(n) if vocal[i]]
    if vidx:
        Rv = R[vidx]; Ev = E[vidx]
        Rn = (Rv - Rv.min()) / (np.ptp(Rv) + 1e-9)
        En = (Ev - Ev.min()) / (np.ptp(Ev) + 1e-9)
        comb = Rn + En
        thr = comb.max() * 0.7 if comb.size else 0.0
        for j, i in enumerate(vidx):
            types[i] = "Chorus" if (comb.max() > 0 and comb[j] >= thr) else "Verse"

    if E[0] < 0.5 * Emax or not vocal[0]:
        types[0] = "Intro"
    if n > 1 and (E[-1] < 0.5 * Emax or not vocal[-1]):
        types[-1] = "Outro"
    return types


def _transition_label(a: str, b: str) -> str:
    return f"{a}→{b}"


def detect_sections(pcm: np.ndarray, sr: int, bpm: Optional[float] = None,
                    *, max_seconds: float = 360.0, hop_s: float = 0.25,
                    n_fft: int = 2048, max_markers: int = 8) -> List[Dict]:
    """Detect structural boundaries and label the transitions.

    Returns a time-sorted list of dicts: {seconds, label ("Verse→Chorus" …),
    from_type, to_type, confidence (0..1)} for each internal boundary (t=0
    excluded). Pure numpy/scipy, one-shot; deterministic. Labels are heuristic
    estimates (see module note). Returns [] for short/ambiguous audio.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size < sr * 8:  # need ~8s before "sections" mean anything
        return []
    if max_seconds and pcm.size > int(sr * max_seconds):
        pcm = pcm[:int(sr * max_seconds)]

    hop = max(1, int(sr * hop_s))
    mag = _stft_mag(pcm, n_fft, hop)
    if mag.shape[0] < 8:
        return []

    melfb = _mel_filterbank(sr, n_fft, 32)
    feat = _l2norm_rows(np.concatenate(
        [_l2norm_rows(_chroma(mag, sr, n_fft)), _l2norm_rows(_timbre(mag, melfb))], axis=1))
    ssm = feat @ feat.T

    M = max(4, int(round(4.0 / hop_s)))  # ~4s half-kernel
    nov = _foote_novelty(ssm, M)
    if nov.max() <= 0:
        return []
    novn = nov / nov.max()

    try:
        from scipy.signal import find_peaks
    except Exception:
        return []
    min_dist = max(1, int(round(5.0 / hop_s)))
    # Prominence is local, so a single very-strong boundary (e.g. a quiet intro)
    # doesn't suppress the rest; a low height floor still rejects flat noise.
    peaks_idx, _props = find_peaks(novn, distance=min_dist, height=0.06, prominence=0.05)
    if peaks_idx.size == 0:
        return []

    bounds = [0] + [int(p) for p in peaks_idx] + [mag.shape[0]]
    types = _label_segments(_segment_descriptors(mag, sr, n_fft, ssm, bounds))

    spb = (4 * 60.0 / float(bpm)) if (bpm and bpm > 0) else 0.0
    results = []
    for k, pidx in enumerate(peaks_idx):
        sec = float(pidx) * hop_s
        if spb > 0:
            sec = round(sec / spb) * spb
        prev_t = types[k] if k < len(types) else "Verse"
        next_t = types[k + 1] if (k + 1) < len(types) else "Verse"
        results.append({
            "seconds": max(0.0, float(sec)),
            "label": _transition_label(prev_t, next_t),
            "from_type": prev_t,
            "to_type": next_t,
            "confidence": float(novn[pidx]),
        })

    # Keep the strongest, then sort by time and drop near-duplicates (<5s apart).
    results.sort(key=lambda r: -r["confidence"])
    results = results[:max_markers]
    results.sort(key=lambda r: r["seconds"])
    deduped: List[Dict] = []
    for r in results:
        if deduped and abs(r["seconds"] - deduped[-1]["seconds"]) < 5.0:
            continue
        deduped.append(r)
    return deduped


# ───────────────────────────── tempo (BPM) estimate ─────────────────────────
def estimate_bpm(pcm: np.ndarray, sr: int,
                 min_bpm: float = 70.0, max_bpm: float = 180.0) -> Optional[float]:
    """Estimate tempo (BPM) from the audio — no tags needed. Pure numpy/scipy.

    Onset-strength envelope (positive spectral flux) → FFT autocorrelation →
    pick the strongest lag in the plausible tempo range, weighted by a soft
    prior around 120 BPM to suppress half/double-tempo octave errors. Returns a
    rounded BPM, or None when the audio is too short / has no clear pulse.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.size < sr * 5:
        return None
    n_fft = 1024
    hop = max(1, int(sr * 0.01))  # 10 ms
    mag = _stft_mag(pcm, n_fft, hop)
    if mag.shape[0] < 16:
        return None

    flux = np.diff(mag, axis=0)
    flux[flux < 0] = 0.0
    onset = flux.sum(axis=1).astype(np.float32)
    onset -= _smooth(onset, 16)  # high-pass the envelope (drop slow drift)
    onset[onset < 0] = 0.0
    if onset.max() <= 0:
        return None
    onset = onset / onset.max()

    fps = sr / float(hop)
    n = onset.size
    spec = np.fft.rfft(onset, n=2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n].real
    if ac.max() <= 0:
        return None

    min_lag = max(1, int(fps * 60.0 / max_bpm))
    max_lag = min(n - 1, int(fps * 60.0 / min_bpm))
    if max_lag <= min_lag:
        return None
    lags = np.arange(min_lag, max_lag + 1)
    bpms = 60.0 * fps / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    strength = ac[min_lag:max_lag + 1] * prior
    if strength.max() <= 0:
        return None
    best_bpm = float(bpms[int(np.argmax(strength))])
    return round(best_bpm, 1)
