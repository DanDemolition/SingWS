//! Rust counterpart of the app's log redaction and last-show package text (0.2.18.1.py: `_sanitize_log_text`, `_recent_log_files`,
//! `_log_line_seconds`, `_last_show_slice`, `_build_last_show_package`). Pure functions, no dependencies.
//!
//! Python's `re` rules are copied, not approximated: `\s` is `str.isspace`, `\d` is Unicode Nd (table generated from Python 3.13/3.14,
//! Unicode 16), and `(?i)` also matches U+0130/U+0131 for i, U+212A for k and U+017F for s. `tools/logpack_parity.py` checks every
//! code point against the real regexes.

/// Zero digits of every Unicode Nd run (each run is ten consecutive code points).
const ND_ZEROS: [u32; 76] = [0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66, 0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90, 0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10, 0x104a0, 0x10d30, 0x10d40, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0, 0x11650, 0x116c0, 0x116d0, 0x116da, 0x11730, 0x118e0, 0x11950, 0x11bf0, 0x11c50, 0x11d50, 0x11da0, 0x11f50, 0x16130, 0x16a60, 0x16ac0, 0x16b50, 0x16d70, 0x1ccf0, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0, 0x1e5f1, 0x1e950, 0x1fbf0];

/// `\d` of Python's `re` on a str, with its decimal value.
pub fn py_digit(c: char) -> Option<u32> {
    let cp = c as u32;
    let idx = ND_ZEROS.partition_point(|&z| z <= cp);
    if idx == 0 {
        return None;
    }
    let d = cp - ND_ZEROS[idx - 1];
    if d < 10 { Some(d) } else { None }
}

/// `\s` of Python's `re` on a str (`str.isspace`).
pub fn py_space(c: char) -> bool {
    matches!(c as u32, 0x09..=0x0d | 0x1c..=0x20 | 0x85 | 0xa0 | 0x1680 | 0x2000..=0x200a | 0x2028 | 0x2029 | 0x202f | 0x205f | 0x3000)
}

/// A pattern letter under `(?i)`; `-` and `_` are exact.
fn ieq(c: char, lit: char) -> bool {
    if c == lit {
        return true;
    }
    if !lit.is_ascii_alphabetic() {
        return false;
    }
    if c.is_ascii() {
        return c.to_ascii_lowercase() == lit.to_ascii_lowercase();
    }
    match lit.to_ascii_lowercase() {
        'i' => c == '\u{130}' || c == '\u{131}',
        'k' => c == '\u{212a}',
        's' => c == '\u{17f}',
        _ => false,
    }
}

fn lit(t: &[char], i: usize, s: &str) -> Option<usize> {
    let mut j = i;
    for l in s.chars() {
        if j >= t.len() || !ieq(t[j], l) {
            return None;
        }
        j += 1;
    }
    Some(j)
}

fn skip_space(t: &[char], mut i: usize) -> usize {
    while i < t.len() && py_space(t[i]) {
        i += 1;
    }
    i
}

/// Pattern 1 keyword: api[_-]?key | x-api-key | token | password | passwd | secret | pin (no two can match at one position).
fn keyword(t: &[char], i: usize) -> Option<usize> {
    if let Some(j) = lit(t, i, "api") {
        let k = if j < t.len() && (t[j] == '_' || t[j] == '-') { j + 1 } else { j };
        return lit(t, k, "key");
    }
    for kw in ["x-api-key", "token", "password", "passwd", "secret", "pin"] {
        if let Some(j) = lit(t, i, kw) {
            return Some(j);
        }
    }
    None
}

/// A QUOTED value: `(kw)(["']?\s*[:=]\s*(["']))[^\r\n]*?(?=\2|[\r\n]|\Z)` -> the text between the quotes (to the closing quote, or the end of the line).
fn p0(t: &[char], i: usize) -> Option<(usize, usize)> {
    let mut j = keyword(t, i)?;
    if j < t.len() && (t[j] == '"' || t[j] == '\'') {
        j += 1;
    }
    j = skip_space(t, j);
    if j >= t.len() || (t[j] != ':' && t[j] != '=') {
        return None;
    }
    j = skip_space(t, j + 1);
    let q = *t.get(j).filter(|&&c| c == '"' || c == '\'')?;
    let start = j + 1;
    let mut e = start;
    while e < t.len() && t[e] != q && t[e] != '\r' && t[e] != '\n' {
        e += 1;
    }
    Some((start, e))
}

