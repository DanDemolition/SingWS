"""The Rust master processor must reproduce singws_master_audio.MasterAudioProcessor (the reference) sample for sample.

Needs the built library: ``SINGWS_DSP_LIB=<path to libsingws_dsp_ffi.dylib>`` (or the dev build in
``~/.cache/singws-rust-target/release``). Skipped when it is not available.
"""
import os
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))

import rust_master_dsp
from singws_master_audio import DEFAULT_PARAMS, MasterAudioProcessor

AVAILABLE = rust_master_dsp.available()
TOL = 2e-5   # both write float32; observed differences are ~1e-7


def signals(rate):
    rng = np.random.default_rng(1234)
    n = rate * 6
    t = np.arange(n) / rate
    env = 0.5 + 0.45 * np.sin(2 * np.pi * 0.35 * t)                       # slow level ride
    music = (np.sin(2 * np.pi * 110 * t) + 0.5 * np.sin(2 * np.pi * 440 * t) + 0.25 * np.sin(2 * np.pi * 3200 * t)) * env * 0.55
    noise = rng.standard_normal((n, 2)) * 0.08
    bursts = np.zeros(n)
    for start in range(rate, n, rate):                                     # sharp transients
        bursts[start:start + 200] = 0.95 * np.sin(2 * np.pi * 1000 * t[:200])
    stereo = np.stack([music + bursts, 0.8 * music - bursts * 0.5], axis=1) + noise
    quiet = stereo * 0.002
    hot = np.clip(stereo * 2.6, -1.4, 1.4)                                 # above full scale
    silence_then_loud = np.concatenate([np.zeros((rate, 2)), stereo[: rate * 2]])
    return {
        "music": stereo.astype(np.float32),
        "quiet": quiet.astype(np.float32),
        "hot": hot.astype(np.float32),
        "silence_then_loud": silence_then_loud.astype(np.float32),
    }


PARAM_SETS = {
    "defaults": {},
    "everything_on": {"gate_enabled": 1.0, "exciter_mix": 0.15, "comp_ratio": 3.0, "comp_threshold_db": -24.0, "low_shelf_db": 2.0, "presence_db": -1.5, "high_shelf_db": 2.5},
    "hard_knee": {"comp_knee_db": 0.0, "comp_ratio": 4.0},
    "gate_aggressive": {"gate_enabled": 1.0, "gate_threshold_db": -30.0, "gate_ratio": 3.0, "gate_floor_db": -30.0},
    "limiter_only": {"eq_enabled": 0.0, "comp_enabled": 0.0},
    "no_limiter_no_comp": {"limiter_enabled": 0.0, "comp_enabled": 0.0, "eq_enabled": 0.0},
    "tight_ceiling": {"limiter_ceiling_db": -6.0, "output_ceiling_db": -3.0, "comp_makeup_db": 9.0},
    "operator_like": {"comp_ratio": 2.0, "exciter_mix": 0.15, "gate_enabled": 1.0, "comp_makeup_db": 4.0},
}


def run_python(rate, params, x, block):
    p = MasterAudioProcessor(sample_rate=44100, channels=2)
    p.configure_stream(rate, 2)
    p.set_params(params)
    p.set_enabled(True)
    out = [np.asarray(p.process_f32_array(x[i:i + block]), dtype=np.float32) for i in range(0, len(x), block)]
    return np.concatenate(out), p


def run_rust(rate, params, x, block):
    p = rust_master_dsp.RustMasterProcessor(sample_rate=44100, channels=2)
    p.configure_stream(rate, 2)
    p.set_params(params)
    p.set_enabled(True)
    out = [np.asarray(p.process_f32_array(x[i:i + block]), dtype=np.float32) for i in range(0, len(x), block)]
    return np.concatenate(out), p


@unittest.skipUnless(AVAILABLE, "libsingws_dsp_ffi.dylib not built (cargo build --release -p singws-dsp-ffi)")
class NativeMatchesPythonTests(unittest.TestCase):
    def test_matches_reference_for_every_signal_and_parameter_set(self):
        worst = 0.0
        for rate in (44100, 48000):
            for sig_name, x in signals(rate).items():
                for set_name, params in PARAM_SETS.items():
                    ref, _ = run_python(rate, params, x, 1200)
                    got, _ = run_rust(rate, params, x, 1200)
                    diff = float(np.max(np.abs(ref - got)))
                    worst = max(worst, diff)
                    self.assertLess(diff, TOL, f"{rate} Hz / {sig_name} / {set_name}: max diff {diff}")
        print(f"\nworst difference vs the Python reference: {worst:.2e}")

    def test_block_size_does_not_change_the_result(self):
        x = signals(48000)["music"]
        params = PARAM_SETS["everything_on"]
        ref, _ = run_rust(48000, params, x, 1200)
        for block in (1, 7, 64, 777, 4096, 48000):
            got, _ = run_rust(48000, params, x[: 48000 * 2], block)
            self.assertLess(float(np.max(np.abs(ref[: 48000 * 2] - got))), 1e-6, f"block {block}")

    def test_disabled_is_a_no_op(self):
        x = signals(48000)["music"][:5000]
        p = rust_master_dsp.RustMasterProcessor(48000, 2)
        self.assertIs(p.process_f32_array(x), x)
        p.set_enabled(True)
        p.set_enabled(False)
        self.assertIs(p.process_f32_array(x), x)

    def test_output_never_exceeds_the_ceiling_and_is_finite(self):
        for rate in (44100, 48000):
            for x in (signals(rate)["hot"], signals(rate)["quiet"]):
                got, _ = run_rust(rate, {}, x, 1200)
                self.assertTrue(np.all(np.isfinite(got)))
                self.assertLessEqual(float(np.max(np.abs(got))), 10 ** (-0.1 / 20) + 1e-6)

    def test_gain_reduction_report_matches_reference(self):
        x = signals(48000)["hot"]
        _, py = run_python(48000, PARAM_SETS["everything_on"], x, 1200)
        _, rs = run_rust(48000, PARAM_SETS["everything_on"], x, 1200)
        a, b = py.gain_reduction_db(), rs.gain_reduction_db()
        for key in ("gate", "comp", "limiter"):
            self.assertAlmostEqual(a[key], b[key], delta=0.05, msg=key)

    def test_param_change_resets_state_like_the_reference(self):
        x = signals(48000)["music"]
        py = MasterAudioProcessor(48000, 2); py.set_enabled(True)
        rs = rust_master_dsp.RustMasterProcessor(48000, 2); rs.set_enabled(True)
        for proc, store in ((py, []), (rs, [])):
            pass
        half = len(x) // 2
        ref = [py.process_f32_array(x[:half])]
        got = [rs.process_f32_array(x[:half])]
        py.set_params({"comp_ratio": 5.0}); rs.set_params({"comp_ratio": 5.0})
        ref.append(py.process_f32_array(x[half:])); got.append(rs.process_f32_array(x[half:]))
        diff = float(np.max(np.abs(np.concatenate(ref).astype(np.float32) - np.concatenate(got).astype(np.float32))))
        self.assertLess(diff, TOL)

    def test_stats_counters_fill_in(self):
        x = signals(48000)["music"][:48000]
        p = rust_master_dsp.RustMasterProcessor(48000, 2); p.set_enabled(True)
        for i in range(0, len(x), 1200):
            p.process_f32_array(x[i:i + 1200])
        s = p.stats_snapshot()
        self.assertEqual(s["blocks"], 40)
        self.assertGreater(s["mean_us"], 0)
        self.assertEqual(s["over_budget"], 0)
        self.assertEqual(p.stats_snapshot()["blocks"], 0)   # snapshot resets


def _float_wav(path, rate, stereo):
    data = np.ascontiguousarray(stereo, dtype=np.float32).tobytes()
    header = (b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
              + (3).to_bytes(2, "little") + (2).to_bytes(2, "little") + rate.to_bytes(4, "little")
              + (rate * 8).to_bytes(4, "little") + (8).to_bytes(2, "little") + (32).to_bytes(2, "little")
              + b"data" + len(data).to_bytes(4, "little"))
    Path(path).write_bytes(header + data)


def _load_bass():
    import ctypes
    import bass_background_engine as bbe
    try:
        bass = ctypes.CDLL(str(bbe._find_library("bass")))
        mix = ctypes.CDLL(str(bbe._find_library("bassmix")))
    except Exception:
        return None
    D, Q, B = ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int
    bass.BASS_Init.argtypes = [ctypes.c_int, D, D, ctypes.c_void_p, ctypes.c_void_p]; bass.BASS_Init.restype = B
    bass.BASS_StreamCreateFile.argtypes = [B, ctypes.c_char_p, Q, Q, D]; bass.BASS_StreamCreateFile.restype = D
    bass.BASS_ChannelGetData.argtypes = [D, ctypes.c_void_p, D]; bass.BASS_ChannelGetData.restype = D
    bass.BASS_ChannelSetDSP.argtypes = [D, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]; bass.BASS_ChannelSetDSP.restype = D
    bass.BASS_ChannelRemoveDSP.argtypes = [D, D]; bass.BASS_ChannelRemoveDSP.restype = B
    bass.BASS_StreamFree.argtypes = [D]; bass.BASS_StreamFree.restype = B
    mix.BASS_Mixer_StreamCreate.argtypes = [D, D, D]; mix.BASS_Mixer_StreamCreate.restype = D
    mix.BASS_Mixer_StreamAddChannel.argtypes = [D, D, D]; mix.BASS_Mixer_StreamAddChannel.restype = B
    mix.BASS_Mixer_ChannelSetPosition.argtypes = [D, Q, D]; mix.BASS_Mixer_ChannelSetPosition.restype = B
    return bass, mix


