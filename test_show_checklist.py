"""tools/show_checklist.py: each check says PASS / FAIL / NOT EXERCISED / MANUAL from synthetic log lines."""
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
