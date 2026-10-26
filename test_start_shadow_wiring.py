"""The start-route shadow as wired into the REAL `play_next_file` (the harness of test_show_critical_regressions), with the real Rust library.

For every route the real method can take (mp4, mp3, CDG+MP3 as a tuple and as a lone .cdg with and without its MP3, MP3+G ZIP cached / invalid / not yet extracted, a
missing file, unsupported formats, external KaraFun) the shadow must log the SAME route and no MISMATCH, and the app must do exactly what it did before: the same play_*
function is called (or none). With the setting off nothing is logged; a shadow that blows up cannot stop a start.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import os
import unittest
from pathlib import Path
from unittest import mock

import start_shadow
import test_show_critical_regressions as base

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


class Wiring(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_start(self, song_info, *, exists=("/lib/",), zip_ready=None, mp3g=True, external=False, setting=True, shadow_factory=None):
        entry = {"request_uid": "rid-1", "song_info": song_info, "artist": "A", "title": "T", "skipped": False}
        singer = {"name": "Dana", "singer_id": "sid-Dana", "skipped": False, "songs": [entry]}
        app = self.playback_app([singer])
        app.settings = {"start_shadow": setting}
        calls = []

        def stub(name):
            def f(*args, **kwargs):
                calls.append(name)
                return True
            return f

        for n in ("play_mp4", "play_mp3", "play_cdg_mp3_dual", "play_mp3g_zip"):
            setattr(app, n, stub(n))
        app.zip_cache = type("Z", (), {"cached_paths_if_ready": lambda _s, p: zip_ready if zip_ready is not None else (None, None)})()
        app.is_mp3g_zip = lambda p: mp3g
        app._is_external_karafun_entry = lambda e: external
        app._start_external_karafun_playback = lambda **kw: calls.append("external")
        app._song_info_primary_path = lambda info: info[0] if isinstance(info, (tuple, list)) and info else (info if isinstance(info, str) else "")
        lines = []
        # created BEFORE os.path.exists is patched, so the loader can see the real library; the app's factory then reuses it
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        patches = [mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))),
                   mock.patch.object(self.singws.os.path, "exists", lambda p: any(str(p).startswith(x) or str(p) == x for x in exists))]
        if shadow_factory is not None:
            patches.append(mock.patch.object(self.singws, "_start_shadow_for", shadow_factory))
        for p in patches:
            p.start()
        try:
            app.play_next_file()
        finally:
            for p in reversed(patches):
                p.stop()
        routes = [x.split("route=")[1].split()[0] for x in lines if "[START-SHADOW] decision route=" in x and not x.split("route=")[1].startswith(("pick", "engine"))]
        mism = [x for x in lines if "[START-SHADOW] MISMATCH" in x]
        return calls, routes, mism, lines

    def expect(self, song_info, route, called, **kw):
        calls, routes, mism, lines = self.run_start(song_info, **kw)
        self.assertEqual(mism, [], mism)
        self.assertEqual(routes, [route], (song_info, lines))
        self.assertEqual(calls, called, song_info)

    def test_every_route_the_real_method_takes_is_the_route_rust_expects(self):
        ok = ("/lib/",)
        self.expect("/lib/a.mp4", "mp4", ["play_mp4"], exists=ok)
        self.expect("/lib/a.mp3", "mp3", ["play_mp3"], exists=ok)
        self.expect(("/lib/a.cdg", "/lib/a.mp3"), "cdg_mp3_dual", ["play_cdg_mp3_dual"], exists=ok)
        self.expect(["/lib/a.cdg", "/lib/a.mp3"], "cdg_mp3_dual", ["play_cdg_mp3_dual"], exists=ok)
        self.expect(("/lib/a.mp4", "/lib/b.mp4"), "mp4", ["play_mp4"], exists=ok)
        self.expect(("/lib/a.mp3", "x"), "mp3", ["play_mp3"], exists=ok)
        self.expect("/lib/a.cdg", "cdg_mp3_dual", ["play_cdg_mp3_dual"], exists=ok + ("/lib/a.mp3",))
        self.expect("/lib/a.cdg", "reject_missing_cdg_audio_pair", [], exists=("/lib/a.cdg",))
        self.expect("/lib/a.zip", "mp3g_zip", ["play_mp3g_zip"], exists=ok, zip_ready=("/t/x.cdg", "/t/x.mp3"), mp3g=True)
        self.expect("/lib/a.zip", "reject_invalid_zip", [], exists=ok, zip_ready=("/t/x.cdg", "/t/x.mp3"), mp3g=False)
        self.expect("/lib/a.zip", "zip_prepare", ["play_mp3g_zip"], exists=ok, zip_ready=(None, None))
        self.expect("/lib/A.ZIP", "reject_unsupported_format", [], exists=ok, zip_ready=("/t/x.cdg", "/t/x.mp3"))
        self.expect("/lib/A.ZIP", "zip_prepare", ["play_mp3g_zip"], exists=ok, zip_ready=(None, None))
        self.expect("/lib/a.txt", "reject_unsupported_format", [], exists=ok)
        self.expect("/lib/a.MP4", "reject_unsupported_format", [], exists=ok)
        self.expect(("/lib/a.zip", "x"), "reject_unsupported_tuple", [], exists=ok)
        self.expect("/gone/a.mp4", "reject_missing_file", [], exists=ok)
        self.expect("/lib/a.mp4", "external_karafun", ["external"], exists=ok, external=True)

    def test_a_start_that_raises_in_python_is_not_reported_as_a_route(self):
        for bad in (("/lib/a.cdg", "/lib/a.mp3", "extra"), ("/lib/a.cdg",), ()):
            try:
                calls, routes, mism, lines = self.run_start(bad, exists=("/lib/",))
            except Exception:
                continue  # the app itself raises for these shapes; the shadow never got a route to compare
            self.assertEqual(mism, [], bad)

    def test_with_the_setting_off_nothing_is_logged_and_the_start_is_unchanged(self):
        calls, routes, mism, lines = self.run_start("/lib/a.mp4", setting=False)
        self.assertEqual((calls, routes, mism), (["play_mp4"], [], []))
        self.assertFalse(any("START-SHADOW" in x for x in lines))

    def test_a_shadow_that_blows_up_cannot_stop_a_start(self):
        def boom(settings):
            raise RuntimeError("shadow factory broke")

        calls, routes, mism, lines = self.run_start("/lib/a.mp4", shadow_factory=boom)
        self.assertEqual(calls, ["play_mp4"])

        class Bad:
            available = True

            def check(self, *a, **k):
                raise RuntimeError("check broke")

        calls, routes, mism, lines = self.run_start("/lib/a.mp4", shadow_factory=lambda s: Bad())
        self.assertEqual(calls, ["play_mp4"])


class PickWiring(unittest.TestCase):
    """Who sings next, from the REAL `play_next_file` over random queues (skipped singers, singers without songs, skipped songs, older tuple entries): the shadow's Rust answer must
    equal the singer and song the real method picked. Nothing is logged for a locked rotation."""
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_queue(self, queue, *, locked=False, enabled=True):
        app = self.playback_app(queue)
        app.settings = {"start_shadow": enabled}
        app._is_rotation_locked = lambda: locked
        app._rotation_reweave_locked_tail = lambda: None
        app._ensure_queue_entry_id = lambda entry: (entry.setdefault("request_uid", f"rid-{entry.get('song_info')}") if isinstance(entry, dict) else f"rid-{entry[0]}")
        picked = {}
        original_pop = None
        app.play_mp3 = lambda *a, **k: (picked.setdefault("path", a[0]), True)[1]
        lines = []
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None), mock.patch.object(self.singws.os.path, "exists", lambda p: True):
            app.play_next_file()
        return app, picked, lines

    def test_random_queues_pick_the_same_singer_and_song_as_rust(self):
        import random
        rng = random.Random(20261026)
        checked = picks = nones = 0
        for case in range(400):
            queue = []
            for i in range(rng.randint(0, 7)):
                songs = []
                for j in range(rng.randint(0, 4)):
                    r = rng.random()
                    path = f"/lib/s{i}_{j}.mp3"
                    if r < 0.2:
                        songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": True})
                    elif r < 0.3:
                        songs.append((path, 0))  # an older tuple-format entry
                    else:
                        songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": False})
                queue.append({"name": f"S{i}", "singer_id": f"sid{i}", "skipped": rng.random() < 0.25, "songs": songs})
            app, picked, lines = self.run_queue(queue)
            mism = [x for x in lines if "MISMATCH" in x]
            self.assertEqual(mism, [], (case, mism))
            decisions = [x for x in lines if "decision route=pick" in x]
            if queue:
                self.assertEqual(len(decisions), 1, (case, "a non-empty queue must produce exactly one compared pick (a skipped comparison would hide a disagreement)", lines))
                checked += 1
            if picked.get("path"):
                picks += 1
            else:
                nones += 1
        self.assertGreater(picks, 100)
        self.assertGreater(nones, 10)

    def test_the_all_skipped_quirk_is_what_the_app_really_does(self):
        """Documented finding: with every singer skipped, the loop is left holding the last one it looked at and that SKIPPED singer's song starts. The shadow models the
        app, not the wish, so it must agree (and the quirk stays visible here until the operator decides to change it)."""
        q = [{"name": "A", "singer_id": "a", "skipped": True, "songs": [{"request_uid": "x", "song_info": "/lib/a.mp3", "skipped": False}]},
             {"name": "B", "singer_id": "b", "skipped": True, "songs": [{"request_uid": "y", "song_info": "/lib/b.mp3", "skipped": False}]}]
        app, picked, lines = self.run_queue(q)
        self.assertEqual(picked["path"], "/lib/b.mp3", "the LAST skipped singer sings")
        self.assertEqual([x for x in lines if "MISMATCH" in x], [])
        self.assertTrue(any("decision route=pick agree=1" in x for x in lines))

    def test_a_locked_rotation_is_not_compared_and_off_logs_nothing(self):
        q = lambda: [{"name": "A", "singer_id": "a", "skipped": True, "songs": [{"request_uid": "x", "song_info": "/lib/a.mp3", "skipped": False}]},
                     {"name": "B", "singer_id": "b", "skipped": False, "songs": [{"request_uid": "y", "song_info": "/lib/b.mp3", "skipped": False}]}]
        app, picked, lines = self.run_queue(q(), locked=True)
        self.assertEqual(picked["path"], "/lib/b.mp3")
        self.assertFalse(any("route=pick" in x or "MISMATCH" in x for x in lines), lines)
        app, picked, lines = self.run_queue(q(), enabled=False)
        self.assertEqual(picked["path"], "/lib/b.mp3")
        self.assertEqual(lines, [])
        app, picked, lines = self.run_queue(q())
        self.assertTrue(any("decision route=pick agree=1" in x for x in lines), lines)

    def test_a_queue_with_something_odd_in_it_is_skipped_by_the_shadow(self):
        self.assertIsNone(start_shadow.pick_facts([{"songs": [], "skipped": False}, "not a dict"]))
        self.assertIsNone(start_shadow.pick_facts([{"songs": "abc"}]))
        self.assertEqual(start_shadow.pick_facts([{"songs": None}]), [{"skipped": False, "songs": ""}])
        self.assertEqual(start_shadow.pick_facts([{"skipped": 1, "songs": [{"skipped": True}, {"skipped": 0}, ("x", 1), {}]}]), [{"skipped": True, "songs": "sdtd"}])


class EngineWiring(unittest.TestCase):
    """The REAL `_karaoke_rust_voice_for_song` with a fake video bridge, fake audio engines and a fake voice class, over every combination of facts."""

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_case(self, setting, supports, engine_kind, voice_error, *, enabled=True):
        import types
        import sys
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"karaoke_audio_engine": setting, "start_shadow": enabled}
        app._rust_karaoke_voice = None
        engine = {"none": None, "headless": types.SimpleNamespace(_headless=True, _stats=lambda: (0, 0, 0, 0, 1000)),
                  "running": types.SimpleNamespace(_headless=False, _stats=lambda: (0, 0, 0, 0, 1000)), "raises": "raises"}[engine_kind]

        def soundboard_runtime():
            if engine == "raises":
                raise RuntimeError("no engine")
            return engine

        app.bg_music = types.SimpleNamespace(soundboard_runtime=soundboard_runtime)
        shadow_audio = []

        def supports_call():
            if supports == "raises":
                raise RuntimeError("bridge")
            return supports

        plugin = types.SimpleNamespace(supportsShadowAudio=supports_call, setShadowAudio=lambda v: shadow_audio.append(v))

        class FakeVoice:
            def __init__(self, e):
                if voice_error:
                    raise RuntimeError("voice failed")
                self._e = e

            def set_output_latency_seconds(self, x):
                pass

        fake_module = types.ModuleType("rust_karaoke_voice")
        fake_module.RustKaraokeVoice = FakeVoice
        lines = []
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        with mock.patch.dict(sys.modules, {"rust_karaoke_voice": fake_module}), mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            voice = app._karaoke_rust_voice_for_song(plugin)
        return voice, shadow_audio, [x for x in lines if "MISMATCH" in x], [x for x in lines if "decision" in x]

    def test_every_combination_of_facts_agrees_with_rust_and_the_method_behaves_as_before(self):
        n = 0
        for setting in ("rust", " RUST ", "", None, "mpv", "Rust2"):
            for supports in (True, False, "raises"):
                for engine_kind in ("none", "headless", "running", "raises"):
                    for voice_error in (False, True):
                        voice, shadow_audio, mism, decisions = self.run_case(setting, supports, engine_kind, voice_error)
                        self.assertEqual(mism, [], (setting, supports, engine_kind, voice_error, mism))
                        want = str(setting or "rust").strip().lower() == "rust"
                        sup = supports is True
                        expect_voice = want and sup and engine_kind == "running" and not voice_error
                        self.assertEqual(voice is not None, expect_voice, (setting, supports, engine_kind, voice_error))
                        self.assertEqual(shadow_audio, [expect_voice] if sup else [], "the bridge is told exactly what it was told before")
                        self.assertEqual(len(decisions), 1)
                        n += 1
        self.assertEqual(n, 6 * 3 * 4 * 2)

    def test_with_the_setting_off_nothing_is_logged(self):
        voice, shadow_audio, mism, decisions = self.run_case("rust", True, "running", False, enabled=False)
        self.assertIsNotNone(voice)
        self.assertEqual((mism, decisions), ([], []))


if __name__ == "__main__":
    unittest.main()
