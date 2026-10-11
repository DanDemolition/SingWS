"""Every audio format the pad file dialog offers (mp3 wav m4a aac ogg flac aiff) must decode on the Rust pad path (2026-10-11: AIFF did not until the
decoder feature was switched on). Test files are generated with ffmpeg; formats whose encoder this machine lacks are skipped, not failed."""
import math
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import rust_bgm_engine as rbe
from soundboard_channels import RustSoundboardChannel

FFMPEG = shutil.which("ffmpeg")
RATE = 48000

# name, extension, ffmpeg output args, extra input filters
CASES = [
    ("wav 16-bit", "wav", []),
    ("wav 24-bit", "wav", ["-c:a", "pcm_s24le"]),
    ("wav float", "wav", ["-c:a", "pcm_f32le"]),
    ("wav 44.1 kHz", "wav", ["-ar", "44100"]),
    ("wav mono", "wav", ["-ac", "1"]),
    ("aiff big-endian", "aiff", ["-c:a", "pcm_s16be"]),
    ("flac", "flac", []),
    ("aac in m4a", "m4a", ["-c:a", "aac", "-b:a", "128k"]),
    ("alac in m4a", "m4a", ["-c:a", "alac"]),
    ("aac adts", "aac", ["-c:a", "aac", "-f", "adts"]),
    ("mp3", "mp3", ["-c:a", "libmp3lame"]),
    ("ogg vorbis", "ogg", ["-ac", "2", "-c:a", "vorbis", "-strict", "-2"]),
    ("caf", "caf", ["-c:a", "pcm_s16le"]),
]


@unittest.skipUnless(rbe.available(), "libsingws_bgm_ffi.dylib not built")
@unittest.skipUnless(FFMPEG, "ffmpeg not installed")
class PadFormatTests(unittest.TestCase):
    def test_every_pad_dialog_format_decodes_and_plays(self):
        engine = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.addCleanup(engine.close)
        tested, failures = 0, []
        with tempfile.TemporaryDirectory() as tmp:
            for name, ext, args in CASES:
                path = Path(tmp) / f"clip_{tested}.{ext}"
                cmd = [FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=1000:duration=2:sample_rate={RATE}",
                       "-af", "volume=2.0", *args, str(path)]
                if subprocess.run(cmd, capture_output=True).returncode != 0 or not path.exists() or path.stat().st_size == 0:
                    continue                                   # this machine's ffmpeg cannot make that format
                ch = RustSoundboardChannel(lambda: engine)
                try:
                    ch.load(str(path), 1.0)
                    ch.play()
                    out = engine.render(24000)
                    level = float(np.sqrt(np.mean(out[6000:].astype(np.float64) ** 2)))
                    if not (0.05 < level < 0.5):
                        failures.append(f"{name}: level {level:.3f}")
                except Exception as exc:               # noqa: BLE001
                    failures.append(f"{name}: {exc}")
                finally:
                    ch.close()
                    engine.pad_stop_all()
                    engine.render(2400)
                tested += 1
        self.assertGreaterEqual(tested, 6, "ffmpeg could make too few formats to be a meaningful test")
        self.assertEqual(failures, [], f"{tested} formats tested")


if __name__ == "__main__":
    unittest.main()
