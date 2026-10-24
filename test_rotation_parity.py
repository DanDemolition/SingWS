import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import unittest
from pathlib import Path

CLI = os.environ.get("SINGWS_ROTATION_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-rotation-cli")
needs_cli = unittest.skipUnless(Path(CLI).exists(), "needs singws-rotation-cli (cargo build --release -p singws-uimodel)")


@needs_cli
class RotationViewParity(unittest.TestCase):
    def test_rust_rotation_equals_the_apps_update_rotation_on_a_small_run(self):
        from tools import rotation_parity as rp

        rp.failures.clear()
        rp.counts.clear()
        self.assertEqual(rp.main(["--cases", "25", "--seed", "9"]), 0, rp.failures[:3])
        self.assertGreater(rp.counts.get("views", 0), 0)

    def test_the_audience_view_never_gets_an_artist_column_in_the_app(self):
        """Documents a latent PyQt bug the port reproduces: RotationView lacks _queue_entry_artist_title, so the call is swallowed."""
        import ast

        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        rotation_view = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RotationView")
        methods = {n.name for n in rotation_view.body if isinstance(n, ast.FunctionDef)}
        self.assertNotIn("_queue_entry_artist_title", methods)
        self.assertIn("self._queue_entry_artist_title(first_active_song)", src)


if __name__ == "__main__":
    unittest.main()
