//! Port of `track_display.py`: the artist / title the audience card shows for a music file, from a
//! messy file name and tags. Pure functions. `display_from_path` is the one entry point.
//!
//! Python's `re` is replaced by hand-written matchers (the Python uses look-behinds, lazy groups and
//! `$`-before-trailing-newline, which the `regex` crate does not offer). Every matcher keeps the
//! Python's backtracking order; `tools/track_display_parity.py` fuzzes this against the real module.
//!
//! Known, deliberate approximations (documented, not covered by the fuzz alphabet):
//! - `\d` is ASCII 0-9 here; Python's is every Unicode decimal digit.
//! - `\w` is "alphanumeric or _" (Rust's definition); Python excludes combining marks.
//! - `casefold()` is `to_lowercase()` (differs for e.g. the German sharp s).
use unicode_normalization::UnicodeNormalization;

type Chars = [char];

fn sp(c: char) -> bool {
    // Python's str.isspace(): White_Space plus the four ASCII separators 0x1c-0x1f
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}
fn word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}
fn dig(c: char) -> bool {
    c.is_ascii_digit()
}
fn is_dash3(c: char) -> bool {
    matches!(c, '-' | '–' | '—')
}
fn skip_sp(t: &Chars, mut i: usize) -> usize {
    while i < t.len() && sp(t[i]) {
        i += 1;
    }
    i
}
/// `$` without MULTILINE: the end, or just before a final newline.
fn at_end(t: &Chars, i: usize) -> bool {
    i == t.len() || (i + 1 == t.len() && t[i] == '\n')
}
/// Case-insensitive match of an ASCII letter the way Python's `re.IGNORECASE` does it
/// (adds the Kelvin sign, long s, dotless/dotted i).
fn ci(c: char, lit: char) -> bool {
    if c == lit {
        return true;
    }
    if lit.is_ascii_alphabetic() {
        if c.to_ascii_lowercase() == lit.to_ascii_lowercase() {
            return true;
        }
        return match lit.to_ascii_lowercase() {
            'k' => c == '\u{212a}',
            's' => c == '\u{17f}',
            'i' => c == '\u{131}' || c == '\u{130}',
            _ => false,
        };
    }
    false
}
fn lit(t: &Chars, i: usize, s: &str) -> Option<usize> {
    let mut p = i;
    for l in s.chars() {
        if p >= t.len() || !ci(t[p], l) {
            return None;
        }
        p += 1;
    }
    Some(p)
}
fn collect(t: &Chars) -> String {
    t.iter().collect()
}
fn pystrip(s: &str) -> &str {
    s.trim_matches(sp)
}

/// re.sub(pattern, repl, text) for a matcher returning the match end at a start position.
fn sub_all(text: &str, repl: &str, m: impl Fn(&Chars, usize) -> Option<usize>) -> String {
    let t: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < t.len() {
        if let Some(e) = m(&t, i) {
            out.push_str(repl);
            // every pattern used here consumes at least one char
            i = e.max(i + 1);
        } else {
            out.push(t[i]);
            i += 1;
        }
    }
    out
}

fn norm(s: &str) -> String {
    s.nfc().collect()
}

fn tidy(text: &str) -> String {
    // re.sub(r"\s+", " ", text).strip()
    let collapsed = collapse_ws(text);
    let text = pystrip(&collapsed).to_string();
    // re.sub(r"\(\s*\)|\[\s*\]", "", text)
    let t: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < t.len() {
        if t[i] == '(' || t[i] == '[' {
            let close = if t[i] == '(' { ')' } else { ']' };
            let j = skip_sp(&t, i + 1);
            if j < t.len() && t[j] == close {
                i = j + 1;
                continue;
            }
        }
        out.push(t[i]);
        i += 1;
    }
    let collapsed = collapse_ws(&out);
    pystrip(&collapsed).trim_matches(|c| matches!(c, ' ' | '-' | '–' | '—' | '_' | ',')).to_string()
}
fn collapse_ws(s: &str) -> String {
    let mut out = String::new();
    let mut in_ws = false;
    for c in s.chars() {
        if sp(c) {
            if !in_ws {
                out.push(' ');
            }
            in_ws = true;
        } else {
            out.push(c);
            in_ws = false;
        }
    }
    out
}

