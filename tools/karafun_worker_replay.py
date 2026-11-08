#!/usr/bin/env python3
"""Whole-method replay of KaraokeApp._automate_karafun_search_and_play (0.2.18.1.py), ORCHESTRATION included.

The per-stretch tools (karafun_{intake,setup,prelude,flow,finish,helpers}_replay.py and the ladder tool) each run ONE stretch of the method with its closures stubbed. This one
runs the WHOLE real method, read out of the source by AST and compiled unchanged, with its real closures (_schedule_bgm_fade, _schedule_early_handoff, _watch_karafun_start,
_native_double_click_result, _finish) and the worker thread, against ONE recording fake world:

  * threads run inline at .start(); the UI thread runs its callable inline at _run_on_ui_thread();
  * every question the app would ask the machine (KaraFun running? search answer? menu state? ...) is a SCRIPTED answer, drawn from the RNG the first time and replayed after;
  * the session ends after N reads of `_active_external_karafun` made by the worker (scenario `end_session_at`);
  * the clock is fake (time.sleep advances it).

Compared with the Rust twin (singws-karafun `worker`): the method's return value, the entry, every call (name, arguments), every log line, every sleep and the final clock.

  python3 tools/karafun_worker_replay.py [--seeds N]
"""
import argparse
import ast
import json
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import karafun_ladder_parity as lp  # noqa: E402  (the real _is_karafun_* predicates, read by AST)

PREDICATES = lp.PREDICATES
_CACHE = {}


def load():
    if "code" in _CACHE:
        return _CACHE["code"], _CACHE["preds"]
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_automate_karafun_search_and_play":
                    method = sub
    assert method is not None, "the Python moved"
    method.decorator_list = []
    mod = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(mod)
    _CACHE["code"] = compile(mod, "worker", "exec")
    ns = lp.load()
    _CACHE["preds"] = {name: ns[name] for name in PREDICATES}
    return _CACHE["code"], _CACHE["preds"]


class Recorder:
    def __init__(self, scenario, rng=None, answers=None):
        self.sc = scenario
        self.rng = rng
        self.answers = answers if answers is not None else []
        self.pos = 0
        self.calls, self.log, self.sleeps = [], [], []
        self.t = 100.0
        self.reads = 0
        self.armed = False

    def ask(self, kind, make):
        if self.pos < len(self.answers):
            a = self.answers[self.pos]
            assert a["kind"] == kind, f"order differs: expected {kind} got {a['kind']}"
        else:
            a = {"kind": kind, "v": make(self.rng)}
            self.answers.append(a)
        self.pos += 1
        return a["v"]

    def call(self, name, *args, **kw):
        self.calls.append([name, list(args), {k: kw[k] for k in sorted(kw)}])


