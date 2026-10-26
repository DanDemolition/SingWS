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
    }
}

use core::pin::Pin;
use cxx_qt_lib::QString;

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
    fn search(mut self: Pin<&mut Self>, query: &QString) {
        let q = query.to_string();
        let db = self.db_path().to_string();
        match singws_uimodel::search_with_fuzzy(std::path::Path::new(&db), &q, 500, Some(std::time::Duration::from_millis(1500))) {
            Ok(res) => {
                let rows: Vec<serde_like::Row> = res.rows.iter().map(serde_like::Row::from).collect();
                let status = if q.trim().is_empty() {
                    "Type to search your library".to_string()
                } else if res.rows.is_empty() {
                    "No songs match".to_string()
                } else if res.fuzzy_used {
                    format!("{} song{} (closest matches to what you typed)", res.rows.len(), if res.rows.len() == 1 { "" } else { "s" })
                } else {
                    format!("{} song{}", res.rows.len(), if res.rows.len() == 1 { "" } else { "s" })
                };
                self.as_mut().set_results_json(QString::from(serde_like::to_json(&rows).as_str()));
                self.as_mut().set_status(QString::from(status.as_str()));
            }
            Err(e) => {
                self.as_mut().set_results_json(QString::from("[]"));
                self.as_mut().set_status(QString::from(format!("Library unavailable: {e}").as_str()));
            }
        }
    }
}

/// Tiny JSON writer for the list rows (keeps `serde` out of the Qt crate's dependency list).
mod serde_like {
    use singws_uimodel::SongRow;

    pub struct Row {
        pub id: i64,
        pub artist: String,
        pub title: String,
        pub duration: String,
        pub kind: String,
    }

    impl From<&SongRow> for Row {
        fn from(r: &SongRow) -> Self {
            Row { id: r.id, artist: r.artist.clone(), title: r.title.clone(), duration: singws_uimodel::format_duration(r.duration_secs), kind: r.song_type.clone() }
        }
    }

    fn esc(s: &str) -> String {
        let mut o = String::with_capacity(s.len() + 2);
        o.push('"');
        for c in s.chars() {
            match c {
                '"' => o.push_str("\\\""),
                '\\' => o.push_str("\\\\"),
                c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
                c => o.push(c),
            }
        }
        o.push('"');
        o
    }

    pub fn to_json(rows: &[Row]) -> String {
        let items: Vec<String> = rows
            .iter()
            .map(|r| format!("{{\"id\":{},\"artist\":{},\"title\":{},\"duration\":{},\"kind\":{}}}", r.id, esc(&r.artist), esc(&r.title), esc(&r.duration), esc(&r.kind)))
            .collect();
        format!("[{}]", items.join(","))
    }
}
