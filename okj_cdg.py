"""
okj_cdg.py
----------
Python/NumPy port of OpenKJ's CDG decoder (src/cdg/ in OpenKJ 2.1.39):

  * CdgImageFrame  -> CdgFrame:   indexed 8-bit frame + 16-color palette,
                                  subcode ops as NumPy slice writes
  * CdgFileReader  -> CdgReader:  reads whole file, collapses the 300 pkt/s
                                  stream into frames ONLY when pixels actually
                                  change, capped at 60 fps; per-frame PTS +
                                  duration; instant seeks by subcode replay

This is the design that makes OpenKJ's CDG rendering nearly free:
during static lyrics nothing is emitted at all, and RGB conversion is a
single palette lookup done only at display time.

Typical render loop:

    reader = CdgReader("song.cdg")
    while reader.move_to_next_frame():
        rgb = reader.current_frame_rgb()           # (192, 288, 3) uint8
        pts_ms = reader.current_frame_position_ms()
        dur_ms = reader.current_frame_duration_ms()
        present(rgb, at=pts_ms)                    # your sink/display

No per-frame Python pixel loops; everything hot is memoryview/NumPy.
"""

from __future__ import annotations

import numpy as np

# --- CDG constants (libCDG.h) ------------------------------------------------
PACKETS_PER_SECOND = 300
MAX_FPS = 60
MIN_PACKETS_BEFORE_NEW_FRAME = PACKETS_PER_SECOND // MAX_FPS  # 5

FULL_W, FULL_H = 300, 216          # full frame incl. border
CROP_W, CROP_H = 288, 192          # visible area
BORDER_X, BORDER_Y = 6, 12

PACKET_SIZE = 24                   # bytes per subcode packet
SC_MASK = 0x3F
SC_COMMAND = 0x09

CMD_MEMORY_PRESET = 1
CMD_BORDER_PRESET = 2
CMD_TILE_BLOCK = 6
CMD_SCROLL_PRESET = 20
CMD_SCROLL_COPY = 24
CMD_DEFINE_TRANS = 28
CMD_COLORS_LOW = 30
CMD_COLORS_HIGH = 31
CMD_TILE_BLOCK_XOR = 38

_TILE_MASKS = np.array([0x20, 0x10, 0x08, 0x04, 0x02, 0x01], dtype=np.uint8)


