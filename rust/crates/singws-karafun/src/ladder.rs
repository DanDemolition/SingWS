//! The search LADDER of `KaraokeApp._automate_karafun_search_and_play` (0.2.18.1.py, the 8 statements from `found = ""` to the final
//! `raise RuntimeError(last_error or "No acceptable KaraFun match")`), as a pure function.
//!
//! The caller supplies what the real code gets from KaraFun: one `Answer` for each script it asks to run. The function returns what the app
//! would decide and everything observable: the log lines, the scripts requested (in order), the number of sleeps and, when the app would
//! raise, the error text. tools/karafun_ladder_parity.py runs the REAL Python statements against the same scripted answers.
use crate::pure::{accepted_kinds, casefold};
use crate::py_strip;

#[derive(Clone, Debug, PartialEq)]
pub struct Answer {
    pub ok: bool,
    pub text: String,
    pub error: String,
}

#[derive(Debug, Default, PartialEq)]
pub struct Outcome {
    pub found: String,
    pub selected_query: String,
    pub last_error: String,
    pub raised: Option<String>,
    pub log: Vec<String>,
    pub requests: Vec<String>,
    pub sleeps: usize,
    /// a value this port refuses to guess (a character whose Python repr it does not model)
    pub declined: Option<String>,
}

/// Python `repr()` of a str for the characters this port models (ASCII, Latin-1); None for anything else.
pub fn py_repr(s: &str) -> Option<String> {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x7f => out.push(c),
            c if (0xa1..=0xff).contains(&(c as u32)) && c as u32 != 0xad => out.push(c),
            c if (0x80..=0xa0).contains(&(c as u32)) || c as u32 == 0xad => out.push_str(&format!("\\x{:02x}", c as u32)),
            _ => return None,
        }
    }
    out.push(quote);
    Some(out)
}

