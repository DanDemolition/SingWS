import importlib.util
import unittest

import bass_background_engine as bbe


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_bgm", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeBass:
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


class FakeProc:
    """Minimal MasterAudioProcessor stand-in for engine DSP wiring tests."""
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
    # Attrs touched by __del__/close()/stop() during GC of this bare instance.
    eng.primary = None
    eng.secondary = None
    eng._closed = True
    eng._plugin_handles = []
    eng._eq_fx_handles = []
    eng._eq_dsp_handle = 0
    eng._eq_dsp_callback = None
    eng.mix = eng.bass
    return eng


class EngineCompressorTests(unittest.TestCase):
    def test_attach_stores_and_creates_fx(self):
        eng = bare_engine()
        eng.set_master_compressor({"threshold_db": -18.0, "ratio": 2.5})
        self.assertNotEqual(eng._master_fx_handle, 0)
        self.assertEqual(eng._master_params["threshold_db"], -18.0)
        self.assertEqual(eng.bass.set_calls[-1][1], bbe.BASS_FX_DX8_COMPRESSOR)

    def test_detach_removes_fx(self):
        eng = bare_engine()
        eng.set_master_compressor({"ratio": 2.0})
        handle = eng._master_fx_handle
        eng.set_master_compressor(None)
        self.assertEqual(eng._master_fx_handle, 0)
        self.assertIsNone(eng._master_params)
        self.assertIn((eng.mixer, handle), eng.bass.remove_calls)

    def test_ensure_mixer_reattaches_when_configured(self):
        eng = bare_engine()
        eng.mixer = 0  # mixer not yet created
        eng.set_master_compressor({"ratio": 2.0})  # stores params, can't attach yet
        self.assertEqual(eng._master_fx_handle, 0)
        self.assertTrue(eng._master_should_attach())
        # Simulate mixer now existing and re-attach.
        eng.mixer = 0x1234
        eng._attach_master_fx()
        self.assertNotEqual(eng._master_fx_handle, 0)


class EngineMasterDspTests(unittest.TestCase):
    def test_attach_configures_and_sets_dsp(self):
        eng = bare_engine()
        proc = FakeProc()
        eng.set_master_processor(proc)
        self.assertNotEqual(eng._master_dsp_handle, 0)
        self.assertIs(eng._master_proc, proc)
        self.assertIs(eng._master_proc_ref["proc"], proc)
        self.assertEqual(proc.configured, (44100, 2))
        # Master DSP runs after the EQ (priority 0), so it uses a lower priority.
        self.assertEqual(eng.bass.set_calls[-1], (eng.mixer, "DSP", -1))

    def test_detach_removes_dsp(self):
        eng = bare_engine()
        eng.set_master_processor(FakeProc())
        handle = eng._master_dsp_handle
        eng.set_master_processor(None)
        self.assertEqual(eng._master_dsp_handle, 0)
        self.assertIsNone(eng._master_proc)
        self.assertIsNone(eng._master_proc_ref["proc"])
        self.assertIn((eng.mixer, handle), eng.bass.remove_calls)

    def test_same_instance_is_noop(self):
        eng = bare_engine()
        proc = FakeProc()
        eng.set_master_processor(proc)
        handle = eng._master_dsp_handle
        calls_before = len(eng.bass.set_calls)
        eng.set_master_processor(proc)  # same instance -> no re-wire
        self.assertEqual(eng._master_dsp_handle, handle)
        self.assertEqual(len(eng.bass.set_calls), calls_before)

    def test_ensure_mixer_reattaches_when_configured(self):
        eng = bare_engine()
        eng.mixer = 0  # mixer not yet created
        eng.set_master_processor(FakeProc())  # stores proc, can't attach yet
        self.assertEqual(eng._master_dsp_handle, 0)
        eng.mixer = 0x1234
        eng._attach_master_dsp()
        self.assertNotEqual(eng._master_dsp_handle, 0)


