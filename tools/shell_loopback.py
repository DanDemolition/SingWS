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


def run_shell(stack, script, pin=cl.PIN, snapshot=None, snapshot_after=9000, extra_env=None, tab="controls", extra_args=()):
    tmp = Path(tempfile.mkdtemp(prefix="shell_run_"))
    dump = tmp / "view.json"
    args = [SHELL, "--tab", tab, *extra_args, "--control-url", f"http://127.0.0.1:{stack.php_port}", "--control-user", cl.TENANT, "--control-pin", pin,
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


def scenario_the_shell_adds_a_library_song_through_the_relay_and_a_second_add_is_refused():
    """The real Qt shell's act() -> real relay -> the app's REAL add routine (quiet mode) over a throwaway song library."""
    with cl.Stack("apply", library=True) as st:
        ids = st.song_ids
        add = {"singer": "bob new", "song_id": ids["a.mp4"], "key": 2}
        script = [{"label": "Add song", "command": "queue.add_song", "args": add, "nosig": True}, {"label": "Add it again", "command": "queue.add_song", "args": add, "nosig": True}]
        view, p, _ = run_shell(st, script, snapshot_after=8000)
        assert view is not None, p.stderr[-800:]
        cmds = commands_seen(st)
        assert [c["command"] for c in cmds[:2]] == ["queue.add_song", "queue.add_song"], [c["command"] for c in cmds]
        assert cmds[0]["args"] == {"singer": "bob new", "song_id": ids["a.mp4"], "key": 2, "source": "shell"}, cmds[0]["args"]
        first, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == cmds[0]["request_id"], timeout=5)
        second, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == cmds[1]["request_id"], timeout=5)
        assert len(first["order"]) == 4 and sum(len(v) for v in first["songs"].values()) == 6, first  # the 3 sample singers (5 songs) plus Bob and his one song
        assert second["songs"] == first["songs"], "the second add changed nothing"
        assert view["result"]["kind"] == "warn" and "already has this song" in view["result"]["text"], view["result"]
        bob = [s for s in view["singers"] if s["name"] == "Bob New"]
        assert len(bob) == 1 and len(bob[0]["songs"]) == 1, view["singers"]


def scenario_the_shell_changes_a_songs_key_and_speed_and_renames_a_singer_through_the_relay():
    """The Key / Speed / Rename buttons' commands, through the real Qt shell and relay, into the app's REAL dialog routines; a taken name is refused."""
    with cl.Stack("apply") as st:
        view0, p0, _ = run_shell(st, [], snapshot_after=4000)
        assert view0 is not None, p0.stderr[-800:]
        entry = view0["singers"][0]["songs"][0]["entry_id"]
        script = [{"label": "Key", "command": "queue.set_song_key", "args": {"entry_id": entry, "key": -2}},
                  {"label": "Speed", "command": "queue.set_song_tempo", "args": {"entry_id": entry, "tempo_percent": 85}},
                  {"label": "Rename", "command": "queue.rename_singer", "args": {"singer_id": "s1", "name": "Dana Lee"}},
                  {"label": "Rename taken", "command": "queue.rename_singer", "args": {"singer_id": "s2", "name": "dana lee"}}]
        view, p, _ = run_shell(st, script, snapshot_after=9000)
        assert view is not None, p.stderr[-800:]
        names = [c["command"] for c in commands_seen(st)]
        assert names[-4:] == ["queue.set_song_key", "queue.set_song_tempo", "queue.rename_singer", "queue.rename_singer"], names
        last, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == commands_seen(st)[-1]["request_id"], timeout=5)
        assert last["names"]["s1"] == "Dana Lee" and last["names"]["s2"] == "Sam", last["names"]
        assert last["mods"]["s1"][0] == [-2, 85], last["mods"]
        assert view["result"]["kind"] == "warn" and "Another singer" in view["result"]["text"], view["result"]


