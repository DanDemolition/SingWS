import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tools import shadow_report as sr

LAUNCH = '[10:00:00] [INFO] [LAUNCH] {"build_id":"1.0.1.4"}'


def L(tag, msg, t="10:05:00"):
    return f"[{t}] [INFO] [{tag}] {msg}"


FULL = [
    L("SETTINGS-SHADOW", "enabled available=1"),
    L("SETTINGS-SHADOW", "decision check=load agree=1"), L("SETTINGS-SHADOW", "decision check=defaults agree=1"),
    L("SETTINGS-SHADOW", "decision check=save agree=1"),
    L("QUEUE-SHADOW", "enabled available=1"),
    L("QUEUE-SHADOW", "decision branch=tombstone answer=RejectTombstone agree=1"),
    L("QUEUE-SHADOW", "decision branch=terminal_stale answer=RejectTerminalStale agree=1"),
    L("PLAN-SHADOW", "enabled available=1"),
    L("PLAN-SHADOW", "decision op=window kind=bounds agree=1"),
    L("PLAN-SHADOW", "decision op=crossfade kind=natural_fade agree=1"),
    L("WIRE-SHADOW", "enabled available=1"),
    L("WIRE-SHADOW", "decision endpoint=get_requests_v2.php kind=get agree=1"),
    L("WIRE-SHADOW", "decision endpoint=delete_remote_request.php kind=form agree=1"),
    L("STORE-SHADOW", "enabled available=1"),
    L("STORE-SHADOW", "decision store=queue.json layout=indent2+newline agree=1"),
    L("STORE-SHADOW", "decision store=singer_history.json layout=indent2+newline agree=1"),
    L("TRANSPORT-SHADOW", "enabled available=1"),
    L("TRANSPORT-SHADOW", "session 1 started source=karafun external=1 duration=240.0"),
    L("TRANSPORT-SHADOW", "session 1 summary events=40 total_mismatches=0"),
    L("TRANSPORT-DRIVE", "decision=karafun_end python=1 rust=1 selected=1 owner=rust disagreement=0"),
    L("TRANSPORT-DRIVE", "decision=media_end python=1 rust=1 selected=1 owner=rust disagreement=0"),
]


def run(lines, *argv):
    if "--settings" not in argv:
        argv = (*argv, "--settings", "/nonexistent/settings.json")  # never read the machine's real settings.json from a test
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "singws_2026-10-23.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            status = sr.main([str(path), *argv])
    return status, buf.getvalue()


def verdicts(lines, *argv):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "x.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {r.name: r.verdict for r in sr.analyse(sr.read_lines([str(path)], "--all" not in argv))}


SHELL_READY = L("SHELL-CMD", "ready mode=dry_run queue_sig=0123456789abcdef singers=3")


def shell(msg, t="10:06:00"):
    return L("SHELL-CMD", msg, t)


