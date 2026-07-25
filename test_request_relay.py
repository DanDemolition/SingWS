import importlib.util
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_request_relay", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAIN = load_main_module()


def make_app(settings=None):
    app = MAIN.KaraokeApp.__new__(MAIN.KaraokeApp)
    app.settings = settings or {
        "base_url": "https://wskar.com",
        "user": "wsk",
        "api_key": "secret",
    }
    return app


class TransportSelectionTests(unittest.TestCase):
    def test_configured_transport_starts_without_connection_probe_gate(self):
        app = make_app()
        starts = []
        app.start_request_polling = lambda: starts.append("started")
        app.test_server_connection_quick = mock.Mock(side_effect=AssertionError("probe must not gate startup"))

        self.assertTrue(app._start_configured_network_transport())
        self.assertEqual(starts, ["started"])
        app.test_server_connection_quick.assert_not_called()

    def test_version_gate(self):
        app = make_app()
        self.assertGreaterEqual(
            MAIN.KaraokeApp._version_tuple(MAIN.APP_VERSION),
            MAIN.KaraokeApp.RELAY_MIN_APP_VERSION,
            "APP_VERSION must be >= 0.3.0.0 for relay support",
        )
        if MAIN.QTWEBSOCKETS_AVAILABLE:
            self.assertTrue(app._supports_request_relay())

    def test_version_tuple_parsing(self):
        vt = MAIN.KaraokeApp._version_tuple
        self.assertEqual(vt("0.3.0.0"), (0, 3, 0, 0))
        self.assertGreater(vt("0.3.0.0"), vt("0.2.18.3"))
        self.assertGreater(vt("0.10.0.0"), vt("0.9.9.9"))

    def test_host_gating(self):
        app = make_app()
        if not MAIN.QTWEBSOCKETS_AVAILABLE:
            self.skipTest("QtWebSockets unavailable")
        self.assertTrue(app._should_use_request_relay("https://wskar.com", "wsk", "key"))
        self.assertTrue(app._should_use_request_relay("https://www.wskar.com", "wsk", "key"))
        self.assertFalse(app._should_use_request_relay("https://beta.wskar.com", "wsk", "key"))
        self.assertFalse(app._should_use_request_relay("https://example.com", "wsk", "key"))

    def test_credential_gating(self):
        app = make_app()
        if not MAIN.QTWEBSOCKETS_AVAILABLE:
            self.skipTest("QtWebSockets unavailable")
        self.assertFalse(app._should_use_request_relay("https://wskar.com", "", "key"))
        self.assertFalse(app._should_use_request_relay("https://wskar.com", "wsk", ""))

    def test_polling_setting_forces_polling(self):
        app = make_app({"base_url": "https://wskar.com", "user": "wsk",
                        "api_key": "k", "request_transport": "polling"})
        self.assertFalse(app._should_use_request_relay("https://wskar.com", "wsk", "k"))

    def test_transport_setting_normalization(self):
        for raw, expected in (
            ("auto", "auto"), ("", "auto"), ("bogus", "auto"),
            ("websocket", "websocket"), ("relay", "websocket"),
            ("Polling", "polling"),
        ):
            app = make_app({"request_transport": raw})
            self.assertEqual(app._request_transport_setting(), expected, raw)


