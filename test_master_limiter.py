"""Limiter behaviour of the master chain (look-ahead brickwall). Isolated: gate/EQ/exciter/compressor off."""
import unittest

import numpy as np

from singws_master_audio import MasterAudioProcessor

SR = 48000
ISOLATE = {"gate_enabled": 0.0, "eq_enabled": 0.0, "comp_enabled": 0.0, "exciter_enabled": 0.0,
           "output_ceiling_db": -0.1}


def make(**kw):
    p = MasterAudioProcessor(SR, 2, params={**ISOLATE, **kw})
    p.set_enabled(True)
    return p


def run(proc, x, block=1024):
    return np.concatenate([proc.process_f32_array(x[i:i + block]) for i in range(0, len(x), block)])


def stereo(mono):
    return np.stack([mono, mono], axis=1).astype(np.float32)


def sine(amp, n=SR, f=220.0):
    return stereo(amp * np.sin(2 * np.pi * f * np.arange(n) / SR))


class LimiterTests(unittest.TestCase):
    def test_ceiling_holds_for_hot_signals(self):
        rng = np.random.default_rng(1)
        sigs = [sine(2.0), stereo(np.where(np.arange(SR) // 100 % 2, 1.8, -1.8)), stereo(rng.uniform(-3, 3, SR))]
        for ceil in (-1.0, -3.0, -6.0):
            lin = 10 ** (ceil / 20)
            for s in sigs:
                out = run(make(limiter_ceiling_db=ceil), s)
                self.assertLessEqual(float(np.abs(out).max()), lin + 1e-5, ceil)

    def test_below_ceiling_is_untouched_but_delayed(self):
        x = sine(0.3, SR // 2)
        proc = make(limiter_ceiling_db=-1.0)
        out = run(proc, x)
        delay = int(proc._lim_len) - 1
        np.testing.assert_allclose(out[delay:], x[:len(x) - delay], atol=1e-6)

    def test_gain_recovers_after_burst(self):
        x = np.concatenate([sine(0.2, SR // 4), sine(3.0, SR // 20), sine(0.2, SR)])
        out = run(make(), x)
        tail = out[-SR // 4:]
        self.assertAlmostEqual(float(np.abs(tail).max()), 0.2, delta=0.01)

    def test_block_size_independent(self):
        x = sine(1.5, SR // 2)
        a = run(make(), x, 1024)
        b = run(make(), x, 97)
        np.testing.assert_allclose(a, b, atol=1e-6)

    def test_stereo_linked(self):
        x = np.zeros((SR // 4, 2), np.float32)
        x[:, 0] = 2.0 * np.sin(2 * np.pi * 220 * np.arange(len(x)) / SR)
        x[:, 1] = 0.1 * np.sin(2 * np.pi * 220 * np.arange(len(x)) / SR)
        out = run(make(), x)
        ratio_in = 0.1 / 2.0
        sl = slice(SR // 8, SR // 4)
        ratio = np.abs(out[sl, 1]).max() / np.abs(out[sl, 0]).max()
        self.assertAlmostEqual(ratio, ratio_in, delta=0.005)

    def test_limiter_off_has_no_delay(self):
        x = sine(0.3, 4800)
        out = run(make(limiter_enabled=0.0), x)
        np.testing.assert_allclose(out, x, atol=1e-6)

    def test_param_change_keeps_audio(self):
        proc = make()
        x = sine(0.5, SR // 2)
        a = run(proc, x[:SR // 4])
        proc.set_params({"limiter_release_ms": 120.0})
        b = run(proc, x[SR // 4:])
        self.assertGreater(float(np.abs(b[:200]).max()), 0.1)  # no dropout after the change

    def test_reports_gain_reduction(self):
        proc = make()
        run(proc, sine(3.0, SR // 4))
        self.assertLess(proc.gain_reduction_db()["limiter"], -1.0)


if __name__ == "__main__":
    unittest.main()
