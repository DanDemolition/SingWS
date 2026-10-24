use cxx_qt_build::{CxxQtBuilder, QmlModule};

fn main() {
    CxxQtBuilder::new_qml_module(QmlModule::new("com.singws.shell").qml_files(["qml/Main.qml", "qml/LibraryPage.qml", "qml/HistoryPage.qml"]))
        .qt_module("Quick")
        .qt_module("QuickControls2")
        .file("src/library_panel.rs")
        .file("src/history_panel.rs")
        .build();
}
