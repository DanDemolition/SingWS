import importlib.util
import os
import types
import unittest


def load_main_module():
    os.environ["SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"] = "1"
    spec = importlib.util.spec_from_file_location("singws_main_ticker_speed", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClock:
    """Deterministic stand-in for time.monotonic()."""

    def __init__(self):
        self.t = 1000.0

    def advance(self, seconds):
        self.t += seconds

    def __call__(self):
        return self.t


class FakeOwner:
    def __init__(self, saved_speed):
        self.settings = {"ticker_speed_px_per_sec": saved_speed}


class TickerSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_ticker(self, speed):
        """Build a Ticker without running Qt's __init__, with just the state
        _on_frame / set_scroll_speed touch."""
        t = self.singws.Ticker.__new__(self.singws.Ticker)
        t._is_scrolling_enabled = True
        t._active_width = 1000
        t._right_width = 80
        t._right_margin = 32
        t._gap = 16
        t._scroll_x = 1000.0
        t._pending_text = ""
        t._last_frame_ts = None
        t._frame_interval_ms = self.singws.TICKER_FRAME_INTERVAL_MS
        t._frame_dt_ema = None
        t._scroll_speed_px_per_sec = float(speed)
        t._external_settings_owner = FakeOwner(speed)
        # Stub the Qt bits _on_frame / backoff would otherwise call.
        t.isVisible = lambda: True
        t.width = lambda: 1280
        t.update = lambda: None
        t._reset_cycle = lambda start_from_edge=True: None
        t.frame_timer = types.SimpleNamespace(start=lambda *a: None, isActive=lambda: True)
        return t

    def distance_over(self, speed, dt, clock):
        t = self.make_ticker(speed)
        import importlib
        time_mod = importlib.import_module("time")
        original = time_mod.monotonic
        time_mod.monotonic = clock
        try:
            t._on_frame()          # establishes the clock, no movement
            start = t._scroll_x
            clock.advance(dt)
            t._on_frame()          # moves by speed * dt
            return start - t._scroll_x
        finally:
            time_mod.monotonic = original

    def test_first_frame_does_not_move(self):
        clock = FakeClock()
        t = self.make_ticker(100.0)
        import importlib
        time_mod = importlib.import_module("time")
        original = time_mod.monotonic
        time_mod.monotonic = clock
        try:
            before = t._scroll_x
            t._on_frame()
            self.assertEqual(t._scroll_x, before)
        finally:
            time_mod.monotonic = original

    def test_distance_is_speed_times_elapsed_time(self):
        # dt within the 0.25s stall-protection window moves exactly speed * dt.
        moved = self.distance_over(100.0, 0.1, FakeClock())
        self.assertAlmostEqual(moved, 10.0, places=3)  # 100 px/s * 0.1 s

    def test_full_range_is_distinct_no_clamp_ceiling(self):
        # The old bug clamped everything above ~167 px/s to the same 6 ms tick,
        # so max looked like mid. Time-based movement keeps the whole range live.
        dt = 0.1
        slow = self.distance_over(self.singws.TICKER_SPEED_MIN, dt, FakeClock())
        mid = self.distance_over(170.0, dt, FakeClock())
        fast = self.distance_over(self.singws.TICKER_SPEED_MAX, dt, FakeClock())
        self.assertAlmostEqual(slow, 20.0 * dt, places=3)
        self.assertAlmostEqual(mid, 170.0 * dt, places=3)
        self.assertAlmostEqual(fast, self.singws.TICKER_SPEED_MAX * dt, places=3)
        # Max must be meaningfully faster than mid (this was broken before).
        self.assertGreater(fast, mid * 1.5)
        # Fullscreen needs a real "extreme fast" setting, not just a modest bump.
        self.assertGreaterEqual(self.singws.TICKER_SPEED_MAX, 900.0)

    def test_large_dt_is_clamped_to_avoid_leaps(self):
        # A stalled event loop should not fling the text across the screen.
        moved = self.distance_over(self.singws.TICKER_SPEED_MAX, 5.0, FakeClock())
        self.assertAlmostEqual(moved, self.singws.TICKER_SPEED_MAX * 0.25, places=3)  # dt capped at 0.25s

    def test_set_scroll_speed_does_not_change_timer_interval(self):
        t = self.make_ticker(78.0)
        t.set_scroll_speed(self.singws.TICKER_SPEED_MAX)
        self.assertEqual(t._frame_interval_ms, self.singws.TICKER_FRAME_INTERVAL_MS)
        self.assertAlmostEqual(t._scroll_speed_px_per_sec, self.singws.TICKER_SPEED_MAX)

    def test_set_scroll_speed_clamps_to_range(self):
        t = self.make_ticker(78.0)
        t.set_scroll_speed(99999.0)
        self.assertAlmostEqual(t._scroll_speed_px_per_sec, self.singws.TICKER_SPEED_MAX)
        t.set_scroll_speed(0.0)
        self.assertAlmostEqual(t._scroll_speed_px_per_sec, self.singws.TICKER_SPEED_MIN)

    def test_set_scroll_speed_then_frame_moves_at_new_speed(self):
        # The real runtime path: operator changes speed -> next frames move at it.
        t = self.make_ticker(78.0)
        t.set_scroll_speed(self.singws.TICKER_SPEED_MAX)
        clock = FakeClock()
        import importlib
        time_mod = importlib.import_module("time")
        original = time_mod.monotonic
        time_mod.monotonic = clock
        try:
            t._on_frame()
            start = t._scroll_x
            clock.advance(0.1)
            t._on_frame()
            moved_fast = start - t._scroll_x

            t.set_scroll_speed(self.singws.TICKER_SPEED_MIN)
            t._last_frame_ts = None
            t._on_frame()
            start = t._scroll_x
            clock.advance(0.1)
            t._on_frame()
            moved_slow = start - t._scroll_x
        finally:
            time_mod.monotonic = original
        self.assertAlmostEqual(moved_fast, self.singws.TICKER_SPEED_MAX * 0.1, places=3)
        self.assertAlmostEqual(moved_slow, self.singws.TICKER_SPEED_MIN * 0.1, places=3)
        self.assertGreater(moved_fast, moved_slow)

    def test_speed_unaffected_by_cadence_backoff(self):
        # Even after the loop backs off 120 -> 60 FPS, distance/time is unchanged.
        t = self.make_ticker(200.0)
        t._frame_interval_ms = 8
        for _ in range(50):
            t._maybe_back_off_cadence(0.030)  # force backoff to 60 FPS
        self.assertGreaterEqual(t._frame_interval_ms, 16)
        moved = self.distance_over(200.0, 0.1, FakeClock())
        self.assertAlmostEqual(moved, 20.0, places=3)  # 200 px/s * 0.1 s

    def test_refresh_rate_to_interval_targets_120_with_60_floor(self):
        f = self.singws.ticker_frame_interval_ms_for_refresh
        self.assertEqual(f(60.0), 17)    # ~60 FPS panel -> ~16-17 ms
        self.assertEqual(f(120.0), 8)    # ProMotion / 120 Hz -> ~8 ms
        self.assertEqual(f(144.0), 8)    # clamp above 120
        self.assertEqual(f(240.0), 8)
        self.assertEqual(f(30.0), 17)    # below 60 -> 60 floor
        self.assertEqual(f(0.0), 17)     # unknown -> 60 floor
        # 120 FPS cadence must be roughly twice as frequent as 60.
        self.assertLess(f(120.0), f(60.0))

    def test_speed_is_independent_of_cadence(self):
        # Same elapsed wall-clock time -> same distance, whether 60 or 120 FPS.
        # At 120 FPS two 8 ms frames cover the same ground as one 16 ms frame.
        clock = FakeClock()
        moved_one_big = self.distance_over(300.0, 0.016, clock)
        t = self.make_ticker(300.0)
        import importlib
        time_mod = importlib.import_module("time")
        original = time_mod.monotonic
        time_mod.monotonic = clock
        try:
            t._on_frame()
            start = t._scroll_x
            for _ in range(2):
                clock.advance(0.008)
                t._on_frame()
            moved_two_small = start - t._scroll_x
        finally:
            time_mod.monotonic = original
        self.assertAlmostEqual(moved_one_big, moved_two_small, places=4)

    def test_speed_is_independent_of_fullscreen_width(self):
        # Width changes affect where a cycle starts, not px/sec movement.
        clock = FakeClock()
        t = self.make_ticker(500.0)
        import importlib
        time_mod = importlib.import_module("time")
        original = time_mod.monotonic
        time_mod.monotonic = clock
        try:
            t.width = lambda: 1280
            t._on_frame()
            start = t._scroll_x
            clock.advance(0.1)
            t._on_frame()
            moved_normal = start - t._scroll_x

            t.width = lambda: 3840
            t._last_frame_ts = None
            t._on_frame()
            start = t._scroll_x
            clock.advance(0.1)
            t._on_frame()
            moved_fullscreen = start - t._scroll_x
        finally:
            time_mod.monotonic = original
        self.assertAlmostEqual(moved_normal, moved_fullscreen, places=4)

    def test_cadence_backs_off_from_120_when_loop_is_slow(self):
        t = self.make_ticker(100.0)
        t._frame_interval_ms = 8   # pretend we are targeting 120 FPS
        t._frame_dt_ema = None
        # Feed sustained slow frames (~30 ms each => can't hold 120 FPS).
        for _ in range(50):
            t._maybe_back_off_cadence(0.030)
        self.assertEqual(
            t._frame_interval_ms,
            self.singws.ticker_frame_interval_ms_for_refresh(self.singws.TICKER_TARGET_FPS_MIN),
        )

    def test_cadence_holds_120_when_loop_keeps_up(self):
        t = self.make_ticker(100.0)
        t._frame_interval_ms = 8
        t._frame_dt_ema = None
        for _ in range(50):
            t._maybe_back_off_cadence(0.008)  # comfortably hitting 120 FPS
        self.assertEqual(t._frame_interval_ms, 8)

    def test_strip_logical_size_accounts_for_dpr(self):
        # Avoid constructing a real QPixmap (needs a QApplication); the helper
        # only touches isNull()/width()/devicePixelRatio().
        class FakePixmap:
            def isNull(self):
                return False

            def width(self):
                return 400

            def height(self):
                return 60

            def devicePixelRatio(self):
                return 2.0

        w, h = self.singws.Ticker._strip_logical_size(FakePixmap())
        self.assertEqual((w, h), (200, 30))  # device 400x60 @2x -> logical 200x30


if __name__ == "__main__":
    unittest.main()
