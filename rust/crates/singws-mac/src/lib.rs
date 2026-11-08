//! `singws-mac`: the macOS platform shim (Stage E step 1). Safe wrappers over a thin Objective-C shim (`shim/mac_shim.m`).
//!
//! Nothing here prompts the user: Accessibility is checked with the prompt option off and location only reads its authorization status
//! unless it is already granted. Off macOS every call reports "unsupported".

use std::ffi::{c_char, c_int, CStr, CString};
use std::sync::{mpsc, Mutex};
use std::time::Duration;

#[cfg(target_os = "macos")]
extern "C" {
    fn singws_mac_free(p: *mut c_char);
    fn singws_mac_keep_awake_begin(reason: *const c_char) -> c_int;
    fn singws_mac_keep_awake_end();
    fn singws_mac_keep_awake_active() -> c_int;
    fn singws_mac_frontmost_app_name() -> *mut c_char;
    fn singws_mac_ax_is_trusted() -> c_int;
    fn singws_mac_run_applescript(source: *const c_char, out: *mut *mut c_char, err: *mut *mut c_char) -> c_int;
    fn singws_mac_location_status() -> c_int;
    fn singws_mac_location_once(timeout_s: f64, lat: *mut f64, lon: *mut f64, acc: *mut f64, err: *mut *mut c_char) -> c_int;
}

/// Take ownership of a malloc'd C string from the shim and free it.
#[cfg(target_os = "macos")]
unsafe fn take(p: *mut c_char) -> String {
    if p.is_null() {
        return String::new();
    }
    let s = CStr::from_ptr(p).to_string_lossy().into_owned();
    singws_mac_free(p);
    s
}

#[cfg(target_os = "macos")]
static SCRIPT_LOCK: Mutex<()> = Mutex::new(());

/// The result of `run_applescript`, the same contract as the app's `_run_karafun_applescript_sync`: `(ok, stdout, stderr)`,
/// stdout stripped, an empty script is `(false, "", "empty_script")`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScriptResult {
    pub ok: bool,
    pub out: String,
    pub err: String,
}

/// Authorization status names as CoreLocation numbers them (`-1` is "location services disabled", added by the shim).
pub fn location_status_name(status: i32) -> &'static str {
    match status {
        -1 => "services_disabled",
        0 => "not_determined",
        1 => "restricted",
        2 => "denied",
        3 => "authorized_always",
        4 => "authorized_when_in_use",
        _ => "unknown",
    }
}

/// True for the two statuses under which a location request may be made.
pub fn location_status_allows_request(status: i32) -> bool {
    status == 3 || status == 4
}

pub fn supported() -> bool {
    cfg!(target_os = "macos")
}

/// Begin the keep-awake assertion (same options as `mac_keep_awake.py`). Idempotent. False off macOS.
pub fn keep_awake_begin(reason: &str) -> bool {
    #[cfg(target_os = "macos")]
    {
        let reason = CString::new(reason.replace('\0', " ")).unwrap_or_default();
        unsafe { singws_mac_keep_awake_begin(reason.as_ptr()) != 0 }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = reason;
        false
    }
}

pub fn keep_awake_end() {
    #[cfg(target_os = "macos")]
    unsafe {
        singws_mac_keep_awake_end()
    }
}

