"""Exercise the optional reveal and restore callbacks without real macOS windows."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from karafun_fullscreen import renderer_windowed_script


class WindowedRevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('0.2.18.1.py').read_text())
        names = {'_handoff_show_screen_to_karafun', '_restore_show_screen_from_karafun'}
        cls.code = compile(ast.Module(body=[n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), 'reveal-test', 'exec')

    def setUp(self):
        self.timers = []
        rect = SimpleNamespace(x=lambda: -1920, y=lambda: 0, width=lambda: 1920,
                               height=lambda: 1080, center=lambda: None)
        screen = SimpleNamespace(geometry=lambda: rect)
        self.window = mock.Mock()
        self.window.frameGeometry.return_value = rect
        self.window.windowOpacity.return_value = 0.9
        self.window.isFullScreen.return_value = True
        self.window.isMaximized.return_value = False
        self.window.isVisible.return_value = True
        self.native = mock.Mock()
        self.native.styleMask.return_value = 17
        self.native.collectionBehavior.return_value = 8
        self.native.level.return_value = 2
        self.native.ignoresMouseEvents.return_value = False
        self.host = SimpleNamespace(
            settings={'karafun_transparent_handoff': True, 'karafun_windowed_reveal': True},
            video_window=self.window, _active_external_karafun={'entry': {}},
            _karafun_transparent_renderer_ready=True,
            _karafun_auxiliary_show_screen=True,
            _karafun_run_window_script=mock.Mock(),
            _activate_host_window_after_karafun=mock.Mock(),
        )
        self.host._karafun_run_window_script.side_effect = lambda lines, on_complete, **kw: (
            on_complete('WINDOWED_READY') or True)
        self.ns = {
            'sys': SimpleNamespace(platform='darwin'), 'uuid': uuid,
            'QApplication': SimpleNamespace(screenAt=lambda _: screen, processEvents=lambda: None),
            'QWidget': SimpleNamespace(winId=lambda _: 1),
            'Qt': SimpleNamespace(WindowState=SimpleNamespace(WindowNoState=0)),
            'QTimer': SimpleNamespace(singleShot=lambda ms, fn: self.timers.append(fn)),
            '_diag': lambda *a: None, 'renderer_windowed_script': renderer_windowed_script,
            'ensure_renderer_fullscreen': mock.Mock(),
        }
        exec(self.code, self.ns)
        self.modules = mock.patch.dict('sys.modules', {
            'objc': SimpleNamespace(objc_object=lambda **kw: SimpleNamespace(window=lambda: self.native)),
            'AppKit': SimpleNamespace(NSFloatingWindowLevel=3,
                NSWindowCollectionBehaviorCanJoinAllSpaces=1,
                NSWindowCollectionBehaviorFullScreenAuxiliary=2,
                NSWindowCollectionBehaviorFullScreenPrimary=8, NSWindowStyleMaskBorderless=0),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def drain(self):
        while self.timers:
            self.timers.pop(0)()

    def handoff(self):
        self.ns['_handoff_show_screen_to_karafun'](self.host)
        self.drain()

    def test_reveal_only_after_verified_placement_with_click_through(self):
        self.handoff()
        self.window.setWindowOpacity.assert_called_once_with(0.0)
        self.native.setIgnoresMouseEvents_.assert_called_once_with(True)
        self.assertTrue(self.host._karafun_handoff_complete)
        self.ns['ensure_renderer_fullscreen'].assert_not_called()
        script = '\n'.join(self.host._karafun_run_window_script.call_args.args[0])
        self.assertIn('set position of outputWindow to {-1920, 0}', script)

    def test_failed_placement_stays_opaque_and_restores_mouse_handling(self):
        for reply in ('', 'WINDOWED_PLACEMENT_FAILED', 'NO_DUAL_RENDERER', 'FULLSCREEN'):
            with self.subTest(reply=reply):
                self.host._karafun_handoff_complete = False
                self.host._karafun_handoff_in_progress = False
                self.native.reset_mock()
                self.window.reset_mock()
                self.host._karafun_run_window_script.side_effect = lambda lines, on_complete, **kw: (
                    on_complete(reply) or True)
                self.handoff()
                self.window.setWindowOpacity.assert_not_called()
                self.native.setIgnoresMouseEvents_.assert_called_with(False)
                self.assertFalse(self.host._karafun_handoff_complete)

    def test_failed_script_start_does_not_leave_click_through(self):
        self.host._karafun_run_window_script.side_effect = None
        self.host._karafun_run_window_script.return_value = False
        self.handoff()
        self.window.setWindowOpacity.assert_not_called()
        self.native.setIgnoresMouseEvents_.assert_called_with(False)
        self.assertFalse(self.host._karafun_handoff_in_progress)

    def test_stale_reveal_callback_cannot_hide_next_song(self):
        callbacks = []
        self.host._karafun_run_window_script.side_effect = lambda lines, on_complete, **kw: (
            callbacks.append(on_complete) or True)
        self.handoff()
        self.host._karafun_handoff_token = 'new-session'
        callbacks[0]('WINDOWED_READY')
        self.window.setWindowOpacity.assert_not_called()

    def test_restore_recovers_opacity_native_state_and_saved_mouse_behavior(self):
        self.native.ignoresMouseEvents.return_value = True
        self.handoff()
        self.host._active_external_karafun = None
        self.ns['_restore_show_screen_from_karafun'](self.host)
        self.drain()
        self.window.setWindowOpacity.assert_called_with(0.9)
        self.native.setStyleMask_.assert_called_with(17)
        self.native.setCollectionBehavior_.assert_called_with(8)
        self.native.setLevel_.assert_called_with(2)
        self.native.setIgnoresMouseEvents_.assert_called_with(True)
        self.assertFalse(self.host._karafun_auxiliary_show_screen)
        self.assertFalse(self.host._karafun_handoff_complete)

    def test_late_restore_cannot_cover_a_new_karafun_session(self):
        self.handoff()
        self.host._active_external_karafun = None
        self.ns['_restore_show_screen_from_karafun'](self.host)
        self.window.setWindowOpacity.reset_mock()
        self.host._active_external_karafun = {'entry': {'title': 'Next song'}}
        self.drain()
        self.window.setWindowOpacity.assert_not_called()

    def test_previously_hidden_window_recovers_opacity_while_staying_hidden(self):
        self.window.isVisible.return_value = False
        self.handoff()
        self.host._active_external_karafun = None
        self.ns['_restore_show_screen_from_karafun'](self.host)
        self.drain()
        self.window.hide.assert_called()
        self.window.setWindowOpacity.assert_called_with(0.9)

    def test_missing_preflight_does_not_fall_back_to_fullscreen(self):
        self.host._karafun_transparent_renderer_ready = False
        self.handoff()
        self.host._karafun_run_window_script.assert_not_called()
        self.window.setWindowOpacity.assert_not_called()
        self.assertFalse(self.host._karafun_handoff_complete)

    def test_placement_script_checks_bounds_and_never_enters_fullscreen(self):
        script = '\n'.join(renderer_windowed_script(-1920, 0, 1920, 1080))
        self.assertNotIn('Expand Player', script)
        self.assertNotIn('to true', script[script.index('if value of attribute "AXFullScreen"'):script.index('set positioned')])
        self.assertLess(script.index('if outputWindow is missing value'), script.index('set position'))
        self.assertLess(script.index('set p to position'), script.index('return "WINDOWED_READY"'))
        with self.assertRaises(ValueError):
            renderer_windowed_script(0, 0, 0, 1080)