def build_world(rec, preds):
    sc = rec.sc
    R = rec
    session = {"id": 1}

    class FakeSelf:
        def __init__(self):
            self.settings = dict(sc["settings"])
            self._karafun_capture_session = sc["capture_session"] if sc["capture_session"] is not None else None
            if sc["capture_session"] is None:
                del self._karafun_capture_session
            if sc.get("launch_error") is not None:
                self._last_karafun_launch_error = sc["launch_error"]

        # ---- the session object: the worker's reads of it are counted
        @property
        def _active_external_karafun(self):
            if not R.armed:
                return session
            R.reads += 1
            if sc["end_session_at"] is not None and R.reads >= sc["end_session_at"]:
                return None
            return session

        # ---- intake
        def _karafun_entry_artist_title(self, entry):
            return sc["artist"], sc["title"]

        def _set_karafun_entry_status(self, entry, status, message=None, notify=None):
            R.call("set_status", status, message=message, notify=notify)

        def _ensure_queue_entry_id(self, entry):
            R.call("ensure_id")
            return sc["request_uid"]

        def _karafun_search_queries_for_entry(self, entry):
            R.call("queries_for_entry")
            return list(sc["queries"])

        # ---- setup
        def _karafun_process_running(self):
            R.call("process_running")
            return R.ask("running", lambda r: r.random() < 0.5)

        def _run_on_ui_thread(self, fn):
            R.call("ui_thread")
            try:
                fn()
            except Exception:  # noqa: BLE001 - an exception on the UI thread never reaches the worker
                pass

        def _open_karafun_for_entry(self, entry):
            R.call("open_karafun")
            return R.ask("open", lambda r: r.random() < 0.85)

        def _karafun_wait_until_ready(self, was_running=None, session_is_current=None):
            R.call("wait_until_ready", was_running=was_running, session_current=bool(session_is_current()))
            err = R.ask("wait", lambda r: r.choice([None, None, None, "KaraFun never became ready", "session over"]))
            if err:
                raise RuntimeError(err)

        def _karafun_apple_events_preflight(self):
            R.call("preflight")
            v = R.ask("preflight", lambda r: [r.random() < 0.85, r.choice(["", "no automation", "Zoë"])])
            return (bool(v[0]), v[1])

        def _ensure_karafun_audio_output(self):
            R.call("audio_output")
            v = R.ask("audio", lambda r: [r.random() < 0.85, r.choice(["", "wrong device", "Zoë"])])
            return (bool(v[0]), v[1])

        # ---- ladder
        def _karafun_search_script(self, **kw):
            R.call("search_script", **kw)
            return "search-script"

        def _run_karafun_applescript_sync(self, lines, **kw):
            R.call("session_script", lines, **kw)
            if lines == "diag-script":
                v = R.ask("diag", lambda r: [r.random() < 0.7, r.choice(["window ok", "", "x" * 1000]), r.choice(["", "diag error"])])
                return (bool(v[0]), v[1], v[2])
            def make(r):
                if lines == "probe-script" and sc.get("menu_mode") == "stubborn":
                    return [True, "IDLE", ""]
                return [r.random() < 0.8, r.choice(texts_for(lines)), r.choice(SCRIPT_ERRS)]
            v = R.ask("session_script", make)
            return (bool(v[0]), v[1], v[2])

        def _karafun_search_diag_script(self, title):
            R.call("search_diag_script", title)
            return "diag-script"

        # ---- prelude / flow
        def _karafun_clock_seconds(self, text):
            R.call("clock_seconds", text)
            return R.ask("clock", lambda r: r.choice(CLOCKS + [None]))

        def _apply_verified_duration(self, entry, seconds, source=None):
            R.call("apply_verified_duration", seconds, source=source)
            return R.ask("apply_duration", lambda r: r.random() < 0.6)

        def _macos_native_mouse_click(self, x, y, clicks=1):
            R.call("mouse_click", x, y, clicks=clicks)
            return R.ask("mouse_click", lambda r: r.random() < 0.85)

        def _karafun_play_now_queue_script(self):
            R.call("play_now_queue_script")
            return "play-now-queue-script"

        def _show_processing_notification(self, message, level=None):
            R.call("notify", message, level=level)

        def _karafun_adjust_menu_script(self, key_item, key_steps, tempo_item, tempo_steps):
            R.call("adjust_menu_script", key_item, key_steps, tempo_item, tempo_steps)
            return f"adjust:{key_item}:{key_steps}:{tempo_item}:{tempo_steps}"

        def _karafun_show_key_tempo(self, key, tempo):
            R.call("show_key_tempo", key, tempo)

        @property
        def _karafun_handoff_in_progress(self):
            return R.ask("in_progress", lambda r: True if sc.get("stuck_handoff") else r.random() < 0.6)

        @property
        def _karafun_handoff_complete(self):
            return R.ask("handoff_complete", lambda r: r.random() < 0.8)

        def _karafun_start_probe_script(self):
            R.call("start_probe_script")
            return "probe-script"

        def _karafun_renderer_ready_script(self):
            R.call("renderer_ready_script")
            return "ready-script"

        def _karafun_set_key_tempo_script(self, key, tempo):
            R.call("set_key_tempo_script", key, tempo)
            return f"kt-script:{key}:{tempo}"

        def _karafun_press_play_control(self):
            v = R.ask("press", lambda r: [r.random() < 0.8, r.choice(["", "no play button", "AX -1719"])])
            R.call("press_play")
            return (bool(v[0]), v[1] or None)

        def _karafun_activate_result_for_playback(self, point, reuse_point=False):
            R.call("activate_result", list(point), reuse_point=reuse_point)
            v = R.ask("activate", lambda r: [r.random() < 0.85, r.choice(["", "result moved", "timeout"])])
            if v[0] and bool(self.settings.get("karafun_native_double_click", False)):
                # the real method's learned-native branch: the row was double-clicked natively
                session["entry"]["karafun_native_retry_sent"] = True
                session["entry"]["karafun_native_first"] = True
            return (bool(v[0]), v[1] or None)

        def _karafun_playback_menu_state(self):
            R.call("menu_state")
            def make(r):
                mode = sc.get("menu_mode")
                R.menu_asks = getattr(R, "menu_asks", 0) + 1
                if mode == "stubborn":
                    return "IDLE"
                if mode == "slow":
                    return "IDLE" if R.menu_asks <= 10 else "PLAYING"
                return r.choice(["PLAYING", "IDLE", "IDLE", "UNKNOWN"])
            return R.ask("menu_state", make)

        def _karafun_run_window_script(self, lines, timeout=None):
            R.call("window_script", lines, timeout=timeout)
            return None

        def _start_karafun_completion_monitor(self, entry):
            R.call("start_monitor")

        # ---- closures' machine calls
        def _fade_bg_for_external_karafun(self):
            R.call("fade_bg")

        def _handoff_show_screen_to_karafun(self):
            R.call("handoff_show_screen")
            if sc["handoff_error"] is not None:
                raise RuntimeError(sc["handoff_error"])

        def _karafun_raise_results_window(self):
            R.call("raise_results")

        def _karafun_frontmost_app_name(self):
            R.call("frontmost")
            return R.ask("frontmost", lambda r: r.choice(["KaraFun", "SingWS", "it's", "Zoë"]))

        def _macos_native_double_click(self, x, y):
            R.call("native_double_click", x, y)

        def _karafun_click_point_script(self, point):
            R.call("click_point_script", list(point))
            return "click-script"

        def _schedule_save_settings(self):
            R.call("save_settings")

        def _activate_host_during_karafun_capture(self, session_):
            R.call("activate_host", {k: v for k, v in session_.items() if k != "entry"})

        # ---- finish
        def _karafun_adjustment_needs_attention(self, key, tempo, message, applied):
            R.call("needs_attention", key, tempo, message, bool(applied))
            return sc["needs_attention"]

        def _show_karafun_apple_events_setup(self, notify=False):
            R.call("apple_events_setup", notify=notify)
            return "apple events setup text"

        def _show_karafun_accessibility_setup(self, notify=False):
            R.call("accessibility_setup", notify=notify)
            return "accessibility setup text"

        def _stop_karafun_dual_renderer_capture(self):
            R.call("stop_capture")

        def _schedule_save_data(self, delay):
            R.call("schedule_save", delay)

        def update_queue_display(self):
            R.call("update_queue_display")

    me = FakeSelf()
    for name, fn in preds.items():
        setattr(FakeSelf, name, staticmethod(fn))

    class Clock:
        @staticmethod
        def monotonic():
            return R.t

        @staticmethod
        def time():
            return 1_700_000_000.0 + R.t

        @staticmethod
        def sleep(s):
            R.sleeps.append(s)
            R.t += s

    class Thread:
        def __init__(self, target=None, daemon=None, name=None):
            self._t, self._n = target, name

        def start(self):
            R.call("thread", thread_name=self._n)
            if self._n == "karafun-search-play":
                R.armed = True
            try:
                self._t()
            except Exception:  # noqa: BLE001 - an exception in a thread ends that thread only
                pass

    class Event:
        def __init__(self):
            self.flag = False

        def set(self):
            R.call("handoff_dispatched_set")
            self.flag = True

        def wait(self, timeout=None):
            if self.flag:
                return True
            R.call("event_wait", timeout=timeout)
            R.t += timeout or 0.0
            return False

    shadow = types.ModuleType("karafun_flow_shadow")
    shadow.startflow = lambda settings, log, **kw: R.call("shadow_startflow", **{k: (v if isinstance(v, (bool, int, float, str)) or v is None else str(v)) for k, v in kw.items()})
    sys.modules["karafun_flow_shadow"] = shadow

    def renderer_raise_script():
        R.call("renderer_raise_script")
        return ["raise-script"]

    g = {"sys": types.SimpleNamespace(platform="darwin"), "time": Clock, "threading": types.SimpleNamespace(Thread=Thread, Event=Event),
         "_diag": lambda m: R.log.append(m), "renderer_raise_script": renderer_raise_script}
    return me, g, session


