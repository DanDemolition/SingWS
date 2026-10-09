//! Pure song-lifecycle state machine. `step(state, event) -> (state, commands)`; time is carried in events.
//!
//! It encodes the show-found rules from HANDOFF.md / AGENTS.md so they can be tested table-style and replayed
//! against real logs. Stage 3 runs it in SHADOW mode only: it never drives playback.
//!
//! Rules modelled:
//! * a media-end is ignored while a stop is in progress, while a hand-off is already active, or for a stale session;
//! * external (KaraFun) playback is *confirmed* only after two consecutive "playing" observations;
//! * an idle observation before confirmation never completes the song (at most one recovery retry);
//! * after confirmation an idle observation completes only when corroborated by fresh end-clock evidence or by the
//!   verified duration having elapsed since confirmation; unknown readings and the watchdog never advance rotation
//!   (the watchdog asks the operator for a manual Complete);
//! * manual Complete for the current session always completes; for another session it is ignored.

pub type SessionId = u64;

/// Seconds of slack when comparing elapsed time against the verified duration.
pub const DURATION_SLACK_S: f64 = 3.0;
/// End-clock evidence older than this is not "fresh".
pub const END_CLOCK_FRESH_S: f64 = 5.0;

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum State {
    Idle,
    Starting { session: SessionId, external: bool, duration: Option<f64>, retried: bool },
    Playing { session: SessionId, external: bool, duration: Option<f64>, confirmed_at: Option<f64>, hints: u8, end_clock_at: Option<f64> },
    /// Media end accepted; waiting for cleanup (BG overlap / teardown).
    Ending { session: SessionId },
}

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Event {
    StartRequested { session: SessionId, external: bool, duration: Option<f64> },
    PlayingObserved { session: SessionId, now: f64 },
    IdleObserved { session: SessionId, now: f64 },
    /// KaraFun/engine reported the end clock reached the end.
    EndClockObserved { session: SessionId, now: f64 },
    MediaEnd { session: SessionId, now: f64, error: bool },
    StopRequested { session: SessionId },
    ManualComplete { session: SessionId },
    WatchdogExpired { session: SessionId },
    CleanupDone { session: SessionId },
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub enum Command {
    BeginPlayback(SessionId),
    RetryResult(SessionId),
    MarkPlaying(SessionId),
    StartBgHandoff(SessionId),
    CompleteSong(SessionId),
    AdvanceRotation(SessionId),
    WarnOperator(SessionId, &'static str),
    Ignore(&'static str),
}

/// JSON bridge for the Python shadow hook: state/event in, `{"state":..,"commands":[..]}` out.
pub fn step_json(state_json: &str, event_json: &str, stop_in_progress: bool) -> Result<String, String> {
    let state: State = serde_json::from_str(state_json).map_err(|e| format!("state: {e}"))?;
    let event: Event = serde_json::from_str(event_json).map_err(|e| format!("event: {e}"))?;
    let (next, commands) = step(&state, &event, stop_in_progress);
    serde_json::to_string(&serde_json::json!({ "state": next, "commands": commands })).map_err(|e| e.to_string())
}

fn session_of(s: &State) -> Option<SessionId> {
    match s {
        State::Idle => None,
        State::Starting { session, .. } | State::Playing { session, .. } | State::Ending { session } => Some(*session),
    }
}

/// `stop_in_progress` mirrors the app's `_stop_in_progress` flag, which is outside the pure machine.
pub fn step(state: &State, event: &Event, stop_in_progress: bool) -> (State, Vec<Command>) {
    use Command::*;
    // Stale-session guard for every session-bound event except a fresh start.
    if let (Some(cur), Some(ev)) = (session_of(state), event_session(event)) {
        if cur != ev && !matches!(event, Event::StartRequested { .. }) {
            return (state.clone(), vec![Ignore("stale session")]);
        }
    }
    if session_of(state).is_none() && !matches!(event, Event::StartRequested { .. }) {
        return (state.clone(), vec![Ignore("no active session")]);
    }
    match (state, event) {
        (_, Event::StartRequested { session, external, duration }) => (
            State::Starting { session: *session, external: *external, duration: *duration, retried: false },
            vec![BeginPlayback(*session)],
        ),
        (State::Starting { session, external, duration, retried }, Event::PlayingObserved { now, .. }) => {
            // First hint: external playback still needs a second to confirm.
            let confirmed = !*external;
            (
                State::Playing {
                    session: *session, external: *external, duration: *duration,
                    confirmed_at: if confirmed { Some(*now) } else { None },
                    hints: 1, end_clock_at: None,
                },
                if confirmed { vec![MarkPlaying(*session)] } else { let _ = retried; vec![] },
            )
        }
        (State::Starting { session, external, duration, retried }, Event::IdleObserved { .. }) => {
            if *external && !*retried {
                (State::Starting { session: *session, external: true, duration: *duration, retried: true }, vec![RetryResult(*session)])
            } else {
                (state.clone(), vec![Ignore("idle before playing never completes")])
            }
        }
        (State::Playing { session, external, duration, confirmed_at, hints, end_clock_at }, Event::PlayingObserved { now, .. }) => {
            let hints = hints.saturating_add(1);
            let mut cmds = vec![];
            let mut confirmed = *confirmed_at;
            if confirmed.is_none() && (!*external || hints >= 2) {
                confirmed = Some(*now);
                cmds.push(MarkPlaying(*session));
            }
            (State::Playing { session: *session, external: *external, duration: *duration, confirmed_at: confirmed, hints, end_clock_at: *end_clock_at }, cmds)
        }
        (State::Playing { session, external, duration, confirmed_at, hints, .. }, Event::EndClockObserved { now, .. }) => (
            State::Playing { session: *session, external: *external, duration: *duration, confirmed_at: *confirmed_at, hints: *hints, end_clock_at: Some(*now) },
            vec![],
        ),
        (State::Playing { session, confirmed_at, duration, end_clock_at, .. }, Event::IdleObserved { now, .. }) => {
            let Some(c) = confirmed_at else { return (state.clone(), vec![Ignore("idle before confirmation")]) };
            let fresh = end_clock_at.is_some_and(|t| now - t <= END_CLOCK_FRESH_S);
            let elapsed = duration.is_some_and(|d| now - c >= d - DURATION_SLACK_S);
            if fresh || elapsed {
                (State::Ending { session: *session }, vec![CompleteSong(*session), StartBgHandoff(*session)])
            } else {
                (state.clone(), vec![Ignore("uncorroborated idle")])
            }
        }
        (State::Playing { session, external, .. }, Event::MediaEnd { error, .. }) => {
            // Native (non-external) end of stream is authoritative; external completion goes through IdleObserved.
            if stop_in_progress {
                return (state.clone(), vec![Ignore("stop in progress")]);
            }
            if *external && !*error {
                return (state.clone(), vec![Ignore("external end needs idle corroboration")]);
            }
            (State::Ending { session: *session }, vec![CompleteSong(*session), StartBgHandoff(*session)])
        }
        (State::Ending { .. }, Event::MediaEnd { .. }) => (state.clone(), vec![Ignore("duplicate media-end: hand-off active")]),
        (State::Ending { session }, Event::CleanupDone { .. }) => (State::Idle, vec![AdvanceRotation(*session)]),
        (State::Playing { session, .. } | State::Starting { session, .. }, Event::ManualComplete { .. }) => {
            (State::Ending { session: *session }, vec![CompleteSong(*session), StartBgHandoff(*session)])
        }
        (State::Playing { session, .. }, Event::WatchdogExpired { .. }) => {
            (state.clone(), vec![WarnOperator(*session, "no verified end; press Complete")])
        }
        (State::Starting { session, .. }, Event::WatchdogExpired { .. }) => {
            (state.clone(), vec![WarnOperator(*session, "playback never confirmed")])
        }
        (_, Event::StopRequested { .. }) => (State::Idle, vec![]),
        _ => (state.clone(), vec![Ignore("event not valid in this state")]),
    }
}

fn event_session(e: &Event) -> Option<SessionId> {
    match e {
        Event::StartRequested { session, .. } | Event::PlayingObserved { session, .. } | Event::IdleObserved { session, .. }
        | Event::EndClockObserved { session, .. } | Event::MediaEnd { session, .. } | Event::StopRequested { session }
        | Event::ManualComplete { session } | Event::WatchdogExpired { session } | Event::CleanupDone { session } => Some(*session),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use Command::*;

    fn run(events: &[Event]) -> (State, Vec<Vec<Command>>) {
        let mut s = State::Idle;
        let mut out = vec![];
        for e in events {
            let (n, c) = step(&s, e, false);
            s = n;
            out.push(c);
        }
        (s, out)
    }
    fn start(ext: bool, dur: Option<f64>) -> Event { Event::StartRequested { session: 1, external: ext, duration: dur } }
    fn play(now: f64) -> Event { Event::PlayingObserved { session: 1, now } }
    fn idle(now: f64) -> Event { Event::IdleObserved { session: 1, now } }

    #[test]
    fn native_song_plays_ends_and_advances_once() {
        let (s, c) = run(&[start(false, Some(200.0)), play(0.0), Event::MediaEnd { session: 1, now: 200.0, error: false },
            Event::MediaEnd { session: 1, now: 200.1, error: false }, Event::CleanupDone { session: 1 }]);
        assert_eq!(s, State::Idle);
        assert_eq!(c[2], vec![CompleteSong(1), StartBgHandoff(1)]);
        assert!(matches!(c[3][0], Ignore(m) if m.contains("duplicate")));
        assert_eq!(c[4], vec![AdvanceRotation(1)]);
    }

    #[test]
    fn external_needs_two_playing_hints() {
        let (s, c) = run(&[start(true, Some(263.0)), play(10.0)]);
        assert!(matches!(s, State::Playing { confirmed_at: None, .. }));
        assert!(c[1].is_empty());
        let (s, c) = run(&[start(true, Some(263.0)), play(10.0), play(12.0)]);
        assert!(matches!(s, State::Playing { confirmed_at: Some(t), .. } if t == 12.0));
        assert_eq!(c[2], vec![MarkPlaying(1)]);
    }

    /// 2026-08-31 10:50:25: first idle reading completed a 263 s song with 251 s remaining. Must not happen.
    #[test]
    fn early_idle_does_not_complete() {
        let (s, c) = run(&[start(true, Some(263.0)), play(10.0), play(12.0), idle(24.0)]);
        assert!(matches!(s, State::Playing { .. }));
        assert!(matches!(c[3][0], Ignore(m) if m.contains("uncorroborated")));
    }

    #[test]
    fn idle_before_playing_retries_once_then_never_completes() {
        let (s, c) = run(&[start(true, Some(200.0)), idle(5.0), idle(9.0)]);
        assert!(matches!(s, State::Starting { retried: true, .. }));
        assert_eq!(c[1], vec![RetryResult(1)]);
        assert!(matches!(c[2][0], Ignore(_)));
    }

    #[test]
    fn idle_after_duration_or_end_clock_completes() {
        let (s, _) = run(&[start(true, Some(100.0)), play(10.0), play(12.0), idle(111.0)]);
        assert_eq!(s, State::Ending { session: 1 });
        let (s, _) = run(&[start(true, None), play(10.0), play(12.0), Event::EndClockObserved { session: 1, now: 50.0 }, idle(52.0)]);
        assert_eq!(s, State::Ending { session: 1 });
        // stale end-clock evidence does not count
        let (s, _) = run(&[start(true, None), play(10.0), play(12.0), Event::EndClockObserved { session: 1, now: 50.0 }, idle(80.0)]);
        assert!(matches!(s, State::Playing { .. }));
    }

    #[test]
    fn watchdog_warns_never_advances() {
        let (s, c) = run(&[start(true, Some(200.0)), play(1.0), play(2.0), Event::WatchdogExpired { session: 1 }]);
        assert!(matches!(s, State::Playing { .. }));
        assert!(matches!(c[3][0], WarnOperator(1, _)));
    }

    #[test]
    fn stale_session_events_are_ignored() {
        let (s, c) = run(&[start(false, Some(100.0)), play(0.0),
            Event::MediaEnd { session: 99, now: 5.0, error: false }, Event::ManualComplete { session: 99 }]);
        assert!(matches!(s, State::Playing { session: 1, .. }));
        assert!(matches!(c[2][0], Ignore(m) if m.contains("stale")));
        assert!(matches!(c[3][0], Ignore(m) if m.contains("stale")));
    }

    #[test]
    fn manual_complete_and_stop_in_progress() {
        let (s, _) = run(&[start(true, Some(200.0)), play(1.0), Event::ManualComplete { session: 1 }]);
        assert_eq!(s, State::Ending { session: 1 });
        let st = State::Playing { session: 1, external: false, duration: None, confirmed_at: Some(0.0), hints: 2, end_clock_at: None };
        let (_, c) = step(&st, &Event::MediaEnd { session: 1, now: 1.0, error: false }, true);
        assert!(matches!(c[0], Ignore(m) if m.contains("stop")));
    }

    #[test]
    fn events_with_no_session_are_ignored() {
        let (s, c) = run(&[play(1.0), Event::MediaEnd { session: 1, now: 1.0, error: false }]);
        assert_eq!(s, State::Idle);
        assert!(c.iter().all(|v| matches!(v[0], Ignore(_))));
    }

    #[test]
    fn json_bridge_round_trips() {
        let out = step_json("\"Idle\"", r#"{"StartRequested":{"session":7,"external":true,"duration":200.0}}"#, false).unwrap();
        assert!(out.contains("BeginPlayback") && out.contains("Starting"), "{out}");
        assert!(step_json("\"Idle\"", "{bad", false).is_err());
    }

    /// Fuzz: any event order must never panic, and AdvanceRotation can only follow a CleanupDone.
    #[test]
    fn random_orders_never_advance_without_cleanup() {
        let mut seed = 0x1234_5678_u64;
        let mut rnd = || { seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17; seed };
        for _ in 0..2000 {
            let mut s = State::Idle;
            for i in 0..40 {
                let sess = 1 + rnd() % 2;
                let now = i as f64 * 3.0;
                let e = match rnd() % 9 {
                    0 => Event::StartRequested { session: sess, external: rnd() % 2 == 0, duration: Some(60.0) },
                    1 => Event::PlayingObserved { session: sess, now },
                    2 => Event::IdleObserved { session: sess, now },
                    3 => Event::EndClockObserved { session: sess, now },
                    4 => Event::MediaEnd { session: sess, now, error: rnd() % 2 == 0 },
                    5 => Event::StopRequested { session: sess },
                    6 => Event::ManualComplete { session: sess },
                    7 => Event::WatchdogExpired { session: sess },
                    _ => Event::CleanupDone { session: sess },
                };
                let (n, c) = step(&s, &e, rnd() % 5 == 0);
                if c.iter().any(|x| matches!(x, AdvanceRotation(_))) {
                    assert!(matches!(e, Event::CleanupDone { .. }));
                }
                s = n;
            }
        }
    }
}
