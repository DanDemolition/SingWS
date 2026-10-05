"""2026-10-04 22:33, KaraFun song with key -1: SingWS set the key correctly through KaraFun's menu (ok=1) and then told the operator
"KaraFun started, but key/tempo controls need manual adjustment". The note judged a placeholder reply from a step that had been skipped
because the adjustment was already applied."""
import ast
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def needs_attention():
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_karafun_adjustment_needs_attention":
                    sub.decorator_list = []
                    ns = {}
                    exec(compile(ast.Module(body=[sub], type_ignores=[]), "note", "exec"), ns)
                    return ns["_karafun_adjustment_needs_attention"]
    raise AssertionError("missing")


class AdjustmentNoteTests(unittest.TestCase):
    PLACEHOLDER = "ADJUSTED|false|false"                   # what the skipped slider step reports

    def test_an_adjustment_that_was_already_applied_never_warns(self):
        check = needs_attention()
        self.assertFalse(check(-1, 100, self.PLACEHOLDER, True))          # the 22:33 song
        self.assertFalse(check(2, 90, self.PLACEHOLDER, True))
        self.assertFalse(check(0, 110, self.PLACEHOLDER, True))

    def test_an_adjustment_that_was_not_applied_still_warns(self):
        check = needs_attention()
        self.assertTrue(check(-1, 100, self.PLACEHOLDER, False))
        self.assertTrue(check(0, 110, self.PLACEHOLDER, False))
        self.assertTrue(check(3, 120, "ADJUSTED|true|false", False))      # key set by sliders but tempo not

    def test_the_slider_step_reporting_success_clears_the_warning(self):
        check = needs_attention()
        self.assertFalse(check(-1, 100, "ADJUSTED|true|false", False))
        self.assertFalse(check(0, 110, "ADJUSTED|false|true", False))
        self.assertFalse(check(-1, 110, "ADJUSTED|true|true", False))

    def test_default_key_and_tempo_never_warn(self):
        check = needs_attention()
        for applied in (True, False):
            self.assertFalse(check(0, 100, self.PLACEHOLDER, applied))
            self.assertFalse(check(0, 100, "", applied))

    def test_the_started_song_note_uses_the_applied_flag(self):
        start = SOURCE.index("self._karafun_adjustment_needs_attention(\n                            requested_key")
        call = SOURCE[start:start + 330]
        self.assertIn('str(entry.get("karafun_adjustment_applied") or "") == adjustment_signature', call)


if __name__ == "__main__":
    unittest.main()
