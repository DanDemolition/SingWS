#!/usr/bin/env python3
"""Parity of the Rust host relay message builders (rust/crates/singws-relaymsg) against the app's REAL Python.

Oracle = the app module itself: the REAL `KaraokeApp._publish_host_state_ws` runs on a stub object whose message helpers (`_shell_queue_summary`,
`_queue_signature`, `_shell_waitlist_summary`, `_shell_chat_summary`, `_shell_chat_message` and the `_room_*` / `_host_chat_*` helpers) are the app's own
methods; only the things that need the live window (the control state, the waitlist page's row builder, the library lookup) are stubs fed from the corpus.
The inputs for the Rust helper are built by the PRODUCTION snapshot code (`relay_msg_shadow.Recorder.requests`), and compared with the PRODUCTION comparator
(`relay_msg_shadow.compare`: key order, key set and value TYPES, so 1 vs 1.0 vs True differ). Corpus:
  1. handwritten states (empty, one singer, many singers, skipped singers, > 200 singers / > 25 songs, waitlist, chat summaries with attention counts,
     unicode names, KaraFun streaming rows, long strings, hostile values);
  2. seeded random states (--cases N);
  3. a read-only COPY of the real queue.json (copied to a scratch dir; reported by counts only, never by content).
Needs the CLI:  cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-relay cargo build --release -p singws-relaymsg
  ~/Documents/SingWS/.venv/bin/python tools/relay_msg_parity.py [--cases N] [--seed S] [--no-real]       exit 1 on any difference
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import random
import shutil
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
_HOME = tempfile.mkdtemp(prefix="relay_msg_parity_home_")
os.environ["SINGWS_HOME"] = _HOME
sys.path.insert(0, str(ROOT))

CLI = os.environ.get("SINGWS_RELAYMSG_BIN") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-relay")), "release", "singws-relaymsg")
os.environ["SINGWS_RELAYMSG_BIN"] = CLI

failures: list[str] = []
counts: dict[str, int] = {}


def fail(msg):
    failures.append(msg)
    if len(failures) <= 30:
        print("DIFF:", msg[:600])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def load_app():
    spec = importlib.util.spec_from_file_location("singws_main_relaymsg_parity", str(ROOT / "0.2.18.1.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


APP = load_app()
KA = APP.KaraokeApp
import relay_msg_shadow as shadow  # noqa: E402

METHODS = ("_publish_host_state_ws", "_shell_queue_summary", "_queue_signature", "_shell_waitlist_summary", "_shell_chat_summary", "_shell_chat_message",
           "_room_thread_keys", "_room_display_name", "_chat_conversations", "_host_chat_media_rows", "_host_chat_media_unread", "_host_chat_singer_name",
           "_queue_entry_artist_title", "_queue_entry_primary_path", "_queue_entry_song_info", "_split_display_artist_title_disc")
CONSTANTS = ("_SHELL_CHAT_TEXT_MAX", "_SHELL_CHAT_GROUP_ROWS", "_SHELL_CHAT_THREADS", "_SHELL_CHAT_THREAD_ROWS")


class Worker:
    def __init__(self):
        self.sent = []

    def is_connected(self):
        return True

    def send_json(self, msg):
        self.sent.append(copy.deepcopy(msg))


class Stub:
    _ensure_singer_id = staticmethod(KA._ensure_singer_id)
    _ensure_queue_entry_id = staticmethod(KA._ensure_queue_entry_id)

    def lookup_display_name(self, path, artist_title_only=False):
        return f"Lib Artist {len(str(path)) % 7} - Lib Title {len(str(path)) % 5}"

    def _shell_commands_mode(self):
        return self.mode

    def _host_control_state(self):
        if isinstance(self.control, Exception):
            raise self.control
        return self.control

    def _waiting_for_add_sections(self):
        if isinstance(self.sections, Exception):
            raise self.sections
        return self.sections

    def _waiting_for_add_row(self, req, kind, selectable):
        if req.get("explode"):
            raise ValueError("boom")
        row = dict(req.get("row", {}))
        row.setdefault("status_label", "Removed")
        return row


for _n in METHODS:
    setattr(Stub, _n, getattr(KA, _n))
for _n in CONSTANTS:
    setattr(Stub, _n, getattr(KA, _n))


def make_app(queue, settings, control, room=None, chat=(), sections=(), removed=None, mode="dry_run", attention=(), reachable=True, room_settings=None, new=None, media_read=()):
    a = Stub()
    a.queue = queue
    a.settings = settings
    a.control = control
    a.mode = mode
    a.sections = list(sections) if not isinstance(sections, Exception) else sections
    a._waiting_for_add_removed_tonight = removed if removed is not None else {}
    a._room_messages = dict(room or {})
    a._chat_messages = list(chat)
    a._room_attention_ids = set(attention)
    a._room_reachable = reachable
    a._room_settings = room_settings if room_settings is not None else {"group_enabled": True, "dm_enabled": True}
    a._room_new_by_channel = new if new is not None else {"group": 2, "dm": 1}
    a._host_media_read = set(media_read)
    a.host_relay_worker = Worker()
    return a


def run_case(label, **kw):
    """One publish through the REAL `_publish_host_state_ws`; the production recorder collects its messages; Rust rebuilds them."""
    app = make_app(**kw)
    captured = {}
    orig = shadow.finish
    shadow.finish = lambda rec, state, version, log: captured.update(rec=rec, state=state, version=version)
    shadow._last_started = 0.0
    shadow._helper_cache[:] = [Path(CLI)]
    try:
        app._publish_host_state_ws(force=True)
    finally:
        shadow.finish = orig
    rec = captured.get("rec")
    sent = app.host_relay_worker.sent
    if rec is None:
        if sent:
            fail(f"{label}: recorder did not start")
        return
    if len(rec.sent) != len(sent):
        fail(f"{label}: recorder saw {len(rec.sent)} of {len(sent)} messages")
    try:
        batch = rec.requests(captured["state"], captured["version"])
    except shadow.Unsnapshottable:
        tally("snapshot_skipped")
        return
    answers = shadow.run_helper(Path(CLI), [r for _k, r, _e in batch])
    for (kind, _req, expected), rust in zip(batch, answers):
        tally(f"msg:{kind}")
        if isinstance(rust, dict) and "unsupported" in rust:
            tally(f"unsupported:{kind}")
            fail(f"{label}: {kind} unsupported ({rust['unsupported']})") if not kw.get("allow_unsupported") else None
            continue
        d = shadow.compare(kind, expected, rust)
        if d:
            fail(f"{label}: {d}")
        else:
            tally("identical")


# ---------------------------------------------------------------- corpus

NAMES = ["Ann", "Bob", "Cé", "İstanbul", "Straße", "ǅ", "Σίσυφος", "名前", "😀 Emoji", "x" * 300, "", "  pad  ", "O'Neil \"Q\"", "line\nbreak", "tab\t"]
PLAYBACK = {"title": "T", "artist": "A", "position_seconds": 12.345, "duration_seconds": 200.0, "remaining_seconds": 187.655, "is_playing": True, "key": 2,
            "tempo": 1.05, "bgm_volume": 0.8, "karaoke_volume": 1.0}


def rotation(rng):
    return {"last": {"singer": rng.choice(NAMES), "title": ""}, "current": {"singer": rng.choice(NAMES), "title": "x"}, "next": {"singer": "", "title": ""}}


def control(rng):
    pb = dict(PLAYBACK)
    for k in list(pb):
        if rng.random() < 0.1:
            del pb[k]
    st = {"rotation": rotation(rng), "playback": pb, "bg_playing": rng.choice([True, False, 1, 0, None]), "bg_volume": rng.choice([0.0, 0.8, 1.0, 0.123456789]),
          "bg_index": rng.choice([-1, 0, 5]), "bg_track": rng.choice(["", "a.mp3", "Ünï.mp3"])}
    for k in ("rotation", "bg_playing", "bg_volume", "bg_index", "bg_track"):
        if rng.random() < 0.08:
            del st[k]
    if rng.random() < 0.05:
        del st["playback"]
    return st


def entry(rng, i):
    r = rng.random()
    if r < 0.12:
        return ("/lib/some song.zip", 0)  # tuple entry: not a dict
    e = {"request_uid": f"u{i}-{rng.randrange(10**6)}"}
    if rng.random() < 0.8:
        e["artist"], e["title"] = rng.choice(NAMES) or "A", rng.choice(NAMES) or "T"
    if rng.random() < 0.3:
        e["song_info"] = [f"/lib/{rng.choice(NAMES)}.zip"]
    if rng.random() < 0.25:
        e["remote_request_id"] = rng.choice([123456, "77", 0, "0", None])
    if rng.random() < 0.15:
        e["skipped"] = rng.choice([True, False, 1, 0, "yes"])
    if rng.random() < 0.1:
        e["song_info"] = ["karafun_streaming:12345"]
        e["artist"], e["title"] = "KaraFun Artist", "KaraFun Title"
    return e


def singer(rng, i, songs):
    if rng.random() < 0.03:
        return "not a dict"
    s = {"name": rng.choice(NAMES + [None, 5, 7.5])}
    if rng.random() < 0.8:
        s["singer_id"] = f"sg{i}-{rng.randrange(10**6)}"
    for k in ("skipped", "rotation_marker"):
        if rng.random() < 0.2:
            s[k] = rng.choice([True, False, 1, 0, "x"])
    s["songs"] = [entry(rng, f"{i}.{j}") for j in range(songs)]
    return s


def queue(rng, n=None):
    n = rng.choice([0, 1, 2, 5, 12, 40]) if n is None else n
    return [singer(rng, i, rng.choice([0, 1, 2, 3, 6])) for i in range(n)]


def wait_row(rng):
    return {"request_id": rng.choice([1, 2, 99, "12", None, 0, True, 3.9, " 4 "]), "singer": rng.choice(NAMES + [None]), "artist": rng.choice(NAMES), "title": rng.choice(NAMES),
            "status_label": rng.choice(["Waiting", "", None, "Queued", 3]), "first_time_singer": rng.choice([True, False, 1, 0, None])}


def waitlist(rng):
    rows = [wait_row(rng) for _ in range(rng.choice([0, 1, 3, 45]))]
    sections = [{"key": "other", "rows": [wait_row(rng)]}, {"key": "waitlist", "rows": rows}]
    removed = {}
    for i in range(rng.choice([0, 1, 4, 25])):
        removed[f"r{i}"] = {"removed_at": rng.choice([100, 200.5, "300", None, 0, "bad", 50]), "row": wait_row(rng)}
    if rng.random() < 0.1:
        removed["bad"] = "not a dict"
    return sections, removed


def gm(i, text="hi", frm="Ann", **kw):
    return dict({"id": i, "channel": "group", "from": frm, "from_key": frm.lower(), "message": text, "created_at": 1000 + i, "status": "visible"}, **kw)


GIFS = [{"id": "g-1_x", "preview": "https://media.giphy.com/x.gif"}, {"id": "../etc", "preview": "https://media.giphy.com/x.gif"}, {"id": "ok\n", "preview": "https://i.giphy.com/x"},
        {"id": "a", "preview": "http://evil.example/x.gif"}, {"id": "b", "preview": "https://media3.giphy.com/x"}, {}, "nope", {"id": "c" * 70, "preview": "https://media.giphy.com/y"}]


def room_msg(rng, i):
    ch = rng.choice(["group", "group", "dm", "dm", "host"])
    name = rng.choice(["Ann", "Bob", "Cé", "STRASSE", "straße", "Ǆ", "İ", "Σ", "名前"])
    other = rng.choice(["Ann", "Bob", "Cé", "STRASSE", "straße"])
    m = {"id": i, "channel": ch, "from": name, "from_key": name.casefold(), "to_key": other.casefold(), "message": rng.choice(["hi", "x" * 200, "", "ünï", None]),
         "created_at": 1000 + rng.randrange(40), "status": rng.choice(["visible", "held", "removed", "blocked", "weird", ""]), "reports": rng.choice([0, 0, 2, "3", None]),
         "flag": rng.choice(["", "profanity", "z" * 40])}
    if rng.random() < 0.25:
        m["host"] = True
    if rng.random() < 0.3:
        m["kind"] = rng.choice(["image", "gif", "text"])
        m["media_id"] = rng.choice([0, 77, "5", None])
        m["gif"] = rng.choice(GIFS)
    if ch == "dm" and rng.random() < 0.2:
        m["from_key"] = "@host"
        m["from"] = "Host"
        m["thread"] = f"{name.casefold()}|{other.casefold()}"
    if ch == "host":
        m["thread"] = rng.choice(["Ann", "ann", "BOB", "straße", "STRASSE", "ǅ", "Cé"])
    return m


def host_msg(rng, i):
    name = rng.choice(["Ann", "Bob", "Cé", "STRASSE", "straße", "ǅ", ""])
    m = {"id": 5000 + i, "singer": name, "direction": rng.choice(["in", "out"]), "read": rng.choice([True, False, 0]), "message": rng.choice(["yo", "z" * 150]),
         "created_at": 2000 + rng.randrange(30), "status": rng.choice(["", "removed"])}
    if rng.random() < 0.4:
        m["singer_key"] = name.upper()
    return m


def chatstate(rng):
    n = rng.choice([0, 1, 4, 30, 80])
    ids = list(range(1, n + 1))
    rng.shuffle(ids)
    room = {i: room_msg(rng, i) for i in ids}
    chat = [host_msg(rng, i) for i in range(rng.choice([0, 1, 6, 40]))]
    attention = rng.sample(ids, min(len(ids), rng.choice([0, 1, 3, 9]))) if ids else []
    if rng.random() < 0.2:
        attention.append(99999)  # an id no longer in the room
    return dict(room=room, chat=chat, attention=attention, media_read=rng.sample(ids, min(len(ids), 3)) if ids else [],
                reachable=rng.choice([True, False, None]), room_settings=rng.choice([{"group_enabled": True, "dm_enabled": False}, {}, {"dm_enabled": 1}, None]),
                new=rng.choice([None, {}, {"group": 0}, {"group": "4", "dm": 2.0}]))


def settings_for(rng):
    s = {}
    r = rng.random()
    if r < 0.6:
        s["user"] = rng.choice(["wsk", "Üser", ""])
    if r > 0.4:
        s["tenant"] = "tenant1"
    if rng.random() < 0.5:
        s["host_chat_name"] = rng.choice(["DJ Dan", "", None, "Host", 7])
    return s


def random_case(rng, i):
    sections, removed = waitlist(rng)
    return dict(label=f"random#{i}", queue=queue(rng), settings=settings_for(rng), control=control(rng), sections=sections, removed=removed, **chatstate(rng))


def handwritten():
    base = dict(queue=[], settings={"user": "wsk"}, control={"rotation": {"last": {}, "current": {}, "next": {}}, "playback": dict(PLAYBACK), "bg_playing": False, "bg_volume": 0.0,
                                                           "bg_index": -1, "bg_track": ""})
    yield dict(base, label="empty")
    yield dict(base, label="shell-commands-off", mode="off")
    yield dict(base, label="control-state-raises", control=RuntimeError("x"))
    yield dict(base, label="waitlist-sections-raise", sections=RuntimeError("x"), queue=[{"name": "A", "singer_id": "s", "songs": []}])
    one = {"name": "Ann", "singer_id": "s1", "songs": [{"request_uid": "e1", "artist": "A", "title": "T"}]}
    yield dict(base, label="one-singer", queue=[one])
    yield dict(base, label="unicode-names", queue=[{"name": n, "singer_id": f"s{i}", "songs": [{"request_uid": f"e{i}", "artist": n, "title": n}]} for i, n in enumerate(NAMES)])
    rng = random.Random(1)
    yield dict(base, label="205-singers-30-songs", queue=[{"name": f"S{i}", "singer_id": f"s{i}", "skipped": i % 7 == 0, "rotation_marker": i % 11 == 0,
                                                            "songs": [{"request_uid": f"e{i}.{j}", "artist": "A", "title": "T", "skipped": j % 5 == 0} for j in range(30)]} for i in range(205)])
    yield dict(base, label="karafun", queue=[{"name": "K", "singer_id": "k", "songs": [{"remote_request_id": 55, "song_info": ["karafun_streaming:99"], "artist": "KF A", "title": "KF T"}]}])
    yield dict(base, label="long-strings", queue=[{"name": "n" * 5000, "singer_id": "s", "songs": [{"request_uid": "e", "artist": "a" * 5000, "title": "t" * 5000}]}])
    sections = [{"key": "waitlist", "rows": [{"request_id": 5, "singer": "W", "artist": "A", "title": "T", "status_label": "Waiting", "first_time_singer": True}]}]
    yield dict(base, label="waitlist", queue=[one], sections=sections, removed={"a": {"removed_at": 5, "row": {"request_id": 9, "singer": "R"}}, "b": {"removed_at": 7, "row": {"request_id": 8}},
                                                                               "c": {"removed_at": 7, "row": {"request_id": 10}}, "x": {"explode": True, "removed_at": 99}})
    chat_room = {i: gm(i, f"m{i}") for i in range(1, 6)}
    chat_room[6] = gm(6, "to ann", frm="Bob", channel="dm", to_key="ann")
    chat_room[7] = gm(7, "host says", frm="Host", channel="dm", from_key="@host", thread="ann|bob", host=True)
    chat_room[8] = gm(8, "pic", frm="Ann", channel="host", thread="ANN", kind="image", media_id=4)
    yield dict(base, label="chat-attention", queue=[one], room=chat_room, attention=[1, 6, 7, 99], chat=[{"id": 9, "singer": "Ann", "direction": "in", "read": False, "message": "hello", "created_at": 1010}],
               media_read=[])
    yield dict(base, label="chat-raise-midway", queue=[one], room={1: gm(1, "a", media_id="oops", kind="image"), 2: gm(2, "b")}, attention=[1, 2])


def real_cases():
    src = Path(os.path.expanduser("~/SingWS/queue.json"))
    if not src.is_file():
        return
    scratch = Path(tempfile.mkdtemp(prefix="relay_msg_parity_real_")) / "queue.json"
    shutil.copyfile(src, scratch)  # read-only copy; the live file is never opened for writing
    data = json.loads(scratch.read_text())
    if isinstance(data, dict):
        data = data.get("queue", [])
    tally("real_singers", len(data))
    tally("real_songs", sum(len(s.get("songs", [])) for s in data if isinstance(s, dict)))
    base = dict(settings={"user": "wsk", "tenant": "wsk"}, control={"rotation": {"last": {}, "current": {}, "next": {}}, "playback": dict(PLAYBACK), "bg_playing": True, "bg_volume": 0.5,
                                                                    "bg_index": 0, "bg_track": "a.mp3"})
    yield dict(base, label="real-queue", queue=copy.deepcopy(data))
    half = copy.deepcopy(data)
    for s in half[::2]:
        if isinstance(s, dict):
            s["skipped"] = True
    yield dict(base, label="real-queue-half-skipped", queue=half)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--no-real", action="store_true")
    args = ap.parse_args()
    if not os.path.isfile(CLI):
        print(f"missing CLI {CLI}: build it first (see the docstring)")
        return 2
    import subprocess
    bad = subprocess.run([CLI], input=b"{", capture_output=True)
    if bad.returncode != 2:
        fail(f"the CLI must exit 2 on bad JSON, got {bad.returncode}")
    for kw in handwritten():
        run_case(**kw)
    rng = random.Random(args.seed)
    for i in range(args.cases):
        run_case(**random_case(rng, i))
    if not args.no_real:
        for kw in real_cases():
            run_case(**kw)
    print("counts:", json.dumps(counts, sort_keys=True))
    if failures:
        print(f"{len(failures)} DIFFERENCES")
        return 1
    print("IDENTICAL: every message the app's real _publish_host_state_ws sent equals what Rust rebuilt from the production snapshot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
