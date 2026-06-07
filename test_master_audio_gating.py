import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_master", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module, **settings):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    base = {"performance_mode": False, "master_audio_enabled": False, "master_audio_params": {}}
    base.update(settings)
    app.settings = base
    # Bare __new__ widget can't getattr missing attrs; pre-set what we touch.
    app.karaoke_master = None
    app._master_init_error = None
    return app


class MasterGatingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_disabled_by_default(self):
        app = make_app(self.singws)
        self.assertFalse(app._master_processing_active())
        self.assertIsNone(app._ensure_master_processor())

    def test_enabled_when_on_and_not_performance(self):
        app = make_app(self.singws, master_audio_enabled=True)
        self.assertTrue(app._master_processing_active())
        proc = app._ensure_master_processor()
        self.assertIsNotNone(proc)
        self.assertTrue(proc.enabled())

    def test_performance_mode_bypasses(self):
        app = make_app(self.singws, master_audio_enabled=True, performance_mode=True)
        self.assertFalse(app._master_processing_active())
        self.assertIsNone(app._ensure_master_processor())

    def test_toggling_off_disables_existing_instance(self):
        app = make_app(self.singws, master_audio_enabled=True)
        proc = app._ensure_master_processor()
        self.assertTrue(proc.enabled())
        # Turn it off and ensure the existing instance is muted, not left live.
        app.settings["master_audio_enabled"] = False
        self.assertIsNone(app._ensure_master_processor())
        self.assertFalse(proc.enabled())

    def test_params_override_merged(self):
        app = make_app(
            self.singws,
            master_audio_enabled=True,
            master_audio_params={"comp_makeup_db": 1.5},
        )
        proc = app._ensure_master_processor()
        self.assertAlmostEqual(proc.params()["comp_makeup_db"], 1.5, places=3)


class PerStageMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_stage_enables_map_to_params(self):
        app = make_app(
            self.singws,
            master_audio_gate_enabled=True,
            master_audio_eq_enabled=False,
            master_audio_comp_enabled=False,
            master_audio_limiter_enabled=False,
        )
        p = app._compute_master_audio_params()
        self.assertEqual(p["gate_enabled"], 1.0)
        self.assertEqual(p["eq_enabled"], 0.0)
        self.assertEqual(p["comp_enabled"], 0.0)
        self.assertEqual(p["limiter_enabled"], 0.0)

    def test_compressor_amount_curve(self):
        a0 = make_app(self.singws, master_audio_comp_amount=0)._compute_master_audio_params()
        a50 = make_app(self.singws, master_audio_comp_amount=50)._compute_master_audio_params()
        a100 = make_app(self.singws, master_audio_comp_amount=100)._compute_master_audio_params()
        self.assertAlmostEqual(a0["comp_ratio"], 1.5, places=3)
        self.assertAlmostEqual(a0["comp_makeup_db"], 0.0, places=3)
        self.assertAlmostEqual(a50["comp_makeup_db"], 4.0, places=3)
        self.assertAlmostEqual(a100["comp_ratio"], 3.0, places=3)
        self.assertAlmostEqual(a100["comp_makeup_db"], 8.0, places=3)

    def test_tilt_and_ceiling_mapping(self):
        bright = make_app(self.singws, master_audio_tilt=100)._compute_master_audio_params()
        flat = make_app(self.singws, master_audio_tilt=0)._compute_master_audio_params()
        self.assertAlmostEqual(bright["high_shelf_db"], 4.0, places=3)
        self.assertAlmostEqual(flat["high_shelf_db"], 0.0, places=3)
        self.assertAlmostEqual(flat["low_shelf_db"], 1.0, places=3)
        c = make_app(self.singws, master_audio_ceiling_db=-2.5)._compute_master_audio_params()
        self.assertAlmostEqual(c["limiter_ceiling_db"], -2.5, places=3)
        self.assertAlmostEqual(c["output_ceiling_db"], -0.1, places=3)  # fixed safety

    def test_exciter_gated_by_enable(self):
        off = make_app(self.singws, master_audio_exciter_enabled=False,
                       master_audio_exciter_mix=80)._compute_master_audio_params()
        on = make_app(self.singws, master_audio_exciter_enabled=True,
                      master_audio_exciter_mix=80)._compute_master_audio_params()
        self.assertEqual(off["exciter_mix"], 0.0)
        self.assertGreater(on["exciter_mix"], 0.0)

    def test_bgm_active_follows_master_processing(self):
        # BGM now runs the full chain (Python DSP), so it tracks master
        # processing as a whole — not just the compressor stage — and stays off
        # in Performance Mode. Disabling a single stage (e.g. compressor) keeps
        # BGM active; that stage is simply bypassed inside the processor.
        on = make_app(self.singws, master_audio_enabled=True, master_audio_comp_enabled=True)
        comp_off = make_app(self.singws, master_audio_enabled=True, master_audio_comp_enabled=False)
        disabled = make_app(self.singws, master_audio_enabled=False)
        perf = make_app(self.singws, master_audio_enabled=True, performance_mode=True)
        self.assertTrue(on._bgm_master_active())
        self.assertTrue(comp_off._bgm_master_active())
        self.assertFalse(disabled._bgm_master_active())
        self.assertFalse(perf._bgm_master_active())


if __name__ == "__main__":
    unittest.main()
