import unittest
from unittest import mock

from bass_soundboard_engine import BassSoundboardChannel, BassSoundboardError


class _FakeBass:
    def __init__(self):
        self.next_handle = 100
        self.created = []
        self.freed = []
        self.played = []
        self.stopped = []
        self.volumes = {}
        self.active = {}
        self.positions = {}

    def BASS_ErrorGetCode(self):
        return 0

    def BASS_StreamCreateFile(self, _mem, path, _offset, _length, flags):
        handle = self.next_handle
        self.next_handle += 1
        self.created.append((handle, path, flags))
        self.active[handle] = 0
        self.positions[handle] = 0
        return handle

    def BASS_StreamFree(self, handle):
        self.freed.append(handle)
        return 1

    def BASS_ChannelPlay(self, handle, restart):
        self.played.append((handle, bool(restart)))
        self.active[handle] = 1
        if restart:
            self.positions[handle] = 0
        return 1

    def BASS_ChannelStop(self, handle):
        self.stopped.append(handle)
        self.active[handle] = 0
        return 1

    def BASS_ChannelIsActive(self, handle):
        return self.active.get(handle, 0)

    def BASS_ChannelSetAttribute(self, handle, _attribute, value):
        self.volumes[handle] = float(value.value)
        return 1

    def BASS_ChannelGetPosition(self, handle, _mode):
        return self.positions.get(handle, 0)

    def BASS_ChannelSetPosition(self, handle, position, _mode):
        self.positions[handle] = int(position)
        return 1

    def BASS_ChannelSeconds2Bytes(self, _handle, seconds):
        return int(float(seconds) * 1000)

    def BASS_ChannelBytes2Seconds(self, _handle, position):
        return int(position) / 1000.0


class _Runtime:
    def __init__(self):
        self.bass = _FakeBass()
        self._closed = False


class _UnsupportedThenPcmBass(_FakeBass):
    def BASS_ErrorGetCode(self):
        return 41

    def BASS_StreamCreateFile(self, mem, path, offset, length, flags):
        if not bytes(path).endswith(b"cached.wav"):
            return 0
        return super().BASS_StreamCreateFile(mem, path, offset, length, flags)


class BassSoundboardChannelTests(unittest.TestCase):
    def test_load_preloads_and_repeated_hits_reuse_one_stream(self):
        runtime = _Runtime()
        channel = BassSoundboardChannel(lambda: runtime)

        channel.load("airhorn.wav", 0.7)
        for _ in range(100):
            channel.play()

        self.assertEqual(len(runtime.bass.created), 1)
        self.assertEqual(len(runtime.bass.played), 100)
        self.assertTrue(all(restart for _handle, restart in runtime.bass.played))
        self.assertAlmostEqual(runtime.bass.volumes[100], 0.7)

    def test_independent_channels_overlap_without_touching_bgm_mixer(self):
        runtime = _Runtime()
        first = BassSoundboardChannel(lambda: runtime)
        second = BassSoundboardChannel(lambda: runtime)
        first.load("one.wav")
        second.load("two.wav")

        first.play()
        second.play()

        self.assertTrue(first.is_playing())
        self.assertTrue(second.is_playing())
        self.assertEqual([call[0] for call in runtime.bass.played], [100, 101])
        self.assertFalse(hasattr(runtime.bass, "BASS_Mixer_StreamAddChannel"))

    def test_stop_keeps_preload_but_close_releases_it(self):
        runtime = _Runtime()
        channel = BassSoundboardChannel(lambda: runtime)
        channel.load("clip.wav")
        channel.play()

        channel.stop()
        channel.play()
        self.assertEqual(len(runtime.bass.created), 1)
        self.assertEqual(runtime.bass.freed, [])

        channel.close()
        self.assertEqual(runtime.bass.freed, [100])

    def test_output_change_releases_old_handle_and_resumes_on_new_runtime(self):
        current = [_Runtime()]
        channel = BassSoundboardChannel(lambda: current[0])
        channel.load("clip.wav")
        channel.play()
        current[0].bass.positions[100] = 2450
        old = current[0]

        channel.prepare_output_change()
        current[0] = _Runtime()
        channel.complete_output_change()

        self.assertEqual(old.bass.freed, [100])
        self.assertEqual(current[0].bass.positions[100], 2450)
        self.assertEqual(current[0].bass.played, [(100, False)])
        self.assertTrue(channel.is_playing())

    def test_unavailable_runtime_is_diagnostic_and_retryable(self):
        current = [None]
        channel = BassSoundboardChannel(lambda: current[0])
        with self.assertRaisesRegex(BassSoundboardError, "not available"):
            channel.load("clip.wav")

        current[0] = _Runtime()
        channel.play()
        self.assertTrue(channel.is_playing())

    def test_unsupported_source_is_predecoded_once_before_playback(self):
        runtime = _Runtime()
        runtime.bass = _UnsupportedThenPcmBass()
        channel = BassSoundboardChannel(lambda: runtime)

        with mock.patch.object(
            BassSoundboardChannel,
            "_pcm_cache_path",
            return_value="/tmp/cached.wav",
        ) as converter:
            channel.load("clip.m4a")
            channel.play()
            channel.play()

        converter.assert_called_once_with("clip.m4a")
        self.assertEqual(len(runtime.bass.created), 1)
        self.assertEqual(runtime.bass.created[0][1], b"/tmp/cached.wav")
        self.assertEqual(len(runtime.bass.played), 2)


if __name__ == "__main__":
    unittest.main()
