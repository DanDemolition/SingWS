//! The Show tab's header and queue summary text, as a toolkit-free view model. Equal to `KaraokeApp._refresh_header_chips` (the four pills), `_refresh_header_status`
//! (the status line, its tooltip and the venue name), `_set_server_connection_status`'s state word, `_update_queue_eta_label` (the "SINGERS 5 • ETA 21:10 • ENDS 9:41 PM •
//! ROTATION" line and the summary pair it records), `_fmt_m_ss` and `_refresh_queue_summary` ("5 singers · 27 min · LOCKED").
//! What the app reads from live state is an input: the connection word, whether the network is configured, the summed queue seconds (`_compute_queue_eta_seconds`),
//! "is the rotation locked / is an intro looping", and the clock. Colours are named tokens; the toolkit maps them. Anything not reproducible is declined.

use crate::waitlist::py_int;
use serde_json::{Map, Value};
use singws_dbw::pyval::{R, decline, get, py_lower, py_str, py_str_or_empty, py_strip, truthy};

#[derive(Debug, Clone, PartialEq)]
pub struct Chip {
    pub text: String,
    /// good | warn | mute | purple
    pub tone: &'static str,
}

fn setting_bool(settings: &Value, key: &str, default: bool) -> bool {
    match settings.get(key) {
        Some(v) => truthy(v),
        None => default,
    }
}

/// `str(value or default)`
fn str_or(v: &Value, default: &str) -> R<String> {
    if truthy(v) { py_str(v) } else { Ok(default.to_string()) }
}

fn connection_word(connection_attr: &Value, network_configured: bool) -> R<String> {
    str_or(connection_attr, if network_configured { "Connected" } else { "Offline" })
}

fn singers_word(count: i64) -> String {
    format!("{count} singer{}", if count != 1 { "s" } else { "" })
}

/// `_set_server_connection_status(False, message)`: the state word the app stores
pub fn disconnected_state(message: &str) -> R<&'static str> {
    let lower = py_lower(py_strip(message))?;
    Ok(if lower.contains("auth") || lower.contains("failed") { "Offline" } else { "Reconnecting" })
}

/// `_refresh_header_chips` with the redesign on; `summary` is `_header_summary` as `[count, ends]`
pub fn chips(settings: &Value, connection_attr: &Value, network_configured: bool, summary: &Value) -> R<Vec<Chip>> {
    let accepting = setting_bool(settings, "requests_accepting", true);
    let waitlist = setting_bool(settings, "use_waiting_for_add", false);
    let connection = connection_word(connection_attr, network_configured)?;
    let (count, ends) = match summary.as_array().filter(|a| a.len() == 2) {
        Some(a) => (a[0].as_i64().ok_or("decline: summary count is not an integer")?, py_str_or_empty(&a[1])?),
        None => (0, String::new()),
    };
    let online = connection == "Connected";
    Ok(vec![
        Chip { text: if accepting { "Requests open" } else { "Requests closed" }.into(), tone: if accepting { "good" } else { "warn" } },
        Chip { text: if waitlist { "Waitlist on" } else { "Waitlist off" }.into(), tone: if waitlist { "good" } else { "mute" } },
        Chip { text: if online { "Online".to_string() } else { connection }, tone: if online { "good" } else { "warn" } },
        Chip { text: singers_word(count) + &if ends.is_empty() { String::new() } else { format!(" \u{b7} ends {ends}") }, tone: "purple" },
    ])
}

#[derive(Debug, Clone, PartialEq)]
pub struct Status {
    /// `now_singing` or `warning`: the theme colour of the leading dot
    pub dot_token: &'static str,
    /// "Requests Open • Waitlist On • Connected" (the label's text after the dot)
    pub text: String,
    pub tooltip: String,
    /// the venue name, without the leading bullet
    pub location: String,
}

