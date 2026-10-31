#!/usr/bin/env python3
"""Stage E chat parity: the Rust Chat tab view model (singws-uimodel `chat`, `singws-chat-cli`) against the app's REAL Python chat page.

Oracle = the app module: the REAL ``_shell_chat_summary`` turns random server chat state into the summary the shell receives (so the summary is part of what is
compared), the REAL ``_render_room_chat`` draws every Everyone / Private message into HTML (sender, tags, time stamp, text, attachment, via the real
``_room_attachment_html``, ``_room_thread_keys`` and ``_room_display_name``), the REAL ``_render_host_chat_transcript`` draws each Host chat conversation, and the
REAL ``_chat_tab_labels`` makes the tab labels. The HTML is parsed back into (sender, tags, stamp, text, attachment) and compared message by message with the Rust rows.
Fuzzed: statuses (visible / held with a flag / removed / blocked / unknown), report counts, host messages in private threads, pictures and GIFs (valid and invalid),
texts with markup, ampersands, quotes, emoji and line breaks, missing time stamps, fixed-offset time zones (including +05:30), unread counts and which tab is open.
Not compared (by design): text of removed room messages (the summary withholds it), attention per tab (the summary carries one total), the Python-only moderation links.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/chat_parity.py [--cases N] [--seed S]
Exit status 1 on any difference.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import random
import re
import subprocess
import sys
import time
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a scratch SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_CHAT_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-chat-cli")
failures: list[str] = []
counts: dict[str, int] = {}

# fixed-offset zones only (no daylight saving), so one offset per case is exact
ZONES = [("UTC", 0), ("Etc/GMT+7", -420), ("Etc/GMT-5", 300), ("Asia/Kolkata", 330), ("Etc/GMT+12", -720)]


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:1200])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


class View:
    def __init__(self):
        self.html = ""

    def setHtml(self, h):
        self.html = h

    def verticalScrollBar(self):
        return types.SimpleNamespace(value=lambda: 0, maximum=lambda: 0, setValue=lambda v: None)


class Label:
    def setText(self, t):
        pass


def fake(rng, room, host_text):
    ka = hp.app().KaraokeApp
    f = types.SimpleNamespace()
    f.settings = {"host_chat_name": "DJ Dan"}
    f.room_view = View()
    f.room_status_label = Label()
    f.room_mutes_label = Label()
    f.room_filter_combo = types.SimpleNamespace(currentIndex=lambda: 0)
    f.room_search_input = types.SimpleNamespace(text=lambda: "")
    f._room_messages = {m["id"]: m for m in room}
    f._chat_messages = list(host_text)
    f._room_settings = {"group_enabled": rng.random() < 0.9, "dm_enabled": rng.random() < 0.9}
    f._room_mutes = []
    f._room_attention_ids = set()
    f._room_reachable = True
    f._room_media = {}
    f._room_media_thumb = {}
    f._room_gif_full = {}
    f._room_new_by_channel = {"group": rng.randrange(0, 5), "dm": rng.randrange(0, 5)}
    f._host_media_read = set(m["id"] for m in room if m.get("channel") == "host" and rng.random() < 0.3)
    f._room_fetch_media = lambda mid: None
    f._room_fetch_gif = lambda gid, url: None
    f.chat_tab_bar = types.SimpleNamespace(currentIndex=lambda: -1)
    f._ROOM_TAB_CHANNELS = ka._ROOM_TAB_CHANNELS
    for c in ("_SHELL_CHAT_TEXT_MAX", "_SHELL_CHAT_GROUP_ROWS", "_SHELL_CHAT_THREADS", "_SHELL_CHAT_THREAD_ROWS"):
        setattr(f, c, getattr(ka, c))
    for name in ("_render_room_chat", "_room_attachment_html", "_room_thread_keys", "_room_display_name", "_chat_tab_labels", "_room_channel_counts", "_shell_chat_summary",
                 "_shell_chat_message", "_chat_conversations", "_host_chat_media_rows", "_host_chat_media_unread", "_host_chat_singer_name", "_render_host_chat_transcript"):
        setattr(f, name, types.MethodType(getattr(ka, name), f))
    return f


NAMES = {"ann": "Ann", "bo": "Bo", "cy": "Cy D.", "di": "Di <3"}
TEXTS = ["hi", "Hello there!", "a & b", "<b>bold</b>", "it's \"quoted\"", "line one\nline two", "🎤🔥 party", "  padded  ", "x" * 100, "Ünïcode ß", "", "&amp;", "5 > 3 < 7", "<script>alert(1)</script>"]
GIF = {"id": "g1", "preview": "https://media2.giphy.com/media/x/200.gif", "full": "https://media2.giphy.com/media/x/giphy.gif"}


def rand_msg(rng, mid, t0):
    kind = rng.choices(["", "image", "gif"], [6, 2, 2])[0]
    m = {"id": mid, "message": rng.choice(TEXTS), "created_at": rng.choice([0, t0 + mid * 37, t0 + mid * 61 + 86_000]), "status": rng.choice(["visible", "visible", "visible", "held", "removed", "blocked", "weird", None])}
    if m["status"] is None:
        del m["status"]
    if rng.random() < 0.3:
        m["flag"] = rng.choice(["link", "profanity", "", "spam"])
    if rng.random() < 0.25:
        m["reports"] = rng.randrange(0, 4)
    if kind:
        m["kind"] = kind
        if kind == "image":
            m["media_id"] = rng.choice([0, 5, 9])
        else:
            m["gif"] = rng.choice([GIF, GIF, {"id": "g2", "preview": "http://evil.example/x.gif"}, None])
    return m


def build(rng):
    t0 = 1_793_000_000 + rng.randrange(0, 200_000)
    keys = rng.sample(sorted(NAMES), rng.randrange(2, 5))
    room, mid = [], 0
    for _ in range(rng.randrange(0, 14)):
        mid += 1
        m = rand_msg(rng, mid, t0)
        r = rng.random()
        if r < 0.5:
            k = rng.choice(keys)
            m.update(channel="group", **{"from": NAMES[k], "from_key": k})
            if rng.random() < 0.15:
                m.update(host=True, **{"from": "Host", "from_key": "@host"})
        elif r < 0.85:
            a, b = rng.sample(keys, 2)
            if rng.random() < 0.2:
                m.update(channel="dm", host=True, thread=f"{min(a, b)}|{max(a, b)}", to_key="", **{"from": "Host", "from_key": "@host"})
            else:
                m.update(channel="dm", to_key=b, **{"from": NAMES[a], "from_key": a})
        else:
            k = rng.choice(keys)
            m.update(channel="host", thread=k, **{"from": NAMES[k], "from_key": k})
            if rng.random() < 0.3:
                m.update(host=True, **{"from": "DJ Dan", "from_key": "@host"})
        room.append(m)
    # every private singer must have spoken once under a consistent name (the app derives a thread's names from their messages)
    host_text = []
    for n in range(rng.randrange(0, 8)):
        k = rng.choice(keys)
        host_text.append({"id": 100 + n, "singer": NAMES[k], "singer_key": k, "message": rng.choice(TEXTS), "direction": rng.choice(["in", "out"]), "read": rng.random() < 0.5,
                          "created_at": rng.choice([0, t0 + n * 53, t0 + n * 41 + 90_000])})
    return room, host_text


ROW = re.compile(r"<td bgcolor='#1c1c2a'><b style='color:[^']*'>(.*?)</b>  (.*?)  <span style='color:#B4B8C6'>(.*?)</span><br>(.*)<br><span style='font-size:11px;color:#B4B8C6'>(.*?)</span></td>", re.S)
TAG = re.compile(r"<span style='color:#[0-9a-fA-F]+'>(.*?)</span>", re.S)
ATTACH_AT = re.compile(r"<br>(?=<i style='color:#B4B8C6'>loading (?:picture|GIF)…|<a href='room://(?:image|gif)/)")


def split_body(body_html):
    """(text, attachment word) of a rendered message body"""
    parts = ATTACH_AT.split(body_html, maxsplit=1)
    text = parts[0]
    attach = ""
    if len(parts) > 1:
        rest = parts[1]
        attach = "photo" if ("loading picture" in rest or "room://image/" in rest) else "gif"
    text = re.sub(r"</?(?:s|i)(?: [^>]*)?>", "", text).replace("<br>", "\n")
    return _html.unescape(text), attach


def parse_room(html_text):
    rows = {}
    for chunk in html_text.split("</table>"):
        found = ROW.search(chunk)
        if not found:
            continue
        who, tags, stamp, body, acts = found.groups()
        mid = int(re.search(r"room://(?:remove|restore)/(\d+)", acts).group(1))
        text, attach = split_body(body)
        rows[mid] = {"who": _html.unescape(who), "tags": [_html.unescape(t) for t in TAG.findall(tags)], "stamp": stamp, "text": text, "attachment": attach}
    return rows


HOST_P = re.compile(r"<p style='margin:0 0 12px 0'><b>(You|Singer)</b> <span style='color:#B4B8C6'>(.*?)</span><br>(.*?)</p>", re.S)


def run_case(rng):
    zone, offset = rng.choice(ZONES)
    os.environ["TZ"] = zone
    time.tzset()
    room, host_text = build(rng)
    f = fake(rng, room, host_text)
    chat = f._shell_chat_summary()
    f._render_room_chat()
    py_rows = parse_room(f.room_view.html)
    current = rng.choice(["group", "dm", "host"])
    f.chat_tab_bar = types.SimpleNamespace(currentIndex=lambda: {"group": 0, "dm": 1, "host": 2}[current])
    view = rust({"chat": chat, "current": current, "utc_offset_min": offset})
    ctx = f"zone={zone} room={json.dumps(room, ensure_ascii=False)[:600]}"

    # Everyone and Private rows
    rust_rows = [(r, None) for r in view["group"]] + [(r, t["title"]) for t in view["dm"] for r in t["rows"]]
    for r, _thread in rust_rows:
        p = py_rows.get(r["id"])
        if p is None:
            fail(f"row {r['id']} is in the Rust view but the app did not draw it: {ctx}")
            continue
        want = {"who": p["who"], "tags": p["tags"], "stamp": p["stamp"], "attachment": p["attachment"]}
        got = {"who": r["who"], "tags": r["tags"], "stamp": r["stamp"], "attachment": {"": "", "[photo]": "photo", "[GIF]": "gif"}[r["attachment"]]}
        if got != want:
            fail(f"row {r['id']}:\n python={want!r}\n rust  ={got!r}\n {ctx}")
            continue
        tally("room_row_same")
        if r["status"] != "removed":
            msg = room[[m["id"] for m in room].index(r["id"])]
            want_text = p["text"]
            if r["body"] != want_text:
                fail(f"row {r['id']} text: python={want_text!r} rust={r['body']!r} {ctx}")
            else:
                tally("room_text_same")
        else:
            tally("removed_text_withheld")
            if r["body"] != "":
                fail(f"a removed message's text reached the shell: {r!r}")

    # tab labels
    labels = f._chat_tab_labels()
    for tab, want in zip(view["tabs"], labels):
        if tab["label"] != want:
            fail(f"tab {tab['key']} (open={current}): python={want!r} rust={tab['label']!r}")
        else:
            tally("tab_same")

    # Host chat transcripts
    for thread in view["host"]:
        key = thread["key"]
        f.chat_singer_list = types.SimpleNamespace(currentItem=lambda key=key: types.SimpleNamespace(data=lambda role, key=key: key))
        f.chat_transcript = View()
        f._render_host_chat_transcript()
        py = HOST_P.findall(f.chat_transcript.html)
        py = py[-len(thread["rows"]):] if thread["rows"] else []
        if len(py) != len(thread["rows"]):
            fail(f"host chat {key}: python has {len(py)} rows, rust {len(thread['rows'])}: {ctx}")
            continue
        for (who, stamp, body_html), r in zip(py, thread["rows"]):
            text, attach = split_body(body_html)
            want = {"mine": who == "You", "stamp": stamp, "text": text, "attachment": attach}
            got = {"mine": r["mine"], "stamp": r["stamp"], "text": r["body"], "attachment": {"": "", "[photo]": "photo", "[GIF]": "gif"}[r["attachment"]]}
            if got != want:
                fail(f"host chat row:\n python={want!r}\n rust  ={got!r}\n {ctx}")
            else:
                tally("host_row_same")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel --bins", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        run_case(rng)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
