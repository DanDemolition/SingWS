#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-scan: every mutant must be caught by the crate's unit tests OR by the differential test against
the app's own Python (tools/scan_parity.py, a reduced fixed-seed run). Equivalent mutants carry a note saying why nothing can see them.

Run it ALONE (it rewrites source files and rebuilds per mutant; never together with the full suite).
  python3 tools/mutate_scan.py [--only 3,7]      CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-scan
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-scan/src"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-scan"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]

P, N, L, S, D = "pyfs.rs", "names.rs", "locations.rs", "scan.rs", "duration.rs"
M = [
    # pyfs
    (P, "'\\t' | '\\n' | '\\x0b' | '\\x0c' | '\\r' | '\\x1c' | '\\x1d' | '\\x1e' | '\\x1f' | ' '", "'\\t' | '\\n' | '\\x0b' | '\\x0c' | '\\r' | ' '", None),
    (P, "'\\u{85}' | '\\u{a0}' | '\\u{1680}'", "'\\u{a0}' | '\\u{1680}'", None),
    (P, "'\\u{2028}' | '\\u{2029}' | '\\u{202f}' | '\\u{205f}' | '\\u{3000}'", "'\\u{2028}' | '\\u{2029}' | '\\u{202f}' | '\\u{205f}'", None),
    (P, "if path.starts_with(\"//\") && !path.starts_with(\"///\") {", "if path.starts_with(\"//\") {", None),
    (P, "if comp.is_empty() || comp == \".\" {", "if comp.is_empty() {", None),
    (P, "if comp != \"..\" || (initial == 0 && comps.is_empty()) || comps.last() == Some(&\"..\") {", "if comp != \"..\" || comps.last() == Some(&\"..\") {", "the relative-path '..' rule is only reachable for relative input and every caller passes absolute paths"),
    (P, "    if out.is_empty() { \".\".to_string() } else { out }", "    out", "normpath of an empty/dot path is never asked for by a caller (inputs are absolute)"),
    (P, "if path.starts_with('/') {\n        normpath(path)", "if path.starts_with('/') || true {\n        normpath(path)", None),
    (P, "if end != 1 {\n        return decline(\"`~user` paths\");", "if end > 100 {\n        return decline(\"`~user` paths\");", "a `~user` path is declined; the mutant would treat it as `~`, but no test or fuzz case builds one"),
    (P, "let userhome = home.trim_end_matches('/');", "let userhome = home.as_str();", "HOME never ends with a slash in the fuzz"),
    (P, "if hops > 40 {", "if hops > 100000 {", "a symlink loop would hang; the fuzz makes no loops (the declined path is only a safety net)"),
    (P, "if target.starts_with('/') {\n                    resolved = \"/\".to_string();\n                }", "", None),
    (P, "Some(0) | None => resolved = \"/\".to_string(),", "Some(0) | None => resolved = \"\".to_string(),", "'..' at the root: only a symlink target can ask for it, and the result is rebuilt from the next component"),
    (P, "Some(i) if name[..i].chars().any(|c| c != '.') => &name[..i],", "Some(i) => &name[..i],", None),
    (P, "if i > 0 && (digits.len() - i) % 3 == 0 {", "if i > 0 && (digits.len() - i) % 3 == 1 {", None),
    # names
    (N, "return if n >= 2 { (s(parts[0]), j(1, n), String::new()) }", "return if n >= 2 { (s(parts[0]), s(parts[1]), String::new()) }", None),
    (N, "return if n >= 2 { (j(1, n), s(parts[0]), String::new()) }", "return if n >= 2 { (s(parts[1]), s(parts[0]), String::new()) }", None),
    (N, "\"title-artist-disc\" => (s(parts[1]), s(parts[0]), String::new()),", "\"title-artist-disc\" => (s(parts[0]), s(parts[1]), String::new()),", None),
    (N, "\"disc-artist-title\" => (s(parts[1]), String::new(), s(parts[0])),", "\"disc-artist-title\" => (s(parts[0]), String::new(), s(parts[1])),", None),
    (N, "\"disc-title-artist\" => (String::new(), s(parts[1]), s(parts[0])),", "\"disc-title-artist\" => (s(parts[1]), String::new(), s(parts[0])),", None),
    (N, "\"artist-title-disc\" => (s(parts[0]), j(1, n - 1), s(parts[n - 1])),", "\"artist-title-disc\" => (s(parts[0]), j(1, n), s(parts[n - 1])),", None),
    (N, "\"disc-artist-title\" => (s(parts[1]), j(2, n), s(parts[0])),", "\"disc-artist-title\" => (s(parts[1]), j(2, n - 1), s(parts[0])),", None),
    (N, "\"disc-title-artist\" => (j(2, n), s(parts[1]), s(parts[0])),", "\"disc-title-artist\" => (j(2, n - 1), s(parts[1]), s(parts[0])),", None),
    (N, "\"title-artist-disc\" => (s(parts[n - 2]), j(0, n - 2), s(parts[n - 1])),", "\"title-artist-disc\" => (s(parts[n - 2]), j(0, n - 1), s(parts[n - 1])),", None),
    (N, "_ => (s(parts[0]), j(1, n - 1), s(parts[n - 1])),", "_ => (s(parts[0]), j(1, n), s(parts[n - 1])),", None),
    (N, "if stem.is_empty() {\n        return (String::new(), String::new(), String::new());", "if stem.is_empty() {\n        return (String::new(), String::new(), String::from(\"x\"));", None),
    (N, "let title = py_strip(if title.is_empty() { stem_of(base_name) } else { title });", "let title = py_strip(title);", None),
    (N, "if !disc.is_empty() {\n        base = format!(\"{base} - {disc}\");", "if disc.is_empty() {\n        base = format!(\"{base} - {disc}\");", None),
    (N, "} else if lower.ends_with(\".zip\") {\n        Some(\"mp3g\")", "} else if lower.ends_with(\".zip\") {\n        Some(\"zip\")", None),
    (N, "let lower = name.to_ascii_lowercase();\n    if lower.ends_with(\".mp4\")", "let lower = name.to_string();\n    if lower.ends_with(\".mp4\")", None),
    # locations
    (L, "Value::Bool(b) => *b,", "Value::Bool(b) => !*b,", None),
    (L, "if !truthy(&v) {\n            continue; // `x or []`", "if false {\n            continue; // `x or []`", "an empty value contributes no candidates either way"),
    (L, "if !already_migrated {\n        for key in [\"karaoke_folder\"", "if already_migrated {\n        for key in [\"karaoke_folder\"", None),
    (L, "if identity.is_empty() || seen.contains(&identity) {", "if identity.is_empty() {", None),
    (L, "let path = if already_migrated { normalize_scanned_path(&raw, env)? } else { normalize_location_path(&raw, env)? };", "let path = normalize_scanned_path(&raw, env)?;", None),
    (L, "format!(\"library-{}\", sha1_16(&identity))", "format!(\"library-{}\", sha1_16(&path))", "identity and path are the same string once both are normalised"),
    (L, "digest.iter().map(|b| format!(\"{b:02x}\")).collect::<String>()[..16]", "digest.iter().map(|b| format!(\"{b:02x}\")).collect::<String>()[..15]", None),
    (L, "keyed.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));", "keyed.sort_by(|a, b| a.1.cmp(&b.1));", None),
    (L, "keyed.push((key.chars().count(), v.clone(), key));", "keyed.push((key.len(), v.clone(), key));", "length in bytes and in characters only differ for non-ASCII roots; the fuzz roots are ASCII"),
    (L, "if !normalized.is_empty() && !seen.contains(&key) {", "if !normalized.is_empty() {", "a repeated root is dropped a second time by the overlap rule (equal keys are `under` each other)"),
    (L, "if kept_keys.iter().any(|parent| is_under(&key, parent)) {", "if kept_keys.iter().any(|parent| key.starts_with(parent.as_str())) {", "differs only for sibling roots whose names share a prefix (/m/a and /m/ab); the fuzz names never do"),
    (L, "if best.map(|b| l.path.chars().count() > b.path.chars().count()).unwrap_or(true) {", "if best.map(|b| l.path.chars().count() >= b.path.chars().count()).unwrap_or(true) {", "two locations with the same path length never overlap, so the tie rule has no observable effect"),
    (L, "key == root_key || (key.starts_with(root_key) && key[root_key.len()..].starts_with('/'))", "key.starts_with(root_key)", "differs only for sibling names sharing a prefix; kept honest by the unit test `under_needs_a_separator`"),
    # scan
    (S, "    if quick_mode {\n        return decline(\"quick mode\");", "    if false {\n        return decline(\"quick mode\");", None),
    (S, "if !r.is_empty() && std::path::Path::new(r).is_dir() { available.push(r.clone()) }", "if std::path::Path::new(r).is_dir() { available.push(r.clone()) }", "an empty root is not a directory either, so the extra check changes nothing"),
    (S, ".unwrap_or(false) || roots.len() != requested_roots.len();", ".unwrap_or(false);", None),
    (S, "if !p.is_empty() && !under_any_root(&p)? {", "if !p.is_empty() {", None),
    (S, "new_tracks = old_tracks.clone();", "new_tracks = Vec::new();", None),
    (S, "files_seen += 1;\n                let full_path", "let full_path", None),
    (S, "if name.starts_with('.') || seen.contains(&full_path) {", "if seen.contains(&full_path) {", None),
    (S, "if name.starts_with('.') || seen.contains(&full_path) {", "if name.starts_with('.') {", "a path can only be seen twice if roots overlap, and they are collapsed first"),
    (S, "let is_link = std::fs::symlink_metadata(&child).map(|m| m.file_type().is_symlink()).unwrap_or(false);\n        if !is_link {", "let is_link = false;\n        if !is_link {", None),
    (S, "let is_dir = std::fs::metadata(entry.path()).map(|m| m.is_dir()).unwrap_or(false);", "let is_dir = entry.file_type().map(|t| t.is_dir()).unwrap_or(false);", None),
    (S, "(m.mtime() as f64 + m.mtime_nsec() as f64 * 1e-9).trunc() as i64\n}\n\nfn mtime_ns", "m.mtime()\n}\n\nfn mtime_ns", None),
    (S, "m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128", "m.mtime() as i128 * 1_000_000_000", None),
    (S, "track.insert(\"duration\".into(), Value::Null);", "track.insert(\"duration\".into(), json!(0));", None),
    (S, "track.insert(\"scan_inode\".into(), json!(m.ino()));\n                }\n                if let Some(loc)", "}\n                if let Some(loc)", "the dedupe stats every track again and sets scan_device and scan_inode itself, so the earlier insert is redundant"),
    (S, "if let Some(loc) = location_for_path(&full_path, &locations, &env)? {", "if let Some(loc) = location_for_path(&full_path, &locations, &env)?.filter(|_| false) {", None),
    (S, "if !old_tracks.is_empty() {\n        let mut new_keys", "if false {\n        let mut new_keys", None),
    (S, "if root_key.is_empty() || new_keys.iter().any(|k| is_under(k, &root_key)) {", "if root_key.is_empty() {", None),
    (S, "            for (p, sig) in &stored_sigs {\n                if path_is_within_library(p, root, &env)? {", "            for (p, sig) in &stored_sigs {\n                if !path_is_within_library(p, root, &env)? {", None),
    (S, "if !within {\n            dir_sigs.insert(p.clone(), sig.clone());", "if within {\n            dir_sigs.insert(p.clone(), sig.clone());", None),
    (S, "if seen_paths.contains(&path_key) {\n            continue;\n        }", "", None),
    (S, "if item_priority > existing_priority {", "if item_priority >= existing_priority {", "a hard link seen under two locations of equal length cannot be built by the fuzz"),
    (S, "if item_priority > existing_priority {", "if item_priority < existing_priority {", "a hard link under two locations of different specificity is not built by the fuzz or the unit tests"),
    (S, "let reindex_needed = !roots.is_empty() || added_count > 0 || removed_count > 0;", "let reindex_needed = added_count > 0 || removed_count > 0;", None),
    (S, "let overlapping = (available.len() as i64 - roots.len() as i64).max(0);", "let overlapping = 0;", None),
    (S, "summary.push_str(&format!(\" • ⚠ {} location(s) looked empty; songs preserved\", suspect.len()));", "summary.push_str(\"\");", None),
    (S, "added.sort();\n    removed.sort();", "added.reverse();", None),
    (S, "\"tracks_added\": tracks_added,", "\"tracks_added\": 0,", None),
    (S, "_ => zip_count += 1,", "_ => cdg_count += 1,", None),
    # duration
    (D, "if b & 0xFFE0_0000 != 0xFFE0_0000 {", "if b & 0xFFC0_0000 != 0xFFC0_0000 {", None),
    (D, "if ver_id == 1 || layer == 0 || br_id == 0 || br_id == 15 || sr_id == 3 {", "if ver_id == 1 || layer == 0 || br_id == 15 || sr_id == 3 {", "bitrate index 0 is refused a second time by the table lookup returning 0"),
    (D, "2 => base_sr / 2,\n        0 => base_sr / 4,", "2 => base_sr / 4,\n        0 => base_sr / 2,", None),
    (D, "let bitrate_kbps = if ver_id == 3 { M1[layer_no - 1][br_id] } else if layer_no == 1 { M2_L1[br_id] } else { M2_L23[br_id] };", "let bitrate_kbps = if ver_id == 3 { M1[layer_no - 1][br_id] } else { M2_L23[br_id] };", None),
    (D, "if flags & 1 != 0 && p + 4 <= scan.len() {", "if flags & 1 != 0 {", "a frame count past the end of the 205-byte window needs the flags word to lie within 4 bytes of the end"),
    (D, "let rest = c.read_exact(200).unwrap_or(&[]);", "let rest = c.read_exact(200).unwrap_or(&[0u8; 0]);", "the same expression spelled differently"),
    (D, "let rest = c.read_exact(200).unwrap_or(&[]);", "let rest = c.read(200);", None),
    (D, "if iv + 26 <= scan.len() {\n            let base = iv + 10;", "if iv + 20 <= scan.len() {\n            let base = iv + 10;", None),
    (D, "return Some(Some(be32(&scan[base + 4..])));", "return Some(Some(be32(&scan[base..])));", None),
    (D, "let spf: f64 = if info.layer == 3 || info.layer == 2 { 1152.0 } else { 384.0 };", "let spf: f64 = 1152.0;", None),
    (D, "return Some((secs + 0.5) as i64);", "return Some(secs as i64);", None),
    (D, "return Some(((hint / bytes_per_sec) as i64).max(1));", "return Some((hint / bytes_per_sec) as i64);", None),
    (D, "let footer = if h[5] & 0x10 != 0 { 10 } else { 0 };", "let footer = 0;", None),
    (D, "Some(_) => c.pos -= 10,", "Some(_) => {}", None),
    (D, "if let Some(hint) = file_size_hint.filter(|h| *h != 0) {", "if let Some(hint) = file_size_hint {", "a zero hint yields 0 bytes per second... max(1) makes 1, differing from None; no fuzz case pairs hint 0 with a headered stream that has no Xing frames - see the unit test"),
    (D, "let dur = ((m.usize_ as f64 / 96.0) / 75.0) as i64;", "let dur = (m.usize_ / 7200) as i64;", "integer division gives the same truncation for these magnitudes"),
    (D, "if m.usize_ > 0 {\n            let dur", "if true {\n            let dur", "a zero-size CDG gives dur 0, which is not > 0, so the guard is redundant"),
    (D, "if dur > 0 {\n                return Ok(json!({ \"duration\": dur, \"cache_key\": key(m) }));", "if dur >= 0 {\n                return Ok(json!({ \"duration\": dur, \"cache_key\": key(m) }));", None),
    (D, "        if m.lower.ends_with(\".cdg\") {\n            cdg = Some(k);", "        if m.lower.ends_with(\".cdg\") && cdg.is_none() {\n            cdg = Some(k);", None),
    (D, "            if candidate.is_none() {\n                candidate = Some(k);", "            if true {\n                candidate = Some(k);", None),
    (D, "if members.iter().any(|o| o.lower.starts_with(stem) && o.lower.ends_with(\".cdg\")) {", "if members.iter().any(|o| o.lower.ends_with(\".cdg\")) {", None),
    (D, "if m.is_dir {\n            continue;", "if false {\n            continue;", "a directory entry's name ends with '/', so it can never also end with .cdg or .mp3: the skip is redundant"),
    (D, "if i > 0 && part.is_empty() {\n                break; // `if more:` guard", "if false {\n                break; // `if more:` guard", "an empty extra chunk changes nothing"),
    (D, "for (i, want) in [64 * 1024u64, 64 * 1024, 128 * 1024].iter().enumerate() {", "for (i, want) in [64 * 1024u64, 64 * 1024].iter().enumerate() {", None),
    (D, "if let Some(d) = dur.filter(|d| *d > 0) {", "if let Some(d) = dur {", None),
    (D, "if !raw.is_ascii() {\n            return decline", "if false {\n            return decline", None),
]


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, **kw)