/// `(kw)(["']?\s*[:=]\s*)([^\s,'"&]+)` -> start of the value and its end.
fn p1(t: &[char], i: usize) -> Option<(usize, usize)> {
    let mut j = keyword(t, i)?;
    if j < t.len() && (t[j] == '"' || t[j] == '\'') {
        j += 1;
    }
    j = skip_space(t, j);
    if j >= t.len() || (t[j] != ':' && t[j] != '=') {
        return None;
    }
    j = skip_space(t, j + 1);
    let start = j;
    while j < t.len() && !py_space(t[j]) && !matches!(t[j], ',' | '\'' | '"' | '&') {
        j += 1;
    }
    if j > start { Some((start, j)) } else { None }
}

/// `(token=)([^&\s]+)` and `(key=)([^&\s]+)`.
fn p_eq(t: &[char], i: usize, word: &str) -> Option<(usize, usize)> {
    let start = lit(t, i, word)?;
    let mut j = start;
    while j < t.len() && t[j] != '&' && !py_space(t[j]) {
        j += 1;
    }
    if j > start { Some((start, j)) } else { None }
}

/// `(Authorization:\s*Bearer\s+)([^\s]+)`.
fn p_bearer(t: &[char], i: usize) -> Option<(usize, usize)> {
    let j = skip_space(t, lit(t, i, "authorization:")?);
    let j = lit(t, j, "bearer")?;
    let start = skip_space(t, j);
    if start == j {
        return None;
    }
    let mut e = start;
    while e < t.len() && !py_space(t[e]) {
        e += 1;
    }
    if e > start { Some((start, e)) } else { None }
}

fn sub(text: &str, find: impl Fn(&[char], usize) -> Option<(usize, usize)>) -> String {
    let t: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    let mut copied = 0;
    while i < t.len() {
        if let Some((vs, ve)) = find(&t, i) {
            out.extend(&t[copied..vs]);
            out.push_str("***");
            i = ve;
            copied = ve;
        } else {
            i += 1;
        }
    }
    out.extend(&t[copied..]);
    out
}

/// `_sanitize_log_text`: the four patterns applied one after the other.
pub fn sanitize(text: &str) -> String {
    let a = sub(&sub(text, p0), p1);
    let b = sub(&a, |t, i| p_eq(t, i, "token="));
    let c = sub(&b, |t, i| p_eq(t, i, "key="));
    sub(&c, p_bearer)
}

