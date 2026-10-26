"""Stage A5 shadow hook (playback orchestrator, step 1): compares the route `play_next_file` actually takes for a picked song with the Rust ``singws-start`` rules and LOGS
disagreements.

SHADOW ONLY. The Rust answer never drives anything; every method swallows its own errors, so a missing dylib, an odd ``song_info`` or a crash in here cannot affect a
show. Setting ``start_shadow`` (default on through the evidence migration; Python still wins every disagreement).

The app calls :meth:`check` at each point of its start chain where it commits to a route, passing the route it is about to take (``observed``) and every fact it has
already computed at that point (nothing extra is read from disk). Rust walks the same chain with the same facts and must end on the same route.

Log lines (grep ``START-SHADOW``):
  * ``decision``  first time each route is seen, e.g. route mp4 agree=1
  * ``MISMATCH``  Rust chose a different route than the app (the app's route was used)
  * ``summary``   running totals every SUMMARY_EVERY checks
"""

from __future__ import annotations

import ctypes
import json

_OUT_CAP = 512
_TEXT_CAP = 4096
SUMMARY_EVERY = 25

ROUTES = ("external_karafun", "reject_missing_file", "zip_prepare", "cdg_mp3_dual", "mp4", "mp3", "mp3g_zip", "reject_unsupported_tuple", "reject_invalid_zip",
          "reject_missing_cdg_audio_pair", "reject_unsupported_format")


def info_request(song_info):
    """`song_info` as the Rust input describes it: text, a sequence (only the first three items matter), or anything else."""
    if isinstance(song_info, str):
        return {"str": song_info[-_TEXT_CAP:]}
    if isinstance(song_info, (tuple, list)):
        items = []
        for x in list(song_info)[:3]:
            if isinstance(x, str):
                items.append({"s": x[-_TEXT_CAP:]})
            else:
                try:
                    shown = str(x)
                except Exception:
                    shown = ""
                items.append({"o": shown[-_TEXT_CAP:]})
        return {"seq": items}
    return {"other": True}


