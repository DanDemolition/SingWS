import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_duet_limit", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _UIStub:
    """No-op stand-in for the host's Qt widgets touched by operator-driven adds."""

    def clear(self):
        pass

    def findText(self, text):
        return 0

    def setCurrentIndex(self, index):
        pass


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {"queue_mode": "rotation", "karaoke_normalize_enabled": False, "limit_pending_max": 2}
    app.queue = []
    app._waiting_for_add_requests = {}
    app._deferred_remote_adds = []
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app._select_queue_singer_for_host = lambda idx: None
    app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
    app._get_duration_secs = lambda song_path: 180
    # Widgets only touched by operator-driven (non-remote) adds.
    app.singer_input = _UIStub()
    app.key_selector = _UIStub()
    return app


def track(title="Title"):
    return {"artist": "Artist", "title": title, "display": f"Artist • {title}", "duration": 180}


def add(app, singer, title):
    return app._add_song_to_queue(singer, (f"/tmp/{title}.mp3", 0), track=track(title), remote_meta={"request_id": abs(hash((singer, title))) % 100000})


def add_host(app, singer, title):
    """Operator-driven add from the host controls (remote_meta=None)."""
    return app._add_song_to_queue(singer, (f"/tmp/{title}.mp3", 0), track=track(title), remote_meta=None)


class DuetSongLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_singer_with_two_solo_songs_cannot_add_duet(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, "Singer A", "two"))
        self.assertFalse(add(app, "Singer A & Singer B", "duet"))

    def test_singer_with_one_solo_can_add_duet(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, "Singer A & Singer B", "duet"))
        counts = app._active_song_counts_by_singer()
        self.assertEqual(counts[app._queue_limit_name_key("Singer A")], 2)
        self.assertEqual(counts[app._queue_limit_name_key("Singer B")], 1)

    def test_singer_with_solo_and_duet_cannot_add_another_song(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, "Singer A & Singer B", "duet"))
        self.assertFalse(add(app, "Singer A", "third"))

    def test_second_duet_singer_at_limit_blocks_duet(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer B", "one"))
        self.assertTrue(add(app, "Singer B", "two"))
        self.assertFalse(add(app, "Singer A & Singer B", "duet"))

    def test_case_and_spacing_do_not_bypass_limit(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, " singer   a ", "two"))
        self.assertFalse(add(app, "SINGER A", "three"))

    def test_waitlisted_song_does_not_count_toward_remote_limit(self):
        app = make_app(self.singws)
        app._waiting_for_add_requests = {
            7001: {"request_id": 7001, "singer": "Singer A", "artist": "Artist", "title": "Pending"}
        }

        self.assertTrue(add(app, "Singer A", "second"))
        self.assertTrue(add(app, "Singer A", "third"))
        self.assertFalse(add(app, "Singer A", "fourth"))

    def test_active_and_waitlisted_song_blocks_only_at_active_limit(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "active"))
        app._waiting_for_add_requests = {
            7002: {"request_id": 7002, "singer": "Singer A", "artist": "Artist", "title": "Pending"}
        }

        self.assertTrue(add(app, "Singer A", "third"))
        self.assertFalse(add(app, "Singer A", "fourth"))

    def test_two_waitlisted_songs_do_not_inflate_remote_limit(self):
        app = make_app(self.singws)
        app._waiting_for_add_requests = {
            7003: {"request_id": 7003, "singer": "Singer A", "artist": "Artist", "title": "Pending 1"},
            7004: {"request_id": 7004, "singer": "Singer A", "artist": "Artist", "title": "Pending 2"},
        }

        self.assertTrue(add(app, "Singer A", "third"))
        self.assertTrue(add(app, "Singer A", "fourth"))
        self.assertFalse(add(app, "Singer A", "fifth"))


class HostBypassSongLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_host_can_add_third_song_over_limit(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))      # web request
        self.assertTrue(add(app, "Singer A", "two"))      # web request -> at limit
        self.assertFalse(add(app, "Singer A", "three"))   # web request still blocked
        self.assertTrue(add_host(app, "Singer A", "three"))  # host/admin bypass
        counts = app._active_song_counts_by_singer()
        self.assertEqual(counts[app._queue_limit_name_key("Singer A")], 3)

    def test_web_request_still_blocked_after_host_bypass(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, "Singer A", "two"))
        self.assertTrue(add_host(app, "Singer A", "three"))  # host pushes past the cap
        # Public submissions remain capped even once the singer is over the limit.
        self.assertFalse(add(app, "Singer A", "four"))

    def test_host_can_add_duet_over_limit(self):
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, "Singer A", "two"))
        # Singer B is at the cap too; a remote duet would be rejected.
        self.assertTrue(add(app, "Singer B", "one"))
        self.assertTrue(add(app, "Singer B", "two"))
        self.assertFalse(add(app, "Singer A & Singer B", "duet"))
        # Host can still add the duet, counting against both singers past the cap.
        self.assertTrue(add_host(app, "Singer A & Singer B", "duet"))
        counts = app._active_song_counts_by_singer()
        self.assertEqual(counts[app._queue_limit_name_key("Singer A")], 3)
        self.assertEqual(counts[app._queue_limit_name_key("Singer B")], 3)

    def test_host_added_song_persists_in_queue(self):
        # Nothing re-applies the limit after insertion, so host-added songs survive.
        app = make_app(self.singws)
        self.assertTrue(add(app, "Singer A", "one"))
        self.assertTrue(add(app, "Singer A", "two"))
        self.assertTrue(add_host(app, "Singer A", "three"))
        song_titles = []
        for singer in app.queue:
            if app._queue_limit_name_key(singer.get("name", "")) == app._queue_limit_name_key("Singer A"):
                song_titles = [s.get("title") or s.get("display") for s in singer.get("songs", [])]
        self.assertEqual(len(song_titles), 3)

    def test_host_can_bypass_limit_with_waitlisted_songs(self):
        app = make_app(self.singws)
        app._waiting_for_add_requests = {
            7101: {"request_id": 7101, "singer": "Singer A", "artist": "Artist", "title": "Pending 1"},
            7102: {"request_id": 7102, "singer": "Singer A", "artist": "Artist", "title": "Pending 2"},
        }

        self.assertTrue(add(app, "Singer A", "remote-third"))
        self.assertTrue(add(app, "Singer A", "remote-fourth"))
        self.assertFalse(add(app, "Singer A", "remote-fifth"))
        self.assertTrue(add_host(app, "Singer A", "host-fifth"))


if __name__ == "__main__":
    unittest.main()
