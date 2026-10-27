#!/usr/bin/env python3
"""Stage E header / queue-summary parity: the Rust view model (singws-uimodel `header`, `singws-header-cli`) against the app's REAL Python.

Oracle = the app module's real methods bound onto a plain stand-in: ``_refresh_header_chips`` (the four pills; QColor values mapped back to tones),
``_refresh_header_status`` (status line html + tooltip + venue name), ``_set_server_connection_status`` (the state word after a drop), ``_update_queue_eta_label``
(the SINGERS / ETA / ENDS / mode line, ``_header_summary`` and ``_queue_eta_minutes``; the clock is pinned per case) and ``_refresh_queue_summary``. Live state the
app reads is fed identically to both sides: the connection word, network configured, the summed queue seconds, locked / looping flags.
Fuzzed settings (missing keys, falsy and odd types, Unicode venue names), diagnostics strings, queues with unnamed / non-dict / marker entries, seconds at the
rounding and day-wrap points, clocks around midnight and noon. Where Python raises the Rust side must raise or decline.

Run:  QT_QPA_PLATFORM=offscreen /Users/daniel/Documents/SingWS/.venv/bin/python tools/header_parity.py [--cases N] [--seed S]   (TZ=... for other zones)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import history_parity as hp  # noqa: E402  (sets a scratch SINGWS_HOME and loads the app module)

CLI = os.environ.get("SINGWS_HEADER_CLI") or os.path.join(os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), "release", "singws-header-cli")
failures: list[str] = []
counts: dict[str, int] = {}
TONES = {(52, 224, 138): "good", (245, 200, 75): "warn", (150, 156, 184): "mute", (168, 85, 247): "purple"}
CUR = [1_792_000_000]


def fail(msg):
    failures.append(msg)
    if len(failures) <= 25:
        print("DIFF:", msg[:1100])


def tally(k, n=1):
    counts[k] = counts.get(k, 0) + n


def rust(req):
    out = subprocess.run([CLI], input=json.dumps(req), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return json.loads(out.stdout)


class Rec:
    def __init__(self):
        self.calls = []
        self.t = None
        self.tip = None

    def set_state(self, text, color):
        self.calls.append((text, TONES[(color.red(), color.green(), color.blue())]))

    def setText(self, t):
        self.t = t

    def setToolTip(self, t):
        self.tip = t


class DT(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromtimestamp(CUR[0])


def app():
    m = hp.app()
    m.datetime = DT
    return m


def bind(f, cls, names):
    for n in names:
        setattr(f, n, types.MethodType(getattr(cls, n), f))


def compare(label, py_fn, req, extract_rust, ctx):
    try:
        py, err = py_fn(), None
    except Exception as exc:  # noqa: BLE001
        py, err = None, repr(exc)
    r = rust(req)
    if "error" in r:
        if "decline" in r["error"]:
            tally(label + "_declined")
            return
        if err is None:
            fail(f"{label}: Rust errored ({r['error']}) but Python returned {py!r}: {ctx}")
        else:
            tally(label + "_both_error")
        return
    if err is not None:
        fail(f"{label}: Python raised ({err}) but Rust returned {r!r}: {ctx}")
        return
    got = extract_rust(r)
    if got != py:
        fail(f"{label}: {ctx}\n python={py!r}\n rust  ={got!r}")
    else:
        tally(label + "_same")


VENUES = ["Joe's Bar", "  Padded  ", "", None, 0, 7, "Café Ünal", "東京", "<b>x</b>", False]
CONNS = [None, "", "Connected", "Offline", "Reconnecting", "Auth failed", 0, "Ünal"]
DIAG = [None, "", "12:01:02", "boom", "Ünal", 0, 5]


def rand_settings(rng):
    s = {}
    for k in ("requests_accepting", "use_waiting_for_add"):
        if rng.random() < 0.8:
            s[k] = rng.choice([True, False, 1, 0, None, "", "x", [], [1]])
    for k in ("location_name", "venue_name", "tenant", "user"):
        if rng.random() < 0.4:
            s[k] = rng.choice(VENUES)
    if rng.random() < 0.7:
        s["queue_mode"] = rng.choice(["rotation", "classic", " Rotation ", "ROTATION", "", None, "other", 5])
    return s


def case_chips(rng):
    s = rand_settings(rng)
    conn = rng.choice(CONNS)
    net = rng.random() < 0.5
    summ = rng.choice([None, (0, ""), (1, ""), (5, "9:41 PM"), (2, "12:05 AM"), (30, "x")])

    def py():
        a = app()
        f = types.SimpleNamespace(settings=s, header_chips={k: Rec() for k in ("requests", "waitlist", "online", "summary")})
        f._ui_redesign_on = lambda: True
        f.is_network_configured = lambda: net
        if conn is not None:
            f._server_connection_state = conn
        if summ is not None:
            f._header_summary = summ
        a.KaraokeApp._refresh_header_chips(f)
        out = [c.calls[0] for c in (f.header_chips[k] for k in ("requests", "waitlist", "online", "summary"))]
        return [list(x) for x in out]

    compare("chips", py, {"op": "chips", "settings": s, "connection": conn, "network_configured": net, "summary": list(summ) if summ else None},
            lambda r: r["chips"], f"{s!r} conn={conn!r} net={net} summ={summ!r}")


def case_status(rng):
    s = rand_settings(rng)
    conn = rng.choice(CONNS)
    net = rng.random() < 0.5
    last = {k: rng.choice(DIAG) for k in ("received", "accepted", "sync", "error")}
    att = rng.choice([None, {}, {"1": {}, "2": {}}, [], [1]])
    rev = rng.choice([None, 0, 7, "9", "x", 3.7, True, [], 10 ** 6])

    def py():
        a = app()
        f = types.SimpleNamespace(settings=s, header_status_label=Rec(), header_location_label=Rec())
        f._refresh_header_chips = lambda: None
        f.is_network_configured = lambda: net
        if conn is not None:
            f._server_connection_state = conn
        for k, attr in (("received", "_last_request_received_summary"), ("accepted", "_last_request_accepted_summary"), ("sync", "_last_sync_time_label"), ("error", "_last_server_error")):
            if last[k] is not None:
                setattr(f, attr, last[k])
        if att is not None:
            f._remote_attention_requests = att
        if rev is not None:
            f._queue_revision = rev
        f.header_status_label.hasattr = True
        a.KaraokeApp._refresh_header_status(f)
        if f.header_status_label.t is None:
            raise RuntimeError("the app swallowed an error before setting the label")
        html = f.header_status_label.t
        return {"html": html, "tip": f.header_status_label.tip, "loc": f.header_location_label.t}

    def mine(r):
        a = app()
        return {"html": f'<span style="color:{a._v(r["dot_token"])};">●</span>  {r["text"]}', "tip": r["tooltip"], "loc": f"• {r['location']}"}

    last_in = {k: v for k, v in last.items()}
    compare("status", py, {"op": "status", "settings": s, "connection": conn, "network_configured": net, "last": last_in, "attention": att if isinstance(att, dict) else None, "queue_rev": rev},
            mine, f"{s!r} conn={conn!r} last={last!r} att={att!r} rev={rev!r}")


def case_disconnected(rng):
    msg = rng.choice(["", "Auth rejected", "connect FAILED", "timed out", "  failed  ", "AUTHORISED", "İ failed", "Ünal", "no auth", "authentication", "refused"])

    def py():
        a = app()
        f = types.SimpleNamespace(settings={})
        for n in ("_network_circuit_success", "_refresh_header_status", "_flush_host_request_sync_ops", "_sync_remote_removal_tombstones_async"):
            setattr(f, n, lambda *x, **k: None)
        f._is_requests_accepting_cached = lambda: True
        a.KaraokeApp._set_server_connection_status(f, False, msg)
        return f._server_connection_state

    compare("disconnected", py, {"op": "disconnected", "message": msg}, lambda r: r["state"], repr(msg))


def rand_queue(rng):
    q = []
    for _ in range(rng.choice([0, 1, 2, 4, 7])):
        r = rng.random()
        if r < 0.06:
            q.append(rng.choice(["junk", 5, None]))
            continue
        s = {"name": rng.choice(["Ann", "Bo & <Co>", "  ", "", None, 7, "O'Neil", "Zoë \"Z\""])}
        if rng.random() < 0.3:
            s["rotation_marker"] = rng.choice([True, True, False, 1, 0, None])
        q.append(s)
    return q


def case_eta(rng):
    q = rand_queue(rng)
    total = rng.choice([0, -5, 0.4, 59.5, 60, 1500, 1499.5, 1500.5, 3600, 90, 7200.2, 86400, 90000, 30, 89.9, 45.5, 150.0, 4000])
    now = rng.choice([1_792_000_000, 1_792_043_200, 1_791_999_999, 1_792_031_999 - 1, 1_700_000_000 + rng.randrange(86400), 1_780_000_000 + rng.randrange(10 ** 7)])
    mode = rng.choice(["rotation", "classic", " Rotation ", "ROTATION", "", "other"])
    locked, looping = rng.random() < 0.3, rng.random() < 0.3
    s = {"queue_mode": mode}

    def py():
        CUR[0] = now
        a = app()
        f = types.SimpleNamespace(settings=s, queue=q, queue_label=Rec(), _intro_loop_active=looping)
        f._rotation_recompute_round_state = lambda force_reset=False: None
        f._compute_queue_eta_seconds = lambda: (len(q), total)
        f._is_rotation_locked = lambda: locked
        f._refresh_header_chips = lambda: None
        f._refresh_queue_summary = lambda: None
        bind(f, a.KaraokeApp, ("_fmt_m_ss", "_is_rotation_mode", "_rotation_marker_index"))
        a.KaraokeApp._update_queue_eta_label(f)
        return {"html": f.queue_label.t, "summary": list(f._header_summary), "minutes": f._queue_eta_minutes}

    def mine(r):
        return {"html": r["html"], "summary": [r["singer_count"], r["ends"]], "minutes": r["minutes"]}

    compare("eta", py, {"op": "eta", "queue": q, "total_secs": total, "now": now, "queue_mode": mode, "locked": locked, "looping": looping}, mine, f"q={q!r} total={total} now={now} mode={mode!r}")


def case_summary(rng):
    count, mins = rng.choice([0, 1, 2, 5, 40]), rng.choice([0, 1, 27, 500])
    locked, looping = rng.random() < 0.4, rng.random() < 0.4

    def py():
        a = app()
        f = types.SimpleNamespace(_header_summary=(count, ""), _queue_eta_minutes=mins, _intro_loop_active=looping, queue_summary_label=Rec())
        f._ui_redesign_on = lambda: True
        f._is_rotation_locked = lambda: locked
        a.KaraokeApp._refresh_queue_summary(f)
        return f.queue_summary_label.t

    compare("summary", py, {"op": "summary", "count": count, "minutes": mins, "locked": locked, "looping": looping}, lambda r: r["text"], f"{count} {mins} {locked} {looping}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args(argv)
    if not os.path.exists(CLI):
        print(f"missing {CLI}; build with: cd rust && cargo build --release -p singws-uimodel", file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    for _ in range(args.cases):
        case_chips(rng)
        case_status(rng)
        case_disconnected(rng)
        case_eta(rng)
        case_summary(rng)
    print("compared:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("RESULT: IDENTICAL" if not failures else f"RESULT: {len(failures)} DIFFERENCE(S)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
