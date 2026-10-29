#!/usr/bin/env python3
"""Mutation test for the app-side `settings.set` (0.2.18.1.py): every mutant must be killed by test_shell_settings_set.py.
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
    ('        if not isinstance(key, str) or key not in self._SHELL_SETTINGS_SCHEMA:\n            return False, "unknown_setting"', '        if not isinstance(key, str):\n            return False, "unknown_setting"', None),
    ('        if not isinstance(key, str) or key not in self._SHELL_SETTINGS_SCHEMA:\n            return False, "unknown_setting"', '        if key not in self._SHELL_SETTINGS_SCHEMA:\n            return False, "unknown_setting"', "an unhashable key raises inside the executor's try and is refused as failed:"),
    ('        if not self._shell_setting_value_ok(key, value):\n            return False, "bad_value"', '        if False:\n            return False, "bad_value"', None),
    ('            return isinstance(value, bool)', '            return isinstance(value, (bool, int))', None),
    ('            return isinstance(value, str) and len(value) <= spec["max_chars"] and not any(unicodedata.category(c) == "Cc" for c in value)', '            return isinstance(value, str) and len(value) < spec["max_chars"] and not any(unicodedata.category(c) == "Cc" for c in value)', None),
    ('            return isinstance(value, str) and len(value) <= spec["max_chars"] and not any(unicodedata.category(c) == "Cc" for c in value)', '            return isinstance(value, str) and len(value) <= spec["max_chars"]', None),
    ('            return isinstance(value, str) and len(value) <= spec["max_chars"] and not any(unicodedata.category(c) == "Cc" for c in value)', '            return len(value) <= spec["max_chars"] and not any(unicodedata.category(c) == "Cc" for c in value)', "a non-text value then raises in len() or iteration and the executor answers failed:, which is also a refusal; the exact-code test kills it unless the value has a length"),
    ('"ticker_custom_message": {"type": "text", "max_chars": 200},', '"ticker_custom_message": {"type": "text", "max_chars": 300},', None),
    ('        "ticker_custom_enabled": {"type": "bool"},\n', '        "ticker_custom_enabled": {"type": "bool"},\n        "ticker_color": {"type": "text", "max_chars": 9},\n', None),
    ('        if key in self.settings and type(self.settings[key]) is type(value) and self.settings[key] == value:\n            return True, "no_change"', '        if False:\n            return True, "no_change"', None),
    ('        if key in self.settings and type(self.settings[key]) is type(value) and self.settings[key] == value:', '        if self.settings.get(key) == value:', None),
    ('            return True, "dry_run: would change the setting"', '            return False, "dry_run: would change the setting"', None),
    ('        if dry_run:\n            return True, "dry_run: would change the setting"', '        if False:\n            return True, "dry_run: would change the setting"', None),
    ('        self.settings[key] = float(value) if self._SHELL_SETTINGS_SCHEMA[key]["type"] == "float" else value\n        try:\n            self.save_settings()', '        try:\n            self.save_settings()', None),
    ('        self.settings[key] = float(value) if self._SHELL_SETTINGS_SCHEMA[key]["type"] == "float" else value\n        try:\n            self.save_settings()\n        except Exception as exc:', '        self.settings[key] = float(value) if self._SHELL_SETTINGS_SCHEMA[key]["type"] == "float" else value\n        try:\n            pass\n        except Exception as exc:', None),
    ('                object.__getattribute__(self, "__dict__").pop("_cached_ticker_data", None)', '                pass', None),
    ('            if key.startswith("ticker_"):\n                self.schedule_ticker_update()', '            if False:\n                self.schedule_ticker_update()', None),
    ('        return True, "setting_changed"', '        return True, "no_change"', None),
    ('        if action == "settings.set":\n            try:\n                ok, code = self.shell_settings_set(args.get("key"), args.get("value"), dry_run=(mode == "dry_run"))', '        if action == "settings.set":\n            try:\n                ok, code = self.shell_settings_set(args.get("key"), args.get("value"), dry_run=False)', None),
    ('"waitlist.add", "settings.set")\n', '"waitlist.add")\n', None),
    ('        if action.startswith(("queue.", "library.", "bg.", "eq.", "waitlist.", "settings.")):\n            return self._execute_shell_queue_command(action, args)', '        if action.startswith(("queue.", "library.", "bg.", "eq.", "waitlist.")):\n            return self._execute_shell_queue_command(action, args)', None),
    ('            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and spec["min"] <= value <= spec["max"]', '            return isinstance(value, (int, float)) and math.isfinite(value) and spec["min"] <= value <= spec["max"]', None),
    ('            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and spec["min"] <= value <= spec["max"]', '            return isinstance(value, (int, float)) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]', "an infinite or NaN value is JSON-impossible and fails the range check anyway"),
    ('math.isfinite(value) and spec["min"] <= value <= spec["max"]', 'math.isfinite(value) and spec["min"] < value <= spec["max"]', None),
    ('math.isfinite(value) and spec["min"] <= value <= spec["max"]', 'math.isfinite(value) and spec["min"] <= value < spec["max"]', None),
    ('"end_silence_trim_threshold_sec": {"type": "float", "min": 0.5, "max": 60.0},', '"end_silence_trim_threshold_sec": {"type": "float", "min": 0.5, "max": 600.0},', None),
    ('        self.settings[key] = float(value) if self._SHELL_SETTINGS_SCHEMA[key]["type"] == "float" else value', '        self.settings[key] = value', None),
    ('        "karaoke_auto_advance": {"type": "bool"},\n', '        "karaoke_auto_advance": {"type": "bool"},\n        "crash_auto_send_logs": {"type": "bool"},\n', None),
    ('            return isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]', '            return isinstance(value, (int, float)) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]', None),
    ('            return isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]', '            return isinstance(value, int) and spec["min"] <= value <= spec["max"]', None),
    ('            return isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]', '            return isinstance(value, int) and not isinstance(value, bool) and spec["min"] < value <= spec["max"]', None),
    ('            return isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]', '            return isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value < spec["max"]', None),
    ('"rotation_padding_sec": {"type": "int", "min": 0, "max": 600},', '"rotation_padding_sec": {"type": "int", "min": 0, "max": 6000},', None),
    ('"rotation_padding_sec": {"type": "int", "min": 0, "max": 600},', '"rotation_padding_sec": {"type": "int", "min": -5, "max": 600},', None),
    ('            if key in self._SHELL_SETTINGS_QUEUE_REFRESH:\n                self.update_queue_display()', '            if False:\n                self.update_queue_display()', None),
    ('            if key in self._SHELL_SETTINGS_QUEUE_REFRESH:', '            if key.startswith("rotation_estimate"):', None),
    ('    _SHELL_SETTINGS_QUEUE_REFRESH = frozenset({"rotation_estimate_include_all_songs", "rotation_padding_sec"})', '    _SHELL_SETTINGS_QUEUE_REFRESH = frozenset({"rotation_padding_sec"})', None),
    ('        "rotation_estimate_include_all_songs": {"type": "bool"},\n', '        "rotation_estimate_include_all_songs": {"type": "bool"},\n        "queue_mode": {"type": "text", "max_chars": 20},\n', None),
    ('                self._transition_gap_sec = float(self.settings[key])', '                pass', None),
    ('            if key == "bg_to_karaoke_gap_sec":', '            if key == "bg_to_karaoke":', None),
    ('"bg_to_karaoke_gap_sec": {"type": "float", "min": -3.0, "max": 3.0},', '"bg_to_karaoke_gap_sec": {"type": "float", "min": -3.0, "max": 30.0},', None),
    ('"bg_to_karaoke_gap_sec": {"type": "float", "min": -3.0, "max": 3.0},', '"bg_to_karaoke_gap_sec": {"type": "float", "min": 0.0, "max": 3.0},', None),
]


def run():
    return subprocess.run([PY, "-m", "unittest", "test_shell_settings_set"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=600).returncode


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
