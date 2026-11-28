"""Pre-warming the KaraFun player (2026-11-27): a KaraFun request entering the queue brings the player up and signed in, so its song does not wait 33-44 s for the
sign-in (the first KaraFun song of the 2026-11-22 and 11-26 shows). The adapter only asks the driver to (re)start its set-up; the driver does nothing when the
service is already coming up, ready or playing."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import types
import unittest
from pathlib import Path
from unittest import mock

import karafun_web_host_rust as hr
import test_show_critical_regressions as base


def make_host(asked=None, alive=True):
    host = object.__new__(hr.KaraFunWebHostRust)
    host.asked = asked
    host._proc = mock.Mock(poll=lambda: None if alive else 1)       # a driver process that is running (poll() is None) or has exited
    return host


class AdapterWarm(unittest.TestCase):
    def test_warm_asks_the_driver_to_start_its_setup_once_the_process_is_up(self):
        host = make_host()
        sent = []
        with mock.patch.object(hr.KaraFunWebHostRust, "enabled", return_value=True), mock.patch.object(hr.KaraFunWebHostRust, "_ensure_proc", return_value=True), \
             mock.patch.object(hr.KaraFunWebHostRust, "_send", side_effect=lambda m: sent.append(m) or 0):
            self.assertTrue(host.warm())
        self.assertEqual(sent, [{"op": "retry_service"}])

    def test_warm_never_starts_a_song_and_does_nothing_while_one_is_asked(self):
        sent = []
        with mock.patch.object(hr.KaraFunWebHostRust, "enabled", return_value=True), mock.patch.object(hr.KaraFunWebHostRust, "_ensure_proc", return_value=True), \
             mock.patch.object(hr.KaraFunWebHostRust, "_send", side_effect=lambda m: sent.append(m) or 0):
            self.assertFalse(make_host(asked={"id": 5}).warm())
        self.assertEqual(sent, [])

    def test_warm_reports_a_driver_that_has_exited_and_does_not_mistake_the_zero_answer_for_failure(self):
        with mock.patch.object(hr.KaraFunWebHostRust, "enabled", return_value=True), mock.patch.object(hr.KaraFunWebHostRust, "_ensure_proc", return_value=True), \
             mock.patch.object(hr.KaraFunWebHostRust, "_send", return_value=0):
            self.assertTrue(make_host(alive=True).warm())
            self.assertFalse(make_host(alive=False).warm())

    def test_warm_does_nothing_when_disabled_or_the_process_is_not_there_and_never_raises(self):
        sent = []
        with mock.patch.object(hr.KaraFunWebHostRust, "_send", side_effect=lambda m: sent.append(m) or 0):
            with mock.patch.object(hr.KaraFunWebHostRust, "enabled", return_value=False):
                self.assertFalse(make_host().warm())
            with mock.patch.object(hr.KaraFunWebHostRust, "enabled", return_value=True), mock.patch.object(hr.KaraFunWebHostRust, "_ensure_proc", return_value=False):
                self.assertFalse(make_host().warm())
            with mock.patch.object(hr.KaraFunWebHostRust, "enabled", side_effect=RuntimeError("boom")):
                self.assertFalse(make_host().warm())
        self.assertEqual(sent, [])


class AppHook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = base.load_main_module()
        cls.KA = cls.mod.KaraokeApp

    def fake_app(self, settings=None, added=True, warm_result=True):
        calls = {"prewarm": [], "warm": 0}

        class Host:
            def warm(self_inner):
                calls["warm"] += 1
                return warm_result

        app = types.SimpleNamespace(settings=dict({"karafun_provider_enabled": True}, **(settings or {})))
        app._add_song_to_queue_impl = lambda *a, **k: added
        app._karafun_prewarm = lambda reason="queue_add": calls["prewarm"].append(reason)
        app._karafun_web_host = lambda: Host()
        app.__dict__  # SimpleNamespace has one
        return app, calls

    def add(self, app, song_data):
        return self.KA._add_song_to_queue(app, "Ada", song_data)

    def test_a_karafun_request_in_the_queue_asks_for_the_warm_up_and_other_songs_do_not(self):
        app, calls = self.fake_app()
        self.assertTrue(self.add(app, ("karafun_streaming:kf_123", 0)))
        self.assertEqual(calls["prewarm"], ["queue_add"])
        app, calls = self.fake_app()
        self.add(app, ("/lib/Band-Song.zip", 0))
        self.add(app, ("external_karafun:abc", 0))
        self.assertEqual(calls["prewarm"], [])

    def test_an_add_that_was_refused_does_not_warm_anything_and_keeps_its_answer(self):
        app, calls = self.fake_app(added=False)
        self.assertFalse(self.add(app, ("karafun_streaming:kf_123", 0)))
        self.assertEqual(calls["prewarm"], [])

    def test_a_broken_warm_up_never_changes_the_add(self):
        app, calls = self.fake_app()
        app._karafun_prewarm = mock.Mock(side_effect=RuntimeError("boom"))
        self.assertTrue(self.add(app, ("karafun_streaming:kf_123", 0)))
        self.assertTrue(self.add(app, "karafun_streaming:kf_9"))       # a bare path is understood too

    def test_the_warm_up_respects_the_setting_the_provider_switch_and_a_two_minute_gap(self):
        fired = []
        with mock.patch.object(self.mod.QTimer, "singleShot", side_effect=lambda ms, fn: (fired.append(ms), fn())):
            for settings, expect in (({}, 1), ({"karafun_prewarm": False}, 0), ({"karafun_provider_enabled": False}, 0)):
                app, calls = self.fake_app(settings)
                app.__dict__.pop("_karafun_prewarm_at", None)
                fired.clear()
                self.KA._karafun_prewarm(app, "t")
                self.assertEqual(calls["warm"], expect, settings)
            app, calls = self.fake_app()
            self.KA._karafun_prewarm(app, "t")
            self.KA._karafun_prewarm(app, "t")            # inside the two-minute gap
            self.assertEqual(calls["warm"], 1)
            app._karafun_prewarm_at -= 121.0
            self.KA._karafun_prewarm(app, "t")
            self.assertEqual(calls["warm"], 2)

    def test_the_default_is_on_and_the_python_host_has_no_warm_up(self):
        self.assertIs(self.mod.DEFAULTS.get("karafun_prewarm"), True)
        import karafun_web_host
        self.assertFalse(hasattr(karafun_web_host.KaraFunWebHost, "warm"))


if __name__ == "__main__":
    unittest.main()
