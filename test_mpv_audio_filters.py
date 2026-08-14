"""Tests for the SingWS audio chain expressed as an mpv `af` filter chain.

Three things are checked:

1. **Structure** — filters appear in the SingWS signal order and only when the
   corresponding stage is on.
2. **Fidelity** — the 10-band graphic EQ is a genuine port, so its lavfi form
   must match singws_eq.GraphicEQ's magnitude response to within a fraction of
   a dB. (The master bus is a documented approximation and is not held to this.)
3. **Validity** — every generated chain is accepted by the real mpv binary.

2 and 3 need ffmpeg/mpv on PATH and numpy/scipy installed; they skip cleanly
without them. No Qt required.
"""

import shutil
import subprocess
import unittest

import mpv_audio_filters as M


def _host_master_params(comp_amount=50, tilt=25, exciter=20, *, exciter_on=False,
                        gate=False, eq=True, comp=True, limiter=True, ceiling=-1.0):
    """Mirror of KaraokeApp._compute_master_audio_params, so these tests break
    if the host's knob mapping drifts away from what the filter builder gets."""
    c = max(0.0, min(100.0, float(comp_amount))) / 100.0
    t = max(-100.0, min(100.0, float(tilt))) / 100.0
    e = max(0.0, min(100.0, float(exciter))) / 100.0
    return {
        "gate_enabled": 1.0 if gate else 0.0,
        "eq_enabled": 1.0 if eq else 0.0,
        "comp_enabled": 1.0 if comp else 0.0,
        "limiter_enabled": 1.0 if limiter else 0.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 1.5 + c * 1.5,
        "comp_makeup_db": c * 8.0,
        "high_shelf_db": t * 4.0,
        "presence_db": t * 1.5,
        "low_shelf_db": 1.0 - t * 2.0,
        "exciter_mix": (e * 0.5) if exciter_on else 0.0,
        "limiter_ceiling_db": ceiling,
        "output_ceiling_db": -0.1,
    }


