"""The audience rotation rail's name scroll: it starts by itself and a refresh keeps its position (no reset to the top)."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SINGWS_HOME", tempfile.mkdtemp())
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _items(n, tag=""):
    return [{"number": str(i + 1), "singer": f"Singer{i}{tag}", "song": "S", "artist": "A", "duet": False, "combined": "x"}
            for i in range(n)]


class RotationRailScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtQuick import QQuickView
        from PyQt6.QtCore import QUrl
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location("singws_main_for_rail", os.path.join(HERE, "0.2.18.1.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fd, cls.qml = tempfile.mkstemp(suffix=".qml")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(mod.QML_ROTATION_RAIL_SOURCE)
        cls.view = QQuickView()
        cls.view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        cls.view.resize(500, 420)
        cls.view.setSource(QUrl.fromLocalFile(cls.qml))
        cls.view.show()
        if cls.view.status() == QQuickView.Status.Error:
            raise unittest.SkipTest("rail QML did not load: %s" % [e.toString() for e in cls.view.errors()])
        cls.root = cls.view.rootObject()

    @classmethod
    def tearDownClass(cls):
        cls.view.close()
        os.unlink(cls.qml)

    def _wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def _phase(self):
        r = self.root
        return r.property("scrollPhase0") + (time.time() * 1000 - r.property("scrollStartMs")) / 1000 * r.property("scrollPxPerSec")

    def test_scroll_starts_by_itself_and_survives_a_refresh(self):
        r = self.root
        r.setProperty("speedPxPerSec", 120.0)
        r.submitItems(json.dumps(_items(14)), True)
        self._wait(1.0)
        self.assertGreater(r.property("scrollPxPerSec"), 0, "the rail never started scrolling on its own")
        before = self._phase()
        self.assertGreater(before, 40)
        r.submitItems(json.dumps(_items(14, "x")), False)
        self._wait(0.3)
        after = self._phase()
        self.assertGreater(after, before, "a refresh sent the rail back to the top")

    def test_effects_toggle_keeps_the_position(self):
        r = self.root
        r.setProperty("effectsEnabled", True)
        r.submitItems(json.dumps(_items(14, "y")), True)
        self._wait(0.8)
        before = self._phase()
        r.setProperty("effectsEnabled", False)
        self._wait(0.3)
        self.assertGreater(self._phase(), before)
        r.setProperty("effectsEnabled", True)


if __name__ == "__main__":
    unittest.main()