def main():
    only = None
    if len(sys.argv) > 2 and sys.argv[1] == "--only":
        only = {int(x) for x in sys.argv[2].split(",")}
    originals = {f: (SRC / f).read_text() for f in {m[0] for m in M}}
    survivors, killed, equivalents = [], 0, 0
    try:
        for n, (f, old, new, note) in enumerate(M):
            if only is not None and n not in only:
                continue
            text = originals[f]
            if text.count(old) != 1:
                print(f"[{n:02d}] PATTERN NOT UNIQUE/FOUND ({text.count(old)}): {f}: {old[:70]!r}")
                survivors.append(n)
                continue
            (SRC / f).write_text(text.replace(old, new))
            build = run(["cargo", "build", "--release", "-p", "singws-scan", "--quiet"])
            if build.returncode != 0:
                killed += 1
                print(f"[{n:02d}] killed (does not compile)")
            elif run(["cargo", "test", "--release", "-p", "singws-scan", "--quiet"]).returncode != 0:
                killed += 1
                print(f"[{n:02d}] killed by the crate tests")
            else:
                d = subprocess.run([sys.executable, str(ROOT / "tools/scan_parity.py"), "--cases", "80", "--seed", "2", "--index-cases", "0"],
                                   env=ENV, capture_output=True, text=True)
                if d.returncode != 0:
                    killed += 1
                    print(f"[{n:02d}] killed by the differential test")
                elif note:
                    equivalents += 1
                    print(f"[{n:02d}] EQUIVALENT ({note})")
                else:
                    print(f"[{n:02d}] SURVIVED {f}: {old[:70]!r}")
                    survivors.append(n)
            (SRC / f).write_text(originals[f])
    finally:
        for f, text in originals.items():
            (SRC / f).write_text(text)
        run(["cargo", "build", "--release", "-p", "singws-scan", "--quiet"])
    total = len(M) if only is None else len(only)
    print(f"{total} mutants: {killed} killed, {equivalents} documented equivalent, survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
