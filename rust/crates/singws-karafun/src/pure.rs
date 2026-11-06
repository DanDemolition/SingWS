//! The pure decision rules around the KaraFun automation, ported from 0.2.18.1.py:
//!  - `normalize_karafun_duration_seconds`                     -> `normalize_duration_seconds`
//!  - `_karafun_fallback_remaining`, `_karafun_adjustment_needs_attention`
//!  - `_karafun_clean_search_part`, `_karafun_entry_artist_title`, `_karafun_search_queries_for_entry`  (the query ladder, most specific first)
//!  - the accept/reject rule for a search answer (inline in `_automate_karafun_search_and_play`; transcribed, guarded by a source test)
//! Every regex is copied VERBATIM from the Python (including `feat\\.?`, which in a raw string is a literal backslash and an optional character:
//! it does not match "feat." and that is today's behaviour, kept on purpose).
use crate::{py_space, py_strip};
use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

/// A value this port deliberately refuses to guess (Unicode digits that Python's `\d`/`isdigit` accept): the parity tool counts and checks these.
#[derive(Debug, PartialEq)]
pub enum Duration {
    Seconds(i64),
    None,
    Decline,
}

fn finite_seconds(seconds: f64) -> Duration {
    if !seconds.is_finite() || seconds <= 0.0 || seconds > 86400.0 {
        return Duration::None;
    }
    Duration::Seconds(seconds.round_ties_even() as i64) // Python round() is banker's rounding
}

fn ascii_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

pub fn normalize_duration_seconds(value: &Value) -> Duration {
    match value {
        Value::Null | Value::Bool(_) => Duration::None,
        Value::Number(n) => finite_seconds(n.as_f64().unwrap_or(f64::NAN)),
        Value::String(s) => {
            let text = py_strip(s);
            if text.is_empty() {
                return Duration::None;
            }
            if text.chars().any(|c| !c.is_ascii() && c.is_numeric()) {
                return Duration::Decline;
            }
            // \d+(?:\.\d+)?
            let mut it = text.splitn(2, '.');
            let (whole, frac) = (it.next().unwrap_or(""), it.next());
            if ascii_digits(whole) && frac.map_or(true, ascii_digits) {
                return finite_seconds(text.parse::<f64>().unwrap_or(f64::NAN));
            }
            let parts: Vec<&str> = text.split(':').collect();
            if !(parts.len() == 2 || parts.len() == 3) || !parts.iter().all(|p| ascii_digits(p)) {
                return Duration::None;
            }
            let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::MAX / 4 } else { p.parse().unwrap_or(u128::MAX / 4) }).collect();
            if values[values.len() - 1] >= 60 || (values.len() == 3 && values[1] >= 60) {
                return Duration::None;
            }
            let mut total: u128 = 0;
            for v in values {
                total = total.saturating_mul(60).saturating_add(v);
            }
            if total == 0 || total > 86400 { Duration::None } else { Duration::Seconds(total as i64) }
        }
        _ => Duration::None,
    }
}

fn py_int(v: &Value) -> i64 {
    match v {
        Value::Bool(b) => *b as i64, // Python int(True) == 1
        Value::Number(n) => n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(0.0).trunc() as i64),
        Value::String(s) => s.trim().parse::<f64>().map(|f| f.trunc() as i64).unwrap_or(0),
        _ => 0,
    }
}

/// `_karafun_fallback_remaining(duration, elapsed_wall, seek_offset)`: int(duration) - int(elapsed_wall) - int(float(seek_offset or 0.0)).
pub fn fallback_remaining(duration: &Value, elapsed_wall: &Value, seek_offset: &Value) -> i64 {
    let offset = match seek_offset {
        Value::Null => 0.0,
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::Bool(b) => if *b { 1.0 } else { 0.0 },
        _ => 0.0,
    };
    py_int(duration) - py_int(elapsed_wall) - offset.trunc() as i64
}

/// `_karafun_adjustment_needs_attention(requested_key, requested_tempo, adjustment_message, already_applied)`.
pub fn adjustment_needs_attention(requested_key: i64, requested_tempo: i64, message: &str, already_applied: bool) -> bool {
    if already_applied {
        return false;
    }
    let reply = message.to_lowercase();
    (requested_key != 0 && !reply.contains("|true|")) || (requested_tempo != 100 && !reply.ends_with("|true"))
}

