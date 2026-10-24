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
    L("TRANSPORT-SHADOW", "enabled available=1"),
    L("TRANSPORT-SHADOW", "session 1 started source=karafun external=1 duration=240.0"),
    L("TRANSPORT-SHADOW", "session 1 summary events=40 total_mismatches=0"),
    L("TRANSPORT-DRIVE", "decision=karafun_end python=1 rust=1 selected=1 owner=rust disagreement=0"),
    L("TRANSPORT-DRIVE", "decision=media_end python=1 rust=1 selected=1 owner=rust disagreement=0"),
]


def run(lines, *argv):
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


class VerdictTests(unittest.TestCase):
    def test_a_full_clean_session_is_clean_everywhere_and_exits_zero(self):
        v = verdicts([LAUNCH] + FULL)
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

    def test_rare_optional_decisions_do_not_make_a_shadow_partial(self):
        v = verdicts([LAUNCH] + FULL)
        self.assertEqual(v["QUEUE-SHADOW"], "CLEAN")  # id_reuse / terminal_reuse never happened and that is fine
        self.assertIn("not seen (rare, optional): id_reuse", run([LAUNCH] + FULL)[1])

    def test_enabled_but_silent_is_not_exercised_not_clean(self):
        lines = [LAUNCH, L("PLAN-SHADOW", "enabled available=1")]
        self.assertEqual(verdicts(lines)["PLAN-SHADOW"], "NOT EXERCISED")

    def test_a_shadow_that_never_logged_is_not_enabled(self):
        v = verdicts([LAUNCH])
        self.assertEqual({v[k] for k in ("SETTINGS-SHADOW", "QUEUE-SHADOW", "PLAN-SHADOW", "TRANSPORT-SHADOW")}, {"NOT ENABLED"})
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
