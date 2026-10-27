"""The read-only waitlist snapshot the Rust shell's Waitlist page draws from.

The app writes `waitlist_snapshot.json` (setting `waitlist_snapshot`, default off) whenever the waiting-for-add view would refresh. These tests drive the REAL
`KaraokeApp` methods over random waiting / pending / removed requests and queues and check:
* the setting off writes nothing and creates no timer; on, one coalesced timer writes one file;
* the sections Rust builds from the snapshot (`singws-waitlist-cli`, op `sections`) equal the app's own `_waiting_for_add_sections` row for row, text included;
* the app never reads the file back, and a failure while writing can never reach the app's own refresh.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import os
import random
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_show_critical_regressions as base

CLI = os.environ.get("SINGWS_WAITLIST_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-ui"), "release", "singws-waitlist-cli")
ROW_FIELDS = ["request_id", "singer", "artist", "title", "version", "duration", "first_time_singer", "request_time", "server_received_time", "queue_position", "status_kind", "status_label", "selectable"]
NAMES = ["Ann", "Bo", "Cy Duet & Di", "ann", "Éva", " Fay ", "Gus"]


class WaitlistSnapshot(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()

    def make_app(self, waiting, removed, deferred, queue, defer_on, show_removed, enabled=True, inflight=(), handled=()):
        app = self.bare_app()
        app.settings = {"waitlist_snapshot": enabled, "defer_remote_adds_until_between_singers": defer_on}
        app.queue = queue
        app._waiting_for_add_requests = waiting
        app._waiting_for_add_removed_tonight = removed
        app._waiting_for_add_show_removed = show_removed
        app._deferred_remote_adds = deferred
        app._waiting_for_add_handled_ids = set(handled)
        app._remote_request_intake_inflight = set(inflight)
        return app

    def req(self, rng, rid):
        r = {"request_id": rid, "singer": rng.choice(NAMES), "artist": rng.choice(["Queen", "ABBA", ""]), "title": rng.choice(["Bohemian", "SOS", "x"]),
             "request_time": rng.choice([1792000000, "2026-10-26T20:15:00Z", 1792000900.5, None]), "duration_secs": rng.choice([0, 200, 245.5])}
        if rng.random() < 0.3:
            r["received_at_server"] = rng.choice([1792000100, "2026-10-26T20:16:00+00:00"])
        return r

    def random_state(self, rng):
        ids = rng.sample(range(1, 40), 14)
        waiting = {str(i): self.req(rng, i) for i in ids[:rng.randint(0, 6)]}
        removed = {str(i): dict(self.req(rng, i), removed_at=rng.choice([100, 300, 250.5])) for i in ids[6:6 + rng.randint(0, 3)]}
        deferred = []
        for i in ids[9:9 + rng.randint(0, 3)]:
            item = {"request_id": i, "singer": rng.choice(NAMES), "artist": "Queen", "title": "Radio", "deferred_at": 1792000500}
            if rng.random() < 0.5:
                item["remote_meta"] = {"singer": rng.choice(NAMES), "artist": "ABBA"}
            deferred.append(item)
        if deferred and rng.random() < 0.5:      # a deferred add that is also still in the waiting list
            waiting[str(deferred[0]["request_id"])] = self.req(rng, deferred[0]["request_id"])
        queue = []
        for n, name in enumerate(rng.sample(NAMES, rng.randint(0, 4))):
            songs = [{"title": "s", "remote_request_id": rng.choice(list(waiting) + [None, "77", "x"]) if rng.random() < 0.6 and waiting else None} for _ in range(rng.randint(0, 2))]
            queue.append({"name": name, "has_sung": rng.random() < 0.4, "songs": songs})
        return waiting, removed, deferred, queue

    def app_sections(self, app):
        return [{"key": s["key"], "title": s["title"], "helper": s.get("helper"),
                 "rows": [dict({k: r[k] for k in ROW_FIELDS if k in r}, text=app._waiting_for_add_row_text(r)) for r in s["rows"]]} for s in app._waiting_for_add_sections()]

    def rust_sections(self, snapshot):
        request = {k: v for k, v in snapshot.items() if k != "version" and not (k == "pending_acceptance" and v is None)}
        out = subprocess.run([CLI], input=json.dumps(dict(request, op="sections")), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[:300])
        return json.loads(out.stdout)

    def test_the_setting_is_off_by_default(self):
        self.assertIs(self.singws.DEFAULTS.get("waitlist_snapshot"), False)

    def test_with_the_setting_off_nothing_is_written_and_no_timer_exists(self):
        app = self.make_app({}, {}, [], [], False, False, enabled=False)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.singws, "WAITLIST_SNAPSHOT_PATH", Path(d) / "waitlist_snapshot.json"):
            app._schedule_waitlist_snapshot()
            self.assertNotIn("_waitlist_snapshot_timer", app.__dict__)
            self.assertFalse(app._write_waitlist_snapshot())
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_a_burst_of_changes_shares_one_timer_and_writes_the_file(self):
        app = self.make_app({"5": {"request_id": 5, "singer": "Ann", "artist": "A", "title": "T"}}, {}, [], [], False, False)
        timers = []

        class FakeTimer:
            def __init__(self, *_a):
                self.timeout = mock.MagicMock()
                self.starts = []
                timers.append(self)

            def setSingleShot(self, v):
                pass

            def start(self, ms):
                self.starts.append(ms)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.singws, "WAITLIST_SNAPSHOT_PATH", Path(d) / "waitlist_snapshot.json"), mock.patch.object(self.singws, "QTimer", FakeTimer):
            for _ in range(5):
                app._schedule_waitlist_snapshot()
            self.assertEqual(len(timers), 1)
            self.assertEqual(timers[0].starts, [250] * 5)
            self.assertTrue(app._write_waitlist_snapshot())
            data = json.loads((Path(d) / "waitlist_snapshot.json").read_text())
        self.assertEqual((data["version"], list(data["waiting"]), data["pending_acceptance"], data["show_removed"]), (1, ["5"], None, False))

    def test_the_snapshot_draws_the_same_sections_as_the_app_over_random_states(self):
        if not Path(CLI).exists():
            self.skipTest("needs singws-waitlist-cli (cargo build --release -p singws-uimodel)")
        rng = random.Random(20261031)
        seen = {"pending_acceptance": 0, "waitlist": 0, "removed": 0, "first": 0, "hidden_by_queue": 0}
        for case in range(250):
            waiting, removed, deferred, queue = self.random_state(rng)
            defer_on = rng.random() < 0.6
            app = self.make_app(waiting, removed, deferred, queue, defer_on, rng.random() < 0.5, inflight=rng.sample(range(1, 40), 2), handled=rng.sample(range(1, 40), 2))
            expect = self.app_sections(app)
            snapshot = app._waitlist_snapshot_payload()
            json.dumps(snapshot)  # plain JSON
            got = self.rust_sections(snapshot)
            self.assertEqual(got, expect, (case, snapshot))
            for s in expect:
                seen[s["key"]] += 1
                seen["first"] += sum(1 for r in s["rows"] if r.get("first_time_singer"))
            seen["hidden_by_queue"] += len(waiting) - sum(len(s["rows"]) for s in expect if s["key"] == "waitlist")
        self.assertTrue(all(n > 20 for n in seen.values()), seen)

    def test_a_deferred_add_that_is_also_waiting_shows_only_under_pending_acceptance(self):
        if not Path(CLI).exists():
            self.skipTest("needs singws-waitlist-cli")
        item = {"request_id": 7, "singer": "Ann", "artist": "Queen", "title": "Radio", "deferred_at": 1792000500}
        app = self.make_app({"7": {"request_id": 7, "singer": "Ann", "artist": "Queen", "title": "Radio"}}, {}, [item], [], True, False)
        expect = self.app_sections(app)
        self.assertEqual([s["key"] for s in expect], ["pending_acceptance"])
        snapshot = app._waitlist_snapshot_payload()
        self.assertEqual((snapshot["accepted_ids"], snapshot["pending_accepted_ids"]), ([7], []))
        self.assertEqual(self.rust_sections(snapshot), expect)

    def test_a_write_that_fails_never_reaches_the_app(self):
        app = self.make_app({}, {}, [], [], False, False)
        with mock.patch.object(self.singws, "_save_json_atomic", side_effect=OSError("disk full")), mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            self.assertFalse(app._write_waitlist_snapshot())
        app._waitlist_snapshot_payload = lambda: (_ for _ in ()).throw(RuntimeError("payload broke"))
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            self.assertFalse(app._write_waitlist_snapshot())

    def test_the_refresh_scheduler_survives_a_broken_snapshot_hook(self):
        app = self.make_app({}, {}, [], [], False, False)
        app._update_waiting_for_add_nav_state = lambda: None
        app._schedule_waitlist_snapshot = lambda: (_ for _ in ()).throw(RuntimeError("hook broke"))
        app._run_on_ui_thread = lambda fn: fn()
        app.waiting_for_add_list = None
        app._schedule_waiting_for_add_view_refresh(reason="test")   # must not raise

    def test_the_scheduler_asks_for_a_snapshot_on_every_refresh_request(self):
        app = self.make_app({}, {}, [], [], False, False)
        calls = []
        app._update_waiting_for_add_nav_state = lambda: None
        app._schedule_waitlist_snapshot = lambda: calls.append(1)
        app.waiting_for_add_list = None
        app._schedule_waiting_for_add_view_refresh(reason="a")
        app._schedule_waiting_for_add_view_refresh(reason="b")
        self.assertEqual(len(calls), 2)

    def test_the_app_only_ever_writes_the_snapshot(self):
        source = (Path(__file__).parent / "0.2.18.1.py").read_text()
        uses = [m.start() for m in re.finditer(r"WAITLIST_SNAPSHOT_PATH", source)]
        self.assertEqual(len(uses), 2, "the definition and the one write")
        self.assertNotIn("_load_json_file(WAITLIST_SNAPSHOT_PATH", source)
        self.assertNotIn("WAITLIST_SNAPSHOT_PATH.read", source)


if __name__ == "__main__":
    unittest.main()
