"""The emoji button on the host's chat boxes (Everyone / Private bar, and the Host chat tab)."""
import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget
    QT_OK = True
except Exception:  # pragma: no cover
    QT_OK = False

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def picker_namespace():
    tree = ast.parse(SOURCE)
    sections = method = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_EMOJI_SECTIONS" for t in sub.targets):
                    sections = sub
                if isinstance(sub, ast.FunctionDef) and sub.name == "_build_emoji_picker_widget":
                    method = sub
    ns = {"QWidget": QWidget, "QVBoxLayout": QVBoxLayout, "QLabel": QLabel, "Qt": Qt, "section_meta_css": lambda: ""}
    exec(compile(ast.Module(body=[sections, method], type_ignores=[]), "emoji-test", "exec"), ns)
    return ns


@unittest.skipUnless(QT_OK, "PyQt6 not usable here")
class EmojiPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.ns = picker_namespace()
        cls.sections = cls.ns["_EMOJI_SECTIONS"]

    def make(self, edit, **kw):
        from types import SimpleNamespace
        host = SimpleNamespace(_EMOJI_SECTIONS=self.sections)
        return self.ns["_build_emoji_picker_widget"](host, edit, **kw)

    def test_the_set_is_sensible(self):
        tokens = [e for _title, chars in self.sections for e in chars.split()]
        self.assertGreaterEqual(len(tokens), 120)
        self.assertEqual(len(tokens), len(set(tokens)), "no emoji listed twice")
        for must in ("😀", "😂", "👍", "🎤", "🎉", "🔥", "❤️", "👏"):
            self.assertIn(must, tokens)

    def test_one_button_per_emoji_and_clicking_inserts_at_the_cursor(self):
        edit = QLineEdit("ab"); edit.setCursorPosition(1)
        picked = []
        picker = self.make(edit, on_pick=picked.append)
        buttons = picker.widget().findChildren(QToolButton)
        expected = sum(len(chars.split()) for _t, chars in self.sections)
        self.assertEqual(len(buttons), expected)
        next(b for b in buttons if b.text() == "🎤").click()
        self.assertEqual(edit.text(), "a🎤b")
        next(b for b in buttons if b.text() == "❤️").click()
        self.assertEqual(edit.text(), "a🎤❤️b")
        self.assertEqual(picked, ["🎤", "❤️"])

    def test_the_length_limit_of_the_box_is_respected(self):
        edit = QLineEdit(); edit.setMaxLength(2)
        picker = self.make(edit)
        buttons = picker.widget().findChildren(QToolButton)
        next(b for b in buttons if b.text() == "😀").click()
        next(b for b in buttons if b.text() == "😀").click()
        self.assertLessEqual(len(edit.text().encode("utf-16-le")) // 2, 2)


class WiringTests(unittest.TestCase):
    def test_both_chat_boxes_have_an_emoji_button(self):
        self.assertIn("self.chat_emoji_button.clicked.connect(lambda: self._show_emoji_picker(self.chat_message_input, self.chat_emoji_button))", SOURCE)
        self.assertIn("self.room_say_emoji_button.clicked.connect(lambda: self._show_emoji_picker(self.room_say_input, self.room_say_emoji_button))", SOURCE)

    def test_the_room_bar_button_follows_the_bars_enabled_state(self):
        body = SOURCE[SOURCE.index("def _refresh_room_compose(self):"):SOURCE.index("_ROOM_SAY_ERRORS = {")]
        self.assertIn("emoji_button.setEnabled(on)", body)

    def test_the_picker_stays_open_for_several_picks(self):
        body = SOURCE[SOURCE.index("def _show_emoji_picker("):SOURCE.index("def _room_say_context(self):")]
        self.assertIn("QWidgetAction", body)
        self.assertNotIn("menu.close()", body)


if __name__ == "__main__":
    unittest.main()
