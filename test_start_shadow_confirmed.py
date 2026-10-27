"""Stage A5 step 7: the payload published when a started song is CONFIRMED (the transport's started signal), shadowed by Rust.

The REAL `play_next_file` and its commit callback run over random singers (names with odd whitespace, no name, a duet display), command sources, durations, request
identifiers, keys, tempo presets and song shapes; the singer card / song path / identifiers / start context / countdown payload / pending performance record the app ends up
with must be exactly what Rust derives from the same inputs, with no MISMATCH, and each confirmed start is compared once.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import os
import random
import unittest
from pathlib import Path
from unittest import mock

import start_shadow
import test_show_critical_regressions as base

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target-a5/release/libsingws_dsp_ffi.dylib"))


class Counting(start_shadow.StartShadow):
    def __init__(self, log):
        super().__init__(log)
        self.compared = 0
        self.last_observed = None

    def check_confirmed(self, req, observed):
        r = super().check_confirmed(req, observed)
        if r is not None:
            self.compared += 1
            self.last_observed = dict(observed)
        return r


class ConfirmedWiring(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def confirm(self, *, artist="A", title="T", name="Dana", duet=None, song_info="/lib/a.mp3", duration=180, source="internal", remote_id=None, key=0, tempo=None, enabled=True, leftover=False):
        entry = {"request_uid": "rid-1", "song_info": song_info, "artist": artist, "title": title, "skipped": False, "key": key, "tempo_percent": tempo, "duet_display": duet, "duration": duration}
        if remote_id is not None:
            entry["remote_request_id"] = remote_id
        singer = {"name": name, "singer_id": "sid-1", "skipped": False, "songs": [entry]}
        app = self.playback_app([singer])
        app.settings = {"start_shadow": enabled}
        for stub in ("play_mp3", "play_mp4", "play_cdg_mp3_dual", "play_mp3g_zip"):
            app.__dict__.pop(stub, None)
        made = []

        def start(*_a, **_k):
            transport = type("T", (), {})()
            transport.started = type("S", (), {"connect": lambda _s, fn: made.append(fn)})()
            app.karaoke_transport = transport
            return True

        for n in ("play_mp3", "play_mp4", "play_cdg_mp3_dual", "play_mp3g_zip"):
            setattr(app, n, start)
        app.zip_cache = type("Z", (), {"cached_paths_if_ready": lambda _s, p: ("/t/x.cdg", "/t/x.mp3")})()
        app.is_mp3g_zip = lambda p: True
        app._is_external_karafun_entry = lambda e: False
        if leftover:
            app._pending_performance = {"remote_request_id": "old"}
        lines = []
        shadow = Counting(lambda text: lines.append(str(text)))
        self.singws._START_SHADOW = shadow
        with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))), mock.patch.object(self.singws.os.path, "exists", lambda p: True), \
                mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None):
            app.play_next_file(command_source=source)
            self.assertEqual(len(made), 1, "the start must be waiting for the transport's started signal")
            made[0]()
        return app, shadow, lines

    def test_known_payload(self):
        app, shadow, lines = self.confirm(name="Dana", duet="Dana & Lee", song_info=("/lib/a.cdg", "/lib/a.mp3"), duration=215, source="web", remote_id=42, key=-2, tempo=90)
        self.assertEqual([x for x in lines if "MISMATCH" in x], [], lines)
        seen = shadow.last_observed
        self.assertEqual((seen["singer_name"], seen["singer_display"], seen["eta_hold"], seen["song_path"]), ("Dana", "Dana & Lee", 215, "/lib/a.cdg"))
        self.assertEqual((seen["command_source"], seen["perf_singer_name"], seen["perf_request_id"], seen["perf_key"], seen["perf_tempo"]), ("web", "Dana", 42, -2, 90))
        self.assertEqual((seen["ctx_singer"], seen["countdown_singer"], seen["countdown_artist"]), ("Dana & Lee", "Dana & Lee", "A"))
        _app, shadow2, _lines = self.confirm(artist="  Abba ", title=" Waterloo\t")
        self.assertEqual((shadow2.last_observed["countdown_artist"], shadow2.last_observed["countdown_title"]), ("Abba", "Waterloo"))
        self.assertEqual(shadow.compared, 1)
        self.assertEqual(app._pending_performance["song_path"], "/lib/a.cdg")

    def test_random_confirmed_starts_agree_with_rust(self):
        rng = random.Random(20261030)
        names = ["Dana", "", None, "  Lee  ", "Zoë", "A • B", " ", "\u001cX\u001f", "日本"]
        duets = [None, None, "", "Dana & Lee", " ", "The Duo"]
        infos = ["/lib/a.mp3", "/lib/a.mp4", "/lib/a.zip", "/lib/a.cdg", ("/lib/a.cdg", "/lib/a.mp3"), ("/lib/a.mp4", "/lib/o.mp4"), ["/lib/a.mp3", 0]]
        sources = ["internal", "manual_idle", "zip_resume", "web", "", None, "automatic:song_end"]
        seen_none_name = seen_duet = seen_empty = 0
        for case in range(500):
            name, duet = rng.choice(names), rng.choice(duets)
            info = rng.choice(infos)
            pieces = ["A", " Queen ", "", "\u001cX\u001f", "A • B", "  ", "x\u00a0", "T\t"]
            kw = dict(artist=rng.choice(pieces), title=rng.choice(pieces), name=name, duet=duet, song_info=info, duration=rng.choice([0, 1, 59, 180, 3601]), source=rng.choice(sources), remote_id=rng.choice([None, 7, "r-9", 0]),
                      key=rng.choice([0, 3, -4, None]), tempo=rng.choice([None, 80, 100, 125]))
            app, shadow, lines = self.confirm(**kw)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, kw))
            self.assertEqual(shadow.compared, 1, (case, kw, lines))
            seen_none_name += name is None
            seen_duet += bool(duet)
            seen_empty += (name == "" and not duet)
        self.assertGreater(seen_none_name, 20)
        self.assertGreater(seen_duet, 100)
        self.assertGreater(seen_empty, 5)

    def test_a_nameless_singer_is_recorded_under_the_duet_display_or_no_name(self):
        app, shadow, lines = self.confirm(name="", duet="The Duo")
        self.assertEqual(app._pending_performance["singer_name"], "The Duo")
        app, shadow, lines = self.confirm(name="", duet=None)
        self.assertEqual(app._pending_performance["singer_name"], "")
        self.assertEqual([x for x in lines if "MISMATCH" in x], [])

    def test_a_still_pending_performance_is_noted_when_the_next_start_is_confirmed(self):
        app, shadow, lines = self.confirm(leftover=True)
        self.assertEqual(len([x for x in lines if "NOTE pending_performance_overwritten" in x]), 1, lines)
        self.assertEqual(app._pending_performance["remote_request_id"], None, "the old record is replaced: that is the behavior being flagged")
        app, shadow, lines = self.confirm()
        self.assertEqual([x for x in lines if "NOTE pending_performance_overwritten" in x], [])

    def test_with_the_setting_off_nothing_is_compared_or_logged(self):
        app, shadow, lines = self.confirm(enabled=False)
        self.assertEqual((shadow.compared, [x for x in lines if "START-SHADOW" in x]), (0, []))
        self.assertEqual(app._current_karaoke_singer_name, "Dana")

    def test_a_shadow_that_blows_up_cannot_stop_the_confirmation(self):
        class Bad:
            available = True

            def __getattr__(self, name):
                def boom(*a, **k):
                    raise RuntimeError(name)
                return boom

        with mock.patch.object(self.singws, "_start_shadow_for", lambda s: Bad()):
            app, shadow, lines = self.confirm()
        self.assertEqual((app._current_karaoke_singer_name, app._pending_performance["singer_name"]), ("Dana", "Dana"))

    def test_shapes_the_rule_does_not_model_are_skipped_not_reported(self):
        ok = dict(name="D", duet=None, duration=1, song_info="/l/a.mp3", entry_artist="A", entry_title="T", fallback=None, command_source=None, singer_id="s", request_id="r",
                  remote_request_id=None, key=0, tempo_percent=None)
        self.assertIsNotNone(start_shadow.confirmed_facts(**ok))
        for bad in ({"name": 5}, {"duration": 1.5}, {"duration": True}, {"song_info": 7}, {"song_info": ()}, {"singer_id": None}, {"remote_request_id": object()}, {"key": float("nan")},
                    {"song_info": "x" * 5000}, {"command_source": 3}):
            self.assertIsNone(start_shadow.confirmed_facts(**dict(ok, **bad)), bad)


if __name__ == "__main__":
    unittest.main()
