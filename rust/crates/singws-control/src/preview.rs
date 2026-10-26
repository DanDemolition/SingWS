//! The karaoke OUTPUT PREVIEW: the same JPEG frames the phone page shows (`/api/show-screen/snapshot.php`), fetched with the host session. It is a monitor, not a
//! display: the app uploads a frame about once a second while somebody is watching, so this poller only runs while the operator has the preview switched on
//! (every poll counts as a viewer to the app, which then spends a little CPU on capturing frames). Read-only: it never uploads and holds no API key.

use crate::client::Config;
use crate::session::{Session, SessionError};
use serde_json::Value;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::Duration;

/// What the server says about the current frame (the page's rules: a frame is shown only while `active` and an image URL is offered).
#[derive(Debug, Clone, PartialEq)]
pub struct Meta {
    pub enabled: bool,
    pub active: bool,
    pub updated_at_ms: i64,
    pub playback_state: String,
    pub warning: String,
    /// the `v=` of the image URL: changes whenever a new frame is stored
    pub image_version: Option<String>,
}

impl Meta {
    pub fn playing(&self) -> bool {
        self.playback_state.eq_ignore_ascii_case("playing")
    }
}

pub fn parse_meta(v: &Value) -> Result<Meta, String> {
    if v.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(v.get("error").and_then(Value::as_str).unwrap_or("refused").to_string());
    }
    let text = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let url = text("image_url");
    let version = url.split(['?', '&']).find_map(|p| p.strip_prefix("v=")).filter(|s| !s.is_empty()).map(str::to_string);
    let updated = v.get("updated_at_ms").and_then(Value::as_i64).unwrap_or_else(|| v.get("updated_at").and_then(Value::as_i64).unwrap_or(0) * 1000);
    Ok(Meta {
        enabled: v.get("enabled").and_then(Value::as_bool) != Some(false),
        active: v.get("active").and_then(Value::as_bool) == Some(true) && !url.is_empty(),
        updated_at_ms: updated,
        playback_state: text("playback_state"),
        warning: text("warning"),
        image_version: version,
    })
}

/// Milliseconds until the next look: quick while a song plays, slower when nothing is on the screen.
pub fn next_poll_ms(m: Option<&Meta>) -> u64 {
    match m {
        Some(m) if m.active && m.playing() => 400,
        Some(m) if m.active => 1000,
        _ => 1500,
    }
}

pub fn is_jpeg(bytes: &[u8]) -> bool {
    bytes.len() >= 4 && bytes[0] == 0xff && bytes[1] == 0xd8
}

