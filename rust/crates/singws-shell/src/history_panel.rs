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

        #[qinvokable]
        #[cxx_name = "selectSinger"]
        fn select_singer(self: Pin<&mut HistoryPanel>, singer_key: &QString, song_query: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use singws_uimodel::history::{self, Row, Store};
use std::time::SystemTime;

pub struct HistoryPanelRust {
    singers_json: QString,
    songs_json: QString,
    singer_name: QString,
    summary: QString,
    song_meta: QString,
    status: QString,
    path: String,
    store: Option<Store>,
    mtime: Option<SystemTime>,
}

impl Default for HistoryPanelRust {
    fn default() -> Self {
        let path = std::env::var("SINGWS_HISTORY").unwrap_or_else(|_| {
            let home = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
            format!("{home}/singer_history.json")
        });
        HistoryPanelRust {
            singers_json: QString::from("[]"),
            songs_json: QString::from("[]"),
            singer_name: QString::from("Select a singer"),
            summary: QString::from("No singer selected."),
            song_meta: QString::from("0 entries"),
            status: QString::from(""),
            path,
            store: None,
            mtime: None,
        }
    }
}

fn esc(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

fn rows_json(rows: &[Row]) -> String {
    let items: Vec<String> = rows
        .iter()
        .map(|r| {
            let mut lines = r.text.splitn(2, '\n');
            let first = lines.next().unwrap_or("");
            let rest = lines.next().unwrap_or("");
            format!("{{\"key\":{},\"title\":{},\"detail\":{},\"selectable\":{}}}", esc(&r.key), esc(first), esc(rest), r.selectable)
        })
        .collect();
    format!("[{}]", items.join(","))
}

fn now_epoch() -> i64 {
    SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

impl qobject::HistoryPanel {
    /// (Re)load the store if the file changed; `Err` carries the text to show.
    fn ensure_store(mut self: Pin<&mut Self>) -> Result<(), String> {
        let path = self.rust().path.clone();
        let mtime = std::fs::metadata(&path).and_then(|m| m.modified()).ok();
        let stale = self.rust().store.is_none() || self.rust().mtime != mtime;
        if stale {
            match history::load(std::path::Path::new(&path)) {
                Ok(store) => {
                    let mut rust = self.as_mut().rust_mut();
                    rust.store = Some(store);
                    rust.mtime = mtime;
                }
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }

    fn refresh(mut self: Pin<&mut Self>, singer_search: &QString) {
        if let Err(e) = self.as_mut().ensure_store() {
            self.as_mut().set_singers_json(QString::from("[]"));
            self.as_mut().set_status(QString::from(format!("Singer history unavailable: {e}").as_str()));
            return;
        }
        let dir = history::directory(self.rust().store.as_ref().unwrap(), &singer_search.to_string(), 700, 500);
        match dir {
            Ok(d) => {
                let status = format!("{} singer{}", d.total_matches, if d.total_matches == 1 { "" } else { "s" });
                self.as_mut().set_singers_json(QString::from(rows_json(&d.rows).as_str()));
                self.as_mut().set_status(QString::from(status.as_str()));
            }
            Err(e) => {
                self.as_mut().set_singers_json(QString::from("[]"));
                self.as_mut().set_status(QString::from(format!("Could not list singers: {e}").as_str()));
            }
        }
    }

    fn select_singer(mut self: Pin<&mut Self>, singer_key: &QString, song_query: &QString) {
        if self.as_mut().ensure_store().is_err() {
            return;
        }
        let key = singer_key.to_string();
        let (name, summary) = history::details(self.rust().store.as_ref().unwrap(), &key);
        let view = history::songs_view(self.rust().store.as_ref().unwrap(), &key, &song_query.to_string(), 500, now_epoch());
        self.as_mut().set_singer_name(QString::from(name.as_str()));
        self.as_mut().set_summary(QString::from(summary.as_str()));
        match view {
            Ok(v) => {
                self.as_mut().set_songs_json(QString::from(rows_json(&v.rows).as_str()));
                self.as_mut().set_song_meta(QString::from(v.meta.as_str()));
            }
            Err(e) => {
                self.as_mut().set_songs_json(QString::from("[]"));
                self.as_mut().set_song_meta(QString::from(format!("Could not list songs: {e}").as_str()));
            }
        }
    }
}
