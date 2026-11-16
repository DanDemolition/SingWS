"""Announcement box: a short spoken clip recorded on the host's phone (the v2 DAW page), held in MEMORY until the host plays it live.

The phone does the cleanup (noise gate, compressor, normalizer, limiter) and the host approves it there. What arrives here is the finished clip, as a mono
16-bit PCM WAV cut into base64 chunks, one `announce.chunk` command each (a relay frame stays small). Nothing is ever kept on a server: the relay only
forwards, and this box keeps the bytes in RAM. The Rust engine decodes a pad's clip to memory when it loads, so the one short-lived temporary file the
engine needs is deleted the moment it is loaded; the clip then plays from memory and is dropped when it ends.

Commands (all answer with ok + a short message; `_execute_host_control_command` calls `handle`):
  announce.begin  {id, rate, chunks}   start a new clip (drops any clip already held)
  announce.chunk  {id, index, data}    one base64 piece, in order
  announce.end    {id}                 assemble, check the WAV, arm it
  announce.play   {id}                 play the armed clip now, then forget it
  announce.discard {}                  forget the held clip
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import struct
import tempfile
import time
import wave
from collections.abc import Callable

MAX_SECONDS = 30.0
MAX_BYTES = 3_000_000
MAX_CHUNKS = 400
MIN_RATE, MAX_RATE = 8000, 48000
HOLD_SECONDS = 15 * 60          # an armed clip nobody played is dropped after this long
PEAK_LIMIT = 32767 * 0.995       # the phone's limiter keeps below this; a clip that clips anyway is refused


class AnnouncementError(ValueError):
    pass


def check_wav(data: bytes) -> tuple[int, float, int]:
    """Return (sample_rate, seconds, peak) for a mono 16-bit PCM WAV, or raise AnnouncementError."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            pcm = w.readframes(frames)
    except (wave.Error, EOFError, struct.error) as exc:
        raise AnnouncementError("bad_wav") from exc
    if channels != 1 or width != 2:
        raise AnnouncementError("wav_must_be_mono_16bit")
    if not (MIN_RATE <= rate <= MAX_RATE):
        raise AnnouncementError("bad_sample_rate")
    if frames <= 0:
        raise AnnouncementError("empty_clip")
    seconds = frames / float(rate)
    if seconds > MAX_SECONDS:
        raise AnnouncementError("clip_too_long")
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) // 2 * 2])
    peak = max(max(samples), -min(samples))
    if peak >= PEAK_LIMIT:
        raise AnnouncementError("clip_would_distort")
    return rate, seconds, int(peak)


