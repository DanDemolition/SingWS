"""
okj_ticker.py
-------------
Python port of OpenKJ's scrolling ticker (src/tickernew.cpp).

Why OpenKJ's ticker doesn't stutter, and most homemade ones do:
it never lays out text per frame. When the text changes, it renders the
whole string ONCE to an off-screen strip that is (text width + viewport
width) wide, with the head of the text appended again at the end so the
wrap-around is seamless. Scrolling is then just "blit a viewport-sized
crop at offset x, x+1, x+2 ..." — one image copy per frame, no font
shaping, no layout, no allocation.

Two layers here:

  TickerStrip   - framework-agnostic core: owns the pre-rendered strip
                  (a PIL image) and the scroll offset; returns crops.
  TickerWidget  - thin Qt widget (PySide6 or PyQt5, whichever imports)
                  that drives TickerStrip from a QTimer and paints the
                  crop. Use this on your TV-mode second screen for the
                  rules ticker / tip ticker / rotation ticker.

If your second screen isn't Qt, use TickerStrip directly: call
advance() on your frame timer and blit current_crop() however you like
(pygame, tkinter PhotoImage, GStreamer appsrc overlay, ...).

pip install pillow
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


class TickerStrip:
    """Pre-rendered scroll strip + offset. Port of TickerNew/TickerImageCreator."""

    def __init__(self, viewport_width: int, font: ImageFont.FreeTypeFont,
                 fg=(0, 255, 255), bg=(0, 0, 0, 0), pad_px: int = 40):
        self.viewport_width = viewport_width
        self.font = font
        self.fg = fg
        self.bg = bg
        self.pad_px = pad_px          # gap between the end and wrapped head
        self.offset = 0
        self._text = ""
        self.strip: Image.Image | None = None
        self.text_width = 0
        self.overflows = False        # OpenKJ: static text doesn't scroll
        self.height = 0
        self.set_text(" ")

    # ------------------------------------------------------------ render
    def set_text(self, text: str, force: bool = False):
        """(Re)render the strip. Only re-lays-out when text CHANGES —
        the per-frame path never touches fonts (OpenKJ's core trick)."""
        if text == self._text and not force:
            return
        self._text = text

        # measure
        probe = Image.new("RGBA", (1, 1))
        d = ImageDraw.Draw(probe)
        box = d.textbbox((0, 0), text, font=self.font)
        self.text_width = box[2] - box[0]
        ascent, descent = self.font.getmetrics()
        # OpenKJ pads height 20% over tight bounds to avoid clipped descenders
        self.height = int((ascent + descent) * 1.2)

        self.overflows = self.text_width > self.viewport_width

        if not self.overflows:
            # static: strip == one viewport, text drawn once, no scrolling
            strip_w = self.viewport_width
        else:
            # scrolling: text + gap + wrapped copy of the head, so the crop
            # window always sees continuous content (seamless loop)
            strip_w = self.text_width + self.pad_px + self.viewport_width

        img = Image.new("RGBA", (strip_w, self.height), self.bg)
        d = ImageDraw.Draw(img)
        y = (self.height - (ascent + descent)) // 2
        d.text((0, y), text, font=self.font, fill=self.fg)
        if self.overflows:
            # wrapped head copy for seamless wrap-around
            d.text((self.text_width + self.pad_px, y),
                   text, font=self.font, fill=self.fg)
        self.strip = img
        self.offset = 0

    # ------------------------------------------------------------ scroll
    def advance(self, px: int = 1):
        """Move the viewport. Call from your frame timer. No-op for
        non-overflowing text (OpenKJ resets offset to 0 in that case)."""
        if not self.overflows:
            self.offset = 0
            return
        self.offset += px
        if self.offset >= self.text_width + self.pad_px:
            self.offset = 0  # wrapped copy makes this seam invisible

    def current_crop(self) -> Image.Image:
        """Viewport-sized crop at the current offset — the ONLY per-frame
        image work. This is what you blit."""
        return self.strip.crop((self.offset, 0,
                                self.offset + self.viewport_width, self.height))

    def set_viewport_width(self, width: int):
        if width != self.viewport_width:
            self.viewport_width = width
            self.set_text(self._text, force=True)


# --------------------------------------------------------------------------
# Optional Qt widget wrapper (PySide6 preferred, PyQt5 fallback)
# --------------------------------------------------------------------------
try:
    try:
        from PySide6.QtWidgets import QWidget
        from PySide6.QtCore import QTimer, Qt
        from PySide6.QtGui import QPainter, QImage
    except ImportError:  # pragma: no cover
        from PyQt5.QtWidgets import QWidget
        from PyQt5.QtCore import QTimer, Qt
        from PyQt5.QtGui import QPainter, QImage

    class TickerWidget(QWidget):
        """Drop-in ticker widget for the TV-mode screen.

        speed: 1 (slow) .. 50 (fast) — same knob feel as OpenKJ's setting,
        implemented as timer-interval like their usleep(m_speed/2*250).
        """

        def __init__(self, font: ImageFont.FreeTypeFont, fg=(0, 255, 255),
                     bg=(0, 0, 0, 0), speed: int = 25, parent=None):
            super().__init__(parent)
            self._strip = TickerStrip(max(1, self.width()), font, fg, bg)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self.set_speed(speed)
            self.setAttribute(Qt.WA_OpaquePaintEvent, False)

        def set_text(self, text: str):
            self._strip.set_text(text)
            self.setFixedHeight(self._strip.height)
            self.update()

        def set_speed(self, speed: int):
            # OpenKJ sleeps m_speed/2 * 250 usec per 1px step; invert the
            # scale so higher = faster, clamp to sane frame intervals.
            speed = max(1, min(50, speed))
            interval_ms = max(8, int((51 - speed) * 0.6))
            self._timer.start(interval_ms)

        def _tick(self):
            self._strip.advance(1)
            self.update()

        def resizeEvent(self, ev):
            self._strip.set_viewport_width(max(1, self.width()))
            super().resizeEvent(ev)

        def paintEvent(self, _ev):
            crop = self._strip.current_crop()
            qimg = QImage(crop.tobytes("raw", "RGBA"), crop.width,
                          crop.height, QImage.Format_RGBA8888)
            p = QPainter(self)
            p.drawImage(0, (self.height() - crop.height) // 2, qimg)
            p.end()

except ImportError:
    TickerWidget = None  # Qt not installed; use TickerStrip directly


if __name__ == "__main__":
    # headless demo: render, scroll, verify seamless wrap
    font = ImageFont.load_default(size=24)
    t = TickerStrip(300, font, fg=(57, 255, 20))
    t.set_text("Welcome to WildStyle Karaoke!  ***  Up next: Daniel  ***  "
               "Tips: venmo @wildstyle  ***  ")
    frames = 0
    seen_offsets = set()
    for _ in range(t.text_width + t.pad_px + 10):
        crop = t.current_crop()
        assert crop.size == (300, t.height)
        seen_offsets.add(t.offset)
        t.advance()
        frames += 1
    print(f"scrolled {frames} frames, wrapped cleanly: {t.offset < 10}, "
          f"strip size: {t.strip.size}, overflows: {t.overflows}")
