"""KaraFun must be running and ready before the song-start automation searches or clicks (2026-10-01 show: the first,
cold, run searched the moment KaraFun was launched and "did not report active playback")."""
import ast
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def build(clock):
    tree = ast.parse(SOURCE)
    wanted = {"KARAFUN_READY_TIMEOUT_S", "KARAFUN_COLD_SETTLE_S"}
    body = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in wanted for t in n.targets)]
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name in {"_karafun_wait_until_ready", "_karafun_main_window_state", "_karafun_wake_window"}:
                    sub.decorator_list = []
                    body.append(sub)
    ns = {"time": clock, "_diag": lambda *a: None, "subprocess": subprocess}
    exec(compile(ast.Module(body=body, type_ignores=[]), "cold-start", "exec"), ns)
    return ns


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def host(ns, states):
    seq = list(states)
    h = SimpleNamespace()
    h._karafun_main_window_state = lambda: seq.pop(0) if len(seq) > 1 else seq[0]
    h.wake_calls = []
    h._karafun_wake_window = lambda: h.wake_calls.append(1)
    h.wait = lambda **kw: ns["_karafun_wait_until_ready"](h, **kw)
    return h


class WaitUntilReadyTests(unittest.TestCase):
    def test_an_already_running_karafun_goes_straight_through(self):
        clock = FakeClock(); ns = build(clock)
        h = host(ns, ["READY"])
        h.wait(was_running=True, session_is_current=lambda: True)
        self.assertEqual(clock.sleeps, [])

    def test_a_cold_start_waits_for_the_window_then_settles(self):
        clock = FakeClock(); ns = build(clock)
        h = host(ns, ["NOT_RUNNING", "NO_WINDOW", "NO_WINDOW", "READY"])
        h.wait(was_running=False, session_is_current=lambda: True)
        self.assertEqual(clock.sleeps[:3], [0.5, 0.5, 0.5])
        self.assertEqual(clock.sleeps[-1], ns["KARAFUN_COLD_SETTLE_S"])

    def test_it_gives_up_with_a_plain_message(self):
        clock = FakeClock(); ns = build(clock)
        h = host(ns, ["NO_WINDOW"])
        with self.assertRaises(RuntimeError) as ctx:
            h.wait(was_running=False, session_is_current=lambda: True, timeout=5)
        self.assertIn("did not finish starting within 5 seconds", str(ctx.exception))
        self.assertLessEqual(clock.now - 1000.0, 6.0)

    def test_it_cannot_block_forever_when_accessibility_is_missing(self):
        clock = FakeClock(); ns = build(clock)
        h = host(ns, ["ERROR"])
        h.wait(was_running=False, session_is_current=lambda: True)      # returns; the permission check explains
        self.assertEqual(clock.sleeps, [])

    def test_a_cancelled_session_stops_the_wait(self):
        clock = FakeClock(); ns = build(clock)
        h = host(ns, ["NO_WINDOW"])
        with self.assertRaises(RuntimeError) as ctx:
            h.wait(was_running=False, session_is_current=lambda: False)
        self.assertIn("cancelled", str(ctx.exception))

    def test_the_default_timeout_covers_a_slow_cold_start(self):
        self.assertGreaterEqual(build(FakeClock())["KARAFUN_READY_TIMEOUT_S"], 45.0)


class WiringTests(unittest.TestCase):
    def test_worker_checks_before_opening_and_waits_before_any_search(self):
        i = SOURCE.index("karafun_was_running = self._karafun_process_running()")
        block = SOURCE[i:i + 1400]
        order = [block.index(x) for x in ("self._karafun_process_running()", "self._open_karafun_for_entry(entry)",
                                           "self._karafun_wait_until_ready(", "self._karafun_apple_events_preflight()")]
        self.assertEqual(order, sorted(order))
        self.assertIn("was_running=karafun_was_running", block)

    def test_a_cold_start_gets_a_longer_playback_check(self):
        self.assertIn("max_probe_attempts = 12 if karafun_was_running else 30", SOURCE)
        self.assertIn("for probe_attempt in range(max_probe_attempts):", SOURCE)

    @unittest.skipUnless(sys.platform == "darwin", "osacompile is macOS only")
    def test_the_readiness_script_compiles(self):
        captured = {}
        ns = build(FakeClock())
        h = SimpleNamespace(_run_karafun_applescript_sync=lambda lines, timeout=None: (captured.setdefault("src", "\n".join(lines)) and True, "READY", ""))
        self.assertEqual(ns["_karafun_main_window_state"](h), "READY")
        result = subprocess.run(["osacompile", "-o", "/dev/null"], input=captured["src"], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)


class WindowWakeTests(unittest.TestCase):
    """2026-10-03: the first search after KaraFun was launched hidden failed twice ("Can't get splitter group 1 of window Discover")
    and cost ~5 s in retries. The window is now woken once, with fast polls, before the search."""

    def test_a_ready_karafun_is_woken_once_before_the_search(self):
        ns = build(FakeClock())
        h = host(ns, ["READY"])
        ns["_karafun_wait_until_ready"](h, was_running=True, session_is_current=lambda: True, timeout=5)
        self.assertEqual(h.wake_calls, [1])

    def test_the_wake_script_waits_for_the_window_to_fill_and_never_for_long(self):
        src = Path("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index("KARAFUN_WAKE_SCRIPT = [")
        body = src[i:src.index("]", i)]
        self.assertIn("'set frontmost to true'", body)
        self.assertIn("'repeat 30 times'", body)                       # 30 x ~0.5 s: a ceiling, not a delay
        self.assertIn("'set e to entire contents of w'", body)
        self.assertIn("KARAFUN_WINDOW_MIN_ELEMENTS", body)
        self.assertIn("time.sleep(0.4)", src)                          # and the same-query retry no longer pauses 1.5 s
        self.assertNotIn("KaraFun UI not ready; retrying same query\")\n                            time.sleep(1.5)", src)


if __name__ == "__main__":
    unittest.main()
