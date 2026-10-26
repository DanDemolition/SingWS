//! The relay's JSON messages: building and validating what the shell sends, parsing what the app reports. Pure: no sockets, no clocks except where a caller passes `now`.

use serde_json::{Value, json};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// Sortable unique id: milliseconds since the epoch, a process counter, 4 random bytes. The app applies each id once and the server orders by it.
pub fn new_request_id() -> String {
    use std::io::Read;
    let ms = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0);
    let n = COUNTER.fetch_add(1, Ordering::SeqCst) % 10_000;
    let mut rnd = [0u8; 4];
    let _ = std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut rnd));
    format!("{ms:013}-{n:04}-{}", rnd.iter().map(|b| format!("{b:02x}")).collect::<String>())
}

const TRANSPORT_NO_ARGS: &[&str] = &["play_pause", "skip", "restart", "stop", "key_up", "key_down", "tempo_up", "tempo_down", "bg_play_pause"];
const DIRECTIONS: &[&str] = &["up", "down", "top", "bottom"];

fn text<'a>(args: &'a Value, key: &str) -> Option<&'a str> {
    args.get(key).and_then(Value::as_str).filter(|s| !s.trim().is_empty())
}

fn number(args: &Value, key: &str) -> Option<f64> {
    args.get(key).and_then(Value::as_f64).filter(|v| v.is_finite())
}

/// Refuse anything the app would reject for its shape, so a bug in the shell can never put a malformed command on the wire.
pub fn validate_command(command: &str, args: &Value) -> Result<(), String> {
    if !(args.is_object() || args.is_null()) {
        return Err("args must be an object".into());
    }
    let need = |ok: bool, what: &str| if ok { Ok(()) } else { Err(format!("{command}: {what}")) };
    match command {
        c if TRANSPORT_NO_ARGS.contains(&c) => Ok(()),
        "set_key" => need(number(args, "value").is_some_and(|v| v.fract() == 0.0 && v.abs() <= 12.0), "value must be a whole number of semitones, -12 to 12"),
        "set_tempo" => need(number(args, "value").is_some_and(|v| (0.25..=4.0).contains(&v) || (25.0..=400.0).contains(&v)), "value must be a ratio 0.25-4.0 or a percent 25-400"),
        "set_bg_volume" | "set_karaoke_volume" => need(number(args, "value").is_some_and(|v| (0.0..=1.0).contains(&v)), "value must be between 0 and 1"),
        "seek_to" => need(number(args, "seconds").is_some_and(|v| v >= 0.0), "seconds must be zero or more"),
        "queue.noop" => Ok(()),
        "queue.move_singer" => {
            need(text(args, "singer_id").is_some(), "singer_id is required")?;
            need(args.get("direction").and_then(Value::as_str).is_some_and(|d| DIRECTIONS.contains(&d)), "direction must be up, down, top or bottom")
        }
        "queue.move_song" => {
            need(text(args, "entry_id").is_some(), "entry_id is required")?;
            need(args.get("direction").and_then(Value::as_str).is_some_and(|d| DIRECTIONS.contains(&d)), "direction must be up, down, top or bottom")
        }
        "queue.remove_singer" => need(text(args, "singer_id").is_some(), "singer_id is required"),
        "queue.remove_song" => need(text(args, "entry_id").is_some(), "entry_id is required"),
        "queue.add_song" => {
            let singer = args.get("singer").and_then(Value::as_str).map(str::trim).unwrap_or("");
            need(!singer.is_empty() && singer.chars().count() <= 60 && !singer.chars().any(char::is_control), "singer must be a name of 1-60 characters with no control characters")?;
            need(args.get("song_id").and_then(Value::as_u64).is_some_and(|id| id >= 1), "song_id must be a whole number from the library search")?;
            need(args.get("key").is_none_or(|k| k.as_i64().is_some_and(|v| (-4..=4).contains(&v))), "key must be a whole number from -4 to 4")
        }
        "queue.skip_singer" => {
            need(text(args, "singer_id").is_some(), "singer_id is required")?;
            need(args.get("skipped").is_some_and(Value::is_boolean), "skipped must be true or false")
        }
        other => Err(format!("unknown command: {other}")),
    }
}

/// The `command` message the relay forwards to the app. `expect_queue_sig` makes a stale edit refuse itself; `source: shell` lets the app apply its
/// shell-commands switch to transport commands too.
pub fn build_command(user: &str, command: &str, args: &Value, expect_queue_sig: Option<&str>, request_id: &str, sent_at: u64) -> Result<Value, String> {
    validate_command(command, args)?;
    let mut a = match args {
        Value::Object(m) => m.clone(),
        _ => serde_json::Map::new(),
    };
    a.insert("source".into(), json!("shell"));
    if let Some(sig) = expect_queue_sig.filter(|s| !s.is_empty()) {
        if !command.starts_with("queue.") {
            return Err("expect_queue_sig only applies to queue commands".into());
        }
        a.insert("expect_queue_sig".into(), json!(sig));
    }
    Ok(json!({ "type": "command", "user": user, "command": command, "args": Value::Object(a), "request_id": request_id, "sent_at": sent_at }))
}

