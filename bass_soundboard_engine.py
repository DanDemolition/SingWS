from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import tempfile
from collections.abc import Callable

from bass_background_engine import (
    BASS_ACTIVE_PLAYING,
    BASS_ATTRIB_VOL,
    BASS_POS_BYTE,
    BASS_SAMPLE_FLOAT,
    BASS_STREAM_PRESCAN,
)


class BassSoundboardError(RuntimeError):
    pass


class BassSoundboardChannel:
    """One low-latency soundboard stream on the existing BASS output.

    The background engine owns BASS_Init/BASS_Free. This object only owns its
    stream, which lets pads mix independently with the BGM mixer without
    adding a Python callback to the realtime audio path.
    """

    def __init__(self, runtime_provider: Callable[[], object | None]):
        self._runtime_provider = runtime_provider
        self._runtime_owner = None
        self._bass = None
        self._handle = 0
        self._path = ""
        self._playback_path = ""
        self._volume = 1.0
        self._resume_playing = False
        self._resume_seconds = 0.0

    @property
    def path(self) -> str:
        return self._path

    def _runtime(self):
        runtime = self._runtime_provider()
        if runtime is None or getattr(runtime, "_closed", False):
            raise BassSoundboardError("BASS output is not available")
        bass = getattr(runtime, "bass", None)
        if bass is None:
            raise BassSoundboardError("BASS runtime is not loaded")
        return runtime, bass

    @staticmethod
    def _error_code(bass) -> int:
        try:
            return int(bass.BASS_ErrorGetCode())
        except Exception:
            return -1

    def _release_stream(self):
        handle, bass = self._handle, self._bass
        self._handle = 0
        self._runtime_owner = None
        self._bass = None
        if handle and bass is not None:
            try:
                bass.BASS_ChannelStop(handle)
            except Exception:
                pass
            try:
                bass.BASS_StreamFree(handle)
            except Exception:
                pass

    def _ensure_stream(self) -> int:
        if not self._path:
            raise BassSoundboardError("No soundboard clip is loaded")
        runtime, bass = self._runtime()
        if self._handle and self._runtime_owner is runtime and self._bass is bass:
            return self._handle
        self._release_stream()
        flags = BASS_SAMPLE_FLOAT | BASS_STREAM_PRESCAN
        playback_path = self._playback_path or self._path
        handle = self._create_stream(bass, playback_path, flags)
        if not handle and self._error_code(bass) == 41:
            # BASS does not natively decode AAC/M4A in the bundled runtime.
            # Convert once during preload, never on its realtime audio thread.
            playback_path = self._pcm_cache_path(self._path)
            handle = self._create_stream(bass, playback_path, flags)
        if not handle:
            raise BassSoundboardError(
                f"Unable to open soundboard clip (BASS error {self._error_code(bass)})"
            )
        self._runtime_owner = runtime
        self._bass = bass
        self._handle = handle
        self._playback_path = playback_path
        self.set_volume(self._volume)
        return handle

    @staticmethod
    def _create_stream(bass, path: str, flags: int) -> int:
        return int(
            bass.BASS_StreamCreateFile(0, os.fsencode(path), 0, 0, flags) or 0
        )

    @classmethod
    def _pcm_cache_path(cls, source_path: str) -> str:
        source = Path(source_path).resolve()
        try:
            stat = source.stat()
            identity = f"{source}\0{stat.st_size}\0{stat.st_mtime_ns}"
        except OSError as exc:
            raise BassSoundboardError(f"Unable to read soundboard clip: {exc}") from exc
        digest = hashlib.sha256(identity.encode("utf-8", "surrogatepass")).hexdigest()
        cache_dir = Path(tempfile.gettempdir()) / "singws-soundboard-pcm"
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        output = cache_dir / f"{digest}.wav"
        if output.exists() and output.stat().st_size > 44:
            return str(output)
        rendered = None
        try:
            from libmpv_media_jobs import decode_audio_wav
            rendered = decode_audio_wav(
                str(source), sample_rate=48000, channels=2, timeout=120)
            if Path(rendered).stat().st_size <= 44:
                raise BassSoundboardError("Unable to decode soundboard clip")
            os.replace(rendered, output)
            rendered = None
            return str(output)
        except BassSoundboardError:
            raise
        except Exception as exc:
            raise BassSoundboardError(f"Unable to decode soundboard clip: {exc}") from exc
        finally:
            try:
                if rendered and Path(rendered).exists():
                    Path(rendered).unlink()
            except OSError:
                pass

    def load(self, path: str, volume: float = 1.0):
        new_path = str(path or "")
        self._volume = max(0.0, min(1.0, float(volume)))
        if new_path != self._path:
            self._release_stream()
            self._playback_path = ""
        self._path = new_path
        if self._path:
            self._ensure_stream()  # preload once so the first pad hit is fast

    def play(self, start_seconds: float = 0.0):
        handle = self._ensure_stream()
        start = max(0.0, float(start_seconds or 0.0))
        restart = start <= 0.0
        if not restart:
            byte_pos = int(self._bass.BASS_ChannelSeconds2Bytes(handle, start))
            if not self._bass.BASS_ChannelSetPosition(handle, byte_pos, BASS_POS_BYTE):
                raise BassSoundboardError(
                    f"Unable to seek soundboard clip (BASS error {self._error_code(self._bass)})"
                )
        if not self._bass.BASS_ChannelPlay(handle, bool(restart)):
            raise BassSoundboardError(
                f"Unable to play soundboard clip (BASS error {self._error_code(self._bass)})"
            )

    def stop(self, release: bool = False):
        if self._handle and self._bass is not None:
            try:
                self._bass.BASS_ChannelStop(self._handle)
            except Exception:
                pass
        if release:
            self._release_stream()

    def close(self):
        self._resume_playing = False
        self._resume_seconds = 0.0
        self._release_stream()
        self._path = ""
        self._playback_path = ""

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))
        if self._handle and self._bass is not None:
            ok = self._bass.BASS_ChannelSetAttribute(
                self._handle, BASS_ATTRIB_VOL, ctypes.c_float(self._volume)
            )
            if not ok:
                raise BassSoundboardError(
                    f"Unable to set soundboard volume (BASS error {self._error_code(self._bass)})"
                )

    def is_playing(self) -> bool:
        if not self._handle or self._bass is None:
            return False
        try:
            return int(self._bass.BASS_ChannelIsActive(self._handle)) == BASS_ACTIVE_PLAYING
        except Exception:
            return False

    def position_seconds(self) -> float:
        if not self._handle or self._bass is None:
            return 0.0
        try:
            byte_pos = int(self._bass.BASS_ChannelGetPosition(self._handle, BASS_POS_BYTE))
            if byte_pos >= (1 << 63):
                return 0.0
            return max(0.0, float(self._bass.BASS_ChannelBytes2Seconds(self._handle, byte_pos)))
        except Exception:
            return 0.0

    def prepare_output_change(self):
        self._resume_playing = self.is_playing()
        self._resume_seconds = self.position_seconds() if self._resume_playing else 0.0
        # The owning background engine may call BASS_Free next. Release first
        # so no pad retains a handle from the old CoreAudio device.
        self._release_stream()

    def complete_output_change(self):
        should_resume = self._resume_playing
        resume_at = self._resume_seconds
        self._resume_playing = False
        self._resume_seconds = 0.0
        if not self._path:
            return
        self._ensure_stream()
        if should_resume:
            self.play(resume_at)
