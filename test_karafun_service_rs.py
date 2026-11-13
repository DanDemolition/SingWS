"""W1 of the KaraFun web Rust port: the Rust service machine (rust/crates/singws-karafun-svc) must give the same decisions as the real
karafun_web.KaraFunWebService. Runs the parity tool on a small corpus; skips when the CLI is not built. Offline: no Chrome, network or Keychain."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class ServiceParity(unittest.TestCase):
    def test_parity_small_corpus(self):
        sys.path.insert(0, str(ROOT / "tools"))
        os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="kf_svc_test_home_"))
        import karafun_service_parity as p
        try:
            p.find_cli()
        except FileNotFoundError:
            self.skipTest("singws-karafun-svc not built")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "karafun_service_parity.py"), "--cases", "300", "--seed", "3"],
                           capture_output=True, text=True, env={**os.environ, "SINGWS_HOME": os.environ["SINGWS_HOME"]}, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertIn("IDENTICAL", r.stdout)

    def test_host_parity_small_corpus(self):
        os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="kf_svc_test_home_"))
        sys.path.insert(0, str(ROOT / "tools"))
        import karafun_host_parity as p
        try:
            p.find_cli()
        except FileNotFoundError:
            self.skipTest("singws-karafun-svc not built")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "karafun_host_parity.py"), "--cases", "300", "--seed", "3"],
                           capture_output=True, text=True, env={**os.environ, "SINGWS_HOME": os.environ["SINGWS_HOME"]}, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertIn("IDENTICAL", r.stdout)

    def test_matching_parity_against_the_page_script_in_node(self):
        import shutil
        if shutil.which("node") is None:
            self.skipTest("node not installed")
        sys.path.insert(0, str(ROOT / "tools"))
        import karafun_match_parity as p
        try:
            p.find_cli()
        except FileNotFoundError:
            self.skipTest("singws-karafun-svc not built")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "karafun_match_parity.py"), "--cases", "3000", "--seed", "3"], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertIn("IDENTICAL", r.stdout)

    def test_credentials_parity_uses_only_a_test_service(self):
        # the Keychain parity tool itself is run by hand (it writes and deletes items in the login Keychain); here only its safety rule is pinned
        sys.path.insert(0, str(ROOT / "tools"))
        import karafun_credentials_parity as p
        self.assertTrue(p.TEST.startswith("SingWS-TEST-"))
        self.assertNotEqual(p.TEST, p.REAL)

    def test_top100_and_frames_parity_small_corpus(self):
        os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="kf_svc_test_home_"))
        for tool in ("karafun_top100_parity.py", "karafun_frames_parity.py"):
            r = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--cases", "300", "--seed", "3"], capture_output=True, text=True, env={**os.environ}, timeout=300)
            if "not built" in (r.stdout + r.stderr):
                self.skipTest("singws-karafun-svc not built")
            self.assertEqual(r.returncode, 0, tool + r.stdout[-2000:] + r.stderr[-800:])
            self.assertIn("IDENTICAL", r.stdout)


if __name__ == "__main__":
    unittest.main()
