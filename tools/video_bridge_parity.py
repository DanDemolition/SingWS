#!/usr/bin/env python3
"""Run the same scenario through a bridge dylib (the Objective-C one or the Rust `singws-video` one) and compare the results.

    # one process per dylib (the Objective-C bridge needs real Qt windows and a running main loop; windows show for ~40 s, no sound)
    QT_QPA_PLATFORM=cocoa QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/singws-release-qt-platforms \\
      .venv-test-arm64-fresh/bin/python tools/video_bridge_parity.py run --dylib PATH --label objc --out objc.json
    SINGWS_LIBMPV=native/mpv_runtime/artifacts/arm64/Frameworks/singws_libmpv.2.dylib SINGWS_BRIDGE_AO=null \\
      QT_QPA_PLATFORM=cocoa ... tools/video_bridge_parity.py run --dylib ~/.cache/singws-rust-target/release/libsingws_video.dylib --label rust --out rust.json
    .venv-test-arm64-fresh/bin/python tools/video_bridge_parity.py compare objc.json rust.json

The scenario: an MP3+G pair (a library ZIP), then an MP4, volume 0. It records durations, positions, the play/pause/at-end flags and the
`[bridge]` / `[audio]` log lines (the af chain composition, audio-delay, device refusal), plus silence scans of three files.
`compare` exits 0 when the two bridges agree within tolerance (positions 300 ms, durations 5 ms, log lines identical after
normalising paths and serial numbers).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

LIB = Path.home() / "Music/Karaoke/Karaoke Library"
DEFAULT_ZIP = LIB / "SC17631 - PM Dawn - Set Adrift On Memory Bliss.zip"
DEFAULT_MP4 = LIB / "RSK - MxPx - Responsibility.mp4"


def bind(path: str):
    L = ctypes.CDLL(path)
    P, I, D, S, I64 = ctypes.c_void_p, ctypes.c_int, ctypes.c_double, ctypes.c_char_p, ctypes.c_int64
    L.singws_bridge_create.restype = P
    L.singws_bridge_create.argtypes = [ctypes.c_size_t, ctypes.c_size_t, S, S]
    L.singws_bridge_load.restype = I
    L.singws_bridge_load.argtypes = [P, S, S]
    L.singws_bridge_destroy.argtypes = [P]
    for n in ("play", "pause", "stop"):
        getattr(L, f"singws_bridge_{n}").argtypes = [P]
    L.singws_bridge_seek.argtypes = [P, I64]
    for n in ("position", "duration"):
        f = getattr(L, f"singws_bridge_{n}")
        f.restype, f.argtypes = I64, [P]
    for n in ("is_playing", "at_end", "visual_ready"):
        f = getattr(L, f"singws_bridge_{n}")
        f.restype, f.argtypes = I, [P]
    L.singws_bridge_set_volume.argtypes = [P, D]
    L.singws_bridge_set_device.argtypes = [P, S]
    L.singws_bridge_set_tempo.argtypes = [P, I]
    L.singws_bridge_set_key.argtypes = [P, I]
    L.singws_bridge_set_dsp_chain.argtypes = [P, S]
    L.singws_bridge_set_audio_delay.argtypes = [P, D]
    L.singws_bridge_refresh_views.argtypes = [P, ctypes.c_size_t, ctypes.c_size_t]
    L.singws_bridge_set_sidefill.argtypes = [P, I]
    L.singws_bridge_begin_transition.argtypes = [P, I]
    L.singws_bridge_present_count.restype = ctypes.c_uint64
    L.singws_bridge_present_count.argtypes = [P]
    L.singws_bridge_load_background.restype = I
    L.singws_bridge_load_background.argtypes = [P, S, D]
    L.singws_bridge_set_background_opacity.argtypes = [P, D]
    L.singws_bridge_stop_background.argtypes = [P]
    for n, rt in (("background_at_end", I), ("background_position", I64), ("background_paused", I)):
        f = getattr(L, f"singws_bridge_{n}")
        f.restype, f.argtypes = rt, [P]
    L.singws_bridge_grab_frame.restype = P
    L.singws_bridge_grab_frame.argtypes = [P, ctypes.POINTER(I), ctypes.POINTER(I), ctypes.POINTER(I)]
    L.singws_bridge_free_frame.argtypes = [P]
    L.singws_bridge_set_rotation_host.argtypes = [P, ctypes.c_size_t, I]
    L.singws_bridge_set_rotation_spotlight.argtypes = [P, ctypes.c_size_t, I]
    L.singws_bridge_render_thread_active.restype = I
    L.singws_bridge_render_thread_active.argtypes = [P]
    L.singws_bridge_scan_silence.restype = I
    L.singws_bridge_scan_silence.argtypes = [S, D, D, D, ctypes.POINTER(D), ctypes.POINTER(D), ctypes.POINTER(D)]
    L.singws_bridge_scanner_shutdown.argtypes = []
    CB = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
    L.singws_bridge_set_log_callback.argtypes = [CB]
    return L, CB


def normalise(line: str) -> str:
    line = re.sub(r"serial=\d+", "serial=N", line)
    if line.startswith("[scanner] ready"):
        head, sep, path = line.partition(" /")
        if sep:
            line = head + " " + os.path.basename("/" + path)
    line = re.sub(r"(video|file)=\S+", lambda m: f"{m.group(1)}=" + os.path.basename(m.group(0).split("=", 1)[1]), line)
    line = re.sub(r"audio=\S+", lambda m: "audio=" + os.path.basename(m.group(0)[6:]), line)
    return line


def run(args) -> int:
    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QWidget

    lib, CB = bind(args.dylib)
    logs: list[str] = []
    sink = CB(lambda b: logs.append(b.decode(errors="replace")))
    lib.singws_bridge_set_log_callback(sink)
    tmp = tempfile.mkdtemp(prefix="singws-parity-")
    with zipfile.ZipFile(args.zip) as z:
        z.extractall(tmp)
    files = sorted(Path(tmp).rglob("*"))
    cdg = next(str(f) for f in files if f.suffix.lower() == ".cdg")
    mp3 = next(str(f) for f in files if f.suffix.lower() == ".mp3")
    mp4 = str(args.mp4)

    app = QApplication([])
    out_w, prev_w = QWidget(), QWidget()
    out_w.resize(640, 360)
    prev_w.resize(320, 180)
    out_w.show()
    prev_w.show()
    app.processEvents()

    class Done(QObject):
        finished = pyqtSignal()

    class MainCaller(QObject):
        """Runs bridge calls on the Qt main thread (AppKit requires it; the app calls the bridge from Qt's thread too)."""
        call = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.call.connect(self._run, Qt.ConnectionType.BlockingQueuedConnection)

        def _run(self, box):
            try:
                box["result"] = box["fn"](*box["args"])
            except Exception as exc:  # noqa: BLE001
                box["error"] = exc

    caller = MainCaller()

    class Proxy:
        def __getattr__(self, name):
            fn = getattr(lib, name)

            def go(*a):
                box = {"fn": fn, "args": a}
                caller.call.emit(box)
                if "error" in box:
                    raise box["error"]
                return box.get("result")
            return go

    B = Proxy()

    done = Done()
    done.finished.connect(app.quit)
    result: dict = {"label": args.label, "steps": {}}

    def wait_for(fn, timeout=20.0, step=0.05):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if fn():
                return round(time.monotonic() - t0, 2)
            time.sleep(step)
        return None

    def scenario():
        S = result["steps"]
        try:
            h = B.singws_bridge_create(int(out_w.winId()), int(prev_w.winId()), cdg.encode(), mp3.encode())
            S["create_ok"] = bool(h)
            if not h:
                return
            B.singws_bridge_set_volume(h, 0.0)
            S["cdg_loaded_s"] = wait_for(lambda: B.singws_bridge_duration(h) > 0)
            S["cdg_duration_ms"] = B.singws_bridge_duration(h)
            S["cdg_visual_ready"] = wait_for(lambda: B.singws_bridge_visual_ready(h), 8.0) is not None
            B.singws_bridge_play(h)
            time.sleep(2.0)
            S["cdg_playing"] = bool(B.singws_bridge_is_playing(h))
            S["cdg_pos_after_2s_ms"] = B.singws_bridge_position(h)
            B.singws_bridge_set_tempo(h, 90)
            B.singws_bridge_set_key(h, 2)
            time.sleep(1.0)
            B.singws_bridge_set_dsp_chain(h, b"lavfi=[volume=0dB]")
            time.sleep(0.5)
            B.singws_bridge_set_tempo(h, 100)
            B.singws_bridge_set_key(h, 0)
            time.sleep(0.5)
            B.singws_bridge_set_audio_delay(h, 0.5)
            B.singws_bridge_seek(h, 20000)
            time.sleep(1.2)
            S["cdg_pos_after_seek_ms"] = B.singws_bridge_position(h)
            B.singws_bridge_pause(h)
            time.sleep(0.4)
            S["cdg_paused_playing"] = bool(B.singws_bridge_is_playing(h))
            B.singws_bridge_set_device(h, b"External Headphones")
            time.sleep(0.5)
            B.singws_bridge_set_device(h, b"auto")
            time.sleep(0.3)
            # second song: an MP4 on the same handle
            S["mp4_load_call"] = bool(B.singws_bridge_load(h, mp4.encode(), None))
            S["mp4_loaded_s"] = wait_for(lambda: B.singws_bridge_duration(h) > 0)
            S["mp4_duration_ms"] = B.singws_bridge_duration(h)
            S["mp4_visual_ready"] = wait_for(lambda: B.singws_bridge_visual_ready(h), 8.0) is not None
            B.singws_bridge_play(h)
            time.sleep(1.5)
            S["mp4_playing"] = bool(B.singws_bridge_is_playing(h))
            S["mp4_pos_after_1.5s_ms"] = B.singws_bridge_position(h)
            B.singws_bridge_seek(h, max(0, S["mp4_duration_ms"] - 1500))
            S["mp4_at_end_after_s"] = wait_for(lambda: B.singws_bridge_at_end(h), 8.0)
            B.singws_bridge_stop(h)
            time.sleep(0.5)
            S["after_stop_playing"] = bool(B.singws_bridge_is_playing(h))
            B.singws_bridge_destroy(h)
            # silence scans (no views): the extracted MP3, the MP4 and a background-music track
            scans = {}
            bgm = Path.home() / "Music/Karaoke/DJ Music"
            extra = sorted(bgm.glob("*.mp3"))[:1]
            for name, path in (("cdg_mp3", mp3), ("mp4", mp4), *[("bgm", str(p)) for p in extra]):
                lead, trail, dur = ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
                t0 = time.monotonic()
                ok = lib.singws_bridge_scan_silence(path.encode(), -50.0, 0.5, 1.5, ctypes.byref(lead), ctypes.byref(trail), ctypes.byref(dur))
                scans[name] = {"ok": bool(ok), "lead": round(lead.value, 3), "trail": round(trail.value, 3),
                               "duration": round(dur.value, 3), "seconds": round(time.monotonic() - t0, 2)}
            lib.singws_bridge_scanner_shutdown()
            S["scans"] = scans
        except Exception as exc:  # noqa: BLE001
            S["error"] = repr(exc)
        finally:
            result["log"] = [normalise(x) for x in logs if x.startswith(("[bridge]", "[audio]", "[scanner]"))]
            Path(args.out).write_text(json.dumps(result, indent=1))
            done.finished.emit()

    threading.Thread(target=scenario, daemon=True).start()
    QTimer.singleShot(120000, app.quit)
    app.exec()
    print(f"wrote {args.out}")
    return 0


def compare(args) -> int:
    a, b = (json.loads(Path(p).read_text()) for p in (args.a, args.b))
    sa, sb = a["steps"], b["steps"]
    bad = 0

    def row(name, va, vb, ok):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"{'ok  ' if ok else 'DIFF'} {name:28} {a['label']}={va!s:>12}  {b['label']}={vb!s:>12}")

    for key in sorted(set(sa) | set(sb)):
        if key in ("scans", "error"):
            continue
        va, vb = sa.get(key), sb.get(key)
        if key.endswith("_duration_ms"):
            ok = va is not None and vb is not None and abs(va - vb) <= 5
        elif key.endswith("_ms"):
            ok = va is not None and vb is not None and abs(va - vb) <= 300
        elif key.endswith("_s") or key.endswith("_after_s"):
            ok = (va is None) == (vb is None)           # timings differ run to run; both must happen
        else:
            ok = va == vb
        row(key, va, vb, ok)
    for name in sorted(set(sa.get("scans", {})) | set(sb.get("scans", {}))):
        x, y = sa.get("scans", {}).get(name), sb.get("scans", {}).get(name)
        ok = bool(x and y and x["ok"] == y["ok"] and all(abs(x[k] - y[k]) <= 0.005 for k in ("lead", "trail", "duration")))
        row(f"scan {name}", (x or {}).get("trail"), (y or {}).get("trail"), ok)
    la, lb = a.get("log", []), b.get("log", [])
    # mpv's own chatter and timing lines can differ; compare the bridge-authored lines in order
    # view / presentation lines belong to the picture surface (V3+); the headless Rust bridge has none yet
    surface = ("render thread mode", " view ", "drawable reattached", "view presenting", "no-frame")
    keep = lambda L: [x for x in L if not x.startswith("[bridge] picture ") and "buffering" not in x and not any(m in x for m in surface)]
    import unicodedata
    norm = lambda L: [unicodedata.normalize('NFC', normalise(x)) for x in L]   # APFS names may be NFC or NFD in a log line
    ka, kb = norm(keep(la)), norm(keep(lb))
    same = ka == kb
    print(f"{'ok  ' if same else 'DIFF'} bridge log lines identical ({len(ka)} vs {len(kb)})")
    if not same:
        bad += 1
        for i in range(max(len(ka), len(kb))):
            x = ka[i] if i < len(ka) else "<none>"
            y = kb[i] if i < len(kb) else "<none>"
            if x != y:
                print(f"   [{i}] {a['label']}: {x}\n       {b['label']}: {y}")
    print("\nPARITY OK" if not bad and "error" not in sa and "error" not in sb else f"\n{bad} DIFFERENCE(S)")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dylib", required=True)
    r.add_argument("--label", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--zip", default=str(DEFAULT_ZIP))
    r.add_argument("--mp4", default=str(DEFAULT_MP4))
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    return run(args) if args.cmd == "run" else compare(args)


if __name__ == "__main__":
    sys.exit(main())
