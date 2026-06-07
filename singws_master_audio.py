"""Master audio processing for SingWS — a light, real-time-safe "mix bus" chain.

Goal: make songs come out at a more consistent perceived volume and sound a
little more polished — the spirit of a gentle dbx 266xs (gate → compressor →
limiter) feeding a BBE-Sonic-Maximizer-style tilt EQ, but conservative enough
that it never pumps, clips, or makes a quiet intro jump out.

Design notes
------------
* Runs in the *karaoke decode thread* (the same place the 10-band ``GraphicEQ``
  already runs ``scipy`` filters), NOT inside a realtime BASS callback. That is
  what makes a numpy/scipy chain safe here.
* Everything is **vectorised**: envelope detectors and gain smoothing are
  one-pole IIR filters run through ``scipy.signal.lfilter`` (state carried
  across blocks), and the shelves use ``sosfilt``. No per-sample Python loops,
  so cost is in the same ballpark as the existing EQ.
* A single detection signal drives the dynamics so the **same gain is applied
  to both channels** — the stereo image is never pulled left/right.
* Disabled / not-configured fast paths return the input untouched at near-zero
  cost, so Performance Mode (which simply never attaches a processor) and the
  off state are free.
* Signal order: Gate/expander → tilt EQ (+optional exciter) → compressor →
  makeup → brickwall-safety limiter → hard clip guard.

The chain is intended to sit *after* per-track loudness normalization (which
SingWS bakes in upstream via ffmpeg ``volume``). It therefore complements
normalization — the per-track gain gets levels into the right area, and the
compressor/limiter shave the remaining peaks and even out the ride.
"""

from __future__ import annotations

import math
import threading
from typing import Sequence

import numpy as np
from scipy.signal import lfilter, sosfilt


# ----------------------------------------------------------------------------
# Conservative defaults — "improve without overdoing it".
# ----------------------------------------------------------------------------
DEFAULT_PARAMS: dict[str, float] = {
    # Downward expander / gate. Off by default: music tails are easy to chew up,
    # so it only earns its keep on noisy sources. Very low threshold + gentle
    # ratio when enabled.
    "gate_enabled": 0.0,
    "gate_threshold_db": -58.0,
    "gate_ratio": 1.6,            # 1.0 = off; gentle downward expansion
    "gate_attack_ms": 5.0,
    "gate_release_ms": 140.0,
    "gate_floor_db": -18.0,       # max attenuation the expander may apply

    # Tilt / enhancement EQ (BBE-ish: tighten lows, add air + a little presence).
    "eq_enabled": 1.0,
    "low_shelf_hz": 90.0,
    "low_shelf_db": 1.0,
    "presence_hz": 3200.0,
    "presence_db": 1.0,
    "presence_q": 0.7,
    "high_shelf_hz": 9000.0,
    "high_shelf_db": 1.5,
    # Harmonic exciter mix (0..1). 0 by default — nonlinearity is the easiest way
    # to add distortion, so it stays opt-in. When >0, a high-passed, softly
    # saturated copy is blended in for "air".
    "exciter_mix": 0.0,
    "exciter_hz": 4000.0,

    # Compressor (soft-knee, gentle leveling — slow enough to avoid pumping).
    "comp_enabled": 1.0,
    "comp_threshold_db": -20.0,
    "comp_ratio": 2.0,
    "comp_knee_db": 6.0,
    "comp_attack_ms": 18.0,
    "comp_release_ms": 180.0,
    "comp_makeup_db": 4.0,

    # Brickwall-safety limiter. Catches the peaks the compressor lets through so
    # nothing clips, with a fast-but-smooth peak detector.
    "limiter_enabled": 1.0,
    "limiter_ceiling_db": -1.0,
    "limiter_detector_ms": 1.2,
    "limiter_release_ms": 80.0,

    # Final hard clip guard (true ceiling). Slightly below 0 dBFS.
    "output_ceiling_db": -0.1,
}


def _db_to_lin(db: float) -> float:
    return float(10.0 ** (float(db) / 20.0))


def _one_pole_alpha(time_ms: float, sample_rate: float) -> float:
    """One-pole smoothing coefficient for a given time constant."""
    tau = max(1e-4, float(time_ms) / 1000.0)
    return float(math.exp(-1.0 / (max(1.0, float(sample_rate)) * tau)))


