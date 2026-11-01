"""Stage B M1: the Rust queue-state machine (rust/crates/singws-queue-state) must give IDENTICAL results and snapshots to the real Python
queue code on the in-scope corpus (tools/queue_event_replay.py). Skipped when the helper is not built:
  cd rust && CARGO_TARGET_DIR=~/.cache/singws-rust-target-replay cargo build --release -p singws-queue-state
(set SINGWS_QUEUE_STATE_BIN to point at another build)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME before the app is loaded)
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))
import queue_event_replay as qr  # noqa: E402

qr.load_app_module()
try:
    BIN = qr.find_rust_binary()
except FileNotFoundError:
    BIN = None


@unittest.skipUnless(BIN, "singws-queue-state is not built")
class QueueStateParityTests(unittest.TestCase):
    def check(self, name, stream):
        r = qr.compare(stream, qr.PythonImpl(), qr.RustImpl(BIN))
        self.assertTrue(r["equal"], f"{name}: {json.dumps(r['first_divergence'], default=str)[:800]}")

    def test_adversarial_cases_in_scope_are_identical(self):
        for name, stream in qr.core_cases().items():
            self.check(name, stream)

    def test_seeded_core_streams_are_identical(self):
        for seed in range(25):
            self.check(f"seed{seed}", qr.generate_stream(seed, 50, "core"))

    def test_extracted_log_stream_is_identical(self):
        import tempfile
        log = Path(tempfile.mkdtemp()) / "singws_2026-11-01.log"
        rows = [f"[10:00:0{i}] [INFO] [REQUEST-DIAG] request_id=17900000000{i:02d} singer='S{i % 2}' artist='A' title='T{i}' source='' session_id='' selected_version='' received_ts=1 status=received match_result='pending' queue_insert_result='pending' sync_revision=0 server_enabled=1 failure_reason=''" for i in range(6)]
        rows.append("[10:05:00] [INFO] [REMOTE-TOMBSTONE] created request_id=1790000000001 singer='S1' title='T1' artist='A' reason=host_remove_song revision=3 status=removed accepting_requests=1")
        log.write_text("\n".join(rows) + "\n")
        stream, _cov = qr.extract_stream([str(log)])
        self.check("log", stream)

    def test_waitlist_ops_are_declared_unsupported(self):
        impl = qr.RustImpl(BIN)
        impl.reset({})
        self.assertEqual(impl.apply({"op": "waitlist_add", "request_id": 5}), {"ok": False, "code": "unsupported"})
        self.assertEqual(impl.apply({"op": "nonsense"}), {"ok": False, "code": "unknown_op"})

    def test_rust_helper_is_deterministic(self):
        stream = qr.generate_stream(11, 40, "core")
        a = qr.run_stream(qr.RustImpl(BIN), stream)
        b = qr.run_stream(qr.RustImpl(BIN), stream)
        self.assertEqual(qr.canonical(a), qr.canonical(b))


if __name__ == "__main__":
    unittest.main()
