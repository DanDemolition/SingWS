"""Regression tests: tempo and key must be independent on ALL media types.

Live-show bug: changing tempo on MP4 also shifted the key. Root cause: the
transport applied tempo via INSTANT_RATE_CHANGE seeks. scaletempo does not
consume the instant-rate multiplier (it passes through scaletempo AND pitch
untouched), so it reached the audio sink, and GstAudioBaseSink honors rate by
resampling — speed and pitch change together. Only qtdemux (MP4) accepts
instant-rate seeks; CDG/MP3 pipelines reject them and fell back to the
flushing rate seek, which scaletempo consumes correctly (time-stretch, key
kept). The fix removes the instant-rate path so every media type uses the
flushing rate seek.

Two layers:
  * Unit tests (no GStreamer): the transport's modifier path must send only
    flushing rate seeks (never INSTANT_RATE_CHANGE) and must drive key purely
    through the SoundTouch pitch property, identically for mp4/cdg/audio.
  * Integration tests (real GStreamer + ffmpeg, skipped when unavailable):
    play generated 440 Hz sine MP4/MP3 through the transport's audio chain
    and measure the output frequency and length for tempo/key combinations.
"""

import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

from gst_karaoke_transport import (
    GstKaraokeTransport,
    NS_PER_SECOND,
    pitch_ratio_for_semitones,
)


# --------------------------------------------------------------------- unit
class _Recorder:
    def __init__(self, name=""):
        self.name = name
        self.props = {}
        self.prop_history = []

    def set_property(self, key, value):
        self.props[key] = value
        self.prop_history.append((key, value))


class _FakePipeline:
    def __init__(self):
        self.events = []
        self.position_ns = 3 * NS_PER_SECOND

    def send_event(self, event):
        self.events.append(event)
        return True

    def query_position(self, _fmt):
        return True, self.position_ns


class _FakeGst:
    SECOND = NS_PER_SECOND

    class Format:
        TIME = "time"

    class SeekFlags:
        FLUSH = 0x01
        ACCURATE = 0x02
        KEY_UNIT = 0x04
        INSTANT_RATE_CHANGE = 0x8000

    class SeekType:
        SET = "set"
        NONE = "none"

    @staticmethod
    def version():
        return (1, 28, 3, 0)

    class Event:
        @staticmethod
        def new_seek(rate, fmt, flags, cur_type, cur, stop_type, stop):
            return {
                "rate": rate,
                "format": fmt,
                "flags": flags,
                "cur_type": cur_type,
                "cur": cur,
                "stop_type": stop_type,
                "stop": stop,
            }


def make_transport(mode):
    t = GstKaraokeTransport.__new__(GstKaraokeTransport)
    t.Gst = _FakeGst
    t.mode = mode
    t.pipeline = _FakePipeline()
    t.scaletempo = _Recorder("scaletempo")
    t.pitch = _Recorder("pitch")
    t.tempo_ratio = 1.0
    t.semitones = 0.0
    return t


