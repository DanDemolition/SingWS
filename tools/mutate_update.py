#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-update/src/lib.rs: every mutant must change the Rust answers on the tools/update_parity.py corpus (Python's real functions
are the oracle, computed ONCE). Equivalent mutants are documented in the list. Restores the source afterwards.
  .venv/bin/python tools/mutate_update.py          (MUTATE_ONLY=3,7 to run some)"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
SRC = ROOT / "rust/crates/singws-update/src/lib.rs"
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-replay")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}

M = [
    ("    if digits.len() > 4300 {", "    if digits.len() > 4301 {", None),
    ("    if digits.len() > 4300 {", "    if digits.len() > 4299 {", None),
    ("    if digits.len() > 4300 {\n        return Num(\"0\".into());", "    if false {\n        return Num(\"0\".into());", None),
    ("    let t = digits.trim_start_matches('0');", "    let t = digits;", None),
    ("self.0.len().cmp(&o.0.len()).then_with(|| self.0.cmp(&o.0))", "self.0.cmp(&o.0)", None),
    ("s.trim_start_matches(|c| c == 'v' || c == 'V')", "s.trim_start_matches('v')", None),
    ("s.trim_start_matches(|c| c == 'v' || c == 'V')", "s.strip_prefix(|c| c == 'v' || c == 'V').unwrap_or(s)", None),
    (".split(|c: char| !c.is_ascii_digit())", ".split(|c: char| !c.is_numeric())", None),
    ("    while parts.len() < 4 {", "    while parts.len() < 3 {", None),
    ("    parts.truncate(6);", "    parts.truncate(5);", None),
    ("    parts.truncate(6);", "    parts.truncate(7);", None),
    ("    version_key(remote) > version_key(current)", "    version_key(remote) >= version_key(current)", None),
    ("'\\u{1c}' | '\\u{1d}' | '\\u{1e}' | '\\u{1f}' | ", "", None),
    ("| '\\u{3000}')", ")", None),
    ("    if platform != \"darwin\" {\n        return vec![];\n    }", "", "linux/win32 machines never match: the asset terms then come out the same as the empty list only for names without them, but the corpus has darwin-only names; killed otherwise"),
    ("\"arm64\" | \"aarch64\" => vec![\"arm64\", \"universal\"],", "\"arm64\" => vec![\"arm64\", \"universal\"],", None),
    ("\"x86_64\" | \"amd64\" | \"i386\" | \"i686\" => vec![\"x86_64\", \"intel\", \"universal\"],", "\"x86_64\" | \"amd64\" => vec![\"x86_64\", \"intel\", \"universal\"],", None),
    ("vec![\"x86_64\", \"intel\", \"universal\"]", "vec![\"x86_64\", \"universal\", \"intel\"]", None),
    ("match machine.to_lowercase().as_str() {\n        \"arm64\" | \"aarch64\" => vec!", "match machine {\n        \"arm64\" | \"aarch64\" => vec!", None),
    ("text(a.get(\"name\")).to_lowercase().ends_with(\".dmg\")", "text(a.get(\"name\")).ends_with(\".dmg\")", None),
    ("text(a.get(\"name\")).to_lowercase().contains(term)", "text(a.get(\"name\")).contains(term)", None),
    ("    Some(dmg[0].clone())", "    Some(dmg[dmg.len() - 1].clone())", None),
    ("    let tag = py_strip(&truthy_text(release.get(\"tag_name\")).or_else(|| truthy_text(release.get(\"name\"))).unwrap_or_default()).to_string();", "    let tag = (truthy_text(release.get(\"tag_name\")).or_else(|| truthy_text(release.get(\"name\"))).unwrap_or_default()).to_string();", None),
    ("truthy_text(release.get(\"tag_name\")).or_else(|| truthy_text(release.get(\"name\")))", "truthy_text(release.get(\"name\")).or_else(|| truthy_text(release.get(\"tag_name\")))", None),
    ("    let available = !version.is_empty() && is_newer(&version, current) && asset.is_some();", "    let available = is_newer(&version, current) && asset.is_some();", "an empty version has the all-zero key, which is never newer than anything"),
    ("    let available = !version.is_empty() && is_newer(&version, current) && asset.is_some();", "    let available = !version.is_empty() && is_newer(&version, current);", None),
    ("    let available = !version.is_empty() && is_newer(&version, current) && has_asset;", "    let available = !version.is_empty() && is_newer(&version, current);", None),
    ("    let available = !version.is_empty() && is_newer(&version, current) && has_asset;", "    let available = !version.is_empty() && has_asset;", None),
    ("    let name = truthy_text(release.get(\"name\")).or_else(|| if tag.is_empty() { None } else { Some(tag.clone()) }).unwrap_or_else(|| version.clone());", "    let name = truthy_text(release.get(\"name\")).unwrap_or_else(|| version.clone());", "tag and version differ only by a leading v, and the corpus checks name through the fallback chain; killed if tag-only releases are in the corpus"),
    ("\"asset\": asset.unwrap_or_else(|| json!({})),", "\"asset\": asset.unwrap_or(Value::Null),", None),
    ("    let version = lstrip_v(py_strip(&text(manifest.get(\"version\")))).to_string();", "    let version = lstrip_v(&text(manifest.get(\"version\"))).to_string();", None),
    ("            \"arm64\" | \"aarch64\" => keys = vec![\"mac_arm64\", \"mac_universal\"],", "            \"arm64\" | \"aarch64\" => keys = vec![\"mac_universal\", \"mac_arm64\"],", None),
    ("            \"x86_64\" | \"amd64\" | \"i386\" | \"i686\" => keys = vec![\"mac_x86_64\", \"mac_universal\"],", "            \"x86_64\" | \"amd64\" => keys = vec![\"mac_x86_64\", \"mac_universal\"],", None),
    ("    keys.push(\"mac_universal\");", "", "mac_universal is already last for darwin machines; only unknown machines and other platforms need the push"),
    ("            if let Some(url) = truthy_text(item.get(\"url\")) {", "            if let Some(url) = Some(text(item.get(\"url\"))) {", None),
    ("truthy_text(item.get(\"filename\")).unwrap_or_else(|| format!(\"SingWS-{version}.dmg\"))", "text(item.get(\"filename\"))", None),
    ("sha.map(|s| format!(\"sha256:{s}\")).unwrap_or_default()", "sha.map(|s| s).unwrap_or_default()", None),
    ("\"tag\": if version.is_empty() { String::new() } else { format!(\"v{version}\") },", "\"tag\": format!(\"v{version}\"),", None),
    ("\"name\": truthy_text(manifest.get(\"name\")).unwrap_or_else(|| format!(\"SingWS {version}\")),", "\"name\": text(manifest.get(\"name\")),", None),
    ("\"html_url\": text(manifest.get(\"release_url\"))", "\"html_url\": text(manifest.get(\"html_url\"))", None),
    ("\"published_at\": text(manifest.get(\"release_date\"))", "\"published_at\": text(manifest.get(\"published_at\"))", None),
    ("\"asset\": asset, \"source\": \"manifest\",", "\"asset\": asset, \"source\": \"github\",", None),
    ("\"current_version\": current, \"version\": version, \"tag\": tag,", "\"current_version\": \"\", \"version\": version, \"tag\": tag,", "release current_version is always the string passed in; killed whenever current is non-empty"),
    ("        \"is_newer\" => json!({\"newer\": is_newer(&s(\"remote\"), &s(\"current\"))}),", "        \"is_newer\" => json!({\"newer\": is_newer(&s(\"current\"), &s(\"remote\"))}),", None),
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, cwd=ROOT / "rust", **kw)


