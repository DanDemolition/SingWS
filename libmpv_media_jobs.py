"""Offline media inspection/PCM decode using SingWS's bundled libmpv.

Each job owns a short-lived mpv core and never shares state with the live show
transport.  This is the migration path away from the large ffmpeg/ffprobe
executables; callers can request a WAV render and analyze it with numpy.
"""

from __future__ import annotations

import ctypes
import json
import locale
import math
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import time
import wave
import shutil

import numpy as np


MPV_EVENT_NONE = 0
MPV_EVENT_SHUTDOWN = 1
MPV_EVENT_LOG_MESSAGE = 2
MPV_EVENT_END_FILE = 7


class _AnalysisMessageBuffer(list):
    """Keep only filter values consumed by the analysis parsers."""

    _MARKERS = ("I:", "Peak:", "silence_", "lavfi.astats.Overall.RMS_level")

    def append(self, message):
        if any(marker in message for marker in self._MARKERS):
            super().append(message)


class _MpvEvent(ctypes.Structure):
    _fields_ = [
        ("event_id", ctypes.c_int),
        ("error", ctypes.c_int),
        ("reply_userdata", ctypes.c_uint64),
        ("data", ctypes.c_void_p),
    ]


class _MpvLogMessage(ctypes.Structure):
    _fields_ = [
        ("prefix", ctypes.c_char_p),
        ("level", ctypes.c_char_p),
        ("text", ctypes.c_char_p),
        ("log_level", ctypes.c_int),
    ]


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent / "native_dual_view" / "Frameworks"


def _libmpv_path() -> Path:
    candidates = (
        _runtime_root() / "singws_libmpv.2.dylib",
        Path(__file__).resolve().parent / "native_dual_view" / "Frameworks" /
        "singws_libmpv.2.dylib",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("bundled libmpv is unavailable for media analysis")


class OfflineMpvJob:
    def __init__(self):
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.lib = ctypes.CDLL(str(_libmpv_path()), mode=os.RTLD_LOCAL | os.RTLD_LAZY)
        self.lib.mpv_create.restype = ctypes.c_void_p
        self.lib.mpv_set_option_string.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        self.lib.mpv_initialize.argtypes = [ctypes.c_void_p]
        self.lib.mpv_initialize.restype = ctypes.c_int
        self.lib.mpv_command.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_char_p)]
        self.lib.mpv_command.restype = ctypes.c_int
        self.lib.mpv_wait_event.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self.lib.mpv_wait_event.restype = ctypes.POINTER(_MpvEvent)
        self.lib.mpv_request_log_messages.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self.lib.mpv_terminate_destroy.argtypes = [ctypes.c_void_p]
        self.handle = self.lib.mpv_create()
        if not self.handle:
            raise RuntimeError("mpv_create failed")

    def option(self, name: str, value: str):
        result = self.lib.mpv_set_option_string(
            self.handle, os.fsencode(name), os.fsencode(value))
        if result < 0:
            raise RuntimeError(f"libmpv rejected {name}={value}")

    def initialize(self):
        if self.lib.mpv_initialize(self.handle) < 0:
            raise RuntimeError("mpv_initialize failed")

    def command(self, *parts: str):
        argv = (ctypes.c_char_p * (len(parts) + 1))()
        for index, part in enumerate(parts):
            argv[index] = os.fsencode(part)
        if self.lib.mpv_command(self.handle, argv) < 0:
            raise RuntimeError(f"libmpv command failed: {parts[0]}")

    def request_log_messages(self, level: str):
        self.lib.mpv_request_log_messages(self.handle, os.fsencode(level))

    def wait_for_end(
        self, timeout: float, log_messages: list[str] | None = None,
        cancel_check=None,
    ):
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                raise InterruptedError("libmpv offline decode cancelled")
            event = self.lib.mpv_wait_event(self.handle, min(0.25, deadline - time.monotonic()))
            if not event:
                continue
            if event.contents.event_id == MPV_EVENT_END_FILE:
                if event.contents.error < 0:
                    raise RuntimeError(f"libmpv decode ended with error {event.contents.error}")
                return
            if event.contents.event_id == MPV_EVENT_LOG_MESSAGE and log_messages is not None:
                message = ctypes.cast(
                    event.contents.data, ctypes.POINTER(_MpvLogMessage)
                ).contents
                if message.text:
                    log_messages.append(message.text.decode("utf-8", "replace"))
            if event.contents.event_id == MPV_EVENT_SHUTDOWN:
                raise RuntimeError("libmpv shut down before decode completed")
        raise TimeoutError("libmpv offline decode timed out")

    def close(self):
        handle, self.handle = self.handle, None
        if handle:
            self.lib.mpv_terminate_destroy(handle)


