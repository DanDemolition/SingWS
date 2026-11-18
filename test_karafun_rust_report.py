import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import contextlib
import io
import os
import tempfile
import unittest

from tools import karafun_rust_report as kr

LAUNCH = '[10:00:00] [INFO] [LAUNCH] {"version": "1.0.3.10"}'


def W(msg, t="10:01:00"):
    return f"[{t}] [INFO] [KARAFUN-WEB] {msg}"


def K(msg, t="10:01:00"):
    return f"[{t}] [INFO] [KARAFUN] {msg}"


ENGINE = W("engine=rust: the Rust driver runs the player")
SPAWN = W("the Rust driver runs the KaraFun player (pid 123)")
SONG1 = [W("start id=1 title='A' key=0 tempo=100"), W("started after 3.5s title='A' duration=200s")]
SONG2 = [W("start id=2 title='B' key=0 tempo=100"), W("started after 3.6s title='B' duration=180s")]
END = K("status=completed title='A' message='Completed in KaraFun'")
SEEK = W("seek to 101s")
KEY = W("live key/tempo +2/90%")
SKIP = K("finish requested action='return_to_queue' submission_state='karafun_queued'")
SIGNIN = W("signing in with the saved KaraFun login")
FULL = [LAUNCH, ENGINE, SPAWN, SIGNIN, *SONG1, SEEK, KEY, END, *SONG2, SKIP]


def run(lines, **kw):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "singws_2026-11-18.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        report = kr.analyse(kr.shadow_report.read_lines([path], last_launch_only=not kw.get("all", False)))
        return report


class KarafunRustReportTests(unittest.TestCase):
    def test_a_full_rehearsal_is_clean(self):
        r = run(FULL)
        self.assertEqual(r.verdict, "CLEAN", kr.render(r))
        self.assertEqual((r.counts["start_song"], r.counts["played"], r.counts["completed"], r.counts["returned"], r.counts["seek"], r.counts["key_tempo"]), (2, 2, 1, 1, 1, 1))
        self.assertEqual(kr.exit_status(r, True), 0)

    def test_missing_steps_are_partial_and_named(self):
        r = run([LAUNCH, ENGINE, SPAWN, *SONG1])
        self.assertEqual(r.verdict, "PARTIAL")
        for step in ("song ended by itself", "a second song started (the next song)", "seek", "key/tempo change", "skip (Stop / return to queue)"):
            self.assertIn(step, r.unseen)
        self.assertNotIn("sign-in", r.unseen, "a profile that was already signed in writes no sign-in line")
        self.assertEqual(kr.exit_status(r, False), 0)
        self.assertEqual(kr.exit_status(r, True), 3)

    def test_the_python_host_is_not_rust(self):
        r = run([LAUNCH, W("engine=rust: the Rust driver is not here; using the Python host"), *SONG1])
        self.assertEqual(r.verdict, "NOT RUST")
        self.assertIn("Python host ran", r.detail)
        self.assertEqual(kr.exit_status(r, False), 3)
        self.assertEqual(run([LAUNCH, *SONG1]).verdict, "NOT RUST")

    def test_rust_but_no_song_is_not_exercised(self):
        r = run([LAUNCH, ENGINE, SPAWN])
        self.assertEqual(r.verdict, "NOT EXERCISED")
        self.assertEqual(kr.exit_status(r, False), 3)

    def test_each_problem_is_attention_even_when_every_step_was_seen(self):
        problems = [W("the Rust driver stopped"), K("status=failed title='A' message='x'"), W("giving up on the song: no picture"),
                    W("NO PICTURE FRAMES from the graphics chip 8 s after the picture was shown"), W("the Rust driver did not answer seek in 8s"),
                    W("the Rust driver is not answering"), W("effect failed (effect): ValueError()"), W("unknown effect from the Rust driver: x"),
                    f"[10:02:00] [WARN] [TRANSPORT-SHADOW] MISMATCH app auto-completed session 3; machine had not completed it"]
        for line in problems:
            r = run([*FULL, line])
            self.assertEqual(r.verdict, "ATTENTION", line)
            self.assertEqual(kr.exit_status(r, False), 1, line)
            self.assertTrue(r.problems, line)

    def test_the_stray_song_line_is_a_note_not_a_failure(self):
        r = run([*FULL, W("the player was playing a song SingWS did not ask for; stopped it", "10:05:00")])
        self.assertEqual(r.verdict, "CLEAN")
        self.assertEqual(len(r.notes), 1)

    def test_only_the_last_launch_is_read_unless_all(self):
        lines = [LAUNCH, ENGINE, SPAWN, *SONG1, W("the Rust driver stopped"), LAUNCH, ENGINE, SPAWN, *SONG1, *SONG2]
        self.assertEqual(run(lines).counts["driver_stopped"], 0)
        self.assertEqual(run(lines, all=True).counts["driver_stopped"], 1)

    def test_the_text_and_the_main_entry(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "singws_x.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(FULL) + "\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(kr.main([path, "--require-all"]), 0)
            self.assertIn("KaraFun (Rust driver): CLEAN", out.getvalue())
            self.assertIn("songs started 2", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(kr.main(["--checklist"]), 0)
            self.assertIn("Sign out and back in", out.getvalue())
            self.assertEqual(kr.main([os.path.join(d, "missing.log")]), 2)


if __name__ == "__main__":
    unittest.main()
