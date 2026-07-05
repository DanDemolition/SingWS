"""
okj_fileinfo.py
---------------
Python port of OpenKJ's karaoke filename metadata system:
  karaokefileinfo.cpp + karaokefilepatternresolver.cpp + custompattern.cpp

Extracts (artist, title, song_id) from karaoke filenames using the same
naming patterns OpenKJ supports, resolves the right pattern per source
directory (longest-path-prefix wins, exactly like OpenKJ), reads directly
from OpenKJ's openkj.sqlite so any custom patterns and per-directory
settings you've configured in OpenKJ Just Work in Co-Pilot too.

On top (Co-Pilot addition, not in OpenKJ): a brand classifier that maps
the extracted song_id to your brand-priority list (KV -> ZOOM -> CC ->
KARAFUN -> Party Tyme -> SBI -> SoundChoice -> SunFly -> ...), which is
what the auto-select-on-accept feature needs.

Usage:
    resolver = PatternResolver.from_openkj_db(
        Path.home() / "Library/Application Support/OpenKJ/openkj.sqlite")
    info = KaraokeFileInfo("/media/karaoke/KV_45123 - Adele - Hello.zip",
                           resolver)
    info.artist, info.title, info.song_id   # "Adele", "Hello", "KV_45123"
    brand_of(info.song_id)                  # "KV"
    brand_priority(info.song_id)            # 0 (best)

Duration helper included: for bare .cdg files it's computed from file
size exactly like OpenKJ: (size/96)/75 seconds (96 bytes per sector,
75 sectors/sec — pure CDG redbook math, no decode needed).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


class NamingPattern(IntEnum):
    """Same values as OpenKJ's SourceDir::NamingPattern enum, so the ints
    stored in openkj.sqlite's sourceDirs.pattern column map directly."""
    SAT = 0        # SongID - Artist - Title      (OpenKJ's default)
    STA = 1        # SongID - Title - Artist
    ATS = 2        # Artist - Title - SongID
    TAS = 3        # Title - Artist - SongID
    AT = 4         # Artist - Title
    TA = 5         # Title - Artist
    CUSTOM = 6     # user regexes w/ capture groups
    METADATA = 7   # read media tags (not handled here; needs taglib/mutagen)
    S_T_A = 8      # SongID_Title_Artist (underscore separated)


@dataclass
class CustomPattern:
    name: str = ""
    artist_regex: str = ""
    artist_group: int = 0
    title_regex: str = ""
    title_group: int = 0
    songid_regex: str = ""
    songid_group: int = 0

    def is_null(self) -> bool:
        return not (self.artist_regex or self.title_regex or self.songid_regex)


@dataclass
class KaraokeFilePattern:
    pattern: NamingPattern = NamingPattern.SAT
    custom: CustomPattern = field(default_factory=CustomPattern)


DEFAULT_PATTERN = KaraokeFilePattern(NamingPattern.SAT)


