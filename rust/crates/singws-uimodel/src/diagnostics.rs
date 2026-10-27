//! The pre-show diagnostics report as a toolkit-free view model. Equal to the rules of `KaraokeApp._collect_pre_show_diagnostics` (seven read-only checks) and the
//! report text of `run_pre_show_diagnostics` (the "N of M checks passed" title, the PASS / CHECK lines, the log line and which icon the box gets).
//! The probes themselves (the audio output name, the display count, KaraFun's install path, whether the native surfaces and the ticker exist, the signup URL, the
//! background-video folder scan) touch the machine and stay in the app: they arrive as `Facts`.

use serde_json::Value;
use singws_dbw::pyval::{R, get, py_str, py_str_or_empty, py_strip, truthy};

#[derive(Debug, Clone, PartialEq)]
pub struct Check {
    pub ok: bool,
    pub label: &'static str,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub checks: Vec<Check>,
    pub passed: usize,
    /// "3 of 7 checks passed"
    pub title: String,
    /// "PASS  label: detail" / "CHECK  label: detail", one per line
    pub lines: Vec<String>,
    /// the `[PRE-SHOW]` log line
    pub log_line: String,
    /// true: Information icon (everything passed); false: Warning
    pub all_passed: bool,
}

fn str_or(v: &Value, default: &str) -> R<String> {
    if truthy(v) { py_str(v) } else { Ok(default.to_string()) }
}

/// `facts`: {local_name, screen_count, settings{karafun_provider_enabled, ticker_enabled, bg_video_folder, bg_video_enabled}, karafun_path, output_host, preview_host,
/// show_ticker, qr_url, bg_files}
pub fn report(facts: &Value) -> R<Report> {
    let settings = get(facts, "settings");
    let local_name = get(facts, "local_name");
    let has_local = truthy(local_name);
    let screens = get(facts, "screen_count").as_i64().unwrap_or(0);
    let mut checks = Vec::new();
    checks.push(Check { ok: has_local, label: "Local audio output", detail: str_or(local_name, "No safe local output is selected")? });
    checks.push(Check {
        ok: has_local && screens >= 2,
        label: "AirPlay video-only routing",
        detail: if has_local { format!("{screens} displays; audio pinned to {}", py_str(local_name)?) } else { format!("{screens} displays; local audio pin missing") },
    });
    let karafun_enabled = settings.get("karafun_provider_enabled").map(truthy).unwrap_or(true);
    let path = get(facts, "karafun_path");
    let path = if karafun_enabled && truthy(path) { Some(py_str(path)?) } else { None };
    checks.push(Check {
        ok: karafun_enabled && path.is_some(),
        label: "KaraFun",
        detail: match &path {
            Some(p) => p.clone(),
            None if !karafun_enabled => "Disabled in settings".into(),
            None => "Application not found".into(),
        },
    });
    let surfaces = truthy(get(facts, "output_host")) && truthy(get(facts, "preview_host"));
    checks.push(Check { ok: surfaces, label: "Native video surfaces", detail: if surfaces { "show + host preview ready" } else { "show or host preview surface is missing" }.into() });
    let ticker = settings.get("ticker_enabled").map(truthy).unwrap_or(true) && truthy(get(facts, "show_ticker"));
    checks.push(Check { ok: ticker, label: "Show ticker", detail: if ticker { "enabled and constructed" } else { "disabled or not constructed" }.into() });
    let qr = get(facts, "qr_url");
    checks.push(Check { ok: truthy(qr), label: "Signup QR URL", detail: str_or(qr, "No request URL is configured/detectable")? });
    let folder = py_strip(&py_str_or_empty(get(settings, "bg_video_folder"))?).to_string();
    let files = get(facts, "bg_files").as_array().map(|a| a.len()).unwrap_or(0);
    let bg_enabled = settings.get("bg_video_enabled").map(truthy).unwrap_or(false);
    checks.push(Check {
        ok: !bg_enabled || files > 0,
        label: "Background videos",
        detail: if files > 0 {
            format!("{files} playable files in {folder}")
        } else if !bg_enabled {
            "disabled".into()
        } else {
            format!("No playable files in {}", if folder.is_empty() { "(unset)" } else { &folder })
        },
    });
    let passed = checks.iter().filter(|c| c.ok).count();
    let lines: Vec<String> = checks.iter().map(|c| format!("{}  {}: {}", if c.ok { "PASS" } else { "CHECK" }, c.label, c.detail)).collect();
    let log_line = format!("[PRE-SHOW] result={passed}/{} {}", checks.len(), lines.join(" | "));
    Ok(Report { title: format!("{passed} of {} checks passed", checks.len()), all_passed: passed == checks.len(), passed, lines, log_line, checks })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn good() -> Value {
        json!({"local_name": "MacBook Speakers", "screen_count": 2, "settings": {"bg_video_enabled": true, "bg_video_folder": " /vj "}, "karafun_path": "/Applications/KaraFun.app",
               "output_host": true, "preview_host": true, "show_ticker": true, "qr_url": "https://x", "bg_files": ["/vj/a.mp4", "/vj/b.mp4"]})
    }

    #[test]
    fn everything_ready_passes_all_seven() {
        let r = report(&good()).unwrap();
        assert_eq!((r.passed, r.all_passed, r.title.as_str()), (7, true, "7 of 7 checks passed"));
        assert_eq!(r.lines[1], "PASS  AirPlay video-only routing: 2 displays; audio pinned to MacBook Speakers");
        assert_eq!(r.lines[6], "PASS  Background videos: 2 playable files in /vj");
        assert!(r.log_line.starts_with("[PRE-SHOW] result=7/7 PASS  Local audio output: MacBook Speakers | "));
    }

    #[test]
    fn each_failure_reads_the_way_the_app_words_it() {
        let f = json!({"local_name": null, "screen_count": 1, "settings": {"karafun_provider_enabled": false, "ticker_enabled": false, "bg_video_enabled": true}, "karafun_path": "/x",
                       "output_host": true, "preview_host": false, "show_ticker": true, "qr_url": "", "bg_files": []});
        let r = report(&f).unwrap();
        let d: Vec<_> = r.checks.iter().map(|c| (c.ok, c.detail.as_str())).collect();
        assert_eq!(
            d,
            [
                (false, "No safe local output is selected"),
                (false, "1 displays; local audio pin missing"),
                (false, "Disabled in settings"),
                (false, "show or host preview surface is missing"),
                (false, "disabled or not constructed"),
                (false, "No request URL is configured/detectable"),
                (false, "No playable files in (unset)"),
            ]
        );
        assert!(!r.all_passed && r.passed == 0);
    }

    #[test]
    fn a_single_display_or_missing_app_or_disabled_videos_each_fail_or_pass_on_their_own() {
        let mut f = good();
        f["screen_count"] = json!(1);
        assert!(!report(&f).unwrap().checks[1].ok);
        f["karafun_path"] = Value::Null;
        assert_eq!(report(&f).unwrap().checks[2].detail, "Application not found");
        f["settings"]["bg_video_enabled"] = json!(false);
        f["bg_files"] = json!([]);
        let r = report(&f).unwrap();
        assert!(r.checks[6].ok);
        assert_eq!(r.checks[6].detail, "disabled");
    }
}
