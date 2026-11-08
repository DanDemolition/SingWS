//! KaraFun search script builder, a line-for-line port of `KaraokeApp._karafun_search_script` (0.2.18.1.py).
//!
//! The ~230 fixed script lines live in `script_lines.rs`, GENERATED from the Python source by `tools/gen_karafun_script_rs.py` (never edited by
//! hand; the tool's `--check` fails when it is stale). What is written by hand here is only the part that has logic: quoting a value as an
//! AppleScript literal, reducing a queued title to what KaraFun lists, and putting the blocks together for the flags.
mod builders;
mod casefold_table;
mod script_lines;
pub mod ladder;
pub mod monitor;
pub mod pure;
pub mod flow;
pub mod methods;
pub mod geometry;
pub mod readback;
pub mod startflow;
pub mod watchdog;

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

/// The fixed (argument-free) scripts, by name; None for an unknown name. Generated from the Python static builders.
pub fn static_script(name: &str) -> Option<Vec<String>> {
    Some(match name {
        "preflight" => builders::preflight(),
        "main_window_state" => builders::main_window_state(),
        "press_play" => builders::press_play(),
        "playback_menu_state" => builders::playback_menu_state(),
        "raise_results_window" => builders::raise_results_window(),
        "preview_probe" => builders::preview_probe(),
        "read_key_tempo" => builders::read_key_tempo(),
        "toggle_playback" => builders::toggle_playback(),
        "restore" => builders::restore(),
        "play_now_queue" => builders::play_now_queue(),
        "renderer_ready" => builders::renderer_ready(),
        "start_probe" => builders::start_probe(),
        "monitor_probe" => builders::monitor_probe(),
        _ => return None,
    })
}

/// `_karafun_search_diag_script(safe_title)`: the read-only description of KaraFun's window written to the log when a search finds no match.
pub fn search_diag_script(safe_title: &str) -> Vec<String> {
    builders::search_diag(&applescript_literal(&match_title(safe_title)))
}

/// `_karafun_apply_key_tempo_script`: click Key/Tempo menu items `abs(delta)` times (the item names are chosen by the caller from the sign of the delta).
pub fn apply_key_tempo_script(key_delta: i64, tempo_delta: i64, key_item: &str, tempo_item: &str) -> Vec<String> {
    builders::apply_key_tempo(key_delta, tempo_delta, key_item, tempo_item)
}

/// `_karafun_seek_script`: click Skip Forward/Back 10s `count` times.
pub fn seek_script(count: i64, item: &str) -> Vec<String> {
    builders::seek(count, item)
}

/// `_karafun_double_click_script`: double-click a result row at (x, y).
pub fn double_click_script(x: i64, y: i64) -> Vec<String> {
    builders::double_click(x, y)
}

/// `_karafun_handoff_script`: move and size KaraFun's Dual Renderer window onto the audience screen rectangle.
pub fn handoff_script(x: i64, y: i64, width: i64, height: i64) -> Vec<String> {
    builders::handoff(x, y, width, height)
}

/// `_karafun_click_point_script`: two clicks at a screen point.
pub fn click_point_script(point: (i64, i64)) -> Vec<String> {
    builders::click_point(point)
}

/// `_karafun_adjust_menu_script`: Key/Tempo menu clicks, `key_steps` and `tempo_steps` times.
pub fn adjust_menu_script(key_item: &str, key_steps: i64, tempo_item: &str, tempo_steps: i64) -> Vec<String> {
    builders::adjust_menu(key_item, key_steps, tempo_item, tempo_steps)
}

/// `_karafun_set_key_tempo_script`: set the Key and Tempo sliders to the requested values.
pub fn set_key_tempo_script(requested_key: i64, requested_tempo: i64) -> Vec<String> {
    builders::set_key_tempo(requested_key, requested_tempo)
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
