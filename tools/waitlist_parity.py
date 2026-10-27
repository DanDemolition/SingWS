#!/usr/bin/env python3
"""Stage E waitlist parity: the Rust "Waiting for add" view model (singws-uimodel `waitlist`, `singws-waitlist-cli`) against the app's REAL Python.

Oracle = the app module: the real ``KaraokeApp`` methods ``_waiting_for_add_sections``, ``_pending_acceptance_rows``, ``_waiting_for_add_row``, ``_waiting_for_add_row_text``,
``_waiting_for_add_order_key``, the version / duration / time helpers, ``_waiting_for_add_request_id``, ``_is_waiting_for_add_request``,
``_remote_request_is_non_actionable``, ``_waiting_for_add_status_kind`` and ``_waiting_for_add_reason_text`` bound onto a plain stand-in object. Only the two
questions that need live app state are stubbed on the stand-in, on both sides identically: "is this singer on their first turn" (a set of names) and
"does this request already match an accepted queue entry" (a set of request ids), plus the queue's remote-id set.

Fuzzed: requests with every id / time / duration / version / replacement-track shape the code reads (ints, floats, numeric text, ISO-8601 in many forms incl. time
zones, 'Z', fractions, basic forms, impossible dates, junk), odd types, Unicode names, deferred adds with remote_meta and track merging, the removed-tonight list.
ISO text is also compared directly against ``datetime.fromisoformat`` (parsed or not). Where Python raises the Rust side must raise or decline; inputs it declines
(week dates, 24:00, non-ASCII numerals, Unicode case-table skew) are counted, never compared.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/waitlist_parity.py [--cases N] [--seed S]   (TZ=... to try other zones)
Exit status 1 on any difference.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a scratch SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_WAITLIST_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-waitlist-cli")
failures: list[str] = []
counts: dict[str, int] = {}
ROW_FIELDS = ["request_id", "singer", "artist", "title", "version", "duration", "first_time_singer", "request_time", "server_received_time", "queue_position", "status_kind", "status_label", "selectable"]


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:1200])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


def make_fake(first_time, accepted, local_ids, waiting=None, show_removed=False, removed=None, deferred=None, defer_on=False):
    ka = hp.app().KaraokeApp
    f = types.SimpleNamespace()
    f.settings = {"defer_remote_adds_until_between_singers": defer_on}
    f._waiting_for_add_requests = waiting if waiting is not None else {}
    f._waiting_for_add_show_removed = show_removed
    f._waiting_for_add_removed_tonight = removed if removed is not None else {}
    f._deferred_remote_adds = deferred or []
    for name in dir(ka):
        if name.startswith(("_waiting_for_add_", "_pending_acceptance", "_format_waiting", "_is_waiting_for_add", "_remote_request_is_non_actionable")):
            fn = getattr(ka, name)
            if callable(fn):
                setattr(f, name, types.MethodType(fn, f))
    f._is_first_time_waitlist_singer = lambda name: name in first_time
    f._request_matches_accepted_queue_item = lambda req, identity_sets=None: f._waiting_for_add_request_id(req) in accepted
    f._queue_remote_request_ids = lambda: list(local_ids)
    f._accepted_request_identity_sets = lambda: (set(), set(), set())
    f._deferred_remote_request_ids = lambda: set()
    return f


def row_view(row):
    d = {k: row[k] for k in ROW_FIELDS}
    return d


# ---- generators ----------------------------------------------------------------------------------------------------------------------

DATES = ["2026-10-26", "20261026", "2026-02-30", "2024-02-29", "2025-02-29", "2026-13-01", "0000-01-01", "9999-12-31", "1969-12-31", "2026-10-26", "2026-1-26", "2026/10/26"]
SEPS = ["T", "T", " ", "t", "_", "", "Z", " ", "—"]
TIMES = ["20", "20:05", "20:05:07", "20:05:07.123", "20:05:07.1234567", "2005", "200507", "20:5", "25:00", "20:61", "20:05:61", "2005:07", "20:05:07.", "20:05:07,5",
         "20:05:07.12a", "00:00", "12:00", "12:30", "00:59", "23:59:59", "0", "1:05", "2005071", "20:05:07:08", "09:03"]
TZS = ["", "", "", "Z", "+05:30", "-0800", "+05", "+24:00", "+05:30:15", "-08:00:00.5", "+5", "+0530", "-00:00", "z", "+05:60", "Z"]
JUNK = ["", " ", "n/a", "tonight", "12:30 PM", "Unknown", "none", "1792000000.5", "99999999999999", "1e12", "abc123", "0", "-5", "100000", "100001", "5.5", "  17920000  ",
        "20261026", "2026-10-26T", "T20:05"]
EXOTIC = ["٣", "²", "2026-W43-1", "2026W431", "2026-10-26T24:00", "20261026Z0+05:30"]


def iso_text(rng):
    r = rng.random()
    if r < 0.15:
        return rng.choice(JUNK) if rng.random() < 0.85 else rng.choice(EXOTIC)
    s = rng.choice(DATES)
    if rng.random() < 0.9:
        s += rng.choice(SEPS) + rng.choice(TIMES) + rng.choice(TZS)
    if rng.random() < 0.05:
        s = "  " + s + " "
    return s


def time_value(rng):
    r = rng.random()
    if r < 0.30:
        return iso_text(rng)
    if r < 0.50:
        return rng.choice([1792000000, 1792000000.75, 1700000000, 99999, 100000, 100001, 0, 5, -5, 253402300799, 253402300800, 10**15, True, False])
    if r < 0.60:
        return str(rng.choice([1792000000, 1700000012, 1000, 100001, 20261026, 0, 10**14]))
    if r < 0.63:
        return rng.choice([[], {}, ["x"], {"a": 1}, None, ""])
    return rng.choice(JUNK) if rng.random() < 0.9 else rng.choice(EXOTIC)


NAMES = ["Ann", "Bo", "Cy Young", "Zoë", "  Padded  ", "İstanbul", "O'Neil", "東京", "", None, 7, "ǅ", "Ünal"]
DURS = [245, 245.9, 0, -3, "3:45", "245", " 90 ", "", None, True, "abc", "1e2", 3600, 59.5, "inf", "nan", 10**12, []]
BRANDS = ["SC", "Sound Choice", "unknown", "None", "UNKNOWN", "", None, 5, "KV", "  CC  ", "Zoom", "sc"]
PATHS = ["/a/b.zip", "/a/b.MP3", "/a/b.cdg", "/a/noext", "/a/.hidden", "x.tar.gz", "", None, "/a.b/c", "file.", "/a/b.Zip"]
REASONS = ["", "rotation_full", "host_not_accepting", "sync_failed", "auto_accept_failed", "queue_insert_failed", "no_local_library_match", "previous_auto_match_failure",
           "cdg_missing_mp3", "invalid_zip_format", "unsupported_format_mp4", "failed_needs_review", "other", None]
STATES = ["", "waiting", "failed", "failed_needs_review", "accepted", "active", "delivered", "completed", "sung", "removed", "skipped", "pending", "WAITING", " Failed ", None, "İ"]


def rid_value(rng):
    return rng.choice([rng.randrange(1, 60), rng.randrange(1, 60), str(rng.randrange(1, 60)), 0, None, "x", "3.5", 7.9, True, "  12  ", "1_0", "+4", "-4", []])


def track_fields(rng, d, p=0.4):
    for k in ("selected_version", "selected_brand", "selected_disc_id", "selected_source", "disc_id", "discid", "songid", "song_id", "brand", "source", "version"):
        if rng.random() < p * 0.3:
            d[k] = rng.choice(BRANDS + ["D" + str(rng.randrange(9))])
    if rng.random() < p:
        d["path"] = rng.choice(PATHS)
    if rng.random() < p:
        d[rng.choice(["duration_secs", "duration", "length_secs", "len_secs"])] = rng.choice(DURS)
        if rng.random() < 0.4:  # two keys at once: the first usable one in the app's order wins
            d[rng.choice(["duration_secs", "duration", "length_secs", "len_secs"])] = rng.choice(DURS)


def rand_req(rng, with_ids=True):
    d = {}
    if with_ids:
        if rng.random() < 0.9:
            d["request_id"] = rid_value(rng)
        if rng.random() < 0.15:
            d["id"] = rid_value(rng)
    for k in ("singer", "artist", "title"):
        if rng.random() < 0.9:
            d[k] = rng.choice(NAMES)
    track_fields(rng, d)
    for k in ("state", "status"):
        if rng.random() < 0.5:
            d[k] = rng.choice(STATES)
    for k in ("pending_reason", "attention_reason", "last_error", "pending_status", "desktop_sync_status"):
        if rng.random() < 0.25:
            d[k] = rng.choice(REASONS + ["pending", "pending_desktop_sync", "done", "  Needs Review "])
    for k in ("request_time", "requested_at_client", "requested_at", "created_at", "submitted_at", "received_at", "ts", "time", "received_at_server", "server_received_at"):
        if rng.random() < 0.18:
            d[k] = time_value(rng)
    for k in ("sent", "delivered"):
        if rng.random() < 0.15:
            d[k] = rng.choice([True, False, 1, 0, None, "yes"])
    for k in ("added_at", "queued_at", "completed_at", "removed_at"):
        if rng.random() < 0.15:
            d[k] = rng.choice([0, 5, 1792000000, "1792000000", "x", None, -4, 1.5, [], True])
    if rng.random() < 0.12:
        d["queue_position"] = rng.choice([1, 2, 12, 0, -1, None, "", "3", "x", 2.7, True, [], 10**30])
    if rng.random() < 0.25:
        rep = rng.choice([{}, {}, None, "x", []]) if rng.random() < 0.2 else {}
        if rep == {} and rng.random() < 0.8:
            rep = {}
            for k in ("artist", "title"):
                if rng.random() < 0.6:
                    rep[k] = rng.choice(NAMES)
            track_fields(rng, rep, 0.6)
        d["replacement_track"] = rep
    return d


def rand_ctx(rng, ids):
    pool = ids + [rng.randrange(1, 60) for _ in range(3)]
    return (
        set(rng.sample(NAMES_STR, rng.randrange(0, 4))),
        set(x for x in pool if rng.random() < 0.15),
        [x for x in pool if rng.random() < 0.15],
    )


NAMES_STR = [n for n in NAMES if isinstance(n, str)] + ["None", "7"]


# ---- the checks ----------------------------------------------------------------------------------------------------------------------


def compare(label, py_fn, rust_req, extract, ctxdesc):
    try:
        py = py_fn()
        py_err = None
    except Exception as exc:  # noqa: BLE001
        py, py_err = None, repr(exc)
    r = rust(rust_req)
    if isinstance(r, dict) and "error" in r:
        if "decline" in r["error"]:
            tally(label + "_declined")
            tally("  why: " + r["error"][:60])
            return
        if py_err is None:
            fail(f"{label}: Rust errored ({r['error']}) but Python returned. {ctxdesc}")
        else:
            tally(label + "_both_error")
        return
    if py_err is not None:
        fail(f"{label}: Python raised ({py_err}) but Rust returned. {ctxdesc}")
        return
    a, b = extract(py), extract(r)
    if a != b:
        fail(f"{label}: {ctxdesc}\n python={a!r}\n rust  ={b!r}")
    else:
        tally(label + "_same")


def case_sections(rng):
    n = rng.choice([0, 1, 2, 4, 8])
    waiting = {}
    ids = []
    for _ in range(n):
        req = rand_req(rng)
        key = rng.choice([str(rng.randrange(-2, 60)), str(rng.randrange(1, 60)), str(rng.randrange(1, 60)), "x", " 7 "])
        waiting[key] = req
        ids.append(rng.randrange(1, 60))
    removed = {str(i): rand_req(rng) for i in range(rng.choice([0, 0, 1, 3]))}
    for v in removed.values():
        if rng.random() < 0.8:
            v["removed_at"] = rng.choice([100, 300, 300.5, 1792000000, "250", 0])
    show_removed = rng.random() < 0.5
    defer_on = rng.random() < 0.6
    deferred = []
    for i in range(rng.choice([0, 1, 3])):
        item = {"request_id": rid_value(rng)}
        for k in ("singer", "artist", "title"):
            if rng.random() < 0.7:
                item[k] = rng.choice(NAMES)
        if rng.random() < 0.5:
            item["remote_meta"] = rng.choice([rand_req(rng, with_ids=False), {}, None, rand_req(rng)])
        if rng.random() < 0.5:
            tr = {"artist": rng.choice(NAMES), "title": rng.choice(NAMES)}
            track_fields(rng, tr, 0.7)
            item["track"] = tr
        if rng.random() < 0.4:
            item["request_time"] = time_value(rng)
        if rng.random() < 0.4:
            item["deferred_at"] = time_value(rng)
        deferred.append(rng.choice([item, item, item, "junk", None]))
    first_time, accepted, local_ids = rand_ctx(rng, ids)
    desc = f"waiting={waiting!r} removed={removed!r} show_removed={show_removed} deferred={deferred!r} defer_on={defer_on} first={sorted(first_time, key=str)!r} acc={sorted(accepted)!r} local={local_ids!r}"

    def py():
        f = make_fake(first_time, accepted, local_ids, waiting, show_removed, removed, deferred, defer_on)
        secs = f._waiting_for_add_sections()
        return [
            {"key": s["key"], "title": s["title"], "helper": s.get("helper"), "rows": [dict(row_view(r), text=f._waiting_for_add_row_text(r)) for r in s["rows"]]}
            for s in secs
        ]

    req = {"op": "sections", "waiting": waiting, "show_removed": show_removed, "removed": removed, "local_ids": local_ids, "accepted_ids": sorted(accepted),
           "first_time_names": sorted(first_time, key=str)}
    if defer_on:
        req["pending_acceptance"] = deferred
    compare("sections", py, req, lambda x: x, desc)


def case_row(rng):
    req = rand_req(rng)
    kind = rng.choice(["waitlist", "removed", "pending_acceptance", "active", "", "sung", "NEEDS_REVIEW"])
    selectable = rng.random() < 0.5
    first = rng.random() < 0.5

    def py():
        f = make_fake({req.get("singer")} if first and isinstance(req.get("singer"), str) else set(), set(), [])
        row = f._waiting_for_add_row(req, kind, selectable=selectable)
        row["first_time_singer"] = first if row["first_time_singer"] else False
        return dict(row_view(row), text=f._waiting_for_add_row_text(row))

    # the stand-in answers "first turn" from the singer's raw text, so make Rust's flag the same thing
    f_names = {req.get("singer")} if first and isinstance(req.get("singer"), str) else set()
    rr = {"op": "sections", "waiting": {"1": dict(req)}, "first_time_names": sorted(f_names)}
    # single-row path through "row": the first-time flag is a plain input there
    flag = (req.get("singer") in f_names) if "singer" in req and isinstance(req.get("singer"), str) else False
    compare("row", lambda: (lambda d: d)(_py_row(req, kind, selectable, flag)), {"op": "row", "req": req, "kind": kind, "selectable": selectable, "first_time": flag},
            lambda x: {k: x[k] for k in ROW_FIELDS + ["text"]}, f"req={req!r} kind={kind!r}")
    del rr


def _py_row(req, kind, selectable, flag):
    f = make_fake({req["singer"]} if flag else set(), set(), [])
    row = f._waiting_for_add_row(req, kind, selectable=selectable)
    return dict(row_view(row), text=f._waiting_for_add_row_text(row))


def case_intake(rng):
    req = rand_req(rng)
    f = make_fake(set(), set(), [])
    compare("intake", lambda: {"non_actionable": f._remote_request_is_non_actionable(req), "waiting": f._is_waiting_for_add_request(req), "status_kind": f._waiting_for_add_status_kind(req)},
            {"op": "intake", "req": req}, lambda x: {k: x[k] for k in ("non_actionable", "waiting", "status_kind")}, f"req={req!r}")
    compare("reason", lambda: {"reason": f._waiting_for_add_reason_text(req)}, {"op": "reason", "req": req}, lambda x: x["reason"], f"req={req!r}")


def case_iso(rng):
    text = iso_text(rng) if rng.random() < 0.85 else "".join(rng.choice("0123456789-:TZ+. ,") for _ in range(rng.randrange(0, 20)))
    app = hp.app()

    def py():
        try:
            app.datetime.fromisoformat(text.replace("Z", "+00:00"))
            return {"parsed": True}
        except Exception:  # noqa: BLE001
            return {"parsed": False}

    compare("iso", py, {"op": "isoclock", "text": text.replace("Z", "+00:00")}, lambda x: x["parsed"], f"text={text!r}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20261026)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        case_sections(rng)
        case_row(rng)
        case_intake(rng)
        case_iso(rng)
        case_iso(rng)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
