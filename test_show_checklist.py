"""tools/show_checklist.py: each check says PASS / FAIL / NOT EXERCISED / MANUAL from synthetic log lines."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("show_checklist", ROOT / "tools" / "show_checklist.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def L(msg, level="INFO", t="21:00:00"):
    return f"[{t}] [{level}] {msg}"


class Checks(unittest.TestCase):
    def test_clean_run(self):
        self.assertEqual(sc.check_clean_run([L("hello")])[0], sc.PASS)
        self.assertEqual(sc.check_clean_run([L("boom", "ERROR")])[0], sc.FAIL)
        self.assertEqual(sc.check_clean_run([L("Traceback (most recent call last):")])[0], sc.FAIL)
        self.assertEqual(sc.check_clean_run([L("FREEZE_DETECTED x")])[0], sc.FAIL)

    def test_audio_end_rule(self):
        self.assertEqual(sc.check_audio_end_rule([L("[END-AUDIO] last audible audio at 190.00s (x)")])[0], sc.NOTEX)
        self.assertEqual(sc.check_audio_end_rule([L("[END-AUDIO] audio runs to the end of the file; end verified at 200.00s source=scan file=x.mp3")])[0], sc.PASS)
        self.assertEqual(sc.check_audio_end_rule([L("[END-AUDIO] no trailing silence found; full duration will play source=scan")])[0], sc.MANUAL)
        self.assertEqual(sc.check_audio_end_rule([L("[END-AUDIO] scan failed; retaining normal end-of-file file=x")])[0], sc.FAIL)

    def test_prestart(self):
        self.assertEqual(sc.check_prestart([])[0], sc.NOTEX)
        ok = [L("[CROSSFADE] BG pre-start fired (5.0s remaining)"), L("[CROSSFADE] BG pre-start fired (4.6s remaining)")]
        self.assertEqual(sc.check_prestart(ok)[0], sc.PASS)
        old = [L("[CROSSFADE] BG pre-start fired (3.0s remaining)")]
        self.assertEqual(sc.check_prestart(old)[0], sc.MANUAL)
        self.assertEqual(sc.check_prestart(ok + [L("[CROSSFADE] BG pre-start failed: x")])[0], sc.FAIL)

    def test_verified_fade_and_floor(self):
        self.assertEqual(sc.check_verified_fade([])[0], sc.NOTEX)
        self.assertEqual(sc.check_verified_fade([L("[CROSSFADE] BG fade started at verified audio end; x")])[0], sc.PASS)
        self.assertEqual(sc.check_verified_fade([L("[CROSSFADE] verified-audio-tail BG fade failed: x")])[0], sc.FAIL)
        self.assertEqual(sc.check_fade_floor([])[0], sc.NOTEX)
        self.assertEqual(sc.check_fade_floor([L("[BG-ENGINE] fade-in start floor -40 dB (bgm_fade_in_floor_db)")])[0], sc.PASS)
        self.assertEqual(sc.check_fade_floor([L("[BG-ENGINE] fade-in start floor -60 dB (bgm_fade_in_floor_db)")])[0], sc.MANUAL)

    def test_voice_guard_and_seek_guard(self):
        self.assertEqual(sc.check_voice_guard([])[0], sc.NOTEX)
        self.assertEqual(sc.check_voice_guard([L("[KARAOKE-RUST] voice kept its place (x): song 1s")])[0], sc.MANUAL)
        self.assertEqual(sc.check_seek_guard([])[0], sc.NOTEX)
        self.assertEqual(sc.check_seek_guard([L("[CDG-SEEK-GUARD] seek asked 115.00s -> moved back to 100.00s (graphics-free stretch 100.0s to 130.0s)")])[0], sc.MANUAL)

    def test_drives(self):
        line = L("[TRANSPORT-DRIVE] decision=media_end python=1 rust=1 selected=1 owner=rust disagreement=0")
        self.assertEqual(sc.check_drives([line])[0], sc.PASS)
        self.assertEqual(sc.check_drives([])[0], sc.NOTEX)
        bad = L("[TRANSPORT-DRIVE] decision=media_end python=1 rust=0 selected=1 owner=python disagreement=1")
        self.assertEqual(sc.check_drives([bad])[0], sc.FAIL)
        self.assertEqual(sc.check_watchdog_drive([line])[0], sc.NOTEX)

    def test_memory(self):
        def mem(n, rss, phase="start"):
            return L(f"[MEMORY-SONG] phase={phase} n={n} rss_mb={rss} d_rss=+1 py_blocks_k=1 d_blocks_k=0 widgets=1 d_widgets=0 timers=1 threads=1 fds=1 singer='A'")
        self.assertEqual(sc.check_memory([mem(i, 900 + i) for i in range(6)])[0], sc.PASS)
        self.assertEqual(sc.check_memory([mem(i, 5000) for i in range(6)])[0], sc.MANUAL)
        self.assertEqual(sc.check_memory([])[0], sc.NOTEX)


class PythonFreeRoad(unittest.TestCase):
    def doc(self, **status):
        names = {"native process": "pass", "Accessibility (shell)": "pass", "KaraFun UI (shell)": "pass", "host spike (selfcheck)": "pass",
                 "song through Rust picture + voice": "pass", "audience display (TV)": "pass"}
        names.update(status)
        return {"kind": "python_free_readiness", "facts": {"machine_arch": "x86_64"},
                "checks": [{"name": n, "status": s, "detail": "d"} for n, s in names.items()] + [{"name": "machine", "status": "info", "detail": "i"}]}

    def verdict(self, doc):
        return [r for r in sc.check_readiness([doc]) if r[0] == "Python-free road verdict"][0]

    def test_no_readiness_file_is_not_exercised(self):
        rows = sc.check_readiness([])
        self.assertEqual(rows[0][1], sc.NOTEX)

    def test_everything_passing_on_intel_is_ready(self):
        self.assertEqual(self.verdict(self.doc())[1], sc.PASS)

    def test_a_failed_required_check_fails_the_verdict(self):
        v = self.verdict(self.doc(**{"audience display (TV)": "fail"}))
        self.assertEqual(v[1], sc.FAIL)
        self.assertIn("audience display (TV)", v[2])

    def test_a_warning_on_a_required_check_is_not_yet(self):
        v = self.verdict(self.doc(**{"KaraFun UI (shell)": "warn"}))
        self.assertEqual(v[1], sc.NOTEX)
        self.assertIn("KaraFun UI (shell)", v[2])

    def test_passing_on_apple_silicon_leaves_intel_unproven(self):
        d = self.doc()
        d["facts"]["machine_arch"] = "arm64"
        v = self.verdict(d)
        self.assertEqual(v[1], sc.NOTEX)
        self.assertIn("Intel", v[2])

    def test_info_rows_are_not_listed_and_each_check_keeps_its_status(self):
        rows = sc.check_readiness([self.doc(**{"KaraFun UI (shell)": "warn"})])
        names = [r[0] for r in rows]
        self.assertNotIn("py-free: machine", names)
        self.assertIn(("py-free: KaraFun UI (shell)", sc.NOTEX, "d"), rows)

    def test_find_readiness_reads_a_folder_and_a_zip(self):
        import tempfile, zipfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "readiness").mkdir()
            (Path(d) / "readiness" / "readiness.json").write_text(json.dumps(self.doc()))
            self.assertEqual(len(sc.find_readiness([d])), 1)
            z = Path(d) / "show.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("singws-show-x/readiness/readiness.json", json.dumps(self.doc()))
                zf.writestr("singws-show-x/other.json", json.dumps({"kind": "something else"}))
            self.assertEqual(len(sc.find_readiness([str(z)])), 1)
            self.assertEqual(sc.find_readiness(["/nonexistent"]), [])


class ChooseGroup(unittest.TestCase):
    def test_the_newest_real_show_wins_over_a_bigger_older_one(self):
        info = [("singws_2026-11-06.log", object(), 54), ("singws_2026-11-07.log", object(), 52)]
        chosen, others = sc.choose_group(info)
        self.assertEqual(chosen[0], "singws_2026-11-07.log")
        self.assertEqual(others, ["singws_2026-11-06.log (54 songs)"])

    def test_a_short_session_after_a_show_does_not_win(self):
        info = [("singws_2026-11-06.log", object(), 50), ("singws_2026-11-07.log", object(), 3)]
        self.assertEqual(sc.choose_group(info)[0][0], "singws_2026-11-06.log")

    def test_an_explicit_group_is_honoured_and_a_missing_one_is_none(self):
        info = [("a.log", object(), 5), ("b.log", object(), 50)]
        self.assertEqual(sc.choose_group(info, "a.log")[0][0], "a.log")
        self.assertIsNone(sc.choose_group(info, "zzz")[0])


if __name__ == "__main__":
    unittest.main()
