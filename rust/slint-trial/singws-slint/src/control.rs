//! The one interactive pattern of the Slint trial: PIN login and a transport button through the EXISTING control protocol (`singws-control`: the relay client and the
//! view model are the Qt shell's own; this file only owns the connection and forwards its view to Slint). The PIN is handed to the client and never stored or logged;
//! only the server address and tenant are remembered (the same `connection.json` the Qt shell writes; `SINGWS_SHELL_CONFIG_DIR` moves it, which the tests do).
use serde_json::{Value, json};
use singws_control::{Config, Handle, ViewModel, new_request_id, spawn};
use std::sync::{Arc, Mutex};

pub struct Control {
    handle: Option<Handle>,
    vm: Arc<Mutex<ViewModel>>,
}

/// What the page draws, flattened from the view model's JSON.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct ControlView {
    pub status: String, pub enabled: bool, pub logged_in: bool, pub login_error: String,
    pub now: String, pub clock: String, pub playing: bool, pub result_text: String, pub result_kind: String,
    pub bg_playing: bool, pub bg_volume: f64, pub bg_track: String, pub bg_index: i64,
    pub chat: Value,
}

pub fn config_path() -> std::path::PathBuf {
    let dir = std::env::var("SINGWS_SHELL_CONFIG_DIR").map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/Application Support/SingWS-Shell"));
    dir.join("connection.json")
}
pub fn load_saved() -> (String, String) {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(config_path()).unwrap_or_default()).unwrap_or(Value::Null);
    (v.get("url").and_then(Value::as_str).unwrap_or("").into(), v.get("user").and_then(Value::as_str).unwrap_or("").into())
}
fn save(url: &str, user: &str) {
    let path = config_path();
    if let Some(d) = path.parent() { let _ = std::fs::create_dir_all(d); }
    let _ = std::fs::write(path, json!({"url": url, "user": user}).to_string());
}

fn clock(s: f64) -> String { let t = s.max(0.0) as i64; format!("{}:{:02}", t / 60, t % 60) }

pub fn flatten(v: &Value) -> ControlView {
    let pb = &v["playback"];
    let cur = &v["rotation"]["current"];
    let singer = cur["singer"].as_str().unwrap_or("");
    let title = pb["title"].as_str().filter(|t| !t.is_empty()).or(cur["title"].as_str()).unwrap_or("");
    let dur = pb["duration"].as_f64().unwrap_or(0.0);
    ControlView {
        status: v["status"].as_str().unwrap_or("").into(), enabled: v["enabled"].as_bool().unwrap_or(false), logged_in: v["logged_in"].as_bool().unwrap_or(false),
        login_error: v["login_error"].as_str().unwrap_or("").into(),
        now: if singer.is_empty() { "Nobody is singing".into() } else { format!("{singer} — {title}") },
        clock: if dur > 0.0 { format!("{} / {}   Key {:+}   Tempo {}%", clock(pb["position"].as_f64().unwrap_or(0.0)), clock(dur), pb["key"].as_i64().unwrap_or(0), (pb["tempo"].as_f64().unwrap_or(1.0) * 100.0).round() as i64) } else { "No song loaded".into() },
        playing: pb["playing"].as_bool().unwrap_or(false),
        bg_playing: v["bg"]["playing"].as_bool().unwrap_or(false), bg_volume: v["bg"]["volume"].as_f64().unwrap_or(0.0), bg_track: v["bg"]["track"].as_str().unwrap_or("").into(), bg_index: v["bg"]["index"].as_i64().unwrap_or(-1),
        chat: v.get("chat").cloned().unwrap_or(Value::Null),
        result_text: v["result"]["text"].as_str().unwrap_or("").into(), result_kind: v["result"]["kind"].as_str().unwrap_or("").into(),
    }
}

impl Control {
    pub fn new() -> Self {
        let mut vm = ViewModel::new();
        vm.status = "Not connected".into();
        Control { handle: None, vm: Arc::new(Mutex::new(vm)) }
    }
    pub fn view(&self) -> ControlView { flatten(&self.vm.lock().unwrap().to_json()) }

