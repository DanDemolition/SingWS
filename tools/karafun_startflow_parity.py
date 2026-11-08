"""Startflow rules: exhaustive grid, Rust vs a Python transcription of the lines pinned by test_karafun_startflow_rs.py."""
import itertools, json, os, subprocess, sys

def py(r):
    fast, managed, mstate, explicit, ok, state = r["fast_start"], r["managed_handoff"], r["managed_start_state"], r["explicit_play_sent"], r["probe_ok"], r["probe_state"]
    initial = (mstate if managed else "PLAYING") if fast else state
    # the Python tests the state the flow holds (initial_probe_state); for the non-fast path that is the probe answer
    ips = initial
    unknown = bool(fast and managed and ips == "UNKNOWN")
    if not (ok and ips == "PLAYING") and not unknown and not explicit:
        act = "Press"
    elif explicit:
        act = "SkipExplicit"
    elif unknown:
        act = "DeferToMonitor"
    else:
        act = "SkipAlreadyPlaying"
    low = r["text"].lower()
    return {"action": act, "initial": initial, "verified": bool(ok and ips == "PLAYING" and not fast), "attempts": 12 if r["was_running"] else 30,
            "auto": bool(ok and r["text"].strip() == f"ADJUSTED|{r['key_steps']}|{r['tempo_steps']}"),
            "confirmed": (r["key"] == 0 or "|true|" in low) and (r["tempo"] == 100 or low.endswith("|true"))}

def run_rust(binary, reqs):
    out = subprocess.run([binary], input=json.dumps({"batch": reqs}).encode(), capture_output=True, timeout=120)
    return json.loads(out.stdout)

def grid():
    states = ["PLAYING", "IDLE", "UNKNOWN", ""]
    texts = ["", "ADJUSTED|2|3", " ADJUSTED|2|3\n", "ADJUSTED|true|true", "ADJUSTED|false|true", "ADJUSTED|true|false", "x|TRUE", "x|true|y"]
    for f, m, ms, e, ok, ps, wr, t in itertools.product((0, 1), (0, 1), states, (0, 1), (0, 1), states, (0, 1), texts):
        for ks, ts, k, tp in ((2, 3, 0, 100), (2, 4, 2, 90), (0, 0, 2, 100), (1, 1, 0, 90)):
            yield {"op": "startflow", "fast_start": bool(f), "managed_handoff": bool(m), "managed_start_state": ms, "explicit_play_sent": bool(e), "probe_ok": bool(ok),
                   "probe_state": ps, "was_running": bool(wr), "text": t, "key_steps": ks, "tempo_steps": ts, "key": k, "tempo": tp}

def py_watchdog(now, last, confirmed, duration, seek):
    """The rule inside `_check_duration_watchdog` (0.2.18.1.py), transcribed: extend seconds, or None when the operator is warned."""
    last = float(last or 0.0)
    age = (now - last) if last > 0 else None
    remaining = (lambda d, e, o: int(d) - int(e) - int(o or 0))
    conf = remaining(duration, now - confirmed, seek) if confirmed is not None else None
    if conf is not None and conf > 0:
        return conf + 2.0
    if age is not None and age <= 20.0:
        return 15.0
    return None


def watchdog_cases():
    for last in (None, 0.0, 0.5, 1.0, 5.0, 79.9, 80.0, 99.0):
        for confirmed in (None, 0.0, 40.0, 99.0):
            for duration in (0, 60, 263):
                for seek in (0.0, 30.5):
                    yield {"op": "watchdog_check", "now": 100.0, "last_playing_ts": last, "confirmed_at": confirmed, "duration": duration, "seek_offset": seek}


def main(argv=None):
    binary = os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun")
    if argv and "--binary" in argv:
        binary = argv[argv.index("--binary") + 1]
    reqs = list(grid())
    got = run_rust(binary, reqs)
    bad = [(r, g, py(r)) for r, g in zip(reqs, got) if g != py(r)]
    wreqs = list(watchdog_cases())
    wgot = run_rust(binary, wreqs)
    for r, g in zip(wreqs, wgot):
        want = py_watchdog(r["now"], r["last_playing_ts"], r["confirmed_at"], r["duration"], r["seek_offset"])
        ok = ("warn" in g) if want is None else ("extend" in g and abs(g["extend"] - want) < 1e-9)
        if not ok:
            bad.append((r, g, want))
    print(f"startflow cases={len(reqs) + len(wreqs)} " + ("IDENTICAL" if not bad else f"{len(bad)} DIFFERENCE(S): {bad[0]}"))
    return 0 if not bad else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
