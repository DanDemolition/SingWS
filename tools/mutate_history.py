#!/usr/bin/env python3
"""Mutation test for singws-uimodel's history module: each mutant must be killed by the Rust tests or by tools/history_parity.py.
Equivalent mutants carry a reason. Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-uimodel/src/history.rs"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.path.expanduser("~/.cache/singws-rust-target"))
PY = os.path.expanduser("~/.venvs/singws/.venv-build-arm64/bin/python")
M = [
    ("if !truthy(v) {\n        return IntOutcome::Value(default);", "if false {\n        return IntOutcome::Value(default);", None),
    ("IntOutcome::Raise => Ok(0),", "IntOutcome::Raise => decline(\"x\"),", None),
    ("Value::Bool(_) => IntOutcome::Value(1),", "Value::Bool(_) => IntOutcome::Value(0),", None),
    ("let t = f.trunc();\n                        if", "let t = f.round();\n                        if", None),
    ("digits.starts_with('_') || digits.ends_with('_') || digits.contains(\"__\")", "digits.starts_with('_')", None),
    ("(\"CC\", &[\"CC\"]),", "(\"CC\", &[\"CC\", \"SC\"]),", None),
    ("let confidence = if coded_suffix { 94 } else if separator_free_text { 90 } else { 88 };", "let confidence = if coded_suffix { 88 } else if separator_free_text { 90 } else { 94 };", "a coded-suffix candidate (next character a digit) can never coexist with another alias match, because every alias is letters only, so the confidence order never decides a winner"),
    ("let long_alias = ak.len() >= 4;", "let long_alias = ak.len() >= 2;", None),
    ("if out.len() >= 10 {", "if out.len() >= 9 {", None),
    ("if c.is_empty() || out.contains(&c) {", "if c.is_empty() {", None),
    ("Some(i) if i > 0 && i < name.len() - 1 => name[..i].to_string(),", "Some(i) if i < name.len() - 1 => name[..i].to_string(),", None),
    ("filter(|p| !p.is_empty() && *p != \".\")", "filter(|p| !p.is_empty())", None),
    ("'\\'' | '\\u{2019}' | '`' | '\\u{b4}'", "'\\'' | '`' | '\\u{b4}'", None),
    ("if !provider_key.is_empty() && provider_key != \"local\" && !provider_track_key.is_empty() {", "if !provider_key.is_empty() && !provider_track_key.is_empty() {", None),
    ("fold_key_part(py_or(&[path, songid]))", "fold_key_part(py_or(&[songid, path]))", None),
    ("let newer = if incoming.updated_at >= existing.updated_at { incoming } else { existing };", "let newer = if incoming.updated_at > existing.updated_at { incoming } else { existing };", None),
    ("} else if merged.$f.is_empty() && !incoming.$f.is_empty() {", "} else if false {", None),
    ("merged.play_count = 0.max(existing.play_count).max(incoming.play_count);", "merged.play_count = existing.play_count + incoming.play_count;", None),
    ("existing.first_performed_at.min(incoming.first_performed_at)", "existing.first_performed_at.max(incoming.first_performed_at)", None),
    ("merged.last_performed_at = existing.last_performed_at.max(incoming.last_performed_at);", "merged.last_performed_at = incoming.last_performed_at;", None),
    ("let total: i64 = songs.iter().map(|s| s.play_count.max(0)).sum();", "let total: i64 = songs.iter().map(|s| s.play_count).sum();", None),
    ("store.singers[pos] = singer; // same position", "store.singers.push(singer); // same position", None),
    ("singers.sort_by(|a, b| a.1.cmp(&b.1));", "singers.sort_by(|a, b| b.1.cmp(&a.1));", None),
    ("if idx >= song_scan_limit {", "if idx > song_scan_limit {", None),
    ("let haystack = py_lower(&format!(\"{} {}\", name, s.preferred_disc_priority))?;", "let haystack = py_lower(&name)?;", None),
    ("if matched.len() < render_limit {", "if matched.len() <= render_limit {", None),
    ("lines.push(format!(\"Prefers: {pref}\"));", "lines.push(format!(\"Prefer: {pref}\"));", None),
    ("Err(_) => out.extend(c.to_lowercase()),", "Err(_) => out.push(c),", None),
    ("keyed.push(((-s.last_performed_at, py_lower(&s.artist)?, py_lower(&s.title)?), s));", "keyed.push(((s.last_performed_at, py_lower(&s.artist)?, py_lower(&s.title)?), s));", None),
    ("if tokens.iter().all(|t| blob.contains(t)) {", "if tokens.iter().any(|t| blob.contains(t)) {", None),
    ("let filter_active = songs.len() != total_unfiltered || !query.is_empty();", "let filter_active = songs.len() != total_unfiltered;", None),
    ("meta.push(format!(\"Key {:+}\", s.last_key));", "meta.push(format!(\"Key {}\", s.last_key));", None),
    ("if total > render_limit {", "if total >= render_limit {", None),
    ("Some(s) if s != shown_total => format!(\" ({s} shown)\"),", "Some(s) if s != shown_total + 1 => format!(\" ({s} shown)\"),", None),
    ("if ts <= 0 {\n        return \"Never\".into();", "if ts < 0 {\n        return \"Never\".into();", None),
    ("if !(1..=9999).contains(&year) {", "if false {", None),
    ("let h12 = if l.hour % 12 == 0 { 12 } else { l.hour % 12 };", "let h12 = l.hour % 12;", None),
    ("if (p.year, p.month0, p.day) == (c.year, c.month0, c.day) {", "if (p.year, p.month0) == (c.year, c.month0) {", None),
    ("let date = if p.year == c.year {", "let date = if p.year != c.year {", None),
    ("let unique = if s.unique_song_count != 0 { s.unique_song_count } else { s.songs.len() as i64 };", "let unique = s.unique_song_count + 0;", "unique_song_count is always the song count after normalisation, so the fallback never differs"),
    ("summary.push_str(&format!(\"  ·  Preferred brand {pref}\"));", "summary.push_str(&format!(\"  ·  Preferred {pref}\"));", None),
]


def run(cmd, timeout=900):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, timeout=timeout)


def main():
    text = SRC.read_text()
    survivors, equivalents = [], []
    try:
        for n, (old, new, reason) in enumerate(M):
            if old not in text:
                print(f"[{n:02d}] PATTERN NOT FOUND {old[:60]!r}")
                survivors.append(n)
                continue
            SRC.write_text(text.replace(old, new, 1))
            try:
                r = run(["cargo", "test", "-p", "singws-uimodel", "--lib", "--quiet"], timeout=300)
            except subprocess.TimeoutExpired:
                print(f"[{n:02d}] killed by unit tests (hang)")
                continue
            if r.returncode != 0:
                print(f"[{n:02d}] killed by {'build' if 'could not compile' in r.stderr else 'unit tests'}  {old[:50]!r}")
                continue
            run(["cargo", "build", "--release", "-p", "singws-uimodel"])
            p = subprocess.run([PY, str(ROOT / "tools/history_parity.py"), "--cases", "14", "--skip-casefold"], capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                print(f"[{n:02d}] killed by parity  {old[:50]!r}")
            elif reason:
                equivalents.append(n)
                print(f"[{n:02d}] EQUIVALENT ({reason[:60]})")
            else:
                survivors.append(n)
                print(f"[{n:02d}] SURVIVED  {old[:70]!r}")
    finally:
        SRC.write_text(text)
        run(["cargo", "build", "--release", "-p", "singws-uimodel"])
    print(f"equivalent: {len(equivalents)}  survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
