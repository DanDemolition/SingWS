"""Stage B M2: queue_state_shadow (log-only). Python's queue is always the one used; the Rust helper is asked to reproduce each checked reconcile from the
pre-state the app had, and a structural difference is logged. Needs the helper built (see test_queue_state_parity.py)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import queue_event_replay as qr  # noqa: E402

qr.load_app_module()
import queue_state_shadow as qss  # noqa: E402

try:
    BIN = qr.find_rust_binary()
except FileNotFoundError:
    BIN = None


def reset():
    qss._seen.clear()
    qss._last_started = 0.0
    qss._clean_noted = False
    qss._helper_cache.clear()


class _Env:
    def __enter__(self):
        reset()
        self.p = mock.patch.dict("os.environ", {"SINGWS_QUEUE_STATE_BIN": str(BIN or "")})
        self.p.start()
        return self

    def __exit__(self, *a):
        self.p.stop()
        reset()


class GateTests(unittest.TestCase):
    def test_announce_lines(self):
        out = []
        with mock.patch.object(qss, "find_helper", return_value=None):
            qss.announce({"queue_state_shadow": True}, out.append)
        with mock.patch.object(qss, "find_helper", return_value=Path("/x")):
            qss.announce({"queue_state_shadow": True}, out.append)
        qss.announce({"queue_state_shadow": False}, out.append)
        self.assertEqual(out, ["[QUEUE-STATE-SHADOW] enabled available=0", "[QUEUE-STATE-SHADOW] enabled available=1", "[QUEUE-STATE-SHADOW] off (queue_state_shadow=false)"])

    def test_default_setting_is_on(self):
        self.assertIn('"queue_state_shadow": True', (Path(__file__).parent / "0.2.18.1.py").read_text())
        self.assertTrue(qss.enabled({}))
        self.assertFalse(qss.enabled({"queue_state_shadow": False}))

    def test_rows_outside_the_model_are_not_checked(self):
        base = qr._req(1, "Ada", "Alpha")
        for bad in (dict(base, state="waiting"), dict(base, sort_order=3), dict(base, singer="Ada & Bo"), dict(base, selected_source="karafun"),
                    dict(base, removed_at=5), dict(base, delivered=True)):
            with self.assertRaises(qss.Unsnapshottable):
                qss.check_rows([bad])
        qss.check_rows([base])

    def test_differences_never_show_values(self):
        d = qss.first_difference({"queue": [{"name": "Secret Name"}]}, {"queue": [{"name": "Other"}]})
        self.assertNotIn("Secret", json.dumps(d))
        self.assertEqual(qss.first_difference({"a": [1, 2]}, {"a": [1]}), ("$.a", "len=2", "len=1"))
        self.assertIsNone(qss.first_difference({"a": 1}, {"a": 1}))


@unittest.skipUnless(BIN, "singws-queue-state is not built")
class LiveCheckTests(unittest.TestCase):
    def reconcile_checked(self, world, rows, log):
        now = world.clock + 1.0
        rec = qss.begin(world.app, rows, now=now, force=True)
        world.apply({"op": "remote_arrive", "requests": rows})
        thread = qss.finish(rec, world.app, log.append, wait=True)
        return rec, thread

    def test_core_streams_check_clean_at_every_reconcile_even_after_unmodelled_edits(self):
        with _Env():
            checked = 0
            for seed in range(8):
                log = []
                stream = qr.generate_stream(seed, 40, "core")
                world = qr.World(queue_mode=stream[0].get("queue_mode", "rotation"), limit=stream[0].get("limit_pending_max", 20))
                for ev in stream:
                    if ev["op"] == "remote_arrive":
                        rec, _t = self.reconcile_checked(world, ev["requests"], log)
                        checked += rec is not None
                    else:
                        world.apply(ev)  # includes restarts, moves, removals: the next check resynchronises
                self.assertFalse([l for l in log if "MISMATCH" in l or "skipped" in l], (seed, log))
            self.assertGreater(checked, 20)

    def test_first_clean_check_is_announced_once(self):
        with _Env():
            log = []
            world = qr.World()
            self.reconcile_checked(world, [qr._req(1, "Ada", "Alpha")], log)
            self.reconcile_checked(world, [qr._req(2, "Bob", "Bravo")], log)
            self.assertEqual([l for l in log if "first check clean" in l].__len__(), 1)

    def test_a_deliberately_broken_reconcile_is_reported_without_names(self):
        with _Env():
            log = []
            world = qr.World()
            world.apply({"op": "remote_arrive", "requests": [qr._req(1, "Ada", "Alpha")]})
            world.apply({"op": "remove_song", "entry": "R1"})
            rows = [qr._req(1, "Ada", "Alpha")]
            rec = qss.begin(world.app, rows, now=world.clock + 1.0, force=True)
            with mock.patch.object(type(world.app), "_tombstone_signature_conflicts", lambda self, *a, **k: True):
                world.apply({"op": "remote_arrive", "requests": rows})
            qss.finish(rec, world.app, log.append, wait=True)
            mism = [l for l in log if "MISMATCH" in l]
            self.assertTrue(mism, log)
            self.assertNotIn("Ada", mism[0])
            self.assertNotIn("Alpha", mism[0])

    def test_waitlist_activity_during_the_reconcile_skips_the_check(self):
        with _Env():
            log = []
            world = qr.World(waitlist=False)
            rec = qss.begin(world.app, [qr._req(1, "Ada", "Alpha")], now=world.clock + 1.0, force=True)
            world.app._waiting_for_add_requests[99] = {"singer": "x"}
            self.assertIsNone(qss.finish(rec, world.app, log.append, wait=True))
            self.assertEqual(log, [])

    def test_rate_limit_and_waitlist_setting_gate_begin(self):
        with _Env():
            world = qr.World()
            world.app.settings["queue_state_shadow"] = True
            self.assertIsNotNone(qss.begin(world.app, [qr._req(1, "Ada", "A")]))
            self.assertIsNone(qss.begin(world.app, [qr._req(2, "Ada", "B")]))  # inside the 30 s interval
            reset()
            world.app.settings["use_waiting_for_add"] = True
            self.assertIsNone(qss.begin(world.app, [qr._req(3, "Ada", "C")]))


class DecoratorTests(unittest.TestCase):
    def test_hook_never_changes_the_reconcile_result_or_exceptions(self):
        world = qr.World()
        app = world.app
        with mock.patch.object(qss, "begin", side_effect=RuntimeError("boom")):
            app._reconcile_remote_requests([qr._req(1, "Ada", "Alpha")])
        self.assertEqual(len(app.queue), 1)
        with mock.patch.object(qss, "begin", return_value=None):
            with mock.patch.object(type(app), "_prune_expired_empty_rotation_slots", side_effect=KeyError("x")):
                app._reconcile_remote_requests([qr._req(2, "Bob", "Bravo")])  # swallowed by the app's own try/except, as before
        self.assertEqual(len(app.queue), 2)

    def test_source_guard_hook_is_a_decorator_only(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn("    @_queue_state_shadowed\n    def _reconcile_remote_requests(", src)


if __name__ == "__main__":
    unittest.main()
