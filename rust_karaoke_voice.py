"""The karaoke song as a voice of the Rust audio engine (roadmap item 3).

It rides on a :class:`rust_bgm_engine.RustBackgroundEngine` (the same sound-card output as the background music and the soundboard) and has
its own chain inside the engine: streaming decode -> Signalsmith key/tempo -> normalization and volume -> the karaoke graphic EQ -> the
exact master chain. The result is added after the music chain, so the music can fade out underneath it.

Key and tempo are two independent controls. Position is SONG time (what the lyrics follow), so it stays right at any tempo.
Nothing in the application uses this yet (`karaoke_audio_engine` will select it; the default stays mpv until it is proven).
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from rust_bgm_engine import RustBgmUnavailable, _norm

KFLAG_LOADED, KFLAG_PLAYING, KFLAG_PAUSED, KFLAG_FINISHED, KFLAG_PENDING = 1, 2, 4, 8, 16


class RustKaraokeVoice:
    def __init__(self, engine):
        self._e = engine
        self.path: str | None = None
        self._key = 0.0
        self._tempo = 100.0

    # ---- helpers ----
    @property
    def _lib(self):
        return self._e._lib

    @property
    def _h(self):
        return self._e._h

    def _ok(self) -> bool:
        return self._e._ok()

    def _sync(self) -> None:
        self._e._sync()

    # ---- transport ----
    def load(self, path: str, norm_gain: float = 1.0, paused: bool = True, volume: float | None = 1.0) -> None:
        """Open `path` (an audio file: the MP3 of a CDG disc, or an MP4) and make it the karaoke song. Blocks while the decoder opens it."""
        if not self._ok():
            raise RustBgmUnavailable("the Rust engine is closed")
        deck = self._e._open_deck(str(path), norm_gain)
        ptr = self._e._take_ptr(deck)
        self.path = str(path)
        with self._e._lock:
            self._lib.singws_bgm_karaoke_load(self._h, ctypes.c_void_p(ptr), _norm(norm_gain), int(bool(paused)),
                                              float(1.0 if volume is None else volume), int(volume is not None))
            # key and tempo belong to the song being played, so a new song starts from the values already set
            self._lib.singws_bgm_karaoke_set_key(self._h, float(self._key))
            self._lib.singws_bgm_karaoke_set_tempo(self._h, float(self._tempo))
            self._sync()

    def play(self) -> bool:
        return bool(self._ok() and self._lib.singws_bgm_karaoke_play(self._h))

    def pause(self) -> bool:
        return bool(self._ok() and self._lib.singws_bgm_karaoke_pause(self._h))

    def stop(self) -> None:
        if self._ok():
            with self._e._lock:
                self._lib.singws_bgm_karaoke_stop(self._h)
                self._sync()
        self.path = None

    def seek_seconds(self, seconds: float) -> bool:
        return bool(self._ok() and self._lib.singws_bgm_karaoke_seek(self._h, float(max(0.0, seconds))))

    # ---- key / tempo (independent) ----
    def set_key_semitones(self, semitones: float) -> None:
        self._key = max(-24.0, min(24.0, float(semitones or 0.0)))
        if self._ok():
            self._lib.singws_bgm_karaoke_set_key(self._h, self._key)

    def set_tempo_percent(self, percent: float) -> None:
        self._tempo = max(25.0, min(400.0, float(percent or 100.0)))
        if self._ok():
            self._lib.singws_bgm_karaoke_set_tempo(self._h, self._tempo)

    @property
    def key_semitones(self) -> float:
        return self._key

    @property
    def tempo_percent(self) -> float:
        return self._tempo

    # ---- slaving to the picture (mpv is the master clock) ----
    def _measured_rate(self, position_seconds: float, commanded: float) -> float:
        """The pace the picture's clock is REALLY running at (mpv applies a speed change a little late), over the last ~0.6 s."""
        import time
        now = time.monotonic()
        hist = self.__dict__.setdefault("_clock_hist", [])
        if commanded <= 0.0:
            hist.clear()
            return 0.0
        if hist and (now - hist[-1][0] > 0.5 or position_seconds < hist[-1][1] - 0.2 or position_seconds > hist[-1][1] + 2.0):
            hist.clear()                                    # a gap or a jump (a seek): the old samples say nothing about the pace
        hist.append((now, position_seconds))
        while hist and now - hist[0][0] > 0.6:
            hist.pop(0)
        if len(hist) < 8 or hist[-1][0] - hist[0][0] < 0.3:
            return commanded
        rate = (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0])
        return max(0.2, min(5.0, rate))

    def slave_to(self, position_seconds: float, rate: float) -> None:
        """Tell the voice where the picture is NOW (song seconds) and how fast it runs (tempo/100; 0 = paused). Call every ~30 ms; the voice
        extrapolates between calls and trims its own tempo by under 1% to stay on it. A big jump (a seek) is followed with one resync."""
        if self._ok():
            measured = self._measured_rate(float(position_seconds), float(max(0.0, rate)))
            self._lib.singws_bgm_karaoke_slave_clock(self._h, float(position_seconds), measured)

    def free_run(self) -> None:
        if self._ok():
            self._lib.singws_bgm_karaoke_slave_off(self._h)

    def set_output_latency_seconds(self, seconds: float) -> None:
        if self._ok():
            self._lib.singws_bgm_karaoke_set_output_latency(self._h, float(max(0.0, seconds)))

    # ---- level ----
    def set_volume(self, volume: float) -> None:
        if self._ok():
            self._lib.singws_bgm_karaoke_set_volume(self._h, float(max(0.0, min(1.0, volume))))

    def slide_volume(self, volume: float, ms: int) -> None:
        if self._ok():
            self._lib.singws_bgm_karaoke_slide_volume(self._h, float(max(0.0, min(1.0, volume))), int(max(0, ms)))

    def set_norm_gain(self, gain: float) -> None:
        if self._ok():
            self._lib.singws_bgm_karaoke_set_norm(self._h, _norm(gain))

    def volume(self) -> float:
        return float(self._lib.singws_bgm_karaoke_volume(self._h)) if self._ok() else 0.0

    # ---- state ----
    def times(self) -> tuple[float, float]:
        """(audible song position, duration) in seconds."""
        pos, dur = ctypes.c_float(0.0), ctypes.c_float(0.0)
        if self._ok():
            self._lib.singws_bgm_karaoke_times(self._h, ctypes.byref(pos), ctypes.byref(dur))
        return float(pos.value), float(dur.value)

    def position_seconds(self) -> float:
        return self.times()[0]

    def duration_seconds(self) -> float:
        return self.times()[1]

    def _flags(self) -> int:
        return int(self._lib.singws_bgm_karaoke_flags(self._h)) if self._ok() else 0

    def is_loaded(self) -> bool:
        return bool(self._flags() & KFLAG_LOADED)

    def is_playing(self) -> bool:
        return bool(self._flags() & KFLAG_PLAYING)

    def is_paused(self) -> bool:
        return bool(self._flags() & KFLAG_PAUSED)

    def is_finished(self) -> bool:
        """The song played to its end (its tail is drained)."""
        return bool(self._flags() & KFLAG_FINISHED)

    def is_ready(self) -> bool:
        """Loaded and its source can deliver audio now (not still opening, not in the middle of a seek)."""
        f = self._flags()
        return bool(f & KFLAG_LOADED) and not (f & KFLAG_PENDING)

    def pending_frames(self) -> int:
        return int(self._lib.singws_bgm_karaoke_pending_frames(self._h)) if self._ok() else 0

    # ---- the karaoke chain's own EQ and master settings ----
    def set_eq(self, eq) -> None:
        gains = (ctypes.c_float * 10)()
        enabled = 0
        if eq is not None:
            try:
                for i, g in enumerate([max(-12.0, min(12.0, float(g))) for g in eq.gains_db()][:10]):
                    gains[i] = g
                enabled = int(bool(eq.enabled()) and not bool(eq.is_flat()))
            except Exception:
                enabled = 0
        if self._ok():
            self._lib.singws_bgm_karaoke_set_eq(self._h, enabled, gains)

    def set_master_processor(self, processor) -> None:
        if not self._ok():
            return
        if processor is None:
            self._lib.singws_bgm_karaoke_set_master_enabled(self._h, 0)
            return
        try:
            params = dict(processor.params())
            names = self._e._param_names()
            values = (ctypes.c_double * len(names))(*[float(params[n]) for n in names])
            if self._lib.singws_bgm_karaoke_set_master_params(self._h, values, len(names)) != 0:
                return
            self._lib.singws_bgm_karaoke_set_master_enabled(self._h, int(bool(processor.enabled())))
        except Exception:
            self._lib.singws_bgm_karaoke_set_master_enabled(self._h, 0)
