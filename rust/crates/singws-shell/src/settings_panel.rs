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
use singws_uimodel::panels;

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
    /// Rows and wording come from the shared `singws-uimodel::panels::settings_view_data` (secrets are hidden there, by `settings_view`).
    fn refresh(mut self: Pin<&mut Self>, query: &QString) {
        let d = panels::settings_view_data(&self.rust().dir.clone(), &query.to_string());
        let items: Vec<Value> = d.rows.iter().map(|r| json!({"key": r.key, "value": r.value, "hidden": r.hidden})).collect();
        self.as_mut().set_rows_json(QString::from(Value::Array(items).to_string().as_str()));
        self.as_mut().set_status(QString::from(d.status.as_str()));
    }
}