#[derive(Debug, Clone, PartialEq)]
pub struct StateMsg {
    pub connected: bool,
    pub app_version: String,
    pub queue_sig: Option<String>,
    pub rotation: Value,
    pub playback: Value,
    /// the background-music block, from the state's top-level `bg_*` fields: {playing, volume, index, track}
    pub bg: Value,
    pub updated_at: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SongRow {
    pub entry_id: String,
    pub artist: String,
    pub title: String,
    pub skipped: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SingerRow {
    pub singer_id: String,
    pub name: String,
    pub skipped: bool,
    pub marker: bool,
    pub songs: Vec<SongRow>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct QueueMsg {
    pub queue_sig: String,
    pub singers: Vec<SingerRow>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Ack {
    pub request_id: String,
    pub ok: bool,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Incoming {
    Hello { role: String },
    State(StateMsg),
    Queue(QueueMsg),
    Tick { position: f64, duration: f64, remaining: f64, playing: bool },
    Ack(Ack),
    /// relay or app error such as `app_offline`, `bad_token`, `unauthorized`
    Error { request_id: Option<String>, code: String, message: String },
    Other(String),
    NotJson,
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn b(v: &Value, k: &str) -> bool {
    v.get(k).and_then(Value::as_bool).unwrap_or(false)
}

fn f(v: &Value, k: &str) -> f64 {
    v.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

/// Parse one relay text frame. Unknown or malformed pieces degrade to defaults; nothing here can panic or fail the connection.
pub fn parse_incoming(text: &str) -> Incoming {
    let Ok(v) = serde_json::from_str::<Value>(text) else { return Incoming::NotJson };
    if !v.is_object() {
        return Incoming::NotJson;
    }
    match v.get("type").and_then(Value::as_str).unwrap_or("") {
        "hello" => Incoming::Hello { role: s(&v, "role") },
        "state" => Incoming::State(StateMsg {
            connected: v.get("connected").and_then(Value::as_bool).unwrap_or(true),
            app_version: s(&v, "app_version"),
            queue_sig: v.get("queue_sig").and_then(Value::as_str).map(str::to_string),
            rotation: v.get("rotation").cloned().unwrap_or(Value::Null),
            playback: v.get("playback").cloned().unwrap_or(Value::Null),
            bg: json!({"playing": v.get("bg_playing"), "volume": v.get("bg_volume"), "index": v.get("bg_index"), "track": v.get("bg_track")}),
            updated_at: v.get("updated_at").and_then(Value::as_i64).unwrap_or(0),
        }),
        "queue" => {
            let singers = v
                .get("singers")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter(|x| x.is_object())
                        .map(|x| SingerRow {
                            singer_id: s(x, "singer_id"),
                            name: s(x, "name"),
                            skipped: b(x, "skipped"),
                            marker: b(x, "marker"),
                            songs: x
                                .get("songs")
                                .and_then(Value::as_array)
                                .map(|ss| {
                                    ss.iter()
                                        .filter(|y| y.is_object())
                                        .map(|y| SongRow { entry_id: s(y, "entry_id"), artist: s(y, "artist"), title: s(y, "title"), skipped: b(y, "skipped") })
                                        .collect()
                                })
                                .unwrap_or_default(),
                        })
                        .collect()
                })
                .unwrap_or_default();
            Incoming::Queue(QueueMsg { queue_sig: s(&v, "queue_sig"), singers })
        }
        "playback_tick" => Incoming::Tick {
            position: f(&v, "position_seconds"),
            duration: f(&v, "duration_seconds"),
            remaining: f(&v, "remaining_seconds"),
            playing: b(&v, "is_playing"),
        },
        "ack" => Incoming::Ack(Ack { request_id: s(&v, "request_id"), ok: b(&v, "ok"), message: s(&v, "message") }),
        "error" => Incoming::Error {
            request_id: v.get("request_id").and_then(Value::as_str).map(str::to_string),
            code: s(&v, "code"),
            message: s(&v, "message"),
        },
        other => Incoming::Other(other.to_string()),
    }
}

/// The production relay answers `426 Upgrade Required` to any connection without `app_version` >= 0.3.0.1 (found 2026-10-25: 0.1.0 and
/// non-numeric values are refused, 0.3.0.1 and 1.0.1.5 pass), so the shell sends the app release line whose relay protocol it speaks.
pub const RELAY_APP_VERSION: &str = "1.0.1.5";

/// `ws_url` + the query the web DAW page uses: `role=browser&channel=host&user=&token=`, plus `app_version` for the production relay's gate.
pub fn browser_url(ws_url: &str, user: &str, token: &str) -> String {
    let sep = if ws_url.contains('?') { '&' } else { '?' };
    format!(
        "{ws_url}{sep}role=browser&channel=host&user={}&token={}&app_version={RELAY_APP_VERSION}",
        singws_relay::percent_encode(user),
        singws_relay::percent_encode(token)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_ids_are_unique_and_sort_by_creation() {
        let ids: Vec<String> = (0..2000).map(|_| new_request_id()).collect();
        let mut sorted = ids.clone();
        sorted.sort();
        sorted.dedup();
        assert_eq!(sorted.len(), 2000, "no duplicates");
        assert_eq!(ids, {
            let mut s = ids.clone();
            s.sort();
            s
        }, "ids created later never sort earlier");
        assert_eq!(ids[0].len(), 13 + 1 + 4 + 1 + 8);
    }

    #[test]
    fn valid_commands_build_and_carry_the_source_and_signature() {
        let m = build_command("dan", "queue.move_singer", &json!({"singer_id": "a1", "direction": "up"}), Some("abc123"), "rid-1", 1_790_000_000).unwrap();
        assert_eq!(m["type"], "command");
        assert_eq!(m["command"], "queue.move_singer");
        assert_eq!(m["request_id"], "rid-1");
        assert_eq!(m["sent_at"], 1_790_000_000u64);
        assert_eq!(m["args"], json!({"singer_id": "a1", "direction": "up", "source": "shell", "expect_queue_sig": "abc123"}));
        let t = build_command("dan", "play_pause", &json!({}), None, "r", 1).unwrap();
        assert_eq!(t["args"], json!({"source": "shell"}));
        assert!(build_command("dan", "play_pause", &Value::Null, None, "r", 1).is_ok(), "null args are an empty object");
        assert!(build_command("dan", "play_pause", &json!({}), Some("sig"), "r", 1).is_err(), "a signature belongs to queue commands only");
        assert!(build_command("dan", "queue.noop", &json!({}), Some(""), "r", 1).unwrap()["args"].get("expect_queue_sig").is_none(), "an empty signature is not sent");
    }

    #[test]
    fn every_malformed_command_is_refused_before_it_can_be_sent() {
        let bad = [
            ("queue.move_singer", json!({"direction": "up"})),
            ("queue.move_singer", json!({"singer_id": "  ", "direction": "up"})),
            ("queue.move_singer", json!({"singer_id": "a", "direction": "sideways"})),
            ("queue.move_singer", json!({"singer_id": "a"})),
            ("queue.move_song", json!({"direction": "up"})),
            ("queue.move_song", json!({"entry_id": "e", "direction": 3})),
            ("queue.remove_singer", json!({})),
            ("queue.remove_song", json!({"entry_id": ""})),
            ("queue.skip_singer", json!({"singer_id": "a"})),
            ("queue.skip_singer", json!({"skipped": true})),
            ("queue.skip_singer", json!({"singer_id": "", "skipped": true})),
            ("queue.skip_singer", json!({"singer_id": "a", "skipped": "yes"})),
            ("set_key", json!({"value": 1.5})),
            ("set_key", json!({"value": 13})),
            ("set_tempo", json!({"value": 0})),
            ("set_tempo", json!({"value": 1000})),
            ("set_bg_volume", json!({"value": 1.5})),
            ("set_karaoke_volume", json!({"value": -0.1})),
            ("set_karaoke_volume", json!({})),
            ("seek_to", json!({"seconds": -1})),
            ("seek_to", json!({"seconds": "10"})),
            ("queue.add_song", json!({"song_id": 5})),
            ("queue.add_song", json!({"singer": "Dana"})),
            ("queue.add_song", json!({"singer": "  ", "song_id": 5})),
            ("queue.add_song", json!({"singer": "x".repeat(61), "song_id": 5})),
            ("queue.add_song", json!({"singer": "Da\nna", "song_id": 5})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 0})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": -3})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 5.5})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": "5"})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 5, "key": 5})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 5, "key": -5})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 5, "key": 1.5})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 5, "key": "1"})),
            ("queue.explode", json!({})),
            ("rm -rf", json!({})),
            ("play_pause", json!([1, 2])),
        ];
        for (c, a) in bad {
            assert!(validate_command(c, &a).is_err(), "{c} {a} must be refused");
        }
        for (c, a) in [
            ("set_key", json!({"value": -12})),
            ("set_key", json!({"value": 3.0})),
            ("set_tempo", json!({"value": 1.1})),
            ("set_tempo", json!({"value": 95})),
            ("set_bg_volume", json!({"value": 0.0})),
            ("set_karaoke_volume", json!({"value": 1})),
            ("seek_to", json!({"seconds": 0})),
            ("queue.skip_singer", json!({"singer_id": "a", "skipped": false})),
            ("queue.add_song", json!({"singer": "Dana", "song_id": 1})),
            ("queue.add_song", json!({"singer": " Amber & Delores ", "song_id": 133158, "key": -4})),
            ("queue.add_song", json!({"singer": "Mom \u{200d}\u{1F469}", "song_id": 7, "key": 4})),
            ("queue.add_song", json!({"singer": "x".repeat(60), "song_id": 9, "key": 0})),
            ("queue.move_song", json!({"entry_id": "local:x", "direction": "bottom"})),
        ] {
            assert!(validate_command(c, &a).is_ok(), "{c} {a} must be accepted");
        }
    }

    #[test]
    fn state_queue_ack_and_error_messages_parse() {
        let st = parse_incoming(r#"{"type":"state","user":"dan","app_version":"1.0.1.4","connected":true,"updated_at":5,"queue_sig":"ab","rotation":{"current":{"singer":"X"}},"playback":{"is_playing":true}}"#);
        match st {
            Incoming::State(s) => {
                assert!(s.connected);
                assert_eq!((s.app_version.as_str(), s.queue_sig.as_deref(), s.updated_at), ("1.0.1.4", Some("ab"), 5));
                assert_eq!(s.rotation["current"]["singer"], "X");
            }
            other => panic!("{other:?}"),
        }
        let q = parse_incoming(r#"{"type":"queue","queue_sig":"zz","singers":[{"singer_id":"a","name":"A","skipped":true,"marker":false,"songs":[{"entry_id":"e1","artist":"X","title":"T","skipped":false},5]},3]}"#);
        match q {
            Incoming::Queue(q) => {
                assert_eq!(q.queue_sig, "zz");
                assert_eq!(q.singers.len(), 1, "non-object singers are dropped");
                assert!(q.singers[0].skipped);
                assert_eq!(q.singers[0].songs, vec![SongRow { entry_id: "e1".into(), artist: "X".into(), title: "T".into(), skipped: false }]);
            }
            other => panic!("{other:?}"),
        }
        assert_eq!(parse_incoming(r#"{"type":"ack","request_id":"r1","ok":false,"message":"stale_view"}"#), Incoming::Ack(Ack { request_id: "r1".into(), ok: false, message: "stale_view".into() }));
        assert_eq!(
            parse_incoming(r#"{"type":"error","request_id":"r2","code":"app_offline","message":"no app connected"}"#),
            Incoming::Error { request_id: Some("r2".into()), code: "app_offline".into(), message: "no app connected".into() }
        );
        assert_eq!(parse_incoming(r#"{"type":"hello","user":"dan","role":"browser"}"#), Incoming::Hello { role: "browser".into() });
        match parse_incoming(r#"{"type":"playback_tick","position_seconds":1.5,"duration_seconds":200,"remaining_seconds":198.5,"is_playing":true}"#) {
            Incoming::Tick { position, duration, remaining, playing } => assert_eq!((position, duration, remaining, playing), (1.5, 200.0, 198.5, true)),
            other => panic!("{other:?}"),
        }
        assert_eq!(parse_incoming(r#"{"type":"novel"}"#), Incoming::Other("novel".into()));
        assert_eq!(parse_incoming(r#"{"no":"type"}"#), Incoming::Other(String::new()));
        for junk in ["", "not json", "[1,2]", "null", "5"] {
            assert_eq!(parse_incoming(junk), Incoming::NotJson, "{junk:?}");
        }
        match parse_incoming(r#"{"type":"state","rotation":{},"playback":{}}"#) {
            Incoming::State(s) => assert!(s.connected, "a state frame that does not say otherwise comes from a live app"),
            other => panic!("{other:?}"),
        }
        // a state with nothing in it is still a state, and a relay "app offline" state says so
        match parse_incoming(r#"{"type":"state","connected":false,"rotation":{},"playback":{}}"#) {
            Incoming::State(s) => assert!(!s.connected),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn the_browser_url_encodes_the_user_and_token() {
        assert_eq!(browser_url("ws://h:1/relay", "dan", "a.b-c"), "ws://h:1/relay?role=browser&channel=host&user=dan&token=a.b-c&app_version=1.0.1.5");
        assert_eq!(browser_url("wss://h/relay?x=1", "d an", "t/+="), "wss://h/relay?x=1&role=browser&channel=host&user=d%20an&token=t%2F%2B%3D&app_version=1.0.1.5");
    }
}
