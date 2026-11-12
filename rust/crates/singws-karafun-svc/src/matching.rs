//! W7: the song-matching rules of the page script `karafun_bridge.js` (`words`, `stripQualifiers`, `titleKey`, `artistMatches`, `choose`, and the search-term
//! ladder of `singwsPlay`), transcribed from the JavaScript (the oracle, run in Node). Pure: results are positions in the list the caller passes.
//! Unicode: JavaScript lower-cases, NFKD-normalises and classifies with `\p{L}\p{N}`; the classes come from `match_tables.rs` (generated from Node), the rest
//! from the standard library and the `unicode-normalization` crate. JavaScript string lengths count UTF-16 code units, so lengths here do too.
use crate::match_tables::LN_RANGES;
use unicode_normalization::UnicodeNormalization;

fn is_ln(c: char) -> bool {
    let cp = c as u32;
    LN_RANGES.binary_search_by(|&(a, b)| if cp < a { std::cmp::Ordering::Greater } else if cp > b { std::cmp::Ordering::Less } else { std::cmp::Ordering::Equal }).is_ok()
}

/// `s.replace(/[^\p{L}\p{N}]+/gu, ' ')`
fn squash(s: &str) -> String {
    let mut out = String::new();
    let mut in_sep = false;
    for c in s.chars() {
        if is_ln(c) {
            out.push(c);
            in_sep = false;
        } else if !in_sep {
            out.push(' ');
            in_sep = true;
        }
    }
    out
}

/// JavaScript `String.prototype.trim` (its WhiteSpace and LineTerminator sets, not Rust's).
pub fn js_trim(s: &str) -> &str {
    s.trim_matches(|c: char| matches!(c, '\t' | '\n' | '\u{b}' | '\u{c}' | '\r' | ' ' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}' | '\u{feff}'))
}

pub fn words(t: &str) -> String {
    let lowered = t.to_lowercase();
    let mut s = String::new();
    for c in lowered.nfkd() {
        if ('\u{300}'..='\u{36f}').contains(&c) {
            continue;
        }
        match c {
            '&' => s.push_str(" and "),
            '\u{2019}' | '\'' | '`' => {}
            c => s.push(c),
        }
    }
    squash(&s).trim_matches(' ').to_string()
}

const QUAL_WORDS: [&str; 13] = ["karaoke", "version", "explicit", "clean", "remaster", "live", "feat", "ft", "remix", "acoustic", "radio edit", "single", "mix"];

/// `QUAL.test(inner)`: /…/i on these ASCII words behaves as an ASCII case-insensitive substring search.
fn qual(inner: &str) -> bool {
    let l = inner.to_ascii_lowercase();
    QUAL_WORDS.iter().any(|w| l.contains(w))
}

/// `t.replace(/\(([^)]*)\)/g, ...)` (or the `[...]` form): a bracket pair holding a qualifier becomes one space.
fn strip_pairs(t: &str, open: char, close: char) -> String {
    let mut out = String::new();
    let mut rest = t;
    loop {
        let Some(i) = rest.find(open) else { break };
        let after = &rest[i + open.len_utf8()..];
        let Some(j) = after.find(close) else { break };
        let inner = &after[..j];
        out.push_str(&rest[..i]);
        if qual(inner) {
            out.push(' ');
        } else {
            out.push_str(&rest[i..i + open.len_utf8() + j + close.len_utf8()]);
        }
        rest = &after[j + close.len_utf8()..];
    }
    out.push_str(rest);
    out
}

pub fn strip_qualifiers(t: &str) -> String {
    strip_pairs(&strip_pairs(t, '(', ')'), '[', ']')
}

fn nospace(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect()
}

pub fn title_key(t: &str) -> String {
    nospace(&words(&strip_qualifiers(t)))
}

fn utf16_len(s: &str) -> usize {
    s.encode_utf16().count()
}

fn artist_words(t: &str) -> Vec<String> {
    words(t).split(' ').filter(|w| utf16_len(w) >= 3 && !["and", "the", "feat", "featuring"].contains(w)).map(|w| w.to_string()).collect()
}

pub fn artist_matches(have: &str, want: &str) -> bool {
    let h = nospace(&words(have));
    let w = nospace(&words(want));
    if h.is_empty() || w.is_empty() {
        return false;
    }
    if h.contains(&w) || w.contains(&h) {
        return true;
    }
    let hw = artist_words(have);
    artist_words(want).iter().any(|x| hw.contains(x))
}

pub struct Item<'a> {
    pub id: i64,
    pub title: &'a str,
    pub artist: &'a str,
}

