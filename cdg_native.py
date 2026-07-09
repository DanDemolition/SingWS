"""
SingWS native CDG decoder — a pure-Python port of OpenKJ's libCDG
(CdgImageFrame + CdgFileReader + CdgAppSrc, GPLv3, based on Jim Bumgardner's
"CDG Revealed"). No third-party dependencies (no numpy) — frames are emitted
as paletted RGB8P exactly like OpenKJ, so GStreamer's videoconvert does the
palette->RGB expansion in C and Python never touches individual pixels on the
render path.

Why this exists (vs the gst-plugins-rs cdgparse/cdgdec elements):
  * Output is the CROPPED 288x192 visible area (border trimmed, smooth-scroll
    offsets honored) like OpenKJ, instead of the full 300x216 surface.
  * Damaged-file tolerance: corrupt packets are clamped/rejected instead of
    trusted (memory preset / border colors, tile row+column range, repeated
    memory presets). Extra hardening beyond OpenKJ: scroll offsets are clamped
    to the croppable window and a truncated trailing packet is ignored.
  * By default, decoded CDG state is sampled at 60fps presentation cadence and
    emitted only when that sampled image changed. Set SINGWS_CDG_PRESENT_FPS=0
    to emit every visible CDG packet change instead.

Usage from the app:
    src = CdgAppSource(Gst)          # pass the already-initialized Gst module
    src.load("/path/to/file.cdg")
    pipeline.add(src.element)        # appsrc, video/x-raw RGB8P 288x192
    src.element.link(tee)

The decoder half (CdgSurface / CdgFileReader) has no GStreamer dependency and
can be exercised standalone:  python3 cdg_native.py file.cdg [png-prefix]
"""

import os
import threading

CDG_PACKET_SIZE = 24
CDG_PACKETS_PER_SECOND = 300
# Change-driven fallback granularity. The default presentation mode below
# samples decoded CDG state at 60fps instead; set SINGWS_CDG_PRESENT_FPS=0 to
# use this per-visible-change path for A/B testing.
MIN_PACKETS_PER_FRAME = 1
DEFAULT_PRESENT_FPS = 60

FULL_W, FULL_H = 300, 216      # paintable surface per the CDG spec
CROP_W, CROP_H = 288, 192      # intended-visible center region
SIDEFILL_W, SIDEFILL_H = 340, CROP_H  # aligned near-16:9 frame with unstretched CDG center
SIDEFILL_PAD_X = (SIDEFILL_W - CROP_W) // 2
PALETTE_BYTES = 1024           # 256 x 32-bit entries (only 16 used)
FRAME_BYTES = CROP_W * CROP_H + PALETTE_BYTES  # RGB8P: indexed plane + palette
SIDEFILL_FRAME_BYTES = SIDEFILL_W * SIDEFILL_H + PALETTE_BYTES


def _env_present_fps() -> int:
    raw = os.environ.get("SINGWS_CDG_PRESENT_FPS", "").strip()
    if not raw:
        return DEFAULT_PRESENT_FPS
    try:
        return max(0, int(float(raw)))
    except Exception:
        return DEFAULT_PRESENT_FPS

# CD redbook command values
_CMD_MEMORY_PRESET = 1
_CMD_BORDER_PRESET = 2
_CMD_TILE_BLOCK = 6
_CMD_SCROLL_PRESET = 20
_CMD_SCROLL_COPY = 24
_CMD_DEFINE_TRANS = 28
_CMD_COLORS_LOW = 30
_CMD_COLORS_HIGH = 31
_CMD_TILE_BLOCK_XOR = 38

_TILE_MASKS = (0x20, 0x10, 0x08, 0x04, 0x02, 0x01)  # leftmost pixel first