class ShellCommandRowTests(unittest.TestCase):
    def verdict(self, lines, settings=None):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.log"
            path.write_text("\n".join([LAUNCH] + lines) + "\n", encoding="utf-8")
            rows = {r.name: r for r in sr.analyse(sr.read_lines([str(path)], True), settings)}
        return rows["SHELL-COMMANDS"]

    def test_off_by_default_and_it_does_not_hold_back_require_all(self):
        self.assertEqual(self.verdict(FULL).verdict, "NOT ENABLED")
        self.assertEqual(self.verdict(FULL, {"shell_commands": "off"}).verdict, "NOT ENABLED")
        status, out = run([LAUNCH] + FULL, "--require-all")
        self.assertEqual(status, 0, out)
        self.assertIn("SHELL-COMMANDS", out)

    def test_on_but_unused_is_not_exercised_and_then_require_all_notices(self):
        self.assertEqual(self.verdict([SHELL_READY]).verdict, "NOT EXERCISED")
        self.assertEqual(self.verdict(FULL, {"shell_commands": "dry_run"}).verdict, "NOT EXERCISED")
        with tempfile.TemporaryDirectory() as td:
            settings = Path(td) / "settings.json"
            settings.write_text('{"shell_commands": "dry_run"}')
            status, _ = run([LAUNCH] + FULL + [SHELL_READY], "--require-all", "--settings", str(settings))
        self.assertEqual(status, 3)

    def test_dry_run_commands_are_clean_and_counted(self):
        lines = [SHELL_READY,
                 shell("id=1 action=queue.move_singer mode=dry_run ok=True message=dry_run: would queue.move_singer"),
                 shell("id=2 action=play_pause mode=dry_run ok=True message=dry_run: would play_pause"),
                 shell("id=3 action=queue.move_singer mode=dry_run ok=False message=stale_view"),
                 shell("id=4 action=queue.remove_song mode=dry_run ok=False message=unknown_entry"),
                 shell("id=5 action=queue.noop mode=dry_run ok=True message=noop")]
        r = self.verdict(lines, {"shell_commands": "dry_run"})
        self.assertEqual(r.verdict, "CLEAN")
        self.assertEqual(r.seen, {"queue.move_singer", "play_pause", "queue.remove_song", "queue.noop"})
        self.assertIn("dry_run: 5 command(s)", r.detail)
        self.assertIn("stale_view=1", r.detail, "normal refusals are counted, not errors")
        self.assertEqual(r.errors, [])

    def test_defects_are_errors(self):
        for message in ("failed: boom", "clock_skew", "missing_sent_at", "bad_direction"):
            r = self.verdict([SHELL_READY, shell(f"id=1 action=queue.move_singer mode=dry_run ok=False message={message}")], {"shell_commands": "dry_run"})
            self.assertEqual(r.verdict, "ERROR", message)

    def test_an_applied_command_during_a_dry_run_is_an_error(self):
        lines = [SHELL_READY, shell("id=1 action=queue.move_singer mode=apply ok=True message=moved")]
        r = self.verdict(lines, {"shell_commands": "dry_run"})
        self.assertEqual(r.verdict, "ERROR")
        self.assertIn("APPLIED", r.errors[0])
        self.assertEqual(self.verdict(lines, {"shell_commands": "apply"}).verdict, "CLEAN", "in apply mode that is exactly what is expected")
        status, _ = run([LAUNCH] + FULL + lines)
        self.assertEqual(status, 0, "without settings the report cannot know it was a dry run")

    def test_only_the_last_launch_counts(self):
        lines = [shell("id=1 action=queue.noop mode=dry_run ok=True message=noop"), LAUNCH, SHELL_READY]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.log"
            path.write_text("\n".join([LAUNCH] + lines) + "\n", encoding="utf-8")
            rows = {r.name: r for r in sr.analyse(sr.read_lines([str(path)], True))}
        self.assertEqual(rows["SHELL-COMMANDS"].verdict, "NOT EXERCISED")