fn re(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("pattern"))
}

/// `_karafun_clean_search_part`.
pub fn clean_search_part(value: &str) -> String {
    static A: OnceLock<Regex> = OnceLock::new();
    static B: OnceLock<Regex> = OnceLock::new();
    static C: OnceLock<Regex> = OnceLock::new();
    static D: OnceLock<Regex> = OnceLock::new();
    static E: OnceLock<Regex> = OnceLock::new();
    let mut text = re(&A, r"(?i)\([^)]*(karaoke|version|explicit|clean|remaster|live|feat\\.?|ft\\.?)[^)]*\)").replace_all(value, " ").into_owned();
    text = re(&B, r"(?i)\[[^\]]*(karaoke|version|explicit|clean|remaster|live|feat\\.?|ft\\.?)[^\]]*\]").replace_all(&text, " ").into_owned();
    // Python `$` also matches just before a final newline; (?:\n?\z) covers both (the newline becomes a space and is collapsed below anyway)
    text = re(&C, r"(?i)\b(feat\\.?|ft\\.?|featuring)\b.*(?:\n?\z)").replace_all(&text, " ").into_owned();
    text = re(&D, r"(?i)\b(karaoke|instrumental|official|version|lyrics?|lyric video|hd|remaster(?:ed)?|clean|explicit)\b").replace_all(&text, " ").into_owned();
    text = text.replace('&', " and ");
    text = text.replace(['\u{2019}', '\'', '`'], "");
    text = re(&E, r"[^0-9A-Za-z\u{C0}-\u{D6}\u{D8}-\u{F6}\u{F8}-\u{FF}]+").replace_all(&text, " ").into_owned();
    // re.sub(r"\s+", " ", text).strip()
    let mut out = String::with_capacity(text.len());
    let mut in_space = false;
    for c in text.chars() {
        if py_space(c) {
            if !in_space {
                out.push(' ');
            }
            in_space = true;
        } else {
            out.push(c);
            in_space = false;
        }
    }
    py_strip(&out).to_string()
}

/// `_karafun_entry_artist_title(entry)`: the queue entry's artist and title, falling back to a "Artist - Title" display name.
pub fn entry_artist_title(artist: &str, title: &str, display_name: &str, display: &str) -> (String, String) {
    let artist = py_strip(artist).to_string();
    let title = py_strip(title).to_string();
    if !title.is_empty() {
        return (artist, title);
    }
    // `a or b`: the RAW display_name wins whenever it is non-empty, even if it is only spaces; it is stripped afterwards
    let shown = py_strip(if !display_name.is_empty() { display_name } else { display });
    if shown.contains(" - ") {
        let parts: Vec<&str> = shown.split(" - ").map(py_strip).filter(|p| !p.is_empty()).collect();
        if parts.len() >= 2 {
            let a = if artist.is_empty() { parts[0].to_string() } else { artist };
            return (a, parts[1].to_string());
        }
    }
    let t = if !shown.is_empty() { shown.to_string() } else { "KaraFun Song".to_string() };
    (artist, t)
}

/// `_karafun_search_queries_for_entry(entry)`: artist+title, title+artist, title, artist; empties and case-insensitive repeats dropped.
pub fn search_queries_for_entry(artist: &str, title: &str, display_name: &str, display: &str) -> Vec<String> {
    let (artist, title) = entry_artist_title(artist, title, display_name, display);
    let artist = clean_search_part(&artist);
    let title = clean_search_part(&title);
    let join = |a: &str, b: &str| [a, b].iter().filter(|p| !p.is_empty()).cloned().collect::<Vec<_>>().join(" ");
    let candidates = [join(&artist, &title), join(&title, &artist), title.clone(), artist.clone()];
    let mut out: Vec<String> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for q in candidates {
        let key = py_strip(&q).to_lowercase();
        if !key.is_empty() && !seen.contains(&key) {
            out.push(py_strip(&q).to_string());
            seen.push(key);
        }
    }
    out
}

/// What the app does with one search answer for one query (transcribed from `_automate_karafun_search_and_play`; see the source guard in
/// test_karafun_script_rs.py).  `Accepted` carries the answer's fields (kind, x, y, duration text).
#[derive(Debug, PartialEq)]
pub enum Verdict {
    Accepted(Vec<String>),
    RejectedArtistUnconfirmed(String),
    NoMatch(String),
}

