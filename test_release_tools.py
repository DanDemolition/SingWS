import importlib.util
import json
import unittest
from pathlib import Path


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rv = _load("release_version", "tools/release_version.py")
wm = _load("write_manifest", "tools/write_manifest.py")


class BumpTests(unittest.TestCase):
    def test_patch_increment(self):
        self.assertEqual(rv.bump_patch("0.2.18.1"), "0.2.18.2")
        self.assertEqual(rv.bump_patch("0.2.18.9"), "0.2.18.10")
        self.assertEqual(rv.bump_patch("1.2.3"), "1.2.4")
        self.assertEqual(rv.bump_patch("0.3.0"), "0.3.1")

    def test_write_version_updates_entry_and_specs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            entry = Path(d) / "0.2.18.1.py"
            entry.write_text('APP_VERSION = "0.2.18.1"\nprint("hi")\n')
            spec = Path(d) / "SingWS-x86_64.spec"
            spec.write_text(
                "info_plist={\n"
                "    'CFBundleShortVersionString': '0.2.18.1',\n"
                "    'CFBundleVersion': '0.2.18.1',\n"
                "}\n"
            )
            rv.write_version("0.3.0", entry=entry, specs=[spec])
            self.assertEqual(rv.read_version(entry), "0.3.0")
            txt = spec.read_text()
            self.assertIn("'CFBundleShortVersionString': '0.3.0'", txt)
            self.assertIn("'CFBundleVersion': '0.3.0'", txt)

    def test_write_version_rejects_garbage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            entry = Path(d) / "0.2.18.1.py"
            entry.write_text('APP_VERSION = "0.2.18.1"\n')
            with self.assertRaises(SystemExit):
                rv.write_version("; rm -rf /", entry=entry, specs=[])


