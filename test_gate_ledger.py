"""tools/gate_ledger.py: which decisions a show or rehearsal proves, the ledger file, READY / REGRESSED."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import gate_ledger as gl  # noqa: E402
import shadow_report as sr  # noqa: E402
import test_shadow_report as tsr  # noqa: E402


def analyse_lines(lines):
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "singws_2026-10-23.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return sr.analyse(sr.read_lines([path], True))


class DecisionsProven(unittest.TestCase):
    def proven(self, lines):
        return gl.decisions_proven(analyse_lines(lines))

    def test_a_full_clean_session_proves_every_shadow_decision(self):
        p = self.proven([tsr.LAUNCH] + tsr.FULL)
        for key in ("SETTINGS-SHADOW:load", "SETTINGS-SHADOW:defaults", "SETTINGS-SHADOW:save", "QUEUE-SHADOW:tombstone", "PLAN-SHADOW:window", "WIRE-SHADOW:get", "START-SHADOW:route:mp4"):
            self.assertIs(p.get(key), True, (key, p))

    def test_a_disagreement_clears_nothing_in_its_row_but_not_other_rows(self):
        lines = [tsr.LAUNCH] + tsr.FULL + [tsr.L("PLAN-SHADOW", "MISMATCH op=window app=1 rust=2")]
        p = self.proven(lines)
        self.assertIs(p["PLAN-SHADOW:window"], False)
        self.assertIs(p["PLAN-SHADOW:crossfade"], False)
        self.assertIs(p["SETTINGS-SHADOW:load"], True)

    def test_the_drive_row_is_judged_per_decision(self):
        lines = [tsr.LAUNCH, tsr.L("TRANSPORT-DRIVE", "decision=media_end rust=end python=end disagreement=0"),
                 tsr.L("TRANSPORT-DRIVE", "decision=karafun_end rust=end python=idle disagreement=1")]
        p = self.proven(lines)
        self.assertIs(p["TRANSPORT-DRIVE:media_end"], True)
        self.assertIs(p["TRANSPORT-DRIVE:karafun_end"], False)

    def test_start_shadow_kinds_are_folded_into_their_decisions(self):
        lines = [tsr.LAUNCH, tsr.L("START-SHADOW", "enabled available=1"), tsr.L("START-SHADOW", "decision route=mp4 agree=1"), tsr.L("START-SHADOW", "decision route=pick agree=1"),
                 tsr.L("START-SHADOW", "decision route=pick_none agree=1"), tsr.L("START-SHADOW", "decision route=display agree=1"),
                 tsr.L("START-SHADOW", "decision route=rotation agree=1"), tsr.L("START-SHADOW", "decision route=engine_voice=1_reason=- agree=1")]
        p = self.proven(lines)
        self.assertEqual({k for k in p if k.startswith("START-SHADOW")}, {"START-SHADOW:route:mp4", "START-SHADOW:pick", "START-SHADOW:display", "START-SHADOW:rotation", "START-SHADOW:engine"})
        self.assertTrue(all(p[k] for k in p if k.startswith("START-SHADOW")))

    def test_shell_commands_count_only_when_nothing_failed(self):
        ok = [tsr.LAUNCH, tsr.L("SHELL-CMD", "ready mode=dry_run queue_sig=x singers=1"),
              tsr.L("SHELL-CMD", "id=a action=queue.move_singer mode=dry_run ok=True message=dry_run: would queue.move_singer")]
        self.assertIs(self.proven(ok)["SHELL-COMMANDS:dry_run_command"], True)
        bad = ok + [tsr.L("SHELL-CMD", "id=b action=queue.add_song mode=dry_run ok=False message=failed: boom")]
        self.assertIs(self.proven(bad)["SHELL-COMMANDS:dry_run_command"], False)


class LedgerFile(unittest.TestCase):
    def test_add_and_status_round_trip_and_the_policy_is_one_clean_show(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.json")
            log = os.path.join(d, "singws_2026-10-28.log")
            open(log, "w").write("\n".join([tsr.LAUNCH] + tsr.FULL) + "\n")
            self.assertEqual(gl.main(["add", log, "--label", "rehearsal one", "--ledger", path, "--settings", os.path.join(d, "none.json"), "--date", "2026-10-28"]), 0)
            data = json.load(open(path))
            self.assertEqual(data["decisions"]["SETTINGS-SHADOW:load"]["clean"][0]["label"], "rehearsal one")
            rows = {r["decision"]: r for r in gl.status(gl.load(path))}
            self.assertEqual(rows["SETTINGS-SHADOW:load"]["verdict"], "READY", "one clean session is enough")
            self.assertEqual(rows["START-SHADOW:route:mp3g_zip"]["verdict"], "NEEDS A CLEAN SHOW", "never seen in this session")
            self.assertEqual(gl.main(["status", "--ledger", path]), 0)

    def test_a_later_bad_session_regresses_a_decision_until_a_clean_one_follows(self):
        data = {"policy": "x", "decisions": {}}
        gl.record(data, {"PLAN-SHADOW:window": True}, "a", ["x.log"], "2026-10-28")
        self.assertEqual({r["decision"]: r["verdict"] for r in gl.status(data)}["PLAN-SHADOW:window"], "READY")
        gl.record(data, {"PLAN-SHADOW:window": False}, "b", ["y.log"], "2026-10-29")
        self.assertEqual({r["decision"]: r["verdict"] for r in gl.status(data)}["PLAN-SHADOW:window"], "REGRESSED")
        gl.record(data, {"PLAN-SHADOW:window": True}, "c", ["z.log"], "2026-10-30")
        self.assertEqual({r["decision"]: r["verdict"] for r in gl.status(data)}["PLAN-SHADOW:window"], "READY")

    def test_an_after_show_zip_is_read_with_its_logs_and_its_reduced_settings(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, "singws-show-20261028-2145.zip")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("singws-show-20261028-2145/singws_2026-10-28.log", "\n".join([tsr.LAUNCH] + tsr.FULL) + "\n")
                z.writestr("singws-show-20261028-2145/singws_2026-10-27.log", "\n".join([tsr.LAUNCH]) + "\n")
                z.writestr("singws-show-20261028-2145/settings.json", json.dumps({"plan_shadow": True, "queue_shadow": True, "settings_shadow": True}))
                z.writestr("singws-show-20261028-2145/notes.txt", "ignored")
            path = os.path.join(d, "ledger.json")
            self.assertEqual(gl.main(["add", archive, "--label", "rehearsal intel", "--ledger", path, "--date", "2026-10-28"]), 0)
            data = json.load(open(path))
            entry = data["decisions"]["PLAN-SHADOW:window"]["clean"][0]
            self.assertEqual(entry["logs"], ["singws_2026-10-27.log", "singws_2026-10-28.log"], "log names without the extraction prefix; notes.txt and folders ignored")
            self.assertEqual(entry["label"], "rehearsal intel")

    def test_a_missing_or_broken_ledger_starts_empty_and_no_log_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(gl.load(os.path.join(d, "nope.json"))["decisions"], {})
            open(os.path.join(d, "bad.json"), "w").write("not json")
            self.assertEqual(gl.load(os.path.join(d, "bad.json"))["decisions"], {})
            self.assertEqual(gl.main(["add", os.path.join(d, "missing.log"), "--label", "x", "--ledger", os.path.join(d, "l.json")]), 2)


if __name__ == "__main__":
    unittest.main()
