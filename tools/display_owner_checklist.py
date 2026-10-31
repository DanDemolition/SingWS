#!/usr/bin/env python3
"""What the Rust karaoke display window still needs the OWNER'S HARDWARE for, with helpers that record the evidence.

Nothing in here changes the running app: it starts the Rust shell's demo window (`--display-idle`) on its own, reads nothing but `settings.json`'s
`karaoke_window_pos`, plays no sound and talks to no server.

    tools/display_owner_checklist.py                 print the checklist
    tools/display_owner_checklist.py --where         print the screens Qt sees now and where the audience window would open (no window is shown)
    tools/display_owner_checklist.py --watch [SEC]   open the idle demo window where the app's rules put it and log every screen change with a time
                                                     stamp (default 300 s) to ~/Desktop/singws-display-watch.log; unplug and replug the TV, sleep and
                                                     wake the Mac, add or remove an AirPlay display while it runs

The shell binary is $SINGWS_SHELL_BIN or ~/.cache/singws-rust-target-display/release/singws-shell (build: `cd rust && cargo build --release -p singws-shell`
with QMAKE pointing at Homebrew's qmake6).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SHELL = os.environ.get("SINGWS_SHELL_BIN") or str(Path.home() / ".cache/singws-rust-target-display/release/singws-shell")

CHECKLIST = """
STAGE E DISPLAY WINDOW: what only your hardware can answer
=========================================================
Everything below is NOT CHECKED. Each item says what to do and what to send back. Run `--where` first: it shows the screens Qt reports and where the
rules (a port of the app's _place_video_window_bottom_right / _rect_on_attached_screen, proven equal to the app's Python on 27,000 fuzzed layouts)
would open the audience window.

1. TV unplugged and plugged back in (the real case: 2026-08-09 04:10 and 23:15 in the app's history).
   - `--watch`, unplug the TV, wait 10 s, plug it back in, wait 10 s.
   - Expected: a "[SCREEN] screens changed" line per Qt event, then ONE "[SCREEN] settled ... surfaces refreshed" line about 600 ms after the last event
     (the app's _screen_surface_refresh_timer), then a topology line that lists the TV again.
   - Compare with the SingWS app's own log for the same minute: its "[SCREEN] screen added/removed" lines and "[VIDSURFACE-DIAG] stable refresh
     reason=screen_topology_change". The number of events and the time they settle should match.
   - Note: neither the app nor this window MOVES the audience window when its screen disappears; the saved rectangle is only pulled back onto an
     attached screen the next time the window is built or re-shown. Say if that is not what you see on the real TV.
2. Saved window on a missing TV: set karaoke_window_pos in a COPY of settings.json to the TV's coordinates, unplug the TV, run `--where` with
   SINGWS_HOME pointing at the copy. Expected: the rectangle comes back centred on the primary screen at the same size (or a smaller one if it was larger).
3. Second display (extended desktop, no mirroring): the window opens where the cursor is on a first run; with a saved position it opens there. Check
   with `--where` while the cursor is on each display (the cursor position is read when the command runs: put it there with a keyboard shortcut).
4. Fullscreen on the TV (F or double-click in the demo window): the TV, not the laptop, must go fullscreen and the other display stays usable. The app
   goes through macOS Spaces here; report whether the demo window behaves the same (its own Space, Mission Control entry).
5. Retina scale 1x and 1.5x: attach a 1080p TV as the only display (1x) or set the laptop to a scaled resolution, then run
       SINGWS_VIDEO_BRIDGE=rust SINGWS_VIDEO_LIB=<bridge dylib> QT_QPA_PLATFORM=cocoa <venv>/bin/python tools/cdg_window_compare.py SONG.cdg SONG.mp3 --fill off --preview
   (and --fill colour / blur, and --bg VIDEO). Expected "0 of N pixels differ by more than 3". The comparison is pixel for pixel; only 2x has been run.
6. Intel Mac: run the same comparison once, and the software-video CPU figure
       singws-shell --display-demo-video FILE.mp4   (watch Activity Monitor for 30 s at 720p and 1080p)
   Measured on Apple Silicon only: about 26 percent of one core at 960x540 and 42 percent at 1920x1080.
7. Sleep and wake with the idle window open: after wake the idle card's meter must still move and the ticker strip must still scroll.
8. Partial transparency on a REAL disc (DEFINE_TRANSPARENT): the Rust picture now copies libmpv (pixels with alpha below 255 are blended over its
   grey 16-pixel checkerboard, measured on the real window and equal to within 1 level on a made-up file). No real disc that uses it was available.
   If you know one, send the file name and run `tools/cdg_window_compare.py DISC.cdg DISC.mp3 --at 30`: expect 0 pixels differing by more than 8.
"""


def run_shell(args, timeout=60, env_extra=None):
    env = dict(os.environ, QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "cocoa"))
    env.update(env_extra or {})
    return subprocess.run([SHELL, *args], env=env, capture_output=True, text=True, timeout=timeout)


def where():
    p = run_shell(["--display-where"])
    for line in (p.stdout + p.stderr).splitlines():
        if "[WHERE]" in line:
            print(line.split("[WHERE]", 1)[1].strip())
    return p.returncode


def watch(seconds):
    log = Path.home() / "Desktop" / "singws-display-watch.log"
    t0 = time.time()
    print(f"watching for {seconds} s; unplug / replug displays now. Log: {log}")
    proc = subprocess.Popen([SHELL, "--display-idle", "--display-place", "--display-idle-track", "Demo Artist - Demo Title.mp3"],
                            env=dict(os.environ, QT_QPA_PLATFORM="cocoa"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        with open(log, "w") as f:
            f.write(f"# display watch started {time.ctime(t0)}\n")
            end = t0 + seconds
            import selectors
            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            while time.time() < end and proc.poll() is None:
                for key, _ in sel.select(timeout=0.5):
                    line = key.fileobj.readline()
                    if line and ("[SCREEN]" in line or "[PLACE]" in line):
                        stamped = f"{time.time() - t0:8.2f}s  {line.strip()}"
                        print(stamped)
                        f.write(stamped + "\n")
                        f.flush()
    finally:
        proc.terminate()
    print("done; send the log file back")


def main(argv):
    if not Path(SHELL).exists():
        print(f"shell binary not found: {SHELL}\n(set SINGWS_SHELL_BIN or build it, see the top of this file)")
        if "--where" in argv or "--watch" in argv:
            return 2
    if "--where" in argv:
        return where()
    if "--watch" in argv:
        i = argv.index("--watch")
        secs = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 300
        watch(secs)
        return 0
    print(CHECKLIST)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
