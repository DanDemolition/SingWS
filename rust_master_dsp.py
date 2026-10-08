"""Python handle for the Rust master processor (``libsingws_dsp_ffi.dylib``).

Same public surface as :class:`singws_master_audio.MasterAudioProcessor` (``set_enabled``, ``set_params``,
``configure_stream``, ``reset_state``, ``process_f32_array`` ...), so the host can swap one for the other. The
difference that matters: ``native_dsp`` exposes the address of a BASS ``DSPPROC`` that lives in the Rust library, so
``BassBackgroundEngine`` can register it with BASS directly and **no Python runs on the audio thread**.

``singws_master_audio.MasterAudioProcessor`` remains the reference implementation and the fallback: if the library is
missing, has a different ABI, or disagrees about the parameter list, :class:`RustDspUnavailable` is raised and the
host keeps using the Python processor.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np

from singws_master_audio import DEFAULT_PARAMS

EXPECTED_ABI_VERSION = 1
LIB_NAME = "libsingws_dsp_ffi.dylib"
# Upper edges of the native per-block time histogram, microseconds (same as AudioCallbackStats.EDGES_US).
_HIST_EDGES_US = (100, 250, 500, 1000, 2000, 5000, 10000, 25000)

_LIB = None


class RustDspUnavailable(RuntimeError):
    pass


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("SINGWS_DSP_LIB")
    if override:
        paths.append(Path(override))
    roots = [
        getattr(sys, "_MEIPASS", None),
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
        Path(__file__).resolve().parent,
    ]
    for root in roots:
        if root:
            paths.extend([Path(root) / LIB_NAME, Path(root) / "Frameworks" / LIB_NAME])
    return paths


def _load():
    global _LIB
    if _LIB is not None:
        return _LIB
    last = "no candidate path exists"
    for path in _candidate_paths():
        if not path.exists():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.singws_dsp_abi_version.restype = ctypes.c_uint32
            if int(lib.singws_dsp_abi_version()) != EXPECTED_ABI_VERSION:
                last = f"{path}: ABI {lib.singws_dsp_abi_version()} != {EXPECTED_ABI_VERSION}"
                continue
            lib.singws_master_param_count.restype = ctypes.c_uint32
            lib.singws_master_param_names.restype = ctypes.c_char_p
            lib.singws_master_stat_count.restype = ctypes.c_uint32
            names = (lib.singws_master_param_names() or b"").decode().split(",")
            if names != list(DEFAULT_PARAMS) or int(lib.singws_master_param_count()) != len(DEFAULT_PARAMS):
                last = f"{path}: parameter list differs from singws_master_audio.DEFAULT_PARAMS"
                continue
            lib.singws_master_create.argtypes = [ctypes.c_double, ctypes.c_uint32]
            lib.singws_master_create.restype = ctypes.c_void_p
            lib.singws_master_destroy.argtypes = [ctypes.c_void_p]
            lib.singws_master_destroy.restype = None
            lib.singws_master_set_params.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.c_uint32]
            lib.singws_master_set_params.restype = ctypes.c_int32
            lib.singws_master_set_enabled.argtypes = [ctypes.c_void_p, ctypes.c_int32]
            lib.singws_master_set_enabled.restype = None
            lib.singws_master_configure.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_uint32]
            lib.singws_master_configure.restype = None
            lib.singws_master_reset.argtypes = [ctypes.c_void_p]
            lib.singws_master_reset.restype = None
            lib.singws_master_process_f32.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            lib.singws_master_process_f32.restype = ctypes.c_int32
            lib.singws_master_gain_reduction.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
            lib.singws_master_gain_reduction.restype = None
            lib.singws_master_stats.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_int32]
            lib.singws_master_stats.restype = None
            _LIB = lib
            return lib
        except OSError as exc:
            last = f"{path}: {exc}"
    raise RustDspUnavailable(f"{LIB_NAME} not usable ({last})")


def available() -> bool:
    try:
        _load()
        return True
    except RustDspUnavailable:
        return False


class RustMasterProcessor:
    """Drop-in for ``MasterAudioProcessor`` backed by the Rust implementation."""

    native = True

    def __init__(self, sample_rate: float = 44100.0, channels: int = 2, params: dict | None = None):
        self._lib = _load()
        self._h = self._lib.singws_master_create(float(sample_rate), max(1, int(channels)))
        if not self._h:
            raise RustDspUnavailable("singws_master_create failed")
        self._params = dict(DEFAULT_PARAMS)
        self._enabled = False
        self._sample_rate = float(sample_rate)
        self._channels = max(1, int(channels))
        self._push_params()
        if params:
            self.set_params(params)

    # ---- BASS hookup -------------------------------------------------------------------------------------------

    @property
    def native_dsp(self):
        """``(DSPPROC address, user pointer)`` to give ``BASS_ChannelSetDSP``; the callback runs entirely in Rust."""
        proc = ctypes.cast(self._lib.singws_master_dsp_proc, ctypes.c_void_p).value
        return int(proc), int(self._h)

    # ---- MasterAudioProcessor surface --------------------------------------------------------------------------

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._lib.singws_master_set_enabled(self._h, 1 if self._enabled else 0)

    def enabled(self) -> bool:
        return self._enabled

    def set_params(self, params: dict):
        for key, value in (params or {}).items():
            if key in self._params:
                try:
                    self._params[key] = float(value)
                except Exception:
                    pass
        self._push_params()

    def params(self) -> dict:
        return dict(self._params)

    def configure_stream(self, sample_rate: float, channels: int):
        self._sample_rate = float(sample_rate)
        self._channels = max(1, int(channels))
        self._lib.singws_master_configure(self._h, self._sample_rate, self._channels)

    def reset_state(self):
        self._lib.singws_master_reset(self._h)

    def process_f32_array(self, samples: np.ndarray) -> np.ndarray:
        """Process a (frames, channels) array. Used by tests and as a Python-callable fallback; BASS uses ``native_dsp``."""
        if not self._enabled:
            return samples
        flat = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1).copy()
        rc = self._lib.singws_master_process_f32(self._h, flat.ctypes.data, flat.size)
        if rc != 0:
            return samples
        return flat.reshape(samples.shape)

    def process_f32_bytes(self, data: bytes) -> bytes:
        if not data or not self._enabled:
            return data
        arr = np.frombuffer(data, dtype=np.float32).reshape(-1, self._channels)
        return self.process_f32_array(arr).astype(np.float32, copy=False).tobytes()

    def gain_reduction_db(self) -> dict:
        out = (ctypes.c_double * 3)()
        self._lib.singws_master_gain_reduction(self._h, out)
        return {"gate": out[0], "comp": out[1], "limiter": out[2]}

    # ---- timing counters (same shape as bass_background_engine.AudioCallbackStats.summary) ------------------------

    def stats_snapshot(self, reset: bool = True) -> dict:
        n = int(self._lib.singws_master_stat_count())
        out = (ctypes.c_uint64 * n)()
        self._lib.singws_master_stats(self._h, out, 1 if reset else 0)
        blocks, total_ns, max_ns, over, gap_max_ns, g100, g500, restarts = (int(out[i]) for i in range(8))
        hist = [int(out[8 + i]) for i in range(n - 8)]
        p99 = None
        if blocks:
            target, running = blocks * 0.99, 0
            for i, count in enumerate(hist):
                running += count
                if running >= target:
                    p99 = _HIST_EDGES_US[i] if i < len(_HIST_EDGES_US) else None
                    break
        else:
            p99 = 0
        return {
            "blocks": blocks,
            "mean_us": (total_ns / blocks / 1e3) if blocks else 0.0,
            "p99_upper_us": p99,
            "max_us": max_ns / 1e3,
            "over_budget": over,
            "gap_max_ms": gap_max_ns / 1e6,
            "gaps_over_100ms": g100,
            "gaps_over_500ms": g500,
            "restarts": restarts,
        }

    # ---- lifetime ------------------------------------------------------------------------------------------------

    def _push_params(self):
        arr = (ctypes.c_double * len(DEFAULT_PARAMS))(*[self._params[k] for k in DEFAULT_PARAMS])
        if self._lib.singws_master_set_params(self._h, arr, len(DEFAULT_PARAMS)) != 0:
            raise RustDspUnavailable("singws_master_set_params rejected the parameter block")

    def close(self):
        h, self._h = getattr(self, "_h", None), None
        if h:
            # The caller must have removed the DSP from BASS first (BassBackgroundEngine._detach_master_dsp does).
            self._lib.singws_master_destroy(h)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
