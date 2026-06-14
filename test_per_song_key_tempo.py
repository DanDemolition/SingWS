import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_keytempo", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {}
    app.queue = [
        {
            "name": "Ada",
            "songs": [
                {
                    "song_info": "/tmp/song.mp3",
                    "display_name": "Artist • Title",
                    "artist": "Artist",
                    "title": "Title",
                    "disc_id": "SC1234",
                    "duration": 180,
                    "key": 0,
                    "skipped": False,
                }
            ],
        }
    ]
    app._karaoke_pitch_supported = True
    app.save_data = lambda: None
    app.update_queue_display = lambda: None
    app._set_processing_text = lambda *a, **k: None
    # Stubs for row-text helpers.
    app.queue_display = object()
    app._two_col_text = lambda widget, left, right: f"{left}||{right}"
    app._fmt_right_time = lambda dur: "3:00"
    app._split_display_artist_title_disc = lambda *a, **k: ("Artist", "Title", "SC1234")
    return app


class PerSongKeyTempoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self.app = make_app(self.singws)
        self.entry = self.app.queue[0]["songs"][0]

    def _patch_getint(self, value):
        self.singws.QInputDialog.getInt = staticmethod(
            lambda *a, **k: (value, True)
        )

    def _patch_getint_cancel(self):
        self.singws.QInputDialog.getInt = staticmethod(
            lambda *a, **k: (0, False)
        )

    def test_key_dialog_sets_entry_key(self):
        self._patch_getint(3)
        self.app.open_song_key_dialog(0, 0)
        self.assertEqual(self.entry["key"], 3)

    def test_key_dialog_cancel_keeps_key(self):
        self.entry["key"] = 2
        self._patch_getint_cancel()
        self.app.open_song_key_dialog(0, 0)
        self.assertEqual(self.entry["key"], 2)

    def test_tempo_dialog_sets_entry_tempo(self):
        self._patch_getint(110)
        self.app.open_song_tempo_dialog(0, 0)
        self.assertEqual(self.entry["tempo_percent"], 110)

    def test_row_text_shows_key_and_speed(self):
        self.entry["key"] = -2
        self.entry["tempo_percent"] = 90
        visible, tooltip, left, right = self.app._build_queue_song_row_text(self.entry)
        self.assertIn("KEY -2", right)
        self.assertIn("SPD 90%", right)
        self.assertIn("Key: -2", tooltip)
        self.assertIn("Speed: 90%", tooltip)

    def test_row_text_hides_default_speed(self):
        self.entry["key"] = 0
        self.entry["tempo_percent"] = 100
        visible, tooltip, left, right = self.app._build_queue_song_row_text(self.entry)
        self.assertNotIn("SPD", right)
        self.assertNotIn("KEY", right)

    def test_modifiers_survive_entry_move(self):
        # The per-song modifiers live on the entry dict, so moving the entry
        # through the queue preserves them.
        self._patch_getint(4)
        self.app.open_song_key_dialog(0, 0)
        moved = self.app.queue[0]["songs"].pop(0)
        self.app.queue.append({"name": "Bo", "songs": [moved]})
        self.assertEqual(self.app.queue[-1]["songs"][0]["key"], 4)


def make_remote_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {"queue_mode": "rotation", "karaoke_normalize_enabled": False}
    app.queue = [
        {
            "name": "Grace",
            "songs": [
                {
                    "remote_request_id": 77,
                    "artist": "Artist",
                    "title": "Title",
                    "display_name": "Artist • Title",
                    "song_info": "/tmp/song.mp3",
                    "key": 0,
                    "skipped": False,
                }
            ],
            "skipped": False,
        }
    ]
    app._karaoke_pitch_supported = True
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app._set_processing_text = lambda *a, **k: None
    app._select_queue_singer_for_host = lambda idx: None
    app._unmatched_remote_request_ids = set()
    app._pending_remote_order_syncs = {}
    # Pre-set lazily-created attrs that the bare __new__ widget can't getattr.
    app._pending_remote_modifier_pushes = {}
    app._remote_removed_request_ids = set()
    app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
    app._get_duration_secs = lambda song_path: 180
    app.process_external_request = lambda req: False
    return app


class HostWinsModifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        self.app = make_remote_app(self.singws)
        self.entry = self.app.queue[0]["songs"][0]
        # No base_url => the push records a pending override but skips the network.
        self.singws.QInputDialog.getInt = staticmethod(lambda *a, **k: (3, True))

    def test_host_edit_records_pending_override(self):
        self.app.open_song_key_dialog(0, 0)
        self.assertEqual(self.entry["key"], 3)
        pending = self.app._pending_remote_modifier_pushes
        self.assertIn(77, pending)
        self.assertEqual(pending[77]["key"], 3)

    def test_stale_poll_does_not_revert_host_value(self):
        self.app.open_song_key_dialog(0, 0)
        # Server still reports the old value (0); host edit must win.
        self.app._reconcile_remote_requests(
            [{"request_id": 77, "singer": "Grace", "artist": "Artist", "title": "Title", "key": 0, "tempo": 0}]
        )
        self.assertEqual(self.entry["key"], 3)
        self.assertIn(77, self.app._pending_remote_modifier_pushes)

    def test_override_clears_once_server_echoes(self):
        self.app.open_song_key_dialog(0, 0)
        # Server now reflects the host value; the override is confirmed/cleared.
        self.app._reconcile_remote_requests(
            [{"request_id": 77, "singer": "Grace", "artist": "Artist", "title": "Title", "key": 3, "tempo": 0}]
        )
        self.assertEqual(self.entry["key"], 3)
        self.assertNotIn(77, self.app._pending_remote_modifier_pushes)

    def test_delivered_server_edit_updates_existing_queue_entry(self):
        self.app._reconcile_remote_requests(
            [{
                "request_id": 77,
                "singer": "Grace",
                "artist": "Artist",
                "title": "Title",
                "key": -4,
                "tempo": 35,
                "sent": True,
                "state": "delivered",
            }]
        )
        self.assertEqual(self.entry["key"], -4)
        self.assertEqual(self.entry["tempo_percent"], 130)

    def test_remote_order_applies_inside_singer_only(self):
        self.app.queue[0]["songs"].append({
            "remote_request_id": 88,
            "artist": "Artist",
            "title": "Second",
            "display_name": "Artist • Second",
            "song_info": "/tmp/second.mp3",
            "key": 0,
            "skipped": False,
        })
        self.app.queue.append({
            "name": "Other",
            "songs": [{
                "remote_request_id": 99,
                "artist": "Other",
                "title": "Song",
                "display_name": "Other • Song",
                "song_info": "/tmp/other.mp3",
                "key": 0,
                "skipped": False,
            }],
            "skipped": False,
        })

        self.app._reconcile_remote_requests([
            {"request_id": 88, "singer": "Grace", "artist": "Artist", "title": "Second", "key": 0, "tempo": 0, "sent": True},
            {"request_id": 77, "singer": "Grace", "artist": "Artist", "title": "Title", "key": 0, "tempo": 0, "sent": True},
            {"request_id": 99, "singer": "Other", "artist": "Other", "title": "Song", "key": 0, "tempo": 0, "sent": True},
        ])

        self.assertEqual([s["name"] for s in self.app.queue], ["Grace", "Other"])
        self.assertEqual(
            [song["remote_request_id"] for song in self.app.queue[0]["songs"]],
            [88, 77],
        )


if __name__ == "__main__":
    unittest.main()