class VerdictTests(unittest.TestCase):
    def test_a_full_clean_session_is_clean_everywhere_and_exits_zero(self):
        v = {k: x for k, x in verdicts([LAUNCH] + FULL).items() if k not in sr.OPTIONAL_ROWS}
        self.assertEqual(set(v.values()), {"CLEAN"}, v)
        status, out = run([LAUNCH] + FULL, "--require-all")
        self.assertEqual(status, 0, out)
        self.assertIn("every shadow saw all its expected decisions and none disagreed", out)

    def test_a_shadow_that_was_on_but_saw_only_some_decisions_is_partial_with_hints(self):
        lines = [LAUNCH] + [l for l in FULL if "branch=terminal_stale" not in l]
        v = verdicts(lines)
        self.assertEqual(v["QUEUE-SHADOW"], "PARTIAL")
        status, out = run(lines)
        self.assertEqual(status, 0)
        self.assertIn("not seen: terminal_stale", out)
        self.assertIn("RESULT: nothing disagreed, but not every decision type was exercised", out)
        self.assertEqual(run(lines, "--require-all")[0], 3)

    def test_the_store_shadow_needs_the_queue_store_and_reports_the_others_as_optional(self):
        lines = [LAUNCH, L("STORE-SHADOW", "enabled available=1"), L("STORE-SHADOW", "decision store=settings.json layout=indent2+newline agree=1")]
        self.assertEqual(verdicts(lines)["STORE-SHADOW"], "PARTIAL")
        status, out = run(lines)
        self.assertIn("not seen: queue.json  (add or change a request so the queue is saved)", out)
        self.assertEqual(verdicts([LAUNCH] + FULL)["STORE-SHADOW"], "CLEAN")
        bad = [LAUNCH] + FULL + [L("STORE-SHADOW", "MISMATCH store=singer_history.json no_layout_reproduces_the_bytes tried=indent2 bytes=9")]
        self.assertEqual(verdicts(bad)["STORE-SHADOW"], "DISAGREED")
        worker = [LAUNCH] + FULL + [L("STORE-SHADOW", "worker error store=queue.json: ValueError('x')")]
        self.assertEqual(verdicts(worker)["STORE-SHADOW"], "ERROR")

    def test_the_wire_shadow_needs_a_get_and_a_form_post_and_lists_endpoints_and_declined_calls(self):
        lines = [LAUNCH, L("WIRE-SHADOW", "enabled available=1"), L("WIRE-SHADOW", "decision endpoint=get_requests_v2.php kind=get agree=1"),
                 L("WIRE-SHADOW", "declined endpoint=upload_songbook.php reason=multipart")]
        self.assertEqual(verdicts(lines)["WIRE-SHADOW"], "PARTIAL")
        status, out = run(lines)
        self.assertIn("not seen: form  (do something that POSTs to the server", out)
        self.assertIn("1 endpoint(s): get_requests_v2.php", out)
        self.assertIn("declined (not compared): upload_songbook.php:multipart", out)
        self.assertEqual(verdicts([LAUNCH] + FULL)["WIRE-SHADOW"], "CLEAN")
        bad = [LAUNCH] + FULL + [L("WIRE-SHADOW", "MISMATCH endpoint=delete_remote_request.php kind=form fields=body first_body_difference_at=4 sent_len=9 rust_len=8")]
        self.assertEqual(verdicts(bad)["WIRE-SHADOW"], "DISAGREED")

    def test_rare_optional_decisions_do_not_make_a_shadow_partial(self):
        v = verdicts([LAUNCH] + FULL)
        self.assertEqual(v["QUEUE-SHADOW"], "CLEAN")  # id_reuse / terminal_reuse never happened and that is fine
        self.assertIn("not seen (rare, optional): id_reuse", run([LAUNCH] + FULL)[1])

    def test_enabled_but_silent_is_not_exercised_not_clean(self):
        lines = [LAUNCH, L("PLAN-SHADOW", "enabled available=1")]
        self.assertEqual(verdicts(lines)["PLAN-SHADOW"], "NOT EXERCISED")

    def test_a_shadow_that_never_logged_is_not_enabled(self):
        v = verdicts([LAUNCH])
        self.assertEqual({v[k] for k in ("SETTINGS-SHADOW", "QUEUE-SHADOW", "PLAN-SHADOW", "STORE-SHADOW", "WIRE-SHADOW", "TRANSPORT-SHADOW")}, {"NOT ENABLED"})
        self.assertEqual(v["TRANSPORT-DRIVE"], "NOT EXERCISED")

    def test_settings_tell_a_switched_off_shadow_from_one_nothing_reached(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.log"
            path.write_text(LAUNCH + "\n", encoding="utf-8")
            rows = list(sr.read_lines([str(path)], True))
            off = {r.name: r for r in sr.analyse(rows, {"queue_shadow": False, "plan_shadow": True, "settings_shadow": True})}
            self.assertEqual(off["QUEUE-SHADOW"].verdict, "NOT ENABLED")
            self.assertIn("queue_shadow is off", off["QUEUE-SHADOW"].detail)
            self.assertEqual(off["PLAN-SHADOW"].verdict, "NOT EXERCISED")
            self.assertIn("plan_shadow is on, but no decision reached it", off["PLAN-SHADOW"].detail)
            # transport_shadow defaults to ON, so a silent one means no song was played, not that it is off
            self.assertEqual(off["TRANSPORT-SHADOW"].verdict, "NOT EXERCISED")
            on = {r.name: r for r in sr.analyse(rows, {"transport_shadow": False})}
            self.assertEqual(on["TRANSPORT-SHADOW"].verdict, "NOT ENABLED")
            # the drive counts as on if any of its three flags is on
            drive = {r.name: r for r in sr.analyse(rows, {"transport_drive": False, "transport_drive_media_end": True})}
            self.assertEqual(drive["TRANSPORT-DRIVE"].verdict, "NOT EXERCISED")

    def test_settings_never_override_what_the_log_actually_shows(self):
        # the setting says off, but the log has decisions: the log wins
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.log"
            path.write_text("\n".join([LAUNCH] + FULL) + "\n", encoding="utf-8")
            rows = list(sr.read_lines([str(path)], True))
            got = {r.name: r.verdict for r in sr.analyse(rows, {"queue_shadow": False, "plan_shadow": False})}
            self.assertEqual((got["QUEUE-SHADOW"], got["PLAN-SHADOW"]), ("CLEAN", "CLEAN"))

    def test_a_missing_rust_library_is_unavailable_and_never_clean(self):
        lines = [LAUNCH, L("QUEUE-SHADOW", "enabled available=0")]
        self.assertEqual(verdicts(lines)["QUEUE-SHADOW"], "UNAVAILABLE")

    def test_any_mismatch_is_disagreed_and_exits_one(self):
        lines = [LAUNCH] + FULL + [L("SETTINGS-SHADOW", "MISMATCH check=save first_difference_at=12 rust_len=10 app_len=11", "10:09:00")]
        status, out = run(lines)
        self.assertEqual(status, 1)
        self.assertIn("DISAGREED", out)
        self.assertIn("10:09:00 MISMATCH check=save", out)
        self.assertIn("RESULT: 1 shadow(s) need attention: SETTINGS-SHADOW", out)

    def test_drive_disagreements_and_transport_mismatches_are_found(self):
        lines = [LAUNCH] + FULL + [
            L("TRANSPORT-DRIVE", "decision=karafun_end python=1 rust=0 selected=1 owner=python-fallback disagreement=1", "10:20:00"),
            L("TRANSPORT-SHADOW", "MISMATCH app auto-completed session 1; machine had not completed it (state=...)", "10:21:00"),
        ]
        v = verdicts(lines)
        self.assertEqual((v["TRANSPORT-DRIVE"], v["TRANSPORT-SHADOW"]), ("DISAGREED", "DISAGREED"))
        status, out = run(lines)
        self.assertEqual(status, 1)
        self.assertIn("karafun_end: 2 decision(s), 1 disagreement(s)", out)

    def test_a_shadow_internal_error_is_reported_even_when_nothing_disagreed(self):
        lines = [LAUNCH] + FULL + [L("PLAN-SHADOW", "check_window error: ValueError('x')")]
        self.assertEqual(verdicts(lines)["PLAN-SHADOW"], "ERROR")
        self.assertEqual(run(lines)[0], 1)

    def test_drive_fallback_errors_are_reported(self):
        lines = [LAUNCH] + FULL + [L("TRANSPORT-DRIVE", "decision=media_end fallback=python error=RuntimeError('x')")]
        self.assertEqual(verdicts(lines)["TRANSPORT-DRIVE"], "ERROR")
        # and a failed decision is not counted as an exercised one
        self.assertEqual(verdicts([LAUNCH, L("TRANSPORT-DRIVE", "decision=media_end fallback=python error=X")])["TRANSPORT-DRIVE"], "ERROR")


class SegmentationTests(unittest.TestCase):
    def test_only_the_last_app_launch_is_read_by_default(self):
        first = [LAUNCH, L("SETTINGS-SHADOW", "MISMATCH check=load old session", "09:00:00")]
        second = [L("SETTINGS-SHADOW", "enabled available=1", "11:00:01")] if False else []
        lines = first + ['[11:00:00] [INFO] [LAUNCH] {"build_id":"1.0.1.4"}'] + FULL
        self.assertEqual(verdicts(lines)["SETTINGS-SHADOW"], "CLEAN", "the earlier launch's mismatch is not part of this show")
        self.assertEqual(verdicts(lines, "--all")["SETTINGS-SHADOW"], "DISAGREED", "--all reads every launch")

    def test_a_log_with_no_launch_line_is_read_whole(self):
        self.assertEqual(verdicts(FULL)["PLAN-SHADOW"], "CLEAN")

    def test_no_log_is_exit_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sr.main(["/nonexistent/singws.log"]), 2)

    def test_unrelated_and_malformed_lines_are_ignored(self):
        noise = ["garbage", "", L("PERF-DIAG", "took 5ms"), L("SETTINGS-SHADOW", "decision check=load agree=1")]
        self.assertEqual(verdicts([LAUNCH] + noise)["SETTINGS-SHADOW"], "PARTIAL")


