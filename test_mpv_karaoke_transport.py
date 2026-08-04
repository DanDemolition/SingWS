import unittest

from PyQt6.QtCore import QCoreApplication

from mpv_karaoke_transport import MpvKaraokeTransport


class _Plugin:
    def __init__(self):
        self.loaded = None
        self.position = 0
        self.duration = 180000
        self.playing = False
        self.ended = False
        self.volume = 1.0
        self.tempo = 1.0
        self.pitch = 0.0
        self.seeks = []

    def loadSingWSMedia(self, *args, **kwargs):
        self.loaded = (args, kwargs)
        self.playing = True
        return True

    def errorString(self): return ""
    def seekMedia(self, value): self.seeks.append(value); self.position = value
    def positionMs(self): return self.position
    def durationMs(self): return self.duration
    def isPlaying(self): return self.playing
    def visualsReady(self): return True
    def atEnd(self): return self.ended
    def stopMedia(self): self.playing = False
    def pauseMedia(self): self.playing = False
    def playMedia(self): self.playing = True
    def setVolume(self, value): self.volume = value
    def setTempoRatio(self, value): self.tempo = value
    def setPitchSemitones(self, value): self.pitch = value


class MpvTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def make_transport(self, mode="mp4"):
        plugin = _Plugin()
        transport = MpvKaraokeTransport(
            plugin,
            audio_path="song.mp3" if mode == "cdg" else "song.mp4",
            video_path="song.cdg" if mode == "cdg" else "song.mp4",
            mode=mode,
        )
        return plugin, transport

    def test_cdg_load_keeps_external_audio(self):
        plugin, transport = self.make_transport("cdg")
        transport.start(12.5)
        args, kwargs = plugin.loaded
        self.assertEqual(args, ("song.cdg", "song.mp3"))
        self.assertTrue(kwargs["autoplay"])
        self.assertEqual(plugin.seeks[-1], 12500)

    def test_live_key_and_tempo_are_independent(self):
        plugin, transport = self.make_transport()
        transport.set_modifiers(1.2, -3)
        self.assertEqual(plugin.tempo, 1.2)
        self.assertEqual(plugin.pitch, -3.0)

    def test_seek_pause_resume_and_timing_contract(self):
        plugin, transport = self.make_transport()
        transport.start()
        transport.seek(42.25)
        self.assertEqual(plugin.seeks[-1], 42250)
        transport.pause()
        self.assertTrue(transport.is_paused())
        transport.resume()
        self.assertFalse(transport.is_paused())
        duration, position = transport.query_times_ns()
        self.assertEqual(duration, 180_000_000_000)
        self.assertEqual(position, 42_250_000_000)

    def test_intro_loop_seeks_to_start(self):
        plugin, transport = self.make_transport()
        transport.start()
        transport.set_loop(10, 20)
        plugin.position = 20000
        transport._poll()
        self.assertEqual(plugin.seeks[-1], 10000)


if __name__ == "__main__":
    unittest.main()
