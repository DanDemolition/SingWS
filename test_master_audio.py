import unittest

import numpy as np

from singws_master_audio import MasterAudioProcessor, DEFAULT_PARAMS


SR = 44100
CH = 2


def sine(freq, secs, amp, sr=SR, ch=CH):
    n = int(secs * sr)
    t = np.arange(n) / sr
    mono = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return np.stack([mono] * ch, axis=1)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def process_in_blocks(proc, samples, block=1024):
    out = []
    for i in range(0, len(samples), block):
        out.append(proc.process_f32_array(samples[i:i + block]))
    return np.concatenate(out, axis=0)


class MasterAudioTests(unittest.TestCase):
    def make(self, **params):
        proc = MasterAudioProcessor(SR, CH, params=params or None)
        proc.set_enabled(True)
        return proc

    def test_disabled_is_passthrough(self):
        proc = MasterAudioProcessor(SR, CH)
        # Not enabled -> identical bytes back.
        data = sine(440, 0.1, 0.5).tobytes()
        self.assertEqual(proc.process_f32_bytes(data), data)

    def test_output_is_finite(self):
        proc = self.make()
        out = process_in_blocks(proc, sine(440, 1.0, 0.9))
        self.assertTrue(np.all(np.isfinite(out)))

    def test_no_clipping_on_hot_input(self):
        # Feed a signal hotter than 0 dBFS-ish; output must respect the ceiling.
        proc = self.make()
        loud = sine(220, 1.0, 0.99)
        # Add some inter-sample-ish transients.
        loud[::500] = 0.999
        out = process_in_blocks(proc, loud)
        peak = float(np.max(np.abs(out)))
        ceiling_lin = 10 ** (DEFAULT_PARAMS["output_ceiling_db"] / 20.0)
        self.assertLessEqual(peak, ceiling_lin + 1e-4)

    def test_bytes_roundtrip_length(self):
        proc = self.make()
        data = sine(440, 0.05, 0.3).tobytes()
        out = proc.process_f32_bytes(data)
        self.assertEqual(len(out), len(data))

    def test_quiet_intro_not_blown_up(self):
        # A quiet intro should gain at most ~makeup dB — never become "loud".
        proc = self.make()
        quiet = sine(440, 1.0, 0.05)  # ~ -26 dBFS
        out = process_in_blocks(proc, quiet)
        gain_db = 20 * np.log10(rms(out) / rms(quiet))
        # Makeup is +4 dB; allow a little for EQ. Must stay well under +8 dB and
        # the absolute level must stay clearly quiet (< -12 dBFS).
        self.assertLess(gain_db, 8.0)
        out_dbfs = 20 * np.log10(max(1e-7, rms(out)))
        self.assertLess(out_dbfs, -12.0)

    def test_leveling_reduces_dynamic_range(self):
        # A loud section followed by a quiet section: the compressor should pull
        # the two closer together (smaller loud/quiet ratio) than the input.
        proc = self.make()
        loud = sine(300, 1.5, 0.7)
        quiet = sine(300, 1.5, 0.12)
        sig = np.concatenate([loud, quiet], axis=0)
        out = process_in_blocks(proc, sig)
        half = len(loud)
        in_ratio = rms(loud) / rms(quiet)
        out_ratio = rms(out[:half]) / rms(out[half:])
        self.assertLess(out_ratio, in_ratio)

    def test_no_pumping_on_steady_tone(self):
        # A steady loud tone should reach a stable gain — the second half RMS
        # should match the first half closely (no slow pumping oscillation).
        proc = self.make()
        sig = sine(200, 2.0, 0.8)
        out = process_in_blocks(proc, sig)
        n = len(out)
        a = rms(out[n // 2: n * 3 // 4])
        b = rms(out[n * 3 // 4:])
        self.assertLess(abs(20 * np.log10(a / b)), 0.5)

    def test_reset_state_runs(self):
        proc = self.make()
        process_in_blocks(proc, sine(440, 0.2, 0.5))
        proc.reset_state()  # should not raise
        out = process_in_blocks(proc, sine(440, 0.2, 0.5))
        self.assertTrue(np.all(np.isfinite(out)))

    def test_all_stages_off_is_near_passthrough(self):
        # With every stage disabled, only the hard-clip guard remains, so a
        # moderate signal passes through essentially unchanged.
        proc = self.make(
            gate_enabled=0, eq_enabled=0, comp_enabled=0,
            limiter_enabled=0, exciter_mix=0,
        )
        sig = sine(440, 0.3, 0.4)
        out = process_in_blocks(proc, sig)
        self.assertLess(float(np.max(np.abs(out - sig))), 1e-4)

    def test_limiter_off_lets_peaks_through(self):
        # Sanity: disabling the limiter stage removes its gain reduction.
        on = self.make(comp_enabled=0, eq_enabled=0, gate_enabled=0, limiter_enabled=1,
                       limiter_ceiling_db=-6.0)
        off = self.make(comp_enabled=0, eq_enabled=0, gate_enabled=0, limiter_enabled=0)
        sig = sine(220, 0.5, 0.9)
        peak_on = float(np.max(np.abs(process_in_blocks(on, sig))))
        peak_off = float(np.max(np.abs(process_in_blocks(off, sig))))
        self.assertLess(peak_on, peak_off)

    def test_block_size_invariance(self):
        # Processing the same signal at two block sizes should give nearly the
        # same result (state carries across blocks correctly).
        sig = sine(330, 0.5, 0.6)
        a = process_in_blocks(self.make(), sig, block=512)
        b = process_in_blocks(self.make(), sig, block=4096)
        # Allow small numerical differences from filter state granularity.
        diff = float(np.max(np.abs(a - b)))
        self.assertLess(diff, 1e-3)


if __name__ == "__main__":
    unittest.main()
