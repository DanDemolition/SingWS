import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import json
import os
import random
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from tools import history_parity as hp
from tools import waitlist_parity as wp


def _cli():
    return Path(wp.CLI)


class LiveWaitlistTests(unittest.TestCase):
    """`KaraokeApp._publish_live_waitlist`: waitlist_state.json for the read-only Rust shell (setting publish_live_waitlist, default off)."""

    def setUp(self):
        self.mod = hp.app()
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "waitlist_state.json"
        self._old = self.mod.LIVE_WAITLIST_PATH
        self.mod.LIVE_WAITLIST_PATH = self.path

    def tearDown(self):
        self.mod.LIVE_WAITLIST_PATH = self._old
        self.dir.cleanup()

    def host(self, waiting=None, deferred=None, enabled=True, first=(), accepted=(), local=()):
        f = wp.make_fake(set(first), set(accepted), list(local), waiting or {}, False, {}, deferred or [], bool(deferred))
        f.settings["publish_live_waitlist"] = enabled
        f._publish_live_waitlist = types.MethodType(self.mod.KaraokeApp._publish_live_waitlist, f)
        return f

    def test_the_setting_defaults_off_and_a_profile_without_it_writes_nothing(self):
        self.assertFalse(self.mod.DEFAULTS["publish_live_waitlist"])
        h = self.host({"1": {"request_id": 1, "singer": "Ann"}}, enabled=False)
        h._publish_live_waitlist()
        self.assertFalse(self.path.exists())
        h.settings.pop("publish_live_waitlist")
        h._publish_live_waitlist()
        self.assertFalse(self.path.exists())

    def test_what_the_page_lists_is_what_is_written(self):
        h = self.host({"7": {"request_id": 7, "singer": "Dee", "artist": "Q", "title": "R"}, "8": {"request_id": 8, "singer": "Gone"}, "9": {"request_id": 9, "singer": "Eve"}},
                      deferred=[{"request_id": 21, "singer": "Hal", "artist": "Queen", "title": "Bike"}], first=["Dee", "Hal"], accepted=[8], local=[9])
        h._publish_live_waitlist()
        d = json.loads(self.path.read_text())
        self.assertEqual((d["version"], d["pid"]), (1, os.getpid()))
        self.assertEqual(sorted(d["waiting"]), ["7"], "accepted (8) and already-queued (9) requests are filtered out by the app's own rules")
        self.assertEqual([r["request_id"] for r in d["pending_acceptance_requests"]], [21])
        self.assertEqual(d["pending_acceptance_requests"][0]["queue_position"], 1)
        self.assertEqual(d["first_time_names"], ["Dee", "Hal"])
        self.assertGreater(d["updated_at"], 0)

    def test_unchanged_state_is_not_rewritten_and_a_change_is(self):
        h = self.host({"7": {"request_id": 7, "singer": "Dee"}})
        h._publish_live_waitlist()
        self.path.write_text("sentinel")
        h._publish_live_waitlist()
        self.assertEqual(self.path.read_text(), "sentinel")
        h._waiting_for_add_requests["8"] = {"request_id": 8, "singer": "Eve"}
        h._publish_live_waitlist()
        self.assertEqual(sorted(json.loads(self.path.read_text())["waiting"]), ["7", "8"])

    def test_a_failure_never_escapes_and_leaves_no_temp_file(self):
        self.mod.LIVE_WAITLIST_PATH = Path(self.dir.name) / "no" / "such" / "dir" / "waitlist_state.json"
        self.host({"7": {"request_id": 7}})._publish_live_waitlist()  # must not raise
        self.assertEqual([p.name for p in Path(self.dir.name).iterdir()], [])
        self.mod.KaraokeApp._publish_live_waitlist(types.SimpleNamespace())  # not even a settings attribute

    def test_a_request_without_an_id_is_still_published_under_a_private_key(self):
        h = self.host({"4": {"singer": "NoId"}})
        h._publish_live_waitlist()
        d = json.loads(self.path.read_text())
        self.assertEqual(len(d["waiting"]), 1)
        self.assertTrue(int(next(iter(d["waiting"]))) > 0)

    def test_it_is_called_when_the_waitlist_view_refreshes_and_from_the_host_state_sync(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index("    def _schedule_waiting_for_add_view_refresh(")
        self.assertIn("self._publish_live_waitlist()", src[i:i + 1400])
        j = src.index("    def _sync_host_control_state_now(self):")
        self.assertIn("self._publish_live_waitlist()", src[j:j + 400])

    @unittest.skipUnless(_cli().exists(), "needs singws-waitlist-cli")
    def test_the_rust_page_built_from_the_file_equals_the_apps_own_sections(self):
        rng = random.Random(11)
        compared = 0
        for _ in range(25):
            waiting = {str(rng.randrange(1, 40)): wp.rand_req(rng) for _ in range(rng.randrange(0, 6))}
            first = set(rng.sample(wp.NAMES_STR, rng.randrange(0, 3)))
            accepted = {rng.randrange(1, 40) for _ in range(2)}
            local = [rng.randrange(1, 40) for _ in range(2)]
            deferred = [{"request_id": rng.randrange(1, 40), "singer": rng.choice(wp.NAMES_STR), "artist": "A", "title": "T", "deferred_at": 1792000000 + i} for i in range(rng.randrange(0, 3))]
            h = self.host(waiting, deferred, first=first, accepted=accepted, local=local)
            try:
                expected = [(s["key"], [(r["request_id"], r["singer"], r["artist"], r["title"], r["status_label"], r["first_time_singer"], r["queue_position"]) for r in s["rows"]])
                            for s in h._waiting_for_add_sections()]
            except Exception:
                continue
            h._publish_live_waitlist()
            feed = json.loads(self.path.read_text()) if self.path.exists() else None
            if feed is None:
                self.assertEqual(expected, [])
                continue
            got = wp.rust({"op": "sections", "waiting": feed["waiting"], "pending_acceptance_requests": feed["pending_acceptance_requests"], "first_time_names": feed["first_time_names"],
                           "show_removed": False, "removed": {}, "local_ids": [], "accepted_ids": []})
            if isinstance(got, dict) and "error" in got:
                continue  # a declined input (non-ASCII numerals ...): the page would say so
            actual = [(s["key"], [(r["request_id"], r["singer"], r["artist"], r["title"], r["status_label"], r["first_time_singer"], r["queue_position"]) for r in s["rows"]]) for s in got]
            self.assertEqual(actual, expected)
            compared += 1
            self.path.unlink()
        self.assertGreater(compared, 5)


if __name__ == "__main__":
    unittest.main()
