from __future__ import annotations

import ctypes
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path


class BassBackgroundError(RuntimeError):
    pass


DWORD = ctypes.c_uint32
QWORD = ctypes.c_uint64
BOOL = ctypes.c_int
HSTREAM = DWORD

BASS_ACTIVE_STOPPED = 0
BASS_ACTIVE_PLAYING = 1
BASS_ACTIVE_PAUSED = 3

BASS_SAMPLE_FLOAT = 0x100
BASS_STREAM_PRESCAN = 0x20000
BASS_STREAM_DECODE = 0x200000
BASS_POS_BYTE = 0
BASS_ATTRIB_VOL = 2
BASS_LEVEL_STEREO = 2
BASS_LEVEL_RMS = 4
BASS_LEVEL_VOLPAN = 8
BASS_FX_DX8_COMPRESSOR = 1
BASS_FX_DX8_PARAMEQ = 7

BASS_DEVICE_ENABLED = 1
BASS_DEVICE_DEFAULT = 2

BASS_MIXER_POSEX = 0x2000
BASS_MIXER_NONSTOP = 0x20000
BASS_MIXER_CHAN_BUFFER = 0x2000
BASS_MIXER_CHAN_DOWNMIX = 0x400000
BASS_POS_MIXER_RESET = 0x10000


class _BassDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("driver", ctypes.c_char_p),
        ("flags", DWORD),
    ]


class _BassDx8ParamEq(ctypes.Structure):
    _fields_ = [
        ("fCenter", ctypes.c_float),
        ("fBandwidth", ctypes.c_float),
        ("fGain", ctypes.c_float),
    ]


class _BassDx8Compressor(ctypes.Structure):
    # Matches the DirectX8 BASS_DX8_COMPRESSOR parameter struct.
    _fields_ = [
        ("fGain", ctypes.c_float),       # output makeup gain, dB  [-60..60]
        ("fAttack", ctypes.c_float),     # ms  [0.01..500]
        ("fRelease", ctypes.c_float),    # ms  [50..3000]
        ("fThreshold", ctypes.c_float),  # dB  [-60..0]
        ("fRatio", ctypes.c_float),      # n:1 [1..100]
        ("fPredelay", ctypes.c_float),   # ms  [0..4]
    ]


@dataclass
class _Deck:
    path: str
    handle: int
    norm_gain: float = 1.0


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in (
        getattr(sys, "_MEIPASS", None),
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
        Path(__file__).resolve().parent,
    ):
        if raw:
            roots.append(Path(raw))
    return roots


def _library_names(stem: str) -> tuple[str, ...]:
    if sys.platform == "darwin":
        return (f"lib{stem}.dylib",)
    if sys.platform == "win32":
        return (f"{stem}.dll", f"lib{stem}.dll")
    return (f"lib{stem}.so",)


def _find_library(stem: str, required: bool = True) -> Path | None:
    for root in _runtime_roots():
        for rel in (Path("vendor") / "bass", Path("bass"), Path(".")):
            for name in _library_names(stem):
                candidate = root / rel / name
                if candidate.exists():
                    return candidate
    if required:
        raise BassBackgroundError(f"Missing BASS runtime library for {stem}.")
    return None


