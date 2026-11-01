"""net_slice_shadow: the log-only comparison of the app's network decisions (slice 1: the sync probe) with the Rust singws-netslice helper.
The value the app computed is always the one used. Real code under test: the app's `probe_network_sync_status` / `_probe_network_sync_status_impl`
against a scripted fake `requests` (no socket, no real server), the production recorder, batch builder and comparator in net_slice_shadow.py."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import net_slice_shadow as ns

ROOT = Path(__file__).resolve().parent
CLI = os.environ.get("SINGWS_NETSLICE_BIN") or os.path.join(
    os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-netslice")), "release", "singws-netslice")
SECRET = "SUPER-SECRET-KEY-123"
_APP = []


def app():
    if not _APP:
        spec = importlib.util.spec_from_file_location("singws_main_netslice_test", str(ROOT / "0.2.18.1.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _APP.append(mod)
    return _APP[0]


def reset():
    ns._seen.clear()
    ns._helper_cache[:] = []
    ns._clean_noted = False


def fake_helper(tmp, reply, exit_code=0):
    path = Path(tmp) / "singws-netslice"
    path.write_text("#!/bin/sh\ncat >/dev/null\ncat <<'EOF'\n" + json.dumps(reply) + "\nEOF\nexit " + str(exit_code) + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Resp:
    def __init__(self, status=200, text="{}", json_ok=True):
        self.status_code, self.text, self._ok = status, text, json_ok
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        if not self._ok:
            raise ValueError("not json")
        return {}


class Fake:
    """Scripted `requests`: answers by URL, records nothing it should not."""
    def __init__(self, script):
        self.exceptions = app().requests.exceptions
        self.script, self.seen = script, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.seen.append((url, params, headers, timeout))
        for k, v in self.script.items():
            if k in url:
                if isinstance(v, BaseException):
                    raise v
                return v
        raise AssertionError(url)


def healthy():
    return {"get_requests.php": Resp(), "accepting.txt": Resp(200, "1", False), "host_commands.php": Resp(200, "", False),
            "singer_history_sync.php": Resp(405, "", False), "post_rotation.php": Resp(200, "", False)}


class Recorder(unittest.TestCase):
    def test_forwards_the_response_object_and_the_call_unchanged(self):
        r = Resp(200, "abc")
        fake = Fake({"x": r})
        rec = ns.RecordingRequests(fake)
        got = rec.get("http://x/", params={"a": 1}, headers={"h": "v"}, timeout=3.0)
        self.assertIs(got, r)
        self.assertEqual(fake.seen, [("http://x/", {"a": 1}, {"h": "v"}, 3.0)])
        self.assertEqual(rec.calls[0]["outcome"], {"kind": "response", "status": 200, "body": "abc", "json_ok": True, "json_error": ""})

    def test_exceptions_propagate_and_are_classified_timeout_first(self):
        ex = app().requests.exceptions
        for exc, kind in ((ex.ConnectTimeout("t"), "timeout"), (ex.ConnectionError("c"), "connection"), (ValueError("v"), "other"), (ex.SSLError("s"), "connection")):
            rec = ns.RecordingRequests(Fake({"x": exc}))
            with self.assertRaises(type(exc)):
                rec.get("http://x/")
            self.assertEqual(rec.calls[0]["outcome"]["kind"], kind)
            self.assertEqual(rec.calls[0]["outcome"]["type"], type(exc).__name__)

    def test_json_is_only_asked_for_on_a_200_and_a_failure_is_remembered(self):
        bad, ok404 = Resp(200, "<", False), Resp(404, "x", False)
        rec = ns.RecordingRequests(Fake({"a": bad, "b": ok404}))
        rec.get("http://a/")
        rec.get("http://b/")
        self.assertEqual(rec.calls[0]["outcome"]["json_error"], "not json")
        self.assertEqual(ok404.json_calls, 0)

    def test_the_body_is_cut_to_300_characters(self):
        rec = ns.RecordingRequests(Fake({"a": Resp(500, "y" * 900, False)}))
        rec.get("http://a/")
        self.assertEqual(len(rec.calls[0]["outcome"]["body"]), 300)

    def test_other_attributes_are_the_real_modules(self):
        fake = Fake({})
        self.assertIs(ns.RecordingRequests(fake).exceptions, fake.exceptions)


class Batch(unittest.TestCase):
    def test_declines_odd_arguments_and_unrecorded_calls(self):
        self.assertIsNone(ns.probe_batch(None, "t", "k", 6.0, [], {}))
        self.assertIsNone(ns.probe_batch("b", "t", "k", True, [], {}))
        self.assertIsNone(ns.probe_batch("b", "t", "k", 6.0, [{"url": "u", "params": None, "headers": None, "timeout": 1}], {}))   # no outcome
        self.assertIsNone(ns.probe_batch("b", "t", "k", 6.0, [{"url": "u", "params": None, "headers": None, "timeout": 1, "positional": True, "outcome": {}}], {}))


@unittest.skipUnless(os.path.exists(CLI), "singws-netslice not built (cd rust && cargo build --release -p singws-netslice)")
class RealHelper(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["SINGWS_NETSLICE_BIN"] = CLI
        self.lines = []

    def run_probe(self, script, base="https://example.test", tenant="wsk", key="k", timeout=6.0):
        rec = ns.RecordingRequests(Fake(script))
        result = app()._probe_network_sync_status_impl(base, tenant, key, timeout_sec=timeout, requests_module=rec)
        return ns.probe_batch(base, tenant, key, timeout, rec.calls, result), result

    def test_a_healthy_probe_agrees(self):
        batch, result = self.run_probe(healthy())
        self.assertEqual(result["message"], "Connected / Synced")
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")
        self.assertEqual(len(self.lines), 1)
        self.assertIn("first check clean", self.lines[0])
        # the second clean check says nothing
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")
        self.assertEqual(len(self.lines), 1)

    def test_failures_and_exceptions_agree(self):
        ex = app().requests.exceptions
        script = healthy()
        script["get_requests.php"] = ex.ConnectionError("refused")
        script["accepting.txt"] = Resp(200, "maybe", False)
        script["post_rotation.php"] = ex.ReadTimeout("slow")
        batch, result = self.run_probe(script)
        self.assertFalse(result["ok"])
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")

    def test_a_wrong_rust_answer_is_a_mismatch_without_secrets(self):
        batch, _ = self.run_probe(healthy(), key=SECRET)
        name, req, expected = batch
        wrong = json.loads(json.dumps(expected))
        wrong["result"]["message"] = "Connected / Synced!"
        with tempfile.TemporaryDirectory() as tmp:
            helper = fake_helper(tmp, [wrong])
            self.assertEqual(ns.run_shadow([batch], self.lines.append, helper), "mismatch")
            self.assertEqual(ns.run_shadow([batch], self.lines.append, helper), "mismatch")   # the same difference is logged once
        self.assertEqual(len(self.lines), 1)
        self.assertIn("MISMATCH probe at $.result.message", self.lines[0])
        for secret in (SECRET, "example.test", "wsk"):
            self.assertNotIn(secret, self.lines[0])

    def test_type_and_key_order_differences_are_caught(self):
        batch, _ = self.run_probe(healthy())
        expected = batch[2]
        swapped = json.loads(json.dumps(expected))
        swapped["calls"][1]["timeout"] = 5          # int instead of the float the app sent
        self.assertIn("calls[1].timeout", ns.compare("probe", expected, swapped))
        reordered = json.loads(json.dumps(expected))
        reordered["result"] = dict(reversed(list(reordered["result"].items())))
        self.assertIn("<keys>", ns.compare("probe", expected, reordered))

    def test_a_length_difference_and_a_non_object_answer_are_reported(self):
        batch, _ = self.run_probe(healthy())
        expected = batch[2]
        shorter = json.loads(json.dumps(expected))
        shorter["calls"].pop()
        self.assertIn("calls.<len>", ns.compare("probe", expected, shorter))
        self.assertIn("not an object", ns.compare("probe", expected, [1]))

    def test_the_helper_runs_at_low_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nice.txt"
            helper = Path(tmp) / "singws-netslice"
            helper.write_text(f"#!/bin/sh\ncat >/dev/null\nps -o ni= -p $$ > {out}\necho '[{{\"unsupported\": \"x\"}}]'\n")
            helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
            batch, _ = self.run_probe(healthy())
            ns.run_shadow([batch], self.lines.append, helper)
            self.assertGreaterEqual(int(out.read_text().strip()), 10)

    def test_logging_stops_after_max_logged_distinct_differences(self):
        batch, _ = self.run_probe(healthy())
        name, req, expected = batch
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(ns.MAX_LOGGED + 5):
                wrong = json.loads(json.dumps(expected))
                wrong["result"]["message"] = "x" * (i + 1)
                ns.run_shadow([batch], self.lines.append, fake_helper(tmp, [wrong]))
        self.assertEqual(len(self.lines), ns.MAX_LOGGED + 1)
        self.assertIn("suppressed", self.lines[-1])

    def test_unsupported_and_bad_helpers_are_skipped(self):
        batch, _ = self.run_probe(healthy())
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ns.run_shadow([batch], self.lines.append, fake_helper(tmp, [{"unsupported": "x"}])), "clean")
            self.assertEqual(ns.run_shadow([batch], self.lines.append, fake_helper(tmp, [{"unsupported": "x"}], 3)), "skipped")
            self.assertEqual(ns.run_shadow([batch], self.lines.append, fake_helper(tmp, [1, 2])), "skipped")
        self.assertTrue(any(l.startswith("[NET-SLICE-SHADOW] skipped") for l in self.lines))
        self.assertEqual(ns.run_shadow([], self.lines.append), "skipped")

    def test_the_app_wrapper_returns_what_the_probe_computes_with_the_shadow_on_or_off(self):
        a = app()
        script = healthy()
        script["host_commands.php"] = Resp(500, "oops", False)
        results = []
        submitted = []
        real_submit = ns.submit
        ns.submit = lambda batch, log: submitted.append(batch)
        real_requests = a.requests
        try:
            for on in (False, True):
                a._NET_SLICE_SHADOW_ON = on
                a.requests = Fake(script)
                results.append(a.probe_network_sync_status("https://example.test", "wsk", "k", timeout_sec=6.0))
        finally:
            ns.submit = real_submit
            a.requests = real_requests
            a._NET_SLICE_SHADOW_ON = False
        self.assertEqual(results[0], results[1])
        self.assertIsInstance(results[1], dict)
        self.assertEqual(len(submitted), 1)
        self.assertEqual(len(submitted[0][0][1]["calls"]), 5)           # the recorder really was the requests module of the probe
        self.assertEqual(submitted[0][0][2]["result"], results[1])      # and the app's own result is what the shadow compares           # only the shadow-on call was handed over
        self.assertEqual(submitted[0][0][0], "probe")

    def test_an_exception_in_the_probe_still_propagates_with_the_shadow_on(self):
        a = app()
        a._NET_SLICE_SHADOW_ON = True
        try:
            with self.assertRaises(ValueError):
                a.probe_network_sync_status("https://example.test", "wsk", "k", timeout_sec="not a number")
        finally:
            a._NET_SLICE_SHADOW_ON = False


class Launch(unittest.TestCase):
    def setUp(self):
        reset()

    def test_announce_says_enabled_off_and_available(self):
        lines = []
        os.environ["SINGWS_NETSLICE_BIN"] = "/nonexistent"
        self.assertEqual(ns.announce({}, lines.append), "enabled available=0")
        reset()
        self.assertEqual(ns.announce({"net_slice_shadow": False}, lines.append), "off (net_slice_shadow=false)")
        self.assertEqual(lines[0], "[NET-SLICE-SHADOW] enabled available=0")
        if os.path.exists(CLI):
            os.environ["SINGWS_NETSLICE_BIN"] = CLI
            reset()
            self.assertEqual(ns.announce({}, lines.append), "enabled available=1")

    def test_default_is_on_and_the_app_wires_it(self):
        self.assertTrue(ns.enabled({}))
        self.assertFalse(ns.enabled({"net_slice_shadow": False}))
        src = (ROOT / "0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn('"net_slice_shadow": True,', src)
        self.assertIn('for _mod_name in ("logpack_shadow", "relay_msg_shadow", "scan_shadow", "net_slice_shadow"):', src)
        self.assertIn("_NET_SLICE_SHADOW_ON = net_slice_shadow.enabled(self.settings)", src)
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = (ROOT / spec).read_text(encoding="utf-8")
            self.assertIn('"net_slice_shadow.py"', text)
            self.assertIn("'net_slice_shadow'", text)
            self.assertIn("singws-netslice", text)
        for script in ("build_singws_mac_arm64.sh", "build_singws_mac_intel.sh"):
            self.assertIn("build_netslice.sh", (ROOT / script).read_text(encoding="utf-8"))
        self.assertIn("native/singws_netslice/*/singws-netslice", (ROOT / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
