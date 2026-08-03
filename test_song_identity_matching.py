import sqlite3

import song_index


def test_title_identity_ignores_punctuation_and_common_wording_variants():
    expected = song_index.fuzzy_match_key("We're Going to Make It")
    assert song_index.fuzzy_match_key("Were Gonna Make-It") == expected
    assert song_index.fuzzy_match_key("Were Gonna Make It") == expected


def test_artist_identity_accepts_first_last_and_last_first_order():
    assert song_index.artist_names_match("Frank Sinatra", "Sinatra, Frank")
    assert song_index.artist_names_match("Frank Sinatra", "Sinatra Frank")
    assert song_index.artist_names_match("John Michael Smith", "Smith, John Michael")


def test_find_by_artist_title_returns_every_alternately_formatted_version(tmp_path):
    dbfile = tmp_path / "songs.db"
    con = sqlite3.connect(dbfile)
    con.execute(
        "CREATE TABLE songs (path TEXT, artist TEXT, title TEXT, "
        "artist_norm TEXT, title_norm TEXT)"
    )
    rows = [
        ("one.zip", "Frank Sinatra", "We're Going to Make It"),
        ("two.zip", "Sinatra, Frank", "Were Gonna Make-It"),
    ]
    con.executemany(
        "INSERT INTO songs VALUES (?, ?, ?, ?, ?)",
        [
            (path, artist, title, song_index.fuzzy_match_key(artist), song_index.fuzzy_match_key(title))
            for path, artist, title in rows
        ],
    )
    con.commit()
    con.close()

    # This exactly matches the first row; the tolerant union must still expose
    # the differently formatted sibling version to the auto picker.
    matches = song_index.find_by_artist_title(
        "Frank Sinatra", "We're Going to Make It", dbfile=dbfile
    )
    assert {row["path"] for row in matches} == {"one.zip", "two.zip"}
