"""Roadmap item 3, step 3 (final design): mpv stays the PICTURE master (CDG and MP4 timing, seeking and tempo exactly as in production; its
own audio goes to a null output) and the Rust karaoke voice is SLAVED to mpv's clock, trimming its tempo by under 1% to stay on it.

Real pieces, headless: the Rust engine (paced in real time like a sound card), the Rust video bridge with real libmpv, a generated MP4 and a
real CDG disc from the library. The start/seek/pause protocol used here is the one the application will use: transport commands go to both,
and the voice only starts once it is ready. Measured: voice position minus the picture's position."""
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import rust_bgm_engine as rbe
from test_rust_video_bridge import FFMPEG, LIBMPV, ROOT, RUST_DYLIB, run_scenario

SLAVE_SCENARIO = textwrap.dedent("""
    import threading
    sys.path.insert(0, %r)
    import numpy as np
    import rust_bgm_engine as rbe
    from rust_karaoke_voice import RustKaraokeVoice
    L.singws_bridge_play.argtypes = [P]
    eng = rbe.RustBackgroundEngine(headless=True, sample_rate=48000)
    voice = RustKaraokeVoice(eng)
    arr = np.zeros((480, 2), dtype=np.float32)
    buf = arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    stop = threading.Event()
    def pump():                       # a sound card's clock: 10 ms of audio every 10 ms
        nxt = time.perf_counter()
        while not stop.is_set():
            eng._lib.singws_bgm_render(eng._h, buf, 480)
            nxt += 0.010
            d = nxt - time.perf_counter()
            if d > 0: time.sleep(d)
            else: nxt = time.perf_counter()
    h = L.singws_bridge_create(0, 0, sys.argv[2].encode(), sys.argv[3].encode() if sys.argv[3] != "-" else None)
    L.singws_bridge_load(h, sys.argv[2].encode(), sys.argv[3].encode() if sys.argv[3] != "-" else None)
    wait(lambda: L.singws_bridge_duration(h) > 0)
    voice.load(sys.argv[4], paused=True)
    threading.Thread(target=pump, daemon=True).start()
    tempo = [100.0]
    def pic():                        # the picture's position, seconds
        return L.singws_bridge_position(h) / 1000.0
    def feed(seconds, stalled=False):
        # control loop: report the picture's clock to the voice every ~30 ms (a stalled GUI sends nothing)
        t0 = time.time()
        while time.time() - t0 < seconds:
            if not stalled:
                voice.slave_to(pic(), tempo[0] / 100.0 if voice.is_playing() else 0.0)
            time.sleep(0.03)
    def play_both():
        # the voice needs a moment before its first sound comes out (the stretcher's own latency): start it first, and start the picture
        # as soon as the voice's clock begins to move
        voice.play()
        t0 = time.time()
        origin = voice.position_seconds()
        while time.time() - t0 < 0.6 and voice.position_seconds() <= origin + 0.002:
            time.sleep(0.005)
        L.singws_bridge_play(h)
    def start_both():
        # protocol: both loaded paused; the voice is ready (not pending) -> start the voice, then the picture
        voice.slave_to(pic(), 0.0)
        feed(0.3)
        play_both()
    def seek_both(seconds):
        # protocol: pause both, seek both, wait for the voice's source, then play both again
        L.singws_bridge_pause(h); voice.pause()
        L.singws_bridge_seek(h, int(seconds * 1000)); voice.seek_seconds(seconds)
        feed(0.8)
        voice.slave_to(pic(), 0.0)
        play_both()
    phases = []
    def measure(name, seconds, settle=1.5, stalled=False):
        errs = []
        t0 = time.time()
        while time.time() - t0 < seconds:
            if not stalled:
                voice.slave_to(pic(), tempo[0] / 100.0 if voice.is_playing() else 0.0)
            time.sleep(0.03)
            if time.time() - t0 > settle:
                errs.append(abs(voice.position_seconds() - pic()))
        errs.sort()
        phases.append([name, round(errs[len(errs)//2], 3) if errs else None, round(errs[-1], 3) if errs else None, len(errs)])
    start_both()
    measure("steady", 6, 2.5)
    L.singws_bridge_set_tempo(h, 150); voice.set_tempo_percent(150); tempo[0] = 150.0
    measure("tempo 150", 8, 4.0)
    L.singws_bridge_set_tempo(h, 60); voice.set_tempo_percent(60); tempo[0] = 60.0
    measure("tempo 60", 12, 8.0)
    L.singws_bridge_set_tempo(h, 100); voice.set_tempo_percent(100); tempo[0] = 100.0
    measure("tempo 100", 8, 4.0)
    seek_both(20.0)
    measure("after seek to 20 s", 6, 3.0)
    L.singws_bridge_pause(h); voice.pause()
    measure("paused", 2, 0.5)
    play_both()
    measure("resumed", 5, 2.5)
    measure("GUI stalled 5 s (no clock updates)", 5, 0.5, stalled=True)
    stop.set(); time.sleep(0.05)
    R = {"phases": phases}
    L.singws_bridge_destroy(h)
    eng.close()
""")


@unittest.skipUnless(RUST_DYLIB.exists() and LIBMPV.exists() and FFMPEG and rbe.available(), "needs the Rust libraries, libmpv and ffmpeg")
class KaraokeSlaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SINGWS_BRIDGE_SHADOW_AUDIO"] = "1"        # mpv keeps only its audio clock; the Rust voice plays the sound
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.mp4 = str(d / "video.mp4")
        cls.song = str(d / "song.wav")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi", "-i",
                        "sine=f=440:r=44100", "-t", "60", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", cls.mp4], check=True)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:r=44100", "-t", "60", "-ac", "2", cls.song], check=True)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("SINGWS_BRIDGE_SHADOW_AUDIO", None)
        cls.tmp.cleanup()

    def _check(self, r, label, median_limit, worst_limit):
        print(f"\n{label}:", json.dumps(r["phases"]))
        for name, median, worst, count in r["phases"]:
            self.assertIsNotNone(median, name)
            self.assertLess(median, median_limit, f"{label} {name}: median voice-vs-picture error {median} s")
            self.assertLess(worst, worst_limit, f"{label} {name}: worst error {worst} s")

    def test_mp4_voice_stays_on_the_picture_clock(self):
        r = run_scenario(SLAVE_SCENARIO % str(ROOT), self.mp4, "-", self.song)
        self._check(r, "MP4", 0.06, 0.25)

    def test_cdg_voice_stays_on_the_picture_clock(self):
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
        r = run_scenario(SLAVE_SCENARIO % str(ROOT), str(d / "disc.cdg"), str(d / "disc.mp3"), str(d / "disc.mp3"))
        self._check(r, "CDG", 0.06, 0.25)


if __name__ == "__main__":
    unittest.main()
