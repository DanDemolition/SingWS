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
from unittest import mock
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


class LocStub:
    def __init__(self, settings, detect=(None, "denied")):
        self.settings, self._detect = settings, detect
        self.saved = 0

    def _detect_current_device_location(self, timeout_sec=15.0):
        return self._detect

    def save_settings(self):
        self.saved += 1


def loc_settings(**extra):
    st = {"base_url": "example.test/", "user": "wsk", "api_key": SECRET, "session_location_latitude": "40.5", "session_location_longitude": "-73.25",
          "session_location_source": "manual", "session_location_accuracy_meters": "9.5", "session_location_detected_at": 1700000000,
          "session_location_session_id": "sess1", "session_location_ttl_minutes": 60, "session_location_auto_detect": False}
    st.update(extra)
    return st


class LocationBatch(unittest.TestCase):
    def test_declines_values_the_helper_does_not_model(self):
        for bad in ({"session_location_latitude": 40.5}, {"api_key": 5}, {"session_location_ttl_minutes": "60"}, {"session_location_detected_at": True}):
            self.assertIsNone(ns.location_batch(loc_settings(**bad), {}, 1, 2), bad)

    def test_a_failed_detection_label_is_passed_as_the_only_override(self):
        payload = {"location_source": "manual_fallback", "detected_at": 1700000000}
        name, req, expected = ns.location_batch(loc_settings(), payload, 10, 20)
        self.assertEqual(req["source_override"], "manual_fallback")
        self.assertEqual(expected, {"payload": payload})
        self.assertNotIn("source_override", ns.location_batch(loc_settings(), {"location_source": "manual", "detected_at": 1700000000}, 10, 20)[1])

    def test_a_clock_stamp_inside_the_call_window_is_the_apps_own_now(self):
        payload = {"location_source": "manual", "detected_at": 15}
        req = ns.location_batch(loc_settings(session_location_detected_at=0), payload, 10, 20)[1]
        self.assertEqual(req["now"], 15)
        req = ns.location_batch(loc_settings(session_location_detected_at=0), {"location_source": "manual", "detected_at": 99}, 10, 20)[1]
        self.assertEqual(req["now"], 20)          # outside the window: the helper's own answer will differ and be reported
        req = ns.location_batch(loc_settings(), {"location_source": "manual", "detected_at": 15}, 10, 20)[1]
        self.assertEqual(req["now"], 20)          # a saved detected_at is used as is
        self.assertEqual(ns.location_batch(loc_settings(), None, 10, 20)[2], {"payload": None})

    def test_only_the_keys_the_app_reads_are_sent_and_only_those_present(self):
        st = loc_settings(unrelated="x")
        del st["user"]
        req = ns.location_batch(st, None, 1, 2)[1]
        self.assertNotIn("unrelated", req["settings"])
        self.assertNotIn("user", req["settings"])
        self.assertNotIn("session_location_auto_detect", req["settings"])