fn fix_underscores(text: &str) -> String {
    // \s*_-_\s*  ->  " - "
    let t: Vec<char> = text.chars().collect();
    let mut s1 = String::new();
    let mut i = 0;
    while i < t.len() {
        let j = skip_sp(&t, i);
        if j + 3 <= t.len() && t[j] == '_' && t[j + 1] == '-' && t[j + 2] == '_' {
            s1.push_str(" - ");
            i = skip_sp(&t, j + 3);
        } else {
            s1.push(t[i]);
            i += 1;
        }
    }
    // (?<=[A-Za-z])_(?=(?:t|s|re|ve|ll|d|m)\b)  ->  '
    let t: Vec<char> = s1.chars().collect();
    let mut s2 = String::new();
    for i in 0..t.len() {
        let mut c = t[i];
        if c == '_' && i > 0 && t[i - 1].is_ascii_alphabetic() {
            for alt in ["t", "s", "re", "ve", "ll", "d", "m"] {
                let n = alt.len();
                if t.len() >= i + 1 + n
                    && t[i + 1..i + 1 + n].iter().copied().eq(alt.chars())
                    && (i + 1 + n == t.len() || !word(t[i + 1 + n]))
                {
                    c = '\'';
                    break;
                }
            }
        }
        s2.push(c);
    }
    // (?<=[A-Za-z])_(?=\s)  ->  '
    let t: Vec<char> = s2.chars().collect();
    let mut s3 = String::new();
    for i in 0..t.len() {
        let mut c = t[i];
        if c == '_' && i > 0 && t[i - 1].is_ascii_alphabetic() && i + 1 < t.len() && sp(t[i + 1]) {
            c = '\'';
        }
        s3.push(c);
    }
    s3.replace('_', " ")
}

fn normalise_feat(text: &str) -> String {
    let t: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    'outer: while i < t.len() {
        if i > 0 && (sp(t[i - 1]) || t[i - 1] == '(') {
            for alt in ["F", "f", "ft", "Ft", "FT", "feat", "Feat", "featuring", "Featuring"] {
                let n = alt.len();
                if t.len() >= i + n && t[i..i + n].iter().copied().eq(alt.chars()) {
                    let mut p = i + n;
                    if p < t.len() && t[p] == '.' {
                        p += 1;
                    }
                    let q = skip_sp(&t, p);
                    if q > p && q < t.len() && (word(t[q]) || matches!(t[q], '"' | '\'' | '‘' | '“' | '(')) {
                        out.push_str("feat. ");
                        i = q;
                        continue 'outer;
                    }
                }
            }
        }
        out.push(t[i]);
        i += 1;
    }
    out
}

fn dedupe_halves(text: &str) -> String {
    let t: Vec<char> = text.chars().collect();
    for a in 1..=t.len() {
        if t[..a].contains(&'\n') {
            break;
        }
        let d = skip_sp(&t, a);
        if d < t.len() && matches!(t[d], '-' | '–') && d + 1 < t.len() {
            let q = skip_sp(&t, d + 1);
            let g2: &Chars = if q < t.len() {
                if t[q..].contains(&'\n') {
                    continue;
                }
                &t[q..]
            } else {
                if t[t.len() - 1] == '\n' {
                    continue;
                }
                &t[t.len() - 1..]
            };
            let g1 = collect(&t[..a]);
            let g2 = collect(g2);
            return if pystrip(&g1).to_lowercase() == pystrip(&g2).to_lowercase() { pystrip(&g1).to_string() } else { text.to_string() };
        }
    }
    text.to_string()
}

