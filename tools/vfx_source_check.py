#!/usr/bin/env python3
"""The shell's rust/crates/singws-shell/qml/ShowScreenVfx.qml must stay byte-for-byte the app's QML_SHOW_SCREEN_VFX_SOURCE (0.2.18.1.py): the
effects are QML in both, so the Rust window reuses the source and this check keeps the two from drifting. Exit 1 on any difference.
To refresh after the app's effects change:  python3 tools/vfx_source_check.py --write"""
import re, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
src = (root / "0.2.18.1.py").read_text(encoding="utf-8")
m = re.search(r'QML_SHOW_SCREEN_VFX_SOURCE = r"""\n(.*?)"""\n', src, re.S)
target = root / "rust/crates/singws-shell/qml/ShowScreenVfx.qml"
if "--write" in sys.argv:
    target.write_text(m.group(1), encoding="utf-8"); print("written"); sys.exit(0)
same = target.read_text(encoding="utf-8") == m.group(1)
print("identical" if same else "DIFFERENT: run tools/vfx_source_check.py --write")
sys.exit(0 if same else 1)
