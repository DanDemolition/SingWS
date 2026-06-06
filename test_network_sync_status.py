import importlib.util
import os
import unittest

import requests


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_network_sync", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code=200, text="[]", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        if self._payload is not None:
            return self._payload
        return []


class FakeRequests:
    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if not self.routes:
            return FakeResponse(200, "[]")
        item = self.routes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class NetworkSyncStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_success_reports_connected_and_normalizes_base_url(self):
        fake = FakeRequests([
            FakeResponse(200, "[]", []),
            FakeResponse(200, "1"),
            FakeResponse(200, '{"commands": []}', {"commands": []}),
            FakeResponse(405, "method not allowed"),
            FakeResponse(405, "method not allowed"),
        ])

        status = self.singws.probe_network_sync_status("beta.wskar.com/", "venue", "secret", requests_module=fake)

        self.assertTrue(status["ok"])
        self.assertFalse(status["partial"])
        self.assertEqual(status["base_url"], "https://beta.wskar.com")
        self.assertTrue(status["accepting"])
        self.assertIn("/get_requests.php", fake.calls[0][1])
        self.assertEqual(fake.calls[0][2]["params"]["user"], "venue")

    def test_request_intake_success_with_supporting_failure_is_partial(self):
        fake = FakeRequests([
            FakeResponse(200, "[]", []),
            FakeResponse(404, "missing"),
            FakeResponse(200, '{"commands": []}', {"commands": []}),
            FakeResponse(405, "method not allowed"),
            FakeResponse(405, "method not allowed"),
        ])

        status = self.singws.probe_network_sync_status("https://beta.wskar.com", "venue", "secret", requests_module=fake)

        self.assertTrue(status["ok"])
        self.assertTrue(status["partial"])
        self.assertIn("accepting", status["message"])

    def test_request_intake_timeout_fails_gracefully(self):
        fake = FakeRequests([
            requests.exceptions.Timeout("slow"),
            FakeResponse(200, "1"),
            FakeResponse(405, "method not allowed"),
            FakeResponse(405, "method not allowed"),
        ])

        status = self.singws.probe_network_sync_status("https://beta.wskar.com", "venue", "", timeout_sec=2, requests_module=fake)

        self.assertFalse(status["ok"])
        self.assertFalse(status["connected"])
        self.assertIn("timeout", status["message"].lower())


if __name__ == "__main__":
    unittest.main()
