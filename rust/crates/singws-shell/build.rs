use cxx_qt_build::{CxxQtBuilder, QmlModule};

fn main() {
    CxxQtBuilder::new_qml_module(QmlModule::new("com.singws.shell").qml_files(["qml/Main.qml", "qml/LibraryPage.qml", "qml/HistoryPage.qml", "qml/RotationPage.qml", "qml/ControlsPage.qml", "qml/MusicPage.qml", "qml/SettingsPage.qml", "qml/TickerPage.qml", "qml/WaitlistPage.qml", "qml/StatusPage.qml", "qml/DiagnosticsPage.qml", "qml/EqPage.qml", "qml/TvWindow.qml", "qml/KaraokeDisplay.qml", "qml/ShowScreenVfx.qml"]))
        .qt_module("Quick")
        .qt_module("QuickControls2")
        .file("src/library_panel.rs")
        .file("src/history_panel.rs")
        .file("src/rotation_panel.rs")
        .file("src/control_panel.rs")
        .file("src/music_panel.rs")
        .file("src/settings_panel.rs")
        .file("src/ticker_panel.rs")
        .file("src/waitlist_panel.rs")
        .file("src/status_panel.rs")
        .file("src/eq_panel.rs")
        .file("src/cdg_demo.rs")
        .file("src/video_demo.rs")
        .file("src/idle_panel.rs")
        .file("src/placement_panel.rs")
        .cpp_file("cpp/cdg_provider.cpp")
        .cpp_file("cpp/screens.cpp")
        .build();
}
