"""The real MpvKaraokeTransport with the Rust karaoke voice, end to end and headless (roadmap item 3, step 4).

Real pieces: the Rust video bridge in shadow-audio mode (mpv keeps only its audio clock), the real mpv plugin wrapper, the Rust audio engine
(paced like a sound card) and the voice, a generated MP4 and a real CDG disc from the library. The calls are the ones the application makes:
start, key/tempo, seek, pause/resume, stop. What is measured is the sound's song position against the picture's. Runs in a subprocess because
libmpv keeps process-wide state."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import rust_bgm_engine as rbe
from test_rust_video_bridge import FFMPEG, LIBMPV, ROOT, RUST_DYLIB

SCENARIO = textwrap.dedent("""
    import json, os, sys, time, threading
    sys.path.insert(0, %r)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SINGWS_HOME"] = %r
    os.environ["SINGWS_VIDEO_BRIDGE"] = "rust"
    os.environ["SINGWS_VIDEO_LIB"] = %r
    os.environ["SINGWS_LIBMPV"] = %r
    os.environ["SINGWS_BRIDGE_AO"] = "null"
    import numpy as np, ctypes
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import rust_bgm_engine as rbe
    from rust_karaoke_voice import RustKaraokeVoice
    from mpv_playback_iina import MpvPlaybackPlugin
    from mpv_karaoke_transport import MpvKaraokeTransport
    logs = []
    plugin = MpvPlaybackPlugin(log=logs.append)
    assert plugin.setShadowAudio(True), "the Rust bridge must support shadow audio"
    eng = rbe.RustBackgroundEngine(headless=True, sample_rate=48000)
    voice = RustKaraokeVoice(eng)
    arr = np.zeros((480, 2), dtype=np.float32)
    buf = arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    stop = threading.Event()
    def pump():
        nxt = time.perf_counter()
        while not stop.is_set():
            eng._lib.singws_bgm_render(eng._h, buf, 480)
            nxt += 0.010
            d = nxt - time.perf_counter()
            if d > 0: time.sleep(d)
            else: nxt = time.perf_counter()
    threading.Thread(target=pump, daemon=True).start()
    t = MpvKaraokeTransport(plugin, audio_path=sys.argv[3], video_path=sys.argv[2], mode=sys.argv[1])
    t.attach_voice(voice, log=logs.append)
    started = []
    t.started.connect(lambda: started.append(time.time()))
    t.set_modifiers(1.0, 0.0)
    t.set_volume(1.0)
    def spin(seconds):
        t0 = time.time()
        while time.time() - t0 < seconds:
            app.processEvents(); time.sleep(0.01)
    errs = {}
    def measure(name, seconds, settle):
        e = []; t0 = time.time()
        while time.time() - t0 < seconds:
            app.processEvents(); time.sleep(0.02)
            if time.time() - t0 > settle:
                e.append(abs(voice.position_seconds() - t.position_seconds()))
        e.sort()
        errs[name] = [round(e[len(e)//2], 3), round(e[-1], 3), len(e)]
    t.start(0.0)
    spin(1.5)
    measure("steady", 6, 2.0)
    t.set_modifiers(1.25, 2.0)
    measure("tempo 125 + key +2", 12, 8.0)
    t.seek(30.0)
    spin(1.5)
    measure("after seek to 30 s", 6, 2.5)
    pos_after_seek = t.position_seconds()
    t.pause(); spin(0.6)
    paused_pos = (round(t.position_seconds(), 2), round(voice.position_seconds(), 2))
    spin(1.0)
    paused_still = abs(t.position_seconds() - paused_pos[0]) < 0.05 and not voice.is_playing()
    t.resume()
    measure("resumed", 5, 2.5)
    t.stop(); spin(0.3)
    R = {"errs": errs, "started": len(started), "pos_after_seek": round(pos_after_seek, 1), "paused_still": bool(paused_still),
         "voice_playing_after_stop": bool(voice.is_playing()), "fallback": any("falling back" in x for x in logs),
         "voice_active_at_end": t.voice_active()}
    stop.set(); time.sleep(0.05)
    eng.close()
    print("RESULT" + json.dumps(R))
""")


@unittest.skipUnless(RUST_DYLIB.exists() and LIBMPV.exists() and FFMPEG and rbe.available(), "needs the Rust libraries, libmpv and ffmpeg")
class TransportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.mp4 = str(d / "video.mp4")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi", "-i",
                        "sine=f=440:r=44100", "-t", "90", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", cls.mp4], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_scenario(self, mode, video, audio):
        src = SCENARIO % (str(ROOT), self.tmp.name, str(RUST_DYLIB), str(LIBMPV))
        proc = subprocess.run([sys.executable, "-I", "-c", src, mode, video, audio], capture_output=True, text=True, timeout=240)
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT"):
                return json.loads(line[6:])
        raise AssertionError(f"scenario failed rc={proc.returncode}\nstdout={proc.stdout[-1200:]}\nstderr={proc.stderr[-1200:]}")

    def check(self, r, label):
        print(f"\n{label}:", json.dumps(r["errs"]), "started:", r["started"])
        self.assertFalse(r["fallback"], "the voice must not have fallen back to mpv audio")
        self.assertTrue(r["voice_active_at_end"])
        self.assertGreaterEqual(r["started"], 1, "the transport must announce the start")
        for name, (median, worst, n) in r["errs"].items():
            self.assertGreater(n, 20, name)
            self.assertLess(median, 0.08, f"{label} {name}: median error {median} s")
            self.assertLess(worst, 0.30, f"{label} {name}: worst error {worst} s")
        self.assertTrue(30.0 < r["pos_after_seek"] < 50.0, r["pos_after_seek"])   # sought to 30 s, then ~10 s more at 1.25x
        self.assertTrue(r["paused_still"], "pause must stop both the picture and the voice")
        self.assertFalse(r["voice_playing_after_stop"])

    def test_mp4_through_the_real_transport(self):
        self.check(self.run_scenario("mp4", self.mp4, self.mp4), "MP4")

    def test_a_real_cdg_disc_through_the_real_transport(self):
        import glob
        import zipfile
        zips = sorted(glob.glob(str(Path.home() / "Music/Karaoke/Karaoke Library/*.zip")))
        if not zips:
            self.skipTest("no karaoke library on this machine")
        d = Path(self.tmp.name)
        for z in zips:
            try:
                with zipfile.ZipFile(z) as zf:
                    mp3 = next((n for n in zf.namelist() if n.lower().endswith(".mp3") and "__MACOSX" not in n), None)
                    cdg = next((n for n in zf.namelist() if n.lower().endswith(".cdg") and "__MACOSX" not in n), None)
                    if not (mp3 and cdg):
                        continue
                    (d / "disc.mp3").write_bytes(zf.read(mp3))
                    (d / "disc.cdg").write_bytes(zf.read(cdg))
                    break
            except Exception:
                continue
        else:
            self.skipTest("no usable CDG disc found")
        self.check(self.run_scenario("cdg", str(d / "disc.cdg"), str(d / "disc.mp3")), "CDG")


if __name__ == "__main__":
    unittest.main()
