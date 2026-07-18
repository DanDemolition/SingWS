"""Regression tests: host app queue mutations must win over server sync.

Covers the recurring live-show bug where a host deleted a server-added song,
replaced it with a locally-added song, reordered the singer's songs, and the
next sync poll snapped the order back to the server's old order. The root
cause was that the reconcile order-apply step globally sorted a singer's songs
by the server's rank, which forced every server-known request ahead of every
host-added local song (local songs have no remote_request_id and no server
row). See [QUEUE_SYNC_AUTHORITY] diag logging in _reconcile_remote_requests.
"""

import importlib.util
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_queue_authority", "0.2.18.1.py")
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
    """Stand-in for the `requests` module that records POSTs."""

    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return FakeResponse(200, {"ok": True})

    def get(self, url, **kwargs):
        return FakeResponse(200, {"ok": True})


class _InlineThread:
    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


@contextmanager
def fake_network(module):
    fake = RecordingRequests()
    saved_requests = sys.modules.get("requests")
    sys.modules["requests"] = fake
    patches = [mock.patch.object(threading, "Thread", _InlineThread)]
    if hasattr(module, "requests"):
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


def remote_song(request_id, artist, title):
    return {
        "remote_request_id": request_id,
        "artist": artist,
        "title": title,
        "display_name": f"{artist} • {title}",
        "song_info": f"/tmp/{title.replace(' ', '_')}.mp3",
        "key": 0,
        "skipped": False,
    }


def local_song(artist, title):
    entry = remote_song(0, artist, title)
    entry.pop("remote_request_id")
    return entry


def track(title, artist="Artist"):
    return {
        "artist": artist,
        "title": title,
        "display": f"{artist} • {title}",
        "path": f"/tmp/{title.replace(' ', '_')}.mp3",
        "duration": 180,
    }


def req_row(request_id, singer, artist, title, **extra):
    row = {
        "request_id": request_id,
        "singer": singer,
        "artist": artist,
        "title": title,
        "key": 0,
        "tempo": 0,
        "sent": True,
        "state": "delivered",
    }
    row.update(extra)
    return row


def make_app(module, tmp_dir: Path, settings=None):
    module.REMOTE_REQUEST_TOMBSTONES_PATH = tmp_dir / "remote_request_tombstones.json"
    module.DEFERRED_REMOTE_ADDS_PATH = tmp_dir / "deferred_remote_adds.json"
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {
        "queue_mode": "rotation",
        "requests_accepting": True,
        "karaoke_normalize_enabled": False,
        "base_url": "",
        "user": "venue",
        "api_key": "",
    }
    if settings:
        app.settings.update(settings)
    app.queue = []
    app._karaoke_pitch_supported = True
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app._schedule_save_data = lambda *a, **k: None
    app._request_queue_display_refresh = lambda *a, **k: None
    app._schedule_waiting_for_add_view_refresh = lambda *a, **k: None
    app._mark_waiting_for_add_delivered_async = lambda *a, **k: None
    app._set_processing_text = lambda *a, **k: None
    app._select_queue_singer_for_host = lambda idx: None
    app._unmatched_remote_request_ids = set()
    app._pending_remote_order_syncs = {}
    app._queue_revision = 0
    app._pending_remote_modifier_pushes = {}
    app._remote_removed_request_ids = set()
    app._remote_request_tombstones = {"requests": {}}
    app._deferred_remote_adds = []
    app._remote_request_intake_inflight = set()
    app._remote_attention_requests = {}
    app._waiting_for_add_requests = {}
    app._waiting_for_add_handled_ids = set()
    app._disable_accepting_watchdog = True
    app._disable_waitlist_state_pull = True
    app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
    app._get_duration_secs = lambda song_path: 180
    app._intake_calls = []
    app.process_external_request = lambda req: app._intake_calls.append(req) or False
    return app


