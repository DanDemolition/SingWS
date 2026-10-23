#!/usr/bin/env python3
"""Run the transport-shadow acceptance matrix locally against the real Rust dylib.

This is a deterministic substitute for the three-show observation gate when the
operator explicitly accepts local rehearsal. It exercises the same event routes
and writes three show-shaped logs, then delegates the verdict to
``transport_shadow_report`` so the production acceptance rules remain the oracle.
"""

from __future__ import annotations

import tempfile
import sys
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "SINGWS_DSP_LIB" not in os.environ:
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    os.environ["SINGWS_DSP_LIB"] = str(
        ROOT / "native" / "singws_dsp" / arch / "libsingws_dsp_ffi.dylib"
    )

from transport_shadow import TransportShadow
from tools import transport_shadow_report


def _show(run) -> Path:
    lines = ["[LOCAL-ACCEPTANCE] [TRANSPORT-SHADOW] enabled available=1"]
    shadow = TransportShadow(lines.append)
    if not shadow.available:
        raise RuntimeError("Rust transport dylib is unavailable")
    run(shadow)
    if shadow._session:  # local harness owns the object and must close its final session
        shadow._flush_session()
    handle = tempfile.NamedTemporaryFile("w", prefix="singws-local-shadow-", suffix=".log", delete=False)
    with handle:
        handle.write("\n".join(lines) + "\n")
    return Path(handle.name)


def _native_cdg(shadow: TransportShadow) -> None:
    session = shadow.start(False, 200.0, source="native:cdg")
    shadow.observe(session, "playing", now=1.0)
    shadow.note_seek(session, 120.0, 180.0, 200.0)
    shadow.observe(session, "media_end", now=200.0)
    shadow.app_auto_complete(session)
    shadow.app_finished(session, "native_end")


def _native_mp4(shadow: TransportShadow) -> None:
    session = shadow.start(False, 180.0, source="native:mp4")
    shadow.observe(session, "playing", now=1.0)
    shadow.observe(session, "media_end", now=180.0)
    shadow.app_auto_complete(session)
    shadow.app_finished(session, "native_end")

    stopped = shadow.start(False, 120.0, source="native:mp4")
    shadow.observe(stopped, "playing", now=1.0)
    shadow.app_stopped(stopped)


def _karafun(shadow: TransportShadow) -> None:
    automatic = shadow.start(True, 100.0, source="karafun")
    shadow.observe(automatic, "playing", now=10.0)
    shadow.observe(automatic, "playing", now=12.0)
    shadow.observe(automatic, "end_clock", now=111.0)
    shadow.observe(automatic, "idle", now=112.0)
    shadow.app_auto_complete(automatic)
    shadow.app_finished(automatic, "complete")

    manual = shadow.start(True, 240.0, source="karafun")
    shadow.observe(manual, "playing", now=1.0)
    shadow.observe(manual, "playing", now=3.0)
    shadow.observe(manual, "watchdog", now=220.0)
    shadow.app_finished(manual, "complete")


def main() -> int:
    paths = [_show(_native_cdg), _show(_native_mp4), _show(_karafun)]
    results = [transport_shadow_report.summarise(str(path)) for path in paths]
    watched, clean, accepted = transport_shadow_report.verdict(results)
    transport_shadow_report.main([str(path) for path in paths])
    print("local logs: " + ", ".join(str(path) for path in paths))
    if not accepted:
        print(f"LOCAL ACCEPTANCE FAILED: watched={watched} clean={clean}")
        return 1
    print("LOCAL ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
