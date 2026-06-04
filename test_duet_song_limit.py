import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_duet_limit", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {"queue_mode": "rotation", "karaoke_normalize_enabled": False, "limit_pending_max": 2}
    app.queue = []
    app.update_queue_display = lambda: None
    app.save_data = lambda: None
    app._select_queue_singer_for_host = lambda idx: None
    app.lookup_display_name = lambda song_path, artist_title_only=False: "Artist • Title"
    app._get_duration_secs = lambda song_path: 180
    return app


def track(title="Title"):
    return {"artist": "Artist", "title": title, "display": f"Artist • {title}", "duration": 180}


def add(app, singer, title):
    return app._add_song_to_queue(singer, (f"/tmp/{title}.mp3", 0), track=track(title), remote_meta={"request_id": abs(hash((singer, title))) % 100000})


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


if __name__ == "__main__":
    unittest.main()
