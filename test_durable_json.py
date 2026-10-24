import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import errno
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from test_settings_shadow import app_function, app_method

_SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class Harness:
    """Runs the app's REAL `_fsync_durable`, `_fsync_directory` and `_save_json_atomic` with instrumented os/fcntl."""

    def __init__(self, td, durable=True, file_error=None, dir_error=None, dir_open_error=False):
        self.events = []
        self.td = Path(td)
        events = self.events
        real_os = os
        harness = self

        class OsProxy:
            def __getattr__(self, name):
                return getattr(real_os, name)

            def replace(self, a, b):
                events.append("replace")
                return real_os.replace(a, b)

            def fsync(self, fd):
                events.append("plain_fsync(" + harness.kind(fd) + ")")
                return real_os.fsync(fd)

            def open(self, path, flags, *a):
                if dir_open_error:
                    raise OSError(errno.EACCES, "denied")
                return real_os.open(path, flags, *a)

        class FcntlProxy:
            F_FULLFSYNC = fcntl.F_FULLFSYNC

            def fcntl(self, fd, op, *a):
                kind = harness.kind(fd)
                events.append(f"full_fsync({kind}, size={real_os.fstat(fd).st_size if kind == 'file' else '-'})")
                err = file_error if kind == "file" else dir_error
                if err is not None:
                    raise OSError(err, os.strerror(err))
                return fcntl.fcntl(fd, op, *a)

        self.ns = {
            "json": json, "os": OsProxy(), "tempfile": tempfile, "hashlib": hashlib, "time": time, "Path": Path,
            "_fcntl": FcntlProxy(), "_errno": errno, "_DURABLE_JSON_WRITES": durable,
            "SETTINGS_PATH": self.td / "settings.json", "TRACKS_PATH": self.td / "tracks.json",
            "_JSON_WRITE_DIGESTS": {}, "_perf_record": lambda *a, **k: None,
            "_settings_shadow_for": lambda value: None, "_store_shadow_submit": lambda *a, **k: None,
        }
        app_function("_fsync_durable", self.ns)
        app_function("_fsync_directory", self.ns)
        self.atomic_write = app_function("_atomic_write_text", self.ns)
        self.save = app_function("_save_json_atomic", self.ns)

    @staticmethod
    def kind(fd):
        return "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"


class OrderTests(unittest.TestCase):
    def test_flush_file_then_rename_then_flush_directory(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td)
            h.save(h.td / "queue.json", [{"a": 1}])
            payload_len = len(json.dumps([{"a": 1}], indent=2) + "\n")
            self.assertEqual(h.events, [f"full_fsync(file, size={payload_len})", "replace", "full_fsync(dir, size=-)"])
            self.assertEqual(json.loads((h.td / "queue.json").read_text()), [{"a": 1}])

    def test_every_store_gets_the_same_guarantee_including_tracks(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td)
            h.save(h.ns["TRACKS_PATH"], [{"t": "é"}])
            h.save(h.ns["SETTINGS_PATH"], {"k": 1})
            self.assertEqual([e.split("(")[0] for e in h.events], ["full_fsync", "replace", "full_fsync"] * 2)

    def test_an_unchanged_payload_does_no_io_at_all(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td)
            h.save(h.td / "queue.json", {"a": 1})
            h.events.clear()
            mtime = (h.td / "queue.json").stat().st_mtime_ns
            h.save(h.td / "queue.json", {"a": 1})
            self.assertEqual(h.events, [])
            self.assertEqual((h.td / "queue.json").stat().st_mtime_ns, mtime)

    def test_a_deleted_file_is_rewritten_with_full_durability(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td)
            h.save(h.td / "queue.json", {"a": 1})
            (h.td / "queue.json").unlink()
            h.events.clear()
            h.save(h.td / "queue.json", {"a": 1})
            self.assertEqual(len(h.events), 3)


