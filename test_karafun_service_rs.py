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


if __name__ == "__main__":
    unittest.main()
