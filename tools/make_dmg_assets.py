#!/usr/bin/env python3
"""Generate the DMG background (with a styled helper strip) + the helper icon.

Brand-matched to the SingWS app icon: glossy squircle, a cyan->purple->magenta
neon line, and a magenta accent. Built at retina 2x from design/dmg_background@2x.png
and saved at 144 dpi so it stays crisp.

Outputs:
  design/dmg_background_helper.tiff   extended background (art on top + helper strip)
  design/open_me_first_icon.png       the helper's custom icon

Also sets that icon on installer/Open Me First.command and hides its extension.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ART_2X = ROOT / "design" / "dmg_background@2x.png"   # 1200x800
OUT_BG = ROOT / "design" / "dmg_background_helper.tiff"
HELPER = ROOT / "installer" / "Open Me First.command"
HELPER_ICON = ROOT / "design" / "open_me_first_icon.png"

S = 2                 # render scale (retina)
STRIP_PT = 244        # extra height (points) added below the art for the helper

# Brand palette (from the app icon).
CYAN = (34, 211, 238)
PURPLE = (140, 70, 245)
MAGENTA = (245, 39, 155)
WHITE = (255, 255, 255)
SOFT = (203, 210, 230)


def _font(size_pt: int, bold: bool = False):
    names = (
        ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/System/Library/Fonts/Helvetica.ttc"]
        if bold else
        ["/System/Library/Fonts/Helvetica.ttc",
         "/System/Library/Fonts/Supplemental/Arial.ttf"]
    )
    for p in names:
        try:
            return ImageFont.truetype(p, size_pt * S)
        except Exception:
            continue
    return ImageFont.load_default()


def _vgrad(w, h, top, bottom):
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    top = (*top, 255) if len(top) == 3 else top
    bottom = (*bottom, 255) if len(bottom) == 3 else bottom
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4)))
    return img


def _hgrad3(w, h, c0, c1, c2):
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(1, w - 1)
        if t < 0.5:
            a, b, tt = c0, c1, t / 0.5
        else:
            a, b, tt = c1, c2, (t - 0.5) / 0.5
        d.line([(x, 0), (x, h)], fill=(int(a[0] + (b[0] - a[0]) * tt),
                                       int(a[1] + (b[1] - a[1]) * tt),
                                       int(a[2] + (b[2] - a[2]) * tt), 255))
    return img


def _rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def _neon_line(width, *, glow=True):
    """Horizontal cyan->purple->magenta line that fades out at both ends."""
    h = 5 * S
    line = _hgrad3(width, h, CYAN, PURPLE, MAGENTA)
    # Fade the ends so it doesn't hit the window edges harshly.
    fade = Image.new("L", (width, h), 0)
    fd = ImageDraw.Draw(fade)
    for x in range(width):
        t = x / max(1, width - 1)
        edge = min(1.0, t / 0.18, (1 - t) / 0.18)
        fd.line([(x, 0), (x, h)], fill=int(255 * edge))
    line.putalpha(ImageChops.multiply(line.split()[3], fade))
    if glow:
        canvas = Image.new("RGBA", (width, 40 * S), (0, 0, 0, 0))
        blurred = line.filter(ImageFilter.GaussianBlur(7 * S))
        canvas.alpha_composite(blurred, (0, 40 * S // 2 - h))
        canvas.alpha_composite(line, (0, 40 * S // 2 - h // 2))
        return canvas
    return line


def build_helper_icon(px=512):
    """A black glossy squircle that reads as a sibling of the app icon:
    same dark glass form + cyan->purple->magenta neon line, with a white
    'run me' play glyph instead of the SING wordmark."""
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    inset, radius = 52, 118
    body_mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(body_mask).rounded_rectangle([inset, inset, px - inset, px - inset],
                                                radius=radius, fill=255)

    # Soft magenta glow so the dark badge lifts off the dark strip.
    glow = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([62, 70, px - 62, px - 54], radius=radius,
                                           fill=(*MAGENTA, 150))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(38)))

    # Black glass body (top slightly lifted -> bottom near-black).
    body = _vgrad(px, px, (44, 44, 58), (9, 9, 16))
    img.paste(body, (0, 0), body_mask)

    # Soft top gloss (heavily blurred so there's no hard band).
    gloss = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse([inset - 40, inset - 230, px - inset + 40, inset + 120],
                                  fill=(255, 255, 255, 46))
    gloss.putalpha(ImageChops.multiply(gloss.split()[3], body_mask))
    img = Image.alpha_composite(img, gloss.filter(ImageFilter.GaussianBlur(28)))

    # Thin bright rim along the top edge.
    rim = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle([inset, inset, px - inset, px - inset],
                                          radius=radius, outline=(255, 255, 255, 38), width=2)
    img = Image.alpha_composite(img, rim)

    # Neon signature line ~62% down (like the app icon).
    line = _neon_line(px - 2 * inset - 30)
    img.alpha_composite(line, ((px - line.width) // 2, int(px * 0.62)))

    # White play ("run me") triangle, centered in the upper glass.
    tri = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    cx, cy = px / 2 + 8, px * 0.40
    tw, th = 116, 142
    ImageDraw.Draw(tri).polygon(
        [(cx - tw / 2, cy - th / 2), (cx - tw / 2, cy + th / 2), (cx + tw / 2, cy)],
        fill=(255, 255, 255, 255))
    img = Image.alpha_composite(img, tri.filter(ImageFilter.GaussianBlur(7)))  # soft shadow
    img = Image.alpha_composite(img, tri)

    img.save(HELPER_ICON)
    print(f"wrote {HELPER_ICON}")


def _rich_center(d, cx, y, segments):
    widths = [d.textbbox((0, 0), t, font=f)[2] for t, f, _ in segments]
    x = cx - sum(widths) / 2
    for (t, f, fill), w in zip(segments, widths):
        d.text((x, y), t, font=f, fill=fill)
        x += w


def build_background():
    art = Image.open(ART_2X).convert("RGBA")
    aw, ah = art.size                      # 1200 x 800
    strip = STRIP_PT * S
    new = Image.new("RGBA", (aw, ah + strip))

    # Seamless dark strip: continue from the art's bottom color into a deeper tone.
    base = art.getpixel((6, ah - 40))[:3]
    deeper = tuple(int(c * 0.55) for c in base)
    new.paste(_vgrad(aw, strip, base, deeper), (0, ah))
    new.alpha_composite(art, (0, 0))

    d = ImageDraw.Draw(new)
    cx = aw // 2

    # Neon divider between the art and the helper strip.
    line = _neon_line(aw - 120 * S)
    new.alpha_composite(line, ((aw - line.width) // 2, ah - line.height // 2))

    def py(pt):  # point (measured from the art's bottom = 400pt) -> pixel y
        return ah + (pt - 400) * S

    _rich_center(d, cx, py(420), [("First time opening SingWS?", _font(22, bold=True), WHITE)])
    _rich_center(d, cx, py(452), [
        ("Double-click ", _font(14), SOFT),
        ("“Open Me First”", _font(14, bold=True), (255, 120, 200)),
        (" below — you only do this once.", _font(14), SOFT),
    ])

    # Down-chevron in brand magenta, pointing at the helper icon.
    ay = py(486)
    d.line([(cx - 14 * S, ay), (cx, ay + 14 * S)], fill=(*MAGENTA, 255), width=4 * S)
    d.line([(cx + 14 * S, ay), (cx, ay + 14 * S)], fill=(*MAGENTA, 255), width=4 * S)

    out = new.convert("RGB")
    out.save(OUT_BG, dpi=(72 * S, 72 * S))   # 144 dpi -> crisp at the 600pt logical width
    print(f"wrote {OUT_BG} ({out.size[0]}x{out.size[1]} px @ {72*S}dpi -> {out.size[0]//S}x{out.size[1]//S} pt)")


def style_helper_file():
    try:
        from AppKit import NSImage, NSWorkspace
        from Foundation import NSURL
    except Exception as exc:  # pragma: no cover
        print(f"(skipping icon/extension styling — AppKit unavailable: {exc})")
        return
    icon = NSImage.alloc().initWithContentsOfFile_(str(HELPER_ICON))
    ok = NSWorkspace.sharedWorkspace().setIcon_forFile_options_(icon, str(HELPER), 0)
    url = NSURL.fileURLWithPath_(str(HELPER))
    ok2, _ = url.setResourceValue_forKey_error_(True, "NSURLHasHiddenExtensionKey", None)
    print(f"helper custom icon: {bool(ok)}; extension hidden: {bool(ok2)}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate/refresh DMG helper assets.")
    ap.add_argument("--style-only", action="store_true",
                    help="only (re)apply the helper's custom icon + hidden extension "
                         "(filesystem metadata git can't track); skip image regen")
    args = ap.parse_args()
    if not args.style_only:
        build_helper_icon()
        build_background()
    style_helper_file()


if __name__ == "__main__":
    main()
