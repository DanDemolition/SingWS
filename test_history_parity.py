import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import unittest
from pathlib import Path

CLI = os.environ.get("SINGWS_HISTORY_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-history-cli")
needs_cli = unittest.skipUnless(Path(CLI).exists(), "needs singws-history-cli (cargo build --release -p singws-uimodel)")


@needs_cli
class SingerHistoryParity(unittest.TestCase):
    def test_rust_history_equals_the_apps_own_python_on_a_small_run(self):
        from tools import history_parity as hp

        hp.failures.clear()
        hp.counts.clear()
        self.assertEqual(hp.main(["--cases", "6", "--seed", "5", "--skip-casefold", "--no-live"]), 0, hp.failures[:3])
        self.assertGreater(hp.counts.get("fuzz song views", 0), 0)
        self.assertGreater(hp.counts.get("time labels", 0), 0)

    def test_casefold_agrees_with_python_for_the_awkward_characters(self):
        from tools import history_parity as hp

        hp.failures.clear()
        for t in ["Straße", "ΑΣ ς", "İ", "ǅ", "ﬃ", "ᾈ", "ẞ", "ſ", "K", "Å"]:
            got = hp.rust({"op": "casefold", "texts": [t]})["result"][0]
            self.assertEqual(got, t.casefold(), t)


if __name__ == "__main__":
    unittest.main()
