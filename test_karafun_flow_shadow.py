"""karafun_flow_shadow: the three log-only comparisons (watchdog, readback, startflow) against the real Rust helper, plus the hooks in 0.2.18.1.py."""
import os
import unittest
from pathlib import Path

import karafun_flow_shadow as fs
import karafun_script_shadow as ks

ROOT = Path(__file__).parent
HELPER = os.environ.get("SINGWS_KARAFUN_BIN") or str(Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")
HAVE = Path(HELPER).is_file()
if HAVE:
    os.environ["SINGWS_KARAFUN_BIN"] = HELPER


def fresh():
    fs._clean_noted.clear()
    fs._mismatch_seen.clear()


@unittest.skipUnless(HAVE, "needs the built singws-karafun helper")
class WithTheHelper(unittest.TestCase):
    def setUp(self):
        fresh()

    def run_one(self, fn, **kw):
        lines = []
        t = fn({}, lines.append, **kw)
        self.assertIsNotNone(t)
        t.join(20)
        return lines

    def test_watchdog_extend_and_warn_are_clean(self):
        ext = self.run_one(fs.watchdog, now=500.0, last_playing_ts=None, confirmed_at=400.0, duration=263, seek_offset=0.0, python_extend=165.0)
        self.assertEqual(ext, ["[KARAFUN-FLOW-SHADOW] watchdog first check clean"])
        fresh()
        warn = self.run_one(fs.watchdog, now=900.0, last_playing_ts=None, confirmed_at=None, duration=263, seek_offset=0.0, python_extend=None)
        self.assertEqual(warn, ["[KARAFUN-FLOW-SHADOW] watchdog first check clean"])

    def test_watchdog_difference_is_logged_by_field_only(self):
        lines = self.run_one(fs.watchdog, now=500.0, last_playing_ts=None, confirmed_at=400.0, duration=263, seek_offset=0.0, python_extend=15.0)
        self.assertEqual(lines, ["[KARAFUN-FLOW-SHADOW] MISMATCH watchdog fields=extend"])

    def test_readback_clean_and_mismatch(self):
        ok = self.run_one(fs.readback, known=(0, 100), first=(2, 100), second=(2, 100), python_values=(2, 100))
        self.assertEqual(ok, ["[KARAFUN-FLOW-SHADOW] readback first check clean"])
        fresh()
        bad = self.run_one(fs.readback, known=(0, 100), first=(2, 100), second=(2, 100), python_values=None)
        self.assertEqual(bad, ["[KARAFUN-FLOW-SHADOW] MISMATCH readback fields=values"])

    def test_startflow_clean_and_mismatch(self):
        kw = dict(fast_start=True, managed_handoff=False, managed_start_state="UNKNOWN", explicit_play_sent=False, probe_ok=True, probe_state="PLAYING", was_running=True,
                  python_initial="PLAYING", python_verified=False)
        ok = self.run_one(fs.startflow, python_action="SkipAlreadyPlaying", **kw)
        self.assertEqual(ok, ["[KARAFUN-FLOW-SHADOW] startflow first check clean"])
        fresh()
        bad = self.run_one(fs.startflow, python_action="Press", **kw)
        self.assertEqual(bad, ["[KARAFUN-FLOW-SHADOW] MISMATCH startflow fields=action"])


class Quiet(unittest.TestCase):
    def test_off_and_no_helper_do_nothing(self):
        self.assertIsNone(fs.readback({"karafun_flow_shadow": False}, print, known=(0, 100), first=None, second=None, python_values=None))
        old = os.environ.get("SINGWS_KARAFUN_BIN")
        os.environ["SINGWS_KARAFUN_BIN"] = "/nonexistent"
        try:
            orig = ks.find_helper
            ks.find_helper = lambda: None
            self.assertIsNone(fs.readback({}, print, known=(0, 100), first=None, second=None, python_values=None))
            self.assertEqual(fs.announce({}, lambda t: None), "enabled available=0")
        finally:
            ks.find_helper = orig
            if old is None:
                os.environ.pop("SINGWS_KARAFUN_BIN", None)
            else:
                os.environ["SINGWS_KARAFUN_BIN"] = old

    def test_a_broken_helper_answer_is_skipped_not_raised(self):
        lines = []
        self.assertEqual(fs.run_shadow("readback", {"known": None, "first": None, "second": None}, {"values": None}, lines.append, helper=Path("/bin/false")), "skipped")

    def test_the_hooks_are_in_the_app_and_cannot_raise(self):
        src = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
        for needle in ("karafun_flow_shadow.watchdog(", "karafun_flow_shadow.readback(", "karafun_flow_shadow.startflow(", '"karafun_flow_shadow": True'):
            self.assertIn(needle, src)
        self.assertEqual(src.count("import karafun_flow_shadow"), 3)


if __name__ == "__main__":
    unittest.main()
