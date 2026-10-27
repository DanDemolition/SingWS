"""The rotation lock's tail reweave: the REAL `_rotation_reweave_locked_tail` against the Rust `singws-start::reweave` on random locked rotations (markers, singers with nothing to
sing, locked newcomers with every kind of signup order including ties and gaps)."""
import _test_profile  # noqa: F401  (isolate SINGWS_HOME)
import os
import random
import unittest
from pathlib import Path

import start_shadow
import test_show_critical_regressions as base

os.environ.setdefault("SINGWS_DSP_LIB", str(Path.home() / ".cache/singws-rust-target/release/libsingws_dsp_ffi.dylib"))


class ReweaveAgainstTheApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.singws = base.load_main_module()
        cls.shadow = start_shadow.StartShadow(lambda s: None)
        if not cls.shadow.available:
            raise unittest.SkipTest("libsingws_dsp_ffi.dylib with the start shadow is not built")

    def app(self, queue):
        app = self.singws.KaraokeApp.__new__(self.singws.KaraokeApp)
        app.queue = queue
        app.settings = {"queue_mode": "rotation", "rotation_locked": True}
        app._is_rotation_locked = lambda: True
        return app

    @staticmethod
    def random_queue(rng):
        queue = []
        orders = list(range(1, 12))
        rng.shuffle(orders)
        for i in range(rng.randint(0, 9)):
            songs = []
            for j in range(rng.randint(0, 2)):
                songs.append({"song_info": f"/l/{i}{j}.mp3", "skipped": rng.random() < 0.3})
            singer = {"name": f"S{i}", "songs": songs, "skipped": rng.random() < 0.15}
            if rng.random() < 0.2:
                singer["rotation_marker"] = True
            if rng.random() < 0.35:
                singer["rotation_lock_new"] = True
                singer["rotation_lock_order"] = rng.choice([orders[i % len(orders)], 3, 3, 0, None, "7", 2.9])
            queue.append(singer)
        return queue

    def test_random_locked_rotations_are_woven_the_same_way(self):
        rng = random.Random(20261026)
        moved = 0
        for case in range(1500):
            queue = self.random_queue(rng)
            before = list(queue)
            expected = self.shadow.reweave_order(queue)
            self.assertIsNotNone(expected, (case, "the shadow must be able to answer these queues"))
            app = self.app(queue)
            app._rotation_reweave_locked_tail()
            got = [next(i for i, s in enumerate(before) if s is x) for x in app.queue]
            self.assertEqual(got, expected, (case, [(s.get("rotation_marker"), s.get("rotation_lock_new"), s.get("rotation_lock_order"), len(s["songs"])) for s in before]))
            if got != list(range(len(before))):
                moved += 1
        self.assertGreater(moved, 100, "the generator must actually exercise reordering")

    def test_odd_queues_are_skipped_not_guessed(self):
        self.assertIsNone(start_shadow.reweave_facts([{"songs": [], "rotation_lock_order": "x"}]))
        self.assertIsNone(start_shadow.reweave_facts(["nope"]))


if __name__ == "__main__":
    unittest.main()
