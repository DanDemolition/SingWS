import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SHELL = os.environ.get("SINGWS_SHELL_BIN") or os.path.expanduser("~/.cache/singws-rust-target/debug/singws-shell")
needs = unittest.skipUnless(Path(SHELL).exists(), "needs a built singws-shell (QMAKE=... cargo build -p singws-shell)")


@needs
class MusicAndSettingsPanelsTests(unittest.TestCase):
    """The real Qt shell (offscreen) opened on the Music and Settings tabs over a throwaway profile: each must start, draw, and quit by itself."""

    def run_tab(self, tab, home, extra=()):
        shot = Path(home) / f"{tab}.png"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SINGWS_HOME=str(home), SINGWS_SHELL_CONFIG_DIR=str(Path(home) / "cfg"))
        p = subprocess.run([SHELL, "--tab", tab, "--snapshot", str(shot), "--snapshot-after", "2000", *extra], env=env, capture_output=True, text=True, timeout=90)
        return p, shot

    def make_home(self):
        d = tempfile.mkdtemp(prefix="shell_panels_")
        (Path(d) / "bg_playlist.json").write_text(json.dumps([{"path": "/m/a.mp3", "filename": "Adele - Hello.mp3", "title": "x"}, {"path": "/m/b.mp3", "filename": "Zara - Lush.flac", "title": "y"}]))
        (Path(d) / "settings.json").write_text(json.dumps({"bg_enabled": True, "bg_volume": 0.5, "api_key": "sk-SECRET-VALUE", "host_controls_pin": "9876"}))
        return d

    def test_the_music_tab_draws(self):
        d = self.make_home()
        p, shot = self.run_tab("music", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)

    def test_the_music_tab_copes_with_a_broken_and_a_missing_playlist(self):
        d = self.make_home()
        (Path(d) / "bg_playlist.json").write_text('[{"path": "/m/a.mp3"}]')  # no filename: the app itself fails to load this
        p, shot = self.run_tab("music", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        (Path(d) / "bg_playlist.json").unlink()
        p, shot = self.run_tab("music", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)

    def test_the_settings_tab_draws_and_the_process_never_prints_a_secret(self):
        d = self.make_home()
        p, shot = self.run_tab("settings", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)
        for leak in ("sk-SECRET-VALUE", "9876"):
            self.assertNotIn(leak, p.stdout + p.stderr)

    def test_the_numeric_tab_arguments_still_work(self):
        d = self.make_home()
        p, shot = self.run_tab("4", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])


if __name__ == "__main__":
    unittest.main()