class FakeEngine:
    def __init__(self):
        self.calls = []

    def set_master_processor(self, processor):
        self.calls.append(processor)


class HostBgmGatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_app(self, **settings):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        base = {"performance_mode": False, "master_audio_enabled": False, "master_audio_params": {}}
        base.update(settings)
        app.settings = base
        app.bgm_master = None
        bg = type("Bg", (), {})()
        bg._bass_engine = FakeEngine()
        app.bg_music = bg
        return app

    def test_processor_carries_full_chain_params(self):
        # The BGM processor is built from the same params as karaoke, so tilt /
        # exciter / limiter all flow through — not just the compressor.
        app = self.make_app(
            master_audio_enabled=True,
            master_audio_exciter_enabled=True,
            master_audio_params={"comp_ratio": 3.0, "exciter_mix": 0.4, "limiter_ceiling_db": -2.0},
        )
        proc = app._ensure_bgm_master_processor()
        self.assertIsNotNone(proc)
        p = proc.params()
        self.assertEqual(p["comp_ratio"], 3.0)
        self.assertEqual(p["exciter_mix"], 0.4)
        self.assertEqual(p["limiter_ceiling_db"], -2.0)

    def test_apply_attaches_processor_when_active(self):
        app = self.make_app(master_audio_enabled=True)
        app._apply_bgm_master_processing()
        attached = app.bg_music._bass_engine.calls[-1]
        self.assertIsNotNone(attached)
        self.assertTrue(attached.enabled())

    def test_apply_detaches_when_disabled(self):
        app = self.make_app(master_audio_enabled=False)
        app._apply_bgm_master_processing()
        self.assertIsNone(app.bg_music._bass_engine.calls[-1])

    def test_performance_mode_detaches(self):
        app = self.make_app(master_audio_enabled=True, performance_mode=True)
        app._apply_bgm_master_processing()
        self.assertIsNone(app.bg_music._bass_engine.calls[-1])


class NormalizeRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def make_player(self):
        bgp = self.singws.BackgroundMusicPlayer.__new__(self.singws.BackgroundMusicPlayer)
        bgp.playlist = ["/a.mp3"]
        bgp.current_index = 0
        bgp._bg_norm_gen = {"primary": 5}
        bgp._bg_norm_retry = {}
        bgp._bg_normalize_active = lambda: True
        bgp._refresh_calls = []
        bgp._refresh_bg_normalize = lambda p, d: bgp._refresh_calls.append((p, d))
        return bgp

    def test_retry_fires_when_token_matches(self):
        bgp = self.make_player()
        captured = []
        self.singws.QTimer.singleShot = staticmethod(lambda ms, cb: captured.append(cb))
        bgp._schedule_bg_normalize_retry("/a.mp3", "primary")
        self.assertEqual(len(captured), 1)
        captured[0]()  # token (5) still current -> re-applies
        self.assertEqual(bgp._refresh_calls, [("/a.mp3", "primary")])

    def test_retry_cancels_on_track_change(self):
        bgp = self.make_player()
        captured = []
        self.singws.QTimer.singleShot = staticmethod(lambda ms, cb: captured.append(cb))
        bgp._schedule_bg_normalize_retry("/a.mp3", "primary")
        # A new track bumped the generation token -> stale retry must bail.
        bgp._bg_norm_gen = {"primary": 6}
        captured[0]()
        self.assertEqual(bgp._refresh_calls, [])

    def test_retry_is_bounded(self):
        bgp = self.make_player()
        captured = []
        self.singws.QTimer.singleShot = staticmethod(lambda ms, cb: captured.append(cb))
        for _ in range(20):
            bgp._schedule_bg_normalize_retry("/a.mp3", "primary")
        # Capped at 12 scheduled retries for the same (deck, path).
        self.assertEqual(len(captured), 12)


if __name__ == "__main__":
    unittest.main()
