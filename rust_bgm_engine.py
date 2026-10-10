"""Rust background-music engine behind the BASS engine's Python interface (roadmap item 1, milestone 5).

`RustBackgroundEngine` has the same method names and behaviour the application uses on `BassBackgroundEngine`
(load/play/pause/stop, crossfade, preload, volume slides, normalisation, EQ, master processor, meter, times), implemented over
`libsingws_bgm_ffi.dylib` (rust/crates/singws-bgm-ffi) with ctypes. The audio thread is Rust's: Python only sends commands through a
lock-free queue and reads a snapshot, so a stalled UI thread cannot starve the audio.

It is selected by the `bgm_engine` setting ("bass" is the default). If the library is missing, has a different ABI, or the output cannot be
opened, construction raises `RustBgmUnavailable` and the caller keeps using BASS.

State the adapter keeps itself (so a read right after a call is correct, not one audio block stale): playing/paused intent, which
files are loaded as primary/secondary, the master volume. Position, duration, meter and "ended" come from the engine's snapshot; after
any call that changes the current track the adapter waits (a few ms) for the audio thread to confirm, so those reads are fresh too.
"""
from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
import time
from pathlib import Path

LIB_NAME = "libsingws_bgm_ffi.dylib"
EXPECTED_ABI_VERSION = 1
STAT_COUNT = 16

FLAG_PLAYING, FLAG_PAUSED, FLAG_ENDED, FLAG_XFADE_ACTIVE, FLAG_XFADE_FINISHED, FLAG_HAS_PRIMARY, FLAG_HAS_SECONDARY = 1, 2, 4, 8, 16, 32, 64

_LIB = None
_LIB_LOCK = threading.Lock()


class RustBgmUnavailable(RuntimeError):
    pass


def _arch_dir() -> str:
    import platform
    return "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("SINGWS_BGM_LIB")
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
    if not getattr(sys, "frozen", False):
        # Development runs only. A shipped app uses ONLY the library bundled inside it: a stray build in a cache folder must never be loaded.
        here = Path(__file__).resolve().parent
        paths.append(here / "native" / "singws_bgm" / _arch_dir() / LIB_NAME)
        paths.append(Path.home() / ".cache" / "singws-rust-target" / "release" / LIB_NAME)
    return paths


def _load():
    global _LIB
    with _LIB_LOCK:
        if _LIB is not None:
            return _LIB
        last = "no candidate path exists"
        for path in _candidate_paths():
            if not path.exists():
                continue
            try:
                lib = ctypes.CDLL(str(path))
                lib.singws_bgm_abi_version.restype = ctypes.c_uint32
                if int(lib.singws_bgm_abi_version()) != EXPECTED_ABI_VERSION:
                    last = f"{path}: ABI {lib.singws_bgm_abi_version()} != {EXPECTED_ABI_VERSION}"
                    continue
                lib.singws_bgm_stat_count.restype = ctypes.c_uint32
                lib.singws_bgm_master_param_count.restype = ctypes.c_uint32
                lib.singws_bgm_master_param_names.restype = ctypes.c_char_p
                if int(lib.singws_bgm_stat_count()) != STAT_COUNT:
                    last = f"{path}: stats layout differs"
                    continue
                _declare(lib)
                _LIB = lib
                return lib
            except (OSError, AttributeError) as exc:
                last = f"{path}: {exc}"
        raise RustBgmUnavailable(f"{LIB_NAME} not usable ({last})")


