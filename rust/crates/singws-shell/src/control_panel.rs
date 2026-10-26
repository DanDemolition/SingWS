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
        #[qproperty(QString, preview_url, cxx_name = "previewUrl")]
        #[qproperty(QString, preview_status, cxx_name = "previewStatus")]
        #[qproperty(bool, preview_on, cxx_name = "previewOn")]
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

        /// Switch the karaoke output preview on or off. Off means the server is no longer polled, so the app stops capturing frames for it.
        #[qinvokable]
        #[cxx_name = "setPreview"]
        fn set_preview(self: Pin<&mut ControlPanel>, on: bool);

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
use singws_control::{Config, Handle, PreviewEvent, PreviewHandle, ViewModel, new_request_id, spawn, spawn_preview};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

pub struct ControlPanelRust {
    view_json: QString,
    saved_url: QString,
    saved_user: QString,
    preview_url: QString,
    preview_status: QString,
    preview_on: bool,
    /// the preview poller runs beside the relay client; it only asks the server while `preview_wanted` is set
    preview: Option<PreviewHandle>,
    preview_wanted: Arc<AtomicBool>,
    preview_bytes: Arc<AtomicU64>,
    handle: Option<Handle>,
    vm: Arc<Mutex<ViewModel>>,
}

fn config_path() -> std::path::PathBuf {
    let dir = std::env::var("SINGWS_SHELL_CONFIG_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::path::PathBuf::from(std::env::var("HOME").unwrap_or_default()).join("Library/Application Support/SingWS-Shell"));
    dir.join("connection.json")
}

fn preview_dir() -> std::path::PathBuf {
    config_path().parent().map(|p| p.join("preview")).unwrap_or_else(|| std::path::PathBuf::from("preview"))
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
            preview_url: QString::default(),
            preview_status: QString::default(),
            preview_on: false,
            preview: None,
            preview_wanted: Arc::new(AtomicBool::new(false)),
            preview_bytes: Arc::new(AtomicU64::new(0)),
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
        let config = Config::new(url, user, pin.to_string());
        self.as_mut().start_preview(config.clone());
        let handle = spawn(config, move |out| {
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

    /// The poller is created with the connection and stays idle until the operator switches the preview on.
    fn start_preview(mut self: Pin<&mut Self>, config: Config) {
        let wanted = self.rust().preview_wanted.clone();
        wanted.store(*self.as_ref().preview_on(), Ordering::SeqCst);
        let bytes = self.rust().preview_bytes.clone();
        let counter = Arc::new(AtomicU64::new(0));
        let qt = self.qt_thread();
        let dir = preview_dir();
        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::create_dir_all(&dir);
        let handle = spawn_preview(config, wanted, move |event| {
            let update = match event {
                PreviewEvent::Frame { jpeg, .. } => {
                    // a new file name per frame, so the image item never shows a cached older picture
                    let n = counter.fetch_add(1, Ordering::SeqCst) + 1;
                    let file = dir.join(format!("frame-{n}.jpg"));
                    let tmp = dir.join("frame.tmp");
                    if std::fs::write(&tmp, &jpeg).is_err() || std::fs::rename(&tmp, &file).is_err() {
                        (None, "Preview: could not store the picture".to_string())
                    } else {
                        if n > 2 {
                            let _ = std::fs::remove_file(dir.join(format!("frame-{}.jpg", n - 2)));
                        }
                        bytes.store(jpeg.len() as u64, Ordering::SeqCst);
                        (Some(format!("file://{}", file.display())), String::new())
                    }
                }
                PreviewEvent::Waiting(text) => (Some(String::new()), text),
                PreviewEvent::Failed(text) => (None, text),
            };
            let _ = qt.queue(move |mut obj: Pin<&mut qobject::ControlPanel>| {
                if !*obj.as_ref().preview_on() {
                    return;
                }
                if let Some(url) = update.0 {
                    obj.as_mut().set_preview_url(QString::from(url.as_str()));
                }
                obj.as_mut().set_preview_status(QString::from(update.1.as_str()));
            });
        });
        self.as_mut().rust_mut().preview = Some(handle);
    }

    fn set_preview(mut self: Pin<&mut Self>, on: bool) {
        self.as_mut().set_preview_on(on);
        self.rust().preview_wanted.store(on, Ordering::SeqCst);
        if !on {
            self.as_mut().set_preview_url(QString::default());
            self.as_mut().set_preview_status(QString::default());
        } else {
            self.as_mut().set_preview_status(QString::from("Waiting for the first picture…"));
        }
    }

    fn end_session(mut self: Pin<&mut Self>) {
        if let Some(p) = self.as_mut().rust_mut().preview.take() {
            p.stop();
        }
        self.as_mut().set_preview_url(QString::default());
        self.as_mut().set_preview_status(QString::default());
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
        let mut view = self.rust().vm.lock().unwrap().to_json();
        // the preview is a shell concern (not the relay's view model); the snapshot test reads it from here
        view["preview"] = serde_json::json!({"on": self.preview_on, "url": self.preview_url.to_string(), "status": self.preview_status.to_string(), "bytes": self.rust().preview_bytes.load(Ordering::SeqCst)});
        let _ = std::fs::write(path.to_string(), view.to_string());
    }
}
