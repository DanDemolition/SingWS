//! QObject exposed to QML as `RotationPanel`: the READ-ONLY audience rotation, from the persisted `queue.json` (as of the app's last save) and
//! the song library. All rules live in `singws-uimodel::rotation`; the queue is re-read when its modification time changes.

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
        #[qproperty(QString, title_label, cxx_name = "titleLabel")]
        #[qproperty(QString, now_strip, cxx_name = "nowStrip")]
        #[qproperty(QString, next_strip, cxx_name = "nextStrip")]
        #[qproperty(QString, status)]
        type RotationPanel = super::RotationPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut RotationPanel>, now_singing: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::panels::RotationLoader;

pub struct RotationPanelRust {
    rows_json: QString,
    count_label: QString,
    title_label: QString,
    now_strip: QString,
    next_strip: QString,
    status: QString,
    loader: RotationLoader,
}

impl Default for RotationPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        RotationPanelRust {
            rows_json: QString::from("[]"),
            count_label: QString::from("0 SINGERS"),
            title_label: QString::from("SINGER ROTATION"),
            now_strip: QString::from(""),
            next_strip: QString::from(""),
            status: QString::from(""),
            loader: RotationLoader::new(&dir),
        }
    }
}

impl qobject::RotationPanel {
    /// Everything (queue, live state, the app's rules) comes from the shared `singws-uimodel::panels::RotationLoader`, which the Slint trial uses too.
    fn refresh(mut self: Pin<&mut Self>, now_singing: &QString) {
        let d = self.as_mut().rust_mut().loader.view(&now_singing.to_string());
        if !d.ok {
            // the queue could not be read or the view not built: the page keeps its labels and shows why
            self.as_mut().set_rows_json(QString::from("[]"));
            self.as_mut().set_status(QString::from(d.status.as_str()));
            return;
        }
        let rows: Vec<Value> = d.rows.iter().map(|r| json!({"number": r.number, "singer": r.singer, "song": r.song, "artist": r.artist, "combined": r.combined, "duet": r.duet})).collect();
        self.as_mut().set_rows_json(QString::from(Value::Array(rows).to_string().as_str()));
        self.as_mut().set_count_label(QString::from(d.count.as_str()));
        self.as_mut().set_title_label(QString::from(d.title.as_str()));
        self.as_mut().set_now_strip(QString::from(d.now.as_str()));
        self.as_mut().set_next_strip(QString::from(d.next.as_str()));
        self.as_mut().set_status(QString::from(d.status.as_str()));
    }
}
