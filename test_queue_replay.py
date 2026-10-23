import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import queue_replay as qr

A, B, C = 1790974569756, 1790974569900, 1790976695542


def _line(tag, body, t="10:00:00"):
    return f"[{t}] [INFO] [{tag}] {body}\n"


def _write(tmp, name, lines):
    path = Path(tmp) / name
    path.write_text("".join(lines), encoding="utf-8")
    return path


class ParseTests(unittest.TestCase):
    def test_tombstone_created_parses_quoted_values(self):
        e = qr.parse_line(_line("REMOTE-TOMBSTONE", f"created request_id={A} singer='Ada B' title=\"Don't\" artist='X' "
                                                    "reason=host_remove_song revision=1 accepting_requests=0"),
                          date="2026-10-22", seq=4)
        self.assertEqual((e["kind"], e["id"], e["reason"]), ("tombstone_created", A, "host_remove_song"))
        self.assertEqual(e["t"], "2026-10-22T10:00:00")

    def test_guard_and_reuse_lines(self):
        ignored = qr.parse_line(_line("REMOTE-TOMBSTONE", f"poll old request ignored request_id={A} singer='S' title='T' reason=tombstone; re-push removed status"))
        self.assertEqual(ignored["kind"], "tombstone_poll_ignored")
        stale = qr.parse_line(_line("REMOTE-TOMBSTONE", f"stale non-terminal row for terminal id ignored request_id={A} singer='S' title='T'"))
        self.assertEqual(stale["kind"], "tombstone_poll_ignored")
        reuse = qr.parse_line(_line("REMOTE-TOMBSTONE", f"reused request id accepted request_id={A} reason=signature_mismatch"))
        self.assertEqual(reuse["kind"], "tombstone_reuse")

    def test_lifecycle_dedup_insert_and_host_sync(self):
        self.assertEqual(qr.parse_line(_line("REQUEST-LIFECYCLE", f"server confirmed terminal state request_id={A} state=completed attempt=1 status=200"))["state"], "completed")
        d = qr.parse_line(_line("QUEUE-DEDUP", f"suppressed duplicate insert singer='S' title='T' request_id={B} kept_request_id={A} source=server"))
        self.assertEqual((d["id"], d["kept_id"], d["source"]), (B, A, "server"))
        i = qr.parse_line(_line("QUEUE-INSERT", f"singer='S' request_id={A} source=server reason=append_existing_singer queue_revision=7"))
        self.assertEqual((i["source"], i["queue_revision"]), ("server", 7))
        h = qr.parse_line(_line("HOST-REQUEST-SYNC", f"request_key=host:x:local:y request_id={A} singer_session_id=2517 source=host_manual result=synced"))
        self.assertEqual((h["kind"], h["key"]), ("host_sync", "host:x:local:y"))
        self.assertIsNone(qr.parse_line(_line("HOST-REQUEST-SYNC", "queued singer_id=1 request_key=k source=host_manual status=pending")))

    def test_unrelated_and_malformed_lines_are_ignored(self):
        self.assertIsNone(qr.parse_line("not a log line\n"))
        self.assertIsNone(qr.parse_line(_line("PERF-DIAG", "took 3ms")))


