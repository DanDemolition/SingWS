"""Generates the styled background image for the SingWS installer DMG."""

from pathlib import Path
import math
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent
ICON_SRC = ROOT / "singws-icon-transparent.png"

# DMG window logical size is 600x400.  We render twice: a 1x PNG and a 2x PNG,
# then combine them into a single multi-representation TIFF with `tiffutil` so
# Finder treats it as a retina background and keeps the window at 600x400.
LOGICAL_W, LOGICAL_H = 600, 400

OUT_DIR = ROOT / "design"
OUT_DIR.mkdir(exist_ok=True)
OUT_1X = OUT_DIR / "dmg_background.png"
OUT_2X = OUT_DIR / "dmg_background@2x.png"
OUT_TIFF = OUT_DIR / "dmg_background.tiff"


def radial_gradient(size, inner_color, outer_color):
    w, h = size
    cx, cy = w / 2, h / 2 + 60
    max_r = math.hypot(max(cx, w - cx), max(cy, h - cy))
    img = Image.new("RGB", size, outer_color)
    px = img.load()
    ir, ig, ib = inner_color
    or_, og, ob = outer_color
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy) / max_r
            d = min(1.0, d)
            t = d * d  # smoother falloff
            r = int(ir + (or_ - ir) * t)
            g = int(ig + (og - ig) * t)
            b = int(ib + (ob - ib) * t)
            px[x, y] = (r, g, b)
    return img


def add_glow(canvas, xy, radius, color, alpha=160):
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    cx, cy = xy
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color + (alpha,))
    glow = glow.filter(ImageFilter.GaussianBlur(radius // 2))
    canvas.alpha_composite(glow)


def draw_arrow(canvas, start, end, color=(0, 220, 255), thickness=8):
    """Glowing cyan arrow from start to end (canvas should be RGBA)."""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x1, y1 = start
    x2, y2 = end
    d.line((x1, y1, x2, y2), fill=color + (255,), width=thickness)

    # arrowhead
    angle = math.atan2(y2 - y1, x2 - x1)
    head_len = 38
    head_w = 22
    left = (x2 - head_len * math.cos(angle) + head_w * math.sin(angle),
            y2 - head_len * math.sin(angle) - head_w * math.cos(angle))
    right = (x2 - head_len * math.cos(angle) - head_w * math.sin(angle),
             y2 - head_len * math.sin(angle) + head_w * math.cos(angle))
    d.polygon([(x2, y2), left, right], fill=color + (255,))

    # glow layer behind the arrow
    glow = overlay.copy().filter(ImageFilter.GaussianBlur(14))
    canvas.alpha_composite(glow)
    canvas.alpha_composite(overlay)


def find_font(candidates, size):
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def vertical_gradient(size, stops):
    """Produce an RGB image filled with a vertical gradient.

    `stops` is a list of (offset_0_to_1, (r,g,b)) tuples sorted by offset.
    """
    w, h = size
    img = Image.new("RGB", size, stops[0][1])
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        # find the two stops bracketing t
        a = stops[0]
        b = stops[-1]
        for i in range(len(stops) - 1):
            if stops[i][0] <= t <= stops[i + 1][0]:
                a, b = stops[i], stops[i + 1]
                break
        span = max(1e-6, b[0] - a[0])
        local = (t - a[0]) / span
        r = int(a[1][0] + (b[1][0] - a[1][0]) * local)
        g = int(a[1][1] + (b[1][1] - a[1][1]) * local)
        bl = int(a[1][2] + (b[1][2] - a[1][2]) * local)
        for x in range(w):
            px[x, y] = (r, g, bl)
    return img


def render_gradient_text(text, font, gradient_stops, glow_color=None, glow_alpha=140, glow_radius=8):
    """Render `text` as RGBA with a vertical gradient fill and optional glow.

    Returns the rendered image cropped tightly to its visible bounds.
    """
    # Measure with anchor at top-left, including descenders
    tmp = Image.new("L", (10, 10))
    td = ImageDraw.Draw(tmp)
    bbox = td.textbbox((0, 0), text, font=font)
    pad = max(glow_radius * 2 if glow_color else 0, 4)
    tw = bbox[2] - bbox[0] + pad * 2
    th = bbox[3] - bbox[1] + pad * 2

    # Build text mask (alpha) at the padded canvas size
    mask = Image.new("L", (tw, th), 0)
    md = ImageDraw.Draw(mask)
    md.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=255)

    # Gradient fill the size of the canvas; apply mask
    grad = vertical_gradient((tw, th), gradient_stops).convert("RGBA")
    grad.putalpha(mask)

    if glow_color is not None:
        glow_layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        gd.text((pad - bbox[0], pad - bbox[1]), text, font=font,
                fill=glow_color + (glow_alpha,))
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))
        out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        out.alpha_composite(glow_layer)
        out.alpha_composite(grad)
        return out
    return grad


