"""On-screen KaraFun timer: duration, count-up, pause freeze. No app import."""
import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


@lru_cache(maxsize=1)
def display_times():
    tree = ast.parse(Path('0.2.18.1.py').read_text())
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == '_karafun_display_times')
    namespace = {'time': __import__('time')}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'display-time-test', 'exec'), namespace)
    return namespace['_karafun_display_times']


def host(entry):
    return SimpleNamespace(_active_external_karafun={'entry': entry})


class DisplayTimeTests(unittest.TestCase):
    def test_no_active_song_returns_none(self):
        self.assertIsNone(display_times()(SimpleNamespace(_active_external_karafun=None)))

    def test_shows_duration_before_playback_starts(self):
        self.assertEqual(display_times()(host({'duration': 130})), (0.0, 130.0))

    def test_counts_from_playback_start(self):
        with mock.patch('time.monotonic', return_value=1050.0):
            elapsed, duration = display_times()(host(
                {'duration': 130, 'karafun_display_started_at': 1000.0}))
        self.assertEqual((elapsed, duration), (50.0, 130.0))

    def test_clock_duration_overrides_estimate(self):
        with mock.patch('time.monotonic', return_value=1010.0):
            _elapsed, duration = display_times()(host(
                {'duration': 240, 'karafun_display_duration': 130,
                 'karafun_display_started_at': 1000.0}))
        self.assertEqual(duration, 130.0)

    def test_elapsed_is_frozen_while_paused(self):
        entry = {'duration': 130, 'karafun_display_started_at': 1000.0,
                 'karafun_paused_at': 1040.0}
        with mock.patch('time.monotonic', return_value=1500.0):
            self.assertEqual(display_times()(host(entry))[0], 40.0)

    def test_never_exceeds_duration(self):
        with mock.patch('time.monotonic', return_value=5000.0):
            self.assertEqual(display_times()(host(
                {'duration': 130, 'karafun_display_started_at': 1000.0}))[0], 130.0)

    def test_no_duration_returns_none(self):
        self.assertIsNone(display_times()(host({'duration': 0})))


if __name__ == '__main__':
    unittest.main()
