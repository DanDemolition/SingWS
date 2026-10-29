#!/usr/bin/env python3
"""Mutation test for the app-side `waitlist.edit` / `history.edit_song` / `history.add_song` (0.2.18.1.py): every mutant must be killed by test_shell_edit_commands.py.
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
    ('        if bool(getattr(self, "karaoke_playing", False)):\n            return False, "playback_active"', '        if False:\n            return False, "playback_active"', None),
    ('        if not self._history_edit_backup("history_song_edit" if song_key else "history_song_add"):\n            return False, "backup_failed"', '        self._history_edit_backup("x")', None),
    ('        if not self._history_edit_backup("history_song_edit" if song_key else "history_song_add"):\n            return False, "backup_failed"', '        if False:\n            return False, "backup_failed"', None),
    ('        if dry_run:\n            return True, "dry_run: would change the singer history"', '        if False:\n            return True, "dry_run: would change the singer history"', None),
    ('        self._commit_singer_history_change("history_song_edit" if song_key else "history_song_add")\n        return True, "history_song_saved"', '        return True, "history_song_saved"', None),
    ('        self._commit_singer_history_change("history_song_edit" if song_key else "history_song_add")\n        return True, "history_song_saved"', '        self._commit_singer_history_change("history_song_edit")\n        return True, "history_song_saved"', None),
    ('not -24 <= key <= 24', 'not -24 <= key <= 25', None),
    ('not -24 <= key <= 24', 'not -23 <= key <= 24', None),
    ('not 50 <= tempo <= 200', 'not 50 <= tempo <= 201', None),
    ('not 50 <= tempo <= 200', 'not 51 <= tempo <= 200', None),
    ('clean("title", 100, True)', 'clean("title", 100, False)', None),
    ('clean("title", 100, True)', 'clean("title", 101, True)', None),
    ('clean("artist", 100)', 'clean("artist", 101)', None),
    ('clean("disc_id", 40)', 'clean("disc_id", 41)', None),
    ('clean("song_type", 10)', 'clean("song_type", 11)', None),
    ('any(unicodedata.category(c) == "Cc" for c in v) or (required and not v.strip())', '(required and not v.strip())', None),
    ('for n in (key, tempo))', 'for n in (key,))', None),
    ('isinstance(n, int) and not isinstance(n, bool) for n in (key, tempo))', 'isinstance(n, (int, float)) and not isinstance(n, bool) for n in (key, tempo))', None),
    ('isinstance(n, int) and not isinstance(n, bool) for n in (key, tempo))', 'isinstance(n, int) for n in (key, tempo))', None),
    ('        if song_key is not None and song_key not in (record.get("songs", {}) or {}):\n            return False, "unknown_history_song"', '', None),
    ('        if not isinstance(record, dict):\n            return False, "unknown_history_singer"', '', "a missing singer then raises on record.get and the executor answers failed:, a refusal too; the exact-code test kills it"),
    ('        if song_key:\n            songs.pop(song_key, None)\n        new_song = dict(existing)', '        new_song = dict(existing)', None),
    ('new_song["song_type"] = str(fields.get("song_type", "") or "").strip().upper()', 'new_song["song_type"] = str(fields.get("song_type", "") or "").strip()', None),
    ('        record["unique_song_count"] = len(songs)\n        record["updated_at"] = int(time.time())\n        return new_key', '        record["updated_at"] = int(time.time())\n        return new_key', None),
    ('new_song.setdefault("play_count", int(existing.get("play_count") or 0))', 'new_song["play_count"] = 0', None),
    ('for old in sorted(folder.glob("singer_history-*.json"))[:-30]:', 'for old in sorted(folder.glob("singer_history-*.json"))[:-31]:', None),
    ('_save_json_atomic(folder / f"singer_history-{time.strftime(\'%Y%m%d-%H%M%S\')}-{reason}.json", self.singer_history)', '_save_json_atomic(folder / f"singer_history-{time.strftime(\'%Y%m%d-%H%M%S\')}-{reason}.json", {})', None),
    ('        if action == "waitlist.edit":\n            req = getattr(self, "_waiting_for_add_requests", {}).get(request_id)\n            clearable = {self._waiting_for_add_request_id(r) for r in self._waiting_for_add_clearable_requests()}\n            if not isinstance(req, dict) or request_id not in clearable:', '        if action == "waitlist.edit":\n            req = getattr(self, "_waiting_for_add_requests", {}).get(request_id)\n            clearable = {self._waiting_for_add_request_id(r) for r in self._waiting_for_add_clearable_requests()}\n            if not isinstance(req, dict):', None),
    ('            if self._waitlist_apply_edit(req, request_id, f["singer"], f["artist"], f["title"], f["key"], f["tempo"]) is None:\n                return False, "unknown_request"\n            return True, "waitlist_edited"', '            self._waitlist_apply_edit(req, request_id, f["singer"], f["artist"], f["title"], f["key"], f["tempo"])\n            return True, "waitlist_edited"', "the request exists when we get here, so apply never returns None in these tests; the real branch is a race"),
    ('            if dry_run:\n                return True, "dry_run: would edit the request"', '            if False:\n                return True, "dry_run: would edit the request"', None),
    ('_t("singer", 60, True)', '_t("singer", 60, False)', None),
    ('_t("singer", 60, True)', '_t("singer", 61, True)', None),
    ('_t("title", 100, True)', '_t("title", 100, False)', None),
    ('_t("artist", 100)', '_t("artist", 101)', None),
    ('or not -12 <= fields["key"] <= 12 or not -30 <= fields["tempo"] <= 30:', 'or not -13 <= fields["key"] <= 12 or not -30 <= fields["tempo"] <= 30:', None),
    ('or not -12 <= fields["key"] <= 12 or not -30 <= fields["tempo"] <= 30:', 'or not -12 <= fields["key"] <= 12 or not -30 <= fields["tempo"] <= 31:', None),
    ('        updated = self._update_waiting_for_add_request(rid, {\n            "singer": singer,', '        updated = self._update_waiting_for_add_request(rid, {\n            "singer": "",', None),
    ('            "attention_reason": "host_edited_waitlist_metadata",', '            "attention_reason": "x",', None),
    ('                source="waiting_metadata_edit",\n            )\n        except Exception:\n            pass\n        return updated', '                source="x",\n            )\n        except Exception:\n            pass\n        return updated', None),
    ('            self._push_remote_request_replacement(\n                entry,\n                singer_name=singer,\n                old_artist=str(req.get("artist") or ""),', '            (lambda *a, **k: None)(\n                entry,\n                singer_name=singer,\n                old_artist=str(req.get("artist") or ""),', None),
    ('        if action in ("history.edit_song", "history.add_song"):\n            try:', '        if action in ("history.add_song",):\n            try:', None),
    ('"waitlist.restore", "waitlist.add", "waitlist.edit", "history.edit_song", "history.add_song", "settings.set")\n', '"waitlist.restore", "waitlist.add", "waitlist.edit", "history.add_song", "settings.set")\n', None),
    ('        song_key = args.get("song_key") if action == "history.edit_song" else None', '        song_key = args.get("song_key")', None),
]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_edit_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


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
