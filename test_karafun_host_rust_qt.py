"""karafun_web_host_rust.py with REAL Qt: the Chromium question is a real QMessageBox answered by clicking its button, work handed to the UI thread goes through a real
queued signal, and the picture-window code the adapter inherits (timer, placement, restacking) runs for real. The driver process and display are the same
scripted ones the other adapter tests use."""
import _test_profile  # noqa: F401
import os, stat, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

import karafun_web_host_rust as hr
from test_karafun_driver import BIN, FAKE
from test_karafun_web_host import FakeApp

_qapp = QApplication.instance() or QApplication([])


class QtApp(QWidget, FakeApp):
    _call_later = pyqtSignal(object)

    def __init__(self):
        QWidget.__init__(self)
        FakeApp.__init__(self)
        self._call_later.connect(lambda fn: fn(), type=__import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.QueuedConnection)

    def _run_on_ui_thread(self, fn):
        self._call_later.emit(fn)


def pump(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        _qapp.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


def _real_question(parent, title, text, buttons, default):
    """What QMessageBox.question really does (the shared test profile replaces it with a stub that never shows), with a safety close so a missed click cannot hang the run."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(buttons)
    box.setDefaultButton(default)
    safety = QTimer(box)                      # a timer object, not QTimer.singleShot (another test once left that one replaced for the whole process)
    safety.setSingleShot(True)
    safety.timeout.connect(box.reject)
    safety.start(15000)
    return QMessageBox.StandardButton(box.exec())


def click_dialog(label_text):
    """Find the open QMessageBox and click the button with this text, like the user."""
    timer = QTimer(_qapp)
    timer.setInterval(50)

    def go():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QMessageBox) and w.isVisible():
                for b in w.buttons():
                    if b.text().replace("&", "") == label_text:
                        timer.stop()
                        timer.deleteLater()
                        b.click()
                        return
    timer.timeout.connect(go)
    timer.start()


@unittest.skipUnless(BIN.exists(), "build the svc binary first (cargo build -p singws-karafun-svc)")
class RustHostQtTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="kfhostqt_")
        self.fake = os.path.join(d, "fake_display.sh")
        Path(self.fake).write_text(FAKE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.log = os.path.join(d, "display.log")
        Path(self.log).write_text("")
        os.environ["FAKE_LOG"] = self.log
        self.addCleanup(os.environ.pop, "FAKE_LOG", None)
        patcher = mock.patch.object(QMessageBox, "question", staticmethod(_real_question))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = QtApp()
        self.entry = {"artist": "NOFX", "title": "Linoleum", "provider_track_id": "kf_3241559", "duration": 240, "duration_estimated": True}
        self.app.entry = self.entry
        self.app._active_external_karafun = {"entry": self.entry}
        self.logs = []
        self.host = hr.KaraFunWebHostRust(self.app, diag=self.logs.append, svc_path=BIN, display_path=self.fake)
        self.addCleanup(self.host.shutdown)
        self.addCleanup(self.app.close)

    def _start_missing(self):
        os.environ["FAKE_MISSING"] = "1"
        self.addCleanup(os.environ.pop, "FAKE_MISSING", None)
        self.host._start_frames = mock.Mock()
        self.host._stop_frames = mock.Mock()
        self.assertTrue(self.host.start(self.entry, song_path="kf_3241559"))

    def test_warm_brings_the_player_up_and_signed_in_without_asking_for_a_song(self):
        """The pre-warm (a KaraFun request entered the queue): the real driver starts the service and the (scripted) player signs in; no song is ever asked."""
        self.assertIsNone(self.host.asked)
        self.assertTrue(self.host.warm())
        self.assertTrue(pump(lambda: self.host.service.state == "ready", 25), f"state={self.host.service.state} logs={self.logs[-6:]}")
        self.assertIsNone(self.host.asked)
        self.assertEqual([s for s in self.app.statuses if s[0] in ("playing", "failed")], [])
        sent = Path(self.log).read_text()
        self.assertNotIn('"cmd":"play"', sent.replace(" ", ""))          # nothing was played
        # asking again while it is ready changes nothing, and a song asked afterwards still starts normally
        self.assertTrue(self.host.warm())
        self.host._start_frames = mock.Mock()
        self.host._stop_frames = mock.Mock()
        self.assertTrue(self.host.start(self.entry, song_path="kf_3241559"))
        self.assertTrue(pump(lambda: any("started after" in line for line in self.logs), 25), f"logs={self.logs[-6:]}")      # the song starts normally on the warm player
        self.assertIsNotNone(self.host.asked)
        self.assertFalse(self.host.warm())                               # a song is asked now: nothing to warm

    def test_declining_the_chromium_download_in_the_real_dialog_fails_the_song_plainly(self):
        click_dialog("No")
        self._start_missing()
        self.assertTrue(pump(lambda: any(s[0] == "failed" for s in self.app.statuses)), f"statuses={self.app.statuses} logs={self.logs[-6:]}")
        self.assertIn("declined", self.entry.get("karafun_submission_error", ""))
        self.assertTrue(any(level == "error" and "Chromium" in text for level, text in self.app.notes))

    def test_accepting_downloads_once_then_asks_the_driver_to_start_again(self):
        click_dialog("Yes")
        retried = threading.Event()
        self.host._retry_service = retried.set
        with mock.patch.object(hr.karafun_web, "ensure_browser", return_value=Path("/x")) as ensure:
            self._start_missing()
            self.assertTrue(pump(retried.is_set), f"logs={self.logs[-6:]}")
        ensure.assert_called_once()
        self.assertTrue(ensure.call_args.kwargs.get("allow_download"))
        self.assertEqual([s for s in self.app.statuses if s[0] == "failed"], [])

    def test_a_failed_download_is_reported_on_the_ui_thread_not_lost(self):
        click_dialog("Yes")
        with mock.patch.object(hr.karafun_web, "ensure_browser", side_effect=RuntimeError("no network")):
            self._start_missing()
            self.assertTrue(pump(lambda: any(s[0] == "failed" for s in self.app.statuses)), f"logs={self.logs[-6:]}")
        self.assertIn("no network", self.entry.get("karafun_submission_error", ""))

    def test_the_picture_window_code_runs_with_a_real_timer_and_restacks_overlays(self):
        raised = []
        self.app.video_window = mock.Mock(_raise_ticker_surface=lambda: raised.append(1))
        self.app.video_window.screen.return_value = None
        self.app._set_mpv_hosts_visible = lambda v: None
        self.host._place_picture = mock.Mock()
        self.assertTrue(self.host.start(self.entry, song_path="kf_3241559"))
        self.assertTrue(pump(lambda: self.host._picture_timer is not None and self.host._picture_timer.isActive(), 25),
                        f"statuses={self.app.statuses} logs={self.logs[-6:]}")
        self.assertTrue(pump(lambda: self.host._place_picture.call_count >= 2, 5), "the timer keeps placing the picture")
        self.host._page_message({"type": "helper", "state": "window", "id": "3", "number": 77})
        self.assertEqual(self.host._picture_numbers.get("3"), 77)
        self.assertTrue(raised, "the ticker is raised above the picture")
        self.host.on_finish() if hasattr(self.host, "on_finish") else None
        self.assertTrue(pump(lambda: self.host._picture_timer is None or not self.host._picture_timer.isActive(), 10))


if __name__ == "__main__":
    unittest.main()