def decode_audio_wav(
    source: str,
    *,
    sample_rate: int = 16000,
    channels: int = 2,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
    timeout: float = 90.0,
) -> str:
    """Decode audio quickly to a temporary signed-16-bit PCM WAV file.

    The caller owns the returned file and must unlink it after analysis.
    """
    fd, output = tempfile.mkstemp(prefix="singws-mpv-audio-", suffix=".wav")
    os.close(fd)
    # mkstemp safely reserves a unique name, but mpv's ao=pcm driver refuses
    # to overwrite an existing file. Leaving the zero-byte placeholder here
    # made every loudness job finish with "libmpv produced no PCM audio".
    # Remove only this exact reserved file so the PCM driver can create it.
    os.unlink(output)
    job = OfflineMpvJob()
    try:
        job.option("config", "no")
        job.option("vid", "no")
        job.option("ao", "pcm")
        job.option("ao-pcm-file", output)
        job.option("ao-pcm-waveheader", "yes")
        job.option("audio-format", "s16")
        job.option("audio-samplerate", str(max(1000, int(sample_rate))))
        job.option("audio-channels", "mono" if int(channels) == 1 else "stereo")
        job.option("untimed", "yes")
        if start_seconds is not None:
            job.option("start", f"{max(0.0, float(start_seconds)):.6f}")
        if duration_seconds is not None:
            job.option("length", f"{max(0.01, float(duration_seconds)):.6f}")
        job.initialize()
        job.command("loadfile", str(source), "replace")
        job.wait_for_end(timeout)
        if not Path(output).is_file() or Path(output).stat().st_size <= 44:
            raise RuntimeError("libmpv produced no PCM audio")
        return output
    except Exception:
        try:
            os.unlink(output)
        except OSError:
            pass
        raise
    finally:
        job.close()


def sample_video_tail_metrics(
    source: str,
    *,
    duration_seconds: float,
    tail_seconds: float = 45.0,
    frames_per_second: float = 1.0,
    width: int = 32,
    height: int = 18,
    timeout: float = 60.0,
    cancel_check=None,
) -> list[dict[str, float]]:
    """Decode tiny grayscale tail thumbnails and return activity metrics.

    The bundled libmpv image output owns decoding; Python reads only bounded
    32x18 PNGs. This is an offline scan, never a live render path.
    """
    from PIL import Image

    duration = max(0.0, float(duration_seconds))
    if duration <= 0.0:
        return []
    tail = max(1.0, min(duration, float(tail_seconds)))
    start = max(0.0, duration - tail)
    fps = max(0.2, min(4.0, float(frames_per_second)))
    width = max(8, min(160, int(width)))
    height = max(8, min(90, int(height)))
    output_dir = Path(tempfile.mkdtemp(prefix="singws-mpv-video-tail-"))
    job = OfflineMpvJob()
    try:
        job.option("config", "no")
        job.option("ao", "null")
        # This is a video-only metric pass. Leaving the media's audio enabled
        # lets the null audio output pace mpv close to realtime, which made a
        # 45-second tail take about 43 seconds even with untimed=yes.
        job.option("audio", "no")
        job.option("vo", "image")
        job.option("vo-image-outdir", str(output_dir))
        job.option("vo-image-format", "png")
        job.option("start", f"{start:.6f}")
        job.option("length", f"{tail:.6f}")
        job.option("untimed", "yes")
        job.option("vf", f"fps={fps:.6f},scale={width}:{height},format=gray")
        job.initialize()
        job.command("loadfile", str(source), "replace")
        job.wait_for_end(timeout, cancel_check=cancel_check)

        paths = sorted(output_dir.glob("*.png"))
        samples = []
        previous = None
        for index, path in enumerate(paths):
            if cancel_check is not None and cancel_check():
                raise InterruptedError("video tail analysis cancelled")
            with Image.open(path) as image:
                pixels = np.asarray(image.convert("L"), dtype=np.float32) / 255.0
            difference = 1.0 if previous is None else float(np.mean(np.abs(pixels - previous)))
            samples.append({
                "timestamp": min(duration, start + index / fps),
                "mean_luma": float(np.mean(pixels)),
                "difference": difference,
            })
            previous = pixels
        return samples
    finally:
        job.close()
        shutil.rmtree(output_dir, ignore_errors=True)


