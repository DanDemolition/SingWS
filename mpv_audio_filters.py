"""Translate the SingWS audio chain into an mpv/lavfi ``af`` filter string.

Why this exists
---------------
``mpv_playback`` drives mpv as an out-of-process binary over JSON IPC, so audio
samples never reach Python. The NumPy processors in :mod:`singws_eq` and
:mod:`singws_master_audio` are block processors that must sit *in* the sample
path, which means they cannot run when mpv owns the audio. Rather than hand the
clock back to SingWS (which would cost the fast seek and tempo/key that make
mpv's audio engine worth using), the same processing is expressed as an mpv
``af`` chain and applied inside mpv.

Fidelity
--------
Two stages port exactly:

* **Loudness normalization** is a single gain -> ``volume=<db>dB``.
* **The 10-band graphic EQ** is a bank of RBJ peaking biquads, and lavfi's
  ``equalizer=f=..:t=q:w=..:g=..`` is the same filter. Same frequencies, same Q,
  same gains, same result.

The master bus does **not**. ``singws_master_audio.MasterAudioProcessor``
implements its own soft-knee compressor, downward expander, peak limiter and
exciter; ``acompressor`` / ``agate`` / ``alimiter`` / ``aexciter`` are different
implementations of the same *categories*. :func:`chain_fidelity_notes` reports
which stages are approximate so callers can say so in a log.

Knob values are **not** carried across unchanged, because three of them mean
different things on each side and passing them through was measurably wrong
(2026-08-09, against the NumPy chain on the same audio):

* ``acompressor`` detects on a smoothed RMS where MasterAudioProcessor uses a
  smoothed mean-of-|x|, so its threshold needs :data:`_COMP_DETECTOR_OFFSET_DB`.
* ``acompressor``/``agate`` express the knee as a linear factor, not dB.
* ``alimiter`` auto-levels to 0 dBFS unless told not to, which cancelled the
  ceiling entirely.

With those three corrected the compressor's static transfer curve matches
MasterAudioProcessor within 0.02 dB from -40 to -3 dBFS and the limiter matches
exactly below its ceiling; the gate and exciter were already within measurement
noise. What remains approximate is dynamic behaviour (detector topology under
programme material), not level.

The filter order mirrors the SingWS signal path exactly:

    key -> normalize -> graphic EQ -> gate -> tilt EQ -> exciter -> comp -> limiter

Tempo is deliberately absent: mpv applies it with the ``speed`` property, not a
filter, exactly as ``python_karaoke_transport`` keeps tempo out of its DSP.
"""

from __future__ import annotations

from typing import Iterable, Sequence

# Matches singws_eq.DEFAULT_BANDS_HZ / DEFAULT_Q. Duplicated rather than
# imported so building a filter string never drags in numpy/scipy.
DEFAULT_BANDS_HZ: tuple[float, ...] = (
    31.5, 63.0, 125.0, 250.0, 500.0,
    1000.0, 2000.0, 4000.0, 8000.0, 16000.0,
)
DEFAULT_Q = 1.4

# Below this a band/gain is inaudible and the filter is dropped instead of
# spending CPU on a no-op biquad.
_EPSILON_DB = 0.05

# aexciter clamps freq to this range; singws_master_audio allows lower values.
_EXCITER_FREQ_MIN = 2000.0
_EXCITER_FREQ_MAX = 12000.0

# MasterAudioProcessor's compressor detects on a smoothed mean-of-|x|;
# acompressor detects on a smoothed RMS, which reads higher for the same
# programme. Passing the SingWS threshold through unchanged therefore started
# compression early and cost ~1.4 dB of extra gain reduction at normal levels.
# 3.0 dB is measured, not derived: with it, the two static transfer curves agree
# within 0.02 dB from -40 to -3 dBFS (scratch: dsp_curve.py). Without it they
# diverge by up to 1.45 dB.
_COMP_DETECTOR_OFFSET_DB = 3.0

# acompressor/agate express knee as a linear factor spanning the knee, not dB:
# the transition runs from threshold/sqrt(knee) to threshold*sqrt(knee), so the
# width in dB is 20*log10(knee). Passing comp_knee_db straight in made a 6 dB
# knee 15.6 dB wide. ffmpeg clamps the factor to 1..8 (0 .. 18 dB).
_KNEE_FACTOR_MIN = 1.0
_KNEE_FACTOR_MAX = 8.0


