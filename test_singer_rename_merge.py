"""Singer rename-merge: renaming a singer to a name that already exists in
the rotation must merge into the original singer record instead of leaving a
duplicate — across the rotation menu, history rename, waitlist/pending
server requests, and reconnect syncs. See _rename_rotation_singer /
_merge_duplicate_rotation_singers in 0.2.18.1.py."""

import importlib.util
import unittest
from types import SimpleNamespace


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_rename", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module, queue_mode="normal"):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {
        "queue_mode": queue_mode,
        "karaoke_normalize_enabled": False,
        "empty_rotation_slot_timeout_sec": 180,
        "defer_remote_adds_until_between_singers": False,
        "limit_pending_max": 20,
    }
    app.queue = []
    app.singer_preferences = {}
    app.singer_history = {"singers": {}}
    app.update_queue_display = lambda: None
    app.update_rotation_summary_card = lambda: None
    app._update_last_sung_card = lambda: None
    app._schedule_singer_history_refresh = lambda *a, **k: None
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


def song(title, rid=None):
    entry = {
        "song_info": f"/tmp/{title.lower().replace(' ', '_')}.mp3",
        "key": 0,
        "skipped": False,
        "title": title,
        "artist": "Artist",
    }
    if rid is not None:
        entry["remote_request_id"] = rid
    return entry


def singer(name, titles, **extra):
    record = {
        "name": name,
        "songs": [song(t) for t in titles],
        "skipped": False,
        "has_sung": False,
        "round_sung": False,
        "rotation_marker": False,
    }
    record.update(extra)
    return record


def titles_of(record):
    return [e["title"] for e in record["songs"]]


class RenameMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def _app(self, **kw):
        return make_app(self.singws, **kw)

    # -- core merge behavior -------------------------------------------------

    def test_rename_into_existing_active_singer_spec_example(self):
        """Dan(A,B) + Daniel(C); rename Daniel->Dan => one Dan with A,B,C."""
        app = self._app()
        original = singer("Dan", ["Song A", "Song B"])
        app.queue = [original, singer("Daniel", ["Song C"])]

        self.assertTrue(app._rename_rotation_singer(1, "Dan"))

        self.assertEqual(len(app.queue), 1)
        self.assertIs(app.queue[0], original)  # original record survives
        self.assertEqual(app.queue[0]["name"], "Dan")
        self.assertEqual(titles_of(app.queue[0]), ["Song A", "Song B", "Song C"])

    def test_rename_pushes_immutable_server_identity(self):
        app = self._app()
        app.queue = [singer(
            "Daniel",
            ["Song A"],
            singer_id="local-singer-id",
            server_singer_id="server-singer-id",
            server_singer_session_id=741,
        )]
        pushed = []
        app._push_singer_rename_to_server = lambda old, new, **kwargs: pushed.append((old, new, kwargs))

        self.assertTrue(app._rename_rotation_singer(0, "Dan"))
        identity = pushed[0][2]["singer_identity"]
        self.assertEqual(identity["singer_id"], "server-singer-id")
        self.assertEqual(identity["singer_session_id"], 741)

    def test_relative_song_order_preserved(self):
        app = self._app()
        app.queue = [
            singer("Dan", ["A1", "A2"]),
            singer("Grace", ["G1"]),
            singer("Daniel", ["B1", "B2", "B3"]),
        ]
        app._rename_rotation_singer(2, "Dan")
        self.assertEqual([s["name"] for s in app.queue], ["Dan", "Grace"])
        self.assertEqual(titles_of(app.queue[0]), ["A1", "A2", "B1", "B2", "B3"])

    def test_case_insensitive_matches(self):
        for variant in ("DAN", "dan", "dAn"):
            app = self._app()
            original = singer("Dan", ["Song A"])
            app.queue = [original, singer("Daniel", ["Song C"])]
            app._rename_rotation_singer(1, variant)
            self.assertEqual(len(app.queue), 1, variant)
            self.assertIs(app.queue[0], original, variant)
            # Original record's casing wins over what the host typed.
            self.assertEqual(app.queue[0]["name"], "Dan", variant)

    def test_whitespace_normalization(self):
        for variant in ("Dan ", " Dan", "Dan", "  Dan  "):
            app = self._app()
            original = singer("Dan", ["Song A"])
            app.queue = [original, singer("Daniel", ["Song C"])]
            app._rename_rotation_singer(1, variant)
            self.assertEqual(len(app.queue), 1, repr(variant))
            self.assertEqual(app.queue[0]["name"], "Dan", repr(variant))

    def test_survivor_is_oldest_created_at(self):
        app = self._app()
        younger = singer("Dan", ["Y1"], created_at=2000.0)
        older = singer("Daniel", ["O1"], created_at=1000.0)
        app.queue = [younger, older]
        app._rename_rotation_singer(1, "Dan")
        self.assertEqual(len(app.queue), 1)
        self.assertIs(app.queue[0], older)  # oldest creation wins
        self.assertEqual(titles_of(app.queue[0]), ["O1", "Y1"])
        self.assertEqual(app.queue[0]["created_at"], 1000.0)

    def test_untimestamped_record_counts_as_oldest(self):
        app = self._app()
        legacy = singer("Dan", ["L1"])  # pre-created_at record
        newer = singer("Daniel", ["N1"], created_at=5000.0)
        app.queue = [legacy, newer]
        app._rename_rotation_singer(1, "Dan")
        self.assertIs(app.queue[0], legacy)
        self.assertEqual(titles_of(app.queue[0]), ["L1", "N1"])

    def test_merge_preserves_flags_and_rotation_marker(self):
        app = self._app()
        app.queue = [
            singer("Dan", ["A"], has_sung=False, round_sung=False),
            singer("Daniel", ["B"], has_sung=True, round_sung=True,
                   rotation_marker=True, last_sung_at=42.0),
        ]
        app._rename_rotation_singer(1, "Dan")
        surv = app.queue[0]
        self.assertTrue(surv["has_sung"])
        self.assertTrue(surv["round_sung"])
        self.assertTrue(surv["rotation_marker"])
        self.assertEqual(surv["last_sung_at"], 42.0)
        self.assertFalse(surv["skipped"])

    def test_plain_rename_without_conflict_does_not_merge(self):
        app = self._app()
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Danny")
        self.assertEqual([s["name"] for s in app.queue], ["Dan", "Danny"])

    # -- currently singing ----------------------------------------------------

    def test_rename_while_currently_singing_updates_refs_only(self):
        app = self._app()
        app.karaoke_playing = True
        app._current_karaoke_singer_name = "Daniel"
        app._current_karaoke_singer_display = "Daniel"
        app._last_sung_singer_display = "Daniel"
        original = singer("Dan", ["Song A"])
        app.queue = [original, singer("Daniel", ["Song C"])]

        app._rename_rotation_singer(1, "Dan")

        self.assertEqual(app._current_karaoke_singer_name, "Dan")
        self.assertEqual(app._current_karaoke_singer_display, "Dan")
        self.assertEqual(app._last_sung_singer_display, "Dan")
        self.assertIs(app.queue[0], original)
        self.assertEqual(titles_of(app.queue[0]), ["Song A", "Song C"])

    # -- waitlist / pending server requests -----------------------------------

    def test_rename_with_waitlisted_songs_repoints_items(self):
        app = self._app()
        refreshes = []
        app._schedule_waiting_for_add_view_refresh = lambda *a, **k: refreshes.append(k.get("reason"))
        app._waiting_for_add_requests = {
            5: {"request_id": 5, "singer": "Daniel", "title": "W1", "state": "waitlist"},
            6: {"request_id": 6, "singer": "daniel ", "title": "W2", "state": "waitlist"},
            7: {"request_id": 7, "singer": "Grace", "title": "W3", "state": "waitlist"},
        }
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Dan")
        self.assertEqual(app._waiting_for_add_requests[5]["singer"], "Dan")
        self.assertEqual(app._waiting_for_add_requests[6]["singer"], "Dan")
        self.assertEqual(app._waiting_for_add_requests[7]["singer"], "Grace")
        self.assertIn("singer_rename", refreshes)

    def test_pending_server_request_attaches_to_survivor_after_rename(self):
        app = self._app()
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Dan")

        t = {"artist": "Artist", "title": "Late Request", "display": "Artist • Late Request",
             "duration": 180, "path": "/tmp/late.mp3"}
        app._add_song_to_queue(
            "Daniel", ("/tmp/late.mp3", 0, 100), track=t,
            remote_meta={"request_id": 99, "singer": "Daniel", "source": "phone"},
        )
        self.assertEqual(len(app.queue), 1)
        self.assertEqual(app.queue[0]["name"], "Dan")
        self.assertEqual(titles_of(app.queue[0])[-1], "Late Request")

    def test_alias_chain_follows_repeated_renames(self):
        app = self._app()
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Dan")      # Daniel -> Dan
        app._rename_rotation_singer(0, "Danny")    # Dan -> Danny
        self.assertEqual(app._resolve_singer_alias("Daniel"), "Danny")
        self.assertEqual(app._resolve_singer_alias(" DANIEL "), "Danny")

    # -- offline rename followed by reconnect sync -----------------------------

    def test_reconnect_sync_cannot_resurrect_old_name(self):
        """Server redelivers waitlist rows under the retired name after a
        reconnect; ingest must map them to the surviving singer."""
        app = self._app()
        app.settings["use_waiting_for_add"] = True
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Dan")

        import time as _time
        fetched = [
            {"request_id": 11, "singer": "Daniel", "artist": "Artist",
             "title": "W1", "state": "waiting", "waitlisted_at": str(int(_time.time()))},
        ]
        app._set_waiting_for_add_requests(fetched)
        stored = app._waiting_for_add_requests.get(11)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["singer"], "Dan")

    def test_duplicate_queue_on_disk_collapses_on_load_style_merge(self):
        """Simulates a stale/racing client having produced duplicates: the
        idempotent merge pass collapses them and a second run is a no-op."""
        app = self._app()
        app.queue = [
            singer("Dan", ["A"]),
            singer("Grace", ["G"]),
            singer("DAN ", ["B"]),
            singer(" dan", ["C"]),
        ]
        merged = app._merge_duplicate_rotation_singers(reason="test_load")
        self.assertEqual(merged, 2)
        self.assertEqual([s["name"] for s in app.queue], ["Dan", "Grace"])
        self.assertEqual(titles_of(app.queue[0]), ["A", "B", "C"])
        self.assertEqual(app._merge_duplicate_rotation_singers(reason="again"), 0)

    def test_concurrent_rename_race_converges(self):
        """Two 'clients' rename the same singer: the second rename applies on
        top of the merged state without creating any duplicate."""
        app = self._app()
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Dan")
        # Second client raced: its rename arrives against the merged queue —
        # renaming the survivor to the same target is a no-op.
        idx = app._queue_singer_match_index("Daniel")
        self.assertEqual(idx, -1)
        self.assertFalse(app._rename_rotation_singer(0, "Dan"))
        self.assertEqual(len(app.queue), 1)

    # -- history/preferences merge --------------------------------------------

    def test_prefs_and_history_merge_into_new_key(self):
        app = self._app()
        app.singer_preferences = {
            "daniel": {"name": "Daniel", "preferred_disc_priority": "KV, SC"},
        }
        app.singer_history = {"singers": {
            "daniel": {"name": "Daniel", "preferred_disc_priority": "KV, SC",
                       "updated_at": 10, "last_seen_at": 10, "total_performances": 3,
                       "unique_song_count": 1, "songs": {"artist|s1": {"title": "s1"}}},
            "dan": {"name": "Dan", "preferred_disc_priority": "",
                    "updated_at": 5, "last_seen_at": 5, "total_performances": 1,
                    "unique_song_count": 1, "songs": {"artist|s2": {"title": "s2"}}},
        }}
        app.queue = [singer("Dan", ["A"]), singer("Daniel", ["C"])]
        app._rename_rotation_singer(1, "Dan")
        self.assertNotIn("daniel", app.singer_history["singers"])
        merged = app.singer_history["singers"]["dan"]
        self.assertEqual(set(merged["songs"].keys()), {"artist|s1", "artist|s2"})
        self.assertNotIn("daniel", app.singer_preferences)


if __name__ == "__main__":
    unittest.main()
