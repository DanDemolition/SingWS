#!/usr/bin/env python3
"""Run scripted and seeded-random scenarios through the REAL Python ``BackgroundMusicPlayer`` (0.2.18.1.py) with a recording fake
engine on a virtual clock, and write two things per scenario:

  * ``replay``   the inputs the player saw (every event with the engine snapshot it could read), for the Rust ``singws-bgm-policy`` CLI
  * ``expected`` the engine/host commands the player issued, the single-shot timers it asked for, its state and return value, per event

``tools/bgm_policy_parity.py`` feeds ``replay`` to the Rust CLI and compares with ``expected``. Nothing here changes the app: the real
class runs unmodified; only the engine, the host window, QTimer, threading and the clock are replaced for the run.
Design and rules: docs/design/stage-e-bgm-policy.md. Run with the app's venv (needs PyQt6); SINGWS_HOME is forced to a scratch dir.
"""
from __future__ import annotations

import contextlib
import copy
import heapq
import importlib.util
import io
import json
import os
import random
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
T0_MS = 100_000          # virtual monotonic clock starts here (never 0: the silence detector treats 0 as "no previous tick")
_MAIN = None


def load_app():
    global _MAIN
    if _MAIN is not None:
        return _MAIN
    home = os.environ.get("SINGWS_HOME", "")
    if not home or Path(home).expanduser().resolve() == (Path.home() / "SingWS").resolve():
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-bgm-policy-")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("singws_main_bgm_policy", REPO / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    _MAIN = module
    return module


# --------------------------------------------------------------------------------------------------------------------- world
class Deck:
    def __init__(self, world, path, pos0, playing):
        world.deck_seq += 1
        self.id = world.deck_seq
        world.keep_alive.append(self)      # the player keys its preload dedupe on id(deck): a freed deck's address can be reused (see the design doc)
        self.path = path
        self.world = world
        self.pos0 = float(pos0)
        self.t_ref = world.now_ms
        self.playing = bool(playing)

    def pos(self):
        dur = self.world.durations.get(self.path, 30.0)
        p = self.pos0 + ((self.world.now_ms - self.t_ref) / 1000.0 if self.playing else 0.0)
        return min(p, dur)

    def freeze(self):
        self.pos0 = self.pos()
        self.t_ref = self.world.now_ms
        self.playing = False

    def resume(self):
        self.pos0 = self.pos()
        self.t_ref = self.world.now_ms
        self.playing = True


class Prepared:
    def __init__(self, path):
        self.path = path


