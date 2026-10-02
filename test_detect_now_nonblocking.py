"""Detect Now / OK in Network Settings must never wait for CoreLocation on the GUI thread.

2026-10-01 show: at the venue every detection fails after the full 25 s. Detect Now and OK both waited for it on the
GUI thread with user input held back, and the clicks made meanwhile were replayed afterwards, so the Network window
stayed dead for a couple of minutes."""
import unittest
from pathlib import Path

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def between(start, end, text=SOURCE):
    i = text.index(start)
    return text[i:text.index(end, i)]


class DetectNowTests(unittest.TestCase):
    def test_detect_now_runs_in_a_background_thread_one_at_a_time(self):
        body = between("def detect_location_now(", "update_location_status()\n\n            # Server Connectivity")
        self.assertIn('threading.Thread(target=worker, daemon=True, name="singws-detect-now").start()', body)
        self.assertIn('if detect_state["running"]:', body)               # repeat clicks are ignored, not queued
        self.assertIn("detect_now_btn.setEnabled(False)", body)
        self.assertIn("detect_now_btn.setEnabled(True)", body)
        self.assertIn("self._run_on_ui_thread(finish)", body)
        self.assertNotIn("loc, err = self._detect_current_device_location(timeout_sec=timeout_sec)\n                update_location_status", body)

    def test_the_result_is_shown_from_the_gui_thread_only_if_the_dialog_is_still_open(self):
        body = between("def detect_location_now(", "update_location_status()\n\n            # Server Connectivity")
        self.assertIn("if dlg.isVisible():", body)
        self.assertIn("except RuntimeError:", body)                      # widgets deleted after the dialog closed

    def test_ok_does_not_wait_for_a_detection(self):
        accept = between("# Save new values", "btns.accepted.connect(accept)")      # the Network dialog's OK handler
        self.assertNotIn("detect_location_now(", accept)
        self.assertNotIn("_detect_current_device_location(", accept)
        self.assertIn('self.sync_session_location_async("network_settings_saved")', accept)

    def test_only_the_gui_thread_pumps_qt_while_waiting(self):
        body = between("def _detect_current_device_location(", "manager.stopUpdatingLocation()")
        self.assertIn("threading.current_thread() is threading.main_thread()", body)

    def test_failure_message_keeps_the_saved_coordinates_in_view(self):
        body = between("def apply_detect_result(", "def detect_location_now(")
        self.assertIn("stay in use", body)
        self.assertIn("QMessageBox.warning(dlg, \"Location Detection Failed\", friendly_err)", body)


class FailureMessageTests(unittest.TestCase):
    def test_unknown_location_advice_points_at_wifi(self):
        body = between("def _friendly_location_detection_error(", "def _detect_current_device_location(")
        self.assertIn("Wi-Fi is turned on", body)
        self.assertNotIn("Try again near the venue", body)


if __name__ == "__main__":
    unittest.main()
