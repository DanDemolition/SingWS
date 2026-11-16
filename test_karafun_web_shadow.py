"""karafun_web_shadow.py: the Rust twin of the KaraFun web player runs beside the Python (log only) and its answers are compared with what the Python did."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import platform
import re
import unittest
from pathlib import Path
from unittest import mock

import karafun_web
import karafun_web_shadow as shadow

# the Rust library built by rust/build_dsp.sh for this Mac (the app finds it inside its bundle; from source the test points at it)
_ARCH = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
_NATIVE = Path(__file__).parent / "native" / "singws_dsp" / _ARCH / "libsingws_dsp_ffi.dylib"
if not os.environ.get("SINGWS_DSP_LIB") and _NATIVE.is_file():
    os.environ["SINGWS_DSP_LIB"] = str(_NATIVE)


def real_library():
    shadow._loaded = False
    return shadow._library() is not None


class Projection(unittest.TestCase):
    def test_service_actions_become_the_small_vocabulary(self):
        acts = [["set", "starting", "x"], ["log", "text"], ["reload"], ["login"], ["sign_in"], ["close"], ["restart"], ["forward"], ["read_saved_login"]]
        self.assertEqual(shadow.project_service(acts), [["set", "starting"], ["reload"], ["login"], ["sign_in"], ["reset"]])

    def test_host_actions_become_the_small_vocabulary_and_returns_only_for_start_and_finish(self):
        acts = [["entry_set", "a", 1], ["web_play", 5], ["log", "giving up on the song: KaraFun's web player never started the song"], ["start_frames"],
                ["start_frames_hidden"], ["finish_complete"], ["service_stop"], ["returns", True]]
        self.assertEqual(shadow.project_host(acts, "host_start"), [["play"], ["fail", "never_started"], ["started"], ["complete"], ["recover"], ["returns", True]])
        self.assertEqual(shadow.project_host(acts, "host_status"), [["play"], ["fail", "never_started"], ["started"], ["complete"], ["recover"]])

    def test_a_failure_is_reported_as_a_class_never_as_text(self):
        self.assertEqual(shadow.fail_kind("KaraFun's web player is not signed in: sign in to KaraFun in your browser (Settings > KaraFun)"), "sign_in")
        self.assertEqual(shadow.fail_kind("KaraFun's web player never started the song"), "never_started")
        self.assertEqual(shadow.fail_kind("KaraFun's player stopped and could not be restarted (x)"), "recover_gave_up")
        self.assertEqual(shadow.fail_kind("KaraFun's player could not be restarted: boom"), "restart_failed")
        self.assertEqual(shadow.fail_kind("Play failed: Bruno Mars secret title"), "other")


class FakeLibrary(unittest.TestCase):
    """The comparison and the logging, with scripted answers instead of the Rust library."""

    def setUp(self):
        self.lines = []
        patches = [mock.patch.object(shadow, "_log", self.lines.append), mock.patch.object(shadow, "_mode", "shadow"), mock.patch.object(shadow, "_library", lambda: object())]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tap_with(self, answers):
        calls = []

        def call(request):
            calls.append(request)
            if request["op"] == "svc_new":
                return {"id": 1}
            return answers.pop(0) if answers else {"actions": []}
        p = mock.patch.object(shadow, "_call", call)
        p.start()
        self.addCleanup(p.stop)
        return shadow.service_tap(), calls

    def test_agreement_is_logged_as_a_decision_and_silence_is_not_logged(self):
        tap, calls = self.tap_with([{"actions": [["set", "ready", "x"]]}, {"actions": []}])
        with tap.event("svc_message", {"ev": "message", "t": 1.0, "message": {}}):
            tap.fx("set", "ready")
        with tap.event("svc_message", {"ev": "message", "t": 2.0, "message": {}}):
            pass
        self.assertEqual([x for x in self.lines if "decision" in x], ["[KFWEB-SHADOW] decision kind=svc_message agree=1"])
        self.assertFalse([x for x in self.lines if "MISMATCH" in x])

    def test_a_disagreement_is_logged_with_both_lists_and_no_text(self):
        tap, _ = self.tap_with([{"actions": [["login"]]}])
        with tap.event("svc_message", {"ev": "message", "t": 1.0, "message": {"secret": "page text"}}):
            tap.fx("reload")
        mismatch = [x for x in self.lines if "MISMATCH" in x]
        self.assertEqual(mismatch, ['[KFWEB-SHADOW] MISMATCH svc_message python=[["reload"]] rust=[["login"]]'])
        self.assertNotIn("page text", " ".join(self.lines))
        self.assertIn("agree=0", [x for x in self.lines if "decision" in x][0])

    def test_the_saved_login_answer_reaches_rust_as_found_or_none_never_the_login(self):
        tap, calls = self.tap_with([{"actions": []}])
        with tap.event("svc_message", {"ev": "message", "t": 1.0, "message": {}}):
            tap.saved(True)
        self.assertEqual(calls[-1]["event"]["saved"], {"result": "found"})
        self.assertNotIn("password", str(calls))

    def test_a_waiting_check_can_be_logged_as_a_decision(self):
        tap, _ = self.tap_with([{"actions": []}])
        with tap.event("host_stall", {"ev": "watchdog", "t": 1.0}) as b:
            b.always = True
        self.assertEqual([x for x in self.lines if "decision" in x], ["[KFWEB-SHADOW] decision kind=host_stall agree=1 outcome=waiting"])

    def test_an_event_inside_another_is_sent_to_rust_after_it_and_compared_on_its_own(self):
        tap, calls = self.tap_with([{"actions": [["finish_complete"]]}, {"actions": []}])
        tap.project = shadow.project_host
        tap.machine = "host"
        with tap.event("host_status", {"ev": "status", "t": 1.0}):
            tap.fx("complete")
            with tap.event("host_finish", {"ev": "finish", "t": 1.0}):
                pass
        self.assertEqual([c["event"]["ev"] for c in calls if c["op"] == "host_step"], ["status", "finish"])
        self.assertEqual([x for x in self.lines if "decision" in x], ["[KFWEB-SHADOW] decision kind=host_status agree=1"])
        self.assertFalse([x for x in self.lines if "MISMATCH" in x])

    def test_a_python_exception_still_reaches_the_caller_and_is_not_compared(self):
        tap, _ = self.tap_with([{"actions": [["login"]]}])
        with self.assertRaises(ValueError):
            with tap.event("svc_message", {"ev": "message", "t": 1.0, "message": {}}):
                raise ValueError("boom")
        self.assertFalse([x for x in self.lines if "MISMATCH" in x])

    def test_a_library_that_stops_answering_switches_the_tap_off_quietly(self):
        tap, _ = self.tap_with([None])
        with tap.event("svc_message", {"ev": "message", "t": 1.0, "message": {}}):
            pass
        self.assertTrue(tap.dead)
        with tap.event("svc_message", {"ev": "message", "t": 2.0, "message": {}}):
            tap.fx("login")
        self.assertFalse([x for x in self.lines if "MISMATCH" in x])

    def test_declined_cases_are_noted_and_not_compared(self):
        tap, _ = self.tap_with([{"declined": "string-valued number"}])
        with tap.event("svc_message", {"ev": "message", "t": 1.0, "message": {}}):
            tap.fx("login")
        self.assertEqual(self.lines, ["[KFWEB-SHADOW] declined svc_message string-valued number"])


class Modes(unittest.TestCase):
    def test_python_mode_switches_the_shadow_off_and_unknown_values_mean_shadow(self):
        lines = []
        try:
            shadow.configure("python", lines.append)
            self.assertFalse(shadow.enabled())
            self.assertIsNone(shadow.service_tap())
            self.assertIsNone(shadow.host_tap())
            shadow.configure("nonsense", lines.append)
            self.assertEqual(shadow._mode, "shadow")
            shadow.configure("rust", lines.append)
            self.assertTrue(any("rust driving is not built yet" in x for x in lines))
        finally:
            shadow.configure("python", lambda text: None)


@unittest.skipUnless(real_library(), "needs libsingws_dsp_ffi with singws_kfweb_json (rust/build_dsp.sh)")
class RealRustTwin(unittest.TestCase):
    """The REAL Rust machine against the REAL Python service on a re-login story: they must agree, and nothing private is logged."""

    class FakeWeb:
        def __init__(self, **kw): self.commands, self.logins, self.signins = [], [], 0
        def start(self): pass
        def sign_in(self): self.signins += 1
        def command(self, name, **kw): self.commands.append(name)
        def login(self, email, password): self.logins.append((email, password))
        def close(self): pass

    def setUp(self):
        self.lines = []
        for p in (mock.patch.object(shadow, "_log", self.lines.append), mock.patch.object(shadow, "_mode", "shadow")):
            p.start()
            self.addCleanup(p.stop)

    def make(self, credentials):
        holder = {}

        def make_web(**kw):
            holder["web"] = self.FakeWeb(**kw)
            return holder["web"]
        service = karafun_web.KaraFunWebService(lambda s, d: None, profile_dir="/tmp/x", make_web=make_web, find_browser=lambda progress, **kw: Path("/x/display"), credentials=credentials)
        service._shadow = shadow.service_tap(True)
        self.assertIsNotNone(service._shadow)
        service.start()
        service._thread.join(5)
        return service, holder

    def signed_out(self, service, n):
        for _ in range(n):
            service._message({"type": "status", "signedIn": False})

    def test_saved_login_reload_and_relogin_agree_with_rust(self):
        service, holder = self.make(lambda: ("a@b.c", "SECRET-pw"))
        self.signed_out(service, 16)
        service._message({"type": "status", "signedIn": True, "plan": "Pro"})
        with mock.patch.object(karafun_web.time, "monotonic", return_value=karafun_web.time.monotonic() + 20):
            self.signed_out(service, 40)
            service._message({"type": "status", "signedIn": True, "plan": "Pro"})
        self.assertEqual(len(holder["web"].logins), 2)
        decisions = [x for x in self.lines if "decision kind=svc_message" in x]
        self.assertGreaterEqual(len(decisions), 4)
        self.assertTrue(all("agree=1" in x for x in decisions), decisions)
        self.assertFalse([x for x in self.lines if "MISMATCH" in x or "declined" in x], self.lines)
        self.assertNotIn("SECRET-pw", " ".join(self.lines))
        self.assertNotIn("a@b.c", " ".join(self.lines))

    def test_without_a_saved_login_the_browser_page_opens_once_and_rust_agrees(self):
        service, holder = self.make(lambda: None)
        self.signed_out(service, 61)
        self.assertEqual(holder["web"].signins, 1)
        self.assertFalse([x for x in self.lines if "MISMATCH" in x], self.lines)
        self.assertTrue([x for x in self.lines if "decision kind=svc_message agree=1" in x])

    def test_an_engine_crash_resets_the_profile_once_and_rust_agrees(self):
        service, holder = self.make(lambda: ("a@b.c", "pw"))
        crash = {"type": "log", "message": karafun_web.ENGINE_CRASH_TEXT}
        with mock.patch.object(karafun_web.KaraFunWebService, "_reset_profile_after_engine_crash", lambda self: (self._fx("reset") if not self._profile_reset_done else None, setattr(self, "_profile_reset_done", True))):
            service._message(crash)
            service._message(crash)
        self.assertFalse([x for x in self.lines if "MISMATCH" in x], self.lines)
        self.assertEqual(len([x for x in self.lines if "decision kind=svc_message agree=1" in x]), 1)      # the second crash does nothing on either side


@unittest.skipUnless(real_library(), "needs libsingws_dsp_ffi with singws_kfweb_json (rust/build_dsp.sh)")
class RealHostTwin(unittest.TestCase):
    """The REAL Rust host machine against the REAL KaraFunWebHost (fake app and player) on whole songs: a normal song, a song held while the player signs in, and a
    song that never starts. Every compared decision must agree."""

    def setUp(self):
        import test_karafun_web_host as fx
        self.fx = fx
        self.lines = []
        for p in (mock.patch.object(shadow, "_log", self.lines.append), mock.patch.object(shadow, "_mode", "shadow")):
            p.start()
            self.addCleanup(p.stop)

    def make(self, state):
        app, entry, service, host = self.fx.make(state)
        host._arm_watchdog = self.fx.KaraFunWebHost._arm_watchdog.__get__(host)       # the real watchdog, fired by hand below
        host._shadow = shadow.host_tap()
        service._shadow = shadow.service_tap(True)
        self.assertIsNotNone(host._shadow)
        self.addCleanup(host._shadow.close)             # the Rust side keeps at most 16 live sessions
        self.addCleanup(service._shadow.close)
        return app, entry, service, host

    def agreed(self, *kinds):
        self.assertFalse([x for x in self.lines if "MISMATCH" in x or "declined" in x], self.lines)
        got = {re.search(r"kind=(\w+)", x).group(1) for x in self.lines if "decision kind=" in x}
        for kind in kinds:
            self.assertIn(kind, got, self.lines)
        self.assertTrue(all("agree=1" in x for x in self.lines if "decision kind=" in x), self.lines)

    def test_a_song_from_start_to_the_end_agrees_with_rust(self):
        app, entry, service, host = self.make("ready")
        fired = []
        from PyQt6.QtCore import QTimer
        with mock.patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: fired.append(fn)):
            self.assertTrue(host.start(entry, 0, 100))
            host.asked["active"] = app._active_external_karafun
            fired.pop(0)()                                              # one watchdog check while waiting: still waiting
            clock = [5000.0]
            with mock.patch.object(self.fx.host_module.time, "monotonic", side_effect=lambda: clock[0]):
                host._handle(self.fx.status(position=2.0))              # started
                clock[0] += 60
                host._handle(self.fx.status(position=62.0))
                clock[0] += 60
                host._handle(self.fx.status(playing=False, song=None))   # gone: the song is over
                host.on_finish()
        self.agreed("host_start", "host_stall", "host_status", "host_finish")
        self.assertEqual(app.finished[0][0], "complete")

    def test_the_finish_that_runs_inside_the_ending_status_agrees_with_rust(self):
        """The real app finishes the song INSIDE the status event that ended it (2026-11-15 show: six songs, six false MISMATCH host_status python=[complete] rust=[])."""
        app, entry, service, host = self.make("ready")
        fired = []
        from PyQt6.QtCore import QTimer
        original = app._finish_external_karafun_playback

        def finish_and_report(*a, **k):
            result = original(*a, **k)
            host.on_finish()
            return result
        app._finish_external_karafun_playback = finish_and_report
        with mock.patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: fired.append(fn)):
            self.assertTrue(host.start(entry, 0, 100))
            host.asked["active"] = app._active_external_karafun
            clock = [5000.0]
            with mock.patch.object(self.fx.host_module.time, "monotonic", side_effect=lambda: clock[0]):
                host._handle(self.fx.status(position=2.0))
                clock[0] += 120
                host._handle(self.fx.status(playing=False, song=None))
        self.agreed("host_start", "host_status", "host_finish")
        self.assertEqual(app.finished[0][0], "complete")

    def test_a_song_asked_for_while_the_player_signs_in_is_held_then_played_and_rust_agrees(self):
        app, entry, service, host = self.make("sign_in")
        service.state = "sign_in"
        self.assertTrue(host.start(entry, 0, 100))
        self.assertNotIn("play", [c[0] for c in service.web.calls])
        service.state = "ready"
        host._service_state("ready", "Signed in")
        self.assertIn("play", [c[0] for c in service.web.calls])
        self.agreed("host_start", "host_service")

    def test_a_song_that_never_starts_fails_at_the_same_second_in_both(self):
        app, entry, service, host = self.make("ready")
        from PyQt6.QtCore import QTimer
        pending = []
        clock = [1000.0]
        with mock.patch.object(QTimer, "singleShot", side_effect=lambda ms, fn: pending.append(fn)), \
                mock.patch.object(self.fx.host_module.time, "monotonic", side_effect=lambda: clock[0]):
            host.start(entry, 0, 100)
            host.asked["active"] = app._active_external_karafun
            for _ in range(70):
                clock[0] += 1.0
                if not pending:
                    break
                pending.pop(0)()
        self.assertEqual(app.statuses[-1][0], "failed")
        self.agreed("host_start", "host_stall")
        self.assertTrue([x for x in self.lines if "decision kind=host_stall agree=1" in x and "outcome=waiting" not in x], self.lines)


class Wiring(unittest.TestCase):
    def test_both_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(spec).read_text(encoding="utf-8")
            self.assertIn('"karafun_web_shadow.py"', text, spec)
            self.assertIn("'karafun_web_shadow'", text, spec)
            self.assertIn('"phrase_shadow.py"', text, spec)
            self.assertIn("'phrase_shadow'", text, spec)

    def test_the_app_configures_the_shadow_from_the_setting_and_it_defaults_to_shadow(self):
        src = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"karafun_web_engine": "shadow",', src)
        self.assertIn('karafun_web_shadow.configure(self.settings.get("karafun_web_engine", "shadow"), _diag)', src)

    def test_the_host_taps_every_event_the_rust_machine_models(self):
        src = Path("karafun_web_host.py").read_text(encoding="utf-8")
        for kind in ("host_start", "host_service", "host_status", "host_error", "host_stall", "host_finish"):
            self.assertIn(f'"{kind}"', src, kind)


if __name__ == "__main__":
    unittest.main()
