"""Keep macOS from napping/sleeping SingWS during a karaoke session.

The tester's logs showed the app "freezing" for long stretches while idle
between songs (2026-07-18). Those were not hangs — macOS App Nap was throttling
the idle app and the Mac was letting the display/system idle-sleep, so the UI
heartbeat stopped and the freeze detector reported the gap. This holds an
``NSProcessInfo`` activity assertion so, while a session is active, macOS does
not nap the app or idle-sleep the machine. It is a no-op off macOS or if
PyObjC is unavailable, and never raises.

Note: no software assertion can override a closed lid or a manual sleep — this
only prevents *idle* nap/sleep while SingWS is running.
"""

from __future__ import annotations

import ctypes
import sys

# Opt-in (setting ``mac_shim_keep_awake``, default off): the assertion is taken by the Rust platform shim (``singws-mac``, called through ``singws_macshim_keep_awake`` in
# libsingws_dsp_ffi) instead of PyObjC. It falls back to the PyObjC path below whenever the Rust one cannot start, so the assertion is never lost to the switch.
_prefer_rust = False
_rust_lib = None
_rust_loaded = False


def prefer_rust(enabled: bool) -> None:
    global _prefer_rust
    _prefer_rust = bool(enabled)


def _rust():
    """The loaded libsingws_dsp_ffi with the keep-awake function, or None."""
    global _rust_lib, _rust_loaded
    if _rust_loaded:
        return _rust_lib
    _rust_loaded = True
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_macshim_keep_awake          # AttributeError when this dylib predates the shim switch
            fn.argtypes = [ctypes.c_int32, ctypes.c_char_p]
            fn.restype = ctypes.c_int32
            _rust_lib = lib
            return _rust_lib
    except Exception:
        pass
    return None


# NSActivityOptions (from <Foundation/NSProcessInfo.h>).
_NSActivityIdleDisplaySleepDisabled = 1 << 40
_NSActivityIdleSystemSleepDisabled = 1 << 20
_NSActivityUserInitiated = 0x00FFFFFF | _NSActivityIdleSystemSleepDisabled
# App Nap off + system idle sleep off + display idle sleep off.
_KEEP_AWAKE_OPTIONS = (
    _NSActivityUserInitiated
    | _NSActivityIdleSystemSleepDisabled
    | _NSActivityIdleDisplaySleepDisabled
)


class KeepAwake:
    """Begin/end a macOS activity assertion. Safe and idempotent everywhere."""

    def __init__(self, reason: str = "SingWS karaoke session active"):
        self.reason = str(reason or "SingWS session")
        self._token = None
        self._process_info = None
        self._via_rust = False

    @staticmethod
    def _supported() -> bool:
        return sys.platform == "darwin"

    def active(self) -> bool:
        return self._via_rust or self._token is not None

    def backend(self) -> str:
        """Which code holds the assertion: "rust", "pyobjc" or "none" (for the log)."""
        return "rust" if self._via_rust else ("pyobjc" if self._token is not None else "none")

    def begin(self) -> bool:
        """Assert 'keep awake'. Returns True if the assertion is now held."""
        if self._via_rust or self._token is not None:
            return True
        if not self._supported():
            return False
        if _prefer_rust:
            lib = _rust()
            try:
                if lib is not None and int(lib.singws_macshim_keep_awake(1, self.reason.encode("utf-8"))) == 1:
                    self._via_rust = True
                    return True
            except Exception:
                pass
        try:
            from Foundation import NSProcessInfo

            self._process_info = NSProcessInfo.processInfo()
            self._token = self._process_info.beginActivityWithOptions_reason_(
                _KEEP_AWAKE_OPTIONS, self.reason
            )
            return self._token is not None
        except Exception:
            self._token = None
            self._process_info = None
            return False

    def end(self) -> None:
        """Release the assertion (safe to call when not held)."""
        if self._via_rust:
            self._via_rust = False
            try:
                lib = _rust()
                if lib is not None:
                    lib.singws_macshim_keep_awake(0, None)
            except Exception:
                pass
            return
        token = self._token
        self._token = None
        if token is None or self._process_info is None:
            self._process_info = None
            return
        try:
            self._process_info.endActivity_(token)
        except Exception:
            pass
        finally:
            self._process_info = None

    def set_enabled(self, enabled: bool) -> bool:
        """Turn the assertion on/off live (e.g. from a settings toggle)."""
        if enabled:
            return self.begin()
        self.end()
        return False