class QueueSyncAuthorityTests(unittest.TestCase):
    """Server adds Song A; host deletes it, replaces it with local Song B,
    reorders; sync runs repeatedly; the host state must win every time."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = make_app(self.singws, Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _titles(self, singer_idx=0):
        return [song["title"] for song in self.app.queue[singer_idx]["songs"]]

    def _ids(self, singer_idx=0):
        return [song.get("remote_request_id") for song in self.app.queue[singer_idx]["songs"]]

    def _seed_grace(self, songs):
        self.app.queue = [{"name": "Grace", "songs": songs, "skipped": False}]

    def _host_delete_song(self, song_idx, singer_idx=0):
        # Mirrors remove_selected(): delete server-side, drop locally, re-push order.
        entry = self.app.queue[singer_idx]["songs"][song_idx]
        request_id = self.app._queue_entry_remote_request_id(entry)
        if request_id is not None:
            self.app._delete_remote_request(
                request_id,
                entry=entry,
                singer_name=self.app.queue[singer_idx]["name"],
                reason="host_remove_song",
            )
        del self.app.queue[singer_idx]["songs"][song_idx]
        self.app._sync_remote_singer_order(singer_idx, reason="host_remove_song")

    def test_replaced_song_and_host_reorder_survive_repeated_syncs(self):
        # Server added Song A (101); the app already imported it. Singer also
        # has a second server request Song C (102).
        self._seed_grace([remote_song(101, "Artist", "Song A"), remote_song(102, "Artist", "Song C")])

        # Host deletes server-added Song A and replaces it with local Song B.
        self._host_delete_song(0)
        self.app.queue[0]["songs"].append(local_song("Artist", "Song B"))
        self.app._sync_remote_singer_order(0, reason="host_add_song")

        # Host reorders: local Song B ahead of server-known Song C.
        songs = self.app.queue[0]["songs"]
        songs[0], songs[1] = songs[1], songs[0]
        self.app._sync_remote_singer_order(0)
        host_stamp = self.app.queue[0]["host_order_updated_at"]
        self.assertEqual(self._titles(), ["Song B", "Song C"])

        # The server keeps the removed Song A row (soft delete) and echoes the
        # host order it stored for Song C. Sync several times: nothing may move.
        server_payload = [
            req_row(101, "Grace", "Artist", "Song A", sent=False, state="pending"),
            req_row(
                102, "Grace", "Artist", "Song C",
                sort_order=1,
                last_order_source="host",
                host_order_updated_at=host_stamp,
                order_revision=self.app.queue[0].get("order_revision", 0),
            ),
        ]
        for _ in range(3):
            self.app._reconcile_remote_requests([dict(row) for row in server_payload])
            self.assertEqual(self._titles(), ["Song B", "Song C"])
            self.assertEqual(self._ids(), [None, 102])

        # Song A never re-entered the queue and was never re-imported.
        self.assertNotIn("Song A", self._titles())
        self.assertEqual([r for r in self.app._intake_calls if r.get("request_id") == 101], [])

    def test_stale_server_order_never_overrides_newer_host_order(self):
        self._seed_grace([remote_song(102, "Artist", "Song C"), remote_song(103, "Artist", "Song D")])
        self.app.queue[0]["songs"].insert(0, local_song("Artist", "Song B"))

        # Host order: B, D, C.
        songs = self.app.queue[0]["songs"]
        songs[1], songs[2] = songs[2], songs[1]
        self.app._sync_remote_singer_order(0)
        host_stamp = self.app.queue[0]["host_order_updated_at"]
        self.assertEqual(self._titles(), ["Song B", "Song D", "Song C"])

        # A poll races in carrying the server's OLD order (C before D) with an
        # older host stamp. It must be ignored.
        stale = [
            req_row(102, "Grace", "Artist", "Song C", sort_order=1,
                    last_order_source="host", host_order_updated_at=host_stamp - 5000),
            req_row(103, "Grace", "Artist", "Song D", sort_order=2,
                    last_order_source="host", host_order_updated_at=host_stamp - 5000),
        ]
        for _ in range(3):
            self.app._reconcile_remote_requests([dict(row) for row in stale])
            self.assertEqual(self._titles(), ["Song B", "Song D", "Song C"])

    def test_newer_singer_reorder_permutes_only_server_songs_and_pins_local(self):
        self._seed_grace([remote_song(102, "Artist", "Song C"), remote_song(103, "Artist", "Song D")])
        self.app.queue[0]["songs"].insert(0, local_song("Artist", "Song B"))

        # Host establishes order B, D, C.
        songs = self.app.queue[0]["songs"]
        songs[1], songs[2] = songs[2], songs[1]
        self.app._sync_remote_singer_order(0)
        host_stamp = self.app.queue[0]["host_order_updated_at"]

        # The singer legitimately reorders their songs on the phone AFTER the
        # host change: C ahead of D. The server songs swap, but the host-added
        # local Song B keeps its slot at the top.
        singer_reorder = [
            req_row(102, "Grace", "Artist", "Song C", sort_order=1,
                    last_order_source="singer", singer_order_updated_at=host_stamp + 1000),
            req_row(103, "Grace", "Artist", "Song D", sort_order=2,
                    last_order_source="singer", singer_order_updated_at=host_stamp + 1000),
        ]
        self.app._reconcile_remote_requests(singer_reorder)
        self.assertEqual(self._titles(), ["Song B", "Song C", "Song D"])

    def test_host_reorder_pushes_remote_ids_in_queue_order(self):
        app = make_app(
            self.singws,
            Path(self._tmp.name),
            settings={"base_url": "https://beta.wskar.com", "api_key": "secret", "user": "venue"},
        )
        app.queue = [{
            "name": "Grace",
            "songs": [
                local_song("Artist", "Song B"),
                remote_song(103, "Artist", "Song D"),
                remote_song(102, "Artist", "Song C"),
            ],
            "skipped": False,
        }]
        with fake_network(self.singws) as fake:
            app._sync_remote_singer_order(0)
        order_posts = [p for p in fake.posts if "set_remote_request_order.php" in p["url"]]
        self.assertEqual(len(order_posts), 1)
        data = order_posts[0]["data"]
        pushed_ids = [value for key, value in data if key == "request_ids[]"]
        self.assertEqual(pushed_ids, [103, 102])
        fields = dict((k, v) for k, v in data if k != "request_ids[]")
        self.assertEqual(fields.get("last_order_source"), "host")
        self.assertGreater(int(fields.get("host_order_updated_at") or 0), 0)

    def test_delete_all_server_songs_then_reorder_local_only_is_untouched_by_sync(self):
        # After the host removes every server-added song, a singer queue of
        # purely local songs must never be reordered by a poll.
        self._seed_grace([remote_song(101, "Artist", "Song A")])
        self._host_delete_song(0)
        self.app.queue[0]["songs"] = [local_song("Artist", "Song B"), local_song("Artist", "Song E")]
        self.app._sync_remote_singer_order(0)

        payload = [req_row(101, "Grace", "Artist", "Song A", sent=False, state="pending")]
        for _ in range(2):
            self.app._reconcile_remote_requests([dict(row) for row in payload])
            self.assertEqual(self._titles(), ["Song B", "Song E"])
        self.assertNotIn("Song A", self._titles())

    def test_disabled_waitlist_does_not_store_attention_or_sync_rows(self):
        self.app.settings["use_waiting_for_add"] = False
        reported = []
        self.app._report_remote_attention_request_async = lambda req, reason: reported.append((req, reason))

        self.app._record_remote_attention_request(
            {"request_id": 701, "singer": "Grace", "artist": "Artist", "title": "Needs Review"},
            "auto_accept_failed",
        )
        self.assertEqual(self.app._waiting_for_add_requests, {})
        self.assertEqual(reported, [])

        self.app._set_waiting_for_add_requests([
            {"request_id": 702, "singer": "Grace", "artist": "Artist", "title": "Waitlisted", "state": "waiting"}
        ])
        self.assertEqual(self.app._waiting_for_add_requests, {})

        self.app._record_remote_limit_blocked_request(
            {"request_id": 703, "singer": "Grace", "artist": "Artist", "title": "Limit Blocked"},
            "Grace already has too many songs.",
            report=True,
        )
        self.assertEqual(reported, [])

    def test_same_metadata_remote_insert_with_new_id_is_not_deduplicated(self):
        self.app.settings["use_waiting_for_add"] = False
        self.app.settings["limit_pending_max"] = 2
        self._seed_grace([remote_song(801, "Artist", "Same Song")])

        ok = self.app._add_song_to_queue(
            "Grace",
            ("/tmp/Same_Song.mp3", 0, 100),
            track=track("Same Song"),
            remote_meta={"request_id": 802, "singer": "Grace", "source": "server"},
        )

        self.assertTrue(ok)
        self.assertEqual(self._titles(), ["Same Song", "Same Song"])
        self.assertEqual(self._ids(), [801, 802])
        self.assertEqual(self.app._waiting_for_add_requests, {})
        tombstones = self.app._ensure_remote_request_tombstones().get("requests", {})
        self.assertNotIn("802", tombstones)

    def test_reconcile_preserves_existing_rows_with_distinct_request_ids(self):
        self.app.settings["use_waiting_for_add"] = False
        self._seed_grace([
            remote_song(901, "Artist", "Same Song"),
            remote_song(902, "Artist", "Same Song"),
        ])

        removed = self.app._cleanup_duplicate_singer_songs(reason="test")

        self.assertEqual(removed, 0)
        self.assertEqual(self._titles(), ["Same Song", "Same Song"])
        self.assertEqual(self._ids(), [901, 902])
        tombstones = self.app._ensure_remote_request_tombstones().get("requests", {})
        self.assertNotIn("902", tombstones)


if __name__ == "__main__":
    unittest.main()
