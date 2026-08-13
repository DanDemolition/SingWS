import threading
import time
import unittest
import unittest.mock

from PyQt6.QtCore import QCoreApplication

from mpv_karaoke_transport import MpvKaraokeTransport

try:
    import mpv_playback
except Exception as exc:  # pragma: no cover - depends on the interpreter used
    # The plugin needs python-mpv (and libmpv). The release test runner uses the
    # system Python framework, which has neither; the venv used for development
    # does. Skip the plugin-level cases there instead of failing collection.
    mpv_playback = None
    MPV_IMPORT_ERROR = str(exc)
else:
    MPV_IMPORT_ERROR = ""


class _Plugin:
    def __init__(self):
        self.loaded = None
        self.position = 0
        self.duration = 180000
        self.playing = False
        self.ended = False
        self.volume = 1.0
        self.tempo = 1.0
        self.pitch = 0.0
        self.seeks = []

    def loadSingWSMedia(self, *args, **kwargs):
        self.loaded = (args, kwargs)
        self.playing = True
        return True

    def errorString(self): return ""
    def seekMedia(self, value): self.seeks.append(value); self.position = value
    def positionMs(self): return self.position
    def durationMs(self): return self.duration
    def isPlaying(self): return self.playing
    def visualsReady(self): return True
    def atEnd(self): return self.ended
    def stopMedia(self): self.playing = False
    def pauseMedia(self): self.playing = False
    def playMedia(self): self.playing = True
    def setVolume(self, value): self.volume = value
    def setTempoRatio(self, value): self.tempo = value
    def setPitchSemitones(self, value): self.pitch = value


class MpvTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def make_transport(self, mode="mp4"):
        plugin = _Plugin()
        transport = MpvKaraokeTransport(
            plugin,
            audio_path="song.mp3" if mode == "cdg" else "song.mp4",
            video_path="song.cdg" if mode == "cdg" else "song.mp4",
            mode=mode,
        )
        return plugin, transport

    def test_cdg_load_keeps_external_audio(self):
        plugin, transport = self.make_transport("cdg")
        transport.start(12.5)
        args, kwargs = plugin.loaded
        self.assertEqual(args, ("song.cdg", "song.mp3"))
        self.assertTrue(kwargs["autoplay"])
        self.assertEqual(plugin.seeks[-1], 12500)

    def test_live_key_and_tempo_are_independent(self):
        plugin, transport = self.make_transport()
        transport.set_modifiers(1.2, -3)
        self.assertEqual(plugin.tempo, 1.2)
        self.assertEqual(plugin.pitch, -3.0)

    def test_seek_pause_resume_and_timing_contract(self):
        plugin, transport = self.make_transport()
        transport.start()
        transport.seek(42.25)
        self.assertEqual(plugin.seeks[-1], 42250)
        transport.pause()
        self.assertTrue(transport.is_paused())
        transport.resume()
        self.assertFalse(transport.is_paused())
        duration, position = transport.query_times_ns()
        self.assertEqual(duration, 180_000_000_000)
        self.assertEqual(position, 42_250_000_000)

    def test_intro_loop_seeks_to_start(self):
        plugin, transport = self.make_transport()
        transport.start()
        transport.set_loop(10, 20)
        plugin.position = 20000
        transport._poll()
        self.assertEqual(plugin.seeks[-1], 10000)

    def test_audible_start_eventually_commits_when_visual_is_late(self):
        plugin, transport = self.make_transport("cdg")
        plugin.visualsReady = lambda: False
        transport.start()
        plugin.position = 100
        starts = []
        transport.started.connect(lambda: starts.append(True))
        required = max(4, int(round(1000 / transport._timer.interval())))
        for _ in range(required):
            transport._poll()
        self.assertEqual(starts, [True])

    def test_visual_readiness_still_commits_without_delay(self):
        plugin, transport = self.make_transport("cdg")
        transport.start()
        plugin.position = 100
        starts = []
        transport.started.connect(lambda: starts.append(True))
        transport._poll()
        self.assertEqual(starts, [True])


class _FakeEngine:
    """Stands in for the out-of-process audio master."""

    def __init__(self):
        self.paused = False
        self.log = []
        self.ended = threading.Event()

    def command(self, *args):
        self.log.append(args)

    def set_property(self, name, value):
        self.log.append(("set", name, value))
        if name == "pause":
            self.paused = bool(value)

    def get(self, name, default=None):
        return self.paused if name == "pause" else default


class _FakePlayer:
    def __init__(self, follower):
        self._follower = follower
        self.seeking = False

    def command(self, name, *args):
        if name == "seek":
            self._follower.begin_seek(float(args[0]))


