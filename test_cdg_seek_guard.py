"""A seek into the graphics-free tail of a CDG must not send the picture to the end (docs/design/stage-e-no-python-host-spike-2.md, follow-up).

The unit tests use a built CDG file; the transport test uses the real disc `CC - Radiohead - Let Down` from the library when it is on this machine
(its graphics stop at 278.03 s and the closing clear is at 300.50 s) and runs the real MpvKaraokeTransport with the real Rust bridge, headless."""
import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import cdg_seek_guard as g
import rust_bgm_engine as rbe
from test_rust_video_bridge import LIBMPV, ROOT, RUST_DYLIB

DISC = Path.home() / "Music/Karaoke/Karaoke Library/CC - Radiohead - Let Down"


def build(commands):
    """A CDG file from {packet index: 1} entries, empty packets elsewhere."""
    n = max(commands) + 1
    data = bytearray(n * 24)
    for i in commands:
        data[i * 24] = 9
        data[i * 24 + 1] = 1          # memory preset
    return bytes(data)


class GuardTests(unittest.TestCase):
    def test_a_quiet_tail_before_the_closing_burst_is_found(self):
        gap = g.terminal_gap(build([0, 300, 3000, 3001, 9000, 9001, 9002]))
        self.assertEqual(gap, (3001 / 300.0, 9000 / 300.0))

    def test_no_gap_when_the_graphics_run_to_the_end(self):
        self.assertIsNone(g.terminal_gap(build([0, 300, 600, 900])))

    def test_a_quiet_stretch_in_the_middle_is_not_a_tail(self):
        self.assertIsNone(g.terminal_gap(build([0, 3000, 6000, 6300, 6600, 6900])))

    def test_a_long_quiet_stretch_in_the_middle_is_a_dead_zone_but_a_short_one_is_not(self):
        self.assertEqual(g.dead_zones(build([0, 600, 3000, 3001, 6000, 6300, 6600, 6900])), [(600 / 300.0, 3000 / 300.0), (3001 / 300.0, 6000 / 300.0)])
        self.assertEqual(g.dead_zones(build([0, 300, 600, 900, 1200])), [])

    def test_several_zones_each_clamp_to_their_own_start(self):
        zones = [(100.0, 130.0), (200.0, 260.0)]
        self.assertEqual(g.clamp_seek(zones, 230.0), 200.0)
        self.assertEqual(g.clamp_seek(zones, 115.0), 100.0)
        self.assertEqual(g.clamp_seek(zones, 150.0), 150.0)

    def test_seek_target_moves_back_only_inside_the_gap(self):
        gap = (100.0, 130.0)
        self.assertEqual(g.clamp_seek(gap, 90.0), 90.0)
        self.assertEqual(g.clamp_seek(gap, 100.0), 100.0)
        self.assertEqual(g.clamp_seek(gap, 115.0), 100.0)
        self.assertEqual(g.clamp_seek(gap, 130.0), 130.0)
        self.assertEqual(g.clamp_seek(gap, 200.0), 200.0)
        self.assertEqual(g.clamp_seek(None, 115.0), 115.0)

    def test_a_tile_outside_the_picture_makes_no_frame(self):
        data = bytearray(build([0]))
        data += bytes(24 * 2)
        data[24] = 9
        data[25] = 6
        data[24 + 6] = 31             # row far outside
        self.assertEqual(g.frame_times(bytes(data)), [0.0])

    def test_unreadable_file_means_no_guard(self):
        self.assertIsNone(g.load_gap("/nonexistent/file.cdg"))

    @unittest.skipUnless((DISC.with_suffix(".cdg")).exists(), "the Let Down disc is not on this machine")
    def test_the_real_disc_has_the_documented_gap(self):
        gap = g.load_gap(DISC.with_suffix(".cdg"))[-1]
        self.assertAlmostEqual(gap[0], 278.03, places=1)
        self.assertAlmostEqual(gap[1], 300.50, places=1)


SCENARIO = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, %r)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SINGWS_HOME"] = %r
    os.environ["SINGWS_VIDEO_BRIDGE"] = "rust"
    os.environ["SINGWS_VIDEO_LIB"] = %r
    os.environ["SINGWS_LIBMPV"] = %r
    os.environ["SINGWS_BRIDGE_AO"] = "null"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from mpv_playback_iina import MpvPlaybackPlugin
    from mpv_karaoke_transport import MpvKaraokeTransport
    plugin = MpvPlaybackPlugin(log=lambda *_: None)
    t = MpvKaraokeTransport(plugin, audio_path=sys.argv[2], video_path=sys.argv[1], mode="cdg")
    def spin(s):
        t0 = time.time()
        while time.time() - t0 < s:
            app.processEvents(); time.sleep(0.01)
    t.start(0.0); spin(1.5)
    out = {}
    for target in (270.0, 288.6, 285.0):
        t.seek(target); spin(1.5)
        out[str(target)] = round(t.position_seconds(), 2)
    t.stop()
    print("RESULT" + json.dumps(out))
""")


@unittest.skipUnless(RUST_DYLIB.exists() and LIBMPV.exists() and rbe.available() and DISC.with_suffix(".cdg").exists(),
                     "needs the Rust libraries, libmpv and the Let Down disc")
class TransportSeekTests(unittest.TestCase):
    def test_seeking_into_the_silent_tail_stays_before_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            src = SCENARIO % (str(ROOT), home, str(RUST_DYLIB), str(LIBMPV))
            proc = subprocess.run([sys.executable, "-I", "-c", src, str(DISC.with_suffix(".cdg")), str(DISC.with_suffix(".mp3"))],
                                  capture_output=True, text=True, timeout=120)
        line = next((x for x in proc.stdout.splitlines() if x.startswith("RESULT")), None)
        self.assertIsNotNone(line, f"scenario failed rc={proc.returncode}\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}")
        pos = json.loads(line[6:])
        self.assertAlmostEqual(pos["270.0"], 270.0, delta=1.5)
        for target in ("288.6", "285.0"):
            self.assertLess(pos[target], 280.0, f"seek to {target} sent the picture to {pos[target]}")


if __name__ == "__main__":
    unittest.main()
