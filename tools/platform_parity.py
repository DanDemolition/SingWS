#!/usr/bin/env python3
"""Headless parity checks for rust/crates/singws-mac against independent oracles. Never opens a permission prompt by default.

  1. run_applescript: the probe's `--applescript 'return 1+1'` against /usr/bin/osascript (same stdout, ok flag), plus an error script
     (both must fail) and the empty-script contract.
  2. keep-awake: the probe's `--hold` is visible in `pmset -g assertions` while held (with its reason string, as PreventUserIdleSystemSleep
     and PreventUserIdleDisplaySleep style assertions) and gone after release.
  3. frontmost app: the probe's name against `lsappinfo` (no Automation permission needed).
  4. --json state: the five keys, keep-awake taken and released.

OPT-IN (these MAY SHOW A macOS PERMISSION PROMPT the first time for the probe binary): --system-events runs
`tell application "System Events" to get name of first process` through the probe and through osascript.

  python3 tools/platform_parity.py [--probe PATH] [--system-events]      exit 0 = all equal
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_PROBE = os.path.join(os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-mac")), "release", "singws-mac-probe")
failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ((" -- " + detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def probe_script(probe, source):
    out = subprocess.run([probe, "--applescript", source], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"^applescript .*?: ok=(true|false) out=(\".*?\") err=(\".*\")$", out, re.M)
    return (m.group(1) == "true", json.loads(m.group(2)), json.loads(m.group(3))) if m else (None, None, None)


def osascript(source):
    p = subprocess.run(["osascript", "-e", source], capture_output=True, text=True, timeout=60)
    return p.returncode == 0, p.stdout.strip(), p.stderr.strip()


def assertions():
    return subprocess.run(["pmset", "-g", "assertions"], capture_output=True, text=True, timeout=20).stdout


def lsappinfo_front():
    asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=10).stdout.strip()
    out = subprocess.run(["lsappinfo", "info", "-only", "name", asn], capture_output=True, text=True, timeout=10).stdout
    m = re.match(r'\s*"(.*?)" ASN:', out)
    return m.group(1) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default=DEFAULT_PROBE)
    ap.add_argument("--system-events", action="store_true")
    a = ap.parse_args()
    if not os.access(a.probe, os.X_OK):
        print(f"probe not built: {a.probe}\n  cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-mac cargo build --release -p singws-mac")
        return 2

    ok, out, err = probe_script(a.probe, "return 1+1")
    o_ok, o_out, _ = osascript("return 1+1")
    check("applescript return 1+1 : ok flag", ok is True and o_ok, f"rust={ok} osascript={o_ok}")
    check("applescript return 1+1 : stdout", out == o_out == "2", f"rust={out!r} osascript={o_out!r}")
    ok, out, err = probe_script(a.probe, "return \"  padded  \"")
    check("applescript stdout is stripped", ok is True and out == "padded", f"{out!r}")
    ok, out, err = probe_script(a.probe, "this is not applescript (")
    o_ok, _, _ = osascript("this is not applescript (")
    check("applescript error: both fail", ok is False and not o_ok and err, f"rust ok={ok} err={err!r}")
    ok, out, err = probe_script(a.probe, " ")
    check("applescript empty script contract", ok is False and err == "empty_script", f"{ok} {err!r}")

    before = assertions()
    p = subprocess.Popen([a.probe, "--hold", "5"], stdout=subprocess.PIPE, text=True)
    first = p.stdout.readline()
    time.sleep(0.8)
    held = assertions()
    released_line = p.stdout.readline()  # printed after the release; the probe then stays alive 2 s
    time.sleep(0.5)
    after = assertions()
    p.wait(timeout=30)
    mine_held = [l for l in held.splitlines() if "singws-mac-probe hold" in l]
    check("keep-awake: probe reports it is held", "held=true active=true" in first, first)
    check("keep-awake: pmset shows system-sleep assertion while held", any("PreventUserIdleSystemSleep" in l for l in mine_held) and not any("singws-mac-probe hold" in l for l in before.splitlines()), "")
    check("keep-awake: pmset shows display-sleep assertion while held", any("PreventUserIdleDisplaySleep" in l for l in mine_held), "")
    check("keep-awake: probe reports release", "released active=false" in released_line, released_line)
    check("keep-awake: pmset no longer shows it after release (process still alive)", not any("singws-mac-probe hold" in l for l in after.splitlines()), "")

    front = subprocess.run([a.probe], capture_output=True, text=True, timeout=60).stdout
    m = re.search(r'^frontmost_app: "(.*)"$', front, re.M)
    mine, theirs = (m.group(1) if m else None), lsappinfo_front()
    check("frontmost app equals lsappinfo", mine is not None and mine == theirs, f"rust={mine!r} lsappinfo={theirs!r}  (focus may have moved: rerun)")

    state = json.loads(subprocess.run([a.probe, "--json"], capture_output=True, text=True, timeout=60).stdout)
    check("--json keys", set(state) == {"supported", "frontmost_app", "ax_trusted", "keep_awake_ok", "keep_awake_released"}, str(sorted(state)))
    check("--json keep-awake taken and released", state.get("keep_awake_ok") is True and state.get("keep_awake_released") is True, str(state))

    if a.system_events:
        src = 'tell application "System Events" to get name of first process'
        ok, out, err = probe_script(a.probe, src)
        o_ok, o_out, _ = osascript(src)
        check("System Events first process name equal", ok is True and o_ok and out == o_out, f"rust={ok},{out!r},{err!r} osascript={o_ok},{o_out!r}")
    else:
        print("skip System Events script (opt-in: --system-events; may show a permission prompt for the probe)")

    print(("ALL EQUAL" if not failures else f"{len(failures)} FAILURE(S): {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
