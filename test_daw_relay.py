"""Tests for the v2 DAW host-control-over-WebSocket path (HostControlRelayWorker
wiring in 0.2.18.1.py). Pure logic — no live sockets."""
import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_daw_relay", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeWorker:
    def __init__(self):
        self.sent = []
        self._conn = True

    def is_connected(self):
        return self._conn

    def send_json(self, obj):
        self.sent.append(obj)
        return True


def make_app(module):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {"request_transport": "auto", "user": "acme"}
    return app


class DawRelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def setUp(self):
        # Pretend QtWebSockets is present so version gating can return True.
        self.singws.QTWEBSOCKETS_AVAILABLE = True
        if self.singws.QWebSocket is None:
            self.singws.QWebSocket = object

    def test_version_gate(self):
        app = make_app(self.singws)
        # APP_VERSION (0.3.0.10) > (0,3,0,0) → supported on wskar.com.
        self.assertTrue(app._should_use_host_control_relay("https://wskar.com", "acme", "key"))
        # Wrong host → not used.
        self.assertFalse(app._should_use_host_control_relay("https://example.com", "acme", "key"))
        # Explicit polling preference disables it.
        app.settings["request_transport"] = "polling"
        self.assertFalse(app._should_use_host_control_relay("https://wskar.com", "acme", "key"))

    def test_version_gate_blocks_old_app(self):
        app = make_app(self.singws)
        self.assertLess(self.singws.KaraokeApp._version_tuple("0.3.0.0"),
                        self.singws.KaraokeApp.DAW_RELAY_MIN_APP_VERSION)
        self.assertGreaterEqual(self.singws.KaraokeApp._version_tuple("0.3.0.1"),
                                self.singws.KaraokeApp.DAW_RELAY_MIN_APP_VERSION)

    def test_command_routed_and_acked(self):
        app = make_app(self.singws)
        worker = FakeWorker()
        app.host_relay_worker = worker
        app._host_relay_last_full_sig = ""
        app._host_relay_last_full_at = 0.0
        calls = []
        app._execute_host_control_command = lambda action, args: (calls.append((action, args)) or (True, "did " + action))
        app._host_control_state = lambda: {
            "rotation": {"current": {"singer": "Bob"}},
            "playback": {"position_seconds": 1.0, "duration_seconds": 2.0, "remaining_seconds": 1.0,
                         "is_playing": True, "key": 0, "tempo": 1.0, "bgm_volume": 0.8, "title": "x"},
        }
        app._on_host_relay_command({"command": "set_key", "args": {"value": -3}, "request_id": "r1"})

        self.assertEqual(calls, [("set_key", {"value": -3})])
        acks = [m for m in worker.sent if m.get("type") == "ack"]
        self.assertEqual(len(acks), 1)
        self.assertEqual(acks[0]["request_id"], "r1")
        self.assertTrue(acks[0]["ok"])
        # Forced full state + tick pushed after the command.
        self.assertTrue(any(m.get("type") == "state" for m in worker.sent))
        self.assertTrue(any(m.get("type") == "playback_tick" for m in worker.sent))

    def test_publish_emits_tick_and_state(self):
        app = make_app(self.singws)
        worker = FakeWorker()
        app.host_relay_worker = worker
        app._host_relay_last_full_sig = ""
        app._host_relay_last_full_at = 0.0
        app._host_control_state = lambda: {
            "rotation": {"current": {"singer": "Bob"}},
            "playback": {"position_seconds": 5.0, "duration_seconds": 200.0, "remaining_seconds": 195.0,
                         "is_playing": True, "key": 2, "tempo": 1.1, "bgm_volume": 0.6, "title": "Song"},
        }
        app._publish_host_state_ws(force=True)
        types = [m.get("type") for m in worker.sent]
        self.assertIn("playback_tick", types)
        self.assertIn("state", types)
        # Disconnected worker publishes nothing.
        worker._conn = False
        before = len(worker.sent)
        app._publish_host_state_ws(force=True)
        self.assertEqual(len(worker.sent), before)


if __name__ == "__main__":
    unittest.main()