def upload_frame(stack, active=True, colour=(200, 30, 120), state="playing"):
    """Leave on the loopback server exactly what the app's snapshot producer leaves there (the loopback PHP has no API-key database, so the POST itself is not used):
    snapshot.jpg and snapshot.json in the tenant's show_screen_preview folder, which the real GET endpoints then serve."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (320, 180), colour).save(buf, "JPEG")
    jpeg = buf.getvalue()
    d = stack.tmp / "server" / "tenants" / cl.TENANT / "show_screen_preview"
    d.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    meta = {"active": bool(active), "updated_at": now_ms // 1000, "updated_at_ms": now_ms, "song_id": "t1", "request_id": "r1", "playback_state": state, "warning": ""}
    if active:
        (d / "snapshot.jpg").write_bytes(jpeg)
        meta.update({"image_updated_at": now_ms // 1000, "image_updated_at_ms": now_ms})
    (d / "snapshot.json").write_text(json.dumps(meta))
    return jpeg


def scenario_the_output_preview_shows_the_frame_the_app_uploaded_and_stays_off_until_asked():
    """The real shell polls the real snapshot endpoint with its own login and stores the very bytes the app uploaded; with the switch off it does not ask at all."""
    with cl.Stack("apply") as st:
        jpeg = upload_frame(st)
        view, p, _ = run_shell(st, [], snapshot_after=9000, extra_args=("--preview",))
        assert view is not None, p.stderr[-800:]
        pv = view["preview"]
        assert pv["on"] is True and pv["url"].startswith("file://") and pv["url"].endswith(".jpg"), pv
        data = Path(pv["url"][len("file://"):]).read_bytes()
        assert data == jpeg, "the shell kept exactly the uploaded picture"
        assert pv["bytes"] == len(jpeg) and pv["status"] == "", pv
        meta = json.loads((st.tmp / "server" / "tenants" / cl.TENANT / "show_screen_preview" / "snapshot.json").read_text())
        assert meta.get("daw_viewer_seen_at", 0) > 0, "a watcher was registered, so the app would keep capturing"
        (st.tmp / "server" / "tenants" / cl.TENANT / "show_screen_preview" / "snapshot.json").write_text(json.dumps({k: v for k, v in meta.items() if "viewer" not in k}))
        # switched off (the default): no poll, so no picture and no watcher
        view2, p2, _ = run_shell(st, [], snapshot_after=5000)
        assert view2["preview"]["on"] is False and view2["preview"]["url"] == "" and view2["preview"]["bytes"] == 0, view2["preview"]
        meta2 = json.loads((st.tmp / "server" / "tenants" / cl.TENANT / "show_screen_preview" / "snapshot.json").read_text())
        assert not any("viewer" in k for k in meta2), "with the switch off the shell never asked, so no watcher was registered"


def scenario_the_output_preview_says_so_when_nothing_is_on_the_singer_screen():
    with cl.Stack("apply") as st:
        upload_frame(st, active=False, state="idle")
        view, p, _ = run_shell(st, [], snapshot_after=8000, extra_args=("--preview",))
        assert view is not None, p.stderr[-800:]
        pv = view["preview"]
        assert pv["url"] == "" and pv["status"] == "No active singer screen preview", pv


def scenario_the_music_tab_follows_the_players_position_and_drives_the_background_music():
    """The real shell's Music tab over the real relay: the app says it is on track 2 of 4, so the played tracks hide; play/pause and volume reach the app."""
    import tempfile
    home = Path(tempfile.mkdtemp(prefix="music_home_"))
    (home / "bg_playlist.json").write_text(json.dumps([{"path": f"/m/{n}.mp3", "filename": f"Track {n}.mp3", "title": n} for n in "ABCD"]))
    (home / "settings.json").write_text(json.dumps({"bg_enabled": True, "bg_volume": 0.4}))
    with cl.Stack("apply") as st:
        dump = home / "rows.json"
        script = [{"label": "Background music", "command": "bg_play_pause"}, {"label": "Music volume 70%", "command": "set_bg_volume", "args": {"value": 0.7}}]
        view, p, _ = run_shell(st, script, tab="music", extra_args=("--dump-music", str(dump)), extra_env={"SINGWS_HOME": str(home)}, snapshot_after=9000)
        assert view is not None, p.stderr[-800:]
        assert view["bg"] == {"playing": False, "volume": 0.7, "index": 1, "track": "Track B.mp3"}, view["bg"]
        rows = json.loads(dump.read_text())
        assert [r["filename"] for r in rows] == ["Track C.mp3", "Track D.mp3"], rows  # A and B are at or above the current track (index 1)
        cmds = commands_seen(st)
        assert [c["command"] for c in cmds[:2]] == ["bg_play_pause", "set_bg_volume"], [c["command"] for c in cmds]
        assert cmds[1]["args"]["value"] == 0.7 and cmds[1]["args"]["source"] == "shell"
        final, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == cmds[1]["request_id"], timeout=5)
        assert final["played"] == ["bg_toggle"], final


