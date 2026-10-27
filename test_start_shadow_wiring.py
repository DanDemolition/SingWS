"""The start-route shadow as wired into the REAL `play_next_file` (the harness of test_show_critical_regressions), with the real Rust library.

For every route the real method can take (mp4, mp3, CDG+MP3 as a tuple and as a lone .cdg with and without its MP3, MP3+G ZIP cached / invalid / not yet extracted, a
missing file, unsupported formats, external KaraFun) the shadow must log the SAME route and no MISMATCH, and the app must do exactly what it did before: the same play_*
function is called (or none). With the setting off nothing is logged; a shadow that blows up cannot stop a start.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import os
import unittest
from pathlib import Path
from unittest import mock

import start_shadow
import test_show_critical_regressions as base

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


class Wiring(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_start(self, song_info, *, exists=("/lib/",), zip_ready=None, mp3g=True, external=False, setting=True, shadow_factory=None):
        entry = {"request_uid": "rid-1", "song_info": song_info, "artist": "A", "title": "T", "skipped": False}
        singer = {"name": "Dana", "singer_id": "sid-Dana", "skipped": False, "songs": [entry]}
        app = self.playback_app([singer])
        app.settings = {"start_shadow": setting}
        calls = []

        def stub(name):
            def f(*args, **kwargs):
                calls.append(name)
                return True
            return f

        for n in ("play_mp4", "play_mp3", "play_cdg_mp3_dual", "play_mp3g_zip"):
            setattr(app, n, stub(n))
        app.zip_cache = type("Z", (), {"cached_paths_if_ready": lambda _s, p: zip_ready if zip_ready is not None else (None, None)})()
        app.is_mp3g_zip = lambda p: mp3g
        app._is_external_karafun_entry = lambda e: external
        app._start_external_karafun_playback = lambda **kw: calls.append("external")
        app._song_info_primary_path = lambda info: info[0] if isinstance(info, (tuple, list)) and info else (info if isinstance(info, str) else "")
        lines = []
        # created BEFORE os.path.exists is patched, so the loader can see the real library; the app's factory then reuses it
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        patches = [mock.patch.object(self.singws, "_diag", lambda text, *a, **k: lines.append(str(text))),
                   mock.patch.object(self.singws.os.path, "exists", lambda p: any(str(p).startswith(x) or str(p) == x for x in exists))]
        if shadow_factory is not None:
            patches.append(mock.patch.object(self.singws, "_start_shadow_for", shadow_factory))
        for p in patches:
            p.start()
        try:
            app.play_next_file()
        finally:
            for p in reversed(patches):
                p.stop()
        routes = [x.split("route=")[1].split()[0] for x in lines if "[START-SHADOW] decision route=" in x and not x.split("route=")[1].startswith(("pick", "engine", "display"))]
        mism = [x for x in lines if "[START-SHADOW] MISMATCH" in x]
        return calls, routes, mism, lines

    def expect(self, song_info, route, called, **kw):
        calls, routes, mism, lines = self.run_start(song_info, **kw)
        self.assertEqual(mism, [], mism)
        self.assertEqual(routes, [route], (song_info, lines))
        self.assertEqual(calls, called, song_info)

    def test_every_route_the_real_method_takes_is_the_route_rust_expects(self):
        ok = ("/lib/",)
        self.expect("/lib/a.mp4", "mp4", ["play_mp4"], exists=ok)
        self.expect("/lib/a.mp3", "mp3", ["play_mp3"], exists=ok)
        self.expect(("/lib/a.cdg", "/lib/a.mp3"), "cdg_mp3_dual", ["play_cdg_mp3_dual"], exists=ok)
        self.expect(["/lib/a.cdg", "/lib/a.mp3"], "cdg_mp3_dual", ["play_cdg_mp3_dual"], exists=ok)
        self.expect(("/lib/a.mp4", "/lib/b.mp4"), "mp4", ["play_mp4"], exists=ok)
        self.expect(("/lib/a.mp3", "x"), "mp3", ["play_mp3"], exists=ok)
        self.expect("/lib/a.cdg", "cdg_mp3_dual", ["play_cdg_mp3_dual"], exists=ok + ("/lib/a.mp3",))
        self.expect("/lib/a.cdg", "reject_missing_cdg_audio_pair", [], exists=("/lib/a.cdg",))
        self.expect("/lib/a.zip", "mp3g_zip", ["play_mp3g_zip"], exists=ok, zip_ready=("/t/x.cdg", "/t/x.mp3"), mp3g=True)
        self.expect("/lib/a.zip", "reject_invalid_zip", [], exists=ok, zip_ready=("/t/x.cdg", "/t/x.mp3"), mp3g=False)
        self.expect("/lib/a.zip", "zip_prepare", ["play_mp3g_zip"], exists=ok, zip_ready=(None, None))
        self.expect("/lib/A.ZIP", "reject_unsupported_format", [], exists=ok, zip_ready=("/t/x.cdg", "/t/x.mp3"))
        self.expect("/lib/A.ZIP", "zip_prepare", ["play_mp3g_zip"], exists=ok, zip_ready=(None, None))
        self.expect("/lib/a.txt", "reject_unsupported_format", [], exists=ok)
        self.expect("/lib/a.MP4", "reject_unsupported_format", [], exists=ok)
        self.expect(("/lib/a.zip", "x"), "reject_unsupported_tuple", [], exists=ok)
        self.expect("/gone/a.mp4", "reject_missing_file", [], exists=ok)
        self.expect("/lib/a.mp4", "external_karafun", ["external"], exists=ok, external=True)

    def test_a_start_that_raises_in_python_is_not_reported_as_a_route(self):
        for bad in (("/lib/a.cdg", "/lib/a.mp3", "extra"), ("/lib/a.cdg",), ()):
            try:
                calls, routes, mism, lines = self.run_start(bad, exists=("/lib/",))
            except Exception:
                continue  # the app itself raises for these shapes; the shadow never got a route to compare
            self.assertEqual(mism, [], bad)

    def test_with_the_setting_off_nothing_is_logged_and_the_start_is_unchanged(self):
        calls, routes, mism, lines = self.run_start("/lib/a.mp4", setting=False)
        self.assertEqual((calls, routes, mism), (["play_mp4"], [], []))
        self.assertFalse(any("START-SHADOW" in x for x in lines))

    def test_a_shadow_that_blows_up_cannot_stop_a_start(self):
        def boom(settings):
            raise RuntimeError("shadow factory broke")

        calls, routes, mism, lines = self.run_start("/lib/a.mp4", shadow_factory=boom)
        self.assertEqual(calls, ["play_mp4"])

        class Bad:
            available = True

            def check(self, *a, **k):
                raise RuntimeError("check broke")

        calls, routes, mism, lines = self.run_start("/lib/a.mp4", shadow_factory=lambda s: Bad())
        self.assertEqual(calls, ["play_mp4"])


class PickWiring(unittest.TestCase):
    """Who sings next, from the REAL `play_next_file` over random queues (skipped singers, singers without songs, skipped songs, older tuple entries): the shadow's Rust answer must
    equal the singer and song the real method picked. Nothing is logged for a locked rotation."""
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_queue(self, queue, *, locked=False, enabled=True, real_lock=False, lock_setting=True):
        app = self.playback_app(queue)
        app.settings = {"start_shadow": enabled}
        app._is_rotation_locked = lambda: locked
        app._rotation_reweave_locked_tail = lambda: None
        if real_lock:
            # the app's own rotation-mode and lock methods, driven by the settings, exactly as in a show
            for name in ("_is_rotation_locked", "_is_rotation_mode", "_rotation_reweave_locked_tail"):
                app.__dict__.pop(name, None)
            app.settings = {"start_shadow": enabled, "queue_mode": "rotation", "rotation_locked": bool(lock_setting)}
            app.save_settings = lambda: None
            app._update_rotation_lock_button = lambda: None
        app._ensure_queue_entry_id = lambda entry: (entry.setdefault("request_uid", f"rid-{entry.get('song_info')}") if isinstance(entry, dict) else f"rid-{entry[0]}")
        picked = {}
        original_pop = None
        app.play_mp3 = lambda *a, **k: (picked.setdefault("path", a[0]), True)[1]
        lines = []
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None), mock.patch.object(self.singws.os.path, "exists", lambda p: True):
            app.play_next_file()
        return app, picked, lines

    def test_random_queues_pick_the_same_singer_and_song_as_rust(self):
        import random
        rng = random.Random(20261026)
        checked = picks = nones = 0
        for case in range(400):
            queue = []
            for i in range(rng.randint(0, 7)):
                songs = []
                for j in range(rng.randint(0, 4)):
                    r = rng.random()
                    path = f"/lib/s{i}_{j}.mp3"
                    if r < 0.2:
                        songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": True})
                    elif r < 0.3:
                        songs.append((path, 0))  # an older tuple-format entry
                    else:
                        songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": False})
                queue.append({"name": f"S{i}", "singer_id": f"sid{i}", "skipped": rng.random() < 0.25, "songs": songs})
            app, picked, lines = self.run_queue(queue)
            mism = [x for x in lines if "MISMATCH" in x]
            self.assertEqual(mism, [], (case, mism))
            decisions = [x for x in lines if "decision route=pick" in x]
            if queue:
                self.assertEqual(len(decisions), 1, (case, "a non-empty queue must produce exactly one compared pick (a skipped comparison would hide a disagreement)", lines))
                checked += 1
            if picked.get("path"):
                picks += 1
            else:
                nones += 1
        self.assertGreater(picks, 100)
        self.assertGreater(nones, 10)

    def test_when_every_singer_is_skipped_nothing_starts_and_the_queue_is_untouched(self):
        """Fixed in 1.0.2.1 (it used to start the LAST skipped singer's song and then queue that singer twice): every singer skipped means no start, and every singer is back in the
        order they were in, with all their songs."""
        q = [{"name": "A", "singer_id": "a", "skipped": True, "songs": [{"request_uid": "x", "song_info": "/lib/a.mp3", "skipped": False}]},
             {"name": "B", "singer_id": "b", "skipped": True, "songs": [{"request_uid": "y", "song_info": "/lib/b.mp3", "skipped": False},
                                                                          {"request_uid": "z", "song_info": "/lib/b2.mp3", "skipped": False}]}]
        app, picked, lines = self.run_queue(q)
        self.assertEqual(picked, {}, "nothing was started")
        self.assertEqual([s["name"] for s in app.queue], ["A", "B"])
        self.assertEqual([len(s["songs"]) for s in app.queue], [1, 2], "nobody lost a song")
        self.assertEqual([x for x in lines if "MISMATCH" in x], [])
        self.assertTrue(any("decision route=pick_none agree=1" in x for x in lines), lines)
        # and a rotation of skipped singers plus one who can sing still starts that one
        q2 = [dict(q[0]), {"name": "C", "singer_id": "c", "skipped": False, "songs": [{"request_uid": "w", "song_info": "/lib/c.mp3", "skipped": False}]}]
        app, picked, lines = self.run_queue(q2)
        self.assertEqual(picked["path"], "/lib/c.mp3")

    @staticmethod
    def locked_random_queue(rng):
        queue = []
        for i in range(rng.randint(1, 8)):
            songs = []
            for j in range(rng.randint(0, 3)):
                r = rng.random()
                path = f"/lib/s{i}_{j}.mp3"
                if r < 0.25:
                    songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": True})
                elif r < 0.32:
                    songs.append((path, 0))
                else:
                    songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": False})
            singer = {"name": f"S{i}", "singer_id": f"sid{i}", "skipped": rng.random() < 0.3, "songs": songs}
            if rng.random() < 0.25:
                singer["rotation_marker"] = True
            if rng.random() < 0.35:
                singer["rotation_lock_new"] = True
                singer["rotation_lock_order"] = rng.choice([1, 2, 3, 3, 0, None, "5"])
            queue.append(singer)
        if queue and rng.random() < 0.3:
            # the lock-clearing branch: a locked newcomer at the top, the marker further down, then skipped singers that would be reweaved if the lock stayed on
            queue[0]["rotation_lock_new"] = True
            queue[0]["rotation_lock_order"] = 1
            queue[0].pop("rotation_marker", None)
            if len(queue) > 1:
                queue[-1]["rotation_marker"] = True
                for other in queue[1:-1]:
                    if rng.random() < 0.6:
                        other["skipped"] = True
                    if rng.random() < 0.4:
                        other["rotation_lock_new"] = True
                        other["rotation_lock_order"] = rng.randint(0, 3)
        return queue

    def test_random_locked_rotations_pick_the_same_singer_song_queue_and_lock_state_as_rust(self):
        import random
        rng = random.Random(20261027)
        cleared = reordered = 0
        for case in range(800):
            queue = self.locked_random_queue(rng)
            started_locked = rng.random() < 0.7
            app, picked, lines = self.run_queue(queue, real_lock=True, lock_setting=started_locked)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, [x for x in lines if "MISMATCH" in x]))
            self.assertEqual(len([x for x in lines if "decision route=pick" in x]), 1, (case, "every non-empty queue is compared exactly once", lines))
            if started_locked and not app.settings.get("rotation_locked", True):
                cleared += 1
        self.assertGreater(cleared, 20, "the generator must reach the lock-clearing branch")

    def test_the_setting_off_logs_nothing_even_in_a_locked_rotation(self):
        q = [{"name": "A", "singer_id": "a", "skipped": True, "songs": [{"request_uid": "x", "song_info": "/lib/a.mp3", "skipped": False}]},
             {"name": "B", "singer_id": "b", "skipped": False, "songs": [{"request_uid": "y", "song_info": "/lib/b.mp3", "skipped": False}]}]
        app, picked, lines = self.run_queue(q, enabled=False, real_lock=True)
        self.assertEqual(picked["path"], "/lib/b.mp3")
        self.assertEqual(lines, [])

    def test_a_queue_with_something_odd_in_it_is_skipped_by_the_shadow(self):
        self.assertIsNone(start_shadow.pick_facts([{"songs": [], "skipped": False}, "not a dict"]))
        self.assertIsNone(start_shadow.pick_facts([{"songs": "abc"}]))
        self.assertEqual(start_shadow.pick_facts([{"songs": None}]), [{"skipped": False, "songs": "", "marker": False, "lock_new": False, "lock_order": 0}])
        self.assertEqual(start_shadow.pick_facts([{"skipped": 1, "songs": [{"skipped": True}, {"skipped": 0}, ("x", 1), {}], "rotation_marker": 1, "rotation_lock_new": 1, "rotation_lock_order": "4"}]),
                         [{"skipped": True, "songs": "sdtd", "marker": True, "lock_new": True, "lock_order": 4}])
        self.assertIsNone(start_shadow.pick_facts([{"songs": [], "rotation_lock_order": "x"}]), "a lock order the app could not read is skipped, not guessed")


class RotationWiring(unittest.TestCase):
    """What the app does to the rotation when a started song is committed (the transport's started signal), from the REAL `play_next_file` and its commit callback:
    random queues with random skipped singers / songs, markers (none, one, several), round flags (missing, true, false) and both queue modes. Rust must produce the same
    marker / round / has-sung flags for every singer."""
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_commit(self, queue, *, rotation_mode, locked=False, enabled=True, real_lock=False, lock_setting=True):
        app = self.playback_app(queue)
        app.settings = {"start_shadow": enabled, "queue_mode": "rotation" if rotation_mode else "classic"}
        app._is_rotation_mode = lambda: rotation_mode
        app._is_rotation_locked = lambda: locked
        app._rotation_reweave_locked_tail = lambda: None
        if real_lock:
            for name in ("_is_rotation_locked", "_is_rotation_mode", "_rotation_reweave_locked_tail"):
                app.__dict__.pop(name, None)
            app.settings = {"start_shadow": enabled, "queue_mode": "rotation", "rotation_locked": bool(lock_setting)}
            app.save_settings = lambda: None
            app._update_rotation_lock_button = lambda: None
        app._ensure_queue_entry_id = lambda entry: (entry.setdefault("request_uid", f"rid-{entry.get('song_info')}") if isinstance(entry, dict) else f"rid-{entry[0]}")
        lines = []
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None), mock.patch.object(self.singws.os.path, "exists", lambda p: True):
            app.play_next_file()
            if app.created_transports:
                app.created_transports[-1].started.emit()
        return app, lines

    @staticmethod
    def random_queue(rng):
        queue = []
        for i in range(rng.randint(1, 7)):
            songs = []
            for j in range(rng.randint(0, 3)):
                r = rng.random()
                path = f"/lib/s{i}_{j}.mp3"
                if r < 0.25:
                    songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": True})
                elif r < 0.32:
                    songs.append((path, 0))
                else:
                    songs.append({"request_uid": f"r{i}{j}", "song_info": path, "skipped": False})
            singer = {"name": f"S{i}", "singer_id": f"sid{i}", "skipped": rng.random() < 0.2, "songs": songs}
            if rng.random() < 0.8:
                singer["round_sung"] = rng.random() < 0.5
            if rng.random() < 0.3:
                singer["rotation_marker"] = True
            if rng.random() < 0.5:
                singer["has_sung"] = rng.random() < 0.5
            queue.append(singer)
        return queue

    def test_random_rotations_end_up_with_the_flags_rust_computes(self):
        import random
        rng = random.Random(20261026)
        compared = 0
        for case in range(500):
            queue = self.random_queue(rng)
            app, lines = self.run_commit(queue, rotation_mode=rng.random() < 0.7)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, [x for x in lines if "MISMATCH" in x]))
            if app.created_transports:
                twice = any("NOTE singer_already_in_queue" in x for x in lines)
                self.assertTrue(twice or any("decision route=rotation agree=1" in x for x in lines), (case, "a committed start must be compared (or noted as the all-skipped duplicate)", lines))
                compared += 0 if twice else 1
        self.assertGreater(compared, 150)

    def test_the_marker_moves_with_the_singer_when_it_was_on_top(self):
        q = [{"name": "A", "singer_id": "a", "skipped": False, "rotation_marker": True, "songs": [{"request_uid": "x", "song_info": "/lib/a.mp3", "skipped": False}]},
             {"name": "B", "singer_id": "b", "skipped": False, "songs": [{"request_uid": "y", "song_info": "/lib/b.mp3", "skipped": False}]}]
        app, lines = self.run_commit(q, rotation_mode=True)
        self.assertEqual([x["name"] for x in app.queue], ["B", "A"])
        self.assertEqual([bool(x.get("rotation_marker")) for x in app.queue], [False, True])
        self.assertEqual([x for x in lines if "MISMATCH" in x], [])

    def test_random_locked_rotations_end_up_with_the_order_and_flags_rust_computes(self):
        import random
        rng = random.Random(20261027)
        compared = reweaved = 0
        for case in range(800):
            queue = PickWiring.locked_random_queue(rng)
            for singer in queue:
                if rng.random() < 0.7:
                    singer["round_sung"] = rng.random() < 0.5
                if rng.random() < 0.5:
                    singer["has_sung"] = rng.random() < 0.5
            names_before = [s["name"] for s in queue]
            app, lines = self.run_commit(queue, rotation_mode=True, real_lock=True, lock_setting=rng.random() < 0.7)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, [x for x in lines if "MISMATCH" in x]))
            if app.created_transports:
                twice = any("NOTE singer_already_in_queue" in x for x in lines)
                self.assertTrue(twice or any("decision route=rotation agree=1" in x for x in lines), (case, lines))
                compared += 0 if twice else 1
        self.assertGreater(compared, 200)

    def test_the_setting_off_is_not_compared(self):
        q = [{"name": "A", "singer_id": "a", "skipped": False, "songs": [{"request_uid": "x", "song_info": "/lib/a.mp3", "skipped": False}]}]
        app, lines = self.run_commit(q, rotation_mode=True, enabled=False)
        self.assertEqual(lines, [])


class DisplayWiring(unittest.TestCase):
    """The artist and title published when a song starts, from the REAL `play_next_file`: random entry artists / titles (spaces, Python-only whitespace, the " • "
    separator inside them, empty parts) and random library fallback names must give the payload Rust computes."""
    bare_app = base.ShowCriticalRegressionTests.bare_app
    playback_app = base.ShowCriticalRegressionTests.playback_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_one(self, artist, title, fallback, *, enabled=True):
        entry = {"request_uid": "rid-1", "song_info": "/lib/a.mp3", "skipped": False}
        if artist is not None:
            entry["artist"] = artist
        if title is not None:
            entry["title"] = title
        app = self.playback_app([{"name": "Dana", "singer_id": "sid-Dana", "skipped": False, "songs": [entry]}])
        app.settings = {"start_shadow": enabled}
        app.lookup_display_name = lambda path, **k: fallback
        app.play_mp3 = lambda *a, **k: True
        lines = []
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None), mock.patch.object(self.singws.os.path, "exists", lambda p: True):
            app.play_next_file()
        return app._pending_play_start_context["payload"], lines

    def test_random_names_give_the_payload_rust_computes(self):
        import random
        rng = random.Random(20261026)
        pieces = ["Abba", "  ", "", " Queen ", "A • B", "•", " • ", "x\u00a0", "\u001cZed\u001f", "ñandú", "日本", "Tom\tPetty", "😀", "Lee ", "\n"]
        shown = compared = 0
        for case in range(600):
            artist = rng.choice([None, 0, ""] + pieces)
            title = rng.choice([None, ""] + pieces)
            fallback = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 3)))
            payload, lines = self.run_one(artist, title, fallback)
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (case, artist, title, fallback, payload))
            self.assertTrue(any("decision route=display" in x for x in lines), (case, "every start must be compared", lines))
            compared += 1
            if payload["artist"] and payload["title"]:
                shown += 1
        self.assertEqual(compared, 600)
        self.assertGreater(shown, 50)

    def test_known_payloads(self):
        self.assertEqual(self.run_one("Abba", "Waterloo", "x")[0]["artist"], "Abba")
        p, _ = self.run_one(None, None, "Queen • Bohemian Rhapsody • Live")
        self.assertEqual((p["artist"], p["title"], p["singer"]), ("Queen", "Bohemian Rhapsody • Live", "Dana"))
        p, _ = self.run_one(None, None, "Just A Name")
        self.assertEqual((p["artist"], p["title"]), ("Just A Name", ""))

    def test_the_setting_off_logs_nothing(self):
        p, lines = self.run_one("A", "B", "x", enabled=False)
        self.assertEqual(lines, [])


class EngineWiring(unittest.TestCase):
    """The REAL `_karaoke_rust_voice_for_song` with a fake video bridge, fake audio engines and a fake voice class, over every combination of facts."""

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        if not start_shadow.StartShadow(lambda s: None).available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def run_case(self, setting, supports, engine_kind, voice_error, *, enabled=True):
        import types
        import sys
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"karaoke_audio_engine": setting, "start_shadow": enabled}
        app._rust_karaoke_voice = None
        engine = {"none": None, "headless": types.SimpleNamespace(_headless=True, _stats=lambda: (0, 0, 0, 0, 1000)),
                  "running": types.SimpleNamespace(_headless=False, _stats=lambda: (0, 0, 0, 0, 1000)), "raises": "raises"}[engine_kind]

        def soundboard_runtime():
            if engine == "raises":
                raise RuntimeError("no engine")
            return engine

        app.bg_music = types.SimpleNamespace(soundboard_runtime=soundboard_runtime)
        shadow_audio = []

        def supports_call():
            if supports == "raises":
                raise RuntimeError("bridge")
            return supports

        plugin = types.SimpleNamespace(supportsShadowAudio=supports_call, setShadowAudio=lambda v: shadow_audio.append(v))

        class FakeVoice:
            def __init__(self, e):
                if voice_error:
                    raise RuntimeError("voice failed")
                self._e = e

            def set_output_latency_seconds(self, x):
                pass

        fake_module = types.ModuleType("rust_karaoke_voice")
        fake_module.RustKaraokeVoice = FakeVoice
        lines = []
        self.singws._START_SHADOW = start_shadow.StartShadow(lambda text: lines.append(str(text)))
        with mock.patch.dict(sys.modules, {"rust_karaoke_voice": fake_module}), mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            voice = app._karaoke_rust_voice_for_song(plugin)
        return voice, shadow_audio, [x for x in lines if "MISMATCH" in x], [x for x in lines if "decision" in x]

    def test_every_combination_of_facts_agrees_with_rust_and_the_method_behaves_as_before(self):
        n = 0
        for setting in ("rust", " RUST ", "", None, "mpv", "Rust2"):
            for supports in (True, False, "raises"):
                for engine_kind in ("none", "headless", "running", "raises"):
                    for voice_error in (False, True):
                        voice, shadow_audio, mism, decisions = self.run_case(setting, supports, engine_kind, voice_error)
                        self.assertEqual(mism, [], (setting, supports, engine_kind, voice_error, mism))
                        want = str(setting or "rust").strip().lower() == "rust"
                        sup = supports is True
                        expect_voice = want and sup and engine_kind == "running" and not voice_error
                        self.assertEqual(voice is not None, expect_voice, (setting, supports, engine_kind, voice_error))
                        self.assertEqual(shadow_audio, [expect_voice] if sup else [], "the bridge is told exactly what it was told before")
                        self.assertEqual(len(decisions), 1)
                        n += 1
        self.assertEqual(n, 6 * 3 * 4 * 2)

    def test_with_the_setting_off_nothing_is_logged(self):
        voice, shadow_audio, mism, decisions = self.run_case("rust", True, "running", False, enabled=False)
        self.assertIsNotNone(voice)
        self.assertEqual((mism, decisions), ([], []))


if __name__ == "__main__":
    unittest.main()
