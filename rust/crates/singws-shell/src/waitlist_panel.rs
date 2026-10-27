//! QObject exposed to QML as `WaitlistPanel`: the READ-ONLY "Waiting for add" page and the audience ticker strip. All rules live in `singws-uimodel::waitlist` and
//! `::ticker`. Sources, all read-only and re-read on every refresh:
//!  * `waitlist_removed_tonight.json` (the app's own file) -> the "Removed Tonight" section, with the app's evening/venue gate;
//!  * `waitlist_state.json` (optional LIVE feed: version 1 + the writer's pid, which must still be running) -> the Waitlist and Pending Acceptance sections. The app does
//!    not write this file yet (the live waitlist only exists in its memory), so without it the page says so instead of guessing;
//!  * `queue.json`, `settings.json` and `live_state.json` -> the ticker text the audience would see.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, sections_json, cxx_name = "sectionsJson")]
        #[qproperty(QString, count_label, cxx_name = "countLabel")]
        #[qproperty(QString, ticker_text, cxx_name = "tickerText")]
        #[qproperty(QString, status)]
        type WaitlistPanel = super::WaitlistPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut WaitlistPanel>, show_removed: bool);

        /// test hook (`--dump-waitlist FILE`): write the sections and the ticker text the page is showing
        #[qinvokable]
        #[cxx_name = "dumpView"]
        fn dump_view(self: &WaitlistPanel, path: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Map, Value, json};
use singws_uimodel::{rotation, ticker, waitlist};
use std::collections::HashSet;

pub struct WaitlistPanelRust {
    sections_json: QString,
    count_label: QString,
    ticker_text: QString,
    status: QString,
    dir: String,
}

impl Default for WaitlistPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        WaitlistPanelRust { sections_json: QString::from("[]"), count_label: QString::from("0 WAITING"), ticker_text: QString::from(""), status: QString::from(""), dir }
    }
}

fn read_json(path: &str) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

fn now_epoch() -> i64 {
    std::env::var("SINGWS_NOW").ok().and_then(|v| v.parse().ok()).unwrap_or_else(|| std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0))
}

/// The live feed, when there is one that can be trusted: `SINGWS_WAITLIST_FEED` (a test hook, no liveness check) or `waitlist_state.json` written by a running app.
fn live_feed(dir: &str) -> Option<Value> {
    if let Ok(p) = std::env::var("SINGWS_WAITLIST_FEED") {
        return read_json(&p);
    }
    let v = read_json(&format!("{dir}/waitlist_state.json"))?;
    let alive = v.get("version").and_then(Value::as_i64) == Some(1) && v.get("pid").and_then(Value::as_i64).is_some_and(rotation::process_alive);
    alive.then_some(v)
}

fn ids(v: Option<&Value>) -> HashSet<i64> {
    v.and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default()
}

/// Everything the page shows, as one JSON value: {sections, ticker, status, count_label}
pub fn build_view(dir: &str, show_removed: bool, now: i64) -> Value {
    let settings = read_json(&format!("{dir}/settings.json")).unwrap_or(Value::Null);
    let feed = live_feed(dir);
    let removed_file = read_json(&format!("{dir}/waitlist_removed_tonight.json")).unwrap_or(Value::Null);
    let removed_rows = waitlist::removed_tonight(&removed_file, &settings, now).unwrap_or_default();
    let mut removed = Map::new();
    for (id, req) in removed_rows {
        removed.insert(id.to_string(), req);
    }
    let null = Value::Null;
    let f = feed.as_ref().unwrap_or(&null);
    let names: HashSet<String> = f.get("first_time_names").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default();
    let (accepted, local) = (ids(f.get("accepted_ids")), ids(f.get("local_ids")));
    let ctx = waitlist::Context { first_time_names: &names, accepted_ids: &accepted, local_ids: &local };
    let pending = f.get("pending_acceptance").and_then(Value::as_array);
    let secs = waitlist::sections(pending.map(|v| v.as_slice()), f.get("waiting").unwrap_or(&null), show_removed, &Value::Object(removed.clone()), &ctx);
    // the live feed carries the pending adds already merged and filtered by the app
    let secs = secs.and_then(|mut s| {
        if let Some(reqs) = f.get("pending_acceptance_requests").and_then(Value::as_array) {
            if let Some(sec) = waitlist::pending_section(reqs, &ctx)? {
                s.insert(0, sec);
            }
        }
        Ok(s)
    });
    let (sections, mut status) = match secs {
        Ok(s) => (s, String::new()),
        Err(e) => (Vec::new(), format!("The waitlist could not be shown: {e}")),
    };
    let waiting_n: usize = sections.iter().filter(|s| s.key != "removed").map(|s| s.rows.len()).sum();
    if status.is_empty() {
        status = match (feed.is_some(), removed.len()) {
            (true, _) => "Live from the app".into(),
            (false, 0) => "The live waitlist is not published to the shell yet (it only exists inside the app). Nothing was removed tonight.".into(),
            (false, n) => format!("The live waitlist is not published to the shell yet. {n} request{} removed tonight (from the app's file).", if n == 1 { "" } else { "s" }),
        };
    }
    // the ticker the audience sees: the app's saved queue, its ticker settings, and who is on stage now
    let queue = rotation::load_queue(std::path::Path::new(&format!("{dir}/queue.json"))).ok().map(Value::Array).unwrap_or(Value::Null);
    let live = rotation::load_live_state(std::path::Path::new(&format!("{dir}/live_state.json")));
    let now_parts = match &live {
        Some(l) if l.playing || !l.singer.is_empty() => {
            let artist = queue
                .as_array()
                .and_then(|q| q.iter().find(|s| s.get("id").and_then(Value::as_str) == Some(l.singer_id.as_str()) || s.get("name").and_then(Value::as_str) == Some(l.singer.as_str())))
                .and_then(|s| s.get("songs").and_then(Value::as_array))
                .and_then(|songs| songs.iter().find(|x| x.get("title").and_then(Value::as_str).or_else(|| x.get("display_name").and_then(Value::as_str)) == Some(l.title.as_str())))
                .and_then(|x| x.get("artist").and_then(Value::as_str))
                .unwrap_or("")
                .to_string();
            json!([l.singer, artist, l.title])
        }
        _ => Value::Null,
    };
    let ticker_text = ticker::view(&queue, &settings, &now_parts).map(|t| t.text).unwrap_or_else(|e| format!("(ticker unavailable: {e})"));
    let sections_json: Vec<Value> =
        sections.iter().map(|s| json!({"key": s.key, "title": s.title, "helper": s.helper, "rows": s.rows.iter().map(waitlist::row_json).collect::<Vec<_>>()})).collect();
    json!({"sections": sections_json, "ticker": ticker_text, "status": status, "count_label": format!("{waiting_n} WAITING")})
}

