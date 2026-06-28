import importlib.util
import unittest


def load_main():
    spec = importlib.util.spec_from_file_location("singws_main_location_detection", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAIN = load_main()


class LocationDetectionTests(unittest.TestCase):
    def setUp(self):
        self.app = MAIN.KaraokeApp.__new__(MAIN.KaraokeApp)

    def test_corelocation_denied_error_is_user_friendly(self):
        msg = self.app._friendly_location_detection_error('Error Domain=kCLErrorDomain Code=1 "(null)"')

        self.assertIn("Location permission is denied for SingWS", msg)
        self.assertIn("System Settings", msg)
        self.assertNotIn("kCLErrorDomain", msg)
        self.assertNotIn("(null)", msg)

    def test_empty_location_error_uses_manual_coordinate_fallback(self):
        msg = self.app._friendly_location_detection_error("")

        self.assertIn("enter venue coordinates manually", msg)


if __name__ == "__main__":
    unittest.main()
