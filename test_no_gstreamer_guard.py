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


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for fallback decode")
class NoGStreamerAudioDecodeTests(unittest.TestCase):
    def test_ffmpeg_signalsmith_fallback_decodes_generated_wav(self):
        os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
        from python_karaoke_transport import _AudioDecodeWorker

        class FakeTransport:
            channels = 2
            sample_rate = 48_000
            source_chunk_frames = 1024
            normalize_gain_db = 0.0
            _raw_pcm_ready = False
            _raw_pcm = None

            def __init__(self):
                self.chunks = []
                self.level_callbacks = 0
                self.error = ""
                self.done = False

            def _queue_pcm(self, payload, _worker):
                self.chunks.append(bytes(payload))

            def _accept_level(self, _payload, _worker):
                self.level_callbacks += 1

            def _mark_decoder_error(self, message):
                self.error = str(message)

            def _mark_decoder_done(self, _worker):
                self.done = True

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "fallback-tone.wav"
            sample_rate = 48_000
            frames = bytearray()
            for index in range(sample_rate // 5):
                sample = int(12_000 * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
                frames.extend(struct.pack("<hh", sample, sample))
            with wave.open(str(wav_path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(frames)

            transport = FakeTransport()
            worker = _AudioDecodeWorker(transport, str(wav_path), 0.0, 1.0, 0.0)
            worker.run()

        self.assertTrue(transport.done)
        self.assertEqual(transport.error, "")
        self.assertGreater(sum(map(len, transport.chunks)), 0)
        self.assertGreater(transport.level_callbacks, 0)


if __name__ == "__main__":
    unittest.main()