class ManifestTests(unittest.TestCase):
    def _fake_dmgs(self, d: Path, version: str):
        for arch, content in (("arm64", b"A" * 1000),
                              ("x86_64", b"B" * 2000)):
            (d / f"SingWS-{version}-{arch}-installer.dmg").write_bytes(content)

    def test_build_manifest_structure_and_hashes(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            self._fake_dmgs(d, "0.3.0")
            man = wm.build_manifest("v0.3.0", d, release_date="2026-06-07")
            self.assertEqual(man["version"], "0.3.0")  # 'v' stripped
            self.assertEqual(man["release_date"], "2026-06-07")
            self.assertEqual(set(man["downloads"]), {"mac_arm64", "mac_x86_64"})
            arm = man["downloads"]["mac_arm64"]
            self.assertEqual(arm["filename"], "SingWS-0.3.0-arm64-installer.dmg")
            self.assertIn("releases/latest/download/SingWS-0.3.0-arm64-installer.dmg", arm["url"])
            self.assertEqual(arm["sha256"], hashlib.sha256(b"A" * 1000).hexdigest())
            # JSON-serializable.
            json.dumps(man)

    def test_build_manifest_errors_on_missing_dmg(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                wm.build_manifest("0.9.9", Path(tmp))

    def test_build_manifest_allows_intel_only_release(self):
        # arm64 cannot be cross-built on an Intel host (no universal2
        # numpy/scipy for py3.14), so a release may ship Intel-only.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "SingWS-0.3.0-x86_64-installer.dmg").write_bytes(b"B" * 2000)
            man = wm.build_manifest("0.3.0", d)
            self.assertEqual(set(man["downloads"]), {"mac_x86_64"})

    def test_build_manifest_still_requires_intel(self):
        # A missing Intel DMG means the local build failed; that must be fatal
        # rather than quietly publishing an arm64-only manifest.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "SingWS-0.3.0-arm64-installer.dmg").write_bytes(b"A" * 1000)
            with self.assertRaises(SystemExit):
                wm.build_manifest("0.3.0", d)


class UpdateManifestDefaultsTests(unittest.TestCase):
    def test_default_update_manifest_uses_live_raw_github_url(self):
        source = Path("0.2.18.1.py").read_text(encoding="utf-8")
        expected = "https://raw.githubusercontent.com/DanDemolition/SingWS/main/docs/release.json"
        self.assertIn(f'"auto_update_manifest_url": "{expected}"', source)
        self.assertIn(f'update_manifest_edit.setText("{expected}")', source)
        self.assertNotIn("https://dandemolition.github.io/SingWS/release.json", source)


class PackagingSpecTests(unittest.TestCase):
    def test_intel_release_defaults_to_macos12_iina_stack(self):
        spec = Path("SingWS-x86_64.spec").read_text(encoding="utf-8")
        build = Path("build_singws_mac_intel.sh").read_text(encoding="utf-8")
        self.assertIn("Required bundled native mpv bridge/runtime is missing", spec)
        self.assertIn("native/mpv_bridge/libsingws_mpv_bridge.dylib", build)
        self.assertNotIn("SINGWS_MEDIA_STACK", spec + build)
        self.assertIn("'LSMinimumSystemVersion': '12.0'", spec)
        self.assertIn("--maximum 12.0", build)

    def test_specs_bundle_no_gstreamer_and_exclude_gi(self):
        # GStreamer removal: specs must not set up a GST_REGISTRY, bundle the
        # plugin scanner/typelibs/framework, and must exclude gi so PyInstaller
        # cannot pull GStreamer back in transitively.
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                # Matched on the list contents, not the whole assignment: the
                # specs legitimately differ in how they build the rest of the
                # list (arm64 appends 'mpv' conditionally, x86_64 wraps the
                # expression in parens), and asserting the exact literal made
                # this fail on formatting rather than on gi being importable.
                self.assertIn("excludes=", source)
                self.assertIn("'gi', 'gi.repository'", source)
                self.assertNotIn("GST_REGISTRY", source)
                self.assertNotIn("gst-plugin-scanner", source)
                self.assertNotIn("gi_typelibs", source)
                self.assertNotIn('binaries.append((str(plug), "gst_plugins"))', source)

    def test_specs_do_not_bundle_legacy_media_executables(self):
        self.assertFalse(Path("singws_pyinstaller_runtime.py").exists())
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            source = Path(spec).read_text(encoding="utf-8")
            self.assertNotIn('for ff_binary in ("ffmpeg", "ffprobe")', source)
            self.assertIn("'libmpv_media_jobs'", source)
            self.assertIn('"libmpv_background_engine.py"', source)

    def test_apple_silicon_package_matches_permanent_native_stack(self):
        spec = Path("SingWS-arm64.spec").read_text(encoding="utf-8")
        build = Path("build_singws_mac_arm64.sh").read_text(encoding="utf-8")
        self.assertIn("target_arch='arm64'", spec)
        self.assertIn('"arm64" in result.stdout.split()', spec)
        self.assertIn("--runtime --require arm64", build)
        self.assertIn("--bundle \"$APP_PATH\" --require arm64", build)
        self.assertIn("libsingws_mpv_bridge.dylib", spec + build)
        self.assertIn("singws_libmpv.2.dylib", spec + build)
        self.assertNotIn("mpv_playback.py", spec + build)
        self.assertNotIn("python_karaoke_transport", spec + build)

    def test_release_specs_include_karafun_apple_events_authorization(self):
        entitlements = Path("SingWS.entitlements").read_text(encoding="utf-8")
        self.assertIn("com.apple.security.automation.apple-events", entitlements)
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                self.assertIn("NSAppleEventsUsageDescription", source)
                self.assertIn("entitlements_file=str(project_root / 'SingWS.entitlements')", source)

    def test_release_specs_bundle_requests_tls_support(self):
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                self.assertIn("project_root = Path(SPECPATH)", source)
                for module in ("'ssl'", "'_ssl'", "'_hashlib'", "'certifi'", "'urllib3.util.ssl_'"):
                    self.assertIn(module, source)
                for dylib in ('"libssl.3.dylib"', '"libcrypto.3.dylib"'):
                    self.assertIn(dylib, source)

    def test_release_specs_bundle_required_qt_plugins(self):
        required_groups = (
            '"platforms"',
            '"multimedia"',
            '"networkinformation"',
            '"tls"',
        )
        for spec in ("SingWS-arm64.spec", "SingWS-x86_64.spec"):
            with self.subTest(spec=spec):
                source = Path(spec).read_text(encoding="utf-8")
                self.assertIn('qt_plugins_root = (', source)
                self.assertIn('f"PyQt6/Qt6/plugins/{plugin_group}"', source)
                self.assertIn("Required Qt plugin group is missing", source)
                for group in required_groups:
                    self.assertIn(group, source)

    def test_release_verifier_requires_cocoa_platform_plugin(self):
        verifier = Path("tools/verify_macos_arch.py").read_text(encoding="utf-8")
        self.assertIn('"libqcocoa.dylib"', verifier)

if __name__ == "__main__":
    unittest.main()
