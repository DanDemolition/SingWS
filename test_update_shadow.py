"""Stage E update rules: the Rust helper (rust/crates/singws-update) must give IDENTICAL answers to the app's real Python (tools/update_parity.py is the full
corpus), and update_shadow (log-only) must never change the info the update worker emits. No network is ever contacted: nothing here fetches."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import update_parity as up  # noqa: E402
import update_shadow as us  # noqa: E402

try:
    CLI = up.find_cli()
except FileNotFoundError:
    CLI = None


def reset():
    us._seen.clear()
    us._helper_cache.clear()
    us._clean_noted = False


@unittest.skipUnless(CLI, "singws-update is not built")
class ParityTests(unittest.TestCase):
    def test_corpus_sample_is_identical(self):
        bad = []
        for i, req in enumerate(up.cases(150, 11)):
            if i % 7:
                continue
            want, got = up.norm(req["op"], up.python_answer(req)), up.norm(req["op"], up.rust(req, CLI))
            if want != got:
                bad.append((req, want, got))
        self.assertEqual(bad[:2], [])

    def test_real_release_manifest_is_identical_for_every_machine(self):
        real = json.loads((Path(__file__).parent / "docs" / "release.json").read_text())
        for machine in up.MACHINES:
            req = {"op": "manifest_info", "manifest": real, "current": "1.0.0.1", "platform": "darwin", "machine": machine}
            self.assertEqual(up.norm("manifest_info", up.python_answer(req)), up.norm("manifest_info", up.rust(req, CLI)), machine)

    def test_python_only_quirks_are_reproduced(self):
        for v in ("1." + "9" * 4300, "1." + "9" * 4301, "١٢٣.٤", "vV1.2", "1.0.0.0.0.0.9"):
            req = {"op": "version_key", "value": v}
            self.assertEqual(up.python_answer(req), up.rust(req, CLI), v[:12])


@unittest.skipUnless(CLI, "singws-update is not built")
class ShadowTests(unittest.TestCase):
    MANIFEST = {"version": "1.0.2.8", "downloads": {"mac_arm64": up.dl("a", "1.0.2.8"), "mac_x86_64": up.dl("x", "1.0.2.8")}}

    def info(self, manifest=None):
        m = up.app()
        with up.patched("darwin", "arm64"):
            return m.GitHubUpdateWorker._manifest_info(types.SimpleNamespace(current_version="1.0.2.7"), manifest or self.MANIFEST)

    def check(self, info, log):
        return us.run_check("manifest_info", self.MANIFEST, info, "1.0.2.7", "darwin", "arm64", log.append, helper=Path(CLI))

    def test_agreement_is_noted_once(self):
        reset()
        log = []
        self.assertEqual(self.check(self.info(), log), "clean")
        self.assertEqual(self.check(self.info(), log), "clean")
        self.assertEqual(len([l for l in log if "first check clean" in l]), 1)

    def test_a_different_decision_is_reported_once(self):
        reset()
        log = []
        wrong = dict(self.info(), available=False)
        self.assertEqual(self.check(wrong, log), "mismatch")
        self.assertEqual(self.check(wrong, log), "mismatch")
        self.assertEqual(len([l for l in log if "MISMATCH manifest_info at $.available" in l]), 1)

    def test_observe_does_not_touch_the_info(self):
        reset()
        info = self.info()
        before = json.dumps(info, sort_keys=True)
        with mock.patch.dict(os.environ, {"SINGWS_UPDATE_BIN": CLI}):
            reset()
            t = us.observe("manifest_info", self.MANIFEST, info, "1.0.2.7", lambda s: None, wait=True)
        self.assertIsNotNone(t)
        self.assertEqual(json.dumps(info, sort_keys=True), before)

    def test_missing_helper_is_silent(self):
        reset()
        with mock.patch.object(us, "find_helper", return_value=None):
            self.assertIsNone(us.observe("manifest_info", self.MANIFEST, {}, "1", lambda s: self.fail("logged")))
        reset()


class GateTests(unittest.TestCase):
    def test_announce_lines(self):
        out = []
        with mock.patch.object(us, "find_helper", return_value=None):
            us.announce({}, out.append)
        with mock.patch.object(us, "find_helper", return_value=Path("/x")):
            us.announce({"update_shadow": True}, out.append)
        us.announce({"update_shadow": False}, out.append)
        self.assertEqual(out, ["[UPDATE-SHADOW] enabled available=0", "[UPDATE-SHADOW] enabled available=1", "[UPDATE-SHADOW] off (update_shadow=false)"])

    def test_app_wiring_is_log_only_and_off_the_network_code(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text()
        self.assertIn('"update_shadow": True', src)
        self.assertIn('self._update_shadow("manifest_info", manifest, info)', src)
        self.assertIn('self._update_shadow("release_info", release, info)', src)
        body = src[src.index("    def _update_shadow(self"):src.index("    def _download_asset(self")]
        self.assertNotIn("requests", body)
        self.assertNotIn("info[", body)

    def test_worker_run_still_emits_python_info_when_the_hook_raises(self):
        m = up.app()
        worker = m.GitHubUpdateWorker.__new__(m.GitHubUpdateWorker)
        worker.current_version = "1.0.2.7"
        with mock.patch.dict(sys.modules, {"update_shadow": types.SimpleNamespace(observe=mock.Mock(side_effect=RuntimeError("boom")))}):
            worker._update_shadow("manifest_info", {}, {"available": True})  # must not raise


if __name__ == "__main__":
    unittest.main()
