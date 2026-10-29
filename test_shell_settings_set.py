"""`settings.set`: the shell / phone changing the two ticker custom-message settings (the only keys on the schema; see `KaraokeApp._SHELL_SETTINGS_SCHEMA`).

Real code under test: `_execute_shell_queue_command` (gate, dedupe, expiry, validation), `shell_settings_set`, `_shell_setting_value_ok`. The same table of values the Rust validator is tested
against (docs/design/settings_set_cases.json) is run here, so the two sides cannot disagree about what is allowed. Recorded stand-ins: settings persistence and the ticker refresh."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import random
import time
import types
import unittest
from pathlib import Path

import test_shell_add_song as ta
import test_shell_queue_commands as base

CASES = json.loads((Path(__file__).parent / "docs/design/settings_set_cases.json").read_text())["cases"]


def harness(mode="apply", settings=None):
    h = ta.AddHarness(base.random_queue(random.Random(3)))
    a = h.app
    a.settings.update({"shell_commands": mode, "ticker_custom_enabled": False, "ticker_custom_message": "old", "api_key": "SECRET", "host_controls_pin": "1234",
                       "ticker_color": "#ffffff", "ticker_enabled": True})
    a.settings.update(settings or {})
    a.save_settings = lambda: h.record("save_settings")
    a.schedule_ticker_update = lambda: h.record("ticker_update")
    a._cached_ticker_data = (["Ann"], "old")
    assert "_cached_ticker_data" in object.__getattribute__(a, "__dict__")
    for name in ("shell_settings_set",):
        setattr(a, name, types.MethodType(getattr(h.m.KaraokeApp, name), a))
    return h


def run(h, args, rid):
    return h.app._execute_shell_queue_command("settings.set", dict(args, _request_id=rid, _sent_at=time.time()))


class SchemaTests(unittest.TestCase):
    def test_every_case_in_the_shared_table_is_accepted_or_refused_with_the_stated_code(self):
        for n, c in enumerate(CASES):
            h = harness()
            ok, code = run(h, c["args"], f"c{n}")
            if c["ok"]:
                self.assertTrue(ok, f"{c['args']}: {code}")
                self.assertIn(code, ("setting_changed", "no_change"), c["args"])
            else:
                self.assertEqual((ok, code), (False, c["code"]), c["args"])
                self.assertEqual(h.app.settings["ticker_custom_message"], "old", c["args"])
                self.assertEqual(h.app.settings["ticker_custom_enabled"], False, c["args"])
                self.assertEqual([x for x in h.calls if x[0] in ("save_settings", "ticker_update")], [], c["args"])

    def test_secrets_and_surface_settings_are_never_touched_by_any_refused_case(self):
        for n, c in enumerate(CASES):
            h = harness()
            run(h, c["args"], f"s{n}")
            a = h.app
            self.assertEqual((a.settings["api_key"], a.settings["host_controls_pin"], a.settings["ticker_color"], a.settings["ticker_enabled"]), ("SECRET", "1234", "#ffffff", True), c["args"])
        self.assertNotIn("base_url", harness().app.settings)

    def test_the_schema_has_exactly_the_two_ticker_message_keys(self):
        self.assertEqual(sorted(harness().m.KaraokeApp._SHELL_SETTINGS_SCHEMA), ["ticker_custom_enabled", "ticker_custom_message"])


class ApplyTests(unittest.TestCase):
    def test_a_change_is_stored_saved_and_refreshes_only_the_ticker_text(self):
        h = harness()
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "Tip your host | %nextSinger"}, "a1"), (True, "setting_changed"))
        a = h.app
        self.assertEqual(a.settings["ticker_custom_message"], "Tip your host | %nextSinger")
        self.assertNotIn("_cached_ticker_data", object.__getattribute__(a, "__dict__"), "the cached ticker text was dropped")
        self.assertEqual([x[0] for x in h.calls if x[0] in ("save_settings", "ticker_update")], ["save_settings", "ticker_update"])
        self.assertEqual(run(h, {"key": "ticker_custom_enabled", "value": True}, "a2"), (True, "setting_changed"))
        self.assertIs(a.settings["ticker_custom_enabled"], True)

    def test_the_same_value_is_no_change_and_writes_nothing(self):
        h = harness()
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "old"}, "n1"), (True, "no_change"))
        self.assertEqual(run(h, {"key": "ticker_custom_enabled", "value": False}, "n2"), (True, "no_change"))
        self.assertEqual([x for x in h.calls if x[0] in ("save_settings", "ticker_update")], [])

    def test_a_number_saved_by_an_older_version_is_replaced_by_the_real_boolean(self):
        h = harness(settings={"ticker_custom_enabled": 1})
        self.assertEqual(run(h, {"key": "ticker_custom_enabled", "value": True}, "t1"), (True, "setting_changed"), "1 == True in Python, but the stored type differs")
        self.assertIs(h.app.settings["ticker_custom_enabled"], True)

    def test_a_missing_setting_is_set_not_no_change(self):
        h = harness()
        h.app.settings.pop("ticker_custom_message")
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": ""}, "m1"), (True, "setting_changed"))
        self.assertEqual(h.app.settings["ticker_custom_message"], "")

    def test_a_failing_save_does_not_raise_and_the_value_still_applies_in_memory(self):
        h = harness()
        h.app.save_settings = lambda: (_ for _ in ()).throw(OSError("disk full"))
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "x"}, "f1"), (True, "setting_changed"))
        self.assertEqual(h.app.settings["ticker_custom_message"], "x")


class GateTests(unittest.TestCase):
    def test_off_refuses_and_dry_run_checks_but_changes_nothing(self):
        h = harness("off")
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "x"}, "o1"), (False, "shell_commands_off"))
        self.assertEqual(h.app.settings["ticker_custom_message"], "old")
        h = harness("dry_run")
        ok, msg = run(h, {"key": "ticker_custom_message", "value": "x"}, "d1")
        self.assertTrue(ok and msg.startswith("dry_run"), msg)
        self.assertEqual(run(h, {"key": "api_key", "value": "x"}, "d2"), (False, "unknown_setting"), "a dry run still refuses what apply would refuse")
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "x" * 201}, "d3"), (False, "bad_value"))
        self.assertEqual((h.app.settings["ticker_custom_message"], [x for x in h.calls if x[0] in ("save_settings", "ticker_update")]), ("old", []))

    def test_a_replayed_request_id_is_not_applied_twice(self):
        h = harness()
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "first"}, "same"), (True, "setting_changed"))
        self.assertEqual(run(h, {"key": "ticker_custom_message", "value": "second"}, "same"), (True, "duplicate: setting_changed"))
        self.assertEqual(h.app.settings["ticker_custom_message"], "first")

    def test_an_expired_or_unsent_command_is_refused(self):
        h = harness()
        a = h.app
        self.assertEqual(a._execute_shell_queue_command("settings.set", {"key": "ticker_custom_message", "value": "x", "_request_id": "e1", "_sent_at": time.time() - 60}), (False, "expired"))
        self.assertEqual(a._execute_shell_queue_command("settings.set", {"key": "ticker_custom_message", "value": "x", "_request_id": "e2"}), (False, "missing_sent_at"))
        self.assertEqual(a._execute_shell_queue_command("settings.set", {"key": "ticker_custom_message", "value": "x", "_sent_at": time.time()}), (False, "missing_request_id"))
        self.assertEqual(h.app.settings["ticker_custom_message"], "old")

    def test_the_host_entry_points_route_settings_commands(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        self.assertEqual(src.count('startswith(("queue.", "library.", "bg.", "eq.", "waitlist.", "settings."))'), 3)
        self.assertIn('"waitlist.add", "settings.set")\n', src)


if __name__ == "__main__":
    unittest.main()