def _measure_wav_lufs(rendered: str):
    """Measure a 48 kHz PCM WAV with bounded memory.

    Library scans can process many thousands of full-length songs.  Reading a
    whole WAV and making several float64/filter copies leaves very large NumPy
    arenas resident after every song.  Stream the K weighting instead and keep
    only the overlap needed for the 400 ms / 100 ms BS.1770 blocks.
    """
    from scipy.signal import lfilter

    with wave.open(rendered, "rb") as handle:
        channels = handle.getnchannels()
        rate = handle.getframerate()
        if rate != 48000 or channels not in (1, 2) or handle.getsampwidth() != 2:
            return None, None

        block = int(rate * 0.400)
        hop = int(block * 0.25)
        shelf_b = [1.53512485958697, -2.69169618940638, 1.19839281085285]
        shelf_a = [1.0, -1.69065929318241, 0.73248077421585]
        rlb_b = [1.0, -2.0, 1.0]
        rlb_a = [1.0, -1.99004745483398, 0.99007225036621]
        shelf_state = np.zeros((2, channels), dtype=np.float64)
        rlb_state = np.zeros((2, channels), dtype=np.float64)
        pending = np.empty((0, channels), dtype=np.float64)
        energies = []
        peak = 0.0

        while True:
            raw = handle.readframes(65536)
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<i2").reshape(-1, channels)
            peak = max(peak, float(np.max(np.abs(samples.astype(np.int32)))))
            floating = samples.astype(np.float64) / 32768.0
            weighted, shelf_state = lfilter(
                shelf_b, shelf_a, floating, axis=0, zi=shelf_state)
            weighted, rlb_state = lfilter(
                rlb_b, rlb_a, weighted, axis=0, zi=rlb_state)
            pending = np.concatenate((pending, weighted), axis=0)
            while pending.shape[0] >= block:
                segment = pending[:block]
                energies.append(float(np.sum(np.mean(segment * segment, axis=0))))
                pending = pending[hop:]

    peak_db = 20.0 * np.log10(max(peak / 32768.0, 1e-12))
    if not energies:
        return None, float(peak_db)
    energies = np.asarray(energies, dtype=np.float64)
    block_lufs = -0.691 + 10.0 * np.log10(np.maximum(energies, 1e-15))
    absolute = energies[block_lufs >= -70.0]
    if absolute.size == 0:
        return None, float(peak_db)
    ungated_lufs = -0.691 + 10.0 * np.log10(float(np.mean(absolute)))
    gated = energies[(block_lufs >= -70.0) & (block_lufs >= ungated_lufs - 10.0)]
    if gated.size == 0:
        return None, float(peak_db)
    integrated = -0.691 + 10.0 * np.log10(float(np.mean(gated)))
    if not (-70.0 <= integrated <= 0.0):
        return None, float(peak_db)
    return float(integrated), float(peak_db)


def _configure_ebur128_job(job: "OfflineMpvJob", *, include_envelope: bool = False):
    """Apply the shared pre-initialize options for an ebur128 measurement."""
    job.option("config", "no")
    job.option("vid", "no")
    job.option("ao", "null")
    job.option("ao-null-untimed", "yes")
    job.option("untimed", "yes")
    graph = "ebur128=peak=sample:framelog=quiet"
    if include_envelope:
        # Continue the same decoded stream through fixed 100 ms RMS windows.
        # ametadata prints one compact scalar per window; no PCM/waveform is
        # retained and loudness + transition boundaries share one decode pass.
        graph += (
            ",asetnsamples=n=4800:p=1,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level"
        )
    job.option("af", f"lavfi=[{graph}]")