class CdgFrame:
    """Port of CdgImageFrame: one indexed frame + palette, mutated in place."""

    def __init__(self):
        self.pixels = np.zeros((FULL_H, FULL_W), dtype=np.uint8)
        self.palette = np.zeros((16, 3), dtype=np.uint8)  # RGB
        self.h_offset = 0
        self.v_offset = 0
        self._last_was_mempreset = False

    # ------------------------------------------------------------- subcode
    def apply_subcode(self, pkt: memoryview) -> bool:
        """Apply one 24-byte packet. Returns True if visible pixels changed."""
        if (pkt[0] & SC_MASK) != SC_COMMAND:
            return False
        instr = pkt[1] & SC_MASK
        data = pkt[4:20]
        updated = False

        if instr == CMD_MEMORY_PRESET:
            updated = self._cmd_memory_preset(data)
        elif instr == CMD_BORDER_PRESET:
            self._cmd_border_preset(data)
            updated = True
        elif instr == CMD_TILE_BLOCK:
            updated = self._cmd_tile_block(data, xor=False)
        elif instr == CMD_SCROLL_PRESET:
            self._cmd_scroll(data, copy=False)
            updated = True
        elif instr == CMD_SCROLL_COPY:
            self._cmd_scroll(data, copy=True)
            updated = True
        elif instr == CMD_COLORS_LOW:
            updated = self._cmd_colors(data, high=False)
        elif instr == CMD_COLORS_HIGH:
            updated = self._cmd_colors(data, high=True)
        elif instr == CMD_TILE_BLOCK_XOR:
            updated = self._cmd_tile_block(data, xor=True)
        # CMD_DEFINE_TRANS: unsupported in OpenKJ too (rare, spec-unclear)

        self._last_was_mempreset = instr == CMD_MEMORY_PRESET
        return updated

    # ------------------------------------------------------------ commands
    def _cmd_memory_preset(self, d) -> bool:
        color, repeat = d[0] & 0x0F, d[1] & 0x0F
        if color >= 16:
            return False
        if self._last_was_mempreset and repeat:
            return False  # OpenKJ: skip redundant repeated presets
        self.pixels.fill(color)
        return True

    def _cmd_border_preset(self, d):
        color = d[0] & 0x0F
        if color >= 16:
            return
        px = self.pixels
        px[:BORDER_Y, :] = color
        px[FULL_H - 13:, :] = color          # lines > 202 (OpenKJ parity)
        px[:, :BORDER_X] = color
        px[:, FULL_W - BORDER_X:] = color

    def _cmd_colors(self, d, high: bool) -> bool:
        """8 palette entries, 2 bytes each: RRRRGGGG GGBBBB (4 bits/chan)."""
        base = 8 if high else 0
        changed = False
        for i in range(8):
            b0, b1 = d[i * 2], d[i * 2 + 1]
            r = (b0 & 0x3C) >> 2
            g = ((b0 & 0x03) << 2) | ((b1 & 0x30) >> 4)
            b = b1 & 0x0F
            rgb = np.array([r * 17, g * 17, b * 17], dtype=np.uint8)  # 4->8 bit
            if not np.array_equal(self.palette[base + i], rgb):
                self.palette[base + i] = rgb
                changed = True
        return changed

    def _cmd_tile_block(self, d, xor: bool) -> bool:
        color0 = d[0] & 0x0F
        color1 = d[1] & 0x0F
        row = d[2] & 0x1F
        col = d[3] & 0x3F
        if row >= 18 or col >= 50 or color0 >= 16 or color1 >= 16:
            return False  # corrupted packet guard, same as OpenKJ
        top, left = row * 12, col * 6

        rows = np.frombuffer(bytes(d[4:16]), dtype=np.uint8)          # (12,)
        bits = (rows[:, None] & _TILE_MASKS[None, :]) != 0            # (12, 6)
        tile = np.where(bits, np.uint8(color1), np.uint8(color0))

        target = self.pixels[top:top + 12, left:left + 6]
        if xor:
            target ^= tile
        else:
            target[:] = tile
        return True

    def _cmd_scroll(self, d, copy: bool):
        color = d[0] & 0x0F
        h_cmd, h_off = (d[1] & 0x30) >> 4, d[1] & 0x07
        v_cmd, v_off = (d[2] & 0x30) >> 4, d[2] & 0x0F
        px = self.pixels

        if h_cmd == 2:    # left 6px
            edge = px[:, :6].copy()
            px[:, :-6] = px[:, 6:]
            px[:, -6:] = edge if copy else color
        elif h_cmd == 1:  # right 6px
            edge = px[:, -6:].copy()
            px[:, 6:] = px[:, :-6]
            px[:, :6] = edge if copy else color
        if v_cmd == 2:    # up 12px
            edge = px[:12, :].copy()
            px[:-12, :] = px[12:, :]
            px[-12:, :] = edge if copy else color
        elif v_cmd == 1:  # down 12px
            edge = px[-12:, :].copy()
            px[12:, :] = px[:-12, :]
            px[:12, :] = edge if copy else color

        self.h_offset, self.v_offset = h_off, v_off

    # ------------------------------------------------------------- output
    def cropped_indexed(self) -> np.ndarray:
        """Visible 192x288 indexed view (honors smooth-scroll offsets)."""
        y0 = BORDER_Y + self.v_offset
        x0 = BORDER_X + self.h_offset
        return self.pixels[y0:y0 + CROP_H, x0:x0 + CROP_W]

    def cropped_rgb(self) -> np.ndarray:
        """(192, 288, 3) uint8 RGB — the ONLY palette->RGB conversion,
        one fancy-index op, done only when a frame is actually displayed."""
        return self.palette[self.cropped_indexed()]