class InvariantTests(unittest.TestCase):
    def events(self, *lines):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "singws_2026-10-22.log", lines)
            return qr.load_events([path])

    def tomb(self, rid=A, reason="host_remove_song"):
        return _line("REMOTE-TOMBSTONE", f"created request_id={rid} singer='S' title='T' artist='A' reason={reason} revision=1 accepting_requests=0")

    def insert(self, rid=A, source="server"):
        return _line("QUEUE-INSERT", f"singer='S' request_id={rid} source={source} reason=append_existing_singer queue_revision=2")

    def test_host_deleted_request_resurrected_by_server_is_a_violation(self):
        v = qr.check_invariants(self.events(self.tomb(), self.insert()))
        self.assertEqual([x["invariant"] for x in v], ["RESURRECTION"])

    def test_guarded_poll_and_host_reinsert_are_not_violations(self):
        ev = self.events(
            self.tomb(),
            _line("REMOTE-TOMBSTONE", f"poll old request ignored request_id={A} singer='S' title='T' reason=tombstone; re-push removed status"),
            self.insert(source="host"),
        )
        self.assertEqual(qr.check_invariants(ev), [])

    def test_legitimate_id_reuse_clears_the_tombstone(self):
        ev = self.events(self.tomb(), _line("REMOTE-TOMBSTONE", f"reused request id accepted request_id={A} reason=signature_mismatch"), self.insert())
        self.assertEqual(qr.check_invariants(ev), [])

    def test_completed_tombstone_also_blocks_resurrection(self):
        v = qr.check_invariants(self.events(self.tomb(reason="song_completed"), self.insert()))
        self.assertEqual(len(v), 1)

    def test_oldest_duplicate_wins(self):
        ok = self.events(_line("QUEUE-DEDUP", f"suppressed duplicate insert singer='S' title='T' request_id={B} kept_request_id={A} source=server"))
        self.assertEqual(qr.check_invariants(ok), [])
        bad = self.events(_line("QUEUE-DEDUP", f"suppressed duplicate insert singer='S' title='T' request_id={A} kept_request_id={B} source=server"))
        self.assertEqual([x["invariant"] for x in qr.check_invariants(bad)], ["DEDUP_OLDEST"])
        same = self.events(_line("QUEUE-DEDUP", f"removed accidental duplicate singer='S' title='T' request_id={A} kept_request_id={A} reason=reconnect"))
        self.assertEqual(qr.check_invariants(same), [])

    def test_a_request_key_keeps_one_id(self):
        sync = lambda rid: _line("HOST-REQUEST-SYNC", f"request_key=host:k request_id={rid} singer_session_id=1 source=host_manual result=synced")
        self.assertEqual(qr.check_invariants(self.events(sync(A), sync(A))), [])
        v = qr.check_invariants(self.events(sync(A), sync(C)))
        self.assertEqual([x["invariant"] for x in v], ["ID_STABLE"])


class CorpusTests(unittest.TestCase):
    def test_unit_test_ids_are_dropped_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "singws_2026-07-10.log", [
                _line("REMOTE-TOMBSTONE", "created request_id=202 singer='Ada' title='T' artist='A' reason=host_remove_song revision=1 accepting_requests=0"),
                _line("QUEUE-INSERT", "singer='Ada' request_id=202 source=server reason=append_existing_singer queue_revision=1"),
                _line("QUEUE-DEDUP", f"removed accidental duplicate singer='S' title='T' request_id={B} kept_request_id={A} reason=reconnect"),
            ])
            events, dropped = qr.filter_real(qr.load_events([path]))
            self.assertEqual(dropped, 2)
            self.assertEqual([e["kind"] for e in events], ["dedup"])
            self.assertEqual(qr.check_invariants(events), [])

    def test_cli_writes_corpus_and_sets_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "singws_2026-10-22.log", [
                _line("REMOTE-TOMBSTONE", f"created request_id={A} singer='S' title='T' artist='A' reason=host_remove_song revision=1 accepting_requests=0"),
                _line("QUEUE-INSERT", f"singer='S' request_id={A} source=server reason=append_existing_singer queue_revision=2"),
            ])
            out = Path(tmp) / "corpus.jsonl"
            with contextlib.redirect_stdout(io.StringIO()):
                status = qr.main([str(path), "--corpus", str(out)])
            self.assertEqual(status, 1)
            rows = [json.loads(l) for l in out.read_text().splitlines()]
            self.assertEqual([r["kind"] for r in rows], ["tombstone_created", "queue_insert"])
            self.assertEqual(rows[0]["file"], "singws_2026-10-22.log")

    def test_clean_logs_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "singws_2026-10-22.log", [
                _line("REQUEST-LIFECYCLE", f"completion queued request_id={A} reason=song_completed durable=1"),
            ])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                status = qr.main([str(path)])
            self.assertEqual(status, 0)
            self.assertIn("NOT EXERCISED", buf.getvalue())  # one completion line proves nothing about the invariants


def _events(*lines, name="singws_2026-10-22.log"):
    with tempfile.TemporaryDirectory() as tmp:
        return qr.load_events([_write(tmp, name, lines)])


def _auth(decision, incoming, before, after, singer="S"):
    return _line("QUEUE_SYNC_AUTHORITY", f"partial remote reorder singer='{singer}' ranked={len(incoming)} pinned_local=1 "
                                         f"incoming_order={incoming} before={before} after={after} decision={decision}")


