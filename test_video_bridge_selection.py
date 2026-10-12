"""Which video bridge mpv_playback_iina loads (roadmap item 2, milestone V6): the Objective-C one by default, the Rust one only when asked, and
ANY problem with the Rust one falls back to the Objective-C one with the reason. Selection runs in a subprocess per case (module singletons)."""
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
            self.assertEqual(have - {"set_follow", "follow_clock", "set_shadow_audio"}, set(m.BRIDGE_EXPORTS))
            self.assertTrue({"set_follow", "follow_clock", "set_shadow_audio"} <= have)

    def test_default_setting_is_rust_and_the_launch_hook_maps_it(self):
        src = (ROOT / "0.2.18.1.py").read_text()
        self.assertRegex(src, r'"video_bridge":\s*"rust"')
        self.assertIn('os.environ.setdefault("SINGWS_VIDEO_BRIDGE", "objc" if _video_bridge == "objc" else "rust")', src)

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

    def test_nothing_asked_means_objective_c(self):
        r = choose({})
        self.assertTrue(r.get("kind") == "objc" or "error" in r, r)   # (the Objective-C dylib itself may not load in a bare checkout)
        self.assertNotEqual(r.get("kind"), "rust")

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