class HandleRelayRequestsTests(unittest.TestCase):
    def make_handler_app(self, results):
        app = make_app()
        app._relay_processed_request_ids = set()
        app.reconciled = []
        app._queue_ids = set()
        app.acked = []

        def reconcile(rows, **kwargs):
            for req in rows:
                if not isinstance(req, dict):
                    continue
                rid = req.get("request_id") or req.get("id")
                app.reconciled.append(rid)
                if results.get(rid, False):
                    app._queue_ids.add(rid)

        app._reconcile_remote_requests = reconcile
        app._queue_remote_request_ids = lambda: sorted(app._queue_ids)
        app.ack_remote_requests = lambda ids: app.acked.extend(ids)
        return app

    def test_only_successes_are_acked(self):
        rows = [
            {"id": 1, "singer": "A", "artist": "X", "title": "T1", "key": 0},
            {"id": 2, "singer": "B", "artist": "Y", "title": "T2", "key": 2},
        ]
        app = self.make_handler_app({1: True, 2: False})
        app._handle_relay_requests(rows)
        self.assertEqual(app.reconciled, [1, 2])
        self.assertEqual(app.acked, [1])
        self.assertEqual(app._relay_processed_request_ids, {1})

    def test_redelivered_processed_id_reacked_not_requeued(self):
        app = self.make_handler_app({3: False})
        app._relay_processed_request_ids = {3}
        app._handle_relay_requests([{"id": 3, "singer": "A", "artist": "X", "title": "T"}])
        self.assertEqual(app.reconciled, [3])
        self.assertEqual(app.acked, [3])

    def test_identical_snapshot_skips_reconciliation_but_retries_ack(self):
        app = self.make_handler_app({3: True})
        rows = [{"id": 3, "singer": "A", "artist": "X", "title": "T"}]

        app._handle_relay_requests(rows)
        app._handle_relay_requests(rows)

        self.assertEqual(app.reconciled, [3])
        self.assertEqual(app.acked, [3, 3])

    def test_changed_snapshot_reconciles_again(self):
        app = self.make_handler_app({3: True, 4: True})

        app._handle_relay_requests([{"id": 3, "singer": "A", "artist": "X", "title": "T"}])
        app._handle_relay_requests([
            {"id": 3, "singer": "A", "artist": "X", "title": "T"},
            {"id": 4, "singer": "B", "artist": "Y", "title": "U"},
        ])

        self.assertEqual(app.reconciled, [3, 3, 4])
        self.assertEqual(app.acked, [3, 3, 4])

    def test_reconcile_exception_not_acked(self):
        app = self.make_handler_app({})

        def boom(rows, **kwargs):
            raise RuntimeError("nope")

        app._reconcile_remote_requests = boom
        app._handle_relay_requests([{"id": 9, "artist": "X", "title": "T"}])
        self.assertEqual(app.acked, [])

    def test_non_dict_and_bad_id_rows_ignored(self):
        app = self.make_handler_app({0: True})
        app._handle_relay_requests(["junk", None, {"id": "abc"}, {"singer": "A"}])
        self.assertEqual(app.acked, [])

    def test_relay_id_is_aliased_to_request_id_for_queue_metadata(self):
        app = make_app()
        app._relay_processed_request_ids = set()
        seen = []
        app._reconcile_remote_requests = lambda rows, **kwargs: seen.extend(dict(row) for row in rows)
        app._queue_remote_request_ids = lambda: [42]
        app.ack_remote_requests = lambda ids: None

        app._handle_relay_requests([{"id": 42, "singer": "A", "artist": "X", "title": "T"}])

        self.assertEqual(seen[0]["id"], 42)
        self.assertEqual(seen[0]["request_id"], 42)

    def test_delivered_rows_reconcile_but_are_not_acked(self):
        app = self.make_handler_app({5: True})
        app._handle_relay_requests([
            {"id": 5, "singer": "A", "artist": "X", "title": "T", "sent": True, "state": "delivered"}
        ])
        self.assertEqual(app.reconciled, [5])
        self.assertEqual(app.acked, [])


class FetchOverlapTests(unittest.TestCase):
    def test_second_fetch_queued_while_in_flight(self):
        app = make_app()
        app._relay_fetch_in_flight = True
        app._relay_fetch_queued = False
        app.fetch_remote_requests_once("relay")
        self.assertTrue(app._relay_fetch_queued)

    def test_finish_drains_queued_fetch(self):
        app = make_app()
        app._relay_fetch_in_flight = True
        app._relay_fetch_queued = True
        calls = []
        app.fetch_remote_requests_once = lambda reason="relay": calls.append(reason)
        MAIN.KaraokeApp._relay_fetch_finished(app, None)
        self.assertFalse(app._relay_fetch_in_flight)
        self.assertEqual(calls, ["queued notification"])
        self.assertFalse(app._relay_fetch_queued)


