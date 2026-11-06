#!/usr/bin/env python3
"""Parity for the song-end shadow's idealised driver (songend_shadow.predict): for every scenario of tools/songend_scenarios.py that is ONE uneventful song (a song_start and nothing
else: no pause, seek, stall, skipped tick or stop), the REAL Python code (the harness, fakes for the player and the clock) and the Rust model driven by ``predict`` must start the
background music at the same song position, for the same logged reason.

  <python with PyQt6> tools/songend_predict_parity.py [--seeds N] [--binary PATH]      (SINGWS_HOME is a scratch dir; QT_QPA_PLATFORM=offscreen)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))


def spec_of(sc):
    a = sc["script"][0]["a"]
    return {"settings": sc["settings"], "mode": a["mode"], "media_dur_s": a["media_dur_s"], "arm": a.get("arm", {}), "library_path": a.get("library_path", ""),
            "duration_hint": a.get("duration_hint"), "audio_path": a.get("audio_path", "/t/x.mp3"), "visual_path": a.get("visual_path", "/t/x.cdg"),
            "deliver_ms": a.get("deliver_ms", 40), "level_db": a.get("level_db"), "engine_at_end": a.get("engine_at_end"), "gate": a.get("gate", True),
            "sectors": a.get("sectors"), "db_duration": a.get("db_duration"), "session_started": sc.get("bgm_session_started", True)}


def uneventful(sc):
    return len(sc["script"]) == 1 and sc["script"][0]["ev"] == "song_start" and not sc.get("tick_skips") and sc["script"][0]["a"].get("gate", True) is True


def python_first_fade(out, t0):
    for e in out["expected"]:
        if any(c.get("c") == "bg:fade_in" for c in e["cmds"]):
            return (e["t"] - t0) / 1000.0, [c["a"][0] for c in e["cmds"] if c.get("c") == "log" and c.get("a")]
        if any(c.get("c") in ("host:stop_playback", "host:play_next_file") for c in e["cmds"]):
            return None, []     # the song ended without the music having started first
    return None, []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=400)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-songend-main/release/singws-songend"))
    args = ap.parse_args(argv)
    import tempfile
    if not os.environ.get("SINGWS_HOME"):
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-songend-predict-")
    import songend_scenarios as ss
    import songend_shadow as sh
    corpus = [s for s in ss.scripted() + [ss.random_scenario(i) for i in range(args.seeds)] if uneventful(s)]
    same = diffs = never = 0
    for sc in corpus:
        py_pos, py_logs = python_first_fade(ss.run(sc), ss.T0_MS)
        rs = sh.predict(spec_of(sc), helper=Path(args.binary), max_s=sc["run_ms"] / 1000.0)
        rs_pos, rs_logs = (rs["pos_s"], rs["logs"]) if rs else (None, [])
        py_logs, rs_logs = [sh.reason_of(py_logs)] if py_pos is not None else [], [sh.reason_of(rs_logs)] if rs_pos is not None else []
        if py_pos is None and rs_pos is None:
            never += 1
            continue
        if py_pos == rs_pos and py_logs == rs_logs:
            same += 1
        else:
            diffs += 1
            if diffs <= 8:
                print(f" - {sc['name']}: python={py_pos} {py_logs[:1]} rust={rs_pos} {rs_logs[:1]}")
    print(f"uneventful scenarios={len(corpus)} identical={same} never-start-in-both={never} different={diffs}")
    print("IDENTICAL" if not diffs else f"{diffs} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