class CdgSurface:
    """The 300x216 indexed-color CDG drawing surface (port of CdgImageFrame)."""

    def __init__(self):
        self.pixels = bytearray(FULL_W * FULL_H)  # 4-bit color indices
        # Palette entries are 32-bit ARGB words stored little-endian
        # (B,G,R,A byte order) — same layout OpenKJ feeds GStreamer (QRgb).
        self.palette = bytearray(PALETTE_BYTES)
        for i in range(16):
            self.palette[i * 4 + 3] = 255  # opaque black
        self.h_offset = 0
        self.v_offset = 0
        self._last_cmd_was_mempreset = False

    def apply_packet(self, pkt) -> bool:
        """Apply one 24-byte subcode packet. Returns True if the visible
        image may have changed. Corrupt packets are clamped or ignored."""
        if (pkt[0] & 0x3F) != 0x09:  # not a CDG packet (other subcode data)
            return False
        instr = pkt[1] & 0x3F
        data = pkt[4:20]
        updated = False

        if instr == _CMD_MEMORY_PRESET:
            color = data[0] & 0x0F
            repeat = data[1] & 0x0F
            # Discs repeat memory presets for error resilience; only the
            # first of a run needs to paint.
            if not (self._last_cmd_was_mempreset and repeat):
                cb = bytes((color,))
                self.pixels[:] = cb * (FULL_W * FULL_H)
                updated = True
        elif instr == _CMD_BORDER_PRESET:
            self._border_preset(data[0] & 0x0F)
            updated = True
        elif instr in (_CMD_TILE_BLOCK, _CMD_TILE_BLOCK_XOR):
            updated = self._tile_block(data, xor=(instr == _CMD_TILE_BLOCK_XOR))
        elif instr in (_CMD_SCROLL_PRESET, _CMD_SCROLL_COPY):
            self._scroll(data, copy=(instr == _CMD_SCROLL_COPY))
            updated = True
        elif instr in (_CMD_COLORS_LOW, _CMD_COLORS_HIGH):
            updated = self._load_colors(data, high=(instr == _CMD_COLORS_HIGH))
        elif instr == _CMD_DEFINE_TRANS:
            pass  # rarely-used spec leftover; OpenKJ ignores it too

        self._last_cmd_was_mempreset = (instr == _CMD_MEMORY_PRESET)
        return updated

    def _border_preset(self, color: int):
        px, cb = self.pixels, bytes((color,))
        px[: 12 * FULL_W] = cb * (12 * FULL_W)            # top 12 rows
        px[203 * FULL_W:] = cb * ((FULL_H - 203) * FULL_W)  # bottom 13 rows
        left_fill, right_fill = cb * 6, cb * 6
        for y in range(12, 203):
            row = y * FULL_W
            px[row:row + 6] = left_fill
            px[row + 294:row + 300] = right_fill

    def _tile_block(self, data, xor: bool) -> bool:
        color0 = data[0] & 0x0F
        color1 = data[1] & 0x0F
        row = data[2] & 0x1F
        col = data[3] & 0x3F
        if row >= 18 or col >= 50:  # corrupt packet — off the surface
            return False
        px = self.pixels
        base = (row * 12) * FULL_W + col * 6
        for y in range(12):
            bits = data[4 + y]
            off = base + y * FULL_W
            if xor:
                for x, mask in enumerate(_TILE_MASKS):
                    px[off + x] ^= color1 if bits & mask else color0
            else:
                for x, mask in enumerate(_TILE_MASKS):
                    px[off + x] = color1 if bits & mask else color0
        return True

    def _scroll(self, data, copy: bool):
        color = data[0] & 0x0F
        h, v = data[1] & 0x3F, data[2] & 0x3F
        h_cmd, h_off = (h & 0x30) >> 4, h & 0x07
        v_cmd, v_off = (v & 0x30) >> 4, v & 0x0F
        px, cb = self.pixels, bytes((color,))

        if h_cmd in (1, 2):
            for y in range(FULL_H):
                row = y * FULL_W
                line = px[row:row + FULL_W]
                if h_cmd == 2:  # scroll left 6px
                    filler = line[:6] if copy else cb * 6
                    px[row:row + FULL_W] = line[6:] + filler
                else:           # scroll right 6px
                    filler = line[294:] if copy else cb * 6
                    px[row:row + FULL_W] = filler + line[:294]
        if v_cmd == 2:  # scroll up 12px
            band = 12 * FULL_W
            saved = px[:band] if copy else cb * band
            px[: 204 * FULL_W] = px[band:]
            px[204 * FULL_W:] = saved
        elif v_cmd == 1:  # scroll down 12px
            band = 12 * FULL_W
            saved = px[204 * FULL_W:] if copy else cb * band
            px[band:] = px[: 204 * FULL_W]
            px[:band] = saved

        # Clamp so the cropped window always stays on the surface, even for
        # corrupt offsets (spec range is h 0-5 / v 0-11 anyway).
        self.h_offset = min(h_off, 6)
        self.v_offset = min(v_off, 12)

    def _load_colors(self, data, high: bool) -> bool:
        changed = False
        base = 8 if high else 0
        pal = self.palette
        for i in range(8):
            lo, hi = data[i * 2], data[i * 2 + 1]
            r = ((lo >> 2) & 0x0F) * 17
            g = ((((lo & 0x03) << 2) | ((hi & 0x30) >> 4)) & 0x0F) * 17
            b = (hi & 0x0F) * 17
            off = (base + i) * 4
            entry = bytes((b, g, r, 255))
            if pal[off:off + 4] != entry:
                pal[off:off + 4] = entry
                changed = True
        return changed

    def render_cropped(self) -> bytes:
        """Current visible image as an RGB8P frame: 288x192 color indices
        followed by the 1024-byte palette plane."""
        top = 12 + self.v_offset
        left = 6 + self.h_offset
        px = self.pixels
        out = bytearray(FRAME_BYTES)
        pos = 0
        for y in range(top, top + CROP_H):
            row = y * FULL_W + left
            out[pos:pos + CROP_W] = px[row:row + CROP_W]
            pos += CROP_W
        out[pos:] = self.palette
        return bytes(out)

    def render_sidefill(self) -> bytes:
        """Visible CDG centered in a widescreen RGB8P frame.

        The center keeps the exact 288x192 CDG pixels. The side bars are filled
        from the current border/background color so widescreen output fills
        naturally without stretching lyrics.
        """
        top = 12 + self.v_offset
        left = 6 + self.h_offset
        px = self.pixels
        out = bytearray(SIDEFILL_FRAME_BYTES)
        pos = 0
        for y in range(top, top + CROP_H):
            src_row = y * FULL_W
            side_color = px[src_row] & 0x0F
            out[pos:pos + SIDEFILL_W] = bytes((side_color,)) * SIDEFILL_W
            dst = pos + SIDEFILL_PAD_X
            out[dst:dst + CROP_W] = px[src_row + left:src_row + left + CROP_W]
            pos += SIDEFILL_W
        out[pos:] = self.palette
        return bytes(out)


