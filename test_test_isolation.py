"""Ratchet: a test that loads the app must never touch the operator's live ~/SingWS profile.

Running ``python -m unittest test_x`` directly (as the migration handoff does) bypasses tools/run_tests.sh, so each such test
module has to isolate SINGWS_HOME itself, either with its own mkdtemp or by importing ``_test_profile`` first. Before this
ratchet ~50 modules wrote to the live show log and rewrote live state files (loudness cache, host request sync, ...).
"""
import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_LOADS_APP = re.compile(r"spec_from_file_location|exec_module|subprocess")


class IsolationRatchet(unittest.TestCase):
    def test_every_test_that_loads_the_app_isolates_the_profile(self):
        offenders = []
        for path in sorted(glob.glob(str(ROOT / "test_*.py"))):
            name = os.path.basename(path)
            if name == Path(__file__).name:
                continue
            src = Path(path).read_text(encoding="utf-8", errors="ignore")
            if "0.2.18.1.py" in src and _LOADS_APP.search(src) and "SINGWS_HOME" not in src and "_test_profile" not in src:
                offenders.append(name)
        self.assertEqual(offenders, [], "these tests load the app without isolating SINGWS_HOME; add "
                                        "`import _test_profile` before the app is loaded: " + ", ".join(offenders))


class HelperTests(unittest.TestCase):
    def run_helper(self, env_home):
        env = {k: v for k, v in os.environ.items() if k != "SINGWS_HOME"}
        if env_home is not None:
            env["SINGWS_HOME"] = env_home
        code = "import os, _test_profile; print(os.environ.get('SINGWS_HOME', ''))"
        out = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); {code}"],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_unset_home_gets_a_private_directory_that_is_removed_at_exit(self):
        home = self.run_helper(None)
        self.assertTrue(home and Path(home).name.startswith("singws-test-home-"))
        self.assertNotEqual(Path(home), Path.home() / "SingWS")
        self.assertFalse(Path(home).exists())

    def test_an_explicit_home_is_respected_and_kept(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self.run_helper(td), td)
            self.assertTrue(Path(td).exists())


if __name__ == "__main__":
    unittest.main()
