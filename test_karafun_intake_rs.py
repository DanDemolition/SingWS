"""The intake of the KaraFun worker (duplicate guard, key/tempo clamps, pending state): the statements are still the shape the replay harness reads, and the Rust intake equals them."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_intake_replay as fr  # noqa: E402

BIN = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")


class Segment(unittest.TestCase):
    def test_the_segment_is_found_with_exactly_the_outside_names_the_harness_provides(self):
        intake, n = fr.load()
        self.assertGreaterEqual(n, 15)

    def test_clamps_and_the_duplicate_guard(self):
        intake, _ = fr.load()
        base = {"entry": {}, "artist": "A", "title": "T", "key": 99, "tempo": 5, "request_uid": "u", "queries": ["q"]}
        out = fr.run(intake, base)["result"]
        self.assertEqual((out["requested_key"], out["requested_tempo"]), (12, 50))
        dup = dict(base, entry={"karafun_submission_state": "karafun_queued"})
        self.assertEqual(fr.run(intake, dup)["result"], {"returned": False})


@unittest.skipUnless(BIN.is_file(), "needs the built singws-karafun helper")
class WithRust(unittest.TestCase):
    def test_rust_equals_python_on_a_small_run(self):
        import karafun_intake_parity as fp
        self.assertEqual(fp.main(["--seeds", "400", "--binary", str(BIN)]), 0)


if __name__ == "__main__":
    unittest.main()
