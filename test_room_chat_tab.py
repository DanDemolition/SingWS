"""Host app Room chat tab: rendering, filters, moderation links, alerts. Real Qt widgets (offscreen)."""
import ast
import os
import re
import sys
import time
import unittest
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote, unquote

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QImage, QPixmap
    from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QListWidget, QStackedWidget, QTabBar, QTextBrowser
    QT_OK = True
except Exception:  # pragma: no cover - environments without a working Qt
    QT_OK = False

METHODS = {'_room_chat_conn', '_update_room_tab_title', '_room_poll_tick', '_apply_room_list',
           '_room_display_name', '_render_room_chat', '_on_room_anchor', '_room_view_open', '_room_channel_counts',
           '_chat_tab_labels', '_on_chat_tab_changed'}


@lru_cache(maxsize=1)
def namespace():
    tree = ast.parse(Path('0.2.18.1.py').read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    ns = {'re': re, 'time': time, 'quote': quote, 'unquote': unquote, 'Qt': Qt, 'QUrl': QUrl, 'QImage': QImage,
          '_v': lambda name: '#dddddd', '_diag': lambda *a: None,
          '_network_normalize_base_url': lambda u: u.rstrip('/')}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'room-tab-test', 'exec'), ns)
    return ns


def msg(mid, channel='group', frm='Alice', frm_key='alice', to_key='', text='hi', status='visible', flag='', reports=0,
        kind='text', media_id=0, created=1_800_000_000):
    return {'id': mid, 'channel': channel, 'thread': 'group', 'from': frm, 'from_key': frm_key, 'to_key': to_key,
            'message': text, 'status': status, 'flag': flag, 'reports': reports, 'kind': kind, 'media_id': media_id,
            'created_at': created}