const SMALL: [&str; 20] = [
    "a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs", "via", "with", "n", "feat.", "ft.",
];

fn title_case(text: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    for (i, w) in text.split(' ').enumerate() {
        let bare = w.trim_matches(|c| "()[]\"'‘’“”".contains(c));
        let low = bare.to_lowercase();
        if i > 0 && (SMALL.contains(&low.as_str()) || low == "vs.") {
            out.push(w.to_string());
            continue;
        }
        let mut done = false;
        let s: String = w
            .chars()
            .map(|c| {
                if !done && c.is_ascii_alphabetic() {
                    done = true;
                    c.to_ascii_uppercase()
                } else {
                    c
                }
            })
            .collect();
        out.push(s);
    }
    out.join(" ")
}

// ---- the bracket patterns ------------------------------------------------------------------------

fn pack_keyword(t: &Chars, i: usize) -> Option<usize> {
    // ultimix | kwikmix | ulti-?remix | ulti-?mix, then \b
    let ends = [lit(t, i, "ultimix"), lit(t, i, "kwikmix"), opt_hyphen(t, i, "remix"), opt_hyphen(t, i, "mix")];
    for e in ends.into_iter().flatten() {
        if e >= t.len() || !word(t[e]) {
            return Some(e);
        }
    }
    None
}
fn opt_hyphen(t: &Chars, i: usize, tail: &str) -> Option<usize> {
    let p = lit(t, i, "ulti")?;
    if p < t.len() && t[p] == '-' {
        if let Some(e) = lit(t, p + 1, tail) {
            return Some(e);
        }
    }
    lit(t, p, tail)
}
fn is_closer(c: char) -> bool {
    c == ')' || c == ']'
}
fn is_opener(c: char) -> bool {
    c == '(' || c == '['
}

fn pack_bracket(t: &Chars, i: usize) -> Option<usize> {
    let j = skip_sp(t, i);
    if j >= t.len() || !is_opener(t[j]) {
        return None;
    }
    let k = skip_sp(t, j + 1);
    let e = pack_keyword(t, k)?;
    let c = (e..t.len()).find(|&x| is_closer(t[x]))?;
    Some(c + 1)
}

fn bpm_bracket(t: &Chars, i: usize) -> Option<usize> {
    let j = skip_sp(t, i);
    if j >= t.len() || !is_opener(t[j]) {
        return None;
    }
    let c = (j + 1..t.len()).find(|&x| is_closer(t[x]))?;
    for b in j + 1..c {
        if let Some(e) = lit(t, b, "bpm") {
            if e <= c {
                let mut p = b;
                while p > j + 1 && sp(t[p - 1]) {
                    p -= 1;
                }
                if p > j + 1 && dig(t[p - 1]) {
                    return Some(c + 1);
                }
            }
        }
    }
    None
}

fn ws_then(t: &Chars, i: usize) -> Option<usize> {
    // \s+
    let j = skip_sp(t, i);
    if j > i { Some(j) } else { None }
}
fn first_of(t: &Chars, i: usize, words: &[&str]) -> Vec<usize> {
    words.iter().filter_map(|w| lit(t, i, w)).collect()
}
fn four_digits(t: &Chars, i: usize) -> Option<usize> {
    if i + 4 <= t.len() && t[i..i + 4].iter().all(|&c| dig(c)) { Some(i + 4) } else { None }
}

