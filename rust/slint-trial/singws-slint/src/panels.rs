//! Toolkit-free loaders for the Ticker and Waitlist tabs: read the same snapshot files the Qt shell reads (`queue.json`, `settings.json`, `waitlist_snapshot.json`)
//! and hand the text to the UI. Every RULE lives in `singws-uimodel` (`ticker`, `waitlist`); what is here is the file reading and the status wording, which is the same
//! as `singws-shell`'s `ticker_panel.rs` / `waitlist_panel.rs`. When Slint is adopted these two functions should move into `singws-uimodel` and the Qt shell call them too.
use serde_json::Value;
use singws_uimodel::{ticker, waitlist};
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
    fn missing_and_broken_files() {
        let d = dir("m");
        assert_eq!(ticker_view(&d).text, "");
        std::fs::write(format!("{d}/queue.json"), "{no").unwrap();
        assert!(ticker_view(&d).status.starts_with("Ticker unavailable"));
        assert!(waitlist_view(&d, SystemTime::now()).status.starts_with("No waitlist snapshot"));
    }
}
