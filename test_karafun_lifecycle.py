"""KaraFun lifecycle regressions without controlling the real KaraFun app."""
import importlib.util
import types
import unittest
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton


class KaraFunLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location("singws_karafun_lifecycle", "0.2.18.1.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_search_return_does_not_complete_song_but_explicit_click_does(self):
        host = QMainWindow()
        self.addCleanup(host.close)
        active = {"entry": {"title": "Memory"}, "singer": {"name": "Dan"}}
        host._active_external_karafun = active
        host._karafun_entry_artist_title = lambda entry: ("Sugarcult", "Memory")
        host._finish_external_karafun_playback = mock.Mock()
        host._open_karafun_for_entry = mock.Mock()
        host._copy_karafun_lookup_text = mock.Mock()
        self.module.KaraokeApp._show_external_karafun_dialog(host, activate=False)
        dialog = host._active_external_karafun_dialog
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        buttons = dialog.findChildren(QPushButton)
        for button in buttons:
            self.assertFalse(button.autoDefault())
            self.assertFalse(button.isDefault())
            button.setFocus()
            QTest.keyClick(button, Qt.Key.Key_Return)
            QTest.keyClick(button, Qt.Key.Key_Enter)
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        self.app.processEvents()
        host._finish_external_karafun_playback.assert_not_called()
        self.assertTrue(dialog.isVisible())
        complete = next(b for b in buttons if b.text() == "Complete")
        QTest.mouseClick(complete, Qt.MouseButton.LeftButton)
        host._finish_external_karafun_playback.assert_called_once_with("complete", expected_active=active)

    def test_old_dialog_cannot_complete_a_new_session_even_with_same_entry(self):
        entry = {"title": "Memory"}
        old_session = {"entry": entry}
        new_session = {"entry": entry}
        host = types.SimpleNamespace(_active_external_karafun=new_session)
        self.module.KaraokeApp._finish_external_karafun_playback(
            host, "complete", expected_active=old_session)
        self.assertIs(host._active_external_karafun, new_session)

    def test_cancel_during_search_cannot_activate_result_or_publish_success(self):
        entry = {"title": "Memory", "artist": "Sugarcult"}
        active = {"entry": entry}
        callbacks = []
        workers = []
        host = types.SimpleNamespace(
            settings={"karafun_auto_queue_enabled": True, "karafun_transparent_handoff": False},
            _active_external_karafun=active,
            _karafun_entry_artist_title=lambda e: ("Sugarcult", "Memory"),
            _ensure_queue_entry_id=lambda e: "request-1",
            _karafun_search_queries_for_entry=lambda e: ["Sugarcult Memory"],
            _set_karafun_entry_status=mock.Mock(),
            _open_karafun_for_entry=lambda e: True,
            _karafun_apple_events_preflight=lambda: (True, ""),
            _ensure_karafun_audio_output=lambda: (True, ""),
            _karafun_search_script=lambda **kwargs: ["search"],
            _macos_native_double_click=mock.Mock(),
            _run_on_ui_thread=callbacks.append,
        )

        def finish_while_searching(lines, **kwargs):
            host._active_external_karafun = {"entry": entry}  # replay of same request
            return True, "FOUND|303|217|03:46", ""

        host._run_karafun_applescript_sync = finish_while_searching
        thread = lambda target, **kwargs: types.SimpleNamespace(start=lambda: workers.append(target))
        with mock.patch.object(self.module.sys, "platform", "darwin"), \
             mock.patch.object(self.module.threading, "Thread", side_effect=thread):
            self.assertTrue(self.module.KaraokeApp._automate_karafun_search_and_play(host, entry))
            workers[0]()
            for callback in callbacks:
                callback()
        host._macos_native_double_click.assert_not_called()
        self.assertEqual(host._set_karafun_entry_status.call_count, 1)  # launching only
        self.assertNotEqual(entry.get("karafun_submission_state"), "karafun_queued")

    def test_end_monitor_callback_is_bound_to_its_playback_session(self):
        for replace_session in (False, True):
            with self.subTest(replace_session=replace_session):
                entry = {"title": "Memory", "duration": 10}
                active = {"entry": entry}
                callbacks = []
                clock = [100.0]
                readings = iter(["STATE|PLAYING", "STATE|PLAYING", "STATE|IDLE"])
                host = types.SimpleNamespace(
                    _active_external_karafun=active,
                    _run_on_ui_thread=callbacks.append,
                    _karafun_clock_seconds=lambda v: None,
                    _finish_external_karafun_playback=mock.Mock(),
                    KARAFUN_PLAYBACK_RECOVERY_DELAY_S=12,
                    KARAFUN_HANDOFF_TIMEOUT_RECOVERY_DELAY_S=2,
                    KARAFUN_PLAYBACK_ALERT_DELAY_S=40,
                )

                def probe(script, **kwargs):
                    clock[0] += 10.0
                    return True, next(readings), ""

                host._run_karafun_applescript_sync = probe
                thread = lambda target, **kwargs: types.SimpleNamespace(start=target)
                with mock.patch.object(self.module.threading, "Thread", side_effect=thread), \
                     mock.patch.object(self.module.threading, "Timer"), \
                     mock.patch.object(self.module.time, "monotonic", side_effect=lambda: clock[0]), \
                     mock.patch.object(self.module.time, "sleep"):
                    self.module.KaraokeApp._start_karafun_completion_monitor(host, entry)
                self.assertEqual(len(callbacks), 1)
                if replace_session:
                    host._active_external_karafun = {"entry": entry}
                callbacks[0]()
                if replace_session:
                    host._finish_external_karafun_playback.assert_not_called()
                else:
                    host._finish_external_karafun_playback.assert_called_once_with(
                        "complete", expected_active=active)


if __name__ == "__main__":
    unittest.main()
