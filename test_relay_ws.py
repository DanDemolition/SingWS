import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import relay_parity as rp  # noqa: E402
import relay_ws_harness as h  # noqa: E402

ROOT = Path(__file__).parent
needs_bins = unittest.skipUnless(Path(h.WS_CLI).exists() and Path(rp.CLI_DEFAULT).exists(),
                                 "needs singws-relay-ws-cli and singws-relay-cli (cargo build --release -p singws-relay-ws -p singws-relay)")


async def s_normal_then_close(c):
    for m in ({"type": "hello", "user": "dan"}, {"type": "requests_available"}, {"type": "history_updated"}, "not json", {"type": "zzz"}, {"type": "HISTORY_BULK_SYNC"}):
        await c.send_text(m if isinstance(m, str) else json.dumps(m))
    await asyncio.sleep(0.15)
    await c.close_frame()


async def s_keep(c):
    await c.send_text(json.dumps({"type": "requests_available"}))
    await c.hold(4)


async def s_hold_only(c):
    await c.hold(4)


async def s_drop(c):
    await asyncio.sleep(0.15)
    c.drop()


async def s_host_commands(c):
    await c.send_text(json.dumps({"type": "command", "id": 9, "name": "pause"}))
    await c.send_text(json.dumps({"type": "ack", "id": 1}))     # not a command: dropped
    await c.send_text("garbage")                                 # not JSON: dropped
    await c.send_text(json.dumps({"type": " COMMAND ", "id": 10, "name": "skip"}))
    await c.hold(4)


async def s_host_close_once(c):
    await asyncio.sleep(0.1)
    await c.close_frame()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def both(kind, scripts, seconds, reject=(), **kw):
    """Run the app's real Qt worker and the Rust client against identical fresh servers; return (qt, rust) results."""
    results = []
    for runner in (h.run_qt, h.run_rust):
        with h.Server(scripts, reject) as srv:
            signals = runner(kind, srv.port, seconds, **kw)
            time.sleep(0.15)
            results.append({"signals": signals, "connections": [dict(c) for c in srv.connections]})
    return results


def request_lines(r):
    return [c["request_line"] for c in r["connections"]]


