"""Stage B state-replay harness (tools/queue_event_replay.py): determinism, the AGENTS.md adversarial cases, equivalent reorderings,
the log extractor, and a mutation-style check that the comparer flags deliberately broken queue behaviour."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import contextlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import queue_event_replay as qr  # noqa: E402

qr.load_app_module()
APP = qr._MODULE.KaraokeApp


class FaultyImpl(qr.PythonImpl):
    """Python with one deliberate defect patched into the real app class while each event is applied."""
    name = "faulty"

    def __init__(self, patches):
        super().__init__()
        self._patches = patches

    def apply(self, event):
        with contextlib.ExitStack() as stack:
            for target, replacement in self._patches:
                stack.enter_context(mock.patch.object(APP, target, replacement))
            return super().apply(event)


def final(stream):
    return qr.run_stream(qr.PythonImpl(), stream)[-1]["snapshot"]


class DeterminismTests(unittest.TestCase):
    def test_adversarial_and_random_streams_replay_identically(self):
        cases = list(qr.adversarial_cases().items()) + [(f"seed{i}", qr.generate_stream(i, 30)) for i in range(6)]
        for name, stream in cases:
            r = qr.compare(stream, qr.PythonImpl(), qr.PythonImpl())
            self.assertTrue(r["equal"], f"{name}: {r['first_divergence']}")

    def test_generator_is_seeded(self):
        self.assertEqual(qr.generate_stream(3, 25), qr.generate_stream(3, 25))
        self.assertNotEqual(qr.generate_stream(3, 25), qr.generate_stream(4, 25))

    def test_generated_ids_differ_between_worlds_but_snapshots_do_not(self):
        """Known nondeterminism: the app mints uuid4 singer/entry ids. The harness aliases them, so snapshots still agree."""
        stream = qr.adversarial_cases()["host_local_add_gets_stable_alias"]
        a, b = qr.World(), qr.World()
        for ev in stream:
            a.apply(ev)
            b.apply(ev)
        raw_a = {s["singer_id"] for s in a.app.queue}
        raw_b = {s["singer_id"] for s in b.app.queue}
        self.assertTrue(raw_a and raw_b and raw_a.isdisjoint(raw_b))
        self.assertEqual(qr.canonical(a.snapshot()), qr.canonical(b.snapshot()))

    def test_fingerprint_is_independent_of_hash_seed(self):
        out = []
        for seed in ("1", "2"):
            env = dict(os.environ, PYTHONHASHSEED=seed, SINGWS_HOME=tempfile.mkdtemp())
            proc = subprocess.run([sys.executable, str(Path(qr.__file__)), "corpus", "--fingerprint", "--seeds", "2", "--length", "20"],
                                  capture_output=True, text=True, env=env, timeout=300)
            line = [l for l in (proc.stdout + proc.stderr).splitlines() if "FINGERPRINT" in l]
            self.assertTrue(line, proc.stderr[-500:])
            out.append(line[-1][line[-1].index("FINGERPRINT"):])
        self.assertEqual(out[0], out[1])


class AdversarialRuleTests(unittest.TestCase):
    cases = qr.adversarial_cases()

    def test_host_deleted_request_is_not_recreated_by_relist(self):
        snap = final(self.cases["host_deletes_then_server_relists"])
        self.assertEqual(snap["lifecycle"]["1001"], {"state": "removed", "copies_in_queue": 0})
        self.assertEqual([s["id"] for q in snap["queue"] for s in q["songs"]], ["R1002", "R1003"])

    def test_restart_does_not_recreate_removed_or_completed_song(self):
        for name, rid in (("restart_does_not_recreate_removed_song", "1001"), ("completed_is_not_resurrected", "1001"), ("waitlist_removed_stays_removed_across_restart", "1001")):
            snap = final(self.cases[name])
            self.assertNotEqual(snap["lifecycle"][rid]["state"], "in_queue", name)
            self.assertIn(rid, snap["tombstones"], name)

    def test_duplicate_arrival_keeps_one_copy_with_the_oldest_id(self):
        snap = final(self.cases["duplicate_arrivals_keep_oldest"])
        self.assertEqual(snap["lifecycle"]["1001"]["copies_in_queue"], 1)
        self.assertEqual(snap["queue"][0]["songs"][0]["id"], "R1001")

    def test_ids_survive_restart_and_reordered_relist(self):
        steps = qr.run_stream(qr.PythonImpl(), self.cases["restart_keeps_ids_and_order"])
        before, after = steps[2]["snapshot"]["queue"], steps[-1]["snapshot"]["queue"]
        shape = lambda q: [(s["id"], sorted(x["id"] for x in s["songs"])) for s in q]  # same singers (same S aliases), same entries, same ids
        self.assertEqual(shape(before), shape(after))

    def test_relist_with_rows_in_a_different_order_reorders_songs_inside_a_singer(self):
        """Finding, pinned (not a verdict): after a restart the server's row order inside one singer wins over the order the host
        had (here the host never reordered that singer's songs, so the harness cannot say whether production would push an order first)."""
        steps = qr.run_stream(qr.PythonImpl(), self.cases["restart_keeps_ids_and_order"])
        order = lambda snap: [[x["id"] for x in s["songs"]] for s in snap["queue"] if s["id"] == "S1"][0]
        self.assertEqual(order(steps[2]["snapshot"]), ["R1001", "R1002"])
        self.assertEqual(order(steps[-1]["snapshot"]), ["R1002", "R1001"])

    def test_remove_singer_tombstones_every_request(self):
        snap = final(self.cases["remove_singer_removes_all_their_requests"])
        self.assertEqual({k: v["state"] for k, v in snap["lifecycle"].items()}, {"1001": "removed", "1002": "removed", "1003": "in_queue"})

    def test_waitlist_add_remove_restore_follow_the_lifecycle(self):
        steps = qr.run_stream(qr.PythonImpl(), self.cases["waitlist_add_remove_restore"])
        states = [s["snapshot"]["lifecycle"].get("1003", {}).get("state") for s in steps]
        self.assertEqual(states[1], "waitlisted")
        self.assertIn("removed", states)
        self.assertEqual(states[-2], "waitlisted")   # restored
        self.assertEqual(states[-1], "in_queue")     # then added

    def test_limit_blocked_request_is_in_no_state_at_all(self):
        """Finding, pinned: a request refused at the singer cap is neither queued nor waitlisted nor tombstoned on the host."""
        snap = final(self.cases["limit_blocked_never_waitlisted"])
        self.assertNotIn("1002", snap["lifecycle"])

    def test_duplicate_song_with_a_new_request_id_is_a_new_request(self):
        """Finding, pinned: identity is the request id (AGENTS.md), so the same song re-requested under id 1005 is a second entry."""
        snap = final(self.cases["duplicate_arrivals_keep_oldest"])
        self.assertEqual([s["id"] for s in snap["queue"][0]["songs"]], ["R1001", "R1005"])


class ReorderingTests(unittest.TestCase):
    def base(self):
        reqs = [qr._req(2000 + i, f"Singer{i % 4}", f"Song{i}") for i in range(10)]
        return [{"op": "config", "queue_mode": "rotation", "limit_pending_max": 20}, {"op": "remote_arrive", "requests": reqs}]

    def test_commuting_removals_and_skips_in_any_order_give_the_same_final_state(self):
        tail = [{"op": "remove_song", "entry": f"R{2000 + i}"} for i in (1, 3, 6, 9)] + [{"op": "skip_singer", "singer": f"S{i}", "skipped": True} for i in (1, 3)]
        stream = self.base() + tail
        want = qr.canonical(final(stream))
        for seed in range(12):
            other = self.base() + qr.equivalent_reordering(tail, random.Random(seed))
            self.assertEqual(qr.canonical(final(other)), want, seed)

    def test_arrival_batch_order_is_NOT_commutative(self):
        """Finding, pinned: rows of one poll are inserted in the order the server lists them, so batch order decides queue order
        (even when rows carry sort_order). That is why equivalent_reordering never shuffles arrivals."""
        reqs = [qr._req(3000 + i, f"S{i % 3}", f"T{i}") for i in range(6)]
        names = lambda rs: [x["name"] for x in final([{"op": "config"}, {"op": "remote_arrive", "requests": rs}])["queue"]]
        self.assertEqual(names(reqs), ["S0", "S1", "S2"])
        self.assertEqual(names(list(reversed(reqs))), ["S2", "S1", "S0"])

    def test_reordering_changes_the_stream_but_keeps_prefix(self):
        tail = [{"op": "remove_song", "entry": f"R{2000 + i}"} for i in range(6)]
        shuffled = qr.equivalent_reordering(tail, random.Random(1))
        self.assertNotEqual(shuffled, tail)
        self.assertEqual(sorted(e["entry"] for e in shuffled), sorted(e["entry"] for e in tail))

    def test_non_independent_events_are_not_reordered(self):
        s = [{"op": "remove_song", "entry": "R1"}, {"op": "remove_song", "entry": "R1"}, {"op": "move_singer", "singer": "S1", "where": "top"}]
        self.assertEqual(qr.equivalent_reordering(s, random.Random(0)), s)


class MutationTests(unittest.TestCase):
    """The comparer must flag each deliberately broken behaviour (python vs python-with-a-defect)."""
    cases = qr.adversarial_cases()

    def flagged(self, case, patches):
        r = qr.compare(self.cases[case], qr.PythonImpl(), FaultyImpl(patches))
        self.assertFalse(r["equal"], f"mutation went unnoticed on {case}")
        self.assertIsNotNone(r["first_divergence"]["diff"])
        return r

    def test_dropped_tombstone_is_flagged(self):
        r = self.flagged("host_deletes_then_server_relists", [("_record_remote_request_tombstone", lambda self, *a, **k: None)])
        self.assertTrue(any("tombstones" in d or "lifecycle" in d for d in r["first_divergence"]["diff"]), r)

    def test_recreated_removed_song_is_flagged(self):
        # the app guards a removed id twice (tombstone file + removed-id memory); declaring "different song" defeats the first and discards the second
        r = self.flagged("host_deletes_then_server_relists", [("_tombstone_signature_conflicts", lambda self, *a, **k: True)])
        self.assertEqual(r["first_divergence"]["event"]["op"], "remote_arrive")
        self.assertTrue(any("R1001" in d or "1001" in d for d in r["first_divergence"]["diff"]), r)

    def test_recreated_after_restart_is_flagged(self):
        self.flagged("restart_does_not_recreate_removed_song", [("_tombstone_signature_conflicts", lambda self, *a, **k: True)])

    def test_either_single_guard_alone_does_not_recreate_the_song(self):
        """Documents the layered defence: with only the tombstone matcher disabled, the removed-id memory still holds the line
        (the comparer reports the missing tombstone-file match only through the snapshot, never as a queue change)."""
        r = qr.compare(self.cases["host_deletes_then_server_relists"], qr.PythonImpl(),
                       FaultyImpl([("_remote_request_matches_tombstone", lambda self, req: None)]))
        self.assertTrue(r["equal"] or "queue" not in " ".join(r["first_divergence"]["diff"]), r)

    def test_changed_request_id_is_flagged(self):
        orig = APP._add_song_to_queue

        def bump(self, singer, song, track=None, remote_meta=None, quiet=False):
            if isinstance(remote_meta, dict) and remote_meta.get("request_id"):
                remote_meta = dict(remote_meta, request_id=int(remote_meta["request_id"]) + 1)
            return orig(self, singer, song, track=track, remote_meta=remote_meta, quiet=quiet)

        self.flagged("restart_keeps_ids_and_order", [("_add_song_to_queue", bump)])

    def test_wrong_insertion_position_is_flagged(self):
        orig = APP._add_song_to_queue

        def front(self, singer, song, track=None, remote_meta=None, quiet=False):
            ok = orig(self, singer, song, track=track, remote_meta=remote_meta, quiet=quiet)
            if len(self.queue) > 1:
                self.queue.insert(0, self.queue.pop())  # a new singer jumps the rotation
            return ok

        self.flagged("remove_singer_removes_all_their_requests", [("_add_song_to_queue", front)])

    def test_a_double_copy_of_one_request_is_self_healed_by_the_app(self):
        """Finding, pinned: inserting a second copy of the same request id does not survive; the app's own dedup (QUEUE-AUDIT
        dedup_same_request_id) keeps the oldest, so the comparer correctly sees no difference."""
        orig = APP._add_song_to_queue

        def twice(self, singer, song, track=None, remote_meta=None, quiet=False):
            first = orig(self, singer, song, track=track, remote_meta=remote_meta, quiet=quiet)
            self.queue[-1]["songs"].append(dict(self.queue[-1]["songs"][-1]))
            return first

        r = qr.compare(self.cases["duplicate_arrivals_keep_oldest"], qr.PythonImpl(), FaultyImpl([("_add_song_to_queue", twice)]))
        self.assertTrue(r["equal"], r)

    def test_skip_that_does_nothing_is_flagged(self):
        self.flagged("skip_and_reorder_keep_ids", [("shell_queue_set_singer_skipped", lambda self, sid, skipped: "no_change")])

    def test_reorder_that_does_nothing_is_flagged(self):
        self.flagged("skip_and_reorder_keep_ids", [("shell_queue_move_singer", lambda self, sid, where: "no_change")])

    def test_removal_that_forgets_the_tombstone_but_removes_the_song_is_flagged(self):
        self.flagged("remove_singer_removes_all_their_requests", [("_delete_remote_request", lambda self, *a, **k: None)])

    def test_unmutated_implementation_is_not_flagged(self):
        for name, stream in self.cases.items():
            self.assertTrue(qr.compare(stream, qr.PythonImpl(), FaultyImpl([]))["equal"], name)


class ExtractorTests(unittest.TestCase):
    LOG = (
        "[10:00:00] [INFO] [REQUEST-DIAG] request_id=1790000000001 singer='Ada' artist='A' title='One' source='' session_id='' selected_version='' received_ts=1 status=received match_result='pending' queue_insert_result='pending' sync_revision=0 server_enabled=1 failure_reason=''\n"
        "[10:00:01] [INFO] [REQUEST-DIAG] request_id=1790000000002 singer='Bob' artist='A' title='Two' source='' session_id='' selected_version='' received_ts=1 status=received match_result='pending' queue_insert_result='pending' sync_revision=0 server_enabled=1 failure_reason=''\n"
        "[10:00:02] [INFO] [REQUEST-DIAG] request_id=1790000000002 singer='Bob' artist='A' title='Two' source='' session_id='' selected_version='' received_ts=1 status=received match_result='pending' queue_insert_result='pending' sync_revision=0 server_enabled=1 failure_reason=''\n"
        "[10:05:00] [INFO] [REMOTE-TOMBSTONE] created request_id=1790000000001 singer='Ada' title='One' artist='A' reason=host_remove_song revision=3 status=removed accepting_requests=1\n"
        "[10:06:00] [INFO] [QUEUE-STATE] same_item=True current=('', '')\n"
    )

    def test_extracts_arrivals_and_removals_and_reports_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "singws_2026-11-01.log"
            p.write_text(self.LOG)
            before = p.read_bytes()
            stream, cov = qr.extract_stream([str(p)])
            self.assertEqual(p.read_bytes(), before)  # read-only
        ops = [e["op"] for e in stream]
        self.assertEqual(ops, ["config", "remote_arrive", "remote_arrive", "remove_song"])
        self.assertEqual(cov["arrive"], 2)
        self.assertEqual(cov["arrive_repeat_skipped"], 1)
        self.assertEqual(cov["replay"]["RESURRECTED_after_replay"], [])
        self.assertEqual(cov["replay"]["replay_final_removed_or_completed"], 1)
        self.assertIn("rotation_advance", " ".join(cov["cannot_reconstruct"]))
        self.assertEqual(qr.compare(stream, qr.PythonImpl(), qr.PythonImpl())["equal"], True)

    def test_unit_test_ids_in_a_live_log_are_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "singws_2026-11-01.log"
            p.write_text(self.LOG.replace("1790000000001", "101"))
            stream, _cov = qr.extract_stream([str(p)])
        self.assertEqual([e["op"] for e in stream].count("remote_arrive"), 1)


class SafetyTests(unittest.TestCase):
    def test_harness_never_uses_the_live_profile(self):
        home = Path(os.environ["SINGWS_HOME"]).resolve()
        self.assertNotEqual(home, (Path.home() / "SingWS").resolve())
        w = qr.World()
        self.assertNotIn(str(Path.home() / "SingWS"), str(w.dir))

    def test_stream_is_json_serialisable(self):
        for stream in list(qr.adversarial_cases().values()) + [qr.generate_stream(1, 20)]:
            self.assertEqual(json.loads(json.dumps(stream)), stream)


if __name__ == "__main__":
    unittest.main()
