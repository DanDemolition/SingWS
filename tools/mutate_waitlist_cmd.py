#!/usr/bin/env python3
"""Mutation test for the app-side `waitlist.remove` / `waitlist.restore` (0.2.18.1.py): every mutant must be killed by test_shell_waitlist_commands.py.
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
    ('        if action == "waitlist.remove":\n            req = getattr(self, "_waiting_for_add_requests", {}).get(request_id)', '        if action == "waitlist.remove":\n            req = getattr(self, "_waiting_for_add_removed_tonight", {}).get(request_id)', None),
    ('            if not isinstance(req, dict) or request_id not in clearable:\n                return False, "unknown_request"\n            if dry_run:\n                return True, "dry_run: would remove', '            if not isinstance(req, dict):\n                return False, "unknown_request"\n            if dry_run:\n                return True, "dry_run: would remove', None),
    ('            if not isinstance(req, dict) or request_id not in clearable:\n                return False, "unknown_request"\n            if dry_run:\n                return True, "dry_run: would remove', '            if request_id not in clearable:\n                return False, "unknown_request"\n            if dry_run:\n                return True, "dry_run: would remove', "a request that is clearable is always a dict in the waiting map"),
    ('                return True, "dry_run: would remove the request from the waitlist"\n            self._waitlist_remove_request(req, request_id)', '                return True, "dry_run: would remove the request from the waitlist"\n            pass', None),
    ('            if dry_run:\n                return True, "dry_run: would remove the request from the waitlist"', '            if False:\n                return True, "dry_run: would remove the request from the waitlist"', None),
    ('            self._waitlist_remove_request(req, request_id)\n            return True, "waitlist_removed"', '            return True, "waitlist_removed"', None),
    ('            req = getattr(self, "_waiting_for_add_removed_tonight", {}).get(request_id)\n            if not isinstance(req, dict) or not req.get("_removed_tonight"):', '            req = getattr(self, "_waiting_for_add_requests", {}).get(request_id)\n            if not isinstance(req, dict) or not req.get("_removed_tonight"):', None),
    ('            if not isinstance(req, dict) or not req.get("_removed_tonight"):\n                return False, "unknown_request"\n            if dry_run:', '            if not isinstance(req, dict):\n                return False, "unknown_request"\n            if dry_run:', None),
    ('                return True, "dry_run: would restore the request to the waitlist"\n            self._waitlist_restore_request(req)', '                return True, "dry_run: would restore the request to the waitlist"\n            pass', None),
    ('            if dry_run:\n                return True, "dry_run: would restore the request to the waitlist"', '            if False:\n                return True, "dry_run: would restore the request to the waitlist"', None),
    ('            self._waitlist_restore_request(req)\n            return True, "waitlist_restored"', '            return True, "waitlist_restored"', None),
    ('            if not isinstance(waitlist_rid, int) or isinstance(waitlist_rid, bool) or waitlist_rid <= 0:', '            if not isinstance(waitlist_rid, int) or waitlist_rid <= 0:', None),
    ('            if not isinstance(waitlist_rid, int) or isinstance(waitlist_rid, bool) or waitlist_rid <= 0:', '            if not isinstance(waitlist_rid, int) or isinstance(waitlist_rid, bool) or waitlist_rid < 0:', "request id 0 is not in any waitlist, so it is refused as unknown_request instead"),
    ('            if not isinstance(waitlist_rid, int) or isinstance(waitlist_rid, bool) or waitlist_rid <= 0:\n                return remember(False, "bad_request_id")', '            if False:\n                return remember(False, "bad_request_id")', "a non-int id then finds nothing and is refused as unknown_request or raises into failed:"),
    ('self.shell_waitlist_change(action, waitlist_rid, dry_run=(mode == "dry_run"))', 'self.shell_waitlist_change(action, waitlist_rid, dry_run=False)', None),
    ('"waitlist.restore", "waitlist.add")\n', '"waitlist.restore")\n', None),
    ('        if action.startswith(("queue.", "library.", "bg.", "eq.", "waitlist.")):\n            return self._execute_shell_queue_command(action, args)', '        if action.startswith(("queue.", "library.", "bg.", "eq.")):\n            return self._execute_shell_queue_command(action, args)', None),
    ('        self._waitlist_remove_request(req, rid)\n\n    def _waitlist_remove_request', '        pass\n\n    def _waitlist_remove_request', None),
    ('        self._waitlist_restore_request(req)\n\n    def _waitlist_restore_request', '        pass\n\n    def _waitlist_restore_request', None),
    ('        undo = KaraokeApp._begin_undoable_action(self, "Remove Pending Request")\n        self._archive_waitlist_removed_tonight(req, reason="waiting_for_add_cleared")', '        undo = None\n        self._archive_waitlist_removed_tonight(req, reason="waiting_for_add_cleared")', None),
    ('        self._archive_waitlist_removed_tonight(req, reason="waiting_for_add_cleared")\n        try:\n            self._waiting_for_add_handled_ids.add(rid)', '        try:\n            self._waiting_for_add_handled_ids.add(rid)', None),
    ('        restored["state"] = "waiting"\n        restored["pending_status"] = "Restored by host"', '        restored["state"] = "waiting"\n        restored["pending_status"] = "Restored"', None),
    ('        self._waiting_for_add_removed_tonight.pop(rid, None)\n        self._waiting_for_add_handled_ids.discard(rid)', '        self._waiting_for_add_handled_ids.discard(rid)', None),
    ('        self._restore_waitlist_request_on_server_async(restored, revision)\n        self._schedule_waiting_for_add_view_refresh(reason="restore_removed")', '        self._schedule_waiting_for_add_view_refresh(reason="restore_removed")', None),
    ('                return True, "dry_run: would add the request to the rotation"', '                return False, "dry_run: would add the request to the rotation"', None),
    ('            return (True, "waitlist_added") if ok else (False, code)', '            return (True, "waitlist_added")', None),
    ('                return False, "add_error", str(e)', '                return False, "not_added", str(e)', None),
    ('        if not ok:\n            return False, "not_added", ""\n        try:\n            self._waiting_for_add_handled_ids.add(rid)', '        if False:\n            return False, "not_added", ""\n        try:\n            self._waiting_for_add_handled_ids.add(rid)', None),
    ('        if not ok:\n            return False, "not_added", ""\n        try:\n            self._waiting_for_add_handled_ids.add(rid)', '        if not ok:\n            return False, "not_added", ""\n        try:\n            pass', None),
    ('        self._schedule_waiting_for_add_view_refresh(reason="add_now")\n        self._show_processing_notification("Added waiting request to rotation.", level="success")\n        return True, "added", ""', '        self._show_processing_notification("Added waiting request to rotation.", level="success")\n        return True, "added", ""', None),
    ('QMessageBox.warning(self, "Waiting for Add", detail if code == "add_error" else "That song could not be added from the local library.")', 'QMessageBox.warning(self, "Waiting for Add", "That song could not be added from the local library.")', None),
    ('out["waiting"] = [compact(r, "waitlist") for r in section.get("rows", [])[:40]]', 'out["waiting"] = [compact(r, "waitlist") for r in section.get("rows", [])[:41]]', None),
    ('removed.sort(key=removed_at, reverse=True)', 'removed.sort(key=removed_at, reverse=False)', None),
    ('for r in removed[:20]]', 'for r in removed[:21]]', None),
    ('"first_time": bool(row.get("first_time_singer")),', '"first_time": True,', None),
    ('if section.get("key") == "waitlist":\n                    out["waiting"]', 'if section.get("key") == "removed":\n                    out["waiting"]', None),]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_waitlist_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


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
