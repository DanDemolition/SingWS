"""The current singer's card pinned above the queue (new Show layout, setting ui_queue_pin_current, default on): display only."""
import unittest
from pathlib import Path

SRC = Path(__file__).with_name("0.2.18.1.py").read_text(encoding="utf-8")


class QueuePinCurrentTests(unittest.TestCase):
    def test_default_checkbox_and_hook(self):
        self.assertIn('"ui_queue_pin_current": True', SRC)
        self.assertIn('QCheckBox("Pin the current singer above the queue (new Show layout)")', SRC)
        self.assertIn("self._schedule_pinned_current_sync()", SRC)

    def test_it_never_reorders_or_edits_the_queue_model(self):
        body = SRC[SRC.index("    def _ensure_pinned_current_view"):SRC.index("    def _ui_redesign_on")]
        self.assertIn("qd.setRowHidden(r, True)", body)            # hidden rows keep their index
        self.assertIn("qd.setRowHidden(r, False)", body)           # and come back when off / when the singer changes
        main_model_writes = [l for l in body.splitlines() if "queue_display_model" in l and ("setRows" in l or "setData" in l or "_rows =" in l)]
        self.assertEqual(main_model_writes, [])                    # only the pinned copy's model is written
        self.assertIn("self.queue_pinned_model.setRows(", body)
        self.assertIn("WA_TransparentForMouseEvents", body)        # the pinned copy takes no clicks
        self.assertIn('self._ui_redesign_on() and self.settings.get("ui_queue_pin_current", True)', body)


if __name__ == "__main__":
    unittest.main()
