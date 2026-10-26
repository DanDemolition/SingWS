#!/usr/bin/env python3
"""Drive the application's REAL BackgroundMusicPlayer (playlist advance, scheduled crossfades, fades, seeks, volume) on the Rust music engine.

    QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORM_PLUGIN_PATH=/tmp/singws-release-qt-platforms \\
        .venv-test-arm64-fresh/bin/python tools/bgm_player_rehearsal.py --mode headless --transitions 6
    ... --mode device --minutes 240          # the long soak: the real sound card, SILENT (host volume forced to 0)

The player is the unmodified class from 0.2.18.1.py with a small stand-in for the host window. `headless` uses the Rust engine without a
sound card, paced in real time by a pump thread, so it is silent and independent of hardware; `device` opens the real output with the
player's volume forced to 0 (the stream runs, nothing is heard). To save time each track is started, then seeked to `--tail` seconds before its
end, and the player's own 500 ms scheduler does the rest (adaptive crossfade, preload of the next track, completion, advance).

Reports: transitions completed, silent frames, xruns, dropped commands, deck underruns, tracebacks in the output, resident memory over time.
Exit status 0 when every check passes. Always uses a scratch SINGWS_HOME: it never touches the live show data.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-rehearsal-"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class Tee(io.TextIOBase):
    """Collects everything the app prints (it logs with print) while still showing it."""

    def __init__(self, real):
        self.real = real
        self.lines: list[str] = []
        self._lock = threading.Lock()

    def write(self, s):
        with self._lock:
            for part in s.splitlines():
                if part.strip():
                    self.lines.append(part)
        try:
            return self.real.write(s)
        except Exception:
            return len(s)

    def flush(self):
        try:
            self.real.flush()
        except Exception:
            pass


def rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("headless", "device"), default="headless")
    ap.add_argument("--transitions", type=int, default=6, help="stop after this many automatic track changes (headless default 6)")
    ap.add_argument("--minutes", type=float, default=0.0, help="run for this long instead (device soak)")
    ap.add_argument("--tail", type=float, default=22.0, help="seconds before a track's end to jump to (0 = play whole tracks)")
    ap.add_argument("--tracks", type=int, default=40)
    ap.add_argument("--playlist", default=os.path.expanduser("~/SingWS/bg_playlist.json"), help="read-only: only the file names are used")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--exercise", action="store_true", help="also pause/play/seek/fade/volume-change between transitions")
    ap.add_argument("--pads", type=int, default=0, help="also click this many REAL SoundboardPad widgets at random (layering, Shift-click stops all) while the music runs")
    args = ap.parse_args()

    out = Tee(sys.stdout)
    sys.stdout = out

    spec = importlib.util.spec_from_file_location("singws_main_rehearsal", ROOT / "0.2.18.1.py")
    app_mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app_mod
    spec.loader.exec_module(app_mod)
    from PyQt6.QtCore import QObject, QTimer
    from PyQt6.QtWidgets import QApplication
    import rust_bgm_engine as rbe

    if not rbe.available():
        print("Rust library not built: cd rust && cargo build --release -p singws-bgm-ffi")
        return 2
    qapp = QApplication.instance() or QApplication([])

    class Host(QObject):
        def __init__(self):
            super().__init__()
            self.settings = {"bgm_engine": "rust", "simple_audio_mode": True, "seamless_transitions_enabled": True}
            self._host_bgm_live_volume = 0.0 if args.mode == "device" else 0.8
            self.karaoke_playing = False
            self.bgm_eq = None
            self.bgm_master = None
            self.ui_updates = 0

        def _bgm_master_active(self):
            return False

        def _ensure_bgm_master_processor(self):
            return None

        def _ensure_eq_engines(self):
            pass

        def _master_chain_label(self, *a, **k):
            return "test"

        def _run_on_ui_thread(self, fn, *a, **k):
            return fn(*a, **k)

        def _safe_local_audio_output_name(self, *a, **k):
            return ""

        def update_bg_button_state(self, *a, **k):
            self.ui_updates += 1

        def update_bg_track_display(self, *a, **k):
            self.ui_updates += 1

    if args.mode == "headless":
        class HeadlessRust(rbe.RustBackgroundEngine):
            def __init__(self, output_name=None, sample_rate=48000):
                super().__init__(output_name, sample_rate, headless=True)
        rbe.RustBackgroundEngine = HeadlessRust

    files = []
    try:
        for x in json.load(open(args.playlist)):
            p = x if isinstance(x, str) else x.get("path")
            if p and os.path.exists(p):
                files.append(p)
    except Exception as exc:
        print("cannot read the playlist:", exc)
        return 2
    random.Random(args.seed).shuffle(files)
    files = files[: args.tracks]
    if len(files) < 3:
        print("need at least 3 playable tracks")
        return 2

    host = Host()
    player = app_mod.BackgroundMusicPlayer(host)
    engine = player._bgm_engine
    print(f"[REHEARSAL] engine={getattr(engine, 'backend_name', None)} mode={args.mode} tracks={len(files)}")
    if getattr(engine, "backend_name", "") != "Rust":
        print("FAIL: the player did not choose the Rust engine")
        return 1
    player.playlist = list(files)
    player.current_index = 0
    player.volume = host._host_bgm_live_volume
    player.set_enabled(True)

    pad_widgets = []
    owner = None
    if args.pads > 0:
        from PyQt6.QtWidgets import QWidget
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtTest import QTest

        class Owner(QWidget):
            """What SoundboardPad._app_owner() looks for."""
            bg_music = player

            def _get_selected_audio_output_id(o):
                return "default"

        owner = Owner()
        host.soundboard_strip = None
        player.set_soundboard_volume(0.0 if args.mode == "device" else 0.5)     # device mode must stay inaudible
        clip = files[1]
        for i in range(args.pads):
            pad = app_mod.SoundboardPad(i, parent=owner)
            pad._set_clip(clip, f"pad{i}", 1.0)
            pad_widgets.append(pad)
        print(f"[REHEARSAL] {len(pad_widgets)} real SoundboardPad widgets loaded (layering={pad_widgets[0]._channel.supports_layering})")
    pad_rng = random.Random(args.seed + 1)
    pad_stats = {"clicks": 0, "shift_stops": 0}
    last_pad = [0.0]

    stop_pump = threading.Event()
    pump = None
    if args.mode == "headless":
        def run_pump():
            t = time.perf_counter()
            while not stop_pump.is_set():
                t += 0.010
                try:
                    engine.render(480)
                except Exception:
                    pass
                d = t - time.perf_counter()
                if d > 0:
                    time.sleep(d)
                else:
                    t = time.perf_counter()
        pump = threading.Thread(target=run_pump, daemon=True)
        pump.start()

    player.play()
    t_start = time.time()
    index_seen = player.current_index
    transitions = 0
    last_report = 0.0
    rss0 = rss_mb()
    rss_log = []
    jumped_for = None
    exercised = set()
    errors = []

    def report():
        s = rbe.RustBackgroundEngine  # noqa: F841
        st = engine._stats()
        pos, dur = engine.get_times()
        rss = rss_mb()
        rss_log.append((time.time() - t_start, rss))
        print(f"[REHEARSAL] t={time.time() - t_start:7.0f}s transitions={transitions} idx={player.current_index} pos={pos:6.1f}/{dur:6.1f} "
              f"xruns={st[6]} dropped_cmds={st[10]} underruns={st[11]} gap_frames={st[12]} pending={st[13]} rss={rss:6.1f}MB")

    deadline = t_start + (args.minutes * 60 if args.minutes else 3600 * 3)
    try:
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
            now = time.time()
            if player.current_index != index_seen:
                transitions += 1
                index_seen = player.current_index
                jumped_for = None
                print(f"[REHEARSAL] transition {transitions}: now track {index_seen} {Path(player.playlist[index_seen]).name!r} (crossfade_active={player.crossfade_active})")
                if args.transitions and not args.minutes and transitions >= args.transitions:
                    break
            step = ("pause_play", "seek", "volume", "fade")[transitions % 4]
            key = (transitions, step)
            if args.exercise and key not in exercised and player.is_playing and not player.crossfade_active:
                pos, dur = engine.get_times()
                if 4 < pos < 25 and dur > 60:
                    exercised.add(key)
                    print(f"[REHEARSAL] exercise {step} on track {transitions}")
                    if step == "pause_play":
                        player.pause()
                        for _ in range(10):
                            qapp.processEvents()
                            time.sleep(0.05)
                        player.play()
                    elif step == "seek":
                        player.seek_seconds(pos + 10.0)
                    elif step == "volume":
                        player.set_volume(0.3 if args.mode == "headless" else 0.001)  # device mode must stay inaudible
                        for _ in range(5):
                            qapp.processEvents()
                            time.sleep(0.05)
                        player.set_volume(host._host_bgm_live_volume)
                    elif step == "fade":
                        player.fade_out(400)
                        t_end = time.time() + 1.2
                        while time.time() < t_end:
                            qapp.processEvents()
                            time.sleep(0.05)
                        player.fade_in(host._host_bgm_live_volume, 400)
                    after = engine.get_times()[0]
                    print(f"[REHEARSAL]   after {step}: playing={player.is_playing} engine_playing={engine.is_playing()} pos={after:.1f} master={engine.master_volume:.2f}")
                    continue
            if args.tail > 0 and jumped_for != player.current_index and not player.crossfade_active and player.is_playing \
                    and (not args.exercise or key in exercised):
                pos, dur = engine.get_times()
                if dur > args.tail + 15 and pos > 3:
                    player.seek_seconds(max(0.0, dur - args.tail))
                    jumped_for = player.current_index
            if pad_widgets and now - last_pad[0] >= 0.3:
                last_pad[0] = now
                pad = pad_rng.choice(pad_widgets)
                shift = pad_rng.random() < 0.12
                QTest.mouseClick(pad, _Qt.MouseButton.LeftButton, _Qt.KeyboardModifier.ShiftModifier if shift else _Qt.KeyboardModifier.NoModifier)
                pad_stats["shift_stops" if shift else "clicks"] += 1
            if now - last_report >= (30 if args.mode == "headless" else 600):
                last_report = now
                report()
    finally:
        report()
        stop_pump.set()

    st = engine._stats()
    lines = list(out.lines)
    tracebacks = [l for l in lines if "Traceback" in l or "Error" in l and "[BG" in l]
    peak_rss = max((r for _, r in rss_log), default=rss0)
    growth = peak_rss - rss0
    ok = True

    def check(name, cond, detail):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")

    print("\n[REHEARSAL] results")
    want = args.transitions if not args.minutes else 1
    check("tracks advanced by the player's own scheduler", transitions >= want, f"{transitions} transitions (wanted {want}+)")
    check("no silent frames while playing", st[12] == 0, f"gap frames {st[12]}")
    check("no audio dropouts", st[6] == 0 and st[10] == 0, f"xruns {st[6]}, dropped commands {st[10]}")
    check("no deck underruns", st[11] == 0, f"underruns {st[11]}")
    check("no tracebacks or [BG ...] errors printed", not tracebacks, f"{len(tracebacks)} line(s)" + (": " + tracebacks[0][:160] if tracebacks else ""))
    fallbacks = [l for l in lines if "using BASS" in l]
    check("never fell back to BASS", not fallbacks, f"{len(fallbacks)} fallback line(s)")
    limit = 60.0 if not args.minutes else 150.0
    check("memory growth", growth < limit, f"{rss0:.0f} -> peak {peak_rss:.0f} MB (+{growth:.0f}, limit {limit:.0f})")
    if pad_widgets:
        pad_errors = [l for l in lines if "[SOUNDBOARD]" in l and ("failed" in l or "could not" in l)]
        check("pads fired without errors", not pad_errors, f"{pad_stats['clicks']} clicks, {pad_stats['shift_stops']} Shift-click stops, {len(pad_errors)} error line(s)" + (": " + pad_errors[0][:140] if pad_errors else ""))
    transitions_logged = [l for l in lines if "[TRANSITION] BGM crossfade selected" in l]
    print(f"  (info) player logged {len(transitions_logged)} scheduled crossfade selections; {host.ui_updates} UI refresh calls")
    try:
        player.stop()
        engine.close()
    except Exception:
        pass
    print("\nREHEARSAL PASSED" if ok else "\nREHEARSAL HAS FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
