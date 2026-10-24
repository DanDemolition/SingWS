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
use serde_json::Value;
use singws_uimodel::rotation::{self, Inputs};
use std::time::SystemTime;

pub struct RotationPanelRust {
    rows_json: QString,
    count_label: QString,
    title_label: QString,
    now_strip: QString,
    next_strip: QString,
    status: QString,
    dir: String,
    queue: Option<Vec<Value>>,
    tracks: Option<Vec<Value>>,
    mtime: Option<SystemTime>,
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
            dir,
            queue: None,
            tracks: None,
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

impl qobject::RotationPanel {
    fn refresh(mut self: Pin<&mut Self>, now_singing: &QString) {
        let dir = self.rust().dir.clone();
        let queue_path = std::env::var("SINGWS_QUEUE").unwrap_or_else(|_| format!("{dir}/queue.json"));
        let mtime = std::fs::metadata(&queue_path).and_then(|m| m.modified()).ok();
        if self.rust().queue.is_none() || self.rust().mtime != mtime {
            match rotation::load_queue(std::path::Path::new(&queue_path)) {
                Ok(q) => {
                    let mut rust = self.as_mut().rust_mut();
                    rust.queue = Some(q);
                    rust.mtime = mtime;
                }
                Err(e) => {
                    self.as_mut().set_rows_json(QString::from("[]"));
                    self.as_mut().set_status(QString::from(format!("Rotation unavailable: {e}").as_str()));
                    return;
                }
            }
        }
        if self.rust().tracks.is_none() {
            let tracks = rotation::load_tracks(std::path::Path::new(&format!("{dir}/tracks.json"))).unwrap_or_default();
            self.as_mut().rust_mut().tracks = Some(tracks);
        }
        let db = std::path::PathBuf::from(format!("{dir}/singws.db"));
        let now = now_singing.to_string();
        let view = {
            let rust = self.rust();
            let inp = Inputs {
                db: Some(db.as_path()),
                tracks: rust.tracks.as_deref().unwrap_or(&[]),
                now_singing: if now.is_empty() { "Singer Rotation" } else { &now },
                playing: false,
                active_singer_id: "",
                live_title: "",
            };
            rotation::view(rust.queue.as_deref().unwrap_or(&[]), &inp)
        };
        match view {
            Ok(v) => {
                let items: Vec<String> = v
                    .rows
                    .iter()
                    .map(|r| format!("{{\"number\":{},\"singer\":{},\"song\":{},\"duet\":{}}}", esc(&r.number), esc(&r.singer), esc(&r.song), r.duet))
                    .collect();
                self.as_mut().set_rows_json(QString::from(format!("[{}]", items.join(",")).as_str()));
                self.as_mut().set_count_label(QString::from(v.count_label.as_str()));
                self.as_mut().set_title_label(QString::from(v.title_label.as_str()));
                self.as_mut().set_now_strip(QString::from(v.now_strip.as_str()));
                self.as_mut().set_next_strip(QString::from(v.next_strip.as_str()));
                self.as_mut().set_status(QString::from(if v.rows.is_empty() { "Nobody is waiting (as of the last save)" } else { "As of the app's last save" }));
            }
            Err(e) => {
                self.as_mut().set_rows_json(QString::from("[]"));
                self.as_mut().set_status(QString::from(format!("Could not build the rotation: {e}").as_str()));
            }
        }
    }
}
