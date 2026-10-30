//! singws-shell: the Rust-owned Qt Quick shell (Stage E). READ-ONLY panel 1: song library search.
mod cdg_demo;
mod video_demo;
mod control_panel;
mod eq_panel;
mod history_panel;
mod library_panel;
mod music_panel;
mod rotation_panel;
mod settings_panel;
mod status_panel;
mod ticker_panel;
mod waitlist_panel;

use cxx_qt_lib::{QGuiApplication, QQmlApplicationEngine, QUrl};

unsafe extern "C" {
    fn singws_cdg_install_provider(engine: *mut core::ffi::c_void);
}

fn main() {
    // The native macOS style cannot be customised; the Basic style can. Set before Qt starts (nothing else is running yet).
    unsafe { std::env::set_var("QT_QUICK_CONTROLS_STYLE", "Basic") };
    let mut app = QGuiApplication::new();
    let mut engine = QQmlApplicationEngine::new();
    if let Some(mut engine) = engine.as_mut() {
        // the karaoke-display demo's picture source (image://cdg/<ms>); harmless when --display-demo is not given
        unsafe { singws_cdg_install_provider(engine.as_mut().get_unchecked_mut() as *mut _ as *mut core::ffi::c_void) };
        engine.load(&QUrl::from("qrc:/qt/qml/com/singws/shell/qml/Main.qml"));
    }
    if let Some(app) = app.as_mut() {
        app.exec();
    }
}
