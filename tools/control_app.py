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
    ap.add_argument("--settings-file", default=None, help="write the app's settings here whenever it saves them (the shell's EQ and Music tabs read this file)")
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
    # the "Waiting for add" page's state: three waiting requests; the app's REAL waitlist methods run on it (bound below), the server calls are recorded
    waitlist_events = []
    app._waiting_for_add_requests = {i: {"request_id": i, "singer": n, "artist": "Queen", "title": f"Song {i}", "state": "waiting", "pending_reason": "rotation_full"}
                                     for i, n in ((7, "Ann"), (8, "Bo"), (9, "Cy"))}
    app._waiting_for_add_removed_tonight = {}
    app._waiting_for_add_handled_ids = set()
    app._remote_request_tombstones = {"requests": {}}
    app._remote_removed_request_ids = set()
    app._remote_attention_requests = {}
    app._pending_remote_order_syncs = {}
    app._queue_remote_request_ids = lambda: []
    app._accepted_request_identity_sets = lambda: (set(), set(), set())
    app._request_matches_accepted_queue_item = lambda req, identity_sets=None: False
    app._save_waitlist_removed_tonight = lambda: waitlist_events.append("save_removed")
    app._schedule_waiting_for_add_view_refresh = lambda delay_ms=None, reason="": waitlist_events.append(f"refresh:{reason}")
    app._show_processing_notification = lambda text, level="info": None
    app._waiting_for_add_failed_add = lambda req: False
    app._delete_remote_request = lambda rid, entry=None, singer_name="", reason="": waitlist_events.append(f"delete_remote:{rid}:{reason}")
    app._restore_waitlist_request_on_server_async = lambda req, revision: waitlist_events.append(f"server_restore:{req.get('request_id')}")
    app._drop_remote_request_tombstone = lambda rid, reason="": None
    app._mark_waiting_for_add_delivered_async = lambda rid, req=None: waitlist_events.append(f"delivered:{rid}")
    app._add_waiting_for_add_track = lambda req, track: False
    app.process_external_request = lambda req: (waitlist_events.append(f"add_to_rotation:{req.get('request_id')}"), True)[1]
    app._push_remote_request_replacement = lambda entry, **k: waitlist_events.append(f"push:{entry.get('remote_request_id')}:{entry.get('title')}:{entry.get('key')}:{entry.get('tempo_percent')}")
    history_commits = []
    app.singer_history = {"singers": {"ann": {"name": "Ann", "songs": {"queen|radio ga ga": {"artist": "Queen", "title": "Radio Ga Ga", "disc_id": "SC1", "song_type": "CDG",
                                                                                      "last_key": 0, "last_tempo_percent": 100, "play_count": 2}}}}}
    app.karaoke_playing = False
    app._commit_singer_history_change = lambda reason="": history_commits.append(reason)
    app.toggle_karaoke_pause = lambda: played.append("pause")
    app.play_next_file = lambda skip_confirmation=False: played.append("next")
    app.stop_and_clear_now_singing = lambda skip_confirmation=False: played.append("stop")
    bg = {"playing": True, "volume": 0.4, "index": 1, "track": "Track B.mp3"}
    tracks = ["/m/Track A.mp3", "/m/Track B.mp3", "/m/Track C.mp3", "/m/Track D.mp3"]
    app._bg_main_toggle_playback = lambda: (bg.update(playing=not bg["playing"]), played.append("bg_toggle"))

    def _bg_select(i):
        bg.update(index=i, track=tracks[i].rsplit("/", 1)[1])

    player = types.SimpleNamespace(set_volume=lambda v: bg.update(volume=v), playlist=tracks, current_index=1, is_playing=True)
    player.next_track = lambda: (_bg_select(min(player.current_index + 1, len(tracks) - 1)), setattr(player, "current_index", bg["index"]), played.append("bg_next"))
    player.stop = lambda: played.append("bg_stop")
    player.play = lambda: (_bg_select(player.current_index), played.append(f"bg_play:{player.current_index}"))
    player._bgm_engine = types.SimpleNamespace(set_eq=lambda eq: played.append("bass_set_eq"))
    app.bg_music = player
    app.update_bg_button_state = lambda: None
    app.update_bg_track_display = lambda: None
    app.highlight_current_track = lambda: None
    # the equalisers are the app's REAL GraphicEQ objects; saving settings writes the file the shell's tabs read
    app.settings.update({"simple_audio_mode": False, "eq_bgm": [5.0, 4.0, 3.0, 1.0, -1.0, -1.0, 1.0, 3.0, 4.0, 5.0], "eq_bgm_enabled": True, "eq_karaoke": [], "eq_karaoke_enabled": False})
    app.karaoke_eq = None
    app.bgm_eq = None
    app._push_mpv_audio_processing = lambda reason="update", audio_path="": played.append(f"mpv_push:{reason}")
    app._log_bgm_eq_route = lambda reason: None
    app._ensure_eq_engines()
    played.clear()
    if args.settings_file:
        def _save():
            Path(args.settings_file).write_text(json.dumps(app.settings))
        app.save_settings = _save
        _save()
    else:
        app.save_settings = lambda: None
    app._host_control_state = lambda: {"rotation": {"last": {}, "current": {}, "next": {}},
                                       "playback": {"is_playing": False, "position_seconds": 0.0, "duration_seconds": 0.0, "remaining_seconds": 0.0},
                                       "bg_playing": bg["playing"], "bg_volume": bg["volume"], "bg_index": bg["index"], "bg_track": bg["track"]}
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
                 "shell_queue_set_singer_skipped", "_finish_shell_queue_removal", "toggle_singer_skip", "_step_karaoke_key", "_step_karaoke_tempo",
                 "shell_queue_set_song_modifier", "shell_queue_rename_singer", "_queue_song_entry",
                 "shell_queue_set_singer_brand", "shell_queue_message_singer", "shell_queue_replace_song", "_set_singer_preference", "_clear_singer_preference",
                 "_singer_disc_priority_override", "_normalize_singer_pref_key", "clear_singer_brand_override", "_replace_queue_song_with_track",
                 "_build_queue_entry_from_track_choice", "_waiting_for_add_song_data_from_track",
                 "shell_waitlist_change", "_waitlist_apply_edit", "_update_waiting_for_add_request", "shell_history_song", "_history_song_apply_edit", "_history_edit_backup",
                 "_normalize_history_song_key", "_waitlist_remove_request", "_waitlist_restore_request", "_waitlist_add_request", "_archive_waitlist_removed_tonight",
                 "_waiting_for_add_clearable_requests", "_waiting_for_add_request_id", "_waiting_for_add_order_key"):
        if hasattr(m.KaraokeApp, name):
            setattr(app, name, types.MethodType(getattr(m.KaraokeApp, name), app))
    # the waitlist summary the phone page shows: the app's real row builder and sections over the stand-in's state
    for name in dir(m.KaraokeApp):
        if name.startswith(("_waiting_for_add_", "_pending_acceptance", "_format_waiting", "_is_waiting_for_add", "_remote_request_is_non_actionable")) and not hasattr(app, name):
            setattr(app, name, types.MethodType(getattr(m.KaraokeApp, name), app))
    app._shell_waitlist_summary = types.MethodType(m.KaraokeApp._shell_waitlist_summary, app)
    # settings.set: the app's real schema and routine; the ticker refresh is recorded
    app._SHELL_SETTINGS_SCHEMA = m.KaraokeApp._SHELL_SETTINGS_SCHEMA
    app._shell_setting_value_ok = m.KaraokeApp._shell_setting_value_ok
    app.shell_settings_set = types.MethodType(m.KaraokeApp.shell_settings_set, app)
    app._SHELL_SETTINGS_QUEUE_REFRESH = m.KaraokeApp._SHELL_SETTINGS_QUEUE_REFRESH
    queue_display_updates = []
    app.update_queue_display = lambda: queue_display_updates.append(1)
    ticker_updates = []
    _prev_ticker_update = getattr(app, "schedule_ticker_update", None)
    app.schedule_ticker_update = lambda: ticker_updates.append(1)
    app.settings.update({"ticker_custom_enabled": False, "ticker_custom_message": "", "api_key": "KEEP-ME"})
    app._is_first_time_waitlist_singer = lambda name: name == "Ann"
    app._deferred_remote_request_ids = lambda: set()
    app._SHELL_QUEUE_ACTIONS = m.KaraokeApp._SHELL_QUEUE_ACTIONS
    # the dialogs' own plumbing is stubbed (server push, persistence, the real rename has its own tests); the new key / speed / name stay in the queue so the loopback can read them
    app._queue_limit_name_key = m.KaraokeApp._queue_limit_name_key  # a static method: not bound
    app._karaoke_pitch_supported = True
    app._push_remote_request_modifiers = lambda entry: None
    app._persist_song_modifier_change = lambda message="": None

    def _rename(idx, name, source="host", merge_prefs_history=True):
        app.queue[idx]["name"] = name
        return True

    app._rename_rotation_singer = _rename
    # brand priority, direct message and replace: the stand-in keeps the preference store, "sends" messages into a list and skips the server pushes
    app.singer_preferences = {}
    app._get_singer_history_record = lambda *a, **k: None
    app._shell_recent_messages = None
    app.settings.update({"base_url": "https://example.test", "user": args.tenant, "api_key": "k"})
    sent_messages = []
    app._net_send_direct_message = lambda name, text, system_notice=False: (sent_messages.append([name, text]), (True, ""))[1]
    pass  # _push_remote_request_replacement is recorded above
    app._schedule_next_up_prescan = lambda: None
    app.post_rotation = lambda: None
    if not hasattr(app, "save_data") or args.library is None:
        app.save_data = getattr(app, "save_data", lambda: None)
    app.update_queue_display = lambda: queue_display_updates.append(1)

    def snapshot():
        return {"order": [s["singer_id"] for s in app.queue], "songs": {s["singer_id"]: [e.get("request_uid") or e.get("display_name") or "?" for e in s["songs"]] for s in app.queue},
                "skipped": {s["singer_id"]: bool(s.get("skipped")) for s in app.queue},
                "names": {s["singer_id"]: s["name"] for s in app.queue}, "brands": {s["singer_id"]: s.get("preferred_disc_priority") for s in app.queue},
                "titles": {s["singer_id"]: [str(e.get("title") or e.get("song_info")) for e in s["songs"]] for s in app.queue}, "messages": list(sent_messages), "mods": {s["singer_id"]: [[e.get("key"), e.get("tempo_percent")] for e in s["songs"]] for s in app.queue}, "sig": app._queue_signature(), "played": list(played),
                "ticker_updates": len(ticker_updates), "queue_display_updates": len(queue_display_updates), "eta": {"all": app.settings.get("rotation_estimate_include_all_songs"), "pad": app.settings.get("rotation_padding_sec")}, "ticker_settings": {"enabled": app.settings.get("ticker_custom_enabled"), "message": app.settings.get("ticker_custom_message"), "api_key": app.settings.get("api_key")},
                "history": {"commits": list(history_commits), "songs": {k: [v["title"], v["last_key"], v["last_tempo_percent"]] for k, v in app.singer_history["singers"]["ann"]["songs"].items()}}, "waitlist": {"waiting": sorted(app._waiting_for_add_requests), "removed": sorted(app._waiting_for_add_removed_tonight), "events": list(waitlist_events)}}

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