def _low_shelf(freq, gain_db, sr, q=0.707):
    """RBJ low-shelf biquad -> (b0,b1,b2,a0,a1,a2)."""
    A = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * float(freq) / float(sr)
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / 2.0 * math.sqrt((A + 1.0 / A) * (1.0 / q - 1.0) + 2.0)
    tsa = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1) - (A - 1) * cw + tsa)
    b1 = 2 * A * ((A - 1) - (A + 1) * cw)
    b2 = A * ((A + 1) - (A - 1) * cw - tsa)
    a0 = (A + 1) + (A - 1) * cw + tsa
    a1 = -2 * ((A - 1) + (A + 1) * cw)
    a2 = (A + 1) + (A - 1) * cw - tsa
    return b0, b1, b2, a0, a1, a2


def _high_shelf(freq, gain_db, sr, q=0.707):
    A = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * float(freq) / float(sr)
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / 2.0 * math.sqrt((A + 1.0 / A) * (1.0 / q - 1.0) + 2.0)
    tsa = 2.0 * math.sqrt(A) * alpha
    b0 = A * ((A + 1) + (A - 1) * cw + tsa)
    b1 = -2 * A * ((A - 1) + (A + 1) * cw)
    b2 = A * ((A + 1) + (A - 1) * cw - tsa)
    a0 = (A + 1) - (A - 1) * cw + tsa
    a1 = 2 * ((A - 1) - (A + 1) * cw)
    a2 = (A + 1) - (A - 1) * cw - tsa
    return b0, b1, b2, a0, a1, a2


def _peaking(freq, gain_db, sr, q=0.7):
    A = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * math.pi * float(freq) / float(sr)
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * float(q))
    b0 = 1 + alpha * A
    b1 = -2 * cw
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * cw
    a2 = 1 - alpha / A
    return b0, b1, b2, a0, a1, a2


def _highpass(freq, sr, q=0.707):
    w0 = 2.0 * math.pi * float(freq) / float(sr)
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2.0 * float(q))
    b0 = (1 + cw) / 2.0
    b1 = -(1 + cw)
    b2 = (1 + cw) / 2.0
    a0 = 1 + alpha
    a1 = -2 * cw
    a2 = 1 - alpha
    return b0, b1, b2, a0, a1, a2


