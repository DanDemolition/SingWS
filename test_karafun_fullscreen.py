import unittest
import threading
from unittest.mock import patch

from karafun_fullscreen import ensure_renderer_fullscreen


class Host:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.scripts = []
        self.clicks = []

    def _karafun_run_window_script(self, lines, on_complete, **kwargs):
        self.scripts.append(lines)
        on_complete(next(self.replies))
        return True

    def _macos_native_double_click(self, *point):
        self.clicks.append(point)
        return True

    def _run_on_ui_thread(self, callback):
        callback()


class InlineThread(threading.Thread):
    def __init__(self, group=None, target=None, **kwargs):
        super().__init__(group=group, target=target, **kwargs)

    def start(self):
        self.run()


class InlineTimer:
    def __init__(self, _interval, function, **_kwargs):
        self.function = function
        self.daemon = False

    def start(self):
        self.function()


class FullscreenTests(unittest.TestCase):
    @patch("threading.Thread", InlineThread)
    def test_windowed_uses_current_renderer_bounds_once_then_only_verifies(self):
        host = Host(["WINDOWED", "CLICK|-500|400", "FULLSCREEN"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(host.clicks, [(-500, 400)])
        self.assertEqual(results, ["FULLSCREEN"])
        self.assertNotIn('set value of attribute "AXFullScreen"', "\n".join(host.scripts[-1]))

    def test_no_click_if_fullscreen_or_missing_or_attribute_unknown(self):
        for state in ("FULLSCREEN", "NO_DUAL_RENDERER", "AX_STATE_ERROR|-25205"):
            host = Host([state])
            results = []
            ensure_renderer_fullscreen(host, results.append, lambda: True)
            self.assertEqual(host.clicks, [])
            self.assertEqual(results, [state])

    def test_no_toggle_if_renderer_enters_fullscreen_before_fallback(self):
        host = Host(["WINDOWED", "FULLSCREEN"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(host.clicks, [])
        self.assertEqual(results, ["FULLSCREEN"])

    def test_stale_song_cannot_start_automation(self):
        host = Host([])
        ensure_renderer_fullscreen(host, self.fail, lambda: False)
        self.assertEqual(host.scripts, [])

    @patch("threading.Timer", InlineTimer)
    @patch("threading.Thread", InlineThread)
    def test_failed_verification_does_not_claim_success_or_click_again(self):
        host = Host(["WINDOWED", "CLICK|400|300", "WINDOWED", "WINDOWED"])
        results = []
        ensure_renderer_fullscreen(host, results.append, lambda: True)
        self.assertEqual(results, ["WINDOWED"])
        self.assertEqual(len(host.clicks), 1)