class CdgReader:
    """Port of CdgFileReader: whole-file in memory, change-driven frames,
    60 fps cap, PTS/duration per frame, instant replay-based seeking."""

    def __init__(self, filename: str):
        with open(filename, "rb") as f:
            self._data = memoryview(f.read())
        self._n_packets = len(self._data) // PACKET_SIZE
        self.rewind()

    # ----------------------------------------------------------- position
    def total_duration_ms(self) -> int:
        return self._pkts_to_ms(self._n_packets)

    def current_frame_position_ms(self) -> int:
        return self._pkts_to_ms(self._cur_pkt_idx)

    def current_frame_duration_ms(self) -> int:
        return self._pkts_to_ms(self._next_pkt_idx - self._cur_pkt_idx)

    def position_of_final_frame_ms(self) -> int:
        return self._pkts_to_ms(self._last_change_idx) if self._is_eof() else -1

    # ------------------------------------------------------------ frames
    def move_to_next_frame(self) -> bool:
        """Advance to the next VISIBLY DIFFERENT frame (<= 60 fps).

        Returns False when the final frame has been reached. This is the
        heart of OpenKJ's low CPU use: no change -> no frame -> no render.
        """
        if self._cur_pkt_idx == 0:
            while not self._is_eof() and not self._process_next_packet():
                pass

        # snapshot next -> current
        self._cur_indexed = self._frame.cropped_indexed().copy()
        self._cur_palette = self._frame.palette.copy()
        self._cur_pkt_idx = self._next_pkt_idx

        changed = False
        while True:
            if self._is_eof():
                return self._cur_pkt_idx != self._next_pkt_idx
            if changed and (self._next_pkt_idx - self._cur_pkt_idx) >= MIN_PACKETS_BEFORE_NEW_FRAME:
                return True
            if self._process_next_packet():
                changed = True
                self._last_change_idx = self._next_pkt_idx

    def current_frame_rgb(self) -> np.ndarray:
        """Current frame as (192, 288, 3) RGB uint8."""
        return self._cur_palette[self._cur_indexed]

    def current_frame_indexed(self) -> tuple[np.ndarray, np.ndarray]:
        """(pixels, palette) if your sink can take paletted data directly
        (e.g. GStreamer appsrc with RGB8P caps, like OpenKJ does)."""
        return self._cur_indexed, self._cur_palette

    # -------------------------------------------------------------- seek
    def seek_ms(self, position_ms: int) -> bool:
        """Instant seek: rewind if needed, then replay subcodes (no render).

        Replaying is pure array ops — a full song replays in a few ms.
        """
        pkt_idx = (position_ms * PACKETS_PER_SECOND) // 1000
        if pkt_idx > self._n_packets:
            return False
        if pkt_idx < self._cur_pkt_idx:
            self.rewind()
        while self._next_pkt_idx < pkt_idx:
            self._process_next_packet()
        return True

    def rewind(self):
        self._pos = 0
        self._frame = CdgFrame()
        self._cur_indexed = np.zeros((CROP_H, CROP_W), dtype=np.uint8)
        self._cur_palette = np.zeros((16, 3), dtype=np.uint8)
        self._cur_pkt_idx = 0
        self._next_pkt_idx = 0
        self._last_change_idx = -1

    # ----------------------------------------------------------- internal
    def _process_next_packet(self) -> bool:
        pkt = self._data[self._pos:self._pos + PACKET_SIZE]
        self._pos += PACKET_SIZE
        self._next_pkt_idx += 1
        return self._frame.apply_subcode(pkt)

    def _is_eof(self) -> bool:
        return self._pos + PACKET_SIZE > len(self._data)

    @staticmethod
    def _pkts_to_ms(n: int) -> int:
        return (n * 1000) // PACKETS_PER_SECOND


if __name__ == "__main__":
    import sys, time
    r = CdgReader(sys.argv[1])
    t0 = time.perf_counter()
    frames = 0
    while r.move_to_next_frame():
        frames += 1
    dt = time.perf_counter() - t0
    print(f"{frames} visible frames from {r.total_duration_ms()/1000:.1f}s of CDG "
          f"decoded in {dt*1000:.1f} ms "
          f"(vs {int(r.total_duration_ms()/1000*60)} frames at fixed 60fps)")