class FakeEngine:
    """Recording stand-in for RustBackgroundEngine: the methods BackgroundMusicPlayer calls, with a tiny time model."""
    backend_name = "Fake"

    def __init__(self, world):
        self.w = world
        self.primary = None
        self.secondary = None
        self.master_volume = 0.0
        self._paused = False
        self._xf_end_ms = None

    # ---- recording helpers
    def _r(self, name, *args):
        self.w.cmd(name, list(args))

    def _resp(self, name, default=True):
        v = self.w.responses.get(name, default)
        if isinstance(v, dict) and "raise" in v:
            raise RuntimeError(v["raise"])
        return v

    # ---- state reads
    def get_times(self):
        if self.primary is None:
            return 0.0, 0.0
        return self.primary.pos(), float(self.w.durations.get(self.primary.path, 30.0))

    def source_ended(self):
        if self.primary is None:
            return False
        dur = float(self.w.durations.get(self.primary.path, 30.0))
        return self.primary.playing and self.primary.pos() >= dur

    def crossfade_finished(self):
        return self._xf_end_ms is not None and self.w.now_ms >= self._xf_end_ms

    def crossfade_lead_seconds(self):
        return float(self.w.lead_s)

    def meter_level(self):
        return float(self.w.meter_target)

    def is_playing(self):
        return self.primary is not None and self.primary.playing

    def is_paused(self):
        return self._paused

    def fade_settle_delay_ms(self):
        return int(self.w.settle_ms)

    def _effective_master(self):
        return self.master_volume

    # ---- commands
    def set_master_volume(self, v):
        self._r("set_master_volume", float(v))
        self.master_volume = float(v)

    def slide_master_volume(self, v, ms):
        self._r("slide_master_volume", float(v), int(ms))
        self.master_volume = float(v)      # the model completes the slide at once; snapshots show the end value

    def load(self, path, paused=False, volume=None):
        self._r("load", str(path), bool(paused))
        self._resp("load")
        self.primary = Deck(self.w, str(path), 0.0, not paused)
        self._paused = bool(paused)

    def play(self):
        self._r("play")
        ok = bool(self._resp("play"))
        if ok and self.primary is not None:
            self.primary.resume()
            self._paused = False
        return ok

    def pause(self):
        self._r("pause")
        if self.primary is not None:
            self.primary.freeze()
        self._paused = True

    def stop(self):
        self._r("stop")
        self.primary = None
        self.secondary = None
        self._xf_end_ms = None
        self._paused = False

    def seek(self, seconds):
        self._r("seek", float(seconds))
        return True

    def start_crossfade(self, path, ms, norm_gain=1.0, start_seconds=0.0):
        self._r("start_crossfade", str(path), int(ms), float(norm_gain), float(start_seconds))
        ok = bool(self._resp("start_crossfade"))
        if ok:
            self.secondary = Deck(self.w, str(path), start_seconds, True)
            self._xf_end_ms = self.w.now_ms + int(ms)
        return ok

    def complete_crossfade(self):
        self._r("complete_crossfade")
        ok = bool(self._resp("complete_crossfade"))
        if ok and self.secondary is not None:
            self.primary = self.secondary
            self.secondary = None
            self._xf_end_ms = None
        return ok

    def cancel_crossfade(self):
        self._r("cancel_crossfade")
        self.secondary = None
        self._xf_end_ms = None

    def set_primary_normalize_gain(self, v):
        self._r("set_primary_normalize_gain", float(v))

    def set_secondary_normalize_gain(self, v):
        self._r("set_secondary_normalize_gain", float(v))

    def prepare_primary(self, path, norm_gain=1.0):
        self._r("prepare_primary", str(path), float(norm_gain))
        queue = self.w.prepare_results.get(str(path))
        result = queue.pop(0) if queue else "ok"
        self.w.prepare_outcome[self.w.current_spawn] = result
        if result != "ok":
            raise RuntimeError(result)
        return Prepared(str(path))

    def install_prepared_secondary(self, prepared):
        self._r("install_prepared_secondary", prepared.path)
        if not self._resp("install_prepared_secondary"):
            raise RuntimeError("attach refused")
        self.secondary = Deck(self.w, prepared.path, 0.0, False)

    def install_prepared_primary(self, prepared, volume=None):
        self._r("install_prepared_primary", prepared.path, float(volume))
        ok = bool(self._resp("install_prepared_primary"))
        if ok:
            self.primary = Deck(self.w, prepared.path, 0.0, False)
            self._paused = True
        return ok

    def discard_prepared_primary(self, prepared):
        self._r("discard_prepared_primary", prepared.path)

    def invalidate_secondary_preload(self):
        self._r("invalidate_secondary_preload")
        if self.secondary is not None and not self.w.engine_xf_active():
            self.secondary = None

    def set_fade_in_floor_db(self, db):
        self._r("set_fade_in_floor_db", float(db))
        return True

    def close(self):
        pass

    def __getattr__(self, name):
        if name.startswith("set_") or name in ("pad_play",):
            if name == "pad_play":
                raise AttributeError(name)

            def other(*a, **k):
                return True
            return other
        raise AttributeError(name)


class FakeHost:
    pass


class World:
    def __init__(self, scenario):
        self.sc = scenario
        self.now_ms = T0_MS
        self.seq = 0
        self.heap = []
        self.deck_seq = 0
        self.log = []                    # commands of the current event
        self.durations = {}
        self.lead_s = scenario.get("lead_s", 0.0)
        self.settle_ms = scenario.get("settle_ms", 0)
        self.meter_target = 0.5
        self.responses = {}
        self.prepare_results = {k: list(v) for k, v in scenario.get("prepare_results", {}).items()}
        self.records = scenario.get("records", {})
        self.loud = scenario.get("loud", {})
        self.preload_latency_ms = scenario.get("preload_latency_ms", 40)
        self.spawns = 0
        self.dir = None
        self.prepare_outcome = {}
        self.keep_alive = []
        self.current_spawn = None
        self.real_threading = None
        self.real_time = None
        self.timer_args = {}

    def cmd(self, name, args):
        self.log.append({"c": name, "a": args})

    def engine_xf_active(self):
        return self.engine._xf_end_ms is not None

    def schedule(self, delay_ms, kind, payload):
        self.seq += 1
        heapq.heappush(self.heap, (self.now_ms + int(delay_ms), self.seq, kind, payload))


def classify_timer(cb):
    import inspect
    if inspect.ismethod(cb):
        return cb.__name__, []
    code = cb.__code__
    names = [n for n in code.co_names if n.startswith("_")]
    kind = names[0] if names else cb.__name__
    return kind, list(cb.__defaults__ or ())


