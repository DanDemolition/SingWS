//! singws-shell: the Rust-owned Qt Quick shell (Stage E). READ-ONLY panel 1: song library search.
mod control_panel;
mod history_panel;
mod library_panel;
mod music_panel;
mod rotation_panel;
mod settings_panel;

use cxx_qt_lib::{QGuiApplication, QQmlApplicationEngine, QUrl};

fn main() {
    // The native macOS style cannot be customised; the Basic style can. Set before Qt starts (nothing else is running yet).
    unsafe { std::env::set_var("QT_QUICK_CONTROLS_STYLE", "Basic") };
    let mut app = QGuiApplication::new();
    let mut engine = QQmlApplicationEngine::new();
    if let Some(engine) = engine.as_mut() {
        engine.load(&QUrl::from("qrc:/qt/qml/com/singws/shell/qml/Main.qml"));
    }
    if let Some(app) = app.as_mut() {
        app.exec();
    }
}
