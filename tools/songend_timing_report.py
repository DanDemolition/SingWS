#!/usr/bin/env python3
"""Report only: when does the background music START relative to the last audible audio, per case, as computed by the song-end model.

Each case is a scripted scenario run through the REAL Python hand-off code (tools/songend_scenarios.py) and through the Rust ``singws-songend``
machine; the two must agree (asserted), and the start time is read from the first ``bg:fade_in`` command. Changes nothing in the app.
  .venv/bin/python tools/songend_timing_report.py [--binary PATH]
Operator-like settings (the dev Mac's settings.json 2026-11-02): crossfade on, trim on, hold 1.0 s, overlap 3.0 s, fade-in floor -40 dB.
"""
import argparse
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import songend_scenarios as sc  # noqa: E402
import songend_parity as par  # noqa: E402

OP = dict(karaoke_bgm_crossfade_enabled=True, karaoke_trim_verified_tail=True, end_silence_trim_threshold_sec=1.0, bgm_overlap_before_audio_end_sec=3.0)
E = 240.0                                  # file (media) end, seconds
FAIL = {"scan_probe": "raise", "scan_trailing": "raise"}


def first_fade(scenario, binary):
    out = par.run_python(scenario)
    res = par.rust(binary, out["replay"])
    assert not par.compare(scenario["name"], out, res), "Python and Rust differ"
    for e, r in zip(out["expected"], res):
        for c in r["cmds"]:
            if c["c"] == "bg:fade_in":
                return (e["t"] - sc.T0_MS) / 1000.0, c["a"][1]
    return None, None


def row(label, t, ms, last_audio):
    if t is None:
        return f"| {label} | no start inside the run | | |"
    rel = t - last_audio
    fade = {3000: "+1.0 s (documented: -40 dB floor is -25 dB under final at 1.0 s of a 3 s fade)", 1000: "~+0.33 s (the same curve scaled to 1 s: estimate)"}.get(ms, "")
    return f"| {label} | {t:.1f} | {rel:+.1f} | {ms} ms; audible {fade} |"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", default=str(par.DEFAULT_BIN))
    args = ap.parse_args()
    lines = []
    lines.append("### Normal song end, audio end NOT verified (scan failed/pending): fallback pre-start\n")
    lines.append("File end 240.0 s. `rel` = music command time minus the last audible audio (negative = music begins before the audio ends).\n")
    lines.append("| case | command at (s) | rel (s) | fade |")
    lines.append("|---|---|---|---|")
    for lead_label, lead in (("1.0.2.8 fixed 3.2 s", 3.2), ("1.0.2.9 default 5.0 s", 5.0), ("8.0 s (clamp max)", 8.0)):
        for tail in (0.0, 3.0, 5.0, 8.0, 12.0):
            s = sc.scen(f"fb_{lead}_{tail}", [sc.start(0, "cdg", E, FAIL, duration_hint=E)], E + 15, **dict(OP, bgm_fallback_prestart_sec=lead))
            t, ms = first_fade(s, args.binary)
            lines.append(row(f"{lead_label}, dead tail {tail:g} s", t, ms, E - tail))
    lines.append("\n### Normal song end, audio end verified (cached), tail cleared; for comparison\n")
    lines.append("| case | command at (s) | rel (s) | fade |")
    lines.append("|---|---|---|---|")
    LIB = "/lib/s.zip"
    for tail in (3.0, 5.0, 8.0):
        arm = {"cached": {LIB: {"duration": E, "audio_end": E - tail}, "/t/x.cdg": {"visual_end": E - tail + 1.0, "visual_confidence": 0.9}}}
        s = sc.scen(f"ver_{tail}", [sc.start(0, "cdg", E, arm, library_path=LIB, duration_hint=E)], E + 15, **OP)
        t, ms = first_fade(s, args.binary)
        lines.append(row(f"verified, overlap 3.0 s, dead tail {tail:g} s", t, ms, E - tail))
    lines.append("\n### KaraFun end (no transport clock, no pre-start): music starts only when the completion is detected\n")
    lines.append("`D` = how long after the last audible audio the monitor completes the song (not in the app's log shape here: parameter).\n")
    lines.append("| case | command at (s) | rel (s) | fade |")
    lines.append("|---|---|---|---|")
    for d in (1.0, 3.0, 6.0, 10.0):
        s = sc.scen(f"kf_{d}", [sc.at(10.0 + d, "karafun_complete")], 10.0 + d + 5, **OP)
        t, ms = first_fade(s, args.binary)
        lines.append(row(f"KaraFun, completion detected D={d:g} s after the audio", t, ms, 10.0))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
