#!/usr/bin/env python3
"""Does libmpv's SOFTWARE renderer (the no-GL video path a Qt Quick display can use) give the picture a normal decode gives? Makes a 6 s test clip
with ffmpeg (testsrc2, 640x360, made up), renders frames at several times with `singws-video-swframe` (libmpv sw render, no window, no sound) and
compares them with ffmpeg's own decode of the same instants. Prints the mean and largest channel difference and the share of pixels differing by
more than --tolerance. Needs ffmpeg and the libmpv dylib (SINGWS_LIBMPV, default native/mpv_runtime/artifacts/<arch>/Frameworks/singws_libmpv.2.dylib).
"""
import argparse, os, platform, subprocess, sys, tempfile
from pathlib import Path
import numpy as np

W, H = 640, 360
ap = argparse.ArgumentParser(); ap.add_argument("--tolerance", type=int, default=6)
ap.add_argument("--times", type=float, nargs="*", default=[0.5, 2.0, 4.5])
a = ap.parse_args()
root = Path(__file__).resolve().parent.parent
arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
env = dict(os.environ)
env.setdefault("SINGWS_LIBMPV", str(root / f"native/mpv_runtime/artifacts/{arch}/Frameworks/singws_libmpv.2.dylib"))
exe = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-cdg")) + "/release/singws-video-swframe"
tmp = Path(tempfile.mkdtemp(prefix="vsw-")); clip = tmp / "clip.mp4"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15", str(clip)], check=True)
bad = 0
for t in a.times:
    got = subprocess.run([exe, str(clip), str(t), str(W), str(H)], env=env, capture_output=True)
    if got.returncode != 0 or len(got.stdout) != W * H * 4:
        print(f"t={t}: swframe failed: {got.stderr.decode()[-300:]}"); bad += 1; continue
    ref = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(clip), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    g = np.frombuffer(got.stdout, np.uint8).reshape(-1, 4)[:, :3].astype(int); r = np.frombuffer(ref, np.uint8).reshape(-1, 4)[:, :3].astype(int)
    d = np.abs(g - r).max(axis=1)
    print(f"t={t}s: mean diff {np.abs(g - r).mean():.2f}, largest {d.max()}, {100.0 * (d > a.tolerance).mean():.2f}% of pixels differ by more than {a.tolerance}")
    bad += (d > a.tolerance).mean() > 0.02
sys.exit(1 if bad else 0)
