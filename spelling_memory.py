"""What the host's manual fixes teach the automatic picker about spelling.

When the host replaces a requested song with a library track that is the same song spelled differently ("Rightous Brothers" -> "Righteous Brothers", "Cryin" ->
"Crying"), the request's spelling is remembered. The next request that spells it the same way goes straight to the track the host chose, with all its disc
versions, so the host fixes each misspelling once, however many singers keep spelling it that way.

Only fixes that look like spelling are learned: one field (artist or title) unchanged and the other within a few typos, or the same words in another form. A host who
swaps a singer's request for a different song teaches nothing. Stored in SingWS's data folder (spelling_memory.json), small, never sent anywhere.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional, Tuple

import song_index

FILE_NAME = "spelling_memory.json"
MAX_ENTRIES = 5000
_LOCK = threading.RLock()
_STATE = {"path": None, "data": None, "version": 0}


def _path() -> str:
    return str(song_index.user_singws_dir() / FILE_NAME)


def _load() -> dict:
    path = _path()
    if _STATE["path"] == path and _STATE["data"] is not None:
        return _STATE["data"]
    data = {"pairs": {}}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict) and isinstance(loaded.get("pairs"), dict):
            data = loaded
    except (OSError, ValueError):
        pass
    _STATE.update(path=path, data=data)
    return data


def _save(data: dict) -> None:
    path = _path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass


def request_key(artist: str, title: str) -> str:
    return song_index.fuzzy_match_key(artist) + "|" + song_index.fuzzy_match_key(title)


def version() -> int:
    """Changes whenever something was learned (lookups that depend on it are cached by this)."""
    return _STATE["version"]


def looks_like_spelling_fix(old_artist: str, old_title: str, new_artist: str, new_title: str) -> bool:
    """The replacement is the same song as the request, spelled differently (not a different song)."""
    if request_key(old_artist, old_title) == request_key(new_artist, new_title):
        return False                    # nothing to learn: the same spelling after normalisation
    if song_index.same_song(old_artist, old_title, new_artist, new_title):
        return True
    # Looser than the picker's rule, because a human confirmed it: each field is the same or mostly the same words (typos and a missing "the"/"a" allowed).
    return _mostly_same(old_artist, new_artist) and _mostly_same(old_title, new_title)


def _mostly_same(left: str, right: str) -> bool:
    a, b = song_index._identity_tokens(left), song_index._identity_tokens(right)
    if not a or not b:
        return False
    if a == b:
        return True
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    hits = sum(1 for w in small if any(song_index.tokens_close(w, c, 3) >= 0 for c in big))
    return hits == len(small) and len(small) >= 0.6 * len(big)


def learn(old_artist: str, old_title: str, new_artist: str, new_title: str) -> bool:
    """Remember a spelling fix. Returns whether anything was learned."""
    old_artist, old_title, new_artist, new_title = (str(x or "").strip() for x in (old_artist, old_title, new_artist, new_title))
    if not (old_title and new_title) or not looks_like_spelling_fix(old_artist, old_title, new_artist, new_title):
        return False
    with _LOCK:
        data = _load()
        pairs = data["pairs"]
        key = request_key(old_artist, old_title)
        entry = pairs.get(key) or {"count": 0}
        entry.update(artist=new_artist, title=new_title, last=int(time.time()), count=int(entry.get("count", 0)) + 1,
                     asked_artist=old_artist[:120], asked_title=old_title[:120])
        pairs[key] = entry
        if len(pairs) > MAX_ENTRIES:
            for stale in sorted(pairs, key=lambda k: pairs[k].get("last", 0))[: len(pairs) - MAX_ENTRIES]:
                pairs.pop(stale, None)
        _STATE["version"] += 1
        _save(data)
    return True


def lookup(artist: str, title: str) -> Optional[Tuple[str, str]]:
    """The (artist, title) the host chose last time for a request spelled like this, or None."""
    with _LOCK:
        entry = _load()["pairs"].get(request_key(artist, title))
    if not entry:
        return None
    return str(entry.get("artist") or ""), str(entry.get("title") or "")


def forget(artist: str, title: str) -> bool:
    with _LOCK:
        data = _load()
        gone = data["pairs"].pop(request_key(artist, title), None) is not None
        if gone:
            _STATE["version"] += 1
            _save(data)
    return gone
