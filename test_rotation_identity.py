import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_rotation", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {"queue_mode": "rotation", "karaoke_normalize_enabled": False}
    app.queue = []
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app._select_queue_singer_for_host = lambda idx: None
    app._unmatched_remote_request_ids = set()
    app._pending_remote_order_syncs = {}
    app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
    app._get_duration_secs = lambda song_path: 180
    app.process_external_request = lambda req: False
    return app


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


if __name__ == "__main__":
    unittest.main()