class ChainStructureTests(unittest.TestCase):
    def test_nothing_active_is_empty(self):
        self.assertEqual(M.build_af_chain(), "")
        self.assertIn("passthrough", M.describe_chain(""))

    def test_tempo_is_never_a_filter(self):
        # mpv applies tempo with the `speed` property, mirroring the Python
        # transport keeping tempo out of its DSP.
        chain = M.build_af_chain(semitones=3, normalize_gain_db=-4.0,
                                 master_enabled=True,
                                 master_params=_host_master_params())
        for banned in ("atempo", "rubberband=tempo", "speed="):
            self.assertNotIn(banned, chain)

    def test_key_uses_rubberband_pitch_scale(self):
        self.assertEqual(M.key_filter(0), "")
        self.assertIn("rubberband=pitch-scale=1.122462", M.key_filter(2))
        self.assertIn("rubberband=pitch-scale=0.890899", M.key_filter(-2))

    def test_normalize_is_a_single_gain(self):
        self.assertEqual(M.normalize_filter(0.0), "")
        self.assertEqual(M.normalize_filter(-5.1), "volume=-5.1dB")

    def test_inaudible_gains_are_dropped(self):
        self.assertEqual(M.normalize_filter(0.01), "")
        self.assertEqual(M.graphic_eq_filters([0.0] * 10), [])

    def test_eq_emits_one_biquad_per_active_band(self):
        gains = [3.0, 0.0, -2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0]
        filters = M.graphic_eq_filters(gains)
        self.assertEqual(len(filters), 3)
        self.assertIn("equalizer=f=31.5:t=q:w=1.4:g=3", filters[0])
        self.assertIn("equalizer=f=125:t=q:w=1.4:g=-2", filters[1])
        self.assertIn("equalizer=f=16000:t=q:w=1.4:g=4", filters[2])

    def test_signal_order_matches_the_singws_chain(self):
        chain = M.build_af_chain(
            semitones=1, normalize_gain_db=-5.0, eq_enabled=True,
            eq_gains_db=[3.0] + [0.0] * 9, master_enabled=True,
            master_params=_host_master_params(exciter=100, exciter_on=True, gate=True),
        )
        order = ["rubberband", "volume=", "equalizer=f=31.5", "agate=",
                 "bass=", "aexciter=", "acompressor=", "alimiter="]
        positions = [chain.index(token) for token in order]
        self.assertEqual(positions, sorted(positions), chain)

    def test_gate_threshold_converts_db_to_linear(self):
        # agate takes linear 0..1, not dB. -58 dB -> ~0.00126.
        chain = ",".join(M.master_bus_filters(_host_master_params(gate=True)))
        self.assertIn("agate=threshold=0.001259", chain)
        self.assertIn("range=0.125893", chain)  # gate_floor_db -18 dB

    def test_disabled_stages_are_absent(self):
        chain = ",".join(M.master_bus_filters(
            _host_master_params(gate=False, comp=False, exciter_on=False)))
        self.assertNotIn("agate=", chain)
        self.assertNotIn("acompressor=", chain)
        self.assertNotIn("aexciter=", chain)

    def test_single_limiter_and_guard_takes_over_when_limiter_off(self):
        on = ",".join(M.master_bus_filters(_host_master_params(limiter=True)))
        self.assertEqual(on.count("alimiter="), 1)
        self.assertIn("alimiter=limit=-1dB", on)
        off = ",".join(M.master_bus_filters(_host_master_params(limiter=False)))
        self.assertEqual(off.count("alimiter="), 1)
        self.assertIn("alimiter=limit=-0.1dB", off)

    def test_limiter_never_auto_levels(self):
        # alimiter's "auto level" defaults to ON and divides the output by the
        # limit, normalizing back to 0 dBFS. Measured against the NumPy limiter
        # it added exactly +1.00 dB at every level below the ceiling, so the
        # ceiling did nothing at all. Regression guard: it must stay disabled
        # on both the limiter and the clip-guard branch.
        for limiter in (True, False):
            chain = ",".join(M.master_bus_filters(
                _host_master_params(limiter=limiter)))
            self.assertIn("level=disabled", chain)

    def test_compressor_threshold_carries_the_detector_offset(self):
        # acompressor detects on smoothed RMS, MasterAudioProcessor on smoothed
        # mean-of-|x|. Passing the threshold through unchanged started
        # compression ~3 dB early. With the offset the two static transfer
        # curves agree within 0.02 dB from -40 to -3 dBFS.
        params = _host_master_params(comp=True)
        params["comp_threshold_db"] = -20.0
        chain = ",".join(M.master_bus_filters(params))
        self.assertIn(f"threshold={-20.0 + M._COMP_DETECTOR_OFFSET_DB:g}dB", chain)
        self.assertAlmostEqual(M._COMP_DETECTOR_OFFSET_DB, 3.0, places=6)

    def test_compressor_knee_is_a_factor_not_decibels(self):
        # lavfi's knee spans threshold/sqrt(k) .. threshold*sqrt(k), so the
        # width in dB is 20*log10(k). Passing 6 dB through as "6" produced a
        # 15.6 dB knee.
        self.assertAlmostEqual(M._knee_factor(6.0), 10 ** (6.0 / 20.0), places=6)
        self.assertAlmostEqual(M._knee_factor(0.0), 1.0, places=6)
        # ffmpeg clamps the factor to 1..8; anything wider must not be emitted.
        self.assertAlmostEqual(M._knee_factor(60.0), 8.0, places=6)
        params = _host_master_params(comp=True)
        params["comp_knee_db"] = 6.0
        self.assertIn("knee=1.995262", ",".join(M.master_bus_filters(params)))

    def test_exciter_frequency_is_clamped_to_the_filter_range(self):
        params = _host_master_params(exciter=100, exciter_on=True)
        params["exciter_hz"] = 500.0  # below aexciter's 2000 Hz floor
        self.assertIn("freq=2000", ",".join(M.master_bus_filters(params)))
        params["exciter_hz"] = 20000.0
        self.assertIn("freq=12000", ",".join(M.master_bus_filters(params)))

    def test_master_params_ignored_when_master_is_off(self):
        chain = M.build_af_chain(master_enabled=False,
                                 master_params=_host_master_params(gate=True))
        self.assertEqual(chain, "")

    def test_never_emits_exponent_notation(self):
        # lavfi rejects "1e-05" style numbers.
        params = _host_master_params(gate=True)
        params["gate_threshold_db"] = -120.0
        self.assertNotIn("e-", ",".join(M.master_bus_filters(params)).replace("release", ""))

    def test_garbage_inputs_do_not_raise(self):
        chain = M.build_af_chain(
            semitones="nonsense", normalize_gain_db=None, eq_enabled=True,
            eq_gains_db=[None, "x", float("nan")], master_enabled=True,
            master_params={"comp_ratio": "bad", "limiter_enabled": 1.0},
        )
        self.assertIsInstance(chain, str)

    def test_fidelity_notes_name_the_approximate_stages(self):
        chain = M.build_af_chain(
            master_enabled=True,
            master_params=_host_master_params(gate=True, exciter=50, exciter_on=True))
        self.assertEqual(
            M.chain_fidelity_notes(chain),
            ["gate", "exciter", "compressor", "limiter"],
        )
        # A pure EQ/normalize chain is a true port and must claim nothing.
        exact = M.build_af_chain(normalize_gain_db=-3.0, eq_enabled=True,
                                 eq_gains_db=[2.0] * 10)
        self.assertEqual(M.chain_fidelity_notes(exact), [])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not on PATH")
