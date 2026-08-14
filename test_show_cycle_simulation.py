import unittest
from pathlib import Path

from tools.show_cycle_simulation import run


class ShowCycleSimulationTests(unittest.TestCase):
    def test_cdg_mp4_karafun_cdg_cycle_with_rotation_and_static_cdg(self):
        result = run(Path(__file__).resolve().parent)
        self.assertTrue(result["ok"], result["contracts"])


if __name__ == "__main__":
    unittest.main()
