#!/usr/bin/env python3
"""Mutation test for singws-http: each mutant must be killed by the Rust tests or by tools/http_parity.py. Exit 1 if one survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-http/src/lib.rs"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.path.expanduser("~/.cache/singws-rust-target"))
M = [
    ("if s.closing {", "if false {"),
    ("s.active += 1;", ""),
    ("s.active -= 1;", ""),
    ("self.0.idle.notify_all();", ""),
    ("while s.active != 0 {", "while false {"),
    (".connect_timeout(t)", ""),
    (".read_timeout(t)", ".timeout(t)"),
    ("Policy::limited(30)", "Policy::none()"),
    ("(None, Some(0)) => builder = builder.header(\"Content-Length\", \"0\"),", "(None, Some(0)) => {}"),
    ("(Some(body), None) if body.is_empty() => {", "(Some(body), None) if false => {"),
    (".header(\"Accept\", \"*/*\")", ""),
    ("HttpError::Other(format!(\"body: {e}\"))", "classify(&e)"),
    ("if !(timeout.is_finite() && timeout > 0.0) {", "if false {"),
    ("tls_certs_only(roots.iter().cloned())", "tls_certs_merge(roots.iter().cloned())"),
    ("if !(prepared.url.starts_with(\"http://\") || prepared.url.starts_with(\"https://\")) {", "if false {"),
    (".header(\"Content-Type\", ct)", ""),
]


def run(cmd, timeout=900):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, timeout=timeout)


def main():
    survivors = 0
    text = SRC.read_text()
    try:
        for n, (old, new) in enumerate(M):
            if old not in text:
                print(f"[{n:02d}] PATTERN NOT FOUND {old[:50]!r}")
                survivors += 1
                continue
            SRC.write_text(text.replace(old, new, 1))
            run(["cargo", "build", "--tests", "-p", "singws-http", "--lib", "--quiet"])
            try:
                r = run(["cargo", "test", "-p", "singws-http", "--lib", "--quiet"], timeout=90)  # a mutant that deadlocks a test is a killed mutant
            except subprocess.TimeoutExpired:
                subprocess.run(["pkill", "-f", "singws_http-"])
                print(f"[{n:02d}] killed by unit tests (hang)  {old[:50]!r}")
                continue
            if r.returncode != 0:
                print(f"[{n:02d}] killed by {'build' if 'could not compile' in r.stderr else 'unit tests'}  {old[:50]!r}")
                continue
            run(["cargo", "build", "--release", "-p", "singws-http"])
            p = subprocess.run([sys.executable, str(ROOT / "tools/http_parity.py"), "--cases", "40"], capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                print(f"[{n:02d}] killed by parity  {old[:50]!r}")
            else:
                print(f"[{n:02d}] SURVIVED  {old[:60]!r}")
                survivors += 1
    finally:
        SRC.write_text(text)
        run(["cargo", "build", "--release", "-p", "singws-http"])
    print(f"survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
