"""`queue.add_song` (the Rust shell / phone adding a LIBRARY song) against the operator's own add routine.

DIFFERENTIAL: for random queues, singer names (existing, new, lower case, duets) and library songs of every kind (MP4, CDG+MP3 pair, MP3+G zip, plain file), the
REAL `_add_song_to_queue` runs once as the operator's Add button does it and once through `shell_queue_add_song`; the resulting queues (ids and times masked), the host
syncs and saves must be identical, and the shell run must NEVER touch the operator's screen (singer box, key selector, selection).
Plus the refusals the operator asked for (a unit test per code), the dry run, the argument checks and request-id dedupe.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import copy
import json
import os
import random
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path

import song_index
import test_shell_queue_commands as base

_ENV = {}


def env():
    """A throwaway library: real files for each kind, one missing file, one CDG without its MP3, one bad zip; a real song index over them."""
    if _ENV:
        return _ENV
    d = Path(tempfile.mkdtemp(prefix="shell_add_song_"))
    (d / "a.mp4").write_bytes(b"x")
    (d / "b.cdg").write_bytes(b"x")
    (d / "b.mp3").write_bytes(b"x")
    (d / "c.cdg").write_bytes(b"x")  # no c.mp3
    (d / "plain.kfn").write_bytes(b"x")
    with zipfile.ZipFile(d / "good.zip", "w") as z:
        z.writestr("song.cdg", b"x")
        z.writestr("song.mp3", b"x")
    with zipfile.ZipFile(d / "bad.zip", "w") as z:
        z.writestr("only.txt", b"x")
    names = ["a.mp4", "b.cdg", "c.cdg", "plain.kfn", "good.zip", "bad.zip", "gone.mp4"]
    tracks = [{"path": str(d / n), "artist": f"Artist {i}", "title": f"Title {i}", "discid": f"D{i}", "display": f"Artist {i} - Title {i}", "duration_secs": 100 + i,
               "type": Path(n).suffix[1:]} for i, n in enumerate(names)]
    tracks += [{"path": str(d / f"bulk{i:02d}.kfn"), "artist": "Bulk Artist", "title": f"Bulk Song {i:02d}", "discid": f"B{i}", "display": f"Bulk Artist - Bulk Song {i:02d}",
                "duration_secs": 200 + i, "type": "kfn"} for i in range(40)]
    tp = d / "tracks.json"
    tp.write_text(json.dumps(tracks))
    db = d / "s.db"
    song_index.rebuild_from_tracks_json(tp, db, verbose=False)
    song_index.db_path = lambda: db  # the app module calls song_index.find_by_id() with no database argument
    con = song_index._connect(db, read_only=True)
    ids = {Path(r["path"]).name: r["id"] for r in con.execute("SELECT id, path FROM songs")}
    con.close()
    _ENV.update(dir=d, db=db, ids=ids)
    return _ENV


class AddHarness(base.Harness):
    """The queue-command harness plus what the REAL add routine needs: recorded UI widgets and the server-sync hooks."""

    def __init__(self, queue, **kw):
        super().__init__(queue, **kw)
        a = self.app
        rec = self.record
        a.settings.update({"limit_pending_max": 2})
        a.singer_input = types.SimpleNamespace(clear=lambda: rec("singer_input.clear"), text=lambda: "typed by the operator")
        a.key_selector = types.SimpleNamespace(setCurrentIndex=lambda i: rec("key_selector.set", i), findText=lambda t: 4)
        a._queue_host_request_sync = lambda idx, song_idx: rec("host_sync", idx, song_idx)
        a._mark_inserted_singer_order_authoritative = lambda idx, is_remote=False, reason="": rec("order_authoritative", idx, reason)
        a._log_queue_insert_resolution = lambda **k: None
        a._mark_server_queue_mutation = lambda reason="": rec("server_mutation", reason)
        a._queue_order_snapshot_for_log = lambda singer: []
        a._select_queue_singer_for_host = lambda idx: rec("select_singer", idx)
        a._rotation_reweave_locked_tail = lambda: rec("reweave")
        a._rotation_insert_locked_new_singer = lambda s: (rec("insert_locked_new"), a.queue.append(s))[0]
        a._rotation_marker_index = lambda: -1
        a._rotation_boundary_index = lambda: len(a.queue)
        a._rotation_repair_marker = lambda: None
        a._rotation_recompute_round_state = lambda force_reset=False: None
        a._karaoke_normalize_active = lambda: False
        a._karaoke_normalize_bypass_reason = lambda: "test"
        a._prune_expired_empty_rotation_slots = lambda: None
        a._clear_temporary_empty_rotation_slot = lambda singer, reason="": None
        a._cleanup_duplicate_singer_songs = lambda reason="": None
        a._pending_request_counts_by_singer = lambda **k: {}
        a._queue_insert_source_label = lambda meta: "operator"
        a._update_queue_eta_label = lambda: None

    def ui_calls(self):
        return [c for c in self.calls if c[0] in ("singer_input.clear", "key_selector.set", "select_singer", "setCurrentRow")]


def mask(queue):
    """The queue with generated ids and times masked, so two runs can be compared."""
    out = copy.deepcopy(queue)
    for s in out:
        s["singer_id"] = "<id>" if s.get("singer_id") else ""
        s.pop("created_at", None)
        for e in s.get("songs", []):
            if isinstance(e, dict):
                for k in ("request_uid", "entry_id", "added_at"):
                    e.pop(k, None)
    return out


def track_for(name):
    e = env()
    return song_index.find_by_id(e["ids"][name])


def operator_add(h, singer, track, key=0):
    """What the Add button does for a library song: (path, key) / ((cdg, mp3), key) data and the track dict, with the screen side effects."""
    song_data = h.app._waiting_for_add_song_data_from_track({"key": key}, track)
    return h.app._add_song_to_queue(singer, song_data, track=track)


class AddSongTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env()

    def harness(self, queue=None, **kw):
        h = AddHarness(queue if queue is not None else base.random_queue(random.Random(1)), **kw)
        h.app._is_rotation_mode = lambda: True
        return h

    def test_it_adds_exactly_what_the_operators_add_adds_and_never_touches_the_screen(self):
        rng = random.Random(20261026)
        ok_names = ["a.mp4", "b.cdg", "good.zip", "plain.kfn"]
        compared = 0
        for case in range(300):
            queue = base.random_queue(rng)
            for s in queue:
                s["songs"] = s["songs"][:1]  # stay under the song limit so both paths add
            singer = rng.choice([queue[0]["name"], queue[0]["name"].lower(), "Brand New", "new person", "Amber & Delores", f"  {queue[-1]['name'].upper()}  "])
            name = rng.choice(ok_names)
            key = rng.randint(-4, 4)
            track = track_for(name)
            ha, hb = self.harness(copy.deepcopy(queue)), self.harness(copy.deepcopy(queue))
            if any(self._holds(hb.app, singer, track["path"]) for _ in [0]):
                continue  # the shell refuses duplicates by design; that case has its own test
            operator_add(ha, singer, track, key)
            ok, code = hb.app.shell_queue_add_song(singer, track["id"], key)
            self.assertTrue(ok, (code, singer, name))
            self.assertEqual(code, "added")
            self.assertEqual(mask(ha.app.queue), mask(hb.app.queue), f"queue differs for {singer!r} + {name}")
            host = lambda h: [c for c in h.calls if c[0] in ("host_sync", "order_authoritative", "request_queue_display_refresh", "insert_locked_new")]
            self.assertEqual(host(ha), host(hb))
            self.assertEqual(hb.ui_calls(), [], "the shell add must never clear the singer box, reset the key or move the selection")
            self.assertTrue(ha.ui_calls(), "(sanity) the operator's add does those things")
            self.assertEqual(hb.undo_names()[-1:], ["Add Song"])
            self.assertIn(("schedule_save", 0), hb.calls)
            compared += 1
        self.assertGreater(compared, 200)

    @staticmethod
    def _holds(app, singer, path):
        keys = {app._queue_limit_name_key(n) for n in app._duet_participants_from_display(*app._parse_duet_singer(app._capitalize_singer_name(singer)))}
        return any(app._queue_limit_name_key(s.get("name", "")) in keys and any(app._song_info_primary_path(e.get("song_info")) == path for e in s.get("songs", []))
                   for s in app.queue)

    def refusal(self, h, singer, song_id, key=0):
        before, undo_before, saves_before = copy.deepcopy(h.app.queue), list(h.undo_names()), [c for c in h.calls if c[0] == "schedule_save"]
        ok, code = h.app.shell_queue_add_song(singer, song_id, key)
        self.assertFalse(ok, code)
        self.assertEqual(h.app.queue, before, f"a refused add ({code}) must not change the queue")
        self.assertEqual(h.ui_calls(), [])
        self.assertEqual(h.undo_names(), undo_before, "a refused add leaves no undo entry")
        self.assertEqual([c for c in h.calls if c[0] == "schedule_save"], saves_before, "a refused add saves nothing")
        return code

    def test_each_refusal_has_its_own_clear_code_and_changes_nothing(self):
        e = env()
        ids = e["ids"]
        h = self.harness([])
        self.assertEqual(self.refusal(h, "Dana", 987654), "unknown_song")
        self.assertEqual(self.refusal(h, "Dana", ids["gone.mp4"]), "file_missing")
        self.assertEqual(self.refusal(h, "Dana", ids["c.cdg"]), "missing_mp3")
        # the app's own zip check (`is_mp3g_zip`) looks only at the file extension, so a zip with the wrong contents is NOT detected at this stage;
        # the shell inherits exactly that rule rather than inventing a stricter one
        self.assertTrue(h.app.shell_queue_add_song("Dana", ids["bad.zip"])[0])
        for bad in ("", "   ", "x" * 61, "line\nbreak", "tab\there", "bell\x07ring", "nul\x00byte", "esc\x1bseq", "del\x7fchar"):
            self.assertEqual(self.refusal(h, bad, ids["a.mp4"]), "bad_singer", repr(bad))

    def test_names_with_emoji_joiners_and_no_break_spaces_are_fine(self):
        h = self.harness([])
        for n, name in enumerate(("Mom \u200d\U0001F469 Dad", "Dan\u00a0Smith", "\u65e5\u672c \u592a\u90ce")):
            self.assertTrue(h.app.shell_queue_add_song(name, env()["ids"]["a.mp4"])[0], repr(name))

    def test_a_locked_rotation_refuses(self):
        h = self.harness(base.random_queue(random.Random(5)))
        h.app._is_rotation_locked = lambda: True
        self.assertEqual(self.refusal(h, "Someone", env()["ids"]["a.mp4"]), "rotation_locked")

    def test_a_singer_already_holding_the_song_is_refused_even_when_skipped_or_a_duet_partner(self):
        e = env()
        track = track_for("a.mp4")
        h = self.harness([])
        self.assertTrue(h.app.shell_queue_add_song("dana", track["id"])[0])
        self.assertEqual(self.refusal(h, "Dana", track["id"]), "duplicate")
        self.assertEqual(self.refusal(h, "  DANA ", track["id"]), "duplicate", "names are matched the way the queue limit matches them")
        h.app.queue[0]["skipped"] = True
        self.assertEqual(self.refusal(h, "Dana", track["id"]), "duplicate", "a skipped singer's song is still queued")
        self.assertTrue(h.app.shell_queue_add_song("Someone Else", track["id"])[0], "another singer may sing the same song")
        self.assertEqual(self.refusal(h, "Amber & Dana", track["id"]), "duplicate", "a duet partner already holding it counts too")

    def test_a_song_that_was_skipped_is_no_longer_queued_so_it_can_be_added_again(self):
        track = track_for("a.mp4")
        h = self.harness([])
        self.assertTrue(h.app.shell_queue_add_song("Dana", track["id"])[0])
        h.app.queue[0]["songs"][0]["skipped"] = True
        self.assertTrue(h.app.shell_queue_add_song("Dana", track["id"])[0])
        self.assertEqual(len(h.app.queue[0]["songs"]), 2)

    def test_the_song_limit_refuses_where_the_operators_add_would_go_over(self):
        e = env()
        h = self.harness([])
        for n in ("a.mp4", "b.cdg"):
            self.assertTrue(h.app.shell_queue_add_song("Dana", e["ids"][n])[0])
        self.assertEqual(h.app.settings["limit_pending_max"], 2)
        self.assertEqual(self.refusal(h, "Dana", e["ids"]["good.zip"]), "limit")
        self.assertEqual(len(h.app.queue[0]["songs"]), 2)
        # the operator's own Add button is allowed past the limit (the app logs the bypass); the shell must not be
        op = self.harness(copy.deepcopy(h.app.queue))
        operator_add(op, "Dana", track_for("good.zip"))
        self.assertEqual(len(op.app.queue[0]["songs"]), 3)

    def test_a_dry_run_runs_every_check_and_adds_nothing(self):
        e = env()
        h = self.harness([])
        before = copy.deepcopy(h.app.queue)
        self.assertEqual(h.app.shell_queue_add_song("Dana", e["ids"]["a.mp4"], 0, dry_run=True), (True, "dry_run: would add"))
        self.assertEqual(h.app.queue, before)
        self.assertEqual(h.app.shell_queue_add_song("Dana", 987654, 0, dry_run=True), (False, "unknown_song"))
        for n in ("a.mp4", "b.cdg"):
            h.app.shell_queue_add_song("Dana", e["ids"][n])
        self.assertEqual(h.app.shell_queue_add_song("Dana", e["ids"]["good.zip"], 0, dry_run=True), (False, "limit"))
        self.assertEqual(h.app.shell_queue_add_song("Dana", e["ids"]["a.mp4"], 0, dry_run=True), (False, "duplicate"))

    def command(self, h, args, mode="apply", rid="r1", extra=None):
        h.app.settings["shell_commands"] = mode
        a = dict(args, _request_id=rid, _sent_at=time.time())
        a.update(extra or {})
        return h.app._execute_shell_queue_command("queue.add_song", a)

    def test_the_command_checks_its_arguments_and_honours_the_mode_and_request_ids(self):
        e = env()
        h = self.harness([])
        good = {"singer": "Dana", "song_id": e["ids"]["a.mp4"], "key": 2}
        for bad, code in (({"song_id": 0}, "bad_song_id"), ({"song_id": -3}, "bad_song_id"), ({"song_id": True}, "bad_song_id"), ({"song_id": "5"}, "bad_song_id"),
                          ({"song_id": 1.0}, "bad_song_id"), ({"key": 5}, "bad_key"), ({"key": -5}, "bad_key"), ({"key": "1"}, "bad_key"), ({"key": True}, "bad_key"),
                          ({"singer": 5}, "bad_singer"), ({"singer": None}, "bad_singer")):
            ok, message = self.command(h, dict(good, **bad), rid=f"bad-{code}-{id(bad)}")
            self.assertEqual((ok, message), (False, code), bad)
        self.assertEqual(h.app.queue, [])
        self.assertEqual(self.command(h, good, mode="off", rid="off1"), (False, "shell_commands_off"))
        self.assertEqual(self.command(h, good, mode="dry_run", rid="dry1"), (True, "dry_run: would add"))
        self.assertEqual(h.app.queue, [])
        self.assertEqual(self.command(h, good, mode="apply", rid="go1"), (True, "added"))
        self.assertEqual(len(h.app.queue[0]["songs"]), 1)
        self.assertEqual(h.app.queue[0]["songs"][0]["key"], 2)
        again = self.command(h, good, mode="apply", rid="go1")
        self.assertEqual(again, (True, "duplicate: added"), "the same request id answers from memory and adds nothing")
        self.assertEqual(len(h.app.queue[0]["songs"]), 1)

    def test_refusals_come_back_through_the_command_as_not_ok_with_their_code(self):
        e = env()
        h = self.harness([])
        for n, (args, code) in enumerate((({"singer": "Dana", "song_id": 987654}, "unknown_song"), ({"singer": "Dana", "song_id": e["ids"]["gone.mp4"]}, "file_missing"),
                                          ({"singer": "Dana", "song_id": e["ids"]["c.cdg"]}, "missing_mp3"), ({"singer": "", "song_id": e["ids"]["a.mp4"]}, "bad_singer"))):
            self.assertEqual(self.command(h, args, rid=f"ref{n}"), (False, code))
        self.assertEqual(h.app.queue, [])
        self.assertEqual(self.command(h, {"singer": "Dana", "song_id": e["ids"]["a.mp4"]}, mode="dry_run", rid="refd")[0], True)

    def test_a_stale_list_fingerprint_is_still_honoured_when_one_is_sent(self):
        e = env()
        h = self.harness(base.random_queue(random.Random(3)))
        ok, message = self.command(h, {"singer": "Dana", "song_id": e["ids"]["a.mp4"]}, rid="s1", extra={"expect_queue_sig": "0000000000000000"})
        self.assertEqual((ok, message), (False, "stale_view"))


class LibrarySearchTests(unittest.TestCase):
    """`library.search`: read-only song lookup for the phone / shell; the answer is one `library` message, never a path."""

    @classmethod
    def setUpClass(cls):
        env()

    def make(self, mode="apply", connected=True):
        import threading as th
        h = AddHarness([])
        a = h.app
        a.settings["shell_commands"] = mode
        h.sent, h.ui_calls_count, h.arrived = [], [0], th.Event()

        class Worker:
            def is_connected(self_inner):
                return connected

            def send_json(self_inner, obj):
                h.sent.append(obj)
                h.arrived.set()

        a.host_relay_worker = Worker()

        def on_ui(fn):
            h.ui_calls_count[0] += 1
            fn()

        a._run_on_ui_thread = on_ui
        return h

    def search(self, h, query, rid="q1", mode=None):
        h.arrived.clear()
        args = {"query": query, "_request_id": rid, "_sent_at": time.time()}
        return h.app._execute_shell_queue_command("library.search", args)

    def test_rows_come_back_as_one_library_message_without_paths(self):
        h = self.make()
        self.assertEqual(self.search(h, "title 3"), (True, "searching"))
        self.assertTrue(h.arrived.wait(5))
        self.assertEqual(len(h.sent), 1)
        msg = h.sent[0]
        self.assertEqual((msg["type"], msg["request_id"], msg["query"], msg["fuzzy"]), ("library", "q1", "title 3", False))
        expect = song_index.search_songs("title 3", limit=25, fuzzy=False)
        self.assertEqual([r["id"] for r in msg["rows"]], [r["id"] for r in expect])
        self.assertEqual(msg["rows"][0], {"id": expect[0]["id"], "artist": "Artist 3", "title": "Title 3", "kind": expect[0]["song_type"], "duration_secs": 103})
        self.assertNotIn("path", json.dumps(msg).lower().replace("duration", ""), "no file path ever leaves the app")
        self.assertEqual(h.ui_calls_count[0], 1, "the reply is posted through the GUI thread, never sent from the worker thread")

    def test_a_misspelling_is_rescued_and_flagged_and_nothing_found_is_an_empty_list(self):
        h = self.make()
        self.search(h, "artst 3", rid="q2")
        self.assertTrue(h.arrived.wait(5))
        self.assertTrue(h.sent[-1]["fuzzy"])
        self.assertIn("Artist 3", [r["artist"] for r in h.sent[-1]["rows"]])
        self.search(h, "zzzzzzzzzz", rid="q3")
        self.assertTrue(h.arrived.wait(5))
        self.assertEqual((h.sent[-1]["rows"], h.sent[-1]["fuzzy"]), ([], False))

    def test_at_most_25_rows(self):
        h = self.make()
        self.search(h, "bulk")
        self.assertTrue(h.arrived.wait(5))
        self.assertEqual(len(h.sent[0]["rows"]), 25)
        self.assertEqual(h.sent[0]["rows"][0]["title"], "Bulk Song 00", "strict results keep the app's artist/title order")

    def test_bad_queries_and_a_missing_relay_are_refused_before_any_search(self):
        h = self.make()
        for bad in ("", "   ", "x" * 81, 5, None, ["a"]):
            self.assertEqual(self.search(h, bad, rid=f"b{id(bad)}"), (False, "bad_query"), repr(bad))
        self.assertEqual(h.sent, [])
        self.assertEqual(self.search(self.make(connected=False), "title"), (False, "needs_relay"))

    def test_off_refuses_dry_run_still_answers_because_a_search_changes_nothing(self):
        self.assertEqual(self.search(self.make("off"), "title"), (False, "shell_commands_off"))
        h = self.make("dry_run")
        self.assertEqual(self.search(h, "title 3"), (True, "searching"))
        self.assertTrue(h.arrived.wait(5))

    def test_it_arrives_through_the_relay_command_entry_point_with_an_ack_then_the_rows(self):
        h = self.make()
        h.app._publish_host_state_ws = lambda force=False: None
        h.arrived.clear()
        h.app._on_host_relay_command({"type": "command", "command": "library.search", "args": {"query": "title 3"}, "request_id": "r9", "sent_at": int(time.time())})
        self.assertTrue(h.arrived.wait(5))
        types_ = [m["type"] for m in h.sent]
        self.assertEqual(h.sent[0], {"type": "ack", "request_id": "r9", "ok": True, "message": "searching"})
        deadline = time.time() + 5
        while len(h.sent) < 2 and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual([m["type"] for m in h.sent], ["ack", "library"], types_)
        self.assertEqual(h.sent[1]["request_id"], "r9")
        # the same entry point refuses it cleanly when remote control is off
        off = self.make("off")
        off.app._publish_host_state_ws = lambda force=False: None
        off.app._on_host_relay_command({"type": "command", "command": "library.search", "args": {"query": "x"}, "request_id": "r10", "sent_at": int(time.time())})
        self.assertEqual(off.sent[0], {"type": "ack", "request_id": "r10", "ok": False, "message": "shell_commands_off"})

    def test_the_same_request_id_searches_once(self):
        h = self.make()
        self.assertEqual(self.search(h, "title 3", rid="same"), (True, "searching"))
        self.assertTrue(h.arrived.wait(5))
        self.assertEqual(self.search(h, "title 3", rid="same"), (True, "duplicate: searching"))
        time.sleep(0.2)
        self.assertEqual(len(h.sent), 1)

    def test_a_failing_search_still_answers_with_an_empty_list_and_a_short_error(self):
        h = self.make()
        real = song_index.search_songs
        song_index.search_songs = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk gone"))
        try:
            self.assertEqual(self.search(h, "title"), (True, "searching"))
            self.assertTrue(h.arrived.wait(5))
        finally:
            song_index.search_songs = real
        self.assertEqual((h.sent[0]["rows"], h.sent[0]["error"]), ([], "disk gone"))


if __name__ == "__main__":
    unittest.main()