# ----------------------------------------------------------------------------------------------------------------- run one
def build_player(main, world):
    from PyQt6.QtCore import QObject

    class Host(QObject):
        def __init__(h):
            super().__init__()
            h.settings = {k: v for k, v in world.sc["settings"].items() if k not in world.sc.get("drop_settings", ())}
            h._host_bgm_live_volume = world.sc.get("live_volume")
            h.karaoke_playing = False
            h.bg_manager = None

        def update_bg_track_display(h):
            world.cmd("host:update_bg_track_display", [])

        def update_bg_button_state(h):
            world.cmd("host:update_bg_button_state", [])

        def _run_on_ui_thread(h, fn):
            world.schedule(world.preload_latency_ms, "finish", (world.current_spawn, fn))

    host = Host()
    world.host = host

    class FakeQTimer:
        def __init__(self, *a, **k):
            self.timeout = types.SimpleNamespace(connect=lambda fn: None)

        def start(self, ms=None):
            pass

        def stop(self):
            pass

        def setInterval(self, ms):
            pass

        def isActive(self):
            return False

        @staticmethod
        def singleShot(ms, cb):
            kind, args = classify_timer(cb)
            if "_probe" in getattr(getattr(cb, "__code__", None), "co_freevars", ()):
                return          # play()'s diagnostic volume probes only write log lines; they decide nothing
            world.cmd("timer", [int(ms), kind, args])
            world.schedule(int(ms), "timer", (kind, args, cb))

    class FakeThread:
        def __init__(self, target=None, daemon=None, name=None):
            self.target = target
            self.name = name or ""

        def start(self):
            world.spawns += 1
            n = world.spawns
            kind = "startup" if "startup" in self.name else "next"
            world.cmd("spawn", [kind, n])
            world.current_spawn = n
            self.target()                       # the worker body runs now; its result is delivered by the later "finish" event
            world.current_spawn = None

    class FakeTime(types.SimpleNamespace):
        pass

    main.QTimer = FakeQTimer
    main.threading = types.SimpleNamespace(**{**vars(world.real_threading), "Thread": FakeThread})
    main.time = types.SimpleNamespace(**{**vars(world.real_time), "monotonic": lambda: world.now_ms / 1000.0,
                                         "time": lambda: world.now_ms / 1000.0, "sleep": lambda s: None,
                                         "perf_counter": lambda: world.now_ms / 1000.0})

    def transition_analysis_cached(path):
        rec = world.records.get(str(path))
        if rec is None:
            return None
        ta = main.transition_analysis.TransitionAnalysis(
            path=str(path), mtime=0, size=0, media_kind="audio",
            duration=rec["duration"], hop_seconds=rec.get("hop_seconds", 0.1),
            audio_start=rec.get("audio_start"), audio_end=rec.get("audio_end"), fade_start=rec.get("fade_start"),
            fade_confidence=rec.get("fade_confidence", 0.0), analysis_version=rec.get("analysis_version", main.transition_analysis.TRANSITION_ANALYSIS_VERSION))
        return ta

    main.transition_analysis_cached = transition_analysis_cached

    def loudness_info_cached(path):
        info = world.loud.get(str(path))
        return dict(info) if info else None

    main.loudness_info_cached = loudness_info_cached
    main.analyze_loudness_async = lambda path: world.cmd("analyze_loudness_async", [str(path)])
    main._plan_shadow_for = lambda settings: None
    main._diag = lambda msg: None

    def init_engine(self, output_name=None):
        world.engine = FakeEngine(world)
        self._bgm_engine = world.engine
        return True

    saved = main.BackgroundMusicPlayer._init_bgm_engine
    main.BackgroundMusicPlayer._init_bgm_engine = init_engine
    try:
        player = main.BackgroundMusicPlayer(host)
    finally:
        main.BackgroundMusicPlayer._init_bgm_engine = saved
    return player, host


def stat_sig(path):
    try:
        st = os.stat(path)
        return [True, int(st.st_size), int(st.st_mtime_ns)]
    except OSError:
        return [False, None, None]


def snapshot(world, player, host):
    eng = world.engine
    pos, dur = eng.get_times()
    files = {p: stat_sig(p) for p in world.paths}
    return {
        "primary": eng.primary.id if eng.primary else None,
        "secondary": eng.secondary.id if eng.secondary else None,
        "pos": pos, "dur": dur,
        "source_ended": eng.source_ended(),
        "xf_finished": eng.crossfade_finished(),
        "lead": world.lead_s,
        "meter": world.meter_target,
        "engine_playing": eng.is_playing(),
        "engine_paused": eng.is_paused(),
        "master": eng.master_volume,
        "settle_ms": world.settle_ms,
        "settings": {k: host.settings[k] for k in host.settings},
        "live_volume": host._host_bgm_live_volume,
        "karaoke": bool(host.karaoke_playing),
        "files": files,
        "records": {p: world.records.get(p) for p in world.paths},
        "loud": {p: world.loud.get(p) for p in world.paths},
        "responses": copy.deepcopy(world.responses),
    }


