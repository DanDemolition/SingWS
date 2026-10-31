"""Stage E screens pass 2026-10-30, shell scenarios: the Library tab says what the app's search rows say, and the Now playing tab draws the hero card from the snapshot.

The real Qt shell (offscreen) is started over a throwaway profile: a song index built by `song_index`, and a `status_snapshot.json`. The Library rows are compared with the
REAL `KaraokeApp._build_search_row_text` over the rows `song_index.search_songs` returns; the Now playing view is compared with the hero labels the real
`_apply_redesign_hero_text` and `_hero_remaining_text` produce."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path

SHELL = os.environ.get("SINGWS_SHELL_BIN") or os.path.expanduser("~/.cache/singws-rust-target/debug/singws-shell")
needs = unittest.skipUnless(Path(SHELL).exists(), "needs a built singws-shell (QMAKE=... cargo build --release -p singws-shell)")

TRACKS = [
    {"path": "/lib/SC8671-01.zip", "artist": "Queen", "title": "Radio Ga Ga", "discid": "SC8671", "duration": 212, "type": "mp3g"},
    {"path": "/lib/queen-bicycle.mp4", "artist": "Queen", "title": "Bicycle Race", "disc_id": "KV-12", "duration": "3:45", "type": "Video"},
    {"path": "/lib/queen-flac.flac", "artist": "Queen", "title": "Flac Song", "duration": 0},
    {"path": "/lib/karafun-bohemian", "artist": "Queen", "title": "Bohemian Rhapsody", "provider": "karafun_streaming", "type": "online", "duration": 355},
    {"path": "/lib/abba.cdg", "artist": "Abba", "title": "Dancing Queen", "discid": "SF1", "duration": 231},
]


@needs
class ShellScreens(unittest.TestCase):
    def run_shell(self, home, *args):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SINGWS_HOME=str(home), SINGWS_DB=str(Path(home) / "singws.db"), SINGWS_SHELL_CONFIG_DIR=str(Path(home) / "cfg"))
        p = subprocess.run([SHELL, *args], env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        for bad in ("Unable to assign", "TypeError", "ReferenceError"):
            self.assertNotIn(bad, p.stdout + p.stderr)
        return p

    def test_the_library_tab_shows_the_apps_search_row_text(self):
        import song_index
        import test_show_critical_regressions as base
        singws = base.load_main_module()
        with tempfile.TemporaryDirectory() as home:
            Path(home, "tracks.json").write_text(json.dumps(TRACKS))
            db = Path(home) / "singws.db"
            song_index._clear_read_caches()
            song_index.rebuild_from_tracks_json(Path(home) / "tracks.json", db, verbose=False)
            f = types.SimpleNamespace(results_list=None)
            f._two_col_text = lambda lw, left, right, *a, **k: "unused"      # only the pixel fitting is stubbed: left / right / tooltip are compared
            for n in ("_build_search_row_text", "_track_media_badge", "_compact_media_badge", "_hero_media_badge_text", "_fmt_right_time", "_fmt_mmss"):
                setattr(f, n, types.MethodType(getattr(singws.KaraokeApp, n), f))
            for query in ("queen", "dancing queen", "zzzz"):
                out = Path(home) / "rows.json"
                out.unlink(missing_ok=True)
                self.run_shell(home, "--tab", "library", "--query", query, "--dump-library", str(out), "--snapshot", str(Path(home) / "lib.png"), "--snapshot-after", "3000")
                shown = json.loads(out.read_text())
                want = []
                for r in song_index.search_songs(query, dbfile=db, limit=500):
                    x = f._build_search_row_text(r)
                    want.append({"left": x[2], "right": x[3], "tooltip": x[1]})
                self.assertEqual([{k: r[k] for k in ("left", "right", "tooltip")} for r in shown], want, query)
                if query == "queen":
                    self.assertGreaterEqual(len(shown), 5)
                    self.assertTrue(any("KaraFun ONLINE" in r["right"] for r in shown), "a provider row carries its badge")
                    self.assertTrue(any(r["right"].startswith("SC8671  ZIP") or r["right"].startswith("SC8671  MP3+G") for r in shown))

    def test_the_library_tab_still_draws_without_an_index(self):
        with tempfile.TemporaryDirectory() as home:
            out = Path(home) / "rows.json"
            self.run_shell(home, "--tab", "library", "--query", "queen", "--dump-library", str(out), "--snapshot", str(Path(home) / "lib.png"), "--snapshot-after", "3000")
            self.assertEqual(json.loads(out.read_text()), [])

    def snapshot(self, hero):
        snap = {"version": 1, "taken_at": int(time.time()), "settings": {"venue_name": "Joe's", "api_key": "sk-SECRET-VALUE"}, "connection": "Connected", "network_configured": True,
                "last": {}, "attention": None, "queue_rev": 1, "queue": [{"name": "Ann", "rotation_marker": True}], "total_secs": 200, "locked": False, "looping": False,
                "diagnostics": {"local_name": "Speakers", "screen_count": 2, "karafun_path": None, "output_host": True, "preview_host": True, "show_ticker": True, "qr_url": "https://x/", "bg_files": []}}
        if hero is not None:
            snap["hero"] = hero
        return snap

    def test_the_now_playing_tab_draws_the_hero_card_from_the_snapshot(self):
        import test_show_critical_regressions as base
        singws = base.load_main_module()

        class Lab:
            def __init__(self, t=""):
                self.t = t

            def text(self):
                return self.t

            def setText(self, t):
                self.t = t

            def styleSheet(self):
                return ""

            def setStyleSheet(self, _css):
                pass

        app = types.SimpleNamespace(rotation_summary_subtitle=Lab(), rotation_summary_meta=Lab(), karaoke_remaining_label=Lab("03:21"), _subtitle_css_off="", _hero_format_text="")
        app._ui_redesign_on = lambda: True
        for n in ("_apply_redesign_hero_text", "_hero_media_badge_text", "_hero_remaining_text"):
            setattr(app, n, types.MethodType(getattr(singws.KaraokeApp, n), app))
        app._apply_redesign_hero_text(" Radio Ga Ga ", "Queen", " SC8671 ", "/lib/SC8671-01.zip")
        hero = {"kicker": "NOW SINGING", "singer": "Ann", "subtitle": app.rotation_summary_subtitle.text(), "meta": app.rotation_summary_meta.text(),
                "format": app._hero_format_text, "remaining": app.karaoke_remaining_label.text()}
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / "status_snapshot.json"
            out = Path(home) / "view.json"
            path.write_text(json.dumps(self.snapshot(hero)))
            self.run_shell(home, "--tab", "nowplaying", "--dump-nowplaying", str(out), "--snapshot", str(Path(home) / "np.png"), "--snapshot-after", "3000")
            view = json.loads(out.read_text())
            self.assertEqual(view["hero"], {"kicker": "NOW SINGING", "singer": "Ann", "subtitle": app.rotation_summary_subtitle.text(), "meta": "SC8671", "format": "ZIP",
                                            "remaining": app._hero_remaining_text()})
            self.assertEqual(view["hero"]["remaining"], "-03:21")
            self.assertTrue((Path(home) / "np.png").stat().st_size > 5000)
            self.assertNotIn("sk-SECRET-VALUE", out.read_text())
            for label, write in (("older snapshot", lambda: path.write_text(json.dumps(self.snapshot(None)))), ("broken", lambda: path.write_text("{nope")), ("missing", lambda: path.unlink())):
                write()
                out.unlink(missing_ok=True)
                self.run_shell(home, "--tab", "nowplaying", "--dump-nowplaying", str(out), "--snapshot", str(Path(home) / "np2.png"), "--snapshot-after", "3000")
                self.assertIsNone(json.loads(out.read_text()).get("hero"), label)


if __name__ == "__main__":
    unittest.main()
