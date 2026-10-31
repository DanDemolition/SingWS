"""Stage A5 start drive (setting `start_drive`, default OFF): Rust's start decisions are logged as [START-DRIVE] lines and selected only when they equal Python's.

The replay runs random queues through the REAL `play_next_file` three times: the drive off, the drive on with the real Rust answers, and the drive on with every Rust
answer corrupted. What the app does (queue order, transport arguments, published artist and title) must be identical in all three; the corrupted run must log disagreements
and select Python's answer every time.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import ctypes
import json
import os
import random
import re
import unittest
from pathlib import Path
from unittest import mock

import start_shadow
import test_show_critical_regressions as base
from test_start_shadow_plan import SHAPES, StartPlan as _PlanBase  # noqa: F401

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))
LINE = re.compile(r"^\[START-DRIVE\] decision=(\S+) python=(.*) rust=(.*) selected=(.*) owner=(\S+) disagreement=([01])$")


class Selection(unittest.TestCase):
    def test_python_wins_every_disagreement_and_rust_only_on_agreement(self):
        sel = start_shadow.select_start_decision
        self.assertEqual(sel("mp4", "mp4", True), ("mp4", False, "rust"))
        self.assertEqual(sel("mp4", "mp3", True), ("mp4", True, "python-fallback"))
        self.assertEqual(sel("mp4", None, True), ("mp4", False, "python"))
        self.assertEqual(sel("mp4", "mp3", False), ("mp4", False, "python"))
        self.assertEqual(sel({"a": 1}, {"a": 2}, True, agree=True), ({"a": 2}, False, "rust"))   # an explicit verdict decides
        self.assertEqual(sel({"a": 1}, {"a": 1}, True, agree=False), ({"a": 1}, True, "python-fallback"))

    def test_off_by_default_and_logs_nothing_when_off(self):
        lines = []
        shadow = start_shadow.StartShadow(lines.append)
        self.assertFalse(shadow.drive)
        self.assertIsNone(shadow._drive("route", True, "mp4", "mp4"))
        self.assertEqual(lines, [])
        shadow.drive = True
        self.assertEqual(shadow._drive("route", False, "mp4", "mp3"), "mp4")
        self.assertEqual(shadow.drive_disagreements, 1)
        m = LINE.match(lines[0])
        self.assertTrue(m, lines)
        self.assertEqual((m.group(1), m.group(5), m.group(6)), ("route", "python-fallback", "1"))

    def test_a_logger_that_raises_cannot_break_the_start(self):
        def boom(_text):
            raise RuntimeError("log failed")
        shadow = start_shadow.StartShadow(boom)
        shadow.drive = True
        self.assertIsNone(shadow._drive("route", True, "mp4", "mp4"))


class FakeLib:
    """A start library that answers every call with one canned JSON object."""

    def __init__(self, answer):
        self.answer = json.dumps(answer).encode()

    def singws_start_json(self, payload, buf, cap):
        ctypes.memmove(buf, self.answer, len(self.answer))
        return len(self.answer)


class DirectChecks(unittest.TestCase):
    """The decisions the replay cannot reach (they sit behind the transport it replaces), through a canned library."""

    def shadow(self, answer):
        lines = []
        shadow = start_shadow.StartShadow(lines.append, lib=FakeLib(answer))
        shadow.drive = True
        return shadow, lines

    def drive_lines(self, lines):
        return [LINE.match(x).groups() for x in lines if x.startswith("[START-DRIVE]")]

    def test_engine_decision(self):
        answer = {"voice": True, "reason": "", "set_shadow_audio": True}
        shadow, lines = self.shadow(answer)
        self.assertTrue(shadow.check_engine({"voice": True, "reason": "", "set_shadow_audio": True}, True, True, "running"))
        [(name, py, rs, sel, owner, dis)] = self.drive_lines(lines)
        self.assertEqual((name, owner, dis), ("engine", "rust", "0"))
        shadow, lines = self.shadow(answer)
        self.assertFalse(shadow.check_engine({"voice": False, "reason": "error", "set_shadow_audio": False}, True, True, "running"))
        [(name, py, rs, sel, owner, dis)] = self.drive_lines(lines)
        self.assertEqual((owner, dis, json.loads(sel)["reason"]), ("python-fallback", "1", "error"))

    def test_rotation_decision(self):
        obj = {"name": "a", "songs": []}
        row = start_shadow.rotation_after([obj])[0]
        facts = {"op": "rotation", "queue": [row], "_objects": [obj]}
        shadow, lines = self.shadow({"queue": [row], "order": [0]})
        self.assertTrue(shadow.check_rotation(facts, [obj]))
        self.assertEqual([(x[0], x[4], x[5]) for x in self.drive_lines(lines)], [("rotation", "rust", "0")])
        shadow, lines = self.shadow({"queue": [dict(row, skipped=True)], "order": [0]})
        self.assertFalse(shadow.check_rotation(facts, [obj]))
        self.assertEqual([(x[0], x[4], x[5]) for x in self.drive_lines(lines)], [("rotation", "python-fallback", "1")])

    def test_pick_and_display_disagreements_keep_the_python_column(self):
        shadow, lines = self.shadow({"singer": 1, "song": 0, "queue": [], "lock_cleared": False})
        self.assertFalse(shadow.check_pick([{"skipped": False, "songs": "d"}], 0, 0, queue=[]))
        [(name, py, rs, sel, owner, dis)] = self.drive_lines(lines)
        self.assertEqual((name, json.loads(py)["singer"], json.loads(rs)["singer"], owner, dis), ("pick", 0, 1, "python-fallback", "1"))
        shadow, lines = self.shadow({"artist": "X", "title": "Y"})
        self.assertFalse(shadow.check_display("A", "B", "", "", None))
        [(name, py, rs, sel, owner, dis)] = self.drive_lines(lines)
        self.assertEqual((name, json.loads(py)["artist"], json.loads(rs)["artist"], json.loads(sel)["artist"], dis), ("display", "A", "X", "A", "1"))

    def test_confirmed_and_performance_decisions(self):
        shadow, lines = self.shadow({"singer_name": "A", "title": "T"})
        self.assertTrue(shadow.check_confirmed({"op": "confirmed"}, {"singer_name": "A"}))
        self.assertFalse(shadow.check_confirmed({"op": "confirmed"}, {"singer_name": "B"}))
        self.assertEqual([(x[0], x[4], x[5]) for x in self.drive_lines(lines)], [("confirmed", "rust", "0"), ("confirmed", "python-fallback", "1")])


class Setting(unittest.TestCase):
    def test_default_is_off_and_the_evidence_migration_does_not_switch_it_on(self):
        singws = base.load_main_module()
        self.assertIs(singws.DEFAULTS["start_drive"], False)
        self.assertNotIn("start_drive", singws._RUST_EVIDENCE_FLAGS)
        saved = {}
        singws._migrate_rust_evidence_defaults(saved)
        self.assertNotIn("start_drive", saved)

    def test_the_lookup_follows_the_setting_each_time(self):
        singws = base.load_main_module()
        singws._START_SHADOW = None
        shadow = singws._start_shadow_for({"start_shadow": True, "start_drive": True})
        if shadow is None:
            self.skipTest("start shadow library not built")
        self.assertTrue(shadow.drive)
        self.assertFalse(singws._start_shadow_for({"start_shadow": True}).drive)
        self.assertIsNone(singws._start_shadow_for({"start_drive": True}))   # the drive needs the shadow
        singws._START_SHADOW = None


class Replay(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app
    make_app = _PlanBase.make_app
    random_queue = _PlanBase.random_queue

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_case(self, seed, drive, corrupt):
        rng = random.Random(seed)
        singers = self.random_queue(rng)
        missing = {p for p in ("/lib/a.mp4", "/lib/a.mp3", "/lib/o.mp4", "/lib/a.cdg") if rng.random() < 0.15}
        zip_ready = rng.choice([("/t/x.cdg", "/t/x.mp3"), (None, None)])
        phrase = rng.choice([0.0, 3.25, 17.0])
        app = self.make_app(singers, phrase, zip_ready)
        app.settings = {"start_shadow": True, "plan_shadow_live": True, "start_drive": drive}
        lines = []
        self.singws._START_SHADOW = None   # a fresh shadow reads the drive flag from this app's settings on its first lookup
        displayed = []
        with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))), \
                mock.patch.object(self.singws.os.path, "exists", lambda p: p not in missing), \
                mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None), \
                mock.patch.object(self.singws, "QThread", lambda *a, **k: mock.MagicMock()), \
                mock.patch.object(self.singws, "ZipExtractWorker", lambda *a, **k: mock.MagicMock()):
            shadow = self.singws._start_shadow_for(app.settings)
            self.assertEqual(shadow.drive, bool(drive))
            if corrupt:
                real_ask, real_call = shadow._ask, shadow._call

                def bad_ask(request, cap=1024):
                    got = real_ask(request, cap)
                    return None if got is None else {k: ("WRONG" if k in ("route", "voice", "artist", "mode") else v) for k, v in got.items()}
                shadow._ask = bad_ask
                shadow._call = lambda request: "WRONG"
            orig_display = shadow.check_display

            def spy(artist, title, *a):
                displayed.append((str(artist), str(title)))
                return orig_display(artist, title, *a)
            shadow.check_display = spy
            app.play_next_file()
        outcome = {"queue": [s["name"] for s in app.queue], "transport": app.transport_calls, "displayed": displayed}
        return outcome, [x for x in lines if x.startswith("[START-DRIVE]")], lines

    def test_replay_over_random_queues_off_on_and_corrupted(self):
        decisions, started = set(), 0
        for case in range(120):
            seed = 20261031 + case
            off, off_drive, _ = self.run_case(seed, False, False)
            on, on_drive, on_lines = self.run_case(seed, True, False)
            bad, bad_drive, _ = self.run_case(seed, True, True)
            self.assertEqual(off_drive, [], case)
            self.assertEqual(off, on, case)       # byte-identical behaviour with the drive on and agreeing
            self.assertEqual(off, bad, case)      # and with every Rust answer wrong: Python wins
            self.assertEqual([x for x in on_lines if "MISMATCH" in x], [], (case, on_lines))
            for line in on_drive:
                m = LINE.match(line)
                self.assertTrue(m, line)
                self.assertEqual((m.group(5), m.group(6)), ("rust", "0"), line)
                if len(m.group(2)) < 400 and len(m.group(4)) < 400:   # longer values are cut in the log
                    self.assertEqual(json.loads(m.group(2)), json.loads(m.group(4)), line)   # equal by ==; Rust may spell 95 as 95.0
                decisions.add(m.group(1))
            if on["transport"]:
                started += 1
            for line in bad_drive:
                m = LINE.match(line)
                self.assertTrue(m, line)
                self.assertNotIn("WRONG", m.group(2), line)   # the Python column is never the corrupted answer
                if m.group(6) == "1":
                    self.assertEqual((m.group(5), m.group(2)), ("python-fallback", m.group(4)), line)   # the selected text is Python's, character for character
        self.assertGreater(started, 20)
        for name in ("pick", "route", "transport_", "zip_args", "display", "plan"):   # engine, confirmed and rotation sit behind the transport this harness replaces
            self.assertTrue(any(d == name or d.startswith(name) for d in decisions), (name, decisions))

    def test_corrupted_answers_are_reported_as_disagreements(self):
        found = 0
        for case in range(40):
            _, drive_lines, _ = self.run_case(20261031 + case, True, True)
            found += sum(1 for x in drive_lines if x.endswith("disagreement=1"))
        self.assertGreater(found, 20)


if __name__ == "__main__":
    unittest.main()