class MasterAudioProcessor:
    """Light mastering chain for the karaoke song bus. See module docstring."""

    def __init__(self, sample_rate: float = 44100.0, channels: int = 2,
                 params: dict | None = None):
        self._lock = threading.RLock()
        self._enabled = False
        self._sample_rate = float(sample_rate)
        self._channels = max(1, int(channels))
        self._params = dict(DEFAULT_PARAMS)
        if params:
            self.set_params(params)
        self._rebuild()

    # ---------------- configuration ----------------

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = bool(enabled)

    def enabled(self) -> bool:
        return self._enabled

    def set_params(self, params: dict):
        with self._lock:
            for k, v in (params or {}).items():
                if k in self._params:
                    try:
                        self._params[k] = float(v)
                    except Exception:
                        pass
            self._rebuild()

    def params(self) -> dict:
        with self._lock:
            return dict(self._params)

    def configure_stream(self, sample_rate: float, channels: int):
        sr = float(sample_rate)
        ch = max(1, int(channels))
        with self._lock:
            if abs(sr - self._sample_rate) < 0.5 and ch == self._channels:
                return
            self._sample_rate = sr
            self._channels = ch
            self._rebuild()

    def reset_state(self):
        """Clear all filter/envelope memory (e.g. after a seek)."""
        with self._lock:
            self._reset_state_locked()

    # ---------------- internal build ----------------

    def _reset_state_locked(self):
        ch = self._channels
        # EQ shelf state (sosfilt zi): (n_sections, ch, 2)
        if self._eq_sos is not None and self._eq_sos.shape[0] > 0:
            self._eq_zi = np.zeros((self._eq_sos.shape[0], ch, 2), dtype=np.float64)
        else:
            self._eq_zi = None
        # Exciter high-pass state.
        if self._exc_sos is not None and self._exc_sos.shape[0] > 0:
            self._exc_zi = np.zeros((self._exc_sos.shape[0], ch, 2), dtype=np.float64)
        else:
            self._exc_zi = None
        # One-pole detector/smoother states (mono envelopes -> scalar state).
        # Detectors start at silence (0). Gain smoothers are seeded at UNITY so
        # the first block plays at full level instead of ramping up from zero
        # (which would briefly duck the song's opening).  For the one-pole
        # y[n] = (1-a)x[n] + a*y[n-1], the lfilter state that holds output v is
        # a*v, hence the `*_rel_a` seeding below.
        self._gate_det_zi = np.zeros(1, dtype=np.float64)
        self._gate_gain_zi = np.array([self._gate_rel_a], dtype=np.float64)
        self._gate_gain_last = 1.0
        self._comp_det_zi = np.zeros(1, dtype=np.float64)
        self._comp_gain_zi = np.array([self._comp_rel_a], dtype=np.float64)
        self._comp_gain_last = 1.0
        self._lim_det_zi = np.zeros(1, dtype=np.float64)
        self._lim_gain_zi = np.array([self._lim_rel_a], dtype=np.float64)
        self._lim_gain_last = 1.0

    def _rebuild(self):
        p = self._params
        sr = self._sample_rate

        # --- EQ shelves / presence ---
        sos_rows = []
        if p["eq_enabled"] >= 0.5:
            if abs(p["low_shelf_db"]) > 0.01:
                sos_rows.append(_low_shelf(p["low_shelf_hz"], p["low_shelf_db"], sr))
            if abs(p["presence_db"]) > 0.01:
                sos_rows.append(_peaking(p["presence_hz"], p["presence_db"], sr, p["presence_q"]))
            if abs(p["high_shelf_db"]) > 0.01:
                sos_rows.append(_high_shelf(p["high_shelf_hz"], p["high_shelf_db"], sr))
        self._eq_sos = self._norm_sos(sos_rows)

        # --- Exciter high-pass (only if mix > 0) ---
        if p["exciter_mix"] > 0.001:
            self._exc_sos = self._norm_sos([_highpass(p["exciter_hz"], sr)])
        else:
            self._exc_sos = None

        # --- Precompute coefficients ---
        self._gate_det_a = _one_pole_alpha(p["gate_attack_ms"], sr)
        self._gate_rel_a = _one_pole_alpha(p["gate_release_ms"], sr)
        self._comp_det_a = _one_pole_alpha(p["comp_attack_ms"], sr)
        self._comp_rel_a = _one_pole_alpha(p["comp_release_ms"], sr)
        self._lim_det_a = _one_pole_alpha(p["limiter_detector_ms"], sr)
        self._lim_rel_a = _one_pole_alpha(p["limiter_release_ms"], sr)

        self._gate_thr_lin = _db_to_lin(p["gate_threshold_db"])
        self._gate_floor_lin = _db_to_lin(p["gate_floor_db"])
        self._comp_makeup_lin = _db_to_lin(p["comp_makeup_db"])
        self._lim_ceiling_lin = _db_to_lin(p["limiter_ceiling_db"])
        self._out_ceiling_lin = _db_to_lin(p["output_ceiling_db"])

        self._reset_state_locked()

    @staticmethod
    def _norm_sos(rows):
        if not rows:
            return None
        out = []
        for (b0, b1, b2, a0, a1, a2) in rows:
            out.append([b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0])
        return np.asarray(out, dtype=np.float64)

    # ---------------- one-pole helper ----------------

    @staticmethod
    def _smooth(x: np.ndarray, alpha: float, zi: np.ndarray):
        """Vectorised one-pole low-pass with carried state. Returns (y, new_zi)."""
        b = [1.0 - alpha]
        a = [1.0, -alpha]
        y, new_zi = lfilter(b, a, x, zi=zi)
        return y, new_zi

    # ---------------- processing ----------------

    def process_f32_bytes(self, data: bytes) -> bytes:
        if not data or not self._enabled:
            return data
        try:
            ch = self._channels
            n_total = len(data) // 4
            if n_total <= 0 or n_total % ch != 0:
                return data
            n_frames = n_total // ch
            samples = np.frombuffer(data, dtype=np.float32).reshape(n_frames, ch)
            out = self._process(samples)
            if out is samples:
                return data
            return out.astype(np.float32, copy=False).tobytes()
        except Exception:
            return data

    def process_f32_array(self, samples: np.ndarray) -> np.ndarray:
        if not self._enabled:
            return samples
        try:
            return self._process(samples)
        except Exception:
            return samples

    def _process(self, samples: np.ndarray) -> np.ndarray:
        with self._lock:
            p = self._params
            x = samples.astype(np.float64, copy=True)
            if x.ndim == 1:
                x = x.reshape(-1, 1)
            n, ch = x.shape

            # ---- Gate / downward expander ----
            if p["gate_enabled"] >= 0.5 and p["gate_ratio"] > 1.0:
                det = np.abs(x).mean(axis=1)
                env, self._gate_det_zi = self._smooth(det, self._gate_det_a, self._gate_det_zi)
                env = np.maximum(env, 1e-7)
                env_db = 20.0 * np.log10(env)
                thr_db = p["gate_threshold_db"]
                ratio = p["gate_ratio"]
                # Below threshold, expand downward by (ratio-1) of the deficit.
                deficit = np.minimum(0.0, env_db - thr_db)
                gain_db = deficit * (ratio - 1.0)
                gain_db = np.maximum(gain_db, p["gate_floor_db"])
                gtarget = np.power(10.0, gain_db / 20.0)
                gsm, self._gate_gain_zi = self._smooth(gtarget, self._gate_rel_a, self._gate_gain_zi)
                self._gate_gain_last = float(gsm[-1])
                x *= gsm[:, None]

            # ---- Tilt EQ ----
            if self._eq_sos is not None:
                if self._eq_zi is None or self._eq_zi.shape != (self._eq_sos.shape[0], ch, 2):
                    self._eq_zi = np.zeros((self._eq_sos.shape[0], ch, 2), dtype=np.float64)
                y, self._eq_zi = sosfilt(self._eq_sos, x, axis=0, zi=self._eq_zi)
                x = y

            # ---- Exciter (subtle harmonic air) ----
            if self._exc_sos is not None and p["exciter_mix"] > 0.001:
                if self._exc_zi is None or self._exc_zi.shape != (self._exc_sos.shape[0], ch, 2):
                    self._exc_zi = np.zeros((self._exc_sos.shape[0], ch, 2), dtype=np.float64)
                hp, self._exc_zi = sosfilt(self._exc_sos, x, axis=0, zi=self._exc_zi)
                # Soft, gentle saturation (tanh) generates harmonics without the
                # hard edges that cause audible distortion.
                excited = np.tanh(hp * 2.0) * 0.5
                x = x + excited * float(p["exciter_mix"])

            # ---- Compressor (soft-knee, gentle leveling) ----
            if p["comp_enabled"] >= 0.5 and p["comp_ratio"] > 1.0:
                det = np.abs(x).mean(axis=1)
                env, self._comp_det_zi = self._smooth(det, self._comp_det_a, self._comp_det_zi)
                env = np.maximum(env, 1e-7)
                env_db = 20.0 * np.log10(env)
                thr = p["comp_threshold_db"]
                ratio = p["comp_ratio"]
                knee = max(0.0, p["comp_knee_db"])
                over = env_db - thr
                # Soft-knee gain computer -> gain reduction in dB (<= 0).
                gr = np.zeros_like(env_db)
                slope = (1.0 / ratio) - 1.0
                if knee > 0.0:
                    half = knee / 2.0
                    knee_mask = (over > -half) & (over < half)
                    above_mask = over >= half
                    gr[above_mask] = slope * over[above_mask]
                    k = over[knee_mask] + half
                    gr[knee_mask] = slope * (k * k) / (2.0 * knee)
                else:
                    above_mask = over > 0.0
                    gr[above_mask] = slope * over[above_mask]
                gtarget = np.power(10.0, gr / 20.0)
                gsm, self._comp_gain_zi = self._smooth(gtarget, self._comp_rel_a, self._comp_gain_zi)
                self._comp_gain_last = float(gsm[-1])
                x *= gsm[:, None]
                x *= self._comp_makeup_lin

            # ---- Brickwall-safety limiter ----
            if p["limiter_enabled"] >= 0.5:
                peak = np.abs(x).max(axis=1)
                penv, self._lim_det_zi = self._smooth(peak, self._lim_det_a, self._lim_det_zi)
                penv = np.maximum(penv, 1e-7)
                ceil = self._lim_ceiling_lin
                # Reduce only where the smoothed peak exceeds the ceiling.
                lim_gain = np.minimum(1.0, ceil / penv)
                # Smooth the release so the limiter recovers gently; attack stays
                # fast because we then take the min with the instantaneous target
                # (so a peak can never sneak through while the gain rides back up).
                lgsm, self._lim_gain_zi = self._smooth(lim_gain, self._lim_rel_a, self._lim_gain_zi)
                lg = np.minimum(lim_gain, lgsm)
                self._lim_gain_last = float(lg[-1])
                x *= lg[:, None]

            # ---- Hard clip guard ----
            np.clip(x, -self._out_ceiling_lin, self._out_ceiling_lin, out=x)

            if samples.ndim == 1:
                x = x.reshape(-1)
            return x

    # ---------------- diagnostics ----------------

    def gain_reduction_db(self) -> dict:
        """Approximate current gain reduction per stage (for metering/tests)."""
        def to_db(g):
            try:
                return 20.0 * math.log10(max(1e-7, float(g)))
            except Exception:
                return 0.0
        return {
            "gate": to_db(getattr(self, "_gate_gain_last", 1.0)),
            "comp": to_db(getattr(self, "_comp_gain_last", 1.0)),
            "limiter": to_db(getattr(self, "_lim_gain_last", 1.0)),
        }
