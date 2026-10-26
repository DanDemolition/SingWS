import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_bgm", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEngine:
    def __init__(self):
        self.calls = []

    def set_master_processor(self, processor):
        self.calls.append(processor)


class HostBgmGatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_app(self, **settings):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        base = {"master_audio_enabled": False, "master_audio_params": {}}
        base.update(settings)
        app.settings = base
        app.bgm_master = None
        bg = type("Bg", (), {})()
        bg._bass_engine = FakeEngine()
        app.bg_music = bg
        return app

    def test_processor_carries_full_chain_params(self):
        # The BGM processor is built from the same params as karaoke, so tilt /
        # exciter / limiter all flow through — not just the compressor.
        app = self.make_app(
            master_audio_enabled=True,
            master_audio_exciter_enabled=True,
            master_audio_params={"comp_ratio": 3.0, "exciter_mix": 0.4, "limiter_ceiling_db": -2.0},
        )
        proc = app._ensure_bgm_master_processor()
        self.assertIsNotNone(proc)
        p = proc.params()
        self.assertEqual(p["comp_ratio"], 3.0)
        self.assertEqual(p["exciter_mix"], 0.4)
        self.assertEqual(p["limiter_ceiling_db"], -2.0)

    def test_apply_attaches_processor_when_active(self):
        app = self.make_app(master_audio_enabled=True)
        app._apply_bgm_master_processing()
        attached = app.bg_music._bass_engine.calls[-1]
        self.assertIsNotNone(attached)
        self.assertTrue(attached.enabled())

    def test_apply_detaches_when_disabled(self):
        app = self.make_app(master_audio_enabled=False)
        app._apply_bgm_master_processing()
        self.assertIsNone(app.bg_music._bass_engine.calls[-1])

class NormalizeRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_player(self):
        bgp = self.singws.BackgroundMusicPlayer.__new__(self.singws.BackgroundMusicPlayer)
        bgp.playlist = ["/a.mp3"]
        bgp.current_index = 0
        bgp._bg_norm_gen = {"primary": 5}
        bgp._bg_norm_retry = {}
        bgp._bg_normalize_active = lambda: True
        bgp._refresh_calls = []
        bgp._refresh_bg_normalize = lambda p, d: bgp._refresh_calls.append((p, d))
        return bgp

    def capture_single_shot(self, captured):
        """Record QTimer.singleShot callbacks for this test only; the real timer comes back afterwards (a leaked recorder silently stops every later test's timers)."""
        original = self.singws.QTimer.singleShot
        self.addCleanup(setattr, self.singws.QTimer, "singleShot", staticmethod(original))
        self.singws.QTimer.singleShot = staticmethod(lambda ms, cb: captured.append(cb))

    def test_retry_fires_when_token_matches(self):
        bgp = self.make_player()
        captured = []
        self.capture_single_shot(captured)
        bgp._schedule_bg_normalize_retry("/a.mp3", "primary")
        self.assertEqual(len(captured), 1)
        captured[0]()  # token (5) still current -> re-applies
        self.assertEqual(bgp._refresh_calls, [("/a.mp3", "primary")])

    def test_retry_cancels_on_track_change(self):
        bgp = self.make_player()
        captured = []
        self.capture_single_shot(captured)
        bgp._schedule_bg_normalize_retry("/a.mp3", "primary")
        # A new track bumped the generation token -> stale retry must bail.
        bgp._bg_norm_gen = {"primary": 6}
        captured[0]()
        self.assertEqual(bgp._refresh_calls, [])

    def test_retry_is_bounded(self):
        bgp = self.make_player()
        captured = []
        self.capture_single_shot(captured)
        for _ in range(20):
            bgp._schedule_bg_normalize_retry("/a.mp3", "primary")
        # Capped at 12 scheduled retries for the same (deck, path).
        self.assertEqual(len(captured), 12)


class BackgroundTrackCrossfadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_player(self):
        player = self.singws.BackgroundMusicPlayer.__new__(self.singws.BackgroundMusicPlayer)
        player.playlist = ["/a.mp3", "/b.mp3", "/c.mp3"]
        player.current_index = 1
        player.is_playing = True
        player.crossfade_active = False
        player._bg_unplayable_signatures = {}
        player.targets = []
        player._start_crossfade = lambda target_index=None: player.targets.append(target_index) or True
        player.stop = lambda: self.fail("playing track navigation must not hard-stop audio")
        return player

    def test_default_track_crossfade_is_five_seconds(self):
        self.assertEqual(self.singws.BGM_TRACK_CROSSFADE_MS, 5000)

    def test_equal_power_midpoint_keeps_constant_power(self):
        outgoing, incoming = self.singws.BackgroundMusicPlayer._crossfade_mix_gains(0.5)
        self.assertAlmostEqual((outgoing * outgoing) + (incoming * incoming), 1.0, places=6)
        self.assertAlmostEqual(outgoing, incoming, places=6)

    def test_next_track_crossfades_without_changing_index_early(self):
        player = self.make_player()
        player.next_track()
        self.assertEqual(player.targets, [2])
        self.assertEqual(player.current_index, 1)

    def test_previous_track_crossfades_without_changing_index_early(self):
        player = self.make_player()
        player.previous_track()
        self.assertEqual(player.targets, [0])
        self.assertEqual(player.current_index, 1)

    def test_active_track_ignores_silent_secondary_preload(self):
        player = self.singws.BackgroundMusicPlayer.__new__(self.singws.BackgroundMusicPlayer)
        player.playlist = ["/audible.mp3", "/next.mp3"]
        player.current_index = 0
        player.crossfade_active = False
        player._bass_ready = lambda: True
        player._bass_engine = SimpleNamespace(
            primary=SimpleNamespace(path="/audible.mp3"),
            secondary=SimpleNamespace(path="/next.mp3"),
        )

        self.assertEqual(player.get_active_track_path(), "/audible.mp3")

    def test_active_track_reports_incoming_deck_during_crossfade(self):
        player = self.singws.BackgroundMusicPlayer.__new__(self.singws.BackgroundMusicPlayer)
        player.playlist = ["/outgoing.mp3", "/incoming.mp3"]
        player.current_index = 0
        player.crossfade_active = True
        player._bass_ready = lambda: True
        player._bass_engine = SimpleNamespace(
            primary=SimpleNamespace(path="/outgoing.mp3"),
            secondary=SimpleNamespace(path="/incoming.mp3"),
        )

        self.assertEqual(player.get_active_track_path(), "/incoming.mp3")


class StartupPreloadContractTests(unittest.TestCase):
    def test_app_startup_uses_worker_preload_not_gui_thread_prescan(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        bootstrap = source[source.index("def _bootstrap_bg_playlist_on_startup"):]
        bootstrap = bootstrap[:bootstrap.index("def save_data")]
        async_preload = source[source.index("def preload_current_track_paused_async"):]
        async_preload = async_preload[:async_preload.index("def play(self)")]

        self.assertIn("preload_current_track_paused_async", bootstrap)
        self.assertNotIn("self.bg_music.preload_current_track_paused)", bootstrap)
        self.assertIn("threading.Thread", async_preload)
        self.assertIn('name="bgm-startup-preload"', async_preload)
        self.assertIn("host._run_on_ui_thread(_finish)", async_preload)
        self.assertIn("honoring queued startup action=", async_preload)

        play = source[source.index("def play(self)"):]
        play = play[:play.index("def pause(self)")]
        self.assertIn("play queued behind startup preload", play)

        stop = source[source.index("def stop(self)"):]
        stop = stop[:stop.index("def set_stop_after_current")]
        self.assertIn("_startup_preload_generation", stop)
        self.assertIn("_startup_preload_resume_request = None", stop)


if __name__ == "__main__":
    unittest.main()
