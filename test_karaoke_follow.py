"""Roadmap item 3, step 3: the picture follows the Rust audio engine's clock.

The REAL pieces, headless: the Rust engine plays a tone as the karaoke voice (paced in real time by a thread, like a sound card would pull),
the Rust video bridge loads a test video in follower mode (picture only) and is fed the voice's clock, and the video position reported by
mpv is compared with the audio's song position through steady play, tempo changes, a seek, a pause and a resume. What is measured is mpv's
`time-pos` (the frame being shown), which is the thing that must track the audio; the final on-screen proof with a flash-and-beep clip is
part of the rehearsal. Skipped without the Rust libraries, libmpv or ffmpeg."""
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import rust_bgm_engine as rbe
from test_rust_video_bridge import FFMPEG, LIBMPV, ROOT, RUST_DYLIB, run_scenario

FOLLOW_SCENARIO = textwrap.dedent("""
    import threading
    sys.path.insert(0, %r)
    import rust_bgm_engine as rbe
    from rust_karaoke_voice import RustKaraokeVoice
    L.singws_bridge_set_follow.argtypes = [P, I]
    L.singws_bridge_follow_clock.argtypes = [P, D, D]
    eng = rbe.RustBackgroundEngine(headless=True, sample_rate=48000)
    voice = RustKaraokeVoice(eng)
    stop = threading.Event()
    def pump():                       # a sound card's clock: 10 ms of audio every 10 ms
        nxt = time.perf_counter()
        while not stop.is_set():
            eng._lib.singws_bgm_render(eng._h, buf, 480)
            nxt += 0.010
            d = nxt - time.perf_counter()
            if d > 0: time.sleep(d)
            else: nxt = time.perf_counter()
    import numpy as np
    arr = np.zeros((480, 2), dtype=np.float32)
    buf = arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    h = L.singws_bridge_create(0, 0, sys.argv[2].encode(), None)
    L.singws_bridge_set_follow(h, 1)
    L.singws_bridge_load(h, sys.argv[2].encode(), None)
    wait(lambda: L.singws_bridge_duration(h) > 0)
    # the start protocol: the picture is loaded paused and already receiving the (paused) clock BEFORE the audio is started
    voice.load(sys.argv[3], paused=True)
    threading.Thread(target=pump, daemon=True).start()
    for _ in range(10):
        L.singws_bridge_follow_clock(h, voice.position_seconds(), 0.0); time.sleep(0.03)
    voice.play()
    phases = []
    SPARSE = sys.argv[2].endswith(".cdg")
    def run_phase(name, seconds, settle=1.5, static=False):
        # compare the picture with the audio at the moments a NEW picture appears (a CDG shows nothing new for seconds at a time)
        errs = []
        t0 = time.time()
        last_v = None
        last_change = 0.0
        while time.time() - t0 < seconds:
            pos = voice.position_seconds()
            playing = voice.is_playing()
            L.singws_bridge_follow_clock(h, pos, (voice.tempo_percent / 100.0) if playing else 0.0)
            time.sleep(0.03)
            v = L.singws_bridge_position(h) / 1000.0
            if last_v is not None and v != last_v:
                now = time.time()
                first_of_burst = (now - last_change) > 0.3          # a CDG redraw is a burst; only its first frame is a clock reading
                if now - t0 > settle and not static and (first_of_burst or not SPARSE):
                    errs.append(v - voice.position_seconds())
                last_change = now
            last_v = v
        if static:
            errs = [L.singws_bridge_position(h) / 1000.0 - voice.position_seconds()]
        errs = sorted(abs(e) for e in errs)
        phases.append([name, round(errs[len(errs)//2], 3) if errs else None, round(errs[-1], 3) if errs else None, len(errs)])
    run_phase("steady", 5, 2.0)
    voice.set_tempo_percent(150); run_phase("tempo 150", 5)
    voice.set_tempo_percent(60); run_phase("tempo 60", 5)
    voice.set_tempo_percent(100); run_phase("tempo 100", 4)
    voice.seek_seconds(20.0); run_phase("after seek to 20 s", 5, 2.5)
    voice.pause(); run_phase("paused", 3, 1.0, static=True)
    voice.play(); run_phase("resumed", 4, 2.0)
    # the GUI thread stalls: NO clock updates for 4 s (the follower must extrapolate), measured against the real audio position
    errs = []
    t0 = time.time()
    last_v = None
    last_change = 0.0
    while time.time() - t0 < 4.0:
        time.sleep(0.03)
        v = L.singws_bridge_position(h) / 1000.0
        if last_v is not None and v != last_v:
            now = time.time()
            if now - t0 > 0.5 and ((now - last_change) > 0.3 or not SPARSE):
                errs.append(abs(v - voice.position_seconds()))
            last_change = now
        last_v = v
    errs.sort()
    phases.append(["GUI stalled 4 s", round(errs[len(errs)//2], 3) if errs else None, round(errs[-1], 3) if errs else None, len(errs)])
    stop.set(); time.sleep(0.05)
    R = {"phases": phases, "seeks": len([x for x in logs if x.startswith("[follow] seek")]),
         "follower_on": any("follower mode ON" in x for x in logs), "log": [x for x in logs if x.startswith("[follow")][:60]}
    L.singws_bridge_destroy(h)
    eng.close()
""")


@unittest.skipUnless(RUST_DYLIB.exists() and LIBMPV.exists() and FFMPEG and rbe.available(), "needs the Rust libraries, libmpv and ffmpeg")
class KaraokeFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.video = str(d / "video.mp4")
        cls.song = str(d / "song.wav")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-t", "60",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", cls.video], check=True)
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:r=44100", "-t", "60", "-ac", "2", cls.song], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_picture_stays_on_the_audio_clock(self):
        r = run_scenario(FOLLOW_SCENARIO % str(ROOT), self.video, self.song)
        print("\nfollow:", json.dumps(r["phases"]), "hard seeks:", r["seeks"])
        self.assertTrue(r["follower_on"])
        for name, median, worst, count in r["phases"]:
            self.assertIsNotNone(median, name)
            self.assertLess(median, 0.06, f"{name}: median picture error {median} s")
            self.assertLess(worst, 0.20, f"{name}: worst picture error {worst} s")
        # a seek and a couple of tempo/pause corrections are expected, not a seek storm
        self.assertLess(r["seeks"], 8)


    def test_a_real_cdg_disc_follows_its_mp3_played_by_the_rust_engine(self):
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
        r = run_scenario(FOLLOW_SCENARIO % str(ROOT), str(d / "disc.cdg"), str(d / "disc.mp3"))
        print("\nCDG follow:", json.dumps(r["phases"]), "hard seeks:", r["seeks"])
        for name, median, worst, count in r["phases"]:
            if name == "paused":
                continue        # a CDG shows its last changed screen while paused; there is no frame to compare
            if median is None:
                continue        # no new picture appeared in this phase (a long static lyrics screen): nothing to judge
            self.assertLess(median, 0.08, f"{name}: median picture error {median} s")
            self.assertLess(worst, 0.30, f"{name}: worst picture error {worst} s")


if __name__ == "__main__":
    unittest.main()
