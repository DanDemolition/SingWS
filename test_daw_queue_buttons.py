"""The DAW page's singer row offers to-top / to-bottom as well as one step up / down (2026-11-20: moving a singer across a 15-singer rotation one place at a time was too slow).
The app already accepts direction top/bottom for queue.move_singer (differential test in test_shell_queue_commands.py)."""
import _test_profile  # noqa: F401
import re
import subprocess
import unittest
from pathlib import Path

JS = Path("SingWS-Server/v2/assets/daw.js")


class DawQueueButtonTests(unittest.TestCase):
    def test_every_direction_the_page_sends_is_one_the_app_accepts(self):
        src = JS.read_text(encoding="utf-8")
        sent = set(re.findall(r"dispatchQueue\('queue\.move_singer',\{singer_id:id,direction:'(\w+)'\}\)", src))
        self.assertEqual(sent, {"up", "down", "top", "bottom"})
        app = Path("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('where not in ("up", "down", "top", "bottom")', app)

    def test_the_top_and_bottom_buttons_are_small_and_labelled(self):
        src = JS.read_text(encoding="utf-8")
        self.assertIn("'Move '+s.name+' to the top'", src)
        self.assertIn("'Move '+s.name+' to the bottom'", src)
        self.assertRegex(src, r"direction:'top'\}\),'q-small'\)")
        self.assertRegex(src, r"direction:'bottom'\}\),'q-small'\)")

    def test_the_script_still_parses(self):
        r = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
