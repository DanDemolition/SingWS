#!/usr/bin/env python3
"""Parity of the Rust KaraFun web SERVICE machine (rust/crates/singws-karafun-svc, milestone W1) against the REAL Python `karafun_web.KaraFunWebService`.

The Python is the oracle: the real class runs with a fake player object, a fake set-up thread (so `start()` only resets and `_run()` is called inline), a scripted
`time.monotonic` and a scripted saved-login callable. Every scenario (set-up macros, status streams with signedIn variants and plans, error messages, stop, the
8 s settle boundaries, the 60/120-status rules, saved login found / missing / raising) is recorded as a trace of ordered actions (state set, log line, player
command, saved-login read, message forward) and the Rust crate must produce the same trace and the same final state.
No network, no Chrome, no Keychain (the credentials callable is a stand-in). Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv/bin/python tools/karafun_service_parity.py [--cases N] [--seed S]      exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if not os.environ.get("SINGWS_HOME") or Path(os.environ["SINGWS_HOME"]).resolve() == (Path.home() / "SingWS").resolve():
    os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="kf_service_parity_home_")
sys.path.insert(0, str(ROOT))
import karafun_web  # noqa: E402

CREDS = ("a@example.com", "not-a-real-password")


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


class FakeThread:
    def __init__(self, alive=False, target=None, name=None, daemon=None):
        self.alive = alive

    def start(self):
        pass

    def is_alive(self):
        return self.alive


class Err(Exception):
    pass


class Err2(KeyError):
    pass


def python_trace(sc: dict) -> dict:
    log: list = []
    clock = [0.0]

    class FakeWeb:
        def __init__(self, **kw):
            pass

        def start(self):
            if sc.get("start_fail"):
                raise RuntimeError("start failed")

        def sign_in(self):
            log.append(["sign_in"])

        def command(self, name, **kw):
            log.append([name])

        def login(self, email, password):
            assert (email, password) == CREDS
            log.append(["login"])

        def close(self):
            log.append(["close"])

    saved_by_event = {}
    cur = {"saved": None}

    def credentials():
        log.append(["read_saved_login"])
        s = cur["saved"] or {"result": "none"}
        if s["result"] == "found":
            return CREDS
        if s["result"] == "error":
            raise {"Err": Err, "Err2": Err2, "ValueError": ValueError}[s["kind"]]("secret-ish text must not be logged")
        return s.get("empty")  # None or () (both falsy)

    def find(progress):
        for d, t in sc.get("progress", []):
            progress(d, t)
        if sc.get("find_fail"):
            raise RuntimeError(sc["error"])
        return Path("/x/chrome")

    def make_web(**kw):
        if sc.get("make_fail"):
            raise RuntimeError(sc["error"])
        return FakeWeb(**kw)

    service = karafun_web.KaraFunWebService(lambda s, d: log.append(["set", s, d]), on_message=lambda m: log.append(["forward"]), on_log=lambda t: log.append(["log", t]),
                                            profile_dir="/tmp/x", make_web=make_web, find_browser=find, credentials=credentials if sc["has_credentials"] else None)
    traces = []
    with mock.patch.object(karafun_web.time, "monotonic", lambda: clock[0]):
        for e in sc["events"]:
            clock[0] = e.get("t", 0.0)
            mark = len(log)
            kind = e["ev"]
            if kind == "start":
                service._thread = FakeThread(alive=e["alive"])
                with mock.patch.object(karafun_web.threading, "Thread", lambda **kw: FakeThread(False, **kw)):
                    service.start()
            elif kind == "setup":
                service._run()
            elif kind == "stop":
                service.stop()
            elif kind == "message":
                cur["saved"] = e.get("saved")
                service._message(e["message"])
            traces.append([list(x) for x in log[mark:]])
    return {"trace": traces, "state": service.state}


def to_rust_events(sc: dict):
    """Rust events plus how many of them each scenario event became (a Python set-up macro is several Rust pieces)."""
    out, sizes = [], []
    for e in sc["events"]:
        t = e.get("t", 0.0)
        before = len(out)
        if e["ev"] == "setup":
            out.append({"ev": "run_begin", "t": t})
            for d, tt in sc.get("progress", []):
                out.append({"ev": "progress", "done": d, "total": tt, "t": t})
            if sc.get("find_fail"):
                out.append({"ev": "setup_failed", "error": sc["error"], "t": t})
            else:
                out.append({"ev": "browser_found", "t": t})
                if sc.get("make_fail"):
                    out.append({"ev": "setup_failed", "error": sc["error"], "t": t})
                else:
                    out.append({"ev": "web_made", "t": t})
                    if sc.get("start_fail"):
                        out.append({"ev": "setup_failed", "error": "start failed", "t": t})
        else:
            out.append(dict(e))
        sizes.append(len(out) - before)
    return out, sizes


def rust_trace(sc: dict, cli: str) -> dict:
    events, sizes = to_rust_events(sc)
    p = subprocess.run([cli], input=json.dumps({"has_credentials": sc["has_credentials"], "events": events}), capture_output=True, text=True, timeout=30)
    r = json.loads(p.stdout)
    if "trace" not in r:
        return r
    merged, i = [], 0
    for n in sizes:
        merged.append([a for part in r["trace"][i:i + n] for a in part])
        i += n
    return {"trace": merged, "state": r["state"]}


STATUS_VARIANTS = [True, False, True, False, 1, 0, None, "yes", "", "0", [], [1], {}, {"a": 1}]
PLANS = [None, "Pro", "", "Premium Plus", "Plan é", 3, 0, True, False, "x" * 40]
ERRORS = ["KaraFun's web player stopped", "Chrome stopped", "Chrome stopped", "other", "", None, 5]
TEXTS = ["no internet", "", "Unicode ünï", "a b c", "x" * 200]


def gen(rng: random.Random) -> dict:
    sc = {"has_credentials": rng.random() < 0.6, "events": [], "progress": [], "error": rng.choice(TEXTS)}
    t = 0.0

    def tick(step=None):
        nonlocal t
        t += step if step is not None else rng.choice([0.0, 0.5, 0.5, 1.0, 2.5])
        return t

    saved_kinds = [{"result": "found"}, {"result": "none"}, {"result": "none", "empty": ()}, {"result": "error", "kind": "Err"},
                   {"result": "error", "kind": "Err2"}, {"result": "error", "kind": "ValueError"}]
    if rng.random() < 0.7:  # set-up first, usually fine
        sc["progress"] = [(rng.randint(0, 300), rng.choice([0, 0, 1, 7, 200, 300])) for _ in range(rng.choice([0, 0, 1, 3]))]
        sc["progress"] = [(min(d, t_) if t_ else d, t_) for d, t_ in sc["progress"]]
        r = rng.random()
        if r < 0.1:
            sc["find_fail"] = True
        elif r < 0.17:
            sc["make_fail"] = True
        elif r < 0.22:
            sc["start_fail"] = True
        if rng.random() < 0.15:
            sc["events"].append({"ev": "start", "alive": rng.random() < 0.3, "t": tick()})
        sc["events"].append({"ev": "setup", "t": tick()})
    streak_choices = [1, 3, 4, 5, 20, 59, 60, 61, 100, 119, 120, 121, 130]
    for _ in range(rng.randint(1, 9)):
        r = rng.random()
        if r < 0.45:
            for _ in range(rng.choice(streak_choices) if rng.random() < 0.6 else rng.randint(1, 8)):
                sc["events"].append(msg(rng, tick(), False))
        elif r < 0.75:
            plan_status = msg(rng, tick(), True)
            sc["events"].append(plan_status)
            # right after a reload the settle window boundaries matter
            for dt in rng.choice([[7.999, 0.001], [8.0], [7.5, 0.5], [0.0, 8.0, 0.5], [3.0, 5.0]]):
                sc["events"].append(msg(rng, tick(dt), rng.random() < 0.7))
        elif r < 0.85:
            sc["events"].append({"ev": "message", "t": tick(), "message": {"type": "error", "message": rng.choice(ERRORS)}})
        elif r < 0.9:
            sc["events"].append({"ev": "message", "t": tick(), "message": rng.choice([{"type": "ready"}, {"type": "log", "text": "x"}, {}, {"type": 5}])})
        elif r < 0.95:
            sc["events"].append({"ev": "stop", "t": tick()})
        else:
            sc["events"].append({"ev": "start", "alive": rng.random() < 0.5, "t": tick()})
            if rng.random() < 0.5:
                sc["events"].append({"ev": "setup", "t": tick()})
        for e in sc["events"]:
            if e["ev"] == "message" and "saved" not in e and rng.random() < 0.9:
                e["saved"] = rng.choice(saved_kinds)
    for e in sc["events"]:
        if e["ev"] == "message" and "saved" not in e:
            e["saved"] = rng.choice(saved_kinds)
    return sc


def story(rng: random.Random) -> dict:
    """Set-up, a long signed-out stretch (60 statuses = the saved-login rule), then the signed-in / reload / settle sequence."""
    sc = {"has_credentials": True, "events": [{"ev": "setup", "t": 0.0}], "progress": [], "error": "x"}
    t = 0.0
    for _ in range(rng.choice([3, 4, 59, 60, 61, 70, 119, 120, 121])):
        t += 0.5
        sc["events"].append(msg(rng, t, False))
    for e in sc["events"]:
        if e["ev"] == "message":
            e["saved"] = rng.choice([{"result": "found"}] * 4 + [{"result": "none"}, {"result": "error", "kind": "Err"}])
    for dt in rng.choice([[0.5, 7.5, 0.5], [0.5, 7.999, 0.001, 0.5], [0.5, 8.0, 0.5, 0.5], [0.5, 4.0, 4.0, 4.0], [30.0, 0.5]]):
        t += dt
        sc["events"].append(msg(rng, t, rng.random() < 0.75))
    for _ in range(rng.choice([0, 3, 70])):
        t += 0.5
        sc["events"].append(msg(rng, t, rng.random() < 0.5))
        sc["events"][-1]["saved"] = {"result": rng.choice(["found", "none"])}
    return sc


def msg(rng, t, signed_in):
    m = {"type": "status", "signedIn": signed_in if rng.random() < 0.85 else rng.choice(STATUS_VARIANTS)}
    if rng.random() < 0.5:
        m["plan"] = rng.choice(PLANS) if rng.random() < 0.995 else rng.choice([2.5, ["a"], {"k": 1}])
    if rng.random() < 0.05:
        del m["signedIn"]
    return {"ev": "message", "t": t, "message": m}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    cli = find_cli()
    rng = random.Random(a.seed)
    same = diff = declined = 0
    seen: dict = {}
    first = []
    for i in range(a.cases):
        sc = story(rng) if i % 3 == 0 else gen(rng)
        py = python_trace(sc)
        rs = rust_trace(sc, cli)
        if "declined" in rs:
            declined += 1
            continue
        if py == rs:
            same += 1
            for tag in sorted({a[0] if a[0] != 'set' else 'set:' + a[1] for t in py['trace'] for a in t}):
                seen[tag] = seen.get(tag, 0) + 1
        else:
            diff += 1
            if len(first) < 3:
                first.append((i, sc, py, rs))
    print(f"scenarios {a.cases}: identical {same}, different {diff}, declined {declined}")
    print("coverage (scenarios containing each action):", json.dumps(seen, sort_keys=True))
    for i, sc, py, rs in first:
        print(f"--- scenario {i}\nscenario: {json.dumps(sc)[:1500]}\npython: {json.dumps(py)[:1500]}\nrust:   {json.dumps(rs)[:1500]}")
    print("IDENTICAL" if diff == 0 else "DIFFERENT")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
