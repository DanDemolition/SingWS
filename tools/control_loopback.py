#!/usr/bin/env python3
"""Loopback proof for the Rust control client (singws-control): everything is real except the app's windows and the network is 127.0.0.1 only.

  * the REAL PHP server code (a temp copy of SingWS-Server run with `php -S`): PIN login with its session cookie, and /v2/api/ws-token.php (HMAC token)
  * the repo's reference relay (SingWS-Server/v2/dev/relay-server/relay.py) verifying that token
  * the REAL app logic: tools/control_app.py runs 0.2.18.1.py's `_on_host_relay_command`, `_execute_host_control_command`, queue commands, `_queue_signature`,
    `_publish_host_state_ws` on a bare app, connected to the relay as role=app
  * the Rust client: `singws-control-cli`, driven over stdin/stdout

Run with the app's venv Python:  ~/.venvs/singws/.venv-build-arm64/bin/python tools/control_loopback.py     (also used by test_control_loopback.py)
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "SingWS-Server"
CLI = os.environ.get("SINGWS_CONTROL_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-control-cli")
PHP = shutil.which("php")
SECRET = "loopback-secret-0123456789abcdef0123456789abcdef0123456789abcdef"
PIN = "4321"
TENANT = "dan"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_port(port, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


class Proc:
    def __init__(self, cmd, env=None, cwd=None):
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.log: list[str] = []
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=cwd, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            line = line.rstrip("\n")
            self.log.append(line)
            self.lines.put(line)

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def kill(self):
        try:
            self.p.send_signal(signal.SIGTERM)
            self.p.wait(timeout=5)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


class Stream:
    """JSON lines from a process with wait-for-predicate helpers; every line is kept."""

    def __init__(self, proc: Proc, key: str):
        self.proc, self.key, self.seen = proc, key, []

    def wait_for(self, pred, timeout=10.0, since=0):
        end = time.time() + timeout
        i = since
        while time.time() < end:
            while i < len(self.seen):
                if pred(self.seen[i]):
                    return self.seen[i], i + 1
                i += 1
            try:
                line = self.proc.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.seen.append(json.loads(line))
            except ValueError:
                self.seen.append({self.key: "raw", "text": line})
        raise AssertionError(f"timed out waiting; last seen: {self.seen[-6:]}")

    def mark(self):
        return len(self.seen)

    def drain(self):
        """Pull in every line the process has printed so far."""
        time.sleep(0.3)
        while True:
            try:
                line = self.proc.lines.get_nowait()
            except queue.Empty:
                return
            try:
                self.seen.append(json.loads(line))
            except ValueError:
                self.seen.append({self.key: "raw", "text": line})


class Stack:
    """php + relay + the app stand-in, in a temp dir, torn down on exit."""

    def __init__(self, mode="apply", mute=False, start_app=True, tls=False):
        self.mode, self.mute, self.start_app, self.tls = mode, mute, start_app, tls
        self.procs: list[Proc] = []

    def __enter__(self):
        if not PHP:
            raise unittest_skip("php is not installed")
        self.tmp = Path(tempfile.mkdtemp(prefix="control_loopback_"))
        self.relay_port, self.php_port = free_port(), free_port()
        srv = self.tmp / "server"
        shutil.copytree(SERVER, srv, ignore=shutil.ignore_patterns(".git", "node_modules", "*.db"))
        (srv / "tenants" / TENANT).mkdir(parents=True, exist_ok=True)
        (srv / "data").mkdir(exist_ok=True)
        (srv / "tenants" / TENANT / "settings.json").write_text(json.dumps({"host_controls_pin": PIN}))
        sessions = self.tmp / "sessions"
        sessions.mkdir()
        self.sessions = sessions
        prepend = self.tmp / "prepend.php"
        self.tls_port = free_port() if self.tls else None
        public = f"wss://127.0.0.1:{self.tls_port}/relay" if self.tls else f"ws://127.0.0.1:{self.relay_port}/relay"
        prepend.write_text(f"<?php define('WS_RELAY_SECRET', '{SECRET}'); define('WS_PUBLIC_URL', '{public}');\n")
        self.php = Proc([PHP, "-d", f"auto_prepend_file={prepend}", "-d", f"session.save_path={sessions}", "-S", f"127.0.0.1:{self.php_port}", "-t", str(srv)], cwd=str(srv))
        self.procs.append(self.php)
        assert wait_port(self.php_port), "php did not start"
        self.start_relay()
        if self.tls:
            self.ca = make_private_ca(self.tmp)
            self.proxy = Proc([sys.executable, str(ROOT / "tools/tls_proxy.py"), "--listen", str(self.tls_port), "--target", str(self.relay_port),
                               "--cert", str(self.tmp / "srv.pem"), "--key", str(self.tmp / "srv.key")])
            self.procs.append(self.proxy)
            assert wait_port(self.tls_port), "tls proxy did not start"
        if self.start_app:
            self.start_the_app()
        return self

    def start_relay(self):
        self.relay = Proc([sys.executable, str(SERVER / "v2/dev/relay-server/relay.py"), "--host", "127.0.0.1", "--port", str(self.relay_port), "--secret", SECRET])
        self.procs.append(self.relay)
        assert wait_port(self.relay_port), "relay did not start"

    def stop_relay(self):
        self.relay.kill()
        self.procs.remove(self.relay)

    def start_the_app(self):
        cmd = [sys.executable, str(ROOT / "tools/control_app.py"), "--url", f"ws://127.0.0.1:{self.relay_port}/relay", "--tenant", TENANT, "--mode", self.mode]
        if self.mute:
            cmd.append("--mute")
        self.app = Proc(cmd, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        self.app_events = Stream(self.app, "event")
        self.procs.append(self.app)
        self.app_events.wait_for(lambda e: e.get("event") == "connected", timeout=40)

    def shell(self, pin=PIN, env=None, trust_ca=False):
        e = dict(os.environ)
        e.update({"CONTROL_RECONNECT_MS": "400", "CONTROL_ACK_TIMEOUT_MS": "1200"})
        if trust_ca:
            e["CONTROL_ROOTS_PEM"] = str(self.ca)
        e.update(env or {})
        p = Proc([CLI, f"http://127.0.0.1:{self.php_port}", TENANT, pin], env=e)
        self.procs.append(p)
        return Stream(p, "out"), p

    def __exit__(self, *exc):
        for p in reversed(self.procs):
            p.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_private_ca(d: Path) -> Path:
    """A throwaway CA and a 127.0.0.1 server certificate signed by it (srv.pem / srv.key in `d`); returns the CA file."""
    run = lambda *a: subprocess.run(a, check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "ca.key"), "-out", str(d / "ca.pem"), "-days", "2",
        "-subj", "/CN=SingWS loopback CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "srv.key"), "-out", str(d / "srv.csr"), "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n"
                               "authorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n")
    run("openssl", "x509", "-req", "-in", str(d / "srv.csr"), "-CA", str(d / "ca.pem"), "-CAkey", str(d / "ca.key"), "-CAcreateserial", "-out", str(d / "srv.pem"),
        "-days", "2", "-extfile", str(d / "ext.cnf"))
    return d / "ca.pem"


class unittest_skip(Exception):
    pass


def is_msg(t):
    return lambda e: e.get("out") == "message" and e["msg"].get("type") == t


def scenario_wss_works_when_the_servers_certificate_is_trusted():
    with Stack("apply", tls=True) as st:
        out, proc = connect(st, trust_ca=True)
        q, _ = first_queue(out)
        _, ack = command(out, proc, "queue.move_singer", {"singer_id": "s1", "direction": "down"}, sig=q["queue_sig"])
        assert ack["msg"]["ok"] and ack["msg"]["message"] == "moved", ack
        proc.send({"quit": True})


def scenario_wss_refuses_a_certificate_it_does_not_trust():
    with Stack("apply", tls=True) as st:
        out, proc = st.shell()  # built-in public roots only: the private CA is not among them
        out.wait_for(lambda e: e.get("out") == "logged_in", timeout=15)
        fail1, _ = out.wait_for(lambda e: e.get("out") == "status" and not e.get("connected") and "relay" in e.get("text", ""), timeout=15)
        time.sleep(1.5)
        assert not any(e.get("out") == "status" and e.get("connected") for e in out.seen), "it must never connect to an untrusted server"
        assert any("relay connect failed" in e.get("text", "") for e in out.seen if e.get("out") == "log"), out.seen[-5:]
        proc.send({"send": "play_pause"})
        e, _ = out.wait_for(lambda e: e.get("out") == "send_error", timeout=5)
        assert "not connected" in e["error"]


def connect(stack, **kw):
    out, proc = stack.shell(**kw)
    out.wait_for(lambda e: e.get("out") == "logged_in", timeout=15)
    out.wait_for(lambda e: e.get("out") == "status" and e.get("connected"), timeout=15)
    return out, proc


def first_queue(out, since=0):
    q, i = out.wait_for(is_msg("queue"), timeout=15, since=since)
    return q["msg"], i


def command(out, proc, name, args=None, sig=None, since=None, request_id=None):
    mark = out.mark() if since is None else since
    msg = {"send": name, "args": args or {}}
    if sig:
        msg["expect_queue_sig"] = sig
    if request_id:
        msg["request_id"] = request_id
    proc.send(msg)
    sent, _ = out.wait_for(lambda e: e.get("out") in ("sent", "send_error"), timeout=5, since=mark)
    if sent["out"] == "send_error":
        return sent, None
    ack, _ = out.wait_for(lambda e: (is_msg("ack")(e) and e["msg"]["request_id"] == sent["request_id"]) or
                          (e.get("out") == "no_answer" and e["request_id"] == sent["request_id"]) or
                          (is_msg("error")(e) and e["msg"].get("request_id") == sent["request_id"]), timeout=8, since=mark)
    return sent, ack


# ---- scenarios (each returns nothing and raises AssertionError on failure) --------------------------------------------------------------------

def scenario_login_connect_and_view():
    with Stack("apply") as st:
        out, proc = connect(st)
        hello, _ = out.wait_for(is_msg("hello"), timeout=5)
        assert hello["msg"]["role"] == "browser"
        state, _ = out.wait_for(lambda e: is_msg("state")(e) and e["msg"]["connected"] and e["msg"].get("queue_sig"), timeout=10)
        q, _ = first_queue(out)
        assert q["queue_sig"] == state["msg"]["queue_sig"], "state and queue carry the same fingerprint"
        assert [s["singer_id"] for s in q["singers"]] == ["s1", "s2", "s3"]
        assert q["singers"][0]["marker"] is True and q["singers"][0]["songs"][0]["entry_id"] == "local:uid1"
        assert q["singers"][0]["songs"][0]["artist"] == "Artist1"
        proc.send({"quit": True})


def scenario_queue_edit_applies_and_the_view_updates():
    with Stack("apply") as st:
        out, proc = connect(st)
        q, _ = first_queue(out)
        mark = out.mark()
        sent, ack = command(out, proc, "queue.move_singer", {"singer_id": "s1", "direction": "down"}, sig=q["queue_sig"])
        assert ack["msg"]["ok"] and ack["msg"]["message"] == "moved", ack
        after, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == sent["request_id"])
        assert after["order"] == ["s2", "s1", "s3"], after
        newq, _ = out.wait_for(lambda e: is_msg("queue")(e) and e["msg"]["queue_sig"] != q["queue_sig"], timeout=10, since=mark)
        assert [s["singer_id"] for s in newq["msg"]["singers"]] == ["s2", "s1", "s3"]
        # a song within a singer, then a removal
        mark = out.mark()
        _, ack = command(out, proc, "queue.move_song", {"entry_id": "local:uid5", "direction": "up"}, sig=newq["msg"]["queue_sig"])
        assert ack["msg"]["ok"] and ack["msg"]["message"] == "moved", ack
        q3, _ = out.wait_for(lambda e: is_msg("queue")(e) and e["msg"]["queue_sig"] != newq["msg"]["queue_sig"], timeout=10, since=mark)
        lee = next(s for s in q3["msg"]["singers"] if s["singer_id"] == "s3")
        assert [y["entry_id"] for y in lee["songs"]] == ["local:uid5", "local:uid4"], lee
        _, ack = command(out, proc, "queue.remove_song", {"entry_id": "local:uid3"}, sig=q3["msg"]["queue_sig"])
        assert ack["msg"]["ok"] and ack["msg"]["message"] == "removed", ack
        _, ack = command(out, proc, "queue.skip_singer", {"singer_id": "s2", "skipped": True})
        assert ack["msg"]["ok"] and ack["msg"]["message"] in ("changed", "no_change"), ack
        proc.send({"quit": True})


def scenario_a_stale_view_is_rejected_and_nothing_changes():
    with Stack("apply") as st:
        out, proc = connect(st)
        q, _ = first_queue(out)
        _, ack = command(out, proc, "queue.move_singer", {"singer_id": "s1", "direction": "down"}, sig=q["queue_sig"])
        assert ack["msg"]["ok"]
        first_after, _ = st.app_events.wait_for(lambda e: e.get("event") == "after")
        sent, stale = command(out, proc, "queue.move_singer", {"singer_id": "s3", "direction": "top"}, sig=q["queue_sig"])
        assert stale["msg"]["ok"] is False and stale["msg"]["message"] == "stale_view", stale
        after, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == sent["request_id"], timeout=5)
        assert after["order"] == ["s2", "s1", "s3"] and after["sig"] == first_after["sig"], "the stale command left the queue alone"
        proc.send({"quit": True})


def scenario_dry_run_and_off_change_nothing():
    for mode, expect in (("dry_run", "dry_run: would"), ("off", "shell_commands_off")):
        with Stack(mode) as st:
            out, proc = connect(st)
            base, _ = st.app_events.wait_for(lambda e: e.get("event") == "connected")
            _, ack = command(out, proc, "queue.move_singer", {"singer_id": "s1", "direction": "down"})
            assert expect in ack["msg"]["message"], (mode, ack)
            assert ack["msg"]["ok"] is (mode == "dry_run")
            after, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == ack["msg"]["request_id"])
            assert after["order"] == ["s1", "s2", "s3"] and after["sig"] == base["sig"], (mode, after)
            # a transport command from the shell follows the same switch
            _, ack = command(out, proc, "skip")
            assert expect in ack["msg"]["message"] or ack["msg"]["message"] == "next", (mode, ack)
            after2, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == ack["msg"]["request_id"])
            assert after2["played"] == [], (mode, "dry_run/off must not play anything")
            proc.send({"quit": True})
    with Stack("apply") as st:
        out, proc = connect(st)
        _, ack = command(out, proc, "skip")
        assert ack["msg"]["ok"] and ack["msg"]["message"] == "next"
        after, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == ack["msg"]["request_id"])
        assert after["played"] == ["next"]
        proc.send({"quit": True})


def scenario_the_same_request_id_applies_once():
    with Stack("apply") as st:
        out, proc = connect(st)
        _, a1 = command(out, proc, "queue.move_singer", {"singer_id": "s1", "direction": "down"}, request_id="retry-1")
        _, a2 = command(out, proc, "queue.move_singer", {"singer_id": "s1", "direction": "down"}, request_id="retry-1")
        assert a1["msg"]["message"] == "moved" and a2["msg"]["message"] == "duplicate: moved", (a1, a2)
        after, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == "retry-1")
        assert after["order"] == ["s2", "s1", "s3"]
        proc.send({"quit": True})


def scenario_bad_commands_never_reach_the_wire():
    with Stack("apply") as st:
        out, proc = connect(st)
        seen_before = len([e for e in st.app_events.seen if e.get("event") == "command"])
        for bad in ({"send": "queue.move_singer", "args": {"singer_id": "s1", "direction": "sideways"}}, {"send": "set_key", "args": {"value": 99}},
                    {"send": "queue.explode", "args": {}}, {"send": "queue.skip_singer", "args": {"singer_id": "s1"}}):
            mark = out.mark()
            proc.send(bad)
            e, _ = out.wait_for(lambda e: e.get("out") in ("sent", "send_error"), timeout=5, since=mark)
            assert e["out"] == "send_error", bad
        time.sleep(0.6)
        assert len([e for e in st.app_events.seen if e.get("event") == "command"]) == seen_before
        proc.send({"quit": True})


def scenario_wrong_pin_stops_without_connecting():
    with Stack("apply") as st:
        out, proc = st.shell(pin="0000")
        refused, _ = out.wait_for(lambda e: e.get("out") == "login_refused", timeout=10)
        assert "PIN" in refused["text"]
        time.sleep(1.2)  # longer than the reconnect delay: it must NOT retry
        assert not any(e.get("out") == "logged_in" for e in out.seen)
        assert not any("browser" in line for line in st.relay.log), st.relay.log
        _, ack = None, None
        proc.send({"send": "play_pause"})
        e, _ = out.wait_for(lambda e: e.get("out") == "send_error", timeout=5)
        assert "not connected" in e["error"]


def scenario_no_app_means_an_app_offline_error():
    with Stack("apply", start_app=False) as st:
        out, proc = connect(st)
        state, _ = out.wait_for(is_msg("state"), timeout=5)
        assert state["msg"]["connected"] is False, "the relay tells a new browser that no app is connected"
        sent, ack = command(out, proc, "play_pause")
        assert ack["msg"]["code"] == "app_offline", ack
        proc.send({"quit": True})


def scenario_a_frozen_app_gives_no_answer():
    with Stack("apply", mute=True) as st:
        out, proc = connect(st)
        sent, ack = command(out, proc, "queue.noop")
        assert ack.get("out") == "no_answer" and ack["request_id"] == sent["request_id"], ack
        proc.send({"quit": True})


def scenario_the_relay_restarts_and_the_client_comes_back_with_a_fresh_token():
    with Stack("apply") as st:
        out, proc = connect(st)
        first_queue(out)
        mark = out.mark()
        st.stop_relay()
        out.wait_for(lambda e: e.get("out") == "status" and not e.get("connected"), timeout=10, since=mark)
        st.start_relay()
        st.start_the_app()  # the app side also lost the relay; a new one joins
        out.wait_for(lambda e: e.get("out") == "status" and e.get("connected"), timeout=20, since=mark)
        q, _ = first_queue(out, since=mark)
        assert len(q["singers"]) == 3
        _, ack = command(out, proc, "queue.noop")
        assert ack["msg"]["ok"], ack
        proc.send({"quit": True})


def scenario_an_expired_server_session_logs_in_again_by_itself():
    with Stack("apply") as st:
        out, proc = connect(st)
        first_queue(out)
        mark = out.mark()
        for f in st.sessions.glob("sess_*"):
            f.unlink()  # the server forgets the session
        st.stop_relay()
        out.wait_for(lambda e: e.get("out") == "status" and not e.get("connected"), timeout=10, since=mark)
        st.start_relay()
        st.start_the_app()
        out.wait_for(lambda e: e.get("out") == "logged_in", timeout=20, since=mark)
        out.wait_for(lambda e: e.get("out") == "status" and e.get("connected"), timeout=20, since=mark)
        _, ack = command(out, proc, "queue.noop")
        assert ack["msg"]["ok"], ack
        proc.send({"quit": True})


SCENARIOS = [v for k, v in sorted(globals().items()) if k.startswith("scenario_")]


def main():
    if not PHP or not os.path.exists(CLI):
        print("needs php and singws-control-cli (cargo build --release -p singws-control)", file=sys.stderr)
        return 2
    failed = 0
    for fn in SCENARIOS:
        t0 = time.time()
        try:
            fn()
            print(f"ok    {fn.__name__}  ({time.time() - t0:.1f}s)")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL  {fn.__name__}: {exc!r}")
            traceback.print_exc(limit=-3)
    print("RESULT: ALL SCENARIOS PASSED" if not failed else f"RESULT: {failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
