import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHELL = os.environ.get("SINGWS_SHELL_BIN") or os.path.expanduser("~/.cache/singws-rust-target/debug/singws-shell")
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target")
needs_shell = unittest.skipUnless(Path(SHELL).exists(), "needs a built singws-shell (QMAKE=... cargo build -p singws-shell)")
needs_cli = unittest.skipUnless(all((Path(TARGET) / "release" / n).exists() for n in ("singws-idle-overlay-cli", "singws-window-cli", "singws-cdg-dump")),
                                "needs release builds of the singws-cdg tools (cargo build --release -p singws-cdg)")

TWO = {"screens": [{"x": 0, "y": 0, "w": 1440, "h": 900, "ax": 0, "ay": 25, "aw": 1440, "ah": 875},
                   {"x": 1440, "y": 0, "w": 1920, "h": 1080, "ax": 1440, "ay": 0, "aw": 1920, "ah": 1080}], "primary": 0, "cursor_screen": 1}


def run_shell(args, home, timeout=90):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SINGWS_HOME=str(home), SINGWS_SHELL_CONFIG_DIR=str(Path(home) / "cfg"))
    return subprocess.run([SHELL, *args], env=env, capture_output=True, text=True, timeout=timeout)


@needs_shell
class DisplayWindowShellTests(unittest.TestCase):
    """The Rust karaoke display demo window (offscreen): the idle card, the audience-window placement rules, the screen-change debounce."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="display_window_")

    def test_the_idle_screen_draws_the_now_playing_card(self):
        from PyQt6.QtGui import QImage
        shot = Path(self.home) / "idle.png"
        p = run_shell(["--display-idle", "--display-idle-track", "/music/Queen - Bohemian Rhapsody (Remastered 2011).mp3", "--display-ticker", "1. Alex",
                       "--display-snapshot", str(shot), "--display-snapshot-after", "1500"], self.home)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        img = QImage(str(shot))
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), (1280, 720))
        # the window is 1280x720 with a ticker strip of 61 px: the picture area is 659 high, the card is 22 percent of it at the bottom left
        card_y = 659 - int(659 * 0.22) - 6
        inside = img.pixelColor(30, card_y + 8)
        self.assertGreater(inside.blue(), inside.red(), "the card is the dark violet (8, 6, 20)")
        self.assertLess(inside.red(), 40)
        self.assertGreater(inside.blue(), 5)
        self.assertEqual(img.pixelColor(900, 300).getRgb()[:3], (0, 0, 0), "the idle screen is black without a background image")
        # some bright text pixel exists in the card's text lane (the title is #E6E8F0)
        found = any(img.pixelColor(x, y).red() > 200 for x in range(60, 400, 2) for y in range(card_y + 14, card_y + 50))
        self.assertTrue(found, "title text is drawn in the card")

    def test_a_silent_background_player_draws_no_card(self):
        from PyQt6.QtGui import QImage
        shot = Path(self.home) / "silent.png"
        p = run_shell(["--display-idle", "--display-idle-silent", "--display-idle-track", "A - B.mp3", "--display-snapshot", str(shot), "--display-snapshot-after", "1200"], self.home)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        img = QImage(str(shot))
        self.assertEqual(img.pixelColor(30, 560).getRgb()[:3], (0, 0, 0))

    def decide(self, extra, topology=TWO):
        f = Path(self.home) / "screens.json"
        f.write_text(json.dumps(topology))
        p = run_shell(["--display-where", "--display-screens", str(f), *extra], self.home)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        lines = [l for l in (p.stdout + p.stderr).splitlines() if "[WHERE] decision" in l]
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        return json.loads(lines[0].split("decision", 1)[1])

    def test_first_run_opens_bottom_right_on_the_screen_under_the_cursor(self):
        d = self.decide(["--display-saved", "none"])
        self.assertEqual(d["resize"], [480, 360])
        self.assertEqual(d["move"], [1440 + 1920 - 480 - 24, 1080 - 360 - 24])
        self.assertTrue(d["show"])

    def test_a_saved_position_on_a_missing_screen_comes_back_onto_the_primary(self):
        one = {"screens": [TWO["screens"][0]], "primary": 0, "cursor_screen": 0}
        saved = json.dumps({"x": 1500, "y": 40, "width": 800, "height": 600})
        d = self.decide(["--display-saved", saved], one)
        self.assertEqual(d["geometry"], {"x": 320, "y": 25 + 137, "w": 800, "h": 600})
        d = self.decide(["--display-saved", saved])
        self.assertEqual(d["geometry"], {"x": 1500, "y": 40, "w": 800, "h": 600})

    def test_saved_position_is_read_from_settings_json(self):
        (Path(self.home) / "settings.json").write_text(json.dumps({"karaoke_window_pos": {"x": 0, "y": 0, "width": 640, "height": 480}}))
        d = self.decide([])
        self.assertEqual(d["geometry"], {"x": 100, "y": 100, "w": 640, "h": 480}, "the app turns a saved 0 into 100")

    def test_screen_changes_are_debounced_into_one_refresh(self):
        f = Path(self.home) / "screens.json"
        f.write_text(json.dumps(TWO))
        p = run_shell(["--display-idle", "--display-place", "--display-screens", str(f), "--display-saved", "none", "--display-simulate-screen-events", "4,150"], self.home)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        out = p.stdout + p.stderr
        self.assertEqual(out.count("simulated event"), 4)
        settled = re.findall(r"settled (\d+) ms after the last event", out)
        self.assertEqual(len(settled), 1, out[-600:])
        self.assertTrue(580 <= int(settled[0]) <= 900, settled)


@needs_cli
class DisplayParityToolsTests(unittest.TestCase):
    """The parity tools against the app's own Python (quick settings): they need PyQt6 and the release builds of the singws-cdg tools."""

    def run_tool(self, name, *args):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CDG_PARITY_QUICK="1", CARGO_TARGET_DIR=TARGET)
        return subprocess.run([sys.executable, str(HERE / "tools" / name), *args], env=env, capture_output=True, text=True, timeout=600)

    def test_idle_overlay_matches_the_apps_card(self):
        p = self.run_tool("idle_overlay_parity.py", "--quick")
        self.assertEqual(p.returncode, 0, p.stdout[-800:] + p.stderr[-400:])
        self.assertIn(" 0 differences", p.stdout)

    def test_window_rules_match_the_apps_placement(self):
        p = self.run_tool("window_rules_parity.py")
        self.assertEqual(p.returncode, 0, p.stdout[-800:] + p.stderr[-400:])
        self.assertIn(" 0 differences", p.stdout)

    def test_partial_alpha_is_blended_over_the_grey_squares_libmpv_draws(self):
        """tools/make_alpha_cdg.py's colour 3 (alpha 63) on a dark square is (176, 176, 230) in the real bridge window (measured on screen, plus or minus libmpv's dither of 1);
        the Rust composite must say (175, 175, 230), and an alpha-255 pixel must stay as it is."""
        with tempfile.TemporaryDirectory() as d:
            cdg = Path(d) / "alpha.cdg"
            subprocess.run([sys.executable, str(HERE / "tools" / "make_alpha_cdg.py"), str(cdg)], check=True)
            p = subprocess.run([str(Path(TARGET) / "release" / "singws-cdg-dump"), str(cdg), "--at", "5", "--bridge", "900x648:off"], capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(p.stdout), 900 * 648 * 4, "900x648 is exactly 3 times the 300x216 picture")
        px = lambda x, y: tuple(p.stdout[(y * 900 + x) * 4:][:4])
        self.assertEqual(px(295, 271), (175, 175, 230, 255), "the window shows 176 because libmpv dithers by 1")
        near_transparent = px(3 * 140 + 1, 3 * 90 + 1)  # colour 4, alpha 3: the plain square
        self.assertTrue(all(215 <= c <= 240 for c in near_transparent[:3]), near_transparent)
        self.assertEqual(px(1, 1), (0, 0, 0, 255), "the border colour 0 is opaque black")


if __name__ == "__main__":
    unittest.main()
