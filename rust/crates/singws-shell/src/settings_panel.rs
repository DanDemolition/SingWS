//! QObject exposed to QML as `SettingsPanel`: a READ-ONLY look at `settings.json`. Secrets are never put into a row (see `singws-uimodel::settings_view`).

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
        #[qproperty(QString, status)]
        type SettingsPanel = super::SettingsPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut SettingsPanel>, query: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::{Value, json};
use singws_uimodel::settings_view;

pub struct SettingsPanelRust {
    rows_json: QString,
    status: QString,
    dir: String,
}

impl Default for SettingsPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        SettingsPanelRust { rows_json: QString::from("[]"), status: QString::from(""), dir }
    }
}

impl qobject::SettingsPanel {
    fn refresh(mut self: Pin<&mut Self>, query: &QString) {
        let path = format!("{}/settings.json", self.rust().dir);
        let result = std::fs::read_to_string(&path)
            .map_err(|e| e.to_string())
            .and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string()))
            .and_then(|v| settings_view::rows(&v, &query.to_string()));
        match result {
            Ok(rows) => {
                let n = rows.len();
                let hidden = rows.iter().filter(|r| r.hidden).count();
                let items: Vec<Value> = rows.iter().map(|r| json!({"key": r.key, "value": r.value, "hidden": r.hidden})).collect();
                self.as_mut().set_rows_json(QString::from(Value::Array(items).to_string().as_str()));
                let status = if n == 0 { "No settings match".to_string() } else { format!("{n} setting{}   •   {hidden} hidden (keys, PINs and tokens are never shown)", if n == 1 { "" } else { "s" }) };
                self.as_mut().set_status(QString::from(status.as_str()));
            }
            Err(e) => {
                self.as_mut().set_rows_json(QString::from("[]"));
                self.as_mut().set_status(QString::from(format!("Settings unavailable: {e}").as_str()));
            }
        }
    }
}
