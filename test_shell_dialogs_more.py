"""`queue.set_singer_brand`, `queue.message_singer` and `queue.replace_song`: the shell / phone doing what the rotation menu's Preferred Brand Override, Direct Message and Replace Track do.

Real code under test: `_execute_shell_queue_command`, the three `shell_queue_*` helpers, the app's own `normalize_disc_priority`, `_set_singer_preference`, `_clear_singer_preference`,
`_singer_disc_priority_override` and `_replace_queue_song_with_track` (over the throwaway library of test_shell_add_song). Recorded stand-ins: the HTTP send of a direct message and the server push of a replaced request.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import random
import threading
import time
import types
import unittest

import test_shell_add_song as ta
import test_shell_queue_commands as base


def harness(mode="apply", queue=None):
    h = ta.AddHarness(queue if queue is not None else base.random_queue(random.Random(11)))
    a = h.app
    a._is_rotation_mode = lambda: True
    a.settings.update({"shell_commands": mode, "base_url": "https://example.test", "user": "dan", "api_key": "k"})
    a.singer_preferences = {}
    a._shell_recent_messages = None  # an uninitialised Qt object raises on a missing attribute
    a._get_singer_history_record = lambda *args, **kw: None
    for name in ("_set_singer_preference", "_clear_singer_preference", "_singer_disc_priority_override", "_normalize_singer_pref_key", "clear_singer_brand_override",
                 "shell_queue_set_singer_brand", "shell_queue_message_singer", "shell_queue_replace_song", "_replace_queue_song_with_track", "_build_queue_entry_from_track_choice", "_waiting_for_add_song_data_from_track"):
        setattr(a, name, types.MethodType(getattr(h.m.KaraokeApp, name), a))
    a.save_data = lambda: h.record("save_data")
    a._schedule_next_up_prescan = lambda: h.record("prescan")
    a.post_rotation = lambda: h.record("post_rotation")
    a._push_remote_request_replacement = lambda entry, **kw: h.record("push_replacement", entry.get("title"), kw.get("source"))
    a._run_on_ui_thread = lambda fn: fn()
    return h


def run(h, action, args, rid):
    return h.app._execute_shell_queue_command(action, dict(args, _request_id=rid, _sent_at=time.time()))


class BrandTests(unittest.TestCase):
    def test_save_stores_the_normalised_list_everywhere_the_dialog_does_and_refreshes(self):
        h = harness()
        s = h.app.queue[0]
        want = ", ".join(h.m.normalize_disc_priority("kv, sc, kv, sbi"))
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": s["singer_id"], "brands": "kv, sc, kv, sbi"}, "b1"), (True, "brand_set"))
        self.assertEqual(s["preferred_disc_priority"], want)
        self.assertEqual(h.app.singer_preferences[s["name"].lower()]["preferred_disc_priority"], want)
        self.assertEqual(h.app._singer_disc_priority_override(s["name"]), want.split(", "))
        self.assertEqual([c[0] for c in h.calls if c[0] in ("save_data", "update_queue_display")], ["save_data", "update_queue_display"])

    def test_the_same_list_is_no_change_and_an_empty_one_clears_like_the_clear_button(self):
        h = harness()
        s = h.app.queue[0]
        args = {"singer_id": s["singer_id"], "brands": "KV, SC"}
        self.assertEqual(run(h, "queue.set_singer_brand", args, "b1"), (True, "brand_set"))
        n = len(h.calls)
        self.assertEqual(run(h, "queue.set_singer_brand", args, "b2"), (True, "no_change"))
        self.assertEqual(len(h.calls), n, "nothing saved or refreshed")
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": s["singer_id"], "brands": "  "}, "b3"), (True, "brand_cleared"))
        self.assertNotIn("preferred_disc_priority", s)
        self.assertFalse(any("preferred_disc_priority" in p for p in h.app.singer_preferences.values()), "the preference field is gone (the dialog's Clear leaves the name record, as here)")
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": s["singer_id"], "brands": ""}, "b4"), (True, "no_change"), "clearing nothing")

    def test_bad_input_is_refused_and_changes_nothing(self):
        h = harness()
        sid = h.app.queue[0]["singer_id"]
        for n, bad in enumerate((", ,", ",", "KV\x07", "K\nV", "X" * 201, None, 5, ["KV"])):
            self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": sid, "brands": bad}, f"x{n}"), (False, "bad_brands"), repr(bad))
        self.assertNotIn("preferred_disc_priority", h.app.queue[0])
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": "nobody", "brands": "KV"}, "u1"), (False, "unknown_singer"))
        self.assertEqual(run(h, "queue.set_singer_brand", {"brands": "KV"}, "u2"), (False, "unknown_singer"))
        self.assertFalse(any("preferred_disc_priority" in p for p in h.app.singer_preferences.values()))

    def test_the_limit_is_ten_brands_like_the_dialog(self):
        h = harness()
        s = h.app.queue[0]
        many = ", ".join(f"B{i}" for i in range(15))
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": s["singer_id"], "brands": many}, "m1"), (True, "brand_set"))
        self.assertEqual(len(s["preferred_disc_priority"].split(", ")), 10)

    def test_dry_run_checks_and_changes_nothing_off_refuses(self):
        h = harness("dry_run")
        sid = h.app.queue[0]["singer_id"]
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": sid, "brands": "KV"}, "d1"), (True, "dry_run: would change the brand priority"))
        self.assertNotIn("preferred_disc_priority", h.app.queue[0])
        self.assertEqual([c for c in h.calls if c[0] in ("save_data", "update_queue_display")], [])
        self.assertEqual(run(h, "queue.set_singer_brand", {"singer_id": sid, "brands": "KV" * 120}, "d2"), (False, "bad_brands"), "a dry run still validates")
        off = harness("off")
        self.assertEqual(run(off, "queue.set_singer_brand", {"singer_id": "x", "brands": "KV"}, "o1"), (False, "shell_commands_off"))


class MessageTests(unittest.TestCase):
    def rig(self, mode="apply", ok=True, error="", hold=None):
        h = harness(mode)
        a = h.app
        done = threading.Event()
        sent = []
        a.host_relay_worker = types.SimpleNamespace(is_connected=lambda: True, send_json=lambda m: sent.append(m))

        def fake(name, text, system_notice=False):
            h.record("net_send", name, text)
            if hold is not None:
                hold.wait(3)
            try:
                return ok, error
            finally:
                threading.Timer(0.05, done.set).start()

        a._net_send_direct_message = fake
        return h, sent, done

    def wait(self, done, sent):
        self.assertTrue(done.wait(3), "the worker ran")
        deadline = time.time() + 3
        while not sent and time.time() < deadline:
            time.sleep(0.01)

    def test_it_answers_sending_at_once_then_reports_the_real_outcome_as_a_notice(self):
        h, sent, done = self.rig()
        s = h.app.queue[0]
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": s["singer_id"], "message": "  You are up next!  "}, "m1"), (True, "sending"))
        self.wait(done, sent)
        self.assertEqual([c for c in h.calls if c[0] == "net_send"], [("net_send", s["name"], "You are up next!")], "the same text the dialog would send, trimmed")
        self.assertEqual(sent, [{"type": "notice", "request_id": "m1", "ok": True, "code": "message_sent", "message": ""}])

    def test_a_failure_is_reported_and_may_be_retried_but_a_double_tap_is_refused(self):
        hold = threading.Event()
        h, sent, done = self.rig(ok=False, error="Server returned HTTP 500.", hold=hold)
        sid = h.app.queue[0]["singer_id"]
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": "Hello"}, "f1"), (True, "sending"))
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": "Hello"}, "f2"), (False, "duplicate_message"), "refused while the first is in flight")
        hold.set()
        self.wait(done, sent)
        self.assertEqual(sent[0]["code"], "message_failed")
        self.assertEqual(sent[0]["message"], "Server returned HTTP 500.")
        self.assertFalse(sent[0]["ok"])
        deadline = time.time() + 2
        while h.app._shell_recent_messages and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": "Hello"}, "f3"), (True, "sending"), "after a failure the same text may be sent again")

    def test_a_delivered_message_blocks_the_same_text_for_thirty_seconds_but_not_other_text_or_singers(self):
        h, sent, done = self.rig()
        a, b = h.app.queue[0], h.app.queue[1]
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": a["singer_id"], "message": "Hi"}, "d1"), (True, "sending"))
        self.wait(done, sent)
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": a["singer_id"], "message": "Hi"}, "d2"), (False, "duplicate_message"))
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": a["singer_id"], "message": "Hi again"}, "d3"), (True, "sending"))
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": b["singer_id"], "message": "Hi"}, "d4"), (True, "sending"))
        for k in list(h.app._shell_recent_messages):
            h.app._shell_recent_messages[k] -= 31.0
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": a["singer_id"], "message": "Hi"}, "d5"), (True, "sending"), "after 30 s it is allowed again")

    def test_message_checks(self):
        h, sent, done = self.rig()
        sid = h.app.queue[0]["singer_id"]
        for n, bad in enumerate(("", "   ", "x" * 501, "bell\x07", "nul\x00", None, 7, ["a"])):
            self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": bad}, f"b{n}"), (False, "bad_message"), repr(bad))
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": "nobody", "message": "Hi"}, "u1"), (False, "unknown_singer"))
        self.assertEqual([c for c in h.calls if c[0] == "net_send"], [], "nothing was sent for any refusal")
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": "x" * 500}, "ok1"), (True, "sending"), "500 characters is allowed")
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": h.app.queue[1]["singer_id"], "message": "line one\\nline two".replace("\\n", "\n")}, "ok2"), (True, "sending"), "a line break is allowed")

    def test_missing_network_settings_refuse(self):
        for key in ("base_url", "user", "api_key"):
            h, sent, done = self.rig()
            h.app.settings[key] = ""
            self.assertEqual(run(h, "queue.message_singer", {"singer_id": h.app.queue[0]["singer_id"], "message": "Hi"}, "n1"), (False, "network_not_configured"), key)
            self.assertEqual([c for c in h.calls if c[0] == "net_send"], [])

    def test_dry_run_sends_nothing_and_does_not_remember_the_text(self):
        h, sent, done = self.rig("dry_run")
        sid = h.app.queue[0]["singer_id"]
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": "Hi"}, "r1"), (True, "dry_run: would send the message"))
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": sid, "message": "Hi"}, "r2"), (True, "dry_run: would send the message"), "a dry run never blocks a later real send")
        time.sleep(0.1)
        self.assertEqual([c for c in h.calls if c[0] == "net_send"], [])
        self.assertEqual(sent, [])

    def test_a_dropped_relay_just_loses_the_notice(self):
        h, sent, done = self.rig()
        h.app.host_relay_worker = types.SimpleNamespace(is_connected=lambda: False, send_json=lambda m: sent.append(m))
        self.assertEqual(run(h, "queue.message_singer", {"singer_id": h.app.queue[0]["singer_id"], "message": "Hi"}, "x1"), (True, "sending"))
        self.assertTrue(done.wait(3))
        time.sleep(0.1)
        self.assertEqual(sent, [])


class ReplaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ta.env()

    def setup(self, **kw):
        h = harness(**kw)
        a = h.app
        a.queue[:] = [{"singer_id": "s1", "name": "Dana", "songs": [], "skipped": False, "created_at": 1.0}, {"singer_id": "s2", "name": "Sam", "songs": [], "skipped": False, "created_at": 2.0}]
        ids = ta.env()["ids"]
        self.ids = ids
        for singer, fname, key in (("Dana", "a.mp4", 2), ("Dana", "b.cdg", 0), ("Sam", "good.zip", 0)):
            self.assertEqual(a.shell_queue_add_song(singer, ids[fname], key), (True, "added"), fname)
        h.calls.clear()
        return h

    def first_entry(self, h):
        e = h.app.queue[0]["songs"][0]
        return e, str(h.app._ensure_queue_entry_id(e))

    def test_it_swaps_the_song_in_place_keeping_singer_position_and_key_and_syncs_like_the_dialog(self):
        h = self.setup()
        a = h.app
        e0, eid = self.first_entry(h)
        before = [list(s["songs"]) for s in a.queue]
        path = h.app._song_info_primary_path
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["plain.kfn"]}, "r1"), (True, "replaced"))
        now = a.queue[0]["songs"]
        self.assertEqual(len(now), 2)
        self.assertTrue(str(path(now[0]["song_info"])).endswith("plain.kfn"), "the first slot holds the new song")
        self.assertEqual(now[0].get("key"), 2, "the song's own key is kept")
        self.assertIs(now[1], before[0][1], "the singer's other song is the same object")
        self.assertEqual([c[0] for c in h.calls if c[0] in ("update_queue_display", "save_data", "prescan", "post_rotation", "push_replacement")],
                         ["update_queue_display", "save_data", "prescan", "post_rotation", "push_replacement"])
        self.assertEqual([c for c in h.calls if c[0] == "push_replacement"][0][2], "shell", "tagged as coming from the shell")
        self.assertEqual(a.queue[1]["songs"][0], before[1][0], "another singer is untouched")

    def test_every_refusal_changes_nothing(self):
        h = self.setup()
        a = h.app
        e0, eid = self.first_entry(h)
        snapshot = [[dict(x) for x in s["songs"]] for s in a.queue]
        cases = [
            ({"entry_id": "nope", "song_id": self.ids["plain.kfn"]}, "unknown_entry"),
            ({"entry_id": eid, "song_id": 999999}, "unknown_song"),
            ({"entry_id": eid, "song_id": self.ids["gone.mp4"]}, "file_missing"),
            ({"entry_id": eid, "song_id": self.ids["c.cdg"]}, "missing_mp3"),
            ({"entry_id": eid, "song_id": self.ids["b.cdg"]}, "duplicate"),
            ({"entry_id": eid, "song_id": 0}, "bad_song_id"),
            ({"entry_id": eid, "song_id": True}, "bad_song_id"),
            ({"entry_id": eid, "song_id": "5"}, "bad_song_id"),
            ({"entry_id": eid}, "bad_song_id"),
        ]
        for n, (args, code) in enumerate(cases):
            self.assertEqual(run(h, "queue.replace_song", args, f"x{n}"), (False, code), args)
        self.assertEqual([[dict(x) for x in s["songs"]] for s in a.queue], snapshot)
        self.assertEqual([c for c in h.calls if c[0] in ("save_data", "push_replacement")], [])

    def test_a_skipped_copy_of_the_song_does_not_count_as_a_duplicate(self):
        h = self.setup()
        e0, eid = self.first_entry(h)
        h.app.queue[0]["songs"][1]["skipped"] = True  # the singer's b.cdg is skipped
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["b.cdg"]}, "k1"), (True, "replaced"))

    def test_the_same_song_is_no_change(self):
        h = self.setup()
        e0, eid = self.first_entry(h)
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["a.mp4"]}, "s1"), (True, "no_change"))
        self.assertEqual([c for c in h.calls if c[0] == "save_data"], [])

    def test_the_song_that_is_playing_right_now_is_not_replaced(self):
        h = self.setup()
        e0, eid = self.first_entry(h)
        h.app.karaoke_playing = True
        h.app._current_karaoke_request_id = eid
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["plain.kfn"]}, "p1"), (False, "song_playing"))
        e1 = h.app.queue[0]["songs"][1]
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": str(h.app._ensure_queue_entry_id(e1)), "song_id": self.ids["plain.kfn"]}, "p2"), (True, "replaced"), "another song of the same singer is fine")
        h.app.karaoke_playing = False
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["good.zip"]}, "p3"), (True, "replaced"), "once it has stopped it can be replaced")

    def test_dry_run_checks_everything_and_changes_nothing(self):
        h = self.setup(mode="dry_run")
        a = h.app
        e0, eid = self.first_entry(h)
        before = [dict(x) for x in a.queue[0]["songs"]]
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["plain.kfn"]}, "d1"), (True, "dry_run: would replace the song"))
        self.assertEqual([dict(x) for x in a.queue[0]["songs"]], before)
        self.assertEqual(run(h, "queue.replace_song", {"entry_id": eid, "song_id": self.ids["c.cdg"]}, "d2"), (False, "missing_mp3"), "a dry run still refuses")
        self.assertEqual([c for c in h.calls if c[0] in ("save_data", "push_replacement", "update_queue_display")], [])


if __name__ == "__main__":
    unittest.main()