@unittest.skipUnless(os.path.exists(CLI), "singws-netslice not built (cd rust && cargo build --release -p singws-netslice)")
class LocationReal(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["SINGWS_NETSLICE_BIN"] = CLI
        self.lines = []

    def payload(self, settings, detect=(None, "denied"), allow=True):
        import time
        stub = LocStub(settings, detect)
        t0 = int(time.time())
        payload = app().KaraokeApp._session_location_payload_impl(stub, allow)
        return ns.location_batch(dict(stub.settings), payload, t0, int(time.time())), payload

    def test_saved_coordinates_agree(self):
        batch, payload = self.payload(loc_settings())
        self.assertEqual(payload["tenant"], "wsk")
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")

    def test_a_detection_that_ran_agrees(self):
        det = ({"latitude": 12.3456789, "longitude": -45.6789012, "source": "auto_detected", "accuracy_meters": 8.76, "detected_at": 1700000001}, "")
        batch, payload = self.payload(loc_settings(session_location_auto_detect=True, session_location_detected_at=0), det)
        self.assertEqual(payload["location_source"], "auto_detected")
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")

    def test_a_failed_detection_with_saved_coordinates_agrees(self):
        batch, payload = self.payload(loc_settings(session_location_auto_detect=True, session_location_detected_at=0))
        self.assertEqual(payload["location_source"], "manual_fallback")
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")

    def test_no_session_id_yet_and_out_of_range_coordinates_agree(self):
        batch, payload = self.payload(loc_settings(session_location_session_id=""))
        self.assertEqual(len(payload["session_id"]), 32)
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")
        batch, payload = self.payload(loc_settings(session_location_latitude="95"))
        self.assertIsNone(payload)
        self.assertEqual(ns.run_shadow([batch], self.lines.append), "clean")

    def test_a_different_payload_is_a_mismatch_that_never_prints_the_key_or_coordinates(self):
        batch, payload = self.payload(loc_settings())
        wrong = dict(payload, latitude=41.5)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ns.run_shadow([(batch[0], batch[1], {"payload": wrong})], self.lines.append), "mismatch")
        self.assertIn("MISMATCH location at $.payload.latitude", self.lines[-1])
        for secret in (SECRET, "40.5", "41.5", "example.test", "sess1"):
            self.assertNotIn(secret, self.lines[-1])

    def test_the_app_wrapper_returns_the_impl_value_and_hands_the_after_settings_to_the_shadow(self):
        a = app()
        submitted = []
        real = ns.submit
        ns.submit = lambda batch, log: submitted.append(batch)
        stubs = []
        try:
            for on in (False, True):
                a._NET_SLICE_SHADOW_ON = on
                stub = LocStub(loc_settings(session_location_session_id=""))
                stub._session_location_payload_impl = lambda allow, stub=stub: a.KaraokeApp._session_location_payload_impl(stub, allow)
                stubs.append((stub, a.KaraokeApp._session_location_payload(stub)))
        finally:
            ns.submit = real
            a._NET_SLICE_SHADOW_ON = False
        self.assertIsInstance(stubs[1][1], dict)
        self.assertEqual({k: v for k, v in stubs[0][1].items() if k != "session_id"}, {k: v for k, v in stubs[1][1].items() if k != "session_id"})
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0][0][0], "location")
        self.assertEqual(submitted[0][0][2]["payload"], stubs[1][1])
        self.assertEqual(submitted[0][0][1]["settings"]["session_location_session_id"], stubs[1][1]["session_id"])   # the id the call saved, not the blank one


class PostFake:
    """`requests.post` stand-in: remembers what it was given, answers with a canned JSON object."""
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.seen = answer or {"success": True, "count": 1, "ok": True, "stored": True, "emailed": True}, exc, []

    def post(self, url, data=None, files=None, headers=None, timeout=None):
        self.seen.append((url, data, files, headers, timeout))
        if self.exc:
            raise self.exc
        ans = self.answer
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: ans})()


class RecordingPostTests(unittest.TestCase):
    def test_forwards_the_call_and_the_answer_and_describes_files(self):
        fake = PostFake()
        rec = ns.RecordingPost(fake)
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as t:
            t.write("Artist,Title\r\nB\u00e9,x\r\n".encode("utf-8"))
        try:
            with open(t.name, "rb") as f:
                resp = rec.post("http://x/u", data={"a": "1"}, files={"songbook_csv": ("songbook.csv", f, "text/csv"), "z": ("z.zip", b"\x00\x01", "application/zip")},
                                headers={"h": "v"}, timeout=7)
                self.assertEqual(f.read(5), b"Artis")       # the app's own handle was not touched
        finally:
            os.unlink(t.name)
        self.assertEqual(resp.json()["count"], 1)
        self.assertEqual(fake.seen[0][0], "http://x/u")
        self.assertEqual(fake.seen[0][3], {"h": "v"})
        c = rec.calls[0]
        self.assertEqual(c["files"]["songbook_csv"], ["songbook.csv", "Artist,Title\r\nB\u00e9,x\r\n", "text/csv"])
        self.assertEqual(c["files"]["z"][1], {"len": 2, "sha256": __import__("hashlib").sha256(b"\x00\x01").hexdigest()})
        self.assertEqual((c["data"], c["headers"], c["timeout"]), ({"a": "1"}, {"h": "v"}, 7))

    def test_an_exception_propagates(self):
        rec = ns.RecordingPost(PostFake(exc=OSError("down")))
        with self.assertRaises(OSError):
            rec.post("http://x/", data={}, files={}, headers={}, timeout=1)
        self.assertEqual(len(rec.calls), 1)

    def test_other_attributes_are_the_real_modules(self):
        fake = PostFake()
        fake.marker = 5
        self.assertEqual(ns.RecordingPost(fake).marker, 5)


