"""Regression tests: EQ must route into BOTH audio paths (karaoke + BGM).

Live-show symptom: EQ changes audible on karaoke but not on background music
(and vice versa depending on toggle order). Root causes fixed:

  * Toggling Simple Audio Mode updated the BGM engine live but left the
    RUNNING karaoke transport's EQ attached/detached until the next song —
    the two paths disagreed mid-session. `_apply_simple_audio_mode_live`
    now swaps both live.
  * BassBackgroundEngine.stop() freed the mixer without zeroing the master
    FX/DSP handles, so _ensure_mixer's `handle == 0` re-attach guards never
    fired again: the BGM master chain (and compressor) silently dropped
    after the first track change. EQ handles were already detached in stop();
    now master handles are too.
  * The EQ dialog attached BGM EQ even in Simple Audio Mode (karaoke stayed
    bypassed) — now both paths follow the same simple-audio rule and the
    dialog shows a bypass notice.

[EQ-ROUTE] log lines now report the active routing for both paths.
"""

import importlib.util
import os
import unittest

os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")

import bass_background_engine as bbe
from gst_karaoke_transport import GstKaraokeTransport


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_eqroute", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBass:
    """Records FX/DSP wiring calls; same shape as test_bgm_master's fake."""

    def __init__(self):
        self.set_calls = []
        self.remove_calls = []
        self._h = 0

    def BASS_ChannelSetFX(self, mixer, fxtype, prio):
        self._h += 1
        self.set_calls.append((mixer, fxtype, prio))
        return self._h

    def BASS_FXSetParameters(self, handle, ptr):
        return 1

    def BASS_ChannelRemoveFX(self, mixer, handle):
        self.remove_calls.append((mixer, handle))
        return 1

    def BASS_ChannelSetDSP(self, mixer, proc, user, prio):
        self._h += 1
        self.set_calls.append((mixer, "DSP", prio))
        return self._h

    def BASS_ChannelRemoveDSP(self, mixer, handle):
        self.remove_calls.append((mixer, handle))
        return 1

    def BASS_ChannelStop(self, mixer):
        return 1

    def BASS_StreamFree(self, handle):
        return 1

    def BASS_ChannelSetAttribute(self, handle, attrib, value):
        return 1

    def BASS_Mixer_StreamCreate(self, rate, chans, flags):
        self._h += 1
        return 0xB000 + self._h

    def BASS_Mixer_ChannelRemove(self, handle):
        return 1


class FakeEq:
    def __init__(self, enabled=True, flat=False, gains=None):
        self._enabled = enabled
        self._flat = flat
        self._gains = gains if gains is not None else [3.0] * 10

    def enabled(self):
        return self._enabled

    def is_flat(self):
        return self._flat

    def gains_db(self):
        return list(self._gains)

    def configure_stream(self, sr, ch):
        pass

    def process_f32_array(self, frames):
        return frames

    def set_enabled(self, v):
        self._enabled = bool(v)


class FakeProc:
    def __init__(self):
        self.configured = None

    def configure_stream(self, sr, ch):
        self.configured = (sr, ch)

    def process_f32_array(self, frames):
        return frames


def bare_engine():
    eng = bbe.BassBackgroundEngine.__new__(bbe.BassBackgroundEngine)
    eng._master_params = None
    eng._master_fx_handle = 0
    eng._master_proc = None
    eng._master_dsp_handle = 0
    eng._master_dsp_callback = None
    eng._master_proc_ref = {"proc": None}
    eng.sample_rate = 44100
    eng.bass = FakeBass()
    eng.mixer = 0xABCD
    eng.master_volume = 1.0
    eng.primary = None
    eng.secondary = None
    eng._closed = True
    eng._plugin_handles = []
    eng._eq = None
    eng._eq_fx_handles = []
    eng._eq_dsp_handle = 0
    eng._eq_dsp_callback = None
    eng.mix = eng.bass
    return eng


class EqAttachLifecycleTests(unittest.TestCase):
    def test_set_eq_attaches_native_fx_when_enabled_nonflat(self):
        eng = bare_engine()
        eng.set_eq(FakeEq(enabled=True, flat=False))
        self.assertEqual(len(eng._eq_fx_handles), 10)

    def test_set_eq_flat_stays_detached(self):
        eng = bare_engine()
        eng.set_eq(FakeEq(enabled=True, flat=True))
        self.assertEqual(eng._eq_fx_handles, [])
        self.assertEqual(eng._eq_dsp_handle, 0)

    def test_set_eq_none_detaches(self):
        eng = bare_engine()
        eng.set_eq(FakeEq())
        self.assertTrue(eng._eq_fx_handles)
        eng.set_eq(None)
        self.assertEqual(eng._eq_fx_handles, [])

    def test_stop_then_rebuild_reattaches_eq(self):
        eng = bare_engine()
        eng.set_eq(FakeEq())
        self.assertTrue(eng._eq_fx_handles)
        eng.stop()
        self.assertEqual(eng.mixer, 0)
        self.assertEqual(eng._eq_fx_handles, [])
        eng._ensure_mixer()
        self.assertTrue(eng._eq_fx_handles, "EQ must re-attach on mixer rebuild")

    def test_stop_then_rebuild_reattaches_master_processor(self):
        # Regression: stop() used to free the mixer with the master DSP handle
        # still non-zero, so _ensure_mixer never re-attached the master chain
        # and BGM lost master processing after the first track change.
        eng = bare_engine()
        proc = FakeProc()
        eng.set_master_processor(proc)
        self.assertNotEqual(eng._master_dsp_handle, 0)
        eng.stop()
        self.assertEqual(eng._master_dsp_handle, 0, "stop() must zero the master DSP handle")
        eng._ensure_mixer()
        self.assertNotEqual(eng._master_dsp_handle, 0, "master chain must re-attach on rebuild")
        self.assertIs(eng._master_proc_ref["proc"], proc)

    def test_stop_then_rebuild_reattaches_master_compressor(self):
        eng = bare_engine()
        eng.set_master_compressor({"ratio": 2.0})
        self.assertNotEqual(eng._master_fx_handle, 0)
        eng.stop()
        self.assertEqual(eng._master_fx_handle, 0, "stop() must zero the compressor handle")
        eng._ensure_mixer()
        self.assertNotEqual(eng._master_fx_handle, 0, "compressor must re-attach on rebuild")


