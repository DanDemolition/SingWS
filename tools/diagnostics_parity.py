#!/usr/bin/env python3
"""Stage E pre-show-diagnostics parity: the Rust report (singws-uimodel `diagnostics`, `singws-diagnostics-cli`) against the app's REAL Python.

Oracle = the app module's real ``KaraokeApp._collect_pre_show_diagnostics`` and ``run_pre_show_diagnostics`` bound onto a plain stand-in. The machine probes are fed identically
to both sides: the audio output name, the display count (the module's ``QApplication.screens`` is replaced), KaraFun's path, the two native host objects, the ticker object, the
signup URL and the background-video folder scan (the module's ``scan_background_video_folder`` is replaced). ``QMessageBox`` and ``_diag`` are replaced for the duration of each call
to capture the box title / icon / detail text and the ``[PRE-SHOW]`` log line. Compared: every check (ok, label, detail), the title, the PASS / CHECK lines, the log line, the icon.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/diagnostics_parity.py [--cases N] [--seed S]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a scratch SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_DIAG_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-diagnostics-cli")
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:1100])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


class Box:
    last = None
    Icon = types.SimpleNamespace(Information="info", Warning="warn")

    def __init__(self, parent=None):
        Box.last = {}

    def setWindowTitle(self, t):
        Box.last["window"] = t

    def setIcon(self, i):
        Box.last["icon"] = i

    def setText(self, t):
        Box.last["text"] = t

    def setDetailedText(self, t):
        Box.last["detail"] = t

    def setInformativeText(self, t):
        Box.last["info"] = t

    def exec(self):
        return 0


def python_report(facts):
    a = hp.app()
    ka = a.KaraokeApp
    f = types.SimpleNamespace(settings=facts["settings"])
    f._safe_local_audio_output_name = lambda: facts["local_name"]
    f._karafun_application_path = lambda: facts["karafun_path"]
    f._header_qr_url = lambda: facts["qr_url"]
    f._mpv_output_host = object() if facts["output_host"] else None
    f._mpv_preview_host = object() if facts["preview_host"] else None
    f.video_window = types.SimpleNamespace(ticker=object() if facts["show_ticker"] else None)
    f._collect_pre_show_diagnostics = types.MethodType(ka._collect_pre_show_diagnostics, f)
    saved = (a.QApplication, a.scan_background_video_folder, a.QMessageBox, a._diag)
    logs = []
    a.QApplication = types.SimpleNamespace(screens=lambda: [object()] * facts["screen_count"])
    a.scan_background_video_folder = lambda folder: list(facts["bg_files"])
    a.QMessageBox = Box
    a._diag = lambda m, *x, **k: logs.append(m)
    try:
        checks = f._collect_pre_show_diagnostics()
        ka.run_pre_show_diagnostics(f)
    finally:
        a.QApplication, a.scan_background_video_folder, a.QMessageBox, a._diag = saved
    box = Box.last
    return {"checks": [list(c) for c in checks], "title": box["text"], "lines": box["detail"].split("\n"), "log_line": [m for m in logs if m.startswith("[PRE-SHOW]")][0],
            "all_passed": box["icon"] == "info"}


NAMES = ["MacBook Speakers", "", None, "External Headphones", "Ünal", 0]
PATHS = ["/Applications/KaraFun.app", "", None, "/x y"]
URLS = ["https://a.b/c", "", None]
FOLDERS = ["/vj", "  /pad  ", "", None, "~/x", 0, "Ünal"]
FILES = [[], ["/vj/a.mp4"], ["/vj/a.mp4", "/vj/b.mp4", "/vj/c.mp4"]]


def case(rng):
    s = {}
    for k, vals in (("karafun_provider_enabled", [True, False, 1, 0, None, ""]), ("ticker_enabled", [True, False, 1, 0, None]), ("bg_video_folder", FOLDERS),
                    ("bg_video_enabled", [True, False, 1, 0, None])):
        if rng.random() < 0.8:
            s[k] = rng.choice(vals)
    facts = {"local_name": rng.choice(NAMES), "screen_count": rng.choice([0, 1, 2, 3]), "settings": s, "karafun_path": rng.choice(PATHS), "output_host": rng.random() < 0.8,
             "preview_host": rng.random() < 0.8, "show_ticker": rng.random() < 0.8, "qr_url": rng.choice(URLS), "bg_files": rng.choice(FILES)}
    try:
        py, err = python_report(facts), None
    except Exception as exc:  # noqa: BLE001
        py, err = None, repr(exc)
    r = rust(facts)
    if "error" in r:
        if "decline" in r["error"]:
            tally("declined")
            return
        if err is None:
            fail(f"Rust errored ({r['error']}) but Python returned: {facts!r}")
        else:
            tally("both_error")
        return
    if err is not None:
        fail(f"Python raised ({err}) but Rust returned: {facts!r}")
        return
    got = {k: r[k] for k in ("checks", "title", "lines", "log_line", "all_passed")}
    if got != py:
        fail(f"{facts!r}\n python={py!r}\n rust  ={got!r}")
    else:
        tally("same")
        tally("all_passed" if py["all_passed"] else "some_failed")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        case(rng)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
