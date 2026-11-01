import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import shutil
import unittest
from pathlib import Path

CLI = os.environ.get("SINGWS_CONTROL_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-control-cli")
SHELL = os.environ.get("SINGWS_SHELL_BIN") or os.path.expanduser("~/.cache/singws-rust-target/debug/singws-shell")
needs = unittest.skipUnless(Path(CLI).exists() and Path(SHELL).exists() and shutil.which("php"),
                            "needs php, singws-control-cli and a built singws-shell (QMAKE=... cargo build -p singws-shell)")


@needs
class ShellControlsTests(unittest.TestCase):
    """The real Qt shell's Controls tab, driven through its own act() function, against the real PHP login/token, the relay and the app's command code."""

    def test_every_shell_scenario_passes(self):
        from tools import shell_loopback as sl

        failed = []
        for fn in sl.SCENARIOS:
            # Each scenario starts a real Qt shell that logs in and sends its commands within a fixed window (`--snapshot-after`); under a loaded machine
            # (the full suite) the window is sometimes missed and no command is seen. A scenario that fails twice in a row is a real failure.
            for attempt in (1, 2):
                try:
                    fn()
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        failed.append(f"{fn.__name__}: {exc!r}")
        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(sl.SCENARIOS), 5)


if __name__ == "__main__":
    unittest.main()
