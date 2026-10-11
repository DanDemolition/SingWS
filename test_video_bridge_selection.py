"""Which video bridge mpv_playback_iina loads (roadmap item 2, milestone V6): the Objective-C one by default, the Rust one only when asked, and
ANY problem with the Rust one falls back to the Objective-C one with the reason. Selection runs in a subprocess per case (module singletons)."""
import json
import os
import re
import subprocess
import sys
import textwrap
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
            self.assertEqual(have, set(m.BRIDGE_EXPORTS))

    def test_default_setting_and_launch_hook_are_objective_c(self):
        src = (ROOT / "0.2.18.1.py").read_text()
        self.assertRegex(src, r'"video_bridge":\s*"objc"')
        self.assertIn('os.environ.setdefault("SINGWS_VIDEO_BRIDGE"', src)

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