class NetworkRecoveryWatchdogTests(unittest.TestCase):
    def test_relay_watchdog_fetches_pending_requests_without_network_screen(self):
        app = make_app()
        app.relay_worker = object()
        app._host_request_sync_ops = []
        app._relay_last_successful_fetch_at = 0.0
        app._relay_recovery_last_attempt_at = 0.0
        calls = []
        app.fetch_remote_requests_once = lambda reason="relay": calls.append(reason)

        with mock.patch.object(MAIN.time, "monotonic", return_value=100.0):
            app._network_recovery_tick()
        with mock.patch.object(MAIN.time, "monotonic", return_value=105.0):
            app._network_recovery_tick()
        with mock.patch.object(MAIN.time, "monotonic", return_value=110.1):
            app._network_recovery_tick()

        self.assertEqual(calls, ["relay watchdog", "relay watchdog"])

    def test_healthy_relay_uses_longer_recovery_interval(self):
        app = make_app()
        app.relay_worker = mock.Mock()
        app.relay_worker.is_connected.return_value = True
        app._host_request_sync_ops = []
        app._relay_last_successful_fetch_at = 100.0
        app._relay_recovery_last_attempt_at = 0.0
        calls = []
        app.fetch_remote_requests_once = lambda reason="relay": calls.append(reason)

        with mock.patch.object(MAIN.time, "monotonic", return_value=159.9):
            app._network_recovery_tick()
        with mock.patch.object(MAIN.time, "monotonic", return_value=160.1):
            app._network_recovery_tick()

        self.assertEqual(calls, ["relay watchdog"])

    def test_successful_refresh_preserves_batch_identity_for_deduplication(self):
        app = make_app()
        app._relay_fetch_in_flight = True
        app._relay_fetch_queued = False
        seen = []
        rows = [{"id": rid, "singer": f"Singer {rid}", "title": f"Song {rid}"} for rid in range(1, 6)]
        app._handle_relay_requests = lambda batch: seen.extend(row["id"] for row in batch)

        app._relay_fetch_finished(rows)

        self.assertEqual(seen, [1, 2, 3, 4, 5])
        self.assertGreater(app._relay_last_successful_fetch_at, 0.0)


class RelayFallbackPollingTests(unittest.TestCase):
    def test_relay_mode_keeps_legacy_waitlist_poll_connected(self):
        app = make_app()
        connected = []

        class FakeSignal:
            def connect(self, fn):
                connected.append(fn)

        class FakePollWorker:
            requests_received = FakeSignal()

        app.poll_worker = FakePollWorker()
        app._handle_relay_requests = lambda rows: None
        app.handle_requests_from_thread = lambda rows: None

        MAIN.KaraokeApp._connect_poll_worker_requests_received(app, True)

        self.assertEqual(connected, [app.handle_requests_from_thread])

    def test_relay_startup_backlog_queues_each_person_once(self):
        app = make_app()
        relay_starts = []
        created_workers = []
        queued = [{"request_id": 99, "singer": "Existing", "title": "Saved Song"}]

        class FakeSignal:
            def __init__(self):
                self.handlers = []

            def connect(self, fn):
                self.handlers.append(fn)

        class FakeThread:
            def __init__(self, *args, **kwargs):
                self.started = FakeSignal()

            def start(self):
                pass

        class FakePollWorker:
            def __init__(self, *args, **kwargs):
                self.poll_requests = kwargs["poll_requests"]
                self.requests_received = FakeSignal()
                self.host_commands_received = FakeSignal()
                self.host_state_sync_requested = FakeSignal()
                self.connection_status_changed = FakeSignal()
                created_workers.append(self)

            def moveToThread(self, thread):
                pass

            def run(self):
                pass

        app.stop_request_polling = lambda: None
        app._should_use_request_relay = lambda *args: True
        app._start_request_relay = lambda *args: relay_starts.append(args)
        app._should_use_host_control_relay = lambda *args: False
        app._effective_request_poll_interval_sec = lambda: 2
        app._effective_host_poll_interval_sec = lambda: 2
        app.handle_host_commands_from_thread = lambda rows: None
        app._schedule_host_control_state_sync = lambda: None
        app._set_server_connection_status = lambda *args: None
        app._sync_remote_removal_tombstones_async = lambda reason: None
        app.handle_requests_from_thread = lambda rows: queued.extend(rows)
        app._handle_relay_requests = lambda rows: queued.extend(rows)

        backlog = [
            {"request_id": 101, "singer": "Alice", "title": "Song A"},
            {"request_id": 102, "singer": "Bob", "title": "Song B"},
        ]

        with mock.patch.object(MAIN, "QThread", FakeThread), \
             mock.patch.object(MAIN, "SimplePollWorker", FakePollWorker), \
             mock.patch.object(MAIN.QTimer, "singleShot", lambda *args: None):
            MAIN.KaraokeApp.start_request_polling(app)

        self.assertEqual(len(relay_starts), 1)
        self.assertEqual(len(created_workers), 1)
        self.assertTrue(created_workers[0].poll_requests)
        self.assertEqual(created_workers[0].requests_received.handlers, [app.handle_requests_from_thread])

        # Relay recovery remains immediate. The production reconciliation path
        # deduplicates the same permanent request IDs if legacy recovery also
        # includes these rows.
        app._handle_relay_requests(backlog)
        self.assertEqual([row["singer"] for row in queued], ["Existing", "Alice", "Bob"])
        self.assertEqual([row["request_id"] for row in queued], [99, 101, 102])
        self.assertEqual(sum(row["request_id"] == 101 for row in queued), 1)
        self.assertEqual(sum(row["request_id"] == 102 for row in queued), 1)

    def test_relay_snapshot_cannot_clear_legacy_waitlist_snapshot(self):
        app = make_app()
        app._waiting_for_add_requests = {
            501: {
                "request_id": 501,
                "singer": "Ada",
                "artist": "Artist",
                "title": "Waiting Song",
                "state": "waiting",
                "pending_reason": "host_not_accepting",
            },
        }
        app._relay_processed_request_ids = set()
        reconcile_options = []
        app._reconcile_remote_requests = lambda rows, **kwargs: reconcile_options.append(kwargs)
        app._queue_remote_request_ids = lambda: []
        app.ack_remote_requests = lambda ids: None

        app._handle_relay_requests([])

        self.assertEqual(list(app._waiting_for_add_requests), [501])
        self.assertEqual(reconcile_options, [{"update_waitlist": False}])

    def test_polling_mode_keeps_legacy_request_handler(self):
        app = make_app()
        connected = []

        class FakeSignal:
            def connect(self, fn):
                connected.append(fn)

        class FakePollWorker:
            requests_received = FakeSignal()

        app.poll_worker = FakePollWorker()
        app._handle_relay_requests = lambda rows: None
        app.handle_requests_from_thread = lambda rows: None

        MAIN.KaraokeApp._connect_poll_worker_requests_received(app, False)

        self.assertEqual(connected, [app.handle_requests_from_thread])