def _configure_karaoke_transition_job(job: "OfflineMpvJob"):
    """Measure loudness and confirmed silent edges without a dense envelope.

    Karaoke playback consumes only the derived first/last audible timestamps.
    The old 100 ms astats path printed and transferred thousands of values per
    song, then persisted them even though playback never read them again.
    """
    job.option("config", "no")
    job.option("vid", "no")
    job.option("ao", "null")
    job.option("ao-null-untimed", "yes")
    job.option("untimed", "yes")
    job.option(
        "af",
        "lavfi=[ebur128=peak=sample:framelog=quiet,"
        # A short synthetic tail guarantees a final silence event even when
        # the source ends on audible content. ebur128 runs before the pad, so
        # the added silence cannot change the integrated loudness result.
        "apad=pad_dur=0.5,silencedetect=n=-55dB:d=0.3]",
    )


def _parse_ebur128(messages: list[str]):
    """Extract (integrated LUFS, sample peak dBFS) from mpv's verbose log."""
    output = "\n".join(messages)
    integrated = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?)\s+LUFS", output)
    peaks = re.findall(r"\bPeak:\s*(-?\d+(?:\.\d+)?)\s+dBFS", output)
    if not integrated:
        raise RuntimeError("libmpv ebur128 produced no integrated loudness")
    lufs = float(integrated[-1])
    peak_db = float(peaks[-1]) if peaks else None
    if not (-70.0 <= lufs <= 0.0):
        return None, peak_db
    return lufs, peak_db


def _parse_transition_envelope(messages: list[str]) -> list[float]:
    values = re.findall(
        r"lavfi\.astats\.Overall\.RMS_level=(-?(?:\d+(?:\.\d+)?|inf))",
        "\n".join(messages), flags=re.IGNORECASE,
    )
    envelope = []
    for raw in values:
        try:
            value = float(raw)
        except ValueError:
            value = -96.0
        if not math.isfinite(value):
            value = -96.0
        envelope.append(max(-96.0, min(6.0, value)))
    return envelope


def _parse_karaoke_boundaries(messages: list[str]) -> tuple[float, float | None, float | None]:
    """Return duration and conservative non-silent edges from lavfi logs."""
    output = "\n".join(messages)
    events = []
    for match in re.finditer(
        r"silence_(start|end):\s*(\d+(?:\.\d+)?)", output, re.IGNORECASE
    ):
        events.append((match.group(1).lower(), float(match.group(2))))
    silence_ends = [value for kind, value in events if kind == "end"]
    if not silence_ends:
        raise RuntimeError("libmpv transition analysis produced no duration")
    duration = max(0.01, silence_ends[-1] - 0.5)

    audio_start = 0.0
    if events and events[0][0] == "start" and events[0][1] <= 0.11:
        leading_end = next((value for kind, value in events[1:] if kind == "end"), None)
        if leading_end is None or leading_end >= duration - 0.15:
            return duration, None, None
        audio_start = leading_end

    audio_end = duration
    last_start_index = next(
        (index for index in range(len(events) - 1, -1, -1) if events[index][0] == "start"),
        None,
    )
    if last_start_index is not None:
        trailing_start = events[last_start_index][1]
        later_ends = [
            value for kind, value in events[last_start_index + 1:] if kind == "end"
        ]
        if not later_ends or later_ends[-1] >= duration - 0.15:
            audio_end = min(duration, trailing_start)
    if audio_end < audio_start:
        return duration, None, None
    return duration, audio_start, audio_end


def _measure_loudness_lavfi(
    source: str,
    *,
    timeout: float = 120.0,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
):
    """Measure directly in libavfilter without creating an intermediate WAV."""
    job = OfflineMpvJob()
    messages: list[str] = _AnalysisMessageBuffer()
    try:
        _configure_ebur128_job(job)
        if start_seconds is not None:
            job.option("start", f"{max(0.0, float(start_seconds)):.6f}")
        if duration_seconds is not None:
            job.option("length", f"{max(0.01, float(duration_seconds)):.6f}")
        job.initialize()
        # libavfilter's informational output is exposed at mpv's verbose level.
        job.request_log_messages("v")
        job.command("loadfile", str(source), "replace")
        job.wait_for_end(timeout, messages)
    finally:
        job.close()

    return _parse_ebur128(messages)