class AnnouncementBox:
    def __init__(self, runtime_provider: Callable[[], object | None], *, volume: Callable[[], float] | None = None,
                 log: Callable[[str], None] = lambda _m: None, clock: Callable[[], float] = time.monotonic,
                 temp_dir: str | None = None):
        self._provider = runtime_provider
        self._volume = volume or (lambda: 1.0)
        self._log = log
        self._clock = clock
        self._temp_dir = temp_dir
        self._building: dict | None = None
        self._armed: dict | None = None
        self._channel = None

    # ---- state ----

    @property
    def armed_id(self) -> str:
        self._expire()
        return str((self._armed or {}).get("id") or "")

    def holds_audio(self) -> bool:
        return self._building is not None or self._armed is not None

    def discard(self) -> None:
        self._building = None
        self._armed = None

    def _expire(self) -> None:
        if self._armed is not None and self._clock() - self._armed["at"] > HOLD_SECONDS:
            self._log("[ANNOUNCE] armed clip expired unplayed; dropped")
            self._armed = None

    # ---- commands ----

    def handle(self, action: str, args: dict) -> tuple[bool, str]:
        try:
            if action == "announce.begin":
                return self._begin(args)
            if action == "announce.chunk":
                return self._chunk(args)
            if action == "announce.end":
                return self._end(args)
            if action == "announce.play":
                return self._play(args)
            if action == "announce.discard":
                self.discard()
                return True, "discarded"
        except AnnouncementError as exc:
            self._building = None
            self._log(f"[ANNOUNCE] {action} refused: {exc}")
            return False, str(exc)
        return False, "unknown_announce_command"

    def _begin(self, args: dict) -> tuple[bool, str]:
        self.discard()
        clip_id = str(args.get("id") or "")[:64]
        try:
            chunks = int(args.get("chunks"))
            rate = int(args.get("rate"))
        except (TypeError, ValueError) as exc:
            raise AnnouncementError("bad_begin") from exc
        if not clip_id or not (1 <= chunks <= MAX_CHUNKS) or not (MIN_RATE <= rate <= MAX_RATE):
            raise AnnouncementError("bad_begin")
        self._building = {"id": clip_id, "chunks": chunks, "next": 0, "parts": [], "bytes": 0}
        return True, "ready"

    def _chunk(self, args: dict) -> tuple[bool, str]:
        b = self._building
        if b is None or str(args.get("id") or "") != b["id"]:
            raise AnnouncementError("no_clip_in_progress")
        try:
            index = int(args.get("index"))
            piece = base64.b64decode(str(args.get("data") or ""), validate=True)
        except (TypeError, ValueError, binascii.Error) as exc:
            raise AnnouncementError("bad_chunk") from exc
        if index != b["next"] or index >= b["chunks"]:
            raise AnnouncementError("chunk_out_of_order")
        b["bytes"] += len(piece)
        if b["bytes"] > MAX_BYTES:
            raise AnnouncementError("clip_too_big")
        b["parts"].append(piece)
        b["next"] += 1
        return True, str(index)

    def _end(self, args: dict) -> tuple[bool, str]:
        b = self._building
        if b is None or str(args.get("id") or "") != b["id"]:
            raise AnnouncementError("no_clip_in_progress")
        if b["next"] != b["chunks"]:
            raise AnnouncementError("clip_incomplete")
        data = b"".join(b["parts"])
        self._building = None
        rate, seconds, peak = check_wav(data)
        self._armed = {"id": b["id"], "wav": data, "at": self._clock(), "seconds": seconds}
        self._log(f"[ANNOUNCE] clip armed id={b['id']} {seconds:.1f}s {rate}Hz peak={peak / 32767:.3f}")
        return True, f"armed {seconds:.1f}s"

    def _play(self, args: dict) -> tuple[bool, str]:
        self._expire()
        a = self._armed
        if a is None or str(args.get("id") or "") != a["id"]:
            raise AnnouncementError("no_clip_armed")
        runtime = self._provider()
        if runtime is None or not hasattr(runtime, "pad_play"):
            raise AnnouncementError("audio_engine_not_running")
        from soundboard_channels import RustSoundboardChannel, SoundboardError
        self._armed = None                      # one play per clip: it is forgotten whether or not it works
        channel = RustSoundboardChannel(self._provider)
        self._close_channel()
        fd, path = tempfile.mkstemp(prefix="announce_", suffix=".wav", dir=self._temp_dir)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(a["wav"])
            try:
                channel.load(path, max(0.0, min(1.0, float(self._volume()))))   # decodes to memory
            finally:
                try:
                    os.unlink(path)             # the file existed only while the engine decoded it
                except OSError:
                    pass
            channel.play(0.0)
        except SoundboardError as exc:
            channel.close()
            raise AnnouncementError(f"could_not_play: {exc}") from exc
        except Exception:
            channel.close()
            raise
        a["wav"] = b""
        self._channel = channel
        self._log(f"[ANNOUNCE] playing id={a['id']} {a['seconds']:.1f}s; the file is already deleted")
        return True, "playing"

    # ---- finishing ----

    def _close_channel(self) -> None:
        ch, self._channel = self._channel, None
        if ch is not None:
            try:
                ch.close()
            except Exception:
                pass

    def tick(self) -> None:
        """Called by a timer: release the engine slot once the clip has finished, and drop an expired armed clip."""
        self._expire()
        ch = self._channel
        if ch is not None and not ch.is_playing():
            self._close_channel()
            self._log("[ANNOUNCE] clip finished; released")

    def close(self) -> None:
        self.discard()
        self._close_channel()


def sweep_stale_files(temp_dir: str | None = None) -> int:
    """Remove announce_*.wav left by a crash while a clip was being decoded (milliseconds). Returns how many were removed."""
    base = temp_dir or tempfile.gettempdir()
    removed = 0
    try:
        for name in os.listdir(base):
            if name.startswith("announce_") and name.endswith(".wav"):
                try:
                    os.unlink(os.path.join(base, name))
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed
