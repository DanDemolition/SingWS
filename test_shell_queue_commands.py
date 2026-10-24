"""The Rust shell's id-based queue commands against the button handlers they mirror.

DIFFERENTIAL: for random queues and every edit (move singer/song up/down/top/bottom, remove singer/song, skip singer), the REAL handlers
(`move_up`, `move_down`, `remove_selected`, `toggle_singer_skip`) are driven on one copy of the queue through a fake widget list, and the new
`queue.*` commands on another copy. The resulting queues, the recorded calls (display refreshes, remote-order syncs, remote-request deletions,
empty-slot marks, saves, ticker updates), the undo history and the "playback detached" flag must be identical.
Plus every guard: setting off / dry_run / apply, duplicate ids, expiry, clock skew, stale view, unknown ids, bad arguments, transport gating.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import copy
import importlib.util
import random
import sys
import time
import types
import unittest
from pathlib import Path

_MOD = None


def module():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("singws_main_shell_cmd", str(Path(__file__).with_name("0.2.18.1.py")))
        _MOD = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _MOD
        spec.loader.exec_module(_MOD)
    return _MOD


def entry(n, remote=False, skipped=False):
    e = {"song_info": f"/lib/{n}.zip", "artist": f"Artist{n}", "title": f"Title{n}", "key": 0, "skipped": skipped, "request_uid": f"uid{n}"}
    if remote:
        e["remote_request_id"] = 1000 + n
    return e


def random_queue(rng):
    queue, n = [], 0
    for i in range(rng.randint(1, 6)):
        songs = []
        for _ in range(rng.randint(0, 5)):
            n += 1
            songs.append(entry(n, remote=rng.random() < 0.5, skipped=rng.random() < 0.2))
        queue.append({"singer_id": f"s{i}", "name": f"Singer {i}", "songs": songs, "skipped": rng.random() < 0.2, "has_sung": False,
                      "round_sung": False, "rotation_marker": i == 0, "created_at": float(i)})
    return queue


class Harness:
    """A bare KaraokeApp whose collaborators are recorded; `rows` mimics the QListWidget the handlers read."""

    def __init__(self, queue, playing=False, current_singer="", current_request=""):
        m = module()
        self.m = m
        self.calls = []
        a = m.KaraokeApp.__new__(m.KaraokeApp)
        self.app = a
        a.queue = queue
        a.settings = {"shell_commands": "apply", "queue_mode": "rotation"}
        a._waiting_for_add_requests = {}
        a._waiting_for_add_handled_ids = set()
        a._remote_attention_requests = {}
        a._remote_request_tombstones = {"requests": {}}
        a._remote_removed_request_ids = set()
        a._pending_remote_order_syncs = {}
        a._queue_revision = 3
        a._undo_stack = []
        a._undo_limit = 20
        a._undo_restoring = False
        a._undo_action = None
        a.karaoke_playing = playing
        a._current_karaoke_singer_id = current_singer
        a._current_karaoke_request_id = current_request
        a._current_playback_detached_from_rotation = False
        rec = self.record
        a.update_queue_display = lambda: rec("update_queue_display")
        a._request_queue_display_refresh = lambda: rec("request_queue_display_refresh")
        a._sync_remote_singer_order = lambda idx, reason="host_reorder": rec("sync_order", idx, reason)
        a._refresh_queue_song_row_identities = lambda idx: rec("refresh_identities", idx)
        a._update_queue_eta_label = lambda: rec("eta")
        a.schedule_ticker_update = lambda: rec("ticker")
        a._delete_remote_request = lambda rid, entry=None, singer_name="", reason="host_remove": rec("delete_remote", rid, singer_name, reason)
        a._mark_rotation_slot_temporarily_empty = lambda singer, reason="song_removed": (rec("mark_empty", singer["name"], reason), True)[1]
        a._schedule_save_data = lambda delay=1200: rec("schedule_save", delay)
        a.save_data = lambda: rec("save_data")
        a._cancel_pending_singer_start_for_queue_change = lambda reason: rec("cancel_pending_start", reason)
        a._update_undo_action = lambda: None
        a._delete_rotation_singer_row = lambda idx, reason="cleanup": (rec("delete_rotation_row", idx, reason), a.queue.pop(idx))[0]
        # the widget list the handlers read
        a.queue_display = types.SimpleNamespace(count=lambda: len(self.rows()), item=lambda r: self.rows()[r] if 0 <= r < len(self.rows()) else None,
                                                setCurrentRow=lambda r: rec("setCurrentRow", r))
        a._queue_item_kind = lambda item: item[0]
        a._queue_item_singer_index = lambda item, row=-1: item[1]
        a._queue_item_song_indices = lambda item, row=-1: (item[1], item[2])
        a._row_for_singer_index = lambda idx: self.rows().index(("singer", idx, -1))
        self.selected = -1
        a.get_selected_index = lambda: self.selected

    def record(self, *call):
        self.calls.append(call)

    def rows(self):
        out = []
        for i, singer in enumerate(self.app.queue):
            out.append(("singer", i, -1))
            out.extend(("song", i, j) for j in range(len(singer.get("songs", []))))
        return out

    def logical_calls(self):
        return [c for c in self.calls if c[0] != "setCurrentRow"]

    def undo_names(self):
        return [c["name"] for c in self.app._undo_stack]

    def shift(self, held):
        qa = self.m.QApplication
        flag = self.m.Qt.KeyboardModifier.ShiftModifier if held else self.m.Qt.KeyboardModifier.NoModifier
        self._saved = qa.keyboardModifiers
        qa.keyboardModifiers = staticmethod(lambda: flag)

    def unshift(self):
        self.m.QApplication.keyboardModifiers = self._saved

    def command(self, action, args):
        a = dict(args)
        a.setdefault("_request_id", f"r{len(self.calls)}-{time.perf_counter_ns()}")
        a.setdefault("_sent_at", time.time())
        return self.m.KaraokeApp._execute_shell_queue_command(self.app, action, a)


class DifferentialTests(unittest.TestCase):
    def run_pair(self, rng, op):
        queue = random_queue(rng)
        all_entries = [e for s in queue for e in s["songs"]]
        playing = rng.random() < 0.5
        cur_singer = rng.choice(queue)["singer_id"] if playing and rng.random() < 0.5 else ""
        cur_request = f"local:{rng.choice(all_entries)['request_uid']}" if playing and all_entries and rng.random() < 0.5 else ""
        h_button = Harness(copy.deepcopy(queue), playing, cur_singer, cur_request)
        h_cmd = Harness(copy.deepcopy(queue), playing, cur_singer, cur_request)
        si = rng.randrange(len(queue))
        singer = queue[si]
        sj = rng.randrange(len(singer["songs"])) if singer["songs"] else -1
        kind, arg = op
        where = arg
        if kind in ("move_song", "remove_song") and sj < 0:
            return None
        # ---- the button path
        b = h_button
        if kind == "move_singer":
            b.selected = b.rows().index(("singer", si, -1))
            b.shift(where in ("top", "bottom"))
            try:
                (b.app.move_up if where in ("up", "top") else b.app.move_down)()
            finally:
                b.unshift()
            action, args = "queue.move_singer", {"singer_id": singer["singer_id"], "direction": where}
        elif kind == "move_song":
            b.selected = b.rows().index(("song", si, sj))
            b.shift(where in ("top", "bottom"))
            try:
                (b.app.move_up if where in ("up", "top") else b.app.move_down)()
            finally:
                b.unshift()
            action, args = "queue.move_song", {"entry_id": h_cmd.m.KaraokeApp._ensure_queue_entry_id(singer["songs"][sj]), "direction": where}
        elif kind == "remove_singer":
            b.selected = b.rows().index(("singer", si, -1))
            b.shift(False)
            try:
                b.app.remove_selected()
            finally:
                b.unshift()
            action, args = "queue.remove_singer", {"singer_id": singer["singer_id"]}
        elif kind == "remove_song":
            b.selected = b.rows().index(("song", si, sj))
            b.shift(False)
            try:
                b.app.remove_selected()
            finally:
                b.unshift()
            action, args = "queue.remove_song", {"entry_id": h_cmd.m.KaraokeApp._ensure_queue_entry_id(singer["songs"][sj])}
        else:  # skip / unskip
            want = bool(where)
            if bool(singer.get("skipped", False)) != want:
                b.app.toggle_singer_skip(si)
            action, args = "queue.skip_singer", {"singer_id": singer["singer_id"], "skipped": want}
        # ---- the command path
        ok, message = h_cmd.command(action, args)
        self.assertTrue(ok, (action, args, message))
        return b, h_cmd, (action, args, message)

    def compare(self, b, c, label):  # noqa: C901
        self.assertEqual(b.app.queue, c.app.queue, f"{label}: queue differs")
        self.assertEqual(b.logical_calls(), c.logical_calls(), f"{label}: side effects differ")
        self.assertEqual(b.undo_names(), c.undo_names(), f"{label}: undo history differs")
        self.assertEqual(b.app._current_playback_detached_from_rotation, c.app._current_playback_detached_from_rotation, f"{label}: detach flag differs")
        self.assertEqual([c["before"] for c in b.app._undo_stack], [c["before"] for c in c.app._undo_stack], f"{label}: undo snapshots differ")

    def test_every_edit_matches_the_button_handlers(self):
        rng = random.Random(20261024)
        ops = [("move_singer", w) for w in ("up", "down", "top", "bottom")] + [("move_song", w) for w in ("up", "down", "top", "bottom")] + \
              [("remove_singer", None), ("remove_song", None), ("skip", True), ("skip", False)]
        done = {op: 0 for op in ops}
        changed = 0
        for case in range(1200):
            op = ops[case % len(ops)]
            pair = self.run_pair(rng, op)
            if pair is None:
                continue
            b, c, info = pair
            label = f"case {case} {op} {info[0]}"
            self.compare(b, c, label)
            done[op] += 1
            if b.logical_calls():
                changed += 1
        self.assertTrue(all(v > 30 for v in done.values()), done)
        self.assertGreater(changed, 300, "most generated edits must actually change something")

    def test_the_result_text_says_what_happened(self):
        h = Harness([{"singer_id": "a", "name": "A", "songs": [entry(1)]}, {"singer_id": "b", "name": "B", "songs": [entry(2), entry(3)]}])
        self.assertEqual(h.command("queue.move_singer", {"singer_id": "a", "direction": "up"}), (True, "no_change"))
        self.assertEqual(h.command("queue.move_singer", {"singer_id": "b", "direction": "bottom"}), (True, "no_change"))
        self.assertEqual(h.command("queue.move_singer", {"singer_id": "a", "direction": "down"}), (True, "moved"))
        self.assertEqual([s["singer_id"] for s in h.app.queue], ["b", "a"])
        self.assertEqual(h.command("queue.move_song", {"entry_id": "local:uid3", "direction": "up"}), (True, "moved"))
        self.assertEqual([e["request_uid"] for e in h.app.queue[0]["songs"]], ["uid3", "uid2"])
        self.assertEqual(h.command("queue.move_song", {"entry_id": "local:uid3", "direction": "top"}), (True, "no_change"))
        self.assertEqual(h.command("queue.remove_song", {"entry_id": "local:uid2"}), (True, "removed"))
        self.assertEqual(h.command("queue.skip_singer", {"singer_id": "a", "skipped": True}), (True, "changed"))
        self.assertEqual(h.command("queue.skip_singer", {"singer_id": "a", "skipped": True}), (True, "no_change"))
        self.assertEqual(h.command("queue.remove_singer", {"singer_id": "b"}), (True, "removed"))
        self.assertEqual([s["singer_id"] for s in h.app.queue], ["a"])


class GuardTests(unittest.TestCase):
    def harness(self, mode="apply"):
        h = Harness([{"singer_id": "a", "name": "A", "songs": [entry(1), entry(2)]}, {"singer_id": "b", "name": "B", "songs": [entry(3)]}])
        h.app.settings["shell_commands"] = mode
        return h

    def run_cmd(self, h, action, args, **env):
        a = dict(args)
        a["_request_id"] = env.get("rid", f"id-{time.perf_counter_ns()}")
        if env.get("sent_at", "now") is not None:
            a["_sent_at"] = time.time() + env.get("offset", 0) if env.get("sent_at", "now") == "now" else env["sent_at"]
        return module().KaraokeApp._execute_shell_queue_command(h.app, action, a)

    def test_off_is_the_default_and_refuses_everything_without_touching_the_queue(self):
        self.assertEqual(module().DEFAULTS["shell_commands"], "off")
        for mode in ("off", "", "nonsense", None):
            h = self.harness(mode)
            before = copy.deepcopy(h.app.queue)
            self.assertEqual(self.run_cmd(h, "queue.remove_singer", {"singer_id": "a"}), (False, "shell_commands_off"))
            self.assertEqual(h.app.queue, before)
            self.assertEqual(h.calls, [])

    def test_dry_run_validates_and_logs_but_changes_nothing(self):
        h = self.harness("dry_run")
        before = copy.deepcopy(h.app.queue)
        ok, msg = self.run_cmd(h, "queue.remove_singer", {"singer_id": "a"})
        self.assertTrue(ok)
        self.assertTrue(msg.startswith("dry_run: would"))
        self.assertEqual((h.app.queue, h.calls, h.app._undo_stack), (before, [], []))
        self.assertEqual(self.run_cmd(h, "queue.remove_singer", {"singer_id": "nobody"}), (False, "unknown_singer"), "a dry run still rejects bad targets")

    def test_unknown_missing_and_malformed_requests(self):
        h = self.harness()
        self.assertEqual(self.run_cmd(h, "queue.explode", {}), (False, "unknown command: queue.explode"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, rid=""), (False, "missing_request_id"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, sent_at=None), (False, "missing_sent_at"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, sent_at="garbage"), (False, "missing_sent_at"))
        self.assertEqual(self.run_cmd(h, "queue.move_singer", {"singer_id": "a", "direction": "sideways"}), (False, "bad_direction"))
        self.assertEqual(self.run_cmd(h, "queue.move_song", {"entry_id": "local:uid1"}), (False, "bad_direction"))
        self.assertEqual(self.run_cmd(h, "queue.move_singer", {"direction": "up"}), (False, "unknown_singer"))
        self.assertEqual(self.run_cmd(h, "queue.move_song", {"entry_id": "local:none", "direction": "up"}), (False, "unknown_entry"))
        self.assertEqual(self.run_cmd(h, "queue.remove_song", {"entry_id": ""}), (False, "unknown_entry"))
        self.assertEqual(self.run_cmd(h, "queue.skip_singer", {"singer_id": "a", "skipped": "yes"}), (False, "bad_skipped"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}), (True, "noop"))

    def test_expired_and_future_commands(self):
        h = self.harness()
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, offset=-11), (False, "expired"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, offset=-9), (True, "noop"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, offset=61), (False, "clock_skew"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, offset=30), (True, "noop"))

    def test_a_repeated_request_id_is_answered_not_applied_twice(self):
        h = self.harness()
        first = self.run_cmd(h, "queue.move_singer", {"singer_id": "a", "direction": "down"}, rid="same")
        self.assertEqual(first, (True, "moved"))
        again = self.run_cmd(h, "queue.move_singer", {"singer_id": "a", "direction": "down"}, rid="same")
        self.assertEqual(again, (True, "duplicate: moved"))
        self.assertEqual([s["singer_id"] for s in h.app.queue], ["b", "a"], "moved once, not twice")
        for i in range(300):
            self.run_cmd(h, "queue.noop", {}, rid=f"n{i}")
        self.assertLessEqual(len(h.app._shell_cmd_seen), 256, "the memory of ids is bounded")

    def test_a_stale_view_is_rejected_and_a_fresh_one_applied(self):
        h = self.harness()
        sig = h.app._queue_signature()
        self.assertEqual(self.run_cmd(h, "queue.move_singer", {"singer_id": "a", "direction": "down", "expect_queue_sig": sig}), (True, "moved"))
        stale = self.run_cmd(h, "queue.move_singer", {"singer_id": "b", "direction": "down", "expect_queue_sig": sig})
        self.assertEqual(stale, (False, "stale_view"))
        self.assertEqual([s["singer_id"] for s in h.app.queue], ["b", "a"])
        self.assertEqual(self.run_cmd(h, "queue.noop", {"expect_queue_sig": h.app._queue_signature()}), (True, "noop"))

    def test_the_signature_reflects_everything_an_operator_sees(self):
        h = self.harness()
        base = h.app._queue_signature()
        self.assertEqual(h.app._queue_signature(), base, "stable")
        mutations = [
            lambda q: q.reverse(),
            lambda q: q[0].update(name="Renamed"),
            lambda q: q[0].update(skipped=True),
            lambda q: q[1].update(rotation_marker=True),
            lambda q: q[0]["songs"].reverse(),
            lambda q: q[0]["songs"][0].update(skipped=True),
            lambda q: q[0]["songs"].pop(),
            lambda q: q.pop(),
            lambda q: q.append({"singer_id": "c", "name": "C", "songs": []}),
        ]
        sigs = {base}
        for mutate in mutations:
            h2 = self.harness()
            mutate(h2.app.queue)
            sigs.add(h2.app._queue_signature())
        self.assertEqual(len(sigs), len(mutations) + 1, "every kind of visible change gives a different signature")
        h3 = self.harness()
        h3.app.queue[0]["songs"][0]["notes"] = "invisible to the operator"
        self.assertEqual(h3.app._queue_signature(), base, "fields the operator cannot see do not count as a change")

    def test_removing_the_playing_singer_or_song_detaches_playback_exactly_like_the_button(self):
        h = Harness([{"singer_id": "a", "name": "A", "songs": [entry(1)]}], playing=True, current_singer="a", current_request="local:uid1")
        self.assertEqual(h.command("queue.remove_song", {"entry_id": "local:uid1"}), (True, "removed"))
        self.assertTrue(h.app._current_playback_detached_from_rotation)
        h = Harness([{"singer_id": "a", "name": "A", "songs": [entry(1)]}], playing=True, current_singer="a")
        self.assertEqual(h.command("queue.remove_singer", {"singer_id": "a"}), (True, "removed"))
        self.assertTrue(h.app._current_playback_detached_from_rotation)
        h = Harness([{"singer_id": "a", "name": "A", "songs": [entry(1)]}], playing=False, current_singer="a")
        h.command("queue.remove_singer", {"singer_id": "a"})
        self.assertFalse(h.app._current_playback_detached_from_rotation, "only while that singer is actually playing")

    def test_a_failing_edit_reports_failure_and_is_remembered(self):
        h = self.harness()
        h.app.update_queue_display = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        ok, msg = self.run_cmd(h, "queue.move_singer", {"singer_id": "a", "direction": "down"}, rid="x1")
        self.assertFalse(ok)
        self.assertTrue(msg.startswith("failed: boom"))
        self.assertEqual(self.run_cmd(h, "queue.noop", {}, rid="x1")[1].startswith("duplicate: failed"), True)

    def test_the_executor_routes_queue_actions_and_gates_shell_transport(self):
        m = module()
        h = self.harness("apply")
        played = []
        h.app.toggle_karaoke_pause = lambda: played.append("pause")
        h.app.play_next_file = lambda skip_confirmation=False: played.append("next")
        h.app.karaoke_playing = False
        execute = lambda action, args: m.KaraokeApp._execute_host_control_command(h.app, action, args)  # noqa: E731
        sent = {"_request_id": "t1", "_sent_at": time.time()}
        self.assertEqual(execute("queue.noop", dict(sent)), (True, "noop"))
        # a command from the shell obeys the switch; the web remote (no source) always runs
        h.app.settings["shell_commands"] = "off"
        self.assertEqual(execute("play_pause", {"source": "shell"}), (False, "shell_commands_off"))
        self.assertEqual(played, [])
        self.assertEqual(execute("play_pause", {}), (True, "play_pause"))
        self.assertEqual(played, ["next"], "the web remote path is unchanged")
        h.app.settings["shell_commands"] = "dry_run"
        self.assertEqual(execute("skip", {"source": "shell"})[0], True)
        self.assertEqual(played, ["next"], "dry_run did not execute the shell's transport command")
        h.app.settings["shell_commands"] = "apply"
        self.assertEqual(execute("skip", {"source": "shell"}), (True, "next"))
        self.assertEqual(played, ["next", "next"])
        self.assertEqual(execute("state", {"source": "shell"}), (True, "state"))

    def test_the_queue_summary_carries_ids_titles_and_flags(self):
        h = self.harness()
        h.app._queue_entry_artist_title = lambda e: (e.get("artist", ""), e.get("title", ""))
        summary = h.app._shell_queue_summary()
        self.assertEqual([s["singer_id"] for s in summary], ["a", "b"])
        self.assertEqual(summary[0]["songs"][0], {"entry_id": "local:uid1", "artist": "Artist1", "title": "Title1", "skipped": False})
        self.assertEqual(set(summary[0]), {"singer_id", "name", "skipped", "marker", "songs"})
        for i in range(250):
            h.app.queue.append({"singer_id": f"x{i}", "name": "x", "songs": []})
        self.assertEqual(len(h.app._shell_queue_summary()), 200, "bounded")

    def test_the_relay_and_poll_paths_hand_over_the_envelope(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('args["_request_id"] = str(request_id or "")', src)
        self.assertIn('args["_sent_at"] = data.get("sent_at")', src)
        self.assertIn('args["_request_id"] = f"http:{command_id}"', src)
        self.assertIn('"queue_sig": queue_sig', src)

    def test_a_launch_line_says_the_switch_is_on_and_is_silent_when_off(self):
        import contextlib
        import io
        import logging

        m = module()
        for mode, expect in (("dry_run", True), ("apply", True), ("off", False), ("garbage", False)):
            h = self.harness(mode)
            records = []

            class Grab(logging.Handler):
                def emit(self, record):
                    records.append(record.getMessage())

            handler = Grab()
            logging.getLogger().addHandler(handler)
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    m.KaraokeApp._log_shell_commands_ready(h.app)
            finally:
                logging.getLogger().removeHandler(handler)
            seen = [t for t in records + out.getvalue().splitlines() if "[SHELL-CMD] ready" in t]
            self.assertEqual(bool(seen), expect, (mode, seen))
            if expect:
                self.assertIn(f"mode={mode}", seen[0])
                self.assertIn(f"queue_sig={h.app._queue_signature()}", seen[0])
                self.assertIn("singers=2", seen[0])
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index('self._merge_duplicate_rotation_singers(reason="load_data")')
        self.assertIn("self._log_shell_commands_ready()", src[i:i + 300], "the line is written right after the queue is loaded and merged")

    def test_the_old_button_handlers_are_untouched_by_this_step(self):
        # The design keeps the handlers as they were until the command path has run in a dry-run show.
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("    def move_up(self):\n        index = self.get_selected_index()", src)
        self.assertIn("    def remove_selected(self):\n        shift_held = bool(QApplication.keyboardModifiers()", src)


if __name__ == "__main__":
    unittest.main()
