#!/usr/bin/env python3
"""Parity of the Rust log redactor / last-show package (rust/crates/singws-logpack) against the app's REAL Python.

Oracle = the app module itself (loaded like the other parity tools): `_sanitize_log_text`, `_recent_log_files`, `_last_show_slice`,
`_build_last_show_package` (its ZIP is opened and the log entry and extra files compared). Compared:
  1. redaction: handwritten secrets of every kind the Python patterns handle (key=value, headers, URLs, JSON-ish, emails, long lines,
     unicode) + a fuzz corpus + EVERY Unicode code point as a delimiter / as the next character (so `\\s` and `\\d` are checked everywhere)
     + every code point `(?i)` treats as a keyword letter;
  2. file selection (`_recent_log_files` + the mtime ordering) for odd names, suffixes, hidden files, directories and days windows;
  3. the whole package (text, window, files used, errors, extra files) for synthetic shows: midnight crossings, short relaunch runs,
     several files, CRLF / lone CR / odd line separators, invalid UTF-8, files without a final newline, unicode-digit stamps;
  4. read-only COPIES of the tails of a few real logs from ~/SingWS/logs (copied into a scratch dir; differences are reported by length,
     line number and hash only, never by content).
Needs the CLI:  cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-logpack cargo build --release -p singws-logpack
  ~/Documents/SingWS/.venv/bin/python tools/logpack_parity.py [--cases N] [--seed S] [--no-real]       exit 1 on any difference
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
_HOME = tempfile.mkdtemp(prefix="logpack_parity_home_")
os.environ["SINGWS_HOME"] = _HOME
sys.path.insert(0, str(ROOT))

CLI = os.environ.get("SINGWS_LOGPACK_CLI") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-logpack")), "release", "singws-logpack")
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 30:
        print("DIFF:", msg[:600])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def load_app():
    spec = importlib.util.spec_from_file_location("singws_main_logpack_parity", str(ROOT / "0.2.18.1.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rs_redact(text: str) -> str:
    p = subprocess.run([CLI, "redact"], input=text.encode("utf-8", "surrogatepass"), capture_output=True, timeout=120)
    return p.stdout.decode("utf-8", "replace")


def rs_dir(directory, days, now, extras=()):
    args = [CLI, "build-dir", str(directory), "--days", str(days), "--now", repr(float(now))]
    for e in extras:
        args += ["--extra", str(e)]
    p = subprocess.run(args, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"cli exit {p.returncode}")
    return json.loads(p.stdout)


def digest(text):
    return f"len={len(text)} sha={hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:12]}"


def first_diff(a, b):
    n = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
    return n, a[max(0, n - 30):n + 30], b[max(0, n - 30):n + 30]


# ----------------------------------------------------------------------------------------------------------------- redaction
HAND = [
    "", "plain line", "api_key=abc123", "API-KEY: abc123", "apikey = 'abc'", "x-api-key: SECRET", "X-API-Key:abc,def",
    "Authorization: Bearer abc.def.ghi", "authorization:bearer\tTOKEN", "Authorization:Bearer", "Authorization: Bearer ",
    "token=abc&other=1", "https://wskar.com/api?token=abc123&user=x", "https://x/?key=abc&b=2", "GET /a?api_key=zzz HTTP/1.1",
    "password: hunter2", "PASSWD=hunter2", "secret=\"quoted\"", "pin=1234", "shopping=cart", "pinning: x", "tokens=abc", "mytoken: abc",
    "{\"api_key\": \"abc\", \"user\": \"x\"}", "{'password':'abc'}", "user dan@wildstylekaraoke.com sent email", "x" * 20000 + " token=abc",
    "token=" + "a" * 50000, "token:", "token=", "token = ", "token=,", "token=&", "token==abc", "token=\"", "key=", "key=a", "monkey=banana",
    "Token=abc key=def password=ghi", "token=abc\nkey=def\r\nsecret: ghi", "api_key =　abc", "api key=abc", "api__key=abc",
    "TOKEN=éè中文", "Key=abc", "toKen=abc", "ſecret=abc", "passſ=abc", "İd=1 pİn=1234", "pın=1234",
    "emoji \U0001f3a4 token=abc \U0001f3a4", "\x00token=abc\x00", "key=a\x1cb", "key=a\x85b", "key=a b", "token=a​b",
    "Authorization: Bearer abc Authorization: Bearer def", "token=token=abc", "api_key=api_key=abc", "secret:secret:secret",
    "Authorization: Bearer x", "Authorization:\x1cBearer\x1cx", "authorization: bearer\n x",
]
KEYWORDS = ["api_key", "api-key", "apikey", "x-api-key", "token", "password", "passwd", "secret", "pin", "key", "Authorization: Bearer"]
VALUES = ["abc", "a.b-c_d", "x" * 40, "p@ss w0rd", "é中", "a,b", "a'b", "a\"b", "a&b", "a\tb", "", "'", "\"q\"", "=", ":", "***"]
SEPS = ["=", ":", " = ", ": ", "=\"", ": \"", "\": \"", "' = '", "\"='", "\t=\t", "==", " ", "", "= ", " :"]


def rnd_case(r, s):
    return "".join(c.upper() if r.random() < .5 else c for c in s)


def fuzz_text(r, n=12):
    parts = []
    for _ in range(n):
        k = r.random()
        if k < .6:
            parts.append(rnd_case(r, r.choice(KEYWORDS)) + r.choice(SEPS) + r.choice(VALUES))
        elif k < .8:
            parts.append(r.choice(["?", "&", " ", "\n", "\r\n", ", ", "\" ", "' "]) + rnd_case(r, r.choice(["token=", "key=", "Authorization:", "Bearer "])) + r.choice(VALUES))
        else:
            parts.append("".join(chr(r.choice([r.randrange(32, 127), r.randrange(0xa0, 0x3000), r.randrange(0x1f300, 0x1f600)])) for _ in range(r.randrange(1, 12))))
        parts.append(r.choice([" ", "\n", "", "  ", "\t"]))
    return "".join(parts)


def check_redaction(app, cases, seed):
    r = random.Random(seed)
    texts = list(HAND) + [fuzz_text(r) for _ in range(cases)]
    # one code point at a time as: a delimiter before/inside/after the separators, and as a trailing value character.
    cps = [c for c in range(0x110000) if not 0xd800 <= c <= 0xdfff]
    chunk = 4000
    for lo in range(0, len(cps), chunk):
        rows = []
        for cp in cps[lo:lo + chunk]:
            c = chr(cp)
            rows.append(f"token={c}x token{c}=y api_key:{c}z secret {c}: w pin={c} Authorization:{c}Bearer{c}v{c}q key=a{c}b")
        texts.append("\n".join(rows))
    # every code point (?i) lets stand in for a keyword letter
    letters = "apikeyxtonwrdscum"
    pat = re.compile("(?i)[" + letters + "]")
    stand_ins = [c for c in range(128, 0x110000) if pat.fullmatch(chr(c))]
    for cp in stand_ins:
        for kw in ("api_key", "x-api-key", "token", "password", "passwd", "secret", "pin", "key", "Authorization: Bearer"):
            for i, ch in enumerate(kw):
                if ch.lower() in letters and pat.fullmatch(chr(cp)) and re.fullmatch("(?i)" + re.escape(ch), chr(cp)):
                    texts.append(kw[:i] + chr(cp) + kw[i + 1:] + "=value1 and more")
    tally("redaction texts", len(texts))
    tally("(?i) stand-in code points", len(stand_ins))
    for t in texts:
        py = app._sanitize_log_text(t)
        rs = rs_redact(t)
        if py != rs:
            n, a, b = first_diff(py, rs)
            fail(f"redact @{n}: py={a!r} rs={b!r} input_head={t[:80]!r}")
    # type contract: falsy input
    if app._sanitize_log_text(None) != "" or rs_redact("") != "":
        fail("redact falsy input")


# ------------------------------------------------------------------------------------------------------------- file selection
def stamp(h, m, s=0, digits="ascii"):
    t = f"{h % 24:02d}:{m:02d}:{s:02d}"
    if digits == "arabic":
        t = t.translate({ord(str(d)): ord("٠") + d for d in range(10)})
    return f"[{t}]"


def run_lines(r, start_min, minutes, label, *, launch=True, digits="ascii", step=1, odd=True):
    out = []
    if launch:
        out.append(f"{stamp(start_min // 60, start_min % 60, 0, digits)} [INFO] [LAUNCH] " + '{"build_id":"1.0.2.4","executable":"/x/SingWS"}\n')
    for i in range(1, minutes + 1, step):
        t = start_min + i
        line = f"{stamp(t // 60, t % 60, r.randrange(60), digits)} [INFO] {label} minute {i} " + r.choice(["", "token=abc123", "api_key: K-9", "pin=4321", "Authorization: Bearer abc"]) + "\n"
        out.append(line)
        if r.random() < .08:
            out.append("  continuation line without a stamp\n")
        if odd and r.random() < .02:
            out.append(r.choice(["\x0b", "\x1c", " ", "\x85", "\x0c"]) + "after odd separator\n")
    return out


SEP_VARIANTS = ["\n", "\r\n", "\r"]


def encode(r, lines, newline):
    text = "".join(lines)
    if newline != "\n":
        text = text.replace("\n", newline)
    data = text.encode("utf-8")
    if r.random() < .15 and data:
        data = data.rstrip(b"\r\n")                                  # no final newline
    if r.random() < .1 and len(data) > 10:
        i = r.randrange(len(data))
        data = data[:i] + r.choice([b"\xff", b"\xc3", b"\xe2\x82", b"\xf0\x9f"]) + data[i:]     # invalid UTF-8
    return data


NAMES_OK = ["singws_2026-10-29.log", "singws_2026-10-29.log.2026-10-28", "notes.txt", "x.LOG", "Y.Txt", ".hidden.log", "..log"]
NAMES_BAD = ["a.zip", ".log", "readme", "singws_2026-10-29.log.bak", "singws_2026-10-29.log.2026-10-2", "singws_2026-1-29.log.2026-10-28", "log", "a.log.",
             "singws_٢٠٢٦-10-29.log.2026-10-28", "singws_2026-10-29.log.2026-10-28x", "xsingws_2026-10-29.log.2026-10-28", "crash.json"]


def make_logs_dir(r, base, now, *, shows=None):
    """Returns (dir, list of (name, age_hours)). Mtimes keep clear of the days cutoff unless boundary=True elsewhere."""
    d = Path(base) / "logs"
    d.mkdir(parents=True)
    plan = []
    kinds = r.choice(["one", "two_rot", "show_then_short", "three", "short_only", "no_stamps", "empty"])
    files = []
    if kinds == "one":
        files.append(("singws_2026-10-29.log", 2.0, run_lines(r, 21 * 60, r.choice([20, 45, 200]), "SHOW")))
    elif kinds == "two_rot":
        files.append(("singws_2026-10-29.log.2026-10-29", 5.0, run_lines(r, 21 * 60, 170, "SHOW")))
        files.append(("singws_2026-10-30.log", 1.0, run_lines(r, 0, 90, "CONT", launch=False)))
    elif kinds == "show_then_short":
        files.append(("singws_2026-10-29.log.2026-10-29", 7.0, run_lines(r, 20 * 60 + 30, 200, "SHOW")))
        files.append(("singws_2026-10-30.log", 1.0, run_lines(r, 0, 120, "CONT", launch=False) + run_lines(r, 3 * 60 + 12, r.choice([1, 11, 25, 35]), "REOPEN")))
    elif kinds == "three":
        files.append(("singws_2026-10-28.log", 30.0, run_lines(r, 21 * 60, 100, "OLD", digits=r.choice(["ascii", "arabic"]))))
        files.append(("singws_2026-10-29.log", 20.0, run_lines(r, 21 * 60, 250, "SHOW")))
        files.append(("notes.txt", 10.0, run_lines(r, 22 * 60, 5, "NOTES")))
    elif kinds == "short_only":
        files.append(("singws_2026-10-29.log", 1.0, run_lines(r, 23 * 60, 4, "A") + run_lines(r, 23 * 60 + 10, 6, "B")))
    elif kinds == "no_stamps":
        files.append(("singws_2026-10-29.log", 1.0, ["no stamp here\n", "none here token=abc\n"]))
    else:
        files.append(("singws_2026-10-29.log", 1.0, []))
    for n in r.sample(NAMES_OK + NAMES_BAD, r.randrange(0, 6)):
        files.append((n, r.choice([1.0, 3.0, 50.0]), run_lines(r, 12 * 60, r.randrange(1, 30), "EXTRA", launch=r.random() < .5)))
    seen = set()
    for name, age, lines in files:
        if name in seen:
            continue
        seen.add(name)
        p = d / name
        p.write_bytes(encode(r, lines, r.choice(SEP_VARIANTS)))
        os.utime(p, (now - age * 3600, now - age * 3600))
    (d / "a_directory.log").mkdir()
    return d


def listing_key(app_paths):
    return [p.name for p in app_paths]


def check_package(app, cases, seed):
    r = random.Random(seed + 1)
    for i in range(cases):
        with tempfile.TemporaryDirectory(prefix="logpack_case_") as base:
            now = time.time()
            d = make_logs_dir(r, base, now)
            crash = None
            if r.random() < .4:
                crash = Path(base) / "crash_report.log"
                crash.write_bytes(("Traceback x\npassword: hunter2\nAuthorization: Bearer abc.def\n" + "y" * r.randrange(0, 50)).encode() + r.choice([b"", b"\r\n", b"\xff"]))
            days = r.choice([1, 3, 7, 30])
            app.LOGS_DIR = d
            tally("package cases")
            try:
                py = app._build_last_show_package(days, crash_log=crash)
            except Exception as exc:
                fail(f"case {i}: python raised {exc!r}")
                continue
            rs = rs_dir(d, days, time.time(), [crash] if crash else [])
            py_text, py_extras = None, []
            if py["package"]:
                with zipfile.ZipFile(py["package"]) as zf:
                    for info in zf.namelist():
                        body = zf.read(info).decode("utf-8")
                        if info.startswith("singws_last_show_"):
                            py_text = body
                        else:
                            py_extras.append(body)
                tally("package with log entry", py_text is not None)
            if py["error"]:
                tally("python error cases")
            if (py["error"] or "") != rs["error"]:
                fail(f"case {i}: error py={py['error']!r} rs={rs['error']!r}")
                continue
            if py_text != rs["text"]:
                a, b = py_text or "", rs["text"] or ""
                n, x, y = first_diff(a, b)
                fail(f"case {i}: text differs @{n}: py={x!r} rs={y!r} (py {digest(a)}, rs {digest(b)})")
            if py["window"] != rs["window"]:
                fail(f"case {i}: window py={py['window']!r} rs={rs['window']!r}")
            used_py = [p.name for p in py["files"] if not crash or p != crash]
            if used_py != rs["used"]:
                fail(f"case {i}: files py={used_py} rs={rs['used']}")
            if py_extras != rs["extras"]:
                fail(f"case {i}: extras differ")


def check_extras_only(app):
    """No log file in the window but a crash log attached: no error, no log entry, the crash log still redacted (and the plain empty case errors)."""
    with tempfile.TemporaryDirectory(prefix="logpack_extra_") as base:
        d = Path(base) / "logs"
        d.mkdir()
        crash = Path(base) / "crash.log"
        crash.write_text("Traceback\npin: 1234\n")
        app.LOGS_DIR = d
        for c in (crash, None):
            py = app._build_last_show_package(7, crash_log=c)
            rs = rs_dir(d, 7, time.time(), [crash] if c else [])
            tally("extras-only cases")
            if (py["error"] or "") != rs["error"] or rs["text"] is not None or py["window"] != rs["window"]:
                fail(f"extras-only: py={py['error']!r} rs={rs['error']!r} text={rs['text']!r}")
            if c and py["package"]:
                with zipfile.ZipFile(py["package"]) as zf:
                    if [zf.read(n).decode() for n in zf.namelist()] != rs["extras"]:
                        fail("extras-only: extra file text differs")


def check_selection(app, cases, seed):
    """`_recent_log_files` with an explicit `now`, including mtimes exactly on and around the cutoff."""
    r = random.Random(seed + 2)
    for i in range(cases):
        with tempfile.TemporaryDirectory(prefix="logpack_sel_") as base:
            d = Path(base)
            now = 1_792_000_000.0 + r.random()
            days = r.choice([-5, 0, 1, 2, 3, 7, 29, 30, 31, 99])
            clamped = max(1, min(30, days))
            cutoff = now - clamped * 86400
            names = r.sample(NAMES_OK + NAMES_BAD + ["singws_2026-10-29.log.2026-10-28", "b.log", "c.txt", "d.TXT"], r.randrange(1, 12))
            for n in names:
                p = d / n
                if p.exists():
                    continue
                p.write_text(f"[10:00:00] [INFO] [LAUNCH] {{\n[10:40:00] [INFO] {n}\n")
                m = r.choice([cutoff, cutoff - 0.001, cutoff + 0.001, cutoff - 1, cutoff + 1, now - r.random() * 40 * 86400, now])
                os.utime(p, (m, m))
            app.LOGS_DIR = d
            py = app._recent_log_files(days, now=now)
            py_sorted = sorted(py, key=lambda p: (p.stat().st_mtime, p.name))
            rs = rs_dir(d, days, now)
            tally("selection cases")
            if [p.name for p in py_sorted] != rs["used"]:
                fail(f"selection {i}: days={days} py={[p.name for p in py_sorted]} rs={rs['used']}")


def check_slice(app, cases, seed):
    """`_last_show_slice` direct, on line lists where the file layer cannot hide a difference."""
    r = random.Random(seed + 3)
    for i in range(cases):
        lines = []
        for _ in range(r.randrange(0, 5)):
            digits = r.choice(["ascii", "ascii", "arabic"])
            lines += run_lines(r, r.randrange(0, 1440), r.choice([0, 1, 5, 29, 30, 31, 90, 400]), "RUN", launch=r.random() < .85, digits=digits, odd=False)
            if r.random() < .2:
                lines.append(r.choice(["[99:99:99] [INFO] bad stamp\n", "[1 :00:00] [INFO] [LAUNCH] {x}\n", "[10:00:00] [] [LAUNCH] {x}\n", "[10:00:00] [INFO] [LAUNCH] nobrace\n"]))
        py = app._last_show_slice(lines)
        out = rs_dir_slice(lines)
        tally("slice cases")
        if tuple(py[:2]) != tuple(out[:2]) or abs(py[2] - out[2]) > 1e-9:
            fail(f"slice {i}: py={py} rs={out}")


def rs_dir_slice(lines):
    """The CLI's own (first, end, seconds) for one file holding these lines."""
    payload = {"days": 30, "now": 2e9, "files": [{"name": "singws_x.log", "mtime": 2e9 - 10, "text": "".join(lines)}], "extras": []}
    v = json.loads(subprocess.run([CLI, "build"], input=json.dumps(payload).encode(), capture_output=True, timeout=60).stdout)
    return tuple(v["slice"]) if v["text"] is not None else (0, 0, 0.0)


