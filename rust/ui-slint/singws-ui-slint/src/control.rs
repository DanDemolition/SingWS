//! Command channel: the same relay client the Qt shell uses (`singws-control`: PIN login, websocket token, role=browser relay client, command validation). The app stays the
//! single writer; this only sends intents (`waitlist.*`, `settings.set`) and shows the app's answer. The PIN is passed to the client and never stored; only the server
//! address and tenant name are remembered, in the same `connection.json` the Qt shell uses.
use serde_json::{Value, json};
use singws_control::{Config, Handle, ViewModel, new_request_id, spawn, validate_command};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

/// What the window needs to know about the connection, refreshed on a timer
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ControlState {
    pub status: String,
    /// relay up AND the app on the other end: only then do the buttons work
    pub enabled: bool,
    /// the app's last answer: (text, kind ok|warn|error)
    pub result: Option<(String, String)>,
}

pub struct Controller {
    handle: Option<Handle>,
    vm: Arc<Mutex<ViewModel>>,
}

pub fn config_path() -> PathBuf {
    let dir = std::env::var("SINGWS_SHELL_CONFIG_DIR").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/Application Support/SingWS-Shell"));
    dir.join("connection.json")
}

/// the server address and tenant from the last run (never the PIN)
pub fn load_saved(path: &std::path::Path) -> (String, String) {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap_or_default()).unwrap_or(Value::Null);
    (v.get("url").and_then(Value::as_str).unwrap_or("").to_string(), v.get("user").and_then(Value::as_str).unwrap_or("").to_string())
}

pub fn save(path: &std::path::Path, url: &str, user: &str) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(path, json!({"url": url, "user": user}).to_string());
}

impl Controller {
    pub fn new() -> Self {
        let mut vm = ViewModel::new();
        vm.status = "Not connected".into();
        Controller { handle: None, vm: Arc::new(Mutex::new(vm)) }
    }

    pub fn connect(&mut self, url: &str, user: &str, pin: &str) {
        self.disconnect();
        let vm = Arc::new(Mutex::new(ViewModel::new()));
        vm.lock().unwrap().status = "Connecting…".into();
        self.vm = vm.clone();
        save(&config_path(), url.trim(), user.trim());
        let config = Config::new(url.trim().to_string(), user.trim().to_string(), pin.to_string());
        let handle = spawn(config, move |out| {
            let mut v = vm.lock().unwrap();
            v.apply(&out);
        });
        self.handle = Some(handle);
    }

    pub fn disconnect(&mut self) {
        if let Some(h) = self.handle.take() {
            h.stop();
        }
        let mut vm = ViewModel::new();
        vm.status = "Not connected".into();
        self.vm = Arc::new(Mutex::new(vm));
    }

    pub fn state(&self) -> ControlState {
        let v = self.vm.lock().unwrap().to_json();
        state_from_view(&v)
    }

    /// Send one command; `label` is the words the result banner uses. A refusal (validation, not connected) shows as an error result.
    pub fn send(&self, label: &str, command: &str, args: Value) {
        let id = new_request_id();
        self.vm.lock().unwrap().note_sent(&id, label);
        let r = match self.handle.as_ref() {
            Some(h) => h.send_with_id(command, &args, None, &id).map(|_| ()).map_err(|e| e.to_string()),
            None => Err("not connected to the relay".to_string()),
        };
        if let Err(e) = r {
            let mut v = self.vm.lock().unwrap();
            v.forget(&id);
            v.note_failure(&format!("{label}: {}", if e.starts_with("not connected") { "not connected to the app" } else { e.as_str() }));
        }
    }
}

impl Default for Controller {
    fn default() -> Self {
        Self::new()
    }
}

pub fn state_from_view(v: &Value) -> ControlState {
    let result = v.get("result").filter(|r| !r.is_null()).map(|r| (r["text"].as_str().unwrap_or("").to_string(), r["kind"].as_str().unwrap_or("").to_string()));
    ControlState { status: v["status"].as_str().unwrap_or("").to_string(), enabled: v["enabled"].as_bool().unwrap_or(false), result }
}

/// `waitlist.add` | `waitlist.remove` | `waitlist.restore` arguments
pub fn request_args(request_id: i64) -> Value {
    json!({"request_id": request_id})
}

/// `waitlist.edit` arguments: the same request keeps its id and place; the app tells the server
pub fn edit_args(request_id: i64, singer: &str, artist: &str, title: &str, key: i64, tempo: i64) -> Value {
    json!({"request_id": request_id, "singer": singer.trim(), "artist": artist.trim(), "title": title.trim(), "key": key, "tempo": tempo})
}

/// `settings.set` arguments for the two ticker keys
pub fn setting_args(key: &str, value: Value) -> Value {
    json!({"key": key, "value": value})
}

/// Check what the relay client would check before sending, so the window can refuse an empty singer or title without a round trip
pub fn check(command: &str, args: &Value) -> Result<(), String> {
    validate_command(command, args)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_command_the_window_builds_passes_the_relay_clients_validation() {
        assert!(check("waitlist.add", &request_args(5)).is_ok());
        assert!(check("waitlist.remove", &request_args(5)).is_ok());
        assert!(check("waitlist.restore", &request_args(5)).is_ok());
        assert!(check("waitlist.edit", &edit_args(5, " Ann ", "Queen", "Radio", -12, 30)).is_ok());
        assert!(check("settings.set", &setting_args("ticker_custom_message", json!("Tip your host"))).is_ok());
        assert!(check("settings.set", &setting_args("ticker_custom_enabled", json!(true))).is_ok());
    }

    #[test]
    fn bad_values_are_refused_before_sending() {
        assert!(check("waitlist.edit", &edit_args(5, "  ", "", "T", 0, 0)).is_err(), "singer is required");
        assert!(check("waitlist.edit", &edit_args(5, "A", "", "", 0, 0)).is_err(), "title is required");
        assert!(check("waitlist.edit", &edit_args(5, "A", "", "T", 13, 0)).is_err());
        assert!(check("waitlist.add", &request_args(0)).is_err());
        assert!(check("settings.set", &setting_args("ticker_custom_message", json!("x".repeat(201)))).is_err());
        assert!(check("settings.set", &setting_args("audio_output_name", json!("x"))).is_err(), "only reviewed keys can be set");
    }

    #[test]
    fn sending_while_not_connected_shows_an_error_result_not_a_panic() {
        let c = Controller::new();
        assert_eq!(c.state().status, "Not connected");
        c.send("Add Ann", "waitlist.add", request_args(5));
        let s = c.state();
        assert!(!s.enabled);
        assert!(s.result.is_some_and(|r| r.0.contains("not connected to the app")));
    }

    #[test]
    fn saved_connection_keeps_the_address_and_tenant_only() {
        let d = std::env::temp_dir().join(format!("singws-slint-conn-{}", std::process::id()));
        let p = d.join("connection.json");
        assert_eq!(load_saved(&p), (String::new(), String::new()));
        save(&p, "https://x.example", "wsk");
        assert_eq!(load_saved(&p), ("https://x.example".to_string(), "wsk".to_string()));
        assert!(!std::fs::read_to_string(&p).unwrap().to_lowercase().contains("pin"));
    }

    #[test]
    fn the_view_json_becomes_a_state() {
        let v = json!({"status": "Connected", "enabled": true, "result": {"seq": 1, "text": "Add Ann: Added", "kind": "ok"}});
        assert_eq!(state_from_view(&v), ControlState { status: "Connected".into(), enabled: true, result: Some(("Add Ann: Added".into(), "ok".into())) });
        assert_eq!(state_from_view(&json!({"result": null})).result, None);
    }
}
