//! `singws-video-swframe FILE T_SECONDS W H` writes ONE raw RGBA frame (W*H*4 bytes, alpha 255) of a video file at T seconds to stdout, drawn by libmpv's
//! SOFTWARE renderer (no GL, no window, no sound). It is the first piece of a video item for the Rust display window and the probe for
//! tools/video_sw_compare.py. Environment: SINGWS_LIBMPV = the libmpv dylib (as for the bridge).
use singws_video::mpvapi::{api, EVENT_FILE_LOADED, EVENT_NONE};
use std::io::Write;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    if a.len() < 5 {
        eprintln!("usage: singws-video-swframe FILE T W H");
        std::process::exit(2);
    }
    let (file, t, w, h): (&str, f64, i32, i32) = (&a[1], a[2].parse().unwrap(), a[3].parse().unwrap(), a[4].parse().unwrap());
    let mpv = api().expect("libmpv");
    let hd = mpv.create().expect("mpv_create");
    for (k, v) in [("vo", "libmpv"), ("ao", "null"), ("audio", "no"), ("hwdec", "no"), ("pause", "yes"), ("keep-open", "yes"), ("terminal", "no")] {
        mpv.set_option(hd, k, v);
    }
    assert!(mpv.initialize(hd) >= 0, "mpv_initialize");
    let rc = mpv.render_context_create_sw(hd).expect("sw render context");
    assert!(mpv.command(hd, &["loadfile", file]) >= 0);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20);
    loop {
        let ev = mpv.wait_event(hd, 0.1);
        let id = unsafe { (*ev).event_id };
        if id == EVENT_FILE_LOADED {
            break;
        }
        assert!(id == EVENT_NONE || std::time::Instant::now() < deadline, "file did not load");
        assert!(std::time::Instant::now() < deadline, "timeout loading");
    }
    // exact seek while paused, then wait for the frame to be ready
    assert!(mpv.command(hd, &["seek", &format!("{t}"), "absolute+exact"]) >= 0);
    for _ in 0..40 {
        mpv.wait_event(hd, 0.05);
    }
    let _ = mpv.render_update(rc);
    let stride = (w * 4) as usize;
    let mut buf = vec![0u8; stride * h as usize];
    let r = mpv.render_sw(rc, w, h, stride, &mut buf);
    assert!(r >= 0, "render_sw failed: {}", mpv.error_string(r));
    for px in buf.chunks_exact_mut(4) {
        px[3] = 255;
    }
    std::io::stdout().write_all(&buf).unwrap();
    mpv.render_free(rc);
    mpv.terminate_destroy(hd);
}
