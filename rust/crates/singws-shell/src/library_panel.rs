//! QObject exposed to QML as `LibraryPanel`: a debounced-by-QML search box over the READ-ONLY song index. Results travel to QML as one JSON
//! string (an array of rows); the model logic itself lives in the toolkit-free `singws-uimodel` crate.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, results_json, cxx_name = "resultsJson")]
        #[qproperty(QString, status)]
        #[qproperty(QString, db_path, cxx_name = "dbPath")]
        type LibraryPanel = super::LibraryPanelRust;

        #[qinvokable]
        fn search(self: Pin<&mut LibraryPanel>, query: &QString);

        /// test hook (`--dump-library FILE`): write the rows the page is showing
        #[qinvokable]
        #[cxx_name = "dumpRows"]
        fn dump_rows(self: &LibraryPanel, path: &QString);
    }
}

use core::pin::Pin;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::panels;

pub struct LibraryPanelRust {
    results_json: QString,
    status: QString,
    db_path: QString,
}

impl Default for LibraryPanelRust {
    fn default() -> Self {
        let path = std::env::var("SINGWS_DB").unwrap_or_else(|_| {
            let home = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
            format!("{home}/singws.db")
        });
        LibraryPanelRust { results_json: QString::from("[]"), status: QString::from("Type to search your library"), db_path: QString::from(path.as_str()) }
    }
}

impl qobject::LibraryPanel {
    fn dump_rows(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.results_json().to_string());
    }

    /// The search, the row text and the status wording come from the shared `singws-uimodel::panels::library_view` (also used by the Slint trial).
    fn search(mut self: Pin<&mut Self>, query: &QString) {
        let db = self.db_path().to_string();
        let d = panels::library_view(&db, &query.to_string());
        let rows: Vec<Value> = d.rows.iter().map(|r| json!({"id": r.id, "artist": r.artist, "title": r.title, "duration": r.duration, "kind": r.kind, "left": r.left, "right": r.right, "tooltip": r.tooltip})).collect();
        self.as_mut().set_results_json(QString::from(Value::Array(rows).to_string().as_str()));
        self.as_mut().set_status(QString::from(d.status.as_str()));
    }
}
