import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

_MOD = None


def app():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("singws_main_live_state", str(Path(__file__).with_name("0.2.18.1.py")))
        _MOD = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _MOD
        spec.loader.exec_module(_MOD)
    return _MOD


class LiveStateTests(unittest.TestCase):
    def setUp(self):
        self.mod = app()
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "live_state.json"
        self._old = self.mod.LIVE_STATE_PATH
        self.mod.LIVE_STATE_PATH = self.path

    def tearDown(self):
        self.mod.LIVE_STATE_PATH = self._old
        self.dir.cleanup()

    def host(self, **kw):
        h = types.SimpleNamespace(settings={"publish_live_state": True}, karaoke_playing=False, _current_karaoke_singer_id="",
                                  _current_karaoke_singer_display="", _current_karaoke_title="")
        for k, v in kw.items():
            setattr(h, k, v)
        return h

    def publish(self, h):
        self.mod.KaraokeApp._publish_live_state(h)

    def test_the_setting_is_on_by_default_but_a_profile_without_it_writes_nothing(self):
        self.assertTrue(self.mod.DEFAULTS["publish_live_state"])
        h = self.host(settings={}, karaoke_playing=True, _current_karaoke_singer_display="Dana")
        self.publish(h)
        self.assertFalse(self.path.exists())

    def test_a_playing_singer_is_published_with_the_pid(self):
        h = self.host(karaoke_playing=True, _current_karaoke_singer_id="s1", _current_karaoke_singer_display=" Dana ", _current_karaoke_title=" Song ")
        self.publish(h)
        d = json.loads(self.path.read_text())
        self.assertEqual((d["playing"], d["singer_id"], d["singer"], d["title"], d["pid"], d["version"]), (True, "s1", "Dana", "Song", os.getpid(), 1))
        self.assertGreater(d["updated_at"], 0)

    def test_idle_clears_the_id_and_title_but_keeps_the_last_singer_text_like_the_rotation_window(self):
        h = self.host(karaoke_playing=False, _current_karaoke_singer_id="s1", _current_karaoke_singer_display="Dana", _current_karaoke_title="T")
        self.publish(h)
        d = json.loads(self.path.read_text())
        self.assertEqual((d["playing"], d["singer_id"], d["title"], d["singer"]), (False, "", "", "Dana"))

    def test_unchanged_state_is_not_rewritten_and_a_change_is(self):
        h = self.host(karaoke_playing=True, _current_karaoke_singer_display="Dana")
        self.publish(h)
        first = self.path.stat().st_mtime_ns
        self.path.write_text("sentinel")
        self.publish(h)
        self.assertEqual(self.path.read_text(), "sentinel", "same state: no write")
        h._current_karaoke_singer_display = "Sam"
        self.publish(h)
        self.assertEqual(json.loads(self.path.read_text())["singer"], "Sam")
        self.assertIsNotNone(first)

    def test_a_failure_never_escapes_and_leaves_no_temp_file(self):
        self.mod.LIVE_STATE_PATH = Path(self.dir.name) / "no" / "such" / "dir" / "live_state.json"
        self.publish(self.host(karaoke_playing=True))  # must not raise
        self.assertEqual([p.name for p in Path(self.dir.name).iterdir()], [])
        h = types.SimpleNamespace()  # not even a settings attribute
        self.publish(h)

    def test_it_is_called_from_the_rotation_refresh_and_the_host_state_sync(self):
        src = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")
        i = src.index("    def update_rotation_view(self):")
        self.assertIn("self._publish_live_state()", src[i:i + 300])
        j = src.index("    def _sync_host_control_state_now(self):")
        self.assertIn("self._publish_live_state()", src[j:j + 300])


if __name__ == "__main__":
    unittest.main()
