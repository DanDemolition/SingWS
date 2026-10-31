import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SHELL = os.environ.get("SINGWS_SHELL_BIN") or os.path.expanduser("~/.cache/singws-rust-target/debug/singws-shell")
needs = unittest.skipUnless(Path(SHELL).exists(), "needs a built singws-shell (QMAKE=... cargo build -p singws-shell)")


@needs
class MusicAndSettingsPanelsTests(unittest.TestCase):
    """The real Qt shell (offscreen) opened on the Music and Settings tabs over a throwaway profile: each must start, draw, and quit by itself."""

    def run_tab(self, tab, home, extra=()):
        shot = Path(home) / f"{tab}.png"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SINGWS_HOME=str(home), SINGWS_SHELL_CONFIG_DIR=str(Path(home) / "cfg"))
        p = subprocess.run([SHELL, "--tab", tab, "--snapshot", str(shot), "--snapshot-after", "2000", *extra], env=env, capture_output=True, text=True, timeout=90)
        return p, shot

    def make_home(self):
        d = tempfile.mkdtemp(prefix="shell_panels_")
        (Path(d) / "bg_playlist.json").write_text(json.dumps([{"path": "/m/a.mp3", "filename": "Adele - Hello.mp3", "title": "x"}, {"path": "/m/b.mp3", "filename": "Zara - Lush.flac", "title": "y"}]))
        (Path(d) / "settings.json").write_text(json.dumps({"bg_enabled": True, "bg_volume": 0.5, "api_key": "sk-SECRET-VALUE", "host_controls_pin": "9876"}))
        return d

    def test_the_music_tab_draws(self):
        d = self.make_home()
        p, shot = self.run_tab("music", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)

    def test_the_music_tab_copes_with_a_broken_and_a_missing_playlist(self):
        d = self.make_home()
        (Path(d) / "bg_playlist.json").write_text('[{"path": "/m/a.mp3"}]')  # no filename: the app itself fails to load this
        p, shot = self.run_tab("music", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        (Path(d) / "bg_playlist.json").unlink()
        p, shot = self.run_tab("music", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)

    def test_the_settings_tab_draws_and_the_process_never_prints_a_secret(self):
        d = self.make_home()
        p, shot = self.run_tab("settings", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)
        for leak in ("sk-SECRET-VALUE", "9876"):
            self.assertNotIn(leak, p.stdout + p.stderr)

    def test_the_ticker_tab_draws_the_audience_text_and_copes_with_a_broken_queue(self):
        d = self.make_home()
        (Path(d) / "queue.json").write_text(json.dumps([{"name": "Ann", "songs": [{"title": "x"}]}, {"name": "Skip", "skipped": True, "songs": [{}]}, {"name": "Bo", "songs": [{"title": "y"}]}]))
        (Path(d) / "settings.json").write_text(json.dumps({"ticker_custom_enabled": True, "ticker_custom_message": "Tip your host", "api_key": "sk-SECRET-VALUE"}))
        p, shot = self.run_tab("ticker", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)
        self.assertNotIn("sk-SECRET-VALUE", p.stdout + p.stderr)
        (Path(d) / "queue.json").write_text("{not json")
        p, shot = self.run_tab("ticker", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)
        (Path(d) / "queue.json").unlink()
        p, shot = self.run_tab("ticker", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])

    def test_the_waitlist_tab_draws_a_snapshot_and_copes_with_a_missing_or_broken_one(self):
        d = self.make_home()
        snap = {"version": 1, "pending_acceptance": [{"request_id": 9, "singer": "Gus", "artist": "Adele", "title": "Hello"}],
                "waiting": {"5": {"request_id": 5, "singer": "Ann", "artist": "Queen", "title": "Radio"}, "6": {"request_id": 6, "singer": "Bo", "artist": "A", "title": "B"}},
                "show_removed": False, "removed": {}, "local_ids": [6], "accepted_ids": [], "pending_accepted_ids": [], "first_time_names": ["Ann"]}
        path = Path(d) / "waitlist_snapshot.json"
        for label, write in (("snapshot", lambda: path.write_text(json.dumps(snap))), ("broken", lambda: path.write_text("{nope")), ("missing", lambda: path.unlink())):
            write()
            p, shot = self.run_tab("waitlist", d)
            self.assertEqual(p.returncode, 0, (label, p.stderr[-400:]))
            self.assertGreater(shot.stat().st_size, 5000, label)
            self.assertNotIn("Unable to assign", p.stderr, label)
            self.assertNotIn("TypeError", p.stderr, label)

    def test_the_status_diagnostics_and_now_playing_tabs_draw_a_snapshot_and_cope_with_a_missing_or_broken_one(self):
        d = self.make_home()
        import time
        snap = {"version": 1, "taken_at": int(time.time()), "settings": {"venue_name": "Joe's", "use_waiting_for_add": True, "api_key": "sk-SECRET-VALUE"}, "connection": "Connected",
                "network_configured": True, "last": {"sync": "12:03:04"}, "attention": {"1": {}}, "queue_rev": 4, "queue": [{"name": "Ann", "rotation_marker": True}], "total_secs": 600,
                "locked": False, "looping": True,
                "diagnostics": {"local_name": "MacBook Speakers", "screen_count": 2, "karafun_path": None, "output_host": True, "preview_host": True, "show_ticker": True, "qr_url": "https://x/", "bg_files": []}}
        path = Path(d) / "status_snapshot.json"
        snap["hero"] = {"kicker": "NOW SINGING", "singer": "Ann", "subtitle": "Radio Ga Ga \u00b7 Queen", "meta": "SC8671", "format": "ZIP", "remaining": "03:21"}
        for tab in ("status", "diagnostics", "nowplaying"):
            for label, write in (("snapshot", lambda: path.write_text(json.dumps(snap))), ("broken", lambda: path.write_text("{nope")), ("foreign", lambda: path.write_text('{"version": 9}')), ("missing", lambda: path.unlink())):
                write()
                p, shot = self.run_tab(tab, d)
                self.assertEqual(p.returncode, 0, (tab, label, p.stderr[-400:]))
                self.assertGreater(shot.stat().st_size, 5000, (tab, label))
                for bad in ("Unable to assign", "TypeError", "ReferenceError", "sk-SECRET-VALUE"):
                    self.assertNotIn(bad, p.stdout + p.stderr, (tab, label))

    def test_the_eq_tab_draws_and_copes_with_unreadable_settings(self):
        d = self.make_home()
        (Path(d) / "settings.json").write_text(json.dumps({"eq_bgm": [5, 4, 3, 1, -1, -1, 1, 3, 4, 5], "eq_karaoke": [0] * 10, "eq_karaoke_enabled": False, "api_key": "sk-SECRET-VALUE"}))
        p, shot = self.run_tab("eq", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(shot.stat().st_size, 5000)
        self.assertNotIn("sk-SECRET-VALUE", p.stdout + p.stderr)
        (Path(d) / "settings.json").write_text(json.dumps({"eq_bgm": [1, None, 3]}))  # the app gives up on both equalisers; the tab says so instead of drawing garbage
        p, shot = self.run_tab("eq", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        (Path(d) / "settings.json").unlink()
        p, shot = self.run_tab("eq", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])

    def test_the_tv_window_renders_the_rotation_and_survives_an_empty_or_broken_queue(self):
        d = self.make_home()
        songs = [{"song_info": f"/lib/{i}.mp4", "artist": f"Artist {i}", "title": f"Title {i}", "display_name": f"Artist {i} \u2022 Title {i}", "key": 0, "skipped": False, "request_uid": f"u{i}"} for i in range(12)]
        queue = [{"singer_id": f"s{i}", "name": f"Singer {i}", "songs": [songs[i]], "skipped": False, "has_sung": False, "round_sung": False, "rotation_marker": i == 0, "created_at": float(i)} for i in range(12)]
        (Path(d) / "queue.json").write_text(json.dumps(queue))
        tv = Path(d) / "tv.png"
        p, shot = self.run_tab("rotation", d, ("--tv", "1", "--now-singing", "Singer 0", "--tv-snapshot", str(tv)))
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(tv.stat().st_size, 20000, "the TV window drew its rows")
        self.assertGreater(shot.stat().st_size, 5000)
        (Path(d) / "queue.json").write_text("[]")
        tv2 = Path(d) / "tv2.png"
        p, shot = self.run_tab("rotation", d, ("--tv", "1", "--tv-snapshot", str(tv2)))
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertGreater(tv2.stat().st_size, 2000)
        self.assertLess(tv2.stat().st_size, tv.stat().st_size, "an empty queue draws far less than a full one")
        (Path(d) / "queue.json").write_text("not json")
        p, shot = self.run_tab("rotation", d, ("--tv", "1", "--tv-snapshot", str(Path(d) / "tv3.png")))
        self.assertEqual(p.returncode, 0, p.stderr[-400:])

    def test_the_numeric_tab_arguments_still_work(self):
        d = self.make_home()
        p, shot = self.run_tab("4", d)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])


if __name__ == "__main__":
    unittest.main()