class UploadBatches(unittest.TestCase):
    def test_songbook_batch_declines_odd_tracks_and_wrong_call_counts(self):
        call = {"url": "u", "data": {}, "files": {}, "headers": {}, "timeout": 180}
        self.assertIsNone(ns.songbook_batch("b", "t", "k", [{"artist": None, "title": "x"}], [call]))
        self.assertIsNone(ns.songbook_batch("b", "t", "k", [], []))
        self.assertIsNone(ns.songbook_batch("b", "t", "k", [], [call, call]))
        self.assertIsNone(ns.songbook_batch(None, "t", "k", [], [call]))
        self.assertIsNone(ns.songbook_batch("b", "t", "k", [], [dict(call, unreadable=True)]))
        self.assertIsNone(ns.songbook_batch("b", "t", "k", [], [dict(call, positional=True)]))
        self.assertIsNotNone(ns.songbook_batch("b", "t", "k", [{"artist": "a", "title": "t"}, "x", {"title": "only"}], [call]))

    def test_songbook_fold_table_covers_every_string_with_python_semantics(self):
        call = {"url": "u", "data": {}, "files": {}, "headers": {}, "timeout": 180}
        req = ns.songbook_batch("b", "t", "k", [{"artist": " \u0130A ", "title": "\u00df"}, {"title": "x"}], [call])[1]
        self.assertEqual(req["fold"][" \u0130A "], [" \u0130A ".strip(), " \u0130A ".strip().lower()])
        self.assertEqual(req["fold"]["\u00df"], ["\u00df", "\u00df".lower()])
        self.assertIn("", req["fold"])
        self.assertEqual(req["tracks"], [{"artist": " \u0130A ", "title": "\u00df"}, {"title": "x"}])

    def test_support_log_batch_declines_non_text_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "p.zip"
            pkg.write_bytes(b"abc")
            call = {"url": "u", "data": {"reason": "r", "app_version": "v", "platform": "p", "session_window": "w"}, "files": {"logs": ["p.zip", {"len": 3, "sha256": "x"}, "application/zip"]},
                    "headers": {}, "timeout": 30}
            self.assertIsNotNone(ns.support_log_batch({"base_url": "b", "user": "u", "api_key": "k"}, pkg, [call]))
            self.assertIsNone(ns.support_log_batch({"base_url": 5}, pkg, [call]))
            self.assertIsNone(ns.support_log_batch({}, pkg, []))
            req = ns.support_log_batch({"base_url": "b", "extra": "no"}, pkg, [call])[1]
            self.assertEqual(req["package"]["len"], 3)
            self.assertNotIn("extra", req["settings"])


