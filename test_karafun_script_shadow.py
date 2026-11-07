"""KaraFun search-script shadow: a log-only comparison of the app's AppleScript with the Rust builder's. It must never change the script, never
raise into the app, never log the query/title/artist, and must say when the two differ."""
import _test_profile  # noqa: F401
import os
import sys
import unittest
from pathlib import Path

import karafun_script_shadow as shadow

ROOT = Path(__file__).resolve().parent
BINARY = Path(os.environ.get("SINGWS_KARAFUN_BIN") or Path.home() / ".cache/singws-rust-target-karafun/release/singws-karafun")
SOURCE = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
sys.path.insert(0, str(ROOT / "tools"))


def real_script(**kw):
    import karafun_script_parity as par
    _match, _lit, search = par.load_python()
    return search(**kw)


class ShadowTests(unittest.TestCase):
    def setUp(self):
        shadow._seen.clear()
        shadow._clean_noted = False
        self.log = []

    ARGS = dict(query='Sugarcult "Memory"', safe_title="Memory (live)", safe_artist="Sugarcult", require_exact_title=True, resolve_only=False)

    def request(self):
        return {k: v for k, v in self.ARGS.items()}

    @unittest.skipUnless(BINARY.exists(), "singws-karafun is not built")
    def test_the_real_script_is_clean_and_logged_once(self):
        lines = real_script(**self.ARGS)
        self.assertEqual(shadow.run_shadow(lines, self.request(), self.log.append, helper=BINARY), "clean")
        self.assertEqual(shadow.run_shadow(lines, self.request(), self.log.append, helper=BINARY), "clean")
        self.assertEqual(len(self.log), 1)
        self.assertIn("first check clean", self.log[0])

    @unittest.skipUnless(BINARY.exists(), "singws-karafun is not built")
    def test_a_changed_line_and_a_changed_length_are_flagged_without_content(self):
        lines = real_script(**self.ARGS)
        tampered = list(lines)
        tampered[5] = tampered[5] + " "
        self.assertEqual(shadow.run_shadow(tampered, self.request(), self.log.append, helper=BINARY), "mismatch")
        self.assertEqual(shadow.run_shadow(lines + ["extra"], self.request(), self.log.append, helper=BINARY), "mismatch")
        text = "\n".join(self.log)
        self.assertIn("MISMATCH line 5", text)
        self.assertIn("MISMATCH length", text)
        for secret in ("Sugarcult", "Memory"):
            self.assertNotIn(secret, text)
        # the same difference is not repeated
        n = len(self.log)
        shadow.run_shadow(tampered, self.request(), self.log.append, helper=BINARY)
        self.assertEqual(len(self.log), n)

    def test_it_never_changes_the_script_and_never_raises(self):
        lines = ["a", "b"]
        before = list(lines)
        self.assertEqual(shadow.run_shadow(lines, self.request(), self.log.append, helper=Path("/nonexistent/helper")), "skipped")
        self.assertEqual(lines, before)
        self.assertIsNone(shadow.start({"karafun_script_shadow": False}, self.log.append, py_lines=lines, **self.ARGS))

        def broken_log(_):
            raise RuntimeError("log failed")
        shadow.run_shadow(lines, self.request(), broken_log, helper=Path("/nonexistent/helper"))

    def test_announce_and_switch(self):
        self.assertIn("off", shadow.announce({"karafun_script_shadow": False}, self.log.append))
        text = shadow.announce({}, self.log.append)
        self.assertTrue(text.startswith("enabled available="))
        self.assertIn("[KARAFUN-SCRIPT-SHADOW]", self.log[-1])

    def test_the_app_builds_it_in_and_it_defaults_on(self):
        self.assertIn('"karafun_script_shadow": True', SOURCE)
        self.assertIn("karafun_script_shadow.start(", SOURCE)
        self.assertIn('"songend_shadow", "karafun_script_shadow", "karafun_monitor_shadow"):', SOURCE)
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = (ROOT / spec).read_text(encoding="utf-8")
            self.assertIn("karafun_script_shadow.py", text)
            self.assertIn("singws-karafun", text)
        for script in ("build_singws_mac_arm64.sh", "build_singws_mac_intel.sh"):
            self.assertIn("build_karafun.sh", (ROOT / script).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
