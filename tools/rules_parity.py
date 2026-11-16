#!/usr/bin/env python3
"""Parity: three small rule sets ported to the Rust `singws-misc` (nothing in the app calls the Rust yet), each compared with the REAL Python.

  names     `_capitalize_singer_name` (read from 0.2.18.1.py by AST, run unchanged)
  voice     `voice_audio_fallback.py`: the cache file name, the converter's command line, when a copy is used, when a conversion counts as done, which copies are pruned
            (the real `convert` and `_prune` run on a scratch folder with a fake converter)
  announce  `announcement_box.AnnouncementBox` (the real class with a fake engine and pad): scripted runs of begin / chunk / end / play / discard / tick; every answer and every
            effect (load, play, close, music slide) must match, including the WAV checks of Python's `wave` module on mangled files

Spellings the Rust side does not model (a number sent as text, WAVE_FORMAT_EXTENSIBLE, ...) are DECLINED and counted, never guessed.

  CARGO_TARGET_DIR=... python3 tools/rules_parity.py [--seeds N]
"""
import argparse
import ast
import base64
import io
import json
import os
import random
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CLI = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-karafun")) + "/release/singws-misc"


def rust(batch):
    out = subprocess.run([CLI], input=json.dumps({"batch": batch}), capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise SystemExit("rust failed: " + out.stderr[-500:])
    return json.loads(out.stdout)


# ---------------------------------------------------------------- names

def load_name_rule():
    tree = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_capitalize_singer_name":
                    sub.decorator_list = []
                    exec(compile(ast.Module([sub], []), "app", "exec"), ns)
    return ns["_capitalize_singer_name"]


NAME_PARTS = ["dan", "DAN", "McKay", "mary-jane", "O'NEIL", "ñandú", "ÉCOLE", "straße", "İstanbul", "ǆ", "ǅ", "ΣΑΣ", "ὈΔΥΣΣΕΎΣ", "ﬁsh", "ŉ", "a", "", "  ", "\t", "\n", " ", " ", " ",
              "\x1c", "\x1f", "\x85", "​", "　", "😀x", "á", "İ", "ß", "ǰ", "Ⅷ", "ⓐⓑ", "x_y", "9lives", "-", "...", "A B", "  lead", "trail  "]


def names_cases(rng, n):
    cases = []
    for _ in range(n):
        s = "".join(rng.choice(NAME_PARTS) for _ in range(rng.randint(0, 6)))
        cases.append(s)
    return cases


def run_names(rng, n):
    fn = load_name_rule()
    cases = names_cases(rng, n)
    answers = rust([{"op": "singer_name", "name": c} for c in cases])
    bad = [(c, fn(c), a["v"]) for c, a in zip(cases, answers) if fn(c) != a["v"]]
    return len(cases), 0, bad[:5]


# ---------------------------------------------------------------- voice

def run_voice(rng, n):
    import voice_audio_fallback as vf
    bad, total = [], 0
    for _ in range(n):
        with tempfile.TemporaryDirectory() as home:
            os.environ["SINGWS_HOME"] = home
            src = Path(home) / ("src %d é.mp4" % rng.randrange(10**6))
            src.write_bytes(os.urandom(rng.randint(0, 300)))
            mtime = rng.choice([1700000000, 1700000000.9, 1.5e9, 0, 1234567890.123])
            os.utime(src, (mtime, mtime))
            st = src.stat()
            vf.available = lambda: True
            calls, returned = [], {}
            code, out_size, make = rng.choice([(0, 500, True), (0, 44, True), (0, 45, True), (0, 0, True), (1, 500, True), (0, 500, False), (2, 10, False)]), None, None
            rc, size, make_file = code

            def runner(args, capture_output=True, timeout=None):
                calls.append(args)
                if make_file:
                    Path(args[-1]).write_bytes(b"x" * size)
                return type("R", (), {"returncode": rc})()

            folder = vf.cache_dir()
            # sometimes a copy already exists
            key = None
            pre = rng.choice([None, 0, 44, 45, 3000])
            expected_key = rust([{"op": "voice_key", "path": str(src.resolve()), "size": st.st_size, "mtime": int(st.st_mtime)}])[0]["v"]
            if pre is not None:
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{expected_key}.wav").write_bytes(b"y" * pre)
            res = vf.convert(str(src), log=lambda _m: None, runner=runner)
            total += 1
            tmp_name = calls[0][-1] if calls else ""
            facts = rust([
                {"op": "voice_usable", "exists": pre is not None, "size": pre or 0},
                {"op": "voice_ok", "code": rc, "exists": make_file, "size": size if make_file else 0},
                {"op": "voice_args", "src": str(src), "tmp": tmp_name},
            ])
            usable = facts[0]["v"]
            if usable:
                want_res, want_calls = str(folder / f"{expected_key}.wav"), 0
            else:
                ok = facts[1]["v"]
                want_calls = 1
                want_res = str(folder / f"{expected_key}.wav") if ok else None
            problems = []
            if res != want_res:
                problems.append(("result", res, want_res))
            if len(calls) != want_calls:
                problems.append(("converter calls", len(calls), want_calls))
            if calls and facts[2]["v"] != calls[0]:
                problems.append(("args", calls[0], facts[2]["v"]))
            if calls:
                tmp = Path(calls[0][-1])
                if not (tmp.name.startswith(f".{expected_key}.") and tmp.name.endswith(".tmp.wav")):
                    problems.append(("tmp name", tmp.name))
            if problems:
                bad.append(problems)
    # pruning
    for _ in range(n):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home)
            now = time.time()
            wavs, tmps = [], []
            for i in range(rng.randint(0, 20)):
                name = rng.choice(["%020x.wav" % rng.randrange(1 << 60), ".hidden%d.wav" % i, "w%d.wav" % i])
                if (folder / name).exists():
                    continue
                m = now - rng.choice([0, 5, 60, 3600, 86400, 50.5, 7, 7, 7])
                (folder / name).write_bytes(b"z")
                os.utime(folder / name, (m, m))
                wavs.append((name, (folder / name).stat().st_mtime))
            for i in range(rng.randint(0, 4)):
                name = f".{rng.randrange(1 << 40):x}.{i}.tmp.wav"
                m = now - rng.choice([0, 100, 3599, 3601, 7200])
                (folder / name).write_bytes(b"t")
                os.utime(folder / name, (m, m))
                tmps.append((name, (folder / name).stat().st_mtime))
            # the listing the Rust side sees is the folder's: tmp files are also "*.wav" files (hidden), the rule drops them
            listing = [(f.name, f.stat().st_mtime) for f in folder.glob("*.wav")]
            tmp_listing = [(f.name, f.stat().st_mtime) for f in folder.glob(".*.tmp.wav")]
            before = {f.name for f in folder.iterdir()}
            real_now = time.time()
            vf._prune(folder)
            gone_py = before - {f.name for f in folder.iterdir()}
            plan = rust([{"op": "voice_prune", "wavs": listing, "tmps": tmp_listing, "now": real_now}])[0]["v"]
            total += 1
            if set(plan) != gone_py:
                bad.append(("prune", sorted(plan)[:4], sorted(gone_py)[:4]))
    return total, 0, bad[:5]


