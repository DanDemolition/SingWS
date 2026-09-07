import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


class HostStateRetryTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse(Path("0.2.18.1.py").read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "KaraokeApp")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_sync_host_control_state_now")
        self.workers, self.calls, self.delays = [], [], []
        self.response_code = 500
        self.now = 10.0

        def post(*args, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(status_code=self.response_code)

        namespace = {
            "json": json, "time": SimpleNamespace(monotonic=lambda: self.now),
            "threading": SimpleNamespace(Thread=lambda target, **kwargs: SimpleNamespace(start=lambda: self.workers.append(target))),
            "requests": SimpleNamespace(post=post), "_network_normalize_base_url": lambda s: s,
            "_diag": lambda *a: None, "_perf_log_if_slow": lambda *a: None,
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), "<host-state>", "exec"), namespace)
        self.sync = namespace["_sync_host_control_state_now"]
        self.state = {"playing": False}
        self.host = SimpleNamespace(
            settings={"base_url": "https://example.test", "user": "fixture", "api_key": "fixture"},
            _host_control_state=lambda: self.state.copy(),
            _run_on_ui_thread=lambda callback: callback(),
            _schedule_host_control_state_sync=lambda: None,
            _host_control_state_timer=SimpleNamespace(start=self.delays.append),
        )

    def test_http_failure_is_not_cached_and_new_updates_respect_backoff(self):
        self.sync(self.host)
        self.workers.pop()()
        self.assertFalse(hasattr(self.host, "_last_host_control_state_sig"))
        self.sync(self.host)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.workers, [])
        self.now += 2
        self.response_code = 200
        self.sync(self.host)
        self.workers.pop()()
        self.assertEqual(json.loads(self.host._last_host_control_state_sig), self.state)

    def test_inflight_updates_are_serial_and_latest_state_is_sent_next(self):
        self.response_code = 200
        self.sync(self.host)
        self.state["playing"] = True
        self.sync(self.host)
        self.assertEqual(len(self.workers), 1)
        self.workers.pop()()
        self.sync(self.host)
        self.workers.pop()()
        self.assertEqual([json.loads(call["data"]["state_json"])["playing"] for call in self.calls], [False, True])
