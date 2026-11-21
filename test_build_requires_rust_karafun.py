"""Since 1.0.4.0 the Rust KaraFun program is the default engine: both installer builds must STOP when it (or the display program) cannot be built, not print a warning and
ship an installer whose KaraFun silently falls back to the Python host."""
import _test_profile  # noqa: F401
import re
import subprocess
import unittest
from pathlib import Path


class BuildRequiresRustKarafunTests(unittest.TestCase):
    def test_both_builds_abort_when_the_svc_or_the_display_cannot_be_built(self):
        for script in ("build_singws_mac_arm64.sh", "build_singws_mac_intel.sh"):
            text = Path(script).read_text(encoding="utf-8")
            for helper in ("build_karafun_svc.sh", "build_karafun_display.sh"):
                line = next(l for l in text.splitlines() if f"./rust/{helper}" in l)
                self.assertRegex(line, r"\|\|\s*\{.*exit 1", f"{script}: {helper} must abort the build")
                self.assertNotIn("will use the Python host", line, script)

    def test_the_scripts_still_parse(self):
        for script in ("build_singws_mac_arm64.sh", "build_singws_mac_intel.sh"):
            r = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_failing_helper_really_stops_the_line(self):
        # the same construct, run for real: a failing command followed by the abort must not reach the next line
        r = subprocess.run(["bash", "-c", 'false || { echo stop; exit 1; }; echo reached'], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "stop"))


if __name__ == "__main__":
    unittest.main()