def _declare(lib) -> None:
    V, I32, U32, F32, SZ = ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32, ctypes.c_float, ctypes.c_size_t
    CP = ctypes.c_char_p
    sig = {
        "singws_bgm_create": ([CP, U32, U32, ctypes.c_char_p, SZ], V),
        "singws_bgm_create_headless": ([U32], V),
        "singws_bgm_render": ([V, ctypes.POINTER(F32), U32], I32),
        "singws_bgm_destroy": ([V], None),
        "singws_bgm_rate": ([V], U32),
        "singws_bgm_device_name": ([V, ctypes.c_char_p, SZ], None),
        "singws_bgm_fell_back": ([V], I32),
        "singws_bgm_device_lost": ([V], I32),
        "singws_bgm_deck_open": ([V, CP, ctypes.c_char_p, SZ], V),
        "singws_bgm_deck_len_frames": ([V], ctypes.c_int64),
        "singws_bgm_deck_free": ([V], None),
        "singws_bgm_play": ([V], I32),
        "singws_bgm_pause": ([V], I32),
        "singws_bgm_stop": ([V], I32),
        "singws_bgm_invalidate_preload": ([V], I32),
        "singws_bgm_complete_crossfade": ([V], I32),
        "singws_bgm_cancel_crossfade": ([V], I32),
        "singws_bgm_load": ([V, V, F32, I32, F32, I32], I32),
        "singws_bgm_preload": ([V, V, F32], I32),
        "singws_bgm_set_master_volume": ([V, F32], I32),
        "singws_bgm_set_primary_norm": ([V, F32], I32),
        "singws_bgm_set_secondary_norm": ([V, F32], I32),
        "singws_bgm_seek": ([V, F32], I32),
        "singws_bgm_slide_master_volume": ([V, F32, U32], I32),
        "singws_bgm_start_crossfade": ([V, U32, F32], I32),
        "singws_bgm_flush": ([V, U32], I32),
        "singws_bgm_collect_garbage": ([V], U32),
        "singws_bgm_get_times": ([V, ctypes.POINTER(F32), ctypes.POINTER(F32)], None),
        "singws_bgm_meter": ([V], F32),
        "singws_bgm_engine_master_volume": ([V], F32),
        "singws_bgm_flags": ([V], U32),
        "singws_bgm_set_eq": ([V, I32, ctypes.POINTER(F32)], I32),
        "singws_bgm_set_master_enabled": ([V, I32], None),
        "singws_bgm_set_master_params": ([V, ctypes.POINTER(ctypes.c_double), U32], I32),
        "singws_bgm_stats": ([V, ctypes.POINTER(ctypes.c_uint64), I32], None),
    }
    for name, (args, res) in sig.items():
        fn = getattr(lib, name)
        fn.argtypes = args
        fn.restype = res


def available() -> bool:
    try:
        _load()
        return True
    except RustBgmUnavailable:
        return False


class _Deck:
    """What the application sees as `engine.primary` / `engine.secondary`: a loaded file."""

    def __init__(self, path: str, norm_gain: float, ptr=None):
        self.path = str(path)
        self.norm_gain = float(norm_gain)
        self.ptr = ptr          # native deck pointer until it is handed to the engine
        try:
            st = os.stat(path)
            self.mtime, self.size = int(st.st_mtime), int(st.st_size)
        except OSError:
            self.mtime, self.size = 0, 0

    def matches(self, path: str) -> bool:
        if str(path) != self.path:
            return False
        try:
            st = os.stat(path)
            return int(st.st_mtime) == self.mtime and int(st.st_size) == self.size
        except OSError:
            return False


class _BassShim:
    """The application's volume-recovery code calls `eng.bass.BASS_ChannelPlay(eng.mixer, False)`; map it to `play()`."""

    def __init__(self, engine):
        self._engine = engine

    def BASS_ChannelPlay(self, _mixer, _restart):  # noqa: N802 - name mirrors the BASS API
        return 1 if self._engine.play() else 0


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return 0.0


def _norm(v) -> float:
    try:
        return max(0.05, min(4.0, float(v)))
    except Exception:
        return 1.0


