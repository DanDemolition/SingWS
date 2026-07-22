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

import sys

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

    @staticmethod
    def _supported() -> bool:
        return sys.platform == "darwin"

    def active(self) -> bool:
        return self._token is not None

    def begin(self) -> bool:
        """Assert 'keep awake'. Returns True if the assertion is now held."""
        if self._token is not None:
            return True
        if not self._supported():
            return False
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