/// `choose(list, id, wantArtist, wantTitle, query)`: the position of the chosen item, if any. (`wantTitle` empty = JavaScript falsy.)
pub fn choose(list: &[Item], id: i64, want_artist: &str, want_title: &str, query: &str) -> Option<usize> {
    if let Some(i) = list.iter().position(|r| r.id == id) {
        return Some(i);
    }
    if !want_title.is_empty() {
        let key = title_key(want_title);
        let mut same: Vec<usize> = (0..list.len()).filter(|&i| title_key(list[i].title) == key).collect();
        let want_words = words(want_title);
        same.sort_by_key(|&i| if words(list[i].title) == want_words { 0 } else { 1 }); // stable: the exactly-written title first
        if let Some(&c) = same.iter().find(|&&i| artist_matches(list[i].artist, want_artist)) {
            return Some(c);
        }
        if same.len() == 1 {
            return Some(same[0]);
        }
        return None;
    }
    let q = nospace(&words(query));
    if let Some(i) = list.iter().position(|r| nospace(&words(&format!("{} {}", r.artist, r.title))) == q) {
        return Some(i);
    }
    list.iter().position(|r| q.contains(&nospace(&words(r.artist))) && q.contains(&title_key(r.title)))
}

/// The terms `singwsPlay` searches, in order: each query as written, then without punctuation, no repeats.
pub fn terms(queries: &[&str]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for q in queries.iter().filter(|q| !js_trim(q).is_empty()) {
        let plain = squash(q);
        let plain = plain.trim_matches(' ').to_string();
        for t in [q.to_string(), plain] {
            if !t.is_empty() && !out.contains(&t) {
                out.push(t);
            }
        }
    }
    out
}

pub fn handle(req: &serde_json::Value) -> serde_json::Value {
    use serde_json::json;
    let s = |k: &str| req.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    match req.get("op").and_then(|o| o.as_str()).unwrap_or("") {
        "words" => json!({"v": words(&s("t"))}),
        "title_key" => json!({"v": title_key(&s("t"))}),
        "artist_matches" => json!({"v": artist_matches(&s("have"), &s("want"))}),
        "terms" => {
            let qs: Vec<String> = req.get("queries").and_then(|q| q.as_array()).map(|a| a.iter().filter_map(|x| x.as_str().map(|y| y.to_string())).collect()).unwrap_or_default();
            let refs: Vec<&str> = qs.iter().map(|x| x.as_str()).collect();
            json!({"v": terms(&refs)})
        }
        "choose" => {
            let list: Vec<(i64, String, String)> = req
                .get("list")
                .and_then(|l| l.as_array())
                .map(|a| a.iter().map(|r| (r["id"].as_i64().unwrap_or(0), r["title"].as_str().unwrap_or("").to_string(), r["artist"].as_str().unwrap_or("").to_string())).collect())
                .unwrap_or_default();
            let items: Vec<Item> = list.iter().map(|(id, t, a)| Item { id: *id, title: t, artist: a }).collect();
            json!({"v": choose(&items, req.get("id").and_then(|x| x.as_i64()).unwrap_or(0), &s("want_artist"), &s("want_title"), &s("query")).map(|i| i as i64).unwrap_or(-1)})
        }
        o => json!({"declined": format!("unknown op {o}")}),
    }
}
