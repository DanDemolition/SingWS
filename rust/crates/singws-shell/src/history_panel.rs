//! QObject exposed to QML as `HistoryPanel`: READ-ONLY singer history. The store is loaded from `singer_history.json` (reloaded when the file's
//! modification time changes), all rules live in the toolkit-free `singws-uimodel::history`. Rows travel to QML as JSON strings.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, singers_json, cxx_name = "singersJson")]
        #[qproperty(QString, songs_json, cxx_name = "songsJson")]
        #[qproperty(QString, singer_name, cxx_name = "singerName")]
        #[qproperty(QString, summary)]
        #[qproperty(QString, song_meta, cxx_name = "songMeta")]
        #[qproperty(QString, status)]
        type HistoryPanel = super::HistoryPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut HistoryPanel>, singer_search: &QString);

        /// The fields the Edit form starts from for one song, as JSON ("" when the singer or song is not in the store).
        #[qinvokable]
        #[cxx_name = "songFields"]
        fn song_fields(self: Pin<&mut HistoryPanel>, singer_key: &QString, song_key: &QString) -> QString;

        #[qinvokable]
        #[cxx_name = "selectSinger"]
        fn select_singer(self: Pin<&mut HistoryPanel>, singer_key: &QString, song_query: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::panels::{HistRow, HistoryLoader};
use std::time::SystemTime;

pub struct HistoryPanelRust {
    singers_json: QString,
    songs_json: QString,
    singer_name: QString,
    summary: QString,
    song_meta: QString,
    status: QString,
    loader: HistoryLoader,
}

impl Default for HistoryPanelRust {
    fn default() -> Self {
        let home = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        HistoryPanelRust {
            singers_json: QString::from("[]"),
            songs_json: QString::from("[]"),
            singer_name: QString::from("Select a singer"),
            summary: QString::from("No singer selected."),
            song_meta: QString::from("0 entries"),
            status: QString::from(""),
            loader: HistoryLoader::new(&home),
        }
    }
}

fn rows_json(rows: &[HistRow]) -> String {
    Value::Array(rows.iter().map(|r| json!({"key": r.key, "title": r.title, "detail": r.detail, "selectable": r.selectable})).collect()).to_string()
}

fn now_epoch() -> i64 {
    SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

impl qobject::HistoryPanel {
    /// The store (reloaded when the file changes), the directory and the song rows come from the shared `singws-uimodel::panels::HistoryLoader`, which the Slint trial uses too.
    fn refresh(mut self: Pin<&mut Self>, singer_search: &QString) {
        match self.as_mut().rust_mut().loader.singers(&singer_search.to_string()) {
            Ok((rows, status)) => {
                self.as_mut().set_singers_json(QString::from(rows_json(&rows).as_str()));
                self.as_mut().set_status(QString::from(status.as_str()));
            }
            Err(e) => {
                self.as_mut().set_singers_json(QString::from("[]"));
                self.as_mut().set_status(QString::from(e.as_str()));
            }
        }
    }

    /// The fields the Edit form starts from for one song, as JSON ("" when the singer or song is not in the store).
    fn song_fields(mut self: Pin<&mut Self>, singer_key: &QString, song_key: &QString) -> QString {
        QString::from(self.as_mut().rust_mut().loader.song_fields(&singer_key.to_string(), &song_key.to_string()).as_str())
    }

    fn select_singer(mut self: Pin<&mut Self>, singer_key: &QString, song_query: &QString) {
        let Some((name, summary, songs, meta)) = self.as_mut().rust_mut().loader.select(&singer_key.to_string(), &song_query.to_string(), now_epoch()) else { return };
        self.as_mut().set_singer_name(QString::from(name.as_str()));
        self.as_mut().set_summary(QString::from(summary.as_str()));
        self.as_mut().set_songs_json(QString::from(rows_json(&songs).as_str()));
        self.as_mut().set_song_meta(QString::from(meta.as_str()));
    }
}