@unittest.skipUnless(AVAILABLE, "library not built")
class RealBassTests(unittest.TestCase):
    """BASS itself calls the Rust callback (a decode-only mixer on the 'no sound' device: no audio hardware is touched)."""

    def test_bass_calls_the_native_callback_and_matches_the_reference(self):
        import ctypes
        import tempfile
        import bass_background_engine as bbe
        loaded = _load_bass()
        if loaded is None:
            self.skipTest("BASS runtime not available")
        bass, mix = loaded
        rate = 48000
        x = signals(rate)["music"][: rate * 3]
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "in.wav")
            _float_wav(wav, rate, x)
            if not bass.BASS_Init(0, rate, 0, None, None):
                self.skipTest("BASS_Init failed on the no-sound device")
            flags = bbe.BASS_SAMPLE_FLOAT | bbe.BASS_STREAM_DECODE
            mixer = mix.BASS_Mixer_StreamCreate(rate, 2, flags)
            src = bass.BASS_StreamCreateFile(0, wav.encode(), 0, 0, flags)
            self.assertTrue(mixer and src)
            # BASSmix ramps a source in over its first milliseconds unless told not to; that is not the DSP's doing.
            BASS_MIXER_CHAN_NORAMPIN = 0x800000
            self.assertTrue(mix.BASS_Mixer_StreamAddChannel(mixer, src, BASS_MIXER_CHAN_NORAMPIN))
            # Prove the harness is exact before the DSP exists: the mixer must hand back the input untouched.
            probe = (ctypes.c_float * 2400)()
            n0 = bass.BASS_ChannelGetData(mixer, probe, 2400 * 4)
            first = np.frombuffer(probe, dtype=np.float32, count=n0 // 4).reshape(-1, 2)
            self.assertEqual(float(np.max(np.abs(first - x[: len(first)]))), 0.0)
            self.assertTrue(mix.BASS_Mixer_ChannelSetPosition(src, 0, 0))   # rewind
            self.assertTrue(mix.BASS_Mixer_ChannelSetPosition(src, 0, 0))

            proc = rust_master_dsp.RustMasterProcessor(rate, 2)
            proc.set_params({"gate_enabled": 1.0, "exciter_mix": 0.15})
            proc.set_enabled(True)
            eng = bbe.BassBackgroundEngine.__new__(bbe.BassBackgroundEngine)   # real attach code, no audio device
            eng.bass, eng.mixer, eng.sample_rate = bass, mixer, rate
            eng._master_proc, eng._master_proc_ref = proc, {"proc": None}
            eng._master_dsp_handle, eng._master_dsp_callback, eng._closed = 0, None, True
            eng.close = lambda: None   # a stub: nothing to tear down in __del__
            eng._attach_master_dsp()
            self.assertNotEqual(eng._master_dsp_handle, 0)
            self.assertIsNone(eng._master_dsp_callback)   # no Python callback was built

            out = []
            buf = (ctypes.c_float * 2400)()
            frames_wanted = len(x)
            got_frames = 0
            while got_frames < frames_wanted:
                n = bass.BASS_ChannelGetData(mixer, buf, 2400 * 4)
                if n in (0, 0xFFFFFFFF):
                    break
                out.append(np.frombuffer(buf, dtype=np.float32, count=n // 4).copy().reshape(-1, 2))
                got_frames += n // 8
            got = np.concatenate(out)[:frames_wanted]

            ref, _ = run_python(rate, {"gate_enabled": 1.0, "exciter_mix": 0.15}, x, 1200)
            n = min(len(got), len(ref))
            self.assertGreater(n, rate * 2)
            diff = float(np.max(np.abs(ref[:n] - got[:n])))
            self.assertGreater(float(np.max(np.abs(got[:n] - x[:n]))), 0.01, "the DSP did not change the audio")
            self.assertLess(diff, TOL, f"BASS + Rust differs from the Python reference by {diff}")
            self.assertGreater(proc.stats_snapshot()["blocks"], 10)
            eng._detach_master_dsp()
            bass.BASS_StreamFree(mixer)
            bass.BASS_StreamFree(src)


class ParamOrderTests(unittest.TestCase):
    @unittest.skipUnless(AVAILABLE, "library not built")
    def test_names_match_the_python_defaults(self):
        lib = rust_master_dsp._load()
        names = lib.singws_master_param_names().decode().split(",")
        self.assertEqual(names, list(DEFAULT_PARAMS))


class HostWiringTests(unittest.TestCase):
    SRC = (Path(__file__).resolve().parent / "0.2.18.1.py").read_text()

    def test_setting_defaults_to_rust_with_python_fallback(self):
        self.assertRegex(self.SRC, r'"master_dsp_engine":\s*"rust"')

    def test_host_falls_back_to_python_when_the_library_is_missing(self):
        body = self.SRC.split("def _new_bgm_master_processor")[1].split("def _ensure_bgm_master_processor")[0]
        self.assertIn('want == "rust"', body)
        self.assertIn("using the Python processor", body)
        self.assertIn("MasterAudioProcessor(sample_rate=44100, channels=2)", body)


if __name__ == "__main__":
    unittest.main()
