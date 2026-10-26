#!/usr/bin/env python3
"""Mutation test for the app-side `queue.set_song_key`, `queue.set_song_tempo` and `queue.rename_singer` (0.2.18.1.py): every mutant must be killed by test_shell_dialogs.py (+ the queue-command tests).
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
    ('        if entry is None:\n            return False, "unknown_entry"\n        if field == "key" and not bool(getattr(self, "_karaoke_pitch_supported", True)):', '        if field == "key" and not bool(getattr(self, "_karaoke_pitch_supported", True)):', "the executor already refuses an unknown entry id before this method runs; the method keeps its own check as a safety"),
    ('        if field == "key" and not bool(getattr(self, "_karaoke_pitch_supported", True)):\n            return False, "pitch_unsupported"', '        if False:\n            return False, "pitch_unsupported"', None),
    ('        if field == "key" and not bool(getattr(self, "_karaoke_pitch_supported", True)):', '        if not bool(getattr(self, "_karaoke_pitch_supported", True)):', None),
    ('            return True, f"dry_run: would set the song\'s {\'key\' if field == \'key\' else \'speed\'}"\n        name = str(entry.get("display_name") or "this song")\n        entry[field] = int(value)', '            pass\n        name = str(entry.get("display_name") or "this song")\n        entry[field] = int(value)', None),
    ('        entry[field] = int(value)\n        self._push_remote_request_modifiers(entry)', '        self._push_remote_request_modifiers(entry)\n        entry[field] = int(value)', None),
    ('        entry[field] = int(value)\n        self._push_remote_request_modifiers(entry)\n        if field == "key":', '        entry[field] = int(value)\n        if field == "key":', None),
    ('            self._persist_song_modifier_change(f"Key {int(value):+d} set for {name}" if value else f"Key reset for {name}")', '            pass', None),
    ('            self._persist_song_modifier_change(f"Speed {int(value)}% set for {name}" if int(value) != 100 else f"Speed reset for {name}")', '            pass', None),
    ('            lo, hi = (-5, 5) if field == "key" else (70, 130)', '            lo, hi = (-6, 6) if field == "key" else (70, 130)', None),
    ('            lo, hi = (-5, 5) if field == "key" else (70, 130)', '            lo, hi = (-5, 5) if field == "key" else (60, 140)', None),
    ('            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:', '            if not isinstance(value, int) or not lo <= value <= hi:', None),
    ('                return remember(False, "bad_key" if field == "key" else "bad_tempo")', '                return remember(False, "bad_key")', None),
    ('            field = "key" if action == "queue.set_song_key" else "tempo_percent"', '            field = "key"', None),
    ('        if action in ("queue.move_song", "queue.remove_song", "queue.set_song_key", "queue.set_song_tempo") and self._queue_entry_position_by_id(entry_id)[0] < 0:', '        if action in ("queue.move_song", "queue.remove_song") and self._queue_entry_position_by_id(entry_id)[0] < 0:', "the method itself answers unknown_entry for the same case"),
    ('"queue.rename_singer") and self._queue_singer_index_by_id(singer_id) < 0:', '") and self._queue_singer_index_by_id(singer_id) < 0:', "the method itself answers unknown_singer for the same case"),
    ('        if not name or len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):\n            return False, "bad_name"', '        if not name or len(name) > 600 or any(unicodedata.category(c) == "Cc" for c in name):\n            return False, "bad_name"', None),
    ('        if not name or len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):\n            return False, "bad_name"', '        if not name or len(name) > 60:\n            return False, "bad_name"', None),
    ('        if not name or len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):\n            return False, "bad_name"', '        if len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):\n            return False, "bad_name"', None),
    ('        if name == old:\n            return True, "no_change"', '        if False:\n            return True, "no_change"', None),
    ('        key = self._queue_limit_name_key(name)\n        if any(i != idx and', '        key = name\n        if any(i != idx and', None),
    ('        if any(i != idx and isinstance(o, dict) and self._queue_limit_name_key(o.get("name", "")) == key for i, o in enumerate(self.queue or [])):\n            return False, "name_taken"', '        if False:\n            return False, "name_taken"', None),
    ('        if any(i != idx and isinstance(o, dict) and', '        if any(isinstance(o, dict) and', None),
    ('            return True, "dry_run: would rename the singer"\n        if not self._rename_rotation_singer(idx, name, source="shell"):', '            pass\n        if not self._rename_rotation_singer(idx, name, source="shell"):', None),
    ('        if not self._rename_rotation_singer(idx, name, source="shell"):\n            return False, "not_renamed"', '        self._rename_rotation_singer(idx, name, source="shell")', None),
    ('self._rename_rotation_singer(idx, name, source="shell")', 'self._rename_rotation_singer(idx, new_name, source="shell")', None),
    ('            if not isinstance(new_name, str):\n                return remember(False, "bad_name")', '            if False:\n                return remember(False, "bad_name")', "the method turns a non-text name into an empty one and answers bad_name itself"),
    ('"eq.set", "queue.set_song_key", "queue.set_song_tempo", "queue.rename_singer")', '"eq.set", "queue.set_song_key", "queue.set_song_tempo")', None),
]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_dialogs", "test_shell_queue_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


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
