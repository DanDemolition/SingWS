//! Pure decisions of `_automate_karafun_search_and_play` after the result was activated (lines "playback_probe_script = ..." to
//! "playback start event"). Transcribed from the Python; `test_karafun_startflow_rs.py` guards the Python source text.

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum PlayAction {
    Press,
    SkipExplicit,
    DeferToMonitor,
    SkipAlreadyPlaying,
}

pub struct PlayInput<'a> {
    pub fast_start: bool,
    pub managed_handoff: bool,
    pub managed_start_state: &'a str,
    pub explicit_play_sent: bool,
    pub probe_ok: bool,
    pub probe_state: &'a str,
}

/// Initial probe state: fast start probes nothing and assumes PLAYING (or the managed state).
pub fn initial_probe_state<'a>(fast_start: bool, managed_handoff: bool, managed_start_state: &'a str, probed: &'a str) -> &'a str {
    if fast_start {
        if managed_handoff { managed_start_state } else { "PLAYING" }
    } else {
        probed
    }
}

pub fn play_action(i: &PlayInput) -> PlayAction {
    let playing = i.probe_ok && i.probe_state == "PLAYING";
    let state_unknown = i.fast_start && i.managed_handoff && i.probe_state == "UNKNOWN";
    if !playing && !state_unknown && !i.explicit_play_sent {
        PlayAction::Press
    } else if i.explicit_play_sent {
        PlayAction::SkipExplicit
    } else if state_unknown {
        PlayAction::DeferToMonitor
    } else {
        PlayAction::SkipAlreadyPlaying
    }
}

pub fn verified_playing(ok: bool, probe_state: &str, fast_start: bool) -> bool {
    ok && probe_state == "PLAYING" && !fast_start
}

/// Max one-second verify probes: a cold KaraFun gets 30, a warm one 12.
pub fn max_probe_attempts(karafun_was_running: bool) -> u32 {
    if karafun_was_running { 12 } else { 30 }
}

/// The automatic-key/tempo answer check: `ADJUSTED|<key_steps>|<tempo_steps>`.
pub fn auto_adjusted(ok: bool, done: &str, key_steps: i64, tempo_steps: i64) -> bool {
    ok && done.trim() == format!("ADJUSTED|{}|{}", key_steps, tempo_steps)
}

/// The set-sliders answer: both requested changes confirmed.
pub fn adjustment_confirmed(requested_key: i64, requested_tempo: i64, adjusted: &str) -> bool {
    let low = adjusted.to_lowercase();
    let key_ok = requested_key == 0 || low.contains("|true|");
    let tempo_ok = requested_tempo == 100 || low.ends_with("|true");
    key_ok && tempo_ok
}

#[cfg(test)]
mod tests {
    use super::*;
    fn inp<'a>(f: bool, m: bool, s: &'a str, e: bool, ok: bool, p: &'a str) -> PlayInput<'a> {
        PlayInput { fast_start: f, managed_handoff: m, managed_start_state: s, explicit_play_sent: e, probe_ok: ok, probe_state: p }
    }
    #[test]
    fn play_rules() {
        assert_eq!(play_action(&inp(false, false, "", false, true, "IDLE")), PlayAction::Press);
        assert_eq!(play_action(&inp(false, false, "", false, true, "PLAYING")), PlayAction::SkipAlreadyPlaying);
        assert_eq!(play_action(&inp(false, false, "", true, true, "IDLE")), PlayAction::SkipExplicit);
        assert_eq!(play_action(&inp(true, true, "UNKNOWN", false, true, "UNKNOWN")), PlayAction::DeferToMonitor);
        assert_eq!(play_action(&inp(true, false, "", false, true, "UNKNOWN")), PlayAction::Press);
        assert_eq!(play_action(&inp(true, false, "", false, false, "PLAYING")), PlayAction::Press);
    }
    #[test]
    fn misc() {
        assert_eq!(initial_probe_state(true, true, "IDLE", "x"), "IDLE");
        assert_eq!(initial_probe_state(true, false, "IDLE", "x"), "PLAYING");
        assert_eq!(initial_probe_state(false, true, "IDLE", "x"), "x");
        assert!(!verified_playing(true, "PLAYING", true));
        assert!(verified_playing(true, "PLAYING", false));
        assert_eq!(max_probe_attempts(true), 12);
        assert!(auto_adjusted(true, " ADJUSTED|2|3\n", 2, 3));
        assert!(!auto_adjusted(true, "ADJUSTED|2|3", 2, 4));
        assert!(adjustment_confirmed(0, 100, ""));
        assert!(adjustment_confirmed(2, 90, "ADJUSTED|true|true"));
        assert!(!adjustment_confirmed(2, 90, "ADJUSTED|false|true"));
    }
}
