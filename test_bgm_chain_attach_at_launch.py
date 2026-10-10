"""The BGM master chain / EQ must attach at launch, not only after a Settings toggle (fixed 2026-10-10).

The background player is built before the host's `settings` exist; its first attach therefore saw nothing. The host now repeats the
attach on the first turn of the event loop. These tests drive the real player method with a stand-in engine and host."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = Path(__file__).resolve().parent
os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="singws-chainattach-")


def _load():
    spec = importlib.util.spec_from_file_location("singws_main_chainattach", _ROOT / "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeEngine:
    def __init__(self):
        self.eq = "unset"
        self.master = "unset"
        self.diag = None

    def set_eq(self, eq):
        self.eq = eq

    def set_master_processor(self, proc):
        self.master = proc

    def set_audio_diagnostics(self, on):
        self.diag = on


class FakeHost:
    def __init__(self, settings=None, master_on=True):
        if settings is not None:
            self.settings = settings
        self.master_on = master_on
        self.bgm_eq = object()
        self.proc = object()

    def _bgm_master_active(self):
        if not hasattr(self, "settings"):
            raise AttributeError("settings")
        return self.master_on

    def _ensure_bgm_master_processor(self):
        return self.proc

    def _ensure_eq_engines(self):
        pass


class AttachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def _player(self, host):
        P = self.mod.BackgroundMusicPlayer
        p = P.__new__(P)
        p._bass_engine = FakeEngine()
        p.parent = lambda: host
        p._start_audio_diag_counters = lambda parent: setattr(p._bass_engine, "diag", True)
        return p

    def test_no_settings_yet_attaches_nothing(self):
        p = self._player(FakeHost(settings=None))
        p._attach_host_audio_chain()
        self.assertEqual(p._bass_engine.master, "unset")

    def test_repeat_after_settings_exist_attaches_master_chain(self):
        host = FakeHost(settings=None)
        p = self._player(host)
        p._attach_host_audio_chain()
        self.assertEqual(p._bass_engine.master, "unset")
        host.settings = {"simple_audio_mode": True}
        p._attach_host_audio_chain()
        self.assertIs(p._bass_engine.master, host.proc)
        self.assertTrue(p._bass_engine.diag)
        self.assertIsNone(p._bass_engine.eq)  # simple audio mode: EQ left out

    def test_advanced_mode_attaches_the_graphic_eq(self):
        host = FakeHost(settings={"simple_audio_mode": False})
        p = self._player(host)
        p._attach_host_audio_chain()
        self.assertIs(p._bass_engine.eq, host.bgm_eq)

    def test_master_off_detaches(self):
        host = FakeHost(settings={"simple_audio_mode": True}, master_on=False)
        p = self._player(host)
        p._attach_host_audio_chain()
        self.assertIsNone(p._bass_engine.master)

    def test_host_schedules_the_repeat_after_loading_settings(self):
        src = (_ROOT / "0.2.18.1.py").read_text()
        i = src.index("self.settings = self.load_settings()\n        # The background player was built above")
        self.assertIn("QTimer.singleShot(0, self._attach_bgm_chain_after_init)", src[i:i + 700])


if __name__ == "__main__":
    unittest.main()
