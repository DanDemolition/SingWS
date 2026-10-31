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
                let providers = singws_uimodel::providers_for(std::path::Path::new(&db), &res.rows.iter().map(|r| r.id).collect::<Vec<_>>());
                let rows: Vec<serde_like::Row> = res.rows.iter().map(|r| serde_like::Row::with_provider(r, providers.get(&r.id).map(String::as_str))).collect();
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
        /// the app's own row text (`_build_search_row_text`): left "Artist • Title", right "disc  badge  m:ss", and the tooltip
        pub left: String,
        pub right: String,
        pub tooltip: String,
    }

    impl From<&SongRow> for Row {
        fn from(r: &SongRow) -> Self {
            Row::with_provider(r, None)
        }
    }

    impl Row {
        pub fn with_provider(r: &SongRow, provider: Option<&str>) -> Self {
            let duration = singws_uimodel::format_duration(r.duration_secs);
            let (left, right, tooltip) = match singws_uimodel::rows::library_row(r, provider) {
                Ok(row) => (row.left, row.right, row.tooltip),
                // a row the app's rules cannot reproduce exactly (declined or raising): the plain text this page always had
                Err(_) => (if r.artist.is_empty() { r.title.clone() } else { format!("{} \u{2022} {}", r.artist, r.title) }, duration.clone(), String::new()),
            };
            Row { id: r.id, artist: r.artist.clone(), title: r.title.clone(), duration, kind: r.song_type.clone(), left, right, tooltip }
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
            .map(|r| format!("{{\"id\":{},\"artist\":{},\"title\":{},\"duration\":{},\"kind\":{},\"left\":{},\"right\":{},\"tooltip\":{}}}", r.id, esc(&r.artist), esc(&r.title), esc(&r.duration), esc(&r.kind), esc(&r.left), esc(&r.right), esc(&r.tooltip)))
            .collect();
        format!("[{}]", items.join(","))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use singws_uimodel::SongRow;

    fn row(path: &str, discid: &str, dur: Option<i64>, kind: &str) -> SongRow {
        SongRow { id: 1, path: path.into(), artist: "Queen".into(), title: "Radio Ga Ga".into(), discid: discid.into(), duration_secs: dur, song_type: kind.into(), display: "Queen - Radio Ga Ga".into() }
    }

    #[test]
    fn a_library_row_says_what_the_apps_search_row_says() {
        let r = singws_uimodel::rows::library_row(&row("/lib/SC8671-01.zip", "SC8671", Some(212), ""), None).unwrap();
        assert_eq!((r.left.as_str(), r.right.as_str()), ("Queen \u{2022} Radio Ga Ga", "SC8671  ZIP  03:32"));
        assert_eq!(r.tooltip, "Queen \u{2022} Radio Ga Ga\nFormat: ZIP  Disc: SC8671  Duration: 03:32\nFile: SC8671-01.zip");
        let r = singws_uimodel::rows::library_row(&row("", "", None, "MP4"), Some("karafun_streaming")).unwrap();
        assert_eq!((r.left.as_str(), r.right.as_str()), ("Queen \u{2022} Radio Ga Ga", "KaraFun ONLINE  MP4"), "a provider other than local shows its badge, as in the app");
    }

    #[test]
    fn the_rows_json_carries_the_app_text_beside_the_old_fields() {
        let rows = vec![serde_like::Row::from(&row("/lib/a.mp3", "X1", Some(61), "mp3g"))];
        let json = serde_like::to_json(&rows);
        let v: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(v[0]["artist"], "Queen");
        assert_eq!(v[0]["duration"], "01:01");
        assert_eq!(v[0]["left"], "Queen \u{2022} Radio Ga Ga");
        assert_eq!(v[0]["right"], "X1  MP3+G  01:01");
    }
}
