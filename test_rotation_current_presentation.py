"""Current/next rotation presentation must be independent from row selection."""

import importlib.util
import unittest
from types import SimpleNamespace


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_current_presentation", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def singer(singer_id, name, request_uid, skipped=False):
    return {
        "singer_id": singer_id,
        "name": name,
        "skipped": skipped,
        "songs": [{"request_uid": request_uid, "song_info": f"/tmp/{request_uid}.mp3", "skipped": False}],
    }


class CurrentSingerPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_main_module()

    def app(self):
        app = SimpleNamespace()
        app.karaoke_playing = True
        app._current_karaoke_singer_id = "singer-b"
        app._current_karaoke_request_id = "local:req-b"
        app.queue = [
            singer("singer-a", "Alex", "req-a"),
            singer("singer-b", "Alex", "req-b"),  # duplicate display name is intentional
            singer("singer-c", "Chris", "req-c"),
        ]
        app._ensure_queue_entry_id = self.module.KaraokeApp._ensure_queue_entry_id
        app._first_active_entry_for_singer = lambda row: next(
            (entry for entry in row.get("songs", []) if not entry.get("skipped", False)), None
        )
        return app

    def indices(self, app):
        return self.module.KaraokeApp._authoritative_rotation_indices(app)

    def test_duplicate_names_map_by_stable_singer_id(self):
        current, next_up, warning = self.indices(self.app())
        self.assertEqual(current, 1)
        self.assertEqual(next_up, 0)
        self.assertFalse(warning)

    def test_reorder_does_not_change_current_identity(self):
        app = self.app()
        app.queue.reverse()
        current, _next_up, warning = self.indices(app)
        self.assertEqual(app.queue[current]["singer_id"], "singer-b")
        self.assertFalse(warning)

    def test_missing_playback_identity_reports_warning_not_fallback_name(self):
        app = self.app()
        app._current_karaoke_singer_id = "missing"
        app._current_karaoke_request_id = "local:missing"
        current, _next_up, warning = self.indices(app)
        self.assertEqual(current, -1)
        self.assertTrue(warning)

    def test_next_up_never_reuses_current_singer(self):
        app = self.app()
        app.queue[0]["skipped"] = True
        current, next_up, _warning = self.indices(app)
        self.assertEqual(current, 1)
        self.assertEqual(next_up, 2)


if __name__ == "__main__":
    unittest.main()
