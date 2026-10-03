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
    from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QListWidget, QPushButton, QStackedWidget, QTabBar, QTextBrowser
    QT_OK = True
except Exception:  # pragma: no cover - environments without a working Qt
    QT_OK = False

METHODS = {'_room_chat_conn', '_update_room_tab_title', '_room_poll_tick', '_apply_room_list',
           '_room_display_name', '_render_room_chat', '_on_room_anchor', '_room_view_open', '_room_channel_counts',
           '_chat_tab_labels', '_on_chat_tab_changed', '_room_thread_keys', '_room_say_context', '_refresh_room_compose',
           '_room_say_send', '_room_attachment_html', '_host_chat_media_rows', '_host_chat_media_unread'}


@lru_cache(maxsize=1)
def namespace():
    tree = ast.parse(Path('0.2.18.1.py').read_text())
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    ns = {'re': re, 'time': time, 'quote': quote, 'unquote': unquote, 'Qt': Qt, 'QUrl': QUrl, 'QImage': QImage,
          '_v': lambda name: '#dddddd', '_diag': lambda *a: None,
          '_network_normalize_base_url': lambda u: u.rstrip('/'), 'requests': mock.Mock(),
          'threading': SimpleNamespace(Thread=lambda target=None, **kw: SimpleNamespace(start=lambda: target()))}
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
        host._render_chat_page = mock.Mock(); host._render_host_chat_transcript = mock.Mock()
        host._room_action = mock.Mock(); host._room_show_image = mock.Mock(); host._room_fetch_media = mock.Mock(); host._room_fetch_gif = mock.Mock()
        host._schedule_room_poll = mock.Mock()
        host._run_on_ui_thread = lambda fn: fn()
        host._ROOM_SAY_ERRORS = {'group_chat_disabled': 'Everyone chat is switched off.'}
        host._room_say_target = None
        host.room_say_target_label = QLabel(); host.room_say_input = QLineEdit(); host.room_say_send_button = QPushButton('Send')
        host.room_say_gif_button = QPushButton('GIF'); host.room_say_photo_button = QPushButton('Photo')
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

    def test_host_chat_items_are_not_room_messages(self):
        """A picture in a singer's Host chat arrives with the room rows but belongs to Host chat only."""
        host = self.make()
        host._apply_room_list({'messages': [msg(1)], 'mutes': [], 'settings': {}}, replace=True)          # baseline
        private = dict(msg(2, channel='host', frm='Alice', frm_key='alice', text='secret picture', kind='image', media_id=5), thread='alice')
        host._apply_room_list({'messages': [msg(1), private], 'mutes': [], 'settings': {}}, replace=True)
        self.assertEqual(host._room_new_by_channel, {'group': 0, 'dm': 0})
        host.room_filter_combo.setCurrentIndex(1); host._render_room_chat()
        self.assertNotIn('secret picture', host.room_view.toPlainText())
        host.room_filter_combo.setCurrentIndex(2); host._render_room_chat()
        self.assertNotIn('secret picture', host.room_view.toPlainText())
        host._render_chat_page.assert_called()                       # the Host chat page is refreshed instead

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

    def test_chat_tab_bar_is_not_elided_or_centred(self):
        """Regression: in the real app the labels were cut to 'Ever...' and the tabs sat mid-page."""
        source = Path('0.2.18.1.py').read_text()
        build = source[source.index('def _build_chat_page'):source.index('def _chat_tab_bar_css')]
        self.assertIn('setElideMode(Qt.TextElideMode.ElideNone)', build)
        self.assertIn('layout.addWidget(self.chat_tab_bar, 0, Qt.AlignmentFlag.AlignLeft)', build)
        self.assertNotIn('PointingHandCursor', build)          # the app deliberately uses the arrow cursor

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


def host_msg(mid, channel='group', thread='group', text='hello', to_key=''):
    m = msg(mid, channel=channel, frm='DJ Dan', frm_key='@host', to_key=to_key, text=text)
    m['thread'] = thread; m['host'] = True
    return m


