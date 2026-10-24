"""Test harness for the relay transport proof: a minimal RFC 6455 server on 127.0.0.1 and runners for the app's REAL Qt worker classes and the
Rust client. Nothing here ever contacts a real host."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
import subprocess
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QCoreApplication, QObject, QTimer, QUrl, QUrlQuery, pyqtSignal  # noqa: E402
from PyQt6.QtWebSockets import QWebSocket  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import ast  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WS_CLI = os.environ.get("SINGWS_RELAY_WS_CLI") or os.path.expanduser("~/.cache/singws-rust-target/release/singws-relay-ws-cli")
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_TREE = ast.parse((ROOT / "0.2.18.1.py").read_text(encoding="utf-8"))


# ---- scripted server ----------------------------------------------------------------------------------------------------------------

class Conn:
    def __init__(self, reader, writer, record):
        self.reader, self.writer, self.record = reader, writer, record

    async def send_text(self, text):
        payload = text.encode("utf-8")
        n = len(payload)
        head = bytes([0x81]) + (bytes([n]) if n < 126 else (b"\x7e" + struct.pack(">H", n) if n < 65536 else b"\x7f" + struct.pack(">Q", n)))
        self.writer.write(head + payload)
        await self.writer.drain()

    async def recv(self, timeout=2.0):
        """Next frame from the client as (opcode, payload) or None on timeout/EOF. Text frames are recorded."""
        try:
            h = await asyncio.wait_for(self.reader.readexactly(2), timeout)
            opcode, n = h[0] & 0x0F, h[1] & 0x7F
            if n == 126:
                n = struct.unpack(">H", await self.reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", await self.reader.readexactly(8))[0]
            mask = await self.reader.readexactly(4) if h[1] & 0x80 else b"\0\0\0\0"
            data = bytearray(await self.reader.readexactly(n))
            for i in range(n):
                data[i] ^= mask[i % 4]
            if opcode == 1:
                self.record["received"].append(bytes(data).decode("utf-8"))
            if opcode == 8:
                self.record["client_closed"] = True
            return opcode, bytes(data)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            return None

    async def close_frame(self, code=1000):
        self.writer.write(bytes([0x88, 2]) + struct.pack(">H", code))
        await self.writer.drain()
        self.writer.close()

    def drop(self):
        self.writer.transport.abort()

    async def hold(self, seconds):
        """Keep the connection open, draining client frames, for up to `seconds`."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            frame = await self.recv(timeout=0.1)
            if frame and frame[0] == 8:
                return


class Server:
    """`scripts[i]` is an async function run for the i-th connection (the last one repeats). `reject` makes the handshake fail with HTTP 403."""

    def __init__(self, scripts, reject=()):
        self.scripts, self.reject = scripts, set(reject)
        self.connections = []
        self._writers = []
        self.loop = asyncio.new_event_loop()
        self.port = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.server = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", 0))
        self.port = self.server.sockets[0].getsockname()[1]
        self._ready.set()
        self.loop.run_forever()

    def __enter__(self):
        self._thread.start()
        self._ready.wait(5)
        return self

    def __exit__(self, *a):
        async def shutdown():
            self.server.close()
            for writer in list(self._writers):
                try:
                    writer.transport.abort()
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(3)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(2)
        self.loop.close()

    async def _handle(self, reader, writer):
        index = len(self.connections)
        self._writers.append(writer)
        record = {"request_line": None, "headers": {}, "received": [], "client_closed": False, "accepted": False}
        self.connections.append(record)
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        except Exception:
            writer.close()
            return
        lines = raw.decode("latin-1").split("\r\n")
        record["request_line"] = lines[0]
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                record["headers"][k.strip().lower()] = v.strip()
        if index in self.reject:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        accept = base64.b64encode(hashlib.sha1((record["headers"].get("sec-websocket-key", "") + _GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await writer.drain()
        record["accepted"] = True
        script = self.scripts[min(index, len(self.scripts) - 1)]
        try:
            await script(Conn(reader, writer, record))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


# ---- the app's REAL worker classes on REAL Qt -------------------------------------------------------------------------------------------

def real_classes():
    ns = {"QObject": QObject, "pyqtSignal": pyqtSignal, "QTimer": QTimer, "QWebSocket": QWebSocket, "QUrl": QUrl, "QUrlQuery": QUrlQuery,
          "json": json, "print": lambda *a, **k: None}
    for node in _TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_network_normalize_base_url":
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "app", "exec"), ns)
    for node in _TREE.body:
        if isinstance(node, ast.ClassDef) and node.name in ("RelayRequestWorker", "HostControlRelayWorker"):
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "app", "exec"), ns)
    return ns


_APP = None


def qt_app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def run_qt(kind, port, seconds, reconnect_ms=200, send_after_connect=None, user="dan", key="KEY&=1", version="1.0.1.4"):
    """Run the app's real worker against 127.0.0.1:port for `seconds`; returns the signals it emitted, in order."""
    qt_app()
    ns = real_classes()
    cls = ns["RelayRequestWorker" if kind == "request" else "HostControlRelayWorker"]
    cls.RECONNECT_DELAY_MS = reconnect_ms
    worker = cls(f"http://127.0.0.1:{port}", user, key, version)
    out = []
    worker.connection_status_changed.connect(lambda ok, text: out.append({"signal": "status", "ok": ok, "text": text}))
    if kind == "request":
        worker.requests_available.connect(lambda reason: out.append({"signal": "requests_available", "reason": reason}))
        worker.history_available.connect(lambda t: out.append({"signal": "history_available", "type": t}))
    else:
        worker.command_received.connect(lambda c: out.append({"signal": "command", "command": c}))
    worker.start()
    end, sent = time.monotonic() + seconds, False
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if send_after_connect is not None and not sent and worker.is_connected():
            sent = worker.send_json(send_after_connect)
        time.sleep(0.005)
    worker.stop()
    for _ in range(20):
        QCoreApplication.processEvents()
        time.sleep(0.005)
    return out


def run_rust(kind, port, seconds, reconnect_ms=200, send_after_connect=None, user="dan", key="KEY&=1", version="1.0.1.4"):
    """Run the Rust client (same URL the pure crate builds) against 127.0.0.1:port; returns the same signal list."""
    url = subprocess.run([os.path.expanduser("~/.cache/singws-rust-target/release/singws-relay-cli")],
                         input=json.dumps({"op": "url", "base_url": f"http://127.0.0.1:{port}", "user": user, "key": key, "version": version,
                                           "host": kind == "host"}), capture_output=True, text=True, timeout=30)
    url = json.loads(url.stdout)["url"]
    cmd = [WS_CLI, kind, url, "--reconnect-ms", str(reconnect_ms), "--seconds", str(seconds)]
    if send_after_connect is not None:
        cmd += ["--send-after-connect", json.dumps(send_after_connect)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 20)
    return [d for d in (json.loads(l) for l in proc.stdout.splitlines() if l.strip()) if d.get("signal") != "log"]