def player_state(player):
    return {
        "current_index": int(player.current_index),
        "is_playing": bool(player.is_playing),
        "crossfade_active": bool(player.crossfade_active),
        "crossfade_target": player._crossfade_target_index,
        "stop_after_current": bool(player.stop_after_current),
        "volume": float(player.volume),
        "enabled": bool(player.enabled),
        "meter": float(player._meter_level),
        "silence_accum": float(player._bg_silence_accum_s),
        "fade_gen": int(player._bgm_fade_generation),
        "xf_gen": int(player._bgm_crossfade_generation),
        "startup_gen": int(player._startup_preload_generation),
        "startup_in_progress": bool(player._startup_preload_in_progress),
        "next_preload_in_progress": bool(player._next_preload_in_progress),
        "realign": bool(player._pipeline_realign_needed),
        "unplayable": sorted(player._bg_unplayable_signatures.keys()),
    }


def dispatch(player, host, world, ev):
    k = ev["ev"]
    a = ev.get("a", {})
    if k == "tick":
        return player._check_track_position()
    if k == "play":
        return player.play()
    if k == "pause":
        return player.pause()
    if k == "stop":
        return player.stop()
    if k == "stop_with_fade":
        return player.stop_with_fade(*([a["ms"]] if "ms" in a else []))
    if k == "next":
        return player.next_track()
    if k == "prev":
        return player.previous_track()
    if k == "fade_out":
        return player.fade_out(a.get("ms", 1000))
    if k == "fade_in":
        return player.fade_in(a.get("target"), a.get("ms", 1000), a.get("allow", False))
    if k == "ensure_audible":
        return player.ensure_audible("test")
    if k == "cancel_pending":
        return player.cancel_pending_fades(a.get("restore", True))
    if k == "set_volume":
        return player.set_volume(a["v"])
    if k == "stop_after":
        return player.set_stop_after_current(a["v"])
    if k == "enabled":
        return player.set_enabled(a["v"])
    if k == "preload":
        return player.preload_current_track_paused()
    if k == "preload_async":
        return player.preload_current_track_paused_async()
    if k == "init_volume":
        return player.initialize_startup_volume()
    if k == "attach":
        return player._attach_host_audio_chain()
    if k == "playlist":
        player.playlist = list(a["paths"])
        player.current_index = a["index"]
        player._pipeline_realign_needed = bool(a.get("realign", False))
        return None
    if k == "set":              # world mutation: settings / karaoke / meter / responses / files
        if "settings" in a:
            host.settings.update(a["settings"])
        if "live_volume" in a:
            host._host_bgm_live_volume = a["live_volume"]
        if "karaoke" in a:
            host.karaoke_playing = a["karaoke"]
        if "meter" in a:
            world.meter_target = a["meter"]
        if "lead" in a:
            world.lead_s = a["lead"]
        if "settle_ms" in a:
            world.settle_ms = a["settle_ms"]
        for name, v in a.get("responses", {}).items():
            if v is None:
                world.responses.pop(name, None)
            else:
                world.responses[name] = v
        for path, how in a.get("files", {}).items():
            if how == "delete":
                if os.path.exists(path):
                    os.unlink(path)
            elif how == "utime":      # same size, new mtime
                st = os.stat(path)
                os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
            elif how == "touch":
                with open(path, "ab") as fh:
                    fh.write(b"x")
        for path, results in a.get("prepare_results", {}).items():
            world.prepare_results[path] = list(results)
        return None
    raise ValueError(k)


def map_names(obj, names):
    """'@name' tokens in a script refer to tracks; the real temp paths exist only inside the run."""
    if isinstance(obj, str) and obj.startswith("@") and obj[1:] in names:
        return names[obj[1:]]
    if isinstance(obj, list):
        return [map_names(o, names) for o in obj]
    if isinstance(obj, dict):
        return {(map_names(k, names) if isinstance(k, str) else k): map_names(v, names) for k, v in obj.items()}
    return obj


