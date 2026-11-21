"""karafun_web_host_rust.py: the app side of the Rust-driven KaraFun player, against the REAL driver process and a scripted fake display program (no Qt, no network)."""
import _test_profile  # noqa: F401
import os, stat, tempfile, time, unittest
from pathlib import Path
from unittest import mock

import karafun_web_host_rust as hr
from test_karafun_driver import BIN, FAKE
from test_karafun_web_host import make as _unused  # noqa: F401  (keeps the shared fakes importable)
from test_karafun_web_host import FakeApp


@unittest.skipUnless(BIN.exists(), "build the svc binary first (cargo build -p singws-karafun-svc)")
class RustHostTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="kfhost_")
        self.fake = os.path.join(d, "fake_display.sh")
        Path(self.fake).write_text(FAKE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.log = os.path.join(d, "display.log")
        Path(self.log).write_text("")
        os.environ["FAKE_LOG"] = self.log
        self.addCleanup(os.environ.pop, "FAKE_LOG", None)
        self.app = FakeApp()
        self.entry = {"artist": "NOFX", "title": "Linoleum", "provider_track_id": "kf_3241559", "duration": 240, "duration_estimated": True}
        self.app.entry = self.entry
        self.app._active_external_karafun = {"entry": self.entry}
        self.logs = []
        self.host = hr.KaraFunWebHostRust(self.app, diag=self.logs.append, svc_path=BIN, display_path=self.fake)
        self.host._start_frames = mock.Mock()
        self.host._stop_frames = mock.Mock()
        self.addCleanup(self.host.shutdown)

    def wait(self, pred, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            self.host._drain()
            if pred():
                return
            time.sleep(0.05)
        self.fail(f"not reached in {timeout}s; statuses={self.app.statuses} notes={self.app.notes} logs={self.logs[-8:]}")

    def test_a_song_starts_after_the_hold_and_the_entry_looks_like_the_python_hosts(self):
        t0 = time.monotonic()
        self.assertTrue(self.host.start(self.entry, 0, 100))
        self.assertEqual(self.app.statuses[0], ("launching", "Starting KaraFun..."))
        self.assertIn("karafun_pending_at", self.entry)
        self.wait(lambda: self.entry.get("karafun_status") == "playing")
        self.assertGreaterEqual(time.monotonic() - t0, 3.4)
        e = self.entry
        self.assertEqual(e["karafun_submission_state"], "karafun_queued")
        self.assertNotIn("karafun_pending_at", e)
        self.assertEqual((e["karafun_key_current"], e["karafun_tempo_current"]), (0, 100))
        self.assertEqual(e["duration"], 30, "the player's duration is the verified one")
        self.assertEqual(self.app.faded, [1])
        self.assertEqual(self.app.shown_kt, [(0, 100)])
        self.assertIn(("success", "KaraFun queued and started: Linoleum"), self.app.notes)
        # entry times are in the APP's monotonic clock, as with the Python host
        self.assertLess(abs(e["karafun_display_started_at"] - (time.monotonic() - 1.0)), 6.0)
        self.host._start_frames.assert_any_call(show=False)
        self.host._start_frames.assert_any_call()

    def commands(self):
        import json
        return [json.loads(l)["cmd"] for l in Path(self.log).read_text().splitlines() if l.strip()]

    def test_controls_reach_the_player_and_the_end_completes_the_song(self):
        self.host.start(self.entry, 0, 100)
        self.wait(lambda: self.entry.get("karafun_status") == "playing")
        self.assertEqual(self.host.apply_key_tempo(self.entry, 2, 105), (2, 105))
        self.assertTrue(self.host.toggle_pause())
        self.wait(lambda: {"key", "tempo", "pause"} <= set(self.commands()))
        self.assertEqual((self.entry["karafun_key_current"], self.entry["karafun_tempo_current"]), (2, 105))
        # the player reports the end of the song: the app is told it completed, once
        self.host._send({"op": "display", "command": {"cmd": "endsong"}})
        self.wait(lambda: len(self.app.finished) == 1, timeout=15)
        self.assertEqual(self.app.finished[0][0], "complete")
        self.assertIsNone(self.host.asked)

    def test_a_driver_that_dies_mid_song_fails_the_song_and_the_next_song_starts_a_new_driver(self):
        self.assertTrue(self.host.start(self.entry, 0, 100))
        self.wait(lambda: self.entry.get("karafun_status") == "playing")
        first = self.host._proc
        first.kill()
        self.wait(lambda: self.entry.get("karafun_submission_state") == "karafun_failed")
        self.assertIsNone(self.host.asked)
        self.assertEqual(self.host.service.state, "error")
        self.assertIn("stopped unexpectedly", self.entry["karafun_submission_error"])
        self.assertTrue(any(level == "error" and "stopped unexpectedly" in text for level, text in self.app.notes), self.app.notes)
        self.assertEqual(self.host._proc, first, "nothing restarts the driver just to tell it the song is over")
        # the next song: a fresh driver process, which plays it
        nxt = {"artist": "NOFX", "title": "Linoleum", "provider_track_id": "kf_3241559", "duration": 240, "duration_estimated": True}
        self.app.entry = nxt
        self.app._active_external_karafun = {"entry": nxt}
        self.assertTrue(self.host.start(nxt, 0, 100))
        self.assertIsNot(self.host._proc, first)
        self.assertIsNone(self.host._proc.poll())
        self.wait(lambda: nxt.get("karafun_status") == "playing")

    def test_skipping_a_song_stops_the_player_and_the_next_song_plays(self):
        self.assertTrue(self.host.start(self.entry, 0, 100))
        self.wait(lambda: self.entry.get("karafun_status") == "playing")
        proc = self.host._proc
        self.assertTrue(self.host.on_finish(), "a skip with a song open")
        self.assertIsNone(self.host.asked)
        nxt = {"artist": "Sugar Ray", "title": "Someday", "provider_track_id": "kf_3240783", "duration": 200, "duration_estimated": True}
        self.app.entry = nxt
        self.app._active_external_karafun = {"entry": nxt}
        self.assertTrue(self.host.start(nxt, 0, 100))
        self.assertIs(self.host._proc, proc, "the same driver process plays the next song (no restart for a skip)")
        self.wait(lambda: nxt.get("karafun_status") == "playing")
        cmds = self.commands()
        self.assertEqual([c for c in cmds if c in ("play", "stop")], ["play", "stop", "play"], cmds)

    def test_a_driver_that_dies_between_songs_is_just_replaced(self):
        self.host.ensure_service()
        first = self.host._proc
        first.kill()
        self.wait(lambda: self.host.service.state == "error")
        self.assertEqual(self.app.notes, [])
        self.assertTrue(self.host.start(self.entry, 0, 100))
        self.assertIsNot(self.host._proc, first)
        self.wait(lambda: self.entry.get("karafun_status") == "playing")

    def test_on_finish_stops_the_player(self):
        self.host.start(self.entry, 0, 100)
        self.wait(lambda: self.entry.get("karafun_status") == "playing")
        self.assertTrue(self.host.on_finish())
        self.assertIsNone(self.host.asked)
        self.host._stop_frames.assert_called()
        names = [__import__("json").loads(l)["cmd"] for l in Path(self.log).read_text().splitlines() if l.strip()]
        self.assertIn("stop", names)

    def test_a_song_that_is_not_a_karafun_song_is_refused(self):
        other = {"artist": "x", "title": "y", "path": "/music/x.mp3"}
        self.assertFalse(self.host.can_start(other))
        self.assertFalse(self.host.start(other, 0, 100))


if __name__ == "__main__":
    unittest.main()
