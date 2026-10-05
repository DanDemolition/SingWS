"""Artist / title text for the background-music "now playing" card, from messy file names and tags.

The audience card used to split the file name on " - " and show the pieces as they were, so a rip named
"Calvin Harris - One Kiss - 01. One Kiss.flac" read "One Kiss - 01. One Kiss", a DJ pack name carried its pack number, remixer
and BPM, and "Don_t" kept its underscore. Nothing here renames files: it only decides what to SHOW.

Pure Python (no Qt) so it is cheap to test. `display_from_path(path, tags)` is the one entry point.
"""
from __future__ import annotations

import os
import re
import unicodedata

_AUDIO_EXT = re.compile(r"\.(mp3|flac|m4a|m4b|aac|wav|aiff?|ogg|oga|opus|wma|mp4|mka|alac)$", re.IGNORECASE)
_SEPARATOR = re.compile(r"\s+[-–—]\s+")                  # " - ", " – ", " — "
_TRACK_NUMBER_ONLY = re.compile(r"^\(?\d{1,3}\)?\.?$")             # "00." / "01" / "(02)"
_TRACK_NUMBER_PREFIX = re.compile(r"^\(?\d{1,3}\)?\s*[.\-_)]\s*(?=\S)")   # "01. One Kiss", "01 - ", "01_"
_SEGMENT_NUMBER = re.compile(r"^\(?\d{1,3}\)?\.\s*(?=\S)")
_PACK_SEGMENT = re.compile(r"^(?:ultimix|kwikmix|ulti-?remix|ulti-?mix|ultimix)\b.*$", re.IGNORECASE)
_BPM_BRACKET = re.compile(r"\s*[(\[][^)\]]*\d+\s*bpm[^)\]]*[)\]]", re.IGNORECASE)
_PACK_BRACKET = re.compile(r"\s*[(\[]\s*(?:ultimix|kwikmix|ulti-?remix|ulti-?mix)\b[^)\]]*[)\]]", re.IGNORECASE)
_NOISE_BRACKET = re.compile(
    r"\s*[(\[]\s*(?:official(?:\s+(?:music|lyric|lyrics|audio|video|visuali[sz]er))*|lyrics?(?:\s+video)?|audio|video|hd|hq|4k|"
    r"explicit|clean|dirty|radio\s+(?:edit|mix|version)|single\s+version|album\s+version|(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?)\s*[)\]]",
    re.IGNORECASE,
)
_TRAILING_BPM = re.compile(r"(?<=[)\]])\s+\d{2,3}$")                 # "(Ultimix By X) 126" -> the 126 is a BPM
_FEAT = re.compile(r"(?:(?<=\s)|(?<=\())(?:F|f|ft|Ft|FT|feat|Feat|featuring|Featuring)\.?\s+(?=[\w\"'‘“(])")
_CONTRACTION = re.compile(r"(?<=[A-Za-z])_(?=(?:t|s|re|ve|ll|d|m)\b)")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "via", "with", "n", "feat.", "ft.", "vs."}
_JUNK_TAGS = {"", "unknown", "unknown artist", "unknown title", "various", "various artists", "va", "n/a", "na", "none", "untitled",
              "<unknown>", "no artist", "artist", "title"}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or ""))


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–—_,")


def _fix_underscores(text: str) -> str:
    text = re.sub(r"\s*_-_\s*", " - ", text)                      # "Artist_-_Title"
    text = _CONTRACTION.sub("'", text)                            # Don_t -> Don't
    text = re.sub(r"(?<=[A-Za-z])_(?=\s)", "'", text)             # "Lil_ Cheesecake" -> "Lil' Cheesecake"
    return text.replace("_", " ")


def _normalise_feat(text: str) -> str:
    return _FEAT.sub("feat. ", text)


def _dedupe_halves(text: str) -> str:
    """'no tears left to cry- no tears left to cry' -> 'no tears left to cry'."""
    m = re.fullmatch(r"(.+?)\s*[-–]\s*(.+)", text)
    if m and m.group(1).strip().casefold() == m.group(2).strip().casefold():
        return m.group(1).strip()
    return text


