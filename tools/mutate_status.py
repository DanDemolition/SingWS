#!/usr/bin/env python3
"""Mutation test for the status snapshot (Python payload in 0.2.18.1.py + Rust `singws-uimodel::status`): every mutant must be killed by the Rust unit tests or
by test_status_snapshot.py (which compares the snapshot's view with the app's own labels over random states). Equivalent mutants carry a reason. Originals are
restored afterwards (also on Ctrl-C). Exit 1 when a non-equivalent mutant survives.

Usage:  CARGO_TARGET_DIR=~/.cache/singws-rust-target-screens python3 tools/mutate_status.py [rust|python|all]    (MUTANTS=3,5 runs only those)
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RS = ROOT / "rust/crates/singws-uimodel/src/status.rs"
PYSRC = ROOT / "0.2.18.1.py"
CT = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-screens")
ENV = dict(os.environ, CARGO_TARGET_DIR=CT, QT_QPA_PLATFORM="offscreen", SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS="1", SINGWS_STATUS_CLI=f"{CT}/release/singws-status-cli")
PY = os.environ.get("SINGWS_PY") or "/Users/daniel/Documents/SingWS/.venv/bin/python"
CARGO = os.path.expanduser("~/.cargo/bin/cargo")

RUST = [
    ('get(snapshot, "version").as_i64() != Some(1)', 'get(snapshot, "version").as_i64() == Some(2)', None),
    ('if !snapshot.is_object() {', 'if false {', "a non-object snapshot then has no taken_at and is declined on the next line"),
    ('let net = get(snapshot, "network_configured").as_bool().unwrap_or(false);', 'let net = get(snapshot, "network_configured").as_bool().unwrap_or(true);', None),
    ('let locked = get(snapshot, "locked").as_bool().unwrap_or(false);', 'let locked = get(snapshot, "locked").as_bool().unwrap_or(true);', None),
    ('let looping = get(snapshot, "looping").as_bool().unwrap_or(false);', 'let looping = get(snapshot, "looping").as_bool().unwrap_or(true);', None),
    ('get(snapshot, "total_secs"), taken_at, get(settings, "queue_mode")', 'get(snapshot, "total_secs"), taken_at + 1, get(settings, "queue_mode")', None),
    ('get(snapshot, "total_secs"), taken_at, get(settings, "queue_mode")', 'get(snapshot, "total_secs"), 0, get(settings, "queue_mode")', None),
    ('get(snapshot, "queue"), get(snapshot, "total_secs")', 'get(snapshot, "queue"), &Value::from(0)', None),
    ('Value::from(eta.singer_count), Value::from(eta.ends.clone())', 'Value::from(eta.singer_count), Value::from("")', None),
    ('Value::from(eta.singer_count), Value::from(eta.ends.clone())', 'Value::from(eta.minutes), Value::from(eta.ends.clone())', None),
    ('header::chips(settings, connection, net, &summary_pair)', 'header::chips(settings, &Value::Null, net, &summary_pair)', None),
    ('header::status(settings, connection, net, get(snapshot, "last"), get(snapshot, "attention"), get(snapshot, "queue_rev"))', 'header::status(settings, connection, net, get(snapshot, "last"), &Value::Null, get(snapshot, "queue_rev"))', None),
    ('header::status(settings, connection, net, get(snapshot, "last"), get(snapshot, "attention"), get(snapshot, "queue_rev"))', 'header::status(settings, connection, net, &Value::Null, get(snapshot, "attention"), get(snapshot, "queue_rev"))', None),
    ('header::status(settings, connection, net, get(snapshot, "last"), get(snapshot, "attention"), get(snapshot, "queue_rev"))', 'header::status(settings, connection, net, get(snapshot, "last"), get(snapshot, "attention"), &Value::Null)', None),
    ('header::queue_summary(eta.singer_count, eta.minutes, locked, looping)', 'header::queue_summary(eta.singer_count, eta.minutes, false, looping)', None),
    ('header::queue_summary(eta.singer_count, eta.minutes, locked, looping)', 'header::queue_summary(eta.singer_count, eta.minutes, locked, false)', None),
    ('header::queue_summary(eta.singer_count, eta.minutes, locked, looping)', 'header::queue_summary(eta.singer_count, 0, locked, looping)', None),
    ('o.insert("settings".into(), get(snapshot, "settings").clone());', 'o.insert("settings".into(), Value::Null);', None),
    ('let mut facts = get(snapshot, "diagnostics").clone();', 'let mut facts = Value::Object(Default::default());', None),
]

PYTHON = [
    ('            "taken_at": int(now),', '            "taken_at": int(now) + 60,', None),
    ('            "settings": {k: settings[k] for k in self._STATUS_SNAPSHOT_SETTING_KEYS if k in settings},', '            "settings": {k: settings.get(k) for k in self._STATUS_SNAPSHOT_SETTING_KEYS},', None),
    ('            "settings": {k: settings[k] for k in self._STATUS_SNAPSHOT_SETTING_KEYS if k in settings},', '            "settings": dict(settings),', None),
    ('"requests_accepting", "use_waiting_for_add", "location_name", "venue_name", "tenant", "user", "queue_mode",', '"use_waiting_for_add", "location_name", "venue_name", "tenant", "user", "queue_mode",', None),
    ('"requests_accepting", "use_waiting_for_add", "location_name", "venue_name", "tenant", "user", "queue_mode",', '"requests_accepting", "use_waiting_for_add", "location_name", "venue_name", "tenant", "queue_mode",', None),
    ('"karafun_provider_enabled", "ticker_enabled", "bg_video_folder", "bg_video_enabled",\n    )', '"karafun_provider_enabled", "bg_video_folder", "bg_video_enabled",\n    )', None),
    ('            "connection": state.get("_server_connection_state", ""),', '            "connection": "",', None),
    ('            "network_configured": bool(self.is_network_configured()),', '            "network_configured": False,', None),
    ('                "received": state.get("_last_request_received_summary", ""),', '                "received": "",', None),
    ('                "accepted": state.get("_last_request_accepted_summary", ""),', '                "accepted": "",', None),
    ('                "sync": state.get("_last_sync_time_label", ""),', '                "sync": "",', None),
    ('                "error": state.get("_last_server_error", ""),', '                "error": "",', None),
    ('            "attention": dict(attention) if isinstance(attention, dict) else None,', '            "attention": None,', None),
    ('            "queue_rev": state.get("_queue_revision", 0),', '            "queue_rev": 0,', None),
    ('        queue = [{"name": s.get("name"), "rotation_marker": bool(s.get("rotation_marker"))} for s in (self.queue or []) if isinstance(s, dict)]', '        queue = [{"name": s.get("name"), "rotation_marker": False} for s in (self.queue or []) if isinstance(s, dict)]', None),
    ('        queue = [{"name": s.get("name"), "rotation_marker": bool(s.get("rotation_marker"))} for s in (self.queue or []) if isinstance(s, dict)]', '        queue = [{"name": s.get("name"), "rotation_marker": bool(s.get("rotation_marker"))} for s in (self.queue or []) if isinstance(s, dict) and s.get("name")]', None),
    ('            total_secs = self._compute_queue_eta_seconds()[1]', '            total_secs = self._compute_queue_eta_seconds()[0]', None),
    ('            "locked": bool(self._is_rotation_locked()),', '            "locked": False,', None),
    ('            "looping": bool(state.get("_intro_loop_active", False)),', '            "looping": False,', None),
    ('        if not cached or now - cached[0] >= 30.0:', '        if True:', None),
    ('        if not cached or now - cached[0] >= 30.0:', '        if not cached or now - cached[0] > 30.0:', None),
    ('        if not cached or now - cached[0] >= 30.0:', '        if not cached or now - cached[0] >= 3000.0:', None),
    ('            "local_name": local_name,', '            "local_name": local_name or None,', "an empty name and None are both falsy to the report"),
    ('            "screen_count": screen_count,', '            "screen_count": 2,', None),
    ('            "karafun_path": str(karafun_path) if karafun_path else None,', '            "karafun_path": None,', None),
    ('            "output_host": state.get("_mpv_output_host") is not None,', '            "output_host": True,', None),
    ('            "preview_host": state.get("_mpv_preview_host") is not None,', '            "preview_host": True,', None),
    ('            "show_ticker": getattr(state.get("video_window"), "ticker", None) is not None,', '            "show_ticker": True,', None),
    ('            "qr_url": qr_url,', '            "qr_url": "",', None),
    ('            "bg_files": list(bg_files),', '            "bg_files": [],', None),
    ('            bg_files = scan_background_video_folder(str(settings.get("bg_video_folder", "") or "").strip())', '            bg_files = scan_background_video_folder("")', "the test double ignores the folder argument; the folder text itself is covered by the settings mutants"),
    ('            karafun_path = self._karafun_application_path() if bool(settings.get("karafun_provider_enabled", True)) else None', '            karafun_path = self._karafun_application_path()', "the report ignores the path while the provider is disabled"),
    ('            if not bool((getattr(self, "settings", None) or {}).get("status_snapshot", False)):\n                return False\n            _save_json_atomic(STATUS_SNAPSHOT_PATH', '            _save_json_atomic(STATUS_SNAPSHOT_PATH', None),
    ('            if not bool((getattr(self, "settings", None) or {}).get("status_snapshot", False)):\n                return\n            state = object', '            state = object', None),
    ('            timer.start(500)', '            timer.start(1)', None),
    ('            if timer is None:\n                timer = QTimer(self)\n                timer.setSingleShot(True)\n                timer.timeout.connect(self._write_status_snapshot)\n                self._status_snapshot_timer = timer', '            if True:\n                timer = QTimer(self)\n                timer.setSingleShot(True)\n                timer.timeout.connect(self._write_status_snapshot)\n                self._status_snapshot_timer = timer', None),
    ('        self._refresh_queue_summary()\n        self._schedule_status_snapshot()', '        self._refresh_queue_summary()', None),
    ('                self.header_location_label.setText(f"• {tenant_name}")\n        except Exception:\n            pass\n        self._schedule_status_snapshot()', '                self.header_location_label.setText(f"• {tenant_name}")\n        except Exception:\n            pass', None),
    ('    "status_snapshot": True,', '    "status_snapshot": False,', None),
    ('STATUS_SNAPSHOT_PATH = APP_USER_DIR / "status_snapshot.json"', 'STATUS_SNAPSHOT_PATH = APP_USER_DIR / "status_snapshot.json"\n_x = STATUS_SNAPSHOT_PATH.read_text() if STATUS_SNAPSHOT_PATH.exists() else ""', None),
]

ONLY = {int(x) for x in os.environ.get("MUTANTS", "").split(",") if x}


def sh(cmd, cwd, timeout):
    return subprocess.run(cmd, cwd=cwd, env=ENV, capture_output=True, text=True, timeout=timeout)


def rebuild():
    return sh([CARGO, "build", "--release", "-p", "singws-uimodel"], ROOT / "rust", 600)


def pytest():
    return sh([PY, "-m", "unittest", "test_status_snapshot"], ROOT, 900)


def one(label, src, mutants, rust):
    text = src.read_text()
    survivors, equivalents, killed = [], [], 0
    try:
        for n, (old, new, reason) in enumerate(mutants, 1):
            if ONLY and n not in ONLY:
                continue
            if old not in text:
                print(f"[{label} {n:02d}] PATTERN NOT FOUND {old[:60]!r}")
                survivors.append(n)
                continue
            src.write_text(text.replace(old, new, 1))
            how = None
            try:
                if rust:
                    r = sh([CARGO, "test", "--release", "-p", "singws-uimodel", "--lib", "status", "--quiet"], ROOT / "rust", 600)
                    if r.returncode != 0:
                        how = "build" if "could not compile" in r.stderr else "unit tests"
                    elif rebuild().returncode != 0:
                        how = "build"
                if how is None:
                    p = pytest()
                    if p.returncode != 0:
                        how = "test_status_snapshot"
            except subprocess.TimeoutExpired:
                how = "hang"
            if how:
                killed += 1
                print(f"[{label} {n:02d}] killed by {how}  {old[:50]!r}")
            elif reason:
                equivalents.append(n)
                print(f"[{label} {n:02d}] EQUIVALENT ({reason[:70]})")
            else:
                survivors.append(n)
                print(f"[{label} {n:02d}] SURVIVED  {old[:70]!r}")
    finally:
        src.write_text(text)
        os.utime(src, None)
        if rust:
            rebuild()
    print(f"{label}: {len(mutants)} mutants, killed {killed}, equivalent {equivalents}, survivors {survivors}")
    return survivors


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    bad = []
    if which in ("rust", "all"):
        bad += one("rust", RS, RUST, True)
    if which in ("python", "all"):
        bad += one("python", PYSRC, PYTHON, False)
    sys.exit(1 if bad else 0)