# ---------------------------------------------------------------- announce

def wav_bytes(rng):
    kind = rng.choice(["good", "good", "good", "stereo", "bits8", "bits12", "bits24", "hot", "rate_low", "rate_high", "long", "empty", "badtag", "ext", "truncated", "odd_pad",
                       "no_data", "data_first", "garbage", "tiny", "fmt_short", "big_size", "zero_ch"])
    rate = rng.choice([32000, 44100, 16000, 8000, 48000])
    ch, bits, tag = 1, 16, 1
    frames = rng.randint(50, 3000)
    peak = rng.choice([100, 3000, 20000, 26000, 32000, 32500])
    if kind == "stereo": ch = 2
    if kind == "bits8": bits = 8
    if kind == "bits12": bits = 12
    if kind == "bits24": bits = 24
    if kind == "hot": peak = 32767
    if kind == "rate_low": rate = 7999
    if kind == "rate_high": rate = 48001
    if kind == "long": frames = int(rate * rng.choice([29.9, 30.0, 30.1, 31]))
    if kind == "empty": frames = 0
    if kind == "badtag": tag = 3
    if kind == "ext": tag = 0xFFFE
    if kind == "zero_ch": ch = 0
    width = (bits + 7) // 8
    framesize = max(1, ch * width)
    pcm = bytearray()
    for i in range(frames * ch):
        v = int(peak * ((i % 23) - 11) / 11) if width == 2 else rng.randrange(256)
        pcm += struct.pack("<h", max(-32768, min(32767, v))) if width == 2 else bytes(rng.randrange(256) for _ in range(width))
    fmt = struct.pack("<HHLLHH", tag, ch, rate, rate * framesize, framesize, bits)
    if kind == "fmt_short":
        fmt = fmt[:rng.choice([4, 10, 13, 15])]
    chunks = b""
    if kind == "odd_pad":
        chunks += b"LIST" + struct.pack("<L", 3) + b"abc" + b"\x00"
    fmt_chunk = b"fmt " + struct.pack("<L", len(fmt)) + fmt + (b"\x00" if len(fmt) % 2 else b"")
    size_field = len(pcm)
    if kind == "big_size":
        size_field = len(pcm) + rng.choice([2, 1000, 10**6])
    data_chunk = b"data" + struct.pack("<L", size_field) + bytes(pcm)
    if kind == "no_data":
        body = fmt_chunk
    elif kind == "data_first":
        body = data_chunk + fmt_chunk
    else:
        body = chunks + fmt_chunk + data_chunk
    out = b"RIFF" + struct.pack("<L", 4 + len(body)) + b"WAVE" + body
    if kind == "truncated":
        out = out[:rng.randint(0, len(out))]
    if kind == "garbage":
        out = bytes(rng.randrange(256) for _ in range(rng.randint(0, 80)))
    if kind == "tiny":
        out = out[:rng.choice([0, 3, 4, 7, 8, 11, 12, 15, 20])]
    return out


