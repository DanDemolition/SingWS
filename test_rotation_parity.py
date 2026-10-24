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

    def test_the_rotation_view_has_the_artist_title_splitter_it_calls(self):
        """Regression: RotationView called self._queue_entry_artist_title but only the main window defined it, so every split silently failed."""
        import ast

        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        rotation_view = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RotationView")
        methods = {n.name for n in rotation_view.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("_queue_entry_artist_title", methods)
        self.assertIn("self._queue_entry_artist_title(first_active_song)", src)

    def test_the_visible_now_singing_bar_still_names_artist_and_title(self):
        import types

        from tools import history_parity as hp
        from tools import rotation_parity as rp

        view = rp.py_view([{"name": "Dana", "singer_id": "d", "skipped": False, "songs": [
            {"song_info": "/x.zip", "artist": "Garth Brooks", "title": "Friends In Low Places", "skipped": False}]}],
            [], "Dana", False, "", "", str(Path(hp._HOME)))
        self.assertEqual(view["now_strip"], "Dana - Garth Brooks • Friends In Low Places")
        self.assertEqual((view["rows"][0]["artist"], view["rows"][0]["song"]), ("Garth Brooks", "Friends In Low Places"))
        del types


if __name__ == "__main__":
    unittest.main()
