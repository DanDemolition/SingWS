#!/usr/bin/env python3
"""The REAL Rust shell (Qt Quick, offscreen) against the loopback stack of tools/control_loopback.py: real PHP login/token, the reference relay, and the app's real
command code. The Controls tab is driven through its own `act()` function (the one every button calls) with `--do`, and the final view is dumped with `--dump-view`.

Run with the app's venv Python after building the shell:  PATH=/opt/homebrew/opt/qt/bin:$PATH QMAKE=/opt/homebrew/opt/qt/bin/qmake6 cargo build -p singws-shell
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control_loopback as cl  # noqa: E402

SHELL = os.environ.get("SINGWS_SHELL_BIN") or os.path.expanduser("~/.cache/singws-rust-target/debug/singws-shell")


def run_shell(stack, script, pin=cl.PIN, snapshot=None, snapshot_after=9000, extra_env=None):
    tmp = Path(tempfile.mkdtemp(prefix="shell_run_"))
    dump = tmp / "view.json"
    args = [SHELL, "--tab", "controls", "--control-url", f"http://127.0.0.1:{stack.php_port}", "--control-user", cl.TENANT, "--control-pin", pin,
            "--do", json.dumps(script), "--dump-view", str(dump), "--snapshot", str(snapshot or tmp / "shot.png"), "--snapshot-after", str(snapshot_after)]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", SINGWS_SHELL_CONFIG_DIR=str(tmp / "cfg"))
    env.update(extra_env or {})
    p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
    view = json.loads(dump.read_text()) if dump.exists() else None
    return view, p, tmp


def commands_seen(stack):
    stack.app_events.drain()
    return [e for e in stack.app_events.seen if e.get("event") == "command"]


def scenario_the_shell_edits_the_rotation_through_its_own_buttons_code():
    shot = Path(__file__).resolve().parent.parent / "docs/verification/2026-10-24-rust-shell-controls.png"
    with cl.Stack("apply") as st:
        script = [
            {"label": "Move Dana down", "command": "queue.move_singer", "args": {"singer_id": "s1", "direction": "down"}},
            {"label": "Skip Sam", "command": "queue.skip_singer", "args": {"singer_id": "s2", "skipped": True}},
            {"label": "Move song up", "command": "queue.move_song", "args": {"entry_id": "local:uid5", "direction": "up"}},
            {"label": "Remove song", "command": "queue.remove_song", "args": {"entry_id": "local:uid3"}},
            {"label": "Play/pause", "command": "play_pause"},
        ]
        view, p, tmp = run_shell(st, script, snapshot=shot)
        assert view is not None, p.stderr[-800:]
        assert view["enabled"] and view["connected"] and view["app_online"], view["status"]
        assert [s["singer_id"] for s in view["singers"]] == ["s2", "s1", "s3"], view["singers"]
        by = {s["singer_id"]: s for s in view["singers"]}
        assert by["s2"]["skipped"] is True and by["s2"]["songs"] == []
        assert [y["entry_id"] for y in by["s3"]["songs"]] == ["local:uid5", "local:uid4"]
        assert view["result"]["text"].startswith("Play/pause:") and view["result"]["kind"] == "ok", view["result"]
        cmds = commands_seen(st)
        assert [c["command"] for c in cmds[:5]] == ["queue.move_singer", "queue.skip_singer", "queue.move_song", "queue.remove_song", "play_pause"], [c["command"] for c in cmds]
        for c in cmds[:5]:
            assert c["args"]["source"] == "shell"
        for c in cmds[:4]:
            assert len(c["args"]["expect_queue_sig"]) == 16, c["args"]
        assert "expect_queue_sig" not in cmds[4]["args"], "transport commands carry no list fingerprint"
        final, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == cmds[4]["request_id"], timeout=5)
        assert final["order"] == ["s2", "s1", "s3"] and final["played"] == ["next"], final  # play_pause with nothing playing starts the next song
        assert shot.exists() and shot.stat().st_size > 5000
        # the PIN went nowhere: not into the saved connection file, not into any output
        saved = (tmp / "cfg" / "connection.json").read_text()
        assert cl.PIN not in saved and json.loads(saved) == {"url": f"http://127.0.0.1:{st.php_port}", "user": cl.TENANT}
        assert cl.PIN not in p.stdout + p.stderr and cl.PIN not in json.dumps(view)


def scenario_a_stale_edit_is_refused_then_the_shell_refreshes_itself():
    with cl.Stack("apply") as st:
        script = [{"label": "Move Lee to the top", "command": "queue.move_singer", "args": {"singer_id": "s3", "direction": "top"}, "sig": "0000000000000000"}]
        view, p, _ = run_shell(st, script, snapshot_after=7000)
        assert view is not None, p.stderr[-800:]
        assert view["result"]["kind"] == "warn" and "refreshed" in view["result"]["text"], view["result"]
        assert [s["singer_id"] for s in view["singers"]] == ["s1", "s2", "s3"], "nothing moved"
        names = [c["command"] for c in commands_seen(st)]
        assert names[:2] == ["queue.move_singer", "queue.noop"], names  # the refusal triggered exactly one refresh request


def scenario_dry_run_shows_a_dry_run_and_changes_nothing():
    with cl.Stack("dry_run") as st:
        script = [{"label": "Remove Dana", "command": "queue.remove_singer", "args": {"singer_id": "s1"}},
                  {"label": "Skip", "command": "skip"}]
        view, p, _ = run_shell(st, script, snapshot_after=7000)
        assert view is not None, p.stderr[-800:]
        assert [s["singer_id"] for s in view["singers"]] == ["s1", "s2", "s3"]
        assert view["result"]["kind"] == "warn" and "changed nothing" in view["result"]["text"], view["result"]
        final, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["played"] == [], timeout=5)
        assert final["order"] == ["s1", "s2", "s3"] and final["played"] == []


def scenario_a_wrong_pin_shows_the_refusal_and_enables_nothing():
    with cl.Stack("apply") as st:
        view, p, _ = run_shell(st, [], pin="9999", snapshot_after=6000)
        assert view is not None, p.stderr[-800:]
        assert view["enabled"] is False and view["connected"] is False
        assert "PIN" in view["login_error"], view
        assert not commands_seen(st)


def scenario_without_the_app_the_controls_stay_disabled():
    with cl.Stack("apply", start_app=False) as st:
        view, p, _ = run_shell(st, [], snapshot_after=6000)
        assert view is not None, p.stderr[-800:]
        assert view["connected"] is True and view["app_online"] is False and view["enabled"] is False, view
        assert view["singers"] == []


SCENARIOS = [v for k, v in sorted(globals().items()) if k.startswith("scenario_")]


def main():
    if not cl.PHP or not os.path.exists(cl.CLI) or not os.path.exists(SHELL):
        print("needs php, singws-control-cli and the built singws-shell", file=sys.stderr)
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
    print("RESULT: ALL SHELL SCENARIOS PASSED" if not failed else f"RESULT: {failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
