"""`_shell_chat_summary`: the room chat the web remote / shell shows read-only, published in the relay's own `chat` message.

Real code under test: `_shell_chat_summary`, `_shell_chat_message` and the `_room_*` / `_host_chat_*` helpers they reuse, on the state the chat page already keeps
(`_room_messages`, `_chat_messages`). No network, no files."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import random
import types
import unittest
from pathlib import Path

import test_shell_add_song as ta
import test_shell_queue_commands as base

METHODS = ("_shell_chat_message", "_shell_chat_summary", "_room_thread_keys", "_room_display_name", "_chat_conversations", "_host_chat_media_rows",
           "_host_chat_media_unread", "_host_chat_singer_name")
CONSTANTS = ("_SHELL_CHAT_TEXT_MAX", "_SHELL_CHAT_GROUP_ROWS", "_SHELL_CHAT_THREADS", "_SHELL_CHAT_THREAD_ROWS")


def app(room=(), host=(), settings=None):
    h = ta.AddHarness(base.random_queue(random.Random(5)))
    a = h.app
    cls = h.m.KaraokeApp
    for name in METHODS:
        setattr(a, name, types.MethodType(getattr(cls, name), a))
    for name in CONSTANTS:
        setattr(a, name, getattr(cls, name))
    a._room_messages = {m["id"]: m for m in room}
    a._chat_messages = list(host)
    a._room_settings = dict(settings or {"group_enabled": True, "dm_enabled": True})
    a._room_reachable = True
    a._room_new_by_channel = {"group": 2, "dm": 1}
    a._room_attention_ids = set()
    a._host_media_read = set()
    a.settings["host_chat_name"] = "DJ Dan"
    return a


def gm(i, text="hi", frm="Ann", **kw):
    return dict({"id": i, "channel": "group", "from": frm, "from_key": frm.lower(), "message": text, "created_at": 1000 + i, "status": "visible"}, **kw)


class GroupTests(unittest.TestCase):
    def test_group_rows_are_oldest_first_with_the_row_shape(self):
        out = app(room=[gm(2, "b"), gm(1, "a"), gm(3, "host says", frm="Host", host=True, from_key="@host")])._shell_chat_summary()
        self.assertEqual([r["text"] for r in out["group"]], ["a", "b", "host says"])
        self.assertEqual(out["group"][0], {"id": 1, "from": "Ann", "host": False, "at": 1001, "text": "a", "attachment": "", "status": "", "reports": 0, "flag": ""})
        self.assertTrue(out["group"][2]["host"])
        self.assertEqual((out["group_enabled"], out["dm_enabled"], out["reachable"], out["unread"]["group"], out["unread"]["dm"]), (True, True, True, 2, 1))

    def test_only_the_newest_25_and_text_is_clipped(self):
        out = app(room=[gm(i, "x" * 500) for i in range(1, 50)])._shell_chat_summary()
        self.assertEqual((len(out["group"]), out["group"][0]["id"], out["group"][-1]["id"]), (25, 25, 49))
        self.assertEqual(len(out["group"][0]["text"]), 120)

    def test_attachments_are_placeholders_never_urls_or_media_ids(self):
        photo = gm(1, "look", kind="image", media_id=77)
        gif = gm(2, "", kind="gif", gif={"id": "g", "preview": "https://media.giphy.com/x.gif", "full": "https://media.giphy.com/y.gif"})
        out = app(room=[photo, gif])._shell_chat_summary()
        self.assertEqual([r["attachment"] for r in out["group"]], ["photo", "gif"])
        blob = json.dumps(out)
        for forbidden in ("giphy", "http", "media_id", "77"):
            self.assertNotIn(forbidden, blob)

    def test_a_picture_or_gif_the_page_would_not_draw_is_not_announced(self):
        rows = [gm(1, "a", kind="image"), gm(2, "b", kind="image", media_id=0), gm(3, "c", kind="gif"), gm(4, "d", kind="gif", gif={"id": "g", "preview": "http://evil.example/x.gif"}),
                gm(5, "e", kind="gif", gif="nope"), gm(6, "f", kind="image", media_id=3), gm(7, "g", kind="gif", gif={"id": "g", "preview": "https://i.giphy.com/x.gif"})]
        out = app(room=rows)._shell_chat_summary()
        self.assertEqual([r["attachment"] for r in out["group"]], ["", "", "", "", "", "photo", "gif"])

    def test_removed_text_is_withheld_and_held_blocked_are_flagged(self):
        out = app(room=[gm(1, "secret", status="removed"), gm(2, "wait", status="held"), gm(3, "x", status="blocked", reports=2), gm(4, "ok", status="weird")])._shell_chat_summary()
        self.assertEqual([(r["status"], r["text"], r["reports"]) for r in out["group"]], [("removed", "", 0), ("held", "wait", 0), ("blocked", "x", 2), ("", "ok", 0)])
        self.assertNotIn("secret", json.dumps(out))


class PrivateTests(unittest.TestCase):
    def dm(self, i, a, b, text="p", **kw):
        return dict({"id": i, "channel": "dm", "from": a.title(), "from_key": a, "to_key": b, "message": text, "created_at": 2000 + i, "status": "visible"}, **kw)

    def test_a_thread_is_one_conversation_whichever_way_it_was_sent_and_the_host_joins_it(self):
        room = [self.dm(1, "ann", "bo"), self.dm(2, "bo", "ann", "back"), self.dm(3, "cy", "di"),
                self.dm(4, "@host", "", "from host", host=True, thread="ann|bo", **{"from": "Host"})]
        out = app(room=room)._shell_chat_summary()
        threads = {(t["a"], t["b"]): [m["text"] for m in t["messages"]] for t in out["dm"]}
        self.assertEqual(threads, {("ann", "bo"): ["p", "back", "from host"], ("cy", "di"): ["p"]})
        self.assertEqual(out["dm"][0]["a_name"], "Ann", "newest conversation first; names come from _room_display_name")
        self.assertEqual(out["group"], [], "private rows never leak into the group room")

    def test_threads_and_rows_are_capped(self):
        room = [self.dm(i, f"a{i}", f"b{i}") for i in range(1, 30)] + [self.dm(100 + i, "x", "y") for i in range(30)]
        out = app(room=room)._shell_chat_summary()
        self.assertEqual(len(out["dm"]), 10)
        xy = [t for t in out["dm"] if t["a"] == "x"][0]
        self.assertEqual(len(xy["messages"]), 5)


class HostChatTests(unittest.TestCase):
    def test_text_and_pictures_merge_by_time_and_unread_counts(self):
        host = [{"id": 1, "singer": "Ann", "singer_key": "ann", "message": "hello", "direction": "in", "read": False, "created_at": 100},
                {"id": 2, "singer": "Ann", "singer_key": "ann", "message": "hi back", "direction": "out", "read": True, "created_at": 110},
                {"id": 3, "singer": "Bo", "singer_key": "bo", "message": "old", "direction": "in", "read": True, "created_at": 50}]
        pic = {"id": 9, "channel": "host", "thread": "ann", "from": "Ann", "from_key": "ann", "message": "", "kind": "image", "media_id": 5, "created_at": 105}
        out = app(room=[pic], host=host)._shell_chat_summary()
        ann = out["host"][0]
        self.assertEqual((ann["key"], ann["singer"], ann["unread"]), ("ann", "Ann", 2), "one unread text plus one unread picture")
        self.assertEqual([(m["from"], m["host"], m["text"], m["attachment"]) for m in ann["messages"]],
                         [("Ann", False, "hello", ""), ("Ann", False, "", "photo"), ("DJ Dan", True, "hi back", "")])
        self.assertEqual((out["host"][1]["key"], out["unread"]["host"]), ("bo", 2))
        self.assertEqual(out["group"], [], "host-channel picture rows are not group messages")


class RobustTests(unittest.TestCase):
    def test_empty_state_and_garbage_never_raise(self):
        a = app()
        out = a._shell_chat_summary()
        self.assertEqual((out["group"], out["dm"], out["host"]), ([], [], []))
        a._room_messages = {1: "not a dict", 2: gm(2)}
        a._chat_messages = [{"singer_key": "x"}, {"singer_key": "y", "created_at": "bad"}]
        self.assertIsInstance(a._shell_chat_summary(), dict)
        a._room_messages = None
        a._chat_conversations = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        self.assertIsInstance(a._shell_chat_summary(), dict)

    def test_the_worst_case_message_fits_the_relay_limit(self):
        room = [gm(i, "é" * 300, frm="S" * 30) for i in range(1, 200)]
        room += [dict(gm(1000 + i, "é" * 300, frm="S" * 30), channel="dm", to_key=f"t{i}", from_key=f"f{i}") for i in range(200)]
        host = [{"id": i, "singer": f"S{i % 20}", "singer_key": f"s{i % 20}", "message": "é" * 300, "direction": "in", "read": False, "created_at": i} for i in range(300)]
        out = app(room=room, host=host)._shell_chat_summary()
        size = len(json.dumps({"type": "chat", "user": "x" * 30, "chat": out, "updated_at": 1}, ensure_ascii=False).encode("utf-8"))
        self.assertLess(size, 64 * 1024, size)


class WiringTests(unittest.TestCase):
    def test_the_host_relay_sends_a_chat_message_beside_the_queue_message(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index('worker.send_json({"type": "queue"')
        self.assertIn('worker.send_json({"type": "chat"', src[i:i + 1200])
        self.assertIn('"chat": self._shell_chat_summary()', src[i:i + 1200])


if __name__ == "__main__":
    unittest.main()
