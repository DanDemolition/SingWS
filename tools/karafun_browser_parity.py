#!/usr/bin/env python3
"""Parity of the Rust browser-provisioning rules (rust/crates/singws-karafun-svc, src/browser.rs, milestone W3) against the REAL Python
`karafun_web.ensure_browser` / `_download_browser` / `downloaded_chrome` / `_platform_key`.

The real functions run in a scratch SINGWS_HOME with a scripted network (index and archive), a scripted `ditto` and a scripted `--version` (nothing is downloaded,
no program is run). Scenarios: folders already downloaded (newest complete one wins), machines, index JSON (valid, missing keys, wrong shapes, other platforms),
the download URL (https and the three Google hosts, userinfo/port/case/suffix tricks), progress calls over chunk boundaries, Content-Length variants, unpack
failure, program missing, version check. Needs:  cd rust && cargo build --release -p singws-karafun-svc
  SINGWS_HOME=$(mktemp -d) .venv-test-arm64-fresh/bin/python tools/karafun_browser_parity.py [--cases N] [--seed S]      exit 1 on any difference
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BASE = Path(tempfile.mkdtemp(prefix="kf_browser_parity_"))
sys.path.insert(0, str(ROOT))
import karafun_web  # noqa: E402


def find_cli() -> str:
    for c in (os.environ.get("SINGWS_KF_SVC_BIN", ""), os.path.join(os.environ.get("CARGO_TARGET_DIR", ""), "release", "singws-karafun-svc"),
              os.path.expanduser("~/.cache/singws-rust-target-karafun-svc/release/singws-karafun-svc"), str(ROOT / "rust/target/release/singws-karafun-svc")):
        if c and os.path.isfile(c):
            return c
    raise FileNotFoundError("singws-karafun-svc not built")


class Resp:
    def __init__(self, data=b"", headers=None):
        self.data, self.pos, self.headers = data, 0, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self.data) - self.pos
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk


def python_result(sc: dict) -> dict:
    home = Path(tempfile.mkdtemp(dir=BASE))
    os.environ["SINGWS_HOME"] = str(home)
    machine = sc["machine"]
    key = "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    root = home / "Chrome-for-Testing"
    for name, is_dir, has_bin, has_complete in sc["existing"]:
        d = root / name
        if not is_dir:
            root.mkdir(parents=True, exist_ok=True)
            d.write_text("x")
            continue
        d.mkdir(parents=True, exist_ok=True)
        if has_bin:
            b = karafun_web._cft_binary(d) if False else d / f"chrome-{key}" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_text("x")
        if has_complete:
            (d / ".complete").write_text("v")
    calls = []
    progress = []

    def opener(url, timeout=30):
        calls.append(url)
        if len(calls) == 1:
            assert url == karafun_web._CFT_INDEX
            if "error" in sc["index"]:
                raise RuntimeError(sc["index"]["error"])
            return Resp(sc["index"]["text"].encode("utf-8"))
        dl = sc["download"]
        if "error" in dl:
            raise RuntimeError(dl["error"])
        hdr = {} if dl.get("content_length") is None else {"Content-Length": dl["content_length"]}

        class H:
            def get(self, k, d=None):
                return hdr.get(k, d)
        r = Resp(b"\0" * dl["bytes"])
        r.headers = H()
        return r

    def fake_run(cmd, **kw):
        if cmd[0] == "/usr/bin/ditto":
            unpacked = Path(cmd[-1])
            if sc["ditto"]["rc"] != 0:
                return subprocess.CompletedProcess(cmd, sc["ditto"]["rc"], "", sc["ditto"]["stderr"])
            if sc["binary_present"]:
                b = unpacked / f"chrome-{key}" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"
                b.parent.mkdir(parents=True, exist_ok=True)
                b.write_text("x")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        vc = sc["version_check"]
        if "error" in vc:
            raise OSError(vc["error"])
        return subprocess.CompletedProcess(cmd, 0, vc["said"], "")

    try:
        with mock.patch("platform.machine", lambda: machine), mock.patch.object(karafun_web.subprocess, "run", fake_run):
            path = karafun_web.ensure_browser(lambda d, t: progress.append([d, t]), urlopen=opener, verify=sc["verify"])
        rel = path.relative_to(root).parts[0]
        if not calls:
            return {"have": rel}
        assert (root / rel / ".complete").is_file()
        return {"ok": rel, "progress": progress}
    except RuntimeError as error:
        msg = str(error)
        if "error" not in sc["index"] and "could not find a browser to download:" in msg:
            return {"error_kind": "index_invalid"}
        return {"error": msg}
    except Exception as error:
        return {"raises": type(error).__name__}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def rust_result(sc: dict, cli: str) -> dict:
    p = subprocess.run([cli], input=json.dumps(dict(sc, kind="browser", op="ensure")), capture_output=True, text=True, timeout=30)
    return json.loads(p.stdout)


URLS = ["https://storage.googleapis.com/chrome-for-testing-public/1.2/mac/chrome.zip", "https://STORAGE.GOOGLEAPIS.COM/x", "http://storage.googleapis.com/x",
        "https://storage.googleapis.com:443/x", "https://user@storage.googleapis.com/x", "https://evil.com@storage.googleapis.com/x", "https://storage.googleapis.com@evil.com/x",
        "https://storage.googleapis.com.evil.com/x", "https://evilstorage.googleapis.com/x", "https://edgedl.me.gvt1.com/x?y#z", "https://dl.google.com", "https://dl.google.com?x",
        "https://dl.google.com#f", "ftp://dl.google.com/x", "//dl.google.com/x", "https:///x", "https://", "https://:443/x", "storage.googleapis.com/x", "https://dl.google.com:evil/x",
        "https://u:p@dl.google.com:80/x", "https://EDGEDL.me.gvt1.com/x", "", "https:", "https://exa mple.com", "https://dl.google.com\\@evil.com", "https://example.com/" + "a" * 60,
        "https://a@b@dl.google.com/x", "HTTPS://dl.google.com/x", "https://dl.google.com./x", "https://dl.google.com/é"]
FOLDERS = ["1.2.3", "99.0.1", "100.0.1", "138.0.7204.49", ".download-x", ".hidden", "abc", "Z", "a", "10", "9"]


def index_text(rng, key, url, version):
    r = rng.random()
    if r < 0.03:
        return '{"channels": {"Stable": {"version": "%s", "n": NaN, "downloads": {"chrome": [{"platform": "%s", "url": "%s"}]}}}}' % (version, key, url)
    if r < 0.05:
        return '{"channels": {"Stable": {"version": "%s", "n": Infinity}}}' % version
    mac = [{"platform": "mac-arm64", "url": url + "?a"}, {"platform": "mac-x64", "url": url}, {"platform": "linux64", "url": "https://dl.google.com/l"}]
    rng.shuffle(mac)
    if r < 0.6:
        return json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": mac}}}})
    if r < 0.7:
        return json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": [d for d in mac if d["platform"] == "linux64"]}}}})
    if r < 0.75:
        return json.dumps({"channels": {"Beta": {}}})
    if r < 0.8:
        return "not json {"
    if r < 0.83:
        return json.dumps({"channels": {"Stable": {"version": version}}})
    if r < 0.86:
        return json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": [{"url": url}]}}}})
    if r < 0.89:
        return json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": ["x"]}}}})
    if r < 0.92:
        return json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": [{"platform": key}]}}}})
    if r < 0.94:
        return json.dumps({"channels": {"Stable": {"version": 138, "downloads": {"chrome": mac}}}})
    if r < 0.96:
        return json.dumps([1, 2])
    return json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": [{"platform": key, "url": url}, {"platform": key, "url": "https://evil.com/x"}]}}}})


def gen(rng: random.Random) -> dict:
    machine = rng.choice(["arm64", "aarch64", "x86_64", "i386", "", "AMD64"])
    key = "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    version = rng.choice(["138.0.7204.49", "1.2.3", "99.0", "100.0"] * 6 + ["", "1/2", ".5", "\u00e91"])
    url = rng.choice(URLS) if rng.random() < 0.7 else URLS[0]
    existing = []
    if rng.random() < 0.3:
        for name in rng.sample(FOLDERS, rng.randint(1, 5)):
            existing.append([name, rng.random() < 0.9, rng.random() < 0.7, rng.random() < 0.7])
    if rng.random() < 0.15 and version and "/" not in version and not version.startswith(".") and version.isascii() and version not in [e[0] for e in existing]:
        existing.append([version, rng.random() < 0.7, rng.random() < 0.5, False])
    n = rng.choice([0, 1, 100, 1 << 20, (1 << 20) - 1, (1 << 20) + 1, 2 * (1 << 20) + 5]) if rng.random() < 0.9 else 100
    cl = rng.choice([None, "", str(n), str(n + 1), str(max(n - 1, 0)), "0", "abc", "-5", "12abc", str(n), str(n)])
    sc = {"machine": machine, "existing": existing, "verify": rng.random() < 0.8,
          "index": {"error": rng.choice(["no internet", "timed out", ""])} if rng.random() < 0.05 else {"text": index_text(rng, key, url, version)},
          "download": {"error": rng.choice(["connection reset", "HTTP 500"])} if rng.random() < 0.05 else {"content_length": cl, "bytes": n},
          "ditto": {"rc": rng.choice([0, 0, 0, 1, 2]), "stderr": rng.choice(["", "  bad zip \n", "x" * 300, "a b\nc", "bad \u00e9 zip", "a\x07b"])},
          "binary_present": rng.random() < 0.9,
          "version_check": {"error": "exec failed"} if rng.random() < 0.08 else {"said": rng.choice(
              [f"Google Chrome for Testing {version}\n", f"Google Chrome for Testing 1.1.1\n", f"Chromium {version}", "", "Google Chrome for Testing " + "z" * 200, f"  {version} Google Chrome for Testing  "])}}
    if rng.random() < 0.5:   # a mostly-good download with at most one thing different, so the later stages are reached
        good = dict(sc, existing=[], index={"text": index_text(random.Random(0), key, URLS[0], version) if False else json.dumps({"channels": {"Stable": {"version": version, "downloads": {"chrome": [{"platform": key, "url": URLS[0]}]}}}})},
                    download={"content_length": rng.choice([None, str(n)]), "bytes": n}, ditto={"rc": 0, "stderr": ""}, binary_present=True,
                    version_check={"said": f"Google Chrome for Testing {version}\n"}, verify=True)
        stage = rng.choice(["index", "download", "ditto", "binary_present", "version_check", "verify", "none"])
        if stage != "none":
            good[stage] = sc[stage]
        if stage == "index" and "text" in sc["index"]:
            good["index"] = {"text": index_text(rng, key, rng.choice(URLS), version)}
        sc = good
    return sc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    cli = find_cli()
    rng = random.Random(a.seed)
    same = diff = declined = 0
    seen: dict = {}
    first = []
    for i in range(a.cases):
        sc = gen(rng)
        py = python_result(sc)
        rs = rust_result(sc, cli)
        if "declined" in rs:
            declined += 1
            seen["declined: " + rs["declined"]] = seen.get("declined: " + rs["declined"], 0) + 1
            continue
        if py == rs:
            same += 1
            k = next(iter(py))
            if k == "error":
                k += ":" + py["error"].split(":")[0][-40:]
            seen[k] = seen.get(k, 0) + 1
        else:
            diff += 1
            if len(first) < 4:
                first.append((i, sc, py, rs))
    print(f"scenarios {a.cases}: identical {same}, different {diff}, declined {declined}")
    print("coverage:", json.dumps(seen, sort_keys=True))
    for i, sc, py, rs in first:
        print(f"--- scenario {i}\nscenario: {json.dumps(sc)[:900]}\npython: {json.dumps(py)[:700]}\nrust:   {json.dumps(rs)[:700]}")
    shutil.rmtree(BASE, ignore_errors=True)
    print("IDENTICAL" if diff == 0 else "DIFFERENT")
    return 0 if diff == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