class PatternResolver:
    """Port of KaraokeFilePatternResolver: per-source-directory patterns,
    longest matching path prefix wins."""

    def __init__(self, path_pattern_map: dict[str, KaraokeFilePattern] | None = None):
        # sorted by path so we can scan longest-prefix-first like OpenKJ
        self._map = dict(sorted((path_pattern_map or {}).items()))

    @classmethod
    def from_openkj_db(cls, db_path: str | Path) -> "PatternResolver":
        """Read sourceDirs + custompatterns straight from openkj.sqlite —
        Co-Pilot inherits whatever you've configured in OpenKJ."""
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT sourceDirs.path, sourceDirs.pattern,
                   custompatterns.name,
                   custompatterns.artistregex, custompatterns.artistcapturegrp,
                   custompatterns.titleregex,  custompatterns.titlecapturegrp,
                   custompatterns.discidregex, custompatterns.discidcapturegrp
            FROM sourceDirs
            LEFT JOIN custompatterns
                   ON sourceDirs.custompattern == custompatterns.patternid
            ORDER BY sourceDirs.path
        """).fetchall()
        con.close()

        m: dict[str, KaraokeFilePattern] = {}
        for r in rows:
            pat = NamingPattern(r["pattern"])
            custom = CustomPattern()
            if pat == NamingPattern.CUSTOM and r["name"] is not None:
                custom = CustomPattern(
                    r["name"],
                    r["artistregex"] or "", r["artistcapturegrp"] or 0,
                    r["titleregex"] or "", r["titlecapturegrp"] or 0,
                    r["discidregex"] or "", r["discidcapturegrp"] or 0,
                )
            m[r["path"]] = KaraokeFilePattern(pat, custom)
        return cls(m)

    def get_pattern(self, filename: str) -> KaraokeFilePattern:
        # reverse iteration => '/media/abc' matches before '/media/a'
        for path in reversed(self._map):
            if filename.startswith(path):
                return self._map[path]
        return DEFAULT_PATTERN


class KaraokeFileInfo:
    """Port of KaraokeFileInfo::parseMetadata (filename patterns only;
    METADATA falls back to filename parsing here — wire up mutagen if you
    want tag reading, but for your library the DB already has tags)."""

    def __init__(self, filename: str,
                 resolver: PatternResolver | None = None):
        self.filename = filename
        self.artist = ""
        self.title = ""
        self.song_id = ""
        self._resolver = resolver or PatternResolver()
        self._parse()

    # ------------------------------------------------------------ parsing
    def _parse(self):
        pattern = self._resolver.get_pattern(self.filename)
        ok = self._parse_with(pattern)
        # OpenKJ fallback: if the configured pattern produced nothing,
        # retry with the default (SAT)
        if not ok and pattern.pattern != NamingPattern.METADATA:
            self._parse_with(DEFAULT_PATTERN)

    def _parse_with(self, kp: KaraokeFilePattern) -> bool:
        base = Path(self.filename).stem
        p = kp.pattern

        if p == NamingPattern.CUSTOM:
            return self._parse_custom(base, kp.custom)

        if p == NamingPattern.S_T_A:
            parts = base.split("_")
        else:
            # OpenKJ: underscores are treated as spaces, split on " - "
            parts = base.replace("_", " ").split(" - ")

        a = t = s = ""
        if p in (NamingPattern.SAT, NamingPattern.S_T_A, NamingPattern.STA):
            if parts:
                s = parts[0]
            if p == NamingPattern.SAT:
                if len(parts) >= 2:
                    a = parts[1]
                t = " - ".join(parts[2:])
            else:  # STA / S_T_A: SongID, Title, Artist
                if len(parts) >= 2:
                    t = parts[1]
                a = " - ".join(parts[2:])
        elif p == NamingPattern.ATS:
            if parts:
                a = parts[0]
            if len(parts) >= 3:
                s = parts[-1]
                parts = parts[:-1]
            t = " - ".join(parts[1:])
        elif p == NamingPattern.TAS:
            if parts:
                t = parts[0]
            if len(parts) >= 3:
                s = parts[-1]
                parts = parts[:-1]
            a = " - ".join(parts[1:])
        elif p == NamingPattern.AT:
            if parts:
                a = parts[0]
            t = " - ".join(parts[1:])
        elif p in (NamingPattern.TA, NamingPattern.METADATA):
            # METADATA without a tag reader degrades to TA-ish best effort;
            # swap in mutagen here if you ever need true tag reads.
            if parts:
                t = parts[0]
            a = " - ".join(parts[1:])

        self.artist, self.title, self.song_id = a.strip(), t.strip(), s.strip()
        return bool(self.artist or self.title or self.song_id)

    def _parse_custom(self, base: str, c: CustomPattern) -> bool:
        if c.is_null():
            return False

        def grab(regex: str, group: int) -> str:
            if not regex:
                return ""
            m = re.search(regex, base)
            if not m:
                return ""
            try:
                return (m.group(group) or "").strip()
            except (IndexError, error_types):
                return ""

        error_types = re.error
        self.artist = grab(c.artist_regex, c.artist_group)
        self.title = grab(c.title_regex, c.title_group)
        self.song_id = grab(c.songid_regex, c.songid_group)
        return bool(self.artist or self.title or self.song_id)


# ---------------------------------------------------------------------------
# CDG duration from file size (KaraokeFileInfo::getDuration, .cdg branch)
# ---------------------------------------------------------------------------
def cdg_duration_ms(cdg_path: str | Path) -> int:
    """(size/96)/75 seconds: 96-byte sectors at 75/sec, redbook CDG."""
    return (Path(cdg_path).stat().st_size // 96) // 75 * 1000


# ---------------------------------------------------------------------------
# Brand classification (Co-Pilot addition — feeds auto-select-on-accept)
# ---------------------------------------------------------------------------
# Ordered by YOUR priority: Karaoke Version -> ZOOM -> CC -> KARAFUN ->
# Party Tyme -> SBI -> SoundChoice -> SunFly -> personal mp4 -> WSK ->
# Mr. Entertainer. Each entry: (brand key, regexes matched against song_id,
# case-insensitive, anchored at start).
# NOTE: CC (Chris Call Karaoke, literal "CC" only) and CB (Chartbuster) are
# DIFFERENT brands — never merge them. SingWS's DISC_BRAND_ALIASES remains
# the runtime source of truth; this table exists for library audits and
# duplicate-version picking.
#
# SingWS's library convention is "BRAND - Artist - Title.ext" — a bare brand
# token with no disc number — so every brand also matches its exact token
# (r"...$"), not only disc-numbered forms.
BRAND_PATTERNS: list[tuple[str, list[str]]] = [
    ("KV",       [r"KV$", r"KV[\s_-]?\d", r"KVD?\d"]),         # Karaoke Version
    ("ZOOM",     [r"ZOOM$", r"Z(?:OOM)?[\s_-]?\d", r"ZPA\d?", r"ZSC\d?"]),
    ("CC",       [r"CC$", r"CC[\s_-]?\d"]),                    # Chris Call Karaoke
    ("KARAFUN",  [r"KARAFUN$", r"KF[\s_-]?\d", r"KFN"]),
    ("PYT",      [r"PYT$", r"PT[\s_-]?\d", r"PY\d", r"SPT\d?"]),  # Party Tyme
    ("SBI",      [r"SBI$", r"SBI[\s_-]?\d"]),
    ("SC",       [r"SC$", r"SC\d{4,}", r"SC[\s_-]\d"]),        # SoundChoice
    ("SF",       [r"SF$", r"SF(?:MW|KK|G)?[\s_-]?\d"]),        # SunFly family
    ("PHM",      [r"PHM$", r"PHM[\s_-]?\d"]),                  # Pop Hits Monthly
    ("TH",       [r"TH$", r"TH[\s_-]?\d"]),
    ("CB",       [r"CB$", r"CB\d{4,}"]),                       # Chartbuster
    ("WSK",      [r"WSK$", r"WSK[\s_-]?\d?"]),
    ("ME",       [r"ME$", r"MRE[\s_-]?\d", r"MRH\d"]),         # Mr. Entertainer
]
_BRAND_COMPILED = [(brand, [re.compile(rx, re.IGNORECASE) for rx in rxs])
                   for brand, rxs in BRAND_PATTERNS]


def brand_of(song_id: str) -> str | None:
    """Best-effort brand key from a song_id, None if unrecognized
    (e.g. your personal .mp4s with no disc ID)."""
    sid = song_id.strip()
    for brand, regexes in _BRAND_COMPILED:
        if any(rx.match(sid) for rx in regexes):
            return brand
    return None


def brand_priority(song_id: str) -> int:
    """Index into your priority list; lower = preferred. Unknown brands
    rank just below Mr. Entertainer, above nothing."""
    b = brand_of(song_id)
    for i, (brand, _) in enumerate(BRAND_PATTERNS):
        if brand == b:
            return i
    return len(BRAND_PATTERNS)


def pick_preferred(song_ids: list[str]) -> str:
    """Given multiple versions of the same song, pick per brand priority."""
    return min(song_ids, key=brand_priority)


if __name__ == "__main__":
    tests = [
        ("SC8321-01 - Adele - Hello.zip", NamingPattern.SAT,
         ("Adele", "Hello", "SC8321-01")),
        ("KV_45123 - Adele - Hello.zip", NamingPattern.SAT,
         ("Adele", "Hello", "KV 45123")),   # underscores become spaces
        ("Adele - Hello - ZOOM123.mp4", NamingPattern.ATS,
         ("Adele", "Hello", "ZOOM123")),
        ("Hello - Adele.mp4", NamingPattern.TA, ("Adele", "Hello", "")),
    ]
    for fn, pat, expected in tests:
        r = PatternResolver({"/": KaraokeFilePattern(pat)})
        i = KaraokeFileInfo("/" + fn, r)
        got = (i.artist, i.title, i.song_id)
        status = "OK " if got == expected else "FAIL"
        print(f"{status} {pat.name:4s} {fn!r:45s} -> {got}")

    for sid in ["KV 45123", "SC8321-01", "ZOOM123", "SF289-04",
                "KF12345", "PT10234", "SBI0455", "mysong"]:
        print(f"  {sid!r:14s} brand={brand_of(sid)!r:10s} "
              f"priority={brand_priority(sid)}")
    ids = ["SC8321-01", "KV 45123", "SF289-04"]
    print("pick_preferred:", pick_preferred(ids), "(expect the KV one)")
