"""Regression guards proving GStreamer has been fully removed from SingWS.

GStreamer is gone: the native mpv transport is the sole karaoke engine.
These tests fail clearly if any GStreamer dependency is reintroduced — a
resurrected module, an ``import gi`` in the app, or a build spec that starts
bundling gstreamer plugins again.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parent

_REMOVED_MODULES = (
    "gst_karaoke_transport.py",
    "gst_bootstrap.py",
    "okj_audio_backend.py",
    "cdg_native.py",
    "native/gst-soundtouch",
)


class GStreamerRemovedGuardTests(unittest.TestCase):
    def test_deleted_gstreamer_modules_stay_deleted(self):
        for rel in _REMOVED_MODULES:
            self.assertFalse((ROOT / rel).exists(), f"{rel} should not exist after GStreamer removal")

    def test_main_source_never_imports_gi_or_gst_engine(self):
        src = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn("import gi", src)
        self.assertNotIn("from gi.repository", src)
        self.assertNotIn("from gst_karaoke_transport", src)
        self.assertNotIn("gi.require_version", src)

    def test_specs_exclude_gi_and_bundle_no_gst_plugins(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = (ROOT / spec).read_text(encoding="utf-8", errors="ignore")
            self.assertIn("'gi'", text, f"{spec} must exclude gi")
            self.assertNotIn("gst_plugins", text.replace("gstreamer plugins", ""),
                             f"{spec} must not bundle gst_plugins")
            self.assertNotIn("GStreamer.framework", text, f"{spec} must not reference GStreamer.framework")


class NoGStreamerImportGuardTests(unittest.TestCase):
    def test_main_imports_with_gi_blocked_in_explicit_no_gstreamer_mode(self):
        code = r'''
import importlib.abc
import importlib.util
import os
from pathlib import Path
import sys

class BlockGI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "gi" or fullname.startswith("gi."):
            raise ImportError("gi deliberately blocked by no-GStreamer guard")
        return None

sys.meta_path.insert(0, BlockGI())
os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for name in tuple(os.environ):
    if name.startswith("GST_") or name in {
        "GI_TYPELIB_PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
    }:
        os.environ.pop(name, None)

entry = Path("0.2.18.1.py").resolve()
spec = importlib.util.spec_from_file_location("singws_no_gstreamer_guard", entry)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert module.Gst is None, "GStreamer unexpectedly initialized"
assert module.GstVideo is None, "GstVideo unexpectedly initialized"
assert module.GstKaraokeTransport is None, "GStreamer transport unexpectedly available"
assert not hasattr(module, "PythonKaraokeTransport"), "retired Python transport returned"

# Lead-silence analysis must work with gi blocked (FFmpeg scan, not the old
# GStreamer level pipeline).
import math
import struct
import tempfile
import wave

sr = 16000
tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
with wave.open(tmp, "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(sr)
    chunk = bytearray(b"\x00\x00\x00\x00" * int(sr * 1.2))
    for i in range(sr):
        v = int(0.5 * 32767.0 * math.sin(2.0 * math.pi * 440.0 * i / sr))
        chunk += struct.pack("<hh", v, v)
    w.writeframes(bytes(chunk))
tmp.close()
try:
    lead = float(module.detect_lead_silence(tmp.name))
finally:
    os.unlink(tmp.name)
assert 1.0 <= lead <= 1.4, f"lead-silence scan wrong without GStreamer: {lead}"
'''
        env = os.environ.copy()
        env["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"no-GStreamer import guard failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
