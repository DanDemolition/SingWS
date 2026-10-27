#!/usr/bin/env python3
"""Mutation test for the Stage E text view models (singws-uimodel `ticker`, `hero`, `waitlist`): every mutant must be killed by the module's Rust unit tests
or by its differential parity tool against the app's real Python. Equivalent mutants carry a reason. Originals are restored afterwards (also on Ctrl-C).
Exit 1 when a non-equivalent mutant survives.

Usage:  CARGO_TARGET_DIR=~/.cache/singws-rust-target-ui python3 tools/mutate_uimodel_text.py ticker|hero|waitlist|all
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "rust/crates/singws-uimodel/src"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-ui"), QT_QPA_PLATFORM="offscreen")
PY = os.environ.get("SINGWS_PY") or "/Users/daniel/Documents/SingWS/.venv/bin/python"
CARGO = os.path.expanduser("~/.cargo/bin/cargo")

MUTANTS = {
    "ticker": [
        ('if truthy(get(singer, "skipped")) {\n        return false;', "if false {\n        return false;", None),
        ('if !truthy(get(song, "skipped")) {\n            return true;', 'if truthy(get(song, "skipped")) {\n            return true;', None),
        ("if !song.is_object() {\n            return true; // old format", "if song.is_object() {\n            return true; // old format", None),
        ("if !truthy(song) {\n            continue;", "if false {\n            continue;", None),
        (".filter(|a| a.len() >= 3)", ".filter(|a| a.len() >= 2)", None),
        ("out.push(if truthy(&duet) { duet }", "out.push(if !truthy(&duet) { duet }", None),
        ('("%curSinger", cur_singer.clone()),', '("%curSinger", cur_artist.clone()),', None),
        ('format!("Next: {next_singer}")', 'format!("Next {next_singer}")', None),
        ('if cur_singer.is_empty() { String::new() } else { format!("Current: {cur_singer}") }', 'if !cur_singer.is_empty() { String::new() } else { format!("Current: {cur_singer}") }', None),
        ('("%curSong", cur_song),', '("%curSongs", cur_song),', None),
        ('format!("{cur_artist} \\u{2022} {cur_title}")', 'format!("{cur_artist} - {cur_title}")', None),
        ("let p = collapse_doubled(&p, '-');", "let p = p;", None),
        ("let p = collapse_doubled(&p, '\\u{2022}');", "let p = p;", None),
        ("matches!(c, ' ' | '-' | '\\u{2022}')", "matches!(c, ' ' | '-')", None),
        ("if !p.is_empty() {\n            cleaned.push(p);", "if true {\n            cleaned.push(p);", None),
        ('cleaned.join(" | ")', 'cleaned.join("|")', None),
        ('.join("   |   ");', '.join(" | ");', None),
        ("if formatted.is_empty() { msg.to_string() }", "if !formatted.is_empty() { msg.to_string() }", None),
        ('format!("{}. {}", i + 1, n)', 'format!("{}. {}", i, n)', None),
        ("if truthy(msg_v) && !msg_v.is_string() {", "if false {", "a non-text message reads as empty text, which switches the custom message off just the same"),
        ("if custom_enabled && !custom_msg.is_empty() {", "if custom_enabled {", "an empty message renders to an empty message either way"),
        ("(truthy(enabled_v), py_strip", "(true, py_strip", None),
        ("if !s.is_object() {\n            return singws_dbw::pyval::decline", "if false {\n            return singws_dbw::pyval::decline", None),
        ("let Some(list) = songs.as_array() else { return false };", "let Some(list) = songs.as_array() else { return true };", "a truthy non-list songs value is not something the queue holds; Python would raise iterating a number"),
        ("msg = msg.replace(token, value);", "msg = msg.replacen(token, value, 1);", None),
        ("for part in msg.split('|') {", "for part in msg.split('\\n') {", None),
    ],
    "hero": [
        ('if low.is_empty() {\n        return Ok("KJ".into());', 'if false {\n        return Ok("KJ".into());', "an empty name matches no suffix and has no extension, so it falls through to the same KJ"),
        ('(".mkv", "MP4")', '(".mkv", "MKV")', None),
        ('(".mov", "MP4")', '(".mov", "MOV")', None),
        ('(".zip", "ZIP")', '(".zip", "CDG")', None),
        ('(".wav", "WAV")', '(".wav", "WAVE")', None),
        ("let ext = splitext_ext(&low).replace('.', \"\");", "let ext = splitext_ext(&low).to_string();", None),
        ('if up.is_empty() { "KJ".into() } else { up }', 'if up.is_empty() { "".into() } else { up }', None),
        ('.join(" \\u{b7} ")', '.join(" - ")', None),
        ('"Ready to sing".to_string()', '"Ready".to_string()', None),
        (".filter(|p| !p.is_empty())", ".filter(|_| true)", None),
        ('starts_with("karafun_streaming:")', 'starts_with("karafun:")', None),
        ("} else if singws_dbw::pyval::truthy(path) {", "} else if true {", None),
        ("if txt.is_empty() || txt.starts_with('-') { txt }", "if txt.starts_with('-') { txt }", None),
        ('format!("-{txt}")', 'format!("{txt}")', None),
        ("f.round_ties_even()", "f.round()", None),
        ("(f.round_ties_even() as i128).max(0)", "f.round_ties_even() as i128", None),
        ('format!("{:02}:{:02}", secs / 60, secs % 60)', 'format!("{:02}:{:02}", secs % 60, secs / 60)', None),
        ("Value::Bool(b) => Some(*b as u8 as f64),", "Value::Bool(_) => None,", None),
        ("let lead = base.len() - base.trim_start_matches('.').len();", "let lead = 0;", None),
        ("py_lower(py_strip(&py_str_or_empty(song_path)?))?", "py_lower(&py_str_or_empty(song_path)?)?", None),
        ("let meta = py_strip(&py_str_or_empty(brand)?).to_string();", "let meta = py_str_or_empty(brand)?;", None),
    ],
    "waitlist": [
        ("let numeric = matches!(value, Value::Bool(_) | Value::Number(_)) || str_is_digits(value)?;", "let numeric = matches!(value, Value::Number(_)) || str_is_digits(value)?;", None),
        ("let numeric = matches!(value, Value::Bool(_) | Value::Number(_)) || str_is_digits(value)?;", "let numeric = matches!(value, Value::Bool(_) | Value::Number(_));", None),
        ("fn z_to_offset(text: &str) -> String {\n    text.replace('Z', \"+00:00\")", "fn z_to_offset(text: &str) -> String {\n    text.replace('Z', \"\")", None),
        ("if stamp > 100000 {\n                    if let Some(t) = stamp_clock(stamp) {\n                        return Ok(t);\n                    }\n                } else {\n                    continue;", "if stamp > 100001 {\n                    if let Some(t) = stamp_clock(stamp) {\n                        return Ok(t);\n                    }\n                } else {\n                    continue;", None),
        ("if stamp > 100000 {\n                if let Some(t) = stamp_clock(stamp) {\n                    return Ok(t);\n                }\n            }\n        }\n        return Ok(text);", "if stamp > 0 {\n                if let Some(t) = stamp_clock(stamp) {\n                    return Ok(t);\n                }\n            }\n        }\n        return Ok(text);", None),
        ("if secs <= 0 {\n                Ok(String::new())", "if secs < 0 {\n                Ok(String::new())", None),
        ('format!("{}:{:02}", secs / 60, secs % 60)', 'format!("{}:{:02}", secs % 60, secs / 60)', None),
        ("for source in [replacement, req] {", "for source in [req, replacement] {", None),
        ('for key in ["duration_secs", "duration", "length_secs", "len_secs"] {', 'for key in ["duration", "duration_secs", "length_secs", "len_secs"] {', None),
        ('let source = if rep_ok { "Replacement" } else { "Requested" };', 'let source = if !rep_ok { "Replacement" } else { "Requested" };', None),
        ('if low != "unknown" && low != "none" && !pieces.contains(&value) {', 'if low != "unknown" && !pieces.contains(&value) {', None),
        ('if low != "unknown" && low != "none" && !pieces.contains(&value) {', 'if low != "unknown" && low != "none" {', None),
        (".take(3)", ".take(2)", None),
        ("if !ext.is_empty() && !pieces.contains(&ext) {", "if !ext.is_empty() {", None),
        ('"waitlist" => "Waitlisted"', '"waitlist" => "Waiting"', None),
        (".unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1))", ".unwrap_or(std::cmp::Ordering::Equal)", None),
        ("let mut order = f64::INFINITY;", "let mut order = 0.0;", None),
        ("if rid_int <= 0 || ctx.local_ids", "if rid_int < 0 || ctx.local_ids", None),
        ("if rid_int <= 0 || ctx.local_ids.contains(&(rid_int as i64)) || ctx.accepted_ids", "if rid_int <= 0 || ctx.accepted_ids", None),
        ("|| ctx.accepted_ids.contains(&request_id(req)?) {\n                continue;", "{\n                continue;", None),
        ("rows.sort_by(|a, b| b.removed_at.partial_cmp(&a.removed_at)", "rows.sort_by(|a, b| a.removed_at.partial_cmp(&b.removed_at)", None),
        ("if show_removed {", "if true {", None),
        ('status = format!("{status} - First turn");', 'status = format!("{status} - First Turn");', None),
        ("if n > 0 {\n                queue_text", "if n >= 0 {\n                queue_text", None),
        ('if truthy(get(req, "sent")) || truthy(get(req, "delivered")) {\n            return Ok(true);\n        }\n        let sync', 'if truthy(get(req, "sent")) && truthy(get(req, "delivered")) {\n            return Ok(true);\n        }\n        let sync', None),
        ('"" | "pending" | "pending_desktop_sync"', '"" | "pending"', None),
        ("if non_actionable(req)? {\n        return Ok(false);", "if false {\n        return Ok(false);", None),
        ('|| removed_at > 0 {', '|| removed_at >= 0 {', None),
        ('|| completed_at > 0 {', '|| completed_at >= 0 {', None),
        ("if !status.is_empty() {\n        return Ok(status);", "if false {\n        return Ok(status);", None),
        ('reason.starts_with("unsupported_format")', 'reason == "unsupported_format"', None),
        ("d > days_in_month(y as i32, m)", "d > 31", None),
        ("(y % 4 == 0 && y % 100 != 0) || y % 400 == 0", "y % 4 == 0", None),
        ("if y < 1 || !(1..=12).contains(&m)", "if y < 0 || !(1..=12).contains(&m)", None),
        ("if h > 23 || mi > 59 || s > 59 {\n        return Ok(None);", "if h > 24 || mi > 59 || s > 59 {\n        return Ok(None);", "hour 24 is declined just before this check"),
        ("if total >= 86400 {", "if total > 86400 {", None),
        ('req.entry("request_id").or_insert(Value::from(rid as i64));', 'req.insert("request_id".into(), Value::from(rid as i64));', None),
        ('req.insert("queue_position".into(), Value::from(i as i64 + 1));', 'req.insert("queue_position".into(), Value::from(i as i64));', None),
        ("let secs = days * 86400 + clock - off;", "let secs = days * 86400 + clock + off;", None),
        ("for i in 0..6 {", "for i in 0..5 {", None),
        ('if needs_review { ("needs_review".to_string()', 'if false { ("needs_review".to_string()', None),
        ("let a = if track_truthy {", "let a = if false {", None),
        ("rows.sort_by(|a, b| cmp_key(&a.order, &b.order));\n    Ok(rows)", "Ok(rows)", None),
        ("wait_rows.sort_by(|a, b| cmp_key(&a.order, &b.order));", "", None),
        ("let lead = base.len() - base.trim_start_matches('.').len();", "let lead = 0;", None),
        ("if c == b'_' {\n                    if !(i > 0 && i + 1 < b.len()", "if false {\n                    if !(i > 0 && i + 1 < b.len()", None),
        ('if ctx.pending_accepted_ids.contains(&request_id(&req)?) {', 'if ctx.accepted_ids.contains(&request_id(&req)?) {', None),
    ],
}

ONLY = {int(x) for x in os.environ.get("MUTANTS", "").split(",") if x}
MODULE_TEST = {"ticker": "ticker", "hero": "hero", "waitlist": "waitlist"}
PARITY = {"ticker": ("ticker_parity.py", ["--cases", "250"]), "hero": ("hero_parity.py", ["--cases", "300"]), "waitlist": ("waitlist_parity.py", ["--cases", "50", "--seed", "99"])}


def run(cmd, timeout):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, timeout=timeout)


def one(module):
    src = SRC_DIR / f"{module}.rs"
    text = src.read_text()
    survivors, equivalents, killed = [], [], 0
    try:
        for n, (old, new, reason) in enumerate(MUTANTS[module], 1):
            if ONLY and n not in ONLY:
                continue
            if old not in text:
                print(f"[{module} {n:02d}] PATTERN NOT FOUND {old[:60]!r}")
                survivors.append(n)
                continue
            src.write_text(text.replace(old, new, 1))
            try:
                r = run([CARGO, "test", "--release", "-p", "singws-uimodel", "--lib", MODULE_TEST[module], "--quiet"], 400)
            except subprocess.TimeoutExpired:
                print(f"[{module} {n:02d}] killed by unit tests (hang)")
                killed += 1
                continue
            if r.returncode != 0:
                print(f"[{module} {n:02d}] killed by {'build' if 'could not compile' in r.stderr else 'unit tests'}  {old[:50]!r}")
                killed += 1
                continue
            b = run([CARGO, "build", "--release", "-p", "singws-uimodel"], 400)
            if b.returncode != 0:
                print(f"[{module} {n:02d}] killed by build  {old[:50]!r}")
                killed += 1
                continue
            tool, extra = PARITY[module]
            p = subprocess.run([PY, str(ROOT / "tools" / tool), *extra], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=1500)
            if p.returncode != 0:
                print(f"[{module} {n:02d}] killed by parity  {old[:50]!r}")
                killed += 1
            elif reason:
                equivalents.append(n)
                print(f"[{module} {n:02d}] EQUIVALENT ({reason[:70]})")
            else:
                survivors.append(n)
                print(f"[{module} {n:02d}] SURVIVED  {old[:70]!r}")
    finally:
        src.write_text(text)
        run([CARGO, "build", "--release", "-p", "singws-uimodel"], 400)
    print(f"{module}: {len(MUTANTS[module])} mutants, killed {killed}, equivalent {equivalents}, survivors {survivors}")
    return survivors


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    mods = list(MUTANTS) if which == "all" else [which]
    bad = []
    for m in mods:
        bad += one(m)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