/// Python `str.casefold`: per character, the generated table where it differs from lowercase, else the plain lowercase of that one character.
fn casefold(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match crate::casefold_table::special(c) {
            Some(x) => out.push_str(x),
            None => out.extend(c.to_lowercase()),
        }
    }
    out
}

/// Which kinds this query may accept: a query that does not carry the artist (title-only rungs) may only accept an artist-verified FOUND row.
pub fn accepted_kinds(safe_artist: &str, query: &str) -> Vec<&'static str> {
    let artist = py_strip(safe_artist);
    let has_artist = !artist.is_empty() && casefold(query).contains(&casefold(artist));
    if !artist.is_empty() && !has_artist { vec!["FOUND"] } else { vec!["FOUND", "TITLE_ONLY", "FIRST"] }
}

pub fn classify_search_result(candidate: &str, safe_artist: &str, query: &str) -> Verdict {
    let accepted = accepted_kinds(safe_artist, query);
    let parts: Vec<String> = candidate.split('|').map(str::to_string).collect();
    let kind = parts.first().cloned().unwrap_or_default();
    if parts.len() >= 3 && accepted.contains(&kind.as_str()) {
        return Verdict::Accepted(parts);
    }
    if parts.len() >= 3 && ["FOUND", "TITLE_ONLY", "FIRST"].contains(&kind.as_str()) {
        return Verdict::RejectedArtistUnconfirmed(kind);
    }
    Verdict::NoMatch(parts.last().cloned().unwrap_or_else(|| "KaraFun search failed".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn clocks() {
        assert_eq!(normalize_duration_seconds(&json!("3:41")), Duration::Seconds(221));
        assert_eq!(normalize_duration_seconds(&json!("1:02:03")), Duration::Seconds(3723));
        assert_eq!(normalize_duration_seconds(&json!("0:60")), Duration::None);
        assert_eq!(normalize_duration_seconds(&json!(2.5)), Duration::Seconds(2)); // banker's rounding like Python round()
        assert_eq!(normalize_duration_seconds(&json!(true)), Duration::None);
        assert_eq!(normalize_duration_seconds(&json!("\u{663}")), Duration::Decline);
    }

    #[test]
    fn remaining_and_attention() {
        assert_eq!(fallback_remaining(&json!(200), &json!(50), &json!(30)), 120);
        assert_eq!(fallback_remaining(&json!(200), &json!(50), &Value::Null), 150);
        assert!(!adjustment_needs_attention(-1, 100, "ADJUSTED|false|false", true));
        assert!(adjustment_needs_attention(-1, 100, "ADJUSTED|false|false", false));
        assert!(!adjustment_needs_attention(-1, 100, "ADJUSTED|true|true", false));
    }

    #[test]
    fn the_query_ladder() {
        assert_eq!(search_queries_for_entry("Sugarcult", "Memory", "", ""), vec!["Sugarcult Memory", "Memory Sugarcult", "Memory", "Sugarcult"]);
        assert_eq!(search_queries_for_entry("", "", "AC/DC - Back In Black", ""), vec!["AC DC Back In Black", "Back In Black AC DC", "Back In Black", "AC DC"]);
        assert_eq!(search_queries_for_entry("same", "SAME", "", ""), vec!["same SAME", "SAME"]);
        assert_eq!(clean_search_part("Song (Karaoke Version)"), "Song");
        // Python quirk kept on purpose: `feat\\.?` in a raw string is a literal backslash, so "feat." is NOT stripped and the query keeps it
        assert_eq!(clean_search_part("Hello (feat. X)"), "Hello feat X");
    }

    #[test]
    fn accept_and_reject() {
        assert_eq!(classify_search_result("FOUND|1|2|3:00", "Sugarcult", "Memory"), Verdict::Accepted(vec!["FOUND".into(), "1".into(), "2".into(), "3:00".into()]));
        assert_eq!(classify_search_result("TITLE_ONLY|1|2|3:00", "Sugarcult", "Memory"), Verdict::RejectedArtistUnconfirmed("TITLE_ONLY".into()));
        assert!(matches!(classify_search_result("TITLE_ONLY|1|2|3:00", "Sugarcult", "Sugarcult Memory"), Verdict::Accepted(_)));
        assert_eq!(classify_search_result("ERROR|No usable KaraFun search result", "", "q"), Verdict::NoMatch("No usable KaraFun search result".into()));
    }
}
