"""KaraFun Top 100 chart -> the venue server: the once-a-day rule, the upload, the chart reader (fake browser), and the app hooks."""
import _test_profile  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path

import karafun_top100 as T

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")
CONFIG = {"base": "https://example.test", "user": "v1", "key": "secret-key-value"}
BASE = 1_700_000_000.0
SONGS = [{"title": f"Song {i}", "artist": f"Artist {i}", "seconds": 200} for i in range(100)]


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {"ok": True}
    def json(self):
        return self._body


class DueTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "state.json"

    def test_due_when_never_sent_then_not_for_20_hours(self):
        self.assertTrue(T.is_due(BASE, path=self.path))
        T._remember(self.path, BASE)
        self.assertFalse(T.is_due(BASE + 19 * 3600, path=self.path))
        self.assertTrue(T.is_due(BASE + 20 * 3600, path=self.path))

    def test_a_damaged_state_file_means_due(self):
        self.path.write_text("{nope", encoding="utf-8")
        self.assertTrue(T.is_due(BASE, path=self.path))


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "state.json"

    def test_success_sends_and_remembers(self):
        sent = []
        ok, msg = T.refresh(CONFIG, now=BASE, path=self.path, reader=lambda: SONGS, sender=lambda c, s: (sent.append(len(s)) or (True, "sent")))
        self.assertTrue(ok)
        self.assertEqual(sent, [100])
        self.assertFalse(T.is_due(BASE + 3600, path=self.path))

    def test_not_due_does_nothing(self):
        T._remember(self.path, BASE)
        calls = []
        ok, msg = T.refresh(CONFIG, now=BASE + 200, path=self.path, reader=lambda: calls.append("read") or SONGS, sender=lambda c, s: (True, ""))
        self.assertEqual((ok, msg, calls), (True, "not due", []))

    def test_force_ignores_the_daily_limit(self):
        T._remember(self.path, BASE)
        calls = []
        T.refresh(CONFIG, force=True, now=BASE + 200, path=self.path, reader=lambda: calls.append("read") or SONGS, sender=lambda c, s: (True, ""))
        self.assertEqual(calls, ["read"])

    def test_a_failed_read_or_send_is_retried_later(self):
        def bad_reader():
            raise RuntimeError("no chart")
        ok, msg = T.refresh(CONFIG, now=BASE, path=self.path, reader=bad_reader, sender=lambda c, s: (True, ""))
        self.assertFalse(ok)
        self.assertIn("no chart", msg)
        self.assertTrue(T.is_due(BASE + 1, path=self.path))
        ok, msg = T.refresh(CONFIG, now=BASE, path=self.path, reader=lambda: SONGS, sender=lambda c, s: (False, "unauthorized"))
        self.assertEqual((ok, msg), (False, "unauthorized"))
        self.assertTrue(T.is_due(BASE + 1, path=self.path))


class UploadTests(unittest.TestCase):
    def test_posts_json_with_the_key_in_the_header_only(self):
        seen = {}
        def post(url, **kw):
            seen.update(url=url, **kw)
            return FakeResponse()
        ok, msg = T.upload(CONFIG, SONGS, post=post)
        self.assertTrue(ok)
        self.assertEqual(seen["url"], "https://example.test/api/v1/top100_sync.php")
        self.assertEqual(seen["headers"]["X-API-Key"], "secret-key-value")
        self.assertNotIn("secret-key-value", seen["url"] + json.dumps(seen["params"]) + seen["data"])
        self.assertEqual(len(json.loads(seen["data"])["songs"]), 100)

    def test_server_refusals_give_the_reason(self):
        self.assertEqual(T.upload(CONFIG, SONGS, post=lambda *a, **k: FakeResponse(401, {"ok": False, "error": "unauthorized"})), (False, "unauthorized"))

    def test_network_errors_never_raise_or_leak_the_key(self):
        def post(*a, **k):
            raise ConnectionError("boom secret-key-value")
        ok, msg = T.upload(CONFIG, SONGS, post=post)
        self.assertFalse(ok)
        self.assertNotIn("secret-key-value", msg)

    def test_needs_a_server_connection(self):
        self.assertFalse(T.upload({"base": "", "user": "", "key": ""}, SONGS)[0])


class ReaderTests(unittest.TestCase):
    class FakeWeb:
        script = []
        def __init__(self, profile, *, on_message, on_log, chrome_path):
            self.on_message, self.asked, self.closed = on_message, 0, False
        def start(self): pass
        def top(self, chart_id):
            self.asked += 1
            if self.asked == 1:
                self.on_message({"type": "error", "message": "Top: KaraFun is still starting"})
            else:
                self.on_message({"type": "top", "id": chart_id, "total": 100, "songs": SONGS})
        def close(self):
            self.closed = True
            self.on_message({"type": "error", "message": "Chrome stopped"})        # what the helper says as it exits

    def test_asks_again_until_the_page_answers_and_ignores_the_closing_message(self):
        T_wait = T.READ_TIMEOUT_S
        made = []
        def make(*a, **k):
            made.append(self.FakeWeb(*a, **k)); return made[-1]
        import time as _t
        real = _t.monotonic
        # speed up: the reader asks every 4 s of its own clock
        clock = {"t": 0.0}
        T.time.monotonic = lambda: clock["t"]
        orig_wait = T.threading.Event.wait
        try:
            T.threading.Event.wait = lambda self_, timeout=None: (clock.__setitem__("t", clock["t"] + (timeout or 0)) or self_.is_set())
            songs = T.read_chart(make_web=make, find_browser=lambda p: Path("/x/chrome"), profile_dir="/tmp/x", timeout=60)
        finally:
            T.time.monotonic = real
            T.threading.Event.wait = orig_wait
        self.assertEqual(len(songs), 100)
        self.assertTrue(made[0].closed)

    def test_a_real_error_is_reported(self):
        class Broken(self.FakeWeb):
            def top(self, chart_id):
                self.on_message({"type": "error", "message": "Top: the chart 7 did not load"})
        with self.assertRaises(RuntimeError) as ctx:
            T.read_chart(make_web=lambda *a, **k: Broken(*a, **k), find_browser=lambda p: Path("/x/chrome"), profile_dir="/tmp/x", timeout=5)
        self.assertIn("did not load", str(ctx.exception))


class AppHookTests(unittest.TestCase):
    def test_off_by_default_and_gated_in_the_app(self):
        self.assertRegex(SOURCE, r'"karafun_top100_sync": False,')
        body = SOURCE[SOURCE.index("    def _karafun_top100_check(self):"):][:1800]
        self.assertIn('self.settings.get("karafun_top100_sync", False)', body)
        self.assertIn("karafun_top100.is_due()", body)
        self.assertIn('sys.platform != "darwin"', body)

    def test_the_hourly_timer_is_started_once_and_the_toggle_does_not_start_another(self):
        self.assertEqual(SOURCE.count("QTimer.singleShot(120000, self._karafun_top100_tick)"), 1)
        self.assertIn("QTimer.singleShot(500, self._karafun_top100_check)", SOURCE)
        tick = SOURCE[SOURCE.index("    def _karafun_top100_tick(self):"):][:400]
        self.assertEqual(tick.count("QTimer.singleShot(3600 * 1000, self._karafun_top100_tick)"), 1)


if __name__ == "__main__":
    unittest.main()
