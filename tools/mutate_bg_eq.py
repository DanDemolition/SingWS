#!/usr/bin/env python3
"""Mutation test for the app-side `bg.next`, `bg.play_track` and `eq.set` (0.2.18.1.py): every mutant must be killed by test_shell_bg_eq.py (+ the queue-command tests).
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
    ('        if getattr(self, "karaoke_playing", False):\n            return False, "karaoke_playing"\n        bg = getattr(self, "bg_music", None)\n        if bg is None or not getattr(bg, "playlist", None):\n            return False, "no_playlist"\n        if dry_run:\n            return True, "dry_run: would skip',
     '        bg = getattr(self, "bg_music", None)\n        if bg is None or not getattr(bg, "playlist", None):\n            return False, "no_playlist"\n        if dry_run:\n            return True, "dry_run: would skip', None),
    ('        if bg is None or not getattr(bg, "playlist", None):\n            return False, "no_playlist"\n        if dry_run:\n            return True, "dry_run: would skip', '        if dry_run:\n            return True, "dry_run: would skip', None),
    ('            return True, "dry_run: would skip to the next track"\n        self._bg_main_next()', '            return True, "dry_run: would skip to the next track"\n        pass', None),
    ('            return True, "dry_run: would skip to the next track"\n        self._bg_main_next()\n        return True, "skipped"', '        self._bg_main_next()\n        return True, "skipped"', None),
    ('        if not 0 <= index < len(playlist):\n            return False, "unknown_track"', '        if not index < len(playlist):\n            return False, "unknown_track"', "the executor already refuses a negative index (bad_index) before this method runs"),
    ('        if not 0 <= index < len(playlist):\n            return False, "unknown_track"', '        if False:\n            return False, "unknown_track"', None),
    ('        if name != track.strip():\n            return False, "stale_playlist"', '        if False:\n            return False, "stale_playlist"', None),
    ('        if name != track.strip():', '        if name != track:', None),
    ('        self._bgm_session_started = True  # operator-initiated play, as the play button does\n        bg.current_index = index\n        bg.stop()\n        bg.play()', '        self._bgm_session_started = True  # operator-initiated play, as the play button does\n        bg.stop()\n        bg.play()', None),
    ('        bg.current_index = index\n        bg.stop()\n        bg.play()', '        bg.current_index = index\n        bg.play()', None),
    ('        self._bgm_session_started = True  # operator-initiated play', '        pass  # operator-initiated play', None),
    ('        if self._simple_audio_mode():\n            return False, "simple_audio"', '        if False:\n            return False, "simple_audio"', None),
    ('        if not self._ensure_eq_engines():\n            return False, "eq_unavailable"', '        self._ensure_eq_engines()', None),
    ('        values = [round(float(g), 1) for g in gains]', '        values = [float(g) for g in gains]', None),
    ('        eq.set_all_gains_db(values)\n        self.settings[key] = values', '        eq.set_all_gains_db(values)', None),
    ('        self.settings[key] = values\n        eq.set_enabled(bool(enabled))', '        self.settings[key] = values\n        eq.set_enabled(True)', None),
    ('        self.settings[enabled_key] = bool(enabled)\n        self._eq_refresh_live_attachment(key, eq, "shell_eq")', '        self._eq_refresh_live_attachment(key, eq, "shell_eq")', None),
    ('        self._eq_refresh_live_attachment(key, eq, "shell_eq")\n        try:\n            self.save_settings()', '        try:\n            self.save_settings()', None),
    ('        self._eq_refresh_live_attachment(key, eq, "shell_eq")\n        try:\n            self.save_settings()\n        except Exception:\n            pass\n        return True, "changed"', '        self._eq_refresh_live_attachment(key, eq, "shell_eq")\n        return True, "changed"', None),
    ('("eq_bgm", "eq_bgm_enabled", self.bgm_eq) if target == "bgm" else', '("eq_bgm", "eq_bgm_enabled", self.bgm_eq) if target != "bgm" else', None),
    ('            return True, "dry_run: would set the equaliser"\n        eq.set_all_gains_db(values)', '            pass\n        eq.set_all_gains_db(values)', None),
    ('            if target not in ("bgm", "karaoke"):\n                return remember(False, "bad_target")', '            if False:\n                return remember(False, "bad_target")', None),
    ('            if (not isinstance(gains, list) or len(gains) != 10\n', '            if (not isinstance(gains, list) or len(gains) < 10\n', None),
    ('any(isinstance(g, bool) or not isinstance(g, (int, float)) or g != g or abs(g) > 12.0 for g in gains)', 'any(not isinstance(g, (int, float)) or g != g or abs(g) > 12.0 for g in gains)', None),
    ('any(isinstance(g, bool) or not isinstance(g, (int, float)) or g != g or abs(g) > 12.0 for g in gains)', 'any(isinstance(g, bool) or not isinstance(g, (int, float)) or abs(g) > 12.0 for g in gains)', None),
    ('any(isinstance(g, bool) or not isinstance(g, (int, float)) or g != g or abs(g) > 12.0 for g in gains)', 'any(isinstance(g, bool) or not isinstance(g, (int, float)) or g != g or abs(g) > 13.0 for g in gains)', None),
    ('            if not isinstance(enabled, bool):\n                return remember(False, "bad_enabled")', '            if False:\n                return remember(False, "bad_enabled")', None),
    ('            if not isinstance(index, int) or isinstance(index, bool) or index < 0:\n                return remember(False, "bad_index")', '            if not isinstance(index, int) or index < 0:\n                return remember(False, "bad_index")', None),
    ('            if not isinstance(track, str) or not track.strip():\n                return remember(False, "bad_track")', '            if not isinstance(track, str):\n                return remember(False, "bad_track")', None),
    ('"library.search", "bg.next", "bg.play_track", "eq.set")', '"library.search", "bg.next", "bg.play_track")', None),
    ('        if action.startswith(("queue.", "library.", "bg.", "eq.", "waitlist.", "settings.")):\n            return self._execute_shell_queue_command(action, args)', '        if action.startswith(("queue.", "library.")):\n            return self._execute_shell_queue_command(action, args)', None),
    ('        if action.startswith(("queue.", "library.", "bg.", "eq.", "waitlist.", "settings.")) or str(args.get("source") or "").strip().lower() == "shell":', '        if action.startswith(("queue.", "library.")) or str(args.get("source") or "").strip().lower() == "shell":', None),
    ('                self._eq_refresh_live_attachment(settings_key, eq, "eq_dialog_change")\n', '                pass\n', None),
    ('            self._push_mpv_audio_processing(reason)\n            return\n        if settings_key != "eq_bgm":', '            return\n        if settings_key != "eq_bgm":', None),
    ('                bass.set_eq(None if self._simple_audio_mode() else eq)\n            self._log_bgm_eq_route(reason)', '                bass.set_eq(eq)\n            self._log_bgm_eq_route(reason)', None),
]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_bg_eq", "test_shell_queue_commands"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


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
