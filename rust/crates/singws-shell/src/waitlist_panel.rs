//! QObject exposed to QML as `WaitlistPanel`: a READ-ONLY look at the waiting-for-add requests. The app writes `waitlist_snapshot.json` (setting `waitlist_snapshot`) whenever
//! its waitlist changes; this panel only reads it (reloaded on every refresh) and draws the sections with the toolkit-free rules in `singws-uimodel::waitlist`.

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
        #[qproperty(QString, status)]
        type WaitlistPanel = super::WaitlistPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut WaitlistPanel>);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::waitlist;
use std::time::SystemTime;

/// older than this the status says the snapshot may be stale (the app is closed, or the setting was turned off)
const STALE_SECS: u64 = 120;

pub struct WaitlistPanelRust {
    sections_json: QString,
    status: QString,
    dir: String,
}

impl Default for WaitlistPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        WaitlistPanelRust { sections_json: QString::from("[]"), status: QString::from(""), dir }
    }
}

/// (sections as JSON, status line) for the snapshot in `dir`; `now` is the clock the file's age is measured against
pub fn load(dir: &str, now: SystemTime) -> (Value, String) {
    let path = format!("{dir}/waitlist_snapshot.json");
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return (json!([]), "No waitlist snapshot yet. Turn on \"waitlist_snapshot\" in the app's settings; it then writes the file whenever the waitlist changes.".into());
        }
        Err(e) => return (json!([]), format!("Waitlist unavailable: {e}")),
    };
    let age = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| now.duration_since(t).ok()).map(|d| d.as_secs());
    let snapshot: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return (json!([]), format!("Waitlist unavailable: the snapshot is not valid JSON ({e})")),
    };
    match waitlist::sections_from_snapshot(&snapshot) {
        Ok(sections) => {
            let rows: usize = sections.iter().map(|s| s.rows.len()).sum();
            let json_sections: Vec<Value> = sections.iter().map(|s| json!({"key": s.key, "title": s.title, "helper": s.helper, "rows": s.rows.iter().map(waitlist::row_json).collect::<Vec<_>>()})).collect();
            let mut status = if rows == 0 { "Nobody is waiting".to_string() } else { format!("{rows} request{}", if rows == 1 { "" } else { "s" }) };
            match age {
                Some(a) if a > STALE_SECS => status.push_str(&format!("   •   snapshot is {}m old, the app may be closed", a / 60)),
                Some(a) => status.push_str(&format!("   •   updated {a}s ago")),
                None => {}
            }
            (Value::Array(json_sections), status)
        }
        Err(e) => (json!([]), format!("Waitlist unavailable: {e}")),
    }
}

impl qobject::WaitlistPanel {
    fn refresh(mut self: Pin<&mut Self>) {
        let dir = self.rust().dir.clone();
        let (sections, status) = load(&dir, SystemTime::now());
        self.as_mut().set_sections_json(QString::from(sections.to_string().as_str()));
        self.as_mut().set_status(QString::from(status.as_str()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn dir(name: &str) -> String {
        let d = std::env::temp_dir().join(format!("singws-waitlist-panel-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.to_string_lossy().to_string()
    }

    const SNAP: &str = r#"{"version":1,"pending_acceptance":null,"waiting":{"5":{"request_id":5,"singer":"Ann","artist":"Queen","title":"Radio","request_time":1792000000},
        "6":{"request_id":6,"singer":"Bo","artist":"A","title":"B"}},"show_removed":false,"removed":{},"local_ids":[6],"accepted_ids":[],"pending_accepted_ids":[],"first_time_names":["Ann"]}"#;

    #[test]
    fn the_waitlist_snapshot_becomes_sections_and_a_status() {
        let d = dir("a");
        std::fs::write(format!("{d}/waitlist_snapshot.json"), SNAP).unwrap();
        let (sections, status) = load(&d, SystemTime::now());
        let secs = sections.as_array().unwrap();
        assert_eq!(secs.len(), 1);
        assert_eq!(secs[0]["key"], "waitlist");
        assert_eq!(secs[0]["rows"].as_array().unwrap().len(), 1, "the queued request (6) is not waiting any more");
        assert_eq!(secs[0]["rows"][0]["singer"], "Ann");
        assert!(status.starts_with("1 request   •   updated "), "{status}");
    }

    #[test]
    fn an_old_snapshot_is_called_stale() {
        let d = dir("b");
        std::fs::write(format!("{d}/waitlist_snapshot.json"), SNAP).unwrap();
        let later = SystemTime::now() + Duration::from_secs(600);
        assert!(load(&d, later).1.contains("10m old"), "{}", load(&d, later).1);
    }

    #[test]
    fn a_missing_or_broken_snapshot_says_so_instead_of_failing() {
        let d = dir("c");
        let (sections, status) = load(&d, SystemTime::now());
        assert_eq!((sections, status.contains("waitlist_snapshot")), (json!([]), true));
        std::fs::write(format!("{d}/waitlist_snapshot.json"), "{nope").unwrap();
        assert!(load(&d, SystemTime::now()).1.contains("not valid JSON"));
        std::fs::write(format!("{d}/waitlist_snapshot.json"), "[1]").unwrap();
        assert!(load(&d, SystemTime::now()).1.starts_with("Waitlist unavailable"));
        std::fs::write(format!("{d}/waitlist_snapshot.json"), r#"{"waiting":{},"removed":{}}"#).unwrap();
        assert!(load(&d, SystemTime::now()).1.starts_with("Nobody is waiting"));
    }
}
