"""Which video bridge mpv_playback_iina loads (roadmap item 2, milestone V6): the Objective-C one by default, the Rust one only when asked, and
ANY problem with the Rust one falls back to the Objective-C one with the reason. Selection runs in a subprocess per case (module singletons)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import os
import re
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUST = Path(os.environ.get("SINGWS_VIDEO_LIB") or ROOT / "native/singws_video/arm64/libsingws_video.dylib")
LIBMPV = ROOT / "native/mpv_runtime/artifacts/arm64/Frameworks/singws_libmpv.2.dylib"
HAVE_RUST = RUST.is_file() and LIBMPV.is_file() and os.uname().machine == "arm64"


def choose(env_extra: dict, body: str = "") -> dict:
    script = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, %r)
        import mpv_playback_iina as m
        %s
        try:
            lib, kind, note = m._open_bridge_library()
            print("RESULT" + json.dumps({"kind": kind, "note": note}))
        except Exception as exc:
            print("RESULT" + json.dumps({"error": str(exc)}))
    """) % (str(ROOT), body)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SINGWS_VIDEO")}
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, env=env, timeout=60).stdout
    for line in out.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    raise AssertionError(out)


class VideoBridgeSelectionTests(unittest.TestCase):
    def test_export_list_matches_the_rust_dylib(self):
        sys.path.insert(0, str(ROOT))
        import mpv_playback_iina as m
        self.assertEqual(len(m.BRIDGE_EXPORTS), 37)
        if RUST.is_file():
            out = subprocess.run(["nm", "-gU", str(RUST)], capture_output=True, text=True).stdout
            have = {w[len("_singws_bridge_"):] for w in out.split() if w.startswith("_singws_bridge_")}
            # Rust-only extras (never required of the Objective-C bridge): follower mode and shadow audio for karaoke audio played by the Rust engine
            self.assertEqual(have - {"set_shadow_audio"}, set(m.BRIDGE_EXPORTS))
            self.assertTrue({"set_shadow_audio"} <= have)

    def test_default_setting_is_rust_and_the_launch_hook_maps_it(self):
        src = (ROOT / "0.2.18.1.py").read_text()
        self.assertRegex(src, r'"video_bridge":\s*"rust"')
        self.assertIn('os.environ.setdefault("SINGWS_VIDEO_BRIDGE", "rust")', src)
        self.assertIn("saved video_bridge=objc migrated to rust", src)
        self.assertRegex(src, r'"video_bridge_fallback":\s*True')

    def test_a_failed_rust_renderer_falls_back_to_the_objective_c_bridge_for_that_song(self):
        sys.path.insert(0, str(ROOT))
        import mpv_playback_iina as m
        from unittest import mock

        def fake_api(kind, handle):
            lib = mock.MagicMock()
            lib.singws_bridge_create.return_value = handle
            return types.SimpleNamespace(kind=kind, lib=lib, note="")

        rust, objc = fake_api("rust", 0), fake_api("objc", 1234)
        player = m.MpvPlaybackPlugin.__new__(m.MpvPlaybackPlugin)
        player._lock = __import__("threading").RLock()
        player.api = rust
        player._handle = None
        player._output_id, player._preview_id = 11, 22
        player._volume, player._device = 0.5, "auto"
        player._tempo_percent, player._semitones, player._dsp_chain = 100, 0, ""
        player._video_offset_ms, player._sidefill = 0, 0
        player._loaded, player._audio_only, player._error = False, False, ""
        logs = []
        player.log = logs.append
        with mock.patch.object(m, "_fallback_to_objc", return_value=objc) as fb:
            ok = player.loadSingWSMedia("/x/song.mp4", None, autoplay=False)
        self.assertTrue(ok)
        fb.assert_called_once()
        self.assertIs(player.api, objc)
        self.assertEqual(player._handle, 1234)
        objc.lib.singws_bridge_create.assert_called_once()
        # an Objective-C bridge that fails is NOT retried (and nothing is hidden: the load reports failure)
        player2 = m.MpvPlaybackPlugin.__new__(m.MpvPlaybackPlugin)
        player2.__dict__.update(player.__dict__)
        player2.api, player2._handle = fake_api("objc", 0), None
        with mock.patch.object(m, "_fallback_to_objc") as fb2:
            self.assertFalse(player2.loadSingWSMedia("/x/song.mp4", None, autoplay=False))
        fb2.assert_not_called()

    def test_nothing_asked_means_rust(self):
        r = choose({}, "m._rust_bridge_path = lambda: None")   # no Rust library in a bare checkout -> the logged fallback
        self.assertNotEqual(r.get("kind"), "rust")
        if "error" not in r:
            self.assertIn("not found", r["note"])   # proves Rust was tried by default
        r = choose({"SINGWS_VIDEO_BRIDGE": "objc"})
        self.assertNotEqual(r.get("kind"), "rust")

    def _player(self, m, api):
        from unittest import mock
        import threading
        p = m.MpvPlaybackPlugin.__new__(m.MpvPlaybackPlugin)
        p._lock = threading.RLock(); p.api = api; p._handle = None
        p._output_id, p._preview_id = 11, 22
        p._volume, p._device = 0.5, "auto"
        p._tempo_percent, p._semitones, p._dsp_chain = 100, 0, ""
        p._video_offset_ms, p._sidefill = 0, 0
        p._loaded, p._audio_only, p._error = False, False, ""
        p.logs = []; p.log = p.logs.append
        return p

    def test_fallback_use_is_logged_as_error_and_notifies_the_operator(self):
        sys.path.insert(0, str(ROOT))
        import mpv_playback_iina as m
        from unittest import mock
        seen = []
        m.set_operator_notifier(seen.append)
        try:
            with mock.patch.object(m, "_BridgeApi") as ba:
                ba.return_value = types.SimpleNamespace(kind="objc", lib=mock.MagicMock(), note="")
                logs = []
                m._fallback_to_objc(logs.append)
            self.assertTrue(any("ERROR" in l and "[VIDEO-BRIDGE]" in l for l in logs), logs)
            self.assertEqual(len(seen), 1)
            self.assertIn("fallback", seen[0].lower())
        finally:
            m.set_operator_notifier(None)
            os.environ.pop("SINGWS_VIDEO_BRIDGE", None)

    def test_rust_only_mode_retries_once_then_fails_visibly_without_switching(self):
        sys.path.insert(0, str(ROOT))
        import mpv_playback_iina as m
        from unittest import mock
        lib = mock.MagicMock(); lib.singws_bridge_create.return_value = 0
        api = types.SimpleNamespace(kind="rust", lib=lib, note="")
        p = self._player(m, api)
        seen = []
        m.set_operator_notifier(seen.append)
        try:
            with mock.patch.dict(os.environ, {"SINGWS_VIDEO_BRIDGE_FALLBACK": "0"}), \
                    mock.patch.object(m, "_fallback_to_objc") as fb:
                self.assertFalse(p.loadSingWSMedia("/x/song.mp4", None, autoplay=False))
            fb.assert_not_called()
            self.assertEqual(lib.singws_bridge_create.call_count, 2)
            self.assertIs(p.api, api)
            self.assertIn("No picture", p._error)
            self.assertEqual(len(seen), 1)
            self.assertTrue(any("retrying once" in l for l in p.logs))
        finally:
            m.set_operator_notifier(None)

    def test_rust_only_mode_succeeds_when_the_retry_works(self):
        sys.path.insert(0, str(ROOT))
        import mpv_playback_iina as m
        from unittest import mock
        lib = mock.MagicMock(); lib.singws_bridge_create.side_effect = [0, 777]
        p = self._player(m, types.SimpleNamespace(kind="rust", lib=lib, note=""))
        with mock.patch.dict(os.environ, {"SINGWS_VIDEO_BRIDGE_FALLBACK": "0"}):
            self.assertTrue(p.loadSingWSMedia("/x/song.mp4", None, autoplay=False))
        self.assertEqual(p._handle, 777)

    def test_bad_libmpv_path_forces_a_real_renderer_failure(self):
        """The real Rust dylib with a libmpv that does not exist cannot create a core: with the fallback off the load fails visibly."""
        if not HAVE_RUST:
            self.skipTest("needs the built Rust video dylib (arm64)")
        script = textwrap.dedent('''
            import sys, json
            sys.path.insert(0, %r)
            import mpv_playback_iina as m
            seen = []; m.set_operator_notifier(seen.append)
            p = m.MpvPlaybackPlugin(log=lambda t: None)
            ok = p.loadSingWSMedia("/nonexistent/song.mp4", None, autoplay=False)
            print("RESULT" + json.dumps({"ok": ok, "kind": p.api.kind, "seen": len(seen), "err": p.errorString()}))
        ''') % str(ROOT)
        env = {k: v for k, v in os.environ.items() if not k.startswith("SINGWS_VIDEO")}
        env.update({"SINGWS_VIDEO_BRIDGE": "rust", "SINGWS_VIDEO_LIB": str(RUST), "SINGWS_LIBMPV": "/nonexistent/libmpv.dylib",
                    "SINGWS_VIDEO_BRIDGE_FALLBACK": "0"})
        out = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, env=env, timeout=60).stdout
        line = [l for l in out.splitlines() if l.startswith("RESULT")]
        self.assertTrue(line, out)
        r = json.loads(line[0][6:])
        self.assertFalse(r["ok"])
        self.assertEqual(r["kind"], "rust")

    @unittest.skipUnless(HAVE_RUST, "needs the built Rust video dylib and the bundled libmpv (arm64)")
    def test_rust_is_used_when_asked_and_available(self):
        r = choose({"SINGWS_VIDEO_BRIDGE": "rust", "SINGWS_VIDEO_LIB": str(RUST)})
        self.assertEqual(r.get("kind"), "rust", r)

    def test_a_missing_rust_library_falls_back_with_a_reason(self):
        r = choose({"SINGWS_VIDEO_BRIDGE": "rust"}, "m._rust_bridge_path = lambda: None")
        self.assertNotEqual(r.get("kind"), "rust")
        if "error" not in r:
            self.assertIn("not found", r["note"])

    def test_a_library_that_will_not_load_or_lacks_exports_falls_back(self):
        # a real dylib that exports none of the bridge functions (the Rust music engine's)
        other = ROOT / "native/singws_bgm/arm64/libsingws_bgm_ffi.dylib"
        if not other.is_file() or os.uname().machine != "arm64":
            self.skipTest("needs another built arm64 dylib to stand in for a wrong library")
        r = choose({"SINGWS_VIDEO_BRIDGE": "rust", "SINGWS_VIDEO_LIB": str(other)})
        self.assertNotEqual(r.get("kind"), "rust")
        if "error" not in r:
            self.assertIn("could not be used", r["note"])
            self.assertIn("does not export", r["note"])


if __name__ == "__main__":
    unittest.main()
