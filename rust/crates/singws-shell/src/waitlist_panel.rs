//! QObject exposed to QML as `WaitlistPanel`: a READ-ONLY look at the waiting-for-add requests. The app writes `waitlist_snapshot.json` (setting `waitlist_snapshot`) whenever
//! its waitlist changes; this panel only reads it (reloaded on every refresh) and draws the sections with the toolkit-free rules in `singws-uimodel::waitlist`.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, sections_json, cxx_name = "sectionsJson")]
        #[qproperty(QString, status)]
        type WaitlistPanel = super::WaitlistPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut WaitlistPanel>);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use singws_uimodel::panels;
use std::time::SystemTime;

pub struct WaitlistPanelRust {
    sections_json: QString,
    status: QString,
    dir: String,
}

impl Default for WaitlistPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        WaitlistPanelRust { sections_json: QString::from("[]"), status: QString::from(""), dir }
    }
}

impl qobject::WaitlistPanel {
    fn refresh(mut self: Pin<&mut Self>) {
        let dir = self.rust().dir.clone();
        let (sections, status) = panels::waitlist_json(&dir, SystemTime::now());
        self.as_mut().set_sections_json(QString::from(sections.to_string().as_str()));
        self.as_mut().set_status(QString::from(status.as_str()));
    }
}
