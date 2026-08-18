"""Offline media inspection/PCM decode using SingWS's bundled libmpv.

Each job owns a short-lived mpv core and never shares state with the live show
transport.  This is the migration path away from the large ffmpeg/ffprobe
executables; callers can request a WAV render and analyze it with numpy.
"""

from __future__ import annotations

import ctypes
import locale
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import wave

import numpy as np


MPV_EVENT_NONE = 0
MPV_EVENT_SHUTDOWN = 1
MPV_EVENT_LOG_MESSAGE = 2
MPV_EVENT_END_FILE = 7


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

    def wait_for_end(self, timeout: float, log_messages: list[str] | None = None):
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
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


def _configure_ebur128_job(job: "OfflineMpvJob"):
    """Apply the shared pre-initialize options for an ebur128 measurement."""
    job.option("config", "no")
    job.option("vid", "no")
    job.option("ao", "null")
    job.option("ao-null-untimed", "yes")
    job.option("untimed", "yes")
    job.option("af", "lavfi=[ebur128=peak=sample:framelog=verbose]")


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


def _measure_loudness_lavfi(
    source: str,
    *,
    timeout: float = 120.0,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
):
    """Measure directly in libavfilter without creating an intermediate WAV."""
    job = OfflineMpvJob()
    messages: list[str] = []
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

    def _core(self) -> "OfflineMpvJob":
        if self._job is None:
            job = OfflineMpvJob()
            _configure_ebur128_job(job)
            job.initialize()
            job.request_log_messages("v")
            self._job = job
        return self._job

    def measure(self, source: str, *, timeout: float = 120.0):
        """Measure one file. Raises on failure, exactly like the plain path."""
        if self._disabled:
            raise RuntimeError("loudness session disabled after repeated failures")
        messages: list[str] = []
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

    def close(self):
        job, self._job = self._job, None
        if job is not None:
            try:
                job.close()
            except Exception:
                pass

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
