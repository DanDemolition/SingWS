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
from typing import Optional, Tuple

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
