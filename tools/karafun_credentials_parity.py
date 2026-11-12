#!/usr/bin/env python3
"""Parity of the Rust saved-login module (rust/crates/singws-karafun-svc, src/credentials.rs, milestone W4) against the REAL Python `karafun_credentials`.

HARD RULES: only a TEST-ONLY Keychain service (`SingWS-TEST-<random>`, refused by the Rust test tool if it is anything else) is ever read or written, the real service
"SingWS KaraFun account" is never touched, passwords are made-up strings that are never printed (a difference prints the case number and field names only), and every
item created here is deleted at the end. Cases: save/load round trips in both directions (Python writes, Rust reads; Rust writes, Python reads), the stored bytes
(`h:<hex>`), refused emails and passwords, replace-one-account-only, delete one / delete all, and foreign stored data (legacy plain text, bad hex, odd length, whitespace
inside the hex, non-ASCII bytes, empty) read by both. Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv-test-arm64-fresh/bin/python tools/karafun_credentials_parity.py     exit 1 on any difference
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import karafun_credentials as creds  # noqa: E402

REAL = "SingWS KaraFun account"
TEST = "SingWS-TEST-" + uuid.uuid4().hex[:12]
assert TEST != REAL and creds.SERVICE == REAL


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


def rust(cli: str, **req) -> dict:
    p = subprocess.run([cli], input=json.dumps(dict(req, kind="cred")), capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout)


def raw_data(account: str):
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", account, "-s", TEST, "-w"], capture_output=True, text=True, timeout=10)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def seed_raw(account: str, data: bytes) -> bool:
    r = subprocess.run(["/usr/bin/security", "-i"], input=f'add-generic-password -a "{account}" -s "{TEST}" -X {data.hex()}\n', capture_output=True, text=True, timeout=10)
    return r.returncode == 0


EMAILS = ["a@example.invalid", "  padded@example.invalid \t", "plain", "q'uote@example.invalid", "sp ace@example.invalid", "é@example.invalid", "",
          "   ", 'a"b@example.invalid', "a\\b@example.invalid", "a\nb@example.invalid", "a\rb@example.invalid", "x" * 200 + "@example.invalid"]
PASSWORDS = ["simple1", 'p@ss "w\\ord\' 1', "pé 1", "deadbeef", "a b  c", "", " ", "x" * 500, "new\nline", "é\U0001f600", "0", "h:abc", "tab\there"]
FOREIGN = [b"plain-old-password", b"h:", b"h:zz", b"h:abc", b"h:61626", b"h:616263", b"h:61 62", b"h: 6162", b"h:6162 ", b"h:61\n62", b"h:6162\n", b"H:6162",
           b"h:ff", b"h:c3a9", b"h:00", b"h:6162zz", b"\xc3\xa9", b"h:61\xc3\xa9", b"h:6\t162", b"h:61 6 2", b"", b"h:6162 \t\n ", b"h:e29883", b"h:" + b"61" * 300, b"h:61\t62", b"h:61\x0b62", b"h:61\x0c62", b"h:61\r62", b"h:61\x0162", b"h:61\x7f62", b"h:6162\r", b"h:6162\t", b"h:6162\x0b", b"h:6162\x0c", b"h:6162\x01"]


def oracle_cases():
    """(request, expected answer) for the PURE rules (encode / clean_email / decode), expected values taken from the REAL Python module through the TEST service."""
    import random
    rng = random.Random(7)
    out = []
    accounts = set()
    with mock.patch.object(creds, "SERVICE", TEST):
        try:
            for ei, email in enumerate(EMAILS):
                acct = email.strip()
                for pw in PASSWORDS + ["p" + "\u00e9" * rng.randint(1, 5) + str(rng.random()), "\U0001f600" * 3, "a" * 3]:
                    if acct:
                        creds.delete(acct)
                    ok = creds.save(email, pw)
                    if acct:
                        accounts.add(acct)
                    valid_email = bool(acct) and not any(c in acct for c in '"\\\n\r')
                    if valid_email:   # the password rule alone: only with an email the Python accepts
                        out.append(({"op": "encode", "password": pw}, {"stored": raw_data(acct) if ok else None}))
                    if ok and acct:
                        out.append(({"op": "decode", "stored_hex": raw_data(acct).encode().hex()}, {"password": creds.load(email)}))
                out.append(({"op": "clean_email", "email": email}, None))   # filled below from the save rule
            for fi, data in enumerate(FOREIGN + [bytes(rng.choice(b"0123456789abcdefh:  \n\tzZ") for _ in range(rng.randint(0, 20))) for _ in range(60)]):
                acct = f"foreign{fi}@example.invalid"
                accounts.add(acct)
                if seed_raw(acct, data):
                    out.append(({"op": "decode", "stored_hex": data.hex()}, {"password": creds.load(acct)}))
        finally:
            for a in list(accounts):
                creds.delete(a)
    fixed = []
    for req, exp in out:
        if req["op"] == "clean_email":
            e = req["email"].strip()
            exp = {"email": e if e and not any(c in e for c in '"\\\n\r') else None}
        fixed.append((req, exp))
    return fixed


def main() -> int:
    cli = find_cli()
    bad = []
    n = 0
    accounts = set()

    def check(name, a, b):
        nonlocal n
        n += 1
        if a != b:
            bad.append(name)

    with mock.patch.object(creds, "SERVICE", TEST):
        try:
            for ei, email in enumerate(EMAILS):
                acct_norm = email.strip()
                for pi, pw in enumerate(PASSWORDS):
                    tag = f"e{ei}p{pi}"
                    # ---- Python writes; the Rust `security`-tool backend reads (prompt-free: same creator)
                    if acct_norm:
                        creds.delete(acct_norm)
                    ok_py = creds.save(email, pw)
                    if acct_norm:
                        accounts.add(acct_norm)
                    py_load = creds.load(email)
                    check(tag + " py->rust-tool load", py_load, rust(cli, op="tool_load", service=TEST, email=email).get("password"))
                    stored = raw_data(acct_norm) if acct_norm else None
                    check(tag + " stored bytes", stored, (rust(cli, op="encode", password=pw).get("stored") if ok_py else None))
                    check(tag + " clean_email", acct_norm if acct_norm and not any(c in acct_norm for c in '"\\\n\r') else None, rust(cli, op="clean_email", email=email).get("email"))
                    # ---- the Rust `security`-tool backend writes; Python reads
                    if acct_norm:
                        creds.delete(acct_norm)
                    ok_rs = rust(cli, op="tool_save", service=TEST, email=email, password=pw).get("ok")
                    check(tag + " tool save result", ok_py, ok_rs)
                    check(tag + " rust-tool->py load", creds.load(email), rust(cli, op="tool_load", service=TEST, email=email).get("password"))
                    check(tag + " tool stored bytes", raw_data(acct_norm) if acct_norm else None, stored)
                    # ---- the NATIVE Security-framework backend: its own round trip (an item it creates is not readable by the `security` tool without a dialog)
                    NS = TEST + "-native"
                    ok_nat = rust(cli, op="save", service=NS, email=email, password=pw).get("ok")
                    check(tag + " native save result", ok_py, ok_nat)
                    check(tag + " native load", py_load if ok_py else None, rust(cli, op="load", service=NS, email=email).get("password"))
                    st = rust(cli, op="status", service=NS, email=email)
                    check(tag + " native stored length", (len(stored) if stored is not None and ok_py else None), (st.get("len") if st.get("found") else None))
                    rust(cli, op="delete", service=NS, email=email)
            # ---- replace one account only, delete one, delete all (Python and the Rust tool backend, then the native backend on its own)
            for a in list(accounts):
                creds.delete(a)   # the no-email delete only makes 5 passes, so start this part from an empty service
            a1, a2, a3 = "one@example.invalid", "two@example.invalid", "three@example.invalid"
            for who in ("py", "rust-tool"):
                def put(a, pw):
                    return creds.save(a, pw) if who == "py" else rust(cli, op="tool_save", service=TEST, email=a, password=pw)

                def load_all():
                    return [creds.load(x) for x in (a1, a2, a3)]
                for a, pw in ((a1, "pw1"), (a2, "pw2"), (a3, "pw3")):
                    put(a, pw)
                    accounts.add(a)
                put(a1, "pw1-new")
                check(who + " replace one", load_all(), ["pw1-new", "pw2", "pw3"])
                (creds.delete(a2) if who == "py" else rust(cli, op="tool_delete", service=TEST, email=a2))
                check(who + " delete one", load_all(), ["pw1-new", None, "pw3"])
                (creds.delete() if who == "py" else rust(cli, op="tool_delete_all", service=TEST))
                check(who + " delete all", load_all(), [None, None, None])
            NS = TEST + "-native"
            for a, pw in ((a1, "pw1"), (a2, "pw2"), (a3, "pw3")):
                rust(cli, op="save", service=NS, email=a, password=pw)
            rust(cli, op="save", service=NS, email=a1, password="pw1-new")
            nl = lambda: [rust(cli, op="load", service=NS, email=x).get("password") for x in (a1, a2, a3)]
            check("native replace one", nl(), ["pw1-new", "pw2", "pw3"])
            rust(cli, op="delete", service=NS, email=a2)
            check("native delete one", nl(), ["pw1-new", None, "pw3"])
            for x in (a1, a2, a3):
                rust(cli, op="delete", service=NS, email=x)
            # ---- foreign stored data (made by the `security` tool) read by Python and by the Rust tool backend
            for fi, data in enumerate(FOREIGN):
                acct = f"foreign{fi}@example.invalid"
                accounts.add(acct)
                if not seed_raw(acct, data):
                    continue
                check(f"foreign{fi}", creds.load(acct), rust(cli, op="tool_load", service=TEST, email=acct).get("password"))
            # ---- the guard: the Rust tool refuses the real service
            check("guard real service", "refused" in str(rust(cli, op="tool_load", service=REAL, email="x@example.invalid").get("error", "")), True)
            check("guard other service", "refused" in str(rust(cli, op="tool_load", service="Something else", email="x@example.invalid").get("error", "")), True)
        finally:
            for a in list(accounts):
                creds.delete(a)
    print(f"cases {n}: different {len(bad)}")
    for b in bad[:20]:
        print("DIFFERENT:", b)
    print("IDENTICAL" if not bad else "DIFFERENT")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
