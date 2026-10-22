"""MpvKaraokeTransport driving the Rust karaoke voice next to mpv (roadmap item 3, step 4).

Deterministic: a fake mpv plugin and a fake voice, the transport's steps called by hand (no timers). The behaviours that matter on a show:
the picture and the voice start together, a seek moves both and restarts them together, a voice that cannot start hands the song back to mpv's own
audio, and nothing here changes how the transport behaves when no voice is attached."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from mpv_karaoke_transport import MpvKaraokeTransport

_APP = QApplication.instance() or QApplication([])


class FakePlugin:
    def __init__(self):
        self.calls = []
        self.pos_ms = 0
        self.dur_ms = 0
        self.playing = False
        self.at_end = False
        self.shadow = None
        self.load_ok = True

    def loadSingWSMedia(self, source, audio, autoplay=True, semitones=0, tempo_percent=100):
        self.calls.append(("load", source, audio, autoplay, semitones, tempo_percent))
        self.dur_ms = 200_000
        self.playing = bool(autoplay)
        return self.load_ok

    def errorString(self):
        return "boom"

    def seekMedia(self, ms):
        self.calls.append(("seek", ms)); self.pos_ms = ms

    def pauseMedia(self):
        self.calls.append(("pause",)); self.playing = False

    def playMedia(self):
        self.calls.append(("play",)); self.playing = True

    def stopMedia(self):
        self.calls.append(("stop",)); self.playing = False

    def setVolume(self, v):
        self.calls.append(("volume", v))

    def setTempoRatio(self, r):
        self.calls.append(("tempo", r))

    def setPitchSemitones(self, k):
        self.calls.append(("pitch", k))

    def setShadowAudio(self, on):
        self.shadow = bool(on); self.calls.append(("shadow", bool(on))); return True

    def isPlaying(self): return self.playing
    def positionMs(self): return self.pos_ms
    def durationMs(self): return self.dur_ms
    def visualsReady(self): return True
    def atEnd(self): return self.at_end


class FakeVoice:
    def __init__(self, load_error=None):
        self.calls = []
        self.ready = False
        self.pos = 0.0
        self.playing = False
        self.load_error = load_error

    def load(self, path, norm_gain=1.0, paused=True, volume=1.0):
        if self.load_error:
            raise RuntimeError(self.load_error)
        self.calls.append(("load", path, round(norm_gain, 3), paused, volume))

    def set_key_semitones(self, k): self.calls.append(("key", k))
    def set_tempo_percent(self, t): self.calls.append(("tempo", round(t, 3)))
    def seek_seconds(self, s): self.calls.append(("seek", s)); self.ready = False
    def play(self): self.calls.append(("play",)); self.playing = True
    def pause(self): self.calls.append(("pause",)); self.playing = False
    def stop(self): self.calls.append(("stop",)); self.playing = False
    def set_volume(self, v): self.calls.append(("volume", v))
    def slide_volume(self, v, ms): self.calls.append(("slide", v, ms))
    def set_norm_gain(self, g): self.calls.append(("norm", round(g, 3)))
    def set_eq_gains(self, on, g): self.calls.append(("eq", on, list(g)))
    def set_master_params(self, on, p): self.calls.append(("master", on, bool(p)))
    def slave_to(self, pos, rate): self.calls.append(("slave", round(pos, 3), rate))
    def is_ready(self): return self.ready
    def position_seconds(self): return self.pos


def make(voice, mode="cdg"):
    plugin = FakePlugin()
    t = MpvKaraokeTransport(plugin, audio_path="/tmp/song.mp3", video_path="/tmp/song.cdg", mode=mode)
    if voice is not None:
        t.attach_voice(voice, log=lambda *_: None)
    return t, plugin


class TransportVoiceTests(unittest.TestCase):
    def test_without_a_voice_nothing_changes(self):
        t, p = make(None)
        t.start(0.0)
        self.assertEqual(p.calls[0], ("load", "/tmp/song.cdg", "/tmp/song.mp3", True, 0, 100))   # autoplay, as before
        t.pause(); t.resume(); t.seek(12.5)
        self.assertEqual([c for c in p.calls[1:] if c[0] in ("pause", "play", "seek")], [("pause",), ("play",), ("seek", 12500)])
        t.stop()

    def test_the_voice_and_the_picture_start_together(self):
        v = FakeVoice()
        t, p = make(v)
        t.set_modifiers(0.9, 2.0)
        t.start(0.0)
        vload = [c for c in v.calls if c[0] == "load"][0]
        self.assertEqual(vload[:2], ("load", "/tmp/song.mp3"))
        self.assertTrue(vload[3], "the voice is loaded PAUSED")
        load = [c for c in p.calls if c[0] == "load"][0]
        self.assertFalse(load[3], "the picture is loaded without autoplay while the voice is starting")
        self.assertIn(("key", 2.0), v.calls)
        self.assertIn(("tempo", 90.0), v.calls)
        # not ready yet: nothing happens
        t._voice_step()
        self.assertNotIn(("play",), v.calls)
        # ready: the voice plays first, the picture waits for the voice's clock to move
        v.ready = True
        t._voice_step()
        self.assertIn(("play",), v.calls)
        self.assertNotIn(("play",), p.calls)
        self.assertEqual(t._voice_phase, "voice_wait")
        t._voice_step()
        self.assertNotIn(("play",), p.calls, "the voice has not produced sound yet")
        v.pos = 0.05
        t._voice_step()
        self.assertIn(("play",), p.calls)
        self.assertEqual(t._voice_phase, "running")
        # running: the picture's clock is reported to the voice
        p.pos_ms = 4200
        t._poll()
        self.assertIn(("slave", 4.2, 0.9), v.calls)

    def test_a_voice_that_cannot_open_the_song_hands_it_back_to_mpv_audio(self):
        v = FakeVoice(load_error="no decoder")
        t, p = make(v)
        t.start(0.0)
        load = [c for c in p.calls if c[0] == "load"][0]
        self.assertTrue(load[3], "mpv plays the song itself (autoplay) when the voice failed")
        self.assertFalse(p.shadow, "shadow audio must be switched off BEFORE the song loads")
        self.assertFalse(t.voice_active())

    def test_a_voice_that_never_gets_ready_is_dropped_and_mpv_plays(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        t._voice_deadline = 0.0           # the deadline has passed...
        t._voice_step()
        self.assertTrue(t.voice_active(), "...but the picture was only just seen loaded: the voice gets 3 s from then")
        t._voice_loaded_at = 0.001        # ...and the picture has been loaded for a while
        t._voice_step()
        self.assertFalse(t.voice_active())
        self.assertFalse(p.shadow)
        self.assertIn(("play",), p.calls, "mpv must start playing, with its own sound")

    def test_a_slow_picture_load_does_not_cost_the_voice_its_wait(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        p.dur_ms = 0                      # the picture has not loaded yet (a slow disk)
        t._voice_deadline = 0.0           # the old 4 s would be over
        t._voice_step()
        self.assertTrue(t.voice_active(), "an unloaded picture must not get the voice dropped")
        self.assertNotIn(("play",), p.calls)
        t._voice_hard_deadline = 0.0      # a picture that never loads is still given up on, eventually
        t._voice_step()
        self.assertFalse(t.voice_active())

    def test_a_seek_moves_both_and_restarts_them_together(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        v.ready = True; t._voice_step(); v.pos = 0.1; t._voice_step()
        self.assertEqual(t._voice_phase, "running")
        p.calls.clear(); v.calls.clear()
        t.seek(95.0)
        self.assertEqual(t._voice_phase, "seeking")
        self.assertEqual([c for c in p.calls if c[0] in ("pause", "seek")], [("pause",), ("seek", 95000)])
        self.assertIn(("pause",), v.calls)
        self.assertIn(("seek", 95.0), v.calls)
        self.assertFalse(t.is_paused(), "a coordinated seek is not a user-visible pause")
        t._voice_step()                 # the voice is not ready after its seek yet
        self.assertNotIn(("play",), v.calls)
        v.ready = True
        t._voice_step()
        self.assertIn(("play",), v.calls)
        v.pos = 95.2
        t._voice_origin = 95.0
        t._voice_step()
        self.assertIn(("play",), p.calls)
        self.assertEqual(t._voice_phase, "running")

    def test_pause_and_resume_drive_both(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        v.ready = True; t._voice_step(); v.pos = 0.1; t._voice_step()
        p.calls.clear(); v.calls.clear()
        t.pause()
        self.assertIn(("pause",), p.calls); self.assertIn(("pause",), v.calls)
        t.resume()
        self.assertIn(("play",), p.calls); self.assertIn(("play",), v.calls)

    def test_volume_fade_and_modifiers_reach_the_voice(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        t.set_volume(0.7)
        self.assertIn(("volume", 0.7), v.calls)
        t.set_modifiers(1.1, -3.0)
        self.assertIn(("key", -3.0), v.calls); self.assertIn(("tempo", 110.0), v.calls)
        self.assertNotIn(("pitch", -3.0), p.calls, "mpv must not also shift the pitch")
        done = []
        t.fade_out(800, on_finished=lambda: done.append(1))
        self.assertIn(("slide", 0.0, 800), v.calls)

    def test_the_audio_chain_goes_to_the_voice(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        t.apply_audio_spec({"normalize_gain_db": -6.0, "eq_enabled": True, "eq_gains_db": [1.0] * 10, "master_enabled": True, "master_params": {"a": 1.0}})
        self.assertIn(("norm", 0.501), v.calls)
        self.assertIn(("eq", True, [1.0] * 10), v.calls)
        self.assertIn(("master", True, True), v.calls)

    def test_the_song_ending_stops_the_voice(self):
        v = FakeVoice()
        t, p = make(v)
        t.start(0.0)
        v.ready = True; t._voice_step(); v.pos = 0.1; t._voice_step()
        v.calls.clear()
        p.at_end = True; p.pos_ms = 199_000
        ended = []
        t.ended.connect(lambda: ended.append(1))
        t._poll()
        self.assertEqual(ended, [1])
        self.assertIn(("stop",), v.calls)


if __name__ == "__main__":
    unittest.main()