class FakeEngine:
    def __init__(self):
        self.master_volume, self._closed = 0.8, False

    def pad_play(self, *a):
        return True


class FakeChannel:
    env, playing = None, False

    def __init__(self, provider):
        pass

    def is_playing(self):
        return self.playing


def script(rng):
    """One scripted run: a list of events, each with the world it happens in."""
    events, t = [], 1000.0
    clip = wav_bytes(rng)
    cid = rng.choice(["a", "clip-1", "é" * 70, "x" * 64, "x" * 65, ""])
    size = rng.choice([40, 200, 1000, 24000])
    parts = [clip[i:i + size] for i in range(0, max(1, len(clip)), size)] or [b""]

    def env(master="keep"):
        return {"master": (rng.choice([0.8, 0.5, 0.0, 1.0, 0.4, 0.3]) if master == "keep" else master), "closed": rng.random() < 0.1, "volume": rng.choice([1.0, 0.7, 1.7, -0.5, 0.0]),
                "load_fails": rng.random() < 0.15}

    def cmd(action, args, e=None):
        nonlocal t
        t += rng.choice([0.1, 1, 30, 1, 1])
        events.append(["cmd", action, args, t, e or env()])

    for _ in range(rng.randint(1, 4)):
        rounds = rng.choice(["good", "good", "good", "weird"])
        n = rng.choice([len(parts), len(parts), len(parts) + 1, max(1, len(parts) - 1), 0, 401, 3])
        rate = rng.choice([32000, 8000, 48000, 7000, 99999, 32000.7, True, None, [1], "32000"])
        cmd("announce.begin", {"id": cid, "chunks": n if rounds == "weird" else len(parts), "rate": rate if rounds == "weird" else 32000})
        order = list(range(len(parts)))
        if rounds == "weird" and rng.random() < 0.4:
            rng.shuffle(order)
        for i in order:
            data = base64.b64encode(parts[i]).decode()
            r = rng.random()
            if r < 0.06: data = data[:-1]
            elif r < 0.1: data = data + "!"
            elif r < 0.12: data = "é" + data
            elif r < 0.14: data = data + "="
            elif r < 0.16: data = data.rstrip("=") if "=" in data else data
            idx = rng.choice([i, i, i, i + 1, -1, 1.0, 2.9, True, None, "1"]) if rounds == "weird" else i
            cmd("announce.chunk", {"id": rng.choice([cid, cid, cid, "other"]), "index": idx, "data": data})
            if rng.random() < 0.05:
                break
        cmd("announce.end", {"id": cid})
        for _ in range(rng.randint(0, 3)):
            what = rng.choice(["play", "play", "tick", "tick", "discard", "unknown", "big"])
            if what == "play":
                e = env(rng.choice(["keep", None]))
                cmd("announce.play", {"id": rng.choice([cid, cid, "nope", None, 5])}, e)
            elif what == "tick":
                t += rng.choice([1, 400, 1000])
                events.append(["tick", t, rng.random() < 0.5, env()])
            elif what == "discard":
                cmd("announce.discard", {})
            elif what == "unknown":
                cmd("announce.nope", {})
            else:
                t += 2000
                cmd("announce.play", {"id": cid})
    return events