fn head(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

pub fn is_apple_events_error(message: &str) -> bool {
    let text = message.to_lowercase();
    text.contains("-1743") || text.contains("not authorized to send apple events") || (text.contains("apple events") && text.contains("not permitted"))
}

pub fn is_accessibility_error(message: &str) -> bool {
    let text = message.to_lowercase();
    text.contains("not allowed assistive access") || (text.contains("accessibility") && text.contains("not allowed")) || text.contains("-25211")
}

pub fn is_ui_not_ready_error(message: &str) -> bool {
    let text = message.to_lowercase();
    if text.is_empty() || text.contains("-1743") || text.contains("-25211") {
        return false;
    }
    text.contains("-1719") || text.contains("invalid index") || text.contains("can\u{2019}t get") || text.contains("can't get") || text.contains("is not defined")
}

/// What the app does with the answers. `runner` is called once per script the app would run, with a label ("search:<query>:<ui_retry>" or "diag").
pub fn run(queries: &[String], safe_title: &str, safe_artist: &str, runner: &mut dyn FnMut(&str) -> Answer) -> Outcome {
    let mut o = Outcome::default();
    macro_rules! rep {
        ($s:expr) => {
            match py_repr($s) {
                Some(r) => r,
                None => {
                    o.declined = Some("unmodelled character in a repr".to_string());
                    return o;
                }
            }
        };
    }
    let artist_match = py_strip(safe_artist).to_string();
    let mut diag_logged = false;
    for (idx, query) in queries.iter().enumerate() {
        let attempt = idx + 1;
        if query.is_empty() {
            continue;
        }
        let accepted = accepted_kinds(&artist_match, query);
        let mut ok = false;
        let mut candidate = String::new();
        for ui_retry in 0..3usize {
            let mut line = format!("[KARAFUN-AUTO] search attempt={} query={}", attempt, rep!(query));
            if ui_retry > 0 {
                line.push_str(&format!(" ui_retry={}", ui_retry + 1));
            }
            o.log.push(line);
            o.requests.push(format!("search:{}:{}:{}:{}", query, ui_retry, safe_title, artist_match));
            let a = runner("search");
            ok = a.ok;
            candidate = a.text.clone();
            if ok {
                break;
            }
            o.last_error = if !a.error.is_empty() { a.error.clone() } else if !a.text.is_empty() { a.text.clone() } else { "KaraFun search automation failed".to_string() };
            let shown = rep!(&head(&o.last_error, 180));
            o.log.push(format!("[KARAFUN-AUTO] search attempt={} failed error={}", attempt, shown));
            if is_apple_events_error(&o.last_error) || is_accessibility_error(&o.last_error) {
                o.raised = Some(o.last_error.clone());
                return o;
            }
            if is_ui_not_ready_error(&o.last_error) && ui_retry < 2 {
                o.log.push(format!("[KARAFUN-AUTO] search attempt={} KaraFun UI not ready; retrying same query", attempt));
                o.sleeps += 1;
                continue;
            }
            break;
        }
        if !ok {
            continue;
        }
        let parts: Vec<&str> = candidate.split('|').collect();
        let kind = parts.first().copied().unwrap_or("");
        if parts.len() >= 3 && accepted.contains(&kind) {
            o.found = candidate.clone();
            o.selected_query = query.clone();
            let shown = rep!(&o.found);
            o.log.push(format!("[KARAFUN-AUTO] search attempt={} result={}", attempt, shown));
            break;
        }
        if parts.len() >= 3 && ["FOUND", "TITLE_ONLY", "FIRST"].contains(&kind) {
            o.last_error = format!("KaraFun matched a {} row for {} without confirming artist {}", kind, rep!(query), rep!(&artist_match));
            o.log.push(format!("[KARAFUN-AUTO] search attempt={} rejected kind={} artist_unconfirmed=1", attempt, kind));
            continue;
        }
        o.last_error = parts.last().copied().unwrap_or("KaraFun search failed").to_string();
        let shown = rep!(&head(&candidate, 180));
        o.log.push(format!("[KARAFUN-AUTO] search attempt={} no_match result={}", attempt, shown));
        if !diag_logged {
            diag_logged = true;
            o.requests.push(format!("diag:{}", safe_title));
            let d = runner("diag");
            let text = if d.ok { &d.text } else { &d.error };
            let shown = rep!(&head(text, 900));
            o.log.push(format!("[KARAFUN-AUTO] no_match diagnosis: {}", shown));
        }
    }
    let parts: Vec<&str> = o.found.split('|').collect();
    if parts.len() < 3 || !["FOUND", "TITLE_ONLY", "FIRST"].contains(&parts[0]) {
        o.raised = Some(if o.last_error.is_empty() { "No acceptable KaraFun match".to_string() } else { o.last_error.clone() });
    }
    let _ = casefold; // (accepted_kinds applies casefold; imported for the doc link)
    o
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(ok: bool, text: &str, error: &str) -> Answer {
        Answer { ok, text: text.into(), error: error.into() }
    }

    fn go(queries: &[&str], artist: &str, answers: Vec<Answer>) -> Outcome {
        let qs: Vec<String> = queries.iter().map(|s| s.to_string()).collect();
        let mut it = answers.into_iter();
        run(&qs, "Memory", artist, &mut |_| it.next().unwrap_or(a(false, "", "answers exhausted")))
    }

    #[test]
    fn the_first_accepted_row_wins() {
        let o = go(&["Sugarcult Memory", "Memory"], "Sugarcult", vec![a(true, "FOUND|1|2|3:00", "")]);
        assert_eq!(o.found, "FOUND|1|2|3:00");
        assert_eq!(o.selected_query, "Sugarcult Memory");
        assert_eq!(o.raised, None);
    }

    #[test]
    fn a_title_only_rung_may_only_take_a_confirmed_row() {
        let o = go(&["Memory"], "Sugarcult", vec![a(true, "TITLE_ONLY|1|2|3:00", "")]);
        assert!(o.log.iter().any(|l| l.contains("rejected kind=TITLE_ONLY")));
        assert!(o.raised.unwrap().contains("without confirming artist 'Sugarcult'"));
    }

    #[test]
    fn a_window_that_is_not_ready_is_retried_on_the_same_query() {
        let o = go(&["Memory"], "", vec![a(false, "", "AXError -1719"), a(false, "", "Invalid index"), a(true, "FOUND|1|2|3:00", "")]);
        assert_eq!(o.sleeps, 2);
        assert_eq!(o.found, "FOUND|1|2|3:00");
        assert!(o.log.iter().any(|l| l.ends_with("ui_retry=3")));
    }

    #[test]
    fn a_permission_error_stops_everything() {
        let o = go(&["Memory", "x"], "", vec![a(false, "", "error -1743")]);
        assert_eq!(o.raised.as_deref(), Some("error -1743"));
        assert_eq!(o.requests.len(), 1);
    }

    #[test]
    fn a_no_match_asks_for_one_diagnosis_only() {
        let o = go(&["a", "b"], "", vec![a(true, "ERROR|No usable KaraFun search result", ""), a(true, "front=Claude", ""), a(true, "ERROR|x", "")]);
        assert_eq!(o.requests.iter().filter(|r| r.starts_with("diag:")).count(), 1);
        assert_eq!(o.raised.as_deref(), Some("x"));
    }

    #[test]
    fn repr_matches_python() {
        assert_eq!(py_repr("it's").unwrap(), "\"it's\"");
        assert_eq!(py_repr("a'b\"c").unwrap(), "'a\\'b\"c'");
        assert_eq!(py_repr("tab\there\n").unwrap(), "'tab\\there\\n'");
        assert_eq!(py_repr("\u{3042}"), None);
    }
}
