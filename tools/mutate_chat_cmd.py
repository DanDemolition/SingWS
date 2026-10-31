#!/usr/bin/env python3
"""Mutation test for the app-side `chat.say` and the chat summary (0.2.18.1.py): every mutant must be killed by test_shell_chat_commands.py or test_shell_chat_summary.py.
Originals are restored afterwards, also on Ctrl-C. Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "0.2.18.1.py"
PY = os.environ.get("SINGWS_PY") or "/Users/daniel/Documents/SingWS/.venv/bin/python"
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen")
M = [
    ('        if channel not in ("group", "host"):\n            return False, "bad_channel"', '        if channel not in ("group", "host", "dm"):\n            return False, "bad_channel"', None),
    ('        if channel not in ("group", "host"):\n            return False, "bad_channel"', '        if False:\n            return False, "bad_channel"', "any other channel then takes the Everyone branch only if it is 'group'; the tests cover 'dm' etc"),
    ('        if not isinstance(text, str):\n            return False, "bad_text"\n        body = text.strip()', '        body = str(text).strip()', None),
    ('        if not body or len(body) > 300 or any(unicodedata.category(c) == "Cc" for c in body):', '        if not body or len(body) > 301 or any(unicodedata.category(c) == "Cc" for c in body):', None),
    ('        if not body or len(body) > 300 or any(unicodedata.category(c) == "Cc" for c in body):', '        if not body or len(body) > 299 or any(unicodedata.category(c) == "Cc" for c in body):', None),
    ('        if not body or len(body) > 300 or any(unicodedata.category(c) == "Cc" for c in body):', '        if len(body) > 300 or any(unicodedata.category(c) == "Cc" for c in body):', None),
    ('        if not body or len(body) > 300 or any(unicodedata.category(c) == "Cc" for c in body):', '        if not body or len(body) > 300:', None),
    ('            if not isinstance(singer_key, str) or not singer_key.strip() or len(singer_key) > 200 or any(unicodedata.category(c) == "Cc" for c in singer_key):', '            if not isinstance(singer_key, str) or len(singer_key) > 200 or any(unicodedata.category(c) == "Cc" for c in singer_key):', None),
    ('            if not isinstance(singer_key, str) or not singer_key.strip() or len(singer_key) > 200 or any(unicodedata.category(c) == "Cc" for c in singer_key):', '            if not isinstance(singer_key, str) or not singer_key.strip() or any(unicodedata.category(c) == "Cc" for c in singer_key):', None),
    ('            if not isinstance(singer_key, str) or not singer_key.strip() or len(singer_key) > 200 or any(unicodedata.category(c) == "Cc" for c in singer_key):', '            if not isinstance(singer_key, str) or not singer_key.strip() or len(singer_key) > 200:', None),
    ('            key = singer_key.strip().casefold()', '            key = singer_key.strip()', None),
    ('            singer = self._host_chat_singer_name(key)\n            if not singer:\n                return False, "unknown_conversation"', '            singer = self._host_chat_singer_name(key) or key', None),
    ('            if not options:\n                return False, "chat_not_ready"', '            if False:\n                return False, "chat_not_ready"', None),
    ('            if not options.get("group_enabled"):\n                return False, "group_chat_off"', '            if False:\n                return False, "group_chat_off"', None),
    ('        conn = self._room_chat_conn()\n        if not conn:\n            return False, "network_not_configured"\n        recent = getattr(self, "_shell_recent_chat", None)', '        conn = self._room_chat_conn()\n        recent = getattr(self, "_shell_recent_chat", None)', None),
    ('        marker = (channel, key, body)', '        marker = (channel, body)', None),
    ('        marker = (channel, key, body)', '        marker = (key, body)', "key is empty for Everyone and never empty for Host chat, so the channel adds nothing"),
    ('            if now - at > 30.0:\n                recent.pop(k, None)\n        if marker in recent:', '            if now - at > 300.0:\n                recent.pop(k, None)\n        if marker in recent:', None),
    ('        if marker in recent:\n            return False, "duplicate_message"\n        if dry_run:', '        if dry_run:', None),
    ('        if dry_run:\n            return True, "dry_run: would send the message"\n        recent[marker] = now\n        base, user, api_key = conn', '        recent[marker] = now\n        if dry_run:\n            return True, "dry_run: would send the message"\n        base, user, api_key = conn', None),
    ('        if dry_run:\n            return True, "dry_run: would send the message"\n        recent[marker] = now\n        base, user, api_key = conn', '        if False:\n            return True, "dry_run: would send the message"\n        recent[marker] = now\n        base, user, api_key = conn', None),
    ('        recent[marker] = now\n        base, user, api_key = conn', '        base, user, api_key = conn', None),
    ('                    ok, error = self._net_send_direct_message(singer, body)', '                    ok, error = self._net_send_direct_message(singer, text)', None),
    ('"channel": "group", "a": "", "b": "", "message": body, "host_name": host_name}', '"channel": "group", "a": "", "b": "", "message": text, "host_name": host_name}', None),
    ('"channel": "group", "a": "", "b": "", "message": body, "host_name": host_name}', '"channel": "group", "a": "", "b": "", "message": body}', None),
    ('"channel": "group", "a": "", "b": "", "message": body,', '"channel": "dm", "a": "", "b": "", "message": body,', None),
    ('                    ok = bool(r.ok and data.get("ok"))\n                    if not ok:\n                        error = str(data.get("error") or f"Server returned {r.status_code}")\n            except Exception as exc:\n                ok, error = False, str(exc)\n            if not ok:\n                recent.pop(marker, None)', '                    ok = bool(r.ok)\n                    if not ok:\n                        error = str(data.get("error") or f"Server returned {r.status_code}")\n            except Exception as exc:\n                ok, error = False, str(exc)\n            if not ok:\n                recent.pop(marker, None)', None),
    ('            if not ok:\n                recent.pop(marker, None)  # a failed send may be tried again', '            if False:\n                recent.pop(marker, None)  # a failed send may be tried again', None),
    ('"code": "chat_sent" if ok else "chat_failed",', '"code": "chat_sent",', None),
    ('"message": "" if ok else str(self._ROOM_SAY_ERRORS.get(error, error))[:120]}', '"message": "" if ok else str(error)[:120]}', None),
    ('                if ok:\n                    if channel == "host":\n                        self._chat_data_generation', '                if True:\n                    if channel == "host":\n                        self._chat_data_generation', None),
    ('                        self._chat_last_id = 0\n                        self._chat_messages = []\n                        self._schedule_chat_poll()', '                        self._schedule_chat_poll()', None),
    ('                    self._schedule_room_poll(True)\n                worker = getattr(self, "host_relay_worker", None)', '                worker = getattr(self, "host_relay_worker", None)', None),
    ('        return True, "sending"\n\n    def shell_queue_message_singer', '        return True, "queued"\n\n    def shell_queue_message_singer', None),
    ('        if action == "chat.say":\n            try:\n                ok, code = self.shell_chat_say(args.get("channel"), args.get("singer_key"), args.get("text"), request_id, dry_run=(mode == "dry_run"))', '        if action == "chat.say":\n            try:\n                ok, code = self.shell_chat_say(args.get("channel"), args.get("singer_key"), args.get("text"), request_id, dry_run=False)', None),
    ('"settings.set", "chat.say")\n', '"settings.set")\n', None),
    ('        status = status if status in ("held", "removed", "blocked") else ""', '        status = status if status in ("held", "blocked") else ""', None),
    ('        text = "" if status == "removed" else str(m.get("message") or "")[:self._SHELL_CHAT_TEXT_MAX]', '        text = str(m.get("message") or "")[:self._SHELL_CHAT_TEXT_MAX]', None),
    ('        if kind == "image" and int(m.get("media_id") or 0):', '        if kind == "image":', None),
    ('        elif kind == "gif" and gif and re.match(r"^https://(media[0-9]*|i)\\.giphy\\.com/", str(gif.get("preview") or "")):', '        elif kind == "gif":', None),
    ('            attachment = "gif"\n        else:', '            attachment = "photo"\n        else:', None),
    ('            mine = m.get("direction") != "in"\n            sender', '            mine = m.get("direction") == "in"\n            sender', None),
    ('            out["group"] = [self._shell_chat_message(m) for m in ordered if m.get("channel") not in ("dm", "host")][-self._SHELL_CHAT_GROUP_ROWS:]', '            out["group"] = [self._shell_chat_message(m) for m in ordered if m.get("channel") not in ("host",)][-self._SHELL_CHAT_GROUP_ROWS:]', None),
    ('                threads.setdefault(tuple(sorted((a, b))), []).append(m)', '                threads.setdefault((a, b), []).append(m)', None),
    ('            dm.sort(key=lambda t: t["at"], reverse=True)', '            dm.sort(key=lambda t: t["at"])', None),
    ('            host.sort(key=lambda t: t["at"], reverse=True)', '            host.sort(key=lambda t: t["at"])', None),
    ('            out["dm"] = dm[:self._SHELL_CHAT_THREADS]', '            out["dm"] = dm[:self._SHELL_CHAT_THREADS + 1]', None),
    ('            out["unread"]["host"] = sum(t["unread"] for t in host)', '            out["unread"]["host"] = 0', None),
    ('                entries.sort(key=lambda e: e[:3])\n                unread', '                unread', None),
]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_chat_commands", "test_shell_chat_summary"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


def main():
    text = SRC.read_text()
    survivors, killed = [], 0
    try:
        for n, (old, new, reason) in enumerate(M):
            if text.count(old) != 1:
                print(f"[{n:02d}] PATTERN NOT UNIQUE/FOUND ({text.count(old)}): {old[:70]!r}")
                survivors.append(n)
                continue
            SRC.write_text(text.replace(old, new, 1))
            try:
                rc = run()
            except subprocess.TimeoutExpired:
                rc = 1
            finally:
                SRC.write_text(text)
            if rc != 0:
                killed += 1
                print(f"[{n:02d}] killed  {old[:60]!r}")
            elif reason:
                print(f"[{n:02d}] EQUIVALENT ({reason})")
            else:
                survivors.append(n)
                print(f"[{n:02d}] SURVIVED  {old[:70]!r}")
    finally:
        SRC.write_text(text)
    print(f"killed {killed} of {len(M)}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