class CdgFileReader:
    """Iterates a .cdg file as visibly-distinct frames (port of OpenKJ's
    CdgFileReader). Frames are grouped so at most MAX_FPS are emitted/sec."""

    def __init__(self, path: str, sidefill: bool = False, present_fps: int | None = None):
        self._sidefill = bool(sidefill)
        self._present_fps = _env_present_fps() if present_fps is None else max(0, int(present_fps))
        with open(path, "rb") as f:
            data = f.read()
        # Ignore a truncated trailing packet from a damaged rip.
        usable = (len(data) // CDG_PACKET_SIZE) * CDG_PACKET_SIZE
        self._data = data[:usable]
        self._total_packets = usable // CDG_PACKET_SIZE
        self.rewind()

    @property
    def width(self) -> int:
        return SIDEFILL_W if self._sidefill else CROP_W

    @property
    def height(self) -> int:
        return SIDEFILL_H if self._sidefill else CROP_H

    @property
    def frame_bytes(self) -> int:
        return SIDEFILL_FRAME_BYTES if self._sidefill else FRAME_BYTES

    def _render_frame(self) -> bytes:
        return self._surface.render_sidefill() if self._sidefill else self._surface.render_cropped()

    def rewind(self):
        self._surface = CdgSurface()
        self._next_idx = 0  # packets applied to the surface so far
        self._cur_idx = 0   # packet index of the snapshotted current frame
        self._cur_pts_ns = 0
        self._cur_dur_ns = 0
        self._present_tick_idx = 0
        self._cur_frame = self._render_frame()  # black
        self._last_change_idx = -1

    # -- internals ---------------------------------------------------------

    def _is_eof(self) -> bool:
        return self._next_idx >= self._total_packets

    def _process_next_packet(self) -> bool:
        off = self._next_idx * CDG_PACKET_SIZE
        pkt = self._data[off:off + CDG_PACKET_SIZE]
        self._next_idx += 1
        try:
            return self._surface.apply_packet(pkt)
        except Exception:
            return False  # never let one corrupt packet kill playback

    # -- iteration ---------------------------------------------------------

    def move_to_next_frame(self) -> bool:
        """Snapshot the next frame as 'current'. Returns False when the file
        is exhausted and the current frame is the last one."""
        if self._present_fps > 0:
            return self._move_to_next_present_frame()

        if self._cur_idx == 0:
            # Skip the silent lead-in: scan until the first visible change.
            while not self._is_eof() and not self._process_next_packet():
                pass

        self._cur_frame = self._render_frame()
        self._cur_idx = self._next_idx

        image_changed = False
        while True:
            if self._is_eof():
                return self._cur_idx != self._next_idx
            if image_changed and (self._next_idx - self._cur_idx) >= MIN_PACKETS_PER_FRAME:
                return True
            if self._process_next_packet():
                image_changed = True
                self._last_change_idx = self._next_idx

    def _move_to_next_present_frame(self) -> bool:
        """Sample the CDG state at display cadence.

        CDG packets can visibly change the surface at 300Hz. Most displays only
        present at about 60Hz, so this mode applies all packets up to each
        presentation tick and emits one frame only when that tick changed the
        visible image.
        """
        total_ns = self.total_duration_ns()
        while True:
            pts_ns = (self._present_tick_idx * 1_000_000_000) // self._present_fps
            if pts_ns > total_ns:
                while self._next_idx < self._total_packets:
                    if self._process_next_packet():
                        self._last_change_idx = self._next_idx
                return False

            target_packet = min(
                self._total_packets,
                (pts_ns * CDG_PACKETS_PER_SECOND) // 1_000_000_000,
            )
            image_changed = False
            while self._next_idx < target_packet:
                if self._process_next_packet():
                    image_changed = True
                    self._last_change_idx = self._next_idx

            self._present_tick_idx += 1
            next_pts_ns = (self._present_tick_idx * 1_000_000_000) // self._present_fps

            if image_changed:
                self._cur_frame = self._render_frame()
                self._cur_idx = target_packet
                self._cur_pts_ns = pts_ns
                self._cur_dur_ns = max(1, next_pts_ns - pts_ns)
                return True

            if target_packet >= self._total_packets:
                return False

    def current_frame(self) -> bytes:
        return self._cur_frame

    def current_frame_position_ms(self) -> int:
        if self._present_fps > 0:
            return self._cur_pts_ns // 1_000_000
        return (self._cur_idx * 1000) // CDG_PACKETS_PER_SECOND

    def current_frame_duration_ms(self) -> int:
        if self._present_fps > 0:
            return self._cur_dur_ns // 1_000_000
        return ((self._next_idx - self._cur_idx) * 1000) // CDG_PACKETS_PER_SECOND

    @staticmethod
    def _packet_position_ns(packet_idx: int) -> int:
        return (packet_idx * 1_000_000_000) // CDG_PACKETS_PER_SECOND

    def current_frame_position_ns(self) -> int:
        if self._present_fps > 0:
            return self._cur_pts_ns
        return self._packet_position_ns(self._cur_idx)

    def current_frame_duration_ns(self) -> int:
        if self._present_fps > 0:
            return self._cur_dur_ns
        return self._packet_position_ns(self._next_idx) - self._packet_position_ns(self._cur_idx)

    def total_duration_ms(self) -> int:
        return (self._total_packets * 1000) // CDG_PACKETS_PER_SECOND

    def total_duration_ns(self) -> int:
        return self._packet_position_ns(self._total_packets)

    def position_of_final_frame_ms(self) -> int:
        if self._is_eof() and self._last_change_idx >= 0:
            return (self._last_change_idx * 1000) // CDG_PACKETS_PER_SECOND
        return -1

    def seek(self, position_ms: int) -> bool:
        pkg_idx = (position_ms * CDG_PACKETS_PER_SECOND) // 1000
        if pkg_idx > self._total_packets:
            return False
        if pkg_idx < self._cur_idx:
            self.rewind()
        while self._next_idx < pkg_idx:
            self._process_next_packet()
        if self._present_fps > 0:
            position_ns = int(position_ms) * 1_000_000
            self._present_tick_idx = (position_ns * self._present_fps + 999_999_999) // 1_000_000_000
            self._cur_pts_ns = position_ns
            self._cur_dur_ns = 0
        return True


class CdgAppSource:
    """GStreamer appsrc wrapper feeding decoded CDG frames (port of OpenKJ's
    CdgAppSrc). Pass the app's already-initialized Gst module."""

    def __init__(self, gst, name: str = "cdg_native_src", diag=None, sidefill: bool = False):
        self._Gst = gst
        self.sidefill = bool(sidefill)
        self.present_fps = _env_present_fps()
        self.width, self.height = output_size(self.sidefill)
        self.frame_bytes = SIDEFILL_FRAME_BYTES if self.sidefill else FRAME_BYTES
        self._lock = threading.Lock()
        self._reader = None
        self._want_data = False
        self._diag = diag or (lambda msg: None)  # optional app logger
        self._frames_pushed = 0

        src = gst.ElementFactory.make("appsrc", name)
        if src is None:
            raise RuntimeError("Failed to create appsrc for native CDG decoder")
        caps = (
            f"video/x-raw,format=RGB8P,width={self.width},height={self.height},"
            f"framerate={self.present_fps or CDG_PACKETS_PER_SECOND}/1,pixel-aspect-ratio=1/1"
        )
        src.set_property("caps", gst.Caps.from_string(caps))
        src.set_property("format", gst.Format.TIME)
        src.set_property("stream-type", 1)  # GST_APP_STREAM_TYPE_SEEKABLE
        src.set_property("max-bytes", self.frame_bytes * 20)
        src.set_property("emit-signals", True)
        src.connect("need-data", self._on_need_data)
        src.connect("enough-data", self._on_enough_data)
        src.connect("seek-data", self._on_seek_data)
        self.element = src

    def load(self, cdg_path: str):
        with self._lock:
            self._reader = CdgFileReader(cdg_path, sidefill=self.sidefill, present_fps=self.present_fps)
            self._frames_pushed = 0
            self.element.set_property(
                "duration", self._reader.total_duration_ns()
            )
            self._diag(
                f"[CDG-NATIVE] loaded: {self._reader._total_packets} packets, "
                f"{self._reader.total_duration_ms() / 1000:.1f}s "
                f"size={self.width}x{self.height} sidefill={self.sidefill} "
                f"present_fps={self.present_fps or 'change-driven'}"
            )

    def reset(self):
        """Detach the reader; safe to call during/after teardown."""
        self._want_data = False
        with self._lock:
            self._reader = None

    def position_of_final_frame_ms(self) -> int:
        with self._lock:
            return self._reader.position_of_final_frame_ms() if self._reader else -1

    # -- appsrc callbacks (GStreamer streaming threads) ----------------------

    def _on_need_data(self, src, _length):
        gst = self._Gst
        self._want_data = True
        while self._want_data:
            with self._lock:
                reader = self._reader
                if reader is None:
                    return
                if not reader.move_to_next_frame():
                    break
                data = reader.current_frame()
                pts_ns = reader.current_frame_position_ns()
                dur_ns = reader.current_frame_duration_ns()
            buf = gst.Buffer.new_wrapped(data)
            buf.pts = pts_ns
            buf.duration = dur_ns
            # push-buffer may fire enough-data synchronously, clearing the flag.
            if src.emit("push-buffer", buf) != gst.FlowReturn.OK:
                return
            self._frames_pushed += 1
            if self._frames_pushed == 1:
                self._diag(f"[CDG-NATIVE] first frame pushed pts={pts_ns / 1_000_000:.3f}ms")
        if self._want_data:  # loop ended because the file is exhausted
            self._diag(f"[CDG-NATIVE] EOS after {self._frames_pushed} frames")
            src.emit("end-of-stream")

    def _on_enough_data(self, _src):
        self._want_data = False

    def _on_seek_data(self, _src, offset_ns) -> bool:
        with self._lock:
            if self._reader is None:
                return False
            ok = self._reader.seek(int(offset_ns) // self._Gst.MSECOND)
        self._diag(f"[CDG-NATIVE] seek to {int(offset_ns) // self._Gst.MSECOND}ms ok={ok}")
        return ok


def output_size(sidefill: bool = False) -> tuple[int, int]:
    return (SIDEFILL_W, SIDEFILL_H) if sidefill else (CROP_W, CROP_H)


def selftest(path: str, dump_png_prefix: str | None = None):
    """Decode a .cdg end to end without GStreamer; optionally dump PNGs
    (via macOS `sips`) at 25/50/75% for eyeballing. Prints a summary."""
    import time
    reader = CdgFileReader(path)
    total_ms = reader.total_duration_ms()
    marks = {int(total_ms * f) for f in (0.25, 0.5, 0.75)}
    frames = 0
    dumped = []
    t0 = time.perf_counter()
    while reader.move_to_next_frame():
        frames += 1
        if dump_png_prefix and marks:
            pos = reader.current_frame_position_ms()
            hit = {m for m in marks if pos >= m}
            if hit:
                marks -= hit
                dumped.append(_dump_frame(reader, dump_png_prefix, pos))
    elapsed = time.perf_counter() - t0
    print(
        f"{path}\n  duration={total_ms / 1000:.1f}s frames={frames} "
        f"(avg {frames / max(total_ms / 1000, 0.01):.1f} fps) "
        f"decoded in {elapsed:.2f}s "
        f"last_change={reader.position_of_final_frame_ms() / 1000:.1f}s"
    )
    for p in dumped:
        print(f"  wrote {p}")


def _dump_frame(reader, prefix, pos_ms):
    import subprocess
    frame = reader.current_frame()
    indices = frame[: CROP_W * CROP_H]
    pal = frame[CROP_W * CROP_H:]
    rgb = bytearray(CROP_W * CROP_H * 3)
    for i, idx in enumerate(indices):
        off = idx * 4
        rgb[i * 3] = pal[off + 2]      # R (palette is B,G,R,A)
        rgb[i * 3 + 1] = pal[off + 1]  # G
        rgb[i * 3 + 2] = pal[off]      # B
    ppm = f"{prefix}-{pos_ms // 1000:03d}s.ppm"
    with open(ppm, "wb") as f:
        f.write(f"P6 {CROP_W} {CROP_H} 255\n".encode())
        f.write(bytes(rgb))
    png = ppm.replace(".ppm", ".png")
    subprocess.run(["sips", "-s", "format", "png", ppm, "--out", png],
                   check=True, capture_output=True)
    return png


if __name__ == "__main__":
    import sys
    selftest(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
