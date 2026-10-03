"""Host chat pictures and GIFs in the app: Host chat items are room-chat rows on channel "host", merged by time with the
plain-text conversation, counted as unread for the Host chat tab (never for Everyone), and sent to the selected singer."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
WANTED = ("_host_chat_media_rows", "_host_chat_media_unread", "_host_chat_singer_name", "_chat_conversations")


def host():
    tree = ast.parse(SOURCE)
    methods = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in WANTED:
                    sub.decorator_list = []
                    methods[sub.name] = sub
    ns = {}
    exec(compile(ast.Module(body=list(methods.values()), type_ignores=[]), "host-chat", "exec"), ns)
    obj = SimpleNamespace(_room_messages={}, _chat_messages=[])
    for name in WANTED:
        setattr(obj, name, (lambda f: (lambda *a, **k: f(obj, *a, **k)))(ns[name]))
    return obj


def room(mid, **kw):
    row = {"id": mid, "channel": "host", "thread": "alice", "from": "Alice", "from_key": "alice", "message": "", "kind": "image",
           "media_id": mid * 10, "status": "visible", "host": False, "created_at": 1000 + mid}
    row.update(kw)
    return row


class HostChatMediaTests(unittest.TestCase):
    def test_only_host_channel_rows_are_host_chat_items(self):
        h = host()
        h._room_messages = {1: room(1), 2: room(2, channel="group", thread="group"), 3: room(3, thread="bob", from_key="bob", from_="Bob")}
        self.assertEqual([m["id"] for m in h._host_chat_media_rows()], [1, 3])
        self.assertEqual([m["id"] for m in h._host_chat_media_rows("alice")], [1])
        self.assertEqual(h._host_chat_media_rows("nobody"), [])

    def test_unread_counts_only_singer_items_not_seen_yet(self):
        h = host()
        h._room_messages = {1: room(1), 2: room(2, host=True, from_key="@host", from_="DJ"), 3: room(3, status="removed"), 4: room(4)}
        self.assertEqual(h._host_chat_media_unread(), 2)                 # 1 and 4: the host's own and removed ones never count
        h._host_media_read = {1}
        self.assertEqual(h._host_chat_media_unread("alice"), 1)
        h._host_media_read = {1, 4}
        self.assertEqual(h._host_chat_media_unread(), 0)

    def test_a_singer_who_only_sent_a_picture_still_has_a_name(self):
        h = host()
        h._room_messages = {1: room(1, from_="Alice")}
        h._room_messages[1]["from"] = "Alice"
        self.assertEqual(h._host_chat_singer_name("alice"), "Alice")
        h._chat_messages = [{"singer_key": "alice", "singer": "Alice B.", "id": 5}]
        self.assertEqual(h._host_chat_singer_name("alice"), "Alice B.")  # the text conversation's spelling wins
        self.assertEqual(h._host_chat_singer_name("zed"), "")


class WiringTests(unittest.TestCase):
    def test_buttons_reuse_the_room_dialogs_and_send_to_the_selected_singer(self):
        self.assertIn("self.chat_gif_button.clicked.connect(lambda: self._room_say_gif(deliver=self._host_chat_send_media))", SOURCE)
        self.assertIn("self._room_say_photo(deliver=self._host_chat_send_media, button=self.chat_photo_button)", SOURCE)
        i = SOURCE.index("def _host_chat_send_media")
        body = SOURCE[i:i + 2200]
        self.assertIn('"channel": "host"', body)
        self.assertIn('"singer_name": singer', body)

    def test_host_chat_items_never_count_as_everyone_messages(self):
        i = SOURCE.index("def _apply_room_list")
        body = SOURCE[i:i + 2500]
        self.assertIn('if self._room_messages[i].get("channel") == "host": continue', body)

    def test_transcript_is_clickable_html(self):
        self.assertIn("self.chat_transcript.anchorClicked.connect(self._on_room_anchor)", SOURCE)
        i = SOURCE.index("def _render_host_chat_transcript")
        self.assertIn("view.setHtml(", SOURCE[i:i + 2600])

    def test_the_room_view_and_host_chat_share_one_attachment_renderer(self):
        self.assertEqual(SOURCE.count("def _room_attachment_html"), 1)
        self.assertIn("body = text + self._room_attachment_html(m)", SOURCE)


if __name__ == "__main__":
    unittest.main()