pub fn keep_awake_active() -> bool {
    #[cfg(target_os = "macos")]
    {
        unsafe { singws_mac_keep_awake_active() != 0 }
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

/// Localized name of the frontmost application ("" when unknown), as `NSWorkspace` reports it to the app.
pub fn frontmost_app_name() -> String {
    #[cfg(target_os = "macos")]
    {
        unsafe { take(singws_mac_frontmost_app_name()) }
    }
    #[cfg(not(target_os = "macos"))]
    {
        String::new()
    }
}

/// Whether THIS process is a trusted Accessibility client. Never prompts.
pub fn ax_is_trusted() -> bool {
    #[cfg(target_os = "macos")]
    {
        unsafe { singws_mac_ax_is_trusted() != 0 }
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

/// Run AppleScript in this process, giving up after `timeout`. On timeout the script thread is left running (NSAppleScript cannot be
/// interrupted) and `(false, "", "timeout")` is returned.
pub fn run_applescript(source: &str, timeout: Duration) -> ScriptResult {
    if source.trim().is_empty() {
        return ScriptResult { ok: false, out: String::new(), err: "empty_script".into() };
    }
    #[cfg(target_os = "macos")]
    {
        // Default since 2026-11-08: the `osascript` tool, exactly how the Python app runs its scripts. The in-process NSAppleScript below NEVER finished a KaraFun search whose
        // query had several words (`keystroke "Journey Dont Stop Believin"`) although `osascript` finishes it in ~6 s (found by the live ladder comparison); it also cannot be
        // interrupted, while a timed-out `osascript` is killed. SINGWS_APPLESCRIPT_RUNNER=inprocess selects the old runner.
        if std::env::var("SINGWS_APPLESCRIPT_RUNNER").map_or(true, |v| v != "inprocess") {
            return run_osascript(source, timeout);
        }
    }
    run_applescript_in_process(source, timeout)
}

/// The script through `osascript -` (script on stdin), killed when it outlasts `timeout`. On macOS only; elsewhere "unsupported".
pub fn run_osascript(source: &str, timeout: Duration) -> ScriptResult {
    #[cfg(target_os = "macos")]
    {
        use std::io::{Read, Write};
        use std::process::{Command, Stdio};
        use std::time::Instant;
        let spawned = Command::new("/usr/bin/osascript").arg("-").stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn();
        let Ok(mut child) = spawned else { return ScriptResult { ok: false, out: String::new(), err: "osascript could not start".into() } };
        if let Some(mut stdin) = child.stdin.take() {
            let src = source.as_bytes().to_vec();
            std::thread::spawn(move || {
                let _ = stdin.write_all(&src);
            });
        }
        // read the pipes on their own threads so a chatty script cannot fill them and stall
        let read = |pipe: Option<Box<dyn Read + Send>>| {
            std::thread::spawn(move || {
                let mut buf = Vec::new();
                if let Some(mut p) = pipe {
                    let _ = p.read_to_end(&mut buf);
                }
                String::from_utf8_lossy(&buf).into_owned()
            })
        };
        let out_t = read(child.stdout.take().map(|p| Box::new(p) as Box<dyn Read + Send>));
        let err_t = read(child.stderr.take().map(|p| Box::new(p) as Box<dyn Read + Send>));
        let start = Instant::now();
        let status = loop {
            match child.try_wait() {
                Ok(Some(st)) => break Some(st),
                Ok(None) if start.elapsed() < timeout => std::thread::sleep(Duration::from_millis(20)),
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
            }
        };
        let (out, err) = (out_t.join().unwrap_or_default(), err_t.join().unwrap_or_default());
        return match status {
            None => ScriptResult { ok: false, out: String::new(), err: "timeout".into() },
            Some(st) => ScriptResult { ok: st.success(), out: if st.success() { out.trim().to_string() } else { String::new() }, err: err.trim().to_string() },
        };
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (source, timeout);
        ScriptResult { ok: false, out: String::new(), err: "unsupported".into() }
    }
}

/// The original runner: NSAppleScript inside this process (see `run_applescript` for why it is no longer the default).
pub fn run_applescript_in_process(source: &str, timeout: Duration) -> ScriptResult {
    if source.trim().is_empty() {
        return ScriptResult { ok: false, out: String::new(), err: "empty_script".into() };
    }
    #[cfg(target_os = "macos")]
    {
        let source = match CString::new(source) {
            Ok(c) => c,
            Err(_) => return ScriptResult { ok: false, out: String::new(), err: "script contains a NUL byte".into() },
        };
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            // The AppleScript component is not safe to run concurrently in one process (a parallel test saw another script's result), so
            // scripts run one at a time; a timed-out script keeps the lock until it finishes, later callers time out waiting.
            let _one_at_a_time = SCRIPT_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            let (mut out, mut err): (*mut c_char, *mut c_char) = (std::ptr::null_mut(), std::ptr::null_mut());
            let ok = unsafe { singws_mac_run_applescript(source.as_ptr(), &mut out, &mut err) } != 0;
            let (out, err) = unsafe { (take(out), take(err)) };
            let _ = tx.send(ScriptResult { ok, out: out.trim().to_string(), err });
        });
        match rx.recv_timeout(timeout) {
            Ok(r) => r,
            Err(_) => ScriptResult { ok: false, out: String::new(), err: "timeout".into() },
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (timeout, mpsc::channel::<()>, &Mutex::new(()));
        ScriptResult { ok: false, out: String::new(), err: "unsupported".into() }
    }
}

/// CoreLocation authorization status for this process (`-1` services disabled; see `location_status_name`). Never prompts.
pub fn location_status() -> i32 {
    #[cfg(target_os = "macos")]
    {
        unsafe { singws_mac_location_status() }
    }
    #[cfg(not(target_os = "macos"))]
    {
        -1
    }
}

/// One location fix `(latitude, longitude, horizontal accuracy m)`, or an error text. Declines with "not authorized: status N" when this
/// process has no grant; it never asks for one.
pub fn device_location_once(timeout: Duration) -> Result<(f64, f64, f64), String> {
    #[cfg(target_os = "macos")]
    {
        let (mut lat, mut lon, mut acc) = (0.0, 0.0, 0.0);
        let mut err: *mut c_char = std::ptr::null_mut();
        let ok = unsafe { singws_mac_location_once(timeout.as_secs_f64(), &mut lat, &mut lon, &mut acc, &mut err) } != 0;
        let err = unsafe { take(err) };
        if ok { Ok((lat, lon, acc)) } else { Err(err) }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = timeout;
        Err("unsupported".into())
    }
}

/// The three values the app's `platform_shadow` compares with its own Python answers.
pub fn shadow_state_json() -> serde_json::Value {
    let began = keep_awake_begin("SingWS platform shadow");
    let active = keep_awake_active();
    keep_awake_end();
    serde_json::json!({
        "supported": supported(),
        "frontmost_app": frontmost_app_name(),
        "ax_trusted": ax_is_trusted(),
        "keep_awake_ok": began && active,
        "keep_awake_released": !keep_awake_active(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn status_names_cover_every_corelocation_value() {
        assert_eq!(location_status_name(-1), "services_disabled");
        assert_eq!(location_status_name(0), "not_determined");
        assert_eq!(location_status_name(1), "restricted");
        assert_eq!(location_status_name(2), "denied");
        assert_eq!(location_status_name(3), "authorized_always");
        assert_eq!(location_status_name(4), "authorized_when_in_use");
        assert_eq!(location_status_name(99), "unknown");
    }

    #[test]
    fn only_the_two_authorized_statuses_allow_a_request() {
        for s in -1..=5 {
            assert_eq!(location_status_allows_request(s), s == 3 || s == 4, "status {s}");
        }
    }

    #[test]
    fn empty_script_is_refused_like_the_app() {
        for src in ["", "   \n\t "] {
            let r = run_applescript(src, Duration::from_secs(1));
            assert_eq!(r, ScriptResult { ok: false, out: String::new(), err: "empty_script".into() });
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn harmless_script_returns_its_value_trimmed() {
        let r = run_applescript("return 1+1", Duration::from_secs(20));
        assert!(r.ok, "{r:?}");
        assert_eq!(r.out, "2");
        assert_eq!(r.err, "");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn script_error_is_reported_not_swallowed() {
        let r = run_applescript("this is not applescript (", Duration::from_secs(20));
        assert!(!r.ok);
        assert!(!r.err.is_empty());
        assert_eq!(r.out, "");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn script_that_outlasts_the_timeout_reports_timeout() {
        let r = run_applescript("delay 3\nreturn 1", Duration::from_millis(200));
        assert_eq!(r, ScriptResult { ok: false, out: String::new(), err: "timeout".into() });
    }

    // The keep-awake tests share one process-wide assertion, so they run as one test.
    #[cfg(target_os = "macos")]
    #[test]
    fn keep_awake_begin_is_idempotent_and_end_releases() {
        keep_awake_end();
        assert!(!keep_awake_active());
        assert!(keep_awake_begin("test"));
        assert!(keep_awake_begin("test again"));
        assert!(keep_awake_active());
        keep_awake_end();
        assert!(!keep_awake_active());
        keep_awake_end(); // safe when not held
        let state = shadow_state_json();
        assert_eq!(state["keep_awake_ok"], true);
        assert_eq!(state["keep_awake_released"], true);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn location_never_requests_without_a_grant() {
        // The test binary has no grant; this must decline immediately instead of prompting or waiting out the timeout.
        let t = std::time::Instant::now();
        let status = location_status();
        let r = device_location_once(Duration::from_secs(5));
        if !location_status_allows_request(status) {
            assert!(r.is_err());
            assert!(t.elapsed() < Duration::from_secs(3));
        }
    }
}