@needs_bins
class PureLogicParityTests(unittest.TestCase):
    def test_urls_messages_and_lifecycle_match_the_apps_real_worker_classes(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status = rp.main(["--cases", "1500", "--seed", "7"])
        self.assertEqual(status, 0, buf.getvalue())
        self.assertIn("RELAY PARITY OK", buf.getvalue())


@needs_bins
class TransportAgainstRealQtTests(unittest.TestCase):
    def assertSame(self, qt, rust, what=""):
        self.assertEqual(qt["signals"], rust["signals"], f"{what}: signals differ")
        self.assertEqual(request_lines(qt), request_lines(rust), f"{what}: handshake request lines differ")

    def test_normal_session_then_server_close_then_reconnect(self):
        qt, rust = both("request", [s_normal_then_close, s_keep], 1.8)
        self.assertSame(qt, rust)
        kinds = [(s["signal"], s.get("ok"), s.get("reason") or s.get("type")) for s in rust["signals"]]
        self.assertEqual(kinds[:7], [("status", True, None), ("requests_available", None, "connect"), ("requests_available", None, "relay"),
                                     ("history_available", None, "history_updated"), ("history_available", None, "history_bulk_sync"),
                                     ("status", False, None), ("status", True, None)])
        self.assertEqual(len(rust["connections"]), 2)

    def test_abrupt_connection_drop_reconnects(self):
        qt, rust = both("request", [s_drop, s_keep], 1.6)
        self.assertSame(qt, rust)
        self.assertEqual(sum(1 for s in rust["signals"] if s["signal"] == "status" and not s["ok"]), 1)
        self.assertEqual(len(rust["connections"]), 2)

    def test_a_rejected_handshake_reports_one_disconnect_and_retries(self):
        qt, rust = both("request", [s_keep], 1.6, reject=(0,))
        self.assertSame(qt, rust)
        self.assertEqual([s["signal"] for s in rust["signals"]][:2], ["status", "status"])
        self.assertFalse(rust["signals"][0]["ok"])

    def test_nothing_listening_keeps_retrying_on_the_timer(self):
        port = free_port()
        results = []
        for runner in (h.run_qt, h.run_rust):
            results.append(runner("request", port, 1.3, reconnect_ms=200))
        qt, rust = results
        self.assertTrue(qt and rust)
        self.assertEqual({(s["signal"], s["ok"]) for s in qt}, {("status", False)})
        self.assertEqual({(s["signal"], s["ok"]) for s in rust}, {("status", False)})
        self.assertGreaterEqual(len(qt), 3)
        self.assertGreaterEqual(len(rust), 3)
        self.assertLessEqual(abs(len(qt) - len(rust)), 2, f"attempt counts: Qt {len(qt)}, Rust {len(rust)}")

    def test_host_worker_receives_only_commands_and_sends_compact_ascii_json(self):
        ack = {"type": "ack", "id": 9, "note": "é"}
        qt, rust = both("host", [s_host_commands], 1.2, send_after_connect=ack)
        self.assertSame(qt, rust)
        commands = [s["command"] for s in rust["signals"] if s["signal"] == "command"]
        self.assertEqual([c["id"] for c in commands], [9, 10])
        self.assertEqual(qt["connections"][0]["received"], rust["connections"][0]["received"])
        self.assertEqual(rust["connections"][0]["received"], ['{"type":"ack","id":9,"note":"\\u00e9"}'])
        self.assertIn("role=app&channel=host", request_lines(rust)[0])

    def test_host_worker_has_no_disconnect_status_but_reconnects(self):
        qt, rust = both("host", [s_host_close_once, s_hold_only], 1.5)
        self.assertSame(qt, rust)
        self.assertEqual([(s["signal"], s["ok"]) for s in rust["signals"]], [("status", True), ("status", True)])
        self.assertEqual(len(rust["connections"]), 2)

    def test_awkward_identities_are_percent_encoded_identically_on_the_wire(self):
        kw = dict(user="Dan & Co", key="a b+c%d é", version="v 1")
        qt, rust = both("request", [s_hold_only], 0.7, **kw)
        self.assertSame(qt, rust)
        self.assertIn("user=Dan%20%26%20Co&token=a%20b%2Bc%25d%20%C3%A9&app_version=v%201", request_lines(rust)[0])

    def test_the_handshake_carries_exactly_the_headers_qt_sends(self):
        qt, rust = both("host", [s_hold_only], 0.6)
        self.assertEqual(sorted(qt["connections"][0]["headers"]), sorted(rust["connections"][0]["headers"]))
        for key in ("connection", "upgrade", "sec-websocket-version"):
            self.assertEqual(qt["connections"][0]["headers"][key].lower(), rust["connections"][0]["headers"][key].lower(), key)

    def test_after_stop_no_further_connection_is_made(self):
        for runner in (h.run_qt, h.run_rust):
            with h.Server([s_hold_only]) as srv:
                runner("request", srv.port, 0.5)
                seen = len(srv.connections)
                time.sleep(0.8)  # several reconnect periods
                self.assertEqual(len(srv.connections), seen, runner.__name__)
                self.assertEqual(seen, 1)


class BoundaryTests(unittest.TestCase):
    def test_the_pure_crate_has_no_networking_and_the_transport_crate_only_tungstenite(self):
        pure = (ROOT / "rust/crates/singws-relay/Cargo.toml").read_text()
        deps = pure[pure.index("[dependencies]"):]
        for name in ("tungstenite", "reqwest", "hyper", "tokio", "rustls", "native-tls", "ureq", "curl"):
            self.assertNotIn(name, deps)
        src = "".join(p.read_text() for p in (ROOT / "rust/crates/singws-relay/src").rglob("*.rs"))
        for needle in ("std::net", "TcpStream", "std::thread", "Instant::now", "SystemTime"):
            self.assertNotIn(needle, src, "the lifecycle logic must stay pure: no sockets, threads or clocks")
        ws = (ROOT / "rust/crates/singws-relay-ws/Cargo.toml").read_text()
        wdeps = ws[ws.index("[dependencies]"):]
        for name in ("reqwest", "hyper", "tokio", "native-tls", "ureq", "curl"):
            self.assertNotIn(name, wdeps)
        self.assertIn('tls = ["tungstenite/rustls-tls-webpki-roots"]', ws)
        self.assertIn('default = []', ws, "wss/TLS stays opt-in until the owner chooses the stack")

    def test_the_app_does_not_use_the_rust_relay_yet(self):
        app = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
        for needle in ("singws-relay", "relay_ws", "relay_cli", "singws_relay"):
            self.assertNotIn(needle, app)
        for cargo in ("singws-dsp-ffi",):
            self.assertNotIn("singws-relay", (ROOT / f"rust/crates/{cargo}/Cargo.toml").read_text())

    def test_the_harness_only_ever_talks_to_loopback(self):
        text = (ROOT / "tools/relay_ws_harness.py").read_text()
        self.assertNotIn("wskar", text)
        self.assertNotIn("github", text)
        self.assertIn('"127.0.0.1"', text)
        self.assertIn('f"http://127.0.0.1:{port}"', text)


if __name__ == "__main__":
    unittest.main()