def _title_case(text: str) -> str:
    """Capitalise a title that was typed entirely in lower case (everything else is left as the file or tag has it)."""
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        bare = word.strip("()[]\"'‘’“”")
        if i > 0 and bare.lower() in _SMALL_WORDS:
            out.append(word)
            continue
        out.append(re.sub(r"[A-Za-z]", lambda m: m.group(0).upper(), word, count=1))
    return " ".join(out)


def clean_title(text: str) -> str:
    """Clean one title string: pack/BPM/noise brackets out, feat. spelled out, a lower-case title capitalised."""
    text = _fix_underscores(_norm(text))
    text = _TRAILING_BPM.sub("", text)          # first: it is recognised by the bracket right before it
    text = _PACK_BRACKET.sub("", text)
    text = _BPM_BRACKET.sub("", text)
    text = _NOISE_BRACKET.sub("", text)
    text = _normalise_feat(text)
    text = _tidy(_dedupe_halves(_tidy(text)))
    if text and text == text.lower() and re.search(r"[a-z]", text):
        text = _title_case(text)
    return text


def clean_artist(text: str) -> str:
    text = _normalise_feat(_fix_underscores(_norm(text)))
    return _tidy(text)


def _stem(name: str) -> str:
    base = os.path.basename(str(name or ""))
    return _AUDIO_EXT.sub("", base)


def parse_filename(name: str) -> tuple[str, str]:
    """(artist, title) from a file name or path. Either may be empty."""
    stem = _fix_underscores(_norm(_stem(name))).strip()
    if not stem:
        return "", ""
    leading = _TRACK_NUMBER_PREFIX.match(stem)
    if leading and _SEPARATOR.search(stem[leading.end():]):          # "01 - Artist - Title": a track number, not part of the name
        stem = stem[leading.end():]
    segments = [s.strip() for s in _SEPARATOR.split(stem) if s.strip()]
    kept = []
    for seg in segments:
        if _TRACK_NUMBER_ONLY.match(seg) or _PACK_SEGMENT.match(seg):   # "00." and "Ultimix 330" say nothing about the song
            continue
        seg = _SEGMENT_NUMBER.sub("", seg, count=1)                      # "01. One Kiss" -> "One Kiss" ("1-800" stays)
        if seg:
            kept.append(seg)
    if not kept:
        return "", clean_title(stem)
    if len(kept) == 1:
        return "", clean_title(kept[0])
    # Artist - Title, or Artist - Album/Pack - Title (the album sits in the middle and is not shown).
    return clean_artist(kept[0]), clean_title(kept[-1])


def _usable_tag(value: str) -> str:
    value = _tidy(_norm(value))
    low = value.casefold()
    if low in _JUNK_TAGS or re.fullmatch(r"(?:audio\s+)?track\s*\d*", low) or "http" in low or "www." in low:
        return ""
    return value


def display_from_path(path: str, tag_artist: str = "", tag_title: str = "") -> tuple[str, str]:
    """(artist, title) to show for a music file. Good tags win; the file name fills in whatever the tags lack."""
    file_artist, file_title = parse_filename(path)
    artist = clean_artist(_usable_tag(tag_artist))
    title = clean_title(_usable_tag(tag_title))
    if artist and title:
        return artist, title
    return artist or file_artist, title or file_title


def read_tags(path: str) -> tuple[str, str]:
    """(artist, title) from the file's own tags; empty strings when mutagen or the tags are missing."""
    try:
        from mutagen import File as _MutagenFile
        mf = _MutagenFile(path)
        tags = getattr(mf, "tags", None) if mf is not None else None
        if not tags:
            return "", ""

        def first(*names):
            for n in names:
                try:
                    v = tags.get(n)
                except Exception:
                    v = None
                if isinstance(v, list) and v:
                    return str(v[0]).strip()
                if v:
                    return str(v).strip()
            return ""

        return first("TPE1", "ARTIST", "artist", "\xa9ART"), first("TIT2", "TITLE", "title", "\xa9nam")
    except Exception:
        return "", ""


def display_for_file(path: str) -> tuple[str, str]:
    """(artist, title) for a music file on disk: tags when they are good, the cleaned file name otherwise."""
    tag_artist, tag_title = read_tags(path)
    return display_from_path(path, tag_artist, tag_title)