class HostActionParseTests(unittest.TestCase):
    def test_authority_parses_none_slots_and_ids(self):
        e = qr.parse_line(_auth("applied", [A, B], [None, B, A], [None, A, B]))
        self.assertEqual((e["kind"], e["decision"]), ("authority", "applied"))
        self.assertEqual((e["incoming"], e["before"], e["after"]), ([A, B], [None, B, A], [None, A, B]))

    def test_slot_actions(self):
        for text, action in (("preserved empty singer slot singer='S' reason=server_completed timeout_sec=180", "preserved"),
                             ("removed empty singer row singer='S' reason=host_remove_singer", "removed"),
                             ("filled preserved singer slot singer='S' reason=empty_slot_retained", "filled")):
            self.assertEqual(qr.parse_line(_line("ROTATION-SLOT", text))["action"], action)
        self.assertIsNone(qr.parse_line(_line("ROTATION-SLOT", "something else singer='S'")))

    def test_server_state_audit_and_queue_state(self):
        m = qr.parse_line(_line("SERVER-STATE", "accepting watchdog mismatch reason=remote_reconcile server=0 local=1 action=repair_server_to_local"))
        self.assertEqual((m["kind"], m["server"], m["local"], m["action"]), ("accepting_mismatch", 0, 1, "repair_server_to_local"))
        self.assertIsNone(qr.parse_line(_line("SERVER-STATE", "connection connected=1 state=ok message=x accepting_requests=1")))
        d = qr.parse_line(_line("QUEUE-AUDIT", f"action=dedup_same_request_id singer='S' title='T' identity=('S', 1) request_id={B} kept_request_id={A} reason=reconnect"))
        self.assertEqual((d["kind"], d["id"], d["kept_id"], d["source"]), ("dedup", B, A, "audit"))
        r = qr.parse_line(_line("QUEUE-AUDIT", "action=play_start_rollback reason=rapid_skip singers=3"))
        self.assertEqual((r["kind"], r["reason"], r["singers"]), ("play_rollback", "rapid_skip", 3))
        q = qr.parse_line(_line("QUEUE-STATE", "last='' current=('', '') next_singer='' next_item=('', '') same_item=False"))
        self.assertEqual((q["has_current"], q["same_item"]), (False, False))
        q = qr.parse_line(_line("QUEUE-STATE", "last='x' current=('leon', '/a.cdg') next_singer='' next_item=('', '') same_item=False"))
        self.assertTrue(q["has_current"])


class HostActionInvariantTests(unittest.TestCase):
    def names(self, events):
        return [v["invariant"] for v in qr.check_invariants(events)]

    def test_valid_reorders_pass(self):
        ev = _events(_auth("applied", [A, B], [None, B, A], [None, A, B]),
                     _auth("newer_singer_order", [A, B], [B, A], [A, B]),
                     _auth("host_echo", [A], [None, A], [None, A]),
                     _auth("pending_host_order", [A, B], [A, B, None], [A, B, None]))
        self.assertEqual(self.names(ev), [])

    def test_reorder_that_adds_or_drops_an_id_is_a_violation(self):
        self.assertEqual(self.names(_events(_auth("applied", [A, B], [A], [A, B]))), ["AUTH_PERMUTATION"])
        self.assertEqual(self.names(_events(_auth("applied", [A], [A, B], [A]))), ["AUTH_PERMUTATION"])

    def test_applied_reorder_must_follow_the_host_order(self):
        self.assertEqual(self.names(_events(_auth("applied", [A, B], [B, A], [B, A]))), ["AUTH_HOST_ORDER"])

    def test_echo_must_not_change_the_order(self):
        self.assertEqual(self.names(_events(_auth("host_echo", [A, B], [B, A], [A, B]))), ["AUTH_ECHO_NOOP"])

    def test_slot_lifecycle(self):
        pres = _line("ROTATION-SLOT", "preserved empty singer slot singer='S' reason=server_completed timeout_sec=180")
        fill = _line("ROTATION-SLOT", "filled preserved singer slot singer='S' reason=empty_slot_retained")
        rem = _line("ROTATION-SLOT", "removed empty singer row singer='S' reason=host_remove_singer")
        self.assertEqual(self.names(_events(pres, fill, fill)), [])  # repeated fills are normal
        v = qr.check_invariants(_events(pres, rem, fill))
        self.assertEqual([(x["invariant"], x["origin"]) for x in v], [("SLOT_FILL", "ambiguous")])
        v = qr.check_invariants(_events(fill))  # no history: the preserving log is missing
        self.assertEqual([(x["invariant"], x["origin"]) for x in v], [("SLOT_FILL", "unverifiable")])

    def test_slot_state_survives_a_restart_into_the_next_log_file(self):
        pres = _line("ROTATION-SLOT", "preserved empty singer slot singer='S' reason=server_completed timeout_sec=180", t="23:58:00")
        fill = _line("ROTATION-SLOT", "filled preserved singer slot singer='S' reason=empty_slot_retained", t="00:07:52")
        with tempfile.TemporaryDirectory() as tmp:
            events = qr.load_events([_write(tmp, "singws_2026-07-14.log", [pres]), _write(tmp, "singws_2026-07-15.log", [fill])])
        self.assertEqual(qr.check_invariants(events), [])

    def test_accepting_mismatch_must_repair_server_to_local(self):
        ok = _line("SERVER-STATE", "accepting watchdog mismatch reason=remote_reconcile server=0 local=1 action=repair_server_to_local")
        bad = _line("SERVER-STATE", "accepting watchdog mismatch reason=remote_reconcile server=0 local=1 action=repair_local_to_server")
        self.assertEqual(self.names(_events(ok)), [])
        self.assertEqual(self.names(_events(bad)), ["ACCEPTING_HOST"])

    def test_audit_dedup_feeds_the_oldest_wins_check(self):
        bad = _line("QUEUE-AUDIT", f"action=dedup_same_request_id singer='S' title='T' identity=('S', 1) request_id={A} kept_request_id={B} reason=reconnect")
        self.assertEqual(self.names(_events(bad)), ["DEDUP_OLDEST"])