def scenario_the_shell_changes_the_equaliser_and_the_playing_track_through_the_relay():
    """Real Qt shell -> real relay -> the app's REAL `eq.set` (GraphicEQ, shared live re-attach, settings saved) and `bg.play_track` / `bg.next`; the EQ tab then shows what the
    app saved, read from the settings file exactly as the app wrote it."""
    import tempfile
    home = Path(tempfile.mkdtemp(prefix="eq_home_"))
    settings_file = home / "settings.json"
    (home / "bg_playlist.json").write_text(json.dumps([{"path": f"/m/Track {n}.mp3", "filename": f"Track {n}.mp3", "title": n} for n in "ABCD"]))
    with cl.Stack("apply", settings_file=settings_file) as st:
        dump = home / "eq.json"
        flat_gains = [0.0] * 10
        script = [
            {"label": "Next track", "command": "bg.next", "nosig": True},
            {"label": "Play C", "command": "bg.play_track", "args": {"index": 2, "track": "Track C.mp3"}, "nosig": True},
            {"label": "Play with the wrong name", "command": "bg.play_track", "args": {"index": 3, "track": "Track A.mp3"}, "nosig": True},
            {"label": "Karaoke EQ", "command": "eq.set", "args": {"target": "karaoke", "gains": [-3, -3, -2, -1, 1, 3, 4, 3, 2, 0.04], "enabled": True}, "nosig": True},
            {"label": "Music EQ flat and off", "command": "eq.set", "args": {"target": "bgm", "gains": flat_gains, "enabled": False}, "nosig": True},
        ]
        view, p, _ = run_shell(st, script, tab="eq", extra_args=("--dump-eq", str(dump)), extra_env={"SINGWS_HOME": str(home)}, snapshot_after=11000)
        assert view is not None, p.stderr[-800:]
        cmds = commands_seen(st)
        assert [c["command"] for c in cmds[:5]] == ["bg.next", "bg.play_track", "bg.play_track", "eq.set", "eq.set"], [c["command"] for c in cmds]
        final, _ = st.app_events.wait_for(lambda e: e.get("event") == "after" and e["request_id"] == cmds[4]["request_id"], timeout=5)
        assert final["played"][:3] == ["bg_next", "bg_stop", "bg_play:2"], final["played"]  # the wrong-name play was refused: nothing after bg_play:2 until the EQ pushes
        assert "mpv_push:shell_eq" in final["played"] and "bass_set_eq" in final["played"], final["played"]
        saved = json.loads(settings_file.read_text())
        assert saved["eq_karaoke"] == [-3.0, -3.0, -2.0, -1.0, 1.0, 3.0, 4.0, 3.0, 2.0, 0.0] and saved["eq_karaoke_enabled"] is True, saved
        assert saved["eq_bgm"] == flat_gains and saved["eq_bgm_enabled"] is False, saved
        shown = json.loads(dump.read_text())
        assert shown["karaoke"]["enabled"] and shown["karaoke"]["gains"][0] == -3.0, shown["karaoke"]
        assert shown["bgm"]["flat"] and not shown["bgm"]["enabled"], shown["bgm"]
        assert view["bg"]["index"] == 2 and view["bg"]["track"] == "Track C.mp3", view["bg"]
        assert view["result"]["kind"] == "ok", view["result"]  # the last command (EQ) succeeded


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


def scenario_the_volume_and_seek_commands_reach_the_app_with_their_values():
    """The Controls tab's sliders call act() with these exact commands; they must arrive validated, tagged `shell`, with no list fingerprint."""
    with cl.Stack("apply") as st:
        script = [
            {"label": "Karaoke volume 50%", "command": "set_karaoke_volume", "args": {"value": 0.5}},
            {"label": "Music volume 30%", "command": "set_bg_volume", "args": {"value": 0.3}},
            {"label": "Seek to 0:12", "command": "seek_to", "args": {"seconds": 12.0}},
        ]
        view, p, _ = run_shell(st, script)
        assert view is not None, p.stderr[-800:]
        cmds = commands_seen(st)
        assert [c["command"] for c in cmds[:3]] == ["set_karaoke_volume", "set_bg_volume", "seek_to"], [c["command"] for c in cmds]
        assert cmds[0]["args"]["value"] == 0.5 and cmds[1]["args"]["value"] == 0.3 and cmds[2]["args"]["seconds"] == 12.0, cmds[:3]
        for c in cmds[:3]:
            assert c["args"]["source"] == "shell" and "expect_queue_sig" not in c["args"], c


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
