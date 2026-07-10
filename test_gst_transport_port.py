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


def _cdg_pkt(instr, data16):
    p = bytearray(24)
    p[0] = 0x09
    p[1] = instr
    p[4:4 + len(data16)] = data16
    return bytes(p)


def _tile(row, col, bits=0x3F, c0=0, c1=1, xor=False):
    return _cdg_pkt(38 if xor else 6, bytes([c0, c1, row, col] + [bits] * 12))


def _write_stream(path, packets):
    Path(path).write_bytes(b"".join(packets))


class CdgNoLookaheadTests(unittest.TestCase):
    """Regression: the direct-QImage presentation path must never display CDG
    state from beyond the playback position. Before 2026-07-10 the adapter
    stepped the appsrc frame iterator, whose one-tick frame durations made it
    fetch the NEXT change frame during idle gaps — the next lyric line's first
    packet batch appeared seconds early as garbage blocks at line starts."""

    EMPTY = bytes(24)

    def _adapter_for(self, packets):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = os.path.join(self.tmp.name, "t.cdg")
        _write_stream(path, packets)
        return _CdgAdapter(path)

    def _gap_track(self):
        """preset+palette @0.5s, tile A @1.5s, 4s gap, tile B @5.5s."""
        pkts = [self.EMPTY] * 150
        pkts.append(_cdg_pkt(1, bytes([0, 0] + [0] * 14)))
        pkts.append(_cdg_pkt(30, struct.pack(">8H", *[0x0FFF] * 8)))
        pkts += [self.EMPTY] * 298
        pkts.append(_tile(6, 6))               # packet 450 -> 1500ms
        pkts += [self.EMPTY] * 1199
        pkts.append(_tile(10, 20))             # packet 1650 -> 5500ms
        pkts += [self.EMPTY] * 300
        return pkts

    @staticmethod
    def _tile_b_lit(img):
        # tile row 10 col 20 -> surface (120,120); crop offset (-6,-12)
        return img is not None and img.pixelIndex(120 - 6 + 3, 120 - 12 + 6) != 0

    def test_future_frames_never_presented_early(self):
        adapter = self._adapter_for(self._gap_track())
        last = None
        for ms in range(0, 6100, 16):
            img = adapter.frame_for_position_ms(ms)
            if img is not None:
                last = img
            # The decoder must never have consumed packets beyond those due.
            self.assertLessEqual(adapter.reader._next_idx, (ms * 300) // 1000)
            if ms < 5500:
                self.assertFalse(
                    self._tile_b_lit(last),
                    f"tile due at 5500ms visible at {ms}ms",
                )
        self.assertTrue(self._tile_b_lit(last))

    def test_consecutive_tiles_in_one_batch_publish_together(self):
        """Several tile blocks due within a single poll interval (near-equal
        presentation times) must land in ONE published frame, fully drawn."""
        pkts = [self.EMPTY] * 300
        for i in range(6):                     # packets 300-305, all ~1000ms
            pkts.append(_tile(4, 10 + i))
        pkts += [self.EMPTY] * 300
        adapter = self._adapter_for(pkts)
        adapter.frame_for_position_ms(900)     # black baseline
        img = adapter.frame_for_position_ms(1100)
        self.assertIsNotNone(img)
        for i in range(6):
            x = (10 + i) * 6 - 6 + 3
            self.assertEqual(img.pixelIndex(x, 4 * 12 - 12 + 6), 1)

    def test_memory_preset_then_tiles_same_batch(self):
        pkts = [self.EMPTY] * 300
        pkts.append(_cdg_pkt(1, bytes([2, 0] + [0] * 14)))  # clear to color 2
        pkts.append(_tile(4, 10, c0=2, c1=5))
        adapter = self._adapter_for(pkts + [self.EMPTY] * 300)
        img = adapter.frame_for_position_ms(1100)
        self.assertEqual(img.pixelIndex(0, 0), 2)                    # cleared
        self.assertEqual(img.pixelIndex(10 * 6 - 6 + 3, 4 * 12 - 12 + 6), 5)

    def test_palette_change_immediately_before_draw(self):
        pkts = [self.EMPTY] * 300
        # CDG color word: byte0 = 00rrrrgg, byte1 = 00ggbbbb -> red F = 0x3C00
        pkts.append(_cdg_pkt(30, struct.pack(">8H", *[0x3C00] * 8)))
        pkts.append(_tile(4, 10))
        adapter = self._adapter_for(pkts + [self.EMPTY] * 300)
        img = adapter.frame_for_position_ms(1100)
        color = img.colorTable()[img.pixelIndex(10 * 6 - 6 + 3, 4 * 12 - 12 + 6)]
        self.assertEqual(color & 0x00FFFFFF, 0x00FF0000)  # opaque red

    def test_seek_backward_matches_fresh_decode(self):
        adapter = self._adapter_for(self._gap_track())
        adapter.frame_for_position_ms(6000)    # decode everything
        adapter.seek_seconds(2.0)
        replayed = bytes(adapter.reader.current_frame())
        fresh = _CdgAdapter(os.path.join(self.tmp.name, "t.cdg"))
        fresh.frame_for_position_ms(2000)
        self.assertEqual(replayed, bytes(fresh.reader.current_frame()))

    def test_seek_presents_target_frame_not_stale_image(self):
        """After a backward seek into a stretch with no upcoming changes, the
        published frame must reflect the seek target, not the pre-seek image."""
        adapter = self._adapter_for(self._gap_track())
        img = adapter.frame_for_position_ms(6000)
        self.assertTrue(self._tile_b_lit(img))
        adapter.seek_seconds(0.2)              # before anything is drawn
        img = adapter.frame_for_position_ms(210, force=True)
        self.assertFalse(self._tile_b_lit(img))

    def test_rewind_resets_all_state(self):
        adapter = self._adapter_for(self._gap_track())
        adapter.frame_for_position_ms(6000)
        r = adapter.reader
        r.rewind()
        self.assertEqual((r._next_idx, r._cur_idx), (0, 0))
        from cdg_native import FRAME_BYTES, PALETTE_BYTES, CROP_W, CROP_H
        frame = r.current_frame()
        self.assertEqual(len(frame), FRAME_BYTES)
        self.assertEqual(frame[: CROP_W * CROP_H], bytes(CROP_W * CROP_H))
        # palette back to opaque black
        pal = frame[CROP_W * CROP_H:]
        for i in range(16):
            self.assertEqual(pal[i * 4:(i + 1) * 4], bytes((0, 0, 0, 255)))


class CdgSurfaceCommandTests(unittest.TestCase):
    """Exact command behavior on the raw 300x216 surface."""

    def _surface(self):
        from cdg_native import CdgSurface
        return CdgSurface()

    def test_xor_tile_xors_existing_indices(self):
        s = self._surface()
        s.apply_packet(_tile(4, 10, bits=0x3F, c0=0, c1=0x05))       # set 5
        s.apply_packet(_tile(4, 10, bits=0x3F, c0=0, c1=0x03, xor=True))
        from cdg_native import FULL_W
        self.assertEqual(s.pixels[(4 * 12) * FULL_W + 10 * 6], 5 ^ 3)

    def test_border_preset_leaves_visible_interior_untouched(self):
        """Regression: the bottom border band started one row early (203) and
        wiped the last visible pixel row on every border preset."""
        s = self._surface()
        s.apply_packet(_cdg_pkt(1, bytes([5, 0] + [0] * 14)))  # fill color 5
        s.apply_packet(_cdg_pkt(2, bytes([9] + [0] * 15)))     # border color 9
        from cdg_native import FULL_W
        for y in (12, 107, 203):                # first, middle, last visible
            row = s.pixels[y * FULL_W + 6: y * FULL_W + 294]
            self.assertEqual(row, bytes([5] * 288), f"row {y} corrupted")
        for y in (0, 11, 204, 215):             # border rows repainted
            self.assertEqual(s.pixels[y * FULL_W], 9)
        self.assertEqual(s.pixels[12 * FULL_W], 9)      # left edge
        self.assertEqual(s.pixels[12 * FULL_W + 299], 9)  # right edge

    def test_scroll_preset_fills_no_stale_edges(self):
        s = self._surface()
        s.apply_packet(_cdg_pkt(1, bytes([7, 0] + [0] * 14)))
        s.apply_packet(_cdg_pkt(20, bytes([3, 0x20, 0] + [0] * 13)))  # left 6px
        from cdg_native import FULL_W
        for y in range(216):
            self.assertEqual(
                s.pixels[y * FULL_W + 294: (y + 1) * FULL_W], bytes([3] * 6)
            )
            self.assertEqual(s.pixels[y * FULL_W], 7)

    def test_scroll_copy_wraps_pixels(self):
        s = self._surface()
        s.apply_packet(_tile(0, 0, bits=0x3F, c0=0, c1=4))  # tile at far left
        s.apply_packet(_cdg_pkt(24, bytes([0, 0x20, 0] + [0] * 13)))  # copy left
        from cdg_native import FULL_W
        # the leftmost 6 columns wrapped to the right edge
        self.assertEqual(s.pixels[294:300], bytes([4] * 6))

    def test_color_index_masking(self):
        s = self._surface()
        # color fields carry junk in the high nibble; must be masked to 0-15
        s.apply_packet(_cdg_pkt(6, bytes([0xF0 | 2, 0xF0 | 7, 4, 10] + [0x3F] * 12)))
        from cdg_native import FULL_W
        self.assertEqual(s.pixels[(4 * 12) * FULL_W + 10 * 6], 7)


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