class StartShadow:
    def __init__(self, log, lib=None):
        self._log = log
        self._lib = lib if lib is not None else _load_lib()
        self.available = self._lib is not None
        self.checks = 0
        self.mismatches = 0
        self._seen = set()

    def _say(self, text):
        try:
            self._log(f"[START-SHADOW] {text}")
        except Exception:
            pass

    def _call(self, request):
        if not self.available:
            return None
        try:
            payload = json.dumps(request, allow_nan=False)
        except (TypeError, ValueError):
            return None
        buf = ctypes.create_string_buffer(_OUT_CAP)
        n = self._lib.singws_start_json(payload.encode(), buf, _OUT_CAP)
        if n < 0:
            self._say(f"start call failed code={n}")
            return None
        return json.loads(buf.raw[:n].decode()).get("route")

    def check_engine(self, observed, want_rust, supports, engine, voice_error=False):
        """Compare who plays the sound of the song about to start (`observed` = {"voice": bool, "reason": str, "set_shadow_audio": None|bool}) with Rust.
        Never raises; returns agree, or None when skipped."""
        try:
            if not self.available:
                return None
            req = engine_facts(want_rust, supports, engine, voice_error)
            buf = ctypes.create_string_buffer(_OUT_CAP)
            n = self._lib.singws_start_json(json.dumps(req).encode(), buf, _OUT_CAP)
            if n < 0:
                self._say(f"engine call failed code={n}")
                return None
            got = json.loads(buf.raw[:n].decode())
            want = {"voice": bool(observed.get("voice")), "reason": str(observed.get("reason") or ""), "set_shadow_audio": observed.get("set_shadow_audio")}
            self.checks += 1
            agree = got == want
            kind = f"engine voice={int(want['voice'])} reason={want['reason'] or '-'}"
            if not agree:
                self.mismatches += 1
                self._say(f"MISMATCH engine app={want} rust={got} facts={ {k: v for k, v in req.items() if k != 'op'} }")
            elif kind not in self._seen:
                self._seen.add(kind)
                self._say(f"decision route={kind.replace(' ', '_')} agree=1")
            if self.checks % SUMMARY_EVERY == 0:
                self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
            return agree
        except Exception as exc:
            self._say(f"check_engine error: {exc!r}")
            return None

    def check_display(self, artist, title, entry_artist, entry_title, fallback):
        """Compare the artist and title the app published for the song that just started with Rust. `entry_artist` / `entry_title` are the entry's raw values as text,
        `fallback` is the library display name the app looked up (None when it did not need one). Never raises; returns agree, or None when skipped."""
        try:
            if not self.available:
                return None
            req = {"op": "display", "artist": str(entry_artist), "title": str(entry_title)}
            if fallback is not None:
                req["fallback"] = str(fallback)
            buf = ctypes.create_string_buffer(_OUT_CAP * 8)
            n = self._lib.singws_start_json(json.dumps(req, allow_nan=False).encode(), buf, _OUT_CAP * 8)
            if n < 0:
                self._say(f"display call failed code={n}")
                return None
            got = json.loads(buf.raw[:n].decode())
            want = {"artist": str(artist), "title": str(title)}
            self.checks += 1
            agree = got == want
            if not agree:
                self.mismatches += 1
                self._say(f"MISMATCH display app={want!r} rust={got!r} entry=({str(entry_artist)[:60]!r}, {str(entry_title)[:60]!r}) fallback={str(fallback)[:80]!r}")
            elif "display" not in self._seen:
                self._seen.add("display")
                self._say("decision route=display agree=1")
            if self.checks % SUMMARY_EVERY == 0:
                self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
            return agree
        except Exception as exc:
            self._say(f"check_display error: {exc!r}")
            return None

    def check_pick(self, facts, singer, song):
        """Compare who the app picked to sing next (`singer` = index in the queue as it was before the pick, `song` = index in that singer's songs; both None when
        nobody could sing) with Rust. `facts` is :func:`pick_facts` of the queue taken before the loop ran. Never raises; returns agree, or None when skipped."""
        try:
            if not self.available or facts is None:
                return None
            payload = json.dumps({"op": "pick", "singers": facts}, allow_nan=False)
            buf = ctypes.create_string_buffer(_OUT_CAP)
            n = self._lib.singws_start_json(payload.encode(), buf, _OUT_CAP)
            if n < 0:
                self._say(f"pick call failed code={n}")
                return None
            got = json.loads(buf.raw[:n].decode())
            want = {"singer": singer, "song": song}
            self.checks += 1
            agree = got == want
            kind = "pick_none" if singer is None else "pick"
            if not agree:
                self.mismatches += 1
                self._say(f"MISMATCH pick app={want} rust={got} queue={json.dumps(facts)[:200]}")
            elif kind not in self._seen:
                self._seen.add(kind)
                self._say(f"decision route={kind} agree=1")
            if self.checks % SUMMARY_EVERY == 0:
                self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
            return agree
        except Exception as exc:
            self._say(f"check_pick error: {exc!r}")
            return None

    def check(self, observed, song_info, *, external=False, has_primary=False, primary_exists=None, zip_cached=None, mp3g=None, pair=None):
        """Compare the route the app is taking (`observed`) with Rust's. Never raises; returns agree, or None when skipped."""
        try:
            if not self.available:
                return None
            req = {"external": bool(external), "info": info_request(song_info), "has_primary": bool(has_primary)}
            for key, value in (("primary_exists", primary_exists), ("zip_cached", zip_cached), ("mp3g", mp3g), ("pair", pair)):
                if value is not None:
                    req[key] = bool(value)
            got = self._call(req)
            if got is None:
                return None
            self.checks += 1
            agree = got == observed
            if not agree:
                self.mismatches += 1
                shown = repr(song_info)
                self._say(f"MISMATCH app={observed} rust={got} info={shown[:160]} facts={ {k: v for k, v in req.items() if k != 'info'} }")
            elif observed not in self._seen:
                self._seen.add(observed)
                self._say(f"decision route={observed} agree=1")
            if self.checks % SUMMARY_EVERY == 0:
                self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
            return agree
        except Exception as exc:
            self._say(f"check error: {exc!r}")
            return None


def engine_facts(want_rust, supports, engine, voice_error=False):
    """The request for `_karaoke_rust_voice_for_song`'s decision. `engine` is "none", "headless" or "running"."""
    return {"op": "engine", "want_rust": bool(want_rust), "supports_shadow_audio": bool(supports), "engine": str(engine), "voice_error": bool(voice_error)}


def pick_facts(queue):
    """The queue as the pick loop sees it: [{"skipped": bool, "songs": "dds..."}] with d = dict entry not skipped, s = dict entry skipped, t = older tuple entry.
    None when the queue holds something the pick loop itself would raise on (a singer that is not a dict, `songs` that is not a list)."""
    out = []
    for singer in queue:
        if not isinstance(singer, dict):
            return None
        songs = singer.get("songs", [])
        if songs is None:
            songs = []
        if not isinstance(songs, (list, tuple)):
            return None
        kinds = []
        for song in songs:
            if isinstance(song, dict):
                kinds.append("s" if song.get("skipped", False) else "d")
            else:
                kinds.append("t")
        out.append({"skipped": bool(singer.get("skipped", False)), "songs": "".join(kinds)})
    return out


StartShadow.pick_facts = staticmethod(pick_facts)  # the app calls it on the instance it already holds

def _load_lib():
    try:
        from rust_master_dsp import _candidate_paths
        for path in _candidate_paths():
            if not path.exists():
                continue
            lib = ctypes.CDLL(str(path))
            fn = lib.singws_start_json  # AttributeError if this dylib predates the start shadow
            fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int64
            return lib
    except Exception:
        pass
    return None
