#!/usr/bin/env python3
"""Differential check: the real Python `MpvKaraokeTransport` against the Rust protocol machine (rust/crates/singws-shell/src/karaoke_proto.rs).

Each case in docs/design/karaoke_transport_cases.json is a script (operations at times, plus changes to the fake world). The Python side drives the real
transport with a fake plugin, a fake voice, a virtual clock (`time.monotonic`) and a virtual QTimer; the Rust side is `singws-karaoke-proto-cli`. Both write
one log of `[ms, text]` lines (every command sent to the picture and the voice, every log line, every event, a few query results); the logs must be identical.

    CARGO_TARGET_DIR=~/.cache/singws-rust-target-karaoke-proto python3 tools/karaoke_transport_parity.py [--cli PATH] [--show NAME] [CASES.json]

Needs a Python with PyQt6 importable (no QApplication and no display are used). Nothing here touches ~/SingWS. Ties between two timers due at the same instant are
ordered shorter interval first (Qt leaves that undefined); the world (picture/voice positions) advances on a 10 ms grid before the operations of each tick.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mpv_karaoke_transport as mkt  # noqa: E402

BASE_US = 1_000_000_000
GRID_US = 10_000


class Clock:
    def __init__(self):
        self.t_us = 0

    def monotonic(self):
        return (BASE_US + self.t_us) / 1e6


class Sched:
    def __init__(self, clock):
        self.clock, self.timers, self.seq = clock, [], 0

    def add(self, t):
        self.seq += 1
        t.seq = self.seq
        self.timers.append(t)

    def run_until(self, until_us):
        while True:
            due = [t for t in self.timers if t.due <= until_us]
            if not due:
                break
            t = min(due, key=lambda x: (x.due, x.interval_us, x.seq))
            self.clock.t_us = t.due
            if t.repeat:
                t.due += t.interval_us
            else:
                self.timers.remove(t)
                t.active = False
            t.fire()
        self.clock.t_us = until_us


class _Sig:
    def __init__(self):
        self.cb = None

    def connect(self, cb):
        self.cb = cb


SCHED = None


class FakeQTimer:
    def __init__(self, parent=None):
        self.interval_us, self.active, self.repeat = 0, False, True
        self.timeout = _Sig()
        self.due = 0
        self.fire = lambda: self.timeout.cb()

    def setInterval(self, ms):
        self.interval_us = int(ms) * 1000
        if self.active:
            self.start()

    def start(self):
        if self.active:
            SCHED.timers.remove(self)
        self.due = SCHED.clock.t_us + self.interval_us
        self.active = True
        SCHED.add(self)

    def stop(self):
        if self.active:
            SCHED.timers.remove(self)
        self.active = False

    def deleteLater(self):
        self.stop()

    @staticmethod
    def singleShot(ms, cb):
        t = FakeQTimer()
        t.repeat, t.interval_us = False, int(ms) * 1000
        t.due = SCHED.clock.t_us + t.interval_us
        t.active = True
        t.fire = cb
        SCHED.add(t)


def _interval(self):
    return self.interval_us // 1000


FakeQTimer.interval = _interval   # QTimer.interval() is in ms


class World:
    def __init__(self, case, clock):
        self.c, self.clock, self.log = case, clock, []
        self.p_dur = 0
        self.p_dur_on_load = case.get("duration_ms", 200_000)
        self.p_pos_us, self.p_playing, self.p_end = 0, False, False
        self.p_end_ms = case.get("end_ms")
        self.p_visuals, self.p_load_ok = True, case.get("picture_load_ok", True)
        self.p_tempo_pct, self.p_seek_holding = 100, False
        self.v_ready, self.v_pos_us, self.v_playing, self.v_moves = False, 0, False, True
        self.v_resync, self.v_duration = None, case.get("voice_duration", 200.0)

    def note(self, s):
        self.log.append([self.clock.t_us // 1000, s])


def b(x):
    return "true" if x else "false"


class FakePlugin:
    def __init__(self, w, offset_supported):
        self.w = w
        if offset_supported:
            self.setVideoOffsetMs = self._offset

    def _offset(self, v):
        self.w.note(f"p.offset {int(v)}")

    def loadSingWSMedia(self, source, audio, autoplay=True, semitones=0, tempo_percent=100):
        w = self.w
        w.note(f"p.load {source} {audio if audio else '-'} autoplay={b(autoplay)} semi={semitones} tempo={tempo_percent}")
        w.p_dur, w.p_playing, w.p_pos_us, w.p_tempo_pct = w.p_dur_on_load, bool(autoplay), 0, tempo_percent
        return w.p_load_ok

    def errorString(self):
        return "mpv media load failed"

    def seekMedia(self, ms):
        self.w.p_pos_us = ms * 1000
        self.w.note(f"p.seek {ms}")

    def pauseMedia(self):
        self.w.p_playing = False
        self.w.note("p.pause")

    def playMedia(self):
        self.w.p_playing = True
        self.w.note("p.play")

    def stopMedia(self):
        self.w.p_playing = False
        self.w.note("p.stop")

    def setVolume(self, v):
        self.w.note(f"p.volume {v:.3f}")

    def setTempoRatio(self, r):
        self.w.p_tempo_pct = int(round(r * 100.0))
        self.w.note(f"p.tempo {r:.3f}")

    def setPitchSemitones(self, k):
        self.w.note(f"p.pitch {k:.3f}")

    def setShadowAudio(self, on):
        self.w.note(f"p.shadow {b(on)}")
        return True

    def isSeekHolding(self):
        return self.w.p_seek_holding

    def isPlaying(self): return self.w.p_playing
    def positionMs(self): return self.w.p_pos_us // 1000
    def durationMs(self): return self.w.p_dur
    def visualsReady(self): return self.w.p_visuals
    def atEnd(self): return self.w.p_end


class FakeVoice:
    def __init__(self, w):
        self.w = w

    def load(self, path, norm_gain=1.0, paused=True, volume=1.0):
        if self.w.c.get("voice_load_error"):
            raise RuntimeError(self.w.c["voice_load_error"])
        self.w.note(f"v.load {path} norm={norm_gain:.3f} {'paused' if paused else 'playing'} vol={volume:.3f}")

    def set_key_semitones(self, k): self.w.note(f"v.key {k:.3f}")
    def set_tempo_percent(self, t): self.w.note(f"v.tempo {t:.3f}")

    def seek_seconds(self, s):
        self.w.v_ready = False
        self.w.v_pos_us = int(round(s * 1e6))
        self.w.note(f"v.seek {s:.3f}")

    def play(self): self.w.v_playing = True; self.w.note("v.play")
    def pause(self): self.w.v_playing = False; self.w.note("v.pause")
    def stop(self): self.w.v_playing = False; self.w.note("v.stop")
    def set_volume(self, v): self.w.note(f"v.volume {v:.3f}")
    def slide_volume(self, v, ms): self.w.note(f"v.slide {v:.3f} {ms}")
    def set_norm_gain(self, g): self.w.note(f"v.norm {g:.3f}")
    def set_eq_gains(self, on, g): self.w.note(f"v.eq {b(on)} {len(list(g))}")
    def set_master_params(self, on, p): self.w.note(f"v.master {b(on)} {b(bool(p))}")
    def slave_to(self, pos, rate): self.w.note(f"v.slave {pos:.3f} {rate:.3f}")
    def is_ready(self): return self.w.v_ready
    def position_seconds(self): return self.w.v_pos_us / 1e6
    def resync_info(self): return self.w.v_resync
    def duration_seconds(self): return self.w.v_duration


def world_set(w, o):
    for k, v in o.items():
        if k == "voice_ready": w.v_ready = v
        elif k == "voice_pos": w.v_pos_us = int(round(v * 1e6))
        elif k == "voice_moves": w.v_moves = v
        elif k == "picture_duration_ms": w.p_dur = v
        elif k == "picture_pos_ms": w.p_pos_us = v * 1000
        elif k == "picture_playing": w.p_playing = v
        elif k == "at_end": w.p_end = v
        elif k == "visuals": w.p_visuals = v
        elif k == "seek_holding": w.p_seek_holding = v
        elif k == "resync": w.v_resync = tuple(v)
        else: raise KeyError(k)


def run_python(case):
    """Run one scenario through the real Python transport, then put back everything the run replaced on the shared modules (QTimer, time, print,
    cdg_seek_guard.load_gap), so a later test in the same process sees the real ones."""
    _missing = object()
    saved = {
        "QTimer": getattr(mkt, "QTimer", _missing),
        "time": getattr(mkt, "time", _missing),
        "print": mkt.__dict__.get("print", _missing),
        "load_gap": mkt.cdg_seek_guard.load_gap,
    }
    try:
        return _run_python(case)
    finally:
        for name in ("QTimer", "time", "print"):
            if saved[name] is _missing:
                mkt.__dict__.pop(name, None)
            else:
                setattr(mkt, name, saved[name])
        mkt.cdg_seek_guard.load_gap = saved["load_gap"]


def _run_python(case):
    global SCHED
    clock = Clock()
    SCHED = Sched(clock)
    mkt.QTimer = FakeQTimer
    mkt.time = clock
    w = World(case, clock)
    zones = [tuple(z) for z in case.get("zones", [])]
    mkt.cdg_seek_guard.load_gap = lambda path: zones
    mkt.print = lambda *a: w.note("print: " + " ".join(str(x) for x in a))
    plugin = FakePlugin(w, case.get("offset_supported", False))
    t = mkt.MpvKaraokeTransport(plugin, audio_path=case.get("audio", ""), video_path=case.get("video", ""), mode=case.get("mode", "cdg"))
    t._voice_log = lambda m: w.note("log:" + m)   # the app sets this to its own log for EVERY song (also one with no voice), so the seek-guard line always reaches the log
    if case.get("voice", True):
        t.attach_voice(FakeVoice(w), log=lambda m: w.note("log:" + m))
    t.started.connect(lambda: w.note("event:started"))
    t.ended.connect(lambda: w.note("event:ended"))
    t.visual_stalled.connect(lambda p, ms: w.note(f"event:visual_stalled {p} {ms}"))
    steps = case.get("steps", [])
    tt = 0
    while tt <= case.get("run_ms", 1000) * 1000:
        clock.t_us = tt
        if tt > 0:
            if w.p_playing:
                w.p_pos_us += GRID_US * w.p_tempo_pct // 100
            if w.v_playing and w.v_moves:
                w.v_pos_us += GRID_US
            if w.p_end_ms is not None and w.p_pos_us // 1000 >= w.p_end_ms:
                w.p_end = True
        for st in [s for s in steps if s.get("t", 0) * 1000 == tt]:
            op = st.get("op", "world")
            if op == "world": world_set(w, st["set"])
            elif op == "start":
                try:
                    t.start(st.get("start", 0.0))
                except RuntimeError as exc:
                    w.note(f"raised:{exc}")
            elif op == "stop": t.stop()
            elif op == "pause": t.pause()
            elif op == "resume": t.resume()
            elif op == "seek": t.seek(st.get("seconds", 0.0))
            elif op == "modifiers": t.set_modifiers(st.get("tempo", 1.0), st.get("semitones", 0.0))
            elif op == "volume": t.set_volume(st.get("value", 1.0))
            elif op == "fade":
                cb = (lambda: w.note("event:fade_finished")) if st.get("cb", True) else None
                w.note(f"ret fade {b(t.fade_out(st.get('ms', 0), cb))}")
            elif op == "loop": t.set_loop(st.get("start", 0.0), st.get("end", 0.0))
            elif op == "poll_ms": t.set_visual_timer_interval_ms(st.get("ms", 50))
            elif op == "offset": t.set_video_offset_ms(st.get("ms", 0))
            elif op == "audio_spec":
                spec = {"normalize_gain_db": st.get("gain_db", 0.0), "eq_enabled": st.get("eq_enabled", False), "eq_gains_db": st.get("eq_gains", []),
                        "master_enabled": st.get("master_enabled", False)}
                if st.get("master_params"):
                    spec["master_params"] = {"gate_threshold_db": -50.0}
                t.apply_audio_spec(spec)
            elif op == "query_paused": w.note(f"q.is_paused {b(t.is_paused())}")
            else: raise KeyError(op)
        SCHED.run_until(tt)
        tt += GRID_US
    return w.log


def find_cli(arg):
    if arg:
        return arg
    base = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-karaoke-proto")
    return os.path.join(base, "release", "singws-karaoke-proto-cli")


def run_rust(cli, cases_path):
    out = subprocess.run([cli, str(cases_path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main(argv):
    cli = None
    show = None
    path = ROOT / "docs/design/karaoke_transport_cases.json"
    it = iter(argv)
    for a in it:
        if a == "--cli": cli = next(it)
        elif a == "--show": show = next(it)
        else: path = Path(a)
    cases = json.loads(path.read_text())
    rust = run_rust(find_cli(cli), path)
    bad = 0
    for c in cases:
        py = run_python(c)
        rs = rust[c["name"]]
        same = py == rs
        print(("IDENTICAL " if same else "DIFFERENT ") + c["name"] + f"  ({len(py)} lines)")
        if show == c["name"]:
            for line in py: print("  py", line)
        if not same:
            bad += 1
            for i in range(max(len(py), len(rs))):
                a_ = py[i] if i < len(py) else None
                b_ = rs[i] if i < len(rs) else None
                if a_ != b_:
                    print(f"   first difference at line {i}: python={a_} rust={b_}")
                    break
    print("ALL IDENTICAL" if not bad else f"{bad} DIFFERENT")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
