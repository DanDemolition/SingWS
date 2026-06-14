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
    app._remote_attention_requests = {}
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app.save_settings = lambda: None
    app._refresh_header_status = lambda: None
    app._apply_idle_background = lambda *args, **kwargs: None
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
            self.assertTrue(app._remote_attention_requests)

    def test_accepting_on_imports_burst_of_remote_requests(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")

            app._reconcile_remote_requests([
                {
                    "request_id": 1000 + i,
                    "singer": f"Singer {i}",
                    "artist": f"Artist {i}",
                    "title": f"Title {i}",
                    "key": 0,
                    "tempo": 0,
                }
                for i in range(10)
            ])

            self.assertEqual(len(app.processed_requests), 10)
            self.assertEqual(app.settings["requests_accepting"], True)

    def test_server_payload_missing_queued_request_does_not_drop_local_queue(self):
        """Relay/v2 may stop listing acked requests; local accepted queue wins."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Ada",
                "songs": [{
                    "song_info": "/music/queued.mp3",
                    "artist": "Artist",
                    "title": "Queued",
                    "remote_request_id": 501,
                    "key": 0,
                    "tempo_percent": 100,
                    "skipped": False,
                }],
                "skipped": False,
                "has_sung": False,
            }]

            app._reconcile_remote_requests([])

            self.assertEqual(app._queue_remote_request_ids(), [501])
            self.assertEqual(app.queue[0]["songs"][0]["title"], "Queued")

    def test_completed_song_does_not_disable_accepting(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app.settings["requests_accepting"] = True

            with fake_network(self.singws):
                app._complete_remote_request(
                    606,
                    entry={"artist": "Artist", "title": "Title"},
                    singer_name="Ada",
                    reason="song_completed",
                )

            self.assertTrue(app.settings["requests_accepting"])

    def test_network_hiccup_does_not_disable_accepting(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app.settings["requests_accepting"] = True

            with fake_network(self.singws, fail=True):
                app._delete_remote_request(
                    707,
                    entry={"artist": "Artist", "title": "Title"},
                    singer_name="Ada",
                    reason="host_remove_song",
                )

            self.assertTrue(app.settings["requests_accepting"])

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

    def test_completed_song_pushes_completed_state_without_delete(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)

            with fake_network(self.singws) as net:
                app._complete_remote_request(
                    505,
                    entry={"artist": "Artist", "title": "Title"},
                    singer_name="Ada",
                    reason="song_completed",
                )

            complete_posts = [p for p in net.posts if "complete_remote_request.php" in p["url"]]
            delete_posts = [p for p in net.posts if "delete_remote_request.php" in p["url"]]
            self.assertEqual(len(complete_posts), 1)
            self.assertEqual(delete_posts, [])
            payload = complete_posts[0]["data"]
            self.assertEqual(int(payload["request_id"]), 505)
            self.assertEqual(payload["state"], "completed")

            tombstone = app._ensure_remote_request_tombstones()["requests"]["505"]
            self.assertEqual(tombstone["status"], "completed")
            self.assertIsNotNone(tombstone["server_synced_at"])

    def test_replace_track_builder_preserves_queue_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            old_entry = {
                "song_info": "/music/old.mp3",
                "key": 3,
                "tempo_percent": 94,
                "remote_request_id": 707,
                "phrase_start_seconds": 12.5,
                "duet_display": "Ada & Bob",
                "skipped": True,
                "custom_metadata": "keep-me",
                "artist": "Old Artist",
                "title": "Old Title",
            }
            track = {
                "artist": "New Artist",
                "title": "New Title",
                "disc_id": "KJ-100",
                "duration": 211,
                "path": "/music/new.mp3",
                "type": "mp3",
                "songid": "song-100",
            }

            new_entry = app._build_queue_entry_from_track_choice(old_entry, track)

            self.assertEqual(new_entry["artist"], "New Artist")
            self.assertEqual(new_entry["title"], "New Title")
            self.assertEqual(new_entry["disc_id"], "KJ-100")
            self.assertEqual(new_entry["duration"], 211)
            self.assertEqual(new_entry["song_info"], "/music/new.mp3")
            self.assertEqual(new_entry["songid"], "song-100")
            self.assertEqual(new_entry["key"], 3)
            self.assertEqual(new_entry["tempo_percent"], 94)
            self.assertEqual(new_entry["remote_request_id"], 707)
            self.assertEqual(new_entry["phrase_start_seconds"], 12.5)
            self.assertEqual(new_entry["duet_display"], "Ada & Bob")
            self.assertTrue(new_entry["skipped"])
            self.assertEqual(new_entry["custom_metadata"], "keep-me")

    def test_replace_track_pushes_authenticated_replace_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            entry = {
                "remote_request_id": 808,
                "artist": "New Artist",
                "title": "New Title",
                "disc_id": "KJ-200",
                "duration": 199,
                "path": "/music/new.mp3",
                "songid": "song-200",
                "key": -2,
                "tempo_percent": 112,
            }

            with fake_network(self.singws) as net:
                app._push_remote_request_replacement(
                    entry,
                    singer_name="Ada",
                    old_artist="Old Artist",
                    old_title="Old Title",
                    source="unit_test",
                )

            replace_posts = [p for p in net.posts if "replace_remote_request.php" in p["url"]]
            self.assertEqual(len(replace_posts), 1)
            payload = replace_posts[0]["data"]
            headers = replace_posts[0]["headers"]
            self.assertEqual(headers["X-API-Key"], "secret-key")
            self.assertEqual(int(payload["request_id"]), 808)
            self.assertEqual(payload["singer_name"], "Ada")
            self.assertEqual(payload["old_artist"], "Old Artist")
            self.assertEqual(payload["old_title"], "Old Title")
            self.assertEqual(payload["artist"], "New Artist")
            self.assertEqual(payload["title"], "New Title")
            self.assertEqual(payload["disc_id"], "KJ-200")
            self.assertEqual(payload["path"], "/music/new.mp3")
            self.assertEqual(int(payload["song_key"]), -2)
            self.assertEqual(int(payload["tempo"]), 12)

    def test_websocket_relay_id_survives_queueing_and_replace_track(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(
                self.singws,
                Path(td) / "tombstones.json",
                settings={**CONNECTED_SETTINGS, "karaoke_normalize_enabled": False},
            )
            relay_track = {
                "artist": "Relay Artist",
                "title": "Relay Title",
                "disc_id": "KJ-300",
                "discid": "KJ-300",
                "duration": 188,
                "path": "/music/relay.mp3",
                "type": "mp3",
                "display": "Relay Artist - Relay Title - KJ-300",
            }
            app._find_song_for_request = lambda artist, title: [relay_track]
            app._relay_processed_request_ids = set()
            app._unmatched_request_sigs = set()
            app._pending_track_data = None
            app.acked = []
            app.ack_remote_requests = lambda ids: app.acked.extend(ids)
            app.post_rotation = lambda: None
            app._schedule_next_up_prescan = lambda: None
            app.process_external_request = self.singws.KaraokeApp.process_external_request.__get__(app, self.singws.KaraokeApp)

            app._handle_relay_requests([
                {"id": 909, "singer": "Ada", "artist": "Relay Artist", "title": "Relay Title", "key": 1, "tempo": -3}
            ])

            self.assertEqual(app.acked, [909])
            entry = app.queue[0]["songs"][0]
            self.assertEqual(entry["remote_request_id"], 909)
            self.assertEqual(entry["key"], 1)
            self.assertEqual(entry["tempo_percent"], 97)

            replacement = {
                "artist": "Replacement Artist",
                "title": "Replacement Title",
                "disc_id": "KJ-301",
                "duration": 205,
                "path": "/music/replacement.mp3",
                "type": "mp3",
            }
            with fake_network(self.singws) as net:
                ok = app._replace_queue_song_with_track(0, 0, replacement, source="relay_replace_regression")

            self.assertTrue(ok)
            replace_posts = [p for p in net.posts if "replace_remote_request.php" in p["url"]]
            self.assertEqual(len(replace_posts), 1)
            payload = replace_posts[0]["data"]
            self.assertEqual(int(payload["request_id"]), 909)
            self.assertEqual(payload["old_artist"], "Relay Artist")
            self.assertEqual(payload["old_title"], "Relay Title")
            self.assertEqual(payload["artist"], "Replacement Artist")
            self.assertEqual(payload["title"], "Replacement Title")
            self.assertEqual(int(payload["song_key"]), 1)
            self.assertEqual(int(payload["tempo"]), -3)

    def test_singer_history_song_tombstone_removes_remote_song_only(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.singer_history = {
                "singers": {
                    "ada": {
                        "name": "Ada",
                        "updated_at": 100,
                        "last_seen_at": 100,
                        "songs": {
                            "old artist|old title|OLD-1": {
                                "artist": "Old Artist",
                                "title": "Old Title",
                                "songid": "OLD-1",
                                "updated_at": 100,
                            }
                        },
                    }
                },
                "deletions": {},
                "song_deletions": {},
            }
            app.queue = [{
                "name": "Ada",
                "songs": [{
                    "artist": "Old Artist",
                    "title": "Old Title",
                    "song_info": "/music/active.mp3",
                    "key": 4,
                    "tempo_percent": 91,
                }],
            }]

            app._merge_remote_singer_history({
                "singers": {
                    "ada": {
                        "name": "Ada",
                        "updated_at": 100,
                        "last_seen_at": 100,
                        "songs": {
                            "old artist|old title|OLD-1": {
                                "artist": "Old Artist",
                                "title": "Old Title",
                                "songid": "OLD-1",
                                "updated_at": 100,
                            }
                        },
                    }
                },
                "song_deletions": {
                    "ada": {
                        "old artist|old title|OLD-1": {
                            "name": "Ada",
                            "song_key": "old artist|old title|OLD-1",
                            "artist": "Old Artist",
                            "title": "Old Title",
                            "songid": "OLD-1",
                            "deleted_at": 200,
                        }
                    }
                },
            })

            self.assertEqual(app.singer_history["singers"]["ada"]["songs"], {})
            self.assertEqual(app.queue[0]["songs"][0]["key"], 4)
            self.assertEqual(app.queue[0]["songs"][0]["tempo_percent"], 91)
            exported = app._export_singer_history_payload()
            self.assertIn("old artist|old title|old-1", exported["song_deletions"]["ada"])

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
