import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import ctypes
import http.server
import json
import os
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import requests

import wire_shadow as ws
from network_lifecycle import ShutdownRequests
from tools import wire_parity as wp

_DYLIB = Path(os.environ.get("SINGWS_DSP_LIB") or os.path.expanduser("~/.cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))
_SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
SECRET = "SECRET-API-KEY-12345"


def lib():
    handle = ctypes.CDLL(str(_DYLIB))
    fn = handle.singws_wire_json
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
    fn.restype = ctypes.c_int64
    return handle


def sent_for(method, url, **kw):
    """The PreparedRequest requests itself builds, wrapped like the response object the observer receives."""
    prepared = requests.Request(method, url, **kw).prepare()
    return SimpleNamespace(request=prepared, history=[], status_code=200)


class Fake:
    def __init__(self, answer):
        self.answer = json.dumps(answer).encode()
        self.singws_wire_json = self._call

    def _call(self, request, buf, cap):
        ctypes.memmove(buf, self.answer, len(self.answer))
        return len(self.answer)


class BehaviourTests(unittest.TestCase):
    def test_unavailable_library_is_a_silent_no_op(self):
        lines = []
        sh = ws.WireShadow(lines.append, lib=None)
        sh.available = False
        self.assertIsNone(sh.observe("post", ("https://x.test/a.php",), {"data": {"a": 1}}, sent_for("POST", "https://x.test/a.php", data={"a": 1})))
        self.assertEqual(lines, [])

    def test_a_disagreement_is_logged_with_field_names_and_never_the_values(self):
        lines = []
        wrong = {"ok": True, "method": "POST", "url": "https://x.test/api/v1/delete_remote_request.php", "body": "DIFFERENT", "content_type":
                 "application/x-www-form-urlencoded", "content_length": 9}
        sh = ws.WireShadow(lines.append, lib=Fake(wrong))
        kwargs = {"data": {"user": "dan", "api_key": SECRET}, "headers": {"X-API-Key": SECRET}}
        resp = sent_for("POST", "https://x.test/api/v1/delete_remote_request.php?key=" + SECRET, data=kwargs["data"])
        self.assertFalse(sh.observe("post", ("https://x.test/api/v1/delete_remote_request.php?key=" + SECRET,), kwargs, resp))
        text = "\n".join(lines)
        self.assertIn("MISMATCH endpoint=delete_remote_request.php kind=form fields=", text)
        self.assertIn("body", text)
        self.assertIn("first_body_difference_at=", text)
        self.assertIn("sent_len=", text)
        self.assertEqual((sh.mismatches, sh.checks), (1, 1), "the mismatch is counted, not only logged")
        self.assertNotIn(SECRET, text)
        self.assertNotIn("x.test", text, "the host is never logged")
        self.assertNotIn("dan", text.replace("delete_remote_request", ""))

    def test_declined_calls_are_counted_not_mismatched_and_logged_once_per_reason(self):
        lines = []
        sh = ws.WireShadow(lines.append, lib=Fake({"ok": True}))
        url = "https://x.test/api/v1/upload_songbook.php"
        for _ in range(3):
            sh.check("POST", url, {"data": {"a": 1}, "files": {"f": ("a.csv", b"x")}}, requests.Request("POST", url, files={"f": ("a.csv", b"x")}, data={"a": 1}).prepare())
        self.assertEqual((sh.mismatches, sh.checks, sh.declined), (0, 0, 3))
        self.assertEqual(sum("declined endpoint=upload_songbook.php reason=multipart" in l for l in lines), 1)

    def test_other_declines(self):
        sh = ws.WireShadow(lambda _t: None, lib=Fake({"ok": True}))
        base = "https://x.test/a.php"
        prepared = requests.Request("POST", base, data="raw=1").prepare()
        self.assertIsNone(sh.check("POST", base, {"data": "raw=1"}, prepared))                       # str data
        self.assertIsNone(sh.check("POST", base, {"data": b"raw=1"}, prepared))                      # bytes data
        big = requests.Request("POST", base, data={"k": "x" * (ws.MAX_BODY + 10)}).prepare()
        self.assertIsNone(sh.check("POST", base, {"data": {"k": "x"}}, big))                          # large body
        self.assertIsNone(sh.check("POST", base, {"data": {"k": object()}}, sent_for("POST", base, data={"k": "x"}).request))  # not JSON
        self.assertEqual(sh.declined, 4)
        self.assertEqual(sh.mismatches, 0)

    def test_a_failing_log_callback_cannot_break_the_caller(self):
        def boom(_t):
            raise RuntimeError("log failed")
        sh = ws.WireShadow(boom, lib=Fake({"ok": False, "error": "x"}))
        sh.observe("get", ("https://x.test/a.php",), {}, sent_for("GET", "https://x.test/a.php"))


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the wire export")
class RealLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = lib()

    def test_every_random_call_the_generator_makes_agrees_with_what_requests_prepared(self):
        sh = ws.WireShadow(lambda _t: None, lib=self.lib)
        cases = wp.make_cases(4000, 99)
        for case in cases:
            kwargs = {k: case[k] for k in ("params", "data", "json") if k in case}
            sh.check(case["method"].upper(), case["url"], kwargs, requests.Request(case["method"], case["url"], **kwargs).prepare())
        self.assertEqual(sh.mismatches, 0)
        self.assertEqual(sh.declined, 0)
        self.assertEqual(sh.checks, 4000)

    def test_a_header_the_caller_set_is_not_compared(self):
        lines = []
        sh = ws.WireShadow(lines.append, lib=self.lib)
        url = "https://x.test/a.php"
        kwargs = {"data": {"a": 1}, "headers": {"Content-Type": "text/plain; charset=utf-8", "Content-Length": "3"}}
        self.assertTrue(sh.check("POST", url, kwargs, requests.Request("POST", url, **kwargs).prepare()))
        self.assertEqual(sh.mismatches, 0)

    def test_after_a_redirect_the_first_request_is_the_one_compared(self):
        sh = ws.WireShadow(lambda _t: None, lib=self.lib)
        first = requests.Request("POST", "https://x.test/api/v1/a.php", data={"a": "b c"}).prepare()
        second = requests.Request("GET", "https://x.test/api/v1/moved.php").prepare()  # what the redirect target received
        resp = SimpleNamespace(request=second, history=[SimpleNamespace(request=first)])
        self.assertTrue(sh.observe("post", ("https://x.test/api/v1/a.php",), {"data": {"a": "b c"}}, resp))

    def test_a_tampered_request_is_caught_in_every_compared_field(self):
        url = "https://x.test/a.php"
        for field, mutate in (("url", lambda p: setattr(p, "url", p.url + "&x=1")), ("body", lambda p: setattr(p, "body", "a=2")),
                              ("content_type", lambda p: p.headers.__setitem__("Content-Type", "text/plain")),
                              ("content_length", lambda p: p.headers.__setitem__("Content-Length", "99"))):
            lines = []
            sh = ws.WireShadow(lines.append, lib=self.lib)
            resp = sent_for("POST", url, data={"a": 1})
            mutate(resp.request)
            self.assertFalse(sh.observe("post", (url,), {"data": {"a": 1}}, resp), field)
            self.assertIn(field, "\n".join(lines))


class _Handler(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        _Handler.seen.append((self.command, self.path, body, self.headers.get("Content-Type")))
        out = json.dumps({"ok": True, "echo": body.decode("utf-8", "replace")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _reply


@unittest.skipUnless(_DYLIB.exists(), "needs the Rust dylib with the wire export")
class ThroughTheRealChokePointTests(unittest.TestCase):
    """Real HTTP to a server on 127.0.0.1 through the app's actual ShutdownRequests object: the hook must change nothing."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.lib = lib()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.seen.clear()
        self.lines = []
        self.client = ShutdownRequests()
        self.shadow = ws.WireShadow(self.lines.append, lib=self.lib)

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/api/v1/{name}"

    def test_form_get_and_json_calls_are_observed_and_the_responses_are_untouched(self):
        plain = ShutdownRequests()
        self.client.set_observer(self.shadow.observe)
        calls = [("post", self.url("delete_remote_request.php"), {"data": {"user": "dan", "request_id": 7, "reason": "host remove"}}),
                 ("get", self.url("get_requests_v2.php"), {"params": {"since": 5, "q": "a b"}}),
                 ("post", self.url("singer_history_sync.php"), {"json": {"singers": [{"name": "Zoë", "n": 3}]}}),
                 ("post", self.url("clear_remote_queue.php"), {"data": {}})]
        for name, url, kw in calls:
            with_hook = getattr(self.client, name)(url, timeout=3, headers={"X-API-Key": SECRET, "Accept": "application/json"}, **kw)
            without = getattr(plain, name)(url, timeout=3, headers={"X-API-Key": SECRET, "Accept": "application/json"}, **kw)
            self.assertEqual((with_hook.status_code, with_hook.text), (without.status_code, without.text))
        self.assertEqual(self.shadow.checks, 4)
        self.assertEqual(self.shadow.mismatches, 0, self.lines)
        kinds = {l.split("kind=")[1].split(" ")[0] for l in self.lines if "decision" in l}
        self.assertEqual(kinds, {"form", "get", "json", "post_empty"})
        self.assertNotIn(SECRET, "\n".join(self.lines))
        self.assertNotIn("127.0.0.1", "\n".join(self.lines))

    def test_errors_propagate_exactly_as_without_the_hook_and_the_observer_is_not_called(self):
        calls = []
        self.client.set_observer(lambda *a: calls.append(a))
        closed = f"http://127.0.0.1:{self.server.server_address[1] + 1}/x.php"  # nothing listens here
        with self.assertRaises(requests.exceptions.ConnectionError):
            self.client.post(closed, data={"a": 1}, timeout=2)
        self.assertEqual(calls, [])

    def test_an_observer_that_raises_cannot_affect_the_response_or_the_accounting(self):
        def boom(*a):
            raise RuntimeError("shadow bug")
        self.client.set_observer(boom)
        resp = self.client.post(self.url("a.php"), data={"a": 1}, timeout=3)
        self.assertEqual(resp.status_code, 200)
        self.client.begin_shutdown()
        done = threading.Event()
        threading.Thread(target=lambda: (self.client.wait_for_idle(), done.set())).start()
        self.assertTrue(done.wait(2), "the in-flight counter was released even though the observer raised")

    def test_streaming_calls_are_not_observed(self):
        calls = []
        self.client.set_observer(lambda *a: calls.append(a))
        with self.client.get(self.url("a.php"), stream=True, timeout=3) as r:
            self.assertEqual(r.status_code, 200)
        self.assertEqual(calls, [])

    def test_clearing_the_observer_restores_the_plain_behaviour(self):
        calls = []
        self.client.set_observer(lambda *a: calls.append(a))
        self.client.set_observer(None)
        self.client.get(self.url("a.php"), timeout=3)
        self.assertEqual(calls, [])

    def test_the_server_really_received_what_the_shadow_checked(self):
        self.client.set_observer(self.shadow.observe)
        self.client.post(self.url("a.php"), data={"user": "dan", "title": "Don't Stop & é"}, timeout=3)
        method, path, body, ctype = _Handler.seen[0]
        self.assertEqual((method, path, ctype), ("POST", "/api/v1/a.php", "application/x-www-form-urlencoded"))
        self.assertEqual(body.decode(), "user=dan&title=Don%27t+Stop+%26+%C3%A9")
        self.assertEqual(self.shadow.mismatches, 0)


class WiringTests(unittest.TestCase):
    def test_off_by_default_installed_after_settings_load_and_uses_the_choke_point(self):
        self.assertIn('"wire_shadow": False', _SRC)
        self.assertLess(_SRC.index("self.settings = self.load_settings()\n        _install_wire_shadow(self.settings)"),
                        _SRC.index("# The background player was built above"))
        block = _SRC[_SRC.index("def _install_wire_shadow"):][:700]
        self.assertIn("requests.set_observer(shadow.observe)", block)
        self.assertIn("except Exception:", block)

    def test_the_observer_runs_after_the_real_response_and_inside_the_accounting(self):
        life = Path(__file__).with_name("network_lifecycle.py").read_text()
        call = life[life.index("def call(*args, **kwargs):"):life.index("def _finished")]
        self.assertLess(call.index("response = getattr(module, name)(*args, **kwargs)"), call.index("observer(name, args, kwargs, response)"))
        self.assertLess(call.index('if kwargs.get("stream", False):'), call.index("observer(name, args, kwargs, response)"))
        self.assertLess(call.index("observer(name, args, kwargs, response)"), call.index("return response"))
        self.assertIn("finally:", call)
        self.assertIn("except Exception:\n                        pass", call)

    def test_specs_bundle_the_module_and_the_library_exports_the_function(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = Path(__file__).with_name(spec).read_text()
            self.assertIn('"wire_shadow.py"', text)
            self.assertIn("'wire_shadow'", text)
        ffi = (Path(__file__).parent / "rust/crates/singws-dsp-ffi/src/lib.rs").read_text()
        self.assertIn("pub unsafe extern \"C\" fn singws_wire_json", ffi)

    def test_the_wire_crate_still_cannot_network(self):
        cargo = (Path(__file__).parent / "rust/crates/singws-wire/Cargo.toml").read_text()
        deps = cargo[cargo.index("[dependencies]"):]
        for name in ("reqwest", "hyper", "ureq", "tokio", "rustls", "native-tls", "tungstenite"):
            self.assertNotIn(name, deps)


if __name__ == "__main__":
    unittest.main()
