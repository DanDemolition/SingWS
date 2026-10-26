import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import show_report as sh  # noqa: E402

SETTINGS = {"transport_shadow": True, "queue_shadow": True, "plan_shadow": True, "settings_shadow": True, "store_shadow": True, "wire_shadow": True, "shell_commands": "dry_run"}


def launch(t, version):
    return f'[{t}] [INFO] [LAUNCH] {{"build_id":"{version}","executable":"/Applications/SingWS.app/Contents/MacOS/SingWS","startup_utc":"2026-10-26T04:07:06.986+00:00","version":"{version}"}}'


def song(t, sid, source, end="media_end", agree=True):
    """A finished song as the app logs it (only the lines the report reads)."""
    lines = [f"[{t}] [INFO] [TRANSPORT-SHADOW] event session={sid} source={source} kind=playing",
             f"[{t}] [INFO] [TRANSPORT-SHADOW] session {sid} summary events=10 total_mismatches={0 if agree else 1}"]
    if source == "karafun":
        lines.insert(1, f"[{t}] [INFO] [KARAFUN] completion monitor started title='Song {sid}' fallback_duration=145 clock_age=1.1s")
        lines.insert(1, f"[{t}] [INFO] [KARAFUN] duration corrected from 240s to 145s (was_estimated=0) title='Song {sid}'")
        lines.insert(3, f"[{t}] [INFO] [KARAFUN] monitor state idle=1 playing=0 remaining=None idle_stop_count=1")
        lines.insert(4, f"[{t}] [INFO] [TRANSPORT-DRIVE] decision=karafun_end python=1 rust={1 if agree else 0} selected=1 owner={'rust' if agree else 'python-fallback'} disagreement={0 if agree else 1}")
        lines.insert(5, f"[{t}] [INFO] [KARAFUN] completion event received reason=karaFun_idle_expected_end remaining=-2 idle=1")
    else:
        lines.insert(1, f"[{t}] [INFO] [TRANSPORT-DRIVE] decision=media_end python=1 rust=1 selected=1 owner=rust disagreement=0")
    return lines


def mem_song(t, n, rss, d, singer="Athena"):
    return f"[{t}] [INFO] [MEMORY-SONG] phase=end n={n} rss_mb={rss} d_rss={d:+d} py_blocks_k=6005 d_blocks_k=+0 widgets=449 d_widgets=+0 timers=30 threads=6 fds=59 singer='{singer}'"


def native(t, label, total, **cats):
    return f"[{t}] [INFO] [MEMORY-NATIVE] label={label} footprint_mb={total} top=" + " | ".join(f"{k.replace('_', ' ')} {v}" for k, v in cats.items())


def write(path, lines):
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sh.main(list(args))
    return code, out.getvalue()


class ShowReportTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="show_report_test_"))
        (self.d / "settings.json").write_text(json.dumps(SETTINGS))
        # the show: launched 21:07 on the old build, relaunched 21:12 (1.0.1.6), midnight rotation in the middle
        pre = [launch("21:07:09", "1.0.1.4"), launch("21:12:00", "1.0.1.6")]
        pre += song("21:30:00", 1, "native:cdg") + song("22:00:00", 2, "karafun", agree=False) + song("23:00:00", 3, "native:mp4")
        pre += [mem_song("21:40:00", 1, 1000, +300), mem_song("22:40:00", 2, 1400, +400), native("21:12:30", "launch", 900, Malloc_Large=100, IOSurface=50)]
        post = song("00:30:00", 4, "karafun") + song("01:00:00", 5, "native:cdg")
        post += [mem_song("00:40:00", 3, 1500, +100), mem_song("01:10:00", 4, 1510, +10), native("01:20:00", "song_end_4", 1500, Malloc_Large=700, IOSurface=40)]
        write(self.d / "singws_2026-10-25.log.2026-10-25", pre)
        write(self.d / "singws_2026-10-25.log", post)
        # the short check session after the show (no songs)
        write(self.d / "singws_2026-10-26.log", [launch("01:50:06", "1.0.1.6")])

    def test_the_pre_midnight_file_is_joined_and_the_show_group_is_chosen(self):
        code, text = run(str(self.d), "--settings", str(self.d / "settings.json"))
        self.assertIn("singws_2026-10-25.log: 2 file(s)", text)
        self.assertIn("ANALYSING singws_2026-10-25.log  (build 1.0.1.6", text)
        self.assertIn("5 songs", text)  # 3 before midnight + 2 after: nothing lost at the rotation
        self.assertIn("2 native:cdg", text)
        self.assertIn("1 native:mp4", text)
        self.assertNotIn("ANALYSING singws_2026-10-26.log", text)

    def test_the_last_launch_only_is_read_so_the_old_build_before_it_is_ignored(self):
        _, text = run(str(self.d), "--settings", str(self.d / "settings.json"))
        self.assertIn("build 1.0.1.6", text)
        self.assertNotIn("ANALYSING singws_2026-10-25.log  (build 1.0.1.4", text)

    def test_drives_and_the_karafun_disagreement_are_explained(self):
        code, text = run(str(self.d), "--settings", str(self.d / "settings.json"))
        self.assertEqual(code, 1, "a disagreement is a non-zero exit")
        self.assertIn("DRIVE karafun_end: 2 decisions, 1 disagreement(s)", text)
        self.assertIn("DRIVE media_end: 3 decisions, 0 disagreement(s)  <- Rust agreed every time", text)
        self.assertIn("KARAFUN-END DISAGREEMENT at 22:00:00", text)
        self.assertIn("completion event received reason=karaFun_idle_expected_end remaining=-2", text)
        self.assertIn("duration corrected from 240s to 145s", text)
        self.assertIn("fallback_duration=145", text)
        self.assertEqual(text.count("KARAFUN-END DISAGREEMENT"), 1, "the agreeing KaraFun decision at 00:30 is not a disagreement")
        self.assertNotIn("DISAGREEMENT at 00:30:00", text)

    def test_memory_trail_and_native_growth_by_category(self):
        _, text = run(str(self.d), "--settings", str(self.d / "settings.json"))
        self.assertIn("MEMORY (RSS at each song end, 4 songs): first 1000 MB at 21:40:00, last 1510 MB at 01:10:00, peak 1510 MB, growth +510 MB", text)
        self.assertIn("+400 MB at 22:40:00 (Athena)", text)
        self.assertIn("NATIVE MEMORY (footprint categories, 2 probes): 900 MB at 21:12:30 -> 1500 MB at 01:20:00", text)
        self.assertIn("Malloc Large 100->700 (+600)", text)
        self.assertIn("IOSurface 50->40 (-10)", text)
        self.assertIn("+600 MB at 01:20:00 (song_end_4)", text)

    def test_a_zip_with_macos_resource_files_works_like_the_folder(self):
        z = self.d / "singws-show-20261026-0219.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for p in self.d.glob("singws_*"):
                zf.write(p, f"singws-show/{p.name}")
                zf.writestr(f"singws-show/._{p.name}", b"resource fork")
            zf.write(self.d / "settings.json", "singws-show/settings.json")
        _, text = run(str(z))
        self.assertIn("5 songs", text)
        self.assertIn("singws_2026-10-25.log: 2 file(s)", text)

    def test_choosing_a_group_by_name_and_no_logs(self):
        _, text = run(str(self.d), "--settings", str(self.d / "settings.json"), "--group", "singws_2026-10-26.log")
        self.assertIn("ANALYSING singws_2026-10-26.log", text)
        empty = Path(tempfile.mkdtemp())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(sh.main([str(empty)]), 2)

    def test_an_old_build_without_the_native_probe_says_so(self):
        _, text = run(str(self.d), "--settings", str(self.d / "settings.json"), "--group", "singws_2026-10-26.log")
        self.assertIn("no [MEMORY-NATIVE] lines", text)


if __name__ == "__main__":
    unittest.main()