fn noise_ends(t: &Chars, i: usize) -> Vec<usize> {
    let mut ends = Vec::new();
    // official(?:\s+(?:music|lyric|lyrics|audio|video|visuali[sz]er))*
    fn official(t: &Chars, p: usize, ends: &mut Vec<usize>) {
        ends.push(p);
        if let Some(q) = ws_then(t, p) {
            let mut nxt = first_of(t, q, &["music", "lyric", "lyrics", "audio", "video"]);
            if let Some(v) = lit(t, q, "visuali") {
                if v < t.len() && (ci(t[v], 's') || ci(t[v], 'z')) {
                    if let Some(e) = lit(t, v + 1, "er") {
                        nxt.push(e);
                    }
                }
            }
            for e in nxt {
                official(t, e, ends);
            }
        }
    }
    if let Some(p) = lit(t, i, "official") {
        official(t, p, &mut ends);
    }
    // lyrics?(?:\s+video)?
    if let Some(p) = lit(t, i, "lyric") {
        let mut bases = vec![p];
        if p < t.len() && ci(t[p], 's') {
            bases.push(p + 1);
        }
        for b in bases {
            ends.push(b);
            if let Some(q) = ws_then(t, b) {
                if let Some(e) = lit(t, q, "video") {
                    ends.push(e);
                }
            }
        }
    }
    ends.extend(first_of(t, i, &["audio", "video", "hd", "hq", "4k", "explicit", "clean", "dirty"]));
    // radio\s+(?:edit|mix|version)
    if let Some(p) = lit(t, i, "radio") {
        if let Some(q) = ws_then(t, p) {
            ends.extend(first_of(t, q, &["edit", "mix", "version"]));
        }
    }
    for head in ["single", "album"] {
        if let Some(p) = lit(t, i, head) {
            if let Some(q) = ws_then(t, p) {
                ends.extend(lit(t, q, "version"));
            }
        }
    }
    // (?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?
    let mut starts = vec![i];
    if let Some(p) = four_digits(t, i) {
        if let Some(q) = ws_then(t, p) {
            starts.push(q);
        }
    }
    for s in starts {
        if let Some(p) = lit(t, s, "remaster") {
            let mut bases = vec![p];
            if let Some(e) = lit(t, p, "ed") {
                bases.push(e);
            }
            for b in bases {
                ends.push(b);
                if let Some(q) = ws_then(t, b) {
                    ends.extend(four_digits(t, q));
                }
            }
        }
    }
    ends
}

fn noise_bracket(t: &Chars, i: usize) -> Option<usize> {
    let j = skip_sp(t, i);
    if j >= t.len() || !is_opener(t[j]) {
        return None;
    }
    let k = skip_sp(t, j + 1);
    for e in noise_ends(t, k) {
        let c = skip_sp(t, e);
        if c < t.len() && is_closer(t[c]) {
            return Some(c + 1);
        }
    }
    None
}

fn trailing_bpm(t: &Chars, i: usize) -> Option<usize> {
    // (?<=[)\]])\s+\d{2,3}$
    if i == 0 || !is_closer(t[i - 1]) {
        return None;
    }
    let j = ws_then(t, i)?;
    for n in [3usize, 2] {
        if j + n <= t.len() && t[j..j + n].iter().all(|&c| dig(c)) && at_end(t, j + n) {
            return Some(j + n);
        }
    }
    None
}

pub fn clean_title(text: &str) -> String {
    let text = fix_underscores(&norm(text));
    let text = sub_all(&text, "", trailing_bpm);
    let text = sub_all(&text, "", pack_bracket);
    let text = sub_all(&text, "", bpm_bracket);
    let text = sub_all(&text, "", noise_bracket);
    let text = normalise_feat(&text);
    let mut text = tidy(&dedupe_halves(&tidy(&text)));
    if !text.is_empty() && text == text.to_lowercase() && text.chars().any(|c| c.is_ascii_lowercase()) {
        text = title_case(&text);
    }
    text
}

pub fn clean_artist(text: &str) -> String {
    tidy(&normalise_feat(&fix_underscores(&norm(text))))
}