/// `_refresh_header_status`; the `last_*` values are the app's strings (empty = default), `attention` its `_remote_attention_requests`
pub fn status(settings: &Value, connection_attr: &Value, network_configured: bool, last: &Value, attention: &Value, queue_rev: &Value) -> R<Status> {
    let accepting = setting_bool(settings, "requests_accepting", true);
    let waitlist = setting_bool(settings, "use_waiting_for_add", false);
    let connection = connection_word(connection_attr, network_configured)?;
    let rev = py_int(&if truthy(queue_rev) { queue_rev.clone() } else { Value::from(0) })?.unwrap_or(0);
    let received = str_or(get(last, "received"), "None")?;
    let accepted = str_or(get(last, "accepted"), "None")?;
    let sync = str_or(get(last, "sync"), "Never")?;
    let error = str_or(get(last, "error"), "None")?;
    let pending = attention.as_object().map(Map::len).unwrap_or(0);
    let accepting_text = if accepting { "Requests Open" } else { "Requests Closed" };
    let waitlist_text = if waitlist { "Waitlist On" } else { "Waitlist Off" };
    let mut location = String::from("Local");
    for k in ["location_name", "venue_name", "tenant", "user"] {
        let v = get(settings, k);
        if truthy(v) {
            location = py_str(v)?;
            break;
        }
    }
    Ok(Status {
        dot_token: if accepting { "now_singing" } else { "warning" },
        text: format!("{accepting_text} \u{2022} {waitlist_text} \u{2022} {connection}"),
        tooltip: format!(
            "Server diagnostics\nEnabled: {}\nWaitlist mode: {}\nConnection: {connection}\nLast request received: {received}\nLast request accepted: {accepted}\nLast sync: {sync}\nLast error: {error}\nQueue revision: {rev}\nNeeds attention: {pending}",
            if accepting { "Yes" } else { "No" },
            if waitlist { "On" } else { "Off" }
        ),
        location: py_strip(&location).to_string(),
    })
}

/// `_fmt_m_ss(secs)`
pub fn fmt_m_ss(secs: f64) -> String {
    if secs.is_nan() || secs <= 0.0 {
        return "0:00".into();
    }
    let s = secs.trunc() as i64;
    format!("{}:{:02}", s / 60, s % 60)
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&#x27;")
}

unsafe extern "C" {
    fn tzset();
}

/// seconds since local midnight for an epoch second (`datetime.fromtimestamp(ts)`), None where Python would raise
fn local_second_of_day(ts: i64) -> Option<i64> {
    let t: libc::time_t = ts as libc::time_t;
    if t as i64 != ts {
        return None;
    }
    // SAFETY: tzset and localtime_r get valid pointers to locals
    let tm = unsafe {
        tzset();
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&t, &mut tm).is_null() {
            return None;
        }
        tm
    };
    if !(1..=9998).contains(&(tm.tm_year + 1900)) {
        return None;
    }
    Some(tm.tm_hour as i64 * 3600 + tm.tm_min as i64 * 60 + tm.tm_sec as i64)
}

#[derive(Debug, Clone, PartialEq)]
pub struct EtaView {
    pub singer_count: i64,
    /// "ends 9:41 PM" time text, empty when nothing is queued
    pub ends: String,
    /// `_queue_eta_minutes`
    pub minutes: i64,
    /// the HTML the queue label shows
    pub html: String,
}

