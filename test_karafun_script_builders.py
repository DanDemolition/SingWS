"""The small KaraFun AppleScript lists were moved out of their methods into static builders (tools/karafun_extract.py, proven by syntax-tree equality with
the old inline lists). The builders must stay plain lists of strings and the methods must keep using them."""
import _test_profile  # noqa: F401
import ast
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
BUILDERS = {
    "_karafun_preflight_script": "_karafun_apple_events_preflight",
    "_karafun_main_window_state_script": "_karafun_main_window_state",
    "_karafun_press_play_script": "_karafun_press_play_control",
    "_karafun_playback_menu_state_script": "_karafun_playback_menu_state",
    "_karafun_raise_results_window_script": "_karafun_raise_results_window",
    "_karafun_read_key_tempo_script": "_karafun_read_key_tempo",
    "_karafun_apply_key_tempo_script": "_karafun_apply_key_tempo",
    "_karafun_seek_script": "_karafun_seek_to",
    "_karafun_toggle_playback_script": "_toggle_karafun_playback",
    "_karafun_double_click_script": "_karafun_activate_result_for_playback",
}


def klass():
    tree = ast.parse(SOURCE)
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and any(isinstance(s, ast.FunctionDef) and s.name == "_karafun_preflight_script" for s in n.body))


class BuilderTests(unittest.TestCase):
    def test_each_builder_is_a_static_list_of_system_events_lines(self):
        cls = klass()
        for builder in BUILDERS:
            func = next(s for s in cls.body if isinstance(s, ast.FunctionDef) and s.name == builder)
            self.assertTrue(any(ast.unparse(d) == "staticmethod" for d in func.decorator_list), builder)
            ret = next(n for n in func.body if isinstance(n, ast.Return))
            self.assertIsInstance(ret.value, ast.List, builder)
            self.assertEqual(ret.value.elts[0].value, 'tell application "System Events"', builder)
            self.assertEqual(ret.value.elts[-1].value, "end tell", builder)

    def test_the_methods_use_their_builders(self):
        cls = klass()
        for builder, method in BUILDERS.items():
            func = next(s for s in cls.body if isinstance(s, ast.FunctionDef) and s.name == method)
            self.assertIn(f"self.{builder}(", ast.unparse(func), method)
            self.assertFalse(any(isinstance(n, ast.List) and n.elts and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == 'tell application "System Events"'
                                 for n in ast.walk(func)), f"{method} still holds an inline script")


if __name__ == "__main__":
    unittest.main()
