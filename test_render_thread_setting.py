"""Render-thread mode guards (2026-10-10). The real proof is tools/render_thread_probe.py, which needs a display."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class RenderThreadGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = (ROOT / "0.2.18.1.py").read_text()
        cls.bridge = (ROOT / "native" / "mpv_bridge" / "bridge.mm").read_text()

    def test_setting_is_on_by_default_and_reaches_the_bridge_through_the_environment(self):
        # Flipped to on 2026-10-10 at the operator's request, to rehearse it in the real app before any release.
        self.assertIn('"karaoke_render_thread": True', self.main)
        self.assertIn('self.settings.get("karaoke_render_thread", True)', self.main)
        self.assertIn('os.environ.setdefault("SINGWS_RENDER_THREAD", "1")', self.main)

    def test_bridge_defaults_to_the_main_queue(self):
        # With the mode off every dispatch that names _glQueue must be the old main-queue dispatch.
        self.assertIn("std::atomic<int> g_renderThreadRequested{0};", self.bridge)
        self.assertIn("_glQueue=dispatch_get_main_queue();", self.bridge)
        self.assertEqual(self.bridge.count("dispatch_async(dispatch_get_main_queue(),^{ self->_renderQueued"), 0)

    def test_every_frame_and_event_dispatch_uses_the_gl_queue(self):
        for needle in ("dispatch_async(_glQueue,^{ self->_renderQueued=false;",
                       "dispatch_async(_glQueue,^{ self->_eventQueued=false;"):
            self.assertIn(needle, self.bridge)

    def test_render_thread_mode_never_reads_appkit_state_when_drawing(self):
        i = self.bridge.index("- (void)presentViewNow:")
        body = self.bridge[i:self.bridge.index("- (void)shutdown {", i)]
        self.assertIn("if(!_renderThreadMode)[ctx update];", body)
        self.assertIn("view->backingW.load()", body)
        self.assertIn("view->presentable.load()", body)

    def test_probe_tool_ships_with_the_repo(self):
        self.assertTrue((ROOT / "tools" / "render_thread_probe.py").is_file())

    def test_exports_for_the_probe(self):
        for name in ("singws_bridge_set_render_thread", "singws_bridge_render_thread_active", "singws_bridge_present_count"):
            self.assertIn(name, self.bridge)


if __name__ == "__main__":
    unittest.main()
