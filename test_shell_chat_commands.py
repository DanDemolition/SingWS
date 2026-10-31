"""`chat.say`: the shell / phone speaking as the host in the Everyone room or in one singer's Host chat, through the paths the Chat page's Send uses.

Real code under test: `_execute_shell_queue_command` (gate, dedupe, expiry, validation), `shell_chat_say`, `_chat_conversations`, `_host_chat_media_rows`, `_host_chat_singer_name`,
`_room_chat_conn`. Recorded stand-ins: the HTTP post for the Everyone room, `_net_send_direct_message` for Host chat, the relay worker (the `notice` it receives) and the polls."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import test_shell_dialogs_more as td


def rig(mode="apply", ok=True, error="", hold=None, room=True, http_ok=None):
    h = td.harness(mode)
    a = h.app
    cls = h.m.KaraokeApp
    for name in ("shell_chat_say", "_chat_conversations", "_host_chat_media_rows", "_host_chat_singer_name", "_room_chat_conn"):
        setattr(a, name, types.MethodType(getattr(cls, name), a))
    a._ROOM_SAY_ERRORS = cls._ROOM_SAY_ERRORS
    a._room_settings = {"group_enabled": True, "dm_enabled": True} if room else {}
    a._room_messages = {9: {"id": 9, "channel": "host", "thread": "cy", "from": "Cy", "from_key": "cy", "message": "", "kind": "image", "created_at": 5}}
    a._chat_messages = [{"id": 1, "singer": "Ann", "singer_key": "ann", "message": "hello", "direction": "in", "read": False, "created_at": 100}]
    a._shell_recent_chat = None
    a._chat_data_generation = 0
    a._chat_last_id = 7
    a.settings["host_chat_name"] = "DJ Dan"
    sent, posts, done = [], [], threading.Event()
    a.host_relay_worker = types.SimpleNamespace(is_connected=lambda: True, send_json=lambda m: (sent.append(m), done.set()))
    a._schedule_chat_poll = lambda: h.record("chat_poll")
    a._schedule_room_poll = lambda full=False: h.record("room_poll", full)

    def direct(name, text, system_notice=False):
        h.record("net_send", name, text)
        if hold is not None:
            hold.wait(3)
        return ok, error
    a._net_send_direct_message = direct

    class Resp:
        content = b"{}"

        def __init__(self):
            self.ok = http_ok if http_ok is not None else ok

        def json(self):
            return {"ok": ok, "error": error}
    mock.patch.object(h.m.requests, "post", lambda url, data=None, headers=None, timeout=None: (posts.append((url, dict(data), dict(headers))), Resp())[1]).start()
    return h, sent, posts, done


unittest.addModuleCleanup(mock.patch.stopall)


def run(h, args, rid):
    return h.app._execute_shell_queue_command("chat.say", dict(args, _request_id=rid, _sent_at=time.time()))


def wait(done):
    assert done.wait(3), "the worker answered"


class EveryoneTests(unittest.TestCase):
    def test_it_answers_sending_then_reports_a_notice_and_posts_what_the_page_posts(self):
        h, sent, posts, done = rig()
        self.assertEqual(run(h, {"channel": "group", "text": "  Welcome all!  "}, "g1"), (True, "sending"))
        wait(done)
        self.assertEqual(len(posts), 1)
        url, data, headers = posts[0]
        self.assertEqual(url, "https://example.test/api/v1/host_chat_moderation.php")
        self.assertEqual(data, {"user": "dan", "action": "say", "channel": "group", "a": "", "b": "", "message": "Welcome all!", "host_name": "DJ Dan"})
        self.assertEqual(headers["X-API-Key"], "k")
        self.assertEqual(sent, [{"type": "notice", "request_id": "g1", "ok": True, "code": "chat_sent", "message": ""}])
        self.assertIn(("room_poll", True), h.calls, "refreshes the chat page like the page does")
        self.assertEqual([c for c in h.calls if c[0] == "net_send"], [], "Everyone never goes through the direct-message path")

    def test_a_server_refusal_is_reported_in_plain_words_and_may_be_retried(self):
        h, sent, posts, done = rig(ok=False, error="group_chat_disabled")
        self.assertEqual(run(h, {"channel": "group", "text": "Hi"}, "g1"), (True, "sending"))
        wait(done)
        self.assertEqual((sent[0]["ok"], sent[0]["code"]), (False, "chat_failed"))
        self.assertIn("Everyone chat is switched off", sent[0]["message"])
        self.assertEqual(run(h, {"channel": "group", "text": "Hi"}, "g2"), (True, "sending"), "after a failure the same text may be sent again")

    def test_a_server_answer_that_says_not_ok_is_a_failure_even_with_http_200(self):
        h, sent, posts, done = rig(ok=False, error="message_too_long", http_ok=True)
        run(h, {"channel": "group", "text": "Hi"}, "g1")
        wait(done)
        self.assertEqual((sent[0]["ok"], sent[0]["code"]), (False, "chat_failed"))

    def test_chat_off_or_not_loaded_is_refused_before_anything_is_sent(self):
        h, *_ = rig()
        h.app._room_settings = {"group_enabled": False, "dm_enabled": True}
        self.assertEqual(run(h, {"channel": "group", "text": "Hi"}, "o1"), (False, "group_chat_off"))
        h.app._room_settings = {}
        self.assertEqual(run(h, {"channel": "group", "text": "Hi"}, "o2"), (False, "chat_not_ready"))


class HostChatTests(unittest.TestCase):
    def test_it_sends_through_the_direct_message_path_to_the_singer_of_that_conversation(self):
        h, sent, posts, done = rig()
        self.assertEqual(run(h, {"channel": "host", "singer_key": "ANN", "text": " hi Ann "}, "h1"), (True, "sending"))
        wait(done)
        self.assertEqual([c for c in h.calls if c[0] == "net_send"], [("net_send", "Ann", "hi Ann")])
        self.assertEqual(posts, [])
        self.assertEqual(sent[0]["code"], "chat_sent")
        self.assertEqual((h.app._chat_data_generation, h.app._chat_last_id, h.app._chat_messages), (1, 0, []), "the page re-reads the conversation like Send does")
        self.assertIn(("chat_poll",), h.calls)

    def test_a_conversation_that_only_has_a_picture_counts(self):
        h, sent, posts, done = rig()
        h.app._host_chat_singer_name = lambda key: "Cy"
        self.assertEqual(run(h, {"channel": "host", "singer_key": "cy", "text": "hi"}, "h1"), (True, "sending"))
        wait(done)

    def test_unknown_conversations_and_bad_keys_are_refused(self):
        h, *_ = rig()
        self.assertEqual(run(h, {"channel": "host", "singer_key": "nobody", "text": "hi"}, "u1"), (False, "unknown_conversation"))
        for n, bad in enumerate((None, "", "  ", 5, ["ann"], "a" * 201, "an\nn")):
            self.assertEqual(run(h, {"channel": "host", "singer_key": bad, "text": "hi"}, f"k{n}"), (False, "bad_singer_key"), repr(bad))
        self.assertEqual(run(h, {"channel": "host", "text": "hi"}, "k9"), (False, "bad_singer_key"))
        self.assertEqual([c for c in h.calls if c[0] == "net_send"], [])

    def test_a_host_chat_failure_is_reported(self):
        h, sent, posts, done = rig(ok=False, error="direct_messages_disabled")
        run(h, {"channel": "host", "singer_key": "ann", "text": "hi"}, "f1")
        wait(done)
        self.assertEqual((sent[0]["code"], sent[0]["ok"]), ("chat_failed", False))
        self.assertIn("Messaging singers is switched off", sent[0]["message"])
        self.assertEqual(h.app._chat_data_generation, 0, "nothing was sent, so nothing is re-read")


class ValidationTests(unittest.TestCase):
    def test_channel_and_text_are_checked(self):
        h, *_ = rig()
        for n, ch in enumerate(("dm", "private", "", None, 3, "GROUP")):
            self.assertEqual(run(h, {"channel": ch, "text": "hi"}, f"c{n}"), (False, "bad_channel"), repr(ch))
        for n, bad in enumerate(("", "   ", None, 5, ["x"], "x" * 301, "a\nb", "a\tb", "a\x07b", "a\x00b", "a\u0085b")):
            self.assertEqual(run(h, {"channel": "group", "text": bad}, f"t{n}"), (False, "bad_text"), repr(bad))
        self.assertEqual(h.calls and [c for c in h.calls if c[0] == "net_send"], [])

    def test_exactly_300_characters_and_emoji_are_allowed(self):
        h, sent, posts, done = rig()
        self.assertEqual(run(h, {"channel": "group", "text": "x" * 300}, "e1"), (True, "sending"))
        self.assertEqual(run(h, {"channel": "group", "text": "🎤🔥 party 🎉"}, "e2"), (True, "sending"))

    def test_missing_network_settings_refuse(self):
        for key in ("base_url", "user", "api_key"):
            h, *_ = rig()
            h.app.settings[key] = ""
            self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "n1"), (False, "network_not_configured"), key)

    def test_a_double_tap_is_refused_for_the_same_place_but_not_elsewhere(self):
        hold = threading.Event()
        h, sent, posts, done = rig(hold=hold)
        self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "d1"), (True, "sending"))
        self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "d2"), (False, "duplicate_message"))
        self.assertEqual(run(h, {"channel": "group", "text": "hi again"}, "d3"), (True, "sending"))
        self.assertEqual(run(h, {"channel": "host", "singer_key": "ann", "text": "hi"}, "d4"), (True, "sending"), "same text, other place")
        self.assertEqual(run(h, {"channel": "host", "singer_key": "ann", "text": "hi"}, "d5"), (False, "duplicate_message"))
        recent = h.app._shell_recent_chat
        for k in list(recent):
            recent[k] -= 31
        self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "d6"), (True, "sending"), "after 30 s it is allowed again")
        hold.set()


class GatingTests(unittest.TestCase):
    def test_off_refuses_and_dry_run_checks_but_sends_nothing(self):
        off, *_ = rig("off")
        self.assertEqual(run(off, {"channel": "group", "text": "hi"}, "x"), (False, "shell_commands_off"))
        h, sent, posts, done = rig("dry_run")
        self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "r1"), (True, "dry_run: would send the message"))
        self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "r2"), (True, "dry_run: would send the message"), "a dry run never blocks a later real send")
        self.assertEqual(run(h, {"channel": "group", "text": "x" * 301}, "r3"), (False, "bad_text"), "a dry run still validates")
        self.assertEqual(run(h, {"channel": "host", "singer_key": "nobody", "text": "hi"}, "r4"), (False, "unknown_conversation"))
        self.assertEqual((posts, [c for c in h.calls if c[0] == "net_send"], sent), ([], [], []))

    def test_a_repeated_request_id_is_not_run_twice_and_an_old_command_expires(self):
        h, sent, posts, done = rig()
        self.assertEqual(run(h, {"channel": "group", "text": "hi"}, "same"), (True, "sending"))
        self.assertEqual(run(h, {"channel": "group", "text": "other"}, "same"), (True, "duplicate: sending"))
        wait(done)
        self.assertEqual(len(posts), 1)
        old = h.app._execute_shell_queue_command("chat.say", {"channel": "group", "text": "late", "_request_id": "old", "_sent_at": time.time() - 30})
        self.assertEqual(old, (False, "expired"))
        none = h.app._execute_shell_queue_command("chat.say", {"channel": "group", "text": "x", "_sent_at": time.time()})
        self.assertEqual(none, (False, "missing_request_id"))

    def test_the_prefix_reaches_the_executor_from_every_entry_point(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('"history.", "settings.", "chat.")'), 3)
        self.assertIn('"settings.set", "chat.say")', src)


if __name__ == "__main__":
    unittest.main()