class RealFormatTests(unittest.TestCase):
    """The line formats the shadows really write, copied from the code that writes them."""

    def test_formats_match_the_writers(self):
        root = Path(__file__).parent
        expectations = {
            "settings_shadow.py": ["decision check={check} agree=1", "MISMATCH check={check}"],
            "queue_shadow.py": ["decision branch={branch} answer={got} agree=1", "MISMATCH request_id="],
            "plan_shadow.py": ["decision op={op} kind={kind} agree=1", "MISMATCH op={op}"],
            "transport_shadow.py": ["MISMATCH", "total_mismatches=", "started source="],
            "store_shadow.py": ["decision store={store} layout={layout} agree=1", "MISMATCH store={store}"],
            "wire_shadow.py": ["decision endpoint={endpoint} kind={kind} agree=1", "MISMATCH endpoint={endpoint}", "declined endpoint={endpoint} reason={reason}"],
        }
        for fname, needles in expectations.items():
            text = (root / fname).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{fname} no longer writes {needle!r}; update tools/shadow_report.py")
        app = (root / "0.2.18.1.py").read_text(encoding="utf-8")
        for tag in ("SETTINGS-SHADOW", "QUEUE-SHADOW", "PLAN-SHADOW", "TRANSPORT-SHADOW"):
            self.assertIn(f"[{tag}] enabled available=", app, f"the app no longer logs the {tag} 'enabled' line")
        self.assertIn("[TRANSPORT-DRIVE] decision=karafun_end", app)
        self.assertIn("[TRANSPORT-DRIVE] decision=media_end", app)
        self.assertIn("[TRANSPORT-DRIVE] decision=watchdog", app)
        self.assertIn("disagreement={int(disagreed)}", app)


if __name__ == "__main__":
    unittest.main()
