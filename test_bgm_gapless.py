"""BGM scheduling plus real BASSmix rendering, without an audio output device."""
import ctypes
import importlib.util
from pathlib import Path
from types import MethodType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

import bass_background_engine as bass
import transition_analysis as ta


class NativeBackgroundCrossfadeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = self._tone("a.wav", 440, 0.5)
        self.b = self._tone("b.wav", 660, 0.25, lead=1.0)

        # Device zero and a decode-only mixer cannot play through the show's
        # speakers. Exercise the same shipped dylibs and deck methods.
        with patch.object(bass.BassBackgroundEngine, "_init_output"):
            self.engine = bass.BassBackgroundEngine()
        e = self.engine
        self.assertTrue(e.bass.BASS_Init(0, 48000, 0, None, None))
        self.addCleanup(e.bass.BASS_Free)
        self.addCleanup(e.close)
        e.play = Mock(return_value=True)
        e._ensure_mixer = lambda: self._ensure_decode_mixer(e)
        e.bass.BASS_ChannelGetData.argtypes = [bass.DWORD, ctypes.c_void_p, bass.DWORD]
        e.bass.BASS_ChannelGetData.restype = bass.DWORD
        e.load(self.a, volume=1.0)

    def _ensure_decode_mixer(self, e):
        if not e.mixer:
            e.mixer = e.mix.BASS_Mixer_StreamCreate(
                48000, 2, bass.BASS_SAMPLE_FLOAT | bass.BASS_STREAM_DECODE | bass.BASS_MIXER_NONSTOP,
            )
            self.assertTrue(e.mixer)

    def _tone(self, name, frequency, amplitude, lead=0.0, tail=0.0):
        samples = amplitude * np.sin(np.arange(48000 * 8) * (2 * np.pi * frequency / 48000))
        samples[:int(48000 * lead)] = 0.0
        if tail:
            samples[-int(48000 * tail):] = 0.0
        pcm = (np.repeat(samples[:, None], 2, axis=1) * 32767).astype("<i2")
        path = Path(self.tmp.name) / name
        with wave.open(str(path), "wb") as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(48000)
            out.writeframes(pcm.tobytes())
        return str(path)

    def render(self, seconds):
        e = self.engine
        data = np.empty((round(seconds * 48000), 2), dtype=np.float32)
        read = e.bass.BASS_ChannelGetData(e.mixer, data.ctypes.data, data.nbytes)
        self.assertEqual(read, data.nbytes)
        return data[:, 0]

    def test_preload_stays_at_start_and_equal_power_mix_has_no_hole(self):
        e = self.engine
        self.assertTrue(e.preload_secondary(self.b))
        prepared_handle = e.secondary.handle
        before = self.render(2.0)
        self.assertEqual(e.bass.BASS_ChannelGetPosition(prepared_handle, bass.BASS_POS_BYTE), 0)
        self.assertGreater(np.sqrt(np.mean(before ** 2)), 0.34)

        self.assertTrue(e.start_crossfade(self.b, 1000, norm_gain=2.0, start_seconds=1.0))
        self.assertEqual(e.secondary.handle, prepared_handle)
        self.assertFalse(e.crossfade_finished())
        mixed = self.render(0.5)
        self.assertFalse(e.crossfade_finished())
        mixed = np.concatenate([mixed, self.render(0.51)])
        self.assertTrue(e.crossfade_finished())
        # Distinct tones with matched normalized power should stay within
        # 0.4 dB of their steady level in every 50ms window, including midpoint.
        rms = np.sqrt(np.mean(mixed[:48000].reshape(-1, 2400) ** 2, axis=1))
        db = 20 * np.log10(rms / (0.5 / np.sqrt(2)))
        self.assertLess(np.max(np.abs(db)), 0.4)
        self.assertTrue(e.complete_crossfade())
        after = self.render(0.2)
        self.assertGreater(np.sqrt(np.mean(after ** 2)), 0.34)

    def test_worker_prepared_deck_does_not_consume_intro(self):
        e = self.engine
        prepared = e.prepare_primary(self.b)
        self.assertTrue(e.install_prepared_secondary(prepared))
        self.render(1.0)
        self.assertEqual(e.bass.BASS_ChannelGetPosition(prepared.handle, bass.BASS_POS_BYTE), 0)
        self.assertGreater(e.meter_level(), 0.0)

    def test_cancel_restores_outgoing_audio_and_removes_envelope(self):
        e = self.engine
        self.assertTrue(e.start_crossfade(self.b, 1000, start_seconds=1.0))
        self.render(0.5)
        e.cancel_crossfade()
        self.assertIsNone(e.secondary)
        self.assertFalse(e.crossfade_finished())
        samples = self.render(0.2)
        self.assertGreater(np.sqrt(np.mean(samples ** 2)), 0.34)

    def test_short_incoming_track_does_not_leave_crossfade_stuck(self):
        e = self.engine
        self.assertTrue(e.start_crossfade(self.b, 5000, start_seconds=7.75))
        self.render(0.3)
        self.assertTrue(e.crossfade_finished())
        self.assertTrue(e.complete_crossfade())

    def test_exhausted_outgoing_source_can_still_start_next_track(self):
        e = self.engine
        self.render(8.1)
        self.assertTrue(e.source_ended())
        self.assertTrue(e.start_crossfade(self.b, 100, start_seconds=1.0))
        self.render(0.2)
        self.assertTrue(e.crossfade_finished())
        self.assertTrue(e.complete_crossfade())

    def test_failed_incoming_file_preserves_current_deck(self):
        e = self.engine
        with self.assertRaises(bass.BassBackgroundError):
            e.start_crossfade(str(Path(self.tmp.name) / "missing.wav"), 1000)
        self.assertIsNone(e.secondary)
        self.assertGreater(np.sqrt(np.mean(self.render(0.5) ** 2)), 0.34)

    def test_partial_envelope_failure_restores_audio(self):
        e = self.engine
        native_set = e.mix.BASS_Mixer_ChannelSetEnvelope

        def fail_incoming(handle, kind, nodes, count):
            if count and handle == e.secondary.handle:
                return 0
            return native_set(handle, kind, nodes, count)

        with patch.object(e.mix, "BASS_Mixer_ChannelSetEnvelope", side_effect=fail_incoming):
            with self.assertRaises(bass.BassBackgroundError):
                e.start_crossfade(self.b, 1000, start_seconds=1.0)
        self.assertIsNone(e.secondary)
        self.assertGreater(np.sqrt(np.mean(self.render(0.5) ** 2)), 0.34)


class BackgroundTransitionSchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("singws_bgm_gapless", "0.2.18.1.py")
        cls.app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.app)

    def player(self, position=213.6, duration=240.0):
        player = self.app.BackgroundMusicPlayer.__new__(self.app.BackgroundMusicPlayer)
        self.app.QObject.__init__(player)
        player.playlist = ["old.wav", "new.wav"]
        player.current_index = 0
        player.is_playing = True
        player.crossfade_active = False
        player.crossfade_duration_ms = 5000
        player.stop_after_current = False
        player._bass_crossfade_generation = 1
        player._bass_ready = lambda: True
        player._bass_update_meter = lambda: None
        player._check_bg_tail_silence = lambda *_: False
        player._prepare_next_background_track = Mock()
        player._start_crossfade = Mock(return_value=True)
        player.parent = lambda: SimpleNamespace(settings={})
        player._bass_engine = SimpleNamespace(
            primary=object(), source_ended=lambda: False,
            get_times=lambda: (position, duration), crossfade_lead_seconds=lambda: 1.5,
        )
        return player

    def record(self):
        return ta.TransitionAnalysis(
            path="old.wav", mtime=1, size=1, media_kind="bgm", duration=240.0,
            audio_start=0.0, audio_end=220.0, hop_seconds=0.1,
        )

    def test_crossfade_starts_before_verified_silence_including_output_buffer(self):
        p = self.player()
        with patch.object(self.app, "transition_analysis_cached", return_value=self.record()):
            p._check_track_position()
        p._start_crossfade.assert_called_once()
        self.assertAlmostEqual(p._start_crossfade.call_args.kwargs["duration_ms"], 5000, delta=1)
        p._prepare_next_background_track.assert_called_once()

    def test_late_tick_shortens_fade_to_remaining_audio(self):
        p = self.player(position=218.0)
        with patch.object(self.app, "transition_analysis_cached", return_value=self.record()):
            p._check_track_position()
        self.assertAlmostEqual(p._start_crossfade.call_args.kwargs["duration_ms"], 600, delta=1)

    def test_missing_metadata_does_not_cut_an_unknown_outro(self):
        p = self.player()
        with patch.object(self.app, "transition_analysis_cached", return_value=None):
            p._check_track_position()
        p._start_crossfade.assert_not_called()

    def test_stop_after_current_does_not_preload_or_crossfade(self):
        p = self.player()
        p.stop_after_current = True
        p._check_track_position()
        p._prepare_next_background_track.assert_not_called()
        p._start_crossfade.assert_not_called()

    def test_manual_next_near_end_does_not_fade_through_dead_air(self):
        p = self.player(position=238.0)
        p._sync_volume_from_ui_or_settings = lambda *_: None
        p._bg_norm_factor_for_path = lambda _: (1.0, None)
        p._bg_dsp_chain_label = lambda *_: "test"
        p._bass_engine.start_crossfade = Mock(return_value=True)
        p._bass_engine.crossfade_finished = lambda: False
        with patch.object(self.app, "transition_analysis_cached", return_value=None):
            self.assertTrue(self.app.BackgroundMusicPlayer._start_crossfade(p, target_index=1))
        self.assertEqual(p._bass_engine.start_crossfade.call_args.args, ("new.wav", 500))

    def test_native_completion_waits_for_transport_and_does_not_run_while_paused(self):
        p = self.player()
        p.crossfade_active = True
        p._complete_crossfade = Mock()
        p._bass_engine.crossfade_finished = Mock(return_value=False)
        p._check_track_position()
        p._complete_crossfade.assert_not_called()
        p.is_playing = False
        p._bass_engine.crossfade_finished.return_value = True
        p._check_track_position()
        p._complete_crossfade.assert_not_called()
        p.is_playing = True
        p._check_track_position()
        p._complete_crossfade.assert_called_once_with(1)

    def test_preload_worker_discards_results_after_reorder_stop_or_new_engine(self):
        for change in ("reorder", "stop", "engine", "crossfade", "unchanged"):
            with self.subTest(change=change):
                p = self.player()
                finishes = []
                host = SimpleNamespace(_run_on_ui_thread=finishes.append)
                p.parent = lambda: host
                p._bg_norm_factor_for_path = lambda _: (1.0, None)
                engine = p._bass_engine
                prepared = object()
                engine.prepare_primary = Mock(return_value=prepared)
                engine.install_prepared_secondary = Mock()
                engine.invalidate_secondary_preload = Mock()
                engine.discard_prepared_primary = Mock()
                with patch.object(self.app.threading, "Thread") as thread:
                    # Use the real method instead of the scheduler fixture stub.
                    self.app.BackgroundMusicPlayer._prepare_next_background_track(p)
                    worker = thread.call_args.kwargs["target"]
                    worker()
                    if change == "reorder":
                        p.playlist[1] = "different.wav"
                    elif change == "stop":
                        engine.primary = None
                    elif change == "engine":
                        p._bass_engine = object()
                    elif change == "crossfade":
                        p.crossfade_active = True
                    finishes.pop()()
                if change == "unchanged":
                    engine.install_prepared_secondary.assert_called_once_with(prepared)
                    engine.discard_prepared_primary.assert_not_called()
                else:
                    engine.install_prepared_secondary.assert_not_called()
                    engine.discard_prepared_primary.assert_called_once_with(prepared)

    def test_player_and_native_mixer_cross_silent_padding_without_dead_air(self):
        fixture = NativeBackgroundCrossfadeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        engine = fixture.engine
        old = fixture._tone("padded.wav", 440, 0.5, tail=2.0)
        engine.load(old, volume=1.0)
        engine.preload_secondary(fixture.b)
        p = self.player()
        p.playlist = [old, fixture.b]
        p._bass_engine = engine
        p._sync_volume_from_ui_or_settings = lambda *_: None
        p._bg_norm_factor_for_path = lambda _: (2.0, None)
        p._bg_dsp_chain_label = lambda *_: "test"
        p._start_crossfade = MethodType(self.app.BackgroundMusicPlayer._start_crossfade, p)
        records = {
            old: ta.TransitionAnalysis(old, 1, 1, "bgm", 8.0, audio_start=0.0, audio_end=6.0),
            fixture.b: ta.TransitionAnalysis(fixture.b, 1, 1, "bgm", 8.0, audio_start=1.0, audio_end=8.0),
        }
        # Drive the production scheduler at its normal 500ms cadence. Native
        # envelopes continue processing between ticks without Qt gain updates.
        chunks = []
        with patch.object(self.app, "transition_analysis_cached", side_effect=records.get):
            while not p.crossfade_active:
                p._check_track_position()
                chunks.append(fixture.render(0.5))
                self.assertLess(len(chunks), 12)
            while not engine.crossfade_finished():
                chunks.append(fixture.render(0.5))
                self.assertLess(len(chunks), 20)
        engine.complete_crossfade()
        chunks.append(fixture.render(0.5))
        audio = np.concatenate(chunks)
        rms = np.sqrt(np.mean(audio.reshape(-1, 2400) ** 2, axis=1))
        self.assertGreater(rms.min(), 0.33)


if __name__ == "__main__":
    unittest.main()
