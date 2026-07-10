"""Regression coverage for MP4 streams whose video ends before audio.

Video-branch EOS is normal for muxes with an audio outro.  It must retain the
last decoded frame and never emit the transport's completion signal; only the
whole GStreamer pipeline (audio-master completion) may do that.
"""

import os
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

from PyQt6.QtGui import QImage

from gst_karaoke_transport import GstKaraokeTransport


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, value=None):
        self.emitted.append(value)


class _AppSink:
    def __init__(self, *, eos: bool):
        self.eos = eos

    def emit(self, name, timeout):
        assert name == "try-pull-sample"
        assert timeout == 0
        return None

    def get_property(self, name):
        assert name == "eos"
        return self.eos

    def find_property(self, _name):
        return None


def _transport(*, appsink_eos: bool):
    transport = GstKaraokeTransport.__new__(GstKaraokeTransport)
    transport.appsink = _AppSink(eos=appsink_eos)
    transport.audio_path = "/tmp/audio-outro.mp4"
    transport._video_eof_received = True
    transport._video_eof_drained = False
    transport._video_eof_hold_emitted = False
    transport._video_eof_monotonic = 1.0
    transport._audio_eof_received = False
    transport._last_video_frame_pts_ns = 4_000_000_000
    transport._video_frames_delivered = 120
    transport._last_video_image = QImage(4, 4, QImage.Format.Format_RGBX8888)
    transport._last_video_image.fill(0xFF336699)
    transport.frame_ready = _Signal()
    transport.ended = _Signal()
    return transport


class VideoEofHoldTests(unittest.TestCase):
    def test_video_eof_reasserts_last_frame_without_completing_playback(self):
        transport = _transport(appsink_eos=True)

        transport._pull_video_frame()

        self.assertTrue(transport._video_eof_drained)
        self.assertTrue(transport._video_eof_hold_emitted)
        self.assertEqual(transport.frame_ready.emitted, [transport._last_video_image])
        self.assertEqual(transport.ended.emitted, [])

    def test_upstream_video_eof_waits_for_appsink_to_drain_final_samples(self):
        transport = _transport(appsink_eos=False)

        transport._pull_video_frame()

        self.assertFalse(transport._video_eof_drained)
        self.assertFalse(transport._video_eof_hold_emitted)
        self.assertEqual(transport.frame_ready.emitted, [])

    def test_hold_is_emitted_once(self):
        transport = _transport(appsink_eos=True)

        transport._pull_video_frame()
        transport._pull_video_frame()

        self.assertEqual(len(transport.frame_ready.emitted), 1)


if __name__ == "__main__":
    unittest.main()
