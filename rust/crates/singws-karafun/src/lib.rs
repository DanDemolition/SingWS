//! KaraFun search script builder, a line-for-line port of `KaraokeApp._karafun_search_script` (0.2.18.1.py).
//!
//! The ~230 fixed script lines live in `script_lines.rs`, GENERATED from the Python source by `tools/gen_karafun_script_rs.py` (never edited by
//! hand; the tool's `--check` fails when it is stale). What is written by hand here is only the part that has logic: quoting a value as an
//! AppleScript literal, reducing a queued title to what KaraFun lists, and putting the blocks together for the flags.
mod casefold_table;
mod script_lines;
pub mod pure;

/// Python `str.isspace` for one character (what `str.strip()` and the regex `\s` use on text).
pub(crate) fn py_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\u{0b}' | '\u{0c}' | '\r' | '\u{1c}'..='\u{1f}' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

pub(crate) fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// `_karafun_applescript_literal`: backslash and quote escaped, line breaks turned into spaces, wrapped in quotes. `None` and "" give `""`.
pub fn applescript_literal(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for c in value.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\r' | '\n' => out.push(' '),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Does `chars[i..]` match `(?:\s*[(\[][^)\]]*[)\]])+\s*$`? Each group is forced: spaces, an opener, everything up to the FIRST closer.
fn trailing_qualifiers_from(chars: &[char], mut i: usize) -> bool {
    let n = chars.len();
    let mut groups = 0;
    loop {
        let mut j = i;
        while j < n && py_space(chars[j]) {
            j += 1;
        }
        if j < n && matches!(chars[j], '(' | '[') {
            let mut k = j + 1;
            while k < n && !matches!(chars[k], ')' | ']') {
                k += 1;
            }
            if k >= n {
                return false; // opener never closed
            }
            groups += 1;
            i = k + 1;
            continue;
        }
        // no further group: only whitespace may remain
        return groups > 0 && chars[j..].is_empty();
    }
}

/// `karafun_match_title`: the queued title without trailing "(live)" / "[remix]" style qualifiers; an empty result keeps the original.
pub fn match_title(title: &str) -> String {
    let text = py_strip(title);
    let chars: Vec<char> = text.chars().collect();
    let mut cut = None;
    for i in 0..=chars.len() {
        if trailing_qualifiers_from(&chars, i) {
            cut = Some(i);
            break;
        }
    }
    let stripped: String = match cut {
        Some(i) => chars[..i].iter().collect(),
        None => text.to_string(),
    };
    let stripped = py_strip(&stripped);
    if stripped.is_empty() { text.to_string() } else { stripped.to_string() }
}

/// `_karafun_search_script(query=, safe_title=, safe_artist=, require_exact_title=, resolve_only=)`: the AppleScript lines.
pub fn search_script(query: &str, safe_title: &str, safe_artist: &str, require_exact_title: bool, resolve_only: bool) -> Vec<String> {
    let query_literal = applescript_literal(query);
    let safe_title_literal = applescript_literal(&match_title(safe_title));
    let safe_artist_literal = applescript_literal(safe_artist);
    let mut lines = if resolve_only { script_lines::resolve_only() } else { script_lines::base(&query_literal) };
    // Python tests `safe_title` as given (not the reduced title) for truthiness
    if require_exact_title && !safe_title.is_empty() {
        lines.extend(script_lines::exact_common());
        if !safe_artist.is_empty() {
            lines.extend(script_lines::artist_pass(&safe_title_literal, &safe_artist_literal));
        }
        let verdict = if !safe_artist.is_empty() { "TITLE_ONLY" } else { "FOUND" };
        lines.extend(script_lines::title_pass(&safe_title_literal, verdict));
    } else {
        lines.push("return \"SEARCHED\"".to_string());
    }
    lines.extend(script_lines::tail());
    lines.extend(script_lines::duration_handler());
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn literal_escapes_like_python() {
        assert_eq!(applescript_literal(""), "\"\"");
        assert_eq!(applescript_literal("a\"b\\c\r\nd"), "\"a\\\"b\\\\c  d\"");
    }

    #[test]
    fn title_loses_trailing_qualifiers_only() {
        assert_eq!(match_title("Just the Way You Are (live)"), "Just the Way You Are");
        assert_eq!(match_title("Song [Remix] (Radio Edit)  "), "Song");
        assert_eq!(match_title("(Everything I Do) I Do It For You"), "(Everything I Do) I Do It For You");
        assert_eq!(match_title("(live)"), "(live)");
        assert_eq!(match_title("Open (never closed"), "Open (never closed");
        assert_eq!(match_title(""), "");
    }

    #[test]
    fn flags_pick_the_blocks() {
        let plain = search_script("q", "", "", false, false);
        assert!(plain.contains(&"return \"SEARCHED\"".to_string()));
        assert!(plain.contains(&"keystroke \"q\"".to_string()));
        let exact = search_script("q", "Memory", "Sugarcult", true, false);
        assert!(exact.iter().any(|l| l.contains("TITLE_ONLY|")));
        assert!(exact.iter().any(|l| l.contains("aName contains \"Sugarcult\"")));
        let no_artist = search_script("q", "Memory", "", true, false);
        assert!(no_artist.iter().any(|l| l.contains("\"FOUND|\" & rowX")));
        assert!(!no_artist.iter().any(|l| l.contains("aName contains")));
        let resolve = search_script("q", "Memory", "", true, true);
        assert!(resolve.iter().any(|l| l.contains("KaraFun results window not found")));
        assert!(!resolve.iter().any(|l| l.starts_with("keystroke")));
        // every script ends with the duration handler
        assert_eq!(resolve.last().map(String::as_str), Some("end isDurationText"));
    }
}