class TempoKeyIndependenceUnitTests(unittest.TestCase):
    def _seeks(self, transport):
        return [e for e in transport.pipeline.events if isinstance(e, dict)]

    def assert_no_instant_rate(self, transport):
        for ev in self._seeks(transport):
            self.assertFalse(
                ev["flags"] & _FakeGst.SeekFlags.INSTANT_RATE_CHANGE,
                f"INSTANT_RATE_CHANGE seek sent on mode={transport.mode}: {ev}",
            )

    def test_tempo_change_never_uses_instant_rate_any_mode(self):
        for mode in ("mp4", "cdg", "audio"):
            t = make_transport(mode)
            t.set_modifiers(1.2, 0)
            seeks = self._seeks(t)
            self.assertEqual(len(seeks), 1, mode)
            self.assert_no_instant_rate(t)
            self.assertEqual(seeks[0]["rate"], 1.2)
            self.assertTrue(seeks[0]["flags"] & _FakeGst.SeekFlags.FLUSH, mode)
            # position is preserved: seek targets the current position
            self.assertEqual(seeks[0]["cur"], t.pipeline.position_ns)

    def test_tempo_change_does_not_touch_pitch(self):
        for mode in ("mp4", "cdg", "audio"):
            t = make_transport(mode)
            t.set_modifiers(1.1, 0)
            # tempo +10% key 0 = faster, same key: pitch stays at unity and
            # the SoundTouch tempo property is never used for speed.
            self.assertEqual(t.pitch.props.get("pitch"), 1.0, mode)
            self.assertNotIn("tempo", t.pitch.props, mode)

    def test_key_change_does_not_send_seek(self):
        for mode in ("mp4", "cdg", "audio"):
            t = make_transport(mode)
            t.set_modifiers(1.0, 2)
            # tempo 0% key +2 = same speed, higher key: pure property set.
            self.assertEqual(self._seeks(t), [], mode)
            self.assertAlmostEqual(
                t.pitch.props["pitch"], pitch_ratio_for_semitones(2), places=9
            )

    def test_combined_change_keeps_paths_separate(self):
        for mode in ("mp4", "cdg", "audio"):
            t = make_transport(mode)
            t.set_modifiers(0.9, -2)
            seeks = self._seeks(t)
            self.assertEqual(len(seeks), 1, mode)
            self.assertEqual(seeks[0]["rate"], 0.9)
            self.assert_no_instant_rate(t)
            self.assertAlmostEqual(
                t.pitch.props["pitch"], pitch_ratio_for_semitones(-2), places=9
            )
            self.assertNotIn("tempo", t.pitch.props, mode)

    def test_scaletempo_retuned_on_tempo_change(self):
        t = make_transport("mp4")
        t.set_modifiers(1.3, 0)
        self.assertIn("stride", t.scaletempo.props)
        self.assertIn("search", t.scaletempo.props)

    def test_mp4_and_cdg_and_mp3_behave_identically(self):
        results = {}
        for mode in ("mp4", "cdg", "audio"):
            t = make_transport(mode)
            t.set_modifiers(1.15, 3)
            results[mode] = (
                [(e["rate"], e["flags"]) for e in self._seeks(t)],
                t.pitch.props.get("pitch"),
            )
        self.assertEqual(results["mp4"], results["cdg"])
        self.assertEqual(results["mp4"], results["audio"])


# -------------------------------------------------------------- integration
def _gst_available():
    try:
        import gi  # noqa: F401
        return shutil.which("ffmpeg") is not None
    except Exception:
        return False