def render(scale: int) -> Image.Image:
    """Render the background at the given pixel scale (1 or 2)."""
    W, H = LOGICAL_W * scale, LOGICAL_H * scale
    s = scale  # shorthand

    # Layout in LOGICAL coordinates (will be scaled below).  Icon centers
    # sit at y=265 in the lower half of the window; title/subtitle and arrow
    # cluster around them.
    ICON_Y = 265                   # icon center (logical)
    APP_X = 150                    # left icon center x
    APPS_X = 450                   # right icon center x
    ARROW_PAD = 80                 # gap between icon edge and arrow tip

    bg = radial_gradient((W, H), inner_color=(26, 28, 56), outer_color=(8, 8, 18))
    canvas = bg.convert("RGBA")

    # Soft cyan + purple ambient glows
    add_glow(canvas, (W // 2 - 120 * s, H // 2 + 40 * s), 200 * s,
             (60, 90, 220), alpha=80)
    add_glow(canvas, (W // 2 + 140 * s, H // 2 + 40 * s), 200 * s,
             (140, 60, 200), alpha=70)

    # Title mirrors the v2 app icon: huge bold italic "Sing" with a small
    # "WS" tucked above-right.  Use SF Pro Italic (variable, can hit Heavy
    # weight 900) so the result has the chunky italic feel of the icon.
    sing_font = find_font(
        ["/System/Library/Fonts/SFNSItalic.ttf",
         "/System/Library/Fonts/SFCompactItalic.ttf",
         "/System/Library/Fonts/HelveticaNeue.ttc"],
        size=92 * s,
    )
    ws_font = find_font(
        ["/System/Library/Fonts/SFNSItalic.ttf",
         "/System/Library/Fonts/SFCompactItalic.ttf",
         "/System/Library/Fonts/HelveticaNeue.ttc"],
        size=28 * s,
    )
    # Try to push both fonts to the Heavy weight axis if available.
    for font in (sing_font, ws_font):
        try:
            font.set_variation_by_axes([900])
        except Exception:
            pass

    # Polished chrome/silver gradient — brighter highlights, sharper midtone
    # for a more reflective metallic look (closer to the icon).
    chrome_stops = [
        (0.00, (255, 255, 255)),
        (0.20, (250, 252, 255)),
        (0.45, (210, 220, 235)),
        (0.60, (95, 105, 130)),
        (0.78, (215, 225, 240)),
        (1.00, (255, 255, 255)),
    ]
    # Blue → purple for "WS"
    neon_stops = [
        (0.00, (90, 160, 255)),
        (0.50, (140, 100, 240)),
        (1.00, (210, 70, 255)),
    ]

    sing_glow_radius = 10 * s
    ws_glow_radius = 6 * s
    sing_img = render_gradient_text(
        "Sing", sing_font, chrome_stops,
        glow_color=(150, 180, 255), glow_alpha=150, glow_radius=sing_glow_radius,
    )
    ws_img = render_gradient_text(
        "WS", ws_font, neon_stops,
        glow_color=(190, 90, 255), glow_alpha=170, glow_radius=ws_glow_radius,
    )

    # Layout: huge "Sing" centered; small "WS" tucked above-right of "Sing",
    # mimicking the v2 icon arrangement.
    sing_pad = max(sing_glow_radius * 2, 4)
    ws_pad = max(ws_glow_radius * 2, 4)
    sing_x = (W - sing_img.width) // 2
    sing_y = 22 * s
    canvas.alpha_composite(sing_img, (sing_x, sing_y))

    # Place WS so its visible bottom sits just above the visible top of Sing,
    # right-aligned roughly with the "g" of Sing.  Account for the empty
    # padding in each gradient image when positioning.
    ws_visible_w = ws_img.width - ws_pad * 2
    # Anchor the WS so its right edge lines up with the right side of "Sing"
    # (minus a few px so it doesn't fall past the descender of g).
    ws_right_anchor_x = sing_x + sing_img.width - sing_pad - 18 * s
    ws_x = ws_right_anchor_x - ws_img.width + ws_pad
    ws_y = sing_y + sing_pad - ws_visible_w // 4 - 4 * s  # slight overlap upward
    canvas.alpha_composite(ws_img, (ws_x, ws_y))

    # Subtitle
    sub_font = find_font(
        ["/System/Library/Fonts/HelveticaNeue.ttc",
         "/System/Library/Fonts/Helvetica.ttc"],
        size=16 * s,
    )
    draw = ImageDraw.Draw(canvas)
    sub_text = "Drag SingWS to your Applications folder"
    sw = draw.textlength(sub_text, font=sub_font)
    # Position just below the visible bottom of the "Sing" wordmark.
    sub_y = sing_y + sing_img.height - sing_pad + 6 * s
    draw.text(((W - sw) / 2, sub_y), sub_text,
              font=sub_font, fill=(170, 180, 210, 255))

    # Glowing arrow between the two icon slots.  Arrow tip stops short of
    # the right icon so it doesn't get covered by the folder.
    arrow_y = ICON_Y * s
    arrow_start = ((APP_X + ARROW_PAD) * s, arrow_y)
    arrow_end = ((APPS_X - ARROW_PAD) * s, arrow_y)
    draw_arrow(canvas, start=arrow_start, end=arrow_end,
               color=(0, 220, 255), thickness=5 * s)

    # Subtle waveform decoration along the bottom (kept well above the
    # bottom edge so it survives Finder's title-bar offset).
    wave = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    wd = ImageDraw.Draw(wave)
    base_y = H - 40 * s
    for i, x in enumerate(range(30 * s, W - 30 * s, 9 * s)):
        amp = (3 + (i * 7 % 11)) * s
        wd.rectangle((x, base_y - amp, x + 3 * s, base_y + amp),
                     fill=(0, 220, 255, 90))
    wave = wave.filter(ImageFilter.GaussianBlur(1 * s))
    canvas.alpha_composite(wave)

    # Light "pill" backgrounds behind the icon labels.  macOS dynamically
    # picks label text colour by sampling the background under the label —
    # a clearly bright/uniform area makes Finder choose dark text, which is
    # what we want for readability.  Keep the pill fully BELOW the icon
    # (icon bottom = y=329 with size 128 centered at y=265) and make it
    # solidly white with crisp edges so Finder samples it as a light area.
    label_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(label_layer)
    pill_w = 160 * s        # wide enough that "Applications" + padding fits
    pill_h = 26 * s
    pill_y_center = 346 * s  # pill top at 333 → 4px gap below icon bottom
    for icon_cx in (150 * s, 450 * s):
        x0 = icon_cx - pill_w // 2
        x1 = icon_cx + pill_w // 2
        y0 = pill_y_center - pill_h // 2
        y1 = pill_y_center + pill_h // 2
        ld.rounded_rectangle((x0, y0, x1, y1), radius=pill_h // 2,
                             fill=(255, 255, 255, 240))
    # Very small soft shadow so the card has presence without bleeding.
    shadow = label_layer.filter(ImageFilter.GaussianBlur(3 * s))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(label_layer)

    return canvas.convert("RGB")


def main():
    img1 = render(scale=1)
    img1.save(OUT_1X, "PNG", optimize=True)
    print(f"wrote {OUT_1X} ({OUT_1X.stat().st_size // 1024} KB)")

    img2 = render(scale=2)
    img2.save(OUT_2X, "PNG", optimize=True)
    print(f"wrote {OUT_2X} ({OUT_2X.stat().st_size // 1024} KB)")

    # Combine into a single multi-representation TIFF that Finder treats as
    # retina.  Without this, Finder sizes the window to the largest image's
    # pixel dimensions.
    result = subprocess.run(
        ["tiffutil", "-cathidpicheck", str(OUT_1X), str(OUT_2X),
         "-out", str(OUT_TIFF)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tiffutil failed: {result.stderr}")
    print(f"wrote {OUT_TIFF} ({OUT_TIFF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
