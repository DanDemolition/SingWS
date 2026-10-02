"""KaraFun result search: a label such as "Display:" must never be taken for a song length, and a queued title
with a trailing "(live)" style qualifier must still find its row (2026-10-01 show: the search clicked a label and
the song took 63 seconds to start)."""
import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def namespace():
    tree = ast.parse(SOURCE)
    wanted_funcs = {"karafun_match_title"}
    wanted_assigns = {"KARAFUN_DURATION_HANDLER_LINES"}
    wanted_methods = {"_karafun_search_script", "_karafun_applescript_literal", "_karafun_script_source"}
    body = []
    methods = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted_funcs:
            body.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in wanted_assigns for t in node.targets):
            body.append(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in wanted_methods:
                    sub.decorator_list = []
                    methods[sub.name] = sub
    ns = {"re": re}
    exec(compile(ast.Module(body=body + list(methods.values()), type_ignores=[]), "karafun-search", "exec"), ns)
    host = SimpleNamespace()
    host._karafun_applescript_literal = ns["_karafun_applescript_literal"]
    host._karafun_script_source = ns["_karafun_script_source"]
    host._karafun_search_script = lambda **kw: ns["_karafun_search_script"](host, **kw)
    return ns, host


NS, HOST = namespace()


class MatchTitleTests(unittest.TestCase):
    def test_trailing_qualifiers_are_dropped(self):
        f = NS["karafun_match_title"]
        self.assertEqual(f("Just the Way You Are (live)"), "Just the Way You Are")
        self.assertEqual(f("Louder (feat. Sian Evans) (Doctor P & Flux Pavillion Remix)"), "Louder")
        self.assertEqual(f("Hold On [Remastered]"), "Hold On")

    def test_real_titles_are_kept(self):
        f = NS["karafun_match_title"]
        self.assertEqual(f("Memory"), "Memory")
        self.assertEqual(f("(I Can't Get No) Satisfaction"), "(I Can't Get No) Satisfaction")
        self.assertEqual(f("(Live)"), "(Live)")                      # never reduce a title to nothing
        self.assertEqual(f(""), "")
        self.assertEqual(f(None), "")


@unittest.skipUnless(sys.platform == "darwin", "AppleScript runs on macOS only")
class DurationHandlerTests(unittest.TestCase):
    """Runs the real AppleScript handler (no UI scripting, no System Events) on sample row texts."""

    def run_handler(self, text):
        lines = list(NS["KARAFUN_DURATION_HANDLER_LINES"])
        source = "\n".join(lines) + f'\nreturn isDurationText({ascript_literal(text)})\n'
        out = subprocess.run(["osascript", "-e", source], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip() == "true"

    def test_real_lengths_are_accepted(self):
        for text in ("03:41", "3:41", "02:49", "04:15", "1:02:03", " 3:41"):
            self.assertTrue(self.run_handler(text), text)

    def test_labels_are_rejected(self):
        for text in ("Display:", "Discover", "My Playlists", "Artist:", "Time:", "", ":", "12", "3:41 PM", "Duration: 3:41"):
            self.assertFalse(self.run_handler(text), text)


def ascript_literal(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


class GeneratedScriptTests(unittest.TestCase):
    def script(self, **kw):
        return HOST._karafun_script_source(HOST._karafun_search_script(**kw))

    def test_all_three_row_checks_use_the_strict_length_test(self):
        source = self.script(query="Bruno Mars Just the Way You Are", safe_title="Just the Way You Are (live)",
                             safe_artist="Bruno Mars", require_exact_title=True)
        self.assertEqual(source.count("my isDurationText(dName)"), 3)
        self.assertNotIn('dName contains ":"', source)
        self.assertIn("on isDurationText(t)", source)

    def test_the_row_is_matched_on_the_plain_title(self):
        source = self.script(query="x", safe_title="Just the Way You Are (live)", safe_artist="Bruno Mars", require_exact_title=True)
        self.assertIn('contains "Just the Way You Are")', source)
        self.assertNotIn("(live)", source)

    def test_a_plain_search_still_builds(self):
        source = self.script(query="Bruno Mars")
        self.assertIn('return "SEARCHED"', source)

    @unittest.skipUnless(sys.platform == "darwin", "osacompile is macOS only")
    def test_every_variant_compiles(self):
        for kw in (dict(query="q", safe_title="Just the Way You Are (live)", safe_artist="Bruno Mars", require_exact_title=True),
                   dict(query="q", safe_title="Memory", require_exact_title=True),
                   dict(query="q", safe_title="Memory", safe_artist="Sugarcult", require_exact_title=True, resolve_only=True),
                   dict(query="q")):
            result = subprocess.run(["osacompile", "-o", "/dev/null"], input=self.script(**kw), capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, f"{kw}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
