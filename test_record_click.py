"""The spinning record in the hero card as a failsafe: click = play/pause, Shift-click = stop, through the transport buttons' own handlers."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import os
import unittest
from types import SimpleNamespace


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_record", os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RecordClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.m = load_main()

    def test_click_and_shift_click_reach_the_handler(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        art = self.m.CircularAvatar()
        art.resize(150, 150)
        art.show()
        calls = []
        QTest.mouseClick(art, Qt.MouseButton.LeftButton)
        self.assertEqual(calls, [], "no handler yet: a click does nothing (the plain ring of the old layout)")
        art.set_click_action(lambda shift: calls.append(shift))
        QTest.mouseClick(art, Qt.MouseButton.LeftButton)
        QTest.mouseClick(art, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
        QTest.mouseClick(art, Qt.MouseButton.RightButton)
        self.assertEqual(calls, [False, True])                   # right click is not an action
        art.set_click_action(None)
        QTest.mouseClick(art, Qt.MouseButton.LeftButton)
        self.assertEqual(calls, [False, True])
        art.close()

    def test_the_record_asks_first_and_then_presses_the_right_transport_button(self):
        from unittest import mock
        pressed, asked = [], []

        def button(name):
            return SimpleNamespace(click=lambda: pressed.append(name))

        class Dlg:
            answer = True
            def __init__(self, parent=None, **kw):
                asked.append((kw.get("title"), kw.get("confirm_text")))
            def exec(self):
                return self.m.QDialog.DialogCode.Accepted if Dlg.answer else self.m.QDialog.DialogCode.Rejected
        Dlg.m = self.m
        app = SimpleNamespace(stop_button=button("stop"), karaoke_pause_button=button("pause"), play_next=button("play"),
                              karaoke_playing=False, settings={"ui_record_confirm": True}, _is_karaoke_paused=lambda: False)
        handler = self.m.KaraokeApp._on_record_clicked
        with mock.patch.object(self.m, "ConfirmInterruptDialog", Dlg):
            handler(app, False)                    # idle: asks, then play
            app.karaoke_playing = True
            handler(app, False)                    # playing: asks, then pause
            app._is_karaoke_paused = lambda: True
            handler(app, False)                    # paused: asks, then resume (the same button)
            handler(app, True)                     # shift: asks, then stop
            Dlg.answer = False
            handler(app, True)                     # Cancel: nothing is pressed
        self.assertEqual(pressed, ["play", "pause", "pause", "stop"])
        self.assertEqual([a[0] for a in asked], ["Play the next singer?", "Pause the song?", "Resume the song?", "Stop the song?", "Stop the song?"])

    def test_with_the_warning_switched_off_a_click_acts_at_once(self):
        from unittest import mock
        pressed = []

        def button(name):
            return SimpleNamespace(click=lambda: pressed.append(name))
        app = SimpleNamespace(stop_button=button("stop"), karaoke_pause_button=button("pause"), play_next=button("play"),
                              karaoke_playing=True, settings={"ui_record_confirm": False}, _is_karaoke_paused=lambda: False)
        with mock.patch.object(self.m, "ConfirmInterruptDialog", side_effect=AssertionError("must not ask")):
            self.m.KaraokeApp._on_record_clicked(app, False)
            self.m.KaraokeApp._on_record_clicked(app, True)
        self.assertEqual(pressed, ["pause", "stop"])

    def test_the_warning_is_a_setting_that_defaults_on(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        self.assertIn('"ui_record_confirm": True', src)
        self.assertIn('self.settings["ui_record_confirm"] = bool(checked)', src)
        self.assertNotIn("PointingHandCursor", src)                  # the project's rule: the system arrow cursor everywhere

    def test_it_is_wired_only_with_the_redesigned_record(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        self.assertIn("art.set_click_action(self._on_record_clicked)", src)
        self.assertIn("art.set_click_action(None)", src)

    def test_shift_click_on_play_restarts_the_song_that_is_on(self):
        from unittest import mock
        from PyQt6.QtCore import Qt
        calls = []
        app = SimpleNamespace(
            karaoke_transport=object(), _active_external_karafun=None,
            restart_track=lambda skip_confirmation=False: calls.append(("restart", skip_confirmation)),
            _is_karaoke_paused=lambda: True, toggle_karaoke_pause=lambda: calls.append(("resume",)),
        )
        with mock.patch.object(self.m.QGuiApplication, "keyboardModifiers", return_value=Qt.KeyboardModifier.ShiftModifier):
            self.m.KaraokeApp._on_play_control_clicked(app)
        self.assertEqual(calls, [("restart", True), ("resume",)])           # from the beginning, and playing again if it was paused

    def test_shift_click_does_not_touch_a_karafun_song_or_an_idle_player(self):
        from unittest import mock
        from PyQt6.QtCore import Qt
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        body = src[src.index("    def _on_play_control_clicked"):src.index("starting = bool(", src.index("    def _on_play_control_clicked"))]
        self.assertIn("karaoke_transport", body)
        self.assertIn("_active_external_karafun", body)                        # KaraFun plays outside SingWS: no restart there


if __name__ == "__main__":
    unittest.main()
