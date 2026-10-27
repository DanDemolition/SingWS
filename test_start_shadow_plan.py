"""Stage A5 step 9: the whole start plan (`"op":"plan"` in singws-start) against the REAL `play_next_file` chain.

Random queues (skipped singers, skipped songs, every song shape, missing files, uncached ZIPs) run through the real chain with only the mpv transport replaced by a recorder.
The plan Rust computes from the same facts must name the singer and song the app picked, the route it took, the arguments that reached the transport and the artist and
title it published, for every start that proceeds; a route that rejects or prepares must have no transport call in the app and none in the plan.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import os
import random
import unittest
from pathlib import Path
from unittest import mock

import start_shadow
import test_show_critical_regressions as base

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))

SHAPES = ["/lib/a.mp4", "/lib/a.mp3", "/lib/a.zip", "/lib/a.cdg", ("/lib/a.cdg", "/lib/a.mp3"), ("/lib/a.mp4", "/lib/o.mp4"), ("/lib/a.mp3", 0), "/lib/a.xyz"]


class Recording(start_shadow.StartShadow):
    """Only watches what the app decides; the comparison under test is the plan, not these hooks."""

    def __init__(self, log):
        super().__init__(log)
        self.displayed = []
        self.routes = []

    def check_display(self, artist, title, entry_artist, entry_title, fallback):
        self.displayed.append((str(artist), str(title)))
        return super().check_display(artist, title, entry_artist, entry_title, fallback)

    def check(self, observed, song_info, **kw):
        self.routes.append(observed)
        return super().check(observed, song_info, **kw)


class StartPlan(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        cls.shadow_for_ask = start_shadow.StartShadow(lambda s: None)
        if not cls.shadow_for_ask.available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def ask(self, request):
        got = self.shadow_for_ask._ask(request, cap=1 << 16)
        self.assertIsNotNone(got, request)
        return got

    def make_app(self, singers, phrase_start, zip_ready):
        app = self.playback_app(singers)
        app.settings = {"start_shadow": True}
        for stub in ("play_mp3", "play_mp4", "play_cdg_mp3_dual", "play_mp3g_zip"):
            app.__dict__.pop(stub, None)
        app._resolve_phrase_start = lambda *_a, **_k: phrase_start
        app.zip_cache = type("Z", (), {"cached_paths_if_ready": lambda _s, p: zip_ready})()
        app.is_mp3g_zip = lambda p: True
        app._is_external_karafun_entry = lambda e: False
        app._reset_end_silence_state = lambda: None
        app._prepare_karaoke_start = lambda p: None
        app._playback_safe_ui_delay_ms = lambda ms: ms
        app._start_lyrics_background_video = lambda: None
        app._mp3g_pending_start, app._mp3g_pending_loop, app._mp3g_pending_duration = {}, {}, {}
        app.transport_calls = []

        def recorder(**kw):
            app.transport_calls.append(kw)
            return True

        app._start_mpv_karaoke_transport = recorder
        app._set_processing_text = lambda *a, **k: None
        return app

    def random_queue(self, rng):
        singers = []
        for n in range(rng.randint(0, 4)):
            songs = []
            for k in range(rng.randint(0, 3)):
                info = rng.choice(SHAPES)
                songs.append({"request_uid": f"rid-{n}-{k}", "song_info": info, "artist": rng.choice(["A", "", " "]), "title": rng.choice(["T", "", "Only Title"]),
                              "skipped": rng.random() < 0.3, "key": rng.choice([0, 0, 1, -1, 5, -7, 2.0, None]), "duration": rng.choice([0, 95, 180, 241])})
            singers.append({"name": f"S{n}", "singer_id": f"sid-{n}", "skipped": rng.random() < 0.25, "songs": songs})
        return singers

    def plan_request(self, singers, missing, zip_ready, phrase_start):
        facts = []
        for s in singers:
            kinds = "".join("s" if e["skipped"] else "d" for e in s["songs"])
            item = {"skipped": bool(s["skipped"]), "songs": kinds}
            first = next((e for e in s["songs"] if not e["skipped"]), None)
            if first is not None:
                info = first["song_info"]
                primary = info[0] if isinstance(info, tuple) else info
                route = {"external": False, "info": start_shadow.info_request(info), "has_primary": bool(primary), "primary_exists": primary not in missing,
                         "zip_cached": bool(zip_ready[0] and zip_ready[1]), "mp3g": True, "pair": (str(info)[:-4] + ".mp3") not in missing if isinstance(info, str) else None}
                route = {k: v for k, v in route.items() if v is not None}
                mp3 = info[1] if isinstance(info, tuple) and len(info) == 2 else (info[:-4] + ".mp3" if isinstance(info, str) and info.endswith(".cdg") else "")
                cdg = info[0] if isinstance(info, tuple) else info
                if isinstance(info, str) and info.endswith(".zip"):
                    cdg, mp3 = zip_ready
                mp3 = mp3 if isinstance(mp3, str) else ""
                item["entry"] = {"route": route, "artist": str(first["artist"] or "").strip(), "title": str(first["title"] or "").strip(), "fallback": f"Artist {primary} \u2022 Title {primary}", "song": primary if isinstance(primary, str) else "",
                                 "cdg": cdg if isinstance(cdg, str) else "", "mp3": mp3, "semitones": first["key"] or 0, "start": phrase_start,
                                 "duration": first["duration"] or 180}
            facts.append(item)
        return {"op": "plan", "singers": facts}

    def test_random_queues_agree_with_the_plan_over_the_real_chain(self):
        rng = random.Random(20261030)
        started = {}
        for case in range(300):
            singers = self.random_queue(rng)
            missing = {p for p in ("/lib/a.mp4", "/lib/a.mp3", "/lib/o.mp4", "/lib/a.cdg") if rng.random() < 0.15}
            zip_ready = rng.choice([("/t/x.cdg", "/t/x.mp3"), (None, None)])
            phrase = rng.choice([0.0, 3.25, 17.0])
            names_before = [s["name"] for s in singers]
            app = self.make_app(singers, phrase, zip_ready)
            lines = []
            shadow = Recording(lambda text: lines.append(str(text)))
            self.singws._START_SHADOW = shadow
            plan = self.ask(self.plan_request(singers, missing, zip_ready, phrase))
            with mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))), \
                    mock.patch.object(self.singws.os.path, "exists", lambda p: p not in missing), \
                    mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None), \
                    mock.patch.object(self.singws, "QThread", lambda *a, **k: mock.MagicMock()), \
                    mock.patch.object(self.singws, "ZipExtractWorker", lambda *a, **k: mock.MagicMock()):
                app.play_next_file()
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, lines))
            if not plan["route"]:
                self.assertEqual(app.transport_calls, [], (case, plan))
                self.assertEqual(shadow.routes, [], (case, plan))
                continue
            self.assertEqual(shadow.routes[-1:], [plan["route"]], (case, plan))
            if plan["transport"] is None:
                self.assertEqual(app.transport_calls, [], (case, plan))
                continue
            started[plan["transport"]["mode"]] = started.get(plan["transport"]["mode"], 0) + 1
            self.assertEqual(len(app.transport_calls), 1, (case, plan))
            kw = app.transport_calls[0]
            for key in ("audio_path", "video_path", "mode", "semitones", "start_seconds", "loop_seconds", "duration_seconds"):
                self.assertEqual(kw[key], plan["transport"][key], (case, key, kw, plan))
            self.assertEqual(shadow.displayed[-1:], [(plan["display"]["artist"], plan["display"]["title"])], (case, plan))
            self.assertEqual([s["name"] for s in app.queue], [names_before[i] for i in plan["queue"]], (case, plan))
        self.assertEqual(sorted(started), ["audio", "cdg", "mp4"], started)
        self.assertTrue(all(n > 15 for n in started.values()), started)


if __name__ == "__main__":
    unittest.main()
