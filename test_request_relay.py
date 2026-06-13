import importlib.util
import os
import sys
import unittest

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
        app.processed = []
        app.acked = []

        def process(req):
            app.processed.append(req.get("id"))
            return results.get(req.get("id"), False)

        app.process_external_request = process
        app.ack_remote_requests = lambda ids: app.acked.extend(ids)
        return app

    def test_only_successes_are_acked(self):
        rows = [
            {"id": 1, "singer": "A", "artist": "X", "title": "T1", "key": 0},
            {"id": 2, "singer": "B", "artist": "Y", "title": "T2", "key": 2},
        ]
        app = self.make_handler_app({1: True, 2: False})
        app._handle_relay_requests(rows)
        self.assertEqual(app.processed, [1, 2])
        self.assertEqual(app.acked, [1])
        self.assertEqual(app._relay_processed_request_ids, {1})

    def test_redelivered_processed_id_reacked_not_requeued(self):
        app = self.make_handler_app({3: True})
        app._relay_processed_request_ids = {3}
        app._handle_relay_requests([{"id": 3, "singer": "A", "artist": "X", "title": "T"}])
        self.assertEqual(app.processed, [], "already-processed request must not be re-queued")
        self.assertEqual(app.acked, [3])

    def test_processing_exception_not_acked(self):
        app = self.make_handler_app({})

        def boom(req):
            raise RuntimeError("nope")

        app.process_external_request = boom
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
        app.process_external_request = lambda req: seen.append(dict(req)) or True
        app.ack_remote_requests = lambda ids: None

        app._handle_relay_requests([{"id": 42, "singer": "A", "artist": "X", "title": "T"}])

        self.assertEqual(seen[0]["id"], 42)
        self.assertEqual(seen[0]["request_id"], 42)


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
        worker.stop()

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
