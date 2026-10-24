import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import shutil
import unittest
from pathlib import Path

CLI = os.environ.get("SINGWS_CONTROL_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-control-cli")
needs = unittest.skipUnless(Path(CLI).exists() and shutil.which("php"), "needs php and singws-control-cli (cargo build --release -p singws-control)")


@needs
class ControlLoopbackTests(unittest.TestCase):
    """Real PHP login and token endpoints + the reference relay + the app's real command code, against the Rust client (127.0.0.1 only)."""

    def test_every_scenario_passes(self):
        from tools import control_loopback as cl

        failed = []
        for fn in cl.SCENARIOS:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{fn.__name__}: {exc!r}")
        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(cl.SCENARIOS), 11)


if __name__ == "__main__":
    unittest.main()