class EqFidelityTests(unittest.TestCase):
    """The graphic EQ is claimed to be a genuine port, so prove it."""

    def test_lavfi_eq_matches_the_numpy_eq(self):
        try:
            import numpy as np
            from singws_eq import GraphicEQ
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"numpy/scipy/singws_eq unavailable: {exc}")

        sr, n = 44100, 1 << 16
        gains = [6.0, -4.0, 3.0, 0.0, -5.0, 2.0, 0.0, 4.0, -6.0, 3.0]

        impulse = np.zeros((n, 2), dtype=np.float32)
        impulse[0, :] = 1.0

        eq = GraphicEQ(sample_rate=sr, channels=2)
        eq.configure_stream(sr, 2)
        eq.set_all_gains_db(gains)
        eq.set_enabled(True)
        numpy_out = eq.process_f32_array(impulse.copy())

        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-f", "f32le",
             "-ar", str(sr), "-ac", "2", "-i", "pipe:0",
             "-af", ",".join(M.graphic_eq_filters(gains)),
             "-f", "f32le", "-ar", str(sr), "-ac", "2", "pipe:1"],
            input=impulse.tobytes(), capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:400])
        lavfi_out = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2)

        length = min(len(numpy_out), len(lavfi_out))

        def magnitude_db(block):
            spectrum = np.fft.rfft(block[:length, 0].astype(np.float64))
            return 20.0 * np.log10(np.abs(spectrum) + 1e-12)

        freqs = np.fft.rfftfreq(length, 1.0 / sr)
        audible = (freqs >= 20.0) & (freqs <= 20000.0)
        deviation = np.max(np.abs(magnitude_db(numpy_out) - magnitude_db(lavfi_out))[audible])
        # Well under the ~0.5 dB a trained ear resolves on broadband material.
        self.assertLess(deviation, 0.1, f"EQ ports differ by {deviation:.3f} dB")


