//! QObject exposed to QML as `MusicPanel`: the READ-ONLY background-music playlist from the persisted `bg_playlist.json` (as of the app's last save) with the
//! app's own search rules. All rules live in `singws-uimodel::music`; the three settings shown in the status line are plain on/off/volume values.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, rows_json, cxx_name = "rowsJson")]
        #[qproperty(QString, count_label, cxx_name = "countLabel")]
        #[qproperty(QString, info)]
        #[qproperty(QString, status)]
        type MusicPanel = super::MusicPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut MusicPanel>, query: &QString, cutoff: i32);

        /// test hook (`--dump-music FILE`): write the rows the tab is showing
        #[qinvokable]
        #[cxx_name = "dumpRows"]
        fn dump_rows(self: &MusicPanel, path: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::music;

pub struct MusicPanelRust {
    rows_json: QString,
    count_label: QString,
    info: QString,
    status: QString,
    dir: String,
}

impl Default for MusicPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        MusicPanelRust { rows_json: QString::from("[]"), count_label: QString::from("0 tracks"), info: QString::from(""), status: QString::from(""), dir }
    }
}

fn read_json(path: &str) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

/// "Music on, autoplay when idle on, volume 65%" from the three plain settings; anything missing is left out.
fn info_line(dir: &str) -> String {
    let Ok(s) = read_json(&format!("{dir}/settings.json")) else { return String::new() };
    let onoff = |k: &str| s.get(k).and_then(Value::as_bool).map(|b| if b { "on" } else { "off" });
    let mut parts = Vec::new();
    if let Some(v) = onoff("bg_enabled") {
        parts.push(format!("Background music {v}"));
    }
    if let Some(v) = onoff("bg_autoplay_on_idle") {
        parts.push(format!("autoplay when idle {v}"));
    }
    if let Some(v) = s.get("bg_volume").and_then(Value::as_f64) {
        parts.push(format!("volume {}%", (v * 100.0).round() as i64));
    }
    parts.join("   •   ")
}

impl qobject::MusicPanel {
    fn dump_rows(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.rows_json().to_string());
    }

    fn refresh(mut self: Pin<&mut Self>, query: &QString, cutoff: i32) {
        let dir = self.rust().dir.clone();
        let info = info_line(&dir);
        self.as_mut().set_info(QString::from(info.as_str()));
        let path = format!("{dir}/bg_playlist.json");
        if !std::path::Path::new(&path).exists() {
            self.as_mut().set_rows_json(QString::from("[]"));
            self.as_mut().set_count_label(QString::from("0 tracks"));
            self.as_mut().set_status(QString::from("No background-music playlist saved yet"));
            return;
        }
        let result = read_json(&path).and_then(|raw| music::parse_playlist(&raw)).and_then(|rows| music::view_from(&rows, &query.to_string(), i64::from(cutoff)));
        match result {
            Ok(v) => {
                let rows: Vec<Value> = v.rows.iter().map(|r| json!({"filename": r.filename, "path": r.path})).collect();
                self.as_mut().set_rows_json(QString::from(Value::Array(rows).to_string().as_str()));
                self.as_mut().set_count_label(QString::from(v.count_label.as_str()));
                self.as_mut().set_status(QString::from(if v.total == 0 { "The playlist is empty" } else if v.shown == 0 { "No tracks match" } else { "" }));
            }
            Err(e) => {
                self.as_mut().set_rows_json(QString::from("[]"));
                self.as_mut().set_count_label(QString::from("0 tracks"));
                self.as_mut().set_status(QString::from(format!("The playlist could not be read: {e}").as_str()));
            }
        }
    }
}