class FailureTests(unittest.TestCase):
    def test_a_filesystem_without_full_fsync_falls_back_to_plain_fsync(self):
        for err in (errno.ENOTSUP, errno.EINVAL, errno.ENOTTY):
            with tempfile.TemporaryDirectory() as td:
                h = Harness(td, file_error=err, dir_error=err)
                h.save(h.td / "queue.json", {"a": 1})
                self.assertIn("plain_fsync(file)", h.events, errno.errorcode[err])
                self.assertTrue(h.events.index("plain_fsync(file)") < h.events.index("replace"))
                self.assertEqual(json.loads((h.td / "queue.json").read_text()), {"a": 1})

    def test_a_real_io_error_on_the_data_flush_fails_the_save_and_protects_the_old_file(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td)
            path = h.td / "queue.json"
            h.save(path, {"version": "old"})
            h.ns["os"]  # keep the proxy alive
            bad = Harness(td, file_error=errno.EIO)
            bad.ns["_JSON_WRITE_DIGESTS"][str(path)] = "stale-digest"
            with self.assertRaises(OSError) as ctx:
                bad.save(path, {"version": "new"})
            self.assertEqual(ctx.exception.errno, errno.EIO)
            self.assertEqual(json.loads(path.read_text()), {"version": "old"}, "the previous file is untouched")
            self.assertNotIn("replace", bad.events, "the rename never happens after a failed flush")
            self.assertEqual([p.name for p in Path(td).iterdir()], ["queue.json"], "temp file removed")
            self.assertNotIn(str(path), bad.ns["_JSON_WRITE_DIGESTS"], "a failed save is retried next time, not skipped")

    def test_a_directory_flush_failure_does_not_fail_a_save_whose_data_is_safe(self):
        for kwargs in ({"dir_error": errno.EIO}, {"dir_open_error": True}):
            with tempfile.TemporaryDirectory() as td:
                h = Harness(td, **kwargs)
                h.save(h.td / "queue.json", {"a": 1})
                self.assertEqual(json.loads((h.td / "queue.json").read_text()), {"a": 1}, kwargs)

    def test_the_rollback_switch_turns_every_flush_off(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td, durable=False)
            h.save(h.td / "queue.json", {"a": 1})
            self.assertEqual(h.events, ["replace"])