def check_real(app, seed):
    src = Path.home() / "SingWS" / "logs"
    if not src.is_dir():
        print("note: no ~/SingWS/logs, real-log check skipped")
        return
    files = sorted((p for p in src.glob("singws_*.log*") if p.is_file()), key=lambda p: p.stat().st_mtime)[-3:]
    if not files:
        return
    with tempfile.TemporaryDirectory(prefix="logpack_real_") as base:
        d = Path(base) / "logs"
        d.mkdir()
        now = time.time()
        for k, p in enumerate(files):
            data = p.read_bytes()                                    # read only; the copy is what the tools see
            tail = b"\n".join(data.splitlines()[-4000:]) + b"\n"
            q = d / p.name
            q.write_bytes(tail)
            t = now - (len(files) - k) * 3600
            os.utime(q, (t, t))
            tally("real log copies")
            tally("real log bytes", len(tail))
            text = tail.decode("utf-8", "replace")
            py, rs = app._sanitize_log_text(text), rs_redact(text)
            if py != rs:
                n, _, _ = first_diff(py, rs)
                fail(f"real redaction {p.name}: differs at char {n} ({digest(py)} vs {digest(rs)}) [content not shown]")
        app.LOGS_DIR = d
        built = app._build_last_show_package(7)
        rs = rs_dir(d, 7, time.time())
        py_text = None
        if built["package"]:
            with zipfile.ZipFile(built["package"]) as zf:
                py_text = next((zf.read(n).decode("utf-8") for n in zf.namelist() if n.startswith("singws_last_show_")), None)
        if py_text != rs["text"] or built["window"] != rs["window"]:
            a, b = py_text or "", rs["text"] or ""
            n, _, _ = first_diff(a, b)
            fail(f"real package differs at char {n} ({digest(a)} vs {digest(b)}), windows {built['window']!r} / {rs['window']!r} [content not shown]")
        else:
            tally("real packages identical")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--no-real", action="store_true")
    a = ap.parse_args()
    if not os.access(CLI, os.X_OK):
        print(f"CLI not built: {CLI}\n  cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-logpack cargo build --release -p singws-logpack")
        return 2
    app = load_app()
    check_redaction(app, a.cases * 4, a.seed)
    check_extras_only(app)
    check_selection(app, a.cases, a.seed)
    check_slice(app, a.cases, a.seed)
    check_package(app, a.cases, a.seed)
    if not a.no_real:
        check_real(app, a.seed)
    print("counts:", json.dumps(counts))
    if failures:
        print(f"{len(failures)} DIFFERENCES")
        return 1
    print("IDENTICAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