class _FakeFollower:
    """A follower whose seek takes `settle_samples` polls to complete."""

    def __init__(self, tag, settle_samples=3):
        self.tag = tag
        self.player = _FakePlayer(self)
        self.clock = 5.0
        self.settle_samples = settle_samples
        self._samples = 0
        self.threads = []

    def begin_seek(self, secs):
        self.clock = secs
        self._samples = 0
        self.player.seeking = True

    def enqueue_operation(self, label, func, *args):
        thread = threading.Thread(target=func, args=args, daemon=True)
        self.threads.append(thread)
        thread.start()

    def time(self):
        self._samples += 1
        if self._samples > self.settle_samples:
            self.player.seeking = False
            self.clock += 0.05          # running again
        return self.clock


@unittest.skipIf(mpv_playback is None, f"python-mpv unavailable: {MPV_IMPORT_ERROR}")
class CoordinatedSeekTests(unittest.TestCase):
    """A seek must leave audio and video on the SAME position, not on the same
    requested timestamp — the video engine needs far longer to get there."""

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def make_plugin(self, audio_only=False, settle_samples=3):
        plugin = mpv_playback.MpvPlaybackPlugin.__new__(
            mpv_playback.MpvPlaybackPlugin
        )
        plugin.log = lambda *a, **k: None
        plugin._engine = _FakeEngine()
        plugin._out = _FakeFollower("out", settle_samples)
        plugin._prev = _FakeFollower("prev", settle_samples)
        plugin._audio_only = audio_only
        plugin._is_cdg = True
        plugin._loaded = True
        plugin._shutdown = False
        plugin._stop_evt = threading.Event()
        plugin._seek_lock = threading.Lock()
        plugin._seek_generation = 0
        plugin._seek_pending = set()
        plugin._seek_landed = None
        plugin._seek_target = 0.0
        plugin._seek_resume = False
        plugin._seek_started_at = 0.0
        plugin._seek_hold_active = False
        plugin._external_audio = None
        return plugin

    def wait_for_release(self, plugin, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with plugin._seek_lock:
                if not plugin._seek_hold_active:
                    return True
            time.sleep(0.01)
        return False

    def test_master_is_held_then_aligned_to_where_the_video_landed(self):
        plugin = self.make_plugin()
        plugin.seekMedia(42000)

        # The audible clock is paused immediately and NOT seeked yet.
        self.assertTrue(plugin._engine.paused)
        self.assertNotIn("seek", [c[0] for c in plugin._engine.log])
        self.assertTrue(plugin.isSeekHolding())

        self.assertTrue(self.wait_for_release(plugin))
        for follower in (plugin._out, plugin._prev):
            for thread in follower.threads:
                thread.join(timeout=2)

        seeks = [c for c in plugin._engine.log if c[0] == "seek"]
        self.assertEqual(len(seeks), 1)
        # Aligned to the output follower's real position, which has moved past
        # the requested timestamp while it was seeking.
        self.assertAlmostEqual(seeks[0][1], plugin._out.clock, delta=0.2)
        self.assertGreater(seeks[0][1], 42.0)
        self.assertFalse(plugin._engine.paused)
        self.assertFalse(plugin.isSeekHolding())

    def test_seek_while_paused_stays_paused(self):
        plugin = self.make_plugin()
        plugin._engine.paused = True
        plugin.seekMedia(42000)
        self.assertTrue(self.wait_for_release(plugin))
        self.assertTrue(plugin._engine.paused)

    def test_audio_only_seeks_the_master_directly(self):
        plugin = self.make_plugin(audio_only=True)
        plugin.seekMedia(42000)
        self.assertEqual(plugin._engine.log, [("seek", 42.0, "absolute+exact")])
        self.assertFalse(plugin._engine.paused)
        self.assertFalse(plugin.isSeekHolding())

    def test_a_second_seek_supersedes_the_first(self):
        plugin = self.make_plugin(settle_samples=8)
        plugin.seekMedia(42000)
        plugin.seekMedia(90000)
        self.assertTrue(self.wait_for_release(plugin))
        seeks = [c for c in plugin._engine.log if c[0] == "seek"]
        self.assertEqual(len(seeks), 1)
        self.assertGreater(seeks[0][1], 90.0)


class _FakeHost:
    def __init__(self, visible=True):
        self._visible = visible

    def isVisible(self):
        return self._visible


class _RevealFollower:
    """Follower stub carrying the real readiness state machine's outputs."""

    def __init__(self, tag):
        self.tag = tag
        self.host = _FakeHost()
        self.win = object()
        self.always_visible = False
        self._hidden = True
        self.shown = 0
        self.hidden_calls = 0
        self.ready_for = None
        self.armed_for = None

    def window_alive(self):
        return True

    def awaiting_visual(self, generation):
        return self.armed_for == generation and self.ready_for != generation

    def show(self):
        self._hidden = False
        self.shown += 1
        return False

    def hide(self):
        self._hidden = True
        self.hidden_calls += 1

    def reposition(self, force=False):
        return False


@unittest.skipIf(mpv_playback is None, f"python-mpv unavailable: {MPV_IMPORT_ERROR}")
class VisualRevealGateTests(unittest.TestCase):
    """A surface uncovered before gpu-next has configured the replacement
    stream shows one frame of whatever the Metal drawable held — the colour
    flash at song start. The readiness the followers already publish from mpv's
    VO events has to actually gate the reveal."""

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def make_plugin(self, audio_only=False):
        plugin = mpv_playback.MpvPlaybackPlugin.__new__(
            mpv_playback.MpvPlaybackPlugin
        )
        plugin.log = lambda *a, **k: None
        plugin._out = _RevealFollower("out")
        plugin._prev = _RevealFollower("prev")
        plugin._audio_only = audio_only
        plugin._loaded = True
        plugin._shutdown = False
        plugin._window_transition_active = False
        plugin._media_generation = 3
        plugin._follower_state_lock = threading.Lock()
        plugin._followers_loading = set()
        plugin._reveal_hold_started_at = time.monotonic()
        plugin._start_gate_generation = 0
        plugin._pending_rebuild = set()
        plugin._settle_timer = unittest.mock.Mock()
        return plugin

    def test_surface_stays_hidden_while_its_file_is_still_loading(self):
        plugin = self.make_plugin()
        with plugin._follower_state_lock:
            plugin._followers_loading = {"out", "prev"}
        plugin._tick()
        self.assertEqual(plugin._out.shown, 0)
        self.assertEqual(plugin._prev.shown, 0)

    def test_surface_stays_hidden_until_the_new_stream_is_configured(self):
        plugin = self.make_plugin()
        plugin._out.armed_for = 3
        plugin._prev.armed_for = 3
        plugin._tick()
        self.assertEqual(plugin._out.shown, 0)

        # mpv reports video-out-params for the new generation.
        plugin._out.ready_for = 3
        plugin._prev.ready_for = 3
        plugin._tick()
        self.assertEqual(plugin._out.shown, 1)
        self.assertEqual(plugin._prev.shown, 1)

    def test_a_file_that_never_configures_still_reveals(self):
        """Better a stale frame than a permanently dark show screen."""
        plugin = self.make_plugin()
        plugin._out.armed_for = 3
        plugin._prev.armed_for = 3
        plugin._reveal_hold_started_at = (
            time.monotonic() - mpv_playback.MpvPlaybackPlugin.REVEAL_HOLD_TIMEOUT - 0.1
        )
        plugin._tick()
        self.assertEqual(plugin._out.shown, 1)

    def test_audio_only_never_holds_a_reveal(self):
        plugin = self.make_plugin(audio_only=True)
        plugin._out.armed_for = 3
        plugin._out.always_visible = True
        plugin._prev.always_visible = True
        plugin._tick()
        self.assertEqual(plugin._out.shown, 1)

    def test_between_songs_the_persistent_window_is_not_held(self):
        """Nothing armed for this generation: no load in flight, so the last
        frame keeps showing instead of being blanked."""
        plugin = self.make_plugin()
        plugin._out.armed_for = 2  # previous song
        plugin._out.ready_for = 2
        plugin._prev.armed_for = 2
        plugin._prev.ready_for = 2
        plugin._tick()
        self.assertEqual(plugin._out.shown, 1)

    def test_the_start_gate_is_released_as_soon_as_visuals_are_ready(self):
        """visualsReady() had no callers, so the coordinated start could only
        ever be released by its 1400ms safety timeout."""
        plugin = self.make_plugin()
        plugin._start_gate_generation = 3
        plugin._start_gate_started_at = time.monotonic()
        plugin._seek_lock = threading.Lock()
        plugin._seek_hold_active = False
        plugin._external_audio = None
        plugin._engine = _FakeEngine()
        for f in (plugin._out, plugin._prev):
            f.armed_for = 3
            f.ready_for = 3
            f.is_visual_ready = lambda g, _f=f: _f.ready_for == g
            f.enqueue_operation = lambda label, func, *a: func(*a)
            f.player = _FakePlayer(f)
            f.player.pause = True

        plugin._tick()

        self.assertEqual(plugin._start_gate_generation, 0)
        self.assertFalse(plugin._engine.paused)


class _FakeAudioEngine:
    """Stands in for SingWS's own PythonKaraokeTransport."""

    def __init__(self):
        self.position = 5.0
        self.paused = False
        self.seeks = []

    def position_seconds(self):
        return self.position

    def is_paused(self):
        return self.paused

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def seek(self, seconds):
        self.seeks.append(seconds)
        self.position = seconds


@unittest.skipIf(mpv_playback is None, f"python-mpv unavailable: {MPV_IMPORT_ERROR}")
class ExternalAudioMasterTests(unittest.TestCase):
    """Best of both: mpv renders video, SingWS's audio engine keeps the clock,
    so the karaoke DSP chain stays in the signal path."""

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def make_plugin(self, settle_samples=3):
        plugin = CoordinatedSeekTests.make_plugin(self, False, settle_samples)
        plugin._audio = _FakeAudioEngine()
        plugin.setExternalAudioMaster(plugin._audio)
        return plugin

    def wait_for_release(self, plugin, timeout=5.0):
        return CoordinatedSeekTests.wait_for_release(self, plugin, timeout)

    def test_the_mpv_audio_engine_is_never_touched(self):
        plugin = self.make_plugin()
        plugin.seekMedia(42000)
        self.assertTrue(self.wait_for_release(plugin))
        self.assertEqual(plugin._engine.log, [])

    def test_seek_holds_and_realigns_the_singws_audio_engine(self):
        plugin = self.make_plugin()
        plugin.seekMedia(42000)
        self.assertTrue(plugin._audio.paused)
        self.assertEqual(plugin._audio.seeks, [])

        self.assertTrue(self.wait_for_release(plugin))
        self.assertEqual(len(plugin._audio.seeks), 1)
        self.assertGreater(plugin._audio.seeks[0], 42.0)
        self.assertFalse(plugin._audio.paused)

    def test_position_and_playing_state_come_from_singws_audio(self):
        plugin = self.make_plugin()
        plugin._audio.position = 63.5
        self.assertEqual(plugin.positionMs(), 63500)
        self.assertTrue(plugin.isPlaying())
        plugin._audio.pause()
        self.assertFalse(plugin.isPlaying())

    def test_volume_key_and_tempo_stay_with_singws(self):
        plugin = self.make_plugin()
        plugin.setVolume(0.5)
        plugin.setPitchSemitones(3)
        plugin.setTempoRatio(1.1)
        # Only the muted video followers may take tempo; nothing reaches the
        # mpv audio engine, whose sound would bypass the master bus.
        self.assertEqual(plugin._engine.log, [])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(mpv_playback is None, f"python-mpv unavailable: {MPV_IMPORT_ERROR}")
class OrphanedEngineReapTests(unittest.TestCase):
    """`mpv --idle` never exits on its own, so an engine outlives any SingWS
    that does not shut down cleanly. It must be reaped — and nothing else."""

    LISTING = (
        " 101 /Applications/SingWS.app/Contents/Frameworks/mpv --idle=yes"
        " --input-ipc-server=/tmp/singws-mpv-100-karaoke.sock\n"
        " 201 /Applications/SingWS.app/Contents/Frameworks/mpv --idle=yes"
        " --input-ipc-server=/tmp/singws-mpv-200-karaoke.sock\n"
        " 301 /Applications/SingWS.app/Contents/Frameworks/mpv --idle=yes"
        " --input-ipc-server=/tmp/singws-mpv-300-karaoke.sock\n"
        " 401 /usr/local/bin/mpv --idle=yes --input-ipc-server=/tmp/other-app.sock\n"
        " 501 /Applications/VLC.app/Contents/MacOS/VLC\n"
    )

    def run_sweep(self, *, own_pid, live_pids):
        killed = []

        def fake_kill(pid, sig):
            if sig == 0:
                if pid not in live_pids:
                    raise OSError("no such process")
                return
            killed.append((pid, sig))

        class _Result:
            stdout = self.LISTING

        with unittest.mock.patch.object(mpv_playback.os, "kill", fake_kill), \
             unittest.mock.patch.object(mpv_playback.os, "getpid", lambda: own_pid), \
             unittest.mock.patch.object(mpv_playback.subprocess, "run",
                                        lambda *a, **k: _Result()), \
             unittest.mock.patch.object(mpv_playback.glob, "glob", lambda p: []), \
             unittest.mock.patch.object(mpv_playback.time, "sleep", lambda s: None):
            mpv_playback._sweep_stale_sockets()
        return killed

    def test_engines_of_dead_sessions_are_killed(self):
        killed = self.run_sweep(own_pid=999, live_pids={999})
        self.assertEqual({pid for pid, _sig in killed}, {101, 201, 301})

    def test_a_live_session_keeps_its_engine(self):
        killed = self.run_sweep(own_pid=999, live_pids={999, 200})
        self.assertEqual({pid for pid, _sig in killed}, {101, 301})

    def test_this_session_never_reaps_itself(self):
        killed = self.run_sweep(own_pid=300, live_pids={300})
        self.assertNotIn(301, {pid for pid, _sig in killed})

    def test_unrelated_processes_are_never_touched(self):
        killed = self.run_sweep(own_pid=999, live_pids={999})
        touched = {pid for pid, _sig in killed}
        self.assertNotIn(401, touched)  # another app's mpv
        self.assertNotIn(501, touched)  # not mpv at all
