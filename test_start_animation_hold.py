"""1.0.4.0 (2026-11-19 show: "make sure the animation finishes before the actual audio starts playing"): when nothing is playing and the next song is a native one, the
singer-start animation is shown first and the real start runs after the hold. Stop cancels it; anything unusual starts at once, as before."""
import _test_profile  # noqa: F401
import unittest
from unittest import mock

from test_recent_regressions import load_main_module, make_app


class StartHoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_main_module()

    def make(self, hold=3.1, karafun=False, shown=True, playing=False):
        app = make_app(self.m)
        app.settings["show_start_animation_hold_s"] = hold
        app.queue = [{"name": "Dan", "songs": [{"artist": "NOFX", "title": "Linoleum"}]}]
        app.karaoke_transport = object() if playing else None
        app._next_in_progress = False
        app._pending_play_start_token = None
        app._stop_in_progress = False
        app._intro_loop_active = False
        app._start_hold_token = 0
        app._start_hold_pending = False
        app._start_hold_released = 0
        app._singer_start_vfx_preshown = False
        app._next_up_queue_target = lambda: (app.queue[0], app.queue[0]["songs"][0])
        app._is_external_karafun_entry = lambda entry: karafun
        app._next_up_transition_payload_for = lambda singer, entry: {"singer": "Dan", "artist": "NOFX", "title": "Linoleum"}
        self.vfx = []
        app._trigger_show_screen_singer_start_vfx = lambda *a, **k: (self.vfx.append(a), shown)[1]
        self.starting = []
        app._set_play_control_starting = lambda on: self.starting.append(bool(on))
        self.timers = []
        patcher = mock.patch.object(self.m.QTimer, "singleShot", staticmethod(lambda ms, fn: self.timers.append((ms, fn))))
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    def test_the_animation_comes_first_and_the_start_runs_once_after_the_hold(self):
        app = self.make()
        self.assertTrue(app._start_hold_gate(False, "internal"), "this call is deferred")
        self.assertEqual(self.vfx, [("Dan", "NOFX", "Linoleum")])
        self.assertEqual(self.timers[0][0], 3100)
        self.assertEqual(self.starting, [True])
        self.assertTrue(app._start_hold_pending and app._singer_start_vfx_preshown)
        # the operator presses Play again during the animation: ignored, no second animation
        self.assertTrue(app._start_hold_gate(False, "internal"))
        self.assertEqual(len(self.vfx), 1)
        # the hold ends: the real start is called again and is let through exactly once
        calls = []
        def fake_play(skip_confirmation=False, *, command_source="internal"):
            calls.append((skip_confirmation, command_source, app._start_hold_gate(skip_confirmation, command_source)))
        app.play_next_file = fake_play
        self.timers[0][1]()
        self.assertEqual(calls, [(False, "internal", False)], "let through")
        self.assertFalse(app._start_hold_pending)
        self.assertEqual(app._start_hold_released, 0, "the pass is used up")

    def test_a_zip_resume_of_the_same_start_is_never_held_again(self):
        """The 2026-11-20 show: a ZIP song rolls its start back to prepare the files and calls play_next_file again as zip_resume; the animation played twice."""
        app = self.make()
        self.assertTrue(app._start_hold_gate(False, "manual_idle"))
        calls = []
        def fake_play(skip_confirmation=False, *, command_source="internal"):
            calls.append((command_source, app._start_hold_gate(skip_confirmation, command_source)))
        app.play_next_file = fake_play
        self.timers[0][1]()                                           # the hold ends: the first pass, let through
        self.assertEqual(calls, [("manual_idle", False)])
        # the start rolled back for the ZIP preparation and comes back as zip_resume: no second animation, no second wait
        self.assertFalse(app._start_hold_gate(True, "zip_resume"))
        self.assertEqual(len(self.vfx), 1)
        self.assertEqual(len(self.timers), 2, "only the 15 s flag clean-up was added, no second hold")
        # even with another source name, a call right after a hold is the same start
        self.assertFalse(app._start_hold_gate(True, "internal"))
        self.assertEqual(len(self.vfx), 1)

    def test_a_zip_resume_without_an_earlier_hold_is_not_held_either(self):
        app = self.make()
        self.assertFalse(app._start_hold_gate(True, "zip_resume"))
        self.assertEqual((self.vfx, self.timers), ([], []))

    def test_a_later_start_after_the_window_is_held_again(self):
        app = self.make()
        app._start_hold_gate(False, "manual_idle")
        app.play_next_file = lambda *a, **k: None
        self.timers[0][1]()
        with mock.patch.object(self.m.time, "monotonic", return_value=object.__getattribute__(app, "__dict__")["_start_hold_done_at"] + 31.0):
            self.assertTrue(app._start_hold_gate(False, "manual_idle"), "31 s later it is a new start")

    def test_stop_during_the_animation_cancels_the_start(self):
        app = self.make()
        app._start_hold_gate(False, "internal")
        app._cancel_start_hold("stop_playback")
        self.assertFalse(app._start_hold_pending or app._singer_start_vfx_preshown)
        self.assertEqual(self.starting[-1], False)
        calls = []
        app.play_next_file = lambda *a, **k: calls.append(1)
        self.timers[0][1]()
        self.assertEqual(calls, [], "the deferred start never happens")

    def test_no_hold_when_it_is_off_karafun_something_is_playing_or_nothing_is_shown(self):
        for kw in ({"hold": 0}, {"karafun": True}, {"playing": True}, {"shown": False}):
            app = self.make(**kw)
            self.assertFalse(app._start_hold_gate(False, "internal"), kw)
            self.assertFalse(app._start_hold_pending, kw)
            self.assertFalse(app._singer_start_vfx_preshown, kw)
            self.assertEqual(self.timers, [], kw)

    def test_the_confirmed_start_does_not_show_the_animation_a_second_time(self):
        app = self.make()
        app._trigger_show_screen_singer_start_vfx = lambda *a, **k: self.fail("replaced below")
        real = self.m.KaraokeApp._trigger_show_screen_singer_start_vfx
        app._singer_start_vfx_preshown = True
        self.assertFalse(real.__wrapped__(app, "Dan", "NOFX", "Linoleum", generation=5) if hasattr(real, "__wrapped__") else real(app, "Dan", "NOFX", "Linoleum", generation=5))
        self.assertFalse(object.__getattribute__(app, "__dict__").get("_singer_start_vfx_preshown"), "the flag is used up")

    def test_the_hold_is_clamped_and_read_from_the_setting(self):
        self.assertEqual(self.make(hold=3.1)._start_hold_seconds(), 3.1)
        self.assertEqual(self.make(hold=99)._start_hold_seconds(), 8.0)
        self.assertEqual(self.make(hold=-1)._start_hold_seconds(), 0.0)
        self.assertEqual(self.make(hold="x")._start_hold_seconds(), 0.0)
        self.assertEqual(self.m.DEFAULTS["show_start_animation_hold_s"], 3.1)

    def test_play_next_file_asks_the_gate_first_and_stop_cancels_it(self):
        import inspect
        play = inspect.getsource(self.m.KaraokeApp.play_next_file)
        self.assertLess(play.index("self._start_hold_gate(skip_confirmation, command_source)"), play.index("_flush_deferred_remote_adds"))
        src = open("0.2.18.1.py", encoding="utf-8").read()
        self.assertIn('self._cancel_start_hold("stop_playback")', src)


if __name__ == "__main__":
    unittest.main()
