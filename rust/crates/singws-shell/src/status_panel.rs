//! QObject exposed to QML as `StatusPanel`: a READ-ONLY look at the Show tab's header (the four pills, the status line, the SINGERS / ETA line, the queue summary) and the
//! pre-show diagnostics report. The app writes `status_snapshot.json` (setting `status_snapshot`) whenever its header refreshes; this panel only reads it (reloaded on every
//! refresh) and draws it with the toolkit-free rules in `singws-uimodel::status`, which reuse the header and diagnostics view models proven equal to the app.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, view_json, cxx_name = "viewJson")]
        #[qproperty(QString, status)]
        type StatusPanel = super::StatusPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut StatusPanel>);

        /// test hook (`--dump-status FILE`): write the view the page is showing
        #[qinvokable]
        #[cxx_name = "dumpView"]
        fn dump_view(self: &StatusPanel, path: &QString);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use singws_uimodel::panels;

pub struct StatusPanelRust {
    view_json: QString,
    status: QString,
    dir: String,
}

impl Default for StatusPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        StatusPanelRust { view_json: QString::from("{}"), status: QString::from(""), dir }
    }
}

fn unix_now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

impl qobject::StatusPanel {
    fn dump_view(&self, path: &QString) {
        let _ = std::fs::write(path.to_string(), self.view_json().to_string());
    }

    fn refresh(mut self: Pin<&mut Self>) {
        let dir = self.rust().dir.clone();
        let (view, status) = panels::status_json(&dir, unix_now());
        self.as_mut().set_view_json(QString::from(view.to_string().as_str()));
        self.as_mut().set_status(QString::from(status.as_str()));
    }
}
