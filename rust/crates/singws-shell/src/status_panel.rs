//! QObject exposed to QML as `StatusPanel`: a READ-ONLY look at the Show tab's header (the four pills, the status line, the SINGERS / ETA line, the queue summary) and the
//! pre-show diagnostics report. The app writes `status_snapshot.json` (setting `status_snapshot`) whenever its header refreshes; this panel only reads it (reloaded on every
//! refresh) and draws it with the toolkit-free rules in `singws-uimodel::status`, which reuse the header and diagnostics view models proven equal to the app.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, view_json, cxx_name = "viewJson")]
        #[qproperty(QString, status)]
        type StatusPanel = super::StatusPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut StatusPanel>);

        /// test hook (`--dump-status FILE`): write the view the page is showing
        #[qinvokable]
        #[cxx_name = "dumpView"]
        fn dump_view(self: &StatusPanel, path: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::status;
use std::time::{SystemTime, UNIX_EPOCH};

/// older than this the status says the snapshot may be stale (the app is closed, or the setting was turned off). The app writes the file when its header refreshes,
/// which on an idle night can be minutes apart, so the limit is generous.
const STALE_SECS: i64 = 300;

pub struct StatusPanelRust {
    view_json: QString,
    status: QString,
    dir: String,
}

impl Default for StatusPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        StatusPanelRust { view_json: QString::from("{}"), status: QString::from(""), dir }
    }
}

/// (view as JSON, status line) for the snapshot in `dir`; `now` is the clock (Unix seconds) the snapshot's own `taken_at` is measured against
pub fn load(dir: &str, now: i64) -> (Value, String) {
    let path = format!("{dir}/status_snapshot.json");
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return (json!({}), "No status snapshot yet. Turn on \"status_snapshot\" in the app's settings; it then writes the file whenever the header refreshes.".into());
        }
        Err(e) => return (json!({}), format!("Status unavailable: {e}")),
    };
    let snapshot: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return (json!({}), format!("Status unavailable: the snapshot is not valid JSON ({e})")),
    };
    match status::view(&snapshot) {
        Ok(v) => {
            let age = (now - v.taken_at).max(0);
            let status = if age > STALE_SECS { format!("snapshot is {}m old, the app may be closed", age / 60) } else { format!("updated {age}s ago") };
            let view = json!({
                "chips": v.chips.iter().map(|c| json!({"text": c.text, "tone": c.tone})).collect::<Vec<_>>(),
                "statusText": v.status.text, "dotToken": v.status.dot_token, "tooltip": v.status.tooltip, "location": v.status.location,
                "etaHtml": v.eta.html, "summary": v.summary, "stale": age > STALE_SECS,
                "hero": v.hero.as_ref().map(|h| json!({"kicker": h.kicker, "singer": h.singer, "subtitle": h.subtitle, "meta": h.meta, "format": h.format_text, "remaining": h.remaining})),
                "reportTitle": v.report.title, "allPassed": v.report.all_passed,
                "checks": v.report.checks.iter().map(|c| json!({"ok": c.ok, "label": c.label, "detail": c.detail})).collect::<Vec<_>>(),
            });
            (view, status)
        }
        Err(e) => (json!({}), format!("Status unavailable: {e}")),
    }
}

fn unix_now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

impl qobject::StatusPanel {
    fn dump_view(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.view_json().to_string());
    }

    fn refresh(mut self: Pin<&mut Self>) {
        let dir = self.rust().dir.clone();
        let (view, status) = load(&dir, unix_now());
        self.as_mut().set_view_json(QString::from(view.to_string().as_str()));
        self.as_mut().set_status(QString::from(status.as_str()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> String {
        let d = std::env::temp_dir().join(format!("singws-status-panel-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.to_string_lossy().to_string()
    }

    fn snapshot() -> String {
        json!({
            "version": 1, "taken_at": 1_000_000, "settings": {"venue_name": "Joe's"}, "connection": "Connected", "network_configured": true, "last": {}, "attention": null, "queue_rev": 3,
            "queue": [{"name": "Ann", "rotation_marker": false}], "total_secs": 200, "locked": false, "looping": false,
            "diagnostics": {"local_name": "Speakers", "screen_count": 2, "karafun_path": null, "output_host": true, "preview_host": true, "show_ticker": true, "qr_url": "https://x/", "bg_files": []}
        })
        .to_string()
    }

    #[test]
    fn a_snapshot_becomes_pills_status_eta_and_seven_checks() {
        let d = dir("a");
        std::fs::write(format!("{d}/status_snapshot.json"), snapshot()).unwrap();
        let (v, status) = load(&d, 1_000_020);
        assert_eq!(status, "updated 20s ago");
        assert_eq!(v["chips"][0]["text"], "Requests open");
        assert_eq!(v["statusText"], "Requests Open \u{2022} Waitlist Off \u{2022} Connected");
        assert_eq!(v["location"], "Joe's");
        assert_eq!(v["checks"].as_array().unwrap().len(), 7);
        assert_eq!(v["reportTitle"], "6 of 7 checks passed");
        assert_eq!(v["checks"][2]["detail"], "Application not found");
        assert_eq!(v["summary"], "1 singer \u{b7} 3 min");
    }

    #[test]
    fn the_hero_card_travels_to_the_page_and_is_null_for_an_older_snapshot() {
        let d = dir("d");
        std::fs::write(format!("{d}/status_snapshot.json"), snapshot()).unwrap();
        assert!(load(&d, 1_000_020).0["hero"].is_null());
        let mut s: Value = serde_json::from_str(&snapshot()).unwrap();
        s["hero"] = json!({"kicker": "NOW SINGING", "singer": "Ann", "subtitle": "Radio Ga Ga \u{b7} Queen", "meta": "SC8671", "format": "ZIP", "remaining": "03:21"});
        std::fs::write(format!("{d}/status_snapshot.json"), s.to_string()).unwrap();
        let h = load(&d, 1_000_020).0["hero"].clone();
        assert_eq!((h["singer"].as_str(), h["remaining"].as_str(), h["format"].as_str()), (Some("Ann"), Some("-03:21"), Some("ZIP")));
    }

    #[test]
    fn an_old_snapshot_is_called_old_and_a_future_one_counts_as_just_written() {
        let d = dir("b");
        std::fs::write(format!("{d}/status_snapshot.json"), snapshot()).unwrap();
        let (v, status) = load(&d, 1_000_000 + 301);
        assert_eq!((status.as_str(), v["stale"].as_bool()), ("snapshot is 5m old, the app may be closed", Some(true)));
        let (v, status) = load(&d, 1_000_000 + 300);
        assert_eq!((status.as_str(), v["stale"].as_bool()), ("updated 300s ago", Some(false)));
        assert_eq!(load(&d, 5).1, "updated 0s ago");
    }

    #[test]
    fn a_missing_or_broken_snapshot_says_so_instead_of_drawing_guesses() {
        let d = dir("c");
        let (v, status) = load(&d, 0);
        assert!(v.as_object().unwrap().is_empty() && status.contains("status_snapshot"));
        std::fs::write(format!("{d}/status_snapshot.json"), "{not json").unwrap();
        assert!(load(&d, 0).1.starts_with("Status unavailable: the snapshot is not valid JSON"));
        std::fs::write(format!("{d}/status_snapshot.json"), r#"{"version":2}"#).unwrap();
        assert!(load(&d, 0).1.starts_with("Status unavailable: decline"));
    }
}
