#!/usr/bin/env python3
"""Roadmap item 3, step 1b: karaoke audio through the RUST path (decode -> Signalsmith key/tempo -> graphic EQ -> exact master chain, via
`singws-bgm-render --process`) against what the app plays today (the same audio through libmpv with the real `af` chain, key/tempo by the
mpv `signalsmith` filter and the `speed` property). Real library songs, the operator's real karaoke EQ and master settings.

    .venv-test-arm64-fresh/bin/python tools/karaoke_rust_compare.py [--songs 3] [--seconds 40] [--render PATH]

Identity (key 0, tempo 100%) should match closely (same ordering of nothing but the chain). With key/tempo the Rust path stretches BEFORE
the effects (they act on what is heard) while mpv must stretch LAST, so the null residual is not expected to be tiny there: read loudness,
peaks, octave-band levels and dynamics instead.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-krc-"))
import karaoke_chain_compare as kc  # noqa: E402

RATE = kc.RATE
DEFAULT_RENDER = Path(os.environ.get("CARGO_TARGET_DIR") or Path.home() / ".cache/singws-rust-target") / "release/singws-bgm-render"


def mpv_render_kt(src, af_dsp, semitones, speed):
    from libmpv_media_jobs import OfflineMpvJob
    out = os.path.join(tempfile.mkdtemp(prefix="singws-krc-mpv-"), "o.wav")
    chain = af_dsp
    if semitones or speed != 1.0:
        chain = (chain + "," if chain else "") + f"@stretch:signalsmith=pitch-scale={2 ** (semitones / 12.0):.6f}"
    job = OfflineMpvJob()
    try:
        for k, v in (("config", "no"), ("vid", "no"), ("ao", "pcm"), ("ao-pcm-file", out), ("ao-pcm-waveheader", "yes"),
                     ("audio-format", "floatp"), ("audio-samplerate", str(RATE)), ("audio-channels", "stereo"), ("untimed", "yes")):
            job.option(k, v)
        if chain:
            job.option("af", chain)
        if speed != 1.0:
            job.option("speed", f"{speed:.6f}")
        job.initialize()
        job.command("loadfile", src, "replace")
        job.wait_for_end(240.0)
    finally:
        job.close()
    return kc.read_wav(out)


def rust_render_kt(render, src, semitones, tempo_pct, eq_on, eq_gains, master_on, params):
    out = os.path.join(tempfile.mkdtemp(prefix="singws-krc-rs-"), "o.wav")
    cmd = [str(render), "--process", "--rate", str(RATE), "--volume", "1.0", "--out", out, "--key", str(semitones), "--tempo", str(tempo_pct)]
    if eq_on:
        cmd += ["--eq", ",".join(f"{g:.4f}" for g in eq_gains)]
    if master_on:
        pf = os.path.join(os.path.dirname(out), "params.txt")
        with open(pf, "w") as fh:
            for k, v in params.items():
                if isinstance(v, (int, float, bool)):
                    fh.write(f"{k} {float(v)}\n")
        cmd += ["--master-params", pf]
    cmd.append(src)
    subprocess.run(cmd, check=True, capture_output=True)
    return kc.read_wav(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=3)
    ap.add_argument("--seconds", type=int, default=40)
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    ap.add_argument("--render", default=str(DEFAULT_RENDER))
    args = ap.parse_args()
    if not Path(args.render).exists():
        print("build the renderer first: cd rust && cargo build --release -p singws-bgm --bin singws-bgm-render")
        return 1
    import importlib.util
    spec = importlib.util.spec_from_file_location("singws_main_krc", ROOT / "0.2.18.1.py")
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    import mpv_audio_filters as maf
    from singws_master_audio import DEFAULT_PARAMS

    raw = json.load(open(args.settings))
    st = {k: v for k, v in raw.items() if str(k).startswith("master_audio")}
    eq_gains = [float(v or 0.0) for v in (raw.get("eq_karaoke") or [0.0] * 10)]
    eq_on = bool(raw.get("eq_karaoke_enabled", False))
    params = {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=st))}
    af_dsp = maf.build_af_chain(eq_enabled=eq_on, eq_gains_db=eq_gains, master_enabled=True, master_params=params)
    songs = kc.pick_songs(args.songs, args.seconds)
    print(f"{len(songs)} real karaoke tracks, loudest {args.seconds} s; operator EQ on={eq_on}\n")
    cases = [("identity (key 0, 100%)", 0, 100), ("key +2", 2, 100), ("key -3", -3, 100), ("tempo 90%", 0, 90), ("tempo 110%", 0, 110),
             ("key +2, tempo 95%", 2, 95)]
    summary = {}
    for name, path, _frames in songs:
        print(name)
        for case, semis, tempo in cases:
            a = mpv_render_kt(path, af_dsp, semis, tempo / 100.0)
            b = rust_render_kt(args.render, path, semis, tempo, eq_on, eq_gains, True, params)
            r = kc.compare(a, b)
            kc.line(case, r)
            summary.setdefault(case, []).append(r)
        print()
    print("AVERAGE (A = what karaoke plays today via mpv, B = the Rust path):")
    for case, rs in summary.items():
        avg = {k: np.mean([r[k] for r in rs], axis=0) for k in ("rms_a", "rms_b", "peak_a", "peak_b", "band_diff", "dyn_p95", "null_db")}
        kc.line(case, avg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
