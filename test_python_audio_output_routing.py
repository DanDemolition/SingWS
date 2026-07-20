"""Regression coverage for Qt output routing in the fallback karaoke engine."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock


os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import python_karaoke_transport as transport_module


class FakeDevice:
    def __init__(self, name: str, *, null: bool = False):
        self.name = name
        self.null = bool(null)

    def description(self):
        return self.name

    def isNull(self):
        return self.null


class DeviceNameMatchingTests(unittest.TestCase):
    def test_exact_normalized_name_match(self):
        wanted = FakeDevice("USB Audio CODEC")
        devices = [FakeDevice("MacBook Pro Speakers"), wanted]
        match = transport_module.match_qt_audio_device(devices, "USB-Audio Codec")
        self.assertIs(match, wanted)

    def test_unique_containment_match(self):
        wanted = FakeDevice("BlackHole 2ch (Core Audio)")
        match = transport_module.match_qt_audio_device([wanted], "BlackHole 2ch")
        self.assertIs(match, wanted)

    def test_ambiguous_containment_does_not_guess(self):
        devices = [FakeDevice("USB Audio Left"), FakeDevice("USB Audio Right")]
        self.assertIsNone(transport_module.match_qt_audio_device(devices, "USB Audio"))


class FallbackSinkRoutingTests(unittest.TestCase):
    def test_selected_device_is_passed_to_qaudio_sink(self):
        device = FakeDevice("USB Audio CODEC")
        created = []

        class FakeSink:
            def __init__(self, *args):
                created.append(args)

            def setBufferSize(self, _size):
                pass

            def start(self, _feeder):
                pass

        transport = transport_module.PythonKaraokeTransport(
            "/tmp/not-decoded-here.wav",
            audio_device=device,
            audio_device_name=device.description(),
        )
        with mock.patch.object(transport_module, "QAudioSink", FakeSink):
            transport._ensure_sink_running()

        self.assertEqual(len(created), 1)
        self.assertIs(created[0][0], device)
        self.assertEqual(transport.audio_device_name, "USB Audio CODEC")
        transport._feeder.close()

    def test_null_device_uses_qt_default_overload(self):
        device = FakeDevice("Missing", null=True)
        created = []

        class FakeSink:
            def __init__(self, *args):
                created.append(args)

            def setBufferSize(self, _size):
                pass

            def start(self, _feeder):
                pass

        transport = transport_module.PythonKaraokeTransport(
            "/tmp/not-decoded-here.wav",
            audio_device=device,
            audio_device_name=device.description(),
        )
        with mock.patch.object(transport_module, "QAudioSink", FakeSink):
            transport._ensure_sink_running()

        self.assertEqual(len(created), 1)
        self.assertIsNot(created[0][0], device)
        transport._feeder.close()


class HostDeviceAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "singws_qt_audio_routing",
            Path(__file__).resolve().parent / "0.2.18.1.py",
        )
        cls.singws = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.singws)

    def test_host_maps_existing_gstreamer_selection_by_display_name(self):
        selected_device = FakeDevice("USB Audio CODEC")
        default_device = FakeDevice("MacBook Pro Speakers")

        class FakeMediaDevices:
            @staticmethod
            def defaultAudioOutput():
                return default_device

            @staticmethod
            def audioOutputs():
                return [default_device, selected_device]

        owner = type("Owner", (), {})()
        owner._get_selected_audio_output_id = lambda: "dev_selected"
        owner._audio_output_cache = [
            {"id": "default", "name": "Default (System)"},
            {"id": "dev_selected", "name": "USB-Audio Codec"},
        ]
        owner._refresh_audio_output_cache = lambda: owner._audio_output_cache

        with mock.patch("PyQt6.QtMultimedia.QMediaDevices", FakeMediaDevices):
            device, name = self.singws.KaraokeApp._qt_audio_device_for_selected_output(owner)

        self.assertIs(device, selected_device)
        self.assertEqual(name, "USB-Audio Codec")

    def test_unmatched_selection_falls_back_to_qt_default(self):
        default_device = FakeDevice("MacBook Pro Speakers")

        class FakeMediaDevices:
            @staticmethod
            def defaultAudioOutput():
                return default_device

            @staticmethod
            def audioOutputs():
                return [default_device]

        owner = type("Owner", (), {})()
        owner._get_selected_audio_output_id = lambda: "dev_missing"
        owner._audio_output_cache = [{"id": "dev_missing", "name": "Disconnected USB Interface"}]
        owner._refresh_audio_output_cache = lambda: owner._audio_output_cache

        with mock.patch("PyQt6.QtMultimedia.QMediaDevices", FakeMediaDevices):
            device, name = self.singws.KaraokeApp._qt_audio_device_for_selected_output(owner)

        self.assertIs(device, default_device)
        self.assertEqual(name, "MacBook Pro Speakers")


if __name__ == "__main__":
    unittest.main()