def texts_for(script):
    """What the machine could answer to this script: the right answer first, then the near misses."""
    script = str(script)
    if script == "search-script":
        return ["FOUND|404|202|3:41", "FOUND|404.7|202.2|0:00", "TITLE_ONLY|1|2|", "FIRST|1|2|3:00", "ERROR|No usable KaraFun search result", "SEARCHED", "", "FOUND|1", "x|y|z"]
    if script == "play-now-queue-script":
        return ["QUEUED_PAUSED", "QUEUED_PAUSED", "QUEUED", "", " QUEUED_PAUSED ", "weird"]
    if script.startswith("adjust:"):
        _, _, ks, _, ts = script.split(":")
        return [f"ADJUSTED|{ks}|{ts}", f"ADJUSTED|{ks}|{ts}", f" ADJUSTED|{ks}|{ts} ", "ADJUSTED|0|0", "", "weird"]
    if script.startswith("kt-script:"):
        return ["ADJUSTED|true|true", "ADJUSTED|true|false", "ADJUSTED|false|true", "ADJUSTED|false|false", "", "weird"]
    if script == "probe-script":
        return ["PLAYING", "PLAYING", "IDLE", "UNKNOWN", "ERROR|KaraFun control window not found", "", "weird"]
    if script == "ready-script":
        return ["READY", "READY", "weird", ""]
    return SCRIPT_TEXTS


