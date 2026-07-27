import importlib.util
import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "minimal")


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_model_view_qa", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeAction:
    def __init__(self, text):
        self._text = text
        self.triggered = _FakeSignal()

    def text(self):
        return self._text


class _FakeMenu:
    instances = []

    def __init__(self, *args, **kwargs):
        self.actions = []
        self.exec_position = None
        _FakeMenu.instances.append(self)

    def addAction(self, text):
        action = _FakeAction(text)
        self.actions.append(action)
        return action

    def addSeparator(self):
        self.actions.append(None)

    def exec(self, position):
        self.exec_position = position
        return None

    def setStyleSheet(self, *_args):
        pass


class _FakeTimer:
    def __init__(self):
        self.started = []

    def start(self, ms):
        self.started.append(ms)


class ModelBackedViewQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()
        cls.qt_app = cls.singws.QApplication.instance() or cls.singws.QApplication([])

    def make_app(self):
        module = self.singws
        app = module.KaraokeApp.__new__(module.KaraokeApp)
        module.QWidget.__init__(app)
        app.settings = dict(module.DEFAULTS)
        app.settings["list_font_scale"] = 1.0
        app.queue = []
        app.tracks = []
        app.singer_preferences = {}
        app.singer_history = {"singers": {}}
        app.karaoke_playing = False
        app._waiting_for_add_requests = {}
        app._waiting_for_add_handled_ids = set()
        app._waiting_for_add_recent_terminal_requests = {}
        app._queue_remote_request_ids = lambda: []
        app._parse_duet_singer = lambda name: (str(name).split("&")[0].strip(), "")
        app._queue_limit_name_key = lambda name: str(name or "").strip().lower()
        app._is_waitlist_enabled_cached = lambda: False
        app.save_settings = lambda: None
        return app

    def setup_queue_shell(self, app):
        module = self.singws
        app.queue_display = module.QueueListView()
        app.queue_display_model = module.QueueListModel(app)
        app.queue_display.setModel(app.queue_display_model)
        app.queue_label = module.QLabel()
        app._row_left_role = int(module.Qt.ItemDataRole.UserRole) + 20
        app._row_right_role = int(module.Qt.ItemDataRole.UserRole) + 21
        app._row_disc_role = int(module.Qt.ItemDataRole.UserRole) + 22
        app._row_duet_role = int(module.Qt.ItemDataRole.UserRole) + 23
        app._queue_row_kind_role = int(module.Qt.ItemDataRole.UserRole) + 30
        app._queue_singer_index_role = int(module.Qt.ItemDataRole.UserRole) + 31
        app._queue_song_index_role = int(module.Qt.ItemDataRole.UserRole) + 32
        app._queue_base_point_size = 12
        app._rotation_recompute_round_state = lambda *args, **kwargs: None
        app._rotation_repair_marker = lambda *args, **kwargs: None
        app._update_rotation_lock_button = lambda *args, **kwargs: None
        app._update_queue_eta_label = lambda *args, **kwargs: app.queue_label.setText("SINGERS 30")
        app._update_rotation_summary_card = lambda *args, **kwargs: None
        app._update_last_sung_card = lambda *args, **kwargs: None
        app._is_rotation_mode = lambda: False
        app._rotation_marker_index = lambda: -1
        app.is_singer_active = lambda singer: bool(singer.get("songs"))
        app._first_active_entry_for_singer = lambda singer: singer.get("songs", [{}])[0]
        app._song_info_primary_path = lambda song_info: song_info
        app._queue_entry_duration_for_display = lambda entry: int(entry.get("duration") or 0) if isinstance(entry, dict) else 0
        app._singer_duet_suffix = lambda singer: ""
        app._duet_partner_suffix = lambda duet: ""
        app.update_rotation_view = lambda *args, **kwargs: None
        app.rotation_post_timer = _FakeTimer()
        app.schedule_ticker_update = lambda *args, **kwargs: None
        app._schedule_host_control_state_sync = lambda *args, **kwargs: None
        app._schedule_next_up_prescan = lambda *args, **kwargs: None
        app._sync_remote_singer_order = lambda *args, **kwargs: None

        def row_text(entry):
            title = entry.get("title", "")
            artist = entry.get("artist", "")
            left = f"   • {artist} — {title}"
            right = f"{entry.get('disc_id', 'KV')}  {entry.get('duration', 0) // 60}:{entry.get('duration', 0) % 60:02d}"
            return f"{left}    {right}", f"{artist} - {title}", left, right

        app._build_queue_song_row_text = row_text

    def assert_perf_under(self, label, elapsed_ms, budget_ms):
        print(f"[PERF-SUMMARY] {label} ms={elapsed_ms:.1f} budget_ms={budget_ms:.1f}")
        self.assertLess(elapsed_ms, budget_ms, f"{label} took {elapsed_ms:.1f}ms")

    def test_singer_history_long_rows_many_rows_and_text_scale(self):
        module = self.singws
        app = self.make_app()
        app.singer_history_page = app._build_singer_history_page()
        # The view only rebuilds while History is the visible workspace page.
        # Without a stack the refresh hits its own except/return and silently
        # does nothing, so anything "timed" here would just be the wait below.
        app.left_workspace_stack = SimpleNamespace(
            currentWidget=lambda: app.singer_history_page
        )
        long_name = "Alexandria Cassandra Montgomery-Silverstone With An Extremely Long Stage Name"
        long_title = "A Very Long Karaoke Song Title That Needs To Wrap Cleanly In The History Details Pane"
        app.singer_history = {
            "singers": {
                f"singer_{i:03d}": {
                    "name": f"{long_name} #{i:03d}",
                    "total_performances": i + 1,
                    "unique_song_count": 3,
                    "last_seen_at": int(time.time()) - i,
                    "preferred_disc_priority": "SC, KV" if i % 2 == 0 else "",
                    "songs": {
                        f"song_{i}_{j}": {
                            "artist": f"Long Artist Name {j}",
                            "title": f"{long_title} #{i}-{j}",
                            "disc_id": f"SC-{i:03d}-{j}",
                            "song_type": "CDG",
                            "last_key": j - 1,
                            "last_tempo_percent": 100 + j,
                            "last_performed_at": int(time.time()) - j,
                        }
                        for j in range(3)
                    },
                }
                for i in range(260)
            }
        }

        # Rebuilds are deferred while a song plays; that deferral is the fix for
        # the 650-1160ms stalls seen at every song stop, so assert it holds.
        app.karaoke_playing = True
        app._refresh_singer_history_view()
        module.QApplication.processEvents()
        self.assertEqual(app.singer_history_singer_model.rowCount(), 0)

        app.karaoke_playing = False
        started = time.perf_counter()
        app._refresh_singer_history_view()
        deadline = time.time() + 2.0
        while time.time() < deadline:
            module.QApplication.processEvents()
            if app.singer_history_singer_model.rowCount() >= 260 and app.singer_history_songs_model.rowCount() > 0:
                break
            time.sleep(0.01)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.assert_perf_under("qa_singer_history_long_rows_refresh", elapsed_ms, 1500.0)

        self.assertIsInstance(app.singer_history_singer_list, module.QListView)
        self.assertIsInstance(app.singer_history_songs_list, module.QListView)
        self.assertEqual(app.singer_history_singer_list.horizontalScrollBarPolicy(), module.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(app.singer_history_songs_list.horizontalScrollBarPolicy(), module.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(app.singer_history_singer_model.rowCount(), 260)
        self.assertGreater(app.singer_history_songs_model.rowCount(), 0)
        self.assertTrue(app._selected_singer_history_key())
        self.assertTrue(app._selected_singer_history_song_key())

        before = app.singer_history_singer_list.font().pointSize()
        app.settings["list_font_scale"] = 1.4
        app._apply_list_font_scale()
        module.QApplication.processEvents()
        self.assertGreaterEqual(app.singer_history_singer_list.font().pointSize(), before)
        self.assertGreaterEqual(app.singer_history_songs_list.font().pointSize(), before)

    def test_waitlist_long_rows_context_menu_and_text_scale(self):
        module = self.singws
        app = self.make_app()
        app.waiting_for_add_page = app._build_waiting_for_add_page()
        long_singer = "Benjamin Bartholomew Kensington The Third Featuring A Very Long Duet Partner"
        long_title = "An Overly Long Requested Karaoke Song Title That Should Wrap Instead Of Clipping"
        now = int(time.time())
        app._waiting_for_add_requests = {
            9000 + i: {
                "request_id": 9000 + i,
                "singer": f"{long_singer} #{i:02d}",
                "artist": f"Extremely Long Artist Name {i:02d}",
                "title": f"{long_title} #{i:02d}",
                "disc_id": f"KV-{i:04d}",
                "duration_secs": 245 + i,
                "state": "waiting",
                "created_at": now + i,
                "pending_reason": "rotation_full",
            }
            for i in range(25)
        }

        app.karaoke_playing = True
        started = time.perf_counter()
        app._refresh_waiting_for_add_view()
        module.QApplication.processEvents()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.assert_perf_under("qa_waitlist_long_rows_refresh", elapsed_ms, 1000.0)

        self.assertIsInstance(app.waiting_for_add_list, module.QListView)
        self.assertEqual(app.waiting_for_add_list.horizontalScrollBarPolicy(), module.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertEqual(app.waiting_for_add_model.rowCount(), 26)
        self.assertIsNotNone(app._selected_waiting_for_add_request())
        first_waitlist_index = app.waiting_for_add_model.firstSelectableIndex()
        self.assertIn("Version:", app.waiting_for_add_model.data(first_waitlist_index))

        before = app.waiting_for_add_list.font().pointSize()
        app.settings["list_font_scale"] = 1.4
        app._apply_list_font_scale()
        module.QApplication.processEvents()
        self.assertGreaterEqual(app.waiting_for_add_list.font().pointSize(), before)

        _FakeMenu.instances.clear()
        index = first_waitlist_index
        app.waiting_for_add_list.setCurrentIndex(index)
        position = app.waiting_for_add_list.visualRect(index).center()
        with mock.patch.object(module, "QMenu", _FakeMenu):
            app._show_waiting_for_add_context_menu(position)
        self.assertEqual(len(_FakeMenu.instances), 1)
        action_texts = [action.text() for action in _FakeMenu.instances[0].actions if action is not None]
        self.assertEqual(
            action_texts,
            [
                "Replace with another karaoke version",
                "Replace with a different song",
                "Edit singer/name/song metadata...",
                "Remove from waitlist",
            ],
        )

    def test_queue_last_long_rows_text_scale_and_identity_move(self):
        module = self.singws
        app = self.make_app()
        self.setup_queue_shell(app)
        long_singer = "Charlotte Maximiliana Rutherford-Singh With A Long Queue Name"
        long_title = "A Very Long Queue Song Title That Should Stay Readable While Music Is Playing"
        app.queue = [
            {
                "name": f"{long_singer} #{i:02d}",
                "has_sung": i % 3 == 0,
                "songs": [
                    {
                        "song_info": f"/music/song_{i}_{j}.mp4",
                        "display_name": f"Extremely Long Artist {i} - {long_title} #{j}",
                        "artist": f"Extremely Long Artist {i}",
                        "title": f"{long_title} #{j}",
                        "disc_id": f"KV-{i:03d}-{j}",
                        "duration": 240 + i + j,
                        "key": j,
                    }
                    for j in range(2)
                ],
            }
            for i in range(30)
        ]
        app.karaoke_playing = True
        app._current_karaoke_singer_name = app.queue[0]["name"]
        app._current_karaoke_song_path = "/music/song_0_0.mp4"

        started = time.perf_counter()
        app.update_queue_display()
        module.QApplication.processEvents()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.assert_perf_under("qa_queue_long_rows_refresh", elapsed_ms, 1000.0)

        self.assertIsInstance(app.queue_display, module.QListView)
        self.assertIsInstance(app.queue_display_model, module.QueueListModel)
        self.assertEqual(app.queue_display.count(), 90)
        self.assertEqual(app.queue_display_model.rowCount(), 90)
        self.assertEqual(app._queue_item_kind(app.queue_display.item(0)), "singer")
        self.assertEqual(app._queue_item_singer_index(app.queue_display.item(0), 0), 0)
        self.assertEqual(app._queue_item_kind(app.queue_display.item(2)), "song")
        self.assertEqual(app._queue_item_song_indices(app.queue_display.item(2), 2), (0, 1))
        self.assertIn(long_title, app.queue_display.item(2).data(module.Qt.ItemDataRole.DisplayRole))

        before = app.queue_display.font().pointSize()
        app.settings["list_font_scale"] = 1.35
        app._apply_list_font_scale()
        module.QApplication.processEvents()
        self.assertGreaterEqual(app.queue_display.font().pointSize(), before)

        resets = []
        changes = []
        app.queue_display_model.modelAboutToBeReset.connect(lambda: resets.append("reset"))
        app.queue_display_model.dataChanged.connect(lambda *_args: changes.append("changed"))
        app.queue[0]["songs"][1]["title"] = f"{long_title} #1 Updated"
        app.queue[0]["songs"][1]["display_name"] = f"Extremely Long Artist 0 - {long_title} #1 Updated"
        app.update_queue_display()
        module.QApplication.processEvents()
        self.assertEqual(resets, [])
        self.assertTrue(changes)
        self.assertIn("Updated", app.queue_display.item(2).data(module.Qt.ItemDataRole.DisplayRole))

        app.queue_display.setCurrentRow(2)
        app.move_up()
        module.QApplication.processEvents()

        self.assertTrue(app.queue[0]["songs"][0]["title"].endswith("#1 Updated"))
        self.assertEqual(app._queue_item_song_indices(app.queue_display.item(1), 1), (0, 0))
        self.assertEqual(app._queue_item_song_indices(app.queue_display.item(2), 2), (0, 1))

    def test_queue_keeps_singers_without_songs_visible_and_in_place_unnumbered(self):
        app = self.make_app()
        self.setup_queue_shell(app)
        app._first_active_entry_for_singer = lambda singer: next((song for song in singer.get("songs", []) if not song.get("skipped", False)), None)
        app.queue = [
            {
                "name": "Dan",
                "skipped": False,
                "has_sung": True,
                "songs": [{"song_info": "/tmp/a.mp3", "display_name": "A - Song A", "artist": "A", "title": "Song A", "duration": 180, "skipped": False}],
            },
            {"name": "Steve", "skipped": False, "has_sung": True, "songs": []},
            {
                "name": "Bill",
                "skipped": False,
                "has_sung": True,
                "songs": [{"song_info": "/tmp/b.mp3", "display_name": "B - Song B", "artist": "B", "title": "Song B", "duration": 200, "skipped": False}],
            },
        ]

        app.update_queue_display()

        singer_rows = [row for row in app.queue_display_model._rows if row.get("kind") == "singer"]
        left_role = app._row_left_role
        right_role = app._row_right_role
        # Steve holds his row and his place but takes no number: the numbers
        # count only singers who actually have a song, so "how many until I'm
        # up" reads straight off the list. Bill is therefore 2, not 3.
        self.assertEqual(
            [row.get("roles", {}).get(left_role) for row in singer_rows],
            ["1. Dan", "\u2014 Steve", "2. Bill"],
        )
        self.assertEqual(
            [row.get("roles", {}).get(right_role) for row in singer_rows],
            ["", "WAITING FOR SONG", ""],
        )
        self.assertEqual(app._row_for_singer_index(1), 2)

    def test_adding_song_updates_existing_waiting_singer_row_in_place(self):
        app = self.make_app()
        self.setup_queue_shell(app)
        app._first_active_entry_for_singer = lambda singer: next(
            (song for song in singer.get("songs", []) if not song.get("skipped", False)), None
        )
        app.queue = [
            {"singer_id": "a", "name": "Ada", "songs": [], "skipped": False, "has_sung": True},
            {"singer_id": "b", "name": "Bob", "songs": [], "skipped": False, "has_sung": True},
        ]
        app.update_queue_display()
        self.assertEqual(app._row_for_singer_index(1), 1)

        app.queue[0]["songs"].append({
            "request_uid": "song-a",
            "song_info": "/tmp/a.mp3",
            "display_name": "Artist - Song A",
            "artist": "Artist",
            "title": "Song A",
            "duration": 180,
            "skipped": False,
        })
        app.update_queue_display()

        singer_rows = [row for row in app.queue_display_model._rows if row.get("kind") == "singer"]
        self.assertEqual([row.get("singer_idx") for row in singer_rows], [0, 1])
        self.assertEqual([app.queue[i]["singer_id"] for i in range(2)], ["a", "b"])
        self.assertEqual(
            [row.get("roles", {}).get(app._row_right_role) for row in singer_rows],
            ["", "WAITING FOR SONG"],
        )

    def test_empty_singer_can_be_manually_reordered_without_disappearing(self):
        app = self.make_app()
        self.setup_queue_shell(app)
        app._first_active_entry_for_singer = lambda singer: next(
            (song for song in singer.get("songs", []) if not song.get("skipped", False)), None
        )
        song = lambda title: {
            "song_info": f"/tmp/{title}.mp3", "display_name": f"Artist - {title}",
            "artist": "Artist", "title": title, "duration": 180, "skipped": False,
        }
        app.queue = [
            {"name": "Dan", "songs": [song("One")], "skipped": False},
            {"name": "Steve", "songs": [], "skipped": False},
            {"name": "Bill", "songs": [song("Two")], "skipped": False},
        ]
        app.update_queue_display()
        app.queue_display.setCurrentRow(app._row_for_singer_index(1))
        app.move_up()

        self.assertEqual([singer["name"] for singer in app.queue], ["Steve", "Dan", "Bill"])
        self.assertEqual(app._row_for_singer_index(0), 0)
        first = app.queue_display_model.rowDict(0)
        self.assertEqual(first.get("roles", {}).get(app._row_left_role), "\u2014 Steve")
        self.assertEqual(first.get("roles", {}).get(app._row_right_role), "WAITING FOR SONG")

    def test_rotation_start_is_visible_in_header_and_waiting_row(self):
        app = self.make_app()
        self.setup_queue_shell(app)
        app.queue = [
            {"name": "Ada", "songs": [], "skipped": False, "rotation_marker": False},
            {"name": "Bob", "songs": [], "skipped": False, "rotation_marker": True},
        ]
        app._is_rotation_mode = lambda: True
        app._rotation_marker_index = lambda: 1
        app._first_active_entry_for_singer = lambda singer: next(
            (song for song in singer.get("songs", []) if not song.get("skipped", False)), None
        )
        app._compute_queue_eta_seconds = lambda: (0, 0)
        app._fmt_m_ss = lambda seconds: "0:00"
        app._is_rotation_locked = lambda: False
        app._update_queue_eta_label = self.singws.KaraokeApp._update_queue_eta_label.__get__(app)

        app.update_queue_display()

        self.assertIn("START", app.queue_label.text())
        self.assertIn("Bob", app.queue_label.text())
        bob_row = app.queue_display_model.rowDict(app._row_for_singer_index(1))
        self.assertEqual(
            bob_row.get("roles", {}).get(app._row_right_role),
            "ROTATION START • WAITING FOR SONG",
        )

    def test_rotation_window_shows_waiting_slot_but_does_not_cue_it(self):
        rendered = []
        next_states = []
        fake = SimpleNamespace(
            queue_items=[],
            rotation_rail=SimpleNamespace(set_items=lambda items: rendered.extend(items)),
            list_widget=SimpleNamespace(),
            now_singing_label=SimpleNamespace(setText=lambda text: None),
            queue_count_label=SimpleNamespace(setText=lambda text: None),
            queue_title_label=SimpleNamespace(setText=lambda text: next_states.append(("title", text))),
            now_singing_surface=SimpleNamespace(
                set_state=lambda current, next_singer, countdown: next_states.append((current, next_singer))
            ),
            display_name_for_queue_entry=lambda entry, path, tracks: entry.get("title", "Song"),
        )
        queue = [
            {"name": "Waiting Singer", "songs": [], "skipped": False, "rotation_marker": True},
            {"name": "Ready Singer", "songs": [{"song_info": "/tmp/song.mp3", "title": "Ready Song"}], "skipped": False},
        ]

        self.singws.RotationView.update_rotation(fake, queue, [], "Current Singer")

        self.assertEqual([item["singer"] for item in rendered], ["Waiting Singer", "Ready Singer"])
        self.assertIn("Waiting for song", rendered[0]["song"])
        self.assertEqual(next_states[-1], ("Current Singer", "Ready Singer"))
        self.assertIn("START: Waiting Singer", next_states[0][1])


if __name__ == "__main__":
    unittest.main()
