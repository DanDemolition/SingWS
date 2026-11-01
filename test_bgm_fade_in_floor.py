"""Setting `bgm_fade_in_floor_db`: where the background music's fade IN from silence starts (default -60 dB = the curve measured on the old BASS
engine; about -40 makes the music audible sooner). Runs the REAL Rust engine headless; the engine tests skip when the library lacks the setter
(a library built before this setting)."""
import importlib.util
import math
import os
import re
import tempfile
import unittest
from pathlib import Path

import numpy as np

import rust_bgm_engine as rbe
from test_rust_bgm_engine import tone_file, RATE

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent


def _has_setter() -> bool:
    try:
        lib = rbe._load()
    except Exception:
        return False
    return getattr(lib, "singws_bgm_set_fade_in_floor_db", None) is not None


def level_db(x) -> float:
    return 20 * math.log10(max(1e-9, float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))))


class SettingTests(unittest.TestCase):
    def test_the_default_keeps_the_bass_curve(self):
        src = (_ROOT / "0.2.18.1.py").read_text()
        self.assertRegex(src, r'"bgm_fade_in_floor_db": -60\.0,')

    def test_the_app_applies_the_setting_when_the_host_chain_attaches(self):
        src = (_ROOT / "0.2.18.1.py").read_text()
        i = src.index("def _attach_host_audio_chain")
        body = src[i:i + 1800]
        self.assertIn('"bgm_fade_in_floor_db"', body)
        self.assertIn("set_fade_in_floor_db", body)


@unittest.skipUnless(rbe.available() and _has_setter(), "Rust BGM library with set_fade_in_floor_db not built")
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.addCleanup(self.e.close)
        self.path = tone_file(self.tmp.name, "t.wav", 440, 0.5, 8.0)

    def fade_levels(self, floor_db):
        e = self.e
        if floor_db is not None:
            self.assertTrue(e.set_fade_in_floor_db(floor_db))
        e.load(self.path, paused=False, volume=0.0)
        e.slide_master_volume(0.75, 3000)
        first = e.render(RATE // 20)             # 0 .. 0.05 s
        e.render(int(RATE * 0.95) - RATE // 20)  # up to 0.95 s
        at_1s = e.render(RATE // 10)             # 0.95 .. 1.05 s
        e.render(int(RATE * 1.9))                # up to ~2.95 s
        e.render(RATE // 2)                      # past the end of the fade
        final = e.render(RATE // 4)
        return level_db(first), level_db(at_1s) - level_db(final), level_db(final)

    def test_default_curve_is_still_inaudible_after_a_second(self):
        first, at_1s, _ = self.fade_levels(None)
        self.assertLess(at_1s, -35.0, f"the old -60 dB curve is about -38 dB under the final level at 1 s, was {at_1s:.1f}")
        self.assertLess(first, -50.0)

    def test_a_raised_floor_is_audible_sooner_and_ends_at_the_same_level(self):
        first_d, at_1s_d, final_d = self.fade_levels(None)
        self.e.stop()
        first, at_1s, final = self.fade_levels(-40.0)
        self.assertGreater(at_1s, -29.0, f"-40 dB floor: about -25 dB under the final level at 1 s, was {at_1s:.1f}")
        self.assertLess(at_1s, -21.0)
        self.assertGreater(first, first_d + 10.0, "the very first moments are far louder than the default curve")
        self.assertAlmostEqual(final, final_d, delta=0.2, msg="the final level is the same")

    def test_nonsense_values_are_clamped_not_fatal(self):
        for v in (-200.0, 50.0, float("nan"), float("inf")):
            self.assertTrue(self.e.set_fade_in_floor_db(v))
        self.e.load(self.path, paused=False, volume=0.0)
        self.e.slide_master_volume(0.75, 500)
        self.e.render(RATE)
        self.assertGreater(float(np.abs(self.e.render(RATE // 10)).max()), 0.1)


if __name__ == "__main__":
    unittest.main()