# events a Rust policy must be told about (everything the world feeds the player)
def run(scenario):
    main = load_app()
    saved = (main.QTimer, main.threading, main.time, main.transition_analysis_cached, main.loudness_info_cached,
             main.analyze_loudness_async, main._plan_shadow_for, main._diag)
    tmp = tempfile.TemporaryDirectory(prefix="bgm-policy-files-")
    try:
        world = World(scenario)
        world.real_threading, world.real_time = saved[1], saved[2]
        world.dir = tmp.name
        # real (empty) files for every track so the unplayable-signature logic runs against the real filesystem
        paths = []
        durations, records, loud, prepare = {}, {}, {}, {}
        for i, tr in enumerate(scenario["tracks"]):
            p = os.path.join(tmp.name, f"{tr['name']}.mp3")
            with open(p, "wb") as fh:
                fh.write(b"x" * (10 + i))
            paths.append(p)
            durations[p] = tr["duration"]
            if tr.get("record"):
                records[p] = dict(tr["record"], duration=tr["record"].get("duration", tr["duration"]))
            if tr.get("loud"):
                loud[p] = tr["loud"]
            if tr.get("prepare"):
                prepare[p] = list(tr["prepare"])
        world.paths = paths
        names = {tr["name"]: p for tr, p in zip(scenario["tracks"], paths)}
        for nm, info in scenario.get("loud_by_name", {}).items():
            loud[names[nm]] = info
        for nm, res in scenario.get("prepare_results", {}).items():
            prepare[names[nm]] = list(res)
        scenario = dict(scenario, script=map_names(scenario["script"], names))
        world.durations, world.records, world.loud, world.prepare_results = durations, records, loud, prepare
        with contextlib.redirect_stdout(io.StringIO()):
            player, host = build_player(main, world)
            player.playlist = list(paths)
            player.current_index = scenario.get("start_index", 0)
            player.crossfade_duration_ms = scenario.get("crossfade_ms", 5000)
            player.volume = scenario.get("volume", 0.8)

            replay = {"paths": paths, "start_index": player.current_index, "crossfade_ms": player.crossfade_duration_ms,
                      "volume": player.volume, "init": snapshot(world, player, host), "events": []}
            expected = []

            for ev in scenario["script"]:
                world.schedule(ev["at"], "script", ev)
            end_ms = T0_MS + int(scenario["run_ms"])
            n = 1
            while T0_MS + 500 * n <= end_ms:
                if not any(a * 1000 <= 500 * n < b * 1000 for a, b in scenario.get("tick_skips", [])):
                    world.schedule(500 * n, "tick", {"ev": "tick"})
                n += 1
            # schedule() is relative to now_ms (= T0_MS here), so the absolute times above are right

            while world.heap:
                t, _seq, kind, payload = heapq.heappop(world.heap)
                if t > end_ms:
                    break
                world.now_ms = t
                world.log = []
                record = {"t": t}
                snap = snapshot(world, player, host)
                if kind in ("tick", "script"):
                    ev = payload
                    record.update({"ev": ev["ev"], "a": ev.get("a", {})})
                    try:
                        ret = dispatch(player, host, world, ev)
                    except Exception as exc:        # an uncaught exception in a Qt slot: reported by the parity tool
                        ret = "EXC:" + type(exc).__name__
                elif kind == "timer":
                    tk, targs, cb = payload
                    record.update({"ev": "timer", "a": {"kind": tk, "args": targs}})
                    if tk == "_start_artwork_precache":
                        world.cmd("host:artwork_precache", [])      # starts a real thread in the app; not part of the policy
                        ret = None
                    else:
                        ret = cb()
                elif kind == "finish":
                    # nth spawn: the order of finish events equals the order of spawns at equal latency; resolve by the closure's spawn number
                    spawn_no, fn = payload
                    outcome = world.prepare_outcome.get(spawn_no, "ok")
                    record.update({"ev": "finish", "a": {"spawn": spawn_no, "error": "" if outcome == "ok" else outcome}})
                    ret = fn()
                else:
                    raise ValueError(kind)
                # the world after the call (decks changed by commands) is invisible to the policy except through the next snapshot
                record["snap"] = snap
                replay["events"].append(record)
                expected.append({"t": t, "cmds": world.log, "state": player_state(player),
                                 "ret": ret if isinstance(ret, (bool, int, float, str, type(None))) else None})
    finally:
        (main.QTimer, main.threading, main.time, main.transition_analysis_cached, main.loudness_info_cached,
         main.analyze_loudness_async, main._plan_shadow_for, main._diag) = saved
        tmp.cleanup()
    return {"replay": replay, "expected": expected}


if __name__ == "__main__":
    sc = json.load(open(sys.argv[1]))
    out = run(sc)
    json.dump(out, sys.stdout)
