"""Tests for the native-mpv MP4-background-behind-CDG-lyrics feature.

Covers the folder scan (graceful fallback), the no-repeat shuffle bag, and
the app-level start/stop gating (enabled flag, opacity, folder contents,
settings persistence keys). The native bridge is covered by source-contract
tests here and exercised visually in an installed macOS build."""

import importlib.util
import inspect
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")


def load_main_module():
    import sys

    spec = importlib.util.spec_from_file_location("singws_main_bgvideo", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    # Registering the module lets inspect.getsource resolve class sources.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FolderScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_missing_folder_returns_empty(self):
        self.assertEqual(self.singws.scan_background_video_folder("/nope/never/here"), [])
        self.assertEqual(self.singws.scan_background_video_folder(""), [])

    def test_filters_and_sorts_videos(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("b.mp4", "a.MP4", "c.mov", "d.m4v", "cover.jpg", "notes.txt"):
                Path(d, name).write_bytes(b"x")
            files = self.singws.scan_background_video_folder(d)
            names = [os.path.basename(f) for f in files]
            self.assertEqual(names, sorted(["b.mp4", "a.MP4", "c.mov", "d.m4v"]))

    def test_empty_folder_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.singws.scan_background_video_folder(d), [])


class ShuffleBagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def bag(self, files, shuffle=True, seed=1234):
        return self.singws.BackgroundVideoShuffleBag(files, shuffle=shuffle, rng=random.Random(seed))

    def test_empty_returns_none(self):
        self.assertIsNone(self.bag([]).next())

    def test_every_video_plays_once_per_cycle(self):
        files = [f"/v/{i}.mp4" for i in range(5)]
        bag = self.bag(files)
        for _cycle in range(4):
            picks = [bag.next() for _ in range(len(files))]
            self.assertEqual(sorted(picks), sorted(files), "each cycle must play every file once")

    def test_no_immediate_repeat_across_cycles(self):
        files = [f"/v/{i}.mp4" for i in range(3)]
        for seed in range(50):
            bag = self.bag(files, seed=seed)
            picks = [bag.next() for _ in range(30)]
            for prev, cur in zip(picks, picks[1:]):
                self.assertNotEqual(prev, cur, f"immediate repeat with seed={seed}: {picks}")

    def test_single_video_repeats_gracefully(self):
        bag = self.bag(["/v/only.mp4"])
        self.assertEqual([bag.next() for _ in range(3)], ["/v/only.mp4"] * 3)

    def test_shuffle_off_is_alphabetical_loop(self):
        files = ["/v/a.mp4", "/v/b.mp4", "/v/c.mp4"]
        bag = self.bag(files, shuffle=False)
        picks = [bag.next() for _ in range(6)]
        self.assertEqual(picks, files + files)


class FakeVideoArea:
    def __init__(self):
        self.frames = []

    def set_background_video_frame(self, image):
        self.frames.append(image)


class FakeVideoWindow:
    def __init__(self):
        self.video_area = FakeVideoArea()

    def isVisible(self):
        return True

    def isMinimized(self):
        return False


class FakeNativePlugin:
    def __init__(self):
        self.loads = []
        self.at_end = False
        self.stops = 0
        self.opacity_values = []
        self.position = 1

    def loadBackgroundVideo(self, path, opacity):
        self.loads.append((path, opacity))
        self.at_end = False
        return True

    def backgroundVideoAtEnd(self):
        return self.at_end

    def backgroundVideoPositionMs(self):
        return self.position

    def backgroundVideoPaused(self):
        return False

    def setBackgroundVideoOpacity(self, value):
        self.opacity_values.append(value)

    def stopBackgroundVideo(self):
        self.stops += 1


class FakePlayer:
    instances = []

    def __init__(self, plugin, parent=None, *, opacity=1.0):
        self.plugin = plugin
        self.opacity = opacity
        self.started_with = None
        self.stopped = None
        FakePlayer.instances.append(self)

    def start(self, files, shuffle=True):
        self.started_with = (list(files), bool(shuffle))
        return True

    def stop(self, reason="stop"):
        self.stopped = reason


class StartStopGatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        FakePlayer.instances = []
        self.tmp = tempfile.TemporaryDirectory()
        for name in ("one.mp4", "two.mp4"):
            Path(self.tmp.name, name).write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def make_app(self, **settings):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {
            "bg_video_enabled": True,
            "bg_video_folder": self.tmp.name,
            "bg_video_shuffle": True,
            "lyrics_background_video_opacity": 40,
        }
        app.settings.update(settings)
        app.video_window = FakeVideoWindow()
        app._mpv_playback = FakeNativePlugin()
        # bare __new__ QObject: preset attrs that getattr() would otherwise
        # turn into Qt "super-class __init__ never called" RuntimeErrors.
        app._lyrics_bg_video_player = None
        return app

    def start(self, app):
        with mock.patch.object(self.singws, "NativeLyricsBackgroundVideoPlayer", FakePlayer):
            app._start_lyrics_background_video()

    def test_starts_with_folder_files_and_shuffle(self):
        app = self.make_app()
        self.start(app)
        player = getattr(app, "_lyrics_bg_video_player", None)
        self.assertIsNotNone(player)
        files, shuffle = player.started_with
        self.assertEqual(sorted(os.path.basename(f) for f in files), ["one.mp4", "two.mp4"])
        self.assertTrue(shuffle)
        self.assertIs(player.plugin, app._mpv_playback)
        self.assertAlmostEqual(player.opacity, 0.40)

    def test_disabled_does_not_start(self):
        app = self.make_app(bg_video_enabled=False)
        self.start(app)
        self.assertIsNone(getattr(app, "_lyrics_bg_video_player", None))

    def test_zero_opacity_does_not_start(self):
        app = self.make_app(lyrics_background_video_opacity=0)
        self.start(app)
        self.assertIsNone(getattr(app, "_lyrics_bg_video_player", None))

    def test_missing_folder_falls_back(self):
        app = self.make_app(bg_video_folder="/does/not/exist")
        self.start(app)
        self.assertIsNone(getattr(app, "_lyrics_bg_video_player", None))

    def test_shuffle_setting_respected(self):
        app = self.make_app(bg_video_shuffle=False)
        self.start(app)
        _files, shuffle = app._lyrics_bg_video_player.started_with
        self.assertFalse(shuffle)

    def test_stop_clears_player_and_frame(self):
        app = self.make_app()
        self.start(app)
        player = app._lyrics_bg_video_player
        app._stop_lyrics_background_video("test")
        self.assertIsNone(app._lyrics_bg_video_player)
        self.assertEqual(player.stopped, "test")
        self.assertEqual(app.video_window.video_area.frames[-1], None)

    def test_restart_stops_previous_player(self):
        app = self.make_app()
        self.start(app)
        first = app._lyrics_bg_video_player
        self.start(app)  # e.g. next CDG song
        self.assertIsNotNone(first.stopped)
        self.assertIsNot(app._lyrics_bg_video_player, first)

    def test_default_settings_present(self):
        defaults = self.singws.DEFAULTS
        self.assertIn("bg_video_enabled", defaults)
        self.assertIn("bg_video_folder", defaults)
        self.assertIn("bg_video_shuffle", defaults)
        self.assertFalse(defaults["bg_video_enabled"])
        self.assertTrue(defaults["bg_video_shuffle"])


class NativeMpvDecodeTests(unittest.TestCase):
    """Decorative video stays inside the existing native GPU compositor."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_player_loads_muted_native_playlist_and_advances_at_end(self):
        plugin = FakeNativePlugin()
        player = self.singws.NativeLyricsBackgroundVideoPlayer(plugin, opacity=0.35)
        self.assertTrue(player.start(["/v/a.mp4", "/v/b.mp4"], shuffle=False))
        self.assertEqual(plugin.loads, [("/v/a.mp4", 0.35)])
        plugin.at_end = True
        player._poll_count = 7
        player._poll_end()
        self.assertEqual(plugin.loads[-1], ("/v/b.mp4", 0.0))
        self.assertEqual(player._transition, "wait_frame")
        # The outgoing texture is frozen by the native compositor while the
        # replacement decoder produces its first frame.
        player._poll_end()
        self.assertEqual(player._transition, "fade_in")
        for _ in range(player._FADE_STEPS):
            player._poll_end()
        self.assertAlmostEqual(plugin.opacity_values[0], 0.35 / 12)
        self.assertAlmostEqual(plugin.opacity_values[-1], 0.35)
        player.stop("test")
        self.assertEqual(plugin.stops, 1)
        self.assertEqual(player.diagnostics()["decoder"], "native-libmpv")

    def test_active_start_path_has_no_frame_copy_or_transcode(self):
        import inspect
        source = inspect.getsource(self.singws.KaraokeApp._start_lyrics_background_video)
        self.assertIn("NativeLyricsBackgroundVideoPlayer", source)
        self.assertNotIn("FfmpegVideoReader", source)
        self.assertNotIn("prepare_background_video_playback_files", source)
        self.assertNotIn("set_background_video_frame", source)

    def test_native_compositor_freezes_outgoing_frame_for_crossfade(self):
        bridge = Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
        self.assertIn("previousBackgroundTexture", bridge)
        self.assertIn("backgroundCrossfadeMix", bridge)
        self.assertIn("glCopyTexSubImage2D", bridge)
        self.assertIn("_previousBackgroundHasFrame", bridge)

        poll_source = inspect.getsource(
            self.singws.NativeLyricsBackgroundVideoPlayer._poll_end
        )
        self.assertNotIn('"fade_out"', poll_source)
        self.assertIn('self._advance("eos_crossfade", opacity=0.0)', poll_source)

    def test_cdg_foreground_uses_nearest_without_changing_video_filtering(self):
        bridge = Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
        self.assertIn("_isCdg?GL_NEAREST:GL_LINEAR", bridge)
        self.assertIn("Keep linear filtering for", bridge)
        self.assertIn("_cdgTexture", bridge)
        self.assertIn("_cdgFbo", bridge)
        self.assertIn("_isCdg?300:_width", bridge)
        self.assertIn("_isCdg?216:_height", bridge)
        self.assertIn("_isCdg?_cdgTexture:_texture", bridge)
        self.assertIn("_isCdg?1.0:", bridge)
        # Do not revive the mpv scale=nearest setting that previously stopped
        # valid CDGs from reaching visual readiness.
        self.assertNotIn('[self setOption:"scale" value:"nearest"]', bridge)

    def test_native_bridge_contract_is_muted_and_uses_a_separate_texture(self):
        bridge = Path("native/mpv_bridge/bridge.mm").read_text()
        compact = bridge.replace(" ", "")
        self.assertIn('mpv_set_option_string(_backgroundMpv,"ao","null")', compact)

    def test_background_frames_present_to_output_and_preview_without_cdg_activity(self):
        bridge = Path("native/mpv_bridge/bridge.mm").read_text(encoding="utf-8")
        compact = bridge.replace(" ", "")
        callback = bridge[bridge.index("- (void)scheduleBackgroundRender {"):]
        callback = callback[:callback.index("- (void)scheduleBackgroundEvents")]
        self.assertIn("[self presentView:self->_outputView]", callback)
        self.assertIn("[self presentView:self->_previewView]", callback)
        self.assertNotIn('mpv_set_option_string(_backgroundMpv,"audio","no")', compact)
        self.assertIn('mpv_set_option_string(_backgroundMpv,"mute","yes")', compact)
        self.assertIn('mpv_set_option_string(_backgroundMpv,"video-aspect-override","16:9")', compact)
        self.assertIn('mpv_set_option_string(_backgroundMpv,"ao","null")', compact)
        self.assertIn('mpv_set_option_string(_backgroundMpv,"hwdec","no")', compact)
        self.assertIn('mpv_set_option_string(_backgroundMpv,"keep-open","no")', compact)
        self.assertIn('mpv_set_option_string(_backgroundMpv,"idle","yes")', compact)
        self.assertIn('mpv_set_property(_backgroundMpv,"pause",MPV_FORMAT_FLAG,&paused)', compact)
        self.assertIn("_backgroundTexture", bridge)
        self.assertIn("_backgroundFbo", bridge)
        self.assertIn("singws_bridge_load_background", bridge)
        self.assertIn("singws_bridge_background_at_end", bridge)
        self.assertIn("singws_bridge_background_position", bridge)
        self.assertIn("singws_bridge_background_paused", bridge)
        self.assertIn("vec3 borderBg", bridge)
        self.assertIn("vec3 tileBg", bridge)
        self.assertIn("vec3 panelBg", bridge)
        self.assertIn("distance(fg.rgb,panelBg)", bridge)
        self.assertIn("delta<0.16", bridge)
        self.assertIn("fg.r<0.20&&delta<0.28", bridge)
        self.assertNotIn("delta<0.42", bridge)
        self.assertNotIn("delta<0.75", bridge)
        self.assertIn('mpv_get_property(_backgroundMpv,"eof-reached"', compact)
        self.assertIn("[self presentView:self->_previewView]", bridge)
        self.assertIn("const bool backgroundActive=(_isCdg", bridge)
        active = bridge[bridge.index("if(backgroundActive){"):]
        active = active[:active.index("} else if(sidefill)")]
        self.assertIn("glUniform2f(_uvScaleUniform,1,1)", active)
        self.assertNotIn("backgroundAspect", active)
        self.assertIn('\"video-aspect-override\",_isCdg?\"no\":\"16:9\"', bridge)
        self.assertIn("if(!_isCdg){sx=1;sy=1;}", bridge)
        self.assertIn("singws_bridge_set_background_opacity", bridge)

    def test_painted_background_fallback_stretches_without_scaled_pixmap(self):
        source = inspect.getsource(self.singws.VideoAreaWidget._draw_background_video_pixmap)
        self.assertIn("painter.drawPixmap(self.rect(), pixmap, pixmap.rect())", source)
        self.assertNotIn("pixmap.scaled", source)


if __name__ == "__main__":
    unittest.main()