/// Python's `str.splitlines(keepends=True)`.
pub fn splitlines_keepends(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = text.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        let mut end = None;
        match c {
            '\r' => {
                end = Some(if let Some(&(_, '\n')) = it.peek() {
                    it.next();
                    i + 2
                } else {
                    i + 1
                });
            }
            '\n' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}' => end = Some(i + c.len_utf8()),
            _ => {}
        }
        if let Some(e) = end {
            out.push(&text[start..e]);
            start = e;
        }
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// `Path.read_text` newline handling: \r\n and lone \r become \n.
pub fn universal_newlines(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

/// `^\[(\d\d):(\d\d):(\d\d)\]` -> (seconds of day, the matched "[HH:MM:SS]").
fn log_stamp(line: &str) -> Option<(i64, &str)> {
    let mut it = line.char_indices();
    if it.next()?.1 != '[' {
        return None;
    }
    let mut vals = [0i64; 3];
    for (n, v) in vals.iter_mut().enumerate() {
        let a = py_digit(it.next()?.1)?;
        let b = py_digit(it.next()?.1)?;
        *v = (a * 10 + b) as i64;
        let sep = it.next()?.1;
        if sep != if n < 2 { ':' } else { ']' } {
            return None;
        }
    }
    let end = it.next().map(|(i, _)| i).unwrap_or(line.len());
    Some((vals[0] * 3600 + vals[1] * 60 + vals[2], &line[..end]))
}

/// `^\[\d\d:\d\d:\d\d\] \[[A-Z]+\] \[LAUNCH\] \{`.
fn is_launch_line(line: &str) -> bool {
    let Some((_, stamp)) = log_stamp(line) else { return false };
    let Some(rest) = line[stamp.len()..].strip_prefix(" [") else { return false };
    let n = rest.chars().take_while(|c| c.is_ascii_uppercase()).count();
    n > 0 && rest[n..].starts_with("] [LAUNCH] {")
}

/// `_log_line_seconds`: seconds since the first stamped line (None for other lines), counting midnight rollovers.
pub fn log_line_seconds(lines: &[&str]) -> Vec<Option<f64>> {
    let mut out = Vec::with_capacity(lines.len());
    let (mut base, mut day, mut previous) = (None::<i64>, 0i64, None::<i64>);
    for line in lines {
        let Some((t, _)) = log_stamp(line) else {
            out.push(None);
            continue;
        };
        if let Some(p) = previous {
            if t < p - 6 * 3600 {
                day += 1;
            }
        }
        previous = Some(t);
        let absolute = day * 86400 + t;
        let b = *base.get_or_insert(absolute);
        out.push(Some((absolute - b) as f64));
    }
    out
}

pub const LAST_SHOW_MIN_SECONDS: f64 = 30.0 * 60.0;

/// `_last_show_slice`: (first, end, seconds) of the most recent run of the app that lasted at least 30 minutes (else the latest run).
pub fn last_show_slice(lines: &[&str]) -> (usize, usize, f64) {
    if lines.is_empty() {
        return (0, 0, 0.0);
    }
    let mut starts: Vec<usize> = (0..lines.len()).filter(|&i| is_launch_line(lines[i])).collect();
    if starts.first() != Some(&0) {
        starts.insert(0, 0);
    }
    let seconds = log_line_seconds(lines);
    let runs: Vec<(usize, usize, f64)> = (0..starts.len())
        .map(|k| {
            let (first, end) = (starts[k], if k + 1 < starts.len() { starts[k + 1] } else { lines.len() });
            let stamped: Vec<f64> = seconds[first..end].iter().flatten().copied().collect();
            let len = if stamped.is_empty() {
                0.0
            } else {
                stamped.iter().cloned().fold(f64::MIN, f64::max) - stamped.iter().cloned().fold(f64::MAX, f64::min)
            };
            (first, end, len)
        })
        .collect();
    for r in runs.iter().rev() {
        if r.2 >= LAST_SHOW_MIN_SECONDS {
            return *r;
        }
    }
    *runs.last().unwrap()
}

/// A file in the log folder as the selection sees it. `text` is already decoded (None = not available / unreadable).
#[derive(Clone, Debug)]
pub struct LogFile {
    pub name: String,
    pub mtime: f64,
    pub text: Option<String>,
}

fn path_suffix(name: &str) -> &str {
    // pathlib (3.14): leading dots are not part of the stem; the suffix starts at the last dot that is not the first remaining character.
    let trimmed = name.trim_start_matches('.');
    match trimmed.rfind('.') {
        Some(i) if i > 0 => &trimmed[i..],
        _ => "",
    }
}

fn digits_at(c: &[char], i: usize, n: usize) -> bool {
    i + n <= c.len() && c[i..i + n].iter().all(|&x| py_digit(x).is_some())
}

/// `singws_\d{4}-\d{2}-\d{2}\.log\.\d{4}-\d{2}-\d{2}` (full match).
fn is_rotated_log(name: &str) -> bool {
    // Each element: literal text, or a run of that many digits.
    const PARTS: [(&str, usize); 8] = [("singws_", 0), ("", 4), ("-", 2), ("-", 2), (".log.", 4), ("-", 2), ("-", 2), ("", 0)];
    let c: Vec<char> = name.chars().collect();
    let mut i = 0;
    for (text, digits) in PARTS {
        for l in text.chars() {
            if c.get(i) != Some(&l) {
                return false;
            }
            i += 1;
        }
        if digits > 0 {
            if !digits_at(&c, i, digits) {
                return false;
            }
            i += digits;
        }
    }
    i == c.len()
}

/// `_recent_log_files` followed by the mtime sort of `_build_last_show_package`: indices into `files`, oldest first.
pub fn select_files(files: &[LogFile], days: i64, now: f64) -> Vec<usize> {
    let days = days.clamp(1, 30);
    let cutoff = now - (days * 86400) as f64;
    let mut picked: Vec<usize> = (0..files.len())
        .filter(|&i| {
            let f = &files[i];
            let suffix = path_suffix(&f.name).to_lowercase();
            (suffix == ".log" || suffix == ".txt" || is_rotated_log(&f.name)) && f.mtime >= cutoff
        })
        .collect();
    picked.sort_by(|&a, &b| {
        files[a].mtime.partial_cmp(&files[b].mtime).unwrap_or(std::cmp::Ordering::Equal).then_with(|| files[a].name.cmp(&files[b].name))
    });
    picked
}

#[derive(Debug, Default, PartialEq)]
pub struct Package {
    /// The sanitized single log (header + the last show's lines); None when only extra files exist.
    pub text: Option<String>,
    pub window: String,
    /// Names of the log files that were read, in order.
    pub used: Vec<String>,
    pub show_lines: usize,
    /// (first, end, seconds) of the last-show slice within the merged lines.
    pub slice: (usize, usize, f64),
    pub error: String,
}

/// `_build_last_show_package` minus the ZIP: the sanitized last-show text, the window label and the files used.
/// `has_extra` = a crash log is attached (so an empty log folder is not an error).
pub fn build(files: &[LogFile], days: i64, now: f64, has_extra: bool) -> Package {
    let mut lines_owned: Vec<String> = Vec::new();
    let mut used = Vec::new();
    for i in select_files(files, days, now) {
        let Some(text) = &files[i].text else {
            return Package { error: format!("no_text:{}", files[i].name), ..Default::default() };
        };
        let mut t = text.clone();
        if !t.is_empty() && !t.ends_with('\n') {
            t.push('\n');
        }
        used.push(files[i].name.clone());
        lines_owned.extend(splitlines_keepends(&t).into_iter().map(String::from));
    }
    if lines_owned.is_empty() && !has_extra {
        return Package { error: "No SingWS log files from the requested window.".into(), ..Default::default() };
    }
    let lines: Vec<&str> = lines_owned.iter().map(String::as_str).collect();
    let (first, end, length) = last_show_slice(&lines);
    let show = &lines[first..end];
    if show.is_empty() {
        return Package { used, ..Default::default() };
    }
    let stamps: Vec<&str> = show.iter().filter_map(|l| log_stamp(l).map(|s| s.1)).collect();
    let window = match (stamps.first(), stamps.last()) {
        (Some(a), Some(b)) => format!("{} - {} ({} min)", a.trim_matches(|c| c == '[' || c == ']'), b.trim_matches(|c| c == '[' || c == ']'), (length / 60.0).floor() as i64),
        _ => String::new(),
    };
    let from = if used.is_empty() { "crash log only".to_string() } else { used.join(", ") };
    let header = format!("# SingWS last show log: {} lines, {}; from {}\n", show.len(), if window.is_empty() { "no timestamps" } else { &window }, from);
    Package { text: Some(sanitize(&(header + &show.concat()))), window, used, show_lines: show.len(), slice: (first, end, length), error: String::new() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_every_pattern() {
        assert_eq!(sanitize("api_key=abc123 ok"), "api_key=*** ok");
        assert_eq!(sanitize("X-API-Key: SECRET,rest"), "X-API-Key: ***,rest");
        assert_eq!(sanitize("x-api-key:abc"), "x-api-key:***");
        assert_eq!(sanitize("password = hunter2"), "password = ***");
        assert_eq!(sanitize("password = 'hunter 2' next"), "password = '***' next");
        assert_eq!(sanitize("{\"api_key\": \"abc\", \"u\": 1}"), "{\"api_key\": \"***\", \"u\": 1}");
        assert_eq!(sanitize("secret='open\nnext"), "secret='***\nnext");
        assert_eq!(sanitize("\"token\": 123}"), "\"token\": ***");
        assert_eq!(sanitize("pin: \"it's\""), "pin: \"***\"");
        assert_eq!(sanitize("pin=1234 passwd=x secret:y"), "pin=*** passwd=*** secret:***");
        assert_eq!(sanitize("https://h/a?token=abc&u=1"), "https://h/a?token=***&u=1");
        assert_eq!(sanitize("https://h/a?key=abc&b=2"), "https://h/a?key=***&b=2");
        assert_eq!(sanitize("Authorization: Bearer abc.def x"), "Authorization: Bearer *** x");
        assert_eq!(sanitize("authorization:bearer\tT"), "authorization:bearer\t***");
    }

    #[test]
    fn leaves_what_the_patterns_do_not_cover() {
        for s in ["plain", "token", "token=", "token = ", "Authorization: Bearer", "Authorization:Bearer ", "pinning: x", "key=&"] {
            assert_eq!(sanitize(s), s, "{s}");
        }
        assert_eq!(sanitize("api key=abc"), "api key=***"); // only the bare `key=` pattern fires
        assert_eq!(sanitize("token=,"), "token=***"); // pattern 1 stops at the comma, pattern 2 takes it
    }

    #[test]
    fn case_folding_follows_python() {
        assert_eq!(sanitize("\u{212a}ey=abc"), "\u{212a}ey=***");
        assert_eq!(sanitize("\u{17f}ecret=abc"), "\u{17f}ecret=***");
        assert_eq!(sanitize("p\u{130}n=1"), "p\u{130}n=***");
        assert_eq!(sanitize("p\u{e9}n=1"), "p\u{e9}n=1");
    }

    #[test]
    fn whitespace_is_pythons() {
        assert_eq!(sanitize("api_key\u{a0}=\u{3000}abc d"), "api_key\u{a0}=\u{3000}*** d");
        assert!(py_space('\u{1c}') && py_space('\u{85}') && !py_space('\u{200b}') && !py_space('a'));
    }

    #[test]
    fn digits_are_unicode_nd() {
        assert_eq!(py_digit('7'), Some(7));
        assert_eq!(py_digit('\u{665}'), Some(5));
        assert_eq!(py_digit('\u{1d7ff}'), Some(9));
        assert_eq!(py_digit('a'), None);
        assert_eq!(py_digit(' '), None);
        assert_eq!(py_digit('\0'), None);
        assert_eq!(py_digit('\u{b2}'), None);
    }

    #[test]
    fn launch_lines_need_letters_and_the_brace() {
        assert!(is_launch_line("[10:00:00] [INFO] [LAUNCH] {\"a\":1}\n"));
        assert!(!is_launch_line("[10:00:00] [] [LAUNCH] {\"a\":1}\n"));
        assert!(!is_launch_line("[10:00:00] [INFO] [LAUNCH] no brace\n"));
        assert!(!is_launch_line("[10:00:00] [info] [LAUNCH] {\n"));
        assert!(log_stamp("[1 :00:00] x").is_none());
    }

    #[test]
    fn splitlines_matches_python() {
        assert_eq!(splitlines_keepends("a\nb\r\nc\rd\u{b}e\u{2028}f"), vec!["a\n", "b\r\n", "c\r", "d\u{b}", "e\u{2028}", "f"]);
        assert!(splitlines_keepends("").is_empty());
        assert_eq!(universal_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
    }

    fn run(start: i64, minutes: i64, label: &str, launch: bool) -> Vec<String> {
        let mut v = Vec::new();
        let s = |m: i64| format!("[{:02}:{:02}:00]", (m / 60) % 24, m % 60);
        if launch {
            v.push(format!("{} [INFO] [LAUNCH] {{\"x\":1}}\n", s(start)));
        }
        for i in 1..=minutes {
            v.push(format!("{} [INFO] {label} {i}\n", s(start + i)));
        }
        v
    }

    fn refs(v: &[String]) -> Vec<&str> {
        v.iter().map(String::as_str).collect()
    }

    #[test]
    fn the_last_long_run_is_the_show() {
        let mut lines = run(21 * 60 + 34, 250, "SHOW", true);
        let n = lines.len();
        lines.extend(run(25 * 60 + 41, 1, "REOPEN", true));
        let (first, end, secs) = last_show_slice(&refs(&lines));
        assert_eq!((first, end), (0, n));
        assert_eq!(secs, 250.0 * 60.0);
    }

    #[test]
    fn a_session_just_under_thirty_minutes_is_not_the_show() {
        let mut lines = run(21 * 60, 100, "SHOW", true);
        let n = lines.len();
        lines.extend(run(23 * 60, 29, "SHORT", true));
        assert_eq!(last_show_slice(&refs(&lines)).0, 0);
        assert_eq!(last_show_slice(&refs(&lines)).1, n);
        let mut lines2 = run(21 * 60, 100, "SHOW", true);
        lines2.extend(run(23 * 60, 30, "EDGE", true));
        assert_eq!(last_show_slice(&refs(&lines2)).0, n);
    }

    #[test]
    fn with_no_long_run_the_latest_is_used_and_empty_is_empty() {
        let mut lines = run(60, 5, "A", true);
        let n = lines.len();
        lines.extend(run(600, 5, "B", true));
        assert_eq!(last_show_slice(&refs(&lines)).0, n);
        assert_eq!(last_show_slice(&[]), (0, 0, 0.0));
    }

    #[test]
    fn midnight_is_counted_as_one_day_forward() {
        let secs = log_line_seconds(&["[23:59:00] a\n", "continuation\n", "[00:01:00] b\n"]);
        assert_eq!(secs, vec![Some(0.0), None, Some(120.0)]);
    }

    fn file(name: &str, age_hours: f64, text: &str) -> LogFile {
        LogFile { name: name.into(), mtime: 1_000_000.0 - age_hours * 3600.0, text: Some(text.into()) }
    }

    #[test]
    fn selection_filters_names_and_age_and_orders_by_mtime() {
        let files = vec![
            file("singws_2026-10-30.log", 1.0, ""),
            file("singws_2026-10-29.log.2026-10-29", 5.0, ""),
            file("a.zip", 1.0, ""),
            file(".log", 1.0, ""),
            file("notes.TXT", 2.0, ""),
            file("old.log", 24.0 * 8.0, ""),
            file("singws_2026-10-29.log.bak", 1.0, ""),
        ];
        let picked: Vec<&str> = select_files(&files, 7, 1_000_000.0).iter().map(|&i| files[i].name.as_str()).collect();
        assert_eq!(picked, vec!["singws_2026-10-29.log.2026-10-29", "notes.TXT", "singws_2026-10-30.log"]);
        assert_eq!(select_files(&files, 99, 1_000_000.0).len(), 4); // 30-day clamp lets old.log in
        assert_eq!(select_files(&files, 0, 1_000_000.0).len(), 3); // 1-day minimum: only files < 24 h old
    }

    #[test]
    fn name_helpers_follow_pathlib_and_the_rotation_regex() {
        assert_eq!(path_suffix("a.log"), ".log");
        assert_eq!(path_suffix(".log"), "");
        assert_eq!(path_suffix("..log"), "");
        assert_eq!(path_suffix("a..log"), ".log");
        assert_eq!(path_suffix("a."), ".");
        assert!(is_rotated_log("singws_2026-10-29.log.2026-10-28"));
        assert!(is_rotated_log("singws_\u{662}\u{660}\u{662}\u{666}-10-29.log.2026-10-28"));
        assert!(!is_rotated_log("singws_2026-10-29.log.2026-10-2"));
        assert!(!is_rotated_log("singws_2026-10-29.log.2026-10-28x"));
        assert!(!is_rotated_log("xsingws_2026-10-29.log.2026-10-28"));
    }

    #[test]
    fn the_package_has_the_apps_header_and_is_redacted() {
        let mut text = String::new();
        for l in run(21 * 60, 40, "SHOW", true) {
            text.push_str(&l.replace("SHOW", "token=abc SHOW"));
        }
        let files = vec![file("singws_2026-10-30.log", 1.0, text.trim_end())]; // no final newline
        let p = build(&files, 7, 1_000_000.0, false);
        let t = p.text.unwrap();
        assert!(t.starts_with("# SingWS last show log: 41 lines, 21:00:00 - 21:40:00 (40 min); from singws_2026-10-30.log\n"), "{t}");
        assert!(t.contains("token=*** SHOW 40\n") && !t.contains("abc"));
        assert_eq!(p.window, "21:00:00 - 21:40:00 (40 min)");
        assert_eq!(p.used, vec!["singws_2026-10-30.log"]);
    }

    #[test]
    fn header_without_stamps_and_error_cases() {
        let files = vec![file("a.log", 1.0, "no stamp\n")];
        let t = build(&files, 7, 1_000_000.0, false).text.unwrap();
        assert!(t.starts_with("# SingWS last show log: 1 lines, no timestamps; from a.log\n"));
        assert_eq!(build(&[], 7, 1_000_000.0, false).error, "No SingWS log files from the requested window.");
        let only_extra = build(&[], 7, 1_000_000.0, true);
        assert!(only_extra.error.is_empty() && only_extra.text.is_none());
        let none_text = vec![LogFile { name: "a.log".into(), mtime: 999_000.0, text: None }];
        assert!(build(&none_text, 7, 1_000_000.0, false).error.starts_with("no_text:"));
    }

    #[test]
    fn files_are_joined_across_the_midnight_rotation() {
        let a = run(23 * 60, 59, "BEFORE", true).concat();
        let b = run(0, 59, "AFTER", false).concat();
        let files = vec![file("singws_2026-10-30.log", 1.0, &b), file("singws_2026-10-29.log.2026-10-29", 3.0, &a)];
        let p = build(&files, 7, 1_000_000.0, false);
        assert_eq!(p.used, vec!["singws_2026-10-29.log.2026-10-29", "singws_2026-10-30.log"]);
        assert!(p.window.starts_with("23:00:00 - 00:59:00 (119 min)"), "{}", p.window);
    }
}
