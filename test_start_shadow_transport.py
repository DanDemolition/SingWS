"""Stage A5 step 6: the arguments that reach `_start_karaoke_transport`, shadowed by Rust.

The REAL `play_next_file` -> `play_*` -> `_play_*` -> `_start_karaoke_transport` chain runs (only the mpv transport behind it is replaced by a recorder) over random song
shapes, keys, phrase-start offsets and durations: the shadow must find exactly one transport comparison per start and no MISMATCH, and the arguments the recorder saw must
equal the ones this test derives on its own. `play_mp3g_zip`'s merge with the values stashed for an uncached ZIP is driven the same way.
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


class CountingShadow(start_shadow.StartShadow):
    def __init__(self, log):
        super().__init__(log)
        self.compared = {"transport": 0, "zip": 0}

    def check_transport(self, req, observed):
        r = super().check_transport(req, observed)
        if r is not None:
            self.compared["transport"] += 1
        return r

    def check_zip_args(self, req, observed):
        r = super().check_zip_args(req, observed)
        if r is not None:
            self.compared["zip"] += 1
        return r


class TransportWiring(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def make_app(self, song_info, key, phrase_start, duration, zip_ready=None, enabled=True):
        entry = {"request_uid": "rid-1", "song_info": song_info, "artist": "A", "title": "T", "skipped": False, "key": key, "duration": duration}
        singer = {"name": "Dana", "singer_id": "sid-Dana", "skipped": False, "songs": [entry]}
        app = self.playback_app([singer])
        app.settings = {"start_shadow": enabled}
        for stub in ("play_mp3", "play_mp4", "play_cdg_mp3_dual", "play_mp3g_zip"):
            app.__dict__.pop(stub, None)   # the base harness replaces play_mp3; the real chain is wanted here
        app._resolve_phrase_start = lambda *_a, **_k: phrase_start
        app.zip_cache = type("Z", (), {"cached_paths_if_ready": lambda _s, p: zip_ready if zip_ready is not None else (None, None)})()
        app.is_mp3g_zip = lambda p: True
        app._is_external_karafun_entry = lambda e: False
        app._reset_end_silence_state = lambda: None
        app._prepare_karaoke_start = lambda p: None
        app._playback_safe_ui_delay_ms = lambda ms: ms
        app._start_lyrics_background_video = lambda: None
        app._mp3g_pending_start, app._mp3g_pending_loop, app._mp3g_pending_duration = {}, {}, {}
        app.transport_calls = []

        def recorder(**kw):
            app.transport_calls.append(kw)
            return True

        app._start_mpv_karaoke_transport = recorder
        return app

    def run_app(self, app, action):
        lines = []
        shadow = CountingShadow(lambda text: lines.append(str(text)))
        self.singws._START_SHADOW = shadow
        with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))), \
                mock.patch.object(self.singws.os.path, "exists", lambda p: True), \
                mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None):
            action(app)
        return shadow, lines

    def play(self, song_info, key=0, phrase_start=0.0, duration=180, zip_ready=None, enabled=True):
        app = self.make_app(song_info, key, phrase_start, duration, zip_ready, enabled)
        shadow, lines = self.run_app(app, lambda a: a.play_next_file())
        return app, shadow, lines

    def test_every_wrapper_forwards_what_rust_expects(self):
        ready = ("/t/x.cdg", "/t/x.mp3")
        cases = [("/lib/a.mp4", "mp4"), ("/lib/a.mp3", "mp3"), ("/lib/a.zip", "cdg"), ("/lib/a.cdg", "cdg"), (("/lib/a.cdg", "/lib/a.mp3"), "cdg"),
                 (("/lib/a.mp4", "/lib/b.mp4"), "mp4"), (("/lib/a.mp3", "x"), "mp3")]
        for info, kind in cases:
            app, shadow, lines = self.play(info, key=2, phrase_start=1.5, duration=200, zip_ready=ready)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (info, lines))
            self.assertEqual(shadow.compared["transport"], 1, (info, lines))
            self.assertEqual(len(app.transport_calls), 1, info)
            kw = app.transport_calls[0]
            self.assertEqual(kw["mode"], {"mp4": "mp4", "cdg": "cdg", "mp3": "audio"}[kind], info)
            self.assertEqual(kw["semitones"], 2)
            self.assertEqual(kw["start_seconds"], 1.5)
            if kind == "mp4":
                self.assertEqual((kw["audio_path"], kw["video_path"], kw["loop_seconds"], kw["duration_seconds"]), (kw["video_path"], kw["audio_path"], None, None))
            elif kind == "mp3":
                self.assertEqual((kw["video_path"], kw["loop_seconds"], kw["duration_seconds"]), (None, None, None))
            else:
                self.assertEqual(kw["duration_seconds"], 200.0 if info == "/lib/a.zip" else 200, info)
                self.assertTrue(kw["video_path"].endswith(".cdg") and kw["audio_path"].endswith(".mp3"), kw)

    def test_random_starts_agree_with_rust_over_the_real_chain(self):
        rng = random.Random(20261028)
        shapes = ["/lib/a.mp4", "/lib/a.mp3", "/lib/a.zip", "/lib/a.cdg", ("/lib/a.cdg", "/lib/a.mp3"), ("/lib/a.mp4", "/lib/o.mp4"), ("/lib/a.mp3", 0), ["/lib/a.mp4", "/lib/o.mp4"]]
        seen = {}
        for case in range(300):
            info = rng.choice(shapes)
            key = rng.choice([0, 0, 1, -1, 5, -7, 12, 2.0, None])
            phrase = rng.choice([0.0, 0.0, 3.25, 17.0, 0.5, -1.0])
            duration = rng.choice([0, 95, 180, 241, 3600])
            app, shadow, lines = self.play(info, key=key, phrase_start=phrase, duration=duration, zip_ready=("/t/x.cdg", "/t/x.mp3"))
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, info, key, phrase, duration))
            self.assertEqual(shadow.compared["transport"], 1, (case, info, lines))
            mode = app.transport_calls[0]["mode"]
            seen[mode] = seen.get(mode, 0) + 1
        self.assertEqual(sorted(seen), ["audio", "cdg", "mp4"])
        self.assertTrue(all(n > 20 for n in seen.values()), seen)

    def test_the_zip_merge_agrees_with_rust_for_random_calls_and_stashes(self):
        rng = random.Random(20261029)
        zp = "/lib/a.zip"
        numbers = [None, 0, 0.0, 2.5, 30, -4.0]
        merged = 0
        for case in range(400):
            app = self.make_app(zp, 0, 0.0, 180, zip_ready=("/t/x.cdg", "/t/x.mp3"))
            app._mp3g_pending_start, app._mp3g_pending_loop, app._mp3g_pending_duration = {}, {}, {}
            for d in (app._mp3g_pending_start, app._mp3g_pending_loop, app._mp3g_pending_duration):
                if rng.random() < 0.5:
                    d[zp] = rng.choice([x for x in numbers if x is not None])
                if rng.random() < 0.2:
                    d["/lib/other.zip"] = 99.0
            start, loop, dur = rng.choice(numbers), rng.choice(numbers), rng.choice(numbers)
            expected_pending = (app._mp3g_pending_start.get(zp), app._mp3g_pending_loop.get(zp), app._mp3g_pending_duration.get(zp))
            shadow, lines = self.run_app(app, lambda a: a.play_mp3g_zip(zp, 3, start_seconds=start, loop_seconds=loop, duration_seconds=dur))
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, start, loop, dur, expected_pending))
            self.assertEqual((shadow.compared["zip"], shadow.compared["transport"]), (1, 1), (case, lines))
            kw = app.transport_calls[0]
            if any(p is not None and p not in (0, 0.0) for p in expected_pending):
                merged += 1
            # the stash is used up by a start that proceeds
            self.assertNotIn(zp, app._mp3g_pending_start)
        self.assertGreater(merged, 100)

    def test_an_uncached_zip_stashes_and_the_next_call_gets_the_stash(self):
        zp = "/lib/a.zip"
        app = self.make_app(zp, 0, 0.0, 180, zip_ready=(None, None))
        app._set_processing_text = lambda *a, **k: None
        app._zip_extract_thread = None
        # first call: not extracted yet, the values are stashed (QThread is replaced: no worker runs)
        with mock.patch.object(self.singws, "QThread", lambda *a, **k: mock.MagicMock()), mock.patch.object(self.singws, "ZipExtractWorker", lambda *a, **k: mock.MagicMock()):
            self.run_app(app, lambda a: a.play_mp3g_zip(zp, 0, start_seconds=7.5, loop_seconds=None, duration_seconds=210))
        self.assertEqual((app._mp3g_pending_start[zp], app._mp3g_pending_duration[zp]), (7.5, 210.0))
        app.zip_cache = type("Z", (), {"cached_paths_if_ready": lambda _s, p: ("/t/x.cdg", "/t/x.mp3")})()
        shadow, lines = self.run_app(app, lambda a: a.play_mp3g_zip(zp, 0))
        self.assertEqual([x for x in lines if "MISMATCH" in x], [], lines)
        kw = app.transport_calls[0]
        self.assertEqual((kw["start_seconds"], kw["duration_seconds"], kw["loop_seconds"]), (7.5, 210.0, None), "the second call plays with what the first one stashed")
        self.assertEqual(shadow.compared["zip"], 1)

    def test_a_direct_transport_call_with_no_wrapper_is_not_compared(self):
        app = self.make_app("/lib/a.mp4", 0, 0.0, 180)
        shadow, lines = self.run_app(app, lambda a: a._start_karaoke_transport(audio_path="/x.mp3", video_path=None, mode="audio", semitones=0))
        self.assertEqual((shadow.compared["transport"], [x for x in lines if "START-SHADOW" in x]), (0, []))

    def test_with_the_setting_off_nothing_is_compared_or_logged(self):
        app, shadow, lines = self.play("/lib/a.mp4", enabled=False)
        self.assertEqual(len(app.transport_calls), 1)
        self.assertFalse(any("START-SHADOW" in x for x in lines))
        self.assertNotIn("_transport_shadow_facts", app.__dict__)

    def test_a_shadow_that_blows_up_cannot_change_the_arguments(self):
        class Bad:
            available = True

            def transport_facts(self, *a, **k):
                raise RuntimeError("facts broke")

            def check_transport(self, *a, **k):
                raise RuntimeError("check broke")

            def check_zip_args(self, *a, **k):
                raise RuntimeError("zip broke")

            def zip_facts(self, *a, **k):
                raise RuntimeError("zip facts broke")

            def __getattr__(self, name):
                return lambda *a, **k: None

        for info in ("/lib/a.mp4", "/lib/a.zip"):
            app = self.make_app(info, 2, 1.0, 100, zip_ready=("/t/x.cdg", "/t/x.mp3"))
            with mock.patch.object(self.singws, "_start_shadow_for", lambda s: Bad()), mock.patch.object(self.singws, "_diag", lambda *a, **k: None), \
                    mock.patch.object(self.singws.os.path, "exists", lambda p: True), mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None):
                app.play_next_file()
            self.assertEqual(len(app.transport_calls), 1, info)
            self.assertEqual(app.transport_calls[0]["semitones"], 2)

    def test_values_the_rule_does_not_model_are_skipped_not_reported(self):
        self.assertIsNone(start_shadow.transport_facts("mp3", song=b"x"))
        self.assertIsNone(start_shadow.transport_facts("mp3", song="a", semitones="2"))
        self.assertIsNone(start_shadow.transport_facts("mp3", song="a", start_seconds=float("nan")))
        self.assertIsNone(start_shadow.zip_facts(True, None, None, None, None, None))
        self.assertIsNotNone(start_shadow.transport_facts("mp3", song="a", semitones=None))


if __name__ == "__main__":
    unittest.main()
