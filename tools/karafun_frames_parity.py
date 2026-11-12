#!/usr/bin/env python3
"""Parity of the Rust frame hand-off rules (rust/crates/singws-karafun-svc, src/frames.rs, milestone W6, offline part) against the REAL
`karafun_web_host.KaraFunWebHost` (`_on_frame`, `_paint_latest`, `_start_frames`, `_stop_frames`) with fake windows, a fake image decoder (a picture that "does not
decode" is a null image), a fake player helper and a fake timer. NOT covered (needs a real screen): how the picture looks in the audience window, real timing.
Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv-test-arm64-fresh/bin/python tools/karafun_frames_parity.py [--cases N] [--seed S]      exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if not os.environ.get("SINGWS_HOME"):
    os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="kf_frames_parity_home_")
sys.path.insert(0, str(ROOT))
import karafun_web_host  # noqa: E402


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc-fr/release/singws-karafun-svc"), os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


def python_trace(events):
    log: list = []

    class Img:
        def __init__(self, ident=None): self.ident = ident
        def isNull(self): return self.ident is None

    class QImage(Img):
        @staticmethod
        def fromData(data, fmt=None):
            return Img(None if data.startswith(b"bad") else int(data.split(b":")[1]))

    class Timer:
        def __init__(self, parent=None):
            log.append(["timer_new", None])
            self.timeout = mock.Mock()
        def setInterval(self, ms): log[-1][1] = ms
        def start(self): log.append(["timer_start"])
        def stop(self): log.append(["timer_stop"])

    class Area:
        def __init__(self, who): self.who = who
        def set_karaoke_frame(self, frame, stretch_fill=None):
            if self.who == "vw":
                log.append(["vw_frame_clear"] if frame.isNull() else ["vw_frame", frame.ident, bool(stretch_fill)])
            else:
                log.append(["preview_frame_clear"] if frame.isNull() else ["preview_frame", frame.ident])

    class VW:
        video_area = Area("vw")
        _idle = None
        @property
        def idle(self): return self._idle
        @idle.setter
        def idle(self, v): self._idle = v; log.append(["vw_idle", v])

    class Preview:
        video_area = Area("preview")
        _fb = None
        @property
        def force_black(self): return self._fb
        @force_black.setter
        def force_black(self, v): self._fb = v; log.append(["preview_force_black", v])

    class Web:
        def audience(self, on): log.append(["audience", on])
        def start_screencast(self, w, h, q): log.append(["screencast", True, w, h, q])
        def stop_screencast(self): log.append(["screencast", False])

    class App:
        video_window = None
        preview_window = None
        def _set_mpv_hosts_visible(self, v): log.append(["hide_mpv_hosts"])

    app = App()
    host = karafun_web_host.KaraFunWebHost(app)
    service = mock.Mock()
    host.service = service
    out = []
    with mock.patch("PyQt6.QtGui.QImage", QImage), mock.patch("PyQt6.QtCore.QTimer", Timer):
        for e in events:
            app.video_window = VW() if e.get("vw", True) else None
            app.preview_window = Preview() if e.get("preview", True) else None
            service.web = Web() if e.get("has_web", True) else None
            mark = len(log)
            kind = e["ev"]
            if kind == "frame":
                host._on_frame(f"{'bad' if not e.get('decodes', True) else 'ok'}:{e['id']}".encode())
            elif kind == "paint":
                host._paint_latest()
            elif kind == "start":
                host._start_frames()
            elif kind == "stop":
                host._stop_frames()
            out.append([x for x in log[mark:]])
    return {"trace": out}


def gen(rng):
    ev = []
    n = 0
    for _ in range(rng.randint(1, 25)):
        r = rng.random()
        flags = {"has_web": rng.random() < 0.9, "vw": rng.random() < 0.9, "preview": rng.random() < 0.9}
        if r < 0.35:
            n += 1
            ev.append({"ev": "frame", "id": n, "decodes": rng.random() < 0.85, **flags})
        elif r < 0.7:
            ev.append({"ev": "paint", **flags})
        elif r < 0.85:
            ev.append({"ev": "start", **flags})
        else:
            ev.append({"ev": "stop", **flags})
    return ev


def fix_decode(events):
    """The Rust side learns whether the newest frame decodes from the paint event: carry the frame's own flag to the paint that consumes it."""
    out, last_decodes = [], True
    for e in events:
        e = dict(e)
        if e["ev"] == "frame":
            last_decodes = e.get("decodes", True)
        elif e["ev"] == "paint":
            e["decodes"] = last_decodes
        out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    cli = find_cli()
    rng = random.Random(a.seed)
    same = diff = 0
    first = []
    for _ in range(a.cases):
        ev = gen(rng)
        py = python_trace(ev)
        rs = json.loads(subprocess.run([cli], input=json.dumps({"kind": "frames", "events": fix_decode(ev)}), capture_output=True, text=True, timeout=30).stdout)
        if py == rs:
            same += 1
        else:
            diff += 1
            if len(first) < 3:
                first.append((ev, py, rs))
    print(f"scenarios {a.cases}: identical {same}, different {diff}")
    for ev, py, rs in first:
        print("--- events:", json.dumps(ev)[:600], "\n python:", json.dumps(py)[:600], "\n rust:  ", json.dumps(rs)[:600])
    print("IDENTICAL" if diff == 0 else "DIFFERENT")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
