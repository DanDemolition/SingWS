#!/usr/bin/env python3
"""Run scripted and seeded-random scenarios through the REAL Python song-end hand-off code of 0.2.18.1.py (the host-side decisions that start the
background music at the end of a karaoke song) on a bare ``KaraokeApp`` with fakes for the player, the transport clock and the settings.

Real, unmodified methods under test: ``update_time_left``, ``_maybe_trim_end_silence``, ``_prefire_bgm_at_verified_audio_end``,
``_arm_audio_end_floor`` / ``_apply_audio_end_floor``, ``_arm_visual_end_floor`` / ``_apply_visual_end_floor`` / ``_apply_visual_end_values``,
``_effective_karaoke_duration_ns``, ``_setup_end_silence_state`` / ``_reset_end_silence_state``, ``_handle_media_end_safe``,
``_schedule_media_end_cleanup`` / ``_run_media_end_cleanup`` / ``_cancel_pending_media_end_cleanup`` / ``_finish_media_end_cleanup``,
``_schedule_bg_resume`` / ``_on_bg_resume_timer``, ``_start_bg_with_fade[_safe]``, ``_verify_bg_resume_started``,
``_auto_play_next_if_generation`` and the one-line KaraFun hand-off ``_schedule_bg_resume(120, reason="external_karafun_complete")``
(the surrounding ``_finish_external_karafun_playback`` is NOT driven: it is queue/history work; only its BGM hand-off is in scope).

Replaced by recording stand-ins: the BackgroundMusicPlayer (``bg_music``: its own policy is proven in tools/bgm_policy_parity.py), the transport
clock, QTimer (virtual clock), threading (scan workers), time, and the host-window collaborators that do UI/queue work in the cleanup
(stop_playback, clear_now_singing, ... -> recorded as ``host:*`` commands so their ORDER against the music start is checked).

Per event the harness records (a) the INPUT the Rust ``singws-songend`` crate gets (event + a snapshot of everything readable) and (b) the OUTPUT:
every command (bg/host calls, single-shot timers, QTimer start/stop, whitelisted log lines) plus the state afterwards. See
docs/design/stage-e-songend-handoff.md. Needs PyQt6; SINGWS_HOME is forced to a scratch dir.
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
T0_MS = 100_000
NS = 1_000_000_000
_MAIN = None
_QAPP = None


def load_app():
    global _MAIN
    if _MAIN is not None:
        return _MAIN
    home = os.environ.get("SINGWS_HOME", "")
    if not home or Path(home).expanduser().resolve() == (Path.home() / "SingWS").resolve():
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-songend-")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("singws_main_songend", REPO / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    from PyQt6.QtWidgets import QApplication
    global _QAPP
    _QAPP = QApplication.instance() or QApplication([])
    _MAIN = module
    return module


class World:
    def __init__(self, sc):
        self.sc = sc
        self.now_ms = T0_MS
        self.seq = 0
        self.heap = []
        self.log = []
        self.timers = {}
        self.cancelled = set()
        self.deliver_seq = 0

    def cmd(self, c, a=None):
        self.log.append({"c": c, "a": a if a is not None else []})

    def schedule(self, delay_ms, kind, payload):
        self.seq += 1
        item = (self.now_ms + int(delay_ms), self.seq, kind, payload)
        heapq.heappush(self.heap, item)
        return item[1]


class FakeTimer:
    """A QTimer INSTANCE of the app (single shot): start/stop are recorded, firing is an event."""
    def __init__(self, world, name, cb):
        self.w, self.name, self.cb, self.token = world, name, cb, None
        self.timeout = types.SimpleNamespace(connect=lambda fn: None)

    def start(self, ms=None):
        self.stop(record=False)
        self.w.cmd("timer_start", [self.name, int(ms)])
        self.token = self.w.schedule(int(ms), "qtimer", self)

    def stop(self, record=True):
        if record:
            self.w.cmd("timer_stop", [self.name])
        if self.token is not None:
            self.w.cancelled.add(self.token)
            self.token = None

    def setSingleShot(self, v):
        pass


class FakeTransport:
    def __init__(self, world):
        self.w = world
        self.last_level_ts = 0.0
        self.last_level_db = None

    def query_times_ns(self):
        t = self.w.times()
        if t is None:
            return None, None
        return t

    def engine_at_end(self):
        v = self.w.sc_state.get("engine_at_end")
        return v

    def cdg_lyrics_finished(self):
        v = self.w.sc_state.get("gate", True)
        if v == "raise":
            raise RuntimeError("gate")
        return v

    def cdg_generation(self):
        return self.w.sc_state.get("generation", 0)

    def cdg_sectors_remaining(self):
        return self.w.sc_state.get("sectors")


class FakeBg:
    def __init__(self, world):
        self.w = world
        self.playlist = ["a.mp3"]
        self.is_playing = False
        self.enabled = True

    def _starts(self, allow):
        st = self.w.sc_state
        if st.get("karaoke_playing") and not allow:
            return False
        return bool(self.enabled and st.get("fade_in_works", True))

    def fade_in(self, target=None, ms=1000, allow_during_karaoke=False):
        self.w.cmd("bg:fade_in", [target, int(ms), bool(allow_during_karaoke)])
        if self._starts(allow_during_karaoke):
            self.is_playing = True

    def is_effectively_playing(self):
        return bool(self.is_playing and self.w.sc_state.get("bg_audible", True))

    def stop(self):
        self.w.cmd("bg:stop")
        self.is_playing = False

    def preload_current_track_paused(self):
        self.w.cmd("bg:preload_current_track_paused")
        return True

    def ensure_audible(self, reason):
        self.w.cmd("bg:ensure_audible", [reason])

    def recover_failed_resume(self):
        self.w.cmd("bg:recover_failed_resume")
        return bool(self.w.sc_state.get("recover_ok", True))

    def get_current_track_info(self):
        return "t"


def classify(cb):
    import inspect
    if inspect.ismethod(cb):
        return cb.__name__, []
    if getattr(cb, "__name__", "") in HOST_STUBS:
        return cb.__name__, []
    code = cb.__code__
    names = [n for n in code.co_names if n.startswith("_")]
    kind = names[0] if names else cb.__name__
    if cb.__defaults__:
        return kind, list(cb.__defaults__)
    return kind, [c for c in code.co_consts if isinstance(c, str) and c != kind and "." not in c and not c.startswith("_")][:1]


KEEP = ("[END", "[CROSSFADE]", "[BG-HANDOFF]", "[TRANSITION]", "[TIME]", "[INTRO-LOOP]", "[PLAY-COMMAND]", "Karaoke track", "Finishing karaoke",
        "Starting background", "Cannot start background", "[BG]", "[ROTATION]", "[BG-ENGINE]")

SILENT_STUBS = ("_update_karaoke_pause_button", "_update_karaoke_seek_ui")
HOST_STUBS = ("_update_karaoke_pause_button", "_update_karaoke_seek_ui", "stop_playback", "_flush_deferred_remote_adds",
              "_mark_next_up_overlay_pending_after_completion", "_clear_next_up_overlay_pending", "clear_now_singing", "_detach_video_sinks_now",
              "_recreate_video_surfaces", "_show_idle_background_after_karaoke", "_commit_pending_performance", "_schedule_idle_full_gc",
              "_clear_bg_transition_timers", "update_bg_button_state", "play_next_file")


def build_app(main, world):
    app = main.KaraokeApp.__new__(main.KaraokeApp)
    from PyQt6.QtCore import QObject
    QObject.__init__(app)          # hasattr() on a QObject whose base was never initialised raises; the widget machinery stays unbuilt
    d = app.__dict__
    sc = world.sc
    d["settings"] = dict(sc["settings"])
    d["_end_silence_db_threshold"] = -38.0
    d["_end_silence_min_s"] = 2.5
    d["_end_silence_near_end_window_s"] = 20.0
    d["_end_silence_min_elapsed_s"] = 20.0
    d["_end_silence_confident_remain_s"] = 6.0
    d["_end_silence_confident_min_s"] = 0.7
    d["_end_silence_cdg_sector_threshold"] = 120
    d["_karaoke_end_overlap_ms"] = 3200
    d["karaoke_playing"] = False
    d["karaoke_transport"] = None
    d["bg_music"] = FakeBg(world)
    d["rotation_view"] = None
    d["_active_external_karafun"] = None
    d["_media_end_handoff_active"] = False
    d["_media_end_cleanup_gen"] = 0
    d["_scheduled_media_end_cleanup_gen"] = 0
    d["_media_end_cleanup_end_silence_triggered"] = False
    d["_media_end_cleanup_schedule_bg_resume"] = False
    d["_media_end_overlay_eligible"] = False
    d["_bg_resume_reason"] = "default"
    d["_bg_soundboard_resume_pending_reason"] = ""
    d["_bg_resume_verify_gen"] = 0
    d["_bg_resume_verify_retried"] = False
    d["_bg_resume_verify_recovered"] = False
    d["_confirmed_singer_start_generation"] = 0
    d["_pending_play_start_token"] = None
    d["_next_in_progress"] = False
    d["_stop_in_progress"] = False
    d["_bgm_session_started"] = bool(sc.get("bgm_session_started", True))
    if sc.get("karaoke_played_this_session"):
        d["_karaoke_played_this_session"] = True
    d["_current_karaoke_song_path"] = ""
    d["_get_time_left_text"] = lambda: ""
    d["_transport_shadow"] = lambda: None
    d["_should_start_intro_loop"] = lambda: bool(world.sc_state.get("intro_loop", False))
    d["_is_karaoke_paused"] = lambda: bool(world.sc_state.get("paused", False))
    def run_on_ui_thread(fn):
        world.deliver_seq += 1
        world.cmd("deliver_later", [world.deliver_seq])
        world.schedule(world.sc_state.get("deliver_ms", 40), "finish", (world.deliver_seq, fn))
    d["_run_on_ui_thread"] = run_on_ui_thread
    d["_get_duration_secs"] = lambda p: _dur_or_raise(world.sc_state.get("db_duration"))
    def make_stub(n):
        def stub(*a, **k):
            if n not in SILENT_STUBS:
                world.cmd("host:" + n, [k.get("reason")] if "reason" in k else [])
        stub.__name__ = n
        return stub
    for name in HOST_STUBS:
        d[name] = make_stub(name)
    d["_song_outro_payload_from_current"] = lambda: None

    def stop_playback(skip_confirmation=False):
        # stand-in for KaraokeApp.stop_playback (61923): the parts that touch the song-end state, in the real order
        world.cmd("host:stop_playback")
        app._cancel_pending_media_end_cleanup("stop_playback")
        app.karaoke_playing = False
        app._reset_end_silence_state()
        app.karaoke_transport = None
        world.sc_state["transport"] = False
        world.sc_state["karaoke_playing"] = False
    d["stop_playback"] = stop_playback
    # the three QTimer instances of the app
    d["_bg_resume_timer"] = FakeTimer(world, "bg_resume", app._on_bg_resume_timer)
    d["_media_end_handoff_timer"] = FakeTimer(world, "handoff_guard", app._clear_media_end_handoff_guard)
    d["_media_end_cleanup_timer"] = FakeTimer(world, "media_end_cleanup", app._run_media_end_cleanup)
    return app


def _dur_or_raise(v):
    if v == "raise":
        raise RuntimeError("no duration")
    return v


def times_for(world):
    st = world.sc_state
    if not st.get("transport"):
        return None
    dur = st["media_dur_ns"]
    if st.get("pos_override") is not None:
        return dur, st["pos_override"]
    pos = st["pos0_ns"]
    if st.get("playing_clock") and not st.get("paused"):
        pos += (world.now_ms - st["t_ref"]) * 1_000_000
    pos = min(pos, dur)
    if st.get("frozen_at") is not None:
        pos = st["frozen_at"]
    return dur, pos


def snapshot(world, app):
    st = world.sc_state
    bg = app.bg_music
    t = times_for(world)
    return {
        "settings": copy.deepcopy(app.settings),
        "times": list(t) if t else None,
        "level_db": (lambda tr: tr.last_level_db if tr is not None and (world.now_ms / 1000.0 - tr.last_level_ts) <= 1.5 else None)(app.karaoke_transport),
        "engine_at_end": st.get("engine_at_end"),
        "paused": bool(st.get("paused", False)),
        "gate": st.get("gate", True) if st.get("transport") else "missing",
        "generation": st.get("generation", 0),
        "sectors": st.get("sectors"),
        "karaoke_playing": bool(st.get("karaoke_playing", False)),
        "intro_loop": bool(st.get("intro_loop", False)),
        "bg": {"has": True, "playlist": bool(bg.playlist), "is_playing": bool(bg.is_playing), "enabled": bool(bg.enabled),
               "audible": bool(st.get("bg_audible", True)),
               "starts_normal": bg._starts(False), "starts_allow": bg._starts(True),
               "recover_ok": bool(st.get("recover_ok", True))},
        "session_started": bool(app.__dict__.get("_bgm_session_started", False)),
        "karaoke_played": bool(app.__dict__.get("_karaoke_played_this_session", False)),
        "gen_current": int(app.__dict__.get("_confirmed_singer_start_generation", 0)),
        "pending_token": bool(app.__dict__.get("_pending_play_start_token") is not None),
        "next_in_progress": bool(app.__dict__.get("_next_in_progress", False)),
        "stop_in_progress": bool(app.__dict__.get("_stop_in_progress", False)),
        "active_karafun": False,
        "db_duration": st.get("db_duration"),
        "arm": copy.deepcopy(st.get("arm")),
        "audio_true_duration": dict(app.__dict__.get("_audio_true_duration", {})),
    }


def state_of(app):
    d = app.__dict__
    g = lambda k, dflt=None: d.get(k, dflt)
    return {
        "audio_end_s": g("_karaoke_audio_end_s"), "audio_duration_s": g("_karaoke_audio_duration_s"),
        "audio_end_path": g("_karaoke_audio_end_path", ""),
        "visual_end_s": g("_karaoke_visual_end_s"), "visual_conf": g("_karaoke_visual_end_confidence", 0.0),
        "visual_path": g("_karaoke_visual_end_path", ""), "visual_reason": g("_karaoke_visual_end_reason", ""),
        "prefired": bool(g("_bg_crossfade_prefired", False)), "tail_started": bool(g("_end_silence_tail_handoff_started", False)),
        "triggered": bool(g("_end_silence_triggered", False)), "accum_s": g("_end_silence_accum_s", 0.0),
        "last_tick": g("_end_silence_last_tick", 0.0), "last_reason": g("_end_silence_last_reason", ""),
        "mode": g("_end_silence_mode", ""), "auto_advance_next": bool(g("_end_silence_auto_advance_next", False)),
        "eos_guard": g("_eos_guard", 0), "last_pos_ns": g("_last_pos_ns"), "stalled_ticks": g("_stalled_pos_ticks", 0),
        "handoff_active": bool(g("_media_end_handoff_active", False)), "cleanup_gen": g("_media_end_cleanup_gen", 0),
        "scheduled_cleanup_gen": g("_scheduled_media_end_cleanup_gen", 0),
        "cleanup_end_silence": bool(g("_media_end_cleanup_end_silence_triggered", False)),
        "cleanup_schedule_resume": bool(g("_media_end_cleanup_schedule_bg_resume", False)),
        "overlay_eligible": bool(g("_media_end_overlay_eligible", False)),
        "resume_reason": g("_bg_resume_reason", ""), "verify_gen": g("_bg_resume_verify_gen", 0),
        "verify_retried": bool(g("_bg_resume_verify_retried", False)), "verify_recovered": bool(g("_bg_resume_verify_recovered", False)),
        "soundboard_pending": g("_bg_soundboard_resume_pending_reason", ""),
        "duration_floor_logged": bool(g("_karaoke_duration_floor_logged", False)),
        "unknown_logged": bool(g("_end_audio_unknown_logged", False)), "no_level_logged": bool(g("_end_silence_no_level_logged", False)),
        "cdg_gen": g("_end_silence_cdg_last_generation"), "cdg_gen_ts": g("_end_silence_cdg_last_generation_ts", 0.0),
        "hold_log_ts": g("_end_audio_hold_log_ts", 0.0), "debug_ts": g("_end_silence_debug_ts", 0.0),
        "visual_unknown_ts": g("_end_visual_unknown_log_ts", 0.0), "suppress_ts": g("_end_silence_cdg_suppress_log_ts", 0.0),
        "bg_playing": bool(app.bg_music.is_playing),
        "pending_intro": bool(g("_pending_intro_loop", False)),
    }


def run(scenario):
    main = load_app()
    saved = (main.QTimer, main.threading, main.time, main.transition_analysis_cached, main.trailing_silence_cached,
             main.detect_trailing_silence, main.phrase_detect, main.transition_analysis, main._diag, main._song_memory_hook)
    try:
        world = World(scenario)
        world.sc_state = {"transport": False}
        world.times = lambda: times_for(world)
        world.real_threading, world.real_time = saved[1], saved[2]
        with contextlib.redirect_stdout(io.StringIO()):
            app = build_app(main, world)
            # ---- patches (restored in finally)
            def single_shot(ms, cb):
                kind, args = classify(cb)
                world.cmd("timer", [int(ms), kind, args])
                world.schedule(int(ms), "timer", (kind, args, cb))
            main.QTimer = types.SimpleNamespace(singleShot=staticmethod(single_shot))

            class FakeThread:
                def __init__(self, target=None, args=(), daemon=None, name=None):
                    self.target, self.args, self.name = target, args, name or ""

                def start(self):
                    world.cmd("spawn", [self.name])
                    self.target(*self.args)
            main.threading = types.SimpleNamespace(**{**vars(world.real_threading), "Thread": FakeThread})
            main.time = types.SimpleNamespace(**{**vars(world.real_time), "monotonic": lambda: world.now_ms / 1000.0,
                                                 "time": lambda: world.now_ms / 1000.0, "sleep": lambda s: None,
                                                 "perf_counter": lambda: world.now_ms / 1000.0})

            def diag(msg, *a, **k):
                if str(msg).startswith(KEEP):
                    world.cmd("log", [str(msg)])
            main._diag = diag
            main._song_memory_hook = lambda *a, **k: None

            def ta_cached(path):
                v = world.sc_state.get("arm", {}).get("cached", {}).get(str(path))
                if v is None:
                    return None
                return types.SimpleNamespace(duration=v.get("duration", 0.0), audio_end=v.get("audio_end"), visual_end=v.get("visual_end"),
                                             visual_confidence=v.get("visual_confidence", 0.0), safety_reason=v.get("safety_reason", "cached_visual"))
            main.transition_analysis_cached = ta_cached

            def trailing_cached(path):
                return world.sc_state.get("arm", {}).get("trailing_cached", {}).get(str(path))
            main.trailing_silence_cached = trailing_cached

            def probe(path):
                v = world.sc_state["arm"].get("scan_probe")
                if v in (None, "raise"):
                    raise RuntimeError("probe")
                return v
            main.phrase_detect = types.SimpleNamespace(probe_duration_seconds=probe)

            def det(path, duration_hint=None, raise_on_error=False):
                v = world.sc_state["arm"].get("scan_trailing")
                if v in (None, "raise"):
                    raise RuntimeError("scan failed")
                return v
            main.detect_trailing_silence = det

            def cdg_scan(path):
                r = world.sc_state["arm"].get("cdg_scan") or {}
                return types.SimpleNamespace(safe_for_early_completion=r.get("safe", False), visual_end=r.get("visual_end"),
                                             confidence=r.get("confidence", 0.0), reason=r.get("reason", "analysis_unavailable"))
            main.transition_analysis = types.SimpleNamespace(analyze_cdg_visual=cdg_scan)

            replay = {"events": [], "settings": dict(scenario["settings"])}
            expected = []
            for ev in scenario["script"]:
                world.schedule(ev["at"], "script", ev)
            end_ms = T0_MS + int(scenario["run_ms"])
            n = 1
            while T0_MS + 500 * n <= end_ms:
                if not any(a * 1000 <= 500 * n < b * 1000 for a, b in scenario.get("tick_skips", [])):
                    world.schedule(500 * n, "tick", {"ev": "tick", "a": {}})      # a late/skipped tick = a GUI stall
                n += 1

            while world.heap:
                item = heapq.heappop(world.heap)
                t, seq, kind, payload = item
                if t > end_ms:
                    break
                if seq in world.cancelled:
                    continue
                world.now_ms = t
                world.log = []
                record = {"t": t}
                if kind == "tick":
                    if not world.sc_state.get("transport") and (t - T0_MS) % 5000:
                        continue                  # the 500 ms timer keeps firing with no transport too (times=None branch); one in ten is enough
                    record.update({"ev": "tick", "a": {}})
                    if world.sc_state.get("level_db") is not None and app.karaoke_transport is not None:
                        app.karaoke_transport.last_level_ts = world.now_ms / 1000.0
                    snap = snapshot(world, app)
                    ret = app.update_time_left()
                elif kind == "script":
                    ev = payload
                    record.update({"ev": ev["ev"], "a": ev.get("a", {})})
                    ret = apply_script(main, app, world, ev, record)
                    snap = record.pop("snap_override", None) or record.get("snap")
                elif kind == "timer":
                    tk, targs, cb = payload
                    record.update({"ev": "timer", "a": {"kind": tk, "args": targs}})
                    snap = snapshot(world, app)
                    ret = cb()
                elif kind == "qtimer":
                    tm = payload
                    tm.token = None
                    record.update({"ev": "qtimer", "a": {"name": tm.name}})
                    snap = snapshot(world, app)
                    ret = tm.cb()
                elif kind == "finish":
                    did, fn = payload
                    record.update({"ev": "deliver", "a": {"id": did}})
                    snap = snapshot(world, app)
                    ret = fn()
                else:
                    raise ValueError(kind)
                if "snap" not in record:
                    record["snap"] = snap
                replay["events"].append(record)
                expected.append({"t": t, "cmds": world.log, "state": state_of(app),
                                 "ret": ret if isinstance(ret, (bool, int, float, str, type(None))) else None})
    finally:
        (main.QTimer, main.threading, main.time, main.transition_analysis_cached, main.trailing_silence_cached,
         main.detect_trailing_silence, main.phrase_detect, main.transition_analysis, main._diag, main._song_memory_hook) = saved
    return {"replay": replay, "expected": expected}


def apply_script(main, app, world, ev, record):
    k, a = ev["ev"], ev.get("a", {})
    st = world.sc_state
    if k == "song_start":
        st.update({"transport": True, "karaoke_playing": True, "media_dur_ns": int(a["media_dur_s"] * NS), "pos0_ns": 0, "t_ref": world.now_ms,
                   "playing_clock": True, "frozen_at": None, "pos_override": None, "paused": False, "arm": copy.deepcopy(a.get("arm", {})),
                   "level_db": a.get("level_db"), "engine_at_end": a.get("engine_at_end"), "gate": a.get("gate", True),
                   "generation": 0, "sectors": a.get("sectors"), "db_duration": a.get("db_duration"),
                   "deliver_ms": a.get("deliver_ms", 40)})
        app.karaoke_playing = True
        app.karaoke_transport = FakeTransport(world)
        if a.get("level_db") is not None:
            app.karaoke_transport.last_level_ts = world.now_ms / 1000.0
            app.karaoke_transport.last_level_db = a["level_db"]
        app.__dict__["_current_karaoke_song_path"] = a.get("library_path", "")
        record["snap"] = snapshot(world, app)
        app.__dict__["_bg_crossfade_prefired"] = False
        app._reset_end_silence_state()
        app._setup_end_silence_state(a["mode"], None)
        app._arm_audio_end_floor(a.get("audio_path", "/t/x.mp3"), a.get("duration_hint"))
        app._arm_visual_end_floor(a.get("visual_path", "/t/x.cdg"), a["mode"])
        return None
    if k == "set":
        if "settings" in a:
            app.settings.update(a["settings"])
        for key in ("sectors", "paused", "level_db", "engine_at_end", "gate", "generation", "sectors", "fade_in_works", "bg_audible", "recover_ok",
                    "intro_loop", "pos_override", "deliver_ms", "frozen_at"):
            if key in a:
                st[key] = a[key]
        if "paused" in a:
            now_pos = (times_for(world) or (0, 0))[1]
            if a["paused"]:
                st["frozen_at"] = now_pos
            else:
                st["frozen_at"] = None
                st["pos0_ns"] = now_pos
                st["t_ref"] = world.now_ms
        if "level_db" in a and app.karaoke_transport is not None:
            app.karaoke_transport.last_level_db = a["level_db"]
            app.karaoke_transport.last_level_ts = world.now_ms / 1000.0 if a["level_db"] is not None else 0.0
        if "seek_s" in a:
            st["pos0_ns"] = int(a["seek_s"] * NS)
            st["t_ref"] = world.now_ms
        for key, val in a.get("attrs", {}).items():
            app.__dict__[key] = val
        if "bg_playing" in a:
            app.bg_music.is_playing = bool(a["bg_playing"])
        if "bg_enabled" in a:
            app.bg_music.enabled = bool(a["bg_enabled"])
        if "bg_playlist" in a:
            app.bg_music.playlist = ["a.mp3"] if a["bg_playlist"] else []
        record["snap"] = snapshot(world, app)
        return None
    # events that run real methods: snapshot first
    if k == "eos_during_stop":
        app._stop_in_progress = True
    record["snap"] = snapshot(world, app)
    if k == "karafun_complete":
        return app._schedule_bg_resume(120, reason="external_karafun_complete")
    if k == "karafun_return":
        return app._schedule_bg_resume(120, reason="external_karafun_return")
    if k == "manual_stop":
        # harness glue standing in for stop_playback()'s relevant effects: invalidate pending cleanup, drop the transport, reset the detector
        app._cancel_pending_media_end_cleanup("manual_stop")
        app.karaoke_playing = False
        app._reset_end_silence_state()
        app.karaoke_transport = None
        st["transport"] = False
        st["karaoke_playing"] = False
        return None
    if k == "eos":
        return app._handle_media_end_safe(a.get("trigger", "eos"))
    if k == "eos_during_stop":
        app._stop_in_progress = True
        try:
            return app._handle_media_end_safe(a.get("trigger", "eos"))
        finally:
            app._stop_in_progress = False
    raise ValueError(k)


# ------------------------------------------------------------------------------------------------------------------ scenarios
BASE = {"seamless_transitions_enabled": True, "karaoke_bgm_crossfade_enabled": True, "karaoke_trim_verified_tail": True,
        "bg_enabled": True, "bg_autoplay_on_idle": True, "bg_volume": 0.8, "karaoke_auto_advance": False,
        "bgm_overlap_before_audio_end_sec": 2.0, "bgm_fallback_prestart_sec": 5.0, "end_silence_trim_threshold_sec": 2.5}


def scen(name, script, run_s, **settings):
    s = dict(BASE)
    s.update(settings)
    return {"name": name, "settings": s, "script": script, "run_ms": int(run_s * 1000)}


def at(sec, ev, **a):
    return {"at": int(round(sec * 1000)), "ev": ev, "a": a}


def start(sec, mode, dur, arm=None, **a):
    return at(sec, "song_start", mode=mode, media_dur_s=dur, arm=arm or {}, **a)


def cached(path, **v):
    return {path: v}


def scripted():
    out = []
    LIB = "/lib/s.zip"
    # --- verified audio end, CDG with a verified visual end and a lyrics gate that has finished
    arm_ok = {"cached": {LIB: {"duration": 200.0, "audio_end": 193.0}, "/t/x.cdg": {"visual_end": 195.0, "visual_confidence": 0.9}}}
    out.append(scen("verified_cdg_normal", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215))
    out.append(scen("verified_cdg_no_meter_gate_pending",
                    [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0, gate=False), at(197, "set", gate=True)], 215))
    out.append(scen("verified_mp4", [start(0, "mp4", 200.0, {"cached": {LIB: {"duration": 200.0, "audio_end": 196.0}, "/t/x.mp4": {"visual_end": 198.0, "visual_confidence": 0.95}}},
                                         library_path=LIB, audio_path="/t/x.mp4", visual_path="/t/x.mp4")], 215))
    out.append(scen("verified_cdg_with_meter", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0, level_db=-60.0)], 215))
    out.append(scen("verified_cdg_meter_loud_tail", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0, level_db=-20.0), at(199, "set", level_db=-60.0)], 215))
    out.append(scen("verified_overlap_zero", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, bgm_overlap_before_audio_end_sec=0.0))
    out.append(scen("verified_overlap_large", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, bgm_overlap_before_audio_end_sec=4.0))
    out.append(scen("verified_hold_short", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, end_silence_trim_threshold_sec=0.5))
    out.append(scen("verified_hold_long", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, end_silence_trim_threshold_sec=9.0))
    out.append(scen("verified_auto_advance", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, karaoke_auto_advance=True))
    # --- verified audio end, visual unverified / uncertain
    arm_novis = {"cached": {LIB: {"duration": 200.0, "audio_end": 193.0}}, "cdg_scan": {"safe": False, "visual_end": None, "confidence": 0.0, "reason": "static_or_active_nonblank_final_screen"}}
    out.append(scen("cdg_visual_unverified", [start(0, "cdg", 200.0, arm_novis, library_path=LIB, duration_hint=200.0)], 215))
    arm_scanvis = dict(arm_novis, cdg_scan={"safe": True, "visual_end": 194.0, "confidence": 0.9, "reason": "verified_final_clear"})
    out.append(scen("cdg_visual_scan_late", [start(0, "cdg", 200.0, arm_scanvis, library_path=LIB, duration_hint=200.0, deliver_ms=60000)], 215))
    out.append(scen("cdg_visual_low_confidence", [start(0, "cdg", 200.0, dict(arm_novis, cached={LIB: {"duration": 200.0, "audio_end": 193.0}, "/t/x.cdg": {"visual_end": 190.0, "visual_confidence": 0.5}}),
                                          library_path=LIB, duration_hint=200.0)], 215))
    # --- unverified audio end (fallback pre-start): quiet tail, file duration, settings
    sc_ = scen("late_ticks_over_overlap_window", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215)
    sc_["tick_skips"] = [[188, 196]]
    out.append(sc_)
    sc_ = scen("late_ticks_miss_fallback_prestart", [start(0, "cdg", 200.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=200.0)], 215)
    sc_["tick_skips"] = [[194, 199.9]]
    out.append(sc_)
    sc_ = scen("late_ticks_around_end", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215)
    sc_["tick_skips"] = [[196, 205]]
    out.append(sc_)
    out.append(scen("fallback_cdg", [start(0, "cdg", 200.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=200.0)], 215))
    for lead in (0.5, 2.0, 5.0, 8.0, 12.0):
        out.append(scen(f"fallback_prestart_{lead}", [start(0, "mp4", 120.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=120.0, audio_path="/t/y.mp4")], 130,
                        bgm_fallback_prestart_sec=lead))
    out.append(scen("fallback_crossfade_disabled", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0)], 115, karaoke_bgm_crossfade_enabled=False))
    out.append(scen("fallback_seamless_disabled", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0)], 115, seamless_transitions_enabled=False))
    out.append(scen("fallback_bg_playing", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0), at(1, "set", bg_playing=True)], 115))
    out.append(scen("fallback_bg_empty_playlist", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0), at(1, "set", bg_playlist=False)], 115))
    out.append(scen("fallback_bg_disabled_setting", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0)], 115, bg_enabled=False))
    out.append(scen("fallback_autoplay_off", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0)], 115, bg_autoplay_on_idle=False))
    out.append(scen("fallback_engine_at_end_false", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0, engine_at_end=False)], 115))
    out.append(scen("fallback_engine_at_end_true", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0, engine_at_end=True)], 115))
    out.append(scen("fallback_fade_in_refused", [start(0, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0, ), at(0.1, "set", fade_in_works=False)], 115))
    # --- scanned audio end (worker thread) incl. late delivery
    arm_scan = {"scan_probe": 200.0, "scan_trailing": 6.0, "cdg_scan": {"safe": True, "visual_end": 195.0, "confidence": 0.9, "reason": "verified_final_clear"}}
    out.append(scen("scan_fast", [start(0, "cdg", 200.0, arm_scan, duration_hint=200.0, deliver_ms=100)], 215))
    out.append(scen("scan_arrives_after_prefire_window", [start(0, "cdg", 200.0, arm_scan, duration_hint=200.0, deliver_ms=196000)], 215))
    out.append(scen("scan_capped_tail", [start(0, "cdg", 200.0, dict(arm_scan, scan_trailing=17.9), duration_hint=200.0)], 215))
    out.append(scen("scan_no_trailing_silence", [start(0, "cdg", 200.0, dict(arm_scan, scan_trailing=0.0), duration_hint=200.0)], 215))
    out.append(scen("scan_probe_differs_from_hint", [start(0, "cdg", 200.0, dict(arm_scan, scan_probe=230.0, scan_trailing=5.0), duration_hint=200.0)], 235))
    out.append(scen("scan_no_hint_no_probe", [start(0, "cdg", 200.0, {"scan_probe": "raise", "scan_trailing": 5.0})], 215, ))
    out.append(scen("scan_db_duration_used", [start(0, "cdg", 200.0, arm_scan, db_duration=200.0, deliver_ms=100)], 215))
    out.append(scen("trailing_cache_hit", [start(0, "cdg", 200.0, {"trailing_cached": {"/t/x.mp3": 4.0}}, duration_hint=200.0)], 215))
    out.append(scen("trim_disabled", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, karaoke_trim_verified_tail=False))
    out.append(scen("legacy_trim_enabled", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, karaoke_trim_verified_tail=False,
                    karaoke_allow_early_silence_trim=True, end_silence_trim_enabled=True))
    # --- the legacy (karaoke_trim_verified_tail = False) detector: meter/dwell/sector rules
    LEG = dict(karaoke_trim_verified_tail=False, karaoke_allow_early_silence_trim=True, end_silence_trim_enabled=True)
    arm_leg = {"cached": {LIB: {"duration": 200.0, "audio_end": 190.0}, "/t/x.cdg": {"visual_end": 192.0, "visual_confidence": 0.9}}}
    out.append(scen("legacy_cdg_complete_sectors_done", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50)], 215, **LEG))
    out.append(scen("legacy_cdg_sectors_active", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=500)], 215, **LEG))
    out.append(scen("legacy_cdg_sectors_unknown", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0)], 215, **LEG))
    out.append(scen("legacy_cdg_gate_pending", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50, gate=False), at(197, "set", gate=True)], 215, **LEG))
    out.append(scen("legacy_cdg_gate_raises", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50, gate="raise")], 215, **LEG))
    out.append(scen("legacy_cdg_generation_changes", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=500)]
                    + [at(193 + i, "set", generation=i + 1) for i in range(6)], 215, **LEG))
    out.append(scen("legacy_cdg_meter_quiet", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50, level_db=-60.0)], 215, **LEG))
    out.append(scen("legacy_cdg_meter_loud_then_quiet", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50, level_db=-20.0), at(196, "set", level_db=-60.0)], 215, **LEG))
    out.append(scen("legacy_cdg_not_near_end", [start(0, "cdg", 400.0, {"cached": {LIB: {"duration": 300.0, "audio_end": 295.0}, "/t/x.cdg": {"visual_end": 296.0, "visual_confidence": 0.9}}},
                                                library_path=LIB, duration_hint=400.0, sectors=50)], 410, **LEG))
    out.append(scen("legacy_cdg_not_near_end_no_sectors", [start(0, "cdg", 400.0, {"cached": {LIB: {"duration": 300.0, "audio_end": 295.0}, "/t/x.cdg": {"visual_end": 296.0, "visual_confidence": 0.9}}},
                                                library_path=LIB, duration_hint=400.0)], 410, **LEG))
    out.append(scen("legacy_cdg_hold_long", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50)], 215, end_silence_trim_threshold_sec=9.0, **LEG))
    out.append(scen("legacy_mp4_complete", [start(0, "mp4", 200.0, {"cached": {LIB: {"duration": 200.0, "audio_end": 190.0}, "/t/x.mp4": {"visual_end": 192.0, "visual_confidence": 0.9}}},
                                            library_path=LIB, audio_path="/t/x.mp4", visual_path="/t/x.mp4")], 215, **LEG))
    out.append(scen("legacy_visual_unverified", [start(0, "cdg", 200.0, arm_novis, library_path=LIB, duration_hint=200.0)], 260, **LEG))
    out.append(scen("legacy_visual_scan_after", [start(0, "cdg", 200.0, arm_scanvis, library_path=LIB, duration_hint=200.0, deliver_ms=198000, sectors=50)], 215, **LEG))
    out.append(scen("legacy_prefire_then_complete_auto_advance", [start(0, "cdg", 200.0, arm_leg, library_path=LIB, duration_hint=200.0, sectors=50)], 215, karaoke_auto_advance=True, **LEG))
    # --- short tail cache record (< 0.3 s), short files, graphics shorter than the audio
    out.append(scen("record_tail_below_threshold", [start(0, "cdg", 200.0, {"cached": {LIB: {"duration": 200.0, "audio_end": 199.8}}, "scan_probe": 200.0, "scan_trailing": 1.0}, library_path=LIB, duration_hint=200.0)], 215))
    out.append(scen("short_file_under_min_elapsed", [start(0, "cdg", 18.0, {"cached": {LIB: {"duration": 18.0, "audio_end": 15.0}}}, library_path=LIB, duration_hint=18.0)], 30))
    out.append(scen("graphics_shorter_than_audio", [start(0, "cdg", 180.0, {"cached": {LIB: {"duration": 230.0, "audio_end": 224.0}, "/t/x.cdg": {"visual_end": 178.0, "visual_confidence": 0.9}}},
                                                     library_path=LIB, duration_hint=180.0)], 240))
    out.append(scen("stale_scan_after_next_song", [start(0, "cdg", 100.0, arm_scan, duration_hint=100.0, deliver_ms=30000, audio_path="/t/a.mp3", visual_path="/t/a.cdg"),
                                                    start(5, "cdg", 100.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=100.0, audio_path="/t/b.mp3", visual_path="/t/b.cdg")], 120))
    out.append(scen("visual_scan_for_other_song_ignored", [start(0, "cdg", 100.0, arm_scanvis, duration_hint=100.0, deliver_ms=30000), at(10, "manual_stop")], 120))
    # --- pause / seek / stalls / late ticks / stop
    out.append(scen("pause_in_the_prefire_window", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0), at(190.0, "set", paused=True), at(200, "set", paused=False)], 230))
    out.append(scen("seek_into_tail", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0), at(30, "set", seek_s=196.0)], 230))
    out.append(scen("position_stalls", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0), at(40, "set", frozen_at=int(60 * NS))], 120))
    out.append(scen("position_stalls_engine_playing", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0, engine_at_end=False), at(40, "set", frozen_at=int(60 * NS))], 120))
    out.append(scen("manual_stop_mid_song", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0), at(100, "manual_stop")], 215))
    out.append(scen("stop_during_overlap", [start(0, "cdg", 200.0, {"scan_probe": "raise", "scan_trailing": "raise"}, duration_hint=200.0), at(196, "eos"), at(196.5, "manual_stop")], 215))
    out.append(scen("eos_while_stop_in_progress", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(59, "eos_during_stop")], 70))
    out.append(scen("duplicate_eos", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos"), at(58.1, "eos")], 70))
    out.append(scen("cleanup_skipped_stop_in_progress_guard_fires", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(30, "eos"), at(30.5, "set", attrs={"_stop_in_progress": True}),
                                                                    at(40, "set", attrs={"_stop_in_progress": False}), at(41, "eos")], 90))
    out.append(scen("auto_advance_stale_generation", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos"), at(58.2, "set", attrs={"_confirmed_singer_start_generation": 1})], 70, karaoke_auto_advance=True))
    out.append(scen("auto_advance_yields_to_active_command", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos"), at(58.2, "set", attrs={"_next_in_progress": True})], 70, karaoke_auto_advance=True))
    out.append(scen("auto_advance_yields_to_pending_start", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos"), at(58.2, "set", attrs={"_pending_play_start_token": 5})], 70, karaoke_auto_advance=True))
    out.append(scen("verified_end_crossfade_off_early_end", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, karaoke_bgm_crossfade_enabled=False))
    sc_ = scen("verified_prefire_window_tie", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0)], 215, bgm_overlap_before_audio_end_sec=1.0)
    sc_["tick_skips"] = [[192, 193]]
    out.append(sc_)
    out.append(scen("verified_meter_exactly_at_threshold", [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0, level_db=-38.0)], 215))
    out.append(scen("legacy_meter_exactly_at_threshold", [start(0, "cdg", 200.0, {"cached": {LIB: {"duration": 200.0, "audio_end": 190.0}, "/t/x.cdg": {"visual_end": 192.0, "visual_confidence": 0.9}}},
                                                          library_path=LIB, duration_hint=200.0, sectors=50, level_db=-38.0)], 215,
                    karaoke_trim_verified_tail=False, karaoke_allow_early_silence_trim=True, end_silence_trim_enabled=True))
    out.append(scen("stop_in_progress_and_handoff_active", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(30, "eos"), at(30.5, "set", attrs={"_stop_in_progress": True}), at(31, "eos"),
                                                            at(31.5, "set", attrs={"_stop_in_progress": False})], 60))
    out.append(scen("cleanup_bg_playing_empty_playlist", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_playing=True, bg_playlist=False), at(58, "eos")], 80, karaoke_bgm_crossfade_enabled=False))
    for nm, attrs in (("stale_generation", {"_confirmed_singer_start_generation": 1}), ("next_in_progress", {"_next_in_progress": True}),
                      ("pending_token", {"_pending_play_start_token": 5}), ("both_busy", {"_next_in_progress": True, "_pending_play_start_token": 5}), ("runs", {})):
        out.append(scen("verified_tail_auto_advance_" + nm, [start(0, "cdg", 200.0, arm_ok, library_path=LIB, duration_hint=200.0), at(195.7, "set", attrs=attrs)], 215, karaoke_auto_advance=True))
    out.append(scen("legacy_mp4_not_near_end", [start(0, "mp4", 400.0, {"cached": {LIB: {"duration": 300.0, "audio_end": 295.0}, "/t/x.mp4": {"visual_end": 296.0, "visual_confidence": 0.9}}},
                                               library_path=LIB, audio_path="/t/x.mp4", visual_path="/t/x.mp4")], 410,
                    karaoke_trim_verified_tail=False, karaoke_allow_early_silence_trim=True, end_silence_trim_enabled=True))
    sc_ = scen("overlap_start_blocked_by_session_gate", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(30, "eos")], 60)
    sc_["bgm_session_started"] = False
    out.append(sc_)
    out.append(scen("eos_error_trigger", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos", trigger="error")], 70))
    out.append(scen("eos_with_bg_already_playing", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_playing=True), at(58, "eos")], 70))
    out.append(scen("eos_bg_playing_not_audible", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_playing=True, bg_audible=False), at(58, "eos")], 80))
    out.append(scen("eos_overlap_disabled", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos")], 70, karaoke_bgm_crossfade_enabled=False))
    out.append(scen("eos_overlap_fade_refused", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", fade_in_works=False), at(58, "eos")], 80))
    out.append(scen("eos_auto_advance", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos")], 70, karaoke_auto_advance=True))
    out.append(scen("eos_intro_loop", [start(0, "cdg", 60.0, {}, duration_hint=60.0, ), at(1, "set", intro_loop=True), at(58, "eos")], 70))
    out.append(scen("eos_sessionless_blocked", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos")], 80, ))
    # session gating: no BGM Play pressed and no song played yet this session
    sc = scen("resume_blocked_no_session", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos")], 80, karaoke_bgm_crossfade_enabled=False)
    sc["bgm_session_started"] = False
    out.append(sc)
    sc = scen("resume_allowed_after_karaoke", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos")], 80, karaoke_bgm_crossfade_enabled=False)
    sc["bgm_session_started"] = False
    sc["karaoke_played_this_session"] = True
    out.append(sc)
    out.append(scen("cleanup_resume_verify_ok", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(58, "eos")], 80, karaoke_bgm_crossfade_enabled=False))
    out.append(scen("cleanup_resume_verify_retry", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_audible=False), at(58, "eos")], 90, karaoke_bgm_crossfade_enabled=False))
    out.append(scen("cleanup_resume_recovery_fails", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_audible=False, recover_ok=False), at(58, "eos")], 90, karaoke_bgm_crossfade_enabled=False))
    out.append(scen("cleanup_stale_bg_playing", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_playing=True, bg_audible=False), at(58, "eos")], 90, karaoke_bgm_crossfade_enabled=False))
    out.append(scen("cleanup_bg_already_audible", [start(0, "cdg", 60.0, {}, duration_hint=60.0), at(5, "set", bg_playing=True), at(58, "eos")], 90, karaoke_bgm_crossfade_enabled=False))
    # --- KaraFun
    out.append(scen("karafun_complete", [at(0, "karafun_complete")], 10))
    out.append(scen("karafun_complete_fade_in_refused", [at(0, "set", fade_in_works=False), at(0.1, "karafun_complete")], 10))
    out.append(scen("karafun_complete_bg_disabled", [at(0, "karafun_complete")], 10, bg_enabled=False))
    out.append(scen("karafun_complete_no_autoplay", [at(0, "karafun_complete")], 10, bg_autoplay_on_idle=False))
    sc = scen("karafun_complete_no_session", [at(0, "karafun_complete")], 10)
    sc["bgm_session_started"] = False
    out.append(sc)
    out.append(scen("karafun_return", [at(0, "karafun_return")], 10))
    out.append(scen("karafun_complete_then_return", [at(0, "karafun_complete"), at(0.05, "karafun_return")], 10))
    out.append(scen("karafun_complete_verify_retry", [at(0, "set", bg_audible=False), at(0.1, "karafun_complete")], 15))
    return out


def random_scenario(seed):
    r = random.Random(seed)
    LIB = "/lib/r.zip"
    mode = r.choice(["cdg", "cdg", "mp4"])
    media = r.choice([r.uniform(25, 60), r.uniform(120, 320)])
    dur_audio = media if r.random() < 0.7 else media + r.choice([-8.0, 3.0, 12.0, 25.0])
    dur_audio = max(10.0, dur_audio)
    tail = r.choice([0.0, 0.2, r.uniform(0.3, 3.0), r.uniform(3.0, 12.0), r.uniform(12.0, 25.0)])
    arm = {}
    src = r.choice(["library", "cache", "scan", "scan_fail", "none"])
    audio_path = "/t/x.mp3" if mode == "cdg" else "/t/x.mp4"
    if src == "library":
        arm["cached"] = {LIB: {"duration": round(dur_audio, 3), "audio_end": round(max(0.0, dur_audio - tail), 3)}}
    elif src == "cache":
        arm["trailing_cached"] = {audio_path: round(tail, 3)}
    elif src == "scan":
        arm.update({"scan_probe": round(dur_audio, 3), "scan_trailing": round(tail, 3)})
    elif src == "scan_fail":
        arm.update({"scan_probe": r.choice([None, "raise"]), "scan_trailing": "raise"})
    else:
        arm.update({"scan_probe": "raise", "scan_trailing": "raise"})
    vis = r.choice(["cache", "cache_low", "scan_ok", "scan_bad", "none"])
    visual_path = "/t/x.cdg" if mode == "cdg" else audio_path
    vend = round(max(0.0, min(media, dur_audio) - r.uniform(0.0, 8.0)), 3)
    if vis == "cache":
        arm.setdefault("cached", {})[visual_path] = {"visual_end": vend, "visual_confidence": r.choice([0.85, 0.9, 1.0]), "safety_reason": "verified_visual"}
    elif vis == "cache_low":
        arm.setdefault("cached", {})[visual_path] = {"visual_end": vend, "visual_confidence": 0.5}
    elif vis == "scan_ok":
        arm["cdg_scan"] = {"safe": True, "visual_end": vend, "confidence": 0.9, "reason": "verified_final_clear"}
    elif vis == "scan_bad":
        arm["cdg_scan"] = {"safe": False, "visual_end": None, "confidence": 0.0, "reason": "static_or_active_nonblank_final_screen"}
    settings = {"karaoke_bgm_crossfade_enabled": r.random() < 0.85, "karaoke_trim_verified_tail": r.random() < 0.8,
                "bgm_overlap_before_audio_end_sec": r.choice([0.0, 1.0, 2.0, 3.0, 4.5]),
                "bgm_fallback_prestart_sec": r.choice([1.0, 3.2, 5.0, 8.0, 20.0]),
                "end_silence_trim_threshold_sec": r.choice([0.5, 1.5, 2.5, 5.0, 70.0]),
                "karaoke_auto_advance": r.random() < 0.15, "bg_enabled": r.random() < 0.95, "bg_autoplay_on_idle": r.random() < 0.95,
                "seamless_transitions_enabled": r.random() < 0.93}
    if r.random() < 0.3:
        settings.update({"karaoke_trim_verified_tail": False, "karaoke_allow_early_silence_trim": True, "end_silence_trim_enabled": r.random() < 0.85})
    script = [start(0, mode, round(media, 3), arm, library_path=LIB if src == "library" else "", duration_hint=round(media, 3) if r.random() < 0.85 else None,
                    audio_path=audio_path, visual_path=visual_path, deliver_ms=r.choice([40, 40, 500, 15000, 120000]),
                    level_db=r.choice([None, None, None, -60.0, -30.0]), engine_at_end=r.choice([None, None, False, True]),
                    gate=r.choice([True, True, False, "raise"]), sectors=r.choice([None, 50, 500, 120, 121]), db_duration=r.choice([None, None, round(dur_audio, 3), "raise"]))]
    run = media + 15
    for _ in range(r.randint(0, 5)):
        t = r.uniform(1, media + 5)
        kind = r.choice(["pause", "resume", "seek", "gate", "level", "bg", "stall", "stop", "eos", "fade", "generation", "sectors"])
        if kind == "pause":
            script.append(at(t, "set", paused=True))
        elif kind == "resume":
            script.append(at(t, "set", paused=False))
        elif kind == "seek":
            script.append(at(t, "set", seek_s=round(r.uniform(0, media), 3)))
        elif kind == "gate":
            script.append(at(t, "set", gate=r.choice([True, False, "raise"])))
        elif kind == "level":
            script.append(at(t, "set", level_db=r.choice([None, -60.0, -20.0])))
        elif kind == "bg":
            script.append(at(t, "set", bg_playing=r.random() < 0.5, bg_audible=r.random() < 0.7))
        elif kind == "stall":
            script.append(at(t, "set", frozen_at=int(r.uniform(0, media) * NS)))
        elif kind == "generation":
            script.append(at(t, "set", generation=r.randint(0, 5)))
        elif kind == "sectors":
            script.append(at(t, "set", sectors=r.choice([None, 50, 500])))
        elif kind == "stop":
            script.append(at(t, "manual_stop"))
        elif kind == "eos":
            script.append(at(t, "eos", trigger=r.choice(["eos", "error"])))
        else:
            script.append(at(t, "set", fade_in_works=r.random() < 0.7))
    script.sort(key=lambda e: e["at"])
    # stable sort keeps song_start first for ties at 0; any event that sorts before it only matters on an idle app and is fine
    s = scen(f"random_{seed}", script, run, **settings)
    if r.random() < 0.3:
        a0 = r.uniform(5, media)
        s["tick_skips"] = [[round(a0, 1), round(a0 + r.uniform(1, 12), 1)]]
    s["bgm_session_started"] = r.random() < 0.9
    return s


if __name__ == "__main__":
    sc = json.load(open(sys.argv[1]))
    json.dump(run(sc), sys.stdout)
