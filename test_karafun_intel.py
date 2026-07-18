import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_karafun_intel", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KaraFunIntelRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = load_main_module()

    def test_minus_1743_is_reported_as_apple_events_not_accessibility(self):
        error = (
            'NSAppleScriptErrorBriefMessage = "Not authorized to send Apple events to System Events."; '
            'NSAppleScriptErrorNumber = "-1743";'
        )
        self.assertTrue(self.singws.KaraokeApp._is_karafun_apple_events_error(error))
        self.assertFalse(self.singws.KaraokeApp._is_karafun_accessibility_error(error))

    def test_preflight_preserves_full_apple_events_error(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        full_error = "Not authorized to send Apple events to System Events (-1743)"
        app._run_karafun_applescript_sync = mock.Mock(return_value=(False, "", full_error))

        ok, message = app._karafun_apple_events_preflight()

        self.assertFalse(ok)
        self.assertEqual(message, full_error)

    def test_launch_uses_absolute_system_open_and_native_app_not_browser(self):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.settings = {"karafun_auto_queue_enabled": True}
        entry = {"provider_url": "https://www.karafun.com/karaoke/artist/song/"}
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(self.singws.sys, "platform", "darwin"),
            mock.patch.object(
                self.singws.KaraokeApp,
                "_karafun_application_path",
                return_value=Path("/Applications/KaraFun.app"),
            ),
            mock.patch.object(self.singws.subprocess, "run", return_value=completed) as run,
            mock.patch.object(self.singws.QDesktopServices, "openUrl") as browser,
        ):
            self.assertTrue(app._open_karafun_for_entry(entry))

        run.assert_called_once_with(
            ["/usr/bin/open", "/Applications/KaraFun.app"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        browser.assert_not_called()

    def test_all_macos_packages_declare_and_entitle_apple_events(self):
        entitlement = Path("SingWS.entitlements").read_text(encoding="utf-8")
        self.assertIn("com.apple.security.automation.apple-events", entitlement)
        for spec_name in ("SingWS-arm64.spec", "SingWS-x86_64.spec", "SingWS-universal.spec"):
            with self.subTest(spec=spec_name):
                source = Path(spec_name).read_text(encoding="utf-8")
                self.assertIn("NSAppleEventsUsageDescription", source)
                self.assertIn("SingWS.entitlements", source)

    def test_intel_spec_rejects_arm_only_dependencies(self):
        source = Path("SingWS-x86_64.spec").read_text(encoding="utf-8")
        self.assertIn("target_arch='x86_64'", source)
        self.assertIn("def _keep_intel_binary", source)
        self.assertIn('"x86_64" in result.stdout.split()', source)


if __name__ == "__main__":
    unittest.main()
