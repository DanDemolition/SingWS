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

    def reweave_order(self, queue):
        """Rust's new order (indices into `queue`) for the rotation lock's reweave, or None when it cannot answer."""
        try:
            facts = reweave_facts(queue)
            if not self.available or facts is None:
                return None
            size = _OUT_CAP + 12 * len(facts)
            buf = ctypes.create_string_buffer(size)
            n = self._lib.singws_start_json(json.dumps({"op": "reweave", "queue": facts}).encode(), buf, size)
            if n < 0:
                return None
            return json.loads(buf.raw[:n].decode())["order"]
        except Exception:
            return None

    def note(self, kind, text=""):
        """A one-time `NOTE` line for something the shadow cannot compare but a report reader should know happened. Never raises."""
        try:
            key = f"note:{kind}"
            if key not in self._seen:
                self._seen.add(key)
                self._say(f"NOTE {kind} {text}".rstrip())
        except Exception:
            pass

    def check_rotation(self, facts, queue_after):
        """Compare the singers' marker / round / has-sung flags after the app committed a started song with Rust. Never raises; None when skipped."""
        try:
            if not self.available or facts is None:
                return None
            objects = facts["_objects"]
            payload = json.dumps({k: v for k, v in facts.items() if k != "_objects"}, allow_nan=False)
            size = _OUT_CAP + 160 * (len(facts["queue"]) + 1)
            buf = ctypes.create_string_buffer(size)
            n = self._lib.singws_start_json(payload.encode(), buf, size)
            if n < 0:
                self._say(f"rotation call failed code={n}")
                return None
            answer = json.loads(buf.raw[:n].decode())
            got = [dict(row, order=i) for row, i in zip(answer["queue"], answer["order"])]
            index = {id(o): i for i, o in enumerate(objects)}
            want = [dict(row, order=index.get(id(s), -1)) for row, s in zip(rotation_after(queue_after), queue_after)]
            self.checks += 1
            agree = got == want
            if not agree:
                self.mismatches += 1
                self._say(f"MISMATCH rotation app={json.dumps(want)[:240]} rust={json.dumps(got)[:240]} before={json.dumps({k: v for k, v in facts.items() if k != '_objects'})[:240]}")
            elif "rotation" not in self._seen:
                self._seen.add("rotation")
                self._say("decision route=rotation agree=1")
            if self.checks % SUMMARY_EVERY == 0:
                self._say(f"summary checks={self.checks} mismatches={self.mismatches}")
            return agree
        except Exception as exc:
            self._say(f"check_rotation error: {exc!r}")
            return None

    def check_pick(self, facts, singer, song, queue=None, lock_cleared=False, rotation_mode=False, setting_locked=False):
        """Compare who the app picked to sing next (`singer` = index in the queue as it was before the pick, `song` = index in that singer's songs; both None when
        nobody could sing) with Rust. `facts` is :func:`pick_facts` of the queue taken before the loop ran. Never raises; returns agree, or None when skipped."""
        try:
            if not self.available or facts is None:
                return None
            payload = json.dumps({"op": "pick", "singers": facts, "rotation_mode": bool(rotation_mode), "setting_locked": bool(setting_locked)}, allow_nan=False)
            size = _OUT_CAP + 12 * len(facts)
            buf = ctypes.create_string_buffer(size)
            n = self._lib.singws_start_json(payload.encode(), buf, size)
            if n < 0:
                self._say(f"pick call failed code={n}")
                return None
            got = json.loads(buf.raw[:n].decode())
            want = {"singer": singer, "song": song, "queue": list(queue) if queue is not None else got["queue"], "lock_cleared": bool(lock_cleared)}
            self.checks += 1
            agree = got == want
            kind = "pick_none" if singer is None else "pick"
            if not agree:
                self.mismatches += 1
                self._say(f"MISMATCH pick app={want} rust={got} locked={int(bool(setting_locked))} mode={int(bool(rotation_mode))} queue={json.dumps(facts)[:200]}")
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
    """The queue as the pick loop sees it: [{"skipped", "songs": "dds..", "marker", "lock_new", "lock_order"}] with d = dict entry not skipped, s = dict entry skipped,
    t = older tuple entry. None when the queue holds something the pick loop itself would raise on (a singer that is not a dict, `songs` that is not a list) or a lock
    order the app could not turn into a whole number."""
    out = []
    for singer in queue:
        if not isinstance(singer, dict):
            return None
        kinds = _kinds(singer.get("songs", []))
        if kinds is None:
            return None
        try:
            order = int(singer.get("rotation_lock_order", 0) or 0)
        except Exception:
            return None
        out.append({"skipped": bool(singer.get("skipped", False)), "songs": kinds, "marker": bool(singer.get("rotation_marker", False)),
                    "lock_new": bool(singer.get("rotation_lock_new", False)), "lock_order": order})
    return out


StartShadow.pick_facts = staticmethod(pick_facts)  # the app calls it on the instance it already holds

def _kinds(songs):
    if songs is None:
        songs = []
    if not isinstance(songs, (list, tuple)):
        return None
    return "".join(("s" if x.get("skipped", False) else "d") if isinstance(x, dict) else "t" for x in songs)


def _rot_singer(singer):
    if not isinstance(singer, dict):
        return None
    kinds = _kinds(singer.get("songs", []))
    if kinds is None:
        return None
    try:
        order = int(singer.get("rotation_lock_order", 0) or 0)
    except Exception:
        return None
    return {"skipped": bool(singer.get("skipped", False)), "songs": kinds, "round_sung": (bool(singer["round_sung"]) if "round_sung" in singer else None),
            "marker": bool(singer.get("rotation_marker", False)), "has_sung": bool(singer.get("has_sung", False)),
            "lock_new": bool(singer.get("rotation_lock_new", False)), "lock_order": order}


def rotation_facts(queue, singer, rotation_mode, marker_was_top, setting_locked=False):
    """The rotation state right before a started song is committed: the queue WITHOUT the singer, the singer, the mode flags and the saved lock. None for anything odd."""
    rows = []
    for other in queue:
        r = _rot_singer(other)
        if r is None:
            return None
        rows.append(r)
    me = _rot_singer(singer)
    if me is None:
        return None
    facts = {"op": "after_start", "queue": rows, "singer": me, "rotation_mode": bool(rotation_mode), "marker_was_top": bool(marker_was_top), "setting_locked": bool(setting_locked)}
    facts["_objects"] = list(queue) + [singer]   # identities, to read the new order back; never sent to Rust
    return facts


def rotation_after(queue):
    """The three flags the commit changes, per singer, in queue order."""
    out = []
    for s in queue:
        out.append({"marker": bool(s.get("rotation_marker", False)), "round_sung": (bool(s["round_sung"]) if "round_sung" in s else None), "has_sung": bool(s.get("has_sung", False))})
    return out


StartShadow.rotation_facts = staticmethod(rotation_facts)


def reweave_facts(queue):
    """The queue as the rotation lock's reweave sees it: [{"active","marker","lock_new","lock_order"}], or None for anything odd (a lock order that is not a number)."""
    out = []
    for singer in queue:
        if not isinstance(singer, dict):
            return None
        songs = singer.get("songs", [])
        kinds = _kinds(songs)
        if kinds is None:
            return None
        try:
            order = int(singer.get("rotation_lock_order", 0) or 0)
        except Exception:
            return None
        out.append({"active": (not singer.get("skipped", False)) and bool(songs) and any(c != "s" for c in kinds), "marker": bool(singer.get("rotation_marker", False)),
                    "lock_new": bool(singer.get("rotation_lock_new", False)), "lock_order": order})
    return out


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