fn stem(name: &str) -> String {
    let base = name.rsplit('/').next().unwrap_or("");
    let t: Vec<char> = base.chars().collect();
    const EXTS: [&str; 14] = ["mp3", "flac", "m4a", "m4b", "aac", "wav", "aif", "ogg", "oga", "opus", "wma", "mp4", "mka", "alac"];
    for i in 0..t.len() {
        if t[i] != '.' {
            continue;
        }
        let mut ends = Vec::new();
        for e in EXTS {
            if let Some(p) = lit(&t, i + 1, e) {
                ends.push(p);
                if e == "aif" && p < t.len() && ci(t[p], 'f') {
                    ends.push(p + 1);
                }
            }
        }
        if ends.iter().any(|&e| at_end(&t, e)) {
            return collect(&t[..i]) + &collect(&t[ends.iter().copied().find(|&e| at_end(&t, e)).unwrap()..]);
        }
    }
    base.to_string()
}

fn track_number_prefix(t: &Chars) -> Option<usize> {
    // ^\(?\d{1,3}\)?\s*[.\-_)]\s*(?=\S)
    let mut p = 0;
    if p < t.len() && t[p] == '(' {
        p += 1;
    }
    let s = p;
    while p < t.len() && dig(t[p]) {
        p += 1;
    }
    if !(1..=3).contains(&(p - s)) {
        return None;
    }
    let mut starts = Vec::new();
    if p < t.len() && t[p] == ')' {
        starts.push(p + 1);
    }
    starts.push(p);
    for q in starts {
        let r = skip_sp(t, q);
        if r < t.len() && matches!(t[r], '.' | '-' | '_' | ')') {
            let e = skip_sp(t, r + 1);
            if e < t.len() {
                return Some(e);
            }
        }
    }
    None
}

fn segment_number_len(t: &Chars) -> usize {
    // ^\(?\d{1,3}\)?\.\s*(?=\S) -> match length, 0 when no match
    let mut p = 0;
    if p < t.len() && t[p] == '(' {
        p += 1;
    }
    let s = p;
    while p < t.len() && dig(t[p]) {
        p += 1;
    }
    if !(1..=3).contains(&(p - s)) {
        return 0;
    }
    if p < t.len() && t[p] == ')' {
        p += 1;
    }
    if p < t.len() && t[p] == '.' {
        let e = skip_sp(t, p + 1);
        if e < t.len() {
            return e;
        }
    }
    0
}

fn track_number_only(t: &Chars) -> bool {
    // ^\(?\d{1,3}\)?\.?$
    let mut p = 0;
    if p < t.len() && t[p] == '(' {
        p += 1;
    }
    let s = p;
    while p < t.len() && dig(t[p]) {
        p += 1;
    }
    if !(1..=3).contains(&(p - s)) {
        return false;
    }
    if p < t.len() && t[p] == ')' {
        p += 1;
    }
    if p < t.len() && t[p] == '.' {
        p += 1;
    }
    at_end(t, p)
}

fn pack_segment(t: &Chars) -> bool {
    // ^(?:ultimix|kwikmix|ulti-?remix|ulti-?mix)\b.*$
    match pack_keyword(t, 0) {
        Some(e) => {
            let mut p = e;
            while p < t.len() && t[p] != '\n' {
                p += 1;
            }
            at_end(t, p)
        }
        None => false,
    }
}

fn split_separator(t: &Chars) -> Vec<Vec<char>> {
    // re.split(r"\s+[-–—]\s+", ...)
    let mut parts = Vec::new();
    let mut cur_start = 0;
    let mut i = 0;
    while i < t.len() {
        if sp(t[i]) {
            let j = skip_sp(t, i);
            if j < t.len() && is_dash3(t[j]) && j + 1 < t.len() && sp(t[j + 1]) {
                parts.push(t[cur_start..i].to_vec());
                let e = skip_sp(t, j + 1);
                cur_start = e;
                i = e;
                continue;
            }
        }
        i += 1;
    }
    parts.push(t[cur_start..].to_vec());
    parts
}
fn has_separator(t: &Chars) -> bool {
    split_separator(t).len() > 1
}

