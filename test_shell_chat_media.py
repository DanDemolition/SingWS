"""`shell_chat_media` (the read-only `chat.media` command): a thumbnail of a chat picture / GIF for the shell, from the app's own copy.

Real code under test: `shell_chat_media`, `_shell_chat_summary`. The room-media downloads and the relay are stand-ins; no network, no files."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import base64
import random
import types
import unittest
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QColor, QImage

import test_shell_chat_summary as ts


def png_uri(w=100, h=60):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#336699"))
    buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly); img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode("ascii")


def _method(name):
    h = ts.ta.AddHarness(ts.base.random_queue(random.Random(1)))
    return getattr(h.m.KaraokeApp, name)


def app(room):
    a = ts.app(room=room)
    a.shell_chat_media = types.MethodType(_method("shell_chat_media"), a)
    a._SHELL_CHAT_MEDIA_MAX_URI = 60_000
    a._room_media = {}
    a._room_media_thumb = {}
    a.fetched = []
    a._room_fetch_media = lambda i: a.fetched.append(("photo", i))
    a._room_fetch_gif = lambda g, u: a.fetched.append(("gif", g, u))
    a.sent = []
    a.host_relay_worker = types.SimpleNamespace(send_json=a.sent.append, is_connected=lambda: True)
    return a


PHOTO = ts.gm(1, "look", kind="image", media_id=77)
GIF = ts.gm(2, "", kind="gif", gif={"id": "g1", "preview": "https://media.giphy.com/x.gif", "full": "https://media.giphy.com/y.gif"})


class MediaTests(unittest.TestCase):
    def test_a_downloaded_photo_goes_out_as_a_small_jpeg(self):
        a = app([PHOTO])
        img = QImage(); img.loadFromData(base64.b64decode(png_uri(800, 600).split(",", 1)[1]))
        a._room_media[77] = img
        self.assertEqual(a.shell_chat_media("photo:77"), (True, "sent"))
        msg = a.sent[0]
        self.assertEqual((msg["type"], msg["ref"]), ("chat_media", "photo:77"))
        self.assertTrue(msg["uri"].startswith("data:image/jpeg;base64,"))
        out = QImage(); out.loadFromData(base64.b64decode(msg["uri"].split(",", 1)[1]))
        self.assertEqual(out.width(), 240, "scaled down")
        self.assertLess(len(msg["uri"]), 60_000)

    def test_a_picture_not_downloaded_yet_is_fetched_and_answered_pending(self):
        a = app([PHOTO])
        self.assertEqual(a.shell_chat_media("photo:77"), (True, "loading"))
        self.assertEqual(a.fetched, [("photo", 77)])
        self.assertEqual(a.sent, [{"type": "chat_media", "ref": "photo:77", "pending": True}])

    def test_a_gif_uses_the_still_the_chat_page_already_holds_or_asks_for_it(self):
        a = app([GIF])
        self.assertEqual(a.shell_chat_media("gif:g1"), (True, "loading"))
        self.assertEqual(a.fetched, [("gif", "g1", "https://media.giphy.com/x.gif")])
        a._room_media_thumb["gif:g1"] = png_uri(200, 120)
        a.sent.clear()
        self.assertEqual(a.shell_chat_media("gif:g1"), (True, "sent"))
        self.assertTrue(a.sent[0]["uri"].startswith("data:image/jpeg;base64,"))

    def test_only_refs_the_summary_shows_are_served(self):
        a = app([PHOTO, GIF])
        a._room_media[5] = QImage(10, 10, QImage.Format.Format_RGB32)
        for bad in ("photo:5", "photo:77 ", "gif:other", "", None, 77, "file:/etc/passwd", "photo:../1"):
            self.assertEqual(a.shell_chat_media(bad), (False, "unknown_media"), bad)
        self.assertEqual(a.fetched, [])
        self.assertEqual(a.sent, [])
        removed = dict(PHOTO, id=9, media_id=88, status="removed")
        self.assertEqual(app([removed]).shell_chat_media("photo:88"), (False, "unknown_media"), "a removed picture is never served")

    def test_an_oversized_thumbnail_is_refused(self):
        a = app([PHOTO])
        a._room_media[77] = QImage(240, 240, QImage.Format.Format_RGB32)
        a._SHELL_CHAT_MEDIA_MAX_URI = 100
        self.assertEqual(a.shell_chat_media("photo:77"), (False, "media_too_big"))
        self.assertEqual(a.sent, [])


class WiringTests(unittest.TestCase):
    def test_the_command_is_read_only_and_listed(self):
        src = Path(ts.__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"settings.set", "chat.say", "chat.media")', src)
        i = src.index('if action == "chat.media":')
        block = src[i:src.index('if action == "chat.say":', i)]
        self.assertIn('"needs_relay"', block)
        self.assertNotIn("dry_run", block, "read-only: it answers the same in dry_run and apply")


if __name__ == "__main__":
    unittest.main()
