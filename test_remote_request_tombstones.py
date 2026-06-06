import importlib.util
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_tombstones", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.text = text

    def json(self):
        return self._payload


class RecordingRequests:
    """Stand-in for the `requests` module that records POSTs and can fail."""

    def __init__(self, fail=False):
        self.fail = fail
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        if self.fail:
            raise OSError("server unreachable")
        return FakeResponse(200, {"ok": True})

    def get(self, url, **kwargs):
        if self.fail:
            raise OSError("server unreachable")
        return FakeResponse(200, {"ok": True})


class _InlineThread:
    """Runs the worker body synchronously so network sync is deterministic."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


@contextmanager
def fake_network(module=None, fail=False):
    """Make all network paths deterministic.

    Methods that do a local ``import requests`` pick up ``sys.modules`` while
    methods that use the module-level ``requests`` global need the loaded
    module patched too. Threads run inline so sync work completes in-test.
    """
    fake = RecordingRequests(fail=fail)
    saved_requests = sys.modules.get("requests")
    sys.modules["requests"] = fake
    patches = [mock.patch.object(threading, "Thread", _InlineThread)]
    if module is not None and hasattr(module, "requests"):
        patches.append(mock.patch.object(module, "requests", fake))
    try:
        for p in patches:
            p.start()
        yield fake
    finally:
        for p in reversed(patches):
            p.stop()
        if saved_requests is not None:
            sys.modules["requests"] = saved_requests
        else:
            sys.modules.pop("requests", None)


CONNECTED_SETTINGS = {
    "base_url": "https://beta.wskar.com",
    "user": "venue",
    "api_key": "secret-key",
    "queue_mode": "classic",
}


def make_app(module, tombstone_path: Path, settings=None):
    module.REMOTE_REQUEST_TOMBSTONES_PATH = tombstone_path
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {
        "requests_accepting": True,
        "base_url": "",
        "user": "venue",
        "api_key": "",
        "queue_mode": "classic",
    }
    if settings:
        app.settings.update(settings)
    app.queue = []
    app.singer_history = {"singers": {}, "deletions": {}}
    app._remote_request_tombstones = app._load_remote_request_tombstones()
    app._remote_removed_request_ids = set()
    app._unmatched_remote_request_ids = set()
    app._pending_remote_order_syncs = {}
    app._queue_revision = 0
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app.processed_requests = []

    def process(req):
        app.processed_requests.append(req)
        return True

    app.process_external_request = process
    return app


class RemoteRequestTombstoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_accepting_off_does_not_import_new_remote_request(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["requests_accepting"] = False

            app._reconcile_remote_requests([
                {"request_id": 101, "singer": "Ada", "artist": "Artist", "title": "Title", "key": 0, "tempo": 0}
            ])

            self.assertEqual(app.processed_requests, [])

    def test_local_remote_delete_creates_unsynced_tombstone(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")

            app._delete_remote_request(
                202,
                entry={"artist": "Artist", "title": "Title"},
                singer_name="Ada",
                reason="host_remove_song",
            )

            data = app._load_remote_request_tombstones()
            tombstone = data["requests"]["202"]
            self.assertEqual(tombstone["status"], "removed")
            self.assertEqual(tombstone["removed_by"], "host")
            self.assertIsNone(tombstone["server_synced_at"])
            self.assertIn(202, app._remote_removed_request_ids)

    def test_tombstoned_old_remote_request_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._record_remote_request_tombstone(
                303,
                entry={"artist": "Artist", "title": "Title"},
                singer_name="Ada",
                reason="host_remove_song",
            )

            app._reconcile_remote_requests([
                {"request_id": 303, "singer": "Ada", "artist": "Artist", "title": "Title", "key": 0, "tempo": 0}
            ])

            self.assertEqual(app.processed_requests, [])

    def test_accepting_off_remove_pushes_removal_to_server(self):
        """Accepting Requests off, but Connected: removing a song still pushes
        the removal to the server and marks the tombstone synced."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app.settings["requests_accepting"] = False

            with fake_network(self.singws) as net:
                app._delete_remote_request(
                    202,
                    entry={"artist": "Artist", "title": "Title"},
                    singer_name="Ada",
                    reason="host_remove_song",
                )

            removal_posts = [p for p in net.posts if "complete_remote_request.php" in p["url"]]
            self.assertTrue(removal_posts, "expected a removal POST while requests are off")
            payload = removal_posts[0]["data"]
            self.assertEqual(int(payload["request_id"]), 202)
            self.assertEqual(payload["state"], "removed")

            tombstone = app._ensure_remote_request_tombstones()["requests"]["202"]
            self.assertIsNotNone(tombstone["server_synced_at"])

    def test_server_unreachable_queues_tombstone_then_syncs_later(self):
        """Server down at removal time: tombstone is queued unsynced, and a
        later sync pass pushes it once the server is reachable again."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)

            with fake_network(self.singws, fail=True):
                app._delete_remote_request(
                    404,
                    entry={"artist": "Artist", "title": "Title"},
                    singer_name="Ada",
                    reason="host_remove_song",
                )

            tombstone = app._ensure_remote_request_tombstones()["requests"]["404"]
            self.assertIsNone(tombstone["server_synced_at"], "should stay unsynced while unreachable")

            with fake_network(self.singws) as net:
                app._sync_remote_removal_tombstones_async("retry")

            synced_posts = [p for p in net.posts if "complete_remote_request.php" in p["url"]]
            self.assertTrue(synced_posts, "queued tombstone should sync once server is reachable")
            self.assertEqual(int(synced_posts[0]["data"]["request_id"]), 404)

            tombstone = app._ensure_remote_request_tombstones()["requests"]["404"]
            self.assertIsNotNone(tombstone["server_synced_at"])

    def test_singer_history_syncs_while_requests_off(self):
        """Accepting Requests off must not stop singer-history sync; only the
        connection config gates it."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app.settings["requests_accepting"] = False

            with fake_network(self.singws) as net:
                app._sync_singer_history_async("requests_off")

            history_posts = [p for p in net.posts if "singer_history_sync.php" in p["url"]]
            self.assertTrue(history_posts, "history should sync even when requests are off")
            self.assertEqual(history_posts[0]["json"]["user"], "venue")


if __name__ == "__main__":
    unittest.main()
