#!/usr/bin/env python3
"""Count UI literals that fall outside the design scales in singws_theme.py.

    python3 tools/ui_token_audit.py [--list]

Reports, for the main app file: font sizes, border radii and hex colours that are not on the theme's scales/palette.
`test_ui_tokens_ratchet.py` imports `measure()` and fails if any of these numbers goes UP, so the app can only get more
uniform. Lower the baselines in that test whenever a migration step brings a number down.
"""
import collections
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "0.2.18.1.py")
THEME = os.path.join(ROOT, "singws_theme.py")


def _load_theme():
    spec = importlib.util.spec_from_file_location("singws_theme_audit", THEME)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def measure(path: str = MAIN) -> dict:
    theme = _load_theme()
    src = open(path, encoding="utf-8").read()
    palette = {c.upper() for c in re.findall(r"#[0-9a-fA-F]{6}\b", open(THEME, encoding="utf-8").read())}
    fonts = collections.Counter(int(n) for n in re.findall(r"font-size:\s*(\d+)px", src))
    radii = collections.Counter(int(n) for n in re.findall(r"border-radius:\s*(\d+)px", src))
    hexes = collections.Counter(c.upper() for c in re.findall(r"#[0-9a-fA-F]{6}\b", src))
    off_fonts = {k: v for k, v in fonts.items() if k not in theme.FONT_SCALE}
    off_radii = {k: v for k, v in radii.items() if k not in theme.RADIUS_SCALE}
    off_hex = {k: v for k, v in hexes.items() if k not in palette}
    return {
        "inline_stylesheets": src.count("setStyleSheet"),
        "font_sizes_distinct": len(fonts), "font_off_scale_uses": sum(off_fonts.values()), "font_off_scale": off_fonts,
        "radii_distinct": len(radii), "radius_off_scale_uses": sum(off_radii.values()), "radius_off_scale": off_radii,
        "hex_distinct": len(hexes), "hex_off_palette_distinct": len(off_hex), "hex_off_palette_uses": sum(off_hex.values()),
        "hex_off_palette": off_hex,
    }


def main() -> int:
    m = measure()
    print(f"inline setStyleSheet calls : {m['inline_stylesheets']}")
    print(f"font sizes                 : {m['font_sizes_distinct']} distinct, {m['font_off_scale_uses']} uses off the scale")
    print(f"border radii               : {m['radii_distinct']} distinct, {m['radius_off_scale_uses']} uses off the scale")
    print(f"hex colours                : {m['hex_distinct']} distinct, {m['hex_off_palette_distinct']} off the palette ({m['hex_off_palette_uses']} uses)")
    if "--list" in sys.argv:
        print("off-scale fonts :", dict(sorted(m["font_off_scale"].items())))
        print("off-scale radii :", dict(sorted(m["radius_off_scale"].items())))
        print("off-palette hex :", sorted(m["hex_off_palette"].items(), key=lambda kv: -kv[1])[:40])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