class StoreSaveTests(unittest.TestCase):
    """The tombstone store and the live BGM playlist now use the same durable writer, with their original byte layouts."""

    def setup(self, td, **kw):
        h = Harness(td, **kw)
        h.ns.update({"REMOTE_REQUEST_TOMBSTONES_PATH": h.td / "remote_request_tombstones.json", "_diag": lambda *a, **k: None,
                     "_atomic_write_text": h.atomic_write})
        return h

    def test_tombstone_store_is_written_atomically_with_the_same_layout_as_before(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            h = self.setup(td)
            save = app_method("_save_remote_request_tombstones", h.ns)
            data = {"requests": {"2": {"title": "é"}, "1": {"title": "B"}}, "version": 1}
            save(SimpleNamespace(), data)
            on_disk = h.ns["REMOTE_REQUEST_TOMBSTONES_PATH"].read_text(encoding="utf-8")
            self.assertEqual(on_disk, json.dumps(data, indent=2, sort_keys=True), "byte-identical to the old json.dump layout")
            self.assertFalse(on_disk.endswith("\n"), "the old layout had no trailing newline")
            self.assertEqual(h.events[0].split(",")[0], f"full_fsync(file")
            self.assertEqual([e.split("(")[0] for e in h.events], ["full_fsync", "replace", "full_fsync"])

    def test_a_failed_tombstone_save_keeps_the_previous_tombstones(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            good = self.setup(td)
            save = app_method("_save_remote_request_tombstones", good.ns)
            save(SimpleNamespace(), {"requests": {"7": {"title": "keep me"}}, "version": 1})
            before = good.ns["REMOTE_REQUEST_TOMBSTONES_PATH"].read_text(encoding="utf-8")
            bad = self.setup(td, file_error=errno.EIO)
            bad_save = app_method("_save_remote_request_tombstones", bad.ns)
            bad_save(SimpleNamespace(), {"requests": {}, "version": 1})  # logs "save failed", must not raise or damage the file
            self.assertEqual(bad.ns["REMOTE_REQUEST_TOMBSTONES_PATH"].read_text(encoding="utf-8"), before)
            self.assertEqual([p.name for p in Path(td).iterdir()], ["remote_request_tombstones.json"])

    def test_live_playlist_is_written_atomically_with_the_same_layout_as_before(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as td:
            h = self.setup(td)
            save = app_method("save_current_playlist", h.ns)
            playlist = ["/music/a.mp3", "/music/é.mp3"]
            save(SimpleNamespace(playlist_path=h.td / "bg_playlist.json", current_playlist=playlist))
            self.assertEqual((h.td / "bg_playlist.json").read_text(encoding="utf-8"), json.dumps(playlist, indent=2))
            self.assertEqual([e.split("(")[0] for e in h.events], ["full_fsync", "replace", "full_fsync"])

    def test_shared_helper_failure_modes(self):
        with tempfile.TemporaryDirectory() as td:
            h = Harness(td)
            path = h.td / "sub" / "x.json"
            h.atomic_write(path, "ONE")
            self.assertEqual(path.read_text(), "ONE")
            bad = Harness(td, file_error=errno.EIO)
            with self.assertRaises(OSError):
                bad.atomic_write(path, "TWO")
            self.assertEqual(path.read_text(), "ONE")
            self.assertEqual([p.name for p in path.parent.iterdir()], ["x.json"])


class RealRunTests(unittest.TestCase):
    def test_without_instrumentation_the_real_writer_still_writes_the_same_file(self):
        with tempfile.TemporaryDirectory() as td:
            ns = {
                "json": json, "os": os, "tempfile": tempfile, "hashlib": hashlib, "time": time, "Path": Path, "_fcntl": fcntl,
                "_errno": errno, "_DURABLE_JSON_WRITES": True, "SETTINGS_PATH": Path(td) / "settings.json",
                "TRACKS_PATH": Path(td) / "tracks.json", "_JSON_WRITE_DIGESTS": {}, "_perf_record": lambda *a, **k: None,
                "_settings_shadow_for": lambda v: None, "_store_shadow_submit": lambda *a, **k: None,
            }
            app_function("_fsync_durable", ns)
            app_function("_fsync_directory", ns)
            app_function("_atomic_write_text", ns)
            save = app_function("_save_json_atomic", ns)
            data = {"b": [1, 2.5, "é"], "a": None}
            path = Path(td) / "nested" / "dir" / "queue.json"
            save(path, data)
            self.assertEqual(path.read_text(encoding="utf-8"), json.dumps(data, indent=2) + "\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual([p.name for p in path.parent.iterdir()], ["queue.json"])

    def test_the_cost_of_durability_stays_small_for_a_realistic_store(self):
        with tempfile.TemporaryDirectory() as td:
            ns = {
                "json": json, "os": os, "tempfile": tempfile, "hashlib": hashlib, "time": time, "Path": Path, "_fcntl": fcntl,
                "_errno": errno, "_DURABLE_JSON_WRITES": True, "SETTINGS_PATH": Path(td) / "settings.json",
                "TRACKS_PATH": Path(td) / "tracks.json", "_JSON_WRITE_DIGESTS": {}, "_perf_record": lambda *a, **k: None,
                "_settings_shadow_for": lambda v: None, "_store_shadow_submit": lambda *a, **k: None,
            }
            app_function("_fsync_durable", ns)
            app_function("_fsync_directory", ns)
            app_function("_atomic_write_text", ns)
            save = app_function("_save_json_atomic", ns)
            data = {f"k{i}": "x" * 40 for i in range(250)}  # ~12 KB, the size of settings.json
            times = []
            for i in range(10):
                data["n"] = i
                t = time.perf_counter()
                save(Path(td) / "settings.json", data)
                times.append((time.perf_counter() - t) * 1000)
            # Measured ~10 ms median on the dev Mac. The bound is loose on purpose: it catches an accidental order-of-magnitude
            # regression (e.g. a flush in a loop), not machine speed.
            self.assertLess(sorted(times)[len(times) // 2], 250.0, times)


class WiringTests(unittest.TestCase):
    def test_source_has_the_order_the_tests_assume(self):
        helper = _SRC[_SRC.index("def _atomic_write_text"):][:1400]
        write = helper.index("f.write(payload)")
        flush = helper.index("_fsync_durable(f.fileno())")
        replace = helper.index("os.replace(tmp_name, path)")
        dirsync = helper.index("_fsync_directory(path.parent)")
        self.assertLess(write, flush)
        self.assertLess(flush, replace)
        self.assertLess(replace, dirsync)
        body = _SRC[_SRC.index("def _save_json_atomic"):][:2600]
        self.assertLess(body.index("_atomic_write_text(path, payload)"), body.index("_JSON_WRITE_DIGESTS[key] = digest"),
                        "the digest is recorded only after the save is durable")

    def test_no_store_is_written_with_a_plain_overwrite_any_more(self):
        self.assertNotIn('open(REMOTE_REQUEST_TOMBSTONES_PATH, "w"', _SRC)
        self.assertNotIn("with open(self.playlist_path, 'w')", _SRC)
        self.assertIn("_atomic_write_text(REMOTE_REQUEST_TOMBSTONES_PATH,", _SRC)
        self.assertIn("_atomic_write_text(Path(self.playlist_path),", _SRC)

    def test_rollback_switch_and_default(self):
        self.assertIn('os.environ.get("SINGWS_DURABLE_JSON", "1").strip() != "0"', _SRC)

    def test_full_fsync_is_the_primary_call_on_macos(self):
        self.assertIn('getattr(_fcntl, "F_FULLFSYNC", None)', _SRC)
        self.assertTrue(hasattr(fcntl, "F_FULLFSYNC") or os.uname().sysname != "Darwin")


if __name__ == "__main__":
    unittest.main()
