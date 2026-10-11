"""Soundboard pad channels: one interface, two engines (2026-10-11).

`SoundboardChannel` is what a pad (`SoundboardPad` in 0.2.18.1.py) talks to. It has exactly the interface the BASS pad channel had, and picks its
implementation from the runtime the app hands it:

* the Rust music engine (`rust_bgm_engine.RustBackgroundEngine`, the default): pads are voices inside the engine, mixed through the same EQ and master
  chain as the music, with their own soundboard volume; hitting a playing pad LAYERS another instance and `stop()` stops every instance;
* anything else with real BASS stream functions (the BASS engine, when `bgm_engine = "bass"`): the old single-stream `BassSoundboardChannel`
  (a hit while playing restarts the clip; no layering).

Both raise `SoundboardError` (the BASS channel's `BassSoundboardError` is a subclass), so callers catch one type."""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path


class SoundboardError(RuntimeError):
    pass


class RustSoundboardChannel:
    """One pad on the Rust engine. The clip is decoded to memory on `load`; every `play` starts another instance."""

    supports_layering = True

    def __init__(self, runtime_provider: Callable[[], object | None]):
        self._provider = runtime_provider
        self._runtime = None
        self._slot: int | None = None
        self._path = ""
        self._volume = 1.0
        self._last_play = 0.0
        self._resume_playing = False
        self._resume_seconds = 0.0

    @property
    def path(self) -> str:
        return self._path

    # -- binding to the current engine (it is replaced when the output device changes) --

    def _engine(self):
        engine = self._provider()
        if engine is None or getattr(engine, "_closed", False) or not hasattr(engine, "pad_play"):
            raise SoundboardError("Soundboard output is not available")
        return engine

    def _bind(self):
        engine = self._engine()
        if engine is self._runtime and self._slot is not None:
            return engine
        # first use, or the engine was rebuilt (output change): take a slot on THIS engine and decode the clip again
        self._runtime, self._slot = None, None
        if not self._path:
            raise SoundboardError("No soundboard clip is loaded")
        try:
            slot = engine.pad_allocate_slot()
            try:
                engine.pad_load(slot, self._path, self._volume)
            except Exception:
                engine.pad_release_slot(slot)
                raise
        except SoundboardError:
            raise
        except Exception as exc:
            raise SoundboardError(f"Unable to open soundboard clip: {exc}") from exc
        self._runtime, self._slot = engine, slot
        return engine

    def _release(self):
        engine, slot = self._runtime, self._slot
        self._runtime, self._slot = None, None
        if engine is not None and slot is not None and not getattr(engine, "_closed", False):
            try:
                engine.pad_release_slot(slot)
            except Exception:
                pass

    # -- the BassSoundboardChannel interface --

    def load(self, path: str, volume: float = 1.0):
        new_path = str(path or "")
        self._volume = max(0.0, min(1.0, float(volume)))
        if new_path != self._path:
            self._release()
        self._path = new_path
        if self._path:
            self._bind()      # decode now, so the first hit is instant

    def play(self, start_seconds: float = 0.0):
        engine = self._bind()
        if not engine.pad_play(self._slot, max(0.0, float(start_seconds or 0.0))):
            raise SoundboardError("Unable to play soundboard clip")
        self._last_play = time.monotonic()

    def stop(self, release: bool = False):
        """Stop every instance of this pad."""
        engine, slot = self._runtime, self._slot
        if engine is not None and slot is not None and not getattr(engine, "_closed", False):
            try:
                engine.pad_stop(slot)
            except Exception:
                pass
        if release:
            self._release()

    def close(self):
        self._resume_playing = False
        self._resume_seconds = 0.0
        self.stop(release=True)
        self._path = ""

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))
        engine, slot = self._runtime, self._slot
        if engine is not None and slot is not None and not getattr(engine, "_closed", False):
            engine.pad_set_volume(slot, self._volume)

    def is_playing(self) -> bool:
        engine, slot = self._runtime, self._slot
        if engine is None or slot is None or getattr(engine, "_closed", False):
            return False
        # the engine's snapshot is refreshed once per audio block: a hit just made is playing even if it is not in the snapshot yet
        if time.monotonic() - self._last_play < 0.05:
            return True
        try:
            return engine.pad_instances(slot) > 0
        except Exception:
            return False

    def position_seconds(self) -> float:
        engine, slot = self._runtime, self._slot
        if engine is None or slot is None or getattr(engine, "_closed", False):
            return 0.0
        try:
            return float(engine.pad_position_seconds(slot))
        except Exception:
            return 0.0

    def prepare_output_change(self):
        self._resume_playing = self.is_playing()
        self._resume_seconds = self.position_seconds() if self._resume_playing else 0.0
        self._release()          # the old engine goes away: do not keep a slot on it

    def complete_output_change(self):
        should_resume, resume_at = self._resume_playing, self._resume_seconds
        self._resume_playing, self._resume_seconds = False, 0.0
        if not self._path:
            return
        self._bind()
        if should_resume:
            self.play(resume_at)


class SoundboardChannel:
    """The channel a pad uses: delegates to the Rust or the BASS implementation depending on the runtime it is given."""

    def __init__(self, runtime_provider: Callable[[], object | None]):
        self._provider = runtime_provider
        self._impl = None
        self._kind = ""
        self._pending_path = ""
        self._pending_volume = 1.0

    def _runtime_kind(self) -> str:
        runtime = self._provider()
        if runtime is not None and hasattr(runtime, "pad_play"):
            return "rust"
        return "bass"

    def _get(self):
        kind = self._runtime_kind()
        if self._impl is None or kind != self._kind:
            old = self._impl
            if old is not None:
                try:
                    old.stop(release=True)
                except Exception:
                    pass
            if kind == "rust":
                self._impl = RustSoundboardChannel(self._provider)
            else:
                from bass_soundboard_engine import BassSoundboardChannel
                self._impl = BassSoundboardChannel(self._provider)
            self._kind = kind
            if old is not None and getattr(old, "path", ""):
                self._impl._path = old.path           # the new implementation re-opens the same clip on first use
                self._impl._volume = getattr(old, "_volume", 1.0)
        return self._impl

    @property
    def supports_layering(self) -> bool:
        return bool(getattr(self._get(), "supports_layering", False))

    @property
    def path(self) -> str:
        return self._impl.path if self._impl is not None else ""

    def load(self, path, volume=1.0):
        return self._get().load(path, volume)

    def play(self, start_seconds=0.0):
        return self._get().play(start_seconds)

    def stop(self, release=False):
        if self._impl is not None:
            self._impl.stop(release=release)

    def close(self):
        if self._impl is not None:
            self._impl.close()

    def set_volume(self, volume):
        return self._get().set_volume(volume)

    def is_playing(self):
        return bool(self._impl is not None and self._impl.is_playing())

    def position_seconds(self):
        return self._impl.position_seconds() if self._impl is not None else 0.0

    def prepare_output_change(self):
        if self._impl is not None:
            self._impl.prepare_output_change()

    def complete_output_change(self):
        if self._impl is not None:
            self._get().complete_output_change()
