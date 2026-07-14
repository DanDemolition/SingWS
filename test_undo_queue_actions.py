"""Regression coverage for the session-only host queue undo stack."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_undo", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def song(title, request_id):
    return {
        "remote_request_id": request_id,
        "song_info": f"/tmp/{title}.mp3",
        "artist": "Artist",
        "title": title,
        "display_name": f"Artist • {title}",
        "duration": 180,
        "key": 2,
        "tempo_percent": 95,
        "skipped": False,
        "priority": "vip",
        "notes": "keep all metadata",
    }


def singer(name, request_id, title):
    return {
        "id": f"singer-{request_id}",
        "name": name,
        "songs": [song(title, request_id)],
        "skipped": False,
        "has_sung": False,
        "round_sung": False,
        "rotation_marker": request_id == 1,
        "created_at": float(request_id),
    }


class UndoQueueActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_main_module()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.module.REMOTE_REQUEST_TOMBSTONES_PATH = Path(self.tmp.name) / "tombstones.json"
        app = self.module.KaraokeApp.__new__(self.module.KaraokeApp)
        app.queue = [singer("A", 1, "One"), singer("B", 2, "Two")]
        app._waiting_for_add_requests = {
            9: {"request_id": 9, "singer": "C", "title": "Wait", "state": "waitlist"}
        }
        app._waiting_for_add_handled_ids = set()
        app._remote_attention_requests = {}
        app._remote_request_tombstones = {"requests": {}}
        app._remote_removed_request_ids = set()
        app._pending_remote_order_syncs = {}
        app._queue_revision = 3
        app._undo_stack = []
        app._undo_limit = 20
        app._undo_restoring = False
        app._undo_action = None
        app.settings = {"base_url": "", "user": "", "api_key": "", "queue_mode": "rotation"}
        app.save_data = lambda: None
        app.update_queue_display = lambda: None
        app.update_rotation_summary_card = lambda: None
        app._schedule_waiting_for_add_view_refresh = lambda **kwargs: None
        app._show_processing_notification = lambda *args, **kwargs: None
        app.post_rotation = lambda: None
        app._sync_remote_singer_order = lambda *args, **kwargs: None
        app._push_remote_request_replacement = lambda *args, **kwargs: None
        self.app = app

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, name, mutation):
        command = self.module.KaraokeApp._begin_undoable_action(self.app, name)
        mutation()
        self.assertTrue(self.module.KaraokeApp._commit_undoable_action(self.app, command))

    def test_remove_singer_restores_exact_record_and_position(self):
        original = self.module.KaraokeApp._undo_snapshot(self.app)["queue"]
        self.commit("Remove Singer", lambda: self.app.queue.pop(0))
        self.assertTrue(self.module.KaraokeApp.undo_last_action(self.app))
        self.assertEqual(self.app.queue, original)

    def test_remove_one_song_restores_only_that_song_metadata(self):
        second = song("Second", 3)
        self.app.queue[0]["songs"].append(second)
        original = self.module.KaraokeApp._undo_snapshot(self.app)["queue"]
        self.commit("Remove Song", lambda: self.app.queue[0]["songs"].pop(0))
        self.assertTrue(self.module.KaraokeApp.undo_last_action(self.app))
        self.assertEqual(self.app.queue, original)
        self.assertEqual(self.app.queue[0]["songs"][0]["notes"], "keep all metadata")

    def test_waitlist_and_tombstone_state_are_restored(self):
        def remove_waitlist():
            self.app._waiting_for_add_requests.pop(9)
            self.app._waiting_for_add_handled_ids.add(9)
            self.app._remote_request_tombstones["requests"]["9"] = {"request_id": 9}
            self.app._remote_removed_request_ids.add(9)

        self.commit("Remove Pending Request", remove_waitlist)
        self.assertTrue(self.module.KaraokeApp.undo_last_action(self.app))
        self.assertIn(9, self.app._waiting_for_add_requests)
        self.assertNotIn(9, self.app._waiting_for_add_handled_ids)
        self.assertNotIn("9", self.app._remote_request_tombstones["requests"])
        self.assertNotIn(9, self.app._remote_removed_request_ids)

    def test_multiple_consecutive_undos_and_stack_limit(self):
        self.commit("Reorder", lambda: self.app.queue.reverse())
        self.commit("Skip Singer", lambda: self.app.queue[0].update(skipped=True))
        self.assertTrue(self.module.KaraokeApp.undo_last_action(self.app))
        self.assertFalse(self.app.queue[0]["skipped"])
        self.assertTrue(self.module.KaraokeApp.undo_last_action(self.app))
        self.assertEqual([row["name"] for row in self.app.queue], ["A", "B"])

        for index in range(25):
            self.commit("Change", lambda i=index: self.app.queue[0].update(sequence=i))
        self.assertEqual(len(self.app._undo_stack), 20)

    def test_noop_is_not_added_to_history(self):
        command = self.module.KaraokeApp._begin_undoable_action(self.app, "No-op")
        self.assertFalse(self.module.KaraokeApp._commit_undoable_action(self.app, command))
        self.assertEqual(self.app._undo_stack, [])


if __name__ == "__main__":
    unittest.main()
