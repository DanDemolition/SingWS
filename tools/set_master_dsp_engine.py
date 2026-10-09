#!/usr/bin/env python3
"""Switch the BGM master processor between "python" (reference) and "rust" for A/B listening.

    python3 tools/set_master_dsp_engine.py rust      # or: python      (no argument = show the current value)

Only the one key `master_dsp_engine` in settings.json is changed (a dated backup is written next to it first), and
nothing else is printed, so no other setting - including keys and passwords - is ever shown. SingWS must be closed:
it rewrites settings.json on exit and would undo the change. Takes effect at the next launch.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

KEY = "master_dsp_engine"
VALID = ("python", "rust")


def settings_path() -> Path:
    return Path(os.environ.get("SINGWS_HOME") or Path.home() / "SingWS") / "settings.json"


def singws_running() -> bool:
    out = subprocess.run(["pgrep", "-f", "SingWS.app/Contents/MacOS"], capture_output=True, text=True)
    return bool(out.stdout.strip())


def main(argv: list[str]) -> int:
    path = settings_path()
    if not path.exists():
        print(f"no settings file at {path}")
        return 2
    data = json.loads(path.read_text())
    current = str(data.get(KEY, "rust"))
    if not argv:
        print(f"{KEY} = {current}")
        return 0
    wanted = argv[0].strip().lower()
    if wanted not in VALID:
        print(f"choose one of: {', '.join(VALID)}")
        return 2
    if wanted == current:
        print(f"{KEY} is already {current}; nothing changed")
        return 0
    if singws_running() and not os.environ.get("SINGWS_HOME"):
        print("SingWS is running. Quit it first (it rewrites settings.json on exit), then run this again.")
        return 3
    backup = path.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    data[KEY] = wanted
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)
    print(f"{KEY}: {current} -> {wanted}  (backup: {backup.name}). Launch SingWS to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