@unittest.skipUnless(QT_OK, "PyQt6 not usable here")
class HostSpeaksTests(unittest.TestCase):
    """The host can speak in Everyone, and interject in a private conversation (both singers see it)."""
    make = RoomTabTests.make
    feed = RoomTabTests.feed
    text = RoomTabTests.text
    html = RoomTabTests.html

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.requests = namespace()['requests']
        self.requests.reset_mock()
        self.requests.post.return_value = mock.Mock(ok=True, content=b'{}', status_code=200, json=lambda: {'ok': True, 'id': 7})

    def dm(self, mid, frm, frm_key, to_key, text='psst'):
        m = msg(mid, channel='dm', frm=frm, frm_key=frm_key, to_key=to_key, text=text)
        m['thread'] = '|'.join(sorted([frm_key, to_key]))
        return m

    def test_private_messages_offer_interject_for_their_two_singers(self):
        host = self.make()
        self.feed(host, [self.dm(1, 'Alice', 'alice', 'bob'), msg(2, text='group one')])
        host.chat_tab_bar.setCurrentIndex(1); host._render_room_chat()
        html = self.html(host)
        self.assertIn('room://say/alice/bob', html)
        host.chat_tab_bar.setCurrentIndex(0); host._render_room_chat()
        self.assertNotIn('room://say/', self.html(host))               # nothing to interject into on Everyone

    def test_host_messages_are_tagged_and_never_offer_mute(self):
        host = self.make()
        self.feed(host, [self.dm(1, 'Alice', 'alice', 'bob'), host_msg(2, 'dm', 'alice|bob', 'be nice'), host_msg(3, text='welcome')])
        host.chat_tab_bar.setCurrentIndex(1); host._render_room_chat()
        text, html = self.text(host), self.html(host)
        self.assertIn('HOST', text); self.assertIn('PRIVATE ↔ Alice / Bob', text); self.assertIn('be nice', text)
        self.assertNotIn('room://mute/%40host', html); self.assertNotIn('room://mute/@host', html)
        self.assertIn('room://say/alice/bob', html)                    # a host message can be replied to in the same thread

    def test_own_messages_do_not_count_as_new(self):
        host = self.make()
        self.feed(host, [])                                            # baseline load
        self.feed(host, [host_msg(1, text='mine'), msg(2, text='theirs')], replace=False)
        self.assertEqual(host._room_new_by_channel['group'], 1)

    def test_compose_context_follows_the_tab_and_the_picked_conversation(self):
        host = self.make()
        host.chat_tab_bar.setCurrentIndex(0)
        self.assertIn('Speaking to everyone', host.room_say_target_label.text())
        self.assertTrue(host.room_say_input.isEnabled())
        host.chat_tab_bar.setCurrentIndex(1)
        self.assertFalse(host.room_say_input.isEnabled()); self.assertIn('Interject', host.room_say_target_label.text())
        self.feed(host, [self.dm(1, 'Alice', 'alice', 'bob')])
        host._on_room_anchor(QUrl('room://say/alice/bob'))
        self.assertTrue(host.room_say_input.isEnabled())
        self.assertIn('Interjecting in Alice ↔ Bob', host.room_say_target_label.text())
        host.settings['host_chat_name'] = 'DJ Dan'; host._refresh_room_compose()
        self.assertIn('as “DJ Dan”', host.room_say_target_label.text())

    def test_send_to_everyone(self):
        host = self.make(); host.settings['host_chat_name'] = 'DJ Dan'
        host.chat_tab_bar.setCurrentIndex(0)
        host.room_say_input.setText('Welcome! 🎤🔥'); host._room_say_send()
        args, kwargs = self.requests.post.call_args
        self.assertTrue(args[0].endswith('/api/v1/host_chat_moderation.php'))
        data = kwargs['data']
        self.assertEqual((data['action'], data['channel'], data['message'], data['host_name']), ('say', 'group', 'Welcome! 🎤🔥', 'DJ Dan'))
        self.assertEqual(kwargs['headers']['X-API-Key'], 'k')
        self.assertEqual(host.room_say_input.text(), '')               # cleared after a successful send
        host._schedule_room_poll.assert_called_with(True)

    def test_interject_in_a_private_conversation(self):
        host = self.make()
        host.chat_tab_bar.setCurrentIndex(1); host._on_room_anchor(QUrl('room://say/alice/bob'))
        host.room_say_input.setText('keep it clean 😄'); host._room_say_send()
        data = self.requests.post.call_args.kwargs['data']
        self.assertEqual((data['channel'], data['a'], data['b'], data['message']), ('dm', 'alice', 'bob', 'keep it clean 😄'))

    def test_gif_and_picture_go_with_the_message(self):
        host = self.make()
        host.chat_tab_bar.setCurrentIndex(0)
        host._room_say_send(gif_id='abc12345')
        self.assertEqual(self.requests.post.call_args.kwargs['data']['gif_id'], 'abc12345')
        host._room_say_send(media_id=42)
        self.assertEqual(self.requests.post.call_args.kwargs['data']['media_id'], 42)

    def test_nothing_is_sent_when_empty_or_no_conversation_is_picked(self):
        host = self.make()
        host.chat_tab_bar.setCurrentIndex(0); host.room_say_input.setText('   '); host._room_say_send()
        host.chat_tab_bar.setCurrentIndex(1); host.room_say_input.setText('hello'); host._room_say_send()
        self.requests.post.assert_not_called()

    def test_a_refused_message_is_explained_and_kept(self):
        host = self.make()
        self.requests.post.return_value = mock.Mock(ok=False, content=b'{}', status_code=400,
                                                    json=lambda: {'ok': False, 'error': 'group_chat_disabled'})
        host.chat_tab_bar.setCurrentIndex(0); host.room_say_input.setText('hi'); host._room_say_send()
        self.assertEqual(host.room_say_input.text(), 'hi')
        self.assertIn('Everyone chat is switched off.', host._show_processing_notification.call_args.args[0])

    def test_the_chat_name_setting_exists_and_is_saved_from_the_network_dialog(self):
        source = Path('0.2.18.1.py').read_text()
        self.assertIn('"host_chat_name": "Host"', source)
        self.assertIn('self.settings["host_chat_name"] = (chat_name_edit.text().strip() or "Host")[:24]', source)
        self.assertIn('QLabel("Chat name:")', source)


if __name__ == '__main__':
    unittest.main()
