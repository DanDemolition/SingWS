import importlib.util
import unittest
from types import SimpleNamespace


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_rotation", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {
        "queue_mode": "rotation",
        "karaoke_normalize_enabled": False,
        "empty_rotation_slot_timeout_sec": 180,
        "defer_remote_adds_until_between_singers": False,
        "limit_pending_max": 0,
    }
    app.queue = []
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app._schedule_save_data = lambda *a, **k: None
    app._select_queue_singer_for_host = lambda idx: None
    app._unmatched_remote_request_ids = set()
    app._pending_remote_order_syncs = {}
    app._remote_removed_request_ids = set()
    app._deferred_remote_adds = []
    app._waiting_for_add_requests = {}
    app._remote_attention_requests = {}
    app._queue_revision = 0
    app._queue_update_batch_depth = 0
    app._queue_display_batch_dirty = False
    app.karaoke_playing = False
    app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
    app._get_duration_secs = lambda song_path: 180
    app.process_external_request = lambda req: False
    app._save_deferred_remote_adds = lambda: None
    app._update_deferred_remote_add_status = lambda: None
    app._schedule_waiting_for_add_view_refresh = lambda *a, **k: None
    app._show_queue_limit_rejected = lambda *a, **k: None
    app._clear_remote_attention_request = lambda *a, **k: None
    app._record_remote_attention_request = lambda *a, **k: None
    app._record_remote_limit_blocked_request = lambda *a, **k: None
    app._log_remote_request_diag = lambda *a, **k: None
    app.singer_input = SimpleNamespace(clear=lambda: None)
    app.key_selector = SimpleNamespace(findText=lambda text: 0, setCurrentIndex=lambda idx: None)
    return app


def track(title, path=None):
    return {
        "artist": "Artist",
        "title": title,
        "display": f"Artist • {title}",
        "duration": 180,
        "path": path or f"/tmp/{title.lower().replace(' ', '_')}.mp3",
    }


def remote_payload(request_id, singer, title):
    t = track(title)
    return {
        "_ok": True,
        "request_id": request_id,
        "request_time": 1.0,
        "singer": singer,
        "artist": t["artist"],
        "title": t["title"],
        "song_data": (t["path"], 0, 100),
        "track": t,
        "remote_meta": {
            "request_id": request_id,
            "singer": singer,
            "artist": t["artist"],
            "title": t["title"],
            "source": "phone",
        },
    }


class RotationIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_remote_reconcile_preserves_played_empty_rotation_singer(self):
        app = make_app(self.singws)
        app.queue = [
            {
                "name": "Ada",
                "songs": [],
                "skipped": False,
                "has_sung": True,
                "round_sung": True,
                "rotation_marker": False,
                "last_sung_at": 123.0,
            },
            {
                "name": "Grace",
                "songs": [
                    {
                        "remote_request_id": 77,
                        "artist": "Artist",
                        "title": "Title",
                        "song_info": "/tmp/song.mp3",
                        "key": 0,
                        "skipped": False,
                    }
                ],
                "skipped": False,
                "has_sung": False,
                "round_sung": False,
                "rotation_marker": False,
            },
        ]

        app._reconcile_remote_requests(
            [{"request_id": 77, "singer": "Grace", "artist": "Artist", "title": "Title", "key": 0, "tempo": 0}]
        )

        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(app.queue[0]["songs"], [])
        self.assertTrue(app.queue[0]["has_sung"])

    def test_remote_reconcile_preserves_never_sung_empty_rotation_singer(self):
        app = make_app(self.singws)
        app.queue = [
            {
                "name": "Ada",
                "songs": [],
                "skipped": False,
                "has_sung": False,
                "round_sung": False,
                "rotation_marker": False,
            },
            {
                "name": "Grace",
                "songs": [
                    {
                        "remote_request_id": 77,
                        "artist": "Artist",
                        "title": "Title",
                        "song_info": "/tmp/song.mp3",
                        "key": 0,
                        "skipped": False,
                    }
                ],
                "skipped": False,
                "has_sung": False,
                "round_sung": False,
                "rotation_marker": False,
            },
        ]

        app._reconcile_remote_requests(
            [{"request_id": 77, "singer": "Grace", "artist": "Artist", "title": "Title", "key": 0, "tempo": 0}]
        )

        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(app.queue[0]["songs"], [])
        self.assertFalse(app.queue[0]["has_sung"])

    def test_returning_singer_reuses_existing_empty_row(self):
        app = make_app(self.singws)
        app.queue = [
            {
                "name": "Ada",
                "songs": [],
                "skipped": False,
                "has_sung": True,
                "round_sung": True,
                "rotation_marker": False,
                "last_sung_at": 123.0,
            },
            {
                "name": "Grace",
                "songs": [],
                "skipped": False,
                "has_sung": True,
                "round_sung": True,
                "rotation_marker": False,
            },
        ]
        track = {"artist": "Artist", "title": "Title", "display": "Artist • Title", "duration": 180}

        app._add_song_to_queue("ada", ("/tmp/return.mp3", 0), track=track, remote_meta={"request_id": 88})

        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(len(app.queue[0]["songs"]), 1)
        self.assertEqual(app.queue[0]["songs"][0]["remote_request_id"], 88)
        self.assertTrue(app.queue[0]["has_sung"])

    def test_singer_removes_song_then_adds_replacement_reuses_slot(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [{"remote_request_id": 1, "artist": "Artist", "title": "Old", "song_info": "/tmp/old.mp3", "key": 0, "skipped": False}], "skipped": False},
            {"name": "Grace", "songs": [{"remote_request_id": 2, "artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]

        removed = app._remove_local_remote_request_by_id(1, reason="server_removed")
        self.assertEqual(removed, 1)
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(app.queue[0]["songs"], [])
        self.assertTrue(app.queue[0]["temporary_empty_slot"])

        ok = app._add_song_to_queue("Ada", ("/tmp/new.mp3", 0), track=track("New"), remote_meta={"request_id": 3, "source": "phone"})

        self.assertTrue(ok)
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual([song["remote_request_id"] for song in app.queue[0]["songs"]], [3])
        self.assertNotIn("temporary_empty_slot", app.queue[0])

    def test_replacement_after_deferred_between_singers_keeps_slot(self):
        app = make_app(self.singws)
        app.settings["defer_remote_adds_until_between_singers"] = True
        app.karaoke_playing = True
        app.queue = [
            {"name": "Ada", "songs": [], "skipped": False, "temporary_empty_slot": True, "empty_slot_until": 9999999999.0},
            {"name": "Grace", "songs": [{"remote_request_id": 2, "artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]

        self.assertTrue(app._apply_resolved_remote_add(remote_payload(3, "Ada", "New"), allow_defer=True))
        self.assertEqual(app.queue[0]["songs"], [])
        self.assertEqual(len(app._deferred_remote_adds), 1)

        app.karaoke_playing = False
        self.assertEqual(app._flush_deferred_remote_adds("unit_test"), 1)
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual([song["remote_request_id"] for song in app.queue[0]["songs"]], [3])

    def test_single_slot_song_replacement_preserves_rotation_position(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [{"artist": "Artist", "title": "Old", "song_info": "/tmp/old.mp3", "key": 0, "skipped": False}], "skipped": False},
            {"name": "Grace", "songs": [{"artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]
        old = app.queue[0]["songs"].pop(0)
        app._mark_rotation_slot_temporarily_empty(app.queue[0], reason="host_remove_song")

        self.assertTrue(app._add_song_to_queue("Ada", ("/tmp/new.mp3", 0), track=track("New"), remote_meta=None))

        self.assertEqual(old["title"], "Old")
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(app.queue[0]["songs"][0]["title"], "New")

    def test_two_song_singer_remove_one_then_add_appends_same_slot(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [
                {"artist": "Artist", "title": "One", "song_info": "/tmp/one.mp3", "key": 0, "skipped": False},
                {"artist": "Artist", "title": "Two", "song_info": "/tmp/two.mp3", "key": 0, "skipped": False},
            ], "skipped": False},
            {"name": "Grace", "songs": [{"artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]
        app.queue[0]["songs"].pop(0)

        self.assertTrue(app._add_song_to_queue("Ada", ("/tmp/three.mp3", 0), track=track("Three"), remote_meta=None))

        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual([song["title"] for song in app.queue[0]["songs"]], ["Two", "Three"])

    def test_host_remove_singer_deletes_but_song_remove_preserves_empty_slot(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [], "skipped": False, "temporary_empty_slot": True, "empty_slot_until": 9999999999.0},
            {"name": "Grace", "songs": [{"artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]
        app._delete_rotation_singer_row(0, reason="host_remove_singer")
        self.assertEqual([s["name"] for s in app.queue], ["Grace"])

        app.queue.insert(0, {"name": "Ada", "songs": [{"artist": "Artist", "title": "Only", "song_info": "/tmp/only.mp3", "key": 0, "skipped": False}], "skipped": False})
        app.queue[0]["songs"].pop(0)
        app._mark_rotation_slot_temporarily_empty(app.queue[0], reason="host_remove_song")
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertTrue(app.queue[0]["temporary_empty_slot"])

    def test_clear_queue_preserves_singers_and_clears_songs(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [{"artist": "Artist", "title": "Only", "song_info": "/tmp/only.mp3", "key": 0, "skipped": False}], "skipped": False},
            {"name": "Grace", "songs": [{"artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]

        removed = app._clear_queue_songs_preserving_singers(reason="host_clear_queue")

        self.assertEqual(removed, 2)
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual([s["songs"] for s in app.queue], [[], []])
        self.assertTrue(all(s.get("temporary_empty_slot") for s in app.queue))

    def test_repeated_empty_slot_preservation_keeps_original_created_time(self):
        app = make_app(self.singws)
        singer = {"name": "Ada", "songs": [], "skipped": False}

        app._mark_rotation_slot_temporarily_empty(singer, reason="server_removed")
        first_created_at = singer.get("empty_slot_created_at")
        app._mark_rotation_slot_temporarily_empty(singer, reason="server_removed")

        self.assertEqual(singer.get("empty_slot_reason"), "server_removed")
        self.assertEqual(singer.get("empty_slot_created_at"), first_created_at)
        self.assertTrue(singer.get("empty_slot_until", 0) >= first_created_at)

    def test_expired_empty_slot_cleanup_does_not_remove_singer(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [], "skipped": False, "temporary_empty_slot": True, "empty_slot_until": 1.0},
            {"name": "Grace", "songs": [{"artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]

        cleaned = app._prune_expired_empty_rotation_slots()

        self.assertEqual(cleaned, 1)
        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(app.queue[0]["songs"], [])
        self.assertNotIn("temporary_empty_slot", app.queue[0])

    def test_server_sync_preserves_empty_slot_order(self):
        app = make_app(self.singws)
        app.queue = [
            {"name": "Ada", "songs": [], "skipped": False, "temporary_empty_slot": True, "empty_slot_until": 9999999999.0},
            {"name": "Grace", "songs": [{"remote_request_id": 2, "artist": "Artist", "title": "Next", "song_info": "/tmp/next.mp3", "key": 0, "skipped": False}], "skipped": False},
        ]

        app._reconcile_remote_requests([
            {"request_id": 2, "singer": "Grace", "artist": "Artist", "title": "Next", "key": 0, "tempo": 0}
        ])

        self.assertEqual([s["name"] for s in app.queue], ["Ada", "Grace"])
        self.assertEqual(app.queue[0]["songs"], [])


if __name__ == "__main__":
    unittest.main()
