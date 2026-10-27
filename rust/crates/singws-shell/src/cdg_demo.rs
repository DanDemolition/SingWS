//! Stage E, step 1 of the karaoke display: a DEMO. `--display-demo FILE.cdg` loads a CDG file with the Rust decoder (singws-cdg) and
//! `KaraokeDisplay.qml` draws it through the `image://cdg/<ms>` image provider (cpp/cdg_provider.cpp). Nothing here talks to the app, reads
//! the show data, or plays sound; it exists to prove the decoder's pictures reach a Qt Quick window.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(i32, duration_ms, cxx_name = "durationMs")]
        #[qproperty(i32, packets)]
        #[qproperty(QString, status)]
        type CdgDemo = super::CdgDemoRust;

        /// Load a .cdg file; returns whether it decoded at least one packet.
        #[qinvokable]
        fn load(self: Pin<&mut CdgDemo>, path: &QString) -> bool;
    }
}

use core::pin::Pin;
use cxx_qt_lib::QString;
use singws_cdg::CdgPlayer;
use std::sync::Mutex;

static PLAYER: Mutex<Option<CdgPlayer>> = Mutex::new(None);

#[derive(Default)]
pub struct CdgDemoRust {
    duration_ms: i32,
    packets: i32,
    status: QString,
}

impl qobject::CdgDemo {
    fn load(mut self: Pin<&mut Self>, path: &QString) -> bool {
        match std::fs::read(path.to_string()) {
            Ok(data) if data.len() >= singws_cdg::PACKET_BYTES => {
                let player = CdgPlayer::new(data);
                self.as_mut().set_packets(player.packet_count() as i32);
                self.as_mut().set_duration_ms((player.duration_seconds() * 1000.0) as i32);
                self.as_mut().set_status(QString::from(""));
                *PLAYER.lock().unwrap() = Some(player);
                true
            }
            Ok(_) => {
                self.as_mut().set_status(QString::from("not a CDG file (shorter than one packet)"));
                false
            }
            Err(e) => {
                self.as_mut().set_status(QString::from(format!("cannot read the file: {e}").as_str()));
                false
            }
        }
    }
}

/// Called by the C++ image provider: fill `out` (300x216 RGBA = 259,200 bytes) with the picture at `ms`.
#[unsafe(no_mangle)]
pub extern "C" fn singws_cdg_demo_frame(ms: i32, out: *mut u8) -> i32 {
    let mut guard = PLAYER.lock().unwrap();
    let Some(player) = guard.as_mut() else { return 0 };
    let rgba = player.render_at(ms as f64 / 1000.0).to_rgba();
    unsafe { std::ptr::copy_nonoverlapping(rgba.as_ptr(), out, rgba.len()) };
    1
}
