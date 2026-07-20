"""Regression coverage for durable host/manual request synchronization."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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
        # Isolate every durable store this flow can touch: connection restore
        # also pushes remote-request tombstones, and without this redirect the
        # test reads the REAL ~/SingWS state (a live unsynced tombstone makes
        # the flush POST twice and the count assertion flake).
        self.module.REMOTE_REQUEST_TOMBSTONES_PATH = Path(self.tmp.name) / "remote_request_tombstones.json"
        self.module.DEFERRED_REMOTE_ADDS_PATH = Path(self.tmp.name) / "deferred_remote_adds.json"
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
        self.app._run_on_ui_thread = lambda fn: fn()
        self.app._sync_remote_singer_order = lambda *args, **kwargs: None

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

    @staticmethod
    def _success_response(request_id=700, singer_session_id=42):
        response = mock.Mock()
        response.status_code = 200
        response.content = b"{}"
        response.json.return_value = {
            "ok": True,
            "request_id": request_id,
            "singer_session_id": singer_session_id,
        }
        return response

    def _run_threads_immediately(self):
        class ImmediateThread:
            def __init__(self, target=None, **kwargs):
                self.target = target

            def start(self):
                self.target()

        return mock.patch.object(self.module.threading, "Thread", ImmediateThread)

    def test_online_add_submits_immediately(self):
        self.app.settings.update({"base_url": "https://wskar.com", "api_key": "secret"})
        response = self._success_response(request_id=701)
        with mock.patch.object(self.module.requests, "post", return_value=response) as post, \
             self._run_threads_immediately():
            self.app._queue_host_request_sync(0, 0)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.app.queue[0]["songs"][0]["remote_request_id"], 701)
        self.assertEqual(self.app.queue[0]["songs"][0]["host_sync_status"], "synced")
        self.assertEqual(self.app._host_request_sync_ops, [])

    def test_connection_restore_flushes_offline_add_automatically(self):
        self.app._queue_host_request_sync(0, 0)
        self.assertEqual(len(self.app._host_request_sync_ops), 1)
        self.app.settings.update({
            "base_url": "https://wskar.com",
            "api_key": "secret",
            "requests_accepting": True,
        })
        response = self._success_response(request_id=702)
        with mock.patch.object(self.module.requests, "post", return_value=response) as post, \
             self._run_threads_immediately():
            self.app._set_server_connection_status(True, "Connected")

        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.app._host_request_sync_ops, [])
        self.assertEqual(self.app.queue[0]["songs"][0]["remote_request_id"], 702)

    def test_bulk_pending_additions_flush_once_each(self):
        template = dict(self.app.queue[0]["songs"][0])
        self.app.queue[0]["songs"] = []
        for index in range(4):
            entry = dict(template)
            entry["request_uid"] = f"stable-request-{index}"
            entry["title"] = f"Title {index}"
            self.app.queue[0]["songs"].append(entry)
            self.app._queue_host_request_sync(0, index)
        self.assertEqual(len(self.app._host_request_sync_ops), 4)

        self.app.settings.update({"base_url": "https://wskar.com", "api_key": "secret"})
        request_ids = iter(range(800, 804))

        def post(*args, **kwargs):
            return self._success_response(request_id=next(request_ids))

        with mock.patch.object(self.module.requests, "post", side_effect=post) as send, \
             self._run_threads_immediately():
            scheduled = self.app._flush_host_request_sync_ops()

        self.assertEqual(scheduled, 4)
        self.assertEqual(send.call_count, 4)
        self.assertEqual(self.app._host_request_sync_ops, [])
        self.assertEqual(
            [entry.get("remote_request_id") for entry in self.app.queue[0]["songs"]],
            [800, 801, 802, 803],
        )

    def test_duplicate_flush_while_inflight_does_not_submit_twice(self):
        self.app.settings.update({"base_url": "https://wskar.com", "api_key": "secret"})
        started = []

        class HeldThread:
            def __init__(self, target=None, **kwargs):
                self.target = target

            def start(self):
                started.append(self.target)

        response = self._success_response(request_id=900)
        with mock.patch.object(self.module.threading, "Thread", HeldThread), \
             mock.patch.object(self.module.requests, "post", return_value=response) as post:
            self.app._queue_host_request_sync(0, 0)
            self.app._flush_host_request_sync_ops()
            self.app._queue_host_request_sync(0, 0)
            self.assertEqual(len(started), 1)
            self.assertEqual(post.call_count, 0)
            started[0]()

        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.app._host_request_sync_ops, [])


if __name__ == "__main__":
    unittest.main()
