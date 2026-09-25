"""Startup regressions without opening KaraFun or touching show data."""
import ast
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from karafun_fullscreen import renderer_raise_script, renderer_fullscreen_script


@lru_cache(maxsize=1)
def methods():
    tree = ast.parse(Path('0.2.18.1.py').read_text())
    names = {'_karafun_activate_result_for_playback', '_karafun_search_script',
             '_handoff_show_screen_to_karafun'}
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {'sys': SimpleNamespace(platform='darwin'), '_diag': lambda *a: None,
                 'renderer_raise_script': renderer_raise_script}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'startup-test', 'exec'), namespace)
    return namespace


class StartupTests(unittest.TestCase):
    def host(self, reply=(True, 'FOUND|640|320|4:20', '')):
        return SimpleNamespace(
            _active_external_karafun={'entry': {'title': 'Song', 'artist': 'Artist'}},
            _karafun_entry_artist_title=lambda e: (e['artist'], e['title']),
            _karafun_search_script=mock.Mock(return_value=['resolve']),
            _run_karafun_applescript_sync=mock.Mock(return_value=reply),
            _macos_native_double_click=mock.Mock(return_value=True),
        )

    def activate(self, host):
        return methods()['_karafun_activate_result_for_playback'](host, (100, 200))

    def test_click_uses_fresh_result_coordinates_after_window_moved(self):
        host = self.host()
        self.assertEqual(self.activate(host), (True, ''))
        host._macos_native_double_click.assert_called_once_with(640, 320)
        host._karafun_search_script.assert_called_once_with(
            query='', safe_title='Song', safe_artist='Artist',
            require_exact_title=True, resolve_only=True)
        self.assertEqual(host._active_external_karafun['entry']['karafun_result_activation_point'],
                         (640, 320))

    def test_unverified_or_invalid_result_never_clicks_saved_point(self):
        for ok, result in ((True, 'TITLE_ONLY|640|320'), (True, 'FIRST|640|320'),
                           (True, 'ERROR|missing'), (False, 'FOUND|640|320'),
                           (True, 'FOUND|nan|320'), (True, 'FOUND|640|inf')):
            with self.subTest(result=result, ok=ok):
                host = self.host((ok, result, ''))
                self.assertFalse(self.activate(host)[0])
                host._macos_native_double_click.assert_not_called()

    def test_session_replaced_during_resolution_does_not_start_old_song(self):
        host = self.host()
        def resolve(*args, **kwargs):
            host._active_external_karafun = {'entry': {'title': 'New song'}}
            return True, 'FOUND|640|320', ''
        host._run_karafun_applescript_sync.side_effect = resolve
        self.assertFalse(self.activate(host)[0])
        host._macos_native_double_click.assert_not_called()

    def test_missing_session_does_not_resolve_or_click(self):
        host = self.host()
        host._active_external_karafun = None
        self.assertFalse(self.activate(host)[0])
        host._run_karafun_applescript_sync.assert_not_called()
        host._macos_native_double_click.assert_not_called()

    def test_resolve_only_raises_results_before_reading_rows_without_new_search(self):
        host = SimpleNamespace(_karafun_applescript_literal=lambda v: '"' + v + '"')
        script = '\n'.join(methods()['_karafun_search_script'](
            host, query='', safe_title='Song', safe_artist='Artist',
            require_exact_title=True, resolve_only=True))
        self.assertLess(script.index('AXRaise'), script.index('set elems to entire contents'))
        self.assertIn('aName contains "Artist"', script)
        self.assertNotIn('keystroke', script)
        self.assertNotIn('key code', script)
        self.assertNotIn('click searchField', script)

    def test_completed_handoff_raises_renderer_without_fullscreen_toggle(self):
        host = SimpleNamespace(settings={}, _karafun_handoff_complete=True,
                               _active_external_karafun={'entry': {}},
                               _karafun_run_window_script=mock.Mock())
        methods()['_handoff_show_screen_to_karafun'](host)
        host._karafun_run_window_script.assert_called_once_with(renderer_raise_script(), timeout=5)
        script = '\n'.join(renderer_raise_script())
        self.assertLess(script.index('name of candidateWindow is "Dual Renderer"'),
                        script.index('AXRaise'))
        self.assertNotIn('AXFullScreen', script)
        self.assertNotIn('click', script)
        host._active_external_karafun = None
        host._karafun_run_window_script.reset_mock()
        methods()['_handoff_show_screen_to_karafun'](host)
        host._karafun_run_window_script.assert_not_called()

    def test_existing_fullscreen_is_raised_but_verification_does_not_steal_focus(self):
        script = '\n'.join(renderer_fullscreen_script())
        already_fullscreen = script[script.index('if exists menu item "Exit Player Full Screen"'):]
        self.assertLess(already_fullscreen.index('AXRaise'), already_fullscreen.index('return "FULLSCREEN"'))
        verification = '\n'.join(renderer_fullscreen_script(request=False))
        self.assertNotIn('AXRaise', verification)
        self.assertNotIn('set frontmost', verification)


if __name__ == '__main__':
    unittest.main()
