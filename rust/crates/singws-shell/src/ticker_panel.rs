//! QObject exposed to QML as `TickerPanel`: a READ-ONLY look at the text the audience ticker shows. The queue comes from `queue.json` and the two ticker keys from
//! `settings.json` (reloaded on every refresh); all rules live in the toolkit-free `singws-uimodel::ticker`, which is proven equal to the app's own ticker text.
//! The app's "now singing" parts are not on disk, so a custom message that uses them shows the same text the app would show with nobody singing.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, text)]
        #[qproperty(QString, message)]
        #[qproperty(QString, singers_json, cxx_name = "singersJson")]
        #[qproperty(QString, status)]
        type TickerPanel = super::TickerPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut TickerPanel>);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::Value;
use singws_uimodel::panels;

pub struct TickerPanelRust {
    text: QString,
    message: QString,
    singers_json: QString,
    status: QString,
    dir: String,
}

impl Default for TickerPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        TickerPanelRust { text: QString::from(""), message: QString::from(""), singers_json: QString::from("[]"), status: QString::from(""), dir }
    }
}

impl qobject::TickerPanel {
    /// The files are read and the wording chosen by the shared `singws-uimodel::panels::ticker_view` (also used by the Slint trial).
    fn refresh(mut self: Pin<&mut Self>) {
        let dir = self.rust().dir.clone();
        let v = panels::ticker_view(&dir);
        self.as_mut().set_text(QString::from(v.text.as_str()));
        self.as_mut().set_message(QString::from(v.message.as_str()));
        self.as_mut().set_singers_json(QString::from(Value::from(v.singers).to_string().as_str()));
        self.as_mut().set_status(QString::from(v.status.as_str()));
    }
}