def _knee_factor(knee_db) -> float:
    """Knee width in dB -> the linear factor lavfi wants."""
    db = max(0.0, _as_float(knee_db, 6.0))
    return max(_KNEE_FACTOR_MIN, min(_KNEE_FACTOR_MAX, 10.0 ** (db / 20.0)))

# Stages whose lavfi equivalent is a different implementation, not a port.
_APPROXIMATE_STAGES = ("gate", "exciter", "compressor", "limiter")


def _f(value: float) -> str:
    """Compact fixed-point number: lavfi rejects exponent notation."""
    return f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"


def _db_to_linear(db: float) -> float:
    return 10.0 ** (float(db) / 20.0)


def _as_float(value, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return float(default)
    return out


def _enabled(params: dict, key: str, default: bool = False) -> bool:
    return _as_float(params.get(key, 1.0 if default else 0.0), 0.0) >= 0.5


def key_filter(semitones) -> str:
    """Pitch shift without tempo change. mpv's own rubberband filter."""
    n = int(_as_float(semitones, 0.0))
    if n == 0:
        return ""
    return f"rubberband=pitch-scale={2 ** (n / 12.0):.6f}"


def normalize_filter(gain_db) -> str:
    gain = _as_float(gain_db, 0.0)
    if abs(gain) < _EPSILON_DB:
        return ""
    return f"volume={_f(gain)}dB"


def graphic_eq_filters(
    gains_db: Sequence[float] | None,
    *,
    bands_hz: Sequence[float] = DEFAULT_BANDS_HZ,
    q: float = DEFAULT_Q,
) -> list[str]:
    """The 10-band graphic EQ, one lavfi peaking biquad per non-flat band.

    Identical maths to singws_eq.GraphicEQ: same centre frequencies, same Q,
    same gains, same RBJ peaking topology.
    """
    if not gains_db:
        return []
    out: list[str] = []
    width = _as_float(q, DEFAULT_Q)
    for index, band_hz in enumerate(bands_hz):
        if index >= len(gains_db):
            break
        gain = _as_float(gains_db[index], 0.0)
        if abs(gain) < _EPSILON_DB:
            continue
        out.append(
            f"equalizer=f={_f(band_hz)}:t=q:w={_f(width)}:g={_f(gain)}"
        )
    return out


def master_bus_filters(params: dict | None) -> list[str]:
    """Master bus stages, in signal order. Approximate -- see module docstring.

    Takes the same engine-param dict that drives MasterAudioProcessor (the
    output of the host's _compute_master_audio_params), so the friendly knobs
    keep a single source of truth.
    """
    if not params:
        return []
    p = dict(params)
    out: list[str] = []

    if _enabled(p, "gate_enabled"):
        # agate takes LINEAR threshold and range, not dB.
        threshold = max(0.0, min(1.0, _db_to_linear(_as_float(p.get("gate_threshold_db"), -58.0))))
        floor_lin = max(0.0, min(1.0, _db_to_linear(_as_float(p.get("gate_floor_db"), -18.0))))
        out.append(
            "agate="
            f"threshold={_f(threshold)}:"
            f"range={_f(floor_lin)}:"
            f"ratio={_f(max(1.0, _as_float(p.get('gate_ratio'), 1.6)))}:"
            f"attack={_f(max(0.01, _as_float(p.get('gate_attack_ms'), 5.0)))}:"
            f"release={_f(max(0.01, _as_float(p.get('gate_release_ms'), 140.0)))}"
        )

    if _enabled(p, "eq_enabled", default=True):
        low_db = _as_float(p.get("low_shelf_db"), 1.0)
        if abs(low_db) >= _EPSILON_DB:
            out.append(
                f"bass=f={_f(_as_float(p.get('low_shelf_hz'), 90.0))}:g={_f(low_db)}"
            )
        presence_db = _as_float(p.get("presence_db"), 1.0)
        if abs(presence_db) >= _EPSILON_DB:
            out.append(
                f"equalizer=f={_f(_as_float(p.get('presence_hz'), 3200.0))}"
                f":t=q:w={_f(_as_float(p.get('presence_q'), 0.7))}:g={_f(presence_db)}"
            )
        high_db = _as_float(p.get("high_shelf_db"), 1.5)
        if abs(high_db) >= _EPSILON_DB:
            out.append(
                f"treble=f={_f(_as_float(p.get('high_shelf_hz'), 9000.0))}:g={_f(high_db)}"
            )

    exciter_mix = _as_float(p.get("exciter_mix"), 0.0)
    if exciter_mix > 0.0:
        freq = min(_EXCITER_FREQ_MAX, max(_EXCITER_FREQ_MIN, _as_float(p.get("exciter_hz"), 4000.0)))
        # MasterAudioProcessor caps mix at 0.5 (a blend ratio); aexciter's
        # amount runs 0..64. Scale so a full-mix setting stays subtle.
        out.append(
            f"aexciter=amount={_f(max(0.0, min(64.0, exciter_mix * 2.0)))}:freq={_f(freq)}"
        )

    if _enabled(p, "comp_enabled", default=True):
        threshold_db = (
            _as_float(p.get("comp_threshold_db"), -20.0) + _COMP_DETECTOR_OFFSET_DB
        )
        out.append(
            "acompressor="
            f"threshold={_f(min(0.0, threshold_db))}dB:"
            f"ratio={_f(max(1.0, _as_float(p.get('comp_ratio'), 2.0)))}:"
            f"knee={_f(_knee_factor(p.get('comp_knee_db', 6.0)))}:"
            f"attack={_f(max(0.01, _as_float(p.get('comp_attack_ms'), 18.0)))}:"
            f"release={_f(max(0.01, _as_float(p.get('comp_release_ms'), 180.0)))}:"
            f"makeup={_f(max(1.0, _db_to_linear(_as_float(p.get('comp_makeup_db'), 4.0))))}"
        )

    # One limiter, not two. MasterAudioProcessor runs a limiter and then a hard
    # clip guard, but the guard's ceiling (-0.1 dB) sits above the limiter's
    # (-1.0 dB), so the limiter is always the binding constraint. When the
    # limiter stage is off, the guard becomes the binding one and takes over.
    if _enabled(p, "limiter_enabled", default=True):
        ceiling = _as_float(p.get("limiter_ceiling_db"), -1.0)
    else:
        ceiling = _as_float(p.get("output_ceiling_db"), -0.1)
    # level=disabled is not optional. alimiter's "auto level" defaults to ON and
    # divides the output by the limit, i.e. normalizes straight back to 0 dBFS.
    # That silently cancelled the ceiling: a -1 dB setting produced +1.00 dB of
    # blanket gain at every level below the ceiling (measured exactly, scratch:
    # dsp_gate_lim.py), so the headroom the ceiling exists to protect was gone
    # and the whole master bus ran a decibel hot.
    out.append(
        f"alimiter=limit={_f(min(0.0, ceiling))}dB:"
        f"attack={_f(max(0.1, _as_float(p.get('limiter_detector_ms'), 1.2)))}:"
        f"release={_f(max(1.0, _as_float(p.get('limiter_release_ms'), 80.0)))}:"
        "level=disabled"
    )
    return out


def build_af_chain(
    *,
    semitones=0,
    normalize_gain_db: float = 0.0,
    eq_enabled: bool = False,
    eq_gains_db: Sequence[float] | None = None,
    eq_bands_hz: Sequence[float] = DEFAULT_BANDS_HZ,
    eq_q: float = DEFAULT_Q,
    master_enabled: bool = False,
    master_params: dict | None = None,
) -> str:
    """Build the complete mpv ``af`` value for the current audio settings.

    Returns "" when nothing is active, which is what mpv wants for "no filters".
    """
    stages: list[str] = []
    head = key_filter(semitones)
    if head:
        stages.append(head)
    gain = normalize_filter(normalize_gain_db)
    if gain:
        stages.append(gain)
    if eq_enabled:
        stages.extend(graphic_eq_filters(eq_gains_db, bands_hz=eq_bands_hz, q=eq_q))
    if master_enabled:
        stages.extend(master_bus_filters(master_params))
    return ",".join(stages)


def chain_fidelity_notes(af_chain: str) -> list[str]:
    """Which active stages are approximations rather than ports.

    Callers log this so an operator is never left guessing why mpv's output
    does not match the FFmpeg path exactly.
    """
    notes: list[str] = []
    if "agate=" in af_chain:
        notes.append("gate")
    if "aexciter=" in af_chain:
        notes.append("exciter")
    if "acompressor=" in af_chain:
        notes.append("compressor")
    if "alimiter=" in af_chain:
        notes.append("limiter")
    return notes


def describe_chain(af_chain: str) -> str:
    """Short human-readable summary for the [KARAOKE-AUDIO] log line."""
    if not af_chain:
        return "passthrough (no processing active)"
    count = len([s for s in af_chain.split(",") if s])
    approx = chain_fidelity_notes(af_chain)
    text = f"{count} lavfi stage{'s' if count != 1 else ''}"
    if approx:
        text += f"; approximate: {'+'.join(approx)}"
    return text