class LoudnessSession:
    """Measure many files through ONE reused mpv core.

    Creating a core per track leaks memory that mpv_terminate_destroy does not
    return: measured at ~1.5 MB per track, linear and with no plateau, which
    grew the app past 8 GB during a five-hour library scan on 2026-08-16.
    Reusing a single core plateaus instead (~0.07 MB per track after warm-up),
    and the ebur128 filter and decoder reset on every loadfile, so the measured
    values are identical either way.

    Not thread-safe: one session belongs to one scan pass on one thread.
    """

    # A single bad file should not permanently disable the fast path, but a
    # libmpv build without ebur128 fails every time.  Give up after this many
    # consecutive failures and let callers fall back to the WAV analyzer.
    _MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self):
        self._job = None
        self._failures = 0
        self._disabled = False

    @property
    def usable(self) -> bool:
        return not self._disabled

    def _core(self, *, include_envelope: bool = False) -> "OfflineMpvJob":
        if self._job is None:
            job = OfflineMpvJob()
            _configure_ebur128_job(job, include_envelope=include_envelope)
            job.initialize()
            job.request_log_messages("v")
            self._job = job
        return self._job

    def measure(self, source: str, *, timeout: float = 120.0):
        """Measure one file. Raises on failure, exactly like the plain path."""
        if self._disabled:
            raise RuntimeError("loudness session disabled after repeated failures")
        messages: list[str] = _AnalysisMessageBuffer()
        try:
            job = self._core()
            job.command("loadfile", str(source), "replace")
            job.wait_for_end(timeout, messages)
            result = _parse_ebur128(messages)
        except Exception:
            # A failed load can leave the core in an unusable state, so drop it
            # rather than letting the next track inherit the fault.
            self.close()
            self._failures += 1
            if self._failures >= self._MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
            raise
        self._failures = 0
        return result

    def measure_fast(self, source: str, *, timeout: float = 120.0):
        return self.measure(_fast_loudness_timeline(source), timeout=timeout)

    def measure_transition(self, source: str, *, timeout: float = 120.0):
        """Return LUFS, peak, and 100 ms RMS envelope from one decode pass."""
        # A core's filter graph is immutable after initialization. Full library
        # scans consistently use this method; close a prior plain core if a
        # caller changes modes on the same session.
        if self._job is not None and not bool(getattr(self, "_envelope_core", False)):
            self.close()
        self._envelope_core = True
        messages: list[str] = _AnalysisMessageBuffer()
        try:
            job = self._core(include_envelope=True)
            job.command("loadfile", str(source), "replace")
            job.wait_for_end(timeout, messages)
            lufs, peak = _parse_ebur128(messages)
            envelope = _parse_transition_envelope(messages)
            if not envelope:
                raise RuntimeError("libmpv transition analysis produced no RMS envelope")
            self._failures = 0
            return lufs, peak, envelope
        except Exception:
            self.close()
            self._failures += 1
            if self._failures >= self._MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
            raise

    def measure_karaoke_transition(self, source: str, *, timeout: float = 120.0):
        """Return LUFS, peak, duration and edges without a dense RMS envelope."""
        if self._job is not None and not bool(getattr(self, "_karaoke_transition_core", False)):
            self.close()
        self._karaoke_transition_core = True
        messages: list[str] = _AnalysisMessageBuffer()
        try:
            if self._job is None:
                job = OfflineMpvJob()
                _configure_karaoke_transition_job(job)
                job.initialize()
                job.request_log_messages("v")
                self._job = job
            job = self._job
            job.command("loadfile", str(source), "replace")
            job.wait_for_end(timeout, messages)
            lufs, peak = _parse_ebur128(messages)
            duration, audio_start, audio_end = _parse_karaoke_boundaries(messages)
            self._failures = 0
            return lufs, peak, duration, audio_start, audio_end
        except Exception:
            self.close()
            self._failures += 1
            if self._failures >= self._MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
            raise

    def close(self):
        job, self._job = self._job, None
        if job is not None:
            try:
                job.close()
            except Exception:
                pass
        self._envelope_core = False
        self._karaoke_transition_core = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


_ANALYSIS_RESULT_PREFIX = "SINGWS_ANALYSIS_RESULT "


class AnalysisHelperError(RuntimeError):
    """The analysis service failed; this is not a permanent media failure."""


class AnalysisTrackError(RuntimeError):
    """A responsive helper could not measure this track; leave it retryable."""


