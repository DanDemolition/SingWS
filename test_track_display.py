import unittest

import track_display as td


class ParseFilenameTests(unittest.TestCase):
    CASES = {
        "Charli Xcx - Ultimix 330 - 04. Apple (Ultimix By Dj Brian Howe) 126.mp3": ("Charli Xcx", "Apple"),
        "Knox - Ultimix 322 - 09. Not The 1975 (Ulti-Remix By Mark Roberts) 134.mp3": ("Knox", "Not The 1975"),
        "bbno$ - 1-800 (ft. ironmouse).mp3": ("bbno$", "1-800 (feat. Ironmouse)"),
        "Kendrick Lamar - DAMN. - 08. HUMBLE..mp3": ("Kendrick Lamar", "HUMBLE."),
        "Ariana Grande - no tears left to cry- no tears left to cry - 00..flac": ("Ariana Grande", "No Tears Left to Cry"),
        "01 - Daft Punk - One More Time.mp3": ("Daft Punk", "One More Time"),
        "03. Sabrina Carpenter - Espresso.mp3": ("Sabrina Carpenter", "Espresso"),
        "Artist_-_Song_Title.mp3": ("Artist", "Song Title"),
        "Calvin Harris - One Kiss - 01. One Kiss.flac": ("Calvin Harris", "One Kiss"),
        "Disciples - They Don_t Know (Radio Edit).mp3": ("Disciples", "They Don't Know"),
        "Lil_ Cheesecake - Chef_s Kiss.mp3": ("Lil' Cheesecake", "Chef's Kiss"),
        "Renee Rapp F. Megan Thee Stallion - Mean Girls.mp3": ("Renee Rapp feat. Megan Thee Stallion", "Mean Girls"),
        "Nelly Furtado - Say It Right [Official Video].mp3": ("Nelly Furtado", "Say It Right"),
        "Drake - God's Plan (Explicit).mp3": ("Drake", "God's Plan"),
        "21 Guns.mp3": ("", "21 Guns"),
        "Just A Title.mp3": ("", "Just A Title"),
        "blink-182 - all the small things.mp3": ("blink-182", "All the Small Things"),
    }

    def test_real_world_names(self):
        for name, expected in self.CASES.items():
            with self.subTest(name=name):
                self.assertEqual(td.parse_filename(name), expected)

    def test_directory_is_ignored(self):
        self.assertEqual(td.parse_filename("/Music/BGM/Daft Punk - One More Time.mp3"), ("Daft Punk", "One More Time"))


class TagTests(unittest.TestCase):
    def test_good_tags_win(self):
        self.assertEqual(td.display_from_path("/m/x - y.mp3", "Charli XCX", "Apple"), ("Charli XCX", "Apple"))

    def test_junk_tags_fall_back_to_filename(self):
        self.assertEqual(td.display_from_path("/m/Daft Punk - One More Time.mp3", "Various Artists", "Track 01"),
                         ("Daft Punk", "One More Time"))

    def test_filename_fills_missing_tag(self):
        self.assertEqual(td.display_from_path("/m/Daft Punk - One More Time.mp3", "", "One More Time")[0], "Daft Punk")

    def test_nothing_usable(self):
        self.assertEqual(td.display_from_path("/m/untagged song.mp3", "", ""), ("", "Untagged Song"))


if __name__ == "__main__":
    unittest.main()
