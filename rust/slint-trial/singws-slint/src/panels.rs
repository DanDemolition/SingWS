//! Toolkit-free loaders for the Ticker and Waitlist tabs: read the same snapshot files the Qt shell reads (`queue.json`, `settings.json`, `waitlist_snapshot.json`)
//! and hand the text to the UI. Every RULE lives in `singws-uimodel` (`ticker`, `waitlist`); what is here is the file reading and the status wording, which is the same
//! as `singws-shell`'s `ticker_panel.rs` / `waitlist_panel.rs`. When Slint is adopted these two functions should move into `singws-uimodel` and the Qt shell call them too.
use serde_json::Value;
use singws_uimodel::{status, ticker, waitlist};
use std::time::SystemTime;

pub const STALE_SECS: u64 = 120;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct TickerView { pub text: String, pub message: String, pub singers: Vec<String>, pub status: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct WaitRow { pub text: String, pub kind: String, pub first_time: bool }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct WaitSection { pub key: String, pub title: String, pub helper: String, pub rows: Vec<WaitRow> }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct WaitView { pub sections: Vec<WaitSection>, pub status: String }

pub fn home() -> String {
    std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default()
}

fn read_json(path: &str, missing: Value) -> Result<Value, String> {
    match std::fs::read_to_string(path) {
        Ok(t) => serde_json::from_str::<Value>(&t).map_err(|e| format!("{path}: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(missing),
        Err(e) => Err(format!("{path}: {e}")),
    }
}

pub fn ticker_view(dir: &str) -> TickerView {
    let load = || -> Result<_, String> {
        let queue = read_json(&format!("{dir}/queue.json"), Value::Array(vec![]))?;
        let settings = read_json(&format!("{dir}/settings.json"), Value::Object(Default::default()))?;
        ticker::view(&queue, &settings, &Value::Null)
    };
    match load() {
        Ok(v) => {
            let n = v.singers.len();
            let status = if n == 0 && v.message.is_empty() { "Nothing to show: nobody is waiting in the queue".to_string() } else { format!("{n} singer{} in the ticker   •   what the audience sees", if n == 1 { "" } else { "s" }) };
            TickerView { text: v.text, message: v.message, singers: v.singers, status }
        }
        Err(e) => TickerView { status: format!("Ticker unavailable: {e}"), ..Default::default() },
    }
}

pub fn waitlist_view(dir: &str, now: SystemTime) -> WaitView {
    let path = format!("{dir}/waitlist_snapshot.json");
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return WaitView { status: "No waitlist snapshot yet. Turn on \"waitlist_snapshot\" in the app's settings; it then writes the file whenever the waitlist changes.".into(), ..Default::default() },
        Err(e) => return WaitView { status: format!("Waitlist unavailable: {e}"), ..Default::default() },
    };
    let age = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| now.duration_since(t).ok()).map(|d| d.as_secs());
    let snapshot: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return WaitView { status: format!("Waitlist unavailable: the snapshot is not valid JSON ({e})"), ..Default::default() },
    };
    match waitlist::sections_from_snapshot(&snapshot) {
        Ok(secs) => {
            let n: usize = secs.iter().map(|s| s.rows.len()).sum();
            let mut status = if n == 0 { "Nobody is waiting".to_string() } else { format!("{n} request{}", if n == 1 { "" } else { "s" }) };
            match age {
                Some(a) if a > STALE_SECS => status.push_str(&format!("   •   snapshot is {}m old, the app may be closed", a / 60)),
                Some(a) => status.push_str(&format!("   •   updated {a}s ago")),
                None => {}
            }
            let sections = secs.iter().map(|s| WaitSection {
                key: s.key.into(), title: s.title.into(), helper: s.helper.unwrap_or("").into(),
                rows: s.rows.iter().map(|r| WaitRow { text: r.text.clone(), kind: r.status_kind.clone(), first_time: r.first_time_singer }).collect(),
            }).collect();
            WaitView { sections, status }
        }
        Err(e) => WaitView { status: format!("Waitlist unavailable: {e}"), ..Default::default() },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn dir(n: &str) -> String { let d = std::env::temp_dir().join(format!("singws-slint-{}-{n}", std::process::id())); std::fs::create_dir_all(&d).unwrap(); d.to_string_lossy().into() }
    #[test]
    fn same_text_as_the_qt_shell_for_the_same_files() {
        let d = dir("t");
        std::fs::write(format!("{d}/queue.json"), r#"[{"name":"Ann","songs":[{"title":"x"}]},{"name":"Skip","skipped":true,"songs":[{}]},{"name":"Bo","songs":[{"title":"y"}]}]"#).unwrap();
        std::fs::write(format!("{d}/settings.json"), r#"{"ticker_custom_enabled":true,"ticker_custom_message":"Tip your host"}"#).unwrap();
        let v = ticker_view(&d);
        assert_eq!(v.text, "Tip your host   |   1. Ann   |   2. Bo");
        assert_eq!(v.singers, vec!["Ann", "Bo"]);
    }
    #[test]
    fn status_from_the_fixture_and_html_is_stripped() {
        assert_eq!(strip_tags("<span style='color:#fff'>SINGERS</span>&nbsp;3"), "SINGERS 3");
        let d = dir("s");
        std::fs::write(format!("{d}/status_snapshot.json"), include_str!("../../fixtures/status_snapshot.json")).unwrap();
        let v = status_view(&d, 1_792_000_020);
        assert!(v.has && v.note == "updated 20s ago" && v.location == "Joe's" && v.checks.len() == 7, "{v:?}");
    }
    #[test]
    fn missing_and_broken_files() {
        let d = dir("m");
        assert_eq!(ticker_view(&d).text, "");
        std::fs::write(format!("{d}/queue.json"), "{no").unwrap();
        assert!(ticker_view(&d).status.starts_with("Ticker unavailable"));
        assert!(waitlist_view(&d, SystemTime::now()).status.starts_with("No waitlist snapshot"));
    }
}

// ---- Status tab ------------------------------------------------------------------------------------------------------------------------
pub const STATUS_STALE_SECS: i64 = 300;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct StatusChip { pub text: String, pub tone: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct StatusViewData {
    pub note: String, pub stale: bool, pub has: bool, pub chips: Vec<StatusChip>, pub status_text: String, pub warn_dot: bool, pub location: String,
    pub tooltip: String, pub eta: String, pub summary: String, pub report_title: String, pub checks: Vec<(bool, String)>,
}

/// The app's ETA line is a little HTML (coloured spans); Slint's `Text` has no rich text, so the tags are dropped and the words kept (a known gap, see the design doc).
pub fn strip_tags(html: &str) -> String {
    let mut out = String::new();
    let mut in_tag = false;
    for c in html.chars() {
        match c { '<' => in_tag = true, '>' => in_tag = false, _ if !in_tag => out.push(c), _ => {} }
    }
    out.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
}

pub fn status_view(dir: &str, now: i64) -> StatusViewData {
    let path = format!("{dir}/status_snapshot.json");
    let note = |t: String| StatusViewData { note: t, ..Default::default() };
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return note("No status snapshot yet. Turn on \"status_snapshot\" in the app's settings; it then writes the file whenever the header refreshes.".into()),
        Err(e) => return note(format!("Status unavailable: {e}")),
    };
    let snapshot: Value = match serde_json::from_str(&text) { Ok(v) => v, Err(e) => return note(format!("Status unavailable: the snapshot is not valid JSON ({e})")) };
    match status::view(&snapshot) {
        Ok(v) => {
            let age = (now - v.taken_at).max(0);
            StatusViewData {
                note: if age > STATUS_STALE_SECS { format!("snapshot is {}m old, the app may be closed", age / 60) } else { format!("updated {age}s ago") },
                stale: age > STATUS_STALE_SECS, has: true,
                chips: v.chips.iter().map(|c| StatusChip { text: c.text.clone(), tone: c.tone.to_string() }).collect(),
                status_text: v.status.text.clone(), warn_dot: v.status.dot_token == "warning", location: v.status.location.clone(), tooltip: v.status.tooltip.clone(),
                eta: strip_tags(&v.eta.html), summary: v.summary.clone(), report_title: v.report.title.clone(),
                checks: v.report.checks.iter().map(|c| (c.ok, format!("{}   {}", c.label, c.detail))).collect(),
            }
        }
        Err(e) => note(format!("Status unavailable: {e}")),
    }
}
