"""`waitlist.edit`, `history.edit_song` and `history.add_song`: the shell doing what the Edit Waitlist Request and Edit / Add Song History dialogs' Save buttons do.

Real code under test: `_execute_shell_queue_command` (gate, dedupe, validation), `shell_waitlist_change`, `_waitlist_apply_edit`, `shell_history_song`, `_history_song_apply_edit`,
`_history_edit_backup`, `_normalize_history_song_key`. The history helper is also compared with a frozen copy of the dialog's old inline Save code over random inputs.
Recorded stand-ins: server pushes, the commit (save + server sync), the view refresh."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import copy
import json
import random
import time
import types
import unittest
from pathlib import Path

import test_shell_add_song as ta
import test_shell_queue_commands as base
import test_shell_waitlist_commands as tw


def run(h, action, args, rid):
    return h.app._execute_shell_queue_command(action, dict(args, _request_id=rid, _sent_at=time.time()))


# ---------------------------------------------------------------- waitlist.edit
def wl_harness(mode="apply", waiting=None, accepted=()):
    h = tw.harness(mode=mode, waiting=waiting, accepted=accepted)
    a = h.app
    for name in ("_waitlist_apply_edit", "_update_waiting_for_add_request"):
        setattr(a, name, types.MethodType(getattr(h.m.KaraokeApp, name), a))
    a._remote_attention_requests = {}
    a._push_remote_request_replacement = lambda entry, **k: h.record("push", dict(entry), dict(k))
    a._build_queue_entry_from_track_choice = lambda base_entry, track: dict(base_entry, replaced=True)
    return h


EDIT = {"request_id": 7, "singer": " Ann ", "artist": "Oasis", "title": "Wonderwall", "key": 2, "tempo": -5}


class WaitlistEditTests(unittest.TestCase):
    def test_it_edits_the_same_request_like_the_dialog_and_tells_the_server(self):
        h = wl_harness(waiting={7: tw.req(7)})
        self.assertEqual(run(h, "waitlist.edit", EDIT, "e1"), (True, "waitlist_edited"))
        r = h.app._waiting_for_add_requests[7]
        self.assertEqual((r["request_id"], r["singer"], r["artist"], r["title"], r["key"], r["tempo"]), (7, "Ann", "Oasis", "Wonderwall", 2, -5))
        self.assertEqual((r["attention_reason"], r["pending_status"], r["state"]), ("host_edited_waitlist_metadata", "Metadata edited", "waiting"), "same request, still waiting")
        push = [c for c in h.calls if c[0] == "push"]
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0][1], {"remote_request_id": 7, "artist": "Oasis", "title": "Wonderwall", "key": 2, "tempo_percent": 95})
        self.assertEqual(push[0][2], {"singer_name": "Ann", "old_artist": "Queen", "old_title": "Radio Ga Ga", "source": "waiting_metadata_edit"})
        self.assertEqual([c for c in h.calls if c[0] == "toast"], [], "no toast from a remote edit")

    def test_a_replacement_track_is_kept_and_pushed(self):
        h = wl_harness(waiting={7: dict(tw.req(7), replacement_track={"path": "/x.cdg"})})
        run(h, "waitlist.edit", EDIT, "e1")
        self.assertTrue([c for c in h.calls if c[0] == "push"][0][1]["replaced"])

    def test_unknown_queued_and_accepted_requests_are_refused_and_nothing_changes(self):
        h = wl_harness(waiting={7: tw.req(7), 9: tw.req(9)}, accepted=[9])
        before = copy.deepcopy(h.app._waiting_for_add_requests)
        for n, rid in enumerate((99, 9)):
            self.assertEqual(run(h, "waitlist.edit", dict(EDIT, request_id=rid), f"u{n}"), (False, "unknown_request"))
        self.assertEqual(h.app._waiting_for_add_requests, before)
        self.assertEqual([c for c in h.calls if c[0] == "push"], [])

    def test_every_bad_value_is_refused_with_one_code_and_changes_nothing(self):
        bad = [dict(EDIT, singer=""), dict(EDIT, singer="  "), dict(EDIT, singer="x" * 61), dict(EDIT, singer="A\nB"), dict(EDIT, title=""), dict(EDIT, title="x" * 101),
               dict(EDIT, artist="x" * 101), dict(EDIT, artist="a\tb"), dict(EDIT, artist=None), dict(EDIT, key=13), dict(EDIT, key=-13), dict(EDIT, key=1.0), dict(EDIT, key=True),
               dict(EDIT, key="1"), dict(EDIT, tempo=31), dict(EDIT, tempo=-31), dict(EDIT, tempo=2.5), dict(EDIT, tempo=None)]
        for n, args in enumerate(bad):
            h = wl_harness(waiting={7: tw.req(7)})
            before = copy.deepcopy(h.app._waiting_for_add_requests)
            self.assertEqual(run(h, "waitlist.edit", args, f"b{n}"), (False, "bad_waitlist_value"), args)
            self.assertEqual(h.app._waiting_for_add_requests, before)
            self.assertEqual([c for c in h.calls if c[0] == "push"], [])
        for edge in (dict(EDIT, singer="x" * 60, artist="x" * 100, title="x" * 100, key=-12, tempo=30), dict(EDIT, artist="", key=12, tempo=-30)):
            h = wl_harness(waiting={7: tw.req(7)})
            self.assertEqual(run(h, "waitlist.edit", edge, "ok"), (True, "waitlist_edited"))

    def test_dry_run_changes_and_pushes_nothing_and_off_refuses(self):
        h = wl_harness(mode="dry_run", waiting={7: tw.req(7)})
        ok, msg = run(h, "waitlist.edit", EDIT, "d1")
        self.assertTrue(ok and msg.startswith("dry_run"))
        self.assertEqual(h.app._waiting_for_add_requests[7]["title"], "Radio Ga Ga")
        self.assertEqual([c for c in h.calls if c[0] == "push"], [])
        o = wl_harness(mode="off", waiting={7: tw.req(7)})
        self.assertFalse(run(o, "waitlist.edit", EDIT, "o1")[0])
        self.assertEqual(o.app._waiting_for_add_requests[7]["title"], "Radio Ga Ga")

    def test_a_replayed_request_id_applies_once(self):
        h = wl_harness(waiting={7: tw.req(7)})
        first = run(h, "waitlist.edit", EDIT, "same")
        second = run(h, "waitlist.edit", dict(EDIT, title="Other"), "same")
        self.assertEqual(first, (True, "waitlist_edited"))
        self.assertTrue(second[1].startswith("duplicate"), second)
        self.assertEqual(h.app._waiting_for_add_requests[7]["title"], "Wonderwall")
        self.assertEqual(len([c for c in h.calls if c[0] == "push"]), 1)


# ---------------------------------------------------------------- history
def hist_store():
    return {"singers": {"ann": {"name": "Ann", "songs": {
        "queen|radio ga ga|": {"artist": "Queen", "title": "Radio Ga Ga", "disc_id": "SC1", "songid": "SC1", "song_type": "CDG", "last_key": 1, "last_tempo_percent": 100,
                               "play_count": 3, "first_performed_at": 10, "last_performed_at": 20, "path": "/lib/a.cdg", "updated_at": 5},
    }, "unique_song_count": 1}, "bob": {"name": "Bob", "songs": {}}}}


def hist_harness(mode="apply", playing=False):
    h = ta.AddHarness(base.random_queue(random.Random(2)))
    a = h.app
    a.settings.update({"shell_commands": mode})
    a.singer_history = hist_store()
    a.karaoke_playing = playing
    for name in ("shell_history_song", "_history_song_apply_edit", "_history_edit_backup", "_normalize_history_song_key"):
        setattr(a, name, types.MethodType(getattr(h.m.KaraokeApp, name), a))
    a._commit_singer_history_change = lambda reason="": h.record("commit", reason)
    return h


GOOD = {"singer_key": "ann", "song_key": "queen|radio ga ga|", "artist": "Queen", "title": "Radio Ga Ga (Live)", "disc_id": "SC9", "song_type": "zip", "key": -2, "tempo_percent": 90}


class HistoryEditTests(unittest.TestCase):
    def backups(self, h):
        folder = h.m.APP_USER_DIR / "cache-backups" / "history-edits"
        return sorted(folder.glob("singer_history-*.json")) if folder.exists() else []

    def setUp(self):
        import shutil
        shutil.rmtree(ta.base.Harness([]).m.APP_USER_DIR / "cache-backups" / "history-edits", ignore_errors=True)

    def test_edit_writes_a_backup_of_the_old_history_then_changes_and_commits_once(self):
        h = hist_harness()
        old = copy.deepcopy(h.app.singer_history)
        self.assertEqual(run(h, "history.edit_song", GOOD, "h1"), (True, "history_song_saved"))
        files = self.backups(h)
        self.assertEqual(len(files), 1)
        self.assertEqual(json.loads(files[0].read_text()), old, "the backup is the history as it was BEFORE the edit")
        songs = h.app.singer_history["singers"]["ann"]["songs"]
        self.assertEqual(len(songs), 1)
        s = next(iter(songs.values()))
        self.assertEqual((s["title"], s["disc_id"], s["song_type"], s["last_key"], s["last_tempo_percent"], s["play_count"], s["path"]), ("Radio Ga Ga (Live)", "SC9", "ZIP", -2, 90, 3, "/lib/a.cdg"))
        self.assertEqual([c for c in h.calls if c[0] == "commit"], [("commit", "history_song_edit")])

    def test_add_song_adds_one_with_the_dialogs_defaults(self):
        h = hist_harness()
        args = {k: v for k, v in GOOD.items() if k != "song_key"}
        args["singer_key"] = "bob"
        self.assertEqual(run(h, "history.add_song", args, "h2"), (True, "history_song_saved"))
        songs = h.app.singer_history["singers"]["bob"]["songs"]
        self.assertEqual(len(songs), 1)
        s = next(iter(songs.values()))
        self.assertEqual((s["play_count"], s["first_performed_at"], s["last_performed_at"], s["path"], s["songid"]), (0, 0, 0, "", "SC9"))
        self.assertEqual(h.app.singer_history["singers"]["bob"]["unique_song_count"], 1)
        self.assertEqual([c for c in h.calls if c[0] == "commit"], [("commit", "history_song_add")])
        self.assertEqual(len(self.backups(h)), 1)

    def test_playback_unknown_singer_song_and_bad_values_are_refused_and_nothing_is_written(self):
        cases = [(hist_harness(playing=True), GOOD, "playback_active"),
                 (hist_harness(), dict(GOOD, singer_key="zed"), "unknown_history_singer"),
                 (hist_harness(), dict(GOOD, song_key="nope"), "unknown_history_song"),
                 (hist_harness(), dict(GOOD, song_key=""), "bad_history_key"),
                 (hist_harness(), {k: v for k, v in GOOD.items() if k != "singer_key"}, "bad_history_key")]
        for bad in (dict(GOOD, title=""), dict(GOOD, title="x" * 101), dict(GOOD, artist="x" * 101), dict(GOOD, disc_id="x" * 41), dict(GOOD, song_type="x" * 11), dict(GOOD, title="a\nb"),
                    dict(GOOD, key=25), dict(GOOD, key=-25), dict(GOOD, key=1.5), dict(GOOD, key=True), dict(GOOD, tempo_percent=49), dict(GOOD, tempo_percent=201), dict(GOOD, tempo_percent="90")):
            cases.append((hist_harness(), bad, "bad_history_value"))
        for n, (h, args, code) in enumerate(cases):
            before = copy.deepcopy(h.app.singer_history)
            self.assertEqual(run(h, "history.edit_song", args, f"r{n}"), (False, code), args)
            self.assertEqual(h.app.singer_history, before)
            self.assertEqual([c for c in h.calls if c[0] == "commit"], [])
            self.assertEqual(self.backups(h), [], "no backup for a refused edit")

    def test_the_edges_of_the_ranges_are_accepted(self):
        for n, (k, t) in enumerate(((-24, 50), (24, 200), (0, 100))):
            h = hist_harness()
            self.assertEqual(run(h, "history.edit_song", dict(GOOD, key=k, tempo_percent=t), f"e{n}"), (True, "history_song_saved"), (k, t))

    def test_add_song_ignores_a_stray_song_key_and_never_replaces_an_existing_song(self):
        h = hist_harness()
        self.assertEqual(run(h, "history.add_song", dict(GOOD, title="Another"), "s1"), (True, "history_song_saved"))
        songs = h.app.singer_history["singers"]["ann"]["songs"]
        self.assertEqual(sorted(x["title"] for x in songs.values()), ["Another", "Radio Ga Ga"])

    def test_a_failed_backup_refuses_the_edit(self):
        h = hist_harness()
        h.app._history_edit_backup = lambda reason: False
        before = copy.deepcopy(h.app.singer_history)
        self.assertEqual(run(h, "history.edit_song", GOOD, "f1"), (False, "backup_failed"))
        self.assertEqual(h.app.singer_history, before)
        self.assertEqual([c for c in h.calls if c[0] == "commit"], [])

    def test_dry_run_and_off_change_and_back_up_nothing(self):
        d = hist_harness(mode="dry_run")
        ok, msg = run(d, "history.edit_song", GOOD, "d1")
        self.assertTrue(ok and msg.startswith("dry_run"))
        self.assertEqual(d.app.singer_history, hist_store())
        o = hist_harness(mode="off")
        self.assertFalse(run(o, "history.edit_song", GOOD, "o1")[0])
        self.assertEqual(self.backups(d), [])

    def test_only_the_newest_30_backups_are_kept(self):
        h = hist_harness()
        for n in range(33):
            self.assertTrue(h.app._history_edit_backup(f"r{n:02d}"))
            time.sleep(0.002)
        self.assertLessEqual(len(self.backups(h)), 30)

    def test_the_helper_equals_the_dialogs_old_inline_save_over_random_inputs(self):
        def reference(history, singer_key, song_key, f, now, normalize):
            """The dialog's handle_save before the refactor (frozen copy)."""
            record = history["singers"][singer_key]
            songs = record.setdefault("songs", {})
            existing = dict(songs.get(song_key, {})) if song_key else {}
            if song_key:
                songs.pop(song_key, None)
            new_song = dict(existing)
            new_song["artist"] = str(f["artist"] or "").strip()
            new_song["title"] = str(f["title"] or "").strip()
            new_song["disc_id"] = str(f["disc_id"] or "").strip()
            new_song["song_type"] = str(f["song_type"] or "").strip().upper()
            new_song["last_key"] = int(f["last_key"])
            new_song["last_tempo_percent"] = int(f["last_tempo_percent"])
            new_song["updated_at"] = now
            new_song.setdefault("songid", str(new_song.get("disc_id", "") or ""))
            new_song.setdefault("path", str(existing.get("path", "") or ""))
            new_song.setdefault("play_count", int(existing.get("play_count") or 0))
            new_song.setdefault("first_performed_at", int(existing.get("first_performed_at") or 0))
            new_song.setdefault("last_performed_at", int(existing.get("last_performed_at") or 0))
            new_key = normalize(new_song.get("artist", ""), new_song.get("title", ""), new_song.get("songid", ""), new_song.get("path", ""), new_song.get("provider", ""), new_song.get("provider_track_id", ""))
            if not new_key:
                new_key = song_key or "manual|1800000000.0"
            songs[new_key] = new_song
            record["unique_song_count"] = len(songs)
            record["updated_at"] = now
            return new_key

        rng = random.Random(20261029)
        words = ["", "Queen", " Oasis ", "Don't Stop", "ABBA", "x" * 40, "é ü", "A&B", "  "]
        for n in range(300):
            h = hist_harness()
            a = h.app
            real_time = h.m.time.time
            h.m.time.time = lambda: 1_800_000_000.0
            try:
                fields = {"artist": rng.choice(words), "title": rng.choice(words), "disc_id": rng.choice(["", "SC1", " KV9 "]), "song_type": rng.choice(["", "cdg", " Zip "]),
                          "last_key": rng.randint(-24, 24), "last_tempo_percent": rng.randint(50, 200)}
                song_key = rng.choice([None, "queen|radio ga ga|"])
                singer = rng.choice(["ann", "bob"]) if song_key is None else "ann"
                expected = hist_store()
                key_exp = reference(expected, singer, song_key, fields, 1_800_000_000, a._normalize_history_song_key)
                key_got = a._history_song_apply_edit(singer, song_key, fields)
            finally:
                h.m.time.time = real_time
            self.assertEqual(key_got, key_exp, (n, fields))
            self.assertEqual(a.singer_history, expected, (n, fields))


if __name__ == "__main__":
    unittest.main()