class FakeBgEngine:
    def __init__(self):
        self.eq_history = []
        self._eq_fx_handles = []
        self._eq_dsp_handle = 0

    def set_eq(self, eq):
        self.eq_history.append(eq)
        self._eq_fx_handles = [1] * 10 if eq is not None else []


class FakeBgMusic:
    def __init__(self):
        self._bass_engine = FakeBgEngine()
        self.normalize_refreshes = 0

    def _refresh_bg_normalize(self):
        self.normalize_refreshes += 1


class FakeTransport:
    def __init__(self):
        self.eq = None


class SimpleAudioLiveApplyTests(unittest.TestCase):
    """Toggling Simple Audio Mode must update karaoke AND BGM live."""

    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_app(self, simple=True):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"simple_audio_mode": simple}
        app.karaoke_eq = FakeEq()
        app.bgm_eq = FakeEq()
        app._ensure_eq_engines = lambda: True
        app.bg_music = FakeBgMusic()
        app.karaoke_transport = FakeTransport()
        app._current_karaoke_mode = "mp4"
        return app

    def test_advanced_mode_routes_both_paths(self):
        app = self.make_app(simple=False)
        app.settings["simple_audio_mode"] = False
        app._apply_simple_audio_mode_live(False)
        self.assertIs(app.karaoke_transport.eq, app.karaoke_eq)
        self.assertIs(app.bg_music._bass_engine.eq_history[-1], app.bgm_eq)
        self.assertEqual(app.bg_music.normalize_refreshes, 1)

    def test_simple_mode_bypasses_both_paths(self):
        app = self.make_app(simple=True)
        # simulate a song mid-play with EQ attached from advanced mode
        app.karaoke_transport.eq = app.karaoke_eq
        app._apply_simple_audio_mode_live(True)
        self.assertIsNone(app.karaoke_transport.eq, "karaoke EQ must detach live")
        self.assertIsNone(app.bg_music._bass_engine.eq_history[-1], "BGM EQ must detach live")

    def test_no_transport_running_is_safe(self):
        app = self.make_app(simple=False)
        app.karaoke_transport = None
        app._apply_simple_audio_mode_live(False)  # must not raise
        self.assertIs(app.bg_music._bass_engine.eq_history[-1], app.bgm_eq)


class FakeEqualizerElement:
    def __init__(self):
        self.bands = {}

    def set_property(self, name, value):
        self.bands[name] = value


class KaraokeEqMirrorTests(unittest.TestCase):
    """The karaoke transport mirrors the shared GraphicEQ onto the native
    equalizer-10bands element each tick — identical for CDG/MP3/MP4 (the
    audio bin is shared across modes)."""

    def make_transport(self):
        t = GstKaraokeTransport.__new__(GstKaraokeTransport)
        t.eq = None
        t._eq_last_applied = None
        t.equalizer = FakeEqualizerElement()
        return t

    def test_eq_gains_mirrored_when_attached(self):
        t = self.make_transport()
        t.eq = FakeEq(gains=[1.0, 2.0, 3.0, 4.0, 5.0, -1.0, -2.0, -3.0, -4.0, -5.0])
        t._mirror_eq()
        self.assertEqual(t.equalizer.bands["band0"], 1.0)
        self.assertEqual(t.equalizer.bands["band9"], -5.0)

    def test_detached_eq_zeroes_bands(self):
        t = self.make_transport()
        t.eq = FakeEq(gains=[6.0] * 10)
        t._mirror_eq()
        t.eq = None  # live detach (e.g. simple audio toggled on mid-song)
        t._mirror_eq()
        self.assertEqual(t.equalizer.bands["band0"], 0.0)

    def test_disabled_eq_zeroes_bands(self):
        t = self.make_transport()
        eq = FakeEq(gains=[6.0] * 10)
        t.eq = eq
        t._mirror_eq()
        eq.set_enabled(False)
        t._mirror_eq()
        self.assertEqual(t.equalizer.bands["band0"], 0.0)


if __name__ == "__main__":
    unittest.main()
