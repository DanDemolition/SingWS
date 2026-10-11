//! Host log sink, same contract as the Objective-C bridge: one callback taking a NUL-terminated line, stderr when none is set.
use std::ffi::{c_char, CString};
use std::sync::atomic::{AtomicUsize, Ordering};

pub type LogFn = unsafe extern "C" fn(*const c_char);
static SINK: AtomicUsize = AtomicUsize::new(0);

pub fn set_sink(cb: Option<LogFn>) {
    SINK.store(cb.map(|f| f as usize).unwrap_or(0), Ordering::SeqCst);
}

pub fn line(text: &str) {
    // mpv's own log lines end in a newline; the host log adds its own.
    let trimmed = text.trim_end_matches(['\n', '\r']);
    let mut clipped = trimmed.to_string();
    if clipped.len() > 1023 {
        let mut end = 1023;
        while !clipped.is_char_boundary(end) {
            end -= 1;
        }
        clipped.truncate(end);
    }
    let c = CString::new(clipped.replace('\0', "")).unwrap_or_default();
    let sink = SINK.load(Ordering::SeqCst);
    if sink != 0 {
        let f: LogFn = unsafe { std::mem::transmute::<usize, LogFn>(sink) };
        unsafe { f(c.as_ptr()) };
    } else {
        eprintln!("{}", c.to_string_lossy());
    }
}

#[macro_export]
macro_rules! bridge_log {
    ($($arg:tt)*) => { $crate::log::line(&format!($($arg)*)) };
}
