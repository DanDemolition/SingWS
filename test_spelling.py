"""Typos in requests ("Rightous", "Cryin", "Rodd Rundgren") must not hide a song's other versions from the automatic picker, and a host's manual fix is remembered."""
import _test_profile  # noqa: F401
import json, os, tempfile, unittest
from pathlib import Path

import song_index
import spelling_memory


class SameSongTests(unittest.TestCase):
    def test_the_examples_from_the_show(self):
        for a in [("Righteous Brothers", "Unchained Melody", "Rightous Brothers", "Unchained Melody"),
                  ("Creedence Clearwater Revival", "Cryin", "Creedence Clearwater Revival", "Crying"),
                  ("Roy Orbison", "Crying", "Roy Orbison", "Cryin'"),
                  ("Todd Rundgren", "Hello It's Me", "Rodd Rundgren", "Hello It's Me"),
                  ("Todd Rundgren", "Hello It's Me", "Rundgren Todd", "Hello Its Me"),
                  ("Journey", "Faithfully", "Journey", "Faithfuly"),
                  ("Beatles", "Help", "Beetles", "Help"),
                  ("Hall & Oates", "Maneater", "Hall and Oats", "Maneater")]:
            self.assertTrue(song_index.same_song(*a), a)

    def test_different_songs_and_artists_stay_different(self):
        for a in [("Abba", "Fire", "Abba", "Fine"), ("Abba", "Fire", "Ebba", "Fire"), ("Queen", "Love Me", "Queen", "Live Me"),
                  ("Doors", "Light My Fire", "Doors", "Light My Fyre"), ("Abba", "Fire", "Ebba", "Fine"),
                  ("Todd Rundgren", "Bang The Drum All Day", "Rodd Rundgren", "Hello It's Me")]:
            self.assertFalse(song_index.same_song(*a), a)

    def test_a_dropped_g_is_not_a_typo(self):
        self.assertEqual(song_index.tokens_close("doin", "doing", 5), 0)
        self.assertEqual(song_index.tokens_close("cat", "cot", 5), -1, "short words must match exactly")


