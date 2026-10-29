"""`waitlist.remove` and `waitlist.restore`: the shell / phone doing what the "Waiting for add" page's Remove and Restore buttons do, by request id.

Real code under test: `_execute_shell_queue_command` (gating, validation, request-id replay), `shell_waitlist_change`, `_waitlist_remove_request`, `_waitlist_restore_request`
(the very bodies the two buttons now call), `_archive_waitlist_removed_tonight`, `_waiting_for_add_clearable_requests`. Recorded stand-ins: the server calls, the file save, the view refresh and the toast."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import random
import time
import types
import unittest
from pathlib import Path

import test_shell_add_song as ta
import test_shell_queue_commands as base


def harness(mode="apply", waiting=None, removed=None, local_ids=(), accepted=()):
    h = ta.AddHarness(base.random_queue(random.Random(5)))
    a = h.app
    a.settings.update({"shell_commands": mode})
    a._waiting_for_add_requests = dict(waiting or {})
    a._waiting_for_add_removed_tonight = dict(removed or {})
    a._remote_request_tombstones = {"requests": {}}
    for name in ("shell_waitlist_change", "_waitlist_remove_request", "_waitlist_restore_request", "_archive_waitlist_removed_tonight", "_waiting_for_add_clearable_requests",
                 "_waiting_for_add_request_id", "_waiting_for_add_order_key"):
        setattr(a, name, types.MethodType(getattr(h.m.KaraokeApp, name), a))
    a._queue_remote_request_ids = lambda: list(local_ids)
    a._accepted_request_identity_sets = lambda: (set(), set(), set())
    a._request_matches_accepted_queue_item = lambda req, identity_sets=None: a._waiting_for_add_request_id(req) in accepted
    a._save_waitlist_removed_tonight = lambda: h.record("save_removed")
    a._schedule_waiting_for_add_view_refresh = lambda delay_ms=None, reason="": h.record("view_refresh", reason)
    a._show_processing_notification = lambda text, level="info": h.record("toast", text)
    a._waiting_for_add_failed_add = lambda req: False
    a._notify_singer_waitlist_add_failed = lambda req: h.record("notify_failed")
    a._restore_waitlist_request_on_server_async = lambda req, revision: h.record("server_restore", a._waiting_for_add_request_id(req), revision)
    a._drop_remote_request_tombstone = lambda rid, reason="": h.record("drop_tombstone", rid, reason)
    a._delete_remote_request = lambda rid, entry=None, singer_name="", reason="": h.record("delete_remote", rid, singer_name, reason)
    return h


def run(h, action, args, rid):
    return h.app._execute_shell_queue_command(action, dict(args, _request_id=rid, _sent_at=time.time()))


def req(rid, singer="Ann"):
    return {"request_id": rid, "singer": singer, "artist": "Queen", "title": "Radio Ga Ga", "state": "waiting", "pending_reason": "rotation_full"}


class RemoveTests(unittest.TestCase):
    def test_remove_does_what_the_button_does(self):
        h = harness(waiting={7: req(7)})
        self.assertEqual(run(h, "waitlist.remove", {"request_id": 7}, "w1"), (True, "waitlist_removed"))
        a = h.app
        self.assertNotIn(7, a._waiting_for_add_requests)
        archived = a._waiting_for_add_removed_tonight[7]
        self.assertEqual((archived["state"], archived["removal_reason"], archived["_removed_tonight"]), ("removed", "waiting_for_add_cleared", True))
        self.assertIn(7, a._waiting_for_add_handled_ids)
        names = [c[0] for c in h.calls]
        self.assertIn("save_removed", names)
        self.assertEqual([c for c in h.calls if c[0] == "delete_remote"], [("delete_remote", 7, "Ann", "waiting_for_add_cleared")])
        self.assertEqual([c for c in h.calls if c[0] == "view_refresh"], [("view_refresh", "clear")])
        self.assertEqual(len(a._undo_stack), 1, "one undo step, like the button")

    def test_a_failed_add_still_tells_the_singer(self):
        h = harness(waiting={7: req(7)})
        h.app._waiting_for_add_failed_add = lambda r: True
        run(h, "waitlist.remove", {"request_id": 7}, "w1")
        self.assertIn(("notify_failed",), h.calls)

    def test_unknown_queued_or_accepted_requests_are_refused_and_nothing_changes(self):
        h = harness(waiting={7: req(7), 8: req(8), 9: req(9)}, local_ids=[8], accepted=[9])
        for n, rid in enumerate((99, 8, 9)):
            self.assertEqual(run(h, "waitlist.remove", {"request_id": rid}, f"u{n}"), (False, "unknown_request"), rid)
        self.assertEqual(sorted(h.app._waiting_for_add_requests), [7, 8, 9])
        self.assertEqual(h.app._waiting_for_add_removed_tonight, {})
        self.assertEqual([c for c in h.calls if c[0] in ("delete_remote", "save_removed")], [])

    def test_a_removed_request_cannot_be_removed_again(self):
        h = harness(waiting={7: req(7)})
        self.assertTrue(run(h, "waitlist.remove", {"request_id": 7}, "a")[0])
        self.assertEqual(run(h, "waitlist.remove", {"request_id": 7}, "b"), (False, "unknown_request"))
        self.assertEqual(len([c for c in h.calls if c[0] == "delete_remote"]), 1)


class RestoreTests(unittest.TestCase):
    def test_restore_puts_the_same_request_back_like_the_button(self):
        h = harness(waiting={7: req(7)})
        run(h, "waitlist.remove", {"request_id": 7}, "w1")
        h.calls.clear()
        self.assertEqual(run(h, "waitlist.restore", {"request_id": 7}, "w2"), (True, "waitlist_restored"))
        a = h.app
        self.assertEqual(a._waiting_for_add_removed_tonight, {})
        back = a._waiting_for_add_requests[7]
        self.assertEqual((back["state"], back["pending_status"], back["request_id"], back["singer"]), ("waiting", "Restored by host", 7, "Ann"))
        for gone in ("_removed_tonight", "removed_at", "removal_reason"):
            self.assertNotIn(gone, back)
        self.assertNotIn(7, a._waiting_for_add_handled_ids)
        self.assertEqual(a._queue_revision, 4)
        names = [c[0] for c in h.calls]
        self.assertEqual([n for n in names if n in ("drop_tombstone", "save_removed", "server_restore", "view_refresh", "toast")], ["drop_tombstone", "save_removed", "server_restore", "view_refresh", "toast"])
        self.assertEqual([c for c in h.calls if c[0] == "server_restore"], [("server_restore", 7, 4)])

    def test_only_requests_removed_tonight_can_be_restored(self):
        h = harness(waiting={7: req(7)}, removed={5: dict(req(5), _removed_tonight=True), 6: req(6)})
        self.assertEqual(run(h, "waitlist.restore", {"request_id": 7}, "a"), (False, "unknown_request"), "still waiting")
        self.assertEqual(run(h, "waitlist.restore", {"request_id": 99}, "b"), (False, "unknown_request"))
        self.assertEqual(run(h, "waitlist.restore", {"request_id": 6}, "c"), (False, "unknown_request"), "in the file but not flagged as removed tonight")
        self.assertEqual(sorted(h.app._waiting_for_add_removed_tonight), [5, 6])


class AddTests(unittest.TestCase):
    def harness(self, mode="apply", **kw):
        h = harness(mode, **kw)
        a = h.app
        a._waitlist_add_request = types.MethodType(h.m.KaraokeApp._waitlist_add_request, a)
        a._mark_waiting_for_add_delivered_async = lambda rid, r: h.record("delivered", rid)
        a.process_external_request = lambda r: (h.record("process_external", r.get("request_id")), self.outcome)[1]
        a._add_waiting_for_add_track = lambda r, track: (h.record("add_track", r.get("request_id"), track.get("title")), self.track_outcome)[1]
        self.outcome, self.track_outcome = True, True
        return h

    def test_add_from_the_library_does_what_the_button_does(self):
        h = self.harness(waiting={7: req(7), 8: req(8)})
        self.assertEqual(run(h, "waitlist.add", {"request_id": 7}, "a1"), (True, "waitlist_added"))
        a = h.app
        self.assertNotIn(7, a._waiting_for_add_requests)
        self.assertIn(8, a._waiting_for_add_requests)
        self.assertIn(7, a._waiting_for_add_handled_ids)
        self.assertEqual([c for c in h.calls if c[0] in ("process_external", "delivered", "view_refresh", "toast")],
                         [("process_external", 7), ("delivered", 7), ("view_refresh", "add_now"), ("toast", "Added waiting request to rotation.")])

    def test_a_replacement_track_is_added_through_the_replacement_path(self):
        r = dict(req(7), replacement_track={"title": "Other", "artist": "X"})
        h = self.harness(waiting={7: r})
        self.assertEqual(run(h, "waitlist.add", {"request_id": 7}, "a1"), (True, "waitlist_added"))
        self.assertEqual([c for c in h.calls if c[0] in ("add_track", "process_external")], [("add_track", 7, "Other")])

    def test_a_replacement_that_does_not_add_falls_back_to_the_library_like_the_button(self):
        r = dict(req(7), replacement_track={"title": "Other"})
        h = self.harness(waiting={7: r})
        self.track_outcome = False
        self.assertEqual(run(h, "waitlist.add", {"request_id": 7}, "a1"), (True, "waitlist_added"))
        self.assertEqual([c[0] for c in h.calls if c[0] in ("add_track", "process_external")], ["add_track", "process_external"])

    def test_a_song_that_cannot_be_added_stays_on_the_waitlist(self):
        h = self.harness(waiting={7: req(7)})
        self.outcome = False
        self.assertEqual(run(h, "waitlist.add", {"request_id": 7}, "a1"), (False, "not_added"))
        self.assertIn(7, h.app._waiting_for_add_requests)
        self.assertNotIn(7, h.app._waiting_for_add_handled_ids)
        self.assertEqual([c for c in h.calls if c[0] in ("delivered", "toast")], [])

    def test_a_replacement_error_is_reported_not_raised(self):
        h = self.harness(waiting={7: dict(req(7), replacement_track={"title": "Other"})})

        def boom(r, t):
            raise ValueError("Waiting request has no singer name.")

        h.app._add_waiting_for_add_track = boom
        self.assertEqual(run(h, "waitlist.add", {"request_id": 7}, "a1"), (False, "add_error"))
        self.assertIn(7, h.app._waiting_for_add_requests)

    def test_unknown_queued_or_accepted_requests_are_refused_and_dry_run_adds_nothing(self):
        h = self.harness(waiting={7: req(7), 8: req(8), 9: req(9)}, local_ids=[8], accepted=[9])
        for n, rid in enumerate((99, 8, 9)):
            self.assertEqual(run(h, "waitlist.add", {"request_id": rid}, f"u{n}"), (False, "unknown_request"), rid)
        h2 = self.harness("dry_run", waiting={7: req(7)})
        ok, msg = run(h2, "waitlist.add", {"request_id": 7}, "d")
        self.assertTrue(ok and msg.startswith("dry_run"), msg)
        self.assertEqual([c for c in h2.calls if c[0] in ("process_external", "add_track")], [])
        self.assertIn(7, h2.app._waiting_for_add_requests)
        self.assertEqual(run(self.harness("off", waiting={7: req(7)}), "waitlist.add", {"request_id": 7}, "o"), (False, "shell_commands_off"))

    def test_the_add_button_warns_with_the_right_words(self):
        warned = []
        for outcome, track_error, want in ((False, None, "That song could not be added from the local library."), (None, "no singer name", "no singer name")):
            h = self.harness(waiting={7: dict(req(7), replacement_track={"title": "O"}) if track_error else req(7)})
            a = h.app
            a._selected_waiting_for_add_request = lambda h=h: h.app._waiting_for_add_requests.get(7)
            self.outcome = bool(outcome)
            if track_error:
                def boom(r, t, msg=track_error):
                    raise ValueError(msg)
                a._add_waiting_for_add_track = boom
            saved = h.m.QMessageBox
            h.m.QMessageBox = types.SimpleNamespace(warning=lambda parent, title, text: warned.append((title, text)))
            try:
                h.m.KaraokeApp._add_selected_waiting_for_add(a)
            finally:
                h.m.QMessageBox = saved
            self.assertEqual(warned[-1], ("Waiting for Add", want))
            self.assertIn(7, a._waiting_for_add_requests)

    def test_the_add_button_shares_the_body_and_warns_on_failure(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index("    def _add_selected_waiting_for_add(self):")
        body = src[i:i + 900]
        self.assertIn("self._waitlist_add_request(req, rid)", body)
        self.assertIn("That song could not be added from the local library.", body)


class SummaryTests(unittest.TestCase):
    """`_shell_waitlist_summary`: the waitlist rows the web remote / phone page shows (published in the relay's `queue` message)."""

    def harness(self, **kw):
        h = harness(**kw)
        a = h.app
        for name in dir(h.m.KaraokeApp):
            if name.startswith(("_waiting_for_add_", "_pending_acceptance", "_format_waiting", "_is_waiting_for_add", "_remote_request_is_non_actionable")):
                setattr(a, name, types.MethodType(getattr(h.m.KaraokeApp, name), a))
        a._shell_waitlist_summary = types.MethodType(h.m.KaraokeApp._shell_waitlist_summary, a)
        a._is_first_time_waitlist_singer = lambda name: name == "Ann"
        a._deferred_remote_request_ids = lambda: set()
        return h

    def test_waiting_rows_follow_the_page_and_removed_rows_are_newest_first(self):
        removed = {5: dict(req(5, "Old"), _removed_tonight=True, removed_at=100), 6: dict(req(6, "New"), _removed_tonight=True, removed_at=300)}
        h = self.harness(waiting={7: req(7), 8: req(8, "Bo"), 9: req(9, "Cy"), 10: req(10, "Zed")}, removed=removed, local_ids=[8], accepted=[9])
        out = h.app._shell_waitlist_summary()
        self.assertEqual([(r["request_id"], r["first_time"]) for r in out["waiting"]], [(7, True), (10, False)], "queued (8) and accepted (9) requests are not on the page, so not here; only Ann is on a first turn")
        self.assertEqual(out["waiting"][0], {"request_id": 7, "singer": "Ann", "artist": "Queen", "title": "Radio Ga Ga", "status": "Waitlisted", "first_time": True, "section": "waitlist"})
        self.assertEqual([(r["request_id"], r["status"]) for r in out["removed"]], [(6, "Removed"), (5, "Removed")])

    def test_removed_rows_are_capped_at_twenty(self):
        removed = {i: dict(req(i), _removed_tonight=True, removed_at=i) for i in range(1, 31)}
        out = self.harness(removed=removed).app._shell_waitlist_summary()
        self.assertEqual((len(out["removed"]), out["removed"][0]["request_id"], out["removed"][-1]["request_id"]), (20, 30, 11))

    def test_it_is_capped_and_never_raises(self):
        h = self.harness(waiting={i: req(i, f"S{i}") for i in range(1, 60)})
        self.assertEqual(len(h.app._shell_waitlist_summary()["waiting"]), 40)
        h.app._waiting_for_add_sections = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        h.app._waiting_for_add_removed_tonight = "not a dict"
        self.assertEqual(h.app._shell_waitlist_summary(), {"waiting": [], "removed": []})

    def test_the_queue_message_carries_it(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index('worker.send_json({"type": "queue"')
        self.assertIn('"waitlist": self._shell_waitlist_summary()', src[i:i + 400])


class GatingTests(unittest.TestCase):
    def test_off_refuses_and_dry_run_checks_but_changes_nothing(self):
        for mode, want in (("off", (False, "shell_commands_off")),):
            h = harness(mode, waiting={7: req(7)})
            self.assertEqual(run(h, "waitlist.remove", {"request_id": 7}, "x"), want)
            self.assertIn(7, h.app._waiting_for_add_requests)
        h = harness("dry_run", waiting={7: req(7)}, removed={5: dict(req(5), _removed_tonight=True)})
        ok, msg = run(h, "waitlist.remove", {"request_id": 7}, "d1")
        self.assertTrue(ok and msg.startswith("dry_run"), msg)
        ok, msg = run(h, "waitlist.restore", {"request_id": 5}, "d2")
        self.assertTrue(ok and msg.startswith("dry_run"), msg)
        self.assertEqual(run(h, "waitlist.remove", {"request_id": 99}, "d3"), (False, "unknown_request"), "a dry run still checks the request exists")
        self.assertIn(7, h.app._waiting_for_add_requests)
        self.assertIn(5, h.app._waiting_for_add_removed_tonight)
        self.assertEqual([c for c in h.calls if c[0] in ("delete_remote", "save_removed", "server_restore")], [])

    def test_bad_request_ids_are_refused(self):
        h = harness(waiting={7: req(7)})
        for n, bad in enumerate((0, -1, "7", 7.0, True, None, [7])):
            self.assertEqual(run(h, "waitlist.remove", {"request_id": bad}, f"b{n}"), (False, "bad_request_id"), repr(bad))
        self.assertEqual(run(h, "waitlist.remove", {}, "b9"), (False, "bad_request_id"))
        self.assertIn(7, h.app._waiting_for_add_requests)

    def test_a_replayed_request_id_is_not_run_twice(self):
        h = harness(waiting={7: req(7), 8: req(8)})
        self.assertEqual(run(h, "waitlist.remove", {"request_id": 7}, "same"), (True, "waitlist_removed"))
        self.assertEqual(run(h, "waitlist.remove", {"request_id": 8}, "same"), (True, "duplicate: waitlist_removed"))
        self.assertIn(8, h.app._waiting_for_add_requests)

    def test_a_stale_queue_signature_is_refused_like_every_queue_command(self):
        h = harness(waiting={7: req(7)})
        self.assertEqual(run(h, "waitlist.remove", {"request_id": 7, "expect_queue_sig": "not-the-signature"}, "s"), (False, "stale_view"))
        self.assertIn(7, h.app._waiting_for_add_requests)

    def test_the_host_command_entry_points_route_waitlist_commands(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('startswith(("queue.", "library.", "bg.", "eq.", "waitlist.", "settings."))'), 3)
        i = src.index("    def _execute_host_control_command(self")
        self.assertIn('"waitlist."', src[i:i + 300])
        self.assertIn('"waitlist.remove", "waitlist.restore", "waitlist.add", "settings.set")\n', src)

    def test_the_buttons_and_the_commands_share_one_body(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index("    def _clear_selected_waiting_for_add(self):")
        self.assertIn("self._waitlist_remove_request(req, rid)", src[i:i + 700])
        j = src.index("    def _restore_selected_waiting_for_add(self)")
        self.assertIn("self._waitlist_restore_request(req)", src[j:j + 500])


if __name__ == "__main__":
    unittest.main()