pub fn status_text(m: &Meta) -> String {
    if !m.enabled {
        "The singer-screen preview is switched off for this venue".into()
    } else if !m.active {
        "No active singer screen preview".into()
    } else if !m.warning.is_empty() {
        m.warning.clone()
    } else {
        String::new()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum PreviewEvent {
    Frame { jpeg: Vec<u8>, updated_at_ms: i64, playing: bool },
    /// no picture to show (nothing playing, or the venue switched the preview off); the text says why
    Waiting(String),
    Failed(String),
}

impl Session {
    /// Ask for the frame description. Also tells the app that somebody is watching.
    pub fn snapshot_meta(&self) -> Result<Meta, SessionError> {
        let (status, body) = self.get_json("/api/show-screen/snapshot.php", serde_json::json!({"user": self.user()}))?;
        match parse_meta(&body) {
            Ok(m) => Ok(m),
            Err(e) if e == "unauthenticated" || status == 401 => Err(SessionError::Unauthenticated),
            Err(e) => Err(SessionError::Protocol(format!("preview refused (HTTP {status}): {e}"))),
        }
    }

    /// The JPEG the description points at (`version` is only a cache buster).
    pub fn snapshot_image(&self, version: &str) -> Result<Vec<u8>, SessionError> {
        let (status, body) = self.get_bytes("/api/show-screen/snapshot.php", serde_json::json!({"user": self.user(), "image": "1", "v": version}))?;
        if status != 200 || !is_jpeg(&body) {
            return Err(SessionError::Protocol(format!("no picture (HTTP {status})")));
        }
        Ok(body)
    }
}

pub struct PreviewHandle {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl PreviewHandle {
    pub fn stop(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

fn nap(stop: &AtomicBool, ms: u64) -> bool {
    let end = std::time::Instant::now() + Duration::from_millis(ms);
    while std::time::Instant::now() < end {
        if stop.load(Ordering::SeqCst) {
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
    stop.load(Ordering::SeqCst)
}

/// Poll while `wanted` is true. Logs in on its own (a second session cookie, in memory only); a refused PIN stops it, a dropped network is retried.
pub fn spawn_preview(config: Config, wanted: Arc<AtomicBool>, sink: impl Fn(PreviewEvent) + Send + 'static) -> PreviewHandle {
    let stop = Arc::new(AtomicBool::new(false));
    let stop2 = stop.clone();
    let thread = thread::Builder::new().name("singws-preview".into()).spawn(move || run(config, wanted, stop2, sink)).expect("spawn preview thread");
    PreviewHandle { stop, thread: Some(thread) }
}

fn run(config: Config, wanted: Arc<AtomicBool>, stop: Arc<AtomicBool>, sink: impl Fn(PreviewEvent)) {
    let mut session = match Session::new(&config.base_url, &config.user) {
        Ok(s) => s,
        Err(e) => {
            sink(PreviewEvent::Failed(e));
            return;
        }
    };
    let mut logged_in = false;
    let mut shown: Option<String> = None;
    while !stop.load(Ordering::SeqCst) {
        if !wanted.load(Ordering::SeqCst) {
            shown = None; // switching it back on shows the current frame again
            if nap(&stop, 200) {
                return;
            }
            continue;
        }
        if !logged_in {
            match session.login(&config.pin) {
                Ok(()) => logged_in = true,
                Err(e @ (SessionError::InvalidPin | SessionError::LanOnly | SessionError::NotConfigured | SessionError::TenantMismatch)) => {
                    sink(PreviewEvent::Failed(format!("Preview login refused: {e}")));
                    while !nap(&stop, 200) {}
                    return;
                }
                Err(e) => {
                    sink(PreviewEvent::Failed(format!("Preview: cannot reach the server ({e})")));
                    if nap(&stop, 5000) {
                        return;
                    }
                    continue;
                }
            }
        }
        let meta = match session.snapshot_meta() {
            Ok(m) => Some(m),
            Err(SessionError::Unauthenticated) => {
                session.forget();
                logged_in = false;
                continue;
            }
            Err(e) => {
                sink(PreviewEvent::Failed(format!("Preview update failed ({e})")));
                None
            }
        };
        if let Some(m) = &meta {
            match (&m.image_version, m.active) {
                (Some(v), true) => {
                    if shown.as_deref() != Some(v.as_str()) {
                        match session.snapshot_image(v) {
                            Ok(jpeg) => {
                                shown = Some(v.clone());
                                sink(PreviewEvent::Frame { jpeg, updated_at_ms: m.updated_at_ms, playing: m.playing() });
                            }
                            Err(e) => sink(PreviewEvent::Failed(format!("Preview update failed ({e})"))),
                        }
                    }
                }
                _ => {
                    shown = None;
                    sink(PreviewEvent::Waiting(status_text(m)));
                }
            }
        }
        if nap(&stop, next_poll_ms(meta.as_ref())) {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn meta(v: Value) -> Meta {
        parse_meta(&v).unwrap()
    }

    #[test]
    fn an_active_frame_needs_both_the_flag_and_an_image_url_and_carries_its_version() {
        let m = meta(json!({"ok": true, "enabled": true, "active": true, "updated_at_ms": 1700000000123i64, "playback_state": "playing", "image_url": "/api/show-screen/snapshot.php?user=dan&image=1&v=1700000000123"}));
        assert!(m.active && m.playing() && m.enabled);
        assert_eq!(m.image_version.as_deref(), Some("1700000000123"));
        assert_eq!(m.updated_at_ms, 1700000000123);
        assert!(!meta(json!({"ok": true, "active": true, "image_url": ""})).active, "active without a picture is not shown");
        assert!(!meta(json!({"ok": true, "active": false, "image_url": "/x?v=1"})).active);
        assert!(!meta(json!({"ok": true, "active": "yes", "image_url": "/x?v=1"})).active, "only a real true counts");
        assert_eq!(meta(json!({"ok": true, "active": true, "image_url": "/x?image=1"})).image_version, None);
    }

    #[test]
    fn seconds_only_servers_are_read_as_milliseconds_and_disabled_is_only_an_explicit_false() {
        assert_eq!(meta(json!({"ok": true, "updated_at": 5})).updated_at_ms, 5000);
        assert!(meta(json!({"ok": true})).enabled, "a missing flag means enabled, like the page");
        assert!(!meta(json!({"ok": true, "enabled": false})).enabled);
        assert!(meta(json!({"ok": true, "playback_state": "PLAYING"})).playing());
        assert!(!meta(json!({"ok": true, "playback_state": "paused"})).playing());
    }

    #[test]
    fn refusals_carry_the_servers_word() {
        assert_eq!(parse_meta(&json!({"ok": false, "error": "unauthenticated"})), Err("unauthenticated".into()));
        assert_eq!(parse_meta(&json!({"ok": false})), Err("refused".into()));
        assert_eq!(parse_meta(&json!({})), Err("refused".into()));
    }

    #[test]
    fn polling_is_quick_only_while_a_song_plays() {
        let playing = meta(json!({"ok": true, "active": true, "playback_state": "playing", "image_url": "/x?v=1"}));
        let paused = meta(json!({"ok": true, "active": true, "playback_state": "paused", "image_url": "/x?v=1"}));
        let idle = meta(json!({"ok": true, "active": false}));
        assert_eq!((next_poll_ms(Some(&playing)), next_poll_ms(Some(&paused)), next_poll_ms(Some(&idle)), next_poll_ms(None)), (400, 1000, 1500, 1500));
    }

    #[test]
    fn only_real_jpegs_are_accepted() {
        assert!(is_jpeg(&[0xff, 0xd8, 0xff, 0xe0]));
        assert!(!is_jpeg(&[0xff, 0xd8, 0xff]), "too short");
        assert!(!is_jpeg(b"<html>"));
        assert!(!is_jpeg(&[0x89, b'P', b'N', b'G']));
    }

    #[test]
    fn the_status_words() {
        assert_eq!(status_text(&meta(json!({"ok": true, "enabled": false}))), "The singer-screen preview is switched off for this venue");
        assert_eq!(status_text(&meta(json!({"ok": true, "active": false}))), "No active singer screen preview");
        assert_eq!(status_text(&meta(json!({"ok": true, "active": true, "image_url": "/x?v=1", "warning": "Snapshot capture failed."}))), "Snapshot capture failed.");
        assert_eq!(status_text(&meta(json!({"ok": true, "active": true, "image_url": "/x?v=1"}))), "");
    }
}
