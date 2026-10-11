#!/usr/bin/env python3
"""Blind listening test for key / tempo quality (roadmap item 3/4): the app's rubberband (inside mpv, exactly as the bridge applies it) against
Signalsmith Stretch, on real karaoke audio.

    .venv-test-arm64-fresh/bin/python tools/stretch_blind_test.py --out ~/Downloads/singws-stretch-blind-test [--ss-cli PATH] [--songs 3] [--seconds 15]

Engines (labels are shuffled per case; the key is in `answer_key.json`, which the listening page never loads):
  rubberband   mpv `af=rubberband=pitch-scale=...` + the `speed` property, the way the app does key and tempo today
  signalsmith  Signalsmith Stretch (default preset), no formant compensation (how the old engine ran it)
  signalsmith+formant  the same with formant compensation on (keeps vocals/instruments from sounding chipmunk/giant when the key moves)
Every output of a case is gain-matched to the rubberband one (RMS) so loudness cannot decide the vote. Writes 16-bit WAVs + index.html (a page
with players and a "copy my picks" button) + answer_key.json + speed.txt (how many times real time each engine runs).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-stretch-"))
import karaoke_chain_compare as kc  # noqa: E402

RATE = kc.RATE
SCENARIOS = [("key +2", 2.0, 1.0), ("key -3", -3.0, 1.0), ("tempo 90%", 0.0, 0.90), ("tempo 110%", 0.0, 1.10), ("key +2 and tempo 95%", 2.0, 0.95)]


def write16(path, stereo):
    pcm = (np.clip(stereo, -1.0, 1.0) * 32767.0).astype("<i2")
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + pcm.nbytes) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, RATE, RATE * 4, 4, 16))
        fh.write(b"data" + struct.pack("<I", pcm.nbytes) + pcm.tobytes())


def run_rubberband(src_wav, semitones, speed):
    """mpv offline, exactly the app's routing: rubberband carries key AND tempo; speed is the mpv property."""
    from libmpv_media_jobs import OfflineMpvJob
    out = os.path.join(tempfile.mkdtemp(prefix="singws-rb-"), "o.wav")
    job = OfflineMpvJob()
    try:
        for k, v in (("config", "no"), ("vid", "no"), ("ao", "pcm"), ("ao-pcm-file", out), ("ao-pcm-waveheader", "yes"),
                     ("audio-format", "floatp"), ("audio-samplerate", str(RATE)), ("audio-channels", "stereo"), ("untimed", "yes")):
            job.option(k, v)
        if semitones != 0 or speed != 1.0:
            job.option("af", f"rubberband=pitch-scale={2 ** (semitones / 12.0):.6f}")
        if speed != 1.0:
            job.option("speed", f"{speed:.6f}")
        job.initialize()
        job.command("loadfile", src_wav, "replace")
        job.wait_for_end(180.0)
    finally:
        job.close()
    return kc.read_wav(out)


