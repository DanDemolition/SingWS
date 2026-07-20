"""Tests for the shuffled MP4 background-video-behind-CDG-lyrics feature.

Covers the folder scan (graceful fallback), the no-repeat shuffle bag, and
the app-level start/stop gating (enabled flag, opacity, folder contents,
settings persistence keys). The GStreamer decode itself is exercised
manually / by the player's own diagnostics; everything here runs without
GStreamer (SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS)."""

import importlib.util
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


class FakePlayer:
    instances = []

    def __init__(self, parent=None, *, on_frame=None, max_width=1280, max_height=720, max_fps=60, quality_label="auto"):
        self.on_frame = on_frame
        self.max_width = max_width
        self.max_height = max_height
        self.max_fps = max_fps
        self.quality_label = quality_label
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
            "bg_video_quality": "auto",
            "lyrics_background_video_opacity": 40,
            "mp4_max_height": 720,
        }
        app.settings.update(settings)
        app._effective_mp4_max_height = lambda: 720
        app.video_window = FakeVideoWindow()
        # bare __new__ QObject: preset attrs that getattr() would otherwise
        # turn into Qt "super-class __init__ never called" RuntimeErrors.
        app._lyrics_bg_video_player = None
        return app

    def start(self, app):
        with mock.patch.object(self.singws, "Gst", object()), \
             mock.patch.object(self.singws, "LyricsBackgroundVideoPlayer", FakePlayer):
            app._start_lyrics_background_video()

    def test_starts_with_folder_files_and_shuffle(self):
        app = self.make_app()
        self.start(app)
        player = getattr(app, "_lyrics_bg_video_player", None)
        self.assertIsNotNone(player)
        files, shuffle = player.started_with
        self.assertEqual(sorted(os.path.basename(f) for f in files), ["one.mp4", "two.mp4"])
        self.assertTrue(shuffle)
        self.assertEqual(player.max_width, 1280)
        self.assertEqual(player.max_height, 720)
        self.assertEqual(player.max_fps, 60)
        self.assertEqual(player.quality_label, "auto")

    def test_disabled_does_not_start(self):
        app = self.make_app(bg_video_enabled=False)
        self.start(app)
        self.assertIsNone(getattr(app, "_lyrics_bg_video_player", None))

    def test_zero_opacity_does_not_start(self):
        app = self.make_app(lyrics_background_video_opacity=0)
        self.start(app)
        self.assertIsNone(getattr(app, "_lyrics_bg_video_player", None))

    def test_quality_off_does_not_start(self):
        app = self.make_app(bg_video_quality="off")
        self.start(app)
        self.assertIsNone(getattr(app, "_lyrics_bg_video_player", None))

    def test_540p_quality_uses_lower_decorative_layer_cap(self):
        app = self.make_app(bg_video_quality="540")
        self.start(app)
        player = getattr(app, "_lyrics_bg_video_player", None)
        self.assertIsNotNone(player)
        self.assertEqual(player.max_width, 960)
        self.assertEqual(player.max_height, 540)
        self.assertEqual(player.max_fps, 30)
        self.assertEqual(player.quality_label, "540")

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
        self.assertIn("bg_video_quality", defaults)
        self.assertIn("bg_video_auto_transcode_720p", defaults)
        self.assertFalse(defaults["bg_video_enabled"])
        self.assertTrue(defaults["bg_video_shuffle"])
        self.assertEqual(defaults["bg_video_quality"], "auto")
        self.assertFalse(defaults["bg_video_auto_transcode_720p"])

    def test_quality_profile_aliases(self):
        self.assertEqual(self.singws.background_video_quality_profile("720p")["max_width"], 1280)
        self.assertEqual(self.singws.background_video_quality_profile("960x540")["key"], "540")
        self.assertEqual(self.singws.background_video_quality_profile("surprise")["key"], "auto")


class FfmpegDecodeWorkerTests(unittest.TestCase):
    """The decorative video worker decodes via FfmpegVideoReader, not GStreamer."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_worker_and_player_have_no_gstreamer_references(self):
        import inspect

        for cls_ in (
            self.singws._LyricsBackgroundVideoWorker,
            self.singws.LyricsBackgroundVideoPlayer,
        ):
            source = inspect.getsource(cls_)
            self.assertNotIn("Gst.", source)
            self.assertNotIn("uridecodebin", source)
            self.assertNotIn("appsink", source)
        self.assertIn(
            "FfmpegVideoReader",
            inspect.getsource(self.singws._LyricsBackgroundVideoWorker),
        )

    def test_worker_decodes_generated_mp4_and_loops(self):
        import shutil
        import subprocess
        import time as _time

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is required for the decode test")
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(["test"])
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                    "-f", "lavfi", "-i", "testsrc=duration=0.6:size=64x48:rate=10",
                    "-pix_fmt", "yuv420p", str(clip),
                ],
                check=True, timeout=30,
            )
            worker = self.singws._LyricsBackgroundVideoWorker(
                None, max_width=64, max_height=48, max_fps=10, quality_label="test",
            )
            frames = []

            def collect(image):
                if image is not None:
                    frames.append(image)
                    worker.acknowledge_frame()

            worker.frame_ready.connect(collect)
            stops = []
            worker.stopped.connect(lambda reason: stops.append(reason))
            self.assertTrue(worker.start([str(clip)], shuffle=False))
            # 0.6s clip at 10fps: collecting past 6 frames proves the shuffle
            # bag advanced through EOF and restarted the (single) clip.
            deadline = _time.monotonic() + 15.0
            while len(frames) < 8 and _time.monotonic() < deadline:
                app.processEvents()
                _time.sleep(0.005)
            worker.stop("test_done")
            app.processEvents()
        self.assertGreaterEqual(len(frames), 8, "worker did not deliver frames")
        image = frames[0]
        self.assertEqual((image.width(), image.height()), (64, 48))
        diag = worker.diagnostics()
        self.assertEqual(diag["working_size"], "64x48")
        self.assertIn("test_done", stops)


if __name__ == "__main__":
    unittest.main()
