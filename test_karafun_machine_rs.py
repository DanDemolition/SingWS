"""The small KaraFun decision methods between the worker and the machine (tools/karafun_machine_parity.py): the real Python methods vs the Rust machine.rs."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_machine_parity as mp  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class RealMethods(unittest.TestCase):
    def test_a_scraped_duration_outranks_machine_metadata_but_not_a_host_override(self):
        ns, log = mp.load()
        entry = {"duration": 297, "duration_estimated": False, "title": "Memory"}
        self.assertTrue(ns["_apply_verified_duration"](entry, "4:12", source="karafun_result"))
        self.assertEqual((entry["duration"], entry["duration_source"]), (252, "karafun_result"))
        self.assertEqual(len([l for l in log if "duration corrected from 297s to 252s" in l]), 1)
        manual = {"duration": 297, "duration_estimated": False, "duration_source": "Manual override"}
        self.assertFalse(ns["_apply_verified_duration"](manual, "4:12", source="karafun_result"))
        self.assertEqual(manual["duration"], 297)

    def test_a_display_route_is_refused(self):
        ns, log = mp.load()
        for name in ("AirPlay: Living Room", "Sony BRAVIA", "Display Audio"):
            self.assertFalse(mp.run_python(ns, log, {"kind": "audio", "explicit": name, "safe_local": None})["result"][0], name)

    def test_a_route_called_apple_tv_is_refused_like_the_other_display_routes(self):
        """Fixed 2026-11-08: the guard compared "apple tv" (with a space) against a name whose spaces were removed, so it never matched."""
        ns, log = mp.load()
        for name in ("Apple TV", "apple tv", "AppleTV", "Living Room Apple TV"):
            self.assertFalse(mp.run_python(ns, log, {"kind": "audio", "explicit": name, "safe_local": None})["result"][0], name)
        self.assertTrue(mp.run_python(ns, log, {"kind": "audio", "explicit": "USB Audio", "safe_local": None})["result"][0])


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class Twin(unittest.TestCase):
    def test_a_short_run_is_identical_on_both_sides(self):
        out = subprocess.run([sys.executable, str(ROOT / "tools/karafun_machine_parity.py"), "--seeds", "600", "--binary", str(BIN)], capture_output=True, text=True, timeout=600)
        self.assertEqual(out.returncode, 0, out.stdout[-900:] + out.stderr[-400:])
        self.assertIn("IDENTICAL", out.stdout)


if __name__ == "__main__":
    unittest.main()
