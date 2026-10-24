#!/usr/bin/env python3
"""Mutation test for the app-side shell queue commands (0.2.18.1.py): every mutant must be killed by test_shell_queue_commands.py.
The original file is restored afterwards, also on Ctrl-C. Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "0.2.18.1.py"
PY = os.path.expanduser("~/.venvs/singws/.venv-build-arm64/bin/python")
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen")
M = [
    ('            self.queue[i - 1], self.queue[i] = self.queue[i], self.queue[i - 1]\n        else:', '            self.queue[i], self.queue[i + 1] = self.queue[i + 1], self.queue[i]\n        else:', None),
    ('            self.queue[i], self.queue[i + 1] = self.queue[i + 1], self.queue[i]\n        self.update_queue_display()\n        KaraokeApp._commit_undoable_action(self, undo, "Queue reordered")\n        return "moved"', '            self.queue[i], self.queue[i + 1] = self.queue[i + 1], self.queue[i]\n        KaraokeApp._commit_undoable_action(self, undo, "Queue reordered")\n        return "moved"', None),
    ('            self._refresh_queue_song_row_identities(singer_idx)\n            self._update_queue_eta_label()\n            self._sync_remote_singer_order(singer_idx)\n        self.schedule_ticker_update()\n        KaraokeApp._commit_undoable_action(self, undo, "Queue reordered")\n        return "moved"', '            self._refresh_queue_song_row_identities(singer_idx)\n            self._update_queue_eta_label()\n        self.schedule_ticker_update()\n        KaraokeApp._commit_undoable_action(self, undo, "Queue reordered")\n        return "moved"', None),
    ('            self.update_queue_display()\n            self._sync_remote_singer_order(singer_idx)\n        else:\n            other', '            self.update_queue_display()\n            self._refresh_queue_song_row_identities(singer_idx)\n            self._sync_remote_singer_order(singer_idx)\n        else:\n            other', None),
    ('        self.schedule_ticker_update()\n        KaraokeApp._commit_undoable_action(self, undo, "Queue reordered")\n        return "moved"\n\n    def shell_queue_remove_singer', '        KaraokeApp._commit_undoable_action(self, undo, "Queue reordered")\n        return "moved"\n\n    def shell_queue_remove_singer', None),
    ('                    self._delete_remote_request(remote_request_id, entry=entry, singer_name=str(singer.get("name", "") or ""), reason="host_remove_singer")', '                    pass', None),
    ('        self._delete_rotation_singer_row(singer_idx, reason="host_remove_singer")\n        self._finish_shell_queue_removal(undo, "Singer removed")', '        self._finish_shell_queue_removal(undo, "Singer removed")', None),
    ('                _diag("[ROTATION] current playback detached by host song removal; media continues normally")\n        except Exception:\n            pass\n        try:\n            entry = self.queue[singer_idx]["songs"][song_idx]\n            remote_request_id', '                pass\n        except Exception:\n            pass\n        try:\n            entry = self.queue[singer_idx]["songs"][song_idx]\n            remote_request_id', "only the diagnostic log line is removed; the flag assignment just above it is kept"),
    ('        del self.queue[singer_idx]["songs"][song_idx]\n        try:\n            if singer_idx < len(self.queue) and not self.queue[singer_idx].get("songs"):\n                self._mark_rotation_slot_temporarily_empty(self.queue[singer_idx], reason="host_remove_song")\n        except Exception:\n            pass\n        try:\n            if singer_idx < len(self.queue):\n                self._sync_remote_singer_order(singer_idx, reason="host_remove_song")\n        except Exception:\n            pass\n        self._finish_shell_queue_removal(undo, "Song removed")', '        del self.queue[singer_idx]["songs"][song_idx]\n        try:\n            if singer_idx < len(self.queue):\n                self._sync_remote_singer_order(singer_idx, reason="host_remove_song")\n        except Exception:\n            pass\n        self._finish_shell_queue_removal(undo, "Song removed")', None),
    ('            self._schedule_save_data(1000 if bool(getattr(self, "karaoke_playing", False)) else 0)\n        except Exception:\n            pass\n        KaraokeApp._commit_undoable_action(self, undo, message)', '            self._schedule_save_data(0)\n        except Exception:\n            pass\n        KaraokeApp._commit_undoable_action(self, undo, message)', None),
    ('        if bool(self.queue[idx].get("skipped", False)) == bool(skipped):\n            return "no_change"\n        self.toggle_singer_skip(idx)', '        self.toggle_singer_skip(idx)', None),
    ('        if expect and expect != self._queue_signature():', '        if expect and expect == self._queue_signature():', None),
    ('        if age > 10.0:\n            return done(False, "expired")', '        if age > 100.0:\n            return done(False, "expired")', None),
    ('        if age < -60.0:\n            return done(False, "clock_skew")', '        if age < -6000.0:\n            return done(False, "clock_skew")', None),
    ('        if request_id in seen:\n            ok, message = seen[request_id]\n            return done(ok, f"duplicate: {message}")', '        if False:\n            ok, message = seen[request_id]\n            return done(ok, f"duplicate: {message}")', None),
    ('            seen[request_id] = (ok, message)\n            while len(seen) > 256:', '            while len(seen) > 256:', None),
    ('            while len(seen) > 256:\n                seen.popitem(last=False)', '            while len(seen) > 100000:\n                seen.popitem(last=False)', None),
    ('        if mode == "off":\n            return done(False, "shell_commands_off")\n        if action not in', '        if action not in', None),
    ('        if mode == "dry_run":\n            return remember(True, f"dry_run: would {action}")\n        try:', '        try:', None),
    ('        if action == "queue.noop":\n            return remember(True, "noop")', '        if action == "queue.noop":\n            return remember(False, "noop")', None),
    ('                bool(singer.get("skipped", False)),\n                bool(singer.get("rotation_marker", False)),\n                songs,', '                bool(singer.get("rotation_marker", False)),\n                songs,', None),
    ('                str(singer.get("name", "") or ""),\n                bool(singer.get("skipped", False)),', '                bool(singer.get("skipped", False)),', None),
    ('                    bool(entry.get("skipped", False)) if isinstance(entry, dict) else False,\n                ])\n            parts.append', '                    False,\n                ])\n            parts.append', None),
    ('        for singer in list(self.queue or [])[:200]:', '        for singer in list(self.queue or [])[:300]:', None),
    ('        if action.startswith("queue."):\n            return self._execute_shell_queue_command(action, args)\n        if str(args.get("source")', '        if str(args.get("source")', None),
    ('            if mode == "off":\n                return False, "shell_commands_off"\n            if mode == "dry_run":\n                _diag(f"[SHELL-CMD] id={args.get', '            if mode == "dry_run":\n                _diag(f"[SHELL-CMD] id={args.get', None),
    ('            if mode == "dry_run":\n                _diag(f"[SHELL-CMD] id={args.get(\'_request_id\') or \'-\'} action={action} mode=dry_run ok=True message=dry_run: would {action}")\n                return True, f"dry_run: would {action}"', '            if False:\n                return True, f"dry_run: would {action}"', None),
    ('            args["_sent_at"] = data.get("sent_at")', '            pass', None),
    ('                args["_request_id"] = f"http:{command_id}"', '                pass', None),
    ('        undo = KaraokeApp._begin_undoable_action(self, "Remove Song")', '        undo = KaraokeApp._begin_undoable_action(self, "Remove")', None),
    ('        if (where in ("up", "top") and i <= 0) or (where in ("down", "bottom") and i >= last):\n            return "no_change"', '        if (where in ("up", "top") and i < 0) or (where in ("down", "bottom") and i > last):\n            return "no_change"', None),
    ('        if action in ("queue.move_song", "queue.remove_song") and self._queue_entry_position_by_id(entry_id)[0] < 0:\n            return remember(False, "unknown_entry")', '', None),
]


def run():
    p = subprocess.run([PY, "-m", "unittest", "test_shell_queue_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=240)
    return p.returncode


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