/// `_update_queue_eta_label`. `queue` is the app's list of singers; `total_secs` what `_compute_queue_eta_seconds` returned; `now` the epoch second of "now".
pub fn eta_view(queue: &Value, total_secs: &Value, now: i64, queue_mode: &Value, locked: bool, looping: bool) -> R<EtaView> {
    let total = match total_secs {
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        _ => return decline("total seconds is not a number"),
    };
    if total.abs() > 1.0e7 {
        return decline("an implausible queue length");
    }
    let mut singer_count = 0i64;
    for s in queue.as_array().map(|a| a.as_slice()).unwrap_or(&[]) {
        if s.is_object() && !py_strip(&py_str_or_empty(get(s, "name"))?).is_empty() {
            singer_count += 1;
        }
    }
    let eta = if total > 0.0 { fmt_m_ss(total) } else { "0:00".into() };
    let mut ends = String::new();
    let mut end_html = String::new();
    if total > 0.0 {
        let sod = local_second_of_day(now).ok_or("decline: clock outside Python's range")?;
        let at = ((sod as f64 + total).floor() as i64).rem_euclid(86400);
        let (h, m) = (at / 3600, (at % 3600) / 60);
        let h12 = if h % 12 == 0 { 12 } else { h % 12 };
        ends = format!("{h12}:{m:02} {}", if h < 12 { "AM" } else { "PM" });
        end_html = format!(
            "<span style=\"color:#5C6170;\"> \u{2022} </span><span style=\"color:#FF5C5C; font-weight:700;\">ENDS</span> <span style=\"color:#FF3B3B; font-weight:900;\">{ends}</span>"
        );
    }
    let rotation = py_lower(py_strip(&if queue_mode.is_null() { "classic".to_string() } else { py_str(queue_mode)? }))? == "rotation";
    let mut start_html = String::new();
    if rotation {
        // `_rotation_marker_index` calls `.get` on each singer in turn: a non-dict entry reached before the marker raises, and the app then shows no START
        let mut marker: Option<&Value> = None;
        for s in queue.as_array().map(|a| a.as_slice()).unwrap_or(&[]) {
            if !s.is_object() {
                break;
            }
            if truthy(get(s, "rotation_marker")) {
                marker = Some(s);
                break;
            }
        }
        if let Some(s) = marker {
            let name = py_strip(&py_str_or_empty(get(s, "name"))?).to_string();
            if !name.is_empty() {
                start_html = format!(
                    "<span style=\"color:#5C6170;\"> \u{2022} </span><span style=\"color:#F3CB00; font-weight:900;\">START</span> <span style=\"color:#FFF0A6; font-weight:850;\">{}</span>",
                    html_escape(&name)
                );
            }
        }
    }
    let locked_html = if locked {
        "<span style=\"color:#5C6170;\"> \u{2022} </span><span style=\"color:#F3CB00; font-weight:900;\">LOCKED</span>"
    } else {
        ""
    };
    let loop_html = if looping { "<span style=\"color:#5C6170;\"> \u{2022} </span><span style=\"color:#5AC8FA; font-weight:900;\">LOOPING</span>" } else { "" };
    let html = format!(
        "<span style=\"color:#B4B8C6; font-weight:700;\">SINGERS</span> <span style=\"color:#FFFFFF; font-weight:800;\">{singer_count}</span><span style=\"color:#5C6170;\"> \u{2022} </span><span style=\"color:#B4B8C6; font-weight:700;\">ETA</span> <span style=\"color:#FFFFFF; font-weight:800;\">{eta}</span>{end_html}<span style=\"color:#5C6170;\"> \u{2022} </span><span style=\"color:#B994FF; font-weight:800;\">{}</span>{start_html}{locked_html}{loop_html}",
        if rotation { "ROTATION" } else { "CLASSIC" }
    );
    let minutes = if total > 0.0 { (total / 60.0).round_ties_even() as i64 } else { 0 };
    Ok(EtaView { singer_count, ends, minutes, html })
}

