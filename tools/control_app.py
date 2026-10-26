#!/usr/bin/env python3
"""A stand-in for the running SingWS app for loopback tests of the Rust control client.

It is NOT a re-implementation: it loads the real 0.2.18.1.py, builds a bare KaraokeApp (collaborators stubbed and recorded, like test_shell_queue_commands),
connects to the relay as role=app and feeds every inbound `command` to the app's REAL `_on_host_relay_command`; state, queue and ack messages are produced by the
app's real `_publish_host_state_ws`, `_queue_signature` and `_shell_queue_summary`. Events are printed as JSON lines on stdout for the test to read.

Run with the app's venv Python:  control_app.py --url ws://127.0.0.1:PORT/relay --tenant dan --key K --mode apply|dry_run|off [--mute]
"""
import argparse
import asyncio
import json
import os
import sys
import time
import types
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "SingWS-Server/v2/dev/relay-server"))
import _test_profile  # noqa: E402,F401
import wsutil  # noqa: E402
from test_shell_queue_commands import Harness, entry, module  # noqa: E402


def emit(**kw):
    print(json.dumps(kw), flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--tenant", default="dan")
    ap.add_argument("--key", default="devkey")
    ap.add_argument("--mode", default="apply")
    ap.add_argument("--mute", action="store_true", help="receive commands but never answer (a frozen app)")
    ap.add_argument("--library", default=None, help="write the ids of a throwaway song library to this file and let the app add those songs (queue.add_song)")
    args = ap.parse_args()
    m = module()
    queue = [
        {"singer_id": "s1", "name": "Dana", "songs": [entry(1, remote=False), entry(2)], "skipped": False, "rotation_marker": True},
        {"singer_id": "s2", "name": "Sam", "songs": [entry(3)], "skipped": False},
        {"singer_id": "s3", "name": "Lee", "songs": [entry(4), entry(5)], "skipped": False},
    ]
    if args.library:
        import test_shell_add_song as ta  # the same throwaway library and recorded widgets the add-song tests use

        lib = ta.env()
        Path(args.library).write_text(json.dumps({"ids": lib["ids"]}))
        h = ta.AddHarness(queue)
        h.app._is_rotation_mode = lambda: True
    else:
        h = Harness(queue)
    app = h.app
    app.settings.update({"shell_commands": args.mode, "user": args.tenant})
    played = []
    app.toggle_karaoke_pause = lambda: played.append("pause")
    app.play_next_file = lambda skip_confirmation=False: played.append("next")
    app.stop_and_clear_now_singing = lambda skip_confirmation=False: played.append("stop")
    app._host_control_state = lambda: {"rotation": {"last": {}, "current": {}, "next": {}},
                                       "playback": {"is_playing": False, "position_seconds": 0.0, "duration_seconds": 0.0, "remaining_seconds": 0.0}}
    app._queue_entry_artist_title = lambda e: (e.get("artist", ""), e.get("title", ""))
    app._update_undo_action = lambda: None
    u = urlparse(args.url)
    reader, writer = await asyncio.open_connection(u.hostname, u.port or 80)
    path = f"{u.path}?role=app&channel=host&user={args.tenant}&token={args.key}&app_version=1.0.1.6"
    await wsutil.client_handshake(reader, writer, f"{u.hostname}:{u.port or 80}", path)
    outbox: asyncio.Queue = asyncio.Queue()

    class Worker:
        def is_connected(self):
            return True

        def send_json(self, obj):
            outbox.put_nowait(obj)

    app.host_relay_worker = Worker()
    # the real _run_on_ui_thread posts to the Qt GUI thread, which this bare stand-in does not have: post to its own event loop instead
    _loop = asyncio.get_running_loop()
    app._run_on_ui_thread = lambda fn: _loop.call_soon_threadsafe(fn)
    # the app's own state publisher, bound to the bare app
    app._publish_host_state_ws = types.MethodType(m.KaraokeApp._publish_host_state_ws, app)
    app._on_host_relay_command = types.MethodType(m.KaraokeApp._on_host_relay_command, app)
    app._execute_host_control_command = types.MethodType(m.KaraokeApp._execute_host_control_command, app)
    for name in ("_shell_commands_mode", "_queue_signature", "_shell_queue_summary", "_execute_shell_queue_command", "_queue_singer_index_by_id",
                 "_queue_entry_position_by_id", "shell_queue_move_singer", "shell_queue_move_song", "shell_queue_remove_singer", "shell_queue_remove_song",
                 "shell_queue_set_singer_skipped", "_finish_shell_queue_removal", "toggle_singer_skip", "_step_karaoke_key", "_step_karaoke_tempo"):
        if hasattr(m.KaraokeApp, name):
            setattr(app, name, types.MethodType(getattr(m.KaraokeApp, name), app))
    app._SHELL_QUEUE_ACTIONS = m.KaraokeApp._SHELL_QUEUE_ACTIONS
    app.update_queue_display = lambda: None

    def snapshot():
        return {"order": [s["singer_id"] for s in app.queue], "songs": {s["singer_id"]: [e.get("request_uid") or e.get("display_name") or "?" for e in s["songs"]] for s in app.queue},
                "skipped": {s["singer_id"]: bool(s.get("skipped")) for s in app.queue}, "sig": app._queue_signature(), "played": list(played)}

    async def pump():
        while True:
            obj = await outbox.get()
            await wsutil.send_text(writer, obj, mask=True)

    async def ticker():
        while True:
            app._publish_host_state_ws(force=True)
            await asyncio.sleep(0.4)

    async def receiver():
        while True:
            opcode, payload = await wsutil.read_frame(reader)
            if opcode is None or opcode == wsutil.OP_CLOSE:
                break
            if opcode != wsutil.OP_TEXT:
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            if msg.get("type") == "command":
                emit(event="command", command=msg.get("command"), request_id=msg.get("request_id"), args=msg.get("args"))
                if args.mute:
                    continue
                app._on_host_relay_command(msg)
                emit(event="after", request_id=msg.get("request_id"), **snapshot())

    emit(event="connected", **snapshot())
    try:
        await asyncio.gather(pump(), ticker(), receiver())
    except (ConnectionError, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
