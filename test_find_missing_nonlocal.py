"""tools/find_missing_nonlocal.py: flags the missing-nonlocal bug shape (the 2026-11-06 KaraFun monitor bug), not the correct versions; and the app has no candidates."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tools"))
import find_missing_nonlocal as fm  # noqa: E402


def scan_source(src):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
    try:
        return fm.scan(f.name)
    finally:
        Path(f.name).unlink()


class Detector(unittest.TestCase):
    def test_the_bug_shape_is_flagged(self):
        rows = scan_source("def outer():\n    origin = None\n    def confirm():\n        origin = 5\n    confirm()\n    return origin\n")
        self.assertEqual([(r[1], r[2], r[3]) for r in rows], [("outer", "confirm", "origin")])

    def test_nonlocal_a_parameter_and_a_rebinding_are_not_flagged(self):
        self.assertEqual(scan_source("def o():\n    x = None\n    def f():\n        nonlocal x\n        x = 1\n    f()\n    return x\n"), [])
        self.assertEqual(scan_source("def o():\n    x = None\n    def f(x):\n        x = 1\n    f(2)\n    return x\n"), [])
        self.assertEqual(scan_source("def o():\n    x = None\n    def f():\n        x = 1\n    f()\n    x = 3\n    return x\n"), [])
        self.assertEqual(scan_source("def o(items):\n    k = 0\n    def f(p):\n        k = p\n    for k in items:\n        pass\n    return k\n"), [])

    def test_the_app_modules_have_no_candidates(self):
        files = [str(p) for p in sorted(ROOT.glob("*.py")) if not p.name.startswith("test_") and p.name not in ("conftest.py", "_test_profile.py")]
        found = [(Path(f).name, r) for f in files for r in fm.scan(f)]
        self.assertEqual(found, [], "a nested function assigns a name its enclosing function reads later, without nonlocal")


if __name__ == "__main__":
    unittest.main()