/// `_refresh_queue_summary` with the redesign on: "5 singers · 27 min · LOCKED", empty with nobody queued
pub fn queue_summary(count: i64, minutes: i64, locked: bool, looping: bool) -> String {
    let mut text = singers_word(count);
    if minutes > 0 {
        text += &format!(" \u{b7} {minutes} min");
    }
    if locked {
        text += " \u{b7} LOCKED";
    }
    if looping {
        text += " \u{b7} LOOPING";
    }
    if count != 0 { text } else { String::new() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn chips_follow_the_settings_and_the_connection_word() {
        let c = chips(&json!({}), &Value::Null, true, &json!([3, "9:41 PM"])).unwrap();
        let t: Vec<_> = c.iter().map(|c| (c.text.as_str(), c.tone)).collect();
        assert_eq!(t, [("Requests open", "good"), ("Waitlist off", "mute"), ("Online", "good"), ("3 singers \u{b7} ends 9:41 PM", "purple")]);
        let c = chips(&json!({"requests_accepting": false, "use_waiting_for_add": 1}), &json!("Reconnecting"), true, &json!([1, ""])).unwrap();
        assert_eq!((c[0].text.as_str(), c[0].tone, c[1].tone, c[2].text.as_str(), c[2].tone, c[3].text.as_str()), ("Requests closed", "warn", "good", "Reconnecting", "warn", "1 singer"));
        assert_eq!(chips(&json!({}), &Value::Null, false, &Value::Null).unwrap()[2].text, "Offline");
    }

    #[test]
    fn a_dropped_connection_is_offline_for_auth_or_failure_else_reconnecting() {
        assert_eq!(disconnected_state("Auth rejected").unwrap(), "Offline");
        assert_eq!(disconnected_state("connect FAILED").unwrap(), "Offline");
        assert_eq!(disconnected_state("timed out").unwrap(), "Reconnecting");
        assert_eq!(disconnected_state("").unwrap(), "Reconnecting");
    }

    #[test]
    fn the_status_line_and_tooltip_use_defaults_for_blank_diagnostics() {
        let s = status(&json!({"venue_name": " Joe's "}), &Value::Null, false, &json!({"error": "boom"}), &json!({"1": {}, "2": {}}), &json!("7")).unwrap();
        assert_eq!((s.dot_token, s.text.as_str(), s.location.as_str()), ("now_singing", "Requests Open \u{2022} Waitlist Off \u{2022} Offline", "Joe's"));
        assert_eq!(
            s.tooltip,
            "Server diagnostics\nEnabled: Yes\nWaitlist mode: Off\nConnection: Offline\nLast request received: None\nLast request accepted: None\nLast sync: Never\nLast error: boom\nQueue revision: 7\nNeeds attention: 2"
        );
        assert_eq!(status(&json!({}), &Value::Null, true, &Value::Null, &Value::Null, &Value::Null).unwrap().location, "Local");
        assert_eq!(status(&json!({"requests_accepting": false}), &Value::Null, true, &Value::Null, &Value::Null, &Value::Null).unwrap().dot_token, "warning");
    }

    #[test]
    fn minutes_and_seconds() {
        for (v, t) in [(0.0, "0:00"), (-5.0, "0:00"), (0.9, "0:00"), (59.9, "0:59"), (60.0, "1:00"), (3725.0, "62:05")] {
            assert_eq!(fmt_m_ss(v), t);
        }
    }

    #[test]
    fn the_summary_pair() {
        assert_eq!(queue_summary(5, 27, false, false), "5 singers \u{b7} 27 min");
        assert_eq!(queue_summary(1, 0, true, true), "1 singer \u{b7} LOCKED \u{b7} LOOPING");
        assert_eq!(queue_summary(0, 10, true, false), "");
    }

    #[test]
    fn the_eta_line_counts_named_singers_and_names_the_start_marker() {
        let q = json!([{"name": "Ann"}, {"name": "  "}, {"name": "Bo & <Co>", "rotation_marker": true}, "junk"]);
        let v = eta_view(&q, &json!(1500), 1_792_000_000, &json!(" Rotation "), true, false).unwrap();
        assert_eq!((v.singer_count, v.minutes), (2, 25));
        assert!(v.html.contains(">ROTATION<") && v.html.contains("START") && v.html.contains("Bo &amp; &lt;Co&gt;") && v.html.contains("LOCKED") && !v.html.contains("LOOPING"));
        assert!(v.html.contains("ETA</span> <span style=\"color:#FFFFFF; font-weight:800;\">25:00"));
        let idle = eta_view(&json!([]), &json!(0), 1_792_000_000, &Value::Null, false, true).unwrap();
        let broken = eta_view(&json!([5, {"name": "Z", "rotation_marker": true}]), &json!(60), 1_792_000_000, &json!("rotation"), false, false).unwrap();
        assert!(!broken.html.contains("START"), "a non-dict entry before the marker raises in the app");
        assert!(idle.html.contains("CLASSIC") && idle.html.contains("LOOPING") && !idle.html.contains("ENDS") && idle.ends.is_empty());
    }
}
