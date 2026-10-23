import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools import clean_test_noise_from_logs as cn

REAL = 1790974569756


def L(tag, body, t="10:00:00"):
    return f"[{t}] [INFO] [{tag}] {body}\n"


class ClassifierTests(unittest.TestCase):
    def test_only_lines_whose_every_id_is_a_small_test_id_are_noise(self):
        self.assertTrue(cn.is_test_noise(L("QUEUE-INSERT", "singer='Ada' request_id=88 source=server")))
        self.assertTrue(cn.is_test_noise(L("QUEUE-DEDUP", "request_id=123 kept_request_id=123 reason=test")))
        self.assertFalse(cn.is_test_noise(L("QUEUE-INSERT", f"request_id={REAL} source=server")))
        self.assertFalse(cn.is_test_noise(L("QUEUE-DEDUP", f"request_id=5 kept_request_id={REAL}")), "mixed ids are kept")
        self.assertFalse(cn.is_test_noise(L("QUEUE-INSERT", "request_id= source=host")), "empty id is a host-local insert")
        self.assertFalse(cn.is_test_noise(L("QUEUE-INSERT", "request_id=0 source=host")), "id 0 is not a request id")
        self.assertFalse(cn.is_test_noise(L("ROTATION-SLOT", "preserved empty singer slot singer='Ada'")), "name-only lines are kept")
        self.assertFalse(cn.is_test_noise("Traceback (most recent call last):\n"))
        self.assertFalse(cn.is_test_noise(L("X", "subrequest_id=7")), "word boundary: only request_id/kept_request_id count")


class CleanupTests(unittest.TestCase):
    def make(self, td):
        logs = Path(td) / "logs"
        logs.mkdir()
        keep1 = L("REMOTE-TOMBSTONE", f"created request_id={REAL} singer='Real' title='T'")
        noise = L("REQUEST-DIAG", "request_id=123 singer='Ada'")
        name_only = L("ROTATION-SLOT", "filled preserved singer slot singer='Ada' reason=x")
        a = logs / "singws_2026-10-01.log"
        a.write_text(keep1 + noise + name_only + noise, encoding="utf-8")
        b = logs / "singws_2026-10-02.log"
        b.write_text(keep1 + name_only, encoding="utf-8")  # nothing to remove: must not be touched
        os.utime(a, (1_700_000_000, 1_700_000_000))
        return logs, a, b, keep1, name_only

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            logs, a, b, *_ = self.make(td)
            before = a.read_text()
            self.assertEqual(cn.main(["--logs", str(logs)]), 0)
            self.assertEqual(a.read_text(), before)
            self.assertFalse((Path(td) / "log-archive").exists())

    def test_apply_archives_then_removes_only_test_noise_and_preserves_mtime(self):
        with tempfile.TemporaryDirectory() as td:
            logs, a, b, keep1, name_only = self.make(td)
            b_stat = b.stat().st_mtime_ns
            self.assertEqual(cn.main(["--logs", str(logs), "--apply"]), 0)
            self.assertEqual(a.read_text(), keep1 + name_only)
            self.assertEqual(int(a.stat().st_mtime), 1_700_000_000)
            self.assertEqual(b.stat().st_mtime_ns, b_stat)
            archives = list((Path(td) / "log-archive").glob("*.tar.gz"))
            self.assertEqual(len(archives), 1)
            with tarfile.open(archives[0]) as tar:
                self.assertEqual([m.name for m in tar.getmembers()], ["logs/singws_2026-10-01.log"])
                restored = tar.extractfile("logs/singws_2026-10-01.log").read().decode()
            self.assertEqual(restored.count("request_id=123"), 2, "the archive keeps the original, noise included")
            self.assertEqual(list(logs.glob("*.tmp")), [])

    def test_second_apply_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            logs, a, *_ = self.make(td)
            cn.main(["--logs", str(logs), "--apply"])
            after = a.read_text()
            self.assertEqual(cn.main(["--logs", str(logs), "--apply"]), 0)
            self.assertEqual(a.read_text(), after)
            self.assertEqual(len(list((Path(td) / "log-archive").glob("*.tar.gz"))), 1)


if __name__ == "__main__":
    unittest.main()
