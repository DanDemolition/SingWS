#!/usr/bin/env python3
"""Generate the DMG background (with a helper strip) + style the helper file.

- Extends design/dmg_background.tiff downward with a matching dark strip that
  holds a clear instruction + arrow, so the existing 2-icon art and logo stay
  untouched. Writes design/dmg_background_helper.tiff.
- Gives installer/Open Me First.command a distinct custom icon (so Finder shows
  a clean badge, not the script text) and hides its ".command" extension.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "design" / "dmg_background.tiff"
OUT_BG = ROOT / "design" / "dmg_background_helper.tiff"
HELPER = ROOT / "installer" / "Open Me First.command"
HELPER_ICON = ROOT / "design" / "open_me_first_icon.png"

STRIP_H = 180  # extra height added below the original art for the helper


def _font(size: int):
    for p in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _center_text(draw, cx, y, text, font, fill):
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (r - l) / 2, y), text, font=font, fill=fill)
    return b - t


def build_background():
    art = Image.open(ART).convert("RGBA")
    w, h = art.size  # 600 x 400
    new = Image.new("RGBA", (w, h + STRIP_H))
    # Fill the strip with a dark navy sampled from the art's dark area.
    strip_color = art.getpixel((4, h // 2))
    new.paste(Image.new("RGBA", (w, h + STRIP_H), strip_color), (0, 0))
    new.paste(art, (0, 0))

    d = ImageDraw.Draw(new)
    cyan = (37, 201, 245, 255)
    soft = (200, 208, 230, 255)

    # Subtle divider between the original art and the helper strip.
    d.line([(40, h + 1), (w - 40, h + 1)], fill=(255, 255, 255, 28), width=1)

    cx = w // 2
    _center_text(d, cx, h + 22, "First time opening SingWS?", _font(20), (255, 255, 255, 235))
    _center_text(d, cx, h + 50, "Double-click “Open Me First” below — you only do this once.",
                 _font(13), soft)

    # Down-chevron pointing at the helper icon.
    ay = h + 78
    d.line([(cx - 12, ay), (cx, ay + 12)], fill=cyan, width=4)
    d.line([(cx + 12, ay), (cx, ay + 12)], fill=cyan, width=4)

    new.convert("RGB").save(OUT_BG)
    print(f"wrote {OUT_BG} ({new.size[0]}x{new.size[1]})")
    return new.size


def build_helper_icon():
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inset = 56
    # Cyan rounded-square badge matching the installer arrow.
    d.rounded_rectangle([inset, inset, size - inset, size - inset], radius=96,
                        fill=(37, 201, 245, 255))
    # White right-pointing "play / run" triangle, centered.
    cx, cy = size / 2, size / 2
    tw, th = 150, 180
    d.polygon([(cx - tw / 2, cy - th / 2), (cx - tw / 2, cy + th / 2), (cx + tw / 2, cy)],
              fill=(255, 255, 255, 255))
    img.save(HELPER_ICON)
    print(f"wrote {HELPER_ICON}")
    return HELPER_ICON


def style_helper_file():
    """Custom icon + hidden extension on the .command (best-effort; needs pyobjc)."""
    try:
        from AppKit import NSImage, NSWorkspace
        from Foundation import NSFileManager, NSURL
    except Exception as exc:  # pragma: no cover
        print(f"(skipping icon/extension styling — AppKit unavailable: {exc})")
        return
    icon = NSImage.alloc().initWithContentsOfFile_(str(HELPER_ICON))
    ok = NSWorkspace.sharedWorkspace().setIcon_forFile_options_(icon, str(HELPER), 0)
    print(f"set custom icon on helper: {bool(ok)}")
    # Hide the .command extension so Finder shows just "Open Me First".
    url = NSURL.fileURLWithPath_(str(HELPER))
    ok2, err = url.setResourceValue_forKey_error_(True, "NSURLHasHiddenExtensionKey", None)
    print(f"hid .command extension: {bool(ok2)}")


def main():
    build_background()
    build_helper_icon()
    style_helper_file()


if __name__ == "__main__":
    main()
