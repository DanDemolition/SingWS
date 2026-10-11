#!/usr/bin/env python3
"""Roadmap item 3, step 1 (measurement only): how different is the karaoke audio chain the app uses today (mpv/lavfi, an APPROXIMATION of the
master chain) from the exact Rust master chain the background music and pads use?

    .venv/bin/python tools/karaoke_chain_compare.py [--songs 5] [--seconds 40] [--settings ~/SingWS/settings.json] [--ablate]

For each real karaoke track (an MP3 from a library ZIP, a loud 40 s excerpt) it renders the SAME audio through
  A) the mpv `af` chain exactly as the app builds it (`mpv_audio_filters.build_af_chain`), offline in the bundled libmpv, and
  B) the Rust engine's graphic EQ + master processor (headless, the production DSP),
with the same settings, then aligns them and reports level, peak, per-octave spectrum, short-term loudness tracking and the null residual.
`--ablate` repeats it with ONE stage on at a time (graphic EQ, gate, tilt EQ, exciter, compressor, limiter) to say which stage differs.
Read-only: nothing in ~/SingWS is written, the settings file is only read. Prints no credentials (only master-audio / EQ keys are read).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp(prefix="singws-kchain-"))

RATE = 44100
BANDS = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)


def db(x):
    return 20.0 * np.log10(max(float(x), 1e-12))


def rms(a):
    return float(np.sqrt(np.mean(np.asarray(a, dtype=np.float64) ** 2)))


def write_wav(path, stereo):
    data = np.asarray(stereo, dtype="<f4")
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + data.nbytes) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, RATE, RATE * 8, 8, 32))
        fh.write(b"data" + struct.pack("<I", data.nbytes) + data.tobytes())


def read_wav(path):
    raw = open(path, "rb").read()
    i = raw.find(b"data")
    fmt_at = raw.find(b"fmt ") + 8
    code, ch, _rate, _br, _ba, bits = struct.unpack("<HHIIHH", raw[fmt_at: fmt_at + 16])
    if code == 0xFFFE:
        code = struct.unpack("<H", raw[fmt_at + 24: fmt_at + 26])[0]
    payload = raw[i + 8:]
    if code == 3 and bits == 32:
        arr = np.frombuffer(payload[: len(payload) // 4 * 4], dtype="<f4")
    elif code == 1 and bits == 16:
        arr = np.frombuffer(payload[: len(payload) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    else:
        raise RuntimeError(f"unexpected wav format {code}/{bits}")
    return arr.reshape(-1, ch)


def mpv_render(src_path, af_chain):
    """src file -> stereo float array through libmpv (offline, no sound device) with the given af chain."""
    from libmpv_media_jobs import OfflineMpvJob
    out = os.path.join(tempfile.mkdtemp(prefix="singws-kchain-"), "out.wav")
    job = OfflineMpvJob()
    try:
        job.option("config", "no")
        job.option("vid", "no")
        job.option("ao", "pcm")
        job.option("ao-pcm-file", out)
        job.option("ao-pcm-waveheader", "yes")
        job.option("audio-format", "floatp")
        job.option("audio-samplerate", str(RATE))
        job.option("audio-channels", "stereo")
        job.option("untimed", "yes")
        if af_chain:
            job.option("af", af_chain)
        job.initialize()
        job.command("loadfile", src_path, "replace")
        job.wait_for_end(180.0)
    finally:
        job.close()
    return read_wav(out)


def rust_render(src_path, frames, eq_on, eq_gains, master_on, params):
    import rust_bgm_engine as rbe
    from singws_eq import GraphicEQ
    from singws_master_audio import MasterAudioProcessor
    e = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
    try:
        e.set_master_volume(1.0)
        if eq_on:
            eq = GraphicEQ(sample_rate=RATE, channels=2)
            eq.set_all_gains_db(eq_gains)
            eq.set_enabled(True)
            e.set_eq(eq)
        if master_on:
            proc = MasterAudioProcessor(sample_rate=RATE, channels=2)
            proc.set_params(params)
            proc.set_enabled(True)
            e.set_master_processor(proc)
        e.load(src_path, paused=False, volume=1.0)
        out = []
        got = 0
        while got < frames:
            chunk = e.render(1200)         # paced: a headless render outruns the streaming decoder otherwise
            out.append(chunk)
            got += len(chunk)
            time.sleep(0.002)
        return np.concatenate(out)[:frames]
    finally:
        e.close()


def align(a, b):
    """Return (a, b) trimmed to the common aligned range (b may be delayed by the limiter look-ahead / engine latency)."""
    n = min(len(a), len(b), RATE * 6)
    ma, mb = a[:n].mean(axis=1), b[:n].mean(axis=1)
    fa, fb = np.fft.rfft(ma, 2 * n), np.fft.rfft(mb, 2 * n)
    xc = np.fft.irfft(fa * np.conj(fb))
    lag = int(np.argmax(np.concatenate([xc[-2000:], xc[:2000]]))) - 2000   # >0: a leads b
    if lag > 0:
        a, b = a[lag:], b
    elif lag < 0:
        a, b = a, b[-lag:]
    n = min(len(a), len(b)) - RATE // 2
    return a[RATE // 2: n], b[RATE // 2: n], lag


def octave_levels(sig):
    spec = np.abs(np.fft.rfft(sig.mean(axis=1) * np.hanning(len(sig)))) ** 2
    f = np.fft.rfftfreq(len(sig), 1.0 / RATE)
    out = []
    for c in BANDS:
        m = (f >= c / 2 ** 0.5) & (f < c * 2 ** 0.5)
        out.append(10 * np.log10(max(float(spec[m].sum()), 1e-20)))
    return np.array(out)


def short_term(sig, win=0.4):
    n = int(win * RATE)
    m = sig.mean(axis=1)
    return np.array([db(rms(m[i:i + n])) for i in range(0, len(m) - n, n // 2)])


def compare(a, b):
    a, b, lag = align(a, b)
    la, lb = octave_levels(a), octave_levels(b)
    sa, sb = short_term(a), short_term(b)
    live = (sa > -60) & (sb > -60)
    dyn = (sb - sa)[live]
    return {
        "lag_ms": round(lag * 1000.0 / RATE, 2),
        "rms_a": db(rms(a)), "rms_b": db(rms(b)), "peak_a": db(np.abs(a).max()), "peak_b": db(np.abs(b).max()),
        "band_diff": (lb - la),
        "dyn_mean": float(dyn.mean()), "dyn_p95": float(np.percentile(np.abs(dyn - dyn.mean()), 95)),
        "null_db": db(rms(a - b)) - db(rms(a)),
    }


def line(name, r):
    worst = float(np.max(np.abs(r["band_diff"])))
    flag = ""
    if abs(r["rms_b"] - r["rms_a"]) > 0.5:
        flag += " LEVEL"
    if worst > 1.0:
        flag += " SPECTRUM"
    if r["dyn_p95"] > 1.0:
        flag += " DYNAMICS"
    print(f"  {name:34} A {r['rms_a']:6.1f} B {r['rms_b']:6.1f} dB rms | diff {r['rms_b'] - r['rms_a']:+5.2f} | peaks A {r['peak_a']:5.1f} B {r['peak_b']:5.1f} | "
          f"worst octave {worst:4.1f} dB | dynamics p95 {r['dyn_p95']:4.2f} dB | null {r['null_db']:6.1f} dB{flag}")


def pick_songs(count, seconds):
    lib = Path.home() / "Music/Karaoke/Karaoke Library"
    zips = sorted(glob.glob(str(lib / "*.zip")))
    rng = np.random.default_rng(11)
    rng.shuffle(zips)
    work = Path(tempfile.mkdtemp(prefix="singws-kchain-songs-"))
    songs = []
    for z in zips:
        if len(songs) >= count:
            break
        try:
            with zipfile.ZipFile(z) as zf:
                mp3 = next((n for n in zf.namelist() if n.lower().endswith(".mp3") and "__MACOSX" not in n), None)
                if not mp3:
                    continue
                src = work / f"{len(songs)}.mp3"
                src.write_bytes(zf.read(mp3))
        except Exception:
            continue
        wav = mpv_render(str(src), "")                       # decoded once, no filters: the common input for A and B
        if len(wav) < RATE * (seconds + 20):
            continue
        # the loudest `seconds`-long window in the middle of the song
        hop, win = RATE * 5, RATE * seconds
        best = max(range(RATE * 10, len(wav) - win, hop), key=lambda i: rms(wav[i:i + win]))
        excerpt = wav[best:best + win]
        path = work / f"song{len(songs)}.wav"
        write_wav(path, excerpt)
        songs.append((Path(z).stem[:48], str(path), len(excerpt)))
    return songs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", type=int, default=5)
    ap.add_argument("--seconds", type=int, default=40)
    ap.add_argument("--settings", default=os.path.expanduser("~/SingWS/settings.json"))
    ap.add_argument("--ablate", action="store_true")
    args = ap.parse_args()
    import importlib.util
    spec = importlib.util.spec_from_file_location("singws_main_kchain", ROOT / "0.2.18.1.py")
    app = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = app
    spec.loader.exec_module(app)
    import mpv_audio_filters as maf
    from singws_master_audio import DEFAULT_PARAMS

    raw = json.load(open(args.settings))
    st = {k: v for k, v in raw.items() if str(k).startswith("master_audio")}
    eq_gains = [float(v or 0.0) for v in (raw.get("eq_karaoke") or [0.0] * 10)]
    eq_on = bool(raw.get("eq_karaoke_enabled", False))
    base = {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=st))}
    stress_st = {**st, "master_audio_comp_amount": 70, "master_audio_exciter_mix": 40, "master_audio_tilt": 30, "master_audio_ceiling_db": -3.0}
    stress = {**DEFAULT_PARAMS, **app.KaraokeApp._compute_master_audio_params(SimpleNamespace(settings=stress_st))}
    print("operator karaoke EQ on=%s gains=%s | master settings: %s" % (eq_on, eq_gains, json.dumps(st, sort_keys=True)))

    songs = pick_songs(args.songs, args.seconds)
    print(f"{len(songs)} real karaoke tracks, loudest {args.seconds} s of each\n")
    quiet = {"gate_enabled": 0.0, "eq_enabled": 0.0, "comp_enabled": 0.0, "limiter_enabled": 0.0, "exciter_mix": 0.0}
    cases = [("operator settings", eq_on, eq_gains, True, base), ("stress settings", True, eq_gains, True, stress)]
    if args.ablate:
        full_on = {**DEFAULT_PARAMS, **{k: v for k, v in base.items()}}
        cases += [("EQ only", True, eq_gains, False, base),
                  ("gate only", False, eq_gains, True, {**base, **quiet, "gate_enabled": 1.0}),
                  ("tilt EQ only", False, eq_gains, True, {**base, **quiet, "eq_enabled": 1.0}),
                  ("exciter only", False, eq_gains, True, {**base, **quiet, "exciter_mix": base.get("exciter_mix", 0.15) or 0.15}),
                  ("compressor only", False, eq_gains, True, {**base, **quiet, "comp_enabled": 1.0}),
                  ("limiter only", False, eq_gains, True, {**base, **quiet, "limiter_enabled": 1.0})]
        del full_on
    summary = {}
    for name, path, frames in songs:
        print(name)
        for case, e_on, gains, m_on, params in cases:
            af = maf.build_af_chain(eq_enabled=e_on, eq_gains_db=gains, master_enabled=m_on, master_params=params)
            a = mpv_render(path, af)
            b = rust_render(path, frames, e_on, gains, m_on, params)
            r = compare(a, b)
            line(case, r)
            summary.setdefault(case, []).append(r)
        print()
    print("AVERAGE over the tracks (B = exact Rust chain, A = what karaoke uses today):")
    for case, rs in summary.items():
        avg = {k: (np.mean([r[k] for r in rs], axis=0)) for k in ("rms_a", "rms_b", "peak_a", "peak_b", "band_diff", "dyn_p95", "null_db")}
        line(case, avg)
    print("\nband-by-band (B - A, dB) for 'operator settings':", dict(zip(BANDS, np.round(np.mean([r["band_diff"] for r in summary["operator settings"]], axis=0), 2))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