@unittest.skipUnless(QT_OK, "PyQt6 not usable here")
class RoomTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self):
        ns = namespace()
        host = SimpleNamespace()
        for name in METHODS:
            setattr(host, name, ns[name].__get__(host) if hasattr(ns[name], '__get__') else None)
        host.settings = {'base_url': 'https://x.test', 'user': 'wsk', 'api_key': 'k'}
        host.room_status_label = QLabel(); host.room_mutes_label = QLabel()
        host.room_filter_combo = QComboBox(); host.room_filter_combo.addItems(["Everything", "Group room", "Private messages", "Needs attention"])
        host.room_search_input = QLineEdit(); host.room_view = QTextBrowser()
        host._ROOM_TAB_CHANNELS = {0: 'group', 1: 'dm'}
        host.chat_tab_bar = QTabBar()
        for label in ('Everyone', 'Private', 'Host chat'): host.chat_tab_bar.addTab(label)
        host.chat_stack = QStackedWidget(); host.chat_stack.addWidget(QLabel()); host.chat_stack.addWidget(QLabel())
        host._chat_messages = []
        host._room_new_by_channel = {'group': 0, 'dm': 0}
        host._room_messages = {}; host._room_mutes = []; host._room_settings = {}; host._room_last_id = 0
        host._room_poll_inflight = False; host._room_generation = 0; host._room_tick = 0
        host._room_attention_ids = set(); host._room_notified = set(); host._room_seen_ids = set(); host._room_unread = 0
        host._room_media = {}; host._room_media_thumb = {}; host._room_gif_full = {}; host._room_media_pending = set(); host._room_reachable = True
        host._room_baseline_done = False
        host._update_chat_nav_state = mock.Mock(); host._show_processing_notification = mock.Mock()
        host._room_action = mock.Mock(); host._room_show_image = mock.Mock(); host._room_fetch_media = mock.Mock(); host._room_fetch_gif = mock.Mock()
        host._schedule_room_poll = mock.Mock()
        host.chat_tab_bar.currentChanged.connect(lambda i: host._on_chat_tab_changed(i))
        host.chat_tab_bar.setCurrentIndex(2)          # start on Host chat, so room messages count as unseen
        return host

    def feed(self, host, items, mutes=None, settings=None, replace=True):
        host._apply_room_list({'messages': items, 'mutes': mutes or [],
                               'settings': settings or {'group_enabled': True, 'dm_enabled': True}}, replace=replace)

    def html(self, host):
        return host.room_view.toHtml()

    def text(self, host):
        return host.room_view.toPlainText()

    def test_shows_group_private_and_flags(self):
        host = self.make()
        self.feed(host, [
            msg(1, text='hello room 🎤'),
            msg(2, channel='dm', frm='Bob', frm_key='bob', to_key='alice', text='duet?'),
            msg(3, text='call me 555-1234', status='held', flag='phone_number', frm='Cara', frm_key='cara'),
            msg(4, text='rude thing', status='removed', frm='Dave', frm_key='dave'),
            msg(5, channel='dm', text='are you there', status='blocked', frm='Eve', frm_key='eve', to_key='bob'),
            msg(6, text='reported one', reports=2, frm='Fay', frm_key='fay'),
        ])
        text = self.text(host)
        self.assertIn('hello room 🎤', text)
        self.assertIn('PRIVATE → Alice', text)
        self.assertIn('HELD (phone_number)', text)
        self.assertIn('REMOVED', text)
        self.assertIn('rude thing', text)                      # the host still reads removed text
        self.assertIn('BLOCKED BY RECIPIENT', text)
        self.assertIn('REPORTED ×2', text)
        self.assertIn('are you there', text)                   # blocked private messages are visible to the host
        self.assertIn('6 messages', host.room_status_label.text())
        self.assertIn('2 need attention', host.room_status_label.text())

    def test_actions_are_offered_per_state(self):
        host = self.make()
        self.feed(host, [msg(1), msg(2, status='removed'), msg(3, status='held', flag='link'), msg(4, reports=1)])
        html = self.html(host)
        self.assertIn('room://remove/1', html); self.assertIn('room://restore/2', html)
        self.assertNotIn('room://remove/2', html); self.assertIn('room://approve/3', html)
        self.assertIn('room://resolve/4', html); self.assertIn('room://mute/alice/10', html)
        self.assertIn('room://mute/alice/0', html)

    def test_filters_and_search(self):
        host = self.make()
        self.feed(host, [msg(1, text='group one'), msg(2, channel='dm', text='secret one', frm='Bob', frm_key='bob', to_key='alice'),
                         msg(3, text='held one', status='held', flag='link', frm='Cara', frm_key='cara')])
        host.room_filter_combo.setCurrentIndex(1); host._render_room_chat()
        self.assertIn('group one', self.text(host)); self.assertNotIn('secret one', self.text(host))
        host.room_filter_combo.setCurrentIndex(2); host._render_room_chat()
        self.assertIn('secret one', self.text(host)); self.assertNotIn('group one', self.text(host))
        host.room_filter_combo.setCurrentIndex(3); host._render_room_chat()
        self.assertIn('held one', self.text(host)); self.assertNotIn('group one', self.text(host))
        host.room_filter_combo.setCurrentIndex(0); host.room_search_input.setText('cara'); host._render_room_chat()
        self.assertIn('held one', self.text(host)); self.assertNotIn('group one', self.text(host))
        host.room_search_input.setText('nothing matches'); host._render_room_chat()
        self.assertIn('No messages match this filter', self.text(host))

    def test_message_text_is_escaped(self):
        host = self.make()
        self.feed(host, [msg(1, text="<script>alert(1)</script> <b>bold</b>", frm="<i>x</i>")])
        html = self.html(host)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_mutes_are_listed_with_unmute_links(self):
        host = self.make()
        self.feed(host, [msg(1)], mutes=[{'singer_key': 'cara', 'singer': 'Cara', 'until': 0, 'reason': ''},
                                          {'singer_key': 'bob', 'singer': 'Bob', 'until': int(time.time()) + 600, 'reason': ''}])
        label = host.room_mutes_label.text()
        self.assertIn('Cara (for the night)', label); self.assertIn('Bob (until', label)
        self.assertIn('room://unmute/cara', label)
        self.feed(host, [msg(2, frm='Cara', frm_key='cara')], mutes=[{'singer_key': 'cara', 'singer': 'Cara', 'until': 0, 'reason': ''}], replace=False)
        self.assertIn('room://unmute/cara', self.html(host))   # per-message action flips to Unmute

    def test_anchor_clicks_call_the_right_actions(self):
        host = self.make()
        for href, expected in [
            ('room://remove/7', ('remove', {'id': 7})), ('room://restore/7', ('restore', {'id': 7})),
            ('room://approve/7', ('approve', {'id': 7})), ('room://resolve/7', ('resolve_reports', {'id': 7})),
            ('room://mute/some%20one/60', ('mute', {'singer': 'some one', 'minutes': 60})),
            ('room://mute/cara/0', ('mute', {'singer': 'cara', 'minutes': 0})),
            ('room://unmute/cara', ('unmute', {'singer': 'cara'})),
        ]:
            host._room_action.reset_mock()
            host._on_room_anchor(QUrl(href))
            host._room_action.assert_called_once_with(*expected)
        host._on_room_anchor(QUrl('room://image/9')); host._room_show_image.assert_called_once_with(9)

    def test_pictures_load_then_render_inline(self):
        host = self.make()
        self.feed(host, [msg(1, kind='image', media_id=5, text='look')])
        host._room_fetch_media.assert_called_with(5)
        self.assertIn('loading picture', self.text(host))
        img = QImage(400, 300, QImage.Format.Format_RGB32); img.fill(0xFF3366)
        host._room_media[5] = img
        host._render_room_chat()
        self.assertIn('data:image/png;base64,', self.html(host).replace('&quot;', '"') + host.room_view.document().toHtml())
        self.assertIn('room://image/5', self.html(host))

    def test_gifs_load_then_render_and_only_open_giphy_links(self):
        host = self.make()
        gif = {'id': 'abcde', 'title': 'x', 'url': 'https://media1.giphy.com/a.gif', 'preview': 'https://media1.giphy.com/p.gif',
               'full': 'https://media1.giphy.com/f.gif', 'w': 200, 'h': 100}
        evil = dict(gif, id='evil1', preview='https://evil.example/p.gif', full='https://evil.example/f.gif')
        m1 = msg(1, kind='gif', text=''); m1['gif'] = gif
        m2 = msg(2, kind='gif', text=''); m2['gif'] = evil
        self.feed(host, [m1, m2])
        host._room_fetch_gif.assert_called_once_with('abcde', 'https://media1.giphy.com/p.gif')
        self.assertIn('loading GIF', self.text(host))
        host._room_media_thumb['gif:abcde'] = 'data:image/png;base64,AAAA'
        host._render_room_chat()
        self.assertIn('room://gif/abcde', self.html(host))
        self.assertNotIn('evil', self.html(host))
        with mock.patch.dict(namespace(), {'QDesktopServices': mock.Mock()}) as _:
            opened = namespace()['QDesktopServices']
            host._on_room_anchor(QUrl('room://gif/abcde'))
            opened.openUrl.assert_called_once()
            opened.reset_mock()
            host._room_gif_full['bad'] = 'https://evil.example/f.gif'
            host._on_room_anchor(QUrl('room://gif/bad'))
            opened.openUrl.assert_not_called()

    def test_attention_alerts_once_and_badge_counts(self):
        host = self.make()
        self.feed(host, [msg(1)])
        self.feed(host, [msg(1), msg(2, status='held', flag='link', frm='Cara', frm_key='cara')])
        self.assertEqual(host._room_attention_ids, {2})
        self.assertEqual(host._show_processing_notification.call_count, 1)
        self.feed(host, [msg(1), msg(2, status='held', flag='link', frm='Cara', frm_key='cara')])
        self.assertEqual(host._show_processing_notification.call_count, 1)     # not repeated
        self.feed(host, [msg(1), msg(2, status='visible', frm='Cara', frm_key='cara')])
        self.assertEqual(host._room_attention_ids, set())
        self.assertNotIn('⚠', host.chat_tab_bar.tabText(0))

    def test_new_message_counter_ignores_the_first_load(self):
        host = self.make()
        self.feed(host, [msg(1), msg(2)])
        self.assertEqual(host._room_unread, 0)                                  # history is not "new"
        self.feed(host, [msg(1), msg(2), msg(3), msg(4)])
        self.assertEqual(host._room_unread, 2)
        self.assertIn('2 new', host.chat_tab_bar.tabText(0))                   # the two arrived in the group room
        self.assertNotIn('new', host.chat_tab_bar.tabText(1))
        host.chat_tab_bar.setCurrentIndex(1)                                   # look at Private: Everyone keeps its count
        self.assertIn('2 new', host.chat_tab_bar.tabText(0))
        host.chat_tab_bar.setCurrentIndex(0)
        self.assertEqual(host._room_unread, 0)
        self.assertNotIn('new', host.chat_tab_bar.tabText(0))

    def test_tabs_split_everyone_private_and_host_chat(self):
        host = self.make()
        self.feed(host, [msg(1, text='group one'), msg(2, channel='dm', text='secret one', frm='Bob', frm_key='bob', to_key='alice')])
        host.chat_tab_bar.setCurrentIndex(0)
        self.assertEqual(host.chat_stack.currentIndex(), 0)
        self.assertIn('group one', self.text(host)); self.assertNotIn('secret one', self.text(host))
        host.chat_tab_bar.setCurrentIndex(1)
        self.assertEqual(host.chat_stack.currentIndex(), 0)
        self.assertIn('secret one', self.text(host)); self.assertNotIn('group one', self.text(host))
        host.chat_tab_bar.setCurrentIndex(2)
        self.assertEqual(host.chat_stack.currentIndex(), 1)                    # the host's own one-to-one chat
        self.assertEqual(host.chat_tab_bar.tabText(2), 'Host chat')
        host._chat_messages = [{'direction': 'in', 'read': False}, {'direction': 'in', 'read': True}]
        self.assertEqual(host._chat_tab_labels()[2], 'Host chat (1 new)')

    def test_attention_badges_are_per_channel(self):
        host = self.make()
        self.feed(host, [msg(1), msg(2, channel='dm', status='held', flag='link', frm='Bob', frm_key='bob', to_key='alice')])
        self.assertNotIn('⚠', host.chat_tab_bar.tabText(0))
        self.assertIn('⚠ 1', host.chat_tab_bar.tabText(1))

    def test_incremental_updates_merge_by_id(self):
        host = self.make()
        self.feed(host, [msg(1, text='one'), msg(2, text='two')])
        self.feed(host, [msg(2, text='two', status='removed'), msg(3, text='three')], replace=False)
        self.assertEqual(sorted(host._room_messages), [1, 2, 3])
        self.assertEqual(host._room_messages[2]['status'], 'removed')
        self.assertEqual(host._room_last_id, 3)

    def test_room_off_message(self):
        host = self.make()
        self.feed(host, [], settings={'group_enabled': False, 'dm_enabled': False})
        self.assertIn('Room chat is OFF', host.room_status_label.text())


if __name__ == '__main__':
    unittest.main()
