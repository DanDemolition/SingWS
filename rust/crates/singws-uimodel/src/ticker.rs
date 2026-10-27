//! The audience ticker's text, as a toolkit-free view model. Equal to three pieces of the app: `KaraokeApp.get_singer_names_for_ticker` (who is on the ticker:
//! active singers in queue order, each shown by their first unskipped song's duet name when it has one), `KaraokeApp._render_ticker_custom_message` (the
//! OpenKJ-style `%curSinger` ... `%nextSinger` substitutions, then empty fragments tidied) and `Ticker._format_queue_text` ("custom   |   1. A   |   2. B").
//! Pure text in, text out: no scrolling, painting or timers. Anything Python would do that this module cannot reproduce exactly is declined (`Err("decline: ...")`).

use serde_json::Value;
use singws_dbw::pyval::{R, get, is_py_space, py_str, py_str_or_empty, py_strip, truthy};

/// `KaraokeApp.is_singer_active`
pub fn singer_is_active(singer: &Value) -> bool {
    if truthy(get(singer, "skipped")) {
        return false;
    }
    let songs = get(singer, "songs");
    if !truthy(songs) {
        return false;
    }
    // iterating a non-list truthy value (a number) would raise in Python; JSON lists are what the queue holds
    let Some(list) = songs.as_array() else { return false };
    for song in list {
        if !song.is_object() {
            return true; // old format
        }
        if !truthy(get(song, "skipped")) {
            return true;
        }
    }
    false
}

/// The singers `get_singer_names_for_ticker` lists, as the values Python appends (a duet name or the singer's `name`; later stringified by `str`).
pub fn singer_list(queue: &Value) -> R<Vec<Value>> {
    let Some(q) = queue.as_array() else { return Ok(Vec::new()) };
    let mut out = Vec::new();
    for s in q {
        if !s.is_object() {
            return singws_dbw::pyval::decline("a queue entry is not an object");
        }
        if !singer_is_active(s) {
            continue;
        }
        let mut first: Option<&Value> = None;
        for song in get(s, "songs").as_array().into_iter().flatten() {
            if song.is_object() {
                if !truthy(get(song, "skipped")) {
                    first = Some(song);
                    break;
                }
            } else {
                first = Some(song);
                break;
            }
        }
        let Some(song) = first else { continue };
        if !truthy(song) {
            continue; // `if not first_active_song`
        }
        let duet: Value = if song.is_object() {
            get(song, "duet_display").clone()
        } else if let Some(a) = song.as_array().filter(|a| a.len() >= 3) {
            a[2].clone()
        } else {
            Value::Null
        };
        out.push(if truthy(&duet) { duet } else { s.get("name").cloned().unwrap_or_else(|| Value::String(String::new())) });
    }
    Ok(out)
}

/// `re.sub(r"\s*<c>\s*<c>+", " <c> ", s)`
fn collapse_doubled(s: &str, c: char) -> String {
    let ch: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < ch.len() {
        let mut j = i;
        while j < ch.len() && is_py_space(ch[j]) {
            j += 1;
        }
        if j < ch.len() && ch[j] == c {
            let mut k = j + 1;
            while k < ch.len() && is_py_space(ch[k]) {
                k += 1;
            }
            if k < ch.len() && ch[k] == c {
                while k < ch.len() && ch[k] == c {
                    k += 1;
                }
                out.push(' ');
                out.push(c);
                out.push(' ');
                i = k;
                continue;
            }
        }
        out.push(ch[i]);
        i += 1;
    }
    out
}

/// `re.sub(r"\s+", " ", s)`
fn squeeze_spaces(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut in_space = false;
    for c in s.chars() {
        if is_py_space(c) {
            if !in_space {
                out.push(' ');
            }
            in_space = true;
        } else {
            in_space = false;
            out.push(c);
        }
    }
    out
}

fn strip_dash_dot(s: &str) -> &str {
    s.trim_matches(|c| matches!(c, ' ' | '-' | '\u{2022}'))
}