pub fn parse_filename(name: &str) -> (String, String) {
    let s = fix_underscores(&norm(&stem(name)));
    let mut stem_s = pystrip(&s).to_string();
    if stem_s.is_empty() {
        return (String::new(), String::new());
    }
    let t: Vec<char> = stem_s.chars().collect();
    if let Some(e) = track_number_prefix(&t) {
        if has_separator(&t[e..]) {
            stem_s = collect(&t[e..]);
        }
    }
    let t: Vec<char> = stem_s.chars().collect();
    let mut kept: Vec<String> = Vec::new();
    for seg in split_separator(&t) {
        let seg_s = collect(&seg);
        let seg_s = pystrip(&seg_s);
        if seg_s.is_empty() {
            continue;
        }
        let sc: Vec<char> = seg_s.chars().collect();
        if track_number_only(&sc) || pack_segment(&sc) {
            continue;
        }
        let n = segment_number_len(&sc);
        let seg_out = collect(&sc[n..]);
        if !seg_out.is_empty() {
            kept.push(seg_out);
        }
    }
    match kept.len() {
        0 => (String::new(), clean_title(&stem_s)),
        1 => (String::new(), clean_title(&kept[0])),
        _ => (clean_artist(&kept[0]), clean_title(kept.last().unwrap())),
    }
}

const JUNK: [&str; 15] = [
    "", "unknown", "unknown artist", "unknown title", "various", "various artists", "va", "n/a", "na", "none", "untitled", "<unknown>",
    "no artist", "artist", "title",
];

fn track_tag_shape(low: &str) -> bool {
    // (?:audio\s+)?track\s*\d*  (fullmatch)
    let t: Vec<char> = low.chars().collect();
    let mut starts = vec![0usize];
    if let Some(p) = lit(&t, 0, "audio") {
        if let Some(q) = ws_then(&t, p) {
            starts.insert(0, q);
        }
    }
    for s in starts {
        if let Some(p) = lit(&t, s, "track") {
            let mut q = skip_sp(&t, p);
            while q < t.len() && dig(t[q]) {
                q += 1;
            }
            if q == t.len() {
                return true;
            }
        }
    }
    false
}

fn usable_tag(value: &str) -> String {
    let v = tidy(&norm(value));
    let low = v.to_lowercase();
    if JUNK.contains(&low.as_str()) || track_tag_shape(&low) || low.contains("http") || low.contains("www.") {
        return String::new();
    }
    v
}

/// (artist, title) to show for a music file. Good tags win; the file name fills in whatever the tags lack.
pub fn display_from_path(path: &str, tag_artist: &str, tag_title: &str) -> (String, String) {
    let (file_artist, file_title) = parse_filename(path);
    let artist = clean_artist(&usable_tag(tag_artist));
    let title = clean_title(&usable_tag(tag_title));
    if !artist.is_empty() && !title.is_empty() {
        return (artist, title);
    }
    (if artist.is_empty() { file_artist } else { artist }, if title.is_empty() { file_title } else { title })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_examples_from_the_python_docstrings() {
        assert_eq!(display_from_path("/m/Calvin Harris - One Kiss - 01. One Kiss.flac", "", ""), ("Calvin Harris".into(), "One Kiss".into()));
        assert_eq!(display_from_path("Don_t Stop.mp3", "", ""), ("".into(), "Don't Stop".into()));
        assert_eq!(clean_title("no tears left to cry- no tears left to cry"), "No Tears Left to Cry");
        assert_eq!(clean_title("Song (Ultimix By X) 126"), "Song");
        assert_eq!(clean_title("Song [Official Video]"), "Song");
        assert_eq!(clean_artist("A ft Someone"), "A feat. Someone");
    }

    #[test]
    fn junk_tags_are_ignored_and_the_file_name_fills_in() {
        assert_eq!(display_from_path("Daft Punk - Get Lucky.mp3", "Unknown", "Track 01"), ("Daft Punk".into(), "Get Lucky".into()));
        assert_eq!(display_from_path("x.mp3", "Good Artist", "Good Title"), ("Good Artist".into(), "Good Title".into()));
    }
}
