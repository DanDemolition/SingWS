"""10-band graphic EQ for SingWS — real-time-safe edition.

A single ``GraphicEQ`` instance turns 10 gain values (in dB) into a cascade
of peaking biquads, then runs PCM audio through them with
``scipy.signal.sosfilt`` for low-latency real-time processing.

Designed to be called from audio threads (BASS DSP callbacks, karaoke
decoder workers) without producing glitches:

* SOS matrix is held as a single numpy array, snapshot-locked per call.
* Filter state and a scratch workspace are pre-allocated and reused —
  zero new allocations per processing call once warmed up.
* Both channels are processed in a single ``sosfilt`` invocation using
  the ``axis=0`` mode (vastly faster than the older per-channel loop).
* Disabled / flat fast-paths bypass all work and return the input
  untouched.
"""

from __future__ import annotations

import math
import threading
from typing import Sequence

import numpy as np
from scipy.signal import sosfilt


# Standard ISO 10-band centre frequencies used by every consumer graphic EQ.
DEFAULT_BANDS_HZ: tuple[float, ...] = (
    31.5, 63.0, 125.0, 250.0, 500.0,
    1000.0, 2000.0, 4000.0, 8000.0, 16000.0,
)

DEFAULT_Q = 1.4  # roughly one octave wide; the canonical graphic-EQ choice


def _peaking_biquad(freq: float, gain_db: float, q: float, sample_rate: float) -> tuple[float, ...]:
    """Return the (b0, b1, b2, a0, a1, a2) coefficients of an RBJ peaking biquad."""
    A = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * float(freq) / float(sample_rate)
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * float(q))

    b0 = 1.0 + alpha * A
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / A
    return b0, b1, b2, a0, a1, a2


PRESETS: dict[str, tuple[float, ...]] = {
    "Flat":         (0,   0,   0,   0,   0,   0,   0,   0,   0,   0),
    "Vocal Boost":  (-2, -2,  -1,   0,   2,   4,   5,   4,   2,   0),
    "Rock":         (5,   4,   3,   1,  -1,  -1,   1,   3,   4,   5),
    "Bass Boost":   (7,   6,   5,   3,   1,   0,   0,   0,   0,   0),
    "Treble Boost": (0,   0,   0,   0,   0,   1,   3,   5,   6,   7),
    "Loudness":     (5,   4,   1,   0,  -1,   0,   0,   2,   4,   6),
    "Karaoke Vocal":(-3, -3,  -2,  -1,   1,   3,   4,   3,   2,   0),
}