def run_announce(rng, n):
    declined, bad, total = 0, [], 0
    scripts = [script(rng) for _ in range(n)]
    answers = rust([{"op": "announce", "events": s} for s in scripts])
    for s, a in zip(scripts, answers):
        if "declined" in a:
            declined += 1
            continue
        total += 1
        py = run_python_script_ordered(s)
        if py != a["v"]:
            for i, (p, r) in enumerate(zip(py, a["v"])):
                if p != r:
                    bad.append((i, s[i][:3] if s[i][0] == "cmd" else s[i][:3], p, r))
                    break
            else:
                bad.append(("length", len(py), len(a["v"])))
    return total, declined, bad[:5]


def run_python_script_ordered(events):
    """Like run_python_script but with ONE recorder for pad calls and music slides, so the order between them is compared too."""
    import announcement_box as ab
    import soundboard_channels
    log = []

    class Eng(FakeEngine):
        def slide_master_volume(self, level, ms):
            log.append(["slide", level, ms])
            self.master_volume = level

    class Chan(FakeChannel):
        def load(self, path, volume):
            from soundboard_channels import SoundboardError
            log.append(["load", len(open(path, "rb").read()), volume])
            if Chan.env["load_fails"]:
                raise SoundboardError("boom")

        def play(self, start):
            log.append(["play"])

        def close(self):
            log.append(["close"])

    eng = Eng()
    state, now = {"engine": eng, "vol": 1.0}, [0.0]
    original = soundboard_channels.RustSoundboardChannel
    soundboard_channels.RustSoundboardChannel = Chan
    try:
        with tempfile.TemporaryDirectory() as d:
            box = ab.AnnouncementBox(lambda: state["engine"], volume=lambda: state["vol"], clock=lambda: now[0], temp_dir=d)
            out = []
            for ev in events:
                kind = ev[0]
                e = ev[4] if kind == "cmd" else ev[3]
                now[0] = ev[3] if kind == "cmd" else ev[1]
                state["vol"] = e["volume"]
                state["engine"] = eng if e["master"] is not None else None
                if e["master"] is not None:
                    eng.master_volume = e["master"]
                eng._closed = e["closed"]
                Chan.env, Chan.playing = e, (ev[2] if kind == "tick" else False)
                del log[:]
                if kind == "cmd":
                    ok, msg = box.handle(ev[1], ev[2])
                else:
                    box.tick()
                    ok, msg = True, "tick"
                out.append([ok, msg, list(log)])
            assert os.listdir(d) == [], os.listdir(d)
            return out
    finally:
        soundboard_channels.RustSoundboardChannel = original


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    n = ap.parse_args().seeds
    rng = random.Random(20261116)
    failed = False
    for name, fn in (("names", run_names), ("voice", run_voice), ("announce", run_announce)):
        total, declined, bad = fn(rng, n)
        print(f"{name:9} compared {total}, declined {declined}, different {len(bad)}")
        for b in bad:
            print("   ", repr(b)[:600])
        failed |= bool(bad)
    print("DIFFERENT" if failed else "IDENTICAL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