def _library(rows):
    d = Path(tempfile.mkdtemp(prefix="spelling_"))
    tracks = []
    for i, (artist, title, disc) in enumerate(rows):
        p = d / f"t{i}.kfn"
        p.write_bytes(b"x")
        tracks.append({"path": str(p), "artist": artist, "title": title, "discid": disc, "display": f"{artist} - {title}", "duration_secs": 200, "type": "kfn"})
    tp = d / "tracks.json"
    tp.write_text(json.dumps(tracks))
    db = d / "s.db"
    song_index.rebuild_from_tracks_json(tp, db, verbose=False)
    return db


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.db = _library([
            ("Todd Rundgren", "Hello It's Me", "SC1001"), ("Rodd Rundgren", "Hello It's Me", "KV2002"), ("Todd Rundgren", "Bang The Drum All Day", "SC1003"),
            ("Righteous Brothers", "Unchained Melody", "SC2001"), ("The Righteous Brothers", "Unchained Melody", "SBI2002"),
            ("Creedence Clearwater Revival", "Crying", "SC3001"), ("Creedence Clearwater Revival", "Cryin", "KV3002"),
            ("Abba", "Fire", "SC4001"), ("Ebba", "Fire", "SC4002")])
        spelling_memory._STATE.update(path=None, data=None)
        self.addCleanup(spelling_memory._STATE.update, path=None, data=None)

    def discs(self, artist, title):
        return sorted(r["discid"] for r in song_index.find_by_artist_title(artist, title, dbfile=self.db))

    def test_every_near_spelling_joins_the_set_the_picker_chooses_from(self):
        self.assertEqual(self.discs("Todd Rundgren", "Hello It's Me"), ["KV2002", "SC1001"])
        self.assertEqual(self.discs("Rodd Rundgren", "Hello It's Me"), ["KV2002", "SC1001"])
        self.assertEqual(self.discs("Rightous Brothers", "Unchained Melody"), ["SBI2002", "SC2001"])
        self.assertEqual(self.discs("Creedence Clearwater Revival", "Cryin"), ["KV3002", "SC3001"])
        self.assertEqual(self.discs("Creedence Clearwater Revival", "Crying"), ["KV3002", "SC3001"])

    def test_other_songs_by_a_near_spelled_artist_stay_out(self):
        self.assertNotIn("SC1003", self.discs("Rodd Rundgren", "Hello It's Me"))
        self.assertEqual(self.discs("Abba", "Fire"), ["SC4001"], "Abba and Ebba are different artists")

    def test_the_picker_gets_the_preferred_disc_among_all_spellings(self):
        import importlib.util
        rows = song_index.find_by_artist_title("Rodd Rundgren", "Hello It's Me", dbfile=self.db)
        # the app's rule: the first disc brand in the priority list that has a match
        by_brand = {r["discid"][:2]: r for r in rows}
        self.assertEqual(sorted(by_brand), ["KV", "SC"])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="spelling_home_")
        self._old = os.environ.get("SINGWS_HOME")
        os.environ["SINGWS_HOME"] = self.home
        spelling_memory._STATE.update(path=None, data=None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("SINGWS_HOME", None)
        else:
            os.environ["SINGWS_HOME"] = self._old
        spelling_memory._STATE.update(path=None, data=None)

    def test_a_spelling_fix_is_learned_and_survives_a_restart(self):
        self.assertTrue(spelling_memory.learn("Rightous Brothers", "Unchained Melody", "Righteous Brothers", "Unchained Melody"))
        self.assertEqual(spelling_memory.lookup("rightous brothers", "UNCHAINED MELODY!"), ("Righteous Brothers", "Unchained Melody"))
        spelling_memory._STATE.update(path=None, data=None)       # as after a restart: read back from the file
        self.assertEqual(spelling_memory.lookup("Rightous Brothers", "Unchained Melody"), ("Righteous Brothers", "Unchained Melody"))
        self.assertTrue((Path(self.home) / "spelling_memory.json").exists())

    def test_a_different_song_is_not_learned(self):
        self.assertFalse(spelling_memory.learn("Abba", "Fire", "Queen", "Bohemian Rhapsody"))
        self.assertFalse(spelling_memory.learn("Abba", "Fire", "Abba", "Fire"), "the same spelling teaches nothing")
        self.assertIsNone(spelling_memory.lookup("Abba", "Fire"))

    def test_a_learned_spelling_finds_the_track_the_host_chose_with_its_versions(self):
        db = _library([("Meat Loaf", "Paradise By The Dashboard Light", "SC1"), ("Meatloaf", "Paradise By The Dashboard Light", "KV1")])
        self.assertTrue(spelling_memory.learn("Meat Lof", "Paradise By Dashboard Light", "Meat Loaf", "Paradise By The Dashboard Light"))
        found = sorted(r["discid"] for r in song_index.find_by_artist_title("Meat Lof", "Paradise By Dashboard Light", dbfile=db))
        self.assertEqual(found, ["KV1", "SC1"])

    def test_forget(self):
        spelling_memory.learn("Cryin", "x", "Crying", "x")
        self.assertTrue(spelling_memory.forget("Cryin", "x"))
        self.assertIsNone(spelling_memory.lookup("Cryin", "x"))


class AppWiringTests(unittest.TestCase):
    def test_the_app_picker_and_both_replace_paths_use_it(self):
        src = (Path(__file__).parent / "0.2.18.1.py").read_text(encoding="utf-8")
        self.assertIn("song_index.same_song(", src)
        self.assertIn("search_songs(query, limit=60, fuzzy=True)", src)
        self.assertEqual(src.count("self._learn_spelling_fix("), 2, "learned from the Replace Track dialog / shell and from a waiting-list replacement")


if __name__ == "__main__":
    unittest.main()
