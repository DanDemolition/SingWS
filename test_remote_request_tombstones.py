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

    def __init__(self, fail=False, post_response=None):
        self.fail = fail
        self.post_response = post_response
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        if self.fail:
            raise OSError("server unreachable")
        if self.post_response is not None:
            return self.post_response
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
def fake_network(module=None, fail=False, post_response=None):
    """Make all network paths deterministic.

    Methods that do a local ``import requests`` pick up ``sys.modules`` while
    methods that use the module-level ``requests`` global need the loaded
    module patched too. Threads run inline so sync work completes in-test.
    """
    fake = RecordingRequests(fail=fail, post_response=post_response)
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
    module.DEFERRED_REMOTE_ADDS_PATH = tombstone_path.with_name("deferred_remote_adds.json")
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
    app._deferred_remote_adds = []
    app._remote_request_intake_inflight = set()
    app._queue_revision = 0
    app._remote_attention_requests = {}
    app._disable_accepting_watchdog = True
    app._disable_waitlist_state_pull = True
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app.save_settings = lambda: None
    app._update_deferred_remote_add_status = lambda: None
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
            self.assertFalse(getattr(app, "_remote_attention_requests", {}))
            self.assertFalse(getattr(app, "_waiting_for_add_requests", {}))

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

    def test_remote_burst_batches_queue_display_and_save(self):
        """Accepted remote bursts should not rebuild the queue UI once per song.

        The real add path marks the queue display dirty; reconciliation should
        coalesce that into one final refresh and one persistence request.
        """
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.karaoke_playing = True
            display_calls = []
            save_calls = []
            app.update_queue_display = lambda: display_calls.append("refresh")
            app.save_data = lambda: save_calls.append("save")

            def process(req):
                app.processed_requests.append(req)
                app._request_queue_display_refresh()
                return True

            app.process_external_request = process

            app._reconcile_remote_requests([
                {
                    "request_id": 1200 + i,
                    "singer": f"Singer {i}",
                    "artist": f"Artist {i}",
                    "title": f"Title {i}",
                    "key": 0,
                    "tempo": 0,
                }
                for i in range(10)
            ])

            self.assertEqual(len(app.processed_requests), 10)
            self.assertEqual(display_calls, ["refresh"])
            self.assertEqual(save_calls, ["save"])

    def test_deferred_remote_add_persists_and_flushes_between_singers(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["defer_remote_adds_until_between_singers"] = True
            app.karaoke_playing = True
            added = []
            app._add_song_to_queue = lambda singer, song_data, track=None, remote_meta=None: added.append((singer, song_data, track, remote_meta)) or True
            app._log_remote_request_diag = lambda *args, **kwargs: None
            app._clear_remote_attention_request = lambda *args, **kwargs: None

            payload = {
                "_ok": True,
                "request_id": 1701,
                "request_time": 10,
                "singer": "Ada",
                "song_data": ("/music/song.mp3", 0, 100),
                "track": {"path": "/music/song.mp3", "artist": "Artist", "title": "Title"},
                "remote_meta": {"request_id": 1701, "singer": "Ada", "artist": "Artist", "title": "Title"},
            }

            self.assertTrue(app._apply_resolved_remote_add(payload, allow_defer=True))
            self.assertEqual(added, [])
            self.assertEqual(app._deferred_remote_request_ids(), {1701})
            self.assertTrue(self.singws.DEFERRED_REMOTE_ADDS_PATH.exists())

            app.karaoke_playing = False
            self.assertEqual(app._flush_deferred_remote_adds("test"), 1)
            self.assertEqual(len(added), 1)
            self.assertEqual(app._deferred_remote_adds, [])

    def test_deferred_remote_add_counts_toward_singer_limit(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["defer_remote_adds_until_between_singers"] = True
            app.settings["limit_pending_max"] = 2
            app.karaoke_playing = True
            app.queue = [{
                "name": "Ada",
                "songs": [{"artist": "Artist", "title": "Already In", "display": "Artist • Already In"}],
            }]

            second = {
                "_ok": True,
                "request_id": 1801,
                "request_time": 10,
                "singer": "Ada",
                "song_data": ("/music/second.mp3", 0, 100),
                "track": {"path": "/music/second.mp3", "artist": "Artist", "title": "Second"},
                "remote_meta": {"request_id": 1801, "singer": "Ada", "artist": "Artist", "title": "Second"},
            }
            third = {
                "_ok": True,
                "request_id": 1802,
                "request_time": 11,
                "singer": "Ada",
                "song_data": ("/music/third.mp3", 0, 100),
                "track": {"path": "/music/third.mp3", "artist": "Artist", "title": "Third"},
                "remote_meta": {"request_id": 1802, "singer": "Ada", "artist": "Artist", "title": "Third"},
            }

            self.assertTrue(app._apply_resolved_remote_add(second, allow_defer=True))
            self.assertEqual(app._deferred_remote_request_ids(), {1801})
            self.assertFalse(app._apply_resolved_remote_add(third, allow_defer=True))
            self.assertEqual(app._deferred_remote_request_ids(), {1801})

    def test_server_terminal_state_removes_deferred_remote_add(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._deferred_remote_adds = [{
                "_ok": True,
                "request_id": 1801,
                "request_time": 1,
                "singer": "Ada",
                "song_data": ["/music/song.mp3", 0, 100],
                "track": {"path": "/music/song.mp3"},
                "remote_meta": {"request_id": 1801},
            }]

            app._reconcile_remote_requests([
                {"request_id": 1801, "singer": "Ada", "artist": "Artist", "title": "Title", "state": "removed"}
            ])

            self.assertEqual(app._deferred_remote_adds, [])

    def test_waiting_or_failed_requests_feed_app_waiting_list_not_rotation(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True

            app._reconcile_remote_requests([
                {
                    "request_id": 1301,
                    "singer": "Ada",
                    "artist": "Artist",
                    "title": "Waiting Song",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                },
                {
                    "request_id": 1302,
                    "singer": "Grace",
                    "artist": "Artist",
                    "title": "Failed Song",
                    "state": "failed",
                    "last_error": "No local match",
                },
                {
                    "request_id": 1303,
                    "singer": "Jake",
                    "artist": "Missing Artist",
                    "title": "Missing Song",
                    "state": "failed_needs_review",
                    "pending_reason": "artist_title_not_found",
                    "pending_status": "Needs Review",
                    "request_source": "kiosk",
                },
            ])

            self.assertEqual(app.processed_requests, [])
            self.assertEqual(app._waiting_for_add_count(), 3)
            self.assertEqual(
                sorted(app._waiting_for_add_requests.keys()),
                [1301, 1302, 1303],
            )
            row = app._waiting_for_add_row(app._waiting_for_add_requests[1303], "waitlist", selectable=True)
            self.assertEqual(row["status_label"], "Needs Review")

    def test_requests_off_ignores_stale_pending_with_one_existing_song(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["requests_accepting"] = False
            app.queue = [{
                "name": "Ada",
                "songs": [{
                    "artist": "Artist",
                    "title": "Already Queued",
                    "song_info": "/music/queued.mp3",
                    "remote_request_id": 1801,
                }],
            }]

            app._reconcile_remote_requests([
                {"request_id": 1802, "singer": "Ada", "artist": "Artist", "title": "Second"}
            ])

            self.assertEqual(app.processed_requests, [])
            self.assertNotIn(1802, app._waiting_for_add_requests)
            self.assertFalse(getattr(app, "_remote_attention_requests", {}))

    def test_requests_off_does_not_accumulate_hidden_pending_waitlist(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["requests_accepting"] = False
            app.queue = [{
                "name": "Ada",
                "songs": [{
                    "artist": "Artist",
                    "title": "Already Queued",
                    "song_info": "/music/queued.mp3",
                    "remote_request_id": 1811,
                }],
            }]

            app._reconcile_remote_requests([
                {"request_id": 1812, "singer": "Ada", "artist": "Artist", "title": "Second"},
            ])
            app._reconcile_remote_requests([
                {"request_id": 1812, "singer": "Ada", "artist": "Artist", "title": "Second"},
                {"request_id": 1813, "singer": "Ada", "artist": "Artist", "title": "Third"},
            ])

            self.assertNotIn(1812, app._waiting_for_add_requests)
            self.assertNotIn(1813, app._waiting_for_add_requests)
            self.assertEqual(app.processed_requests, [])

    def test_requests_off_does_not_flush_pending_rows_into_waitlist(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["requests_accepting"] = False

            app._reconcile_remote_requests([
                {"request_id": 1821, "singer": "Ada", "artist": "Artist", "title": "One"},
                {"request_id": 1822, "singer": "Ada", "artist": "Artist", "title": "Two"},
                {"request_id": 1823, "singer": "Ada", "artist": "Artist", "title": "Three"},
            ])

            self.assertEqual(sorted(app._waiting_for_add_requests.keys()), [])
            self.assertEqual(app.processed_requests, [])

    def test_accepting_on_blocks_remote_request_at_active_limit(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Ada",
                "songs": [
                    {"artist": "Artist", "title": "One", "song_info": "/music/one.mp3", "remote_request_id": 1831},
                    {"artist": "Artist", "title": "Two", "song_info": "/music/two.mp3", "remote_request_id": 1832},
                ],
            }]

            app._reconcile_remote_requests([
                {"request_id": 1833, "singer": "Ada", "artist": "Artist", "title": "Three"}
            ])

            self.assertEqual(app.processed_requests, [])
            self.assertNotIn(1833, getattr(app, "_waiting_for_add_requests", {}))

    def test_waiting_list_excludes_already_queued_remote_ids(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Ada",
                "songs": [{
                    "artist": "Artist",
                    "title": "Already Queued",
                    "song_info": "/music/queued.mp3",
                    "remote_request_id": 1401,
                }],
            }]

            app._reconcile_remote_requests([
                {
                    "request_id": 1401,
                    "singer": "Ada",
                    "artist": "Artist",
                    "title": "Already Queued",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                },
            ])

            self.assertEqual(app._waiting_for_add_count(), 0)

    def test_waiting_list_excludes_history_request_already_in_singer_queue(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True
            app.queue = [{
                "name": "Dan",
                "songs": [
                    {
                        "artist": "Manual Artist",
                        "title": "Manual Song",
                        "song_info": "/music/manual.mp3",
                        "remote_request_id": 2401,
                        "request_source": "server",
                    },
                    {
                        "artist": "History Artist",
                        "title": "History Song",
                        "song_info": "/music/history.mp3",
                        "remote_request_id": 2402,
                        "source": "history",
                    },
                ],
            }]

            app._set_waiting_for_add_requests([
                {
                    "request_id": 2402,
                    "singer": "Dan",
                    "artist": "History Artist",
                    "title": "History Song",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                    "request_source": "singer_history",
                    "selected_disc_id": "HIST-77",
                },
            ])

            self.assertEqual(app._waiting_for_add_count(), 0)
            self.assertNotIn(2402, app._waiting_for_add_requests)
            self.assertIn(2402, app._waiting_for_add_handled_ids)

    def test_pressing_play_state_change_does_not_resurrect_accepted_history_request(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Dan",
                "songs": [
                    {"artist": "Manual Artist", "title": "Manual Song", "remote_request_id": 2411},
                    {"artist": "History Artist", "title": "History Song", "song_info": "/music/history.mp3", "remote_request_id": 2412},
                ],
            }]
            pending = {
                "request_id": 2412,
                "singer": "Dan",
                "artist": "History Artist",
                "title": "History Song",
                "state": "waiting",
                "pending_reason": "rotation_full",
                "request_source": "singer_history",
            }

            app._set_waiting_for_add_requests([pending])
            app.queue[0]["songs"].pop(0)
            app._set_waiting_for_add_requests([pending])

            self.assertEqual([song["title"] for song in app.queue[0]["songs"]], ["History Song"])
            self.assertEqual(app._waiting_for_add_count(), 0)
            self.assertNotIn(2412, app._waiting_for_add_requests)

    def test_startup_purges_stale_past_show_waitlist_request(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True
            app.settings["waitlist_stale_hours"] = 24
            purged = {}
            app._cleanup_terminal_removed_requests = lambda items: purged.update(items)

            old_stamp = int(self.singws.time.time() - (3 * 24 * 3600))
            app._set_waiting_for_add_requests([{
                "request_id": 2501,
                "singer": "Past Singer",
                "artist": "Past Artist",
                "title": "Past Song",
                "state": "waiting",
                "pending_reason": "rotation_full",
                "received_at_server": old_stamp,
            }])

            self.assertEqual(app._waiting_for_add_count(), 0)
            self.assertNotIn(2501, app._waiting_for_add_requests)
            self.assertIn(2501, app._waiting_for_add_handled_ids)
            self.assertEqual(purged[2501]["state"], "removed")
            self.assertEqual(purged[2501]["removal_reason"], "stale_waitlist_startup_cleanup")

    def test_startup_keeps_recent_same_show_waitlist_request(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True
            app.settings["waitlist_stale_hours"] = 24
            purged = {}
            app._cleanup_terminal_removed_requests = lambda items: purged.update(items)

            recent_stamp = int(self.singws.time.time() - 3600)
            app._set_waiting_for_add_requests([{
                "request_id": 2502,
                "singer": "Current Singer",
                "artist": "Current Artist",
                "title": "Current Song",
                "state": "waiting",
                "pending_reason": "rotation_full",
                "received_at_server": recent_stamp,
            }])

            self.assertEqual(app._waiting_for_add_count(), 1)
            self.assertIn(2502, app._waiting_for_add_requests)
            self.assertEqual(purged, {})

    def test_waiting_add_now_cannot_duplicate_already_queued_history_request(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Dan",
                "songs": [{"artist": "History Artist", "title": "History Song", "song_info": "/music/history.mp3", "remote_request_id": 2421}],
            }]
            req = {
                "request_id": 2421,
                "singer": "Dan",
                "artist": "History Artist",
                "title": "History Song",
                "state": "waiting",
                "pending_reason": "rotation_full",
                "request_source": "singer_history",
            }
            app._waiting_for_add_requests = {2421: req}
            app._waiting_for_add_handled_ids = set()
            app._show_processing_notification = lambda *args, **kwargs: None
            delivered = []
            added = []
            app._mark_waiting_for_add_delivered_async = lambda rid, req=None: delivered.append(rid)
            app._add_song_to_queue = lambda *args, **kwargs: added.append((args, kwargs)) or True

            track = {"path": "/music/history.mp3", "artist": "History Artist", "title": "History Song"}
            self.assertTrue(app._add_waiting_for_add_track(req, track))

            self.assertEqual(added, [])
            self.assertEqual(delivered, [2421])
            self.assertEqual(len(app.queue[0]["songs"]), 1)
            self.assertNotIn(2421, app._waiting_for_add_requests)
            self.assertIn(2421, app._waiting_for_add_handled_ids)

    def test_resolved_remote_add_uses_request_id_not_history_signature_for_dedupe(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Dan",
                "songs": [{"artist": "History Artist", "title": "History Song", "song_info": "/music/history.mp3", "remote_request_id": 2430}],
            }]
            added = []
            app._add_song_to_queue = lambda *args, **kwargs: added.append((args, kwargs)) or True
            delivered = []
            app._mark_waiting_for_add_delivered_async = lambda rid, req=None: delivered.append(rid)

            payload = {
                "_ok": True,
                "request_id": 2431,
                "request_time": 10,
                "singer": "Dan",
                "song_data": ("/music/history.mp3", 0, 100),
                "track": {"path": "/music/history.mp3", "artist": "History Artist", "title": "History Song"},
                "remote_meta": {
                    "request_id": 2431,
                    "singer": "Dan",
                    "artist": "History Artist",
                    "title": "History Song",
                    "request_source": "singer_history",
                },
            }

            self.assertTrue(app._apply_resolved_remote_add(payload, allow_defer=True))
            self.assertEqual(len(added), 1)
            self.assertEqual(added[0][1]["remote_meta"]["request_id"], 2431)
            self.assertEqual(delivered, [])
            self.assertEqual(len(app.queue[0]["songs"]), 1)

    def test_remote_sync_keeps_host_order_when_server_order_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Ada",
                "songs": [
                    {"artist": "Artist", "title": "B", "song_info": "/music/b.mp3", "remote_request_id": 3002},
                    {"artist": "Artist", "title": "A", "song_info": "/music/a.mp3", "remote_request_id": 3001},
                ],
                "host_order_updated_at": 2000,
                "order_revision": 5,
                "last_order_source": "host",
            }]

            app._reconcile_remote_requests([
                {"request_id": 3001, "singer": "Ada", "artist": "Artist", "title": "A", "sort_order": 1, "last_order_source": "server"},
                {"request_id": 3002, "singer": "Ada", "artist": "Artist", "title": "B", "sort_order": 2, "last_order_source": "server"},
            ])

            self.assertEqual([song["remote_request_id"] for song in app.queue[0]["songs"]], [3002, 3001])

    def test_remote_sync_applies_newer_host_order_from_server_sort_order(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Ada",
                "songs": [
                    {"artist": "Artist", "title": "A", "song_info": "/music/a.mp3", "remote_request_id": 3001},
                    {"artist": "Artist", "title": "B", "song_info": "/music/b.mp3", "remote_request_id": 3002},
                ],
                "host_order_updated_at": 1000,
                "order_revision": 1,
                "last_order_source": "host",
            }]

            app._reconcile_remote_requests([
                {"request_id": 3001, "singer": "Ada", "artist": "Artist", "title": "A", "sort_order": 2, "host_order_updated_at": 3000, "order_revision": 6, "last_order_source": "host"},
                {"request_id": 3002, "singer": "Ada", "artist": "Artist", "title": "B", "sort_order": 1, "host_order_updated_at": 3000, "order_revision": 6, "last_order_source": "host"},
            ])

            self.assertEqual([song["remote_request_id"] for song in app.queue[0]["songs"]], [3002, 3001])

    def test_sync_restart_does_not_resurrect_accepted_history_pending_item(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Dan",
                "songs": [
                    {"artist": "Manual Artist", "title": "Manual Song", "remote_request_id": 2441},
                    {"artist": "History Artist", "title": "History Song", "song_info": "/music/history.mp3", "remote_request_id": 2442},
                ],
            }]
            pending = {
                "request_id": 2442,
                "singer": "Dan",
                "artist": "History Artist",
                "title": "History Song",
                "state": "waiting",
                "pending_reason": "rotation_full",
                "request_source": "singer_history",
            }

            app._set_waiting_for_add_requests([pending])
            app._set_waiting_for_add_requests([pending])
            app._reconcile_remote_requests([pending])

            self.assertEqual(app._waiting_for_add_count(), 0)
            self.assertNotIn(2442, app._waiting_for_add_requests)
            self.assertEqual(app.processed_requests, [])

    def test_waiting_request_can_add_selected_local_track(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            req = {
                "request_id": 1501,
                "singer": "Ada",
                "artist": "Remote Artist",
                "title": "Remote Title",
                "key": -1,
                "tempo": 8,
            }
            app._waiting_for_add_requests = {1501: req}
            app._waiting_for_add_handled_ids = set()
            app._refresh_waiting_for_add_view = lambda: None
            app._show_processing_notification = lambda *args, **kwargs: None
            delivered = []
            added = []
            app._mark_waiting_for_add_delivered_async = lambda rid, req=None: delivered.append(rid)

            def add_song(singer, song_data, track=None, remote_meta=None):
                added.append((singer, song_data, track, remote_meta))
                return True

            app._add_song_to_queue = add_song

            track = {
                "path": "/music/local-match.mp3",
                "artist": "Local Artist",
                "title": "Local Title",
            }
            self.assertTrue(app._add_waiting_for_add_track(req, track))

            self.assertEqual(added, [("Ada", ("/music/local-match.mp3", -1, 108), track, req)])
            self.assertEqual(delivered, [1501])
            self.assertIn(1501, app._waiting_for_add_handled_ids)
            self.assertNotIn(1501, app._waiting_for_add_requests)

    def test_waiting_sections_only_show_pending_waitlist_rows(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [{
                "name": "Ada",
                "songs": [{
                    "artist": "Active Artist",
                    "title": "Active Title",
                    "remote_request_id": 1601,
                    "request_time": 1712340000,
                }],
            }]
            app._waiting_for_add_requests = {
                1602: {
                    "request_id": 1602,
                    "singer": "Bea",
                    "artist": "Wait Artist",
                    "title": "Wait Title",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                    "created_at": 1712340300,
                    "selected_version": "SC",
                    "selected_disc_id": "SC-123",
                    "received_at_server": "2026-06-28T05:00:00Z",
                    "duration_secs": 185,
                },
            }
            app._waiting_for_add_recent_terminal_requests = {
                1603: {
                    "request_id": 1603,
                    "singer": "Cal",
                    "artist": "Done Artist",
                    "title": "Done Title",
                    "state": "sung",
                    "completed_at": 1712340600,
                },
                1604: {
                    "request_id": 1604,
                    "singer": "Dee",
                    "artist": "Skip Artist",
                    "title": "Skip Title",
                    "state": "removed",
                    "removed_at": 1712340900,
                },
            }

            sections = app._waiting_for_add_sections()
            # Completed and Removed/Skipped are no longer rendered; only
            # actionable rows show. Terminal removed rows are purged from the
            # server instead.
            self.assertEqual([section["key"] for section in sections], ["waitlist"])

            row = sections[0]["rows"][0]
            text = app._waiting_for_add_row_text(row)
            self.assertEqual(row["status_label"], "Waitlisted")
            self.assertEqual(row["singer"], "Bea")
            self.assertTrue(row["selectable"])
            self.assertIn("Singer: Bea", text)
            self.assertIn("Song: Wait Title", text)
            self.assertIn("Artist: Wait Artist", text)
            self.assertIn("Version: Requested: SC / SC-123", text)
            self.assertIn("Length: 3:05", text)
            self.assertIn("Server:", text)

    def test_pending_acceptance_section_is_distinct_from_waitlist(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["defer_remote_adds_until_between_singers"] = True
            app._deferred_remote_adds = [{
                "_ok": True,
                "request_id": 1901,
                "request_time": 1712340100,
                "singer": "Ada",
                "artist": "Blue Artist",
                "title": "Blue Title",
                "song_data": ("/music/blue.mp3", 0, 100),
                "track": {"path": "/music/blue.mp3", "artist": "Blue Artist", "title": "Blue Title", "disc_id": "KV-900"},
                "remote_meta": {"request_id": 1901, "singer": "Ada", "artist": "Blue Artist", "title": "Blue Title"},
            }]
            app._waiting_for_add_requests = {
                1901: {
                    "request_id": 1901,
                    "singer": "Ada",
                    "artist": "Blue Artist",
                    "title": "Blue Title",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                },
                1902: {
                    "request_id": 1902,
                    "singer": "Bea",
                    "artist": "Wait Artist",
                    "title": "Wait Title",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                },
            }

            sections = app._waiting_for_add_sections()
            self.assertEqual([section["key"] for section in sections], ["pending_acceptance", "waitlist"])
            pending_row = sections[0]["rows"][0]
            self.assertEqual(pending_row["request_id"], 1901)
            self.assertEqual(pending_row["status_label"], "Pending Acceptance")
            self.assertFalse(pending_row["selectable"])
            self.assertEqual(pending_row["queue_position"], 1)
            self.assertEqual(app._pending_acceptance_count(), 1)
            self.assertIn("Queue: #1", app._waiting_for_add_row_text(pending_row))
            self.assertEqual([row["request_id"] for row in sections[1]["rows"]], [1902])

    def test_waitlist_action_count_includes_ordinary_waitlisted_rows(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True
            app._waiting_for_add_requests = {
                1911: {
                    "request_id": 1911,
                    "singer": "Bea",
                    "artist": "Wait Artist",
                    "title": "Wait Title",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                },
            }

            self.assertEqual(app._pending_acceptance_count(), 0)
            self.assertEqual(app._waiting_for_add_count(), 1)

    def test_delivered_rows_do_not_reappear_as_waitlisted(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True

            app._set_waiting_for_add_requests([
                {
                    "request_id": 1912,
                    "singer": "Ada",
                    "artist": "Old Artist",
                    "title": "Old Title",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                    "sent": 1,
                },
                {
                    "request_id": 1913,
                    "singer": "Bea",
                    "artist": "Accepted Artist",
                    "title": "Accepted Title",
                    "state": "accepted",
                    "pending_reason": "rotation_full",
                },
            ])

            self.assertEqual(app._waiting_for_add_requests, {})
            self.assertIn(1912, app._waiting_for_add_handled_ids)
            self.assertIn(1913, app._waiting_for_add_handled_ids)

    def test_reconcile_does_not_reprocess_delivered_pending_rows(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["use_waiting_for_add"] = True

            app._reconcile_remote_requests([
                {
                    "request_id": 1914,
                    "singer": "Cal",
                    "artist": "Done Artist",
                    "title": "Done Title",
                    "state": "waiting",
                    "pending_reason": "rotation_full",
                    "delivered": 1,
                },
            ])

            self.assertEqual(app.processed_requests, [])
            self.assertEqual(app._waiting_for_add_requests, {})

    def test_add_pending_now_flushes_during_playback(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.settings["defer_remote_adds_until_between_singers"] = True
            app.karaoke_playing = True
            added = []
            app._add_song_to_queue = lambda singer, song_data, track=None, remote_meta=None: added.append((singer, song_data, track, remote_meta)) or True
            app._log_remote_request_diag = lambda *args, **kwargs: None
            app._clear_remote_attention_request = lambda *args, **kwargs: None
            app._show_processing_notification = lambda *args, **kwargs: None
            app._deferred_remote_adds = [{
                "_ok": True,
                "request_id": 1903,
                "request_time": 1712340100,
                "singer": "Ada",
                "song_data": ("/music/now.mp3", 0, 100),
                "track": {"path": "/music/now.mp3", "artist": "Now Artist", "title": "Now Title"},
                "remote_meta": {"request_id": 1903, "singer": "Ada", "artist": "Now Artist", "title": "Now Title"},
            }]

            app._manual_add_deferred_remote_adds()

            self.assertEqual(len(added), 1)
            self.assertEqual(app._deferred_remote_adds, [])

    def test_waiting_replacement_preserves_request_slot_and_order(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._waiting_for_add_requests = {
                1701: {
                    "request_id": 1701,
                    "singer": "Ada",
                    "artist": "Original Artist",
                    "title": "Original Title",
                    "state": "waiting",
                    "created_at": 1712340000,
                },
                1702: {
                    "request_id": 1702,
                    "singer": "Bea",
                    "artist": "Later Artist",
                    "title": "Later Title",
                    "state": "waiting",
                    "created_at": 1712340300,
                },
            }
            app._refresh_waiting_for_add_view = lambda: None
            app._show_processing_notification = lambda *args, **kwargs: None
            pushed = []
            app._push_remote_request_replacement = lambda entry, **kwargs: pushed.append((entry, kwargs))

            track = {
                "path": "/music/replacement.mp3",
                "artist": "Replacement Artist",
                "title": "Replacement Title",
                "disc_id": "KV-123",
                "duration_secs": 242,
            }

            self.assertTrue(app._replace_waiting_for_add_track(app._waiting_for_add_requests[1701], track))

            updated = app._waiting_for_add_requests[1701]
            self.assertEqual(updated["request_id"], 1701)
            self.assertEqual(updated["singer"], "Ada")
            self.assertEqual(updated["created_at"], 1712340000)
            self.assertEqual(updated["artist"], "Replacement Artist")
            self.assertEqual(updated["title"], "Replacement Title")
            self.assertEqual(updated["replacement_track"]["path"], "/music/replacement.mp3")
            self.assertEqual([row["request_id"] for row in app._waiting_for_add_sections()[0]["rows"]], [1701, 1702])
            self.assertEqual(pushed[0][0]["remote_request_id"], 1701)

    def test_waitlist_marks_only_current_show_first_time_singers(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [
                {"name": "Ada", "songs": [], "has_sung": False},
                {"name": "Bea", "songs": [], "has_sung": True},
            ]
            app._waiting_for_add_requests = {
                1751: {"request_id": 1751, "singer": "Ada", "artist": "Artist", "title": "One", "created_at": 1},
                1752: {"request_id": 1752, "singer": "Bea", "artist": "Artist", "title": "Two", "created_at": 2},
            }

            rows = {row["request_id"]: row for row in app._waiting_for_add_sections()[0]["rows"]}
            self.assertTrue(rows[1751]["first_time_singer"])
            self.assertIn("First turn", app._waiting_for_add_row_text(rows[1751]))
            self.assertFalse(rows[1752]["first_time_singer"])

            app.queue[0]["has_sung"] = True
            rows = {row["request_id"]: row for row in app._waiting_for_add_sections()[0]["rows"]}
            self.assertFalse(rows[1751]["first_time_singer"])

    def test_waitlist_toggle_syncs_to_server_and_pulls_server_changes(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app.settings["use_waiting_for_add"] = False
            app._run_on_ui_thread = lambda fn: fn()
            app._refresh_waitlist_toggle_button = lambda *args, **kwargs: None

            with fake_network(self.singws) as fake:
                app._set_waitlist_enabled_from_host(True, reason="test_toggle")

            self.assertTrue(app.settings["use_waiting_for_add"])
            self.assertEqual(len(fake.posts), 1)
            self.assertTrue(fake.posts[0]["url"].endswith("/api/v1/set_waiting_for_add.php"))
            self.assertEqual(fake.posts[0]["data"]["use_waiting_for_add"], "1")

            app.settings["use_waiting_for_add"] = True
            app._net_fetch_waitlist_enabled = lambda base_url, tenant: False
            app._disable_waitlist_state_pull = False
            with mock.patch.object(threading, "Thread", _InlineThread):
                self.assertTrue(app._sync_waitlist_state_from_server_async(reason="test_pull", min_interval_sec=0))
            self.assertFalse(app.settings["use_waiting_for_add"])

    def test_lan_only_browser_access_option_removed_from_desktop_source(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")

        self.assertNotIn("host_controls_lan_only", source)
        self.assertNotIn("LAN-only browser access", source)

    def test_network_settings_exposes_waitlist_checkbox(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")

        self.assertIn('QCheckBox("Enable Waitlist")', source)
        self.assertIn("toggle_waitlist_from_network_dialog", source)
        self.assertIn("_net_set_waitlist_enabled", source)

    def test_delivered_v2_history_rows_are_not_imported_as_new_requests(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")

            app._reconcile_remote_requests([
                {
                    "request_id": 471,
                    "singer": "Old Singer",
                    "artist": "Old Artist",
                    "title": "Old Title",
                    "key": 0,
                    "tempo": 0,
                    "sent": True,
                    "delivered": True,
                    "state": "delivered",
                },
                {
                    "request_id": 1001,
                    "singer": "New Singer",
                    "artist": "New Artist",
                    "title": "New Title",
                    "key": 0,
                    "tempo": 0,
                    "state": "pending",
                },
            ])

            self.assertEqual(
                [req["request_id"] for req in app.processed_requests],
                [1001],
            )

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

    def test_server_removed_tombstone_drops_song_but_preserves_singer_row(self):
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

            app._reconcile_remote_requests([
                {
                    "request_id": 501,
                    "singer": "Ada",
                    "artist": "Artist",
                    "title": "Queued",
                    "state": "removed",
                    "sent": True,
                    "removed_at": 1712345678,
                }
            ])

            self.assertEqual([s["name"] for s in app.queue], ["Ada"])
            self.assertEqual(app.queue[0]["songs"], [])
            self.assertIn(501, app._remote_removed_request_ids)

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

    def test_background_reconcile_repairs_server_accepting_without_flipping_host_state(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app.settings["requests_accepting"] = True
            app._disable_accepting_watchdog = False
            app._net_fetch_accepting = lambda base_url, tenant: False
            repairs = []

            def repair(base_url, tenant, api_key, accepting):
                repairs.append((base_url, tenant, api_key, accepting))
                return True, ""

            app._net_set_accepting = repair

            with mock.patch.object(threading, "Thread", _InlineThread):
                app._reconcile_remote_requests([])

            self.assertTrue(app.settings["requests_accepting"])
            self.assertEqual(repairs, [("https://beta.wskar.com", "venue", "secret-key", True)])

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

    def test_reused_request_id_with_different_signature_is_not_tombstoned(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._record_remote_request_tombstone(
                303,
                entry={"artist": "Old Artist", "title": "Old Title"},
                singer_name="Old Singer",
                reason="host_remove_song",
            )

            app._reconcile_remote_requests([
                {"request_id": 303, "singer": "New Singer", "artist": "New Artist", "title": "New Title", "key": 0, "tempo": 0}
            ])

            self.assertEqual([req["request_id"] for req in app.processed_requests], [303])
            self.assertNotIn("303", app._ensure_remote_request_tombstones()["requests"])

    def test_reused_request_id_clears_stale_removed_memory(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._record_remote_request_tombstone(
                460,
                entry={"artist": "Alice Cooper", "title": "I'm Eighteen"},
                singer_name="Jorge",
                reason="host_clear_queue",
            )
            app._remote_removed_request_ids.add(460)

            app._reconcile_remote_requests([
                {"request_id": 460, "singer": "Dan", "artist": "Avenged Sevenfold", "title": "Almost Easy", "key": 0, "tempo": 0}
            ])

            self.assertEqual([req["request_id"] for req in app.processed_requests], [460])
            self.assertNotIn(460, app._remote_removed_request_ids)
            self.assertNotIn("460", app._ensure_remote_request_tombstones()["requests"])

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
            self.assertIn("old artist|old title", exported["song_deletions"]["ada"])

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

    def test_missing_remote_request_marks_tombstone_synced(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            app._record_remote_request_tombstone(
                707,
                entry={"artist": "Artist", "title": "Title"},
                singer_name="Ada",
                reason="host_remove_song",
            )
            tombstone = app._ensure_remote_request_tombstones()["requests"]["707"]
            self.assertIsNone(tombstone["server_synced_at"])

            missing = FakeResponse(404, {"ok": False, "error": "request_not_found"}, '{"ok":false,"error":"request_not_found"}')
            with fake_network(self.singws, post_response=missing) as net:
                app._sync_remote_removal_tombstones_async("retry")

            removal_posts = [p for p in net.posts if "complete_remote_request.php" in p["url"]]
            self.assertEqual(len(removal_posts), 1)
            tombstone = app._ensure_remote_request_tombstones()["requests"]["707"]
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

    def test_singer_history_normalizes_duplicate_song_versions(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            normalized = app._normalize_singer_history_store({
                "singers": {
                    "John Smith": {
                        "name": "John Smith",
                        "updated_at": 300,
                        "last_seen_at": 300,
                        "songs": {
                            "journey|don't stop believin'|SC-1": {
                                "artist": "Journey",
                                "title": "Don't Stop Believin'",
                                "songid": "SC-1",
                                "disc_id": "SC-1",
                                "play_count": 1,
                                "first_performed_at": 100,
                                "last_performed_at": 100,
                                "updated_at": 100,
                            },
                            "journey|dont stop believin|KV-2": {
                                "artist": "JOURNEY",
                                "title": "dont stop believin",
                                "songid": "KV-2",
                                "disc_id": "KV-2",
                                "play_count": 4,
                                "first_performed_at": 200,
                                "last_performed_at": 300,
                                "updated_at": 300,
                            },
                        },
                    },
                },
            })

            songs = normalized["singers"]["john smith"]["songs"]
            self.assertEqual(list(songs.keys()), ["journey|dont stop believin"])
            song = songs["journey|dont stop believin"]
            self.assertEqual(song["play_count"], 5)
            self.assertEqual(song["last_performed_at"], 300)
            self.assertEqual(song["disc_id"], "KV-2")

    def test_repeated_singer_history_play_updates_one_item(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._karaoke_tempo_percent = 100
            for idx in range(5):
                app._record_singer_history_play(
                    "John Smith",
                    {
                        "artist": "Journey",
                        "title": "Don't Stop Believin'" if idx % 2 == 0 else "dont stop believin",
                        "disc_id": f"DISC-{idx}",
                    },
                    f"/music/DISC-{idx}.mp3",
                    key=0,
                    tempo_percent=100,
                )

            songs = app.singer_history["singers"]["john smith"]["songs"]
            self.assertEqual(list(songs.keys()), ["journey|dont stop believin"])
            self.assertEqual(songs["journey|dont stop believin"]["play_count"], 5)


class HostRemovalAuthorityTests(unittest.TestCase):
    """Host removals must be authoritative: stale server rows must never
    resurrect a removed song, and accidental duplicates from sync drift are
    collapsed. Regression for the re-add-after-manual-remove bug: the old
    tombstone matcher deleted the tombstone on ANY song-signature drift
    (duet display 'Dan & Amy' vs queue singer 'Dan', typed vs library
    metadata), treating it as id reuse."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _entry(self, title, rid=None, artist="Artist", path=None, disc=None):
        e = {"song_info": path or f"/tmp/{title.lower().replace(' ', '_')}.mp3",
             "key": 0, "skipped": False, "artist": artist, "title": title}
        if rid is not None:
            e["remote_request_id"] = rid
        if disc is not None:
            e["disc_id"] = disc
        return e

    def _singer(self, name, entries):
        return {"name": name, "songs": entries, "skipped": False,
                "has_sung": False, "round_sung": False, "rotation_marker": False}

    # -- manual removal followed by normal sync --------------------------------

    def test_manual_remove_then_sync_does_not_readd(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json", settings=CONNECTED_SETTINGS)
            entry = self._entry("Song A", rid=123)
            app.queue = [self._singer("Dan", [entry])]

            with fake_network(self.singws) as net:
                app._delete_remote_request(123, entry=entry, singer_name="Dan", reason="host_remove_song")
            del app.queue[0]["songs"][0]
            # exact item id was sent to the server
            posts = [p for p in net.posts if "complete_remote_request.php" in p["url"]]
            self.assertEqual(int(posts[0]["data"]["request_id"]), 123)
            self.assertEqual(posts[0]["data"]["state"], "removed")

            app._reconcile_remote_requests([
                {"request_id": 123, "singer": "Dan", "artist": "Artist", "title": "Song A",
                 "key": 0, "tempo": 0, "state": "pending"}
            ])
            self.assertEqual(app.processed_requests, [])
            self.assertEqual(app.queue[0]["songs"], [])

    # -- signature drift must NOT destroy the tombstone (the actual bug) -------

    def test_stale_row_with_drifted_signature_stays_removed(self):
        """Duet display + typed-metadata drift used to delete the tombstone
        and resurrect the song on the same pass."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            entry = self._entry("Rocket Man", rid=123, artist="Elton John")
            app.queue = [self._singer("Dan", [entry])]
            app._record_remote_request_tombstone(123, entry=entry, singer_name="Dan",
                                                 reason="host_remove_song")
            del app.queue[0]["songs"][0]

            stale = {"request_id": 123, "singer": "Dan & Amy", "artist": "Elton  John",
                     "title": "Rocketman", "key": 0, "tempo": 0, "state": "pending"}
            app._reconcile_remote_requests([stale])

            self.assertEqual(app.processed_requests, [])
            self.assertIn("123", app._ensure_remote_request_tombstones()["requests"])
            # repeated polls stay ignored (idempotent)
            app._reconcile_remote_requests([stale])
            self.assertEqual(app.processed_requests, [])

    def test_true_id_reuse_still_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._record_remote_request_tombstone(
                123, entry=self._entry("Song A"), singer_name="Dan", reason="host_remove_song")
            app._reconcile_remote_requests([
                {"request_id": 123, "singer": "Zoe", "artist": "Other",
                 "title": "Completely Different Track", "key": 0, "tempo": 0}
            ])
            self.assertEqual([r["request_id"] for r in app.processed_requests], [123])
            self.assertNotIn("123", app._ensure_remote_request_tombstones()["requests"])

    # -- manual removal while offline followed by reconnect ---------------------

    def test_offline_removal_survives_reconnect_sync(self):
        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "tombstones.json"
            app = make_app(self.singws, store)  # no network configured
            entry = self._entry("Song A", rid=123)
            app.queue = [self._singer("Dan", [entry])]
            app._delete_remote_request(123, entry=entry, singer_name="Dan", reason="host_remove_song")
            del app.queue[0]["songs"][0]
            tombstone = app._ensure_remote_request_tombstones()["requests"]["123"]
            self.assertIsNone(tombstone["server_synced_at"])  # queued, unsynced

            # "Restart"/reconnect: fresh app instance loads the persisted store.
            app2 = make_app(self.singws, store, settings=CONNECTED_SETTINGS)
            app2.queue = [self._singer("Dan", [])]
            with fake_network(self.singws) as net:
                app2._reconcile_remote_requests([
                    {"request_id": 123, "singer": "Dan", "artist": "Artist",
                     "title": "Song A", "key": 0, "tempo": 0, "state": "pending"}
                ])
                self.assertEqual(app2.processed_requests, [])
                # removal was re-pushed to the server on reconnect
                repush = [p for p in net.posts if "complete_remote_request.php" in p["url"]]
                self.assertTrue(repush)
                self.assertEqual(int(repush[0]["data"]["request_id"]), 123)

    # -- removal during an incoming server update -------------------------------

    def test_removal_mid_sync_wins_on_next_pass(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            row = {"request_id": 123, "singer": "Dan", "artist": "Artist",
                   "title": "Song A", "key": 0, "tempo": 0, "state": "pending"}

            def process(req):
                # First sync accepts the row; host removes it immediately,
                # while the same payload is still being re-delivered.
                app.processed_requests.append(req)
                entry = self._entry("Song A", rid=req["request_id"])
                app.queue = [self._singer("Dan", [entry])]
                app._delete_remote_request(req["request_id"], entry=entry,
                                           singer_name="Dan", reason="host_remove_song")
                del app.queue[0]["songs"][0]
                return True

            app.process_external_request = process
            app._reconcile_remote_requests([row])
            self.assertEqual(len(app.processed_requests), 1)

            app.process_external_request = lambda req: app.processed_requests.append(req) or True
            app._reconcile_remote_requests([row])  # stale re-delivery
            self.assertEqual(len(app.processed_requests), 1)  # not re-added

    # -- stale client attempting to restore the deleted item ---------------------

    def test_stale_client_cannot_restore_server_terminal_item(self):
        """Server reported the id terminal earlier; a stale client later flips
        the same id (same song) back to pending — must stay dead."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app._reconcile_remote_requests([
                {"request_id": 500, "singer": "Dan", "artist": "Artist",
                 "title": "Song A", "key": 0, "tempo": 0, "state": "removed",
                 "removed_at": 1000}
            ])
            self.assertEqual(app.processed_requests, [])

            app._reconcile_remote_requests([
                {"request_id": 500, "singer": "Dan", "artist": "Artist",
                 "title": "Song A", "key": 0, "tempo": 0, "state": "pending"}
            ])
            self.assertEqual(app.processed_requests, [])

            # ...but the same id carrying a clearly different song (true reuse
            # after a server reset) is accepted.
            app._reconcile_remote_requests([
                {"request_id": 500, "singer": "Zoe", "artist": "Other",
                 "title": "Brand New Different Song", "key": 0, "tempo": 0}
            ])
            self.assertEqual([r["request_id"] for r in app.processed_requests], [500])

    def test_stale_terminal_id_collision_cannot_complete_fresh_host_add(self):
        """Regression: server history reused 1096 and completed a new host song."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            fresh = self._entry("Welcome To My Nightmare", rid=1096, artist="Alice Cooper")
            fresh["host_request_key"] = "host:new-singer:local:new-request"
            fresh["request_source"] = "host_manual"
            app.queue = [self._singer("D", [fresh])]

            app._reconcile_remote_requests([{
                "request_id": 1096,
                "singer": "Jennifer",
                "artist": "Everybody Loves An Outlaw",
                "title": "I See Red",
                "state": "completed",
                "completed_at": 1000,
                "request_source": "phone",
            }])

            self.assertEqual(len(app.queue), 1)
            self.assertEqual(app.queue[0]["songs"], [fresh])
            self.assertEqual(fresh["remote_request_id"], 1096)

    def test_matching_terminal_row_still_completes_live_host_request(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            entry = self._entry("Welcome To My Nightmare", rid=1096, artist="Alice Cooper")
            entry["host_request_key"] = "host:singer:local:request"
            entry["request_source"] = "host_manual"
            app.queue = [self._singer("D", [entry])]

            app._reconcile_remote_requests([{
                "request_id": 1096,
                "singer": "D",
                "artist": "Alice Cooper",
                "title": "Welcome To My Nightmare",
                "state": "completed",
                "completed_at": 1000,
                "request_source": "host_manual",
                "idempotency_key": "host:singer:local:request",
            }])

            self.assertEqual(app.queue[0]["songs"], [])

    # -- duplicate cleanup -------------------------------------------------------

    def test_legacy_local_and_remote_same_metadata_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            older = self._entry("Song A")             # host-added local copy (older)
            newer = self._entry("Song A", rid=123)    # server re-add (newer)
            app.queue = [self._singer("Dan", [older, newer])]
            removed = app._cleanup_duplicate_singer_songs(reason="test")
            self.assertEqual(removed, 0)
            songs = app.queue[0]["songs"]
            self.assertEqual(len(songs), 2)
            self.assertIs(songs[0], older)
            self.assertIs(songs[1], newer)

    def test_duplicate_cleanup_same_id_twice(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            a = self._entry("Song A", rid=123)
            b = self._entry("Song A", rid=123)
            app.queue = [self._singer("Dan", [a, b])]
            self.assertEqual(app._cleanup_duplicate_singer_songs(reason="test"), 1)
            self.assertEqual(app.queue[0]["songs"], [a])

    def test_same_title_two_singers_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [
                self._singer("Dan", [self._entry("Song A", rid=1)]),
                self._singer("Zoe", [self._entry("Song A", rid=2)]),
            ]
            self.assertEqual(app._cleanup_duplicate_singer_songs(reason="test"), 0)
            self.assertEqual(len(app.queue[0]["songs"]), 1)
            self.assertEqual(len(app.queue[1]["songs"]), 1)

    def test_two_versions_same_singer_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [self._singer("Dan", [
                self._entry("Song A", rid=1, path="/lib/sc/song_a.mp3", disc="SC1001"),
                self._entry("Song A", rid=None, path="/lib/kv/song_a.mp4", disc="KV2002"),
            ])]
            self.assertEqual(app._cleanup_duplicate_singer_songs(reason="test"), 0)
            self.assertEqual(len(app.queue[0]["songs"]), 2)

    def test_same_metadata_with_two_request_ids_preserves_both_requests(self):
        """Metadata is not identity: two permanent request IDs are two songs."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [self._singer("Dan", [
                self._entry("Song A", rid=1),
                self._entry("Song A", rid=2),
            ])]
            self.assertEqual(app._cleanup_duplicate_singer_songs(reason="test"), 0)
            self.assertEqual(len(app.queue[0]["songs"]), 2)
            tombstones = app._ensure_remote_request_tombstones().get("requests", {})
            self.assertNotIn("2", tombstones)

    def test_intentional_repeat_after_completion_not_blocked(self):
        """After request 1 completes (tombstone status=completed), the singer
        requests the same title again with a NEW id — it must be accepted."""
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            entry = self._entry("Song A", rid=1)
            app._complete_remote_request(1, entry=entry, singer_name="Dan",
                                         reason="song_completed")
            app._reconcile_remote_requests([
                {"request_id": 2, "singer": "Dan", "artist": "Artist",
                 "title": "Song A", "key": 0, "tempo": 0, "state": "pending"}
            ])
            self.assertEqual([r["request_id"] for r in app.processed_requests], [2])

    def test_local_double_add_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(self.singws, Path(td) / "tombstones.json")
            app.queue = [self._singer("Dan", [
                self._entry("Song A"), self._entry("Song A"),
            ])]
            self.assertEqual(app._cleanup_duplicate_singer_songs(reason="test"), 0)
            self.assertEqual(len(app.queue[0]["songs"]), 2)


if __name__ == "__main__":
    unittest.main()
