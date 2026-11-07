//! The decision core of `KaraokeApp._start_karafun_completion_monitor` (0.2.18.1.py, the `_monitor` loop and its setup) as a pure state machine.
//!
//! One `poll` call is one pass of the loop: the caller supplies what the real loop gets (the answer of the probe script and the time it took,
//! the flags it reads from the host) and receives everything the loop would do: the log lines, the calls (recovery press, operator warning,
//! completion), the entry fields it updates and how long it would sleep. Not modelled on purpose: the Rust transport shadow/drive plumbing
//! (log-only and covered elsewhere), the duration-watchdog timer thread and the key/tempo read thread's body (only the decision to start it).
//! tools/karafun_monitor_parity.py drives the REAL Python code (MonitorReplay's method, scripted answers, fake clock) against the same inputs.
use crate::ladder::py_repr;
use crate::pure::{normalize_duration_seconds, Duration};
use crate::py_strip;
use std::collections::BTreeMap;

#[derive(Clone, Debug, Default)]
pub struct Config {
    pub title: String,
    pub duration: i64,
    pub duration_estimated: bool,
    pub playback_assumed: bool,
    pub handoff_timed_out_before_play: bool,
    /// `entry["karafun_playback_clock_started_at"]` was set (a start that SingWS controlled)
    pub controlled_start: bool,
    pub capture_session: bool,
    pub recovery_delay: f64,         // KARAFUN_PLAYBACK_RECOVERY_DELAY_S
    pub handoff_recovery_delay: f64, // KARAFUN_HANDOFF_TIMEOUT_RECOVERY_DELAY_S
    pub alert_delay: f64,            // KARAFUN_PLAYBACK_ALERT_DELAY_S
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Entry {
    pub display_started_at: Option<f64>,
    pub display_duration: Option<i64>,
    pub last_playing_ts: Option<f64>,
    pub paused_at: Option<f64>,
    pub seek_offset: f64,
}

#[derive(Clone, Debug, Default)]
pub struct Poll {
    /// time.monotonic() when the pass begins (before the probe)
    pub now_before: f64,
    pub handoff_in_progress: bool,
    /// the probe: seconds it took, whether it worked and what it answered
    pub probe_elapsed: f64,
    pub ok: bool,
    pub raw: String,
    /// the session ended while the probe ran (the loop returns)
    pub session_ended: bool,
    /// the entry's status is already "manual"
    pub status_is_manual: bool,
    /// `_karafun_display_times()`: (elapsed, duration) or None
    pub display_times: Option<(f64, f64)>,
    pub kt_running: bool,
    pub kt_dirty: bool,
    /// `entry["karafun_kt_busy"]` (the read thread resets it, so the caller owns it)
    pub kt_busy: bool,
    /// `entry["karafun_seek_offset_s"]` as it is now (seeks change it during the song)
    pub seek_offset: f64,
    /// result of `_karafun_press_play_control()` if the loop asks for it: (pressed, error)
    pub press_result: (bool, String),
}

#[derive(Debug, Default, PartialEq)]
pub struct Out {
    pub log: Vec<String>,
    /// "press_play", "status:<message>", "complete"
    pub calls: Vec<String>,
    pub sleep: f64,
    /// the loop returned (completed, or the session ended)
    pub finished: bool,
    pub kt_read_started: bool,
    pub declined: Option<String>,
}

pub struct Monitor {
    cfg: Config,
    pub started: f64,
    fallback_duration: i64,
    seen_playback: bool,
    pub playback_confirmed_at: Option<f64>,
    recovery_pressed: bool,
    playback_hint_seen: bool,
    playing_hint_count: i64,
    last_state: String,
    last_clock_candidates: BTreeMap<i64, i64>,
    last_clock_remaining: Option<i64>,
    last_clock_at: Option<f64>,
    idle_stop_count: i64,
    last_kt_read: f64,
    pub entry: Entry,
}

/// Python `str.splitlines()` (the line boundaries str recognises).
pub fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let b: Vec<(usize, char)> = s.char_indices().collect();
    let mut i = 0;
    while i < b.len() {
        let (pos, c) = b[i];
        if matches!(c, '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            out.push(&s[start..pos]);
            let mut next = pos + c.len_utf8();
            if c == '\r' && i + 1 < b.len() && b[i + 1].1 == '\n' {
                next += 1;
                i += 1;
            }
            start = next;
        }
        i += 1;
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

impl Monitor {
    /// The setup part: `now` is time.monotonic() at the start, `started` the entry's clock start (or `now`); returns the log line it writes.
    pub fn new(cfg: Config, now: f64, started: f64, seek_offset: f64) -> Result<(Monitor, Vec<String>), String> {
        let started = if started <= 0.0 || started > now + 5.0 { now } else { started };
        let fallback_duration = if cfg.duration_estimated { 0 } else { cfg.duration.max(0) };
        let title = py_repr(&cfg.title).ok_or("unmodelled character in the title")?;
        let log = format!("[KARAFUN] completion monitor started title={} fallback_duration={} clock_age={:.1}s", title, fallback_duration, (now - started).max(0.0));
        let m = Monitor {
            cfg,
            started,
            fallback_duration,
            seen_playback: false,
            playback_confirmed_at: None,
            recovery_pressed: false,
            playback_hint_seen: false,
            playing_hint_count: 0,
            last_state: String::new(),
            last_clock_candidates: BTreeMap::new(),
            last_clock_remaining: None,
            last_clock_at: None,
            idle_stop_count: 0,
            last_kt_read: now - 40.0,
            entry: Entry { seek_offset, ..Entry::default() },
        };
        Ok((m, vec![log]))
    }

    fn confirm_playback(&mut self, now: f64, log: &mut Vec<String>) {
        self.seen_playback = true;
        if self.playback_confirmed_at.is_none() {
            self.playback_confirmed_at = Some(now);
            let delay = (now - self.started).max(0.0);
            // QUIRK KEPT ON PURPOSE: in the Python, `_confirm_playback` assigns `playback_clock_origin` without `nonlocal`, so this value is a throw-away
            // local: it only feeds the log line below and the fallback clock keeps counting from `started` (the "rebase" the comment promises never happens).
            let origin = if self.cfg.controlled_start { self.started } else { now };
            let retained = origin == self.started;
            log.push(format!(
                "[KARAFUN] playback confirmed; duration fallback rebased (launch overhead {:.1}s {})",
                delay,
                if retained { "retained" } else { "excluded" }
            ));
        }
    }

    fn fallback_remaining(&self, elapsed: f64) -> i64 {
        self.fallback_duration - elapsed.trunc() as i64 - self.entry.seek_offset.trunc() as i64
    }

    /// One pass of the loop.
    pub fn poll(&mut self, p: &Poll) -> Out {
        let mut o = Out::default();
        self.entry.seek_offset = p.seek_offset;
        if p.handoff_in_progress {
            o.sleep = 0.25;
            return o;
        }
        let now = p.now_before + p.probe_elapsed;
        if p.session_ended {
            o.finished = true;
            return o;
        }
        let mut remaining: Option<i64> = None;
        if p.probe_elapsed >= 4.0 {
            o.log.push(format!("[KARAFUN] slow playback probe seconds={:.1} ok={}", p.probe_elapsed, p.ok as i32));
        }
        let raw = if p.ok { p.raw.as_str() } else { "" };
        let lines: Vec<String> = py_splitlines(raw).iter().map(|v| py_strip(v).to_string()).filter(|v| !v.is_empty()).collect();
        let ended_reported = lines.iter().any(|v| v == "STATE|ENDED");
        let idle_reported = ended_reported || lines.iter().any(|v| v == "STATE|IDLE");
        let playing_reported = lines.iter().any(|v| v == "STATE|PLAYING");
        let mut clocks: Vec<i64> = Vec::new();
        for v in lines.iter().filter(|v| !v.starts_with("STATE|")) {
            match normalize_duration_seconds(&serde_json::Value::String(v.clone())) {
                Duration::Seconds(n) => clocks.push(n),
                Duration::None => {}
                Duration::Decline => {
                    o.declined = Some("unicode digits in a clock".to_string());
                    return o;
                }
            }
        }
        let mut next_candidates: BTreeMap<i64, i64> = BTreeMap::new();
        for i in 0..clocks.len().saturating_sub(1) {
            let (current, total) = (clocks[i], clocks[i + 1]);
            if total > 0 && 0 <= current && current <= total {
                let previous = self.last_clock_candidates.get(&total).copied();
                let best = next_candidates.get(&total).copied().unwrap_or(-1);
                next_candidates.insert(total, current.max(best));
                if previous.map_or(true, |pv| current <= pv) {
                    continue;
                }
                remaining = Some(total - current);
                self.entry.display_started_at = Some(now - current as f64);
                self.entry.display_duration = Some(total);
                self.last_clock_remaining = remaining;
                self.last_clock_at = Some(now);
                self.entry.last_playing_ts = Some(now);
                if current > 2 {
                    self.confirm_playback(now, &mut o.log);
                }
            }
        }
        self.last_clock_candidates = next_candidates;
        // pause tracking for the on-screen timer
        if playing_reported {
            if let Some(paused_at) = self.entry.paused_at.take() {
                if let Some(ds) = self.entry.display_started_at {
                    if ds != 0.0 {
                        let frozen = paused_at - ds;
                        self.entry.display_started_at = Some(now - frozen.max(0.0));
                    }
                }
            }
        } else if idle_reported && !ended_reported && self.entry.display_started_at.map_or(false, |d| d != 0.0) {
            if self.entry.paused_at.is_none() {
                self.entry.paused_at = Some(now);
            }
        }
        if playing_reported {
            self.playback_hint_seen = true;
            self.playing_hint_count += 1;
            self.idle_stop_count = 0;
            if self.playing_hint_count >= 2 {
                self.confirm_playback(now, &mut o.log);
                self.entry.last_playing_ts = Some(now);
            }
        } else if idle_reported && self.seen_playback {
            self.playing_hint_count = 0;
            self.idle_stop_count += 1;
        } else {
            self.playing_hint_count = 0;
            self.idle_stop_count = 0;
        }
        let rem_text = match remaining {
            Some(r) => r.to_string(),
            None => "None".to_string(),
        };
        let state_label = format!("idle={} playing={} remaining={}", idle_reported as i32, playing_reported as i32, rem_text);
        if state_label != self.last_state {
            o.log.push(format!("[KARAFUN] monitor state {} idle_stop_count={}", state_label, self.idle_stop_count));
            self.last_state = state_label;
        }

        let mut fallback_remaining: Option<i64> = None;
        let mut remaining_from_fallback = false;
        if self.fallback_duration > 0 {
            // the outer `playback_clock_origin` is never set (see confirm_playback), so the origin is always `started`
            fallback_remaining = Some(self.fallback_remaining(now - self.started));
        }
        if remaining.is_none() && fallback_remaining.is_some() {
            remaining = fallback_remaining;
            remaining_from_fallback = true;
        }
        let age = now - self.started;
        let recovery_delay = if self.cfg.handoff_timed_out_before_play { self.cfg.handoff_recovery_delay } else { self.cfg.recovery_delay };
        if let Some(r) = remaining {
            if r > 5 && !remaining_from_fallback {
                self.confirm_playback(now, &mut o.log);
            }
        }
        // recovery: one Play press after a confirmed idle, never a second result click
        if self.cfg.playback_assumed && !self.recovery_pressed && self.playback_confirmed_at.is_none() && !self.playback_hint_seen && idle_reported && !playing_reported && age > recovery_delay {
            self.recovery_pressed = true;
            o.log.push(format!("[KARAFUN] playback never started after {:.0}s; trying Play once (fast start assumed it had)", age));
            o.calls.push("press_play".to_string());
            if p.press_result.0 {
                o.log.push("[KARAFUN] recovery play press sent".to_string());
            } else {
                o.log.push(format!("[KARAFUN] recovery play press failed: {}", p.press_result.1));
                o.calls.push("status:KaraFun did not start this song. Press play in KaraFun.".to_string());
            }
            o.sleep = 1.0;
            return o;
        }
        // unverified playback: tell the operator, never press anything
        if self.cfg.playback_assumed && !playing_reported && !self.playback_hint_seen && self.playback_confirmed_at.is_none() && age > self.cfg.alert_delay && !p.status_is_manual {
            o.log.push(format!("[KARAFUN] playback unverified after {:.0}s; alerting operator idle={}", age, idle_reported as i32));
            let message = if idle_reported {
                "KaraFun is not playing this song. Press play in KaraFun."
            } else {
                "Cannot read KaraFun playback status. Check KaraFun; use Complete when the song ends."
            };
            o.calls.push(format!("status:{}", message));
        }
        let clock_near_end = self.last_clock_remaining.map_or(false, |r| r <= 5) && self.last_clock_at.map_or(false, |at| now - at <= 20.0);
        let expected_end_reached = self.playback_confirmed_at.is_some() && fallback_remaining.map_or(false, |r| r <= 5);
        let should_complete = idle_reported
            && !playing_reported
            && self.seen_playback
            && self.idle_stop_count >= 1
            && age > 8.0
            && (clock_near_end || expected_end_reached || (ended_reported && self.cfg.capture_session));
        if should_complete {
            let reason = if ended_reported {
                "karaFun_explicit_end"
            } else if clock_near_end {
                "karaFun_idle_clock_end"
            } else {
                "karaFun_idle_expected_end"
            };
            o.log.push(format!("[KARAFUN] completion event received reason={} remaining={} idle={}", reason, rem_text_of(remaining), idle_reported as i32));
            o.calls.push("complete".to_string());
            o.finished = true;
            return o;
        }
        // show a key/tempo change made inside KaraFun: decide whether to start the (slow) read
        let remaining_kt = p.display_times.map_or(0.0, |(a, b)| b - a);
        if self.playback_confirmed_at.is_some() && !p.kt_busy && !p.kt_dirty && !p.kt_running && remaining_kt > 35.0 && now - self.last_kt_read >= 60.0 {
            self.last_kt_read = now;
            o.kt_read_started = true;
        }
        o.sleep = 1.0;
        o
    }
}

fn rem_text_of(remaining: Option<i64>) -> String {
    match remaining {
        Some(r) => r.to_string(),
        None => "None".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> Config {
        Config { title: "Memory".into(), duration: 263, playback_assumed: true, recovery_delay: 12.0, handoff_recovery_delay: 6.0, alert_delay: 40.0, ..Config::default() }
    }

    fn poll(m: &mut Monitor, now: f64, elapsed: f64, raw: &str) -> Out {
        m.poll(&Poll { now_before: now, probe_elapsed: elapsed, ok: true, raw: raw.into(), press_result: (true, String::new()), ..Poll::default() })
    }

    #[test]
    fn splitlines_like_python() {
        assert_eq!(py_splitlines("a\r\nb\rc\nd"), vec!["a", "b", "c", "d"]);
        assert_eq!(py_splitlines("a\n"), vec!["a"]);
        assert_eq!(py_splitlines(""), Vec::<&str>::new());
        assert_eq!(py_splitlines("x\u{2028}y\u{85}z"), vec!["x", "y", "z"]);
    }

    #[test]
    fn an_idle_near_the_expected_end_completes_after_two_playing_polls() {
        let (mut m, _) = Monitor::new(cfg(), 100.0, 100.0, 0.0).unwrap();
        let mut now = 100.0;
        for raw in ["STATE|PLAYING", "STATE|PLAYING"] {
            let o = poll(&mut m, now, 5.0, raw);
            assert!(!o.finished);
            now += 5.0 + o.sleep;
        }
        let o = poll(&mut m, now, 263.0, "STATE|IDLE");
        assert!(o.finished);
        assert_eq!(o.calls, vec!["complete"]);
        assert!(o.log.iter().any(|l| l.contains("reason=karaFun_idle_expected_end")));
    }

    #[test]
    fn a_failed_probe_never_completes_even_long_after_the_duration() {
        let (mut m, _) = Monitor::new(cfg(), 100.0, 100.0, 0.0).unwrap();
        let o = m.poll(&Poll { now_before: 100.0, probe_elapsed: 400.0, ok: false, ..Poll::default() });
        // it warns the operator (status) but never completes and never presses Play
        assert!(!o.finished);
        assert!(!o.calls.iter().any(|c| c == "complete" || c == "press_play"));
        assert!(o.calls.iter().any(|c| c.starts_with("status:Cannot read KaraFun playback status")));
    }

    #[test]
    fn the_fallback_clock_counts_from_the_start_not_from_the_confirmation() {
        // the Python quirk: the origin is never rebased, so a late confirmation does not move the expected end
        let mut c = cfg();
        c.duration = 100;
        let (mut m, _) = Monitor::new(c, 100.0, 100.0, 0.0).unwrap();
        poll(&mut m, 100.0, 50.0, "STATE|PLAYING");
        poll(&mut m, 151.0, 1.0, "STATE|PLAYING");
        let o = poll(&mut m, 153.0, 1.0, "STATE|IDLE");
        // elapsed since the START is 54 s: 100 - 54 = 46 s left, not 100 - 4
        assert!(!o.finished);
    }

    #[test]
    fn a_handoff_in_progress_only_waits() {
        let (mut m, _) = Monitor::new(cfg(), 100.0, 100.0, 0.0).unwrap();
        let o = m.poll(&Poll { now_before: 100.0, handoff_in_progress: true, ..Poll::default() });
        assert_eq!((o.sleep, o.finished, o.log.len()), (0.25, false, 0));
    }
}