@unittest.skipUnless(MAIN.QTWEBSOCKETS_AVAILABLE, "QtWebSockets unavailable")
class RelayWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls._qapp = QCoreApplication.instance() or QCoreApplication([])

    def make_worker(self):
        return MAIN.RelayRequestWorker(
            "https://wskar.com", "ten ant", "k&y=1", MAIN.APP_VERSION
        )

    def test_relay_url_encoding_and_shape(self):
        worker = self.make_worker()
        url = worker.relay_url()
        # toEncoded() is the wire form the socket actually opens.
        encoded = bytes(url.toEncoded()).decode("ascii")
        self.assertTrue(encoded.startswith("wss://wskar.com/relay?"))
        self.assertIn("user=ten%20ant", encoded)
        self.assertIn("token=k%26y%3D1", encoded)
        self.assertIn(f"app_version={MAIN.APP_VERSION}", encoded)
        worker.stop()

    def test_redacted_url_hides_token(self):
        worker = self.make_worker()
        redacted = worker._redacted_url()
        self.assertNotIn("k%26y", redacted)
        self.assertNotIn("k&y", redacted)
        self.assertIn("token=***", redacted)
        worker.stop()

    def test_hello_logged_not_treated_as_requests(self):
        worker = self.make_worker()
        seen = []
        worker.requests_available.connect(lambda reason: seen.append(reason))
        worker._on_text_message('{"type": "hello", "user": "ten ant"}')
        self.assertEqual(seen, [])
        worker._on_text_message('{"type": "requests_available"}')
        self.assertEqual(seen, ["relay"])
        worker._on_text_message("not json at all")
        self.assertEqual(seen, ["relay"])
        worker.stop()

    def test_history_event_triggers_history_sync_signal(self):
        worker = self.make_worker()
        seen = []
        worker.history_available.connect(lambda reason: seen.append(reason))
        worker._on_text_message('{"type": "history_updated"}')
        worker._on_text_message('{"type": "history_bulk_sync"}')
        self.assertEqual(seen, ["history_updated", "history_bulk_sync"])
        worker.stop()

    def test_connected_triggers_recovery_fetch(self):
        worker = self.make_worker()
        seen = []
        worker.requests_available.connect(lambda reason: seen.append(reason))
        worker._on_connected()
        self.assertEqual(seen, ["connect"])
        self.assertTrue(worker.is_connected())
        worker.stop()
        self.assertFalse(worker.is_connected())

    def test_stop_prevents_reconnect(self):
        worker = self.make_worker()
        worker.stop()
        worker._schedule_reconnect()
        self.assertFalse(worker._reconnect_timer.isActive())

    def test_single_reconnect_timer(self):
        worker = self.make_worker()
        worker._closing = False
        worker._schedule_reconnect()
        worker._schedule_reconnect()
        self.assertTrue(worker._reconnect_timer.isActive())
        self.assertTrue(worker._reconnect_timer.isSingleShot())
        worker.stop()


if __name__ == "__main__":
    unittest.main()
