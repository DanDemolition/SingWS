#!/usr/bin/env python3
"""Mutation test for the platform shim: rust/crates/singws-mac (lib.rs, the Objective-C shim, the probe) and platform_shadow.py.
A mutant is killed when the crate's unit tests, tools/platform_parity.py (Rust files) or test_platform_shadow.py (Python file) fail.
Equivalent mutants carry a note. Run it ALONE (it rewrites source files and rebuilds per mutant).

NOT mutated on purpose: the Accessibility `kAXTrustedCheckOptionPrompt` flag (flipping it to YES could show a permission prompt) and the
location code (it needs a CoreLocation grant, see docs/design/stage-e-platform-shim-findings.md).
  python3 tools/mutate_platform.py [--only 3,7]      CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-mac
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "rust/crates/singws-mac"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-mac"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
PY = os.environ.get("SINGWS_PYTHON", "/Users/daniel/Documents/SingWS/.venv/bin/python")
LIB, SHIM, PROBE, PSH = CRATE / "src/lib.rs", CRATE / "shim/mac_shim.m", CRATE / "src/bin/probe.rs", ROOT / "platform_shadow.py"

M = [
    (LIB, '4 => "authorized_when_in_use",', '4 => "authorized_always",', None),
    (LIB, "status == 3 || status == 4", "status == 3", None),
    (LIB, "if source.trim().is_empty() {", "if source.is_empty() {", None),
    (LIB, "out: out.trim().to_string(), err", "out, err", None),
    (LIB, "match rx.recv_timeout(timeout) {", "match rx.recv() {", None),
    (LIB, 'Err(_) => ScriptResult { ok: false, out: String::new(), err: "timeout".into() },', 'Err(_) => ScriptResult { ok: false, out: String::new(), err: "timed out".into() },', None),
    (LIB, 'return ScriptResult { ok: false, out: String::new(), err: "empty_script".into() };', 'return ScriptResult { ok: false, out: String::new(), err: "empty".into() };', None),
    (LIB, "keep_awake_ok\": began && active,", "keep_awake_ok\": began,", "active is always true right after a successful begin"),
    (LIB, '"keep_awake_released": !keep_awake_active(),', '"keep_awake_released": true,', "the release cannot fail from the outside; the unit test asserts keep_awake_active() directly and the parity tool reads pmset"),
    (LIB, "    unsafe {\n        singws_mac_keep_awake_end()\n    }", "    {}", None),
    (SHIM, "(NSActivityOptions)1 << 40", "(NSActivityOptions)0", None),
    (SHIM, "if (token != nil) [[NSProcessInfo processInfo] endActivity:token];", "(void)token;", "under ARC dropping the token object ends the activity anyway (pmset shows it gone either way)"),
    (SHIM, "if (g_activity != nil) return 1;", "if (g_activity != nil) { [[NSProcessInfo processInfo] endActivity:g_activity]; }", "ends and immediately re-begins an assertion; not observable from outside"),
    (SHIM, "return dup_ns(app.localizedName);", 'return dup_ns(app.bundleIdentifier);', None),
    (SHIM, "*out = dup_ns(result.stringValue);", '*out = strdup("");', None),
    (SHIM, "if (error != nil) { *err = dup_ns([error description]); return 0; }", "if (error != nil) { *err = dup_ns([error description]); return 1; }", None),
    (PROBE, 'keep_awake_end();\n        println!("released', 'println!("released', None),
    (PSH, "if rs.get(\"frontmost_app\") not in (py_before.get(\"frontmost_app\"), py_after.get(\"frontmost_app\")):", "if rs.get(\"frontmost_app\") != py_before.get(\"frontmost_app\"):", None),
    (PSH, "if bool(py_before.get(\"ax_trusted\")) != bool(rs.get(\"ax_trusted\")) or bool(py_after.get(\"ax_trusted\")) != bool(rs.get(\"ax_trusted\")):", "if bool(py_before.get(\"ax_trusted\")) != bool(rs.get(\"ax_trusted\")):", None),
    (PSH, "if bool(py_after.get(\"keep_awake_active\")) != bool(keep_awake_expected):", "if False:", None),
    (PSH, "if not (rs.get(\"keep_awake_ok\") and rs.get(\"keep_awake_released\")):", "if not rs.get(\"keep_awake_ok\"):", None),
    (PSH, "fresh = [d for d in diffs if d not in _seen]", "fresh = diffs", None),
    (PSH, "say(f\"MISMATCH {d}\")", "pass", None),
    (PSH, "if not rs.get(\"supported\"):", "if False:", None),
    (PSH, "if proc.returncode != 0:", "if False:", None),
    (PSH, "return bool((settings or {}).get(\"platform_shadow\", False)) and sys.platform == \"darwin\"", "return bool((settings or {}).get(\"platform_shadow\", True)) and sys.platform == \"darwin\"", None),
    (PSH, "    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app\n        say(", "    except ZeroDivisionError as exc:  # noqa: BLE001\n        say(", None),
]


def tests_for(path):
    if path == PSH:
        return [[PY, "-m", "unittest", "test_platform_shadow"]]
    return [["cargo", "test", "--release", "-p", "singws-mac"], ["cargo", "build", "--release", "-p", "singws-mac"], [PY, "tools/platform_parity.py"]]


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, env=dict(ENV, SINGWS_HOME=tempfile.mkdtemp()), capture_output=True, text=True, timeout=900).returncode == 0


def main():
    only = None
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    survivors, killed, equivalent = [], 0, 0
    for i, (path, old, new, note) in enumerate(M, 1):
        if only and i not in only:
            continue
        src = path.read_text()
        if src.count(old) != 1:
            print(f"#{i}: PATTERN NOT UNIQUE/FOUND ({src.count(old)}) in {path.name}: {old[:60]!r}")
            survivors.append(i)
            continue
        try:
            path.write_text(src.replace(old, new))
            ok = True
            for cmd in tests_for(path):
                cwd = ROOT / "rust" if cmd[0] == "cargo" else ROOT
                if not run(cmd, cwd):
                    ok = False
                    break
        finally:
            path.write_text(src)
        if not ok:
            killed += 1
            print(f"#{i}: killed   {path.name}: {old[:60]!r}")
        elif note:
            equivalent += 1
            print(f"#{i}: EQUIVALENT ({note})")
        else:
            survivors.append(i)
            print(f"#{i}: SURVIVED {path.name}: {old[:60]!r} -> {new[:60]!r}")
    # leave the build matching the restored source
    subprocess.run(["cargo", "build", "--release", "-p", "singws-mac"], cwd=ROOT / "rust", env=ENV, capture_output=True)
    print(f"\n{killed} killed, {equivalent} equivalent, {len(survivors)} survived: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