@unittest.skipUnless(shutil.which("mpv"), "mpv not on PATH")
class MpvAcceptsChainTests(unittest.TestCase):
    """mpv is the real runtime. Note these chains are NOT valid in plain
    ffmpeg -- rubberband is mpv's own filter -- so they must be checked here."""

    def _assert_mpv_accepts(self, chain):
        proc = subprocess.run(
            ["mpv", "--no-config", "--ao=null", "--vo=null", "--length=0.2",
             "--really-quiet", f"--af={chain}",
             "av://lavfi:sine=frequency=440:duration=1"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, f"{chain}\n{proc.stderr[:400]}")

    def test_full_chain_is_accepted(self):
        self._assert_mpv_accepts(M.build_af_chain(
            semitones=-1, normalize_gain_db=-5.1, eq_enabled=True,
            eq_gains_db=[3.0, 1.0, -2.0, 0.0, 0.0, 1.5, 0.0, 2.0, 4.0, -3.0],
            master_enabled=True,
            master_params=_host_master_params(comp_amount=70, tilt=40,
                                              exciter=60, exciter_on=True, gate=True),
        ))

    def test_extreme_settings_are_accepted(self):
        for gains in ([12.0] * 10, [-12.0] * 10):
            self._assert_mpv_accepts(
                M.build_af_chain(eq_enabled=True, eq_gains_db=gains))
        for semitones in (-12, 12):
            self._assert_mpv_accepts(M.build_af_chain(semitones=semitones))
        self._assert_mpv_accepts(M.build_af_chain(
            master_enabled=True,
            master_params=_host_master_params(comp_amount=100, tilt=-100,
                                              exciter=100, exciter_on=True,
                                              gate=True, ceiling=-6.0)))

    def test_each_stage_alone_is_accepted(self):
        for chain in (
            M.build_af_chain(normalize_gain_db=-5.1),
            M.build_af_chain(eq_enabled=True, eq_gains_db=[3.0] + [0.0] * 9),
            M.build_af_chain(semitones=2),
            M.build_af_chain(master_enabled=True, master_params=_host_master_params()),
            M.build_af_chain(master_enabled=True,
                             master_params=_host_master_params(limiter=False)),
        ):
            self._assert_mpv_accepts(chain)


if __name__ == "__main__":
    unittest.main()


class IinaBackendContractTests(unittest.TestCase):
    """The native IINA backend must satisfy the same contract as the stable
    mpv backend, or key/tempo/seek/EQ silently no-op behind hasattr guards."""

    def _plugin_methods(self):
        import ast
        with open("mpv_playback_iina.py", "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == "MpvPlaybackPlugin")
        return {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}

    def test_full_playback_contract_is_implemented(self):
        required = {
            # host -> plugin
            "attach", "audioDescription", "beginWindowTransition", "errorString",
            "loadSingWSMedia", "pauseMedia", "playMedia", "seekMedia",
            "setAudioProcessing", "setCdgOutputSidefill", "setExternalAudioMaster",
            "setTempoRatio", "setVideoStretch", "shutdown", "stopMedia", "version",
            "setBackgroundVideoOpacity",
            # transport -> plugin
            "setPitchSemitones", "positionMs", "durationMs", "isPlaying",
            "atEnd", "visualsReady", "setVolume", "setAudioDevice",
        }
        self.assertEqual(required - self._plugin_methods(), set())

    def test_dsp_chain_excludes_key(self):
        # The bridge composes key with the DSP chain. If the plugin also put
        # rubberband in the DSP half, pitch would be applied twice.
        with open("mpv_playback_iina.py", "r", encoding="utf-8") as fh:
            source = fh.read()
        block = source[source.index("def setAudioProcessing"):]
        block = block[:block.index("def setExternalAudioMaster")]
        self.assertIn("semitones=0", block)
        self.assertNotIn("self._semitones", block)

    def test_bridge_composes_rather_than_overwrites_af(self):
        # Regression guard: setSemitones: used to write "af" directly, wiping
        # the EQ and master bus on every key change.
        with open("native/mpv_bridge/bridge.mm", "r", encoding="utf-8") as fh:
            bridge = fh.read()
        # Slice the implementation, not the @interface declaration.
        setter = bridge[bridge.index("- (void)setSemitones:(int)semitones {"):]
        setter = setter[:setter.index("- (void)setDspChain:(const char *)chain {")]
        self.assertNotIn('mpv_set_property_string(_mpv,"af"', setter)
        self.assertIn("applyAudioFilters", setter)
        # And the DSP half must be re-applied after each file load. The
        # FILE_LOADED handler used to route through setSemitones:; it now calls
        # applyAudioFilters directly because the setters dispatch onto the
        # control queue and it is already running there.
        # Anchor on drainEvents: the standalone silence scanner has its own
        # FILE_LOADED branch earlier in the file.
        drain = bridge[bridge.index("- (void)drainEvents {"):]
        loaded = drain[drain.index("MPV_EVENT_FILE_LOADED"):]
        loaded = loaded[:loaded.index("MPV_EVENT_END_FILE")]
        self.assertIn("[self applyAudioFilters];", loaded)