def run_isolated_analysis_worker(input_stream=None, output_stream=None) -> int:
    """Serve bounded offline-analysis requests for the parent SingWS process."""
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else sys.stdout
    session = LoudnessSession()
    try:
        for raw in input_stream:
            try:
                request = json.loads(str(raw or ""))
            except (TypeError, ValueError):
                continue
            if request.get("command") == "quit":
                return 0
            response = {"ok": False, "error": "invalid analysis request"}
            try:
                source = str(request.get("source") or "")
                timeout = float(request.get("timeout") or 120.0)
                mode = str(request.get("mode") or "full")
                if mode == "video_tail":
                    # Do not retain the audio filter core alongside the video
                    # decoder. The helper is recyclable specifically so these
                    # native allocations never accumulate in the live app.
                    session.close()
                    samples = sample_video_tail_metrics(
                        source,
                        duration_seconds=float(request.get("duration") or 0.0),
                        timeout=timeout,
                    )
                    response = {"ok": True, "samples": samples}
                elif mode == "transition":
                    lufs, peak, envelope = session.measure_transition(source, timeout=timeout)
                    response = {"ok": True, "lufs": lufs, "peak": peak, "envelope": envelope}
                elif mode == "karaoke_transition":
                    lufs, peak, duration, audio_start, audio_end = session.measure_karaoke_transition(
                        source, timeout=timeout
                    )
                    response = {
                        "ok": True, "lufs": lufs, "peak": peak, "duration": duration,
                        "audio_start": audio_start, "audio_end": audio_end,
                    }
                elif mode == "fast":
                    lufs, peak = session.measure_fast(source, timeout=timeout)
                    response = {"ok": True, "lufs": lufs, "peak": peak}
                else:
                    lufs, peak = session.measure(source, timeout=timeout)
                    response = {"ok": True, "lufs": lufs, "peak": peak}
            except Exception as exc:
                response = {"ok": False, "error": str(exc)[:500]}
                # The request completed with an analysis error, rather than
                # losing the helper/pipe. Recreate the native session so a bad
                # file cannot disable analysis of the tracks following it.
                session.close()
                session = LoudnessSession()
            output_stream.write(_ANALYSIS_RESULT_PREFIX + json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()
    finally:
        session.close()
    return 0


class IsolatedLoudnessSession:
    """Proxy batch analysis through a recyclable helper process.

    The combined RMS-envelope filter leaked native memory in the main process
    during the 2026-08-27 show. A helper is recycled after a bounded number of
    tracks, so all decoder/filter allocations are returned to macOS at process
    exit without disturbing live playback.
    """

    _MAX_TRACKS_PER_HELPER = 100
    _MAX_CONSECUTIVE_FAILURES = 3
    isolated = True

    def __init__(self, command=None):
        self._command = list(command) if command else None
        self._process = None
        self._tracks = 0
        self._failures = 0
        self._disabled = False
        self._scratch = None

    @property
    def usable(self) -> bool:
        return not self._disabled

    def _worker_command(self) -> list[str]:
        if self._command:
            return list(self._command)
        if getattr(sys, "frozen", False):
            return [sys.executable, "--singws-offline-analysis-worker"]
        entrypoint = Path(__file__).resolve().parent / "0.2.18.1.py"
        return [sys.executable, str(entrypoint), "--singws-offline-analysis-worker"]

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        self._scratch = tempfile.mkdtemp(prefix="singws-analysis-worker-")
        environment = os.environ.copy()
        environment["SINGWS_HOME"] = self._scratch
        environment["PYTHONUNBUFFERED"] = "1"
        self._process = subprocess.Popen(
            self._worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            env=environment,
        )
        self._tracks = 0

    def _request(
        self, source: str, mode: str, timeout: float, *,
        extra: dict | None = None, cancel_check=None,
    ):
        if self._disabled:
            raise AnalysisHelperError("isolated loudness session disabled after repeated failures")
        if self._tracks >= self._MAX_TRACKS_PER_HELPER:
            self.close()
        try:
            self._start()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise RuntimeError("offline analysis helper did not start")
            request = {"source": str(source), "mode": str(mode), "timeout": float(timeout)}
            if extra:
                request.update(extra)
            process.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
            process.stdin.flush()
            deadline = time.monotonic() + max(1.0, float(timeout)) + 5.0
            pending = b""
            while True:
                if cancel_check is not None and cancel_check():
                    raise InterruptedError("offline analysis helper request cancelled")
                if time.monotonic() >= deadline:
                    raise TimeoutError("offline analysis helper response timed out")
                readable, _, _ = select.select([process.stdout], [], [], 0.25)
                if not readable:
                    continue
                # Do not combine select with TextIOWrapper.readline: it can
                # read the result ahead while returning a startup log line,
                # leaving select waiting on an empty OS pipe forever.
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("offline analysis helper exited without a result")
                pending += chunk
                response = None
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    if line.startswith(_ANALYSIS_RESULT_PREFIX.encode("ascii")):
                        response = json.loads(line[len(_ANALYSIS_RESULT_PREFIX):])
                        break
                if response is not None:
                    break
            self._tracks += 1
            if not bool(response.get("ok")):
                raise AnalysisTrackError(str(response.get("error") or "offline analysis failed"))
            self._failures = 0
            if mode == "transition":
                return response.get("lufs"), response.get("peak"), list(response.get("envelope") or [])
            if mode == "karaoke_transition":
                return (
                    response.get("lufs"), response.get("peak"), response.get("duration"),
                    response.get("audio_start"), response.get("audio_end"),
                )
            if mode == "video_tail":
                return list(response.get("samples") or [])
            return response.get("lufs"), response.get("peak")
        except AnalysisTrackError:
            self.close()
            self._failures = 0
            raise
        except Exception as exc:
            self.close()
            self._failures += 1
            if self._failures >= self._MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
            raise AnalysisHelperError(str(exc)) from exc

    def measure(self, source: str, *, timeout: float = 120.0):
        return self._request(source, "full", timeout)

    def measure_fast(self, source: str, *, timeout: float = 120.0):
        return self._request(source, "fast", timeout)

    def measure_transition(self, source: str, *, timeout: float = 120.0):
        return self._request(source, "transition", timeout)

    def measure_karaoke_transition(self, source: str, *, timeout: float = 120.0):
        response = self._request(source, "karaoke_transition", timeout)
        return response

    def measure_video_tail(
        self, source: str, *, duration_seconds: float,
        timeout: float = 60.0, cancel_check=None,
    ):
        return self._request(
            source, "video_tail", timeout,
            extra={"duration": float(duration_seconds)},
            cancel_check=cancel_check,
        )

    def close(self):
        process, self._process = self._process, None
        if process is not None:
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write(b'{"command":"quit"}\n')
                    process.stdin.flush()
                process.wait(timeout=3.0)
            except Exception:
                try:
                    process.terminate()
                    process.wait(timeout=2.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            finally:
                for stream in (process.stdin, process.stdout):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
        scratch, self._scratch = self._scratch, None
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        self._tracks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def measure_loudness_lufs(
    source: str,
    *,
    timeout: float = 120.0,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
):
    """Return BS.1770 integrated LUFS and sample peak dBFS.

    Prefer libavfilter's native ebur128 implementation so the decoded audio
    stays in memory.  Retain the bounded-memory WAV analyzer for compatibility
    with older bundled libmpv builds.
    """
    native_error = None
    try:
        return _measure_loudness_lavfi(
            source,
            timeout=timeout,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
        )
    except Exception as exc:
        # Older libmpv/libavfilter builds may not expose ebur128. Keep the
        # bounded-memory PCM implementation as a compatibility fallback.
        native_error = exc
    try:
        rendered = decode_audio_wav(
            source,
            sample_rate=48000,
            channels=2,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            timeout=timeout,
        )
    except Exception as fallback_error:
        raise RuntimeError(
            "no decodable audio; "
            f"native measurement failed: {native_error}; "
            f"PCM fallback failed: {fallback_error}"
        ) from fallback_error
    try:
        return _measure_wav_lufs(rendered)
    finally:
        try:
            os.unlink(rendered)
        except OSError:
            pass


def _fast_loudness_timeline(source: str) -> str:
    """Build the five-section EDL used by fast loudness estimation.

    mpv's EDL joins the sections into one in-memory timeline, so ebur128 sees a
    representative 60-second program without starting five decoder cores.
    Sections beyond the end of a short track are harmlessly truncated.
    """
    source = str(source)
    escaped = f"%{len(os.fsencode(source))}%{source}"
    return "edl://" + ";".join(
        f"{escaped},start={start},length=12"
        for start in (0, 45, 90, 135, 180)
    )


def measure_loudness_fast_lufs(source: str, *, timeout: float = 120.0):
    """Estimate loudness from five short sections spread across a typical song."""
    return measure_loudness_lufs(_fast_loudness_timeline(source), timeout=timeout)
