//! The duration watchdog of `_start_karafun_completion_monitor` (0.2.18.1.py): the timers that exist only to warn the operator when the end of a KaraFun song
//! cannot be verified. It never advances the rotation by itself. Pure functions over the monitor's state; tools/karafun_watchdog_parity.py fires the REAL
//! Python timer callbacks of a replayed monitor and compares what they schedule, log and warn.

pub const WARNING: &str = "Cannot verify that KaraFun has finished. Press the Play/Next button in SingWS when the song ends.";

/// The first timer: `max(1.0, duration - max(0, now - started) + 2.0)` seconds, only for a song with a verified duration.
pub fn initial_delay(fallback_duration: i64, now: f64, started: f64) -> Option<f64> {
    if fallback_duration > 0 { Some((fallback_duration as f64 - (now - started).max(0.0) + 2.0).max(1.0)) } else { None }
}

/// `_karafun_fallback_remaining(duration, elapsed, seek_offset)`: int(duration) - int(elapsed) - int(seek offset).
fn remaining(duration: i64, elapsed: f64, seek_offset: f64) -> i64 {
    duration - elapsed.trunc() as i64 - seek_offset.trunc() as i64
}

/// After a seek the song ends earlier or later than the timer armed at the start: the delay of the re-armed timer.
pub fn rearm_delay(fallback_duration: i64, now: f64, started: f64, confirmed_at: Option<f64>, seek_offset: f64) -> f64 {
    let base = confirmed_at.unwrap_or(started);
    (remaining(fallback_duration, now - base, seek_offset) as f64 + 2.0).max(1.0)
}

#[derive(Debug, PartialEq)]
pub enum Check {
    /// look again after this many seconds (the song may still be playing)
    Extend(f64),
    /// nothing says the song is still playing: warn the operator (the log line is returned)
    Warn { log: String },
}

/// What the timer does when it fires: `last_playing_ts` is `entry["karafun_last_playing_ts"]` (0 / None when never), `confirmed_at` the confirmed playback time.
pub fn check(now: f64, last_playing_ts: Option<f64>, confirmed_at: Option<f64>, fallback_duration: i64, seek_offset: f64) -> Check {
    let last = last_playing_ts.unwrap_or(0.0);
    let playing_age = if last > 0.0 { Some(now - last) } else { None };
    let confirmed_remaining = confirmed_at.map(|c| remaining(fallback_duration, now - c, seek_offset));
    let extend = match (confirmed_remaining, playing_age) {
        (Some(r), _) if r > 0 => Some(r as f64 + 2.0),
        (_, Some(age)) if age <= 20.0 => Some(15.0),
        _ => None,
    };
    match extend {
        Some(e) => Check::Extend(e),
        None => Check::Warn {
            log: format!("[KARAFUN] end unverified reason=duration_watchdog last_playing={}", match playing_age {
                None => "never".to_string(),
                Some(a) => format!("{:.1}s ago", a),
            }),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_first_timer() {
        assert_eq!(initial_delay(0, 100.0, 100.0), None);
        assert_eq!(initial_delay(263, 100.0, 100.0), Some(265.0));
        assert_eq!(initial_delay(10, 200.0, 100.0), Some(1.0));
    }

    #[test]
    fn a_song_that_may_still_be_playing_is_not_warned_about() {
        assert_eq!(check(300.0, Some(295.0), Some(120.0), 263, 0.0), Check::Extend(85.0));      // confirmed: 263 - 180 = 83 left, +2
        assert_eq!(check(300.0, Some(290.0), None, 263, 0.0), Check::Extend(15.0));            // seen playing 10 s ago
        match check(300.0, Some(250.0), None, 263, 0.0) {
            Check::Warn { log } => assert_eq!(log, "[KARAFUN] end unverified reason=duration_watchdog last_playing=50.0s ago"),
            other => panic!("{:?}", other),
        }
        assert!(matches!(check(300.0, None, None, 263, 0.0), Check::Warn { .. }));
    }
}