@unittest.skipUnless(os.path.exists(CLI), "singws-netslice not built (cd rust && cargo build --release -p singws-netslice)")
class UploadsReal(unittest.TestCase):
    def setUp(self):
        reset()
        os.environ["SINGWS_NETSLICE_BIN"] = CLI
        self.lines = []

    def songbook(self, tracks, on=True, key=SECRET, fake=None):
        a = app()
        submitted = []
        real_submit, real_req = ns.submit, a.requests
        ns.submit = lambda batch, log: submitted.append(batch)
        a.requests, a._NET_SLICE_SHADOW_ON = fake or PostFake(), on
        try:
            th = a.SongbookUploadThread(" example.test/ ", " wsk ", key, tracks)
            done = []
            th.done.connect(lambda ok, msg, n: done.append((ok, msg, n)))
            th.run()
        finally:
            ns.submit, a.requests, a._NET_SLICE_SHADOW_ON = real_submit, real_req, False
        return submitted, done

    def test_songbook_upload_agrees_and_the_app_outcome_is_unchanged(self):
        tracks = [{"artist": "ABBA", "title": "Dancing, Queen"}, {"artist": "abba", "title": "dancing, queen"}, {"artist": "Beyonc\u00e9", "title": 'Say "hi"'}, "junk", {"artist": "", "title": "x"}]
        off, done_off = self.songbook(tracks, on=False)
        on, done_on = self.songbook(tracks, on=True)
        self.assertEqual(off, [])
        self.assertEqual(done_off, done_on)
        self.assertEqual(done_on[0][0], True)
        self.assertEqual(len(on), 1)
        self.assertEqual(ns.run_shadow(on[0], self.lines.append), "clean")

    def test_a_wrong_songbook_is_a_mismatch_without_secrets_or_titles(self):
        on, _ = self.songbook([{"artist": "Secret Artist", "title": "Secret Title"}])
        name, req, expected = on[0][0]
        wrong = json.loads(json.dumps(expected))
        wrong["files"]["songbook_csv"][1] += "x"
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ns.run_shadow([(name, req, wrong)], self.lines.append), "mismatch")
        self.assertIn("MISMATCH songbook at $.files.songbook_csv[1]", self.lines[-1])
        for secret in (SECRET, "Secret", "example.test", "wsk"):
            self.assertNotIn(secret, self.lines[-1])

    def test_an_upload_failure_still_propagates_to_the_app_with_the_shadow_on(self):
        on, done = self.songbook([{"artist": "A", "title": "T"}], fake=PostFake(exc=OSError("down")))
        self.assertEqual(on, [])
        self.assertEqual(done[0][0], False)
        self.assertIn("down", done[0][1])

    def support(self, settings, on=True):
        a = app()
        submitted = []
        real_submit, real_req = ns.submit, a.requests
        ns.submit = lambda batch, log: submitted.append(batch)
        fake = PostFake()
        a.requests, a._NET_SLICE_SHADOW_ON = fake, on
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "logs.zip"
            pkg.write_bytes(b"PK\x03\x04 binary \x00\xff")
            try:
                result = a.send_log_package_to_developer(settings, pkg, reason="manual", window="w")
            finally:
                ns.submit, a.requests, a._NET_SLICE_SHADOW_ON = real_submit, real_req, False
        return submitted, result, fake

    def test_support_log_upload_agrees_and_the_app_outcome_is_unchanged(self):
        settings = {"base_url": "example.test", "user": "wsk", "api_key": SECRET}
        off, result_off, fake_off = self.support(settings, on=False)
        on, result_on, fake_on = self.support(settings, on=True)
        self.assertEqual(off, [])
        self.assertEqual(result_off, result_on)
        self.assertEqual(fake_off.seen[0][0], fake_on.seen[0][0])
        self.assertEqual(len(on), 1)
        self.assertEqual(ns.run_shadow(on[0], self.lines.append), "clean")

    def test_a_refused_support_log_upload_makes_no_batch(self):
        on, result, fake = self.support({"base_url": "example.test", "user": "wsk"})
        self.assertEqual((on, fake.seen, result[0]), ([], [], False))

    def test_a_support_log_difference_never_prints_the_key(self):
        on, _, _ = self.support({"base_url": "example.test", "user": "wsk", "api_key": SECRET})
        name, req, expected = on[0][0]
        wrong = json.loads(json.dumps(expected))
        wrong["request"]["headers"]["X-API-Key"] = "other"
        self.assertEqual(ns.run_shadow([(name, req, wrong)], self.lines.append), "mismatch")
        self.assertIn("MISMATCH support_log at $.request.headers.X-API-Key", self.lines[-1])
        self.assertNotIn(SECRET, self.lines[-1])


class Launch(unittest.TestCase):
    def setUp(self):
        reset()

    def test_announce_says_enabled_off_and_available(self):
        lines = []
        os.environ["SINGWS_NETSLICE_BIN"] = "/nonexistent"
        with mock.patch.object(ns, "find_helper", return_value=None):          # whatever is built under native/ on this machine
            reset()
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
        self.assertIn("payload = self._session_location_payload_impl(allow_auto_detect)", src)
        self.assertIn("resp = (shadow_post or requests).post(", src)
        self.assertIn("response = (shadow_post or requests).post(", src)
        self.assertIn('for _mod_name in ("logpack_shadow", "relay_msg_shadow", "scan_shadow", "net_slice_shadow", "queue_state_shadow", "update_shadow", "settings_migrate_shadow", "songend_shadow", "karafun_script_shadow", "karafun_monitor_shadow", "karafun_flow_shadow"):', src)
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
