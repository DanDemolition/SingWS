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
use singws_uimodel::eq_view::{self, Eq};

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

fn one(e: &Eq) -> Value {
    json!({"gains": e.gains.to_vec(), "enabled": e.enabled, "flat": e.flat, "auto_enabled": e.auto_enabled})
}

impl qobject::EqPanel {
    fn dump_eq(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.eq_json().to_string());
    }

    fn refresh(mut self: Pin<&mut Self>) {
        let path = format!("{}/settings.json", self.rust().dir);
        let result = std::fs::read_to_string(&path)
            .map_err(|e| e.to_string())
            .and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string()))
            .and_then(|v| eq_view::read(&v));
        match result {
            Ok((bgm, karaoke)) => {
                let presets: Vec<Value> = eq_view::PRESETS.iter().map(|(n, g)| json!({"name": n, "gains": g.to_vec()})).collect();
                let doc = json!({"bands": eq_view::BANDS_HZ.to_vec(), "presets": presets, "bgm": one(&bgm), "karaoke": one(&karaoke)});
                self.as_mut().set_eq_json(QString::from(doc.to_string().as_str()));
                self.as_mut().set_status(QString::from(""));
            }
            Err(e) => {
                self.as_mut().set_eq_json(QString::from("{}"));
                self.as_mut().set_status(QString::from(format!("The equalisers cannot be shown: {e}").as_str()));
            }
        }
    }
}
