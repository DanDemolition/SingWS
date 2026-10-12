"""The Rust video bridge's headless control plane (roadmap item 2, milestone V2): same C ABI as the Objective-C bridge, same behaviour.

Needs the Rust dylib (`cargo build --release -p singws-video`, or SINGWS_VIDEO_LIB) and the bundled libmpv; skips otherwise. Each scenario
runs in a fresh subprocess because libmpv keeps process-wide state (it once bit us: a leaked core silenced the silence scanner).
Scenario-level parity with the Objective-C bridge on real files is `tools/video_bridge_parity.py`."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUST_DYLIB = Path(os.environ.get("SINGWS_VIDEO_LIB") or Path.home() / ".cache/singws-rust-target/release/libsingws_video.dylib")
LIBMPV = Path(os.environ.get("SINGWS_LIBMPV") or ROOT / "native/mpv_runtime/artifacts/arm64/Frameworks/singws_libmpv.2.dylib")
OBJC_DYLIB = ROOT / "native/mpv_runtime/artifacts/arm64/Frameworks/libsingws_mpv_bridge.dylib"
FFMPEG = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True).stdout.strip()

EXPECTED_EXPORTS = """
create destroy load play pause stop seek position duration is_playing at_end visual_ready set_volume set_device set_tempo set_key
set_dsp_chain set_audio_delay set_sidefill load_background background_at_end background_position background_paused
set_background_opacity stop_background refresh_views begin_transition grab_frame free_frame scan_silence scanner_shutdown
set_rotation_host set_rotation_spotlight set_log_callback set_render_thread render_thread_active present_count
""".split()

PRELUDE = textwrap.dedent("""
    import ctypes, json, sys, time
    L = ctypes.CDLL(sys.argv[1]); D = ctypes.c_double; P = ctypes.c_void_p; I = ctypes.c_int; S = ctypes.c_char_p
    CB = ctypes.CFUNCTYPE(None, S)
    logs = []
    sink = CB(lambda b: logs.append(b.decode(errors="replace")))
    L.singws_bridge_set_log_callback.argtypes = [CB]; L.singws_bridge_set_log_callback(sink)
    L.singws_bridge_create.restype = P; L.singws_bridge_create.argtypes = [ctypes.c_size_t, ctypes.c_size_t, S, S]
    L.singws_bridge_load.restype = I; L.singws_bridge_load.argtypes = [P, S, S]
    L.singws_bridge_destroy.argtypes = [P]
    for n in ("play", "pause", "stop"): getattr(L, "singws_bridge_" + n).argtypes = [P]
    L.singws_bridge_seek.argtypes = [P, ctypes.c_int64]
    for n in ("position", "duration"):
        f = getattr(L, "singws_bridge_" + n); f.restype = ctypes.c_int64; f.argtypes = [P]
    for n in ("is_playing", "at_end", "visual_ready"):
        f = getattr(L, "singws_bridge_" + n); f.restype = I; f.argtypes = [P]
    L.singws_bridge_set_volume.argtypes = [P, D]; L.singws_bridge_set_device.argtypes = [P, S]
    L.singws_bridge_set_tempo.argtypes = [P, I]; L.singws_bridge_set_key.argtypes = [P, I]
    L.singws_bridge_set_dsp_chain.argtypes = [P, S]; L.singws_bridge_set_audio_delay.argtypes = [P, D]
    L.singws_bridge_scan_silence.restype = I
    L.singws_bridge_scan_silence.argtypes = [S, D, D, D, ctypes.POINTER(D), ctypes.POINTER(D), ctypes.POINTER(D)]
    def scan(path):
        a, b, c = D(), D(), D()
        ok = L.singws_bridge_scan_silence(path.encode(), -50.0, 0.5, 1.5, ctypes.byref(a), ctypes.byref(b), ctypes.byref(c))
        return [ok, round(a.value, 2), round(b.value, 2), round(c.value, 2)]
    def wait(fn, t=15.0):
        t0 = time.time()
        while time.time() - t0 < t:
            if fn(): return True
            time.sleep(0.05)
        return False