    /// Log in and keep the connection; `on_change` runs on the relay thread whenever the view changed (the caller hops to the UI thread).
    pub fn connect(&mut self, url: &str, user: &str, pin: &str, on_change: impl Fn() + Send + 'static) {
        self.disconnect();
        let (url, user) = (url.trim().to_string(), user.trim().to_string());
        let vm = Arc::new(Mutex::new(ViewModel::new()));
        vm.lock().unwrap().status = "Connecting…".into();
        self.vm = vm.clone();
        save(&url, &user);
        self.handle = Some(spawn(Config::new(url, user, pin.to_string()), move |out| { vm.lock().unwrap().apply(&out); on_change(); }));
    }
    /// The thumbnail data address of a chat picture/GIF ("" until the app has sent it).
    pub fn media_uri(&self, media_ref: &str) -> String { self.vm.lock().unwrap().media_uri(media_ref) }
    /// Ask the app for a thumbnail (`chat.media`, read-only, no result banner).
    pub fn request_media(&self, media_ref: &str) {
        if !singws_control::protocol::valid_media_ref(media_ref) { return; }
        if let Some(h) = &self.handle { let _ = h.send("chat.media", &json!({"ref": media_ref}), None); }
    }
    pub fn disconnect(&mut self) {
        if let Some(h) = self.handle.take() { h.stop(); }
        let mut vm = ViewModel::new();
        vm.status = "Not connected".into();
        self.vm = Arc::new(Mutex::new(vm));
    }
    /// Send one command by name (`play_pause`, `skip`, `stop`, `restart`, ...). The result banner comes back through the view model's acks.
    pub fn send(&self, label: &str, command: &str) { self.send_args(label, command, &json!({})); }
    pub fn send_args(&self, label: &str, command: &str, args: &Value) {
        let id = new_request_id();
        self.vm.lock().unwrap().note_sent(&id, label);
        let res = match &self.handle { Some(h) => h.send_with_id(command, args, None, &id).map(|_| ()).map_err(|e| e.to_string()), None => Err("not connected to the relay".into()) };
        if let Err(e) = res {
            let mut v = self.vm.lock().unwrap();
            v.forget(&id);
            v.note_failure(&format!("{label}: {}", if e.starts_with("not connected") { "not connected to the app" } else { e.as_str() }));
        }
    }
}

/// `data:image/jpeg;base64,....` -> the bytes (standard alphabet, padding optional); None for anything else.
pub fn decode_data_uri(uri: &str) -> Option<Vec<u8>> {
    let b64 = uri.split_once(";base64,")?.1;
    let mut out = Vec::with_capacity(b64.len() * 3 / 4);
    let (mut acc, mut bits) = (0u32, 0u32);
    for ch in b64.bytes() {
        let v = match ch { b'A'..=b'Z' => ch - b'A', b'a'..=b'z' => ch - b'a' + 26, b'0'..=b'9' => ch - b'0' + 52, b'+' => 62, b'/' => 63, b'=' | b'\n' | b'\r' => continue, _ => return None } as u32;
        acc = (acc << 6) | v; bits += 6;
        if bits >= 8 { bits -= 8; out.push((acc >> bits) as u8); acc &= (1 << bits) - 1; }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    #[test]
    fn data_uris_decode() {
        assert_eq!(super::decode_data_uri("data:image/jpeg;base64,SGVsbG8=").unwrap(), b"Hello");
        assert_eq!(super::decode_data_uri("data:image/jpeg;base64,SGVsbG8").unwrap(), b"Hello");
        assert!(super::decode_data_uri("nope").is_none());
        assert!(super::decode_data_uri("data:x;base64,@@").is_none());
    }
    use super::*;
    #[test]
    fn a_fresh_client_is_not_connected_and_a_send_says_so() {
        let c = Control::new();
        assert_eq!(c.view().status, "Not connected");
        assert!(!c.view().enabled);
        c.send("Play/pause", "play_pause");
        assert!(c.view().result_text.contains("not connected to the app"), "{:?}", c.view());
    }
    #[test]
    fn the_view_is_flattened_from_the_view_model_json() {
        let v = flatten(&json!({"status":"Connected","enabled":true,"logged_in":true,"playback":{"title":"Radio","playing":true,"position":65.0,"duration":200.0,"key":2,"tempo":1.0},"rotation":{"current":{"singer":"Ann","title":"x"}},"result":{"text":"ok","kind":"ok"}}));
        assert_eq!((v.now.as_str(), v.clock.as_str(), v.playing), ("Ann — Radio", "1:05 / 3:20   Key +2   Tempo 100%", true));
        assert_eq!(flatten(&json!({})).now, "Nobody is singing");
    }
    #[test]
    fn a_bad_server_ends_in_a_visible_failure_not_a_hang() {
        // never touch the operator's real saved connection
        let scratch = std::env::temp_dir().join(format!("singws-slint-cfg-{}", std::process::id()));
        unsafe { std::env::set_var("SINGWS_SHELL_CONFIG_DIR", &scratch); }
        let mut c = Control::new();
        let (tx, rx) = std::sync::mpsc::channel();
        c.connect("http://127.0.0.1:9", "nobody", "0000", move || { let _ = tx.send(()); });
        rx.recv_timeout(std::time::Duration::from_secs(20)).expect("the client reports something");
        std::thread::sleep(std::time::Duration::from_millis(300));
        let v = c.view();
        assert!(!v.enabled && !v.logged_in, "{v:?}");
        c.disconnect();
        let _ = std::fs::remove_dir_all(&scratch);
    }
}
