//! The Status and Diagnostics pages' view model: the header pills, the status line, the SINGERS / ETA line, the queue summary and the pre-show check report, all
//! computed from one `status_snapshot.json` the Python app wrote (setting `status_snapshot`, default off). Nothing is re-derived here: every piece is the existing
//! `header` / `diagnostics` function, fed the values the app itself had at the moment it wrote the file (the clock is the snapshot's `taken_at`, so the ENDS time
//! is the one the app showed). Read-only; the app never reads the file back.

use crate::diagnostics::{self, Report};
use crate::header::{self, Chip, EtaView, Status};
use serde_json::Value;
use singws_dbw::pyval::{R, decline, get};

#[derive(Debug, Clone, PartialEq)]
pub struct StatusView {
    pub taken_at: i64,
    pub chips: Vec<Chip>,
    pub status: Status,
    pub eta: EtaView,
    /// `_refresh_queue_summary`: "5 singers · 27 min · LOCKED" (empty when nobody is queued)
    pub summary: String,
    pub report: Report,
}

pub fn view(snapshot: &Value) -> R<StatusView> {
    if !snapshot.is_object() {
        return decline("the snapshot is not an object");
    }
    if get(snapshot, "version").as_i64() != Some(1) {
        return decline("unknown snapshot version");
    }
    let taken_at = get(snapshot, "taken_at").as_i64().ok_or("decline: taken_at is not an integer")?;
    let settings = get(snapshot, "settings");
    let connection = get(snapshot, "connection");
    let net = get(snapshot, "network_configured").as_bool().unwrap_or(false);
    let locked = get(snapshot, "locked").as_bool().unwrap_or(false);
    let looping = get(snapshot, "looping").as_bool().unwrap_or(false);
    let eta = header::eta_view(get(snapshot, "queue"), get(snapshot, "total_secs"), taken_at, get(settings, "queue_mode"), locked, looping)?;
    let summary_pair = Value::Array(vec![Value::from(eta.singer_count), Value::from(eta.ends.clone())]);
    let chips = header::chips(settings, connection, net, &summary_pair)?;
    let status = header::status(settings, connection, net, get(snapshot, "last"), get(snapshot, "attention"), get(snapshot, "queue_rev"))?;
    let summary = header::queue_summary(eta.singer_count, eta.minutes, locked, looping);
    let report = diagnostics::report(&diagnostics_facts(snapshot))?;
    Ok(StatusView { taken_at, chips, status, eta, summary, report })
}

/// The `Facts` of the diagnostics report: the probes the app took plus the settings it keeps in the same snapshot
fn diagnostics_facts(snapshot: &Value) -> Value {
    let mut facts = get(snapshot, "diagnostics").clone();
    if let Some(o) = facts.as_object_mut() {
        o.insert("settings".into(), get(snapshot, "settings").clone());
    }
    facts
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn snap() -> Value {
        json!({
            "version": 1, "taken_at": 1_792_000_000, "settings": {"venue_name": "Joe's", "use_waiting_for_add": true, "queue_mode": "rotation", "bg_video_enabled": false},
            "connection": "Connected", "network_configured": true, "last": {"received": "", "accepted": "", "sync": "12:01:02", "error": ""}, "attention": {"1": {}},
            "queue_rev": 9, "queue": [{"name": "Ann", "rotation_marker": false}, {"name": "Bo", "rotation_marker": true}, {"name": " ", "rotation_marker": false}],
            "total_secs": 600, "locked": true, "looping": false,
            "diagnostics": {"local_name": "Speakers", "screen_count": 2, "karafun_path": null, "output_host": true, "preview_host": true, "show_ticker": true, "qr_url": "https://x/", "bg_files": []}
        })
    }

    #[test]
    fn every_piece_comes_from_the_one_snapshot() {
        let v = view(&snap()).unwrap();
        let t: Vec<_> = v.chips.iter().map(|c| c.text.as_str()).collect();
        assert_eq!(t[..3], ["Requests open", "Waitlist on", "Online"]);
        assert!(t[3].starts_with("2 singers \u{b7} ends "));
        assert_eq!(v.status.text, "Requests Open \u{2022} Waitlist On \u{2022} Connected");
        assert_eq!((v.status.location.as_str(), v.eta.singer_count, v.eta.minutes), ("Joe's", 2, 10));
        assert!(v.eta.html.contains("START</span> <span style=\"color:#FFF0A6; font-weight:850;\">Bo<"));
        assert_eq!(v.summary, "2 singers \u{b7} 10 min \u{b7} LOCKED");
        assert_eq!((v.report.passed, v.report.checks.len()), (6, 7));
        assert_eq!(v.report.checks[2].detail, "Application not found");
    }

    #[test]
    fn the_snapshot_clock_not_the_wall_clock_sets_the_ends_time() {
        let mut s = snap();
        let a = view(&s).unwrap().eta.ends;
        s["taken_at"] = json!(1_792_000_000 + 3600);
        let b = view(&s).unwrap().eta.ends;
        assert_ne!(a, b);
    }

    #[test]
    fn a_foreign_or_broken_snapshot_is_declined_not_guessed() {
        assert!(view(&json!([])).is_err());
        let mut s = snap();
        for v in [json!(0), json!(2), json!(9), json!("1"), Value::Null] {
            s["version"] = v;
            assert!(view(&s).is_err());
        }
        let mut s = snap();
        s["taken_at"] = json!("soon");
        assert!(view(&s).is_err());
    }

    #[test]
    fn absent_flags_mean_offline_unlocked_and_not_looping() {
        let mut s = snap();
        for k in ["network_configured", "locked", "looping", "connection"] {
            s.as_object_mut().unwrap().remove(k);
        }
        let v = view(&s).unwrap();
        assert_eq!(v.chips[2].text, "Offline");
        assert_eq!(v.summary, "2 singers \u{b7} 10 min");
        assert!(!v.eta.html.contains("LOCKED") && !v.eta.html.contains("LOOPING"));
    }

    #[test]
    fn an_empty_queue_has_no_summary_and_no_ends() {
        let mut s = snap();
        s["queue"] = json!([]);
        s["total_secs"] = json!(0);
        let v = view(&s).unwrap();
        assert_eq!((v.summary.as_str(), v.eta.ends.as_str(), v.chips[3].text.as_str()), ("", "", "0 singers"));
    }
}
