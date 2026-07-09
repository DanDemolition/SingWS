"""Tests for the OpenKJ GStreamer port: pitch math, scaletempo tuning, the
CDG adapter (sampled QImage frames from the hardened cdg_native
decoder, seek generations, end gate, corrupt-file tolerance), and the brand
parser rules. None of these need GStreamer to run."""

import os
import struct
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

from gst_karaoke_transport import (
    _CdgAdapter,
    optimize_scaletempo_for_rate,
    pitch_ratio_for_semitones,
)
import okj_fileinfo


def _write_test_cdg(path: str, seconds: int = 6):
    """Memory preset + palette at 0.5s, then one visible tile per second."""
    def pkt(instr, data16):
        p = bytearray(24)
        p[0] = 0x09
        p[1] = instr
        p[4:4 + len(data16)] = data16
        return bytes(p)

    empty = bytes(24)
    stream = [empty] * 150
    stream.append(pkt(1, bytes([0, 0] + [0] * 14)))
    stream.append(pkt(30, struct.pack(">8H", *[0x0FFF] * 8)))
    for sec in range(1, seconds):
        stream += [empty] * (300 - 1)
        tile = bytes([0, 1, 5 + sec, 5 + sec] + [0x3F] * 12)
        stream.append(pkt(6, tile))
    Path(path).write_bytes(b"".join(stream))


class PitchMathTests(unittest.TestCase):
    def test_zero_semitones_is_unity(self):
        self.assertEqual(pitch_ratio_for_semitones(0), 1.0)

    def test_octave_up(self):
        self.assertAlmostEqual(pitch_ratio_for_semitones(12), 2.0, places=6)

    def test_negative_uses_openkj_formula(self):
        # OpenKJ's down formula: 1 - ((100 - stdn^n*100)/100) == stdn^n
        self.assertAlmostEqual(pitch_ratio_for_semitones(-12), 0.94387431268169349664191315666784 ** 12, places=9)

    def test_monotonic(self):
        vals = [pitch_ratio_for_semitones(s) for s in range(-12, 13)]
        self.assertEqual(vals, sorted(vals))


class FakeScaletempo:
    def __init__(self):
        self.props = {}

    def set_property(self, name, value):
        self.props[name] = value


class ScaletempoTuningTests(unittest.TestCase):
    def test_normal_rate(self):
        el = FakeScaletempo()
        optimize_scaletempo_for_rate(el, 1.0)
        self.assertIn("stride", el.props)
        self.assertIn("search", el.props)
        self.assertGreater(el.props["stride"], el.props["search"])

    def test_extremes_clamped(self):
        el = FakeScaletempo()
        optimize_scaletempo_for_rate(el, 5.0)
        self.assertGreaterEqual(el.props["stride"], 40)
        self.assertGreaterEqual(el.props["search"], 15)


class CdgAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cdg_path = os.path.join(self.tmp.name, "test.cdg")
        _write_test_cdg(self.cdg_path, seconds=6)
        self.adapter = _CdgAdapter(self.cdg_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_duration(self):
        self.assertAlmostEqual(self.adapter.duration_seconds, 5.5, delta=0.6)

    def test_change_driven_frames(self):
        """Polling every 50ms must emit far fewer frames than polls — only
        actual pixel changes produce a new frame."""
        frames = 0
        for ms in range(0, 6000, 50):
            if self.adapter.frame_for_position_ms(ms) is not None:
                frames += 1
        self.assertGreaterEqual(frames, 4)
        self.assertLessEqual(frames, 16)

    def test_forced_frames_repeat_current_state_without_advancing_early(self):
        first = self.adapter.frame_for_position_ms(1000, force=True)
        pos_after_first = self.adapter.reader.current_frame_position_ms()
        second = self.adapter.frame_for_position_ms(1005, force=True)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(self.adapter.reader.current_frame_position_ms(), pos_after_first)
        # Normal polling at the same timestamp still suppresses duplicate
        # frames after a forced sample.
        self.assertIsNone(self.adapter.frame_for_position_ms(1005))

    def test_frame_is_indexed_qimage(self):
        image = self.adapter.frame_for_position_ms(2000)
        self.assertIsNotNone(image)
        self.assertEqual((image.width(), image.height()), (288, 192))
        from PyQt6.QtGui import QImage
        self.assertEqual(image.format(), QImage.Format.Format_Indexed8)
        self.assertEqual(len(image.colorTable()), 16)

    def test_sidefill_adapter_keeps_indexed_frame_and_widens_only(self):
        adapter = _CdgAdapter(self.cdg_path, sidefill=True)
        image = adapter.frame_for_position_ms(2000)
        self.assertIsNotNone(image)
        self.assertEqual((image.width(), image.height()), (340, 192))
        from PyQt6.QtGui import QImage
        self.assertEqual(image.format(), QImage.Format.Format_Indexed8)
        self.assertEqual(len(image.colorTable()), 16)

    def test_seek_bumps_generation_and_replays(self):
        g0 = self.adapter.generation
        self.adapter.frame_for_position_ms(4000)
        self.adapter.seek_seconds(1.0)
        self.assertEqual(self.adapter.generation, g0 + 1)
        image = self.adapter.frame_for_position_ms(1500)
        self.assertIsNotNone(image)

    def test_sectors_remaining_decreases(self):
        early = self.adapter.sectors_remaining(1.0)
        late = self.adapter.sectors_remaining(5.0)
        self.assertGreater(early, late)
        self.assertGreaterEqual(late, 0.0)

    def test_final_frame_known_after_full_scan(self):
        # Consume everything: the reader learns the final visible frame at EOF.
        self.adapter.frame_for_position_ms(10_000)
        final_ms = self.adapter.reader.position_of_final_frame_ms()
        self.assertGreater(final_ms, 4000)
        self.assertLess(final_ms, 6001)

class CdgCorruptionToleranceTests(unittest.TestCase):
    """The decoder must survive damaged rips: out-of-range tiles, corrupt
    scroll offsets, and truncated trailing packets."""

    def _reader_for(self, payload: bytes):
        from cdg_native import CdgFileReader
        import tempfile, os as _os
        fd, path = tempfile.mkstemp(suffix=".cdg")
        with _os.fdopen(fd, "wb") as f:
            f.write(payload)
        self.addCleanup(_os.unlink, path)
        return CdgFileReader(path)

    @staticmethod
    def _pkt(instr, data16):
        p = bytearray(24)
        p[0] = 0x09
        p[1] = instr
        p[4:4 + len(data16)] = data16
        return bytes(p)

    def test_out_of_range_tile_is_rejected(self):
        bad_tile = self._pkt(6, bytes([0, 1, 30, 60] + [0x3F] * 12))  # row 30 col 60
        good_tile = self._pkt(6, bytes([0, 1, 4, 4] + [0x3F] * 12))
        reader = self._reader_for(bytes(24) * 10 + bad_tile + good_tile + bytes(24) * 10)
        frames = 0
        while reader.move_to_next_frame():
            frames += 1
        self.assertGreaterEqual(frames, 1)  # good tile still renders

    def test_truncated_trailing_packet_ignored(self):
        tile = self._pkt(6, bytes([0, 1, 4, 4] + [0x3F] * 12))
        payload = bytes(24) * 5 + tile + b"\x09\x06\x00"  # 3 stray bytes
        reader = self._reader_for(payload)
        self.assertEqual(reader._total_packets, 6)
        while reader.move_to_next_frame():
            pass  # must terminate cleanly

    def test_corrupt_scroll_offsets_clamped(self):
        scroll = self._pkt(20, bytes([0, 0x0F, 0x0F] + [0] * 13))  # max offsets
        reader = self._reader_for(bytes(24) * 5 + scroll + bytes(24) * 5)
        while reader.move_to_next_frame():
            pass
        frame = reader.current_frame()
        from cdg_native import FRAME_BYTES
        self.assertEqual(len(frame), FRAME_BYTES)  # crop stayed on-surface

    def test_sidefill_preserves_cropped_center_pixels(self):
        payload = (
            bytes(24) * 5
            + self._pkt(1, bytes([0, 0] + [0] * 14))
            + self._pkt(2, bytes([2] + [0] * 15))
            + self._pkt(6, bytes([0, 1, 4, 4] + [0x3F] * 12))
            + bytes(24) * 5
        )
        import tempfile, os as _os
        from cdg_native import CdgFileReader
        fd, path = tempfile.mkstemp(suffix=".cdg")
        with _os.fdopen(fd, "wb") as f:
            f.write(payload)
        self.addCleanup(_os.unlink, path)
        cropped = CdgFileReader(path)
        sidefill = CdgFileReader(path, sidefill=True)
        cropped.move_to_next_frame()
        sidefill.move_to_next_frame()
        from cdg_native import CROP_W, CROP_H, SIDEFILL_W, SIDEFILL_PAD_X
        crop_plane = cropped.current_frame()[: CROP_W * CROP_H]
        side_plane = sidefill.current_frame()[: SIDEFILL_W * CROP_H]
        for y in range(CROP_H):
            crop_row = crop_plane[y * CROP_W:(y + 1) * CROP_W]
            side_row = side_plane[y * SIDEFILL_W + SIDEFILL_PAD_X:y * SIDEFILL_W + SIDEFILL_PAD_X + CROP_W]
            self.assertEqual(side_row, crop_row)


class BrandParserTests(unittest.TestCase):
    def test_cc_and_cb_are_separate_brands(self):
        self.assertEqual(okj_fileinfo.brand_of("CC"), "CC")
        self.assertEqual(okj_fileinfo.brand_of("CB12345"), "CB")
        self.assertNotEqual(okj_fileinfo.brand_of("CB12345"), okj_fileinfo.brand_of("CC"))

    def test_bare_tokens_match_library_convention(self):
        for token, brand in [
            ("KV", "KV"), ("KARAFUN", "KARAFUN"), ("CC", "CC"), ("SC", "SC"),
            ("PHM", "PHM"), ("ZOOM", "ZOOM"), ("SF", "SF"), ("PYT", "PYT"),
            ("SBI", "SBI"), ("TH", "TH"),
        ]:
            self.assertEqual(okj_fileinfo.brand_of(token), brand, token)

    def test_kv_preferred_over_sc_and_sf(self):
        self.assertEqual(okj_fileinfo.pick_preferred(["SC8812", "KV-12345", "SFG042"]), "KV-12345")

    def test_unknown_returns_none(self):
        self.assertIsNone(okj_fileinfo.brand_of("My Cool Home Track"))


if __name__ == "__main__":
    unittest.main()
