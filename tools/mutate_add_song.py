#!/usr/bin/env python3
"""Mutation test for the app-side `queue.add_song` (0.2.18.1.py, song_index.py): every mutant must be killed by test_shell_add_song.py (+ the queue-command tests for the
shared executor). Originals are restored afterwards, also on Ctrl-C. Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = os.path.expanduser("~/.venvs/singws/.venv-build-arm64/bin/python")
ENV = dict(os.environ, QT_QPA_PLATFORM="offscreen")
A, S = "0.2.18.1.py", "song_index.py"
M = [
    (A, '            if not is_remote and not quiet:\n                self._select_queue_singer_for_host(dup_singer_idx)', '            if not is_remote:\n                self._select_queue_singer_for_host(dup_singer_idx)', "the shell refuses a duplicate before the routine runs and a fresh entry has no request identity, so the routine's duplicate branch cannot be reached by a shell add; the guard stays as a safety"),
    (A, '            if quiet:\n                self._quiet_add_outcome = {"code": "limit", "message": str(limit_message or "")}\n                _diag(f"[QUEUE-LIMIT] shell add refused: {limit_message}")\n                return False\n', '', None),
    (A, '            if quiet:\n                self._quiet_add_outcome = {"code": "duplicate", "message": "already queued"}\n', '', "the shell refuses duplicates before the routine runs; this only labels the routine's own request-id duplicate"),
    (A, '            self._request_queue_display_refresh()\n            if not is_remote and not quiet:\n                self._select_queue_singer_for_host(singer_idx)', '            self._request_queue_display_refresh()\n            if not is_remote:\n                self._select_queue_singer_for_host(singer_idx)', None),
    (A, '        if not is_remote:\n            if not quiet:\n                self.singer_input.clear()', '        if not is_remote:\n            if True:\n                self.singer_input.clear()', None),
    (A, '        if not is_remote and not quiet:\n            self._select_queue_singer_for_host(ins)', '        if not is_remote:\n            self._select_queue_singer_for_host(ins)', None),
    (A, '        if not name or len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):  # control characters only: emoji joiners and no-break spaces are fine in a name', '        if not name or len(name) > 600 or any(unicodedata.category(c) == "Cc" for c in name):  # control characters only: emoji joiners and no-break spaces are fine in a name', None),
    (A, '        if not name or len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):  # control characters only: emoji joiners and no-break spaces are fine in a name', '        if not name or len(name) > 60:  # control characters only: emoji joiners and no-break spaces are fine in a name', None),
    (A, '        if not name or len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):  # control characters only: emoji joiners and no-break spaces are fine in a name', '        if len(name) > 60 or any(unicodedata.category(c) == "Cc" for c in name):  # control characters only: emoji joiners and no-break spaces are fine in a name', "an empty name is refused again a few lines later, when the duet parser finds no singer"),
    (A, '        track = song_index.find_by_id(song_id)\n        if not track:\n            return False, "unknown_song"', '        track = song_index.find_by_id(song_id) or {"path": "x"}', None),
    (A, '        if not path or not os.path.exists(path):\n            return False, "file_missing"', '        if not path:\n            return False, "file_missing"', None),
    (A, '        except FileNotFoundError:\n            return False, "missing_mp3"', '        except FileNotFoundError:\n            raise', None),
    (A, '        if self._is_rotation_locked():\n            return False, "rotation_locked"\n        primary, duet_display', '        primary, duet_display', None),
    (A, '        keys = {self._queue_limit_name_key(n) for n in self._duet_participants_from_display(primary, duet_display)}', '        keys = {self._queue_limit_name_key(primary)}', None),
    (A, '            if not isinstance(singer, dict) or self._queue_limit_name_key(singer.get("name", "")) not in keys:\n                continue', '            if not isinstance(singer, dict) or self._queue_limit_name_key(singer.get("name", "")) not in keys or singer.get("skipped"):\n                continue', None),
    (A, 'if isinstance(entry, dict) and not entry.get("skipped") and self._song_info_primary_path(entry.get("song_info")) == path:', 'if isinstance(entry, dict) and self._song_info_primary_path(entry.get("song_info")) == path:', None),
    (A, '            return (True, "dry_run: would add") if ok else (False, "limit")', '            return (True, "dry_run: would add")', None),
    (A, '        undo = KaraokeApp._begin_undoable_action(self, "Add Song")\n        self._quiet_add_outcome', '        undo = None\n        self._quiet_add_outcome', None),
    (A, '        if not added or outcome.get("code") != "added":\n            return False, str(outcome.get("code") or "not_added")', '        if not added:\n            return False, str(outcome.get("code") or "not_added")', "the routine only returns True with outcome added or duplicate, and duplicates are refused earlier"),
    (A, '            self._schedule_save_data(1000 if bool(getattr(self, "karaoke_playing", False)) else 0)\n        except Exception:\n            pass\n        KaraokeApp._commit_undoable_action(self, undo, "Song added")', '            pass\n        except Exception:\n            pass\n        KaraokeApp._commit_undoable_action(self, undo, "Song added")', None),
    (A, '            if not isinstance(song_id, int) or isinstance(song_id, bool) or song_id <= 0:', '            if not isinstance(song_id, int) or song_id <= 0:', None),
    (A, '            if not isinstance(song_id, int) or isinstance(song_id, bool) or song_id <= 0:', '            if not isinstance(song_id, int) or isinstance(song_id, bool) or song_id < 0:', None),
    (A, '            if not isinstance(key, int) or isinstance(key, bool) or not -4 <= key <= 4:', '            if not isinstance(key, int) or isinstance(key, bool) or not -5 <= key <= 5:', None),
    (A, '            if not isinstance(key, int) or isinstance(key, bool) or not -4 <= key <= 4:', '            if not isinstance(key, int) or not -4 <= key <= 4:', None),
    (A, '            if not isinstance(args.get("singer"), str):\n                return remember(False, "bad_singer")\n', '', None),
    (A, '            return remember(ok, code)\n        if mode == "dry_run":', '            return remember(True, code)\n        if mode == "dry_run":', None),
    (A, '"queue.skip_singer", "queue.add_song", "library.search")', '"queue.skip_singer", "library.search")', None),
    (A, '        data = song_index.find_by_id', '        data = song_index.find_by_id', "placeholder"),
    (A, '        if action.startswith(("queue.", "library.")):\n            return self._execute_shell_queue_command(action, args)', '        if action.startswith("queue."):\n            return self._execute_shell_queue_command(action, args)', None),
    (A, '        if action.startswith(("queue.", "library.")) or str(args.get("source") or "").strip().lower() == "shell":', '        if action.startswith("queue.") or str(args.get("source") or "").strip().lower() == "shell":', None),
    (A, '            if action.startswith(("queue.", "library.")):\n                args = dict(args)\n                args["_request_id"] = f"http:{command_id}"', '            if action.startswith("queue."):\n                args = dict(args)\n                args["_request_id"] = f"http:{command_id}"', "the polling path cannot carry the rows back, so a library.search over it is refused either way (missing_request_id vs needs_relay)"),
    (A, 'if not isinstance(query, str) or not query.strip() or len(query.strip()) > 80:', 'if not isinstance(query, str) or not query.strip() or len(query.strip()) > 800:', None),
    (A, 'if not isinstance(query, str) or not query.strip() or len(query.strip()) > 80:', 'if not isinstance(query, str) or len(query.strip()) > 80:', None),
    (A, 'if not isinstance(query, str) or not query.strip() or len(query.strip()) > 80:', 'if not query.strip() or len(query.strip()) > 80:', None),
    (A, '            if worker is None or not worker.is_connected():\n                return remember(False, "needs_relay")', '            if False:\n                return remember(False, "needs_relay")', None),
    (A, '                rows = song_index.search_songs(query, limit=25, fuzzy=False)\n                fuzzy = False', '                rows = song_index.search_songs(query, limit=250, fuzzy=False)\n                fuzzy = False', "rows[:25] caps the answer again"),
    (A, '                for r in rows[:25]:', '                for r in rows[:250]:', "the search itself is limited to 25 rows"),
    (A, '                    fuzzy = bool(rows)', '                    fuzzy = False', None),
    (A, '                if not rows:\n                    rows = song_index.search_songs(query, limit=25, fuzzy=True)', '                if False:\n                    rows = song_index.search_songs(query, limit=25, fuzzy=True)', None),
    (A, '"kind": str(r.get("song_type") or ""),\n                                "duration_secs"', '"kind": str(r.get("song_type") or ""), "path": r.get("path"),\n                                "duration_secs"', None),
    (A, '"duration_secs": int(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) else None})', '"duration_secs": None})', None),
    (A, '"fuzzy": False, "rows": [], "error": str(exc)[:80]}', '"fuzzy": False, "rows": []}', None),
    (A, '            self._run_on_ui_thread(deliver)\n\n        threading.Thread(target=work', '            deliver()\n\n        threading.Thread(target=work', None),
    (A, '                if worker is not None and worker.is_connected():\n                    worker.send_json(msg)', '                if worker is not None:\n                    worker.send_json(msg)', "the connected check is repeated at delivery; the test worker is always connected"),
    (A, '"queue.add_song", "library.search")', '"queue.add_song")', None),
    (S, '"SELECT * FROM songs WHERE id = ?", (sid,)', '"SELECT * FROM songs WHERE id <> ?", (sid,)', None),
    (S, '        sid = int(song_id)\n    except (TypeError, ValueError):\n        return None', '        sid = int(song_id)\n    except (TypeError, ValueError):\n        sid = 0', "an unparsable id then looks up id 0, which has no row: same answer"),
]
M = [m for m in M if m[3] != "placeholder"]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_add_song", "test_shell_queue_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


def main():
    originals = {f: (ROOT / f).read_text() for f in (A, S)}
    survivors, killed = [], 0
    try:
        for n, (f, old, new, reason) in enumerate(M):
            text = originals[f]
            if text.count(old) != 1:
                print(f"[{n:02d}] PATTERN NOT UNIQUE/FOUND ({text.count(old)}): {old[:70]!r}")
                survivors.append(n)
                continue
            (ROOT / f).write_text(text.replace(old, new, 1))
            try:
                rc = run()
            except subprocess.TimeoutExpired:
                rc = 1
            finally:
                (ROOT / f).write_text(text)
            if rc != 0:
                killed += 1
                print(f"[{n:02d}] killed  {old[:60]!r}")
            elif reason:
                print(f"[{n:02d}] EQUIVALENT ({reason})")
            else:
                survivors.append(n)
                print(f"[{n:02d}] SURVIVED  {old[:70]!r}")
    finally:
        for f, t in originals.items():
            (ROOT / f).write_text(t)
    print(f"killed {killed} of {len(M)}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
