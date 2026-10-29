//! Stage E, step 2 demo: `--display-demo-video FILE` plays a video file in the display window with libmpv's SOFTWARE renderer (no GL, no window, no sound),
//! drawn through the `image://video/<serial>/<w>/<h>` provider. The shell owns the mpv instance; nothing is connected to the app.

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
        #[qproperty(i32, position_ms, cxx_name = "positionMs")]
        #[qproperty(QString, status)]
        type VideoDemo = super::VideoDemoRust;

        /// Start `path` (paused at `at_seconds` when it is >= 0, else playing from the start). Returns whether mpv accepted it.
        #[qinvokable]
        fn load(self: Pin<&mut VideoDemo>, path: &QString, at_seconds: f64) -> bool;
        /// Refresh `positionMs` / `durationMs` from mpv.
        #[qinvokable]
        fn poll(self: Pin<&mut VideoDemo>);
    }
}

use core::pin::Pin;
use cxx_qt_lib::QString;
use singws_video::mpvapi::{api, Handle};
use std::ffi::c_void;
use std::sync::Mutex;

struct State {
    hd: Handle,
    rc: *mut c_void,
}
unsafe impl Send for State {}
static STATE: Mutex<Option<State>> = Mutex::new(None);

#[derive(Default)]
pub struct VideoDemoRust {
    duration_ms: i32,
    position_ms: i32,
    status: QString,
}

impl qobject::VideoDemo {
    fn load(mut self: Pin<&mut Self>, path: &QString, at_seconds: f64) -> bool {
        let mpv = match api() {
            Ok(m) => m,
            Err(e) => {
                self.as_mut().set_status(QString::from(format!("libmpv: {e}").as_str()));
                return false;
            }
        };
        let Some(hd) = mpv.create() else { return false };
        let paused = if at_seconds >= 0.0 { "yes" } else { "no" };
        for (k, v) in [("vo", "libmpv"), ("ao", "null"), ("audio", "no"), ("hwdec", "no"), ("pause", paused), ("keep-open", "yes"), ("loop-file", "inf"), ("terminal", "no")] {
            mpv.set_option(hd, k, v);
        }
        if mpv.initialize(hd) < 0 {
            self.as_mut().set_status(QString::from("mpv_initialize failed"));
            return false;
        }
        let Ok(rc) = mpv.render_context_create_sw(hd) else {
            self.as_mut().set_status(QString::from("software render context failed"));
            return false;
        };
        if mpv.command(hd, &["loadfile", &path.to_string()]) < 0 {
            self.as_mut().set_status(QString::from("loadfile failed"));
            return false;
        }
        if at_seconds >= 0.0 {
            for _ in 0..30 {
                mpv.wait_event(hd, 0.05);
            }
            mpv.command(hd, &["seek", &format!("{at_seconds}"), "absolute+exact"]);
            for _ in 0..30 {
                mpv.wait_event(hd, 0.05);
            }
        }
        *STATE.lock().unwrap() = Some(State { hd, rc });
        self.as_mut().set_status(QString::from(""));
        true
    }

    fn poll(mut self: Pin<&mut Self>) {
        let Ok(mpv) = api() else { return };
        let g = STATE.lock().unwrap();
        let Some(st) = g.as_ref() else { return };
        // drain events so mpv keeps running
        for _ in 0..8 {
            let ev = mpv.wait_event(st.hd, 0.0);
            if unsafe { (*ev).event_id } == singws_video::mpvapi::EVENT_NONE {
                break;
            }
        }
        if let Ok(d) = mpv.get_double(st.hd, "duration") {
            self.as_mut().set_duration_ms((d * 1000.0) as i32);
        }
        if let Ok(p) = mpv.get_double(st.hd, "time-pos") {
            self.as_mut().set_position_ms((p * 1000.0) as i32);
        }
    }
}

/// Called by the C++ image provider: draw mpv's current frame into `out` (w*h*4 bytes RGBA).
#[unsafe(no_mangle)]
pub extern "C" fn singws_video_demo_frame(w: i32, h: i32, out: *mut u8) -> i32 {
    let Ok(mpv) = api() else { return 0 };
    let g = STATE.lock().unwrap();
    let Some(st) = g.as_ref() else { return 0 };
    let _ = mpv.render_update(st.rc);
    let stride = (w * 4) as usize;
    let buf = unsafe { std::slice::from_raw_parts_mut(out, stride * h as usize) };
    if mpv.render_sw(st.rc, w, h, stride, buf) < 0 {
        return 0;
    }
    for px in buf.chunks_exact_mut(4) {
        px[3] = 255;
    }
    1
}