/// `_render_ticker_custom_message(template, singer_list)`; `now_parts` is the app's `_now_singing_parts` (a list of at least three values, otherwise ignored).
pub fn render_custom_message(template: &str, now_parts: &Value, singer_list: &[Value]) -> R<String> {
    if template.is_empty() {
        return Ok(String::new());
    }
    let (mut cur_singer, mut cur_artist, mut cur_title) = (String::new(), String::new(), String::new());
    if let Some(p) = now_parts.as_array().filter(|p| p.len() >= 3) {
        cur_singer = py_strip(&py_str_or_empty(&p[0])?).to_string();
        cur_artist = py_strip(&py_str_or_empty(&p[1])?).to_string();
        cur_title = py_strip(&py_str_or_empty(&p[2])?).to_string();
    }
    let cur_song = if !cur_artist.is_empty() && !cur_title.is_empty() {
        format!("{cur_artist} \u{2022} {cur_title}")
    } else if !cur_title.is_empty() {
        cur_title.clone()
    } else {
        cur_artist.clone()
    };
    let next_singer = match singer_list.first() {
        Some(v) => py_strip(&py_str_or_empty(v)?).to_string(),
        None => String::new(),
    };
    let replacements: [(&str, String); 8] = [
        ("%m_curSinger", if cur_singer.is_empty() { String::new() } else { format!("Current: {cur_singer}") }),
        ("%curSinger", cur_singer.clone()),
        ("%curSong", cur_song),
        ("%m_curArtist", cur_artist.clone()),
        ("%curArtist", cur_artist),
        ("%m_curTitle", cur_title.clone()),
        ("%curTitle", cur_title),
        ("%nextSinger", if next_singer.is_empty() { String::new() } else { format!("Next: {next_singer}") }),
    ];
    let mut msg = template.to_string();
    for (token, value) in &replacements {
        msg = msg.replace(token, value); // in order, so a substituted name that contains a later token is substituted again, as in Python
    }
    let mut cleaned: Vec<String> = Vec::new();
    for part in msg.split('|') {
        let p = squeeze_spaces(py_strip(part));
        let p = strip_dash_dot(py_strip(&p)).to_string();
        let p = collapse_doubled(&p, '-');
        let p = collapse_doubled(&p, '\u{2022}');
        let p = squeeze_spaces(&p);
        let p = strip_dash_dot(py_strip(&p)).to_string();
        if !p.is_empty() {
            cleaned.push(p);
        }
    }
    Ok(cleaned.join(" | "))
}

/// What the ticker shows on its left: the custom message (when enabled and non-empty) and the numbered singers.
#[derive(Debug, Clone, PartialEq)]
pub struct TickerView {
    pub singers: Vec<String>,
    pub message: String,
    pub text: String,
}

/// `Ticker._format_queue_text` for a `(singer_list, custom_message)` pair
pub fn format_queue_text(singers: &[String], custom_message: &str) -> String {
    let msg = py_strip(custom_message);
    let formatted = singers.iter().enumerate().map(|(i, n)| format!("{}. {}", i + 1, n)).collect::<Vec<_>>().join("   |   ");
    if !msg.is_empty() {
        if formatted.is_empty() { msg.to_string() } else { format!("{msg}   |   {formatted}") }
    } else {
        formatted
    }
}

