//! The shell's picture of the remote app, as a pure reducer: feed it the client's [`Output`]s, read a JSON snapshot for the UI.
//! No Qt here, so every rule (what is enabled, what a refusal means in plain words, when a stale edit asks for a refresh) is unit-tested.

use crate::client::Output;
use crate::protocol::{Incoming, SingerRow};
use serde_json::{Value, json};

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Playback {
    pub title: String,
    pub artist: String,
    pub playing: bool,
    pub position: f64,
    pub duration: f64,
    pub key: i64,
    pub tempo: f64,
    pub bgm_volume: f64,
    pub karaoke_volume: f64,
}

/// Background music as the app reports it: which track the player is on (its row in the saved playlist, -1 when there is no playlist).
#[derive(Debug, Clone, PartialEq)]
pub struct Bg {
    pub playing: bool,
    pub volume: f64,
    pub index: i64,
    pub track: String,
}

impl Default for Bg {
    fn default() -> Self {
        Bg { playing: false, volume: 0.0, index: -1, track: String::new() }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct CommandResult {
    pub seq: u64,
    pub text: String,
    /// "ok" | "warn" | "error"
    pub kind: &'static str,
}

#[derive(Debug, Clone, Default)]
pub struct ViewModel {
    pub status: String,
    pub connected: bool,
    pub logged_in: bool,
    pub login_error: String,
    pub app_online: bool,
    pub app_version: String,
    pub playback: Playback,
    pub bg: Bg,
    pub rotation: Value,
    pub queue_sig: Option<String>,
    pub singers: Vec<SingerRow>,
    pending: Vec<(String, Option<String>)>,
    pub result: Option<CommandResult>,
    seq: u64,
    refresh_requested: bool,
}

/// What an acknowledgement means to an operator.
pub fn humanize(message: &str) -> (String, &'static str) {
    let m = message.trim();
    if let Some(rest) = m.strip_prefix("duplicate: ") {
        return humanize(rest);
    }
    if m.starts_with("dry_run") {
        return ("Dry run: the app checked it and changed nothing".into(), "warn");
    }
    match m {
        "stale_view" => ("The list changed before your edit arrived. It was refreshed; check it and try again".into(), "warn"),
        "expired" => ("The command was too old when it arrived, so the app ignored it".into(), "warn"),
        "clock_skew" => ("This computer's clock and the app's clock are too far apart".into(), "error"),
        "unknown_singer" => ("That singer is no longer in the rotation".into(), "warn"),
        "unknown_entry" => ("That song is no longer in the queue".into(), "warn"),
        "unknown_request" => ("That request is no longer on the waitlist (it may have been added, removed or restored already); look at the Waitlist again".into(), "warn"),
        "setting_changed" => ("Setting saved".into(), "ok"),
        "unknown_setting" => ("The shell may not change that setting".into(), "warn"),
        "bad_value" => ("That value is not allowed for this setting".into(), "warn"),
        "waitlist_added" => ("Request added to the rotation".into(), "ok"),
        "not_added" => ("That song could not be added from the local library. Use Replace or add it by hand".into(), "warn"),
        "add_error" => ("The app could not add that replacement song; look at the request in the app".into(), "warn"),
        "waitlist_removed" => ("Request removed from the waitlist".into(), "ok"),
        "waitlist_restored" => ("Request put back on the waitlist".into(), "ok"),
        "bad_request_id" => (format!("The app refused the command ({m})"), "error"),
        "shell_commands_off" => ("Remote control is switched off in the app (setting shell_commands)".into(), "error"),
        "no_change" => ("Already there; nothing changed".into(), "ok"),
        "bad_direction" | "bad_skipped" | "missing_request_id" | "missing_sent_at" => (format!("The app refused the command ({m})"), "error"),
        "added" => ("Song added".into(), "ok"),
        "skipped" => ("Skipped to the next track".into(), "ok"),
        "playing" => ("Playing that track".into(), "ok"),
        "karaoke_playing" => ("A karaoke song is playing, so the background music is off for now".into(), "warn"),
        "no_playlist" => ("There is no background-music playlist".into(), "warn"),
        "stale_playlist" | "unknown_track" => ("The playlist changed; look at the Music tab again and pick the track again".into(), "warn"),
        "simple_audio" => ("Simple Audio Mode is on in the app, so the equalisers are bypassed. Turn it off in the app first".into(), "warn"),
        "eq_unavailable" => ("The app could not start its equalisers".into(), "warn"),
        "bad_index" | "bad_track" | "bad_target" | "bad_gains" | "bad_enabled" => (format!("The app refused the command ({m})"), "error"),
        "unknown_song" => ("That song is not in the library (the song index may have changed); search again".into(), "warn"),
        "file_missing" => ("The song file is not on disk. Is the library drive connected?".into(), "warn"),
        "missing_mp3" => ("This CDG has no matching MP3 file next to it".into(), "warn"),
        "bad_song" | "bad_zip" => ("The app cannot play that file".into(), "warn"),
        "rotation_locked" => ("The rotation is locked. Unlock it in the app to add songs".into(), "warn"),
        "duplicate" => ("That singer already has this song queued".into(), "warn"),
        "limit" => ("That singer is at the song limit".into(), "warn"),
        "bad_singer" => ("That singer name is not valid".into(), "warn"),
        "bad_tempo" => ("The app refused the speed (70 to 130 percent only)".into(), "error"),
        "pitch_unsupported" => ("Key changes are not available on this setup".into(), "warn"),
        "name_taken" => ("Another singer already has that name".into(), "warn"),
        "bad_name" => ("That name is not valid".into(), "warn"),
        "renamed" => ("Singer renamed".into(), "ok"),
        "not_renamed" => ("The app could not rename that singer".into(), "warn"),
        "brand_set" => ("Brand priority saved".into(), "ok"),
        "brand_cleared" => ("Brand priority cleared".into(), "ok"),
        "bad_brands" => ("Enter brands such as KV, SC, SBI: letters and numbers separated by commas".into(), "warn"),
        "sending" => ("Sending the message…".into(), "ok"),
        "message_sent" => ("Message delivered".into(), "ok"),
        "message_failed" => ("The message could not be sent".into(), "warn"),
        "bad_message" => ("The message must be 1 to 500 characters".into(), "warn"),
        "duplicate_message" => ("That exact message was just sent to this singer".into(), "warn"),
        "network_not_configured" => ("The app has no server address, user or API key set, so it cannot send messages".into(), "warn"),
        "replaced" => ("Song replaced".into(), "ok"),
        "not_replaced" => ("The app could not replace that song".into(), "warn"),
        "song_playing" => ("That song is playing now, so it cannot be replaced".into(), "warn"),
        "bad_song_id" | "bad_key" => (format!("The app refused the command ({m})"), "error"),
        "moved" => ("Moved".into(), "ok"),
        "removed" => ("Removed".into(), "ok"),
        "changed" => ("Done".into(), "ok"),
        "noop" => ("Refreshed".into(), "ok"),
        // the app names a finished transport command by its own id ("play_pause", "next", "key_up" ...)
        "play_pause" | "stop" | "next" | "restart" | "key_up" | "key_down" | "tempo_up" | "tempo_down" | "bg_play_pause" => ("Done".into(), "ok"),
        "" => ("Done".into(), "ok"),
        other if other.starts_with("failed:") => (format!("The app could not do it: {}", other.trim_start_matches("failed:").trim()), "error"),
        other => (other.to_string(), "ok"),
    }
}

fn num(v: &Value, k: &str) -> f64 {
    v.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

fn text(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

impl ViewModel {
    pub fn new() -> Self {
        ViewModel { status: "Not connected".into(), rotation: Value::Null, ..Default::default() }
    }

    /// Remember that a command was sent (by id), with the words the result should use.
    pub fn note_sent(&mut self, request_id: &str, label: &str) {
        self.pending.push((request_id.to_string(), Some(label.to_string())));
    }

    /// A command the shell sends on its own (an automatic refresh): its acknowledgement is tracked but never shown as a result,
    /// so it cannot cover the message that caused it.
    pub fn note_sent_silently(&mut self, request_id: &str) {
        self.pending.push((request_id.to_string(), None));
    }

    /// Take a command back out of the pending list (it could not be sent after all).
    pub fn forget(&mut self, request_id: &str) {
        self.pending.retain(|(id, _)| id != request_id);
    }

    /// Show a problem that happened before anything reached the network (an invalid command, no connection).
    pub fn note_failure(&mut self, text: &str) {
        self.seq += 1;
        self.result = Some(CommandResult { seq: self.seq, text: text.to_string(), kind: "error" });
    }

    pub fn pending_count(&self) -> usize {
        self.pending.len()
    }

    /// True once after a stale-view refusal: the owner should ask the app to publish the queue again.
    pub fn take_refresh_request(&mut self) -> bool {
        std::mem::take(&mut self.refresh_requested)
    }

    fn finish(&mut self, request_id: &str, outcome: (String, &'static str)) {
        let label = match self.pending.iter().position(|(id, _)| id == request_id) {
            Some(i) => match self.pending.remove(i).1 {
                Some(label) => label,
                None if outcome.1 == "ok" => return, // a silent command that worked says nothing
                None => String::new(),               // one that failed is still reported
            },
            None => String::new(),
        };
        self.seq += 1;
        let text = if label.is_empty() { outcome.0 } else { format!("{label}: {}", outcome.0) };
        self.result = Some(CommandResult { seq: self.seq, text, kind: outcome.1 });
    }

    pub fn apply(&mut self, out: &Output) {
        match out {
            Output::Status(connected, text) => {
                self.connected = *connected;
                self.status = text.clone();
                if !connected {
                    self.app_online = false;
                }
            }
            Output::LoggedIn => {
                self.logged_in = true;
                self.login_error.clear();
            }
            Output::LoginRefused(why) => {
                self.logged_in = false;
                self.connected = false;
                self.app_online = false;
                self.login_error = why.clone();
            }
            Output::NoAnswer { request_id } => {
                self.finish(request_id, ("No answer from the app. It may or may not have received the command; check the queue before retrying".into(), "warn"));
            }
            Output::Log(_) => {}
            Output::Message(m) => self.apply_message(m),
        }
    }

    fn apply_message(&mut self, m: &Incoming) {
        match m {
            Incoming::Hello { .. } | Incoming::Other(_) | Incoming::NotJson => {}
            Incoming::State(s) => {
                self.app_online = s.connected;
                self.app_version = s.app_version.clone();
                if s.connected {
                    let p = &s.playback;
                    self.playback = Playback {
                        title: text(p, "title"),
                        artist: text(p, "artist"),
                        playing: p.get("is_playing").and_then(Value::as_bool).unwrap_or(false),
                        position: num(p, "position_seconds"),
                        duration: num(p, "duration_seconds"),
                        key: p.get("key").and_then(Value::as_i64).unwrap_or(0),
                        tempo: num(p, "tempo"),
                        bgm_volume: num(p, "bgm_volume"),
                        karaoke_volume: num(p, "karaoke_volume"),
                    };
                    self.rotation = s.rotation.clone();
                    self.bg = Bg {
                        playing: s.bg.get("playing").and_then(Value::as_bool).unwrap_or(false),
                        volume: s.bg.get("volume").and_then(Value::as_f64).unwrap_or(0.0),
                        index: s.bg.get("index").and_then(Value::as_i64).unwrap_or(-1),
                        track: s.bg.get("track").and_then(Value::as_str).unwrap_or("").to_string(),
                    };
                    if let Some(sig) = &s.queue_sig {
                        self.queue_sig = Some(sig.clone());
                    }
                }
            }
            Incoming::Queue(q) => {
                self.queue_sig = Some(q.queue_sig.clone());
                self.singers = q.singers.clone();
            }
            Incoming::Tick { position, duration, remaining: _, playing } => {
                self.playback.position = *position;
                self.playback.duration = *duration;
                self.playback.playing = *playing;
            }
            Incoming::Ack(a) => {
                if a.message.trim() == "stale_view" {
                    self.refresh_requested = true;
                }
                let (text, kind) = humanize(&a.message);
                let kind = if a.ok { kind } else if kind == "ok" { "error" } else { kind };
                self.finish(&a.request_id, (text, kind));
            }
            Incoming::Notice { request_id, ok, code, message } => {
                // the late answer to a command that was already acknowledged ("sending"): shown as its own result
                let (words, kind) = humanize(code);
                let text = if !ok && !message.trim().is_empty() { format!("{words}: {}", message.trim()) } else { words };
                let kind = if *ok { kind } else if kind == "ok" { "warn" } else { kind };
                let _ = request_id;
                self.seq += 1;
                self.result = Some(CommandResult { seq: self.seq, text, kind });
            }
            Incoming::Error { request_id, code, message } => {
                let words = match code.as_str() {
                    "app_offline" => "The app is not connected to the relay right now".to_string(),
                    "bad_token" | "unauthorized" => "The relay refused this session".to_string(),
                    other => format!("Relay error {other}: {message}"),
                };
                if code == "app_offline" {
                    self.app_online = false;
                }
                match request_id {
                    Some(id) if !id.is_empty() => self.finish(id, (words, "error")),
                    _ => {
                        self.seq += 1;
                        self.result = Some(CommandResult { seq: self.seq, text: words, kind: "error" });
                    }
                }
            }
        }
    }

    /// Commands can be sent only while the relay is up AND the app is on the other end.
    pub fn controls_enabled(&self) -> bool {
        self.connected && self.app_online
    }

    pub fn to_json(&self) -> Value {
        let current = |k: &str| {
            let r = self.rotation.get(k).cloned().unwrap_or(Value::Null);
            json!({"singer": text(&r, "singer"), "title": text(&r, "title"), "artist": text(&r, "artist")})
        };
        json!({
            "status": self.status,
            "connected": self.connected,
            "logged_in": self.logged_in,
            "login_error": self.login_error,
            "app_online": self.app_online,
            "app_version": self.app_version,
            "enabled": self.controls_enabled(),
            "playback": {
                "title": self.playback.title, "artist": self.playback.artist, "playing": self.playback.playing,
                "position": self.playback.position, "duration": self.playback.duration, "key": self.playback.key,
                "tempo": self.playback.tempo, "bgm_volume": self.playback.bgm_volume, "karaoke_volume": self.playback.karaoke_volume,
            },
            "bg": {"playing": self.bg.playing, "volume": self.bg.volume, "index": self.bg.index, "track": self.bg.track},
            "rotation": {"last": current("last"), "current": current("current"), "next": current("next")},
            "queue_sig": self.queue_sig,
            "singers": self.singers.iter().map(|s| json!({
                "singer_id": s.singer_id, "name": s.name, "skipped": s.skipped, "marker": s.marker,
                "songs": s.songs.iter().map(|y| json!({"entry_id": y.entry_id, "artist": y.artist, "title": y.title, "skipped": y.skipped})).collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
            "pending": self.pending.len(),
            "result": self.result.as_ref().map(|r| json!({"seq": r.seq, "text": r.text, "kind": r.kind})),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{Ack, QueueMsg, SongRow, StateMsg};

    fn state(connected: bool) -> Output {
        Output::Message(Incoming::State(StateMsg {
            connected,
            app_version: "1.0.1.4".into(),
            queue_sig: Some("sig1".into()),
            rotation: json!({"current": {"singer": "Dana", "title": "Halo", "artist": "Beyoncé"}, "next": {"singer": "Sam"}}),
            bg: json!({"playing": true, "volume": 0.4, "index": 3, "track": "Song.mp3"}),
            playback: json!({"title": "Halo", "artist": "Beyoncé", "is_playing": true, "position_seconds": 12.5, "duration_seconds": 200.0, "key": 2, "tempo": 1.1, "bgm_volume": 0.4, "karaoke_volume": 0.9}),
            updated_at: 1,
        }))
    }

    fn queue() -> Output {
        Output::Message(Incoming::Queue(QueueMsg {
            queue_sig: "sig2".into(),
            singers: vec![SingerRow { singer_id: "s1".into(), name: "Dana".into(), skipped: false, marker: true, songs: vec![SongRow { entry_id: "e1".into(), artist: "A".into(), title: "T".into(), skipped: false }] }],
        }))
    }

    fn ack(id: &str, ok: bool, message: &str) -> Output {
        Output::Message(Incoming::Ack(Ack { request_id: id.into(), ok, message: message.into() }))
    }

    #[test]
    fn controls_need_both_the_relay_and_the_app() {
        let mut v = ViewModel::new();
        assert!(!v.controls_enabled());
        v.apply(&Output::Status(true, "Connected to the relay".into()));
        assert!(!v.controls_enabled(), "relay up but no app state yet");
        v.apply(&state(true));
        assert!(v.controls_enabled());
        v.apply(&state(false));
        assert!(!v.controls_enabled(), "the relay reports the app gone");
        v.apply(&state(true));
        v.apply(&Output::Status(false, "Disconnected".into()));
        assert!(!v.controls_enabled() && !v.app_online, "a dropped relay also means the app cannot be reached");
        assert_eq!(v.status, "Disconnected");
    }

    #[test]
    fn state_queue_and_ticks_fill_the_picture() {
        let mut v = ViewModel::new();
        v.apply(&state(true));
        v.apply(&queue());
        let j = v.to_json();
        assert_eq!(j["playback"]["title"], "Halo");
        assert_eq!(j["playback"]["key"], 2);
        assert_eq!(j["rotation"]["current"]["singer"], "Dana");
        assert_eq!(j["rotation"]["last"]["singer"], "", "an empty slot is empty text, not null");
        assert_eq!(j["queue_sig"], "sig2", "the queue message is the freshest word on the fingerprint");
        assert_eq!(j["singers"][0]["songs"][0]["entry_id"], "e1");
        v.apply(&Output::Message(Incoming::Tick { position: 20.0, duration: 200.0, remaining: 180.0, playing: false }));
        assert_eq!(v.to_json()["playback"]["position"], 20.0);
        assert_eq!(v.to_json()["playback"]["playing"], false);
        // an offline state keeps the last known list but never overwrites the playback with empties
        v.apply(&state(false));
        assert_eq!(v.to_json()["playback"]["title"], "Halo");
        assert_eq!(v.to_json()["singers"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn acknowledgements_become_plain_words_with_the_commands_label() {
        let mut v = ViewModel::new();
        v.note_sent("r1", "Move Dana down");
        assert_eq!(v.pending_count(), 1);
        v.apply(&ack("r1", true, "moved"));
        assert_eq!(v.pending_count(), 0);
        let r = v.result.clone().unwrap();
        assert_eq!((r.text.as_str(), r.kind), ("Move Dana down: Moved", "ok"));
        v.note_sent("r2", "Remove song");
        v.apply(&ack("r2", false, "unknown_entry"));
        assert_eq!(v.result.clone().unwrap().text, "Remove song: That song is no longer in the queue");
        assert_eq!(v.result.clone().unwrap().kind, "warn");
        assert!(v.result.clone().unwrap().seq > r.seq, "every result gets a new sequence number so the UI can notice repeats");
        v.apply(&ack("unknown-id", true, "removed"));
        assert_eq!(v.result.clone().unwrap().text, "Removed", "an ack for something not tracked still shows");
    }

    #[test]
    fn waitlist_answers_are_plain_words() {
        for (code, ok, kind, text) in [
            ("setting_changed", true, "ok", "Setting saved"),
            ("unknown_setting", false, "warn", "The shell may not change that setting"),
            ("bad_value", false, "warn", "That value is not allowed for this setting"),
            ("waitlist_added", true, "ok", "Request added to the rotation"),
            ("not_added", false, "warn", "That song could not be added from the local library"),
            ("add_error", false, "warn", "The app could not add that replacement"),
            ("waitlist_removed", true, "ok", "Request removed from the waitlist"),
            ("waitlist_restored", true, "ok", "Request put back on the waitlist"),
            ("unknown_request", false, "warn", "That request is no longer on the waitlist"),
            ("bad_request_id", false, "error", "The app refused the command (bad_request_id)"),
        ] {
            let mut v = ViewModel::new();
            v.apply(&ack("w", ok, code));
            let r = v.result.clone().unwrap();
            assert!(r.text.starts_with(text), "{code}: {}", r.text);
            assert_eq!(r.kind, kind, "{code}");
        }
    }

    #[test]
    fn a_refusal_is_never_shown_as_success() {
        let mut v = ViewModel::new();
        v.note_sent("a", "Skip");
        v.apply(&ack("a", false, "moved"));
        assert_eq!(v.result.clone().unwrap().kind, "error", "ok=false overrides a success-looking text");
        v.note_sent("b", "Skip");
        v.apply(&ack("b", false, "shell_commands_off"));
        assert!(v.result.clone().unwrap().text.contains("switched off"));
        v.note_sent("c", "Skip");
        v.apply(&ack("c", true, "dry_run: would queue.remove_singer"));
        let r = v.result.clone().unwrap();
        assert_eq!(r.kind, "warn");
        assert!(r.text.contains("changed nothing"));
    }

    #[test]
    fn a_stale_view_asks_for_exactly_one_refresh() {
        let mut v = ViewModel::new();
        v.note_sent("r", "Move up");
        v.apply(&ack("r", false, "stale_view"));
        assert!(v.result.clone().unwrap().text.contains("refreshed"));
        assert!(v.take_refresh_request());
        assert!(!v.take_refresh_request(), "taken once");
        v.note_sent("r2", "x");
        v.apply(&ack("r2", true, "moved"));
        assert!(!v.take_refresh_request());
    }

    #[test]
    fn duplicates_unknowns_and_failures_have_words() {
        assert_eq!(humanize("duplicate: moved"), ("Moved".to_string(), "ok"));
        assert_eq!(humanize("failed: boom").0, "The app could not do it: boom");
        assert_eq!(humanize("failed: boom").1, "error");
        assert_eq!(humanize("no_change").1, "ok");
        assert_eq!(humanize("expired").1, "warn");
        assert_eq!(humanize("clock_skew").1, "error");
        assert_eq!(humanize("something new"), ("something new".to_string(), "ok"));
        assert_eq!(humanize("  ").0, "Done");
        for m in ["play_pause", "stop", "next", "restart", "key_up", "key_down", "tempo_up", "tempo_down", "bg_play_pause"] {
            assert_eq!(humanize(m), ("Done".to_string(), "ok"), "{m}");
        }
        assert_eq!(humanize("set_key +2").0, "set_key +2", "messages with details pass through");
    }

    #[test]
    fn no_answer_and_relay_errors_clear_the_pending_command() {
        let mut v = ViewModel::new();
        v.note_sent("n1", "Skip singer");
        v.apply(&Output::NoAnswer { request_id: "n1".into() });
        assert_eq!(v.pending_count(), 0);
        assert!(v.result.clone().unwrap().text.starts_with("Skip singer: No answer from the app"));
        assert_eq!(v.result.clone().unwrap().kind, "warn");
        v.apply(&Output::Status(true, "up".into()));
        v.apply(&state(true));
        assert!(v.app_online);
        v.note_sent("n2", "Play");
        v.apply(&Output::Message(Incoming::Error { request_id: Some("n2".into()), code: "app_offline".into(), message: "no app".into() }));
        assert_eq!(v.pending_count(), 0);
        assert!(v.result.clone().unwrap().text.contains("not connected to the relay"));
        assert!(!v.app_online);
        v.apply(&Output::Message(Incoming::Error { request_id: None, code: "bad_token".into(), message: "x".into() }));
        assert!(v.result.clone().unwrap().text.contains("refused this session"));
        v.apply(&Output::Message(Incoming::Error { request_id: Some(String::new()), code: "weird".into(), message: "m".into() }));
        assert_eq!(v.result.clone().unwrap().text, "Relay error weird: m");
    }

    #[test]
    fn an_automatic_refresh_never_covers_the_message_that_caused_it() {
        let mut v = ViewModel::new();
        v.note_sent("a", "Move Lee to the top");
        v.apply(&ack("a", false, "stale_view"));
        let shown = v.result.clone().unwrap();
        v.note_sent_silently("auto");
        v.apply(&ack("auto", true, "noop"));
        assert_eq!(v.result.clone().unwrap(), shown, "the successful silent acknowledgement changed nothing");
        assert_eq!(v.pending_count(), 0, "but it is no longer pending");
        v.note_sent_silently("auto2");
        v.apply(&ack("auto2", false, "shell_commands_off"));
        assert!(v.result.clone().unwrap().seq > shown.seq, "a silent command that FAILS is reported");
        v.note_sent_silently("auto3");
        v.apply(&Output::NoAnswer { request_id: "auto3".into() });
        assert!(v.result.clone().unwrap().text.starts_with("No answer"));
    }

    #[test]
    fn a_command_that_could_not_be_sent_leaves_no_ghost_and_says_why() {
        let mut v = ViewModel::new();
        v.note_sent("g1", "Skip");
        v.note_sent("g2", "Next");
        v.forget("g1");
        assert_eq!(v.pending_count(), 1, "only that command was taken back");
        v.forget("g2");
        assert_eq!(v.pending_count(), 0);
        v.forget("never-existed");
        let before = v.result.clone();
        v.note_failure("Not connected to the relay");
        let r = v.result.clone().unwrap();
        assert_eq!((r.text.as_str(), r.kind), ("Not connected to the relay", "error"));
        assert!(before.is_none() && r.seq == 1);
    }

    #[test]
    fn the_background_music_block_follows_the_state_message_and_defaults_to_no_playlist() {
        let mut v = ViewModel::new();
        assert_eq!(v.to_json()["bg"], json!({"playing": false, "volume": 0.0, "index": -1, "track": ""}));
        v.apply(&state(true));
        assert_eq!(v.to_json()["bg"], json!({"playing": true, "volume": 0.4, "index": 3, "track": "Song.mp3"}));
        // an older app that does not publish the fields leaves the defaults, never garbage
        let old = crate::protocol::parse_incoming(r#"{"type":"state","connected":true,"app_version":"1.0.1.4","rotation":{},"playback":{}}"#);
        let mut v2 = ViewModel::new();
        v2.apply(&Output::Message(old));
        assert_eq!(v2.to_json()["bg"], json!({"playing": false, "volume": 0.0, "index": -1, "track": ""}));
        let live = crate::protocol::parse_incoming(r#"{"type":"state","connected":true,"bg_playing":true,"bg_volume":0.65,"bg_index":0,"bg_track":"A.mp3","rotation":{},"playback":{}}"#);
        v2.apply(&Output::Message(live));
        assert_eq!(v2.to_json()["bg"], json!({"playing": true, "volume": 0.65, "index": 0, "track": "A.mp3"}));
    }

    #[test]
    fn music_and_eq_answers_are_plain_words() {
        assert_eq!(humanize("skipped").1, "ok");
        assert_eq!(humanize("playing").1, "ok");
        for code in ["karaoke_playing", "no_playlist", "stale_playlist", "unknown_track", "simple_audio", "eq_unavailable"] {
            assert_eq!(humanize(code).1, "warn", "{code}");
            assert_ne!(humanize(code).0, code, "{code} has words");
        }
        for code in ["bad_index", "bad_track", "bad_target", "bad_gains", "bad_enabled"] {
            assert_eq!(humanize(code).1, "error", "{code}");
        }
        assert_eq!(humanize("changed"), ("Done".to_string(), "ok"));
    }

    #[test]
    fn add_song_answers_are_plain_words_and_refusals_are_warnings() {
        assert_eq!(humanize("added"), ("Song added".to_string(), "ok"));
        for code in ["unknown_song", "file_missing", "missing_mp3", "rotation_locked", "duplicate", "limit", "bad_singer"] {
            assert_eq!(humanize(code).1, "warn", "{code} is a refusal the operator can act on, not an error");
            assert_ne!(humanize(code).0, code, "{code} has words");
        }
        assert_eq!(humanize("duplicate: added"), ("Song added".to_string(), "ok"), "a replayed request id still answers with its first result");
        assert_eq!(humanize("duplicate").0, "That singer already has this song queued", "the bare code is the song duplicate");
        assert_eq!(humanize("bad_song_id").1, "error");
    }

    #[test]
    fn dialog_answers_are_plain_words() {
        assert_eq!(humanize("renamed"), ("Singer renamed".to_string(), "ok"));
        for code in ["pitch_unsupported", "name_taken", "bad_name", "not_renamed"] {
            assert_eq!(humanize(code).1, "warn", "{code}");
            assert_ne!(humanize(code).0, code, "{code} has words");
        }
        assert_eq!(humanize("bad_tempo").1, "error");
        assert_eq!(humanize("bad_key").1, "error");
    }

    #[test]
    fn brand_message_and_replace_answers_are_plain_words() {
        for code in ["brand_set", "brand_cleared", "sending", "message_sent", "replaced"] {
            assert_eq!(humanize(code).1, "ok", "{code}");
            assert_ne!(humanize(code).0, code, "{code} has words");
        }
        for code in ["bad_brands", "message_failed", "bad_message", "duplicate_message", "network_not_configured", "not_replaced", "song_playing"] {
            assert_eq!(humanize(code).1, "warn", "{code}");
            assert_ne!(humanize(code).0, code, "{code} has words");
        }
        assert_eq!(humanize("playing").0, "Playing that track", "the music tab's word is untouched by the replace refusal");
    }

    #[test]
    fn a_late_notice_becomes_its_own_result_with_the_servers_reason() {
        let mut v = ViewModel::new();
        v.apply(&Output::Message(crate::protocol::parse_incoming(r#"{"type":"notice","request_id":"m1","ok":true,"code":"message_sent","message":""}"#)));
        let r = v.result.clone().unwrap();
        assert_eq!((r.text.as_str(), r.kind), ("Message delivered", "ok"));
        v.apply(&Output::Message(crate::protocol::parse_incoming(r#"{"type":"notice","request_id":"m2","ok":false,"code":"message_failed","message":"Server returned HTTP 500."}"#)));
        let r2 = v.result.clone().unwrap();
        assert_eq!((r2.text.as_str(), r2.kind), ("The message could not be sent: Server returned HTTP 500.", "warn"));
        assert!(r2.seq > r.seq);
        v.apply(&Output::Message(crate::protocol::parse_incoming(r#"{"type":"notice","request_id":"m3","ok":false,"code":"message_sent","message":""}"#)));
        assert_eq!(v.result.clone().unwrap().kind, "warn", "a failed notice is never shown as success");
    }

    #[test]
    fn login_outcomes() {
        let mut v = ViewModel::new();
        v.apply(&Output::LoggedIn);
        assert!(v.logged_in);
        v.apply(&Output::LoginRefused("invalid PIN".into()));
        assert!(!v.logged_in && !v.connected);
        assert_eq!(v.to_json()["login_error"], "invalid PIN");
        v.apply(&Output::LoggedIn);
        assert_eq!(v.to_json()["login_error"], "", "a later success clears the old complaint");
        v.apply(&Output::Log("x".into()));
    }
}
