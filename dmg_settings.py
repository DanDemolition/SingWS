"""dmgbuild settings for the styled SingWS installer DMG.

Build with:
    .venv/bin/dmgbuild -s dmg_settings.py "SingWS-0.2.18.0" \
        SingWS-0.2.18.0-arm64-installer.dmg
"""

from pathlib import Path

# dmgbuild loads this file via exec(), so __file__ is not defined.  Use the
# known project root.
ROOT = Path("/Users/daniel/Documents/SingWS")
APP_PATH = ROOT / "dist" / "SingWS.app"
BACKGROUND = ROOT / "design" / "dmg_background.tiff"
ICON_PATH = ROOT / "SingWS.icns"

# Volume / format
format = "UDZO"
filesystem = "HFS+"
size = None  # let dmgbuild size it automatically

# Contents of the DMG
files = [str(APP_PATH)]
symlinks = {"Applications": "/Applications"}

# Volume icon (shown when the DMG mounts)
icon = str(ICON_PATH)

# Finder window
background = str(BACKGROUND)
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
sidebar_width = 180

# Window outer size: 600x430.  macOS title bar takes ~28px, so the visible
# content area becomes ~600x402 — just enough to show the full 600x400
# background without clipping the waveform along the bottom.
window_rect = ((200, 200), (600, 430))

# Icon presentation
default_view = "icon-view"
include_icon_view_settings = "auto"
include_list_view_settings = "auto"

arrange_by = None
grid_offset = (0, 0)
grid_spacing = 100
scroll_position = (0, 0)
label_pos = "bottom"
text_size = 14
icon_size = 128

# Icon positions (window-space coordinates, top-left origin).
# These must match what the background image was drawn for.
icon_locations = {
    "SingWS.app": (150, 265),
    "Applications": (450, 265),
}

# Volume properties
badge_icon = str(ICON_PATH)
