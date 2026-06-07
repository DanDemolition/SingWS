from __future__ import annotations

from array import array
from collections import deque
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from PyQt6.QtCore import QIODevice, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink

import signalsmith_audio_native


NS_PER_SECOND = 1_000_000_000
CDG_WIDTH = 300
CDG_HEIGHT = 216
CDG_PACKET_SIZE = 24
CDG_PACKETS_PER_SECOND = 300
_PERF_LAST_PRINT = {}


def _perf_log_if_slow(name: str, ms: float, threshold_ms: float = 50.0):
    try:
        if float(ms) >= float(threshold_ms):
            now = time.monotonic()
            key = str(name)
            last = float(_PERF_LAST_PRINT.get(key, 0.0) or 0.0)
            if now - last < 1.0:
                return
            _PERF_LAST_PRINT[key] = now
            print(f"[PERF] {name} took {float(ms):.0f}ms")
    except Exception:
        pass


def _read_exact(stream, wanted: int) -> bytes:
    parts = bytearray()
    while len(parts) < wanted:
        chunk = stream.read(wanted - len(parts))
        if not chunk:
            break
        parts.extend(chunk)
    return bytes(parts)


def _ffmpeg_path(binary: str) -> str:
    path = shutil.which(binary)
    if path:
        return path
    for candidate in (
        Path(sys.executable).resolve().parent / binary,
        Path("/opt/homebrew/bin") / binary,
        Path("/usr/local/bin") / binary,
    ):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(f"{binary} is required for Python karaoke playback")


