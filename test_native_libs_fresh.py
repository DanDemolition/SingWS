"""tools/check_native_libs_fresh.py: a Rust library older than the newest commit to its crate is stale (fatal for DSP, a warning for bgm and video)."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("check_native_libs_fresh", Path(__file__).parent / "tools" / "check_native_libs_fresh.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class NativeLibsFresh(unittest.TestCase):
    def run_check(self, lib_mtime, commit_time, present=("singws_dsp/arm64/libsingws_dsp_ffi.dylib", "singws_bgm/arm64/libsingws_bgm_ffi.dylib"), require=False):
        with tempfile.TemporaryDirectory() as d:
            for rel in present:
                path = Path(d) / "native" / rel
                path.parent.mkdir(parents=True)
                path.write_bytes(b"x")
                os.utime(path, (lib_mtime, lib_mtime))
            with mock.patch.object(tool, "ROOT", Path(d)), mock.patch.object(tool, "local_crates", lambda c: {c}), mock.patch.object(tool, "newest_commit", lambda c: commit_time):
                return tool.check(("arm64",), require)

    def test_a_library_newer_than_its_source_is_current(self):
        self.assertEqual(self.run_check(2000, 1000), ([], []))

    def test_a_stale_dsp_library_is_fatal_and_a_stale_bgm_library_only_warns(self):
        stale, warn = self.run_check(1000, 2000)
        self.assertEqual(len(stale), 1)
        self.assertIn("libsingws_dsp_ffi", stale[0])
        self.assertIn("rust/build_dsp.sh", stale[0])
        self.assertEqual(len(warn), 1)
        self.assertIn("libsingws_bgm_ffi", warn[0])

    def test_a_missing_library_fails_only_with_require(self):
        self.assertEqual(self.run_check(2000, 1000, present=(), require=False), ([], []))
        stale, _ = self.run_check(2000, 1000, present=(), require=True)
        self.assertTrue(any("MISSING" in s and "dsp" in s for s in stale))


if __name__ == "__main__":
    unittest.main()
