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
use singws_uimodel::panels;

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

impl qobject::MusicPanel {
    fn dump_rows(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.rows_json().to_string());
    }

    /// The playlist, the search and the info line come from the shared `singws-uimodel::panels::music_view` (also used by the Slint trial).
    fn refresh(mut self: Pin<&mut Self>, query: &QString, cutoff: i32) {
        let dir = self.rust().dir.clone();
        let d = panels::music_view(&dir, &query.to_string(), i64::from(cutoff));
        let rows: Vec<Value> = d.rows.iter().map(|r| json!({"index": r.index, "filename": r.filename, "path": r.path})).collect();
        self.as_mut().set_info(QString::from(d.info.as_str()));
        self.as_mut().set_rows_json(QString::from(Value::Array(rows).to_string().as_str()));
        self.as_mut().set_count_label(QString::from(d.count.as_str()));
        self.as_mut().set_status(QString::from(d.status.as_str()));
    }
}
