//! singws-shell: the Rust-owned Qt Quick shell (Stage E). READ-ONLY panel 1: song library search.
mod cdg_demo;
mod readiness;
mod chat_panel;
mod video_demo;
mod idle_panel;
mod karaoke_proto;
mod play_demo;
mod placement_panel;
mod selfcheck;
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
    // `--readiness-run DIR [--song SONG.zip]`: the whole operator test (checks + selfcheck + a real song + the audience display). See readiness.rs.
    if let Some((dir, song)) = readiness::requested_run(std::env::args().skip(1)) {
        std::process::exit(readiness::run_all(&dir, song.as_deref()));
    }
    // `--readiness REPORT.json` (no Qt, no window): is the Python-free road open on this Mac? See readiness.rs.
    if let Some(path) = readiness::requested(std::env::args().skip(1)) {
        std::process::exit(readiness::run(&path));
    }
    // `--selfcheck` is the no-Python host spike (rust/spike_host.sh): it loads SelfCheck.qml instead of the shell and writes a report.
    let selfcheck = selfcheck::configure(std::env::args().skip(1));
    // `--play-demo VIDEO AUDIO` is the second spike: a CDG song through the Rust bridge + Rust karaoke voice (docs/design/stage-e-no-python-host-spike-2.md).
    let play_demo = play_demo::configure(std::env::args().skip(1));
    let mut app = QGuiApplication::new();
    let mut engine = QQmlApplicationEngine::new();
    if let Some(mut engine) = engine.as_mut() {
        // the karaoke-display demo's picture source (image://cdg/<ms>); harmless when --display-demo is not given
        unsafe { singws_cdg_install_provider(engine.as_mut().get_unchecked_mut() as *mut _ as *mut core::ffi::c_void) };
        let page = if play_demo { "PlayDemo" } else if selfcheck { "SelfCheck" } else { "Main" };
        engine.load(&QUrl::from(format!("qrc:/qt/qml/com/singws/shell/qml/{page}.qml").as_str()));
    }
    if let Some(app) = app.as_mut() {
        app.exec();
    }
}
