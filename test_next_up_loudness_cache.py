"""Next-up loudness: a ZIP song whose measurement is already stored (library scan, keyed by the stable ZIP) must not be measured again
just because playback uses a temporary extracted MP3 whose path changes every session."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path("0.2.18.1.py").read_text(encoding="utf-8")


def host(cache, archive_map):
    tree = ast.parse(SOURCE)
    fn = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "_next_up_loudness_cached":
                    sub.decorator_list = []
                    fn = sub
    ns = {"loudness_gain_db_cached": lambda p: cache.get(p)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "next-up-loudness", "exec"), ns)
    obj = SimpleNamespace(zip_cache=SimpleNamespace(archive_for_extracted_path=lambda p: archive_map.get(p, "")))
    obj._next_up_loudness_cached = lambda *a, **k: ns["_next_up_loudness_cached"](obj, *a, **k)
    return obj


class NextUpLoudnessCacheTests(unittest.TestCase):
    TEMP = "/tmp/singws_mp3g_abc/ff00_extracted.mp3"
    ZIP = "/Music/Karaoke/Library/CC - Green Day - Basket Case.zip"

    def test_a_measurement_stored_under_the_zip_covers_the_temporary_mp3(self):
        h = host({self.ZIP: -3.1}, {})
        self.assertTrue(h._next_up_loudness_cached(self.TEMP, self.ZIP))

    def test_a_measurement_found_by_resolving_the_temporary_mp3_back_to_its_archive_counts(self):
        h = host({self.ZIP: -3.1}, {self.TEMP: self.ZIP})
        self.assertTrue(h._next_up_loudness_cached(self.TEMP, ""))

    def test_the_temporary_path_itself_still_counts(self):
        self.assertTrue(host({self.TEMP: -2.0}, {})._next_up_loudness_cached(self.TEMP, self.ZIP))

    def test_nothing_stored_means_it_still_needs_measuring(self):
        self.assertFalse(host({}, {self.TEMP: self.ZIP})._next_up_loudness_cached(self.TEMP, self.ZIP))
        self.assertFalse(host({}, {})._next_up_loudness_cached(self.TEMP, ""))

    def test_the_prescan_worker_uses_it_before_queueing_an_analysis(self):
        start = SOURCE.index("    def _prescan_next_track(")
        worker = SOURCE[start:SOURCE.index("    def _schedule_next_up_prescan(")]
        self.assertIn("self._next_up_loudness_cached(resolved, scan_path)", worker)
        self.assertLess(worker.index("self._next_up_loudness_cached(resolved, scan_path)"), worker.index("analyze_loudness_async(resolved)"))
        self.assertIn("reason=library_cache_hit", worker)


if __name__ == "__main__":
    unittest.main()