class BassBackgroundEngine:
    """Two-deck BASSmix player for background music fades and crossfades."""
    # Class-level flag to ensure BASS_Init is only called once per process
    _bass_init_done: bool = False
    # Reference count of active engine instances using BASS
    _bass_init_refcount: int = 0

    def __init__(self, output_name: str | None = None, sample_rate: int = 48000):
        self.output_name = str(output_name or "").strip()
        self.sample_rate = int(sample_rate or 48000)
        self.bass = None
        self.mix = None
        self.mixer = 0
        self.primary: _Deck | None = None
        self.secondary: _Deck | None = None
        self.master_volume = 0.8
        self._plugin_handles: list[int] = []
        self._closed = False
        # Optional graphic-EQ applied to the mixer output via BASS_ChannelSetDSP
        self._eq = None
        self._eq_dsp_handle = 0
        self._eq_dsp_callback = None  # keep CFUNCTYPE alive to avoid GC
        self._eq_fx_handles: list[int] = []
        # Optional master "mix bus" compressor (native DX8 FX) on the mixer
        # output. None = disabled. Native FX keep this off the Python audio
        # thread, so it's stable on Intel and Apple Silicon.
        self._master_params: dict | None = None
        self._master_fx_handle = 0
        # Optional full master processor (gate/tilt EQ/exciter/compressor/
        # limiter) applied to the mixer output via a Python DSP, giving BGM the
        # identical chain as karaoke. None = disabled. Heavier than the native
        # compressor above but the only way to match the exciter/limiter stages.
        self._master_proc = None
        self._master_dsp_handle = 0
        self._master_dsp_callback = None  # keep CFUNCTYPE alive to avoid GC
        self._master_proc_ref = {"proc": None}
        self._load_runtime()
        self._init_output()

    def __del__(self):
        """Automatically cleanup BASS resources when engine is destroyed."""
        self.close()

    def _load_runtime(self):
        try:
            mode = getattr(ctypes, "RTLD_GLOBAL", 0)
            self.bass = ctypes.CDLL(str(_find_library("bass")), mode=mode)
            self.mix = ctypes.CDLL(str(_find_library("bassmix")), mode=mode)
        except OSError as exc:
            raise BassBackgroundError(f"Unable to load BASS runtime: {exc}") from exc

        self.bass.BASS_ErrorGetCode.argtypes = []
        self.bass.BASS_ErrorGetCode.restype = ctypes.c_int
        self.bass.BASS_GetDeviceInfo.argtypes = [DWORD, ctypes.POINTER(_BassDeviceInfo)]
        self.bass.BASS_GetDeviceInfo.restype = BOOL
        self.bass.BASS_Init.argtypes = [ctypes.c_int, DWORD, DWORD, ctypes.c_void_p, ctypes.c_void_p]
        self.bass.BASS_Init.restype = BOOL
        self.bass.BASS_Free.argtypes = []
        self.bass.BASS_Free.restype = BOOL
        self.bass.BASS_PluginLoad.argtypes = [ctypes.c_char_p, DWORD]
        self.bass.BASS_PluginLoad.restype = DWORD
        self.bass.BASS_StreamCreateFile.argtypes = [DWORD, ctypes.c_char_p, QWORD, QWORD, DWORD]
        self.bass.BASS_StreamCreateFile.restype = HSTREAM
        self.bass.BASS_StreamFree.argtypes = [DWORD]
        self.bass.BASS_StreamFree.restype = BOOL
        self.bass.BASS_ChannelPlay.argtypes = [DWORD, BOOL]
        self.bass.BASS_ChannelPlay.restype = BOOL
        self.bass.BASS_ChannelPause.argtypes = [DWORD]
        self.bass.BASS_ChannelPause.restype = BOOL
        self.bass.BASS_ChannelStop.argtypes = [DWORD]
        self.bass.BASS_ChannelStop.restype = BOOL
        self.bass.BASS_ChannelIsActive.argtypes = [DWORD]
        self.bass.BASS_ChannelIsActive.restype = DWORD
        self.bass.BASS_ChannelSetAttribute.argtypes = [DWORD, DWORD, ctypes.c_float]
        self.bass.BASS_ChannelSetAttribute.restype = BOOL
        self.bass.BASS_ChannelSlideAttribute.argtypes = [DWORD, DWORD, ctypes.c_float, DWORD]
        self.bass.BASS_ChannelSlideAttribute.restype = BOOL
        self.bass.BASS_ChannelGetLength.argtypes = [DWORD, DWORD]
        self.bass.BASS_ChannelGetLength.restype = QWORD
        self.bass.BASS_ChannelSeconds2Bytes.argtypes = [DWORD, ctypes.c_double]
        self.bass.BASS_ChannelSeconds2Bytes.restype = QWORD
        self.bass.BASS_ChannelBytes2Seconds.argtypes = [DWORD, QWORD]
        self.bass.BASS_ChannelBytes2Seconds.restype = ctypes.c_double

        self.mix.BASS_Mixer_StreamCreate.argtypes = [DWORD, DWORD, DWORD]
        self.mix.BASS_Mixer_StreamCreate.restype = HSTREAM
        self.mix.BASS_Mixer_StreamAddChannel.argtypes = [DWORD, DWORD, DWORD]
        self.mix.BASS_Mixer_StreamAddChannel.restype = BOOL
        self.mix.BASS_Mixer_ChannelRemove.argtypes = [DWORD]
        self.mix.BASS_Mixer_ChannelRemove.restype = BOOL
        self.mix.BASS_Mixer_ChannelIsActive.argtypes = [DWORD]
        self.mix.BASS_Mixer_ChannelIsActive.restype = DWORD
        self.mix.BASS_Mixer_ChannelGetPosition.argtypes = [DWORD, DWORD]
        self.mix.BASS_Mixer_ChannelGetPosition.restype = QWORD
        self.mix.BASS_Mixer_ChannelSetPosition.argtypes = [DWORD, QWORD, DWORD]
        self.mix.BASS_Mixer_ChannelSetPosition.restype = BOOL
        self.mix.BASS_Mixer_ChannelGetLevelEx.argtypes = [
            DWORD,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_float,
            DWORD,
        ]
        self.mix.BASS_Mixer_ChannelGetLevelEx.restype = BOOL
        # optional plugin free if available
        try:
            self.bass.BASS_PluginFree.argtypes = [DWORD]
            self.bass.BASS_PluginFree.restype = BOOL
        except Exception:
            # not all runtimes expose this the same way; ignore if missing
            pass

        # DSP attachment is used by the graphic-EQ feature.
        try:
            self.bass.BASS_ChannelSetDSP.argtypes = [
                DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
            ]
            self.bass.BASS_ChannelSetDSP.restype = DWORD
            self.bass.BASS_ChannelRemoveDSP.argtypes = [DWORD, DWORD]
            self.bass.BASS_ChannelRemoveDSP.restype = BOOL
        except Exception:
            pass
        try:
            self.bass.BASS_ChannelSetFX.argtypes = [DWORD, DWORD, ctypes.c_int]
            self.bass.BASS_ChannelSetFX.restype = DWORD
            self.bass.BASS_ChannelRemoveFX.argtypes = [DWORD, DWORD]
            self.bass.BASS_ChannelRemoveFX.restype = BOOL
            self.bass.BASS_FXSetParameters.argtypes = [DWORD, ctypes.c_void_p]
            self.bass.BASS_FXSetParameters.restype = BOOL
        except Exception:
            pass

    def _error(self, context: str) -> BassBackgroundError:
        code = self.bass.BASS_ErrorGetCode() if self.bass is not None else "?"
        return BassBackgroundError(f"{context} failed (BASS error {code}).")

    def _device_index(self) -> int:
        if not self.output_name:
            return -1
        wanted = self.output_name.casefold()
        exact = None
        partial = None
        default = None
        for idx in range(1, 128):
            info = _BassDeviceInfo()
            if not self.bass.BASS_GetDeviceInfo(idx, ctypes.byref(info)):
                break
            if not (int(info.flags) & BASS_DEVICE_ENABLED):
                continue
            name = os.fsdecode(info.name or b"").strip()
            if int(info.flags) & BASS_DEVICE_DEFAULT:
                default = idx
            if name.casefold() == wanted:
                exact = idx
                break
            if wanted in name.casefold() or name.casefold() in wanted:
                partial = idx
        return exact or partial or default or -1

    def _init_output(self):
        # Initialize BASS once per process. Subsequent instances skip initialization.
        if not BassBackgroundEngine._bass_init_done:
            if not self.bass.BASS_Init(self._device_index(), self.sample_rate, 0, None, None):
                raise self._error("BASS_Init")
            BassBackgroundEngine._bass_init_done = True
            # Resilience tuning: prefer a generous playback buffer over low
            # latency.  At a karaoke gig we never need sub-second seek
            # response from BGM; what matters is that audio never breaks
            # up if the CPU spikes from a scan, decode, or UI redraw.
            try:
                self.bass.BASS_SetConfig.argtypes = [DWORD, DWORD]
                self.bass.BASS_SetConfig.restype = BOOL
                # BASS_CONFIG_BUFFER = 0  → playback buffer in ms (default 500)
                self.bass.BASS_SetConfig(0, 1500)
                # BASS_CONFIG_UPDATEPERIOD = 1 → mixer update period in ms
                # (default 100).  25 ms is small enough that the EQ DSP
                # responds promptly, large enough that occasional GIL
                # contention doesn't starve the audio thread.
                self.bass.BASS_SetConfig(1, 25)
            except Exception:
                pass
        # Track that this instance is using the shared BASS runtime.
        BassBackgroundEngine._bass_init_refcount = int(BassBackgroundEngine._bass_init_refcount) + 1
        for stem in ("bassflac",):
            path = _find_library(stem, required=False)
            if path is not None:
                handle = int(self.bass.BASS_PluginLoad(os.fsencode(path), 0))
                if handle:
                    self._plugin_handles.append(handle)

    def _ensure_mixer(self):
        if self.mixer:
            return
        flags = BASS_SAMPLE_FLOAT | BASS_MIXER_POSEX | BASS_MIXER_NONSTOP
        self.mixer = int(self.mix.BASS_Mixer_StreamCreate(self.sample_rate, 2, flags))
        if not self.mixer:
            raise self._error("BASS_Mixer_StreamCreate")
        self.set_master_volume(self.master_volume)
        # Re-attach the EQ DSP if one is configured (e.g. mixer was torn
        # down and recreated between songs).
        if self._eq_should_attach() and self._eq_dsp_handle == 0 and not self._eq_fx_handles:
            try:
                if not self._attach_native_eq_fx() and os.environ.get("SINGWS_ALLOW_PYTHON_BGM_EQ_DSP") == "1":
                    self._attach_eq_dsp()
            except Exception:
                pass
        # Re-attach the master compressor if one is configured (mixer recreated).
        if self._master_should_attach() and self._master_fx_handle == 0:
            try:
                self._attach_master_fx()
            except Exception:
                pass
        # Re-attach the full master processor DSP if one is configured.
        if self._master_proc is not None and self._master_dsp_handle == 0:
            try:
                self._attach_master_dsp()
            except Exception:
                pass

    def _eq_should_attach(self) -> bool:
        try:
            return bool(self._eq is not None and self._eq.enabled() and not self._eq.is_flat())
        except Exception:
            return False

    def set_eq(self, eq) -> None:
        """Attach (or detach with ``None``) a 10-band graphic EQ to the
        mixer output.  Bands take effect immediately via the live EQ
        instance the caller keeps a reference to."""
        self._detach_eq_dsp()
        self._detach_native_eq_fx()
        self._eq = eq
        if self._eq_should_attach() and self.mixer:
            try:
                if self._attach_native_eq_fx():
                    return
                # The Python callback is kept only as an explicit escape hatch.
                # Default builds avoid putting Python/Numpy/Scipy in BASS's
                # realtime audio callback; if native BASS FX are unavailable,
                # BGM EQ is left detached rather than causing dropouts.
                if os.environ.get("SINGWS_ALLOW_PYTHON_BGM_EQ_DSP") == "1":
                    self._attach_eq_dsp()
            except Exception:
                pass

    def _detach_native_eq_fx(self) -> None:
        if self.mixer and self._eq_fx_handles:
            for handle in list(self._eq_fx_handles):
                try:
                    self.bass.BASS_ChannelRemoveFX(self.mixer, handle)
                except Exception:
                    pass
        self._eq_fx_handles = []

    def _attach_native_eq_fx(self) -> bool:
        if not self.mixer or self._eq is None:
            return False
        if not all(hasattr(self.bass, name) for name in ("BASS_ChannelSetFX", "BASS_FXSetParameters", "BASS_ChannelRemoveFX")):
            return False
        try:
            gains = list(self._eq.gains_db())
        except Exception:
            return False
        bands = (31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
        handles: list[int] = []
        try:
            for freq, gain_db in zip(bands, gains):
                handle = int(self.bass.BASS_ChannelSetFX(self.mixer, BASS_FX_DX8_PARAMEQ, 0))
                if not handle:
                    raise RuntimeError("BASS_ChannelSetFX(PARAMEQ) unavailable")
                params = _BassDx8ParamEq(
                    ctypes.c_float(float(freq)),
                    ctypes.c_float(1.0),
                    ctypes.c_float(max(-12.0, min(12.0, float(gain_db)))),
                )
                if not self.bass.BASS_FXSetParameters(handle, ctypes.byref(params)):
                    raise RuntimeError("BASS_FXSetParameters(PARAMEQ) failed")
                handles.append(handle)
        except Exception:
            for handle in handles:
                try:
                    self.bass.BASS_ChannelRemoveFX(self.mixer, handle)
                except Exception:
                    pass
            return False
        self._eq_fx_handles = handles
        return True

    # ---------------- master "mix bus" compressor ----------------

    def set_master_compressor(self, params: dict | None) -> None:
        """Attach (dict of params) or detach (None) a native DX8 compressor on
        the mixer output. Params are stored so a mixer rebuilt between songs
        re-attaches automatically (see _ensure_mixer)."""
        self._detach_master_fx()
        self._master_params = dict(params) if isinstance(params, dict) and params else None
        if self._master_params and self.mixer:
            try:
                self._attach_master_fx()
            except Exception:
                pass

    def _detach_master_fx(self) -> None:
        if self.mixer and self._master_fx_handle:
            try:
                self.bass.BASS_ChannelRemoveFX(self.mixer, self._master_fx_handle)
            except Exception:
                pass
        self._master_fx_handle = 0

    def _master_should_attach(self) -> bool:
        return bool(self._master_params)

    def _attach_master_fx(self) -> bool:
        if not self.mixer or not self._master_params:
            return False
        if not all(hasattr(self.bass, name) for name in ("BASS_ChannelSetFX", "BASS_FXSetParameters", "BASS_ChannelRemoveFX")):
            return False
        p = self._master_params

        def _clamp(v, lo, hi, default):
            try:
                return max(lo, min(hi, float(v)))
            except Exception:
                return default

        try:
            # Priority below the EQ (which uses 0) so the chain is EQ -> comp;
            # BASS applies higher-priority FX first.
            handle = int(self.bass.BASS_ChannelSetFX(self.mixer, BASS_FX_DX8_COMPRESSOR, -1))
            if not handle:
                return False
            params = _BassDx8Compressor(
                ctypes.c_float(_clamp(p.get("gain_db", 4.0), -60.0, 60.0, 4.0)),
                ctypes.c_float(_clamp(p.get("attack_ms", 18.0), 0.01, 500.0, 18.0)),
                ctypes.c_float(_clamp(p.get("release_ms", 180.0), 50.0, 3000.0, 180.0)),
                ctypes.c_float(_clamp(p.get("threshold_db", -20.0), -60.0, 0.0, -20.0)),
                ctypes.c_float(_clamp(p.get("ratio", 2.0), 1.0, 100.0, 2.0)),
                ctypes.c_float(_clamp(p.get("predelay_ms", 2.0), 0.0, 4.0, 2.0)),
            )
            if not self.bass.BASS_FXSetParameters(handle, ctypes.byref(params)):
                self.bass.BASS_ChannelRemoveFX(self.mixer, handle)
                return False
            self._master_fx_handle = handle
            return True
        except Exception:
            return False

    # ---------------- master "mix bus" full processor (Python DSP) ----------

    def set_master_processor(self, processor) -> None:
        """Attach (a MasterAudioProcessor-like object exposing
        process_f32_array / configure_stream) or detach (None) the full master
        chain on the mixer output via a Python DSP. The processor is stored so a
        mixer rebuilt between songs re-attaches automatically (see
        _ensure_mixer). Passing the already-attached instance is a no-op so live
        param tweaks (made on the processor itself) never glitch the DSP."""
        if processor is not None and processor is self._master_proc and self._master_dsp_handle:
            self._master_proc_ref["proc"] = processor
            return
        self._detach_master_dsp()
        self._master_proc = processor if processor is not None else None
        if self._master_proc is not None and self.mixer:
            try:
                self._attach_master_dsp()
            except Exception:
                pass

    def _detach_master_dsp(self) -> None:
        if self._master_dsp_handle and self.mixer:
            try:
                self.bass.BASS_ChannelRemoveDSP(self.mixer, self._master_dsp_handle)
            except Exception:
                pass
        self._master_dsp_handle = 0
        self._master_proc_ref["proc"] = None

    def _attach_master_dsp(self) -> None:
        if not self.mixer or self._master_proc is None:
            return
        # Build the C callback only once and stash it on self to keep the
        # ctypes object alive — if it's GC'd while BASS still holds the
        # pointer, the audio thread crashes.
        if self._master_dsp_callback is None:
            import numpy as _np
            DSPPROC = ctypes.CFUNCTYPE(
                None, DWORD, DWORD, ctypes.c_void_p, DWORD, ctypes.c_void_p,
            )
            channels = 2  # the mixer is stereo float
            proc_ref = self._master_proc_ref

            def _dsp_proc(handle, channel, buffer_ptr, length, user):
                proc = proc_ref["proc"]
                if proc is None or buffer_ptr == 0 or length == 0:
                    return
                try:
                    # length is in bytes; mixer is float32 stereo.
                    n_floats = int(length) // 4
                    if n_floats <= 0 or n_floats % channels != 0:
                        return
                    arr_t = (ctypes.c_float * n_floats).from_address(int(buffer_ptr))
                    view = _np.ctypeslib.as_array(arr_t)  # zero-copy view
                    frames = view.reshape(-1, channels)
                    processed = proc.process_f32_array(frames)
                    if processed is not frames:
                        view[:] = processed.ravel()
                except Exception:
                    # Audio thread: swallow exceptions so we never crash BASS.
                    pass

            self._master_dsp_callback = DSPPROC(_dsp_proc)

        # Mixer format is fixed (stereo float at self.sample_rate), so configure
        # the processor's filters once here rather than per DSP block.
        try:
            self._master_proc.configure_stream(self.sample_rate, 2)
        except Exception:
            pass
        self._master_proc_ref["proc"] = self._master_proc
        # Priority below the EQ (which uses 0) so the chain is EQ -> master,
        # matching the karaoke transport order. BASS applies higher priorities
        # first, so -1 runs the master stage after the EQ.
        self._master_dsp_handle = int(self.bass.BASS_ChannelSetDSP(
            self.mixer, ctypes.cast(self._master_dsp_callback, ctypes.c_void_p),
            None, -1,
        ))

    def _attach_eq_dsp(self) -> None:
        if not self.mixer or self._eq is None:
            return
        # Build the C callback only once and stash it on self to keep the
        # ctypes object alive — if it's GC'd while BASS still holds the
        # pointer, the audio thread crashes.
        if self._eq_dsp_callback is None:
            import numpy as _np
            DSPPROC = ctypes.CFUNCTYPE(
                None, DWORD, DWORD, ctypes.c_void_p, DWORD, ctypes.c_void_p,
            )
            sample_rate = self.sample_rate
            channels = 2  # the mixer is stereo float
            eq_ref = {"eq": None}
            self._eq_ref = eq_ref  # exposed so set_eq() can swap live

            def _dsp_proc(handle, channel, buffer_ptr, length, user):
                eq = eq_ref["eq"]
                if eq is None or buffer_ptr == 0 or length == 0:
                    return
                try:
                    # length is in bytes; mixer is float32 stereo.
                    n_floats = int(length) // 4
                    if n_floats <= 0 or n_floats % channels != 0:
                        return
                    arr_t = (ctypes.c_float * n_floats).from_address(int(buffer_ptr))
                    view = _np.ctypeslib.as_array(arr_t)  # zero-copy view
                    frames = view.reshape(-1, channels)
                    processed = eq.process_f32_array(frames)
                    if processed is not frames:
                        view[:] = processed.ravel()
                except Exception:
                    # Audio thread: swallow exceptions so we never crash BASS.
                    pass

            self._eq_dsp_callback = DSPPROC(_dsp_proc)

        # Point the closure at the current EQ instance.
        try:
            # Mixer format is fixed (stereo float at self.sample_rate), so do
            # this outside the BASS audio callback instead of repeating the
            # same lock/config check for every DSP block.
            self._eq.configure_stream(sample_rate, channels)
        except Exception:
            pass
        self._eq_ref["eq"] = self._eq
        # Priority 0 = default; positive priorities run first.
        self._eq_dsp_handle = int(self.bass.BASS_ChannelSetDSP(
            self.mixer, ctypes.cast(self._eq_dsp_callback, ctypes.c_void_p),
            None, 0,
        ))

    def _detach_eq_dsp(self) -> None:
        if self._eq_dsp_handle and self.mixer:
            try:
                self.bass.BASS_ChannelRemoveDSP(self.mixer, self._eq_dsp_handle)
            except Exception:
                pass
        self._eq_dsp_handle = 0
        if hasattr(self, "_eq_ref"):
            self._eq_ref["eq"] = None

    def _make_deck(self, path: str, volume: float, norm_gain: float = 1.0) -> _Deck:
        self._ensure_mixer()
        flags = BASS_SAMPLE_FLOAT | BASS_STREAM_DECODE | BASS_STREAM_PRESCAN
        handle = int(self.bass.BASS_StreamCreateFile(0, os.fsencode(path), 0, 0, flags))
        if not handle:
            raise self._error(f"BASS_StreamCreateFile({Path(path).name})")
        if not self.mix.BASS_Mixer_StreamAddChannel(
            self.mixer,
            handle,
            BASS_MIXER_CHAN_BUFFER | BASS_MIXER_CHAN_DOWNMIX,
        ):
            self.bass.BASS_StreamFree(handle)
            raise self._error("BASS_Mixer_StreamAddChannel")
        deck = _Deck(str(path), handle, self._norm_factor(norm_gain))
        self._set_deck_volume(deck, volume)
        return deck

    def _gain(self, value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    def _norm_factor(self, value: float) -> float:
        try:
            return max(0.05, min(4.0, float(value)))
        except Exception:
            return 1.0

    def _deck_output_gain(self, deck: _Deck, volume: float) -> float:
        # Per-track normalization belongs on each source deck.  The mixer
        # master stays reserved for the user's BG slider and fades, so
        # crossfades never let the incoming track's LUFS correction alter the
        # outgoing track.
        return max(0.0, min(4.0, self._gain(volume) * self._norm_factor(deck.norm_gain)))

    def _set_deck_volume(self, deck: _Deck | None, volume: float):
        if deck is None:
            return
        self.bass.BASS_ChannelSetAttribute(
            deck.handle,
            BASS_ATTRIB_VOL,
            ctypes.c_float(self._deck_output_gain(deck, volume)),
        )

    def _slide_deck_volume(self, deck: _Deck | None, volume: float, duration_ms: int):
        if deck is None:
            return
        self.bass.BASS_ChannelSlideAttribute(
            deck.handle,
            BASS_ATTRIB_VOL,
            ctypes.c_float(self._deck_output_gain(deck, volume)),
            max(0, int(duration_ms)),
        )

    def _free_deck(self, deck: _Deck | None):
        if deck is None:
            return
        try:
            self.mix.BASS_Mixer_ChannelRemove(deck.handle)
        except Exception:
            pass
        try:
            self.bass.BASS_StreamFree(deck.handle)
        except Exception:
            pass

    def close(self):
        self.stop()
        if getattr(self, "_closed", False):
            return
        self._closed = True

        # free any plugins loaded by this instance
        if self.bass is not None and self._plugin_handles:
            for handle in list(self._plugin_handles):
                try:
                    # Some runtimes provide BASS_PluginFree
                    if hasattr(self.bass, "BASS_PluginFree"):
                        self.bass.BASS_PluginFree(handle)
                except Exception:
                    pass
            self._plugin_handles.clear()

        try:
            self._detach_master_fx()
        except Exception:
            pass

        # decrement global refcount and free shared BASS when last user closes
        if BassBackgroundEngine._bass_init_done:
            try:
                BassBackgroundEngine._bass_init_refcount = max(0, int(BassBackgroundEngine._bass_init_refcount) - 1)
            except Exception:
                BassBackgroundEngine._bass_init_refcount = 0

            if BassBackgroundEngine._bass_init_refcount == 0:
                try:
                    if self.bass is not None:
                        self.bass.BASS_Free()
                except Exception:
                    pass
                BassBackgroundEngine._bass_init_done = False

    def load(self, path: str, paused: bool = True, volume: float | None = None):
        self.stop()
        if volume is not None:
            self.master_volume = self._gain(volume)
        self.primary = self._make_deck(path, 1.0)
        self.set_master_volume(self.master_volume)
        if not paused:
            self.play()

    def play(self) -> bool:
        if self.primary is None:
            return False
        self._ensure_mixer()
        return bool(self.bass.BASS_ChannelPlay(self.mixer, 0))

    def pause(self) -> bool:
        if not self.mixer:
            return False
        return bool(self.bass.BASS_ChannelPause(self.mixer))

    def stop(self):
        self._free_deck(self.secondary)
        self._free_deck(self.primary)
        self.secondary = None
        self.primary = None
        if self.mixer:
            self._detach_native_eq_fx()
            self._detach_eq_dsp()
            try:
                self.bass.BASS_ChannelStop(self.mixer)
            except Exception:
                pass
            try:
                self.bass.BASS_StreamFree(self.mixer)
            except Exception:
                pass
            self.mixer = 0

    def _effective_master(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.master_volume)))
        except Exception:
            return self._gain(self.master_volume)

    def set_normalize_gain(self, factor: float):
        """Compatibility wrapper: apply normalization to the current source deck.

        Older callers set a mixer-wide normalization multiplier.  Keeping the
        correction per deck avoids duplicate/global gain movement during
        crossfades while preserving the existing API.
        """
        self.set_primary_normalize_gain(factor)

    def set_primary_normalize_gain(self, factor: float):
        if self.primary is None:
            return
        self.primary.norm_gain = self._norm_factor(factor)
        self._set_deck_volume(self.primary, 1.0)

    def set_secondary_normalize_gain(self, factor: float):
        if self.secondary is None:
            return
        self.secondary.norm_gain = self._norm_factor(factor)
        self._set_deck_volume(self.secondary, 0.0)

    def set_master_volume(self, volume: float):
        self.master_volume = self._gain(volume)
        if self.mixer:
            self.bass.BASS_ChannelSetAttribute(
                self.mixer,
                BASS_ATTRIB_VOL,
                ctypes.c_float(self._effective_master()),
            )

    def slide_master_volume(self, volume: float, duration_ms: int):
        self.master_volume = self._gain(volume)
        if self.mixer:
            self.bass.BASS_ChannelSlideAttribute(
                self.mixer,
                BASS_ATTRIB_VOL,
                ctypes.c_float(self._effective_master()),
                max(0, int(duration_ms)),
            )

    def start_crossfade(self, path: str, duration_ms: int, norm_gain: float = 1.0) -> bool:
        if self.primary is None or self.secondary is not None:
            return False
        self.secondary = self._make_deck(path, 0.0, norm_gain=norm_gain)
        self._slide_deck_volume(self.primary, 0.0, duration_ms)
        self._slide_deck_volume(self.secondary, 1.0, duration_ms)
        self.play()
        return True

    def complete_crossfade(self) -> bool:
        if self.secondary is None:
            return False
        old = self.primary
        self.primary = self.secondary
        self.secondary = None
        self._free_deck(old)
        self._set_deck_volume(self.primary, 1.0)
        return True

    def cancel_crossfade(self):
        self._free_deck(self.secondary)
        self.secondary = None
        if self.primary is not None:
            self._set_deck_volume(self.primary, 1.0)

    def get_times(self) -> tuple[float, float]:
        if self.primary is None:
            return 0.0, 0.0
        pos = int(self.mix.BASS_Mixer_ChannelGetPosition(self.primary.handle, BASS_POS_BYTE))
        length = int(self.bass.BASS_ChannelGetLength(self.primary.handle, BASS_POS_BYTE))
        if length <= 0:
            return 0.0, 0.0
        return (
            max(0.0, float(self.bass.BASS_ChannelBytes2Seconds(self.primary.handle, pos))),
            max(0.0, float(self.bass.BASS_ChannelBytes2Seconds(self.primary.handle, length))),
        )

    def seek(self, seconds: float) -> bool:
        if self.primary is None:
            return False
        _pos, duration = self.get_times()
        target = max(0.0, float(seconds or 0.0))
        if duration > 0.0:
            target = min(target, max(0.0, duration - 0.001))
        byte_pos = int(self.bass.BASS_ChannelSeconds2Bytes(self.primary.handle, target))
        return bool(
            self.mix.BASS_Mixer_ChannelSetPosition(
                self.primary.handle,
                byte_pos,
                BASS_POS_BYTE | BASS_POS_MIXER_RESET,
            )
        )

    def source_ended(self) -> bool:
        if self.primary is None:
            return True
        active = int(self.mix.BASS_Mixer_ChannelIsActive(self.primary.handle))
        if active == BASS_ACTIVE_STOPPED:
            return True
        pos, dur = self.get_times()
        return bool(dur > 0.0 and pos >= max(0.0, dur - 0.02))

    def is_playing(self) -> bool:
        if not self.mixer or self.primary is None:
            return False
        return int(self.bass.BASS_ChannelIsActive(self.mixer)) == BASS_ACTIVE_PLAYING

    def is_paused(self) -> bool:
        if not self.mixer:
            return False
        return int(self.bass.BASS_ChannelIsActive(self.mixer)) == BASS_ACTIVE_PAUSED

    def meter_level(self) -> float:
        deck = self.secondary or self.primary
        if deck is None:
            return 0.0
        levels = (ctypes.c_float * 2)()
        ok = self.mix.BASS_Mixer_ChannelGetLevelEx(
            deck.handle,
            levels,
            ctypes.c_float(0.05),
            BASS_LEVEL_STEREO | BASS_LEVEL_RMS | BASS_LEVEL_VOLPAN,
        )
        if not ok:
            return 0.0
        rms = max(0.0, float(levels[0]), float(levels[1]))
        if not math.isfinite(rms):
            return 0.0
        return max(0.0, min(1.0, rms * 4.0))
