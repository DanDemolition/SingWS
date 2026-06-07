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
            spec = Path(d) / "SingWS-universal.spec"
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
                              ("x86_64", b"B" * 2000),
                              ("universal", b"C" * 3000)):
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
            self.assertEqual(set(man["downloads"]), {"mac_arm64", "mac_x86_64", "mac_universal"})
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


if __name__ == "__main__":
    unittest.main()
