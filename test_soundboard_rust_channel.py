"""Soundboard pads on the Rust engine: the pad channel the app uses (2026-10-11). Real engine, headless (no sound card), audio measured."""
import math
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import rust_bgm_engine as rbe
from soundboard_channels import RustSoundboardChannel, SoundboardChannel, SoundboardError

RATE = 48000
LIB_OK = rbe.available()


def tone(directory, name, hz=1000, amp=0.1, seconds=2.0, rate=RATE, channels=2):
    n = int(seconds * rate)
    mono = amp * np.sin(2 * np.pi * hz * np.arange(n) / rate)
    pcm = (np.repeat(mono[:, None], channels, axis=1) * 32767).astype("<i2")
    path = Path(directory) / name
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return str(path)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


@unittest.skipUnless(LIB_OK, "libsingws_bgm_ffi.dylib not built")
class RustPadChannelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.addCleanup(self.engine.close)
        self.clip = tone(self.tmp.name, "pad.wav")

    def channel(self, engine=None):
        eng = engine or self.engine
        ch = RustSoundboardChannel(lambda: eng)
        self.addCleanup(ch.close)
        return ch

    def level(self, frames=2400):
        return rms(self.engine.render(frames)[frames // 4:])

    def test_a_pad_plays_and_reports_state(self):
        ch = self.channel()
        ch.load(self.clip, 1.0)
        self.assertFalse(ch.is_playing())
        ch.play()
        self.assertTrue(ch.is_playing(), "a hit just made counts as playing at once")
        self.assertAlmostEqual(self.level(), 0.1 / math.sqrt(2), delta=0.01)
        time.sleep(0.06)
        self.assertTrue(ch.is_playing())
        self.assertGreater(ch.position_seconds(), 0.03)
        ch.stop()
        self.engine.render(1200)
        time.sleep(0.06)
        self.assertFalse(ch.is_playing())
        self.assertEqual(float(np.abs(self.engine.render(2400)).max()), 0.0)

    def test_hitting_a_playing_pad_layers_and_stop_silences_every_instance(self):
        ch = self.channel()
        ch.load(self.clip, 1.0)
        ch.play()
        self.engine.render(4800)
        ch.play()
        ch.play()
        one = 0.1 / math.sqrt(2)
        self.assertAlmostEqual(self.level(), 3 * one, delta=0.03, msg="three layered instances add")
        self.assertEqual(self.engine.pad_instances(ch._slot), 3)
        ch.stop()                           # what Shift-click does
        self.engine.render(1200)
        self.assertEqual(self.engine.pad_instances(ch._slot), 0, "every instance stopped")
        self.assertEqual(float(np.abs(self.engine.render(2400)).max()), 0.0)

    def test_two_pads_play_together_and_one_stop_leaves_the_other(self):
        a, b = self.channel(), self.channel()
        a.load(self.clip, 1.0)
        b.load(tone(self.tmp.name, "b.wav", hz=2000), 1.0)
        a.play()
        b.play()
        self.assertAlmostEqual(self.level(), math.sqrt(2) * 0.1 / math.sqrt(2), delta=0.02, msg="two uncorrelated tones")
        a.stop()
        self.engine.render(2400)
        self.assertAlmostEqual(self.level(), 0.1 / math.sqrt(2), delta=0.01)

    def test_pad_volume_and_the_soundboard_volume_scale_the_pad_but_the_music_volume_does_not(self):
        ch = self.channel()
        ch.load(self.clip, 1.0)
        ch.play()
        full = self.level()
        ch.set_volume(0.5)
        self.engine.render(480)
        self.assertAlmostEqual(self.level() / full, 0.5, delta=0.03)
        self.engine.set_soundboard_volume(0.5)
        self.engine.render(480)
        self.assertAlmostEqual(self.level() / full, 0.25, delta=0.03, msg="pad volume x soundboard volume")
        self.engine.set_master_volume(0.0)             # the MUSIC volume
        self.engine.slide_master_volume(0.0, 50)
        self.engine.render(4800)
        self.assertAlmostEqual(self.level() / full, 0.25, delta=0.03, msg="the music volume must not touch the pads")
        self.engine.set_soundboard_volume(0.0)
        self.engine.render(480)
        self.assertLess(self.level(), 1e-4)

    def test_pads_follow_the_eq_and_the_master_chain_like_the_music(self):
        from singws_eq import GraphicEQ
        from singws_master_audio import MasterAudioProcessor
        ch = self.channel()
        ch.load(tone(self.tmp.name, "k.wav", hz=1000, amp=0.05, seconds=6.0), 1.0)
        ch.play()
        flat = self.level()
        eq = GraphicEQ(sample_rate=RATE, channels=2)
        eq.set_all_gains_db([0, 0, 0, 0, 0, 12, 0, 0, 0, 0])
        eq.set_enabled(True)
        self.engine.set_eq(eq)
        self.engine.render(2400)
        self.assertAlmostEqual(20 * math.log10(self.level() / flat), 12.0, delta=0.6, msg="the +12 dB band applies to pads")
        self.engine.set_eq(None)
        hot = self.channel()
        hot.load(tone(self.tmp.name, "hot.wav", hz=220, amp=0.95, seconds=6.0), 1.0)
        hot.play()
        proc = MasterAudioProcessor(sample_rate=RATE, channels=2)
        proc.set_params({"limiter_enabled": 1.0, "limiter_ceiling_db": -12.0, "comp_enabled": 0.0, "eq_enabled": 0.0, "exciter_mix": 0.0})
        proc.set_enabled(True)
        self.engine.set_master_processor(proc)
        self.engine.render(RATE // 2)
        peak = float(np.abs(self.engine.render(RATE // 4)).max())
        self.assertLessEqual(peak, 10 ** (-12.0 / 20.0) + 0.01, "the limiter in the chain holds the pads too")

    def test_pads_sound_while_music_is_playing_and_stay_independent_of_its_fades(self):
        music = tone(self.tmp.name, "music.wav", hz=300, amp=0.3, seconds=20.0)
        self.engine.load(music, paused=False, volume=1.0)
        ch = self.channel()
        ch.load(self.clip, 1.0)
        self.engine.render(2400)
        music_only = self.level()
        ch.play()
        expected = math.sqrt(music_only ** 2 + (0.1 / math.sqrt(2)) ** 2)      # two uncorrelated tones add power
        self.assertAlmostEqual(self.level(), expected, delta=0.01)
        self.engine.slide_master_volume(0.0, 100)         # the music fades out for a karaoke song
        self.engine.render(RATE // 4)                       # the music is now silent; the first hit is still sounding
        self.assertAlmostEqual(self.level(), 0.1 / math.sqrt(2), delta=0.02, msg="only the pad is left, at full level")

    def test_a_changed_output_rebinds_the_pad_and_resumes_it(self):
        ch = self.channel()
        ch.load(self.clip, 1.0)
        ch.play()
        self.engine.render(4800)
        ch.prepare_output_change()
        new_engine = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.addCleanup(new_engine.close)
        ch._provider = lambda: new_engine
        ch.complete_output_change()
        out = new_engine.render(2400)
        self.assertGreater(rms(out[600:]), 0.05, "the pad plays on the new engine")
        self.assertIs(ch._runtime, new_engine)

    def test_errors_are_soundboard_errors_not_crashes(self):
        ch = self.channel()
        with self.assertRaises(SoundboardError):
            ch.load("/nonexistent/clip.wav", 1.0)
        with self.assertRaises(SoundboardError):
            ch.play()                                 # nothing loaded
        dead = RustSoundboardChannel(lambda: None)
        with self.assertRaises(SoundboardError):
            dead.load(self.clip, 1.0)

    def test_a_clip_over_five_minutes_is_refused_with_a_clear_message(self):
        long_clip = tone(self.tmp.name, "long.wav", hz=200, amp=0.01, seconds=301.0, rate=8000, channels=1)
        ch = self.channel()
        with self.assertRaises(SoundboardError) as cm:
            ch.load(long_clip, 1.0)
        self.assertIn("300", str(cm.exception))

    def test_a_44k_aac_style_clip_at_another_rate_is_resampled(self):
        clip = tone(self.tmp.name, "r44.wav", hz=1000, amp=0.1, seconds=2.0, rate=44100)
        ch = self.channel()
        ch.load(clip, 1.0)
        ch.play()
        self.assertAlmostEqual(self.level(), 0.1 / math.sqrt(2), delta=0.01)


class AdaptiveChannelTests(unittest.TestCase):
    @unittest.skipUnless(LIB_OK, "libsingws_bgm_ffi.dylib not built")
    def test_the_rust_engine_gets_the_layering_channel(self):
        eng = rbe.RustBackgroundEngine(headless=True, sample_rate=RATE)
        self.addCleanup(eng.close)
        ch = SoundboardChannel(lambda: eng)
        self.assertTrue(ch.supports_layering)
        self.assertIsInstance(ch._impl, RustSoundboardChannel)
        ch.close()

    def test_a_runtime_without_pad_support_raises_a_pad_error(self):
        class NoPads:
            _closed = False

        ch = SoundboardChannel(lambda: NoPads())
        with self.assertRaises(SoundboardError):
            ch.load("x.wav")
        self.assertEqual(ch.path, "")
        ch.stop()      # harmless with nothing loaded


if __name__ == "__main__":
    unittest.main()
