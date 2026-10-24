#!/usr/bin/env python3
"""Stage D4b parity: does the Rust sender (``singws-http-cli``) put the SAME request on a real socket as the app's ``requests`` stack, and treat
responses and failures the same way?  Everything runs against servers on 127.0.0.1 started by this tool; nothing leaves the machine.

Oracle = the app's own ``network_lifecycle.ShutdownRequests`` (requests 2.x). A raw-socket capture server records each request exactly as received
(request line, header names and values, body bytes). For random calls (the same generators as tools/wire_parity.py) and a set of scripted behaviours:

  request    method, request target (path + query), body bytes, Content-Type, Content-Length, absence of Transfer-Encoding, Accept
  response   status, body, Content-Type, final URL, gzip/deflate bodies decoded, 204/304/HEAD without body, a 5 MB body, non-2xx NOT an error
  redirect   301/302/303 on POST become GET without a body, 307/308 keep method and body, the chain recorded by the server is identical
  timeouts   ``timeout=N`` is a connect timeout and a PER-READ timeout: a server that drips bytes for longer than N in total succeeds on both; a server
             that never answers, or stalls mid-body, fails on both within roughly N
  failures   refused connection, connection closed without a response -> an error on both (coarse class compared)
  tls        a self-signed https server on 127.0.0.1: trusted when its certificate is the supplied root bundle, rejected by default, like ``verify=``
Header differences that are not part of the contract (User-Agent, Connection, Accept-Encoding spelling) are not compared.

Usage:  python3 tools/http_parity.py [--cases N] [--seed S]       Exit status 1 on any difference.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402
import wire_parity as wp  # noqa: E402
from network_lifecycle import ShutdownRequests  # noqa: E402

CLI_DEFAULT = os.path.expanduser("~/.cache/singws-rust-target/release/singws-http-cli")
failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 40:
        print("DIFF:", msg)


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


# ---- capture server ------------------------------------------------------------------------------------------------------------------

class Server:
    def __init__(self, tls=None):
        self.records = []
        self.lock = threading.Lock()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(64)
        self.port = self.sock.getsockname()[1]
        self.tls = tls
        self.stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def base(self):
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{self.port}"

    def take(self):
        with self.lock:
            r, self.records = self.records, []
        return r

    def _accept(self):
        while not self.stop:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        try:
            if self.tls:
                c = self.tls.wrap_socket(c, server_side=True)
            c.settimeout(10)
            buf = b""
            while True:
                while b"\r\n\r\n" not in buf:
                    chunk = c.recv(65536)
                    if not chunk:
                        return
                    buf += chunk
                head, _, rest = buf.partition(b"\r\n\r\n")
                lines = head.split(b"\r\n")
                method, target, _ = lines[0].decode("latin-1").split(" ", 2)
                headers = {}
                for ln in lines[1:]:
                    k, _, v = ln.decode("latin-1").partition(":")
                    headers[k.strip().lower()] = v.strip()
                body = b""
                if headers.get("transfer-encoding", "").lower() == "chunked":
                    # decode chunked bodies so they can be compared (and flagged)
                    data = rest
                    while True:
                        while b"\r\n" not in data:
                            data += c.recv(65536)
                        size_line, _, data = data.partition(b"\r\n")
                        size = int(size_line.split(b";")[0], 16)
                        while len(data) < size + 2:
                            data += c.recv(65536)
                        if size == 0:
                            rest = data[2:]
                            break
                        body += data[:size]
                        data = data[size + 2:]
                else:
                    n = int(headers.get("content-length", "0") or 0)
                    while len(rest) < n:
                        rest += c.recv(65536)
                    body, rest = rest[:n], rest[n:]
                buf = rest
                with self.lock:
                    self.records.append({"method": method, "target": target, "headers": headers, "body": body})
                if not self._respond(c, method, target, headers):
                    return
        except Exception:  # noqa: BLE001 - a client that hangs up mid-test is normal
            pass
        finally:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def _respond(self, c, method, target, headers):
        path = target.split("?")[0]

        def send(status, body=b"", extra=(), reason="OK", head_only=False):
            hs = [f"HTTP/1.1 {status} {reason}", f"Content-Length: {len(body)}"] + list(extra)
            c.sendall(("\r\n".join(hs) + "\r\n\r\n").encode("latin-1") + (b"" if head_only else body))
            return True

        if path.startswith("/status/"):
            code = int(path.rsplit("/", 1)[1])
            return send(code, b"" if code in (204, 304) else f"status {code}".encode(), ["Content-Type: text/plain"], "X")
        if path.startswith("/redirect/"):
            code = int(path.split("/")[2])
            return send(code, b"moved", ["Location: /echo?landed=1", "Content-Type: text/plain"], "Moved")
        if path == "/gzip":
            return send(200, gzip.compress(b"zipped body " * 50), ["Content-Encoding: gzip", "Content-Type: text/plain"])
        if path == "/deflate":
            return send(200, zlib.compress(b"deflated body " * 50), ["Content-Encoding: deflate", "Content-Type: text/plain"])
        if path == "/big":
            return send(200, bytes(range(256)) * 20480, ["Content-Type: application/octet-stream"])
        if path == "/head" or method == "HEAD":
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nContent-Type: text/plain\r\n\r\n")
            return True
        if path == "/hang":  # accepts, reads the request, never answers
            time.sleep(4)
            return False
        if path == "/drip":  # 4 chunks 0.3 s apart: 1.2 s in total, never silent for 0.5 s
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nContent-Type: text/plain\r\n\r\n")
            for _ in range(4):
                time.sleep(0.3)
                c.sendall(b"ab")
            return True
        if path == "/stall-body":
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc")
            time.sleep(4)
            return False
        if path == "/close":
            return False
        return send(200, b'{"ok": true}', ["Content-Type: application/json"])


# ---- the two clients ------------------------------------------------------------------------------------------------------------------

def python_call(case, verify=True):
    client = ShutdownRequests()
    t0 = time.time()
    try:
        r = client.request(case["method"], case["url"], params=case.get("params"), data=case.get("data"), json=case.get("json"),
                           timeout=case.get("timeout", 10), verify=verify, allow_redirects=True)
        return {"ok": True, "status": r.status_code, "url": r.url, "body": r.text, "content_type": r.headers.get("Content-Type"),
                "elapsed": time.time() - t0}
    except requests.exceptions.Timeout:
        return {"ok": False, "class": "timeout", "elapsed": time.time() - t0}
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "class": "connection" if isinstance(exc, requests.exceptions.ConnectionError) else "other", "elapsed": time.time() - t0,
                "exc": type(exc).__name__}


def rust_call(cli, case, roots=None):
    env = dict(os.environ)
    if roots:
        env["SINGWS_HTTP_ROOTS"] = roots
    t0 = time.time()
    out = subprocess.run([cli], input=json.dumps(case), capture_output=True, text=True, timeout=120, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"cli failed: {out.stderr[:300]}")
    r = json.loads(out.stdout)
    el = time.time() - t0
    if r.get("ok"):
        ct = next((v for k, v in r["headers"] if k == "content-type"), None)
        return {"ok": True, "status": r["status"], "url": r["url"], "body": r["body"], "content_type": ct, "elapsed": el}
    kind = r["kind"]
    return {"ok": False, "class": "timeout" if kind == "timeout" else ("connection" if kind in ("connect", "other") else kind), "elapsed": el,
            "raw": r["error"][:200]}


def key_fields(rec):
    h = rec["headers"]
    return {"method": rec["method"], "target": rec["target"], "body": rec["body"], "content-type": h.get("content-type"),
            "content-length": h.get("content-length"), "transfer-encoding": h.get("transfer-encoding"), "accept": h.get("accept")}


def compare_calls(label, server, case, cli, roots=None, py_verify=True, expect_same_records=True):
    server.take()
    py = python_call(case, py_verify)
    py_recs = [key_fields(r) for r in server.take()]
    rs = rust_call(cli, case, roots)
    rs_recs = [key_fields(r) for r in server.take()]
    tally(label)
    if py["ok"] != rs["ok"]:
        fail(f"{label}: python ok={py['ok']} ({py.get('exc') or py.get('class')}) rust ok={rs['ok']} ({rs.get('class')}: {rs.get('raw')}) for {case}")
        return py, rs
    if py["ok"]:
        for k in ("status", "body", "content_type"):
            if py[k] != rs[k]:
                fail(f"{label}: {k} python={py[k]!r:.120} rust={rs[k]!r:.120} for {case}")
        if py["url"].split("127.0.0.1")[-1] != rs["url"].split("127.0.0.1")[-1]:
            fail(f"{label}: final url python={py['url']} rust={rs['url']}")
    elif py["class"] != rs["class"]:
        fail(f"{label}: error class python={py['class']} ({py.get('exc')}) rust={rs['class']} ({rs.get('raw')})")
    if expect_same_records and py_recs != rs_recs:
        for a, b in zip(py_recs + [None] * 3, rs_recs + [None] * 3):
            if a != b:
                fail(f"{label}: server saw different requests\n   python={a}\n   rust  ={b}\n   case={case}")
                break
    return py, rs


# ---- scenarios ------------------------------------------------------------------------------------------------------------------------

def check_random(cli, server, rng, n):
    cases = wp.make_cases(n, rng.randint(0, 2**31))
    declined = 0
    for case in cases:
        case = dict(case)
        case["url"] = server.base + "/api/v1/" + case["url"].rsplit("/", 1)[1]
        case["timeout"] = 10
        # does the wire builder accept it at all? (requests may still send things Rust declines)
        rs = rust_call(cli, case)
        if not rs["ok"] and rs.get("class") == "decline":
            declined += 1
            tally("random declined")
            continue
        server.take()
        py = python_call(case)
        py_recs = [key_fields(r) for r in server.take()]
        # run the Rust call again for the record comparison (the first one above also reached the server; discard it)
        server.take()
        rs = rust_call(cli, case)
        rs_recs = [key_fields(r) for r in server.take()]
        tally("random calls")
        if not (py["ok"] and rs["ok"]):
            fail(f"random: python={py} rust={rs} case={case}")
            continue
        if py_recs != rs_recs:
            fail(f"random: server saw different requests\n   python={py_recs}\n   rust  ={rs_recs}\n   case={case}")
        if (py["status"], py["body"]) != (rs["status"], rs["body"]):
            fail(f"random: response differs {py['status']} {rs['status']}")
    return declined


def check_scripted(cli, server):
    base = server.base
    for code in (200, 201, 204, 304, 400, 404, 500, 503):
        compare_calls(f"status {code}", server, {"method": "POST", "url": f"{base}/status/{code}", "data": {"a": "1"}}, cli)
    for code in (301, 302, 303, 307, 308):
        compare_calls(f"redirect {code} POST", server, {"method": "POST", "url": f"{base}/redirect/{code}", "data": {"a": "1", "b": "ü"}}, cli)
        compare_calls(f"redirect {code} GET", server, {"method": "GET", "url": f"{base}/redirect/{code}", "params": {"q": "x y"}}, cli)
    compare_calls("gzip body", server, {"method": "GET", "url": f"{base}/gzip"}, cli)
    compare_calls("deflate body", server, {"method": "GET", "url": f"{base}/deflate"}, cli)
    compare_calls("5 MB body", server, {"method": "GET", "url": f"{base}/big", "timeout": 20}, cli)
    compare_calls("HEAD", server, {"method": "HEAD", "url": f"{base}/head"}, cli)
    compare_calls("empty POST", server, {"method": "POST", "url": f"{base}/echo"}, cli)
    compare_calls("json POST", server, {"method": "POST", "url": f"{base}/echo", "json": {"k": [1, 2.5, None, "é"]}}, cli)


def check_timeouts(cli, server):
    base = server.base
    # per-read semantics: 1.2 s of dripping with a 0.5 s timeout succeeds on both
    py, rs = compare_calls("drip within per-read timeout", server, {"method": "GET", "url": f"{base}/drip", "timeout": 0.5}, cli)
    for name, r in (("python", py), ("rust", rs)):
        if r["ok"] and r["elapsed"] < 1.1:
            fail(f"drip: {name} returned in {r['elapsed']:.2f}s, before the body could have arrived")
    # silence longer than the timeout fails promptly on both
    for path, label in (("/hang", "hang before response"), ("/stall-body", "stall mid-body")):
        t = 0.6
        py, rs = compare_calls(label, server, {"method": "GET", "url": f"{base}{path}", "timeout": t}, cli, expect_same_records=False)
        for name, r in (("python", py), ("rust", rs)):
            if r["ok"]:
                fail(f"{label}: {name} succeeded")
            elif not (t * 0.8 <= r["elapsed"] <= t + 2.5):
                fail(f"{label}: {name} gave up after {r['elapsed']:.2f}s for timeout {t}")
    # connection closed with no response
    compare_calls("closed without a response", server, {"method": "GET", "url": f"{base}/close", "timeout": 2}, cli, expect_same_records=False)
    # nothing listening
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    compare_calls("connection refused", server, {"method": "GET", "url": f"http://127.0.0.1:{port}/x", "timeout": 2}, cli, expect_same_records=False)
    # a non-positive or non-numeric timeout is declined by Rust before anything is sent
    r = rust_call(cli, {"method": "GET", "url": f"{base}/echo", "timeout": 0})
    tally("timeout 0 declined")
    if r["ok"] or r.get("class") != "decline":
        fail(f"timeout 0 should be declined, got {r}")


def make_cert(d):
    """A throwaway CA and a server certificate for 127.0.0.1 signed by it. Returns (server key, server cert chain, CA cert)."""
    d = Path(d)
    run = lambda *a: subprocess.run(a, check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "ca.key"), "-out", str(d / "ca.pem"), "-days", "2",
        "-subj", "/CN=SingWS parity test CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "srv.key"), "-out", str(d / "srv.csr"), "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n")
    run("openssl", "x509", "-req", "-in", str(d / "srv.csr"), "-CA", str(d / "ca.pem"), "-CAkey", str(d / "ca.key"), "-CAcreateserial", "-out",
        str(d / "srv.pem"), "-days", "2", "-extfile", str(d / "ext.cnf"))
    return str(d / "srv.key"), str(d / "srv.pem"), str(d / "ca.pem")


def check_tls(cli):
    with tempfile.TemporaryDirectory(prefix="http_parity_tls_") as d:
        key, crt, ca = make_cert(d)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        srv = Server(tls=ctx)
        case = {"method": "POST", "url": srv.base + "/echo", "data": {"a": "1"}, "timeout": 5}
        # trusted: requests with verify=<bundle> vs Rust with only that bundle
        py = python_call(case, verify=ca)
        srv.take()
        rs = rust_call(cli, case, roots=ca)
        tally("tls trusted")
        if not (py["ok"] and rs["ok"]):
            fail(f"tls trusted: python={py} rust={rs}")
        # untrusted: default verification must refuse the self-signed certificate on both
        py = python_call(case)
        rs = rust_call(cli, case)
        tally("tls untrusted")
        if py["ok"] or rs["ok"]:
            fail(f"tls untrusted: self-signed certificate accepted (python ok={py['ok']}, rust ok={rs['ok']})")
        # a root bundle that does NOT contain the server certificate is refused
        other = Path(d) / "other"
        other.mkdir()
        _, _, ca2 = make_cert(other)
        rs = rust_call(cli, case, roots=ca2)
        tally("tls wrong root")
        if rs["ok"]:
            fail("tls wrong root: accepted a certificate outside the supplied bundle")
        srv.stop = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--cli", default=os.environ.get("SINGWS_HTTP_CLI") or CLI_DEFAULT)
    args = ap.parse_args(argv)
    if not os.path.exists(args.cli):
        print(f"missing {args.cli}; build with: cd rust && cargo build --release -p singws-http", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    server = Server()
    check_random(args.cli, server, rng, args.cases)
    check_scripted(args.cli, server)
    check_timeouts(args.cli, server)
    check_tls(args.cli)
    server.stop = True
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if failures:
        print(f"RESULT: {len(failures)} DIFFERENCE(S)")
        return 1
    print("RESULT: IDENTICAL (same requests on the socket, same responses, same timeout and failure behaviour)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