class GraphicEQ:
    """A real-time-safe 10-band peaking graphic EQ."""

    def __init__(
        self,
        sample_rate: float = 44100.0,
        channels: int = 2,
        bands_hz: Sequence[float] = DEFAULT_BANDS_HZ,
        q: float = DEFAULT_Q,
    ):
        self._lock = threading.RLock()
        self._sample_rate = float(sample_rate)
        self._channels = max(1, int(channels))
        self._bands_hz = tuple(float(f) for f in bands_hz)
        self._q = float(q)
        self._gains_db = [0.0] * len(self._bands_hz)
        self._enabled = False
        # Cached "is the band gain effectively zero" — recomputed whenever
        # gains change so the audio-thread fast path doesn't have to iterate.
        self._is_flat_cached = True
        # SOS coefficient matrix in float64 (scipy's native dtype).  Shape
        # is (n_sections, 6) — single numpy array so the audio thread can
        # snapshot it with a cheap reference assignment under the lock.
        self._sos = self._build_sos()
        # Filter state.  Shape is (n_sections, n_channels, 2); pre-allocated
        # lazily on the first processing call so we never allocate from the
        # audio thread once steady-state.
        self._zi: np.ndarray | None = None
        # Reusable scratch buffer for the float64 working copy of the input
        # block (sosfilt may upcast internally so we feed it a single
        # contiguous float64 array sized to the typical callback buffer).
        self._scratch_f64: np.ndarray | None = None
        # Output workspace in float32 — what we ultimately return.
        self._workspace_f32: np.ndarray | None = None

    # ---------------- configuration ----------------

    def configure_stream(self, sample_rate: float, channels: int):
        """Tell the EQ what audio format to expect; rebuilds coefficients."""
        sr = float(sample_rate)
        ch = max(1, int(channels))
        with self._lock:
            if abs(sr - self._sample_rate) < 0.5 and ch == self._channels:
                return
            self._sample_rate = sr
            self._channels = ch
            self._sos = self._build_sos()
            # Stream dims changed — drop pre-allocated state so it's
            # rebuilt to the new shape on next call.
            self._zi = None
            self._scratch_f64 = None
            self._workspace_f32 = None

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = bool(enabled)

    def enabled(self) -> bool:
        return self._enabled

    def gains_db(self) -> list[float]:
        with self._lock:
            return list(self._gains_db)

    def set_gain_db(self, band_index: int, value_db: float):
        with self._lock:
            if not (0 <= band_index < len(self._gains_db)):
                return
            self._gains_db[band_index] = float(value_db)
            self._sos = self._build_sos()
            self._is_flat_cached = self._compute_is_flat()

    def set_all_gains_db(self, gains: Sequence[float]):
        with self._lock:
            n = min(len(gains), len(self._gains_db))
            for i in range(n):
                self._gains_db[i] = float(gains[i])
            self._sos = self._build_sos()
            self._is_flat_cached = self._compute_is_flat()

    def apply_preset(self, name: str):
        preset = PRESETS.get(name)
        if preset is None:
            return
        self.set_all_gains_db(preset)

    def is_flat(self) -> bool:
        return self._is_flat_cached

    def _compute_is_flat(self) -> bool:
        return all(abs(g) < 0.05 for g in self._gains_db)

    # ---------------- internal ----------------

    def _build_sos(self) -> np.ndarray:
        rows = []
        for freq, gain_db in zip(self._bands_hz, self._gains_db):
            b0, b1, b2, a0, a1, a2 = _peaking_biquad(
                freq, gain_db, self._q, self._sample_rate,
            )
            rows.append([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0])
        return np.asarray(rows, dtype=np.float64)

    def _ensure_workspaces(self, n_frames: int, n_channels: int):
        n_sections = self._sos.shape[0]
        if (self._zi is None
                or self._zi.shape != (n_sections, n_channels, 2)):
            self._zi = np.zeros((n_sections, n_channels, 2), dtype=np.float64)
        if (self._scratch_f64 is None
                or self._scratch_f64.shape != (n_frames, n_channels)):
            self._scratch_f64 = np.empty((n_frames, n_channels), dtype=np.float64)
        if (self._workspace_f32 is None
                or self._workspace_f32.shape != (n_frames, n_channels)):
            self._workspace_f32 = np.empty((n_frames, n_channels), dtype=np.float32)

    def reset_state(self):
        """Zero internal filter state (e.g. after a seek)."""
        with self._lock:
            if self._zi is not None:
                self._zi.fill(0.0)

    # ---------------- processing ----------------

    def process_f32_bytes(self, data: bytes) -> bytes:
        """Run interleaved float32 PCM bytes through the EQ.

        Returns processed bytes (same length/layout).  Bypassed at near-
        zero cost if disabled or flat.
        """
        if not data or not self._enabled or self._is_flat_cached:
            return data
        try:
            n_total = len(data) // 4
            ch = self._channels
            if n_total <= 0 or n_total % ch != 0:
                return data
            n_frames = n_total // ch
            samples = np.frombuffer(data, dtype=np.float32).reshape(n_frames, ch)
            processed = self._process_inplace(samples)
            if processed is samples:
                return data
            return processed.tobytes()
        except Exception:
            return data

    def process_f32_array(self, samples: np.ndarray) -> np.ndarray:
        """Process an already-deinterleaved (n_frames, n_channels) float32 array."""
        if not self._enabled or self._is_flat_cached:
            return samples
        try:
            return self._process_inplace(samples)
        except Exception:
            return samples

    def _process_inplace(self, samples: np.ndarray) -> np.ndarray:
        """Shared core for both byte- and array-based entry points.

        The two-step lock dance keeps the audio thread holding the lock for
        only the duration of an SOS reference snapshot — actual filtering
        runs lock-free so a UI thread tweaking a slider can't stall audio.
        """
        # Snapshot the SOS matrix under the lock so it can't be replaced
        # mid-processing.  numpy arrays are reference-shared, so this is
        # cheap.
        with self._lock:
            sos = self._sos
            n_frames, n_channels = samples.shape[0], samples.shape[1] if samples.ndim > 1 else 1
            self._ensure_workspaces(n_frames, n_channels)
            zi = self._zi
            scratch = self._scratch_f64
            out_f32 = self._workspace_f32

        # Single sosfilt call processes ALL channels at once (axis=0).
        # Feed it a contiguous float64 copy to avoid scipy doing the
        # upcast on every section internally.
        np.copyto(scratch, samples, casting="unsafe")
        y, new_zi = sosfilt(sos, scratch, axis=0, zi=zi)
        # Update filter state for next call.
        np.copyto(zi, new_zi)
        # Clamp to [-1, 1] so heavy boosts don't overflow the float
        # interpretation downstream, then narrow back to float32.
        np.clip(y, -1.0, 1.0, out=y)
        np.copyto(out_f32, y, casting="unsafe")
        return out_f32
