"""The Rust background-music engine behind the BASS engine's Python interface (roadmap item 1, milestone 5).

Runs the REAL engine in headless mode (no sound card, nothing audible): audio is pulled with `render()` and measured. Skipped when the
Rust library has not been built (`cd rust && cargo build --release -p singws-bgm-ffi`)."""
import importlib.util
import math
import os
import time
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import rust_bgm_engine as rbe

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent
RATE = 48000

LIB_OK = rbe.available()


def tone_file(directory, name, hz, amp, seconds, rate=RATE):
    n = int(seconds * rate)
    mono = amp * np.sin(2 * np.pi * hz * np.arange(n) / rate)
    pcm = (np.repeat(mono[:, None], 2, axis=1) * 32767).astype("<i2")
    path = Path(directory) / name
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return str(path)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def freq_of(x, rate=RATE):
    s = x[:, 0]
    crossings = int(np.sum((s[:-1] < 0) & (s[1:] >= 0)))
    return crossings / (len(s) / rate)


@unittest.skipUnless(LIB_OK, "libsingws_bgm_ffi.dylib not built")
class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.addCleanup(self.e.close)

    def f(self, name, hz=440, amp=0.5, seconds=2.0):
        return tone_file(self.tmp.name, name, hz, amp, seconds)

    def test_play_reports_audio_times_state_and_meter(self):
        e = self.e
        e.load(self.f("a.wav", amp=0.4), paused=False, volume=1.0)
        self.assertTrue(e.is_playing())
        self.assertFalse(e.is_paused())
        out = e.render(RATE // 4)
        self.assertAlmostEqual(rms(out[RATE // 20:]), 0.4 / math.sqrt(2), delta=0.01)
        pos, dur = e.get_times()
        self.assertAlmostEqual(pos, 0.25, delta=0.01)
        self.assertAlmostEqual(dur, 2.0, delta=0.01)
        self.assertAlmostEqual(e.meter_level(), min(1.0, 0.4 / math.sqrt(2) * 4), delta=0.05)
        self.assertTrue(e.pause())
        self.assertTrue(e.is_paused() and not e.is_playing())
        self.assertEqual(float(np.abs(e.render(2400)).max()), 0.0)
        self.assertTrue(e.play())
        self.assertGreater(float(np.abs(e.render(2400)).max()), 0.1)

    def test_a_loaded_but_paused_track_is_silent_until_played(self):
        e = self.e
        e.load(self.f("a.wav"), paused=True, volume=1.0)
        self.assertFalse(e.is_playing())
        self.assertEqual(float(np.abs(e.render(2400)).max()), 0.0)

    def test_stop_clears_everything(self):
        e = self.e
        e.load(self.f("a.wav"), paused=False, volume=1.0)
        e.render(2400)
        e.stop()
        self.assertIsNone(e.primary)
        self.assertTrue(e.source_ended())
        self.assertEqual(e.get_times(), (0.0, 0.0))
        self.assertEqual(float(np.abs(e.render(2400)).max()), 0.0)

    def test_state_is_fresh_right_after_a_new_load_even_if_the_old_track_had_ended(self):
        e = self.e
        e.load(self.f("short.wav", seconds=0.2), paused=False, volume=1.0)
        e.render(RATE // 2)
        self.assertTrue(e.source_ended())
        e.load(self.f("long.wav", seconds=3.0), paused=False, volume=1.0)
        self.assertFalse(e.source_ended(), "stale 'ended' from the previous track must not leak into the new one")
        self.assertLess(e.get_times()[0], 0.05)

    def test_seek_moves_the_audible_position(self):
        e = self.e
        e.load(self.f("a.wav", seconds=4.0), paused=False, volume=1.0)
        e.render(4800)
        self.assertTrue(e.seek(2.5))
        e.render(480)
        pos, _ = e.get_times()
        self.assertAlmostEqual(pos, 2.5, delta=0.05)

    def test_crossfade_promotes_the_incoming_track(self):
        e = self.e
        a, b = self.f("a.wav", hz=440, seconds=3.0), self.f("b.wav", hz=1000, seconds=3.0)
        e.load(a, paused=False, volume=1.0)
        e.render(4800)
        self.assertTrue(e.preload_secondary(b, norm_gain=1.0))
        self.assertEqual(e.secondary.path, b)
        self.assertTrue(e.start_crossfade(b, 400, norm_gain=1.0, start_seconds=0.0))
        self.assertFalse(e.crossfade_finished())
        mid = e.render(int(RATE * 0.2))
        self.assertGreater(rms(mid[:, 0]), 0.2)       # both tracks audible, level steady (equal power)
        e.render(int(RATE * 0.3))
        self.assertTrue(e.crossfade_finished())
        self.assertTrue(e.complete_crossfade())
        self.assertEqual(e.primary.path, b)
        self.assertIsNone(e.secondary)
        after = e.render(RATE // 2)
        self.assertAlmostEqual(freq_of(after), 1000, delta=30)
        self.assertEqual(e.get_times()[1], 3.0)

    def test_cancelling_a_crossfade_keeps_the_outgoing_track(self):
        e = self.e
        a, b = self.f("a.wav", hz=440, seconds=3.0), self.f("b.wav", hz=1000, seconds=3.0)
        e.load(a, paused=False, volume=1.0)
        e.preload_secondary(b)
        e.start_crossfade(b, 1000)
        e.render(2400)
        e.cancel_crossfade()
        self.assertEqual(e.primary.path, a)
        self.assertIsNone(e.secondary)
        self.assertAlmostEqual(freq_of(e.render(RATE // 2)), 440, delta=25)

    def test_prepared_decks_install_and_discard(self):
        e = self.e
        a, b = self.f("a.wav", hz=440), self.f("b.wav", hz=1000)
        deck = e.prepare_primary(a, norm_gain=1.0)
        self.assertTrue(e.install_prepared_primary(deck, volume=1.0))
        self.assertEqual(e.primary.path, a)
        self.assertTrue(e.play())
        self.assertGreater(float(np.abs(e.render(2400)).max()), 0.1)
        spare = e.prepare_primary(b)
        e.discard_prepared_primary(spare)       # must not crash or leak the native deck
        self.assertIsNone(spare.ptr)
        sec = e.prepare_primary(b)
        self.assertTrue(e.install_prepared_secondary(sec))
        self.assertEqual(e.secondary.path, b)

    def test_master_volume_slide_and_clamps(self):
        e = self.e
        e.load(self.f("a.wav", amp=0.5), paused=False, volume=5.0)
        self.assertEqual(e.master_volume, 1.0)
        self.assertEqual(e._effective_master(), 1.0)
        e.set_master_volume(-3)
        self.assertEqual(e.master_volume, 0.0)
        e.set_master_volume(1.0)
        e.slide_master_volume(0.0, 100)
        e.render(RATE // 2)
        self.assertLess(float(np.abs(e.render(2400)).max()), 1e-3)

    def test_normalisation_gain_scales_the_deck_and_is_clamped(self):
        e = self.e
        e.load(self.f("a.wav", amp=0.1), paused=False, volume=1.0)
        e.render(2400)
        base = rms(e.render(4800))
        e.set_primary_normalize_gain(2.0)
        e.render(480)
        self.assertAlmostEqual(rms(e.render(4800)) / base, 2.0, delta=0.05)
        e.set_primary_normalize_gain(100.0)
        e.render(480)
        self.assertAlmostEqual(rms(e.render(4800)) / base, 4.0, delta=0.1)

    def test_graphic_eq_changes_the_sound_and_detaches(self):
        from singws_eq import GraphicEQ
        e = self.e
        e.load(self.f("k.wav", hz=1000, amp=0.05, seconds=6.0), paused=False, volume=1.0)
        e.render(RATE // 2)
        flat = rms(e.render(RATE // 2))
        eq = GraphicEQ(sample_rate=RATE, channels=2)
        eq.set_all_gains_db([0, 0, 0, 0, 0, 12, 0, 0, 0, 0])
        eq.set_enabled(True)
        e.set_eq(eq)
        e.render(RATE // 4)
        boosted = rms(e.render(RATE // 2))
        self.assertAlmostEqual(20 * math.log10(boosted / flat), 12.0, delta=0.5)
        e.set_eq(None)
        e.render(RATE // 4)
        self.assertAlmostEqual(20 * math.log10(rms(e.render(RATE // 2)) / flat), 0.0, delta=0.2)

    def test_master_processor_limiter_follows_the_processor_parameters(self):
        from singws_master_audio import MasterAudioProcessor
        e = self.e
        e.load(self.f("hot.wav", hz=220, amp=0.95, seconds=6.0), paused=False, volume=1.0)
        e.render(RATE // 2)
        self.assertGreater(float(np.abs(e.render(RATE // 4)).max()), 0.9)       # untouched with no processor
        proc = MasterAudioProcessor(sample_rate=RATE, channels=2)
        proc.set_params({"limiter_enabled": 1.0, "limiter_ceiling_db": -6.0, "comp_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0})
        proc.set_enabled(True)
        self.assertFalse(e.master_attached)
        e.set_master_processor(proc)
        self.assertTrue(e.master_attached, "the app's launch log reads this flag")
        e.render(RATE // 2)
        ceiling = 10 ** (-6.0 / 20.0)
        peak = float(np.abs(e.render(RATE // 2)).max())
        self.assertLessEqual(peak, ceiling + 0.01)
        self.assertGreater(peak, ceiling * 0.9)
        proc.set_params({"limiter_enabled": 1.0, "limiter_ceiling_db": -12.0, "comp_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0})
        e.set_master_processor(proc)            # the app re-applies after every parameter change
        e.render(RATE // 2)
        self.assertLessEqual(float(np.abs(e.render(RATE // 2)).max()), 10 ** (-12.0 / 20.0) + 0.01)
        e.set_master_processor(None)
        self.assertFalse(e.master_attached)
        e.render(RATE // 2)
        self.assertGreater(float(np.abs(e.render(RATE // 4)).max()), 0.9)

    def test_diagnostics_snapshot_has_the_shape_the_app_logs(self):
        from audio_diag import format_audio_diag
        e = self.e
        e.set_audio_diagnostics(True)
        e.load(self.f("a.wav"), paused=False, volume=1.0)
        e.render(48000)
        snap = e.audio_diagnostics_snapshot()
        self.assertIsNotNone(snap)
        self.assertIn("master", snap)
        self.assertTrue(isinstance(format_audio_diag(snap), str))
        e.set_audio_diagnostics(False)
        self.assertIsNone(e.audio_diagnostics_snapshot())

    def test_play_and_close_are_safe(self):
        e = self.e
        e.load(self.f("a.wav"), paused=True, volume=1.0)
        e.play()
        self.assertTrue(e.is_playing())
        e.close()
        e.close()
        self.assertFalse(e.play())
        self.assertEqual(e.meter_level(), 0.0)

    def test_fade_in_from_silence_follows_the_curve_measured_on_real_bass(self):
        """BASS_SLIDE_LOG starts a slide from silence at -60 dB (measured 2026-10-11) and is linear in dB. The engine once started at -80 dB,
        which pushed the audible part of a 3 s fade-in about a second later (the operator heard silence instead of an overlap)."""
        e = self.e
        e.load(self.f("c.wav", seconds=6.0), paused=True, volume=1.0)
        e.set_master_volume(0.0)
        e.play()
        e.slide_master_volume(0.75, 3000)
        blocks = []
        for _ in range(120):                      # 144,000 frames = 3 s, paced so the streaming decoder keeps up
            blocks.append(e.render(1200))
            time.sleep(0.002)
        r = [rms(b) for b in blocks]
        full = r[-1]
        db = lambda i: 20 * math.log10(max(r[i], 1e-9) / full)
        self.assertLess(db(2), -50.0)             # 0.05 s in: still near the -60 dB start
        self.assertAlmostEqual(db(59), -28.7, delta=2.0)   # half way = half the dB span (57.5 dB)
        self.assertGreater(db(100), -10.0)        # last half second is clearly audible
        self.assertGreater(db(80), -22.0)         # 2 s in: with the old -80 dB floor this was about -33 dB

    def test_a_bad_file_raises_the_unavailable_error(self):
        with self.assertRaises(rbe.RustBgmUnavailable):
            self.e.load("/nonexistent/none.mp3")


class UnavailableTests(unittest.TestCase):
    def test_a_frozen_app_never_looks_in_developer_cache_folders(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            paths = [str(p) for p in rbe._candidate_paths()]
        self.assertFalse(any(".cache" in p or "/native/singws_bgm/" in p for p in paths), paths)

    def test_a_missing_library_raises_and_does_not_poison_a_later_load(self):
        saved = rbe._LIB
        try:
            rbe._LIB = None
            with mock.patch.object(rbe, "_candidate_paths", return_value=[Path("/nonexistent/libsingws_bgm_ffi.dylib")]):
                with self.assertRaises(rbe.RustBgmUnavailable):
                    rbe.RustBackgroundEngine(headless=True)
                self.assertFalse(rbe.available())
        finally:
            rbe._LIB = saved


def _load_app():
    spec = importlib.util.spec_from_file_location("singws_main_rustbgm", _ROOT / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AppSelectionTests(unittest.TestCase):
    """`_init_bass_engine` picks the engine from the `bgm_engine` setting (default rust) and always falls back to BASS."""

    @classmethod
    def setUpClass(cls):
        os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-rustbgm-app-")
        cls.app = _load_app()

    def _player(self, setting):
        from PyQt6.QtCore import QObject
        P = self.app.BackgroundMusicPlayer
        p = P.__new__(P)
        QObject.__init__(p)
        parent = mock.Mock()
        parent.settings = {"bgm_engine": setting} if setting is not None else {}
        p.parent = lambda: parent
        p._platform_audio_label = lambda: "test"
        p._start_audio_diag_counters = lambda parent: None
        p._attach_host_audio_chain = lambda: None
        return p

    def test_default_is_rust_and_a_saved_bass_value_is_only_noted(self):
        self.assertEqual(self.app.DEFAULTS["bgm_engine"], "rust")
        for setting in (None, "rust", " Rust ", "", "nonsense"):
            self.assertFalse(self._player(setting)._legacy_bass_setting(), setting)
        for setting in ("bass", "BASS", " bass "):
            self.assertTrue(self._player(setting)._legacy_bass_setting(), setting)

    def test_rust_is_used_when_available_even_if_settings_still_say_bass(self):
        for setting in ("rust", "bass"):
            p = self._player(setting)
            fake = mock.Mock()
            fake.backend_name = "Rust"
            fake.fell_back_to_default = False
            with mock.patch.dict(sys.modules, {"rust_bgm_engine": mock.Mock(RustBackgroundEngine=mock.Mock(return_value=fake))}):
                self.assertTrue(p._init_bass_engine("Some Device"))
            self.assertIs(p._bass_engine, fake)

    def test_any_rust_failure_falls_back_to_libmpv(self):
        p = self._player("rust")
        libmpv = mock.Mock()
        libmpv.backend_name = "libmpv"
        broken = mock.Mock(RustBackgroundEngine=mock.Mock(side_effect=RuntimeError("no device")))
        with mock.patch.dict(sys.modules, {"rust_bgm_engine": broken}):
            with mock.patch.object(self.app, "LibmpvBackgroundEngine", return_value=libmpv) as ctor:
                self.assertTrue(p._init_bass_engine("Some Device"))
        ctor.assert_called_once()
        self.assertIs(p._bass_engine, libmpv)

    def test_no_bass_module_is_left_in_the_app(self):
        self.assertFalse(hasattr(self.app, "BassBackgroundEngine"))
        self.assertFalse(hasattr(self.app, "BassSoundboardChannel"))


if __name__ == "__main__":
    unittest.main()
