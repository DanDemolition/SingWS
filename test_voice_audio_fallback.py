"""voice_audio_fallback.py: the conversion of a sound the Rust engine cannot open into a cached WAV copy (macOS afconvert)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import voice_audio_fallback as fb


def make_aac(folder: Path) -> Path:
    """A real AAC .m4a made by the system, from one second of a generated tone."""
    wav = folder / "tone.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b"\x10\x20\xf0\xe0" * 44100)
    out = folder / "tone.m4a"
    subprocess.run([fb.AFCONVERT, "-f", "m4af", "-d", "aac", str(wav), str(out)], check=True, capture_output=True)
    return out


class Convert(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="voice_fb_"))
        self.home = self.dir / "home"
        p = mock.patch.dict(os.environ, {"SINGWS_HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)
        self.lines = []

    @unittest.skipUnless(fb.available(), "needs macOS afconvert")
    def test_a_real_aac_file_becomes_a_wav_copy_and_the_copy_is_reused(self):
        src = make_aac(self.dir)
        out = fb.convert(str(src), self.lines.append)
        self.assertTrue(out and out.endswith(".wav") and out.startswith(str(self.home / "cache" / "voice_wav")))
        with wave.open(out, "rb") as w:
            self.assertEqual((w.getnchannels(), w.getframerate()), (2, 44100))
        self.assertTrue(any("converted the sound" in x for x in self.lines))
        self.lines.clear()
        self.assertEqual(fb.convert(str(src), self.lines.append), out)
        self.assertTrue(any("using the converted copy" in x for x in self.lines))

    @unittest.skipUnless(fb.available(), "needs macOS afconvert")
    def test_an_edited_file_converts_again(self):
        src = make_aac(self.dir)
        first = fb.convert(str(src))
        os.utime(src, (time.time() + 100, time.time() + 100))
        self.assertNotEqual(fb.convert(str(src)), first)

    def test_a_missing_file_or_a_failed_converter_returns_none(self):
        self.assertIsNone(fb.convert(str(self.dir / "nope.mp4"), self.lines.append))
        src = self.dir / "x.mp4"
        src.write_bytes(b"x")
        bad = mock.Mock(return_value=mock.Mock(returncode=1))
        with mock.patch.object(fb, "available", lambda: True):
            self.assertIsNone(fb.convert(str(src), self.lines.append, runner=bad))
        self.assertTrue(any("could not convert" in x for x in self.lines))
        self.assertEqual(list((self.home / "cache" / "voice_wav").glob("*.wav")), [])      # no half-written copy is left

    def test_a_converter_that_hangs_or_crashes_returns_none(self):
        src = self.dir / "x.mp4"
        src.write_bytes(b"x")
        with mock.patch.object(fb, "available", lambda: True):
            self.assertIsNone(fb.convert(str(src), runner=mock.Mock(side_effect=subprocess.TimeoutExpired("afconvert", 1))))

    def test_only_the_newest_copies_are_kept(self):
        folder = fb.cache_dir()
        folder.mkdir(parents=True)
        for i in range(fb.KEEP_FILES + 5):
            f = folder / f"{i:02d}.wav"
            f.write_bytes(b"x" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        fb._prune(folder)
        left = sorted(f.name for f in folder.glob("*.wav"))
        self.assertEqual(len(left), fb.KEEP_FILES)
        self.assertIn(f"{fb.KEEP_FILES + 4:02d}.wav", left)
        self.assertNotIn("00.wav", left)


class Packaging(unittest.TestCase):
    def test_both_specs_bundle_the_module(self):
        root = Path(__file__).parent
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = (root / spec).read_text()
            self.assertIn('"voice_audio_fallback.py"', text, spec)       # it is imported inside a function, so PyInstaller would not find it alone
            self.assertIn("'voice_audio_fallback'", text, spec)


if __name__ == "__main__":
    unittest.main()
