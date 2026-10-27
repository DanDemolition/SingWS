"""Stage A5 step 8: when a song counts as SUNG, shadowed by Rust.

A song's start stores a pending performance record; a real media end (`_finish_media_end_cleanup`) commits it BEFORE it stops playback, and any stop (`stop_playback`: the Stop
button, a skip, a removal, closing the app) discards it. The REAL start/commit, `_finish_media_end_cleanup`, `stop_playback`, `_commit_pending_performance` and
`_discard_pending_performance` run here over random event sequences (start, media end, stop, in any order and any number of times); Rust tracks the stored record across the
whole sequence, and the app's effects (request completed on the server, history recorded, request tombstoned) must be exactly the ones the rule gives, with no MISMATCH. This test
also derives the expected effects on its own, without Rust.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import os
import random
import unittest
from pathlib import Path
from unittest import mock

import start_shadow
import test_show_critical_regressions as base

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target-a5/release/libsingws_dsp_ffi.dylib"))


class Recording(start_shadow.StartShadow):
    def __init__(self, log):
        super().__init__(log)
        self.performance = []

    def check_performance(self, event, action, **kw):
        r = super().check_performance(event, action, **kw)
        self.performance.append((event, action, r))
        return r


class PerformanceWiring(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def make(self, songs, enabled=True):
        """An app whose queue holds one singer per song, `songs` = [remote request id or None, ...]."""
        queue = [{"name": f"S{i}", "singer_id": f"sid{i}", "skipped": False,
                  "songs": [dict({"request_uid": f"r{i}", "song_info": f"/lib/s{i}.mp3", "skipped": False, "artist": "A", "title": "T"}, **({"remote_request_id": rid} if rid is not None else {}))]}
                 for i, rid in enumerate(songs)]
        app = self.playback_app(queue)
        app.settings = {"start_shadow": enabled}
        for stub in ("play_mp3", "play_mp4", "play_cdg_mp3_dual", "play_mp3g_zip"):
            app.__dict__.pop(stub, None)
        app._is_external_karafun_entry = lambda e: False
        app.history = []
        app.deleted = []
        app._record_singer_history_play = lambda name, entry, path, key=0, tempo_percent=None: app.history.append((name, path))
        app._delete_remote_request = lambda rid, entry=None, singer_name="", reason="": app.deleted.append((rid, reason))
        app._pending_start_callbacks = []

        def start(*_a, **_k):
            transport = type("T", (), {})()
            transport.started = type("S", (), {"connect": lambda _s, fn: app._pending_start_callbacks.append(fn)})()
            app.karaoke_transport = transport
            return True

        for n in ("play_mp3", "play_mp4", "play_cdg_mp3_dual", "play_mp3g_zip"):
            setattr(app, n, start)
        app._flush_deferred_remote_adds = lambda *a, **k: None
        return app

    def run_events(self, app, events):
        lines = []
        shadow = Recording(lambda text: lines.append(str(text)))
        self.singws._START_SHADOW = shadow
        with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))), mock.patch.object(self.singws.os.path, "exists", lambda p: True), \
                mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None), mock.patch("builtins.print", lambda *a, **k: None):
            for ev in events:
                self.apply(app, ev)
        return shadow, lines

    def apply(self, app, ev):
        if ev == "start":
            app._pending_start_callbacks.clear()
            app.play_next_file(skip_confirmation=True)
            for fn in list(app._pending_start_callbacks):
                fn()
        elif ev == "media_end":
            app._finish_media_end_cleanup(False, False)
            app.karaoke_transport = None
        elif ev == "stop":
            try:
                app.stop_playback(skip_confirmation=True)
            except RuntimeError:
                pass   # the rest of the teardown needs a fully built app; the sung decision is the first thing stop_playback does
            app.karaoke_transport = None

    def test_random_event_sequences_agree_with_rust_and_with_an_independent_model(self):
        rng = random.Random(20261031)
        counts = {}
        for case in range(300):
            ids = [rng.choice([None, 7, "r-9", 0, 1001]) for _ in range(rng.randint(1, 6))]
            app = self.make(ids)
            events = [rng.choice(["start", "start", "media_end", "stop"]) for _ in range(rng.randint(1, 12))]
            # independent model of what must happen
            queue_ids = list(ids)
            pending, expected_history, expected_completed, expected_deleted, overwritten = None, 0, [], [], 0
            for ev in events:
                if ev == "start":
                    if queue_ids:
                        if pending is not None:
                            overwritten += 1
                        pending = ("id", queue_ids.pop(0))
                elif ev == "media_end" and pending is not None:
                    expected_history += 1
                    if pending[1] is not None:
                        expected_completed.append(pending[1])
                    pending = None
                elif ev == "stop" and pending is not None:
                    if pending[1] is not None:
                        expected_deleted.append((pending[1], "host_stopped:stop_playback"))
                    pending = None
            shadow, lines = self.run_events(app, events)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, ids, events))
            self.assertEqual(len(app.history), expected_history, (case, ids, events))
            self.assertEqual(app.completed_requests, expected_completed, (case, ids, events))
            self.assertEqual(app.deleted, expected_deleted, (case, ids, events))
            self.assertEqual(app.__dict__.get("_pending_performance") is not None, pending is not None, (case, ids, events))
            self.assertEqual(len([x for x in lines if "NOTE pending_performance_overwritten" in x]), 1 if overwritten else 0, (case, events))
            self.assertTrue(all(r is True for _e, _a, r in shadow.performance), (case, shadow.performance))
            for _e, a, _r in shadow.performance:
                counts[a] = counts.get(a, 0) + 1
        for action in ("set", "overwritten", "commit", "discard", "none"):
            self.assertGreater(counts.get(action, 0), 30, counts)

    def test_a_song_that_ends_is_committed_and_the_stop_after_it_finds_nothing(self):
        app = self.make([42])
        shadow, lines = self.run_events(app, ["start", "media_end"])
        self.assertEqual((app.completed_requests, len(app.history), app.deleted), ([42], 1, []))
        self.assertEqual([(e, a) for e, a, _ in shadow.performance], [("confirmed", "set"), ("media_end", "commit"), ("stop", "none")],
                         "the cleanup commits first, then stop_playback runs and discards nothing")

    def test_a_song_that_is_stopped_is_not_recorded_and_its_request_is_retired(self):
        app = self.make([42])
        shadow, lines = self.run_events(app, ["start", "stop"])
        self.assertEqual((app.completed_requests, app.history, app.deleted), ([], [], [(42, "host_stopped:stop_playback")]))
        self.assertEqual([(e, a, r) for e, a, r in shadow.performance], [("confirmed", "set", True), ("stop", "discard", True)])

    def test_the_next_song_starting_over_a_pending_one_is_reported_not_endorsed(self):
        app = self.make([1, 2])
        shadow, lines = self.run_events(app, ["start", "start"])
        self.assertEqual([(e, a) for e, a, _ in shadow.performance], [("confirmed", "set"), ("confirmed", "overwritten")])
        self.assertEqual(len([x for x in lines if "NOTE pending_performance_overwritten" in x]), 1)
        self.assertEqual((app.completed_requests, app.history, app.deleted), ([], [], []), "the first song was neither recorded nor retired")

    def test_with_the_setting_off_nothing_is_compared_or_logged(self):
        app = self.make([5], enabled=False)
        shadow, lines = self.run_events(app, ["start", "media_end", "stop"])
        self.assertEqual((shadow.performance, [x for x in lines if "START-SHADOW" in x]), ([], []))
        self.assertEqual((app.completed_requests, len(app.history)), ([5], 1))

    def test_a_shadow_that_blows_up_cannot_change_what_is_recorded(self):
        class Bad:
            available = True

            def __getattr__(self, name):
                def boom(*a, **k):
                    raise RuntimeError(name)
                return boom

        app = self.make([5])
        with mock.patch.object(self.singws, "_start_shadow_for", lambda s: Bad()), mock.patch.object(self.singws, "_diag", lambda *a, **k: None), \
                mock.patch.object(self.singws.os.path, "exists", lambda p: True), mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None), \
                mock.patch("builtins.print", lambda *a, **k: None):
            self.apply(app, "start")
            self.apply(app, "media_end")
        self.assertEqual((app.completed_requests, len(app.history)), ([5], 1))

    def test_callers_with_other_reasons_are_not_compared(self):
        app = self.make([5])
        shadow, lines = self.run_events(app, ["start"])
        shadow.performance.clear()
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            app._discard_pending_performance(reason="something_else")
        self.assertEqual(shadow.performance, [])
        self.assertEqual(app.deleted, [(5, "host_stopped:something_else")])


if __name__ == "__main__":
    unittest.main()
