//! Silence scan (port of `scanSilence`): a window-less, device-less libmpv core running `silencedetect` untimed.
use crate::bridge_log;
use crate::mpvapi::{self, Handle, MpvEventEndFile, MpvEventLogMessage};
use std::ffi::{c_int, CStr};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

static SCAN_LOCK: Mutex<()> = Mutex::new(());
static HANDLE: AtomicUsize = AtomicUsize::new(0);
static CANCEL: AtomicBool = AtomicBool::new(false);

#[cfg(target_os = "macos")]
fn lower_priority() {
    extern "C" {
        fn pthread_set_qos_class_self_np(qos: u32, rel: c_int) -> c_int;
    }
    unsafe { pthread_set_qos_class_self_np(0x11, 0) }; // QOS_CLASS_UTILITY
}
#[cfg(not(target_os = "macos"))]
fn lower_priority() {}

fn make_scanner(api: &'static mpvapi::Mpv) -> Option<Handle> {
    let h = api.create()?;
    let options = [
        ("config", "no"), ("terminal", "no"), ("input-default-bindings", "no"), ("load-scripts", "no"), ("vid", "no"),
        ("audio-display", "no"), ("ao", "null"), ("ao-null-untimed", "yes"), ("keep-open", "no"), ("idle", "yes"),
        ("pause", "no"), ("audio-pitch-correction", "no"),
    ];
    for (name, value) in options {
        let r = api.set_option(h, name, value);
        if r < 0 {
            bridge_log!("[scanner] option {} failed: {}", name, api.error_string(r));
            api.terminate_destroy(h);
            return None;
        }
    }
    let r = api.initialize(h);
    if r < 0 {
        bridge_log!("[scanner] initialize failed: {}", api.error_string(r));
        api.terminate_destroy(h);
        return None;
    }
    // mpv maps libavfilter's AV_LOG_INFO messages to its "v" level, so silencedetect markers are invisible at "info".
    api.request_log_messages(h, "v");
    Some(h)
}

