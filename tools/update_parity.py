#!/usr/bin/env python3
"""Parity of the Rust update rules (rust/crates/singws-update) against the app's REAL Python functions (0.2.18.1.py `_version_key`, `_is_newer_version`,
`_preferred_update_asset`, `GitHubUpdateWorker._release_info` / `_manifest_info`). Pure data in, no network (nothing is fetched; no server is contacted).
`platform.machine()` and `sys.platform` are patched per case, exactly what those functions read.

Corpus: handwritten adversarial versions (leading v/V, 3-6+ parts, junk, empty, very long, Unicode digits, 4300/4301-digit runs), manifests with every
combination of mac_arm64 / mac_x86_64 / mac_universal present or missing (and url/filename/sha variations), releases with assets in different orders and names,
machines arm64 / aarch64 / x86_64 / amd64 / i386 / i686 / other / uppercase, platforms darwin / linux, the real docs/release.json, plus seeded random cases.
Needs the CLI:  cd rust && cargo build --release -p singws-update   (SINGWS_UPDATE_BIN or CARGO_TARGET_DIR/release/singws-update)
  .venv/bin/python tools/update_parity.py [--cases N] [--seed S]       exit 1 on any difference
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import platform
import random
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS", "1")
ROOT = Path(__file__).resolve().parent.parent
if not os.environ.get("SINGWS_HOME") or Path(os.environ["SINGWS_HOME"]).resolve() == (Path.home() / "SingWS").resolve():
    os.environ["SINGWS_HOME"] = tempfile.mkdtemp(prefix="update_parity_home_")
sys.path.insert(0, str(ROOT))

_APP = None


def app():
    global _APP
    if _APP is None:
        spec = importlib.util.spec_from_file_location("singws_main_update_parity", str(ROOT / "0.2.18.1.py"))
        _APP = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _APP
        spec.loader.exec_module(_APP)
    return _APP


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_UPDATE_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-update"),
              os.path.expanduser("~/.cache/singws-rust-target/release/singws-update"), str(ROOT / "rust/target/release/singws-update")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-update not built")


def rust(req: dict, cli: str | None = None) -> dict:
    p = subprocess.run([cli or find_cli()], input=json.dumps(req), capture_output=True, text=True, timeout=20)
    return json.loads(p.stdout)


class patched:
    """sys.platform and platform.machine() as the update functions read them."""

    def __init__(self, plat, machine):
        self.ctx = [mock.patch.object(sys, "platform", plat), mock.patch.object(platform, "machine", lambda: machine)]

    def __enter__(self):
        for c in self.ctx:
            c.start()

    def __exit__(self, *a):
        for c in reversed(self.ctx):
            c.stop()


def python_answer(req: dict) -> dict:
    m = app()
    op = req["op"]
    with patched(req.get("platform", "darwin"), req.get("machine", "")):
        if op == "version_key":
            return {"key": [str(x) for x in m._version_key(req["value"])]}
        if op == "is_newer":
            return {"newer": m._is_newer_version(req["remote"], req["current"])}
        if op == "preferred_asset":
            return {"asset": m._preferred_update_asset(req.get("assets") or [])}
        worker = types.SimpleNamespace(current_version=str(req.get("current") or ""))
        if op == "release_info":
            return m.GitHubUpdateWorker._release_info(worker, req["release"])
        if op == "manifest_info":
            return m.GitHubUpdateWorker._manifest_info(worker, req["manifest"])
    raise ValueError(op)


def norm(op, ans):
    if op == "version_key":  # the Rust key is the decimal text of each part
        return ans
    return json.loads(json.dumps(ans, sort_keys=True))


VERSIONS = ["", "1", "1.0", "1.0.2", "1.0.2.7", "1.0.2.8", "v1.0.2.8", "V1.0.2.8", "vv1.0.2.8", "v", "V", "vV", "  v1.2", "1.0.2.7.1", "1.0.2.7.0.0", "1.0.2.7.0.0.5",
            "1.0.0.0.0.0.0", "0", "00.00.01", "1..2", "1.x.3", "abc", "1.2.3-beta4", "2.0.0-rc.1+build.5", "١٢٣.٤", "1.２.3", "1.0.2.7\n", "٠.٠.٠.١", "9" * 30, "1." + "9" * 4300, "1." + "9" * 4301,
            "0" * 4300 + "7", "0" * 4301 + "7", "1.0.2.10", "1.0.2.9", "10.0.0.0", "1.0.2.07", "1_0_2_8", "v1,0,2,8", None]
MACHINES = ["arm64", "aarch64", "x86_64", "amd64", "i386", "i686", "other", "ARM64", "AMD64", "", "ppc64"]
PLATFORMS = ["darwin", "linux", "win32"]


def dl(kind, ver, *, url=True, filename=True, sha=True):
    item = {}
    if url:
        item["url"] = f"https://example.invalid/{kind}-{ver}.dmg"
    if filename:
        item["filename"] = f"SingWS-{ver}-{kind}.dmg"
    if sha:
        item["sha256"] = "ab" * 32
    return item


def manifests():
    keys = ("mac_arm64", "mac_x86_64", "mac_universal")
    out = []
    for r in range(len(keys) + 1):
        for combo in itertools.combinations(keys, r):
            out.append({"name": "SingWS", "version": "1.0.2.8", "release_date": "2026-11-01", "release_url": "https://example.invalid/r", "downloads": {k: dl(k, "1.0.2.8") for k in combo}})
    base = {"version": "1.0.2.8", "downloads": {"mac_arm64": dl("a", "1"), "mac_universal": dl("u", "1")}}
    out += [
        {}, {"version": ""}, {"version": "v1.0.2.8"}, {"version": "vV1.0.2.8", "downloads": {"mac_universal": dl("u", "1")}}, {"version": " v1.0.2.8 "}, {"version": None, "downloads": None}, {"version": "1.0.2.8", "downloads": []}, {"version": "1.0.2.8", "downloads": {}},
        dict(base, downloads={"mac_arm64": dl("a", "1", url=False), "mac_universal": dl("u", "1")}),
        dict(base, downloads={"mac_arm64": dl("a", "1", filename=False)}), dict(base, downloads={"mac_arm64": dl("a", "1", sha=False)}),
        dict(base, downloads={"mac_arm64": "oops", "mac_x86_64": None, "mac_universal": dl("u", "1")}),
        dict(base, downloads={"mac_arm64": {"url": ""}, "mac_universal": {"url": "x"}}), dict(base, name="", release_url=None, release_date=""),
        dict(base, name="Custom"),
    ]
    return out


def releases():
    names = ["SingWS-1.0.2.8-arm64-installer.dmg", "SingWS-1.0.2.8-x86_64-installer.dmg", "SingWS-1.0.2.8-universal.dmg", "SingWS-1.0.2.8-INTEL.DMG", "notes.txt", "SingWS.dmg",
             "SingWS-1.0.2.8-media-runtime-source.tar.xz", "App-Arm64.Dmg"]
    out = [{}, {"assets": []}, {"tag_name": "v1.0.2.8", "assets": None}, {"tag_name": "", "name": "SingWS 1.0.2.8", "assets": [{"name": "a.dmg"}]},
           {"tag_name": " v1.0.2.8 ", "assets": [{"name": "a.dmg"}]}, {"tag_name": "v1.0.2.8", "name": "N", "html_url": "u", "published_at": "p", "assets": [{"name": "a.dmg", "browser_download_url": "d"}]},
           {"tag_name": "　v1.0.2.8 ", "assets": [{"name": "a.dmg"}]}, {"tag_name": "\x1fv1.0.2.8\x1c", "assets": [{"name": "a.dmg"}]},
           {"tag_name": "vV1.0.2.8", "assets": [{"name": "a.dmg"}]}, {"tag_name": "release", "assets": [{"name": "a.dmg"}]}, {"name": "v2.0", "assets": [{"name": "A.DMG"}]}, {"tag_name": "v1.0.2.8", "assets": [{"name": None}, {}, {"name": "x.dmg"}]}]
    rng = random.Random(1)
    for _ in range(12):
        picked = rng.sample(names, rng.randint(0, len(names)))
        out.append({"tag_name": "v1.0.2.8", "name": "SingWS", "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}"} for n in picked]})
    return out


def cases(n_random: int, seed: int):
    for v in VERSIONS:
        yield {"op": "version_key", "value": v if v is not None else ""}
    for a, b in itertools.product(VERSIONS[:30], VERSIONS[:30]):
        yield {"op": "is_newer", "remote": a or "", "current": b or ""}
    for plat in PLATFORMS:
        for mach in MACHINES:
            for rel in releases():
                yield {"op": "release_info", "release": rel, "current": "1.0.2.7", "platform": plat, "machine": mach}
                yield {"op": "preferred_asset", "assets": rel.get("assets") or [], "platform": plat, "machine": mach}
            for man in manifests():
                for cur in ("1.0.2.7", "1.0.2.8", "", "v9"):
                    yield {"op": "manifest_info", "manifest": man, "current": cur, "platform": plat, "machine": mach}
    real = json.loads((ROOT / "docs" / "release.json").read_text())
    for plat in PLATFORMS:
        for mach in MACHINES:
            for cur in ("1.0.2.6", real.get("version", ""), "9.9"):
                yield {"op": "manifest_info", "manifest": real, "current": cur, "platform": plat, "machine": mach}
    rng = random.Random(seed)
    alphabet = list("0123456789....vV-+_ x\n١٢") + ["10", "99", "0"]
    for _ in range(n_random):
        v = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        c = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        yield {"op": "is_newer", "remote": v, "current": c}
        yield {"op": "release_info", "release": {"tag_name": v, "assets": [{"name": f"{c}.dmg"}]}, "current": c, "platform": "darwin", "machine": rng.choice(MACHINES)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)
    cli = find_cli()
    app()
    total = bad = 0
    for req in cases(args.cases, args.seed):
        total += 1
        want = norm(req["op"], python_answer(req))
        got = norm(req["op"], rust(req, cli))
        if want != got:
            bad += 1
            if bad <= 15:
                print("DIFF", json.dumps(req)[:300], "\n  python:", json.dumps(want)[:300], "\n  rust:  ", json.dumps(got)[:300])
    print(f"update parity: {total - bad}/{total} identical")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
