"""relay_msg_shadow: the log-only comparison of the host's relay messages (playback_tick, state, queue, chat) with the Rust singws-relaymsg helper.
The message the app built is always the one sent. Real code under test: the app's `_publish_host_state_ws` (through tools/relay_msg_parity.py's stub app,
whose message helpers are the app's own methods), the production snapshot code and comparator in relay_msg_shadow.py. No network."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import copy
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import relay_msg_shadow as rs

CLI = os.environ.get("SINGWS_RELAYMSG_BIN") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-relay")), "release", "singws-relaymsg")
SECRET = "SUPER-SECRET-NAME-123"
_PARITY = []


def parity():
    """tools/relay_msg_parity.py as a module (it loads the app once and provides the stub app)."""
    if not _PARITY:
        spec = importlib.util.spec_from_file_location("relay_msg_parity_for_tests", str(Path(__file__).resolve().parent / "tools" / "relay_msg_parity.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _PARITY.append(mod)
    return _PARITY[0]


def fake_helper(tmp, reply, exit_code=0):
    path = Path(tmp) / "singws-relaymsg"
    path.write_text("#!/bin/sh\ncat >/dev/null\ncat <<'EOF'\n" + json.dumps(reply) + "\nEOF\nexit " + str(exit_code) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def reset():
    rs._seen.clear()
    rs._last_started = 0.0
    rs._helper_cache[:] = []


class Compare(unittest.TestCase):
    MSG = {"type": "chat", "user": "u", "chat": {"host": [{"key": "a", "at": 5}, {"key": "b", "at": 5}], "x": 1}, "updated_at": 3}

    def text(self, m=None):
        return json.dumps(m or self.MSG, ensure_ascii=False)

    def test_identical_is_none(self):
        self.assertIsNone(rs.compare("chat", self.text(), json.loads(self.text())))

    def test_unsupported_is_never_a_mismatch(self):
        self.assertIsNone(rs.compare("chat", self.text(), {"unsupported": "x"}))

    def test_a_non_object_answer_is_reported(self):
        self.assertIn("not an object", rs.compare("chat", self.text(), [1]))

    def test_difference_names_the_path_and_never_the_content(self):
        other = copy.deepcopy(self.MSG)
        other["chat"]["x"] = SECRET
        mine = dict(self.MSG, chat=dict(self.MSG["chat"], x=SECRET.lower()))
        d = rs.compare("chat", self.text(mine), other)
        self.assertTrue(d.startswith("chat at $.chat.x: app=len="))
        self.assertNotIn(SECRET, d)
        self.assertNotIn(SECRET.lower(), d)

    def test_value_types_matter(self):
        for a, b in ((1, 1.0), (1, True), (0, False), ("1", 1)):
            m1, m2 = {"type": "state", "v": a}, {"type": "state", "v": b}
            self.assertIsNotNone(rs.compare("state", json.dumps(m1), m2), (a, b))

    def test_key_order_and_key_set_matter(self):
        self.assertIsNotNone(rs.compare("state", '{"a":1,"b":2}', {"b": 2, "a": 1}))
        self.assertIsNotNone(rs.compare("state", '{"a":1}', {"a": 1, "b": 2}))

    def test_list_length_and_element_differences(self):
        self.assertIn(".<len>", rs.compare("state", '{"l":[1,2]}', {"l": [1, 2, 3]}))
        self.assertIn("$.l[1]", rs.compare("state", '{"l":[1,2]}', {"l": [1, 3]}))

    def test_host_threads_with_the_same_time_may_come_in_either_order(self):
        swapped = copy.deepcopy(self.MSG)
        swapped["chat"]["host"].reverse()
        self.assertIsNone(rs.compare("chat", self.text(), swapped))
        later = copy.deepcopy(self.MSG)
        later["chat"]["host"][0]["at"] = 9
        self.assertIsNotNone(rs.compare("chat", self.text(), later))

    def test_only_the_chat_host_list_is_reordered(self):
        a = {"type": "queue", "chat": {"host": [{"key": "b", "at": 5}, {"key": "a", "at": 5}]}}
        b = {"type": "queue", "chat": {"host": [{"key": "a", "at": 5}, {"key": "b", "at": 5}]}}
        self.assertIsNotNone(rs.compare("queue", json.dumps(a), b))


class Runner(unittest.TestCase):
    def setUp(self):
        reset()
        self.tmp = tempfile.mkdtemp()
        self.log = []
        self.batch = [("state", {"op": "state"}, '{"type":"state","a":1}')]

    def run_with(self, reply, code=0, batch=None):
        return rs.run_shadow(batch or self.batch, self.log.append, helper=fake_helper(self.tmp, reply, code))

    def test_clean(self):
        self.assertEqual(self.run_with([{"type": "state", "a": 1}]), "clean")
        self.assertEqual(self.log, [])

    def test_mismatch_is_logged_once_per_distinct_difference(self):
        for _ in range(3):
            self.assertEqual(self.run_with([{"type": "state", "a": 2}]), "mismatch")
        self.assertEqual(len(self.log), 1)
        self.assertTrue(self.log[0].startswith("[RELAY-MSG-SHADOW] MISMATCH state at $.a: app=len="))

    def test_the_log_is_capped_per_session(self):
        for i in range(rs.MAX_LOGGED + 5):
            self.run_with([{"type": "state", "a": i + 10}], batch=[("state", {}, '{"type":"state","a":1}')])
        mism = [l for l in self.log if "MISMATCH" in l]
        self.assertEqual(len(mism), rs.MAX_LOGGED)
        self.assertEqual(sum("suppressed" in l for l in self.log), 1)

    def test_helper_failure_is_skipped_with_a_line(self):
        self.assertEqual(self.run_with([], code=3), "skipped")
        self.assertTrue(self.log[0].startswith("[RELAY-MSG-SHADOW] skipped: RuntimeError"))

    def test_a_nonzero_exit_is_a_failure_even_with_a_good_answer(self):
        self.assertEqual(self.run_with([{"type": "state", "a": 1}], code=3), "skipped")
        self.assertIn("exit status 3", self.log[0])

    def test_wrong_answer_count_is_skipped(self):
        self.assertEqual(self.run_with([{}, {}]), "skipped")

    def test_garbage_output_is_skipped(self):
        p = Path(self.tmp) / "singws-relaymsg"
        p.write_text("#!/bin/sh\ncat >/dev/null\necho not-json\n")
        p.chmod(0o755)
        self.assertEqual(rs.run_shadow(self.batch, self.log.append, helper=p), "skipped")

    def test_no_helper_or_empty_batch_is_silent(self):
        reset()
        rs._helper_cache[:] = [None]
        self.assertEqual(rs.run_shadow(self.batch, self.log.append), "skipped")
        self.assertEqual(rs.run_shadow([], self.log.append, helper=fake_helper(self.tmp, [])), "skipped")
        self.assertEqual(self.log, [])

    def test_a_failing_logger_never_raises(self):
        def boom(_t):
            raise RuntimeError("x")
        self.assertEqual(rs.run_shadow(self.batch, boom, helper=fake_helper(self.tmp, [], 3)), "skipped")

    def test_the_helper_runs_at_low_priority(self):
        p = Path(self.tmp) / "singws-relaymsg"
        p.write_text("#!/bin/sh\ncat >/dev/null\necho \"[{\\\"nice\\\": $(ps -o nice= -p $$ | tr -d ' ')}]\"\n")
        p.chmod(0o755)
        self.assertEqual(rs.run_helper(p, [{}])[0]["nice"] >= 10, True)


class Constants(unittest.TestCase):
    def test_pinned_values(self):
        self.assertEqual(rs.INTERVAL_S, 30.0)
        self.assertEqual((rs.MAX_LOGGED, rs.MAX_CHAT_ROWS, rs.MAX_REMOVED), (20, 3000, 500))

    def test_the_app_default_is_on(self):
        self.assertIs(parity().APP.DEFAULTS["relay_msg_shadow"], True)


class Throttle(unittest.TestCase):
    def setUp(self):
        reset()
        self.app = types_ns(settings={"relay_msg_shadow": True})

    def test_disabled_does_nothing(self):
        rs._helper_cache[:] = [Path("/bin/sh")]
        self.assertIsNone(rs.Recorder.start(types_ns(settings={"relay_msg_shadow": False}), now=100.0))

    def test_default_is_on(self):
        self.assertTrue(rs.enabled({}))
        self.assertFalse(rs.enabled({"relay_msg_shadow": False}))

    def test_no_helper_does_nothing_and_is_looked_up_once(self):
        calls = []
        orig = rs.find_helper
        rs.find_helper = lambda: calls.append(1)
        try:
            self.assertIsNone(rs.Recorder.start(self.app, now=100.0))
            self.assertIsNone(rs.Recorder.start(self.app, now=200.0))
        finally:
            rs.find_helper = orig
        self.assertEqual(len(calls), 1)

    def test_one_batch_per_interval(self):
        rs._helper_cache[:] = [Path("/bin/sh")]
        self.assertIsNotNone(rs.Recorder.start(self.app, now=100.0))
        self.assertIsNone(rs.Recorder.start(self.app, now=100.0 + rs.INTERVAL_S - 1))
        self.assertIsNotNone(rs.Recorder.start(self.app, now=100.0 + rs.INTERVAL_S))

    def test_find_helper_honours_the_override(self):
        os.environ["SINGWS_RELAYMSG_BIN"] = "/bin/sh"
        try:
            self.assertEqual(rs.find_helper(), Path("/bin/sh"))
        finally:
            os.environ.pop("SINGWS_RELAYMSG_BIN")


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


class Wrap(unittest.TestCase):
    class W:
        def __init__(self):
            self.sent, self.flag = [], "real"

        def send_json(self, msg):
            if msg.get("boom"):
                raise RuntimeError("send failed")
            self.sent.append(msg)
            return "sent"

    def test_forwards_first_records_after_and_passes_everything_else_through(self):
        real = self.W()
        rec = rs.Recorder(types_ns(settings={}))
        w = rec.wrap(real)
        self.assertEqual(w.send_json({"a": 1}), "sent")
        self.assertEqual((real.sent, rec.sent, w.flag), ([{"a": 1}], [{"a": 1}], "real"))

    def test_a_failed_send_propagates_and_is_not_recorded(self):
        real = self.W()
        rec = rs.Recorder(types_ns(settings={}))
        with self.assertRaises(RuntimeError):
            rec.wrap(real).send_json({"boom": 1})
        self.assertEqual(rec.sent, [])

    def test_the_recorded_message_is_the_one_sent(self):
        real = self.W()
        rec = rs.Recorder(types_ns(settings={}))
        msg = {"x": 1}
        rec.wrap(real).send_json(msg)
        self.assertIs(rec.sent[0], msg)
        self.assertIs(real.sent[0], msg)


class Hook(unittest.TestCase):
    """The REAL `_publish_host_state_ws`: the messages sent are the same with the shadow on, off, failing or finding a difference."""

    def setUp(self):
        reset()
        self.p = parity()
        self.tmp = tempfile.mkdtemp()
        self.log = []
        self.sent_batches = []

    def case(self, **extra):
        rng = __import__("random").Random(3)
        c = self.p.random_case(rng, 0)
        c.update(extra)
        c.pop("label", None)
        for singer in c["queue"]:  # mint the ids once, so every publish of this case sees the same queue
            if isinstance(singer, dict):
                self.p.KA._ensure_singer_id(singer)
                for e in singer.get("songs", []):
                    self.p.KA._ensure_queue_entry_id(e)
        return c

    def publish(self, enabled, helper=None, sync=True):
        kw = copy.deepcopy(self._case) if hasattr(self, "_case") else self.case()
        self._case = copy.deepcopy(kw)
        kw["settings"] = dict(kw["settings"], relay_msg_shadow=enabled)
        app = self.p.make_app(**kw)
        orig = rs.finish
        if sync:
            rs.finish = lambda rec, state, version, log: self.sent_batches.append(rs.run_shadow(rec.requests(state, version), self.log.append, helper=helper) if rec else None)
        rs._helper_cache[:] = [helper or Path("/nonexistent")]
        rs._last_started = 0.0
        try:
            app._publish_host_state_ws(force=True)
        finally:
            rs.finish = orig
        return app.host_relay_worker.sent

    @staticmethod
    def stable(msgs):
        out = copy.deepcopy(msgs)
        for m in out:
            m.pop("updated_at", None)
        return out

    def test_sent_messages_do_not_depend_on_the_shadow(self):
        off = self.publish(False)
        reply = [{"unsupported": "x"}] * 4
        on = self.publish(True, helper=fake_helper(self.tmp, reply))
        failing = self.publish(True, helper=fake_helper(self.tmp, [], 5))
        self.assertEqual(self.stable(off), self.stable(on))
        self.assertEqual(self.stable(off), self.stable(failing))
        self.assertEqual([m["type"] for m in off], ["playback_tick", "state", "queue", "chat"])

    def test_off_never_starts_a_recorder(self):
        self.publish(False, helper=fake_helper(self.tmp, []))
        self.assertEqual(self.sent_batches, [])

    def test_a_difference_is_logged_and_the_message_is_still_sent_unchanged(self):
        kw = self.case()
        app = self.p.make_app(**kw)
        captured = {}
        orig = rs.finish
        rs.finish = lambda rec, state, version, log: captured.update(rec=rec, state=state, version=version)
        rs._helper_cache[:] = [Path("/x")]
        rs._last_started = 0.0
        try:
            app._publish_host_state_ws(force=True)
        finally:
            rs.finish = orig
        batch = captured["rec"].requests(captured["state"], captured["version"])
        bad = [{"type": "playback_tick", "position_seconds": -1}] + [{"unsupported": "x"}] * 3
        self.assertEqual(rs.run_shadow(batch, self.log.append, helper=fake_helper(self.tmp, bad)), "mismatch")
        self.assertTrue(any("MISMATCH playback_tick" in l for l in self.log))
        self.assertEqual([m["type"] for m in app.host_relay_worker.sent], ["playback_tick", "state", "queue", "chat"])

    def test_a_publish_that_raises_in_the_recorder_still_sends(self):
        orig = rs.Recorder.start
        rs.Recorder.start = classmethod(lambda cls, app, now=None, force=False: (_ for _ in ()).throw(RuntimeError("x")))
        try:
            sent = self.publish(True, helper=fake_helper(self.tmp, []), sync=False)
        finally:
            rs.Recorder.start = orig
        self.assertEqual(len(sent), 4)

    def test_finish_swallows_everything(self):
        class Bad(rs.Recorder):
            def requests(self, *a):
                raise RuntimeError("x")
        self.assertIsNone(rs.finish(Bad(types_ns(settings={})), {}, "1", self.log.append))
        self.assertIsNone(rs.finish(None, {}, "1", self.log.append))


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.p = parity()

    def app(self, **kw):
        base = dict(queue=[], settings={"user": "u"}, control={})
        base.update(kw)
        return self.p.make_app(**base)

    def test_queue_snapshot_resolves_ids_and_marks_non_dicts(self):
        a = self.app(queue=[{"name": "Ann", "songs": [{"artist": "A", "title": "T"}, ("p", 0)]}, "junk"])
        snap = rs.snapshot_queue(a)
        self.assertIsNone(snap[1])
        self.assertTrue(snap[0]["singer_id"] and snap[0]["songs"][0]["entry_id"].startswith("local:"))
        self.assertEqual([s["is_dict"] for s in snap[0]["songs"]], [True, False])
        self.assertEqual(snap[0]["songs"][1]["entry_id"], "")

    def test_only_the_rows_the_summary_emits_are_resolved(self):
        calls = []
        a = self.app(queue=[{"name": f"s{i}", "songs": [{"artist": "A", "title": "T"} for _ in range(30)]} for i in range(210)])
        a._queue_entry_artist_title = lambda e: (calls.append(1), ("x", "y"))[1]
        snap = rs.snapshot_queue(a)
        self.assertEqual(len(calls), 200 * 25)
        self.assertEqual(snap[205]["songs"][0]["artist"], "")
        self.assertEqual(snap[0]["songs"][29]["artist"], "")

    def test_a_failing_lookup_gives_empty_strings_like_the_summary(self):
        a = self.app(queue=[{"name": "s", "songs": [{}]}])
        a._queue_entry_artist_title = lambda e: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(rs.snapshot_queue(a)[0]["songs"][0]["artist"], "")

    def test_waitlist_snapshot_is_best_effort(self):
        a = self.app(sections=RuntimeError("x"), removed={"a": {"removed_at": 1, "explode": True}, "b": "no"})
        snap = rs.snapshot_waitlist(a)
        self.assertEqual(snap["rows"], [])
        self.assertEqual(snap["removed"], [{"removed_at": 1, "row": None}])

    def test_too_many_host_chat_texts_or_removed_rows_are_skipped(self):
        with self.assertRaises(rs.Unsnapshottable):
            rs.snapshot_chat(self.app(chat=[{"id": i} for i in range(rs.MAX_CHAT_ROWS + 1)]))
        with self.assertRaises(rs.Unsnapshottable):
            rs.snapshot_waitlist(self.app(removed={str(i): {"removed_at": i} for i in range(rs.MAX_REMOVED + 1)}))
        rs.snapshot_waitlist(self.app(removed={str(i): {"removed_at": i} for i in range(rs.MAX_REMOVED)}))

    def test_the_last_waitlist_section_wins(self):
        a = self.app(sections=[{"key": "waitlist", "rows": [{"request_id": 1}]}, {"key": "waitlist", "rows": [{"request_id": 2}, {"request_id": 3}]}])
        self.assertEqual([r["request_id"] for r in rs.snapshot_waitlist(a)["rows"]], [2, 3])

    def test_too_many_chat_rows_are_skipped(self):
        a = self.app(room={i: {"id": i, "channel": "group"} for i in range(rs.MAX_CHAT_ROWS + 1)})
        with self.assertRaises(rs.Unsnapshottable):
            rs.snapshot_chat(a)

    def test_non_plain_data_is_skipped(self):
        a = self.app(room={1: {"id": 1, "channel": "group", "message": object()}})
        with self.assertRaises(rs.Unsnapshottable):
            rs.snapshot_chat(a)

    def test_the_fold_table_covers_names_and_thread_halves(self):
        a = self.app(room={1: {"id": 1, "channel": "dm", "from_key": "@host", "thread": "Straße|ǅ", "to_key": ""}}, chat=[{"singer": "İ"}])
        fold = rs.snapshot_chat(a)["fold"]
        self.assertEqual(fold["Straße"], ["strasse", "Straße"])
        self.assertEqual(rs.snapshot_chat(self.app(chat=[{"singer": "ann bob"}]))["fold"]["ann bob"], ["ann bob", "Ann Bob"])
        self.assertIn("ǅ", fold)
        self.assertIn("İ", fold)
        self.assertIn("Straße|ǅ", fold)


@unittest.skipUnless(os.path.isfile(CLI), "singws-relaymsg is not built (cd rust && cargo build --release -p singws-relaymsg)")
class RealHelper(unittest.TestCase):
    def test_the_real_helper_agrees_on_a_publish(self):
        reset()
        p = parity()
        before = dict(p.counts)
        p.failures.clear()
        rng = __import__("random").Random(11)
        for i in range(25):
            p.run_case(**p.random_case(rng, i))
        self.assertEqual(p.failures, [])
        self.assertGreater(p.counts.get("identical", 0) - before.get("identical", 0), 50)

    def test_the_cli_exits_2_on_bad_json(self):
        import subprocess
        self.assertEqual(subprocess.run([CLI], input=b"{", capture_output=True).returncode, 2)

    def test_the_cli_reports_unsupported_not_a_crash(self):
        out = rs.run_helper(Path(CLI), [{"op": "nope"}, {"op": "chat", "chat": {"rooms": [["x", {}]]}}])
        self.assertTrue(all("unsupported" in o for o in out))


class AnnounceAndFirstClean(unittest.TestCase):
    def test_announce_says_whether_it_is_on_and_whether_the_helper_is_there(self):
        from unittest import mock
        lines = []
        with mock.patch.object(rs, "_helper", return_value=None):
            self.assertEqual(rs.announce({"relay_msg_shadow": True}, lines.append), "enabled available=0")
        with mock.patch.object(rs, "_helper", return_value=Path("/x/h")):
            self.assertEqual(rs.announce({"relay_msg_shadow": True}, lines.append), "enabled available=1")
        self.assertEqual(rs.announce({"relay_msg_shadow": False}, lines.append), "off (relay_msg_shadow=false)")
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(x.startswith("[RELAY-MSG-SHADOW] ") for x in lines))

    def test_the_first_clean_batch_is_noted_once(self):
        from unittest import mock
        rs._clean_noted = False
        lines = []
        batch = [("queue", {"x": 1}, "e")]
        with mock.patch.object(rs, "run_helper", return_value=["e"]), mock.patch.object(rs, "compare", return_value=None):
            self.assertEqual(rs.run_shadow(batch, lines.append, helper=Path("/x/h")), "clean")
            self.assertEqual(rs.run_shadow(batch, lines.append, helper=Path("/x/h")), "clean")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("[RELAY-MSG-SHADOW] first check clean"))


if __name__ == "__main__":
    unittest.main()
