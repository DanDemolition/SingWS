"""Server-added requests must not steal the queue selection: after any
server-originated insert (auto-accept, waitlist promotion, between-songs add,
sync burst, reconnect refresh), the rebuild re-selects the TOP actionable
rotation row instead of the newly added row. During playback, an explicitly
selected request is preserved by stable request ID if it still exists; a
removed selection is cleared. Host-driven adds keep the old behavior (select
what the host just added).

Covers _mark_server_queue_mutation / _queue_top_actionable_row /
_restore_queue_selection_after_rebuild in 0.2.18.1.py."""

import importlib.util
import unittest
from types import SimpleNamespace


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_select", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeQueueDisplay:
    """Records selection/scroll calls like QueueListView would receive."""

    def __init__(self):
        self.current_row = None
        self.set_current_calls = []
        self.scrolled_to_top = 0
        self.scroll_calls = []

    def setCurrentRow(self, row):
        self.current_row = int(row)
        self.set_current_calls.append(int(row))

    def scrollToTop(self):
        self.scrolled_to_top += 1

    def scrollToItem(self, *a, **k):
        self.scroll_calls.append(("item", a))

    def item(self, row):
        return row


def make_app(module, queue_mode="rotation"):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {
        "queue_mode": queue_mode,
        "karaoke_normalize_enabled": False,
        "empty_rotation_slot_timeout_sec": 180,
        "defer_remote_adds_until_between_singers": False,
        "limit_pending_max": 20,
    }
    app.queue = []
    app.queue_display = FakeQueueDisplay()
    app.queue_display_model = SimpleNamespace(_rows=[])
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
    app._singer_rename_aliases = {}
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


def song(title):
    return {"song_info": f"/tmp/{title}.mp3", "key": 0, "skipped": False,
            "title": title, "artist": "Artist", "display_name": f"Artist • {title}",
            "duration": 180}


def singer(name, titles, **extra):
    record = {"name": name, "songs": [song(t) for t in titles], "skipped": False,
              "has_sung": False, "round_sung": False, "rotation_marker": False}
    record.update(extra)
    return record


def model_rows_for_queue(queue, leading_header=False):
    """Build model rows the way update_queue_display lays them out:
    one 'singer' row then one 'song' row per song."""
    rows = []
    if leading_header:
        rows.append({"kind": "header", "singer_idx": -1, "song_idx": -1})
    for si, s in enumerate(queue):
        rows.append({"kind": "singer", "singer_idx": si, "song_idx": -1})
        for gi, entry in enumerate(s.get("songs", [])):
            rows.append({"kind": "song", "singer_idx": si, "song_idx": gi,
                         "entry": entry})
    return rows


def track(title):
    return {"artist": "Artist", "title": title, "display": f"Artist • {title}",
            "duration": 180, "path": f"/tmp/{title}.mp3"}


def remote_add(app, request_id, singer_name, title):
    return app._add_song_to_queue(
        singer_name, (f"/tmp/{title}.mp3", 0, 100), track=track(title),
        remote_meta={"request_id": request_id, "singer": singer_name, "source": "phone"},
    )


class ServerAddSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _app(self, **kw):
        return make_app(self.singws, **kw)

    def _rebuild(self, app, selected_identity=None, leading_header=False):
        """Simulate the tail of update_queue_display: sync model rows, then
        apply post-rebuild selection."""
        app.queue_display_model._rows = model_rows_for_queue(app.queue, leading_header=leading_header)
        app._restore_queue_selection_after_rebuild(selected_identity)

    # -- one request added between songs --------------------------------------

    def test_single_between_songs_request_selects_top_rotation_row(self):
        app = self._app()
        app.queue = [singer("Ada", ["A1"]), singer("Grace", ["G1"])]
        self.assertTrue(remote_add(app, 1, "Zoe", "New Song"))  # inserted by server
        self.assertTrue(app._queue_select_top_after_server_update)

        # Host had the (soon index-shifted) Grace row selected before the add.
        self._rebuild(app, selected_identity=("singer", 1, -1))

        self.assertEqual(app.queue_display.current_row, 0)   # top: Ada's singer row
        self.assertEqual(app.queue_display.scrolled_to_top, 1)
        self.assertFalse(app._queue_select_top_after_server_update)  # consumed

    def test_local_host_add_keeps_existing_behavior(self):
        app = self._app()
        selected = []
        app._select_queue_singer_for_host = lambda idx: selected.append(idx)
        app.queue = [singer("Ada", ["A1"])]
        app._add_song_to_queue("Zoe", ("/tmp/z.mp3", 0, 100), track=track("Z"))

        self.assertFalse(getattr_dict(app, "_queue_select_top_after_server_update", False))
        self.assertEqual(selected, [1])  # host add still selects the added singer

    # -- multiple requests added rapidly ---------------------------------------

    def test_burst_of_requests_selects_top_once_no_flicker(self):
        app = self._app()
        app.queue = [singer("Ada", ["A1"])]
        for i, name in enumerate(("Zoe", "Max", "Kim"), start=1):
            self.assertTrue(remote_add(app, i, name, f"S{i}"))
        self.assertTrue(app._queue_select_top_after_server_update)

        self._rebuild(app, selected_identity=("song", 2, 0))
        # Selection applied exactly once, straight to the top row — the new
        # rows were never selected in between.
        self.assertEqual(app.queue_display.set_current_calls, [0])
        self.assertEqual(app.queue_display.current_row, 0)

        # A follow-up rebuild with no new server activity keeps whatever the
        # host has selected (identity restore path).
        self._rebuild(app, selected_identity=("singer", 0, -1))
        self.assertEqual(app.queue_display.current_row, 0)

    def test_server_update_during_playback_preserves_selected_request_by_id(self):
        app = self._app()
        selected = dict(song("G1"), remote_request_id=77)
        app.queue = [singer("Ada", ["A1"]), singer("Grace", [])]
        app.queue[1]["songs"] = [selected]
        stable_id = app._ensure_queue_entry_id(selected)

        # A server insert shifts Grace from singer index 1 to 2.
        app.queue.insert(0, singer("New", ["N1"]))
        app.karaoke_playing = True
        app._queue_select_top_after_server_update = True
        self._rebuild(app, selected_identity=("song", 1, 0, stable_id))

        row = app.queue_display.current_row
        picked = app.queue_display_model._rows[row]
        self.assertIs(picked["entry"], selected)
        self.assertEqual((picked["singer_idx"], picked["song_idx"]), (2, 0))

    def test_server_update_during_playback_clears_removed_request(self):
        app = self._app()
        app.queue = [singer("Ada", ["A1"])]
        app.karaoke_playing = True
        app._queue_select_top_after_server_update = True
        self._rebuild(app, selected_identity=("song", 1, 0, "remote:77"))

        self.assertEqual(app.queue_display.current_row, -1)

    # -- request added while the host is typing --------------------------------

    def test_request_while_typing_resets_selection_but_not_scroll(self):
        app = self._app()
        app._host_is_interacting_elsewhere = lambda: True  # focus in a text field
        app.queue = [singer("Ada", ["A1"])]
        remote_add(app, 1, "Zoe", "S1")
        self._rebuild(app)
        self.assertEqual(app.queue_display.current_row, 0)   # selection reset
        self.assertEqual(app.queue_display.scrolled_to_top, 0)  # scroll untouched

    # -- waitlist item promoted into rotation -----------------------------------

    def test_waitlist_promotion_flags_server_mutation(self):
        app = self._app()
        app.queue = [singer("Ada", ["A1"])]
        req = {"request_id": 7, "singer": "Zoe", "artist": "Artist", "title": "W1",
               "state": "waiting"}
        app._waiting_for_add_requests = {7: dict(req)}
        ok = app._add_waiting_for_add_track(req, track("W1"))
        self.assertTrue(ok)
        self.assertTrue(app._queue_select_top_after_server_update)
        self._rebuild(app)
        self.assertEqual(app.queue_display.current_row, 0)

    # -- server reconnect causing a full refresh --------------------------------

    def test_reconcile_accepting_requests_flags_server_mutation(self):
        app = self._app()
        app.queue = [singer("Ada", ["A1"])]
        app.process_external_request = lambda req: True  # reconnect delivers new rows
        app._reconcile_remote_requests(
            [{"request_id": 55, "singer": "Zoe", "artist": "Artist", "title": "R1",
              "key": 0, "tempo": 0}]
        )
        self.assertTrue(app._queue_select_top_after_server_update)

    def test_reconcile_without_changes_does_not_touch_selection(self):
        app = self._app()
        app.queue = [singer("Grace", [])]
        app.queue[0]["songs"] = [dict(song("T1"), remote_request_id=77)]
        app._reconcile_remote_requests(
            [{"request_id": 77, "singer": "Grace", "artist": "Artist", "title": "T1",
              "key": 0, "tempo": 0}]
        )
        self.assertFalse(getattr_dict(app, "_queue_select_top_after_server_update", False))

    # -- empty active rotation with pending/waitlisted items ---------------------

    def test_empty_active_rotation_selects_first_valid_row(self):
        app = self._app()
        # Only empty slots / skipped singers: no one has an active song.
        app.queue = [
            singer("Skipped", ["X"], skipped=True),
            singer("EmptySlot", []),
        ]
        app._queue_select_top_after_server_update = True
        self._rebuild(app)
        rows = app.queue_display_model._rows
        # First selectable singer/song row (the skipped singer's row is still
        # a valid selectable row; headers would be skipped — see next test).
        self.assertEqual(app.queue_display.current_row, 0)
        self.assertEqual(rows[app.queue_display.current_row]["kind"], "singer")

    def test_headers_are_ignored_when_choosing_top_row(self):
        app = self._app()
        app.queue = [singer("Ada", ["A1"])]
        app._queue_select_top_after_server_update = True
        self._rebuild(app, leading_header=True)
        row = app.queue_display.current_row
        self.assertEqual(app.queue_display_model._rows[row]["kind"], "singer")
        self.assertEqual(app.queue_display_model._rows[row]["singer_idx"], 0)

    def test_completely_empty_list_clears_selection(self):
        app = self._app()
        app.queue = []
        app._queue_select_top_after_server_update = True
        self._rebuild(app)
        self.assertEqual(app.queue_display.current_row, -1)  # cleared

    def test_top_row_skips_skipped_singers_to_first_actionable(self):
        app = self._app()
        app.queue = [
            singer("Skipped", ["X"], skipped=True),
            singer("Ada", ["A1"]),
        ]
        app._queue_select_top_after_server_update = True
        self._rebuild(app)
        row = app.queue_display.current_row
        picked = app.queue_display_model._rows[row]
        self.assertEqual(picked["kind"], "singer")
        self.assertEqual(picked["singer_idx"], 1)  # Ada, the first actionable


def getattr_dict(obj, name, default=None):
    """getattr on bare-__new__ QWidgets raises RuntimeError; read __dict__."""
    return object.__getattribute__(obj, "__dict__").get(name, default)


if __name__ == "__main__":
    unittest.main()
