#!/usr/bin/env python3
"""Live comparison helper (dev Mac only, never during a show): runs the REAL Python search ladder (read by AST, see karafun_ladder_parity.py) against the REAL KaraFun
through `osascript`, for the Rust counterpart `singws-karafun-live ladder <artist> <title>`.
   python3 tools/karafun_live_ladder_py.py [artist title]      (needs KaraFun open and signed in, window visible; types into its search field)"""
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import karafun_ladder_parity as lp  # noqa: E402
import karafun_script_parity as par  # noqa: E402

ns = lp.load()
_m, _l, search = par.load_python()
artist, title = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else ("Journey", "Don't Stop Believin'")
queries = par.PY["queries"]({"artist": artist, "title": title})
log = []


def osa(lines, timeout):
    p = subprocess.run(["osascript"] + [x for l in lines for x in ("-e", str(l))], capture_output=True, text=True, timeout=timeout)
    return (p.returncode == 0), (p.stdout or "").strip(), (p.stderr or "").strip()


me = types.SimpleNamespace(_karafun_search_script=lambda **kw: search(**kw), _karafun_search_diag_script=lambda t: par.PY["search_diag"](t),
                           _run_karafun_applescript_sync=lambda script, **kw: osa(script, 120), **{n: ns[n] for n in lp.PREDICATES})
clock = types.SimpleNamespace(sleep=time.sleep)
found = sel = last = ""
raised = None
t0 = time.monotonic()
try:
    found, sel, last = ns["_ladder"](queries, artist, title, me, lambda script, timeout=18: osa(script, 120), log.append, clock)
except RuntimeError as e:
    raised = str(e)
print("queries:", queries)
for l in log:
    print(l)
print(f"found={found!r} selected_query={sel!r} raised={raised!r} elapsed={time.monotonic() - t0:.1f}s")
