//! QObject exposed to QML as `EqPanel`: the READ-ONLY view of the two equalisers (background music, karaoke) from `settings.json`, read exactly as the app reads them
//! (see `singws-uimodel::eq_view`). The settings file is looked at again when the tab asks (a timer in the page), so a change made in the app shows up within seconds.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, eq_json, cxx_name = "eqJson")]
        #[qproperty(QString, status)]
        type EqPanel = super::EqPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut EqPanel>);

        /// test hook (`--dump-eq FILE`): write what the tab is showing
        #[qinvokable]
        #[cxx_name = "dumpEq"]
        fn dump_eq(self: &EqPanel, path: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::{eq_view, panels};

pub struct EqPanelRust {
    eq_json: QString,
    status: QString,
    dir: String,
}

impl Default for EqPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        EqPanelRust { eq_json: QString::from("{}"), status: QString::from(""), dir }
    }
}

fn one(e: &panels::EqOne) -> Value {
    json!({"gains": e.gains, "enabled": e.enabled, "flat": e.flat, "auto_enabled": e.auto_enabled})
}

impl qobject::EqPanel {
    fn dump_eq(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.eq_json().to_string());
    }

    /// The two equalisers come from the shared `singws-uimodel::panels::eq_data` (also used by the Slint trial); the presets are the app's table in `eq_view`.
    fn refresh(mut self: Pin<&mut Self>) {
        let d = panels::eq_data(&self.rust().dir.clone());
        if !d.status.is_empty() {
            self.as_mut().set_eq_json(QString::from("{}"));
            self.as_mut().set_status(QString::from(d.status.as_str()));
            return;
        }
        let presets: Vec<Value> = eq_view::PRESETS.iter().map(|(n, g)| json!({"name": n, "gains": g.to_vec()})).collect();
        let doc = json!({"bands": eq_view::BANDS_HZ.to_vec(), "presets": presets, "bgm": one(&d.bgm), "karaoke": one(&d.karaoke)});
        self.as_mut().set_eq_json(QString::from(doc.to_string().as_str()));
        self.as_mut().set_status(QString::from(""));
    }
}
