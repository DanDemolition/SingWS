#!/usr/bin/env python3
"""Mutation test for the log redactor / last-show package: rust/crates/singws-logpack (lib.rs, main.rs), logpack_shadow.py and the hook in
0.2.18.1.py's `_build_last_show_package`. A mutant is killed when the crate's unit tests, tools/logpack_parity.py (Rust files) or
test_logpack_shadow.py (Python files) fail. Equivalent mutants carry a note. Run it ALONE (it rewrites source files and rebuilds per mutant).

  python3 tools/mutate_logpack.py [--only 3,7]     CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-logpack
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "rust/crates/singws-logpack"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-logpack"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, QT_QPA_PLATFORM="offscreen")
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
PY = os.environ.get("SINGWS_PYTHON", "/Users/daniel/Documents/SingWS/.venv/bin/python")
LIB, MAIN, SHADOW, APP = CRATE / "src/lib.rs", CRATE / "src/main.rs", ROOT / "logpack_shadow.py", ROOT / "0.2.18.1.py"

M = [
    # --- Python's \s and \d, and (?i)
    (LIB, "0x1c..=0x20 | 0x85", "0x1d..=0x20 | 0x85", None),
    (LIB, "0x1c..=0x20 | 0x85 | 0xa0", "0x1c..=0x20 | 0xa0", None),
    (LIB, "| 0x3000)", ")", None),
    (LIB, "'k' => c == '\\u{212a}',", "'k' => c == '\\u{212b}',", None),
    (LIB, "'i' => c == '\\u{130}' || c == '\\u{131}',", "'i' => c == '\\u{130}',", None),
    (LIB, "'s' => c == '\\u{17f}',", "'s' => c == '\\u{17e}',", None),
    (LIB, "return c.to_ascii_lowercase() == lit.to_ascii_lowercase();", "return c == lit;", None),
    (LIB, "if d < 10 { Some(d) } else { None }", "Some(d)", None),
    (LIB, "if idx == 0 {\n        return None;\n    }", "if idx == 0 {\n        return Some(0);\n    }", None),
    # --- the four patterns
    (LIB, "(t[j] == '_' || t[j] == '-')", "(t[j] == '_')", None),
    (LIB, '["x-api-key", "token", "password", "passwd", "secret", "pin"]', '["x-api-key", "token", "password", "secret", "pin"]', None),
    (LIB, '"secret", "pin"]', '"secret"]', None),
    (LIB, '"x-api-key", "token"', '"token"', "`x-api-key: v` is still redacted by the `api-key` match two characters later; only the value is replaced, so the output is identical"),
    (LIB, "(t[j] != ':' && t[j] != '=')", "(t[j] != '=')", None),
    (LIB, "(t[j] != ':' && t[j] != '=')", "(t[j] != ':')", None),
    (LIB, "matches!(t[j], ',' | '\\'' | '\"' | '&')", "matches!(t[j], '\\'' | '\"' | '&')", None),
    (LIB, "matches!(t[j], ',' | '\\'' | '\"' | '&')", "matches!(t[j], ',' | '\"' | '&')", None),
    (LIB, "matches!(t[j], ',' | '\\'' | '\"' | '&')", "matches!(t[j], ',' | '\\'' | '&')", None),
    (LIB, "matches!(t[j], ',' | '\\'' | '\"' | '&')", "matches!(t[j], ',' | '\\'' | '\"')", None),
    (LIB, "    while j < t.len() && !py_space(t[j]) && !matches", "    while j < t.len() && !matches", None),
    (LIB, "    if j > start { Some((start, j)) } else { None }\n}\n\n/// `(token=)", "    Some((start, j))\n}\n\n/// `(token=)", None),
    (LIB, "while j < t.len() && t[j] != '&' && !py_space(t[j]) {", "while j < t.len() && !py_space(t[j]) {", None),
    (LIB, "while j < t.len() && t[j] != '&' && !py_space(t[j]) {", "while j < t.len() && t[j] != '&' {", None),
    (LIB, "    if j > start { Some((start, j)) } else { None }\n}\n\n/// `(Authorization", "    Some((start, j))\n}\n\n/// `(Authorization", None),
    (LIB, "    if start == j {\n        return None;\n    }", "", None),
    (LIB, 'lit(t, i, "authorization:")', 'lit(t, i, "authorization")', None),
    (LIB, 'lit(t, j, "bearer")', 'lit(t, j, "bear")', None),
    (LIB, "while e < t.len() && !py_space(t[e]) {", "while e < t.len() {", None),
    (LIB, "            i = ve;\n            copied = ve;", "            i = vs;\n            copied = ve;", "after a hit scanning restarts inside the value: only text of the form value-contains-keyword is affected, and those hit the same replacement"),
    (LIB, "    let c = sub(&b, |t, i| p_eq(t, i, \"key=\"));\n    sub(&c, p_bearer)", "    sub(&b, p_bearer)", None),
    (LIB, "    let b = sub(&a, |t, i| p_eq(t, i, \"token=\"));", "    let b = a;", None),
    (LIB, "    sub(&c, p_bearer)", "    c", None),
    (LIB, "    let a = sub(text, p1);", "    let a = text.to_string();", None),
    (LIB, 'out.push_str("***");', 'out.push_str("**");', None),
    # --- line splitting and stamps
    (LIB, "i + 2\n                } else {", "i + 1\n                } else {", None),
    (LIB, " | '\\u{2028}' | '\\u{2029}' =>", " | '\\u{2029}' =>", None),
    (LIB, "'\\n' | '\\u{b}' | '\\u{c}'", "'\\n' | '\\u{c}'", None),
    (LIB, "| '\\u{85}' |", "|", None),
    (LIB, "if start < text.len() {\n        out.push", "if start <= text.len() {\n        out.push", None),
    (LIB, 'text.replace("\\r\\n", "\\n").replace(\'\\r\', "\\n")', 'text.replace(\'\\r\', "\\n")', None),
    (LIB, "if sep != if n < 2 { ':' } else { ']' } {", "if sep != if n < 2 { '.' } else { ']' } {", None),
    (LIB, "*v = (a * 10 + b) as i64;", "*v = (a + b) as i64;", None),
    (LIB, "Some((vals[0] * 3600 + vals[1] * 60 + vals[2], &line[..end]))", "Some((vals[0] * 3600 + vals[1] * 60 + vals[2], &line[..end - 1]))", None),
    (LIB, "n > 0 && rest[n..]", "rest[n..]", None),
    (LIB, '"] [LAUNCH] {"', '"] [LAUNCH]"', None),
    (LIB, '.strip_prefix(" [")', '.strip_prefix("[")', None),
    (LIB, "if t < p - 6 * 3600 {", "if t < p {", None),
    (LIB, "if t < p - 6 * 3600 {", "if t < p - 12 * 3600 {", "no synthetic or real log steps back by 6 to 12 hours"),
    (LIB, "let absolute = day * 86400 + t;", "let absolute = t;", None),
    (LIB, "pub const LAST_SHOW_MIN_SECONDS: f64 = 30.0 * 60.0;", "pub const LAST_SHOW_MIN_SECONDS: f64 = 10.0 * 60.0;", None),
    (LIB, "if r.2 >= LAST_SHOW_MIN_SECONDS {", "if r.2 > LAST_SHOW_MIN_SECONDS {", None),
    (LIB, "for r in runs.iter().rev() {", "for r in runs.iter() {", None),
    (LIB, "    if starts.first() != Some(&0) {\n        starts.insert(0, 0);\n    }", "", None),
    (LIB, "stamped.iter().cloned().fold(f64::MIN, f64::max) - stamped.iter().cloned().fold(f64::MAX, f64::min)", "stamped.iter().cloned().fold(f64::MIN, f64::max)", None),
    (LIB, "    *runs.last().unwrap()\n", "    runs[0]\n", None),
    (LIB, "if lines.is_empty() {\n        return (0, 0, 0.0);", "if lines.is_empty() {\n        return (0, 0, 1.0);", None),
    # --- file selection
    (LIB, "&& f.mtime >= cutoff", "&& f.mtime > cutoff", "needs an mtime exactly on the cutoff: utime round-trips through float so the parity corpus only sometimes hits it"),
    (LIB, "let days = days.clamp(1, 30);", "let days = days.clamp(1, 31);", None),
    (LIB, "let days = days.clamp(1, 30);", "let days = days.clamp(2, 30);", None),
    (LIB, "let days = days.clamp(1, 30);", "let days = days.max(1);", None),
    (LIB, "let cutoff = now - (days * 86400) as f64;", "let cutoff = now - (days * 3600) as f64;", None),
    (LIB, ".then_with(|| files[a].name.cmp(&files[b].name))", "", "equal mtimes with different names need a tie; the synthetic shows give every file a distinct age"),
    (LIB, '(suffix == ".log" || suffix == ".txt" || is_rotated_log(&f.name))', '(suffix == ".log" || is_rotated_log(&f.name))', None),
    (LIB, '(suffix == ".log" || suffix == ".txt" || is_rotated_log(&f.name))', '(suffix == ".txt" || is_rotated_log(&f.name))', None),
    (LIB, '(suffix == ".log" || suffix == ".txt" || is_rotated_log(&f.name))', '(suffix == ".log" || suffix == ".txt")', None),
    (LIB, 'path_suffix(&f.name).to_lowercase()', 'path_suffix(&f.name).to_string()', None),
    (LIB, "Some(i) if i > 0 => &trimmed[i..],", "Some(i) => &trimmed[i..],", "after trimming leading dots the string cannot start with a dot, so the last dot is never at index 0"),
    (LIB, "let trimmed = name.trim_start_matches('.');", "let trimmed = name;", None),
    (LIB, '(".log.", 4)', '(".log-", 4)', None),
    (LIB, '("singws_", 0), ("", 4)', '("singws-", 0), ("", 4)', None),
    (LIB, "    i == c.len()\n}\n\n/// `_recent", "    true\n}\n\n/// `_recent", None),
    (LIB, "i + n <= c.len() && c[i..i + n]", "c[i..i + n]", "an out-of-range slice would panic: the length test can only be replaced by a crash, which a Python/Rust difference also reveals; kept for the panic"),
    # --- package text
    (LIB, "if !t.is_empty() && !t.ends_with('\\n') {", "if !t.is_empty() {", None),
    (LIB, "if !t.is_empty() && !t.ends_with('\\n') {", "if !t.ends_with('\\n') {", None),
    (LIB, '"# SingWS last show log: {} lines, {}; from {}\\n"', '"# SingWS last show log: {} lines, {}, from {}\\n"', None),
    (LIB, '"no timestamps"', '"no stamps"', None),
    (LIB, 'used.join(", ")', 'used.join(",")', None),
    (LIB, '"crash log only"', '"none"', "unreachable: with no log lines the slice is empty and the header is never built (Python has the same dead branch)"),
    (LIB, "(length / 60.0).floor() as i64", "(length / 60.0).round() as i64", None),
    (LIB, '"{} - {} ({} min)"', '"{} to {} ({} min)"', None),
    (LIB, "a.trim_matches(|c| c == '[' || c == ']')", "a.trim_matches(|c| c == '[')", None),
    (LIB, "(stamps.first(), stamps.last())", "(stamps.first(), stamps.first())", None),
    (LIB, 'error: "No SingWS log files from the requested window.".into()', 'error: "No logs.".into()', None),
    (LIB, "if lines_owned.is_empty() && !has_extra {", "if lines_owned.is_empty() {", None),
    (LIB, "if show.is_empty() {\n        return Package { used, ..Default::default() };", "if show.is_empty() {\n        return Package { ..Default::default() };", "unreachable: a non-empty line list always yields a non-empty slice (and `used` is empty when there are no lines)"),
    (LIB, 'return Package { error: format!("no_text:{}", files[i].name), ..Default::default() };', "continue;", None),
    (LIB, "Package { text: Some(sanitize(&(header + &show.concat()))),", "Package { text: Some(header + &show.concat()),", None),
    (LIB, "let (first, end, length) = last_show_slice(&lines);\n    let show = &lines[first..end];", "let (first, end, length) = (0, lines.len(), last_show_slice(&lines).2);\n    let show = &lines[first..end];", None),
    # --- quoted values (p0) and the optional quote after a keyword
    (LIB, "let a = sub(&sub(text, p0), p1);", "let a = sub(text, p1);", None),
    (LIB, "while e < t.len() && t[e] != q && t[e] != '\\r' && t[e] != '\\n' {", "while e < t.len() && t[e] != q && t[e] != '\\n' {", None),
    (LIB, "while e < t.len() && t[e] != q && t[e] != '\\r' && t[e] != '\\n' {", "while e < t.len() && t[e] != '\\r' && t[e] != '\\n' {", None),
    (LIB, "while e < t.len() && t[e] != q && t[e] != '\\r' && t[e] != '\\n' {", "while e < t.len() && t[e] != q {", None),
    (LIB, "let q = *t.get(j).filter(|&&c| c == '\"' || c == '\\'')?;", "let q = '\"';", None),
    (LIB, "    let start = j + 1;\n    let mut e = start;", "    let start = j;\n    let mut e = start;", None),
    (LIB, "    let mut j = keyword(t, i)?;\n    if j < t.len() && (t[j] == '\"' || t[j] == '\\'') {\n        j += 1;\n    }\n    j = skip_space(t, j);\n    if j >= t.len() || (t[j] != ':' && t[j] != '=') {\n        return None;\n    }\n    j = skip_space(t, j + 1);\n    let q", "    let mut j = keyword(t, i)?;\n    j = skip_space(t, j);\n    if j >= t.len() || (t[j] != ':' && t[j] != '=') {\n        return None;\n    }\n    j = skip_space(t, j + 1);\n    let q", None),
    (LIB, "    let mut j = keyword(t, i)?;\n    if j < t.len() && (t[j] == '\"' || t[j] == '\\'') {\n        j += 1;\n    }\n    j = skip_space(t, j);\n    if j >= t.len() || (t[j] != ':' && t[j] != '=') {\n        return None;\n    }\n    j = skip_space(t, j + 1);\n    let start = j;", "    let mut j = keyword(t, i)?;\n    j = skip_space(t, j);\n    if j >= t.len() || (t[j] != ':' && t[j] != '=') {\n        return None;\n    }\n    j = skip_space(t, j + 1);\n    let start = j;", None),
    # --- the CLI
    (MAIN, '"extras": extras.iter().map(|e| sanitize(e)).collect::<Vec<_>>(),', '"extras": extras.iter().map(|e| e.clone()).collect::<Vec<_>>(),', None),
    (MAIN, "universal_newlines(&String::from_utf8_lossy(&b))", "String::from_utf8_lossy(&b).into_owned()", None),
    (MAIN, "let p = build(files, days, now, !extras.is_empty());", "let p = build(files, days, now, false);", None),
    (MAIN, '"slice": [p.slice.0, p.slice.1, p.slice.2],', '"slice": [p.slice.0, p.slice.1, 0.0],', None),
    (MAIN, "Some(\"redact\") => {\n            let text = String::from_utf8_lossy(&read_stdin()).into_owned();\n            let _ = out.lock().write_all(sanitize(&text).as_bytes());", "Some(\"redact\") => {\n            let text = String::from_utf8_lossy(&read_stdin()).into_owned();\n            let _ = out.lock().write_all(text.as_bytes());", None),
    (MAIN, "d.as_secs() as f64 + d.subsec_nanos() as f64 * 1e-9", "d.as_secs() as f64", None),
    (MAIN, 'f["mtime"].as_f64().unwrap_or(0.0)', "0.0", None),
    # --- the Python shadow
    (SHADOW, 'return bool((settings or {}).get("logpack_shadow", True))', 'return bool((settings or {}).get("logpack_shadow", False))', None),
    (SHADOW, "    if a != b:\n", "    if False:\n", None),
    (SHADOW, '    if (py.get("window") or "") != (rs.get("window") or ""):', "    if False:", None),
    (SHADOW, '    if list(py.get("used") or []) != list(rs.get("used") or []):', "    if False:", None),
    (SHADOW, '    if list(py.get("extras") or []) != list(rs.get("extras") or []):', "    if False:", None),
    (SHADOW, '    if rs.get("error"):', "    if False:", None),
    (SHADOW, "fresh = [d for d in diffs if d not in _seen]", "fresh = diffs", None),
    (SHADOW, "        _seen.update(diffs)\n", "", None),
    (SHADOW, "if helper is None:\n            return \"skipped\"", "if helper is None:\n            say(\"skipped: no helper\")\n            return \"skipped\"", None),
    (SHADOW, 'return f"len={len(text)} sha={hashlib.sha256(data).hexdigest()[:10]}"', 'return f"len={len(text)} text={text}"', None),
    (SHADOW, "if proc.returncode != 0:", "if proc.returncode == 99:", None),
    (SHADOW, "if entry.is_file():", "if True:", None),
    (SHADOW, "if not enabled(settings) or find_helper() is None:", "if not enabled(settings):", None),
    (SHADOW, '"extras": [{"name": "extra", "text": t} for t in extra_texts]}', '"extras": []}', None),
    (SHADOW, '"text": texts.get(name)}', '"text": None}', None),
    (SHADOW, '"now": float(now)', '"now": 0.0', None),
    (SHADOW, "    except Exception as exc:  # noqa: BLE001 - a shadow must never reach the app\n        say(", "    except ZeroDivisionError as exc:  # noqa: BLE001\n        say(", None),
    (SHADOW, "return next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))", "return 0", None),
    (SHADOW, '    candidates: list[Path] = []\n    override = os.environ.get("SINGWS_LOGPACK_BIN")\n    if override:\n        candidates.append(Path(override))', '    candidates: list[Path] = []', "the env override is a test/dev convenience; the unit tests pass the helper explicitly"),
    # --- the hook in the app
    (APP, '    "logpack_shadow": True,', '    "logpack_shadow": False,', None),
    (APP, "    shadow_now = time.time()\n", "    shadow_now = 0.0\n", None),
    (APP, "        shadow_texts[Path(path).name] = text\n", "", None),
    (APP, 'py={"text": shadow_text, "window": window, "used": [p.name for p in used], "extras": shadow_extra_out})', 'py={"text": shadow_text, "window": window, "used": [], "extras": shadow_extra_out})', None),
    (APP, "extra_texts=shadow_extra_raw,", "extra_texts=[],", None),
    (APP, "logs_dir=LOGS_DIR, days=days, now=shadow_now,", "logs_dir=LOGS_DIR, days=days + 1, now=shadow_now,", None),
    (APP, '                zf.writestr(f"singws_last_show_{stamp}.log", shadow_text)', '                zf.writestr(f"singws_last_show_{stamp}.log", header + "".join(show_lines))', None),
    (APP, "        os.replace(building, package)\n        try:\n            import logpack_shadow", "        try:\n            import logpack_shadow", "the hook then runs before the rename; the ZIP is complete either way and the shadow never reads it"),
]


def tests_for(path):
    if path in (SHADOW, APP):
        return [[PY, "-m", "unittest", "test_logpack_shadow", "test_last_show_logs"]]
    return [["cargo", "test", "--release", "-p", "singws-logpack", "--offline"], ["cargo", "build", "--release", "-p", "singws-logpack", "--offline"],
            [PY, "tools/logpack_parity.py", "--cases", "60", "--no-real"]]


def run_all(path):
    for cmd in tests_for(path):
        cwd = ROOT / "rust" if cmd[0] == "cargo" else ROOT
        env = dict(ENV, SINGWS_HOME=tempfile.mkdtemp(prefix="mutate_logpack_home_"))
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1500)
        out = p.stdout + p.stderr
        if p.returncode != 0:
            if "error[E" in out or "could not compile" in out:
                return "invalid"
            return "killed"
    return "survived"


def main():
    only = None
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    survivors, invalid = [], []
    for n, (path, old, new, note) in enumerate(M, 1):
        if only and n not in only:
            continue
        original = path.read_text()
        if original.count(old) != 1:
            print(f"#{n}: pattern found {original.count(old)} times in {path.name}: {old[:60]!r}")
            invalid.append(n)
            continue
        path.write_text(original.replace(old, new))
        try:
            verdict = run_all(path)
        finally:
            path.write_text(original)
        tag = {"killed": "killed", "survived": "SURVIVED" + (f" (documented equivalent: {note})" if note else ""), "invalid": "INVALID (does not compile)"}[verdict]
        print(f"#{n} {path.name}: {tag}   {old[:50]!r} -> {new[:40]!r}", flush=True)
        if verdict == "survived" and not note:
            survivors.append(n)
        if verdict == "invalid":
            invalid.append(n)
    subprocess.run(["cargo", "build", "--release", "-p", "singws-logpack", "--offline"], cwd=ROOT / "rust", env=ENV, capture_output=True)
    print(f"\n{len(M)} mutants; undocumented survivors: {survivors or 'none'}; invalid: {invalid or 'none'}")
    return 1 if survivors or invalid else 0


if __name__ == "__main__":
    sys.exit(main())
