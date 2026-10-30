"""Stage A5 step 9, LIVE: the whole-start-plan shadow inside the real `play_next_file` (setting `plan_shadow_live`).

The plan shadow only watches. These tests run random queues through the real chain with only the mpv transport replaced by a recorder, and check that it is silent when it
must be, that it never changes what the app does, that a wrong plan is logged as a MISMATCH, and that it cannot raise or cost the GUI thread anything measurable.
"""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import copy
import random
import time
import unittest
from unittest import mock

import start_shadow
import test_start_shadow_plan as plan_tests

PLAN_LINE = "decision route=plan agree=1"


class LivePlan(plan_tests.StartPlan):
    test_random_queues_agree_with_the_plan_over_the_real_chain = None   # the offline test of the parent class

    def run_chain(self, case_seed, settings, shadow_factory=None, start_shadow_setting=False):
        """One random queue through the real play_next_file. Returns (app, log lines, queue names after, queue length before)."""
        rng = random.Random(case_seed)
        singers = self.random_queue(rng)
        missing = {p for p in ("/lib/a.mp4", "/lib/a.mp3", "/lib/o.mp4", "/lib/a.cdg") if rng.random() < 0.15}
        zip_ready = rng.choice([("/t/x.cdg", "/t/x.mp3"), (None, None)])
        phrase = rng.choice([0.0, 3.25, 17.0])
        app = self.make_app(singers, phrase, zip_ready)
        app.settings = dict({"start_shadow": start_shadow_setting}, **settings)
        lines = []
        log = lambda text, *a, **k: lines.append(str(text))   # noqa: E731
        self.singws._START_SHADOW = shadow_factory(log) if shadow_factory else start_shadow.StartShadow(log)
        with mock.patch.object(self.singws, "_diag", log), \
                mock.patch.object(self.singws.os.path, "exists", lambda p: p not in missing), \
                mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None), \
                mock.patch.object(self.singws, "QThread", lambda *a, **k: mock.MagicMock()), \
                mock.patch.object(self.singws, "ZipExtractWorker", lambda *a, **k: mock.MagicMock()):
            app.play_next_file()
        return app, lines, [s["name"] for s in app.queue]

    def plan_lines(self, lines):
        return [x for x in lines if "START-SHADOW" in x and "plan" in x and "plan_shadow_live" not in x]

    def test_random_queues_log_one_agreeing_plan_line_per_launch_and_no_mismatch(self):
        agreed = started = 0
        for seed in range(300):
            app, lines, _names = self.run_chain(seed, {"plan_shadow_live": True})
            self.assertEqual([x for x in lines if "MISMATCH" in x or "error" in x and "START-SHADOW" in x], [], (seed, lines))
            plans = [x for x in lines if x.endswith(PLAN_LINE)]
            self.assertLessEqual(len(plans), 1, (seed, lines))
            if app.transport_calls:
                started += 1
                self.assertEqual(len(plans), 1, (seed, lines))
            agreed += len(plans)
        self.assertGreater(started, 40)
        self.assertGreater(agreed, 40)

    def test_it_changes_nothing_the_app_does(self):
        for seed in range(120):
            on, lines_on, names_on = self.run_chain(seed, {"plan_shadow_live": True})
            off, lines_off, names_off = self.run_chain(seed, {"plan_shadow_live": False})
            self.assertEqual(on.transport_calls, off.transport_calls, seed)
            self.assertEqual(names_on, names_off, seed)
            self.assertEqual([x for x in lines_on if not x.endswith(PLAN_LINE) and "[START-SHADOW] enabled" not in x], [x for x in lines_off if "[START-SHADOW] enabled" not in x], seed)
            self.assertEqual(self.plan_lines(lines_off), [], seed)

    def test_silent_when_the_setting_is_off_or_missing(self):
        for settings in ({"plan_shadow_live": False}, {}):
            for start_setting in (False, True):   # the older start shadow being on must not switch the plan shadow on
                for seed in range(60):
                    app, lines, _ = self.run_chain(seed, settings, start_shadow_setting=start_setting)
                    self.assertEqual(self.plan_lines(lines), [], (seed, settings, lines))

    def test_silent_when_the_library_is_missing_and_the_start_still_happens(self):
        reference = {seed: self.run_chain(seed, {"plan_shadow_live": False})[0].transport_calls for seed in range(60)}
        with mock.patch.object(start_shadow, "_load_lib", lambda: None):
            for seed in range(60):
                app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True})
                self.assertEqual(self.plan_lines(lines), [], (seed, lines))
                self.assertEqual(app.transport_calls, reference[seed], seed)

    def corrupted(self, change, holder=None):
        def make(log):
            shadow = start_shadow.StartShadow(log)
            if holder is not None:
                holder["shadow"] = shadow
            real = shadow._ask

            def ask(request, cap=1024):
                got = real(request, cap)
                if got is not None and request.get("op") == "plan":
                    got = change(copy.deepcopy(got))
                return got
            shadow._ask = ask
            return shadow
        return make

    def first_started_seed(self, kind=None):
        for seed in range(300):
            app, _, _ = self.run_chain(seed, {"plan_shadow_live": False})
            if app.transport_calls and (kind is None or app.transport_calls[0]["mode"] == kind):
                return seed
        self.fail("no random queue started a song")

    def test_a_deliberately_wrong_plan_logs_a_mismatch_and_changes_nothing(self):
        wrong = {
            "singer": lambda g: dict(g, singer=(g["singer"] or 0) + 1),
            "song": lambda g: dict(g, song=(g["song"] or 0) + 1),
            "queue": lambda g: dict(g, queue=list(reversed(g["queue"])) + [99]),
            "lock_cleared": lambda g: dict(g, lock_cleared=not g["lock_cleared"]),
            "route": lambda g: dict(g, route="reject_unsupported_format"),
            "transport": lambda g: dict(g, transport=dict(g["transport"], semitones=g["transport"]["semitones"] + 1)),
            "display": lambda g: dict(g, display={"artist": "x", "title": "y"}),
        }
        seed = self.first_started_seed()
        reference, _, names = self.run_chain(seed, {"plan_shadow_live": False})
        for name, change in wrong.items():
            holder = {}
            app, lines, names_on = self.run_chain(seed, {"plan_shadow_live": True}, self.corrupted(change, holder))
            self.assertEqual((holder["shadow"].checks, holder["shadow"].mismatches), (1, 1), name)
            mism = [x for x in lines if "MISMATCH plan" in x]
            self.assertEqual(len(mism), 1, (name, lines))
            self.assertEqual([x for x in lines if x.endswith(PLAN_LINE)], [], name)
            self.assertEqual(app.transport_calls, reference.transport_calls, name)
            self.assertEqual(names_on, names, name)

    def routes_by_seed(self):
        found = {}
        for seed in range(300):
            seen = []

            def make(log, seen=seen):
                shadow = start_shadow.StartShadow(log)
                real = shadow.plan_route
                shadow.plan_route = lambda route, *a, **k: (seen.append(route), real(route, *a, **k))[1]
                return shadow
            app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True}, make)
            for route in seen:
                found.setdefault(route, (seed, len([x for x in lines if x.endswith(PLAN_LINE)])))
        return found

    def test_every_route_after_arming_is_judged_once_and_started_ones_are_checked_down_to_the_transport(self):
        found = self.routes_by_seed()
        planned = set(found) - set(start_shadow.UNPLANNED_ROUTES)
        self.assertEqual(planned, {"mp4", "mp3", "cdg_mp3_dual", "mp3g_zip", "reject_unsupported_format", "reject_missing_cdg_audio_pair"} | (planned & {"reject_unsupported_tuple", "reject_invalid_zip"}))
        self.assertIn("mp3g_zip", planned)
        for route in planned:
            seed, plan_lines = found[route]
            self.assertEqual(plan_lines, 1, route)
        for route in sorted(set(found) & {"mp4", "mp3", "cdg_mp3_dual", "mp3g_zip"}):
            seed = found[route][0]
            for change in (lambda g: dict(g, transport=dict(g["transport"], semitones=g["transport"]["semitones"] + 1)), lambda g: dict(g, display={"artist": "x", "title": "y"})):
                _app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True}, self.corrupted(change))
                self.assertEqual(len([x for x in lines if "MISMATCH plan" in x]), 1, (route, lines))

    def test_an_empty_song_path_is_judged_without_a_primary(self):
        lines = []
        shadow = start_shadow.StartShadow(lambda s: lines.append(s))
        singers = [{"name": "A", "songs": [{"song_info": ""}]}]
        shadow.plan_arm(start_shadow.pick_facts(singers), singers, [], singers[0], 0, False, False, False, "", "", 0, 0.0, None, 0, singers[0]["songs"][0], None)
        shadow.plan_route("reject_unsupported_format", "", {"has_primary": False})
        self.assertEqual(lines, ["[START-SHADOW] decision route=plan agree=1"])

    def test_a_wrong_transport_mode_is_a_mismatch_for_every_kind(self):
        for kind in ("mp4", "audio", "cdg"):
            seed = self.first_started_seed(kind)
            _app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True}, self.corrupted(lambda g: dict(g, transport=dict(g["transport"], mode="other"))))
            self.assertEqual(len([x for x in lines if "MISMATCH plan" in x]), 1, (kind, lines))

    def test_a_library_error_or_an_exception_drops_the_call_without_touching_the_start(self):
        seed = self.first_started_seed()
        reference, _, _ = self.run_chain(seed, {"plan_shadow_live": False})
        for behaviour, errors in ((lambda *a, **k: None, 0), (mock.Mock(side_effect=RuntimeError("boom")), 1)):
            def make(log, behaviour=behaviour):
                shadow = start_shadow.StartShadow(log)
                shadow._ask = behaviour
                return shadow
            app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True}, make)
            self.assertEqual(app.transport_calls, reference.transport_calls)
            self.assertEqual([x for x in lines if "MISMATCH" in x or x.endswith(PLAN_LINE)], [])
            self.assertEqual(len([x for x in lines if "plan error" in x]), errors, lines)

    def test_an_error_code_from_the_library_is_dropped(self):
        class Lib:
            def singws_start_json(self, payload, buf, cap):
                return -1
        seed = self.first_started_seed()
        reference, _, _ = self.run_chain(seed, {"plan_shadow_live": False})
        app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True}, lambda log: start_shadow.StartShadow(log, lib=Lib()))
        self.assertEqual(app.transport_calls, reference.transport_calls)
        self.assertEqual([x for x in lines if "MISMATCH" in x or x.endswith(PLAN_LINE) or "plan error" in x], [])
        self.assertEqual(len([x for x in lines if "plan call failed code=-1" in x]), 1, lines)

    def test_a_slow_call_switches_the_plan_shadow_off_for_the_session(self):
        seed = self.first_started_seed()
        ticks = iter(range(0, 10000, 1))   # every perf_counter() reading is a second later than the one before
        with mock.patch.object(start_shadow.time, "perf_counter", lambda: next(ticks)):
            holder = {}

            def make(log):
                holder["shadow"] = start_shadow.StartShadow(log)
                return holder["shadow"]
            _app, lines, _ = self.run_chain(seed, {"plan_shadow_live": True}, make)
            self.assertTrue(holder["shadow"].plan_off)
            self.assertTrue([x for x in lines if "plan_shadow_off" in x])
            shadow = holder["shadow"]
        lines2 = []
        shadow._log = lambda text, *a, **k: lines2.append(str(text))
        self.singws._START_SHADOW = shadow
        app2, _, _ = self.run_chain(seed, {"plan_shadow_live": True}, lambda log: shadow)
        self.assertEqual([x for x in lines2 if "plan" in x and "START-SHADOW" in x], [])

    def arm(self, shadow, count=3, facts="default", after="default", singer="default"):
        singers = [{"name": f"s{i}", "songs": [{"song_info": "/lib/a.mp4", "artist": " A ", "title": " T "}]} for i in range(count)]
        shadow.plan_arm(start_shadow.pick_facts(singers) if facts == "default" else facts, singers, singers[1:] if after == "default" else after,
                        singers[0] if singer == "default" else singer, 0, False, False, False, "/lib/a.mp4", "/lib/a.mp4", 0, 0.0, None, 180, singers[0]["songs"][0], None)
        return singers

    def test_an_overlong_queue_is_not_sent_but_the_longest_allowed_one_is(self):
        shadow = start_shadow.StartShadow(lambda s: None)
        self.arm(shadow, start_shadow.PLAN_MAX_SINGERS + 1)
        self.assertIsNone(shadow._plan)
        self.arm(shadow, start_shadow.PLAN_MAX_SINGERS)
        self.assertIsNotNone(shadow._plan)

    def test_arming_refuses_facts_that_do_not_match_the_queue(self):
        lines = []
        shadow = start_shadow.StartShadow(lambda s: lines.append(s))
        for kwargs in ({"facts": None}, {"facts": [{"skipped": False, "songs": "d"}]}, {"singer": {"name": "stranger", "songs": []}}, {"after": [{"name": "stranger", "songs": []}]}):
            self.arm(shadow, **kwargs)
            self.assertIsNone(shadow._plan, kwargs)
        self.assertEqual(lines, [])   # refused quietly, not by an exception
        self.arm(shadow)
        self.assertEqual((shadow._plan["artist"], shadow._plan["title"], shadow._plan["singer"], shadow._plan["queue"]), ("A", "T", 0, [1, 2]))
        shadow.plan_off = True
        self.arm(shadow)
        self.assertIsNone(shadow._plan)
        shadow.plan_off = False
        shadow.available = False
        self.arm(shadow)
        self.assertIsNone(shadow._plan)

    def test_the_rotation_lock_clearing_is_part_of_the_plan(self):
        for seed in range(40):
            rng = random.Random(seed)
            singers = self.random_queue(rng)
            if not singers:
                continue
            singers[0].update({"skipped": False, "rotation_lock_new": True})
            singers[0]["songs"] = [{"request_uid": "r", "song_info": "/lib/a.mp4", "artist": "A", "title": "T", "skipped": False, "key": 0, "duration": 95}]
            app = self.make_app(singers, 0.0, (None, None))
            state = {"locked": True}
            app.settings = {"start_shadow": False, "plan_shadow_live": True, "rotation_locked": True}
            app._is_rotation_mode = lambda: True
            app._is_rotation_locked = lambda: state["locked"]
            app._rotation_marker_index = lambda: 0
            app._rotation_reweave_locked_tail = lambda: None
            app._rotation_clear_lock = lambda save=True: (state.update(locked=False), app.settings.update(rotation_locked=False))
            lines = []
            log = lambda text, *a, **k: lines.append(str(text))   # noqa: E731
            shadow = start_shadow.StartShadow(log)
            sent = []
            real_finish = shadow.plan_finish
            shadow.plan_finish = lambda plan, *a: (sent.append((plan["lock_cleared"], plan["rotation_mode"], plan["setting_locked"])), real_finish(plan, *a))[1]
            self.singws._START_SHADOW = shadow
            with mock.patch.object(self.singws, "_diag", log), mock.patch.object(self.singws.os.path, "exists", lambda p: True), \
                    mock.patch.object(self.singws.QTimer, "singleShot", lambda *a, **k: None):
                app.play_next_file()
            self.assertEqual([x for x in lines if "MISMATCH" in x], [], (seed, lines))
            self.assertEqual(len([x for x in lines if x.endswith(PLAN_LINE)]), 1, (seed, lines))
            self.assertFalse(state["locked"])
            self.assertEqual(sent, [(True, True, True)], seed)

    def test_every_hundred_percent_agreeing_launches_report_a_summary(self):
        lines = []
        shadow = start_shadow.StartShadow(lambda s: lines.append(s))
        for _ in range(start_shadow.SUMMARY_EVERY):
            self.arm(shadow)
            shadow.plan_route("mp4", "/lib/a.mp4", {"has_primary": True, "primary_exists": True})
            shadow.plan_transport({"audio_path": "/lib/a.mp4", "video_path": "/lib/a.mp4", "mode": "mp4", "semitones": 0, "start_seconds": 0.0, "loop_seconds": None, "duration_seconds": None})
            shadow.plan_started("A", "T", None)
        self.assertEqual([x for x in lines if "MISMATCH" in x], [], lines)
        self.assertEqual(len([x for x in lines if x.endswith(PLAN_LINE)]), start_shadow.SUMMARY_EVERY)
        self.assertEqual(len([x for x in lines if "summary checks=25 mismatches=0" in x]), 1, lines)

    def test_a_stale_plan_from_a_refused_start_is_not_judged_against_the_next_one(self):
        lines = []
        shadow = start_shadow.StartShadow(lambda s: lines.append(s))
        for route in start_shadow.UNPLANNED_ROUTES:
            self.arm(shadow)
            self.assertIsNotNone(shadow._plan)
            shadow.plan_route(route, "/lib/a.mp4", {})
            self.assertIsNone(shadow._plan, route)
        self.assertEqual(sorted(start_shadow.UNPLANNED_ROUTES), ["external_karafun", "reject_missing_file", "zip_prepare"])
        self.assertEqual(lines, [])

    def test_the_call_costs_the_gui_thread_almost_nothing(self):
        singers = [{"name": f"s{i}", "singer_id": f"i{i}", "songs": [{"song_info": f"/lib/{i}.mp4", "artist": "A", "title": "T", "key": 0}] * 3} for i in range(150)]
        shadow = start_shadow.StartShadow(lambda s: None)
        if not shadow.available:
            self.skipTest("library missing")
        timings = []
        for _ in range(30):
            started = time.perf_counter()
            shadow.plan_arm(start_shadow.pick_facts(singers), singers, singers[1:], singers[0], 0, False, False, False, "/lib/0.mp4", "/lib/0.mp4", 0, 0.0, None, 180, singers[0]["songs"][0], None)
            shadow.plan_route("mp4", "/lib/0.mp4", {"has_primary": True, "primary_exists": True})
            shadow.plan_transport({"audio_path": "/lib/0.mp4", "video_path": None, "mode": "mp4", "semitones": 0, "start_seconds": 0.0, "loop_seconds": None, "duration_seconds": None})
            shadow.plan_started("A", "T", None)
            timings.append(time.perf_counter() - started)
        self.assertLess(sorted(timings)[len(timings) // 2], 0.01, timings)
        self.assertFalse(shadow.plan_off)

    def test_the_setting_is_listed(self):
        self.assertIs(self.singws.DEFAULTS["plan_shadow_live"], True)
        self.assertIn("plan_shadow_live", self.singws._RUST_EVIDENCE_FLAGS)


if __name__ == "__main__":
    unittest.main()
