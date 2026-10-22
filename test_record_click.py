"""The spinning record in the hero card as a failsafe: click = play/pause, Shift-click = stop, through the transport buttons' own handlers."""
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

    def test_the_record_presses_the_right_transport_button(self):
        pressed = []

        def button(name):
            return SimpleNamespace(click=lambda: pressed.append(name))
        app = SimpleNamespace(stop_button=button("stop"), karaoke_pause_button=button("pause"), play_next=button("play"), karaoke_playing=False)
        handler = self.m.KaraokeApp._on_record_clicked
        handler(app, False)                    # idle: play
        app.karaoke_playing = True
        handler(app, False)                    # playing: pause / resume
        handler(app, True)                     # shift: stop
        self.assertEqual(pressed, ["play", "pause", "stop"])

    def test_it_is_wired_only_with_the_redesigned_record(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()
        self.assertIn("art.set_click_action(self._on_record_clicked)", src)
        self.assertIn("art.set_click_action(None)", src)


if __name__ == "__main__":
    unittest.main()
