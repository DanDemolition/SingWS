import importlib.util
import unittest


def load_main_module():
    spec = importlib.util.spec_from_file_location("singws_main_rotation_lock", "0.2.18.1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_app(module, *, locked=True, mode="rotation"):
    app = module.KaraokeApp.__new__(module.KaraokeApp)
    app.settings = {"queue_mode": mode, "rotation_locked": locked, "rotation_lock_insert_count": 0}
    app.queue = []
    app.save_settings = lambda: None
    app._update_rotation_lock_button = lambda: None
    return app


def singer(name, *, active=True, round_sung=False, marker=False, has_sung=False, lock_new=None, order=None):
    s = {
        "name": name,
        "songs": [{"skipped": False}] if active else [],
        "skipped": False,
        "has_sung": has_sung,
        "round_sung": round_sung,
        "rotation_marker": marker,
    }
    if lock_new is not None:
        s["rotation_lock_new"] = lock_new
    if order is not None:
        s["rotation_lock_order"] = order
    return s


def names(queue):
    return [s["name"] for s in queue]


class RotationLockModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_main_module()

    def test_locked_only_when_rotation_mode(self):
        app = make_app(self.mod, locked=True, mode="classic")
        # Marker not at index 0 → the lock is meaningful (can_enable True).
        app.queue = [singer("A"), singer("M", active=False, marker=True)]
        self.assertFalse(app._is_rotation_locked())  # classic can't be locked
        app.settings["queue_mode"] = "rotation"
        self.assertTrue(app._is_rotation_locked())
        app.settings["rotation_locked"] = False
        self.assertFalse(app._is_rotation_locked())

    def test_lock_requires_marker_not_already_next(self):
        # Fix: locking is pointless when the yellow top-of-rotation singer is
        # already next (marker at index 0) — it must not count as locked.
        app = make_app(self.mod, locked=True, mode="rotation")
        app.queue = [singer("M", active=False, marker=True), singer("A")]
        self.assertFalse(app._rotation_lock_can_enable())
        self.assertFalse(app._is_rotation_locked())
        # Move the marker down a slot → lock becomes meaningful.
        app.queue = [singer("A"), singer("M", active=False, marker=True)]
        self.assertTrue(app._rotation_lock_can_enable())
        self.assertTrue(app._is_rotation_locked())


class RotationLockInsertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_main_module()

    def test_locked_newcomer_marked_and_behind_active(self):
        app = make_app(self.mod)
        app.queue = [singer("A"), singer("B")]  # two active, unsung, no marker
        app._rotation_insert_locked_new_singer(singer("C"))
        # newcomer tagged
        c = next(s for s in app.queue if s["name"] == "C")
        self.assertTrue(c.get("rotation_lock_new"))
        self.assertEqual(c.get("rotation_lock_order"), 0)
        self.assertEqual(app.settings["rotation_lock_insert_count"], 1)
        # active singers stay ahead of the locked newcomer
        self.assertEqual(names(app.queue)[0], "A")
        self.assertLess(names(app.queue).index("A"), names(app.queue).index("C"))
        self.assertLess(names(app.queue).index("B"), names(app.queue).index("C"))

    def test_two_locked_newcomers_keep_order(self):
        app = make_app(self.mod)
        app.queue = [singer("A"), singer("B")]
        app._rotation_insert_locked_new_singer(singer("C"))
        app._rotation_insert_locked_new_singer(singer("D"))
        self.assertEqual(app.settings["rotation_lock_insert_count"], 2)
        idx = names(app.queue)
        self.assertLess(idx.index("C"), idx.index("D"))  # order 0 before order 1

    def test_reweave_interleaves_after_marker(self):
        app = make_app(self.mod)
        # A active (current rotation), M = non-active next-rotation marker,
        # B = returning singer already sung this round, C = locked newcomer.
        app.queue = [
            singer("A"),
            singer("M", active=False, marker=True),
            singer("B", round_sung=True),
            singer("C", lock_new=True, order=0),
        ]
        app._rotation_reweave_locked_tail()
        order = names(app.queue)
        self.assertEqual(order[0], "A")              # current singer untouched at head
        self.assertEqual(order[1], "M")              # marker pinned at tail start
        self.assertLess(order.index("M"), order.index("C"))  # newcomer after marker
        # Fix: returning singers keep their slot — the locked newcomer is woven
        # in AFTER them, not ahead of them.
        self.assertLess(order.index("B"), order.index("C"))  # returner before newcomer

    def test_existing_active_singer_not_moved_by_reweave(self):
        app = make_app(self.mod)
        app.queue = [singer("A"), singer("B"), singer("C", lock_new=True, order=0)]
        app._rotation_reweave_locked_tail()
        # A and B (non-locked actives) keep their leading positions
        self.assertEqual(names(app.queue)[:2], ["A", "B"])


class RotationLockClearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_main_module()

    def test_clear_lock_removes_flags_and_settings(self):
        app = make_app(self.mod)
        app.queue = [singer("A"), singer("C", lock_new=True, order=0)]
        app.settings["rotation_lock_insert_count"] = 3
        app._rotation_clear_lock(save=False)
        self.assertFalse(app.settings["rotation_locked"])
        self.assertEqual(app.settings["rotation_lock_insert_count"], 0)
        for s in app.queue:
            self.assertNotIn("rotation_lock_new", s)
            self.assertNotIn("rotation_lock_order", s)
        self.assertFalse(app._is_rotation_locked())


if __name__ == "__main__":
    unittest.main()
