"""tools/make_karafun_helpers.sh: the five helper bundles Chromium needs when it runs inside an app bundle (without them KaraFun was a black screen)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VARIANTS = ["", " (Renderer)", " (GPU)", " (Plugin)", " (Alerts)"]


class HelperBundles(unittest.TestCase):
    def make_app(self, root):
        app = Path(root) / "SingWS.app"
        (app / "Contents" / "Frameworks").mkdir(parents=True)
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": "9.9.9", "CFBundleIdentifier": "com.singws.app"}, fh)
        frameworks = app / "Contents" / "Frameworks"
        (frameworks / "singws-karafun-display").write_text("#!/bin/sh\n")
        for lib in ("libEGL.dylib", "libGLESv2.dylib", "libvk_swiftshader.dylib", "vk_swiftshader_icd.json"):
            (frameworks / lib).write_text("x")
        return app

    def test_five_helper_bundles_with_their_programs_ids_and_graphics_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            subprocess.run([str(ROOT / "tools" / "make_karafun_helpers.sh"), str(app)], check=True, capture_output=True)
            ids = set()
            for variant in VARIANTS:
                name = "SingWS KaraFun Helper" + variant
                bundle = app / "Contents" / "Frameworks" / (name + ".app")
                self.assertTrue((bundle / "Contents" / "MacOS" / name).is_file(), name)
                info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
                self.assertEqual(info["CFBundleExecutable"], name)
                self.assertTrue(info["LSUIElement"])
                ids.add(info["CFBundleIdentifier"])
                for lib in ("libEGL.dylib", "libGLESv2.dylib", "libvk_swiftshader.dylib", "vk_swiftshader_icd.json"):
                    link = bundle / "Contents" / "MacOS" / lib
                    self.assertTrue(link.is_symlink() and link.resolve().is_file(), f"{name}/{lib} must reach the real file")
            self.assertEqual(len(ids), 5, "each helper has its own bundle id")

    def test_it_refuses_an_app_without_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.make_app(tmp)
            (app / "Contents" / "Frameworks" / "singws-karafun-display").unlink()
            result = subprocess.run([str(ROOT / "tools" / "make_karafun_helpers.sh"), str(app)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_both_installer_scripts_run_it_before_signing(self):
        for script in ("build_singws_mac_intel.sh", "build_singws_mac_arm64.sh"):
            text = (ROOT / script).read_text()
            self.assertLess(text.index("make_karafun_helpers.sh"), text.index("codesign --force --deep"), script)


if __name__ == "__main__":
    unittest.main()