fn parse_value(text: &str, marker: &str) -> Option<f64> {
    let rest = &text[text.find(marker)? + marker.len()..];
    let rest = rest.trim_start();
    let end = rest
        .char_indices()
        .find(|&(i, c)| !(c.is_ascii_digit() || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E') || (i == 0 && false))
        .map(|(i, _)| i)
        .unwrap_or(rest.len());
    let v: f64 = rest[..end].parse().ok()?;
    v.is_finite().then_some(v)
}

/// Returns (lead, trail, duration) on a clean full decode.
pub fn scan_silence(path: &str, noise_db: f64, lead_minimum: f64, trail_minimum: f64) -> Option<(f64, f64, f64)> {
    if path.is_empty() {
        return None;
    }
    let api = mpvapi::api().ok()?;
    lower_priority();
    let _guard = SCAN_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    CANCEL.store(false, Ordering::SeqCst);
    let mut raw = HANDLE.load(Ordering::SeqCst);
    if raw == 0 {
        raw = make_scanner(api)?.0 as usize;
        HANDLE.store(raw, Ordering::SeqCst);
    }
    let h = Handle(raw as *mut _);

    let detector_minimum = lead_minimum.min(trail_minimum).max(0.05);
    let filter = format!("lavfi=[silencedetect=noise={:.3}dB:d={:.3}]", noise_db, detector_minimum);
    let r = api.set_string(h, "af", &filter);
    if r < 0 {
        bridge_log!("[scanner] silencedetect unavailable: {}", api.error_string(r));
        return None;
    }
    // Drain leftovers from the previous completed scan before replacing it.
    loop {
        let e = api.wait_event(h, 0.0);
        if e.is_null() || unsafe { (*e).event_id } == mpvapi::EVENT_NONE {
            break;
        }
    }
    let r = api.command(h, &["loadfile", path, "replace"]);
    if r < 0 {
        bridge_log!("[scanner] load failed: {}", api.error_string(r));
        return None;
    }

    let (mut duration, mut first_start, mut first_end) = (0.0f64, -1.0f64, -1.0f64);
    let (mut active_start, mut last_start, mut last_end) = (-1.0f64, -1.0f64, -1.0f64);
    let (mut loaded, mut finished, mut clean_eof) = (false, false, false);
    let deadline = Instant::now() + Duration::from_secs(30);
    while !finished && !CANCEL.load(Ordering::SeqCst) && Instant::now() < deadline {
        let e = api.wait_event(h, 0.10);
        if e.is_null() {
            continue;
        }
        let ev = unsafe { &*e };
        match ev.event_id {
            mpvapi::EVENT_NONE => continue,
            mpvapi::EVENT_FILE_LOADED => {
                loaded = true;
                if let Ok(d) = api.get_double(h, "duration") {
                    duration = d;
                }
            }
            mpvapi::EVENT_LOG_MESSAGE => {
                let msg = ev.data as *const MpvEventLogMessage;
                let text = if msg.is_null() || unsafe { (*msg).text.is_null() } {
                    String::new()
                } else {
                    unsafe { CStr::from_ptr((*msg).text).to_string_lossy().into_owned() }
                };
                if let Some(v) = parse_value(&text, "silence_start:") {
                    active_start = v;
                    if first_start < 0.0 {
                        first_start = v;
                    }
                } else if let Some(v) = parse_value(&text, "silence_end:") {
                    if active_start >= 0.0 {
                        if first_end < 0.0 && first_start == active_start {
                            first_end = v;
                        }
                        last_start = active_start;
                        last_end = v;
                        active_start = -1.0;
                    }
                }
            }
            mpvapi::EVENT_END_FILE => {
                let end = ev.data as *const MpvEventEndFile;
                clean_eof = !end.is_null() && unsafe { (*end).reason } == mpvapi::END_FILE_EOF;
                finished = true;
            }
            _ => {}
        }
    }

    if !finished || CANCEL.load(Ordering::SeqCst) {
        api.command(h, &["stop"]);
        bridge_log!("[scanner] scan cancelled or timed out: {}", path);
        return None;
    }
    if !loaded || !clean_eof || duration <= 0.0 {
        bridge_log!("[scanner] scan did not reach a clean EOF: {}", path);
        return None;
    }
    if active_start >= 0.0 {
        if first_end < 0.0 && first_start == active_start {
            first_end = duration;
        }
        last_start = active_start;
        last_end = duration;
    }

    let mut lead = 0.0;
    if first_start >= 0.0 && first_start <= 0.30 && first_end >= 0.0 && (first_end - first_start) >= lead_minimum {
        lead = first_end.min(10.0).max(0.0);
    }
    let mut trail = duration;
    if last_start >= 0.0 && last_end >= duration - 0.70 && (last_end - last_start) >= trail_minimum {
        trail = last_start.min(duration).max(0.0);
    }
    bridge_log!("[scanner] ready lead={:.3} trail={:.3} duration={:.3} {}", lead, trail, duration, path);
    Some((lead, trail, duration))
}

pub fn shutdown() {
    CANCEL.store(true, Ordering::SeqCst);
    let active = HANDLE.load(Ordering::SeqCst);
    if active != 0 {
        if let Ok(api) = mpvapi::api() {
            api.wakeup(Handle(active as *mut _));
        }
    }
    let _guard = SCAN_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let active = HANDLE.swap(0, Ordering::SeqCst);
    if active != 0 {
        if let Ok(api) = mpvapi::api() {
            api.terminate_destroy(Handle(active as *mut _));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::parse_value;
    #[test]
    fn parses_silencedetect_markers() {
        assert_eq!(parse_value("[silencedetect @ 0x1] silence_start: 12.345", "silence_start:"), Some(12.345));
        assert_eq!(parse_value("silence_end: 3.5 | silence_duration: 2.0", "silence_end:"), Some(3.5));
        assert_eq!(parse_value("nothing here", "silence_end:"), None);
    }
}
