#!/usr/bin/env python3
"""Parity: the REAL settings migrations of KaraokeApp.__init__ (0.2.18.1.py) vs the Rust crate ``singws-settingsmig``.

The migrations are one inline block of the app's startup code (from "Merge defaults" up to the DEFAULTS fill). This tool extracts that block's SOURCE from
0.2.18.1.py at run time and executes it unchanged on a stand-in ``self`` (settings dict + the real ``_cdg_timing_engine``), so the app is not modified and the
oracle can never drift from the app. Each corpus case is a settings dict; both sides must produce the same dict (same keys, same order, same value TYPES) and the
same ``changed`` flag. A case the Rust side declines (an integer beyond 64 bits, non-ASCII numeric text) is counted and checked to be one Python really handles
differently (it must contain such a value), never silently skipped.

  <python with PyQt6> tools/settings_migrate_parity.py [--seeds N] [--binary PATH]      (SINGWS_HOME is a scratch dir; QT_QPA_PLATFORM=offscreen)
Build first:  CARGO_TARGET_DIR=~/.cache/singws-rust-target-settingsmig cargo build --release -p singws-settingsmig   (in rust/)
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
import subprocess
import sys
import textwrap
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

START = "# Merge defaults (non-destructive) and persist if anything was missing"
END = "_fill_before = dict(self.settings)"


def real_migration(main):
    lines = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8").splitlines()
    start = next(i for i, x in enumerate(lines) if x.strip() == START)
    end = next(i for i in range(start, len(lines)) if lines[i].strip().startswith(END))
    block = textwrap.dedent("\n".join(lines[start + 1:end]))
    code = "def _blk(self):\n" + textwrap.indent(block, "    ") + "\n    return changed\n"
    ns = dict(main.__dict__)
    ns["_diag"] = lambda *a, **k: None
    exec(compile(code, "<settings-migration-block>", "exec"), ns)
    fn = ns["_blk"]

    def run(settings):
        me = types.SimpleNamespace(settings=copy.deepcopy(settings))
        me._cdg_timing_engine = lambda: main.KaraokeApp._cdg_timing_engine(None)
        changed = fn(me)
        return me.settings, bool(changed)
    return run


def rust_batch(binary, cases):
    """Every case in one process; a case holding an integer beyond 64 bits is answered by the single-case path (which declines it first)."""
    proc = subprocess.run([str(binary)], input=json.dumps({"batch": cases}).encode(), capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-400:])
    return json.loads(proc.stdout)


def rust(binary, settings):
    proc = subprocess.run([str(binary)], input=json.dumps({"settings": settings}).encode(), capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[-400:])
    return json.loads(proc.stdout)


MARKERS = ["transport_shadow_default_on_migrated", "rust_evidence_defaults_migrated", "waitlist_snapshot_default_on_migrated", "performance_debug_default_migrated",
           "show_safe_stall_diagnostics_migrated", "stall_stack_capture_off_migrated", "ffmpeg_cdg_timing_migrated", "ffmpeg_cdg_750_baseline_migrated",
           "cdg_timing_engine_split_migrated", "mpv_cdg_minus_50_baseline_migrated", "mpv_mp4_100_baseline_migrated", "mpv_mp4_zero_baseline_migrated",
           "end_silence_threshold_2_5_migrated"]
NUMERIC_KEYS = ["cdg_timing_offset_ms", "video_timing_offset_ms", "cdg_timing_offset_mpv_ms", "mp4_timing_offset_mpv_ms", "end_silence_trim_threshold_sec"]
OTHER_KEYS = {
    "performance_debug_enabled": [True, False, None, 0, 1, "", "x"],
    "shell_commands": ["off", " OFF ", "Off\n", "dry_run", "apply", "", 0, None, True, ["off"], "offf", " off ", "OFF"],
    "transport_shadow": [True, False, None],
    "stall_stack_capture": [True, False],
    "stall_event_attribution": [True, False],
    "waitlist_snapshot": [True, False],
}
FLAG_VALUES = [True, False, 0, 1, None, "yes", "", "0", [], {}, [0], 2.5, 0.0]
NUM_POOL = [None, True, False, 0, 1, -1, 100, 150, -150, 750, 850, 900, 3000, -3000, 100000, -100000, 3.9, -3.9, 0.0, 150.0, 149.9, "12", " 7 ", "1_0", "1__0", "_1", "1e3",
            "abc", "", "0x10", "+5", "-0", "-150", " 150 ", "5.5", "\t3\n", [], {}, [1], {"a": 1}, 2 ** 40, -(2 ** 40), 5, 6, 5.0, 6.0, "5", "6.0", "5.0", " 6 ", 2.5, 4, 4.0,
            "inf", "nan", "Infinity", "1_0.5", "0b1", "٣", 1e20, 10 ** 20, "99999999999999999999"]
DECLINE_MARKS = (1e20, 10 ** 20, "99999999999999999999", "٣")


def random_case(r):
    s = {}
    if r.random() < 0.5:
        s["junk_first"] = r.randint(0, 9)
    for k in ("performance_mode", "safe_mode", "mp4_max_height", "karafun_transparent_handoff", "karafun_windowed_reveal", "karafun_transparent_rehearsal_reset"):
        if r.random() < 0.15:
            s[k] = r.choice([True, 1, "x"])
    for m in MARKERS:
        if r.random() < 0.45:
            s[m] = r.choice(FLAG_VALUES)
    for k in NUMERIC_KEYS:
        if r.random() < 0.55:
            s[k] = r.choice(NUM_POOL)
    for k, pool in OTHER_KEYS.items():
        if r.random() < 0.4:
            s[k] = r.choice(pool)
    if r.random() < 0.5:
        s["junk_last"] = r.randint(0, 9)
    items = list(s.items())
    r.shuffle(items)
    return dict(items)


def scripted():
    out = [{}, {"cdg_timing_offset_ms": 900}, {"cdg_timing_offset_ms": 850}, {"cdg_timing_offset_ms": 750}, {"video_timing_offset_ms": 800},
           {"mp4_timing_offset_mpv_ms": 40}, {"mp4_timing_offset_mpv_ms": 40, "mpv_mp4_100_baseline_migrated": True},
           {"cdg_timing_offset_mpv_ms": 2950, "ffmpeg_cdg_timing_migrated": True, "ffmpeg_cdg_750_baseline_migrated": True, "cdg_timing_engine_split_migrated": True},
           {"end_silence_trim_threshold_sec": 5}, {"end_silence_trim_threshold_sec": 4.99}, {"end_silence_trim_threshold_sec": None}, {"end_silence_trim_threshold_sec": "5"},
           {"shell_commands": "apply"}, {"shell_commands": "OFF"}, {"performance_debug_enabled": False}, {"stall_stack_capture": True}]
    allm = {m: True for m in MARKERS}
    # exactly ONE migration runs on an otherwise migrated profile: the `changed` flag must come from that block alone
    for m in MARKERS:
        out.append(dict(allm, **{m: False}))
        out.append(dict(allm, **{m: 0, "cdg_timing_offset_ms": 150, "cdg_timing_offset_mpv_ms": 10, "mp4_timing_offset_mpv_ms": 10, "end_silence_trim_threshold_sec": 6.0}))
    for key in ("performance_mode", "safe_mode", "mp4_max_height", "karafun_transparent_handoff", "karafun_windowed_reveal", "karafun_transparent_rehearsal_reset"):
        out.append(dict(allm, **{key: 1}))
    out.append(dict(allm))
    out.append(dict(allm, cdg_timing_offset_ms=77, transport_shadow=False))
    return out


def same(a, b):
    return json.dumps(a, ensure_ascii=True) == json.dumps(b, ensure_ascii=True)


def has_big_int(case):
    return any(isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 2 ** 63 for v in case.values())


def has_decline_value(case):
    return any(v in DECLINE_MARKS and type(v) in (type(x) for x in DECLINE_MARKS) for v in case.values() if not isinstance(v, (list, dict)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3000)
    ap.add_argument("--binary", default=os.path.expanduser("~/.cache/singws-rust-target-settingsmig/release/singws-settingsmig"))
    args = ap.parse_args(argv)
    home = os.environ.get("SINGWS_HOME", "")
    if not home or Path(home).expanduser().resolve() == (Path.home() / "SingWS").resolve():
        import tempfile
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-settingsmig-")
    import songend_scenarios as ss
    main_mod = ss.load_app()
    oracle = real_migration(main_mod)
    cases = scripted() + [random_case(random.Random(seed)) for seed in range(args.seeds)]
    diffs, declined, bad_decline = [], 0, 0
    huge = [i for i, c in enumerate(cases) if has_big_int(c)]
    plain = [i for i in range(len(cases)) if i not in set(huge)]
    answers = {i: o for i, o in zip(plain, rust_batch(args.binary, [cases[i] for i in plain]))}
    for i in huge:
        answers[i] = rust(args.binary, cases[i])
    for i, case in enumerate(cases):
        py_settings, py_changed = oracle(case)
        out = answers[i]
        if "decline" in out:
            declined += 1
            if not has_decline_value(case):
                bad_decline += 1
                diffs.append((i, case, f"unexpected decline: {out['decline']}", None))
            continue
        if not (same(py_settings, out["settings"]) and py_changed == out["changed"]):
            diffs.append((i, case, py_settings, out))
    print(f"cases={len(cases)} declined={declined} (all values Python handles but Rust deliberately refuses)")
    for i, case, py, rs in diffs[:6]:
        print(f" - case {i}: {json.dumps(case)[:300]}\n   python: {json.dumps(py)[:400]}\n   rust:   {json.dumps(rs)[:400]}")
    print("IDENTICAL" if not diffs else f"{len(diffs)} DIFFERENCE(S)")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
