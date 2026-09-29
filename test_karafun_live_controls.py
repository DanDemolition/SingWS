"""Live SingWS -> KaraFun key/tempo control and read-back. No app import."""
import ast
import re
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


@lru_cache(maxsize=1)
def methods():
    tree = ast.parse(Path('0.2.18.1.py').read_text())
    names = {'_karafun_apply_key_tempo', '_karafun_read_key_tempo'}
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {'re': re, '_diag': lambda *a: None}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'live-controls-test', 'exec'), namespace)
    return namespace


def host(reply):
    return SimpleNamespace(_run_karafun_applescript_sync=mock.Mock(return_value=reply))


def script_text(h):
    return "\n".join(h._run_karafun_applescript_sync.call_args.args[0])


class ApplyTests(unittest.TestCase):
    def apply(self, h, entry, key, tempo):
        return methods()['_karafun_apply_key_tempo'](h, entry, key, tempo)

    def test_no_change_clicks_nothing(self):
        h = host((True, '', ''))
        entry = {'karafun_key_current': 2, 'karafun_tempo_current': 105}
        self.assertEqual(self.apply(h, entry, 2, 105), (2, 105))
        h._run_karafun_applescript_sync.assert_not_called()

    def test_up_uses_key_up_and_tempo_up_step_counts(self):
        h = host((True, 'ADJUSTED|2|5', ''))
        entry = {}
        self.assertEqual(self.apply(h, entry, 2, 105), (2, 105))
        text = script_text(h)
        self.assertIn('repeat 2 times', text)
        self.assertIn('repeat 5 times', text)
        self.assertIn('"Key Up"', text)
        self.assertIn('"Tempo Up"', text)
        self.assertEqual((entry['karafun_key_current'], entry['karafun_tempo_current']), (2, 105))

    def test_down_uses_key_down_and_tempo_down(self):
        h = host((True, 'ADJUSTED|3|10', ''))
        entry = {}
        self.assertEqual(self.apply(h, entry, -3, 90), (-3, 90))
        text = script_text(h)
        self.assertIn('"Key Down"', text)
        self.assertIn('"Tempo Down"', text)

    def test_delta_is_relative_to_what_karafun_already_has(self):
        h = host((True, 'ADJUSTED|1|0', ''))
        entry = {'karafun_key_current': 2, 'karafun_tempo_current': 100}
        self.assertEqual(self.apply(h, entry, 3, 100), (3, 100))
        self.assertIn('repeat 1 times', script_text(h))

    def test_stops_where_karafun_stopped(self):
        h = host((True, 'ADJUSTED|1|5', ''))
        entry = {}
        self.assertEqual(self.apply(h, entry, 3, 105), (1, 105))
        self.assertEqual(entry['karafun_key_current'], 1)

    def test_failed_script_leaves_known_values_alone(self):
        h = host((False, '', 'boom'))
        entry = {'karafun_key_current': 1, 'karafun_tempo_current': 100}
        self.assertEqual(self.apply(h, entry, 4, 100), (1, 100))


class ReadTests(unittest.TestCase):
    def read(self, reply):
        return methods()['_karafun_read_key_tempo'](host(reply))

    def test_parses_signed_values(self):
        self.assertEqual(self.read((True, 'KEY=+2 TEMPO=+5%', '')), (2, 105))
        self.assertEqual(self.read((True, 'KEY=-3 TEMPO=0%', '')), (-3, 100))
        self.assertEqual(self.read((True, 'KEY=0 TEMPO=-10%', '')), (0, 90))

    def test_unreadable_result_is_none(self):
        self.assertIsNone(self.read((True, '', '')))
        self.assertIsNone(self.read((True, 'KEY= TEMPO=', '')))
        self.assertIsNone(self.read((False, '', 'error')))


if __name__ == '__main__':
    unittest.main()
