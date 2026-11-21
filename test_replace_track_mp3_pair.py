"""Replace Track must not look for the .mp3 beside a .cdg on the GUI thread (1 to 3 s freeze on a slow library drive, Intel show logs)."""
import os
import re
import tempfile
import unittest

SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "0.2.18.1.py"), encoding="utf-8").read()


def _function(name):
    """The staticmethod, compiled on its own (the app module is too heavy to import here)."""
    m = re.search(r"    @staticmethod\n    def %s\(.*?(?=\n    def |\n    @)" % name, SRC, re.S)
    ns = {"os": os}
    exec("import os\nfrom pathlib import Path\n" + "\n".join(l[4:] for l in m.group(0).splitlines()[1:]), ns)
    return ns[name]


class Mp3Pair(unittest.TestCase):
    def test_lookup_finds_the_pair_including_other_case(self):
        find = _function("_find_mp3_pair_for_cdg_uncached")
        with tempfile.TemporaryDirectory() as d:
            for n in ("a.cdg", "a.mp3", "b.cdg", "B.Mp3", "c.cdg"):
                open(os.path.join(d, n), "w").close()
            self.assertTrue(find(os.path.join(d, "a.cdg")).endswith("a.mp3"))
            self.assertTrue(find(os.path.join(d, "b.cdg")).lower().endswith("b.mp3"))
            self.assertEqual(find(os.path.join(d, "c.cdg")), "")

    def test_the_dialog_pairs_on_a_worker_before_replacing(self):
        i = SRC.index("def apply_choice():", SRC.index("def open_replace_track_dialog"))
        body = SRC[i:i + 2200]
        self.assertLess(body.index("_Mp3PairRelay"), body.index("_replace_queue_song_with_track"))
        self.assertIn("threading.Thread", SRC[SRC.index("class _Mp3PairRelay"):][:900])


if __name__ == "__main__":
    unittest.main()
