"""Regression coverage for durable host/manual request synchronization."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_module():
    spec = importlib.util.spec_from_file_location("singws_host_request_sync", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostManualRequestSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.module.HOST_REQUEST_SYNC_PATH = Path(self.tmp.name) / "host-request-sync.json"
        self.app = self.module.KaraokeApp.__new__(self.module.KaraokeApp)
        self.app.settings = {"base_url": "", "user": "venue", "api_key": ""}
        self.app.queue = [{
            "singer_id": "stable-singer-1",
            "server_singer_session_id": 42,
            "name": "Renamed Singer",
            "songs": [{
                "request_uid": "stable-request-1",
                "song_info": "/media/song.cdg",
                "artist": "Artist",
                "title": "Title",
                "duration": 201,
                "disc_id": "SC-1234",
                "key": -2,
                "tempo_percent": 105,
            }],
        }]
        self.app._host_request_sync_ops = []
        self.app._host_request_sync_inflight = set()
        self.app._schedule_save_data = lambda *args, **kwargs: None
        self.app._show_processing_notification = lambda *args, **kwargs: None

    def tearDown(self):
        self.tmp.cleanup()

    def test_payload_preserves_stable_singer_and_request_identity(self):
        payload = self.app._host_request_sync_payload(self.app.queue[0], self.app.queue[0]["songs"][0], 0)
        self.assertEqual(payload["host_singer_id"], "stable-singer-1")
        self.assertEqual(payload["singer_session_id"], 42)
        self.assertEqual(payload["idempotency_key"], "host:stable-singer-1:local:stable-request-1")
        self.assertEqual(payload["request_source"], "host_manual")
        self.assertEqual(payload["selected_disc_id"], "SC-1234")
        self.assertEqual(payload["key_change"], -2)
        self.assertEqual(payload["tempo_percent"], 105)

    def test_offline_add_is_durable_and_requeue_is_idempotent(self):
        self.app._queue_host_request_sync(0, 0)
        self.app._queue_host_request_sync(0, 0)
        self.assertEqual(len(self.app._host_request_sync_ops), 1)
        self.assertTrue(self.module.HOST_REQUEST_SYNC_PATH.exists())
        self.assertEqual(self.app.queue[0]["songs"][0]["host_sync_status"], "pending")

    def test_success_attaches_server_id_and_preserves_session_mapping(self):
        self.app._queue_host_request_sync(0, 0)
        order_syncs = []
        self.app._sync_remote_singer_order = lambda idx, reason="": order_syncs.append((idx, reason))
        key = self.app._host_request_sync_ops[0]["key"]
        self.app._finish_host_request_sync(key, True, 987, 42, "")
        entry = self.app.queue[0]["songs"][0]
        self.assertEqual(entry["remote_request_id"], 987)
        self.assertEqual(entry["host_sync_status"], "synced")
        self.assertEqual(self.app._host_request_sync_ops, [])
        self.assertEqual(order_syncs, [(0, "host_manual_upsert")])

    def test_failure_remains_pending_for_reconnect_retry(self):
        self.app._queue_host_request_sync(0, 0)
        key = self.app._host_request_sync_ops[0]["key"]
        self.app._finish_host_request_sync(key, False, 0, 0, "offline")
        self.assertEqual(len(self.app._host_request_sync_ops), 1)
        self.assertEqual(self.app._host_request_sync_ops[0]["attempts"], 1)
        self.assertEqual(self.app._host_request_sync_ops[0]["last_error"], "offline")


if __name__ == "__main__":
    unittest.main()
