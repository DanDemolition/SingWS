import unittest

from bass_background_engine import (
    BASS_ATTRIB_VOL,
    BASS_SLIDE_LOG,
    BassBackgroundEngine,
    _Deck,
)


class _FakeBass:
    def __init__(self):
        self.slides = []

    def BASS_ChannelSlideAttribute(self, handle, attrib, value, duration_ms):
        self.slides.append(
            (int(handle), int(attrib), float(value.value), int(duration_ms))
        )
        return 1


class BassFadeCurveTests(unittest.TestCase):
    def _engine(self):
        engine = BassBackgroundEngine.__new__(BassBackgroundEngine)
        engine.bass = _FakeBass()
        engine.mixer = 41
        engine.master_volume = 0.75
        engine.primary = None
        engine.secondary = None
        engine._closed = True
        self.addCleanup(setattr, engine, "mixer", 0)
        return engine

    def test_master_fade_uses_native_logarithmic_slide(self):
        engine = self._engine()
        engine.slide_master_volume(0.0, 3000)
        handle, attrib, target, duration_ms = engine.bass.slides[-1]
        self.assertEqual(handle, 41)
        self.assertEqual(attrib, BASS_ATTRIB_VOL | BASS_SLIDE_LOG)
        self.assertEqual(target, 0.0)
        self.assertEqual(duration_ms, 3000)

    def test_crossfade_decks_use_native_logarithmic_slide(self):
        engine = self._engine()
        deck = _Deck("track.mp3", 73, 1.0)
        engine._slide_deck_volume(deck, 0.0, 2500)
        handle, attrib, target, duration_ms = engine.bass.slides[-1]
        self.assertEqual(handle, 73)
        self.assertEqual(attrib, BASS_ATTRIB_VOL | BASS_SLIDE_LOG)
        self.assertEqual(target, 0.0)
        self.assertEqual(duration_ms, 2500)


if __name__ == "__main__":
    unittest.main()
