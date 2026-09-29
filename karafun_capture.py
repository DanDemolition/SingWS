"""Small polling bridge for KaraFun's ScreenCaptureKit Dual Renderer stream."""

import ctypes
import sys
from pathlib import Path


class KaraFunCapture:
    def __init__(self, library_path=None):
        if library_path is None:
            root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            library_path = root / "libsingws_karafun_capture.dylib"
            if not library_path.exists():
                library_path = root / "native" / "karafun_capture" / library_path.name
        self._lib = ctypes.CDLL(str(library_path))
        self._lib.singws_karafun_capture_start.restype = ctypes.c_int32
        self._lib.singws_karafun_capture_status.restype = ctypes.c_int32
        self._lib.singws_karafun_capture_copy_frame.restype = ctypes.c_int32
        self._lib.singws_karafun_capture_copy_frame.argtypes = [
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int32),
            ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_uint64),
        ]
        self._lib.singws_karafun_capture_serial.restype = ctypes.c_uint64
        self._lib.singws_karafun_capture_stats.argtypes = [
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
        ]
        self._buffer = ctypes.create_string_buffer(1280 * 1024 * 4 + 4096)
        self._serial = 0

    def start(self):
        return self._lib.singws_karafun_capture_start() == 1

    def status(self):
        return self._lib.singws_karafun_capture_status()

    def stop(self):
        self._lib.singws_karafun_capture_stop()

    def stats(self):
        """(capture_lag_ms, frame_arrival_seconds_on_the_host_clock) of the newest frame."""
        lag = ctypes.c_double()
        arrival = ctypes.c_double()
        self._lib.singws_karafun_capture_stats(ctypes.byref(lag), ctypes.byref(arrival))
        return lag.value, arrival.value

    def latest_frame(self):
        # Ask for the frame serial first: copying a multi-megabyte frame on every
        # poll when nothing changed was pure waste (and added latency at high poll rates).
        if self._lib.singws_karafun_capture_serial() == self._serial:
            return None
        width = ctypes.c_int32()
        height = ctypes.c_int32()
        stride = ctypes.c_int32()
        serial = ctypes.c_uint64()
        size = self._lib.singws_karafun_capture_copy_frame(
            self._buffer, len(self._buffer), ctypes.byref(width),
            ctypes.byref(height), ctypes.byref(stride), ctypes.byref(serial),
        )
        if size <= 0 or serial.value == self._serial:
            return None
        if width.value <= 0 or height.value <= 0 or stride.value * height.value != size:
            return None
        self._serial = serial.value
        # .raw copies the whole 5MB buffer before slicing; a memoryview slice copies only the frame.
        return bytes(memoryview(self._buffer)[:size]), width.value, height.value, stride.value