impl qobject::WaitlistPanel {
    fn dump_view(&self, path: &QString) {
        let v = json!({"sections": serde_json::from_str::<Value>(&self.sections_json().to_string()).unwrap_or(Value::Null), "ticker": self.ticker_text().to_string(), "status": self.status().to_string()});
        let _ = std::fs::write(path.to_string(), v.to_string());
    }

    fn refresh(mut self: Pin<&mut Self>, show_removed: bool) {
        let dir = self.rust().dir.clone();
        let v = build_view(&dir, show_removed, now_epoch());
        let get = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        self.as_mut().set_sections_json(QString::from(v.get("sections").cloned().unwrap_or(json!([])).to_string().as_str()));
        self.as_mut().set_count_label(QString::from(get("count_label").as_str()));
        self.as_mut().set_ticker_text(QString::from(get("ticker").as_str()));
        self.as_mut().set_status(QString::from(get("status").as_str()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn home(files: &[(&str, &str)]) -> String {
        static N: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let d = std::env::temp_dir().join(format!("singws_wlpanel_{}_{}", std::process::id(), N.fetch_add(1, std::sync::atomic::Ordering::SeqCst)));
        std::fs::create_dir_all(&d).unwrap();
        for (n, c) in files {
            std::fs::write(d.join(n), c).unwrap();
        }
        d.to_string_lossy().into_owned()
    }

    #[test]
    fn without_any_files_the_page_says_nothing_is_published() {
        let d = home(&[]);
        let v = build_view(&d, true, 1_792_000_000);
        assert_eq!(v["sections"], json!([]));
        assert_eq!(v["ticker"], "");
        assert!(v["status"].as_str().unwrap().contains("not published"));
    }

    #[test]
    fn the_removed_tonight_file_fills_its_section_and_the_queue_fills_the_ticker() {
        let now = 1_792_000_000;
        let ev = singws_uimodel::waitlist::evening_key(now).unwrap();
        let removed = format!(r#"{{"version":1,"evening":"{ev}","tenant":"wsk","requests":{{"5":{{"request_id":5,"singer":"Ann","title":"T","artist":"A","removed_at":100}}}}}}"#);
        let d = home(&[
            ("settings.json", r#"{"user":"wsk","ticker_custom_enabled":true,"ticker_custom_message":"Welcome | %nextSinger"}"#),
            ("waitlist_removed_tonight.json", &removed),
            ("queue.json", r#"[{"name":"Bo","songs":[{"title":"x","artist":"y"}]},{"name":"Cy","songs":[{"title":"z"}]}]"#),
        ]);
        let shown = build_view(&d, true, now);
        assert_eq!(shown["sections"][0]["key"], "removed");
        assert_eq!(shown["sections"][0]["rows"][0]["singer"], "Ann");
        assert_eq!(shown["ticker"], "Welcome | Next: Bo   |   1. Bo   |   2. Cy");
        assert_eq!(shown["count_label"], "0 WAITING");
        let hidden = build_view(&d, false, now);
        assert_eq!(hidden["sections"], json!([]));
    }

    #[test]
    fn a_trusted_live_feed_adds_the_waitlist_section() {
        let d = home(&[]);
        let feed = format!("{d}/feed.json");
        std::fs::write(&feed, r#"{"version":1,"waiting":{"7":{"request_id":7,"singer":"Dee","artist":"Q","title":"R","request_time":"2026-10-26T20:05:00"}},"first_time_names":["Dee"],"local_ids":[],"accepted_ids":[]}"#).unwrap();
        // SAFETY: tests in this module run on one thread each, and only this one sets the variable
        unsafe { std::env::set_var("SINGWS_WAITLIST_FEED", &feed) };
        let v = build_view(&d, false, 1_792_000_000);
        unsafe { std::env::remove_var("SINGWS_WAITLIST_FEED") };
        assert_eq!(v["sections"][0]["key"], "waitlist");
        assert_eq!(v["sections"][0]["rows"][0]["first_time_singer"], true);
        assert_eq!(v["count_label"], "1 WAITING");
        assert_eq!(v["status"], "Live from the app");
    }
}
