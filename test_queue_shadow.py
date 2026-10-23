import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import ast
import ctypes
import os
import random
import re
import unittest
from pathlib import Path

import queue_shadow as qs
from tools import queue_parity as qp
from tools import queue_replay as qr

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


def _lib():
    lib = ctypes.CDLL(str(_DYLIB))
    fn = lib.singws_queue_admit_json
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    fn.restype = ctypes.c_int64
    return lib


def _python_conflict_function():
    """The app's own _tombstone_signature_conflicts, extracted from the source (not re-implemented here)."""
    src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_tombstone_signature_conflicts":
            fn = ast.Module(body=[ast.FunctionDef(name=node.name, args=node.args, body=node.body, decorator_list=[],
                                                  returns=None, type_comment=None, type_params=[])], type_ignores=[])
            ns = {"re": re}
            exec(compile(ast.fix_missing_locations(fn), "app", "exec"), ns)
            return ns[node.name]
    raise AssertionError("_tombstone_signature_conflicts not found in the app")


class Fake:
    """A lib stand-in that always answers the same thing, to prove a disagreement is logged and nothing else happens."""
    def __init__(self, answer):
        self.answer = answer
        self.singws_queue_admit_json = self._call

    def _call(self, facts, buf, cap):
        out = ('{"admit":"%s","accepts":false}' % self.answer).encode()
        ctypes.memmove(buf, out, len(out))
        return len(out)


class NoLibraryTests(unittest.TestCase):
    def test_unavailable_machine_is_a_silent_no_op(self):
        lines = []
        sh = qs.QueueShadow(lines.append, lib=None)
        sh.available = False
        self.assertIsNone(sh.check("tombstone", 1, tombstone_title="A", row_title="A"))
        self.assertEqual(lines, [])

    def test_unknown_branch_never_raises(self):
        sh = qs.QueueShadow(lambda _t: None, lib=Fake("RejectTombstone"))
        self.assertIsNone(sh.check("not-a-branch", 1))


class DisagreementTests(unittest.TestCase):
    def test_a_disagreement_is_logged_once_per_call_and_counted(self):
        lines = []
        sh = qs.QueueShadow(lines.append, lib=Fake("AcceptIdReuse"))
        self.assertFalse(sh.check("tombstone", 42, tombstone_title="Song", row_title="Song"))
        self.assertEqual(sh.mismatches, 1)
        self.assertIn("[QUEUE-SHADOW] MISMATCH request_id=42 app=RejectTombstone rust=AcceptIdReuse", lines[0])

    def test_agreement_logs_each_branch_answer_pair_only_once(self):
        lines = []
        sh = qs.QueueShadow(lines.append, lib=Fake("RejectTombstone"))
        for rid in range(5):
            self.assertTrue(sh.check("tombstone", rid, tombstone_title="S", row_title="S"))
        self.assertEqual([l for l in lines if "decision" in l].__len__(), 1)
        self.assertEqual(sh.mismatches, 0)

    def test_a_failing_log_callback_cannot_break_the_caller(self):
        def boom(_text):
            raise RuntimeError("log failed")
        sh = qs.QueueShadow(boom, lib=Fake("AcceptIdReuse"))
        self.assertFalse(sh.check("tombstone", 1, tombstone_title="S", row_title="S"))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the queue export")
class RealLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()

    def shadow(self):
        lines = []
        return qs.QueueShadow(lines.append, lib=self.lib), lines

    def test_every_app_branch_agrees_with_rust(self):
        sh, lines = self.shadow()
        self.assertTrue(sh.check("tombstone", 1, tombstone_title="Risk It All", id_removed=True, row_title="risk it all"))
        self.assertTrue(sh.check("id_reuse", 2, tombstone_title="Old", row_title="Brand New Song"))
        self.assertTrue(sh.check("terminal_stale", 3, id_removed=True, terminal_title="Song A", row_title="Song A"))
        self.assertTrue(sh.check("terminal_stale", 4, id_removed=True, terminal_title=None, row_title="Song A"))
        self.assertTrue(sh.check("terminal_reuse", 5, id_removed=True, terminal_title="Song A", row_title="Other Tune"))
        self.assertEqual(sh.mismatches, 0, lines)

    def test_a_wrong_app_branch_is_a_mismatch(self):
        sh, lines = self.shadow()
        self.assertFalse(sh.check("tombstone", 9, tombstone_title="Old", row_title="Brand New Song"))
        self.assertTrue(any("MISMATCH" in l for l in lines))

    def test_bad_json_returns_a_negative_code_not_a_crash(self):
        buf = ctypes.create_string_buffer(64)
        self.assertLess(self.lib.singws_queue_admit_json(b"{", buf, 64), 0)
        self.assertEqual(self.lib.singws_queue_admit_json(b'{"tombstone_title":"A","id_removed":false,"terminal_title":null,"row_title":"A"}', buf, 4), -2)

    def test_rust_title_rule_matches_the_apps_own_function_on_random_titles(self):
        py = _python_conflict_function()
        rng = random.Random(20261023)
        alphabet = list("abcXYZ019 -_.,'!?()") + ["é", "İ", "ß", "Σ", "ς", "K", "٣", "日", "本", "😀", "́", "ǅ", "Ⅷ"]
        words = ["Rocket Man", "Rocketman", "Risk It All", "risk it all (karaoke)", "", "!!!", "Don't Stop", "Dont Stop"]
        cases = [(a, b) for a in words for b in words]
        for _ in range(4000):
            cases.append(("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9))),
                          "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9)))))
        bad = []
        for old, new in cases:
            conflict = bool(py({"title": old}, {"title": new}))
            got = qp.rust_admit(self.lib, tombstone_title=old, row_title=new)
            if got != ("AcceptIdReuse" if conflict else "RejectTombstone"):
                bad.append((old, new, conflict, got))
        self.assertEqual(bad[:5], [], f"{len(bad)} of {len(cases)} cases differ between the app and Rust")


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the queue export")
class ParityToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = _lib()

    def ev(self, kind, t="2026-10-22T10:00:00", **kw):
        return {"kind": kind, "t": t, "seq": 0, "file": "f.log", **kw}

    def test_replay_checks_tombstone_decisions_from_the_log(self):
        events = [
            self.ev("tombstone_created", id=7, title="Risk It All"),
            self.ev("tombstone_poll_ignored", id=7, branch="tombstone", title="risk it all"),
            self.ev("tombstone_poll_ignored", id=8, branch="tombstone", title="x"),          # tombstone unknown
            self.ev("tombstone_poll_ignored", id=9, branch="terminal_stale", title="y"),     # weak check
            self.ev("tombstone_reuse", id=7, branch="tombstone", old_title="Old", new_title="Brand New"),
            self.ev("tombstone_reuse", id=7, branch="terminal", old_title="A Song", new_title="Different"),
            self.ev("tombstone_reuse", id=7, branch="tombstone"),                              # no titles in the log
            self.ev("tombstone_poll_ignored", id=7, t="2026-07-01T10:00:00", branch="tombstone", title="Anything"),  # old rule
        ]
        stats, bad = qp.replay(events, self.lib)
        self.assertEqual(bad, [])
        self.assertEqual(dict(stats), {"tombstone_ignored_checked": 1, "skipped_unknown_tombstone": 1,
                                       "terminal_stale_checked_weak": 1, "id_reuse_checked": 2,
                                       "reuse_without_titles_skipped": 1, "skipped_before_rule_change": 1})

    def test_replay_reports_a_disagreement(self):
        events = [self.ev("tombstone_created", id=7, title="Old Song"),
                  self.ev("tombstone_poll_ignored", id=7, branch="tombstone", title="Completely Different")]
        stats, bad = qp.replay(events, self.lib)
        self.assertEqual(len(bad), 1)
        self.assertEqual((bad[0][1], bad[0][2]), ("RejectTombstone", "AcceptIdReuse"))

    def test_second_created_for_an_id_replaces_the_remembered_title(self):
        events = [self.ev("tombstone_created", id=7, title="First"), self.ev("tombstone_created", id=7, title="Second"),
                  self.ev("tombstone_poll_ignored", id=7, branch="tombstone", title="second")]
        self.assertEqual(qp.replay(events, self.lib)[1], [])

    def test_parser_extension_carries_titles_and_branches(self):
        e = qr.parse_line("[10:00:00] [INFO] [REMOTE-TOMBSTONE] poll old request ignored request_id=1790974569756 singer='S' title='T' reason=tombstone; re-push removed status")
        self.assertEqual((e["branch"], e["title"]), ("tombstone", "T"))
        e = qr.parse_line("[10:00:00] [INFO] [REMOTE-TOMBSTONE] stale non-terminal row for terminal id ignored request_id=1790974569756 singer='S' title='T2'")
        self.assertEqual((e["branch"], e["title"]), ("terminal_stale", "T2"))
        e = qr.parse_line("[10:00:00] [INFO] [REMOTE-TOMBSTONE] terminal id reused by new request; accepting request_id=1790974569756 old_title='A' new_title='B'")
        self.assertEqual((e["kind"], e["branch"], e["old_title"], e["new_title"]), ("tombstone_reuse", "terminal", "A", "B"))
        e = qr.parse_line("[10:00:00] [INFO] [REMOTE-TOMBSTONE] reused request id accepted request_id=1790974569756 tombstone_title='A' request_title='B'")
        self.assertEqual((e["branch"], e["old_title"], e["new_title"]), ("tombstone", "A", "B"))


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")

    def test_off_by_default_and_log_only(self):
        self.assertIn('"queue_shadow": False', self.src)
        start = self.src.index("def _queue_shadow_check")
        block = self.src[start:start + 700]
        self.assertIn("except Exception:", block)
        self.assertNotIn("return sh.admit", block)  # the Rust answer is never returned to the decision

    def test_all_four_branches_are_hooked_before_their_side_effects(self):
        for branch, anchor in (("id_reuse", "self._drop_remote_request_tombstone(request_id, reason=\"id_reuse_confirmed\")"),
                               ("tombstone", "if request_id not in logged_tombstone_repush:"),
                               ("terminal_reuse", "removed_ids.discard(request_id)"),
                               ("terminal_stale", "if request_id not in logged_tombstone_repush:")):
            hook = f'self._queue_shadow_check("{branch}"'
            self.assertIn(hook, self.src)
            self.assertLess(self.src.index(hook), self.src.index(anchor, self.src.index(hook)))

    def test_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"queue_shadow.py"', text)
            self.assertIn("'queue_shadow'", text)


if __name__ == "__main__":
    unittest.main()
