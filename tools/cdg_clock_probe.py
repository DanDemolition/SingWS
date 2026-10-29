#!/usr/bin/env python3
"""Which CDG packet does the app's video bridge show at a given position, with a CDG timing offset and tempo? Uses a made-up CDG in which
EVERY packet produces a different picture (one XOR tile per packet, colour changing each pass), so a grabbed frame identifies its packet.
Prints, per (offset ms, tempo %, seek position ms): the bridge's reported position and the time of the packet shown (packet * 10/3 ms).
Silent; opens two small windows. Needs the same environment as cdg_bridge_compare.py.

    SINGWS_VIDEO_BRIDGE=rust SINGWS_VIDEO_LIB=... QT_QPA_PLATFORM=cocoa <venv>/bin/python tools/cdg_clock_probe.py OUT_DIR
"""
import numpy as np
import os, subprocess, sys, time
from pathlib import Path
from PyQt6.QtWidgets import QApplication, QWidget
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import cdg_diff as c  # noqa: E402
from mpv_playback_iina import MpvPlaybackPlugin  # noqa: E402


def counter_cdg(packets=4200) -> bytes:
    p = [c.pk(30, bytes(sum(([k * 4, k * 3 + 1] for k in range(8)), []))), c.pk(31, bytes(sum(([k * 4 + 2, k * 5 + 7] for k in range(8)), []))), c.pk(1, [0])]
    while len(p) < packets:
        i = len(p)
        p.append(c.tile(0, 1 + (i // 900) % 15, (i % 900) // 50, i % 50, [0xFF] * 12, xor=True))
    return b"".join(p)


def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    cdg, mp3 = out / "counter.cdg", out / "counter.mp3"
    cdg.write_bytes(counter_cdg())
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "16", "-q:a", "9", str(mp3)], check=True)
    dump = os.environ.get("CARGO_TARGET_DIR", str(Path.home() / ".cache/singws-rust-target-cdg")) + "/release/singws-cdg-dump"
    allf = subprocess.run([dump, str(cdg)], capture_output=True).stdout   # every packet emits a frame here
    fsize = 300 * 216 * 4
    frames, nframes = allf, len(allf) // fsize
    app = QApplication([])
    o, pv = QWidget(), QWidget()
    o.setGeometry(60, 80, 320, 180); pv.setGeometry(500, 80, 320, 180); o.show(); pv.show(); app.processEvents()
    plugin = MpvPlaybackPlugin(log=lambda m: None)
    plugin.attach(pv, o)
    def pump(s):
        end = time.monotonic() + s
        while time.monotonic() < end:
            app.processEvents(); time.sleep(0.02)
    if not plugin.loadSingWSMedia(str(cdg), str(mp3), autoplay=False):
        raise SystemExit("load failed")
    plugin.setVolume(0.0)
    while not plugin.visualsReady():
        pump(0.05)
    for offset in ((0, 750) if os.environ.get('CDG_PROBE_PLAYING') else (0, 750, -300)):
        for tempo in (1.0, 1.25):
            plugin.setVideoOffsetMs(offset); plugin.setTempoRatio(tempo); pump(0.5)
            for pos in ((2000,) if os.environ.get('CDG_PROBE_PLAYING') else (2000, 6000)):
                plugin.pauseMedia(); pump(0.3); plugin.seekMedia(pos); pump(1.5)
                if os.environ.get("CDG_PROBE_PLAYING"):
                    # while playing the offset (mpv audio-delay) shifts the picture against the clock: measure it live
                    plugin.playMedia(); pump(1.5)
                    p0 = plugin.positionMs(); img = plugin.grabFrame(); p1 = plugin.positionMs()
                    plugin.pauseMedia()
                else:
                    p0 = p1 = None
                    img = plugin.grabFrame()
                img = img.convertToFormat(img.Format.Format_RGBA8888)
                ptr = img.constBits(); ptr.setsize(img.sizeInBytes())
                raw = bytes(ptr)
                # the bridge frame has no alpha and libmpv rounds colours by one: take the packet whose picture is closest
                key = np.frombuffer(raw, np.uint8).reshape(-1, 4)[:, :3].astype(np.int16)
                best, hit = None, None
                for i in range(nframes):
                    f = frames[i * fsize:(i + 1) * fsize]
                    d = int(np.abs(np.frombuffer(f, np.uint8).reshape(-1, 4)[:, :3].astype(np.int16) - key).sum())
                    if best is None or d < best:
                        best, hit = d, i
                if best is not None and best > 3 * 64800:
                    hit = None
                shown = None if hit is None else round(hit * 10 / 3)
                print(f"offset={offset:+5d} ms tempo={int(tempo*100)}% seek={pos}: bridge position {plugin.positionMs()} ms, picture of packet {hit} = {shown} ms (shown - position = {None if shown is None else shown - plugin.positionMs()} ms; live window {p0}..{p1})")
    plugin.shutdown()


if __name__ == "__main__":
    main()
