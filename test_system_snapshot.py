import unittest
from types import SimpleNamespace

import system_snapshot as ss


class _Proc:
    def __init__(self, pid, name, cpu, rss):
        self.pid, self.info, self._cpu, self._rss, self.calls = pid, {"name": name}, cpu, rss, 0

    def cpu_percent(self, interval=None):
        self.calls += 1
        return 0.0 if self.calls == 1 else self._cpu  # like psutil: the first call is always 0.0

    def memory_info(self):
        return SimpleNamespace(rss=self._rss * 1e6)


class FakePsutil:
    def __init__(self, procs):
        self.procs = procs

    def virtual_memory(self):
        return SimpleNamespace(available=24.1e9)

    def swap_memory(self):
        return SimpleNamespace(used=0)

    def process_iter(self, attrs=None):
        return list(self.procs)


class SnapshotTests(unittest.TestCase):
    def test_names_the_busiest_and_biggest_processes(self):
        import os
        procs = [_Proc(1, "Google Chrome", 85.0, 900), _Proc(2, "kernel_task", 5.0, 100),
                 _Proc(os.getpid(), "SingWS", 2.0, 2000), _Proc(4, "Slack", 30.0, 500)]
        out = ss.snapshot(FakePsutil(procs), sleep=lambda s: None, pressure=lambda: "warn")
        self.assertIn("mem_pressure=warn", out)
        self.assertIn("ram_free=24.1GB", out)
        self.assertIn("cpu: Google Chrome 85%, Slack 30%", out)
        self.assertIn("SingWS 2000MB", out.split("| mem:")[1])
        self.assertIn("singws_cpu=2%", out)

    def test_cpu_is_sampled_after_a_pause_not_zero(self):
        # The old FREEZE_DETECTED line read 0.0% because it asked once; here every process is asked twice.
        p = _Proc(1, "X", 50.0, 10)
        out = ss.snapshot(FakePsutil([p]), sleep=lambda s: None, pressure=lambda: "normal")
        self.assertEqual(p.calls, 2)
        self.assertIn("X 50%", out)

    def test_failures_leave_fields_out_instead_of_raising(self):
        class Broken:
            def virtual_memory(self):
                raise RuntimeError("x")

            def swap_memory(self):
                raise RuntimeError("x")

            def process_iter(self, attrs=None):
                raise RuntimeError("x")
        out = ss.snapshot(Broken(), sleep=lambda s: None, pressure=lambda: "?")
        self.assertIn("mem_pressure=?", out)


if __name__ == "__main__":
    unittest.main()


class WiringGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.root = Path(__file__).resolve().parent
        cls.src = (cls.root / "0.2.18.1.py").read_text()

    def test_watchdog_logs_the_snapshot_only_for_stalls_over_a_second(self):
        self.assertIn("if gap >= 1.0 and not long_logged and _system_snapshot_enabled():", self.src)
        self.assertIn('"stall_system_snapshot": True', self.src)

    def test_misleading_cpu_line_is_gone(self):
        self.assertNotIn('logging.warning(f"  SingWS CPU:', self.src)

    def test_specs_bundle_the_module(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            text = (self.root / spec).read_text()
            self.assertIn('"system_snapshot.py"', text)
            self.assertIn("'system_snapshot'", text)