_OMIT = object()
CLOCKS = [0.0, 221.0, 3.0, 0.5, 600.0, 1.0, 59.9, -1.0]
SCRIPT_TEXTS = ["FOUND|404|202|3:41", "TITLE_ONLY|1|2|", "FIRST|1|2|3:00", "ERROR|No usable KaraFun search result", "SEARCHED", "", "FOUND|1", "FOUND|404.7|202.2|0:00", "x|y|z",
                "QUEUED_PAUSED", "QUEUED", "READY", "PLAYING", "IDLE", "UNKNOWN", "ADJUSTED|2|0", "ADJUSTED|0|10", "ADJUSTED|true|true", "ADJUSTED|true|false", "ADJUSTED|false|false", "weird"]
SCRIPT_ERRS = ["", "", "timeout", "AXError -1719", "Invalid index", "not allowed assistive access", "error -1743", "Zoë", "e" * 200]


class Lucky:
    """A lucky scenario's answers lean toward the first (usually successful) choice, so the deep paths of the method are reached as often as the early failures."""

    def __init__(self, r):
        self.r = r

    def random(self):
        return self.r.random() ** 3

    def choice(self, seq):
        return seq[int(len(seq) * self.r.random() ** 2)]


def run(code_and_preds, scenario, rng=None, answers=None):
    code, preds = code_and_preds
    if scenario.get("lucky") and rng is not None:
        rng = Lucky(rng)
    rec = Recorder(scenario, rng, answers)
    me, g, session = build_world(rec, preds)
    g = dict(g)
    exec(code, g)
    entry = dict(scenario["entry"])
    session["entry"] = entry
    out = {}
    try:
        out["returned"] = g["_automate_karafun_search_and_play"](me, entry, key=scenario["key"], tempo_percent=scenario["tempo"]) if scenario["active_ok"] else False
    except Exception as exc:  # noqa: BLE001
        out["raised"] = str(exc)
    return {"result": out, "calls": rec.calls, "log": rec.log, "sleeps": rec.sleeps, "entry": entry, "final_clock": rec.t, "answers": rec.answers}


