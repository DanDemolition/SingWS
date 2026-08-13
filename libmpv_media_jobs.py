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
import sys
import tempfile
import time
import wave

import numpy as np


MPV_EVENT_NONE = 0
MPV_EVENT_SHUTDOWN = 1
MPV_EVENT_END_FILE = 7


class _MpvEvent(ctypes.Structure):
    _fields_ = [
        ("event_id", ctypes.c_int),
        ("error", ctypes.c_int),
        ("reply_userdata", ctypes.c_uint64),
        ("data", ctypes.c_void_p),
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

    def wait_for_end(self, timeout: float):
        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            event = self.lib.mpv_wait_event(self.handle, min(0.25, deadline - time.monotonic()))
            if not event:
                continue
            if event.contents.event_id == MPV_EVENT_END_FILE:
                if event.contents.error < 0:
                    raise RuntimeError(f"libmpv decode ended with error {event.contents.error}")
                return
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


def measure_loudness_lufs(source: str, *, timeout: float = 120.0):
    """Return BS.1770-style integrated LUFS and sample peak dBFS.

    libmpv performs only the decode/resample.  The weighting and two-stage
    gating are deterministic numpy/scipy work over the temporary PCM WAV.
    """
    from scipy.signal import lfilter

    rendered = decode_audio_wav(
        source, sample_rate=48000, channels=2, timeout=timeout)
    try:
        with wave.open(rendered, "rb") as handle:
            channels = handle.getnchannels()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        if rate != 48000 or channels not in (1, 2) or not raw:
            return None, None
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
        samples = samples.reshape(-1, channels)
        peak = float(np.max(np.abs(samples)))
        peak_db = 20.0 * np.log10(max(peak, 1e-12))

        # ITU-R BS.1770 K weighting at 48 kHz: pre-filter shelf followed by
        # the revised low-frequency RLB high-pass stage.
        weighted = lfilter(
            [1.53512485958697, -2.69169618940638, 1.19839281085285],
            [1.0, -1.69065929318241, 0.73248077421585], samples, axis=0)
        weighted = lfilter(
            [1.0, -2.0, 1.0],
            [1.0, -1.99004745483398, 0.99007225036621], weighted, axis=0)

        block = int(rate * 0.400)
        hop = int(block * 0.25)
        if weighted.shape[0] < block:
            return None, peak_db
        energies = []
        for start in range(0, weighted.shape[0] - block + 1, hop):
            segment = weighted[start:start + block]
            energies.append(float(np.sum(np.mean(segment * segment, axis=0))))
        energies = np.asarray(energies, dtype=np.float64)
        block_lufs = -0.691 + 10.0 * np.log10(np.maximum(energies, 1e-15))
        absolute = energies[block_lufs >= -70.0]
        if absolute.size == 0:
            return None, peak_db
        ungated_lufs = -0.691 + 10.0 * np.log10(float(np.mean(absolute)))
        gated = energies[(block_lufs >= -70.0) & (block_lufs >= ungated_lufs - 10.0)]
        if gated.size == 0:
            return None, peak_db
        integrated = -0.691 + 10.0 * np.log10(float(np.mean(gated)))
        if not (-70.0 <= integrated <= 0.0):
            return None, peak_db
        return float(integrated), float(peak_db)
    finally:
        try:
            os.unlink(rendered)
        except OSError:
            pass