class RustBackgroundEngine:
    backend_name = "Rust"

    def __init__(self, output_name: str | None = None, sample_rate: int = 48000, headless: bool = False):
        """`headless=True` (tests and tools): no sound card; pull audio with :meth:`render`."""
        self._lib = _load()
        self.output_name = str(output_name or "").strip()
        err = ctypes.create_string_buffer(512)
        dev = self.output_name.encode() if self.output_name else None
        if headless:
            handle = self._lib.singws_bgm_create_headless(int(sample_rate or 48000))
        else:
            handle = self._lib.singws_bgm_create(dev, int(sample_rate or 48000), 0, err, len(err))
        if not handle:
            raise RustBgmUnavailable(err.value.decode(errors="replace") or "could not open the output")
        self._headless = bool(headless)
        self._h = ctypes.c_void_p(handle)
        self.sample_rate = int(self._lib.singws_bgm_rate(self._h))
        name = ctypes.create_string_buffer(256)
        self._lib.singws_bgm_device_name(self._h, name, len(name))
        self.device_name = name.value.decode(errors="replace")
        self.fell_back_to_default = bool(self._lib.singws_bgm_fell_back(self._h))
        self.master_volume = 0.8
        self.primary: _Deck | None = None
        self.secondary: _Deck | None = None
        self.mixer = 1                      # truthy marker for code that checks `engine.mixer`
        self.bass = _BassShim(self)
        self._playing = False
        self._paused = False
        self._closed = False
        self._eq = None
        self._master_names: list[str] | None = None
        self._diag_enabled = False
        self._diag_last = None
        self.master_attached = False        # a master processor's parameters have been pushed and the chain is on
        self._lock = threading.RLock()      # serialises Python-side bookkeeping only; the audio thread never sees it

    # ---------------- helpers ----------------

    def _ok(self) -> bool:
        return not self._closed and bool(self._h)

    def render(self, frames: int):
        """Headless only: mix `frames` stereo frames and return them as a (frames, 2) float32 numpy array.

        Rendering is far faster than real time, so it is paced: the decoder thread (about 50x real time) gets a moment between small
        blocks, exactly as a sound card's own clock would give it. Without that, a test would measure underrun silence."""
        import numpy as np
        out = np.zeros((int(frames), 2), dtype=np.float32)
        if not self._ok():
            return out
        done = 0
        while done < frames:
            n = min(1200, frames - done)
            view = out[done:done + n]
            self._lib.singws_bgm_render(self._h, view.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), n)
            done += n
            if done < frames:
                time.sleep(0.002)
        return out

    def _sync(self, timeout_ms: int = 150) -> None:
        """Wait until the audio thread has run every command sent so far and published fresh state."""
        if self._headless:
            if self._ok():
                self.render(8)      # nobody else drives a headless engine: run the queued commands now
                self._lib.singws_bgm_collect_garbage(self._h)
            return
        if self._ok():
            self._lib.singws_bgm_flush(self._h, timeout_ms)
            self._lib.singws_bgm_collect_garbage(self._h)

    def _open_deck(self, path: str, norm_gain: float) -> _Deck:
        err = ctypes.create_string_buffer(512)
        ptr = self._lib.singws_bgm_deck_open(self._h, os.fsencode(path), err, len(err))
        if not ptr:
            raise RustBgmUnavailable(f"cannot open {Path(path).name}: {err.value.decode(errors='replace')}")
        return _Deck(path, _norm(norm_gain), ptr)

    def _take_ptr(self, deck: _Deck):
        ptr, deck.ptr = deck.ptr, None
        return ptr

    def _free_deck(self, deck) -> None:
        if deck is not None and deck.ptr:
            self._lib.singws_bgm_deck_free(ctypes.c_void_p(deck.ptr))
            deck.ptr = None

    # ---------------- transport ----------------

    def load(self, path: str, paused: bool = True, volume: float | None = None):
        with self._lock:
            self.stop()
            if volume is not None:
                self.master_volume = _clamp01(volume)
            deck = self._open_deck(path, 1.0)
            self.primary = deck
            self._playing = not paused
            self._paused = bool(paused)
            ptr = self._take_ptr(deck)
            self._lib.singws_bgm_load(self._h, ctypes.c_void_p(ptr), 1.0, int(bool(paused)), float(self.master_volume), 1)
            self._sync()

    def play(self) -> bool:
        if self.primary is None or not self._ok():
            return False
        self._playing, self._paused = True, False
        return bool(self._lib.singws_bgm_play(self._h))

    def pause(self) -> bool:
        if not self._ok():
            return False
        self._paused = True
        self._playing = False
        return bool(self._lib.singws_bgm_pause(self._h))

    def stop(self):
        with self._lock:
            if not self._ok():
                return
            self._lib.singws_bgm_stop(self._h)
            self._free_deck(self.primary)
            self._free_deck(self.secondary)
            self.primary = self.secondary = None
            self._playing = self._paused = False
            self._sync()

    def is_playing(self) -> bool:
        return bool(self._playing and self.primary is not None)

    def is_paused(self) -> bool:
        return bool(self._paused)

    # ---------------- volume / normalisation ----------------

    def _effective_master(self) -> float:
        return _clamp01(self.master_volume)

    def set_master_volume(self, volume: float):
        self.master_volume = _clamp01(volume)
        if self._ok():
            self._lib.singws_bgm_set_master_volume(self._h, float(self.master_volume))

    def slide_master_volume(self, volume: float, duration_ms: int):
        self.master_volume = _clamp01(volume)
        if self._ok():
            self._lib.singws_bgm_slide_master_volume(self._h, float(self.master_volume), max(0, int(duration_ms)))

    def fade_settle_delay_ms(self) -> int:
        return 0

    def set_normalize_gain(self, factor: float):
        self.set_primary_normalize_gain(factor)

    def set_primary_normalize_gain(self, factor: float):
        if self.primary is not None:
            self.primary.norm_gain = _norm(factor)
        if self._ok():
            self._lib.singws_bgm_set_primary_norm(self._h, float(_norm(factor)))

    def set_secondary_normalize_gain(self, factor: float):
        if self.secondary is not None:
            self.secondary.norm_gain = _norm(factor)
        if self._ok():
            self._lib.singws_bgm_set_secondary_norm(self._h, float(_norm(factor)))

    # ---------------- prepared decks (slow file open off the UI thread) ----------------

    def prepare_primary(self, path: str, norm_gain: float = 1.0) -> _Deck:
        return self._open_deck(path, norm_gain)

    def install_prepared_primary(self, deck: _Deck, volume: float | None = None) -> bool:
        with self._lock:
            if not self._ok() or deck is None or not deck.ptr:
                return False
            self.stop()
            if volume is not None:
                self.master_volume = _clamp01(volume)
            self.primary = deck
            self._playing = False
            self._paused = True
            ptr = self._take_ptr(deck)
            ok = bool(self._lib.singws_bgm_load(self._h, ctypes.c_void_p(ptr), float(deck.norm_gain), 1, float(self.master_volume), 1))
            self._sync()
            return ok

    def discard_prepared_primary(self, deck) -> None:
        self._free_deck(deck)

    def install_prepared_secondary(self, deck: _Deck) -> bool:
        with self._lock:
            if not self._ok() or self.primary is None or deck is None or not deck.ptr:
                return False
            self._free_deck(self.secondary)
            self.secondary = deck
            ptr = self._take_ptr(deck)
            ok = bool(self._lib.singws_bgm_preload(self._h, ctypes.c_void_p(ptr), float(deck.norm_gain)))
            self._lib.singws_bgm_collect_garbage(self._h)
            return ok

    def preload_secondary(self, path: str, norm_gain: float = 1.0) -> bool:
        if self.primary is None:
            return False
        if self.secondary is not None and self.secondary.matches(path):
            self.set_secondary_normalize_gain(norm_gain)
            return True
        return self.install_prepared_secondary(self._open_deck(path, norm_gain))

    def invalidate_secondary_preload(self):
        with self._lock:
            if self._ok() and not self._crossfade_running():
                self._lib.singws_bgm_invalidate_preload(self._h)
                self._free_deck(self.secondary)
                self.secondary = None

    # ---------------- crossfade ----------------

    def _crossfade_running(self) -> bool:
        return bool(getattr(self, "_xfade_requested", False))

    def start_crossfade(self, path: str, duration_ms: int, norm_gain: float = 1.0, start_seconds: float = 0.0) -> bool:
        with self._lock:
            if self.primary is None or not self._ok():
                return False
            if self.secondary is None or not self.secondary.matches(path):
                if not self.install_prepared_secondary(self._open_deck(path, norm_gain)):
                    return False
            else:
                self.set_secondary_normalize_gain(norm_gain)
            self._xfade_requested = True
            self._playing, self._paused = True, False
            ok = bool(self._lib.singws_bgm_start_crossfade(self._h, max(0, int(duration_ms)), float(max(0.0, start_seconds))))
            self._sync()
            return ok

    def crossfade_finished(self) -> bool:
        return bool(self._xfade_requested_flag() and self._flags() & FLAG_XFADE_FINISHED)

    def _xfade_requested_flag(self) -> bool:
        return bool(getattr(self, "_xfade_requested", False))

    def crossfade_lead_seconds(self) -> float:
        return 0.0          # no queue between decoder and clock: the engine's position IS the audible position

    def complete_crossfade(self) -> bool:
        with self._lock:
            if self.secondary is None or not self._ok():
                return False
            self._free_deck(self.primary)
            self.primary, self.secondary = self.secondary, None
            self._xfade_requested = False
            self._lib.singws_bgm_complete_crossfade(self._h)
            self._sync()
            return True

    def cancel_crossfade(self):
        with self._lock:
            if not self._ok():
                return
            self._xfade_requested = False
            self._lib.singws_bgm_cancel_crossfade(self._h)
            self._free_deck(self.secondary)
            self.secondary = None
            self._sync()

    # ---------------- position / state ----------------

    def _flags(self) -> int:
        return int(self._lib.singws_bgm_flags(self._h)) if self._ok() else 0

    def get_times(self) -> tuple[float, float]:
        if not self._ok() or self.primary is None:
            return 0.0, 0.0
        pos, dur = ctypes.c_float(), ctypes.c_float()
        self._lib.singws_bgm_get_times(self._h, ctypes.byref(pos), ctypes.byref(dur))
        return float(pos.value), float(dur.value)

    def seek(self, seconds: float) -> bool:
        if self.primary is None or not self._ok():
            return False
        ok = bool(self._lib.singws_bgm_seek(self._h, float(max(0.0, seconds))))
        self._sync()
        return ok

    def source_ended(self) -> bool:
        if self.primary is None or not self._ok():
            return True
        return bool(self._flags() & FLAG_ENDED)

    def meter_level(self) -> float:
        if not self._ok() or self.primary is None:
            return 0.0
        v = float(self._lib.singws_bgm_meter(self._h))
        return v if math.isfinite(v) else 0.0

    # ---------------- effects ----------------

    def set_eq(self, eq) -> None:
        """Attach (or detach with None) the 10-band graphic EQ. Gains are read now; call again after changing them."""
        self._eq = eq
        if not self._ok():
            return
        gains = (ctypes.c_float * 10)()
        enabled = 0
        if eq is not None:
            try:
                values = [max(-12.0, min(12.0, float(g))) for g in eq.gains_db()]
                for i, g in enumerate(values[:10]):
                    gains[i] = g
                enabled = int(bool(eq.enabled()) and not bool(eq.is_flat()))
            except Exception:
                enabled = 0
        self._lib.singws_bgm_set_eq(self._h, enabled, gains)

    def _param_names(self) -> list[str]:
        if self._master_names is None:
            self._master_names = (self._lib.singws_bgm_master_param_names() or b"").decode().split(",")
        return self._master_names

    def set_master_processor(self, processor) -> None:
        """Mirror a master processor's parameters and on/off state into the engine's own copy of the chain.

        The application calls this again after every parameter change (it did the same for BASS, where the instance itself carried the
        change); here each call pushes the processor's current parameters. None switches the chain off."""
        if not self._ok():
            return
        if processor is None:
            self._lib.singws_bgm_set_master_enabled(self._h, 0)
            self.master_attached = False
            return
        try:
            params = dict(processor.params())
            names = self._param_names()
            values = (ctypes.c_double * len(names))(*[float(params[n]) for n in names])
            if self._lib.singws_bgm_set_master_params(self._h, values, len(names)) != 0:
                return
            self._lib.singws_bgm_set_master_enabled(self._h, int(bool(processor.enabled())))
            self.master_attached = bool(processor.enabled())
        except Exception:
            self._lib.singws_bgm_set_master_enabled(self._h, 0)
            self.master_attached = False

    # ---------------- diagnostics ----------------

    def set_audio_diagnostics(self, enabled: bool) -> None:
        self._diag_enabled = bool(enabled)
        self._diag_last = self._stats() if enabled else None

    def _stats(self) -> list[int]:
        arr = (ctypes.c_uint64 * STAT_COUNT)()
        self._lib.singws_bgm_stats(self._h, arr, STAT_COUNT)
        return list(arr)

    def device_lost(self) -> bool:
        return bool(self._lib.singws_bgm_device_lost(self._h)) if self._ok() else True

    def audio_diagnostics_snapshot(self):
        """Counters since the previous snapshot in the shape `format_audio_diag` expects; None when diagnostics are off."""
        if not self._diag_enabled or not self._ok():
            return None
        self._lib.singws_bgm_collect_garbage(self._h)
        now = self._stats()
        last = self._diag_last or [0] * STAT_COUNT
        self._diag_last = now
        blocks = now[0] - last[0]
        total_us = now[2] - last[2]
        return {
            "master": {
                "blocks": blocks,
                "mean_us": (total_us / blocks) if blocks else 0.0,
                "p99_upper_us": float(now[1]),      # a bound: the largest callback since start
                "max_us": float(now[1]),
                "over_budget": now[3] - last[3],
                "gap_max_ms": 0.0,
                "gaps_over_100ms": 0,
                "gaps_over_500ms": 0,
                "restarts": 0,
            },
            "engine": "rust-engine",
            "bass_cpu": None,
            "mixer_stalled": int(now[12] - last[12]) + int(now[11] - last[11]) + int(now[10] - last[10]) + int(now[6] - last[6]),
            "latency_ms": now[4] / 1000.0,
            "device_lost": bool(now[9]),
        }

    # ---------------- shutdown ----------------

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for d in (self.primary, self.secondary):
                self._free_deck(d)
            self.primary = self.secondary = None
            h, self._h = self._h, None
            if h:
                self._lib.singws_bgm_destroy(h)
