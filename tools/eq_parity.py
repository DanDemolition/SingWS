#!/usr/bin/env python3
"""Stage E panel 6 parity: the Rust equaliser view against the app's REAL ``KaraokeApp._ensure_eq_engines`` (which builds ``singws_eq.GraphicEQ`` from the saved ``eq_bgm`` /
``eq_karaoke`` lists and switches on any equaliser whose gains are not flat).

Oracle = the app module, run on a bare app whose ``settings`` is the fuzzed settings object: both ``GraphicEQ`` instances' ``gains_db()``, ``enabled()`` and ``is_flat()`` and the
``*_enabled`` setting the app writes back. Fuzz: lists of 0-14 entries (ints, floats around the 0.05 dB flatness edge, big and negative values, true/false, null, text, nested
lists/objects), non-list values, missing keys, enabled flags of every truthiness. Where Python gives up (any unreadable entry in either list makes BOTH unavailable) Rust must
return an error. Declined, not compared: text entries (Python's float() parses some text) and gains beyond 1000 dB (the app's filter maths overflows near 12,000 dB).

Run with the app's venv Python:  QT_QPA_PLATFORM=offscreen ~/.venvs/singws/.venv-build-arm64/bin/python tools/eq_parity.py [--cases N] [--seed S]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a temp SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_EQ_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-eq-cli")
failures: list[str] = []
counts: dict[str, int] = {}
QAPP = None


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:900])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(settings_text):
    out = subprocess.run([CLI], input=json.dumps({"settings_text": settings_text}), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


def python_view(settings):
    global QAPP
    from PyQt6.QtWidgets import QApplication

    if QApplication.instance() is None:
        QAPP = QApplication([])
    m = hp.app()
    ka = m.KaraokeApp
    a = ka.__new__(ka)
    a.settings = json.loads(json.dumps(settings))
    a.karaoke_eq = None
    a.bgm_eq = None
    if not ka._ensure_eq_engines(a):
        return None
    def one(eq, key):
        return {"gains": eq.gains_db(), "enabled": bool(eq.enabled()), "flat": bool(eq.is_flat()), "setting_after": bool(a.settings.get(key, False))}
    return {"bgm": one(a.bgm_eq, "eq_bgm_enabled"), "karaoke": one(a.karaoke_eq, "eq_karaoke_enabled")}


def rand_entry(rng):
    r = rng.random()
    if r < 0.30:
        return rng.randint(-12, 12)
    if r < 0.50:
        return round(rng.uniform(-12, 12), rng.randint(0, 4))
    if r < 0.62:
        return rng.choice([0.0, 0.04, 0.0499, 0.05, 0.0501, -0.049, -0.05, -0.0501, 1e-9, -1e-9, 0])
    if r < 0.70:
        return rng.choice([100, -100, 999.5, 1000, -1000, 5000, -5000, 10**30, 1e30])
    if r < 0.76:
        return rng.choice([True, False])
    if r < 0.82:
        return None
    if r < 0.88:
        return rng.choice(["3.5", "abc", "", " 2 ", "1e1", "1_0"])
    return rng.choice([[1], {"a": 1}, [], {}])


def clean_entry(rng):
    r = rng.random()
    if r < 0.45:
        return rng.randint(-12, 12)
    if r < 0.8:
        return round(rng.uniform(-12, 12), rng.randint(0, 4))
    if r < 0.93:
        return rng.choice([0.0, 0.04, 0.0499, 0.05, 0.0501, -0.049, -0.05, -0.0501, 1e-9, -1e-9, 0, 100, -100, 999.5])
    return rng.choice([True, False])


def rand_list(rng):
    r = rng.random()
    if r < 0.06:
        return rng.choice([None, 5, "flat", {"a": 1}, True, 1.5, ""])
    n = rng.choice([0, 1, 3, 9, 10, 10, 10, 10, 11, 14])
    if rng.random() < 0.75:   # most saved profiles hold plain numbers: that is where the rules (length, flatness edge, auto-enable) matter
        return [clean_entry(rng) for _ in range(n)]
    return [rand_entry(rng) for _ in range(n)]


def rand_flag(rng):
    return rng.choice([True, False, 1, 0, 0.0, 0.5, "", "no", "yes", None, [], [0], {}, {"a": 1}])


def run_case(rng):
    s = {}
    for k in ("eq_bgm", "eq_karaoke"):
        if rng.random() < 0.9:
            s[k] = rand_list(rng)
    for k in ("eq_bgm_enabled", "eq_karaoke_enabled"):
        if rng.random() < 0.8:
            s[k] = rand_flag(rng)
    text = json.dumps(s)
    contains_text = any(isinstance(v, str) for k in ("eq_bgm", "eq_karaoke") if isinstance(s.get(k), list) for v in s[k][:10])
    huge = any(isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) > 1000 for k in ("eq_bgm", "eq_karaoke") if isinstance(s.get(k), list) for v in s[k][:10])
    try:
        py = python_view(s)
        py_err = None
    except Exception as exc:  # noqa: BLE001
        py, py_err = None, repr(exc)
    r = rust(text)
    if "error" in r:
        if contains_text or huge:
            tally("declined")
            return
        if py is None and py_err is None:
            tally("both_unavailable")
            return
        fail(f"Rust errored ({r['error']}) but Python built the equalisers: {text[:300]} -> {py}")
        return
    if py is None:
        fail(f"Python gave up ({py_err}) but Rust showed equalisers: {text[:300]}")
        return
    for name in ("bgm", "karaoke"):
        want, got = py[name], r[name]
        if want["gains"] != got["gains"] or want["enabled"] != got["enabled"] or want["flat"] != got["flat"] or want["setting_after"] != got["enabled"] or \
                (got["auto_enabled"] != (want["setting_after"] and not bool(s.get(f"eq_{name}_enabled", False)))):
            fail(f"{name}: settings={text[:300]}\n python={want}\n rust  ={got}")
            return
    tally("same")
    tally("on" if r["bgm"]["enabled"] or r["karaoke"]["enabled"] else "all_off")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20261026)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        run_case(rng)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
