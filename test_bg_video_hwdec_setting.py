"""bg_video_hwdec guards: default stays software decode; only videotoolbox-copy reaches the Rust bridge."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class BgVideoHwdecGuards(unittest.TestCase):
    def test_default_is_software_and_setting_maps_to_the_environment(self):
        main = (ROOT / "0.2.18.1.py").read_text()
        self.assertIn('"bg_video_hwdec": "no"', main)
        self.assertIn('os.environ["SINGWS_BG_HWDEC"] = "videotoolbox-copy"', main)
        self.assertIn('os.environ.pop("SINGWS_BG_HWDEC", None)', main)

    def test_rust_bridge_accepts_only_the_copy_variant(self):
        src = (ROOT / "rust/crates/singws-video/src/render.rs").read_text()
        self.assertIn('Ok("videotoolbox-copy") => "videotoolbox-copy"', src)
        self.assertIn('_ => "no"', src)
        self.assertIn('("hwdec", bg_hwdec)', src)


if __name__ == "__main__":
    unittest.main()
