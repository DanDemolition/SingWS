"""Platform shadow (Stage E step 1): compare what the Rust ``singws-mac-probe`` sees with what the app's own Python code sees, and LOG differences.

SHADOW ONLY. The app never uses the Rust answers: Python's frontmost-app name, Accessibility trust and keep-awake state stay the ones in
use. The probe runs in a separate low-priority process on a daemon thread, never prompts (Accessibility is read with the prompt option off,
location is not touched), and every failure is swallowed. Setting ``platform_shadow`` (default False): True runs one comparison at launch.

Log lines (grep ``PLATFORM-SHADOW``), written ONLY when something differs or the probe could not run:
  ``MISMATCH <what>: app=<python> rust=<rust>``  (each distinct difference is logged once per session)
  ``skipped: <reason>``                          (no probe binary, probe failed, not macOS)
An ``ax_trusted`` difference is EXPECTED when the probe binary has no Accessibility grant of its own (the grant belongs to the binary, see
docs/design/stage-e-platform-shim-findings.md); it is exactly the evidence that note asks for.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

PROBE_NAME = "singws-mac-probe"
TIMEOUT_S = 15.0
_seen: set[str] = set()


def enabled(settings) -> bool:
    return bool((settings or {}).get("platform_shadow", False)) and sys.platform == "darwin"


def find_probe() -> Path | None:
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    candidates: list[Path] = []
    override = os.environ.get("SINGWS_MAC_PROBE_BIN")
    if override:
        candidates.append(Path(override))
    for root in (getattr(sys, "_MEIPASS", None),
                 Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
                 Path(__file__).resolve().parent):
        if root:
            root = Path(root)
            candidates += [root / PROBE_NAME, root / "Frameworks" / PROBE_NAME, root / "native" / "singws_mac" / arch / PROBE_NAME]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def python_frontmost_app_name() -> str:
    """What the app's own code reads (``_karafun_frontmost_app_name``)."""
    try:
        from AppKit import NSWorkspace
        return str(NSWorkspace.sharedWorkspace().frontmostApplication().localizedName() or "")
    except Exception:
        return ""


def python_ax_trusted() -> bool:
    """Accessibility trust of THIS process. ``AXIsProcessTrusted`` never prompts."""
    try:
        import ctypes
        lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return False


def python_state(keep_awake=None, frontmost=python_frontmost_app_name, ax=python_ax_trusted) -> dict:
    active = False
    try:
        active = bool(keep_awake.active()) if keep_awake is not None else False
    except Exception:
        pass
    return {"frontmost_app": frontmost(), "ax_trusted": ax(), "keep_awake_active": active}


def compare_state(py_before: dict, py_after: dict, rs: dict, keep_awake_expected: bool) -> list[str]:
    """Differences. The frontmost app is read twice around the probe run and is fine if the probe agrees with either read (focus can move)."""
    out: list[str] = []
    if rs.get("frontmost_app") not in (py_before.get("frontmost_app"), py_after.get("frontmost_app")):
        out.append(f"frontmost_app: app={py_before.get('frontmost_app')!r} rust={rs.get('frontmost_app')!r}")
    if bool(py_before.get("ax_trusted")) != bool(rs.get("ax_trusted")) or bool(py_after.get("ax_trusted")) != bool(rs.get("ax_trusted")):
        out.append(f"ax_trusted: app={bool(py_after.get('ax_trusted'))} rust={bool(rs.get('ax_trusted'))}")
    # Keep-awake: Python should hold the assertion exactly when the setting asks for it, and Rust must be able to take and release one.
    if bool(py_after.get("keep_awake_active")) != bool(keep_awake_expected):
        out.append(f"keep_awake: app_active={bool(py_after.get('keep_awake_active'))} setting_on={bool(keep_awake_expected)}")
    if not (rs.get("keep_awake_ok") and rs.get("keep_awake_released")):
        out.append(f"keep_awake: rust_ok={rs.get('keep_awake_ok')} rust_released={rs.get('keep_awake_released')}")
    return out


def run_probe(probe: Path) -> dict:
    proc = subprocess.run([str(probe), "--json"], capture_output=True, text=True, timeout=TIMEOUT_S, preexec_fn=lambda: os.nice(10))
    if proc.returncode != 0:
        raise RuntimeError(f"probe exit status {proc.returncode}")
    return json.loads(proc.stdout)


def run_shadow(settings, keep_awake, log, probe: Path | None = None, py_state=python_state) -> str:
    """One comparison. Returns ``clean``, ``mismatch`` or ``skipped`` (for tests)."""
    def say(text):
        try:
            log(f"[PLATFORM-SHADOW] {text}")
        except Exception:
            pass

    try:
        probe = probe or find_probe()
        if probe is None:
            say("skipped: the Rust platform probe was not found")
            return "skipped"
        before = py_state(keep_awake)
        rs = run_probe(probe)
        after = py_state(keep_awake)
        if not rs.get("supported"):
            say("skipped: the probe reports an unsupported platform")
            return "skipped"
        diffs = compare_state(before, after, rs, bool((settings or {}).get("keep_mac_awake", True)))
        if not diffs:
            return "clean"
        fresh = [d for d in diffs if d not in _seen]
        _seen.update(diffs)
        for d in fresh:
            say(f"MISMATCH {d}")
        return "mismatch"
    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app
        say(f"skipped: {type(exc).__name__}: {str(exc)[:160]}")
        return "skipped"


def start(settings, keep_awake, log) -> threading.Thread | None:
    """Called once by the app after launch set-up. Returns the daemon thread (None when the shadow is off)."""
    try:
        if not enabled(settings):
            return None
        thread = threading.Thread(target=run_shadow, args=(dict(settings), keep_awake, log), name="platform-shadow", daemon=True)
        thread.start()
        return thread
    except Exception:  # noqa: BLE001
        return None
