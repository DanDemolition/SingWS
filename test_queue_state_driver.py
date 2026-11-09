"""queue_state_shadow's driver (setting queue_state_driver, default dry_run): it may only remove stale local copies of requests the host removed, through the host's own removal, and only
when the Rust queue rules do not keep them. dry_run says what it would do; off does nothing."""
import _test_profile  # noqa: F401
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import queue_state_shadow as qss  # noqa: E402


def post(queue_rids, removed=(), tombs=None):
    return {"queue": [{"name": "Ann", "songs": [{"rid": r} for r in queue_rids]}], "removed": list(removed), "tombstones": tombs or {}}


def rust(rids):
    return {"queue": [{"songs": [{"remote_request_id": r} for r in rids]}]}


class App:
    def __init__(self, rids, removed=(), tombs=None, playing_rid=None):
        self.queue = [{"name": "Ann", "songs": [{"rid": r, "id": f"e{r}"} for r in rids]}]
        self.__dict__["_remote_removed_request_ids"] = set(removed)
        self._tombs = tombs or {}
        self.karaoke_playing = playing_rid is not None
        self._current_karaoke_request_id = f"e{playing_rid}" if playing_rid else ""
        self.removed_calls = []
        self.ui = []

    def _ensure_remote_request_tombstones(self):
        return {"requests": {str(k): {"status": v} for k, v in self._tombs.items()}}

    def _queue_entry_remote_request_id(self, entry):
        return entry["rid"]

    def _ensure_queue_entry_id(self, entry):
        return entry["id"]

    def shell_queue_remove_song(self, entry_id):
        self.removed_calls.append(entry_id)
        for s in self.queue:
            s["songs"] = [e for e in s["songs"] if e["id"] != entry_id]
        return "removed"

    def _run_on_ui_thread(self, fn):
        self.ui.append(fn)


class GhostTests(unittest.TestCase):
    def test_a_request_the_host_removed_and_rust_does_not_keep_is_a_ghost(self):
        self.assertEqual(qss.host_removed_ghosts(post([1, 2, 3], removed=[2]), rust([1, 3])), [2])
        self.assertEqual(qss.host_removed_ghosts(post([1, 2], tombs={"2": {"status": "removed"}}), rust([1])), [2])

    def test_nothing_else_is_ever_a_ghost(self):
        self.assertEqual(qss.host_removed_ghosts(post([1, 2]), rust([1])), [])                                  # missing in Rust but never removed by the host
        self.assertEqual(qss.host_removed_ghosts(post([1, 2], removed=[2]), rust([1, 2])), [])                  # Rust keeps it too
        self.assertEqual(qss.host_removed_ghosts(post([1], removed=[2]), rust([1])), [])                        # not in the app's queue
        self.assertEqual(qss.host_removed_ghosts(post([1], tombs={"1": {"status": "completed"}}), rust([])), [])  # a completed request is not a host removal


