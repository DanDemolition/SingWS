"""The show-screen preview is one picture shared by the DAW page and singers' phones. When ONLY singers are watching the app
sends a lighter stream (grab every 3 s, 320x180, lower JPEG quality); a DAW page, or a server that does not say who is watching,
keeps the full-rate stream exactly as before (min 0.25 s while playing, 1 s timer, 426x240 at quality 28)."""
import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
WANTED = ("_daw_snapshot_singers_only", "_daw_snapshot_min_interval_sec", "_daw_snapshot_viewer_recent", "_daw_snapshot_timer_target_ms")


def host(audience="", playing=True, viewer_recent=True):
    tree = ast.parse(SOURCE)
    methods = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in WANTED:
                    sub.decorator_list = []
                    methods.append(sub)
    ns = {"time": time}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "daw-audience", "exec"), ns)
    obj = SimpleNamespace(
        karaoke_playing=playing, _is_karaoke_paused=lambda: False, _daw_snapshot_audience=audience,
        _daw_snapshot_viewer_recent_until=time.monotonic() + 10 if viewer_recent else 0.0,
        _daw_singer_screen_preview_enabled=lambda: True, _app_closing=False, _daw_preview_server_backoff_until=0.0)
    for name in WANTED:
        setattr(obj, name, (lambda f: (lambda *a, **k: f(obj, *a, **k)))(ns[name]))
    return obj


class DawPreviewAudienceTests(unittest.TestCase):
    def test_a_daw_page_keeps_the_full_rate_stream(self):
        for audience in ("daw", ""):
            h = host(audience)
            self.assertEqual(h._daw_snapshot_min_interval_sec(), 0.25)
            self.assertEqual(h._daw_snapshot_timer_target_ms(), 1000)

    def test_singers_alone_get_a_frame_every_three_seconds(self):
        h = host("singer")
        self.assertTrue(h._daw_snapshot_singers_only())
        self.assertEqual(h._daw_snapshot_min_interval_sec(), 3.0)
        self.assertEqual(h._daw_snapshot_timer_target_ms(), 3000)
        self.assertEqual(host("singer", playing=False)._daw_snapshot_min_interval_sec(), 3.0)

    def test_with_nobody_watching_the_slow_poll_for_a_new_viewer_is_unchanged(self):
        self.assertEqual(host("", viewer_recent=False)._daw_snapshot_timer_target_ms(), 5000)
        self.assertEqual(host("singer", viewer_recent=False)._daw_snapshot_timer_target_ms(), 5000)

    def test_the_viewer_check_chooses_singer_only_when_no_daw_viewer_is_recent_and_old_servers_stay_full_rate(self):
        self.assertIn('"daw_viewer_seen_at" in (payload or {}) or "singer_viewer_seen_at" in (payload or {})', SOURCE)
        self.assertIn('if singer_recent and not daw_recent:\n                                audience = "singer"', SOURCE)
        # default is the full stream: a server that reports no kinds never produces "singer"
        self.assertIn('audience = ""\n                    if viewer_recent:\n                        audience = "daw"', SOURCE)

    def test_the_singer_frame_is_smaller_and_more_compressed_the_daw_frame_is_unchanged(self):
        self.assertIn("320 if singers_only else 426", SOURCE)
        self.assertIn("180 if singers_only else 240", SOURCE)
        self.assertIn('scaled.save(buf, "JPEG", 22 if singers_only else 28)', SOURCE)


if __name__ == "__main__":
    unittest.main()
