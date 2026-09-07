import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from network_lifecycle import ShutdownRequests


class NetworkLifecycleTests(unittest.TestCase):
    def test_shutdown_rejects_new_tls_work_and_waits_for_existing_request(self):
        entered, release, drained = threading.Event(), threading.Event(), threading.Event()
        client = ShutdownRequests()

        def get(*args, **kwargs):
            entered.set()
            release.wait(2)
            return object()

        fake = SimpleNamespace(get=get, exceptions=SimpleNamespace(ConnectionError=ConnectionError))
        with patch("network_lifecycle.importlib.import_module", return_value=fake):
            worker = threading.Thread(target=lambda: client.get("https://example.test", timeout=1))
            worker.start()
            self.assertTrue(entered.wait(1))
            client.begin_shutdown()
            with self.assertRaises(ConnectionError):
                client.get("https://example.test", timeout=1)
            waiter = threading.Thread(target=lambda: (client.wait_for_idle(), drained.set()))
            waiter.start()
            self.assertFalse(drained.wait(0.02))
            release.set()
            worker.join(2)
            waiter.join(2)
            self.assertTrue(drained.is_set())

    def test_failed_request_releases_shutdown_waiter(self):
        client = ShutdownRequests()
        fake = SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
        with patch("network_lifecycle.importlib.import_module", return_value=fake):
            with self.assertRaises(OSError):
                client.get("https://example.test")
        self.assertEqual(client._active, 0)
        client.begin_shutdown()
        client.wait_for_idle()

    def test_stream_remains_live_until_context_exit_and_close_is_idempotent(self):
        client = ShutdownRequests()
        closed = []
        response = SimpleNamespace(close=lambda: closed.append(True))
        fake = SimpleNamespace(get=lambda *a, **k: response)
        with patch("network_lifecycle.importlib.import_module", return_value=fake):
            with client.get("https://example.test", stream=True) as result:
                self.assertEqual(client._active, 1)
                client.begin_shutdown()
            result.close()
        self.assertEqual(client._active, 0)
        self.assertEqual(closed, [True])
