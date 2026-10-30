"""The read-only status snapshot the Rust shell's Status and Diagnostics pages draw from.

The app writes `status_snapshot.json` (setting `status_snapshot`, default off) whenever the header or the SINGERS / ETA line refreshes. These tests drive the REAL
`KaraokeApp` methods (`_refresh_header_chips`, `_refresh_header_status`, `_update_queue_eta_label`, `_refresh_queue_summary`, `_collect_pre_show_diagnostics`) over
random states and check:
* the setting off writes nothing and creates no timer; on, one coalesced timer writes one file;
* what Rust (`singws-status-cli`) builds from the snapshot equals what the app put on its own labels and in its own check list;
* the app never reads the file back, a failing write or a failing hook can never reach the app's own refresh, and the file holds no secret.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import os
import random
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import test_show_critical_regressions as base

CLI = os.environ.get("SINGWS_STATUS_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-ui"), "release", "singws-status-cli")
TONES = {(52, 224, 138): "good", (245, 200, 75): "warn", (150, 156, 184): "mute", (168, 85, 247): "purple"}
NOW = 1_792_000_019      # second :59 of its minute, so a one-second clock error shows in the ENDS time
NAMES = ["Ann", "Bo & <Cy>", "  ", "Éva", "", "Gus"]


class Rec:
    def __init__(self):
        self.state = None
        self.text = None
        self.tip = None

    def set_state(self, text, color):
        self.state = [text, TONES[(color.red(), color.green(), color.blue())]]

    def setText(self, t):
        self.text = t

    def setToolTip(self, t):
        self.tip = t


class FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(NOW)


class StatusSnapshot(unittest.TestCase):
    bare_app = base.ShowCriticalRegressionTests.bare_app

    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()

    def make_app(self, rng=None, enabled=True):
        app = self.bare_app()
        rng = rng or random.Random(1)
        settings = {"status_snapshot": enabled}
        for k in ("requests_accepting", "use_waiting_for_add", "karafun_provider_enabled", "ticker_enabled", "bg_video_enabled"):
            if rng.random() < 0.8:
                settings[k] = rng.choice([True, False, 1, 0])
        for k in ("location_name", "venue_name", "tenant", "user", "bg_video_folder"):
            if rng.random() < 0.4:
                settings[k] = rng.choice(["Joe's Bar", "  Padded  ", "", "Café Ünal", "<b>x</b>", "/vj"])
        if rng.random() < 0.8:
            settings["queue_mode"] = rng.choice(["rotation", "classic", " Rotation ", "ROTATION", ""])
        settings["api_key"] = "SECRET-KEY"
        settings["host_controls_pin"] = "1234"
        app.settings = settings
        queue = []
        for name in rng.sample(NAMES, rng.randint(0, 5)):
            songs = [{"title": "s", "duration": rng.choice([0, 95, 200, 245.5]), "skipped": rng.random() < 0.2} for _ in range(rng.randint(0, 2))]
            queue.append({"name": name, "songs": songs, "skipped": rng.random() < 0.15, "has_sung": rng.random() < 0.2, "rotation_marker": rng.random() < 0.2})
        app.queue = queue
        app.karaoke_playing = False
        app._server_connection_state = rng.choice(["", "Connected", "Offline", "Reconnecting", "Auth failed"])
        app._last_request_received_summary = rng.choice(["", "12:01", "Ünal"])
        app._last_request_accepted_summary = rng.choice(["", "12:02"])
        app._last_sync_time_label = rng.choice(["", "12:03:04"])
        app._last_server_error = rng.choice(["", "boom"])
        app._remote_attention_requests = rng.choice([None, {}, {"1": {}, "2": {}}])
        app._queue_revision = rng.choice([0, 7, "9"])
        app._intro_loop_active = rng.random() < 0.3
        net = rng.random() < 0.5
        locked = rng.random() < 0.3
        app._current_karaoke_singer_name = ""     # an un-initialised QWidget raises RuntimeError (not AttributeError) for attributes that were never set
        app._eta_hold_current_secs = 0
        app.is_network_configured = lambda: net
        app._is_rotation_locked = lambda: locked
        app._rotation_recompute_round_state = lambda force_reset=False: None
        app._ui_redesign_on = lambda: True
        app._rotation_marker_index = lambda: next((i for i, s in enumerate(queue) if s.get("rotation_marker")), -1)
        app._get_duration_secs = lambda p: 0
        app.header_chips = {k: Rec() for k in ("requests", "waitlist", "online", "summary")}
        app.header_status_label = Rec()
        app.header_location_label = Rec()
        app.queue_label = Rec()
        app.queue_summary_label = Rec()
        return app

    def facts(self, rng):
        return {"local_name": rng.choice([None, "", "MacBook Speakers", "Ünal USB"]), "screens": rng.choice([0, 1, 2, 3]),
                "karafun": rng.choice([None, "/Applications/KaraFun.app"]), "output": rng.random() < 0.7, "preview": rng.random() < 0.7,
                "ticker": rng.random() < 0.7, "qr": rng.choice(["", "https://x/tenants/a/"]), "bg": rng.choice([[], ["/vj/a.mp4"], ["/vj/a.mp4", "/vj/b.mp4"]])}

    def install_probes(self, app, f):
        app._safe_local_audio_output_name = lambda: f["local_name"]
        app._karafun_application_path = lambda: Path(f["karafun"]) if f["karafun"] else None
        app._header_qr_url = lambda: f["qr"]
        app._mpv_output_host = object() if f["output"] else None
        app._mpv_preview_host = object() if f["preview"] else None
        app.video_window = mock.MagicMock(ticker=object() if f["ticker"] else None)
        return [mock.patch.object(self.singws, "scan_background_video_folder", lambda folder: list(f["bg"])),
                mock.patch.object(self.singws.QApplication, "screens", staticmethod(lambda: [object()] * f["screens"])),
                mock.patch.object(self.singws, "datetime", FixedDT)]

    def rust_view(self, snapshot):
        out = subprocess.run([CLI], input=json.dumps(snapshot), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[:300])
        return json.loads(out.stdout)

    def app_view(self, app):
        app._update_queue_eta_label()      # also runs _refresh_header_chips and _refresh_queue_summary
        app._refresh_header_status()
        checks = app._collect_pre_show_diagnostics()
        lines = [f"{'PASS' if ok else 'CHECK'}  {label}: {detail}" for ok, label, detail in checks]
        return app, checks, lines

    def test_the_setting_is_on_by_default(self):
        self.assertIs(self.singws.DEFAULTS.get("status_snapshot"), True)

    def test_with_the_setting_off_nothing_is_written_and_no_timer_exists(self):
        app = self.make_app(enabled=False)
        made = []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.singws, "STATUS_SNAPSHOT_PATH", Path(d) / "status_snapshot.json"), mock.patch.object(self.singws, "QTimer", lambda *a: made.append(a)):
            app._schedule_status_snapshot()
            self.assertEqual(made, [])
            self.assertNotIn("_status_snapshot_timer", app.__dict__)
            self.assertFalse(app._write_status_snapshot())
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_a_burst_of_refreshes_shares_one_timer_and_writes_one_file_without_secrets(self):
        app = self.make_app()
        timers = []

        class FakeTimer:
            def __init__(self, *_a):
                self.timeout = mock.MagicMock()
                self.starts = []
                timers.append(self)

            def setSingleShot(self, v):
                pass

            def start(self, ms):
                self.starts.append(ms)

        probes = self.install_probes(app, self.facts(random.Random(3)))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(self.singws, "STATUS_SNAPSHOT_PATH", Path(d) / "status_snapshot.json"), mock.patch.object(self.singws, "QTimer", FakeTimer):
            for p in probes:
                p.start()
            try:
                for _ in range(4):
                    app._schedule_status_snapshot()
                self.assertEqual(len(timers), 1)
                self.assertEqual(timers[0].starts, [500] * 4)
                self.assertTrue(app._write_status_snapshot())
            finally:
                for p in probes:
                    p.stop()
            text = (Path(d) / "status_snapshot.json").read_text()
        data = json.loads(text)
        self.assertEqual(data["version"], 1)
        self.assertNotIn("SECRET-KEY", text)
        self.assertNotIn("host_controls_pin", text)
        self.assertNotIn("api_key", text)

    def test_the_snapshot_draws_what_the_app_drew_over_random_states(self):
        if not Path(CLI).exists():
            self.skipTest("needs singws-status-cli (cargo build --release -p singws-uimodel)")
        rng = random.Random(20261031)
        seen = {"locked": 0, "looping": 0, "ends": 0, "start": 0, "failed_check": 0, "all_passed": 0, "empty": 0}
        for case in range(300):
            app = self.make_app(rng)
            f = self.facts(rng)
            if rng.random() < 0.3:
                f.update(local_name="MacBook Speakers", screens=2, karafun="/Applications/KaraFun.app", output=True, preview=True, ticker=True, qr="https://x/")
                app.settings.update(karafun_provider_enabled=True, ticker_enabled=True)
            probes = self.install_probes(app, f)
            for p in probes:
                p.start()
            try:
                app, checks, lines = self.app_view(app)
                snapshot = app._status_snapshot_payload(now=NOW)
            finally:
                for p in probes:
                    p.stop()
            json.dumps(snapshot)
            got = self.rust_view(snapshot)
            ctx = (case, snapshot)
            self.assertNotIn("error", got, ctx)
            self.assertEqual(got["chips"], [c.state for c in (app.header_chips[k] for k in ("requests", "waitlist", "online", "summary"))], ctx)
            html = app.header_status_label.text
            self.assertEqual(f'<span style="color:{self.singws._v(got["status"]["dot_token"])};">●</span>  {got["status"]["text"]}', html, ctx)
            self.assertEqual(got["status"]["tooltip"], app.header_status_label.tip, ctx)
            self.assertEqual(f"• {got['status']['location']}", app.header_location_label.text, ctx)
            self.assertEqual(got["eta"]["html"], app.queue_label.text, ctx)
            self.assertEqual(got["summary"], app.queue_summary_label.text, ctx)
            self.assertEqual(got["report"]["lines"], lines, ctx)
            self.assertEqual(got["report"]["title"], f"{sum(1 for c in checks if c[0])} of {len(checks)} checks passed", ctx)
            seen["locked"] += bool(snapshot["locked"])
            seen["looping"] += bool(snapshot["looping"])
            seen["ends"] += "ENDS" in app.queue_label.text
            seen["start"] += "START" in app.queue_label.text
            seen["failed_check"] += not got["report"]["all_passed"]
            seen["all_passed"] += got["report"]["all_passed"]
            seen["empty"] += app.queue_summary_label.text == ""
        self.assertTrue(all(n > 10 for n in seen.values()), seen)

    def test_the_diagnostic_probes_are_taken_at_most_every_thirty_seconds(self):
        app = self.make_app()
        calls = []
        app._status_snapshot_diagnostic_facts = lambda: calls.append(1) or {"bg_files": []}
        app._status_snapshot_payload(now=1000.0)
        app._status_snapshot_payload(now=1029.0)
        self.assertEqual(len(calls), 1)
        app._status_snapshot_payload(now=1030.0)
        self.assertEqual(len(calls), 2)

    def test_a_write_that_fails_never_reaches_the_app(self):
        app = self.make_app()
        with mock.patch.object(self.singws, "_save_json_atomic", side_effect=OSError("disk full")), mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            self.assertFalse(app._write_status_snapshot())
        app._status_snapshot_payload = lambda: (_ for _ in ()).throw(RuntimeError("payload broke"))
        with mock.patch.object(self.singws, "_diag", lambda *a, **k: None):
            self.assertFalse(app._write_status_snapshot())

    def test_the_header_refreshes_survive_a_broken_snapshot_hook_and_ask_for_one(self):
        app = self.make_app()
        calls = []
        app._schedule_status_snapshot = lambda: calls.append(1)
        with mock.patch.object(self.singws, "datetime", FixedDT):
            app._update_queue_eta_label()
            app._refresh_header_status()
        self.assertEqual(len(calls), 2)
        app = self.make_app()
        app.settings["status_snapshot"] = True
        with mock.patch.object(self.singws, "QTimer", side_effect=RuntimeError("no timer")), mock.patch.object(self.singws, "datetime", FixedDT):
            app._refresh_header_status()      # must not raise
            self.assertEqual(app.header_status_label.text.count("Requests"), 1)

    def test_the_app_only_ever_writes_the_snapshot(self):
        source = (Path(__file__).parent / "0.2.18.1.py").read_text()
        uses = [m.start() for m in re.finditer(r"STATUS_SNAPSHOT_PATH", source)]
        self.assertEqual(len(uses), 2, "the definition and the one write")
        self.assertNotIn("_load_json_file(STATUS_SNAPSHOT_PATH", source)
        self.assertNotIn("STATUS_SNAPSHOT_PATH.read", source)


if __name__ == "__main__":
    unittest.main()