/// The ticker text for a queue. `settings` carries `ticker_custom_enabled` / `ticker_custom_message` (read as `get_singer_names_for_ticker` reads them:
/// a message that is not text, and not falsy, makes Python's `.strip()` raise inside its `try`, which turns the custom message off).
pub fn view(queue: &Value, settings: &Value, now_parts: &Value) -> R<TickerView> {
    let list = singer_list(queue)?;
    let enabled_v = get(settings, "ticker_custom_enabled");
    let msg_v = get(settings, "ticker_custom_message");
    let (custom_enabled, custom_msg) = if truthy(msg_v) && !msg_v.is_string() {
        (false, String::new())
    } else {
        (truthy(enabled_v), py_strip(msg_v.as_str().unwrap_or("")).to_string())
    };
    let message = if custom_enabled && !custom_msg.is_empty() { render_custom_message(&custom_msg, now_parts, &list)? } else { String::new() };
    let mut singers = Vec::with_capacity(list.len());
    for v in &list {
        singers.push(py_str(v)?);
    }
    let text = format_queue_text(&singers, &message);
    Ok(TickerView { singers, message, text })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn q() -> Value {
        json!([
            {"name": "Ann", "songs": [{"skipped": true}, {"title": "x", "duet_display": "Ann & Bo"}]},
            {"name": "Skipper", "skipped": true, "songs": [{}]},
            {"name": "Empty", "songs": []},
            {"name": "AllSkipped", "songs": [{"skipped": true}]},
            {"name": "Old", "songs": [["a", 1, "Old Duet"]]},
            {"name": "Cy", "songs": [{"title": "y"}]},
        ])
    }

    #[test]
    fn inactive_singers_and_skipped_songs_are_left_off_and_duet_names_win() {
        let names: Vec<_> = singer_list(&q()).unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
        assert_eq!(names, ["Ann & Bo", "Old Duet", "Cy"]);
    }

    #[test]
    fn activity_follows_skips_and_the_old_list_format() {
        assert!(!singer_is_active(&json!({"songs": [{"skipped": true}, {"skipped": 1}]})));
        assert!(singer_is_active(&json!({"songs": [{"skipped": true}, ["a", 1, "D"]]})));
        assert!(!singer_is_active(&json!({"skipped": true, "songs": [{}]})));
        assert!(!singer_is_active(&json!({"songs": []})));
    }

    #[test]
    fn a_two_item_old_format_song_has_no_duet_name() {
        let names = singer_list(&json!([{"name": "N", "songs": [["a", 1]]}])).unwrap();
        assert_eq!(names, [json!("N")]);
    }

    #[test]
    fn an_empty_first_song_hides_the_singer_like_python_falsy_check() {
        // `{}` is falsy, so `if not first_active_song: continue` drops the singer although is_singer_active said yes
        let names = singer_list(&json!([{"name": "Z", "songs": [{}]}])).unwrap();
        assert!(names.is_empty());
    }

    #[test]
    fn numbered_text_has_the_three_space_bar_separator() {
        assert_eq!(format_queue_text(&["A".into(), "B".into()], ""), "1. A   |   2. B");
        assert_eq!(format_queue_text(&[], "Hello"), "Hello");
        assert_eq!(format_queue_text(&["A".into()], "  Hello "), "Hello   |   1. A");
        assert_eq!(format_queue_text(&[], ""), "");
    }

    #[test]
    fn custom_message_substitutions_and_tidying() {
        let now = json!(["Ann", "Queen", "Radio Ga Ga"]);
        let list = vec![json!("Bo")];
        assert_eq!(render_custom_message("%m_curSinger | %curSong | %nextSinger", &now, &list).unwrap(), "Current: Ann | Queen \u{2022} Radio Ga Ga | Next: Bo");
        assert_eq!(render_custom_message("%curArtist - %curTitle", &Value::Null, &[]).unwrap(), "");
        assert_eq!(render_custom_message("Tip jar  |  |  %nextSinger", &Value::Null, &[]).unwrap(), "Tip jar");
        assert_eq!(render_custom_message("a  -  - b", &Value::Null, &[]).unwrap(), "a - b");
        assert_eq!(render_custom_message("a \u{2022}\u{2022} b", &Value::Null, &[]).unwrap(), "a \u{2022} b");
        assert_eq!(render_custom_message("", &now, &list).unwrap(), "");
    }

    #[test]
    fn tokens_in_a_substituted_name_are_substituted_again_in_order() {
        let now = json!(["%nextSinger", "", ""]);
        assert_eq!(render_custom_message("%curSinger", &now, &[json!("Bo")]).unwrap(), "Next: Bo");
    }

    #[test]
    fn short_or_missing_now_singing_parts_are_ignored() {
        assert_eq!(render_custom_message("[%curSinger]", &json!(["A", "B"]), &[]).unwrap(), "[]");
    }

    #[test]
    fn a_non_text_message_turns_the_custom_message_off() {
        let v = view(&q(), &json!({"ticker_custom_enabled": true, "ticker_custom_message": 5}), &Value::Null).unwrap();
        assert_eq!(v.message, "");
        assert_eq!(v.text, "1. Ann & Bo   |   2. Old Duet   |   3. Cy");
        let w = view(&q(), &json!({"ticker_custom_enabled": true, "ticker_custom_message": " Welcome "}), &Value::Null).unwrap();
        assert_eq!(w.text, "Welcome   |   1. Ann & Bo   |   2. Old Duet   |   3. Cy");
        let off = view(&q(), &json!({"ticker_custom_enabled": false, "ticker_custom_message": "Welcome"}), &Value::Null).unwrap();
        assert_eq!(off.message, "");
    }

    #[test]
    fn names_that_are_not_text_print_like_python_str() {
        let v = view(&json!([{"name": null, "songs": [{"a": 1}]}, {"name": 7, "songs": [{"a": 1}]}]), &json!({}), &Value::Null).unwrap();
        assert_eq!(v.text, "1. None   |   2. 7");
    }
}