@unittest.skipUnless(_gst_available(), "GStreamer python bindings or ffmpeg unavailable")
class TempoKeyIndependencePipelineTests(unittest.TestCase):
    """Plays real media through the transport's audio element chain and
    measures the output. CDG/MP3+G use the identical audio chain as the MP3
    case (CDG graphics are decoded Python-side, outside GStreamer)."""

    Gst = None

    @classmethod
    def setUpClass(cls):
        plugin_dir = Path(__file__).resolve().parent / "native" / "gst-soundtouch"
        if plugin_dir.exists():
            existing = os.environ.get("GST_PLUGIN_PATH", "")
            if str(plugin_dir) not in existing.split(os.pathsep):
                os.environ["GST_PLUGIN_PATH"] = (
                    str(plugin_dir) + (os.pathsep + existing if existing else "")
                )
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstApp", "1.0")
        from gi.repository import Gst, GstApp  # noqa: F401
        if not Gst.is_initialized():
            Gst.init(None)
        cls.Gst = Gst
        for factory in ("pitch", "scaletempo", "uridecodebin", "appsink"):
            if not Gst.ElementFactory.find(factory):
                raise unittest.SkipTest(f"GStreamer element {factory!r} unavailable")

        cls.tmp = tempfile.TemporaryDirectory()
        cls.mp4 = os.path.join(cls.tmp.name, "tone440.mp4")
        cls.mp3 = os.path.join(cls.tmp.name, "tone440.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-f", "lavfi", "-i", "color=c=black:s=160x120:d=8",
             "-map", "1:v", "-map", "0:a", "-c:v", "h264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", cls.mp4],
            check=True, timeout=120,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-c:a", "libmp3lame", cls.mp3],
            check=True, timeout=120,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ---- pipeline mirror of GstKaraokeTransport's audio bin (appsink tail)
    def _run(self, path, with_video, tempo, semitones):
        Gst = self.Gst
        pipeline = Gst.Pipeline.new("it")
        dec = Gst.ElementFactory.make("uridecodebin", "d")
        dec.set_property("uri", Gst.filename_to_uri(path))
        pipeline.add(dec)

        def mk(factory, name):
            el = Gst.ElementFactory.make(factory, name)
            self.assertIsNotNone(el, factory)
            pipeline.add(el)
            return el

        q = mk("queue", "q")
        conv = mk("audioconvert", "conv")
        resample = mk("audioresample", "resample")
        scaletempo = mk("scaletempo", "scaletempo")
        eq = mk("equalizer-10bands", "eq")
        conv2 = mk("audioconvert", "conv2")
        pitch = mk("pitch", "pitch")
        pitch.set_property("pitch", pitch_ratio_for_semitones(semitones))
        pitch.set_property("tempo", 1.0)
        conv3 = mk("audioconvert", "conv3")
        caps = mk("capsfilter", "caps")
        caps.set_property(
            "caps", Gst.Caps.from_string("audio/x-raw,format=F32LE,channels=1,rate=44100")
        )
        sink = mk("appsink", "sink")
        sink.set_property("sync", False)

        chain = [q, conv, resample, scaletempo, eq, conv2, pitch, conv3, caps, sink]
        for a, b in zip(chain, chain[1:]):
            self.assertTrue(a.link(b), f"{a.get_name()} -> {b.get_name()}")

        vsink = None
        if with_video:
            vq = mk("queue", "vq")
            vconv = mk("videoconvert", "vconv")
            vsink = mk("appsink", "vsink")
            vsink.set_property("sync", False)
            vsink.set_property("max-buffers", 2)
            vsink.set_property("drop", True)
            self.assertTrue(vq.link(vconv) and vconv.link(vsink))

        def on_pad(_d, pad):
            s = pad.get_current_caps().to_string()
            if s.startswith("audio/"):
                pad.link(q.get_static_pad("sink"))
            elif s.startswith("video/") and with_video:
                pad.link(vq.get_static_pad("sink"))

        dec.connect("pad-added", on_pad)
        pipeline.set_state(Gst.State.PAUSED)
        pipeline.get_state(6 * Gst.SECOND)

        # Same flushing rate seek _apply_tempo_rate now sends (from 0 here so
        # output length is exactly predictable).
        if abs(tempo - 1.0) > 1e-6:
            self.assertTrue(pipeline.send_event(Gst.Event.new_seek(
                tempo, Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                Gst.SeekType.SET, 0, Gst.SeekType.NONE, 0,
            )))
        pipeline.set_state(Gst.State.PLAYING)

        data = bytearray()
        deadline = time.time() + 60
        while time.time() < deadline:
            sample = sink.try_pull_sample(int(0.5 * Gst.SECOND))
            if vsink is not None:
                while vsink.try_pull_sample(0) is not None:
                    pass
            if sample is None:
                if sink.get_property("eos"):
                    break
                continue
            buf = sample.get_buffer()
            ok, mi = buf.map(Gst.MapFlags.READ)
            if ok:
                data.extend(mi.data)
                buf.unmap(mi)
        pipeline.set_state(Gst.State.NULL)

        n = len(data) // 4
        self.assertGreater(n, 44100, "no meaningful audio output")
        samples = struct.unpack(f"<{n}f", bytes(data[: n * 4]))
        # dominant frequency via zero crossings over the middle half
        lo, hi = n // 4, 3 * n // 4
        crossings = 0
        prev = samples[lo]
        for s in samples[lo + 1:hi]:
            if (prev < 0) != (s < 0):
                crossings += 1
            prev = s
        freq = crossings / (2.0 * ((hi - lo - 1) / 44100.0))
        return freq, n / 44100.0

    SOURCE_SECONDS = 8.0

    def assert_case(self, path, with_video, tempo, semitones):
        freq, out_seconds = self._run(path, with_video, tempo, semitones)
        expect_freq = 440.0 * math.pow(2.0, semitones / 12.0)
        expect_len = self.SOURCE_SECONDS / tempo
        label = f"{os.path.basename(path)} tempo={tempo} key={semitones:+d}"
        self.assertAlmostEqual(
            freq, expect_freq, delta=expect_freq * 0.03,
            msg=f"{label}: pitch moved with tempo (freq={freq:.1f} Hz)",
        )
        self.assertAlmostEqual(
            out_seconds, expect_len, delta=max(0.8, expect_len * 0.12),
            msg=f"{label}: tempo not applied (length={out_seconds:.2f}s)",
        )

    def test_mp4_tempo_up_keeps_key(self):
        self.assert_case(self.mp4, True, 1.2, 0)

    def test_mp4_key_up_keeps_tempo(self):
        self.assert_case(self.mp4, True, 1.0, 2)

    def test_mp4_tempo_down_and_key_down(self):
        self.assert_case(self.mp4, True, 0.9, -2)

    def test_mp4_tempo_up_and_key_up(self):
        self.assert_case(self.mp4, True, 1.2, 2)

    def test_mp3_comparison_tempo_up_keeps_key(self):
        self.assert_case(self.mp3, False, 1.2, 0)

    def test_mp3_comparison_key_only(self):
        self.assert_case(self.mp3, False, 1.0, 2)


if __name__ == "__main__":
    unittest.main()