class OriginTests(unittest.TestCase):
    def test_test_ids_filter_authority_events_and_origins_come_from_ids(self):
        lines = [_auth("applied", [1, 2], [2, 1], [2, 1], singer="Ada"),  # test ids, broken on purpose
                 _line("REMOTE-TOMBSTONE", f"created request_id={A} singer='Real' title='T' artist='A' reason=host_remove_song revision=1 accepting_requests=0"),
                 _line("REMOTE-TOMBSTONE", "created request_id=7 singer='Ada' title='T' artist='A' reason=host_remove_song revision=1 accepting_requests=0")]
        events = _events(*lines)
        origins = qr.singer_origins(events)
        self.assertEqual((origins["Real"], origins["Ada"]), ("real", "test"))
        kept, dropped = qr.filter_real(events)
        self.assertEqual(dropped, 2)
        self.assertEqual([e["kind"] for e in kept], ["tombstone_created"])
        # with test ids kept, the broken reorder is reported but tagged test and so does not fail the run
        v = qr.check_invariants(events, origins)
        self.assertTrue(v and all(x["origin"] == "test" for x in v))

    def test_name_only_events_take_the_origin_of_the_singer(self):
        origins = {"Real": "real", "Ada": "test", "Both": "ambiguous"}
        pres = lambda n: _line("ROTATION-SLOT", f"preserved empty singer slot singer='{n}' reason=server_completed timeout_sec=180")
        fill = lambda n: _line("ROTATION-SLOT", f"filled preserved singer slot singer='{n}' reason=empty_slot_retained")
        rem = lambda n: _line("ROTATION-SLOT", f"removed empty singer row singer='{n}' reason=host_remove_singer")
        got = {}
        for n in origins:
            v = qr.check_invariants(_events(pres(n), rem(n), fill(n)), origins)
            got[n] = v[0]["origin"]
        self.assertEqual(got, origins)

    def test_unverifiable_and_test_origins_do_not_fail_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "singws_2026-10-22.log", [_line("ROTATION-SLOT", "filled preserved singer slot singer='S' reason=empty_slot_retained")])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(qr.main([str(path)]), 0)


class CoverageTests(unittest.TestCase):
    def test_coverage_counts_and_flags_missing_evidence(self):
        events = [
            {"kind": "tombstone_created", "id": A, "t": "t", "seq": 0},
            {"kind": "tombstone_poll_ignored", "id": A, "t": "t", "seq": 1},
            {"kind": "queue_insert", "id": B, "source": "server", "t": "t", "seq": 2},
            {"kind": "host_sync", "id": A, "key": "k", "t": "t", "seq": 3},
        ]
        cov = qr.coverage(events)
        self.assertEqual(cov["RESURRECTION"], {"tombstones": 1, "guard_hits": 1, "server_inserts": 1})
        self.assertEqual(cov["ID_STABLE"], {"host_sync_events": 1, "distinct_keys": 1})
        self.assertEqual(qr.unexercised(cov), ["DEDUP_OLDEST", "ID_REUSE_PATH", "AUTH", "SLOT_FILL", "ACCEPTING_HOST"])


if __name__ == "__main__":
    unittest.main()
