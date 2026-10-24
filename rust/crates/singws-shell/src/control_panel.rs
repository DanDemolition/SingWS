//! QObject exposed to QML as `ControlPanel`: the live remote controls. All rules live in `singws-control` (the relay client and the pure view-model); this
//! object only owns the connection, forwards the client's outputs to the Qt thread as one JSON snapshot (`viewJson`), and sends commands the UI asks for.
//! The PIN is passed to the client and never stored; only the server address and the tenant name are remembered between runs.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, view_json, cxx_name = "viewJson")]
        #[qproperty(QString, saved_url, cxx_name = "savedUrl")]
        #[qproperty(QString, saved_user, cxx_name = "savedUser")]
        type ControlPanel = super::ControlPanelRust;

        #[qinvokable]
        #[cxx_name = "connectTo"]
        fn connect_to(self: Pin<&mut ControlPanel>, url: &QString, user: &QString, pin: &QString);

        #[qinvokable]
        #[cxx_name = "endSession"]
        fn end_session(self: Pin<&mut ControlPanel>);

        #[qinvokable]
        fn send(self: Pin<&mut ControlPanel>, label: &QString, command: &QString, args_json: &QString, with_sig: bool);

        /// Test hook: like `send`, but with an explicit queue fingerprint (to prove a stale edit is refused and refreshed).
        #[qinvokable]
        #[cxx_name = "sendWithSig"]
        fn send_with_sig(self: Pin<&mut ControlPanel>, label: &QString, command: &QString, args_json: &QString, sig: &QString);

        #[qinvokable]
        #[cxx_name = "dumpView"]
        fn dump_view(self: &ControlPanel, path: &QString);
    }

    impl cxx_qt::Threading for ControlPanel {}
}

use core::pin::Pin;
use cxx_qt::{CxxQtType, Threading};
use cxx_qt_lib::QString;
use serde_json::Value;
use singws_control::{Config, Handle, ViewModel, new_request_id, spawn};
use std::sync::{Arc, Mutex};

pub struct ControlPanelRust {
    view_json: QString,
    saved_url: QString,
    saved_user: QString,
    handle: Option<Handle>,
    vm: Arc<Mutex<ViewModel>>,
}

fn config_path() -> std::path::PathBuf {
    let dir = std::env::var("SINGWS_SHELL_CONFIG_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/Application Support/SingWS-Shell"));
    dir.join("connection.json")
}

/// the server address and tenant from the last run (never the PIN)
fn load_saved() -> (String, String) {
    let text = std::fs::read_to_string(config_path()).unwrap_or_default();
    let v: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    (v.get("url").and_then(Value::as_str).unwrap_or("").to_string(), v.get("user").and_then(Value::as_str).unwrap_or("").to_string())
}

fn save(url: &str, user: &str) {
    let path = config_path();
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(path, serde_json::json!({"url": url, "user": user}).to_string());
}

impl Default for ControlPanelRust {
    fn default() -> Self {
        let (url, user) = load_saved();
        let vm = ViewModel::new();
        ControlPanelRust {
            view_json: QString::from(vm.to_json().to_string().as_str()),
            saved_url: QString::from(url.as_str()),
            saved_user: QString::from(user.as_str()),
            handle: None,
            vm: Arc::new(Mutex::new(vm)),
        }
    }
}

impl qobject::ControlPanel {
    fn publish(mut self: Pin<&mut Self>) {
        let json = self.rust().vm.lock().unwrap().to_json().to_string();
        self.as_mut().set_view_json(QString::from(json.as_str()));
    }

    fn connect_to(mut self: Pin<&mut Self>, url: &QString, user: &QString, pin: &QString) {
        self.as_mut().end_session();
        let (url, user) = (url.to_string().trim().to_string(), user.to_string().trim().to_string());
        let vm = Arc::new(Mutex::new(ViewModel::new()));
        vm.lock().unwrap().status = "Connecting…".into();
        self.as_mut().rust_mut().vm = vm.clone();
        self.as_mut().publish();
        save(&url, &user);
        self.as_mut().set_saved_url(QString::from(url.as_str()));
        self.as_mut().set_saved_user(QString::from(user.as_str()));
        let qt = self.qt_thread();
        let handle = spawn(Config::new(url, user, pin.to_string()), move |out| {
            let (json, refresh) = {
                let mut v = vm.lock().unwrap();
                v.apply(&out);
                (v.to_json().to_string(), v.take_refresh_request())
            };
            let _ = qt.queue(move |mut obj: Pin<&mut qobject::ControlPanel>| {
                obj.as_mut().set_view_json(QString::from(json.as_str()));
                if refresh {
                    // the list changed under the operator: ask the app to publish it again (any command makes it do so)
                    obj.as_mut().send_with("", "queue.noop", Value::Object(Default::default()), None);
                }
            });
        });
        self.as_mut().rust_mut().handle = Some(handle);
    }

    fn end_session(mut self: Pin<&mut Self>) {
        if let Some(h) = self.as_mut().rust_mut().handle.take() {
            h.stop();
        }
        let mut vm = ViewModel::new();
        vm.status = "Not connected".into();
        *self.rust().vm.lock().unwrap() = vm;
        self.as_mut().publish();
    }

    fn send_internal(self: Pin<&mut Self>, label: &str, command: &str, args: Value, with_sig: bool) {
        let sig = if with_sig { self.rust().vm.lock().unwrap().queue_sig.clone() } else { None };
        self.send_with(label, command, args, sig);
    }

    fn send_with(mut self: Pin<&mut Self>, label: &str, command: &str, args: Value, sig: Option<String>) {
        let vm = self.rust().vm.clone();
        let id = new_request_id();
        if label.is_empty() {
            vm.lock().unwrap().note_sent_silently(&id);
        } else {
            vm.lock().unwrap().note_sent(&id, label);
        }
        let result = match self.rust().handle.as_ref() {
            Some(h) => h.send_with_id(command, &args, sig.as_deref(), &id).map(|_| ()).map_err(|e| e.to_string()),
            None => Err("not connected to the relay".to_string()),
        };
        if let Err(e) = result {
            let mut v = vm.lock().unwrap();
            v.forget(&id);
            v.note_failure(&format!("{label}: {}", if e.starts_with("not connected") { "not connected to the app" } else { e.as_str() }));
        }
        self.as_mut().publish();
    }

    fn send(self: Pin<&mut Self>, label: &QString, command: &QString, args_json: &QString, with_sig: bool) {
        let args: Value = serde_json::from_str(&args_json.to_string()).unwrap_or(Value::Object(Default::default()));
        self.send_internal(&label.to_string(), &command.to_string(), args, with_sig);
    }

    fn send_with_sig(self: Pin<&mut Self>, label: &QString, command: &QString, args_json: &QString, sig: &QString) {
        let args: Value = serde_json::from_str(&args_json.to_string()).unwrap_or(Value::Object(Default::default()));
        self.send_with(&label.to_string(), &command.to_string(), args, Some(sig.to_string()));
    }

    fn dump_view(&self, path: &QString) {
        let json = self.rust().vm.lock().unwrap().to_json().to_string();
        let _ = std::fs::write(path.to_string(), json);
    }
}
