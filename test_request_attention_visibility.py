"""Regression tests for the 2026-07-19 show outage: failed auto-accepts must
never be silently dropped.

That night every remote request failed local matching and the app was in
auto-accept mode (waitlist off). Failed requests were recorded only in a
diagnostics dict — no host UI entry, no server report — so the host saw "no
requests" while singers watched their submissions say "sent". These tests pin
the new behavior: failures stay visible in the Waiting-for-Add store, are
always reported to the server, and an empty library trips a loud warning.
"""

import tempfile
import unittest
from pathlib import Path

from test_queue_sync_authority import fake_network, load_main_module, make_app


def _visibility_app(module, tmp_dir: Path):
    app = make_app(module, tmp_dir, settings={
        "base_url": "https://example.test",
        "user": "venue",
        "api_key": "k123",
        "use_waiting_for_add": False,  # auto-accept mode (the outage config)
    })
    app._processing_texts = []
    # Bare-__new__ Qt objects raise RuntimeError (not AttributeError) on unset
    # attribute reads, so pre-seed flags the production __init__ would own.
    app._empty_library_request_warned = False
    app._set_processing_text = (
        lambda msg, **kw: app._processing_texts.append(str(msg))
    )
    return app


class AttentionVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _visibility_app(self.singws, Path(self._tmp.name))

    def _failed_request(self, rid=501):
        return {
            "request_id": rid,
            "singer": "Grace",
            "artist": "Artist",
            "title": "Missing Song",
            "state": "pending",
            "sent": False,
        }

    def test_failure_stays_visible_with_waitlist_disabled(self):
        with fake_network(self.singws) as net:
            self.app._record_remote_attention_request(self._failed_request(), "auto_accept_failed")
        self.assertIn(501, self.app._waiting_for_add_requests,
                      "failed auto-accept must appear in the waiting-for-add store")
        entry = self.app._waiting_for_add_requests[501]
        self.assertEqual(entry.get("attention_reason"), "auto_accept_failed")
        self.assertTrue(any("NOT added" in t for t in self.app._processing_texts),
                        "host must see an on-screen notice for a dropped request")
        report_posts = [p for p in net.posts if "report_pending_request" in p["url"]]
        self.assertEqual(len(report_posts), 1,
                         "server must be told even in auto-accept mode")
        self.assertEqual(report_posts[0]["data"]["state"], "failed_needs_review")
        self.assertEqual(report_posts[0]["data"]["request_id"], 501)

    def test_limit_block_reports_with_waitlist_disabled(self):
        with fake_network(self.singws) as net:
            self.app._record_remote_limit_blocked_request(
                self._failed_request(502), "Singer song limit reached."
            )
        report_posts = [p for p in net.posts if "report_pending_request" in p["url"]]
        self.assertEqual(len(report_posts), 1)
        self.assertEqual(report_posts[0]["data"]["request_id"], 502)

    def test_rebuild_keeps_failure_entries_when_waitlist_disabled(self):
        with fake_network(self.singws):
            self.app._record_remote_attention_request(self._failed_request(), "auto_accept_failed")
        # A server-driven waitlist entry (no attention_reason) sneaks in too.
        self.app._waiting_for_add_requests[900] = {
            "request_id": 900, "singer": "Bob", "artist": "A", "title": "T",
        }
        with fake_network(self.singws):
            self.app._set_waiting_for_add_requests([], local_remote_ids=[])
        self.assertIn(501, self.app._waiting_for_add_requests,
                      "failure entries must survive the waitlist-disabled rebuild")
        self.assertNotIn(900, self.app._waiting_for_add_requests,
                         "server-only entries are still cleared in auto-accept mode")

    def test_empty_library_trips_loud_warning_once(self):
        self.app.tracks = []
        self.app._find_song_for_request = lambda artist, title: []
        self.app._check_remote_request_song_limit = lambda req: (True, "")
        # make_app stubs process_external_request for reconcile tests; this
        # test exercises the real intake path. Later intake stages hit Qt
        # RuntimeErrors on the bare test app (unset attrs on a __new__
        # instance); the tripwire under test runs before them.
        real_intake = self.singws.KaraokeApp.process_external_request
        with fake_network(self.singws):
            for rid in (601, 602):
                try:
                    real_intake(self.app, self._failed_request(rid))
                except RuntimeError:
                    pass
        self.assertTrue(self.app._empty_library_request_warned)
        library_warnings = [t for t in self.app._processing_texts if "LIBRARY EMPTY" in t]
        self.assertEqual(len(library_warnings), 1, "warn loudly, but only once")


class AcceptedStateActionabilityTests(unittest.TestCase):
    """The server's submit-time validation marks fresh requests
    state='accepted' before the desktop ever sees them (server change of
    2026-07-19). Treating 'accepted' alone as historical filed every new
    request as already-handled and killed the whole show's intake."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _visibility_app(self.singws, Path(self._tmp.name))

    def _fresh_accepted_row(self, rid=1465, **extra):
        row = {
            "request_id": rid,
            "singer": "Dan",
            "artist": "Avenged Sevenfold",
            "title": "Almost Easy",
            "sent": False,
            "delivered": False,
            "state": "accepted",
            "accepted_at": "2026-07-20T19:16:13Z",
            "validation_result": "accepted",
            "desktop_sync_status": "pending_desktop_sync",
            "desktop_add_result": "pending",
        }
        row.update(extra)
        return row

    def test_fresh_accepted_row_is_actionable(self):
        self.assertFalse(
            self.app._remote_request_is_non_actionable(self._fresh_accepted_row())
        )

    def test_delivered_accepted_row_is_historical(self):
        self.assertTrue(
            self.app._remote_request_is_non_actionable(
                self._fresh_accepted_row(sent=True, delivered=True)
            )
        )

    def test_desktop_synced_accepted_row_is_historical(self):
        self.assertTrue(
            self.app._remote_request_is_non_actionable(
                self._fresh_accepted_row(desktop_sync_status="synced")
            )
        )

    def test_submit_time_accepted_at_alone_is_not_delivery_evidence(self):
        row = self._fresh_accepted_row(state="", accepted_at=1784575000)
        self.assertFalse(self.app._remote_request_is_non_actionable(row))

    def test_completed_and_removed_rows_stay_historical(self):
        for state in ("completed", "removed", "sung", "skipped", "active", "delivered"):
            self.assertTrue(
                self.app._remote_request_is_non_actionable(
                    self._fresh_accepted_row(state=state)
                ),
                state,
            )

    def test_reconcile_routes_fresh_accepted_row_to_intake(self):
        with fake_network(self.singws):
            self.app._reconcile_remote_requests([
                self._fresh_accepted_row(),
                self._fresh_accepted_row(rid=1440, title="Old Song", sent=True, delivered=True),
            ])
        intake_ids = [c.get("request_id") for c in self.app._intake_calls]
        self.assertEqual(intake_ids, [1465],
                         "fresh accepted row must reach intake; delivered row must not")


if __name__ == "__main__":
    unittest.main()