class ModeTests(unittest.TestCase):
    def test_default_is_off_and_odd_values_are_off(self):
        self.assertEqual(qss.driver_mode({}), "off")
        self.assertEqual(qss.driver_mode({"queue_state_driver": "APPLY"}), "apply")
        self.assertEqual(qss.driver_mode({"queue_state_driver": "dry_run"}), "dry_run")
        self.assertEqual(qss.driver_mode({"queue_state_driver": "yes"}), "off")
        self.assertIn('"queue_state_driver": "dry_run"', (Path(__file__).parent / "0.2.18.1.py").read_text())

    def test_off_and_dry_run_touch_nothing(self):
        app, out = App([1, 2], removed=[2]), []
        self.assertEqual(qss.drive(app, [2], "off", out.append), "nothing")
        self.assertEqual(qss.drive(app, [2], "dry_run", out.append), "dry_run")
        self.assertEqual((app.removed_calls, app.ui), ([], []))
        self.assertTrue(any("would remove 1" in l for l in out))

    def test_apply_schedules_on_the_ui_thread_and_then_removes_through_the_hosts_own_path(self):
        app, out = App([1, 2], removed=[2]), []
        self.assertEqual(qss.drive(app, [2], "apply", out.append), "scheduled")
        self.assertEqual(app.removed_calls, [])
        app.ui[0]()
        self.assertEqual(app.removed_calls, ["e2"])
        self.assertTrue(any("removed 1 stale" in l for l in out))

    def test_apply_rechecks_and_never_touches_the_song_that_is_playing(self):
        app, out = App([2], removed=[2], playing_rid=2), []
        qss.drive(app, [2], "apply", out.append)
        app.ui[0]()
        self.assertEqual(app.removed_calls, [])
        app2 = App([2], removed=[], tombs={})                       # the host's removal is gone by the time the UI thread runs: leave it
        qss.drive(app2, [2], "apply", out.append)
        app2.ui[0]()
        self.assertEqual(app2.removed_calls, [])

    def test_at_most_five_removals_per_check(self):
        app = App(list(range(1, 11)), removed=list(range(1, 11)))
        qss.drive(app, list(range(1, 11)), "apply", lambda m: None)
        app.ui[0]()
        self.assertEqual(len(app.removed_calls), 5)

    def test_a_failing_ui_thread_hook_never_raises(self):
        class Bad(App):
            def _run_on_ui_thread(self, fn):
                raise RuntimeError("no ui")
        self.assertEqual(qss.drive(Bad([2], removed=[2]), [2], "apply", lambda m: None), "failed")

    def test_announce_says_the_driver_mode_only_when_it_is_on(self):
        out = []
        qss.announce({"queue_state_shadow": True, "queue_state_driver": "dry_run"}, out.append)
        qss.announce({"queue_state_shadow": True}, out.append)
        self.assertTrue(out[0].endswith("driver=dry_run"))
        self.assertFalse(out[1].endswith("driver=dry_run"))


class WiringTests(unittest.TestCase):
    def test_run_check_hands_the_app_to_the_driver_only_on_a_mismatch(self):
        import json
        calls = []
        orig_helper, orig_drive = qss.run_helper, qss.drive
        try:
            qss.run_helper = lambda helper, pre, rows, now: {"result": {"ok": True}, "snapshot": {"queue": [{"name": "Ann", "skipped": False, "has_sung": False, "round_sung": False, "rotation_marker": False,
                                                                                                          "temporary_empty_slot": False, "songs": [{"remote_request_id": 1, "key": 0, "tempo_percent": None, "skipped": False}]}],
                                                                                    "tombstones": {}, "removed_ids": [], "handled_waitlist_ids": []}}
            qss.drive = lambda app, ghosts, mode, log: calls.append((ghosts, mode)) or "x"
            rec = qss.Recorder({}, [], (0, 0, 0, 0), 0.0)
            full = {"queue": [{"name": "Ann", "skipped": False, "has_sung": False, "round_sung": False, "rotation_marker": False, "temp_empty": False,
                               "songs": [{"rid": 1, "key": 0, "tempo": None, "skipped": False}, {"rid": 2, "key": 0, "tempo": None, "skipped": False}]}],
                    "tombstones": {}, "removed": [2], "handled": []}
            app = type("A", (), {})()
            app.settings = {"queue_state_driver": "dry_run"}
            self.assertEqual(qss.run_check(rec, full, lambda m: None, helper=Path("/x"), app=app), "mismatch")
            self.assertEqual(calls, [([2], "dry_run")])
            calls.clear()
            clean = dict(full, queue=[{**full["queue"][0], "songs": [full["queue"][0]["songs"][0]]}], removed=[])
            self.assertEqual(qss.run_check(rec, clean, lambda m: None, helper=Path("/x"), app=app), "clean")
            self.assertEqual(calls, [])
        finally:
            qss.run_helper, qss.drive = orig_helper, orig_drive


if __name__ == "__main__":
    unittest.main()
