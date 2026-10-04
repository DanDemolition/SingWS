"""Host chat: sending a message resets the stored conversation and re-reads it from the server. A poll that was already in
flight at that moment (it asked for 'newer than the old last id') used to land afterwards and become the whole history:
the transcript then showed only the newest line until the next send reset it again (seen on the Intel show Mac 2026-10-03)."""
import ast
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
WANTED = ("_schedule_chat_poll", "_send_chat_message")


class FakeThread:
    pending = []

    def __init__(self, target=None, daemon=None, name=None, **_):
        self.target = target

    def start(self):
        FakeThread.pending.append(self.target)


def build(server_rows):
    """server_rows: list of message dicts the server holds; GET honours since_id."""
    tree = ast.parse(SOURCE)
    methods = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in WANTED:
                    sub.decorator_list = []
                    methods.append(sub)
    requests = types.SimpleNamespace()

    def get(url, params=None, **_):
        since = int((params or {}).get("since_id") or 0)
        rows = [m for m in server_rows if int(m["id"]) > since]
        return SimpleNamespace(ok=True, json=lambda: {"messages": rows})
    requests.get = get
    ns = {"requests": requests, "threading": types.SimpleNamespace(Thread=FakeThread),
          "_network_normalize_base_url": lambda s: s, "Qt": SimpleNamespace(ItemDataRole=SimpleNamespace(UserRole=0))}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "chat-race", "exec"), ns)
    rendered = []
    item = SimpleNamespace(data=lambda role: "alice")
    obj = SimpleNamespace(
        settings={"base_url": "https://x", "user": "u", "api_key": "k"},
        _chat_messages=[], _chat_last_id=0, _chat_poll_inflight=False, _chat_data_generation=0,
        chat_singer_list=SimpleNamespace(currentItem=lambda: item),
        chat_message_input=SimpleNamespace(text=lambda: "hello", clear=lambda: None),
        chat_send_button=SimpleNamespace(setEnabled=lambda v: None),
        _host_chat_singer_name=lambda key: "Alice",
        _net_send_direct_message=lambda singer, message: (True, ""),
        _run_on_ui_thread=lambda fn: fn(),
        _render_chat_page=lambda: rendered.append(list(obj._chat_messages)),
        _show_processing_notification=lambda *a, **k: None)
    for name in WANTED:
        setattr(obj, name, (lambda f: (lambda *a, **k: f(obj, *a, **k)))(ns[name]))
    return obj, rendered


def run_all_pending():
    while FakeThread.pending:
        FakeThread.pending.pop(0)()


class HostChatSendRaceTests(unittest.TestCase):
    def setUp(self):
        FakeThread.pending.clear()

    def test_a_poll_in_flight_during_send_does_not_become_the_whole_history(self):
        server = [{"id": i, "singer_key": "alice", "singer": "Alice", "message": f"m{i}", "direction": "in"} for i in (1, 2, 3)]
        h, rendered = build(server)
        h._chat_messages = list(server); h._chat_last_id = 3
        h._schedule_chat_poll()                       # a poll is now in flight, asking for ids > 3
        inflight = FakeThread.pending.pop(0)
        server.append({"id": 4, "singer_key": "alice", "singer": "Alice", "message": "host says hi", "direction": "out"})
        h._send_chat_message()                        # worker sends, then finish() resets and re-reads
        FakeThread.pending.pop(0)()                   # the send worker -> finish()
        inflight()                                    # the old poll lands AFTER the reset
        run_all_pending()
        h._schedule_chat_poll(); run_all_pending()    # the next 3 s timer tick
        self.assertEqual([m["id"] for m in h._chat_messages], [1, 2, 3, 4], "the whole conversation must come back, not just the newest line")

    def test_an_ordinary_send_still_reloads_the_conversation(self):
        server = [{"id": i, "singer_key": "alice", "singer": "Alice", "message": f"m{i}", "direction": "in"} for i in (1, 2, 3)]
        h, _ = build(server)
        h._chat_messages = list(server); h._chat_last_id = 3
        server.append({"id": 4, "singer_key": "alice", "singer": "Alice", "message": "hi", "direction": "out"})
        h._send_chat_message(); run_all_pending()
        h._schedule_chat_poll(); run_all_pending()
        self.assertEqual([m["id"] for m in h._chat_messages], [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