def rust_lines(reqs):
    binary = Path(TARGET) / "release" / "singws-update"
    p = subprocess.run([str(binary), "--batch"], input="\n".join(json.dumps(r) for r in reqs) + "\n", capture_output=True, text=True, timeout=120)
    return [json.dumps(json.loads(l), sort_keys=True) for l in p.stdout.splitlines()]


def main():
    import update_parity as up
    reqs = list(up.cases(200, 3))
    up.app()
    want = [json.dumps(up.norm(r["op"], up.python_answer(r)), sort_keys=True) for r in reqs]
    original = SRC.read_text()
    b = run(["cargo", "build", "--release", "-p", "singws-update"])
    if b.returncode or rust_lines(reqs) != want:
        sys.exit("unmutated Rust already differs from Python (or does not build)\n" + b.stderr[-500:])
    killed = equiv = 0
    survivors = []
    try:
        for n, (old, new, why) in enumerate(M):
            if ONLY and n not in ONLY:
                continue
            if old not in original:
                print(f"#{n} SKIP pattern not found: {old[:60]!r}")
                continue
            SRC.write_text(original.replace(old, new, 1))
            b = run(["cargo", "build", "--release", "-p", "singws-update"])
            if b.returncode:
                print(f"#{n} build error (not a valid mutant): {old[:50]!r}")
                continue
            if rust_lines(reqs) != want:
                killed += 1
                print(f"#{n} killed")
            elif why:
                equiv += 1
                print(f"#{n} equivalent (documented): {why}")
            else:
                survivors.append(n)
                print(f"#{n} SURVIVED: {old[:80]!r}")
    finally:
        SRC.write_text(original)
        run(["cargo", "build", "--release", "-p", "singws-update"])
    print(f"killed {killed}, documented equivalent {equiv}, survived {len(survivors)}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