""")


def run_scenario(body: str, *files: str, ao_null=True) -> dict:
    env = dict(os.environ, SINGWS_LIBMPV=str(LIBMPV))
    if ao_null:
        env["SINGWS_BRIDGE_AO"] = "null"
    script = PRELUDE + textwrap.dedent(body) + "\nprint('RESULT' + json.dumps(R))\n"
    proc = subprocess.run([sys.executable, "-I", "-c", script, str(RUST_DYLIB), *files], capture_output=True, text=True, env=env, timeout=120)
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    raise AssertionError(f"scenario failed rc={proc.returncode}\nstdout={proc.stdout[-800:]}\nstderr={proc.stderr[-800:]}")


@unittest.skipUnless(RUST_DYLIB.exists() and LIBMPV.exists() and FFMPEG, "needs the built Rust dylib, the bundled libmpv and ffmpeg")
class RustVideoBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        # 2 s of silence, 4 s of tone, 3 s of silence: a known lead / trail for the scanner and a short clip for transport
        cls.clip = str(d / "clip.wav")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "2", str(d / "a.wav")], check=True)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:r=44100:d=4", "-ac", "2", str(d / "b.wav")], check=True)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", str(d / "c.wav")], check=True)
        (d / "list.txt").write_text("".join(f"file '{n}.wav'\n" for n in "abc"))
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(d / "list.txt"), "-c", "copy", cls.clip], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exports_exactly_the_objective_c_abi(self):
        out = subprocess.run(["nm", "-gU", str(RUST_DYLIB)], capture_output=True, text=True).stdout
        have = {w[len("_singws_bridge_"):] for w in out.split() if w.startswith("_singws_bridge_")}
        # Rust-only additions (the Objective-C bridge never gets them): follower mode for karaoke audio played by the Rust engine
        rust_only = {"set_follow", "follow_clock", "set_shadow_audio"}
        self.assertEqual(have, set(EXPECTED_EXPORTS) | rust_only)
        if OBJC_DYLIB.exists():
            objc = subprocess.run(["nm", "-gU", str(OBJC_DYLIB)], capture_output=True, text=True).stdout
            self.assertEqual({w[len("_singws_bridge_"):] for w in objc.split() if w.startswith("_singws_bridge_")}, have - rust_only)

    def test_silence_scan_finds_lead_and_trail(self):
        r = run_scenario("R = {'scan': scan(sys.argv[2])}", self.clip)["scan"]
        ok, lead, trail, duration = r
        self.assertEqual(ok, 1)
        self.assertAlmostEqual(duration, 9.0, delta=0.05)
        self.assertAlmostEqual(lead, 2.0, delta=0.05)       # the 2 s of leading silence
        self.assertAlmostEqual(trail, 6.0, delta=0.05)      # tone ends at 6 s, 3 s of silence after it

    def test_scan_of_a_missing_file_fails_cleanly(self):
        r = run_scenario("R = {'scan': scan('/nonexistent/none.mp3')}")["scan"]
        self.assertEqual(r[0], 0)

    def test_transport_getters_and_remembered_settings(self):
        r = run_scenario("""
            h = L.singws_bridge_create(0, 0, sys.argv[2].encode(), None)
            R = {'created': bool(h)}
            L.singws_bridge_set_volume(h, 0.0)
            R['loaded'] = wait(lambda: L.singws_bridge_duration(h) > 0)
            R['duration'] = L.singws_bridge_duration(h)
            R['playing_before_play'] = L.singws_bridge_is_playing(h)
            L.singws_bridge_play(h); time.sleep(1.0)
            R['playing'] = L.singws_bridge_is_playing(h)
            R['pos'] = L.singws_bridge_position(h)
            L.singws_bridge_set_key(h, 2); L.singws_bridge_set_tempo(h, 90)
            L.singws_bridge_set_dsp_chain(h, b'lavfi=[volume=0dB]'); L.singws_bridge_set_audio_delay(h, 0.5)
            L.singws_bridge_set_tempo(h, 100); L.singws_bridge_set_key(h, 0)
            L.singws_bridge_set_device(h, b'No Such Output Anywhere')
            time.sleep(0.8)
            L.singws_bridge_pause(h); time.sleep(0.3)
            R['playing_after_pause'] = L.singws_bridge_is_playing(h)
            L.singws_bridge_seek(h, max(0, R['duration'] - 800)); L.singws_bridge_play(h)
            R['at_end'] = wait(lambda: L.singws_bridge_at_end(h), 6.0)
            L.singws_bridge_stop(h); time.sleep(0.3)
            R['playing_after_stop'] = L.singws_bridge_is_playing(h)
            L.singws_bridge_destroy(h)
            R['logs'] = [x for x in logs if x.startswith(('[bridge] af', '[bridge] audio-delay', '[audio]', '[bridge] clean'))]
        """, self.clip)
        self.assertTrue(r["created"] and r["loaded"])
        self.assertAlmostEqual(r["duration"], 9000, delta=50)
        self.assertEqual(r["playing_before_play"], 0)
        self.assertEqual(r["playing"], 1)
        self.assertTrue(500 < r["pos"] < 2500, r["pos"])
        self.assertEqual(r["playing_after_pause"], 0)
        self.assertTrue(r["at_end"])
        self.assertEqual(r["playing_after_stop"], 0)
        logs = "\n".join(r["logs"])
        # the af chain: DSP stages first, the stretch filter (signalsmith by default) last; key and DSP survive each other; tempo != 100 alone adds it
        self.assertIn("@stretch:signalsmith=pitch-scale=1.122462", logs)
        self.assertIn("lavfi=[volume=0dB],@stretch:signalsmith=pitch-scale=1.122462", logs)
        # once the stretch filter runs, key/tempo changes go to it live (rebuilding the chain restarts the stretcher = a click) and "no change" is pitch 1.0
        self.assertIn("af key=2 live pitch-scale=1.122462 (chain kept)", logs)
        self.assertIn("af key=0 live pitch-scale=1.000000 (chain kept)", logs)
        self.assertIn("audio-delay=0.500s", logs)
        self.assertIn("requested output unavailable: No Such Output Anywhere; refusing system default", logs)   # never the implicit default
        self.assertIn("clean shutdown", logs)

    def test_a_destroyed_player_leaves_no_core_behind(self):
        """Regression: shutdown() once skipped terminate_destroy, so the leaked core kept libmpv's process-wide ffmpeg log hook and the silence
        scanner (which reads silencedetect from that log) silently reported 'no silence' for every file after the first player."""
        r = run_scenario("""
            h = L.singws_bridge_create(0, 0, sys.argv[2].encode(), None)
            wait(lambda: L.singws_bridge_duration(h) > 0)
            L.singws_bridge_destroy(h)
            R = {'scan': scan(sys.argv[2])}
        """, self.clip)["scan"]
        self.assertEqual(r[0], 1)
        self.assertAlmostEqual(r[1], 2.0, delta=0.05)

    def test_failed_create_returns_null(self):
        r = run_scenario("""
            h = L.singws_bridge_create(0, 0, b'', None)
            R = {'null': not h}
        """)
        self.assertTrue(r["null"])


if __name__ == "__main__":
    unittest.main()
