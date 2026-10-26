"""`queue.set_song_key`, `queue.set_song_tempo` and `queue.rename_singer`: the shell / phone doing what the app's Change Key, Change Speed and Rename Singer dialogs do after OK.

Real code under test: `_execute_shell_queue_command` (modes, request ids, id checks, argument checks), `shell_queue_set_song_modifier`, `shell_queue_rename_singer`, and the app's own
`_queue_song_entry` / `_persist_song_modifier_change`. Recorded stand-ins: the server push of the request modifiers and the real rename (`_rename_rotation_singer`, which has its own tests).
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import copy
import time
import types
import unittest

import test_shell_queue_commands as base


def harness(mode="apply", pitch=True):
    h = base.Harness(base.random_queue(__import__("random").Random(11)))
    a = h.app
    a.settings["shell_commands"] = mode
    a._karaoke_pitch_supported = pitch
    a._push_remote_request_modifiers = lambda entry: h.record("push_modifiers", entry.get("request_uid"), entry.get("key"), entry.get("tempo_percent"))

    def rename(idx, name, source="host", merge_prefs_history=True):
        h.record("rename", idx, name, source)
        a.queue[idx]["name"] = name
        return True

    a._rename_rotation_singer = rename
    return h


def first_entry(h):
    for s in h.app.queue:
        for e in s["songs"]:
            return s, e
    raise AssertionError("no songs in the sample queue")


def run(h, action, args, rid):
    a = dict(args, _request_id=rid, _sent_at=time.time())
    return h.app._execute_shell_queue_command(action, a)


def entry_id(h, e):
    return str(h.app._ensure_queue_entry_id(e))


class SongModifierTests(unittest.TestCase):
    def test_key_sets_the_entry_pushes_it_then_saves_and_refreshes_like_the_dialog(self):
        h = harness()
        _, e = first_entry(h)
        before = copy.deepcopy(e)
        self.assertEqual(run(h, "queue.set_song_key", {"entry_id": entry_id(h, e), "key": -3}, "k1"), (True, "changed"))
        self.assertEqual(e["key"], -3)
        self.assertEqual({k: v for k, v in e.items() if k != "key"}, {k: v for k, v in before.items() if k != "key"}, "nothing else on the entry changed")
        names = [c[0] for c in h.calls if c[0] in ("push_modifiers", "save_data", "update_queue_display")]
        self.assertEqual(names, ["push_modifiers", "save_data", "update_queue_display"], "the dialog's order: push to the server's request, then save, then refresh")
        self.assertEqual([c for c in h.calls if c[0] == "push_modifiers"][0][2], -3, "the server push sees the new key")

    def test_tempo_sets_tempo_percent_the_same_way(self):
        h = harness()
        _, e = first_entry(h)
        self.assertEqual(run(h, "queue.set_song_tempo", {"entry_id": entry_id(h, e), "tempo_percent": 85}, "t1"), (True, "changed"))
        self.assertEqual(e["tempo_percent"], 85)
        self.assertEqual([c[0] for c in h.calls if c[0] in ("push_modifiers", "save_data")], ["push_modifiers", "save_data"])

    def test_the_dialogs_ranges_are_inclusive_and_nothing_outside_them_is_accepted(self):
        h = harness()
        _, e = first_entry(h)
        eid = entry_id(h, e)
        for n, (field, action, ok_values, bad_values, code) in enumerate((
                ("key", "queue.set_song_key", (-5, 0, 5), (-6, 6, 1.0, 2.5, "3", None, True, [1]), "bad_key"),
                ("tempo_percent", "queue.set_song_tempo", (70, 100, 130), (69, 131, 100.0, "100", None, False, 0), "bad_tempo"))):
            for v in ok_values:
                self.assertEqual(run(h, action, {"entry_id": eid, field: v}, f"ok{n}{v}"), (True, "changed"), (field, v))
                self.assertEqual(e[field], v)
            keep = e[field]
            for v in bad_values:
                self.assertEqual(run(h, action, {"entry_id": eid, field: v}, f"bad{n}{id(v)}{v}"), (False, code), (field, v))
            self.assertEqual(e[field], keep, "a refused value changes nothing")
        self.assertEqual(run(h, "queue.set_song_key", {"entry_id": eid}, "nokey"), (False, "bad_key"))

    def test_an_unknown_entry_and_an_unsupported_pitch_are_refused(self):
        h = harness()
        self.assertEqual(run(h, "queue.set_song_key", {"entry_id": "nope", "key": 1}, "u1"), (False, "unknown_entry"))
        self.assertEqual(run(h, "queue.set_song_tempo", {"entry_id": "", "tempo_percent": 90}, "u2"), (False, "unknown_entry"))
        p = harness(pitch=False)
        _, e = first_entry(p)
        self.assertEqual(run(p, "queue.set_song_key", {"entry_id": entry_id(p, e), "key": 2}, "u3"), (False, "pitch_unsupported"))
        self.assertNotEqual(e.get("key"), 2)
        self.assertEqual(run(p, "queue.set_song_tempo", {"entry_id": entry_id(p, e), "tempo_percent": 90}, "u4"), (True, "changed"), "tempo does not depend on pitch support")

    def test_dry_run_and_off_change_nothing(self):
        for mode, want in (("dry_run", (True, "dry_run: would set the song's key")), ("off", (False, "shell_commands_off"))):
            h = harness(mode)
            _, e = first_entry(h)
            before = copy.deepcopy(e)
            self.assertEqual(run(h, "queue.set_song_key", {"entry_id": entry_id(h, e), "key": 4}, f"d{mode}"), want)
            self.assertEqual(e, before)
            self.assertFalse(any(c[0] in ("push_modifiers", "save_data") for c in h.calls))

    def test_the_same_request_id_acts_once_and_a_stale_fingerprint_is_still_honoured(self):
        h = harness()
        _, e = first_entry(h)
        eid = entry_id(h, e)
        self.assertEqual(run(h, "queue.set_song_key", {"entry_id": eid, "key": 1}, "same"), (True, "changed"))
        self.assertEqual(run(h, "queue.set_song_key", {"entry_id": eid, "key": 2}, "same"), (True, "duplicate: changed"))
        self.assertEqual(e["key"], 1)
        self.assertEqual(run(h, "queue.set_song_key", {"entry_id": eid, "key": 3, "expect_queue_sig": "0000000000000000"}, "stale"), (False, "stale_view"))
        self.assertEqual(e["key"], 1)


class RenameSingerTests(unittest.TestCase):
    def test_a_rename_goes_through_the_apps_own_rename_with_the_trimmed_name(self):
        h = harness()
        s = h.app.queue[0]
        self.assertEqual(run(h, "queue.rename_singer", {"singer_id": s["singer_id"], "name": "  Dana Marie  "}, "r1"), (True, "renamed"))
        self.assertEqual(s["name"], "Dana Marie")
        self.assertEqual([c for c in h.calls if c[0] == "rename"], [("rename", 0, "Dana Marie", "shell")])

    def test_the_same_name_changes_nothing_and_a_name_that_belongs_to_another_singer_is_refused(self):
        h = harness()
        a, b = h.app.queue[0], h.app.queue[1]
        self.assertEqual(run(h, "queue.rename_singer", {"singer_id": a["singer_id"], "name": a["name"]}, "r2"), (True, "no_change"))
        for n, taken in enumerate((b["name"], b["name"].upper(), "  " + b["name"].lower() + " ", b["name"].replace(" ", "   "))):
            self.assertEqual(run(h, "queue.rename_singer", {"singer_id": a["singer_id"], "name": taken}, f"r3{n}"), (False, "name_taken"), taken)
        b["skipped"] = True
        self.assertEqual(run(h, "queue.rename_singer", {"singer_id": a["singer_id"], "name": b["name"]}, "r4"), (False, "name_taken"), "a skipped singer still owns the name")
        self.assertFalse(any(c[0] == "rename" for c in h.calls))
        self.assertTrue(run(h, "queue.rename_singer", {"singer_id": a["singer_id"], "name": a["name"].lower()}, "r5")[0], "changing only the case of your own name is allowed")

    def test_bad_names_and_unknown_singers_are_refused(self):
        h = harness()
        sid = h.app.queue[0]["singer_id"]
        for n, bad in enumerate(("", "   ", "x" * 61, "tab\there", "nul\x00", "del\x7f", "line\nbreak")):
            self.assertEqual(run(h, "queue.rename_singer", {"singer_id": sid, "name": bad}, f"b{n}"), (False, "bad_name"), repr(bad))
        for n, bad in enumerate((None, 5, ["a"])):
            self.assertEqual(run(h, "queue.rename_singer", {"singer_id": sid, "name": bad}, f"c{n}"), (False, "bad_name"))
        self.assertEqual(run(h, "queue.rename_singer", {"singer_id": "ghost", "name": "Anyone"}, "g1"), (False, "unknown_singer"))
        self.assertTrue(run(h, "queue.rename_singer", {"singer_id": sid, "name": "x" * 60}, "ok60")[0])
        self.assertTrue(run(h, "queue.rename_singer", {"singer_id": sid, "name": "Mom ‍\U0001F469"}, "emoji")[0])

    def test_dry_run_off_and_a_failed_rename(self):
        d = harness("dry_run")
        s = d.app.queue[0]
        old = s["name"]
        self.assertEqual(run(d, "queue.rename_singer", {"singer_id": s["singer_id"], "name": "Someone New"}, "d1"), (True, "dry_run: would rename the singer"))
        self.assertEqual(s["name"], old)
        o = harness("off")
        self.assertEqual(run(o, "queue.rename_singer", {"singer_id": o.app.queue[0]["singer_id"], "name": "Someone New"}, "o1"), (False, "shell_commands_off"))
        f = harness()
        f.app._rename_rotation_singer = lambda *a, **k: False
        self.assertEqual(run(f, "queue.rename_singer", {"singer_id": f.app.queue[0]["singer_id"], "name": "Someone New"}, "f1"), (False, "not_renamed"))
        e = harness()
        e.app._rename_rotation_singer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk"))
        self.assertEqual(run(e, "queue.rename_singer", {"singer_id": e.app.queue[0]["singer_id"], "name": "Someone New"}, "e1"), (False, "failed: disk"))


class RelayEntryPointTests(unittest.TestCase):
    def test_all_three_arrive_through_the_relay_command_entry_point(self):
        h = harness()
        sent = []
        h.app.host_relay_worker = types.SimpleNamespace(send_json=sent.append, is_connected=lambda: True)
        h.app._publish_host_state_ws = lambda force=False: None
        s, e = first_entry(h)
        for n, (cmd, args, msg) in enumerate((("queue.set_song_key", {"entry_id": entry_id(h, e), "key": 2}, "changed"), ("queue.set_song_tempo", {"entry_id": entry_id(h, e), "tempo_percent": 90}, "changed"),
                                              ("queue.rename_singer", {"singer_id": s["singer_id"], "name": "Renamed One"}, "renamed"))):
            sent.clear()
            h.app._on_host_relay_command({"type": "command", "command": cmd, "args": args, "request_id": f"r{n}", "sent_at": int(time.time())})
            self.assertEqual(sent, [{"type": "ack", "request_id": f"r{n}", "ok": True, "message": msg}], cmd)


if __name__ == "__main__":
    unittest.main()
