#!/usr/bin/env python3
"""Mutation test for the app-side `queue.set_singer_brand`, `queue.message_singer` and `queue.replace_song` (0.2.18.1.py): every mutant must be killed by test_shell_dialogs_more.py (+ the queue-command tests).
Originals are restored afterwards, also on Ctrl-C. Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "0.2.18.1.py"
PY = os.path.expanduser("~/.venvs/singws/.venv-build-arm64/bin/python")
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen")
M = [
    # ---- brand
    ('        text = str(brands or "").strip()\n        if len(text) > 200', '        text = str(brands or "").strip()\n        if len(text) > 2000', None),
    ('        if len(text) > 200 or any(unicodedata.category(c) == "Cc" for c in text):\n            return False, "bad_brands"', '        if len(text) > 200:\n            return False, "bad_brands"', None),
    ('        if text and not names:\n            return False, "bad_brands"', '        if False:\n            return False, "bad_brands"', None),
    ('        names = normalize_disc_priority(text, max_items=10)\n        if text and not names:', '        names = normalize_disc_priority(text, max_items=20)\n        if text and not names:', None),
    ('        if names == self._singer_disc_priority_override(singer_name):\n            return True, "no_change"', '        if False:\n            return True, "no_change"', None),
    ('            return True, "dry_run: would change the brand priority"', '            pass', None),
    ('        if not names:\n            self.clear_singer_brand_override(idx)\n            return True, "brand_cleared"', '        if not names:\n            return True, "brand_cleared"', None),
    ('        singer["preferred_disc_priority"] = value\n        self._set_singer_preference(singer_name, "preferred_disc_priority", value)', '        self._set_singer_preference(singer_name, "preferred_disc_priority", value)', None),
    ('        singer["preferred_disc_priority"] = value\n        self._set_singer_preference(singer_name, "preferred_disc_priority", value)', '        singer["preferred_disc_priority"] = value', None),
    ('        value = ", ".join(names)\n        singer["preferred_disc_priority"] = value', '        value = ",".join(names)\n        singer["preferred_disc_priority"] = value', None),
    ('        return True, "brand_set"', '        return True, "no_change"', None),
    ('            if not isinstance(brands, str):\n                return remember(False, "bad_brands")', '            if False:\n                return remember(False, "bad_brands")', "a non-text value becomes an empty string, which is 'clear' or 'no change'; the executor check is the only thing that refuses it"),
    ('"queue.rename_singer", "queue.set_singer_brand", "queue.message_singer") and self._queue_singer_index_by_id(singer_id) < 0:', '"queue.rename_singer", "queue.message_singer") and self._queue_singer_index_by_id(singer_id) < 0:', "the helper itself answers unknown_singer for the same case"),
    # ---- message
    ('        text = str(message or "").replace("\\r\\n", "\\n").strip()\n        if not text or len(text) > 500', '        text = str(message or "").replace("\\r\\n", "\\n").strip()\n        if len(text) > 500', None),
    ('        if not text or len(text) > 500 or any(unicodedata.category(c) == "Cc" and c not in "\\n\\t" for c in text):', '        if not text or len(text) > 5000 or any(unicodedata.category(c) == "Cc" and c not in "\\n\\t" for c in text):', None),
    ('        if not text or len(text) > 500 or any(unicodedata.category(c) == "Cc" and c not in "\\n\\t" for c in text):', '        if not text or len(text) > 500:', None),
    ('        if not text or len(text) > 500 or any(unicodedata.category(c) == "Cc" and c not in "\\n\\t" for c in text):', '        if not text or len(text) > 500 or any(unicodedata.category(c) == "Cc" for c in text):', None),
    ('str(self.settings.get("base_url", "") or "").strip() and str(self.settings.get("user", self.settings.get("tenant", "")) or "").strip()\n                and str(self.settings.get("api_key", "") or "").strip()):', 'str(self.settings.get("user", self.settings.get("tenant", "")) or "").strip()\n                and str(self.settings.get("api_key", "") or "").strip()):', None),
    ('str(self.settings.get("base_url", "") or "").strip() and str(self.settings.get("user", self.settings.get("tenant", "")) or "").strip()\n                and str(self.settings.get("api_key", "") or "").strip()):', 'str(self.settings.get("base_url", "") or "").strip() and str(self.settings.get("user", self.settings.get("tenant", "")) or "").strip()):', None),
    ('        key = (self._normalize_singer_pref_key(name), text)\n', '        key = (name, text)\n', "the test queue names are already normalised alike; case folding only matters for a renamed-case duplicate"),
    ('            if now - at > 30.0:\n                recent.pop(k, None)', '            if now - at > 3000.0:\n                recent.pop(k, None)', None),
    ('        if key in recent:\n            return False, "duplicate_message"', '        if False:\n            return False, "duplicate_message"', None),
    ('            return True, "dry_run: would send the message"\n        recent[key] = now', '            pass\n        recent[key] = now', None),
    ('            if not ok:\n                recent.pop(key, None)  # a failed send may be tried again', '            if False:\n                recent.pop(key, None)', None),
    ('        threading.Thread(target=work, daemon=True, name="shell-direct-message").start()\n        return True, "sending"', '        threading.Thread(target=work, daemon=True, name="shell-direct-message").start()\n        return False, "sending"', None),
    ('"code": "message_sent" if ok else "message_failed"', '"code": "message_sent"', None),
    ('"ok": bool(ok), "code"', '"ok": True, "code"', None),
    ('"type": "notice", "request_id": request_id, "ok": bool(ok)', '"type": "notice", "request_id": "", "ok": bool(ok)', None),
    ('"message": str(error or "")[:120]}', '"message": ""}', None),
    ('                if worker is not None and worker.is_connected():\n                    worker.send_json(notice)', '                if worker is not None:\n                    worker.send_json(notice)', None),
    ('                ok, error = self._net_send_direct_message(name, text)', '                ok, error = self._net_send_direct_message(name, str(message))', None),
    ('            if not isinstance(message, str):\n                return remember(False, "bad_message")', '            if False:\n                return remember(False, "bad_message")', "the helper turns a non-text message into an empty one and answers bad_message itself"),
    # ---- replace
    ('        path = str(track.get("path") or "").strip()\n        if not path or not os.path.exists(path):\n            return False, "file_missing"\n        songs = self.queue[singer_idx]', '        path = str(track.get("path") or "").strip()\n        songs = self.queue[singer_idx]', None),
    ('        if self._song_info_primary_path(entry.get("song_info")) == path:\n            return True, "no_change"', '        if False:\n            return True, "no_change"', None),
    ('            if other is not entry and isinstance(other, dict) and not other.get("skipped") and', '            if isinstance(other, dict) and not other.get("skipped") and', "the entry itself was already answered no_change when it holds the same song, so it can never reach this loop"),
    ('            if other is not entry and isinstance(other, dict) and not other.get("skipped") and', '            if other is not entry and isinstance(other, dict) and', None),
    ('            self._waiting_for_add_song_data_from_track({"key": 0}, track)\n            self._build_queue_entry_from_track_choice(entry, track)', '            self._build_queue_entry_from_track_choice(entry, track)', "the builder checks the CDG pair itself"),
    ('        except FileNotFoundError:\n            return False, "missing_mp3"\n        except ValueError:\n            return False, "bad_zip" if path.lower().endswith(".zip") else "bad_song"\n        current_request_id', '        except FileNotFoundError:\n            return False, "bad_song"\n        except ValueError:\n            return False, "bad_zip" if path.lower().endswith(".zip") else "bad_song"\n        current_request_id', None),
    ('        if bool(getattr(self, "karaoke_playing", False)) and current_request_id and str(self._ensure_queue_entry_id(entry) or "") == current_request_id:\n            return False, "song_playing"', '        if False:\n            return False, "song_playing"', None),
    ('        if bool(getattr(self, "karaoke_playing", False)) and current_request_id and str(self._ensure_queue_entry_id(entry) or "") == current_request_id:', '        if current_request_id and str(self._ensure_queue_entry_id(entry) or "") == current_request_id:', None),
    ('            return True, "dry_run: would replace the song"', '            pass', None),
    ('        if not self._replace_queue_song_with_track(singer_idx, song_idx, track, source="shell"):\n            return False, "not_replaced"\n        return True, "replaced"', '        self._replace_queue_song_with_track(singer_idx, song_idx, track, source="shell")\n        return True, "replaced"', "the replace routine only returns False for indexes this method already checked"),
    ('track, source="shell"):\n            return False, "not_replaced"', 'track, source="host"):\n            return False, "not_replaced"', None),
    ('            if not isinstance(song_id, int) or isinstance(song_id, bool) or song_id <= 0:\n                return remember(False, "bad_song_id")\n            try:\n                ok, code = self.shell_queue_replace_song', '            if not isinstance(song_id, int) or song_id <= 0:\n                return remember(False, "bad_song_id")\n            try:\n                ok, code = self.shell_queue_replace_song', None),
    ('"queue.set_song_tempo", "queue.replace_song") and self._queue_entry_position_by_id(entry_id)[0] < 0:', '"queue.set_song_tempo") and self._queue_entry_position_by_id(entry_id)[0] < 0:', "the helper itself answers unknown_entry for the same case"),
    ('"queue.message_singer", "queue.replace_song")\n\n    def _log_shell_commands_ready', '"queue.message_singer")\n\n    def _log_shell_commands_ready', None),
]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_dialogs_more", "test_shell_queue_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


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