def _pcm_level_db(raw_f32le: bytes) -> float | None:
    if len(raw_f32le) < 4:
        return None
    sample_count = min(len(raw_f32le) // 4, 4096)
    raw = raw_f32le[: sample_count * 4]
    values = array("f")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    if not values:
        return None
    step = max(1, len(values) // 1024)
    energy = 0.0
    count = 0
    for i in range(0, len(values), step):
        value = max(-1.0, min(1.0, float(values[i])))
        energy += value * value
        count += 1
    if count <= 0:
        return None
    return 20.0 * math.log10(max(1e-9, math.sqrt(energy / count)))


def _probe_duration_seconds(path: str) -> float:
    t0 = time.perf_counter()
    command = [
        _ffmpeg_path("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
        duration = float((json.loads(result.stdout or "{}").get("format") or {}).get("duration") or 0.0)
        _perf_log_if_slow("ffprobe_duration", (time.perf_counter() - t0) * 1000.0, 100.0)
        return max(0.0, duration)
    except Exception:
        _perf_log_if_slow("ffprobe_duration", (time.perf_counter() - t0) * 1000.0, 100.0)
        return 0.0


class _AudioDecodeWorker(threading.Thread):
    def __init__(
        self,
        transport: "PythonKaraokeTransport",
        media_path: str,
        start_seconds: float,
        tempo_ratio: float,
        semitones: float,
    ):
        super().__init__(daemon=True)
        self.transport = transport
        self.media_path = str(media_path)
        self.start_seconds = max(0.0, float(start_seconds))
        self.stop_event = threading.Event()
        self.engine_lock = threading.RLock()
        self.process = None
        self.engine = signalsmith_audio_native.StretchEngine(
            self.transport.channels,
            self.transport.sample_rate,
        )
        self.engine.set_modifiers(float(tempo_ratio), float(semitones))

    def stop(self):
        self.stop_event.set()
        process = self.process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

    def set_modifiers(self, tempo_ratio: float, semitones: float):
        with self.engine_lock:
            self.engine.set_modifiers(float(tempo_ratio), float(semitones))

    def run(self):
        t = self.transport
        # Prefer the fully pre-decoded in-memory PCM when it's ready: a seek then
        # becomes an instant slice of a memory buffer with no ffmpeg process to
        # spawn (the spawn is the remaining seek latency, and it's worst on
        # slower CPUs). Falls back to streaming ffmpeg until the preload is done.
        raw = t._raw_pcm if getattr(t, "_raw_pcm_ready", False) else None
        if raw is not None:
            self._run_from_memory(raw)
        else:
            self._run_from_ffmpeg()

    def _run_from_memory(self, raw):
        t = self.transport
        chunk_bytes = t.source_chunk_frames * t.channels * 4
        frame_bytes = t.channels * 4
        pos = int(self.start_seconds * t.sample_rate) * frame_bytes
        pos = max(0, min(pos, len(raw)))
        pos -= pos % frame_bytes  # align to a frame boundary
        try:
            while not self.stop_event.is_set():
                source = raw[pos:pos + chunk_bytes]
                if not source:
                    break
                pos += len(source)
                with self.engine_lock:
                    output = self.engine.process_f32le(source)
                if output:
                    t._queue_pcm(output, self)
                    t._accept_level(output, self)
                if len(source) < chunk_bytes:
                    break
            if not self.stop_event.is_set():
                with self.engine_lock:
                    tail = self.engine.flush_f32le()
                if tail:
                    t._queue_pcm(tail, self)
                    t._accept_level(tail, self)
        except Exception as exc:
            t._mark_decoder_error(str(exc))
        finally:
            t._mark_decoder_done(self)

    def _run_from_ffmpeg(self):
        command_started = time.perf_counter()
        command = [
            _ffmpeg_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            # Start emitting decoded audio as fast as possible: skip the long
            # default stream probe/analyze pass. This shrinks the decoder
            # spin-up after a seek (the brief silence gap) for simple audio
            # streams like the CDG-paired MP3.
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
            "-fflags",
            "nobuffer",
        ]
        if self.start_seconds > 0.0:
            # Input-side seek (before -i) jumps near the target fast instead of
            # decoding from the top.
            command.extend(["-ss", f"{self.start_seconds:.6f}"])
        command.extend(
            [
                "-i",
                self.media_path,
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
            ]
        )
        try:
            gain_db = float(getattr(self.transport, "normalize_gain_db", 0.0) or 0.0)
        except Exception:
            gain_db = 0.0
        if abs(gain_db) > 0.05:
            command.extend(["-af", f"volume={gain_db:.2f}dB"])
        command.extend(
            [
                "-ac",
                str(self.transport.channels),
                "-ar",
                str(self.transport.sample_rate),
                "-f",
                "f32le",
                "pipe:1",
            ]
        )

        chunk_bytes = self.transport.source_chunk_frames * self.transport.channels * 4
        try:
            print(f"[FFMPEG] audio_decode start hwaccel=none path={Path(self.media_path).name}")
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            stream = self.process.stdout
            if stream is None:
                raise RuntimeError("ffmpeg did not expose decoded PCM")

            while not self.stop_event.is_set():
                read_started = time.perf_counter()
                source = _read_exact(stream, chunk_bytes)
                _perf_log_if_slow("audio_decode_read", (time.perf_counter() - read_started) * 1000.0, 5.0)
                if not source:
                    break
                if self.stop_event.is_set():
                    break
                with self.engine_lock:
                    process_started = time.perf_counter()
                    output = self.engine.process_f32le(source)
                _perf_log_if_slow("audio_processing", (time.perf_counter() - process_started) * 1000.0, 5.0)
                if output:
                    self.transport._queue_pcm(output, self)
                    self.transport._accept_level(output, self)
                if len(source) < chunk_bytes:
                    break

            if not self.stop_event.is_set():
                with self.engine_lock:
                    tail = self.engine.flush_f32le()
                if tail:
                    self.transport._queue_pcm(tail, self)
                    self.transport._accept_level(tail, self)
        except Exception as exc:
            self.transport._mark_decoder_error(str(exc))
        finally:
            _perf_log_if_slow("ffmpeg_command", (time.perf_counter() - command_started) * 1000.0, 100.0)
            process = self.process
            if process is not None:
                try:
                    if self.stop_event.is_set():
                        process.kill()
                    process.wait(timeout=0.3)
                except Exception:
                    pass
            self.transport._mark_decoder_done(self)


class CdgDecoder:
    """Small CD+G packet interpreter with seek-by-replay semantics."""

    def __init__(self, path: str):
        with open(path, "rb") as handle:
            self.packets = handle.read()
        self.duration_seconds = len(self.packets) / float(CDG_PACKET_SIZE * CDG_PACKETS_PER_SECOND)
        self._palette = [(0, 0, 0)] * 16
        self._pixels = bytearray(CDG_WIDTH * CDG_HEIGHT)
        self._packet_index = 0
        self._dirty = True
        self._cached_image = None
        self.generation = 0
        self._reset()

    def _reset(self):
        self._palette = [(0, 0, 0)] * 16
        self._pixels = bytearray(CDG_WIDTH * CDG_HEIGHT)
        self._packet_index = 0
        self._dirty = True
        self.generation += 1

    def sectors_remaining(self, seconds: float) -> float:
        packet = max(0, min(len(self.packets) // CDG_PACKET_SIZE, int(seconds * CDG_PACKETS_PER_SECOND)))
        return max(0.0, (len(self.packets) // CDG_PACKET_SIZE - packet) / 4.0)

    def frame_at(self, seconds: float) -> QImage:
        wanted = max(0, min(len(self.packets) // CDG_PACKET_SIZE, int(seconds * CDG_PACKETS_PER_SECOND)))
        if wanted < self._packet_index:
            self._reset()
        while self._packet_index < wanted:
            start = self._packet_index * CDG_PACKET_SIZE
            self._apply_packet(self.packets[start : start + CDG_PACKET_SIZE])
            self._packet_index += 1
        if self._cached_image is None or self._dirty:
            image = QImage(
                bytes(self._pixels),
                CDG_WIDTH,
                CDG_HEIGHT,
                CDG_WIDTH,
                QImage.Format.Format_Indexed8,
            )
            image.setColorTable(
                [
                    (0xFF << 24) | (red << 16) | (green << 8) | blue
                    for red, green, blue in self._palette
                ]
            )
            self._cached_image = image.copy()
            self._dirty = False
        return self._cached_image

    def _apply_packet(self, packet: bytes):
        if len(packet) != CDG_PACKET_SIZE or (packet[0] & 0x3F) != 0x09:
            return
        instruction = packet[1] & 0x3F
        data = packet[4:20]
        if instruction == 1:
            self._memory_preset(data)
        elif instruction == 2:
            self._border_preset(data)
        elif instruction == 6:
            self._tile(data, xor=False)
        elif instruction == 20:
            self._scroll(data, copy_pixels=False)
        elif instruction == 24:
            self._scroll(data, copy_pixels=True)
        elif instruction == 30:
            self._load_colors(data, 0)
        elif instruction == 31:
            self._load_colors(data, 8)
        elif instruction == 38:
            self._tile(data, xor=True)

    def _memory_preset(self, data: bytes):
        if (data[1] & 0x0F) != 0:
            return
        self._pixels[:] = bytes([data[0] & 0x0F]) * len(self._pixels)
        self._dirty = True
        self.generation += 1

    def _border_preset(self, data: bytes):
        color = data[0] & 0x0F
        for y in range(CDG_HEIGHT):
            row = y * CDG_WIDTH
            if y < 12 or y >= CDG_HEIGHT - 12:
                self._pixels[row : row + CDG_WIDTH] = bytes([color]) * CDG_WIDTH
            else:
                self._pixels[row : row + 6] = bytes([color]) * 6
                self._pixels[row + CDG_WIDTH - 6 : row + CDG_WIDTH] = bytes([color]) * 6
        self._dirty = True
        self.generation += 1

    def _tile(self, data: bytes, xor: bool):
        color0 = data[0] & 0x0F
        color1 = data[1] & 0x0F
        row = data[2] & 0x1F
        column = data[3] & 0x3F
        x0 = column * 6
        y0 = row * 12
        if x0 >= CDG_WIDTH or y0 >= CDG_HEIGHT:
            return
        for line in range(12):
            bits = data[4 + line] & 0x3F
            y = y0 + line
            if y >= CDG_HEIGHT:
                continue
            offset = y * CDG_WIDTH + x0
            for x in range(6):
                if x0 + x >= CDG_WIDTH:
                    break
                color = color1 if bits & (1 << (5 - x)) else color0
                if xor:
                    self._pixels[offset + x] ^= color
                else:
                    self._pixels[offset + x] = color
        self._dirty = True
        self.generation += 1

    def _scroll(self, data: bytes, copy_pixels: bool):
        color = data[0] & 0x0F
        h_command = (data[1] & 0x30) >> 4
        v_command = (data[2] & 0x30) >> 4
        if h_command == 0 and v_command == 0:
            return
        source = bytes(self._pixels)
        moved = bytearray(bytes([color]) * len(self._pixels))
        dx = 6 if h_command == 1 else (-6 if h_command == 2 else 0)
        dy = 12 if v_command == 1 else (-12 if v_command == 2 else 0)
        # CDG scroll packets move the whole 300x216 bitmap by 6/12 pixel
        # blocks.  Row-slice copies avoid the old nested per-pixel Python loop,
        # which showed up as a rendering spike on scroll-heavy CDG files.
        for y in range(CDG_HEIGHT):
            sy = y - dy
            if copy_pixels:
                sy %= CDG_HEIGHT
            if sy < 0 or sy >= CDG_HEIGHT:
                continue
            dst_row = y * CDG_WIDTH
            src_row = sy * CDG_WIDTH
            if dx == 0:
                moved[dst_row : dst_row + CDG_WIDTH] = source[src_row : src_row + CDG_WIDTH]
            elif dx > 0:
                width = CDG_WIDTH - dx
                moved[dst_row + dx : dst_row + CDG_WIDTH] = source[src_row : src_row + width]
                if copy_pixels:
                    moved[dst_row : dst_row + dx] = source[src_row + width : src_row + CDG_WIDTH]
            else:
                shift = -dx
                width = CDG_WIDTH - shift
                moved[dst_row : dst_row + width] = source[src_row + shift : src_row + CDG_WIDTH]
                if copy_pixels:
                    moved[dst_row + width : dst_row + CDG_WIDTH] = source[src_row : src_row + shift]
        self._pixels = moved
        self._dirty = True
        self.generation += 1

    def _load_colors(self, data: bytes, first: int):
        palette = list(self._palette)
        for i in range(8):
            packed = ((data[i * 2] & 0x3F) << 6) | (data[i * 2 + 1] & 0x3F)
            red = ((packed >> 8) & 0x0F) * 17
            green = ((packed >> 4) & 0x0F) * 17
            blue = (packed & 0x0F) * 17
            palette[first + i] = (red, green, blue)
        self._palette = palette
        self._dirty = True
        self.generation += 1


class FfmpegVideoReader:
    # Cap decoded frames to this height (downscale only).  Karaoke MP4s are
    # often 1080p/4K; decoding + piping raw RGB at full resolution is the main
    # cause of choppy MP4 playback on Intel Macs.  Scaling in ffmpeg (C) before
    # the frames reach Python cuts pipe throughput and per-frame QImage copy
    # cost by 4-9x for oversized sources, with no visible loss on a TV/projector.
    DEFAULT_MAX_HEIGHT = 720
    # Frames buffered ahead.  90 frames of 1080p RGB is ~560 MB; 24 is plenty
    # for smooth pacing and keeps memory pressure low on constrained machines.
    MAX_BUFFERED_FRAMES = 24

    def __init__(self, path: str, start_seconds: float, max_height: int | None = None):
        self.path = str(path)
        self.start_seconds = max(0.0, float(start_seconds))
        self.lock = threading.Condition()
        self.frames = deque()
        self.stop_event = threading.Event()
        self.process = None
        self.src_width, self.src_height = 0, 0
        # max_height: None -> default cap; <=0 -> native (no downscale).
        if max_height is None:
            self.max_height = self.DEFAULT_MAX_HEIGHT
        else:
            self.max_height = int(max_height)
        self.width, self.height = 0, 0
        self.fps = 30.0
        self.frame_index = 0
        self.latest_image = None
        self.dropped_frames = 0
        self.frames_decoded = 0
        self.frames_delivered = 0
        self._dropped_since_log = 0
        self._last_drop_log_ts = 0.0
        self._last_queue_log_ts = 0.0
        self._started_ts = time.monotonic()
        self._hwaccel_checked = False
        # Whether VideoToolbox decode is usable for this file.  Decided inside
        # the worker thread (the probe spawns ffmpeg and must NOT block the GUI
        # thread that constructs this reader when a song starts).
        self.use_hwaccel = False
        self.thread = threading.Thread(target=self._read_frames, daemon=True)
        self.thread.start()

    @classmethod
    def _compute_output_size(cls, src_w: int, src_h: int, cap_h: int) -> tuple[int, int]:
        w, h = int(src_w), int(src_h)
        if w <= 0 or h <= 0:
            return w, h
        # cap_h <= 0 means native: no downscaling at all.
        if cap_h <= 0 or h <= cap_h:
            return w, h
        # Cap by height; derive width from aspect (16:9 720 -> 1280, 1080 -> 1920).
        scale = cap_h / h
        out_w = max(2, int(round(w * scale)))
        out_h = max(2, int(round(h * scale)))
        # rawvideo / rgb24 is happiest with even dimensions.
        out_w -= out_w % 2
        out_h -= out_h % 2
        return out_w, out_h

    def _video_filter(self) -> str:
        chain = [f"fps={self.fps:.6f}"]
        if (self.width, self.height) != (self.src_width, self.src_height):
            chain.append(f"scale={self.width}:{self.height}:flags=bilinear")
        return ",".join(chain)

    def _hwaccel_supported(self) -> bool:
        if sys.platform != "darwin":
            return False
        # Mirror the real decode pipeline (hwaccel + filters + rgb24) for a
        # couple of frames.  If that succeeds, full playback will too, so the
        # reader can use hwaccel without risk of a mid-stream stall.
        command = [
            _ffmpeg_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-hwaccel",
            "videotoolbox",
            "-i",
            self.path,
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-vf",
            self._video_filter(),
            "-pix_fmt",
            "rgb24",
            "-frames:v",
            "2",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            return result.returncode == 0
        except Exception:
            return False

    def stop(self):
        self.stop_event.set()
        process = self.process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass
        with self.lock:
            self.lock.notify_all()

    def image_at(self, position_seconds: float) -> QImage | None:
        selected = None
        dropped = 0
        with self.lock:
            while self.frames and self.frames[0][0] <= float(position_seconds) + 0.045:
                if selected is not None:
                    dropped += 1
                selected = self.frames.popleft()
            self.lock.notify_all()
        if selected is not None:
            _timestamp, raw = selected
            convert_started = time.perf_counter()
            self.latest_image = QImage(
                raw,
                self.width,
                self.height,
                self.width * 3,
                QImage.Format.Format_RGB888,
            ).copy()
            self.frames_delivered += 1
            _perf_log_if_slow("qimage_convert", (time.perf_counter() - convert_started) * 1000.0, 4.0)
        if dropped:
            self.dropped_frames += dropped
            self._dropped_since_log += dropped
            now = time.monotonic()
            if now - self._last_drop_log_ts >= 1.0:
                print(f"[PERF] dropped_frame count={self._dropped_since_log} reason=behind_schedule queue={self.queue_size()}")
                self._dropped_since_log = 0
                self._last_drop_log_ts = now
        return self.latest_image

    def queue_size(self) -> int:
        try:
            with self.lock:
                return len(self.frames)
        except Exception:
            return 0

    def stats(self) -> dict:
        elapsed = max(0.001, time.monotonic() - float(self._started_ts or time.monotonic()))
        return {
            "decoder": "ffmpeg/rawvideo",
            "hardware_acceleration": "videotoolbox" if self.use_hwaccel else "none",
            "hardware_acceleration_checked": bool(self._hwaccel_checked),
            "source_size": f"{self.src_width}x{self.src_height}" if self.src_width and self.src_height else "",
            "output_size": f"{self.width}x{self.height}" if self.width and self.height else "",
            "fps": float(self.fps),
            "delivered_fps": float(self.frames_delivered) / elapsed,
            "queue_size": self.queue_size(),
            "decoded_frames": int(self.frames_decoded),
            "delivered_frames": int(self.frames_delivered),
            "dropped_frames": int(self.dropped_frames),
            "max_buffered_frames": int(self.MAX_BUFFERED_FRAMES),
        }

    def _probe_dimensions(self) -> tuple[int, int]:
        t0 = time.perf_counter()
        command = [
            _ffmpeg_path("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            self.path,
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=5)
        _perf_log_if_slow("ffprobe_dimensions", (time.perf_counter() - t0) * 1000.0, 100.0)
        streams = json.loads(result.stdout or "{}").get("streams") or []
        if not streams:
            raise RuntimeError("video stream not found")
        width = int(streams[0].get("width") or 0)
        height = int(streams[0].get("height") or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("video stream dimensions are invalid")
        return width, height

    def _read_frames(self):
        command_started = time.perf_counter()
        try:
            self.src_width, self.src_height = self._probe_dimensions()
            self.width, self.height = self._compute_output_size(
                self.src_width, self.src_height, self.max_height
            )
        except Exception as exc:
            print(f"[FFMPEG] video probe failed: {exc}")
            return
        # Decide hwaccel here (on this worker thread) so the probe never blocks
        # the GUI thread that created the reader.
        if not self.stop_event.is_set():
            self.use_hwaccel = self._hwaccel_supported()
            self._hwaccel_checked = True
        command = [
            _ffmpeg_path("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
        ]
        if self.use_hwaccel:
            command.extend(["-hwaccel", "videotoolbox"])
        # -ss before -i (input seek) is fast and accurate enough here; with
        # hwaccel it also seeks on the GPU-decoded stream.
        if self.start_seconds > 0.0:
            command.extend(["-ss", f"{self.start_seconds:.6f}"])
        command.extend(
            [
                "-i",
                self.path,
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                self._video_filter(),
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
        )
        frame_bytes = self.width * self.height * 3
        try:
            print(
                f"[FFMPEG] video_decode start hwaccel={'videotoolbox' if self.use_hwaccel else 'none'} "
                f"size={self.width}x{self.height} path={Path(self.path).name}"
            )
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            stream = self.process.stdout
            if stream is None:
                return
            while not self.stop_event.is_set():
                with self.lock:
                    while len(self.frames) >= self.MAX_BUFFERED_FRAMES and not self.stop_event.is_set():
                        self.lock.wait(timeout=0.1)
                decode_started = time.perf_counter()
                raw = _read_exact(stream, frame_bytes)
                _perf_log_if_slow("frame_decode", (time.perf_counter() - decode_started) * 1000.0, 8.0)
                if len(raw) != frame_bytes:
                    break
                timestamp = self.start_seconds + (self.frame_index / self.fps)
                self.frame_index += 1
                self.frames_decoded += 1
                with self.lock:
                    self.frames.append((timestamp, raw))
                    if len(self.frames) >= self.MAX_BUFFERED_FRAMES and (time.monotonic() - self._last_queue_log_ts) >= 2.0:
                        print(f"[PERF] frame_queue_size count={len(self.frames)}")
                        self._last_queue_log_ts = time.monotonic()
                    self.lock.notify_all()
        finally:
            _perf_log_if_slow("ffmpeg_command", (time.perf_counter() - command_started) * 1000.0, 100.0)
            process = self.process
            if process is not None:
                try:
                    process.kill()
                except Exception:
                    pass


class _PcmFeeder(QIODevice):
    """Pull-mode audio source for QAudioSink.

    Qt's audio backend calls readData() on its own audio thread whenever the
    output device needs more samples, so audio is no longer drained on the Qt
    main thread. That means:
      * a UI hitch can't starve the output (no main-thread dropout), and
      * the output device runs *continuously* across a seek — we just swap the
        buffered PCM underneath it, so there's no stop/restart (no gap) and no
        leftover old-position audio (no overlap).
    On underrun we return silence so the device never stalls; the only audible
    seek artifact left is the brief decoder spin-up, which is tiny.
    """

    def __init__(self, transport):
        super().__init__(transport)
        self._t = transport

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        t = self._t
        try:
            with t._pcm_lock:
                buffered = t._pcm_bytes + len(t._pending_output)
        except Exception:
            buffered = 0
        # Always advertise at least a small floor so the sink keeps pulling
        # through brief underruns (we pad those with silence to stay seamless).
        floor = max(1, (t.sample_rate * t.channels * 4) // 10)
        return max(buffered, floor) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:
        n = int(maxlen)
        if n <= 0:
            return b""
        t = self._t
        out = bytearray()
        real_audio_bytes = 0
        with t._pcm_lock:
            if t._pending_output:
                take = t._pending_output[:n]
                out += take
                real_audio_bytes += len(take)
                t._pending_output = t._pending_output[len(take):]
            while len(out) < n and t._pcm_chunks:
                chunk = t._pcm_chunks.popleft()
                t._pcm_bytes -= len(chunk)
                need = n - len(out)
                if len(chunk) > need:
                    out += chunk[:need]
                    real_audio_bytes += need
                    t._pending_output = chunk[need:]
                else:
                    out += chunk
                    real_audio_bytes += len(chunk)
            if real_audio_bytes > 0:
                t._audible_output_bytes += int(real_audio_bytes)
            t._pcm_lock.notify_all()
        if len(out) < n:
            # Underrun: pad with silence so the output device never stalls.
            t._audio_underrun_count += 1
            t._audio_underrun_bytes += int(n - len(out))
            out += b"\x00" * (n - len(out))
        return bytes(out)

    def writeData(self, data) -> int:
        return 0


class PythonKaraokeTransport(QObject):
    frame_ready = pyqtSignal(object)
    ended = pyqtSignal()

    def __init__(
        self,
        audio_path: str,
        *,
        video_path: str | None = None,
        mode: str = "audio",
        duration_seconds: float = 0.0,
        probe_duration_on_init: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.audio_path = str(audio_path)
        self.video_path = str(video_path or "")
        self.mode = str(mode or "audio").lower()
        self.duration_seconds = max(0.0, float(duration_seconds or 0.0))
        if self.duration_seconds <= 0.0 and bool(probe_duration_on_init):
            self.duration_seconds = _probe_duration_seconds(self.audio_path)
        self.channels = 2
        self.sample_rate = 48000
        self.source_chunk_frames = 4096
        self.tempo_ratio = 1.0
        self.semitones = 0.0
        # Loudness-normalization gain (dB) applied by the decoder's ffmpeg
        # volume filter so every song plays at a consistent level. 0 = unity.
        self.normalize_gain_db = 0.0
        # Cap MP4 decode resolution (downscale only) to keep playback smooth on
        # weaker GPUs (notably Intel Macs).  Owner code may lower/raise this.
        self.max_video_height = 720
        self.last_level_db = None
        self.last_level_ts = 0.0
        self.decoder_error = ""
        self._audio_underrun_count = 0
        self._audio_underrun_bytes = 0
        self._visual_tick_count = 0
        self._visual_emit_count = 0
        self._visual_render_total_ms = 0.0
        self._visual_render_max_ms = 0.0
        self._visual_started_ts = time.monotonic()
        self._last_sync_render_ms = 0.0
        self._pcm_chunks = deque()
        self._pcm_bytes = 0
        self._pcm_lock = threading.Condition()
        self._pending_output = b""
        self._decoder = None
        self._decoder_done = False
        self._ended_sent = False
        self._clock_source_seconds = 0.0
        self._clock_processed_us = 0
        # Count only real karaoke PCM pulled by the output device. Silence
        # padding during decoder underruns is intentionally excluded so CDG/MP4
        # follows audible song content, not UI wall time or startup gaps.
        self._audible_output_bytes = 0
        self._clock_last_position_seconds = 0.0
        # Pause state: when True, position_seconds() returns the frozen
        # _paused_position rather than advancing.
        self._paused = False
        self._paused_position = 0.0
        # Optional 10-band graphic EQ applied to PCM before it reaches the
        # output sink.  Owner code may attach an externally-managed
        # GraphicEQ instance; if absent or flat/disabled, processing is a
        # zero-cost passthrough.
        self.eq = None  # GraphicEQ instance or None
        self._eq_config_signature = None
        # Optional master "mix bus" processing (gate/comp/limiter/EQ) applied to
        # the song PCM right after the EQ. Owner code attaches an externally
        # managed MasterAudioProcessor; absent/disabled = zero-cost passthrough.
        # Performance Mode simply never attaches one, so it is fully bypassed.
        self.master = None  # MasterAudioProcessor instance or None
        self._master_config_signature = None
        self.audio_sink = None
        self.audio_device = None
        self._feeder = None  # pull-mode QIODevice feeding the sink
        # Visual-only calibration offset (seconds). Nudges the CDG/MP4 FRAME
        # timing without touching the audio clock — audio stays master. Positive
        # shows lyrics earlier (frame ahead of audio); negative shows them later.
        self.video_offset_seconds = 0.0
        # Full-song raw PCM held in memory so seeks are an instant buffer offset
        # instead of a fresh ffmpeg spawn. Populated in the background after play
        # starts; until ready, seeks fall back to streaming ffmpeg.
        self._raw_pcm = None
        self._raw_pcm_ready = False
        self._full_decode_thread = None
        self._full_decode_abort = False
        self.cdg = CdgDecoder(self.video_path) if self.mode == "cdg" else None
        self._last_cdg_generation = -1
        if self.cdg is not None:
            # The audience hears the AUDIO track, so the song's true length is the
            # audio length — NOT the CDG graphics stream, which can stop well
            # before the music (e.g. a long instrumental outro with no lyric
            # graphics). Sizing the playback clock to the graphics makes
            # position_seconds() clamp early; the app then thinks the song ended
            # and cuts it / fades in BG while audio is still playing (songs
            # ending ~45s early). Take the longest candidate so the clock never
            # under-runs the audio. This only affects the position clamp and the
            # seek display — the true end is still driven by the audio decoder's
            # `ended` signal, so an over-estimate here is harmless.
            audio_dur = _probe_duration_seconds(self.audio_path) if bool(probe_duration_on_init) else 0.0
            self.duration_seconds = max(
                float(self.duration_seconds or 0.0),
                float(audio_dur or 0.0),
                float(self.cdg.duration_seconds or 0.0),
            )
        self.video_reader = None
        self._sync_last_log_ns = 0
        self._sync_dropped_visual_packets = 0
        self.visual_timer_interval_ms = 15
        self.timer = QTimer(self)
        self.timer.setInterval(self.visual_timer_interval_ms)
        self.timer.timeout.connect(self._tick)

    def set_visual_timer_interval_ms(self, interval_ms: int):
        """Throttle only visual/CDG/MP4 frame polling; audio remains the clock."""
        try:
            interval = max(15, min(80, int(interval_ms)))
        except Exception:
            interval = 15
        self.visual_timer_interval_ms = interval
        self.timer.setInterval(interval)

    def start(self, start_seconds: float = 0.0):
        self.seek(start_seconds)
        # Begin decoding the whole track to memory in the background so seeks
        # become instant once it's ready (within a couple seconds of playback).
        self._start_full_decode()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self._stop_media()

    def seek(self, seconds: float):
        target = max(0.0, float(seconds or 0.0))
        if self.duration_seconds > 0.0:
            target = min(self.duration_seconds, target)
        # Stop ONLY the decoder; the QAudioSink keeps running in pull mode. We
        # just clear the buffered PCM and let the feeder return silence until the
        # new decoder catches up — the device never stops, so there's no restart
        # gap and (since we own the buffer) no leftover old-position overlap.
        # Non-blocking stop so the new decoder isn't delayed by the old one's
        # teardown (keeps the seek snappy, especially on slower CPUs).
        self._stop_decoder(wait=False)
        with self._pcm_lock:
            self._pcm_chunks.clear()
            self._pcm_bytes = 0
            self._pending_output = b""
            self._audible_output_bytes = 0
            self._pcm_lock.notify_all()
        self._decoder_done = False
        self._ended_sent = False
        self._clock_source_seconds = target
        self._clock_processed_us = self._processed_us()
        self._clock_last_position_seconds = target
        self._paused = False
        self._paused_position = 0.0
        if self.eq is not None:
            try:
                self.eq.reset_state()
                self._eq_config_signature = None
            except Exception:
                pass
        if self.master is not None:
            try:
                self.master.reset_state()
                self._master_config_signature = None
            except Exception:
                pass
        self._start_audio(target)
        if self.mode == "mp4" and self.video_path:
            self.video_reader = FfmpegVideoReader(
                self.video_path, target, max_height=self.max_video_height
            )
        if self.cdg is not None:
            _fp = max(0.0, target + float(getattr(self, "video_offset_seconds", 0.0) or 0.0))
            self.frame_ready.emit(self.cdg.frame_at(_fp))
            self._last_cdg_generation = self.cdg.generation

    def set_modifiers(self, tempo_ratio: float, semitones: float):
        position = self.position_seconds()
        self._clock_source_seconds = position
        self._clock_processed_us = self._processed_us()
        with self._pcm_lock:
            self._audible_output_bytes = 0
        self._clock_last_position_seconds = position
        self.tempo_ratio = max(0.5, min(2.0, float(tempo_ratio or 1.0)))
        self.semitones = max(-24.0, min(24.0, float(semitones or 0.0)))
        worker = self._decoder
        if worker is not None:
            worker.set_modifiers(self.tempo_ratio, self.semitones)

    def is_paused(self) -> bool:
        return bool(self._paused)

    def pause(self):
        """Suspend audio output and freeze the playback clock."""
        if self._paused:
            return
        # Snapshot the position before flipping the flag so the next
        # position_seconds() call returns the right frozen value.
        self._paused_position = self.position_seconds()
        self._paused = True
        sink = self.audio_sink
        if sink is not None:
            try:
                sink.suspend()
            except Exception:
                pass

    def resume(self):
        """Resume audio output and continue advancing the playback clock."""
        if not self._paused:
            return
        # Re-anchor the clock to where we paused so position_seconds() picks
        # up right where it left off, no matter how long the pause lasted.
        self._clock_source_seconds = float(self._paused_position)
        self._clock_processed_us = self._processed_us()
        with self._pcm_lock:
            self._audible_output_bytes = 0
        self._clock_last_position_seconds = float(self._paused_position)
        self._paused = False
        sink = self.audio_sink
        if sink is not None:
            try:
                sink.resume()
            except Exception:
                pass

    def set_video_offset_ms(self, ms) -> None:
        """Set the visual-only timing offset in milliseconds (clamped). Positive
        moves lyrics earlier, negative later. Does not touch audio."""
        try:
            ms = max(-3000.0, min(3000.0, float(ms or 0.0)))
        except Exception:
            ms = 0.0
        self.video_offset_seconds = ms / 1000.0

    def position_seconds(self) -> float:
        if self._paused:
            return float(self._paused_position)

        frame_bytes = max(1, int(self.channels) * 4)
        byte_rate = max(1, int(self.sample_rate) * frame_bytes)
        with self._pcm_lock:
            pulled_bytes = int(self._audible_output_bytes)

        audio_delta_s = max(0.0, pulled_bytes / float(byte_rate))
        position = float(self._clock_source_seconds) + audio_delta_s * float(self.tempo_ratio)
        if self.duration_seconds > 0.0:
            position = max(0.0, min(self.duration_seconds, position))
        else:
            position = max(0.0, position)

        # Audio is the master clock, but visual consumers should never move
        # backward due to a short device-buffer accounting wobble.
        if position < float(self._clock_last_position_seconds):
            return float(self._clock_last_position_seconds)
        self._clock_last_position_seconds = position
        return position

    def query_times_ns(self) -> tuple[int | None, int | None]:
        duration = int(self.duration_seconds * NS_PER_SECOND) if self.duration_seconds > 0.0 else None
        return duration, int(self.position_seconds() * NS_PER_SECOND)

    def diagnostics(self) -> dict:
        elapsed = max(0.001, time.monotonic() - float(self._visual_started_ts or time.monotonic()))
        frame_bytes = max(1, int(self.channels) * 4)
        byte_rate = max(1, int(self.sample_rate) * frame_bytes)
        with self._pcm_lock:
            buffered = self._pcm_bytes + len(self._pending_output)
            audible_output_bytes = int(self._audible_output_bytes)
        audio_buffer_ms = float(buffered) / float(byte_rate) * 1000.0
        audible_delta_seconds = max(0.0, float(audible_output_bytes) / float(byte_rate)) * float(self.tempo_ratio)
        audible_position_seconds = float(self.position_seconds())
        video_offset_ms = float(getattr(self, "video_offset_seconds", 0.0) or 0.0) * 1000.0
        display_position_seconds = max(0.0, audible_position_seconds + (video_offset_ms / 1000.0))
        video = self.video_reader.stats() if self.video_reader is not None and hasattr(self.video_reader, "stats") else {}
        return {
            "media_type": self.mode.upper(),
            "position_seconds": audible_position_seconds,
            "audible_position_seconds": audible_position_seconds,
            "display_position_seconds": display_position_seconds,
            "audio_clock_source_seconds": float(self._clock_source_seconds),
            "audio_clock_delta_seconds": audible_delta_seconds,
            "duration_seconds": float(self.duration_seconds or 0.0),
            # This is queued PCM depth, not measured hardware/output latency.
            "audio_buffer_ms": audio_buffer_ms,
            "audio_latency_ms": audio_buffer_ms,
            "audio_buffer_bytes": int(buffered),
            "audio_audible_output_bytes": audible_output_bytes,
            "audio_underruns": int(self._audio_underrun_count),
            "audio_underrun_bytes": int(self._audio_underrun_bytes),
            "video_offset_ms": video_offset_ms,
            "visual_timer_ms": int(self.visual_timer_interval_ms),
            "visual_ticks": int(self._visual_tick_count),
            "visual_emits": int(self._visual_emit_count),
            "visual_emit_fps": float(self._visual_emit_count) / elapsed,
            "visual_render_avg_ms": float(self._visual_render_total_ms) / max(1, int(self._visual_tick_count)),
            "visual_render_max_ms": float(self._visual_render_max_ms),
            "last_visual_render_ms": float(self._last_sync_render_ms),
            "sync_dropped_visual_packets": int(self._sync_dropped_visual_packets),
            "video": video,
            "decoder_error": str(self.decoder_error or ""),
        }

    def cdg_sectors_remaining(self) -> float | None:
        if self.cdg is None:
            return None
        return self.cdg.sectors_remaining(self.position_seconds())

    def cdg_generation(self) -> int | None:
        """Monotonic count of meaningful CDG bitmap changes.

        The UI uses this as a lyric/graphics activity signal for intelligent
        early-end detection.  A stable generation plus sustained silence means
        the CDG is no longer presenting meaningful lyric changes.
        """
        if self.cdg is None:
            return None
        try:
            return int(self.cdg.generation)
        except Exception:
            return None

    def _processed_us(self) -> int:
        sink = self.audio_sink
        if sink is None:
            return 0
        try:
            return int(sink.processedUSecs())
        except Exception:
            return 0

    def _ensure_sink_running(self):
        """Create the QAudioSink + pull-mode feeder once and keep them running
        continuously for the life of the track. In pull mode Qt reads PCM from
        the feeder on its own audio thread, so the device never has to stop and
        restart on a seek — it just keeps playing while we swap the buffered
        audio underneath it. That's what makes a seek gapless (no stop/restart
        latency) and overlap-free (no leftover buffered old audio)."""
        if self.audio_sink is not None:
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(self.sample_rate)
        fmt.setChannelCount(self.channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        self.audio_sink = QAudioSink(fmt, self)
        # Small buffer: pull mode keeps the device fed from a background thread,
        # so we don't need a big cushion against UI hitches, and a small buffer
        # keeps output latency (and any residual seek artifact) tight.
        self.audio_sink.setBufferSize(int(self.sample_rate * self.channels * 4 * 0.2))
        self._feeder = _PcmFeeder(self)
        self._feeder.open(QIODevice.OpenModeFlag.ReadOnly)
        # Pull mode: hand Qt the feeder; it pulls as the device needs data.
        self.audio_sink.start(self._feeder)

    def _start_audio(self, start_seconds: float):
        # Make sure the (continuous) output device is running, then (re)spawn the
        # decoder for the new position. The sink is NOT restarted on a seek.
        self._ensure_sink_running()
        self._decoder = _AudioDecodeWorker(
            self,
            self.audio_path,
            start_seconds,
            self.tempo_ratio,
            self.semitones,
        )
        self._decoder.start()

    def _start_full_decode(self):
        """Decode the whole audio track to raw PCM in memory, in the background,
        so subsequent seeks become an instant in-memory offset (no per-seek
        ffmpeg spawn — the thing that lags seeks, especially on Intel)."""
        if self._full_decode_thread is not None or self._raw_pcm_ready:
            return
        # Bound memory use: skip very long tracks (~byterate * duration).
        try:
            byterate = self.sample_rate * self.channels * 4
            if self.duration_seconds and (self.duration_seconds * byterate) > (450 * 1024 * 1024):
                return
        except Exception:
            pass
        self._full_decode_abort = False
        th = threading.Thread(target=self._full_decode_run, daemon=True)
        self._full_decode_thread = th
        th.start()

    def _full_decode_run(self):
        proc = None
        command_started = time.perf_counter()
        try:
            command = [
                _ffmpeg_path("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-nostdin", "-i", self.audio_path,
                "-map", "0:a:0", "-vn", "-sn", "-dn",
            ]
            try:
                gain_db = float(getattr(self, "normalize_gain_db", 0.0) or 0.0)
            except Exception:
                gain_db = 0.0
            if abs(gain_db) > 0.05:
                # Bake the same loudness gain the streaming path uses so the two
                # sources are sample-identical.
                command.extend(["-af", f"volume={gain_db:.2f}dB"])
            command.extend(["-ac", str(self.channels), "-ar", str(self.sample_rate),
                            "-f", "f32le", "pipe:1"])
            print(f"[FFMPEG] full_audio_decode start hwaccel=none path={Path(self.audio_path).name}")
            proc = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0,
            )
            stream = proc.stdout
            chunks = []
            while True:
                if self._full_decode_abort:
                    return
                data = stream.read(262144)
                if not data:
                    break
                chunks.append(data)
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
            if self._full_decode_abort:
                return
            self._raw_pcm = b"".join(chunks)
            self._raw_pcm_ready = True
        except Exception:
            self._raw_pcm = None
            self._raw_pcm_ready = False
        finally:
            _perf_log_if_slow("ffmpeg_command", (time.perf_counter() - command_started) * 1000.0, 100.0)
            if proc is not None and self._full_decode_abort:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _stop_decoder(self, wait: bool = True):
        """Stop just the decoder worker + video reader, leaving the audio sink
        open (used by seek so the output device stays warm).

        wait=False (the seek path) signals the old worker to stop and terminates
        its ffmpeg, but does NOT block on the thread join — so spinning up the
        new decoder isn't delayed by the old one's teardown (which matters most
        on slower CPUs like Intel). The stale-chunk guard in _queue_pcm makes it
        safe for the dying worker to linger briefly; Python keeps the thread
        alive until run() returns and it reaps its own ffmpeg."""
        worker = self._decoder
        self._decoder = None
        if worker is not None:
            worker.stop()
            if wait:
                worker.join(timeout=0.4)
        reader = self.video_reader
        self.video_reader = None
        if reader is not None:
            try:
                reader.stop()
            except Exception:
                pass

    def _stop_media(self):
        # Full teardown (used on stop()/song end). Destroy the sink + feeder.
        # (seek() leaves these running and just swaps the buffered audio.)
        self._full_decode_abort = True
        sink = self.audio_sink
        feeder = self._feeder
        self.audio_sink = None
        self.audio_device = None
        self._feeder = None
        if sink is not None:
            try:
                sink.stop()
                sink.deleteLater()
            except Exception:
                pass
        if feeder is not None:
            try:
                feeder.close()
                feeder.deleteLater()
            except Exception:
                pass
        self._stop_decoder()
        # Release the in-memory PCM (~100MB) and the preload thread handle.
        self._raw_pcm = None
        self._raw_pcm_ready = False
        self._full_decode_thread = None

    def _queue_pcm(self, data: bytes, worker=None):
        # Drop PCM from a decoder that is no longer current. On a seek the old
        # worker can finish one in-flight chunk AFTER the new decoder has
        # started; queueing that stale (old-position) audio is what caused the
        # brief overlap on seek — and, because the sink then counts those extra
        # samples, the CD+G frame timing drifted out of sync. Ignoring it keeps
        # the buffer (and the playback clock) aligned to the new position.
        if worker is not None and worker is not self._decoder:
            return
        # Run through the EQ before queueing, so seek/teardown and the
        # output sink see filtered audio without needing extra plumbing.
        eq = self.eq
        if eq is not None:
            try:
                signature = (id(eq), int(self.sample_rate), int(self.channels))
                if signature != self._eq_config_signature:
                    # Audio decode worker hot path: configure only when the
                    # stream/EQ instance changes, not for every PCM chunk.
                    eq.configure_stream(self.sample_rate, self.channels)
                    self._eq_config_signature = signature
                data = eq.process_f32_bytes(data)
            except Exception:
                pass
        # Master "mix bus" processing runs after the EQ and after the upstream
        # loudness-normalization gain (already baked in by the decoder), so it
        # polishes the normalized signal rather than fighting it.
        master = self.master
        if master is not None:
            try:
                signature = (id(master), int(self.sample_rate), int(self.channels))
                if signature != self._master_config_signature:
                    master.configure_stream(self.sample_rate, self.channels)
                    self._master_config_signature = signature
                data = master.process_f32_bytes(data)
            except Exception:
                pass
        with self._pcm_lock:
            while self._pcm_bytes > self.sample_rate * self.channels * 4 * 4 and self._decoder is not None:
                self._pcm_lock.wait(timeout=0.05)
            self._pcm_chunks.append(bytes(data))
            self._pcm_bytes += len(data)
            self._pcm_lock.notify_all()

    def _accept_level(self, data: bytes, worker=None):
        if worker is not None and worker is not self._decoder:
            return
        level = _pcm_level_db(data)
        if level is not None:
            self.last_level_db = float(level)
            self.last_level_ts = time.monotonic()

    def _mark_decoder_done(self, worker):
        if worker is self._decoder:
            self._decoder_done = True

    def _mark_decoder_error(self, message: str):
        self.decoder_error = str(message or "audio decoder failed")

    def _tick(self):
        # Audio is pulled by Qt's audio thread (the _PcmFeeder), so the UI tick
        # no longer drains PCM — it only drives the video frame + end detection.
        position = self.position_seconds()
        self._visual_tick_count += 1
        # Visual-only calibration: the frame is rendered at position + offset so
        # the host can nudge lyric/video timing without affecting the audio clock
        # or end detection (both keep using the true `position`).
        frame_pos = max(0.0, position + float(getattr(self, "video_offset_seconds", 0.0) or 0.0))
        if self.cdg is not None:
            # CD+G packets often leave the bitmap unchanged for multiple UI
            # ticks.  Emitting only when the decoder generation changes avoids
            # needless video-window repaints while preserving every visual
            # change the karaoke graphics stream actually makes.
            before_packet = int(getattr(self.cdg, "_packet_index", 0) or 0)
            render_start_ns = time.monotonic_ns()
            image = self.cdg.frame_at(frame_pos)
            render_ms = (time.monotonic_ns() - render_start_ns) / 1_000_000.0
            self._last_sync_render_ms = render_ms
            self._visual_render_total_ms += render_ms
            self._visual_render_max_ms = max(float(self._visual_render_max_ms), render_ms)
            _perf_log_if_slow("frame_decode", render_ms, 8.0)
            after_packet = int(getattr(self.cdg, "_packet_index", before_packet) or before_packet)
            skipped_packets = max(0, after_packet - before_packet - 8)
            if skipped_packets:
                self._sync_dropped_visual_packets += skipped_packets
            self._maybe_log_sync_diag(position, render_ms, skipped_packets)
            if self.cdg.generation != self._last_cdg_generation:
                self._last_cdg_generation = self.cdg.generation
                self._visual_emit_count += 1
                self.frame_ready.emit(image)
        elif self.video_reader is not None:
            render_start_ns = time.monotonic_ns()
            image = self.video_reader.image_at(frame_pos)
            render_ms = (time.monotonic_ns() - render_start_ns) / 1_000_000.0
            self._last_sync_render_ms = render_ms
            self._visual_render_total_ms += render_ms
            self._visual_render_max_ms = max(float(self._visual_render_max_ms), render_ms)
            _perf_log_if_slow("frame_render", render_ms, 12.0)
            self._maybe_log_sync_diag(position, render_ms, 0)
            if image is not None:
                self._visual_emit_count += 1
                self.frame_ready.emit(image)
        if self._decoder_done:
            with self._pcm_lock:
                empty = (not self._pcm_chunks) and (not self._pending_output)
            if empty and not self._ended_sent:
                self._ended_sent = True
                self.ended.emit()

    def _maybe_log_sync_diag(self, position: float, render_ms: float, skipped_packets: int):
        if render_ms < 35.0 and skipped_packets <= 60:
            return
        now_ns = time.monotonic_ns()
        if now_ns - int(self._sync_last_log_ns or 0) < 2 * NS_PER_SECOND:
            return
        self._sync_last_log_ns = now_ns
        try:
            sink_delta_s = max(0, self._processed_us() - int(self._clock_processed_us)) / 1_000_000.0
            with self._pcm_lock:
                buffered = self._pcm_bytes + len(self._pending_output)
            print(
                "[SYNC] audio-master visual catch-up "
                f"mode={self.mode} pos={position:.3f}s render={render_ms:.1f}ms "
                f"skipped_packets={skipped_packets} total_skipped_packets={self._sync_dropped_visual_packets} "
                f"sink_delta={sink_delta_s:.3f}s buffered={buffered}"
            )
        except Exception:
            pass