def random_scenario(r):
    kind = r.random()
    entry = {}
    if r.random() < 0.2:
        entry["karafun_adjustment_applied"] = r.choice(["key=2;tempo=90", "key=0;tempo=100", "old"])
    if r.random() < 0.25:
        entry["karafun_submission_state"] = r.choice(["karafun_queued", "karafun_pending", "karafun_failed", "other"])
        entry["karafun_pending_at"] = r.choice([1_700_000_099.0, 5.0, 0])
    if r.random() < 0.15:
        entry["karafun_result_activation_point"] = r.choice([[404, 202], [10, 20]])
    artist, title = r.choice([("Sugarcult", "Memory"), ("", "Memory"), ("Zoë", "Don't Stop"), ("a", "b"), ("", ""), ("A", "Zoë (live)")])
    def sv(default_true):
        x = r.random()
        if x < 0.1:
            return _OMIT
        if x < 0.18:
            return r.choice([1, 0, "", "yes", None, [], {}, [1], {"a": 1}, 2.5])
        return r.random() < (0.97 if default_true == "q" else 0.75 if default_true else 0.3)
    settings = {"karafun_auto_queue_enabled": sv("q"), "karafun_dual_renderer_capture": sv(False), "karafun_manage_show_screen": sv(True), "karafun_fast_start_enabled": sv(True),
                "karafun_auto_key_tempo": sv(True), "karafun_native_double_click": sv(False)}
    settings = {k: v for k, v in settings.items() if v is not _OMIT}
    return {"settings": settings,
            "entry": entry, "artist": artist, "title": title, "key": r.choice([0, 0, 2, -1, 3, -12, 24]), "tempo": r.choice([None, 100, 100, 90, 110, 75, 250]),
            "request_uid": r.choice(["uid-1", "uid-é"]), "queries": r.choice([["Memory Sugarcult", "Memory"], ["Memory"], [], ["it's"], ["Zoë", "Zoë x"]]),
            "end_session_at": r.choice([None] * 14 + list(range(1, 60))), "capture_session": r.choice([None, None, True, False]), "handoff_error": r.choice([None, None, None, "no show window"]),
            "needs_attention": r.random() < 0.3, "launch_error": r.choice([None, None, "launch failed"]), "stuck_handoff": r.random() < 0.1, "active_ok": r.random() < 0.97, "lucky": r.random() < 0.6, "menu_mode": r.choice([None] * 5 + ["stubborn", "slow", "slow"])}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=300)
    args = ap.parse_args(argv)
    loaded = load()
    stats = {"returned_true": 0, "returned_false": 0, "raised": 0, "queued": 0, "failed": 0}
    for seed in range(args.seeds):
        r = random.Random(seed)
        out = run(loaded, random_scenario(r), r)
        res = out["result"]
        if "raised" in res:
            stats["raised"] += 1
            if stats["raised"] <= 3:
                print("RAISED", seed, res["raised"])
        else:
            stats["returned_true" if res["returned"] else "returned_false"] += 1
        st = out["entry"].get("karafun_submission_state")
        if st == "karafun_queued":
            stats["queued"] += 1
        elif st == "karafun_failed":
            stats["failed"] += 1
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