def run_signalsmith(cli, stereo, semitones, speed, formant):
    work = tempfile.mkdtemp(prefix="singws-ss-")
    fin, fout = os.path.join(work, "in.f32"), os.path.join(work, "out.f32")
    np.asarray(stereo, dtype="<f4").tofile(fin)
    subprocess.run([cli, fin, fout, "2", str(RATE), f"{1.0 / speed:.8f}", f"{semitones:.4f}", "1" if formant else "0"], check=True)
    return np.fromfile(fout, dtype="<f4").reshape(-1, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads/singws-stretch-blind-test"))
    ap.add_argument("--ss-cli", default="")
    ap.add_argument("--songs", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=15)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    cli = args.ss_cli or os.environ.get("SS_CLI", "")
    if not cli or not os.path.exists(cli):
        print("need --ss-cli (the built Signalsmith driver)")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    songs = kc.pick_songs(args.songs, args.seconds)
    key, cases, speed_log = {}, [], {"rubberband": [], "signalsmith": [], "signalsmith+formant": []}
    for si, (sname, spath, frames) in enumerate(songs, 1):
        src = kc.read_wav(spath)
        for ci, (label, semis, speed) in enumerate(SCENARIOS, 1):
            case = f"song{si}_case{ci}"
            produced = {}
            t0 = time.time(); produced["rubberband"] = run_rubberband(spath, semis, speed); speed_log["rubberband"].append((time.time() - t0, len(src) / RATE))
            t0 = time.time(); produced["signalsmith"] = run_signalsmith(cli, src, semis, speed, False); speed_log["signalsmith"].append((time.time() - t0, len(src) / RATE))
            t0 = time.time(); produced["signalsmith+formant"] = run_signalsmith(cli, src, semis, speed, True); speed_log["signalsmith+formant"].append((time.time() - t0, len(src) / RATE))
            n = min(len(v) for v in produced.values())
            target = kc.rms(produced["rubberband"][RATE // 2: n])
            order = list(produced)
            rng.shuffle(order)
            files = {}
            for letter, engine in zip("ABC", order):
                sig = produced[engine][:n]
                gain = target / max(kc.rms(sig[RATE // 2:]), 1e-9)
                write16(out / f"{case}_{letter}.wav", sig * min(max(gain, 0.7), 1.4))
                files[letter] = f"{case}_{letter}.wav"
                key.setdefault(case, {})[letter] = engine
            cases.append({"case": case, "song": f"song {si}", "change": label, "files": files})
            print(f"{case}: {label}", flush=True)
    (out / "answer_key.json").write_text(json.dumps({"songs": {f"song {i}": s[0] for i, s in enumerate(songs, 1)}, "key": key}, indent=1))
    lines = ["How many times faster than real time each engine ran on this Mac (higher = cheaper):"]
    for eng, vals in speed_log.items():
        lines.append(f"  {eng:22} {sum(d for _t, d in vals) / sum(t for t, _d in vals):6.0f}x")
    (out / "speed.txt").write_text("\n".join(lines) + "\n")
    rows = []
    for c in cases:
        players = "".join(f'<div class="p"><b>{l}</b><audio controls preload="none" src="{f}"></audio><label><input type="radio" name="{c["case"]}" value="{l}"> best</label></div>' for l, f in c["files"].items())
        rows.append(f'<section><h3>{c["song"]}: {c["change"]}</h3>{players}<label>worst: <select name="{c["case"]}_worst"><option value="">-</option><option>A</option><option>B</option><option>C</option></select></label></section>')
    html = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Key and tempo blind test</title>
<style>body{font:15px system-ui;margin:20px auto;max-width:760px;padding:0 16px;color:#222}section{border-bottom:1px solid #ddd;padding:10px 0}.p{display:flex;gap:10px;align-items:center;margin:4px 0}
audio{height:32px}button{font-size:15px;padding:8px 14px}textarea{width:100%;height:120px}h3{margin:6px 0}</style>
<h2>Key and tempo: blind test</h2><p>Three versions (A, B, C) of the same 15 seconds, with the key or tempo changed. The order is shuffled for each, and loudness is matched. Pick the one that sounds best, and optionally the worst. Listen on your show speakers or good headphones.</p>
""" + "".join(rows) + """<p><button onclick="go()">Copy my picks</button></p><textarea id=o readonly placeholder="your picks appear here: paste them to Claude"></textarea>
<script>function go(){const o=[];document.querySelectorAll('section').forEach(s=>{const r=s.querySelector('input:checked'),w=s.querySelector('select');o.push(s.querySelector('h3').textContent+' -> best '+(r?r.value:'?')+(w&&w.value?', worst '+w.value:''))});const t=o.join('\\n');document.getElementById('o').value=t;navigator.clipboard&&navigator.clipboard.writeText(t)}</script>"""
    (out / "index.html").write_text(html)
    print("\n".join(lines))
    print(f"\nwritten to {out}\nopen {out}/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
