import unittest

from bass_soundboard_engine import BassSoundboardChannel, BassSoundboardError


class _NoBassRuntime:
    """What the Rust background engine exposes: a `bass` stub with no stream functions."""
    _closed = False

    class bass:  # noqa: N801
        @staticmethod
        def BASS_ChannelPlay(*_a):
            return 1


class SoundboardOnRustEngineTests(unittest.TestCase):
    def test_pad_press_without_real_bass_is_an_error_not_a_crash(self):
        ch = BassSoundboardChannel(lambda: _NoBassRuntime())
        with self.assertRaises(BassSoundboardError):
            ch.load("/nonexistent.wav", 1.0)


if __name__ == "__main__":
    unittest.main()
