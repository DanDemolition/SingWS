//! SingWS KaraFun display: KaraFun's web player (karafun.com/web) run by Chromium (CEF) inside THIS program with no window of its own, each frame
//! arriving as an IOSurface on the graphics chip and shown in a native window with no copy and no JPEG. The approach is SingWS 2's `karafun_cef.rs`
//! (see cef_host.rs); the commands and messages are the ones `singws-karafun-web` speaks, so the Python host changes little.
//!
//! stdin (JSON lines): the commands of singws-karafun-web (play, pause, resume, stop, seek, key, tempo, volume, signin, signout, top, login, audience,
//! output, reload, screencast, quit) plus
//!   window {on, x, y, w, h, level, above}   show/place/hide the picture window (x, y from the top left of the main screen, as Qt gives them;
//!                                    above = window number to sit directly over)
//!   refresh {rate}                   the audience screen's refresh rate (the page draws at it while the picture is on)
//! stdout (JSON lines): the page's messages ({"type": ...}) and {"type":"helper","state":"started"|"needs_browser"|...}. Logs go to stderr.
//! `singws-karafun-display installed` exits 0 if Chromium's framework is here, 3 if not.
//! `singws-karafun-display get` downloads Chromium's framework (about 135 MB, once) and prints {"type":"progress","text":...} then {"type":"got"} or
//! {"type":"failed","error":...}. Exit code 3 on start means the framework is not here yet.
mod cef_host;

use cef_host::Picture;
use objc2::{msg_send, rc::Retained, runtime::AnyObject, MainThreadMarker, MainThreadOnly};
use objc2_app_kit::{NSApplication, NSApplicationActivationPolicy, NSBackingStoreType, NSColor, NSScreen, NSView, NSWindow, NSWindowOrderingMode, NSWindowStyleMask};
use objc2_foundation::{NSPoint, NSRect, NSSize, NSString};
use serde_json::{json, Value};
use std::{cell::RefCell, io::{BufRead, Write}, path::PathBuf};

const URL: &str = "https://www.karafun.com/web/";
const BRIDGE: &str = include_str!("../../../../karafun_bridge.js");

fn say(value: Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{value}");
    let _ = out.flush();
}

fn arg(name: &str) -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).cloned()
}

/// The page call a host command stands for (None: not one of ours, or missing what it needs). Same as singws-karafun-web.
fn script_for(line: &Value) -> Option<String> {
    let number = |key: &str| line.get(key).and_then(|v| v.as_f64());
    Some(match line.get("cmd")?.as_str()? {
        "play" => {
            let id = line.get("id")?.as_u64()?;
            let query = line.get("query").filter(|q| q.is_string()).cloned().unwrap_or(Value::Null);
            let extra = json!({
                "queries": line.get("queries").filter(|q| q.is_array()).cloned().unwrap_or(json!([])),
                "artist": line.get("artist").filter(|q| q.is_string()).cloned().unwrap_or(Value::Null),
                "title": line.get("title").filter(|q| q.is_string()).cloned().unwrap_or(Value::Null),
            });
            format!("singwsPlay({id}, {query}, {extra})")
        }
        "pause" => "singwsPause()".into(),
        "resume" => "singwsResume()".into(),
        "stop" => "singwsStop()".into(),
        "seek" => format!("singwsSeek({:.1})", number("seconds")?.max(0.0)),
        "key" => format!("singwsKey({})", number("semitones")?.round() as i64),
        "tempo" => format!("singwsTempo({})", number("percent")?.round() as i64),
        "volume" => format!("singwsVolume({})", number("volume")?.clamp(0.0, 100.0).round() as i64),
        "signin" => "singwsSignIn()".into(),
        "signout" => "singwsSignOut()".into(),
        "top" => format!("singwsTop({})", line.get("id")?.as_u64()?),
        "login" => {
            let email = line.get("email").filter(|v| v.is_string())?;
            let password = line.get("password").filter(|v| v.is_string())?;
            format!("singwsLogin({email}, {password})")
        }
        "output" => format!("singwsOutput({})", line.get("device").filter(|v| v.is_string()).cloned().unwrap_or(json!(""))),
        "audience" => format!("singwsAudience({})", line.get("on").and_then(|v| v.as_bool()).unwrap_or(true)),
        _ => return None,
    })
}

thread_local! {
    static CHROME: RefCell<Option<cef_host::Chrome>> = const { RefCell::new(None) };
    static WINDOW: RefCell<Option<(Retained<NSWindow>, Retained<NSView>)>> = const { RefCell::new(None) };
}

/// A borderless black window whose layer shows the page's frames.
fn make_window(mtm: MainThreadMarker, rect: NSRect) -> (Retained<NSWindow>, Retained<NSView>) {
    // SAFETY: AppKit's documented initialiser, on the main thread.
    let window = unsafe {
        NSWindow::initWithContentRect_styleMask_backing_defer(NSWindow::alloc(mtm), rect, NSWindowStyleMask::Borderless, NSBackingStoreType::Buffered, false)
    };
    // SAFETY: the window is ours and stays alive in WINDOW.
    unsafe { window.setReleasedWhenClosed(false) };
    window.setBackgroundColor(Some(&NSColor::blackColor()));
    window.setHasShadow(false);
    window.setIgnoresMouseEvents(true);
    let view = window.contentView().expect("a window has a content view");
    view.setWantsLayer(true);
    // SAFETY: the view's own layer (it wants one, above); the picture is stretched to the window as the audience screen does.
    unsafe {
        let layer: *mut AnyObject = msg_send![&*view, layer];
        if !layer.is_null() { let _: () = msg_send![layer, setContentsGravity: &*NSString::from_str("resize")]; }
    }
    (window, view)
}

/// `x`, `y` from the top left of the main screen (Qt's global coordinates) to AppKit's (bottom left of the main screen).
fn cocoa_rect(mtm: MainThreadMarker, x: f64, y: f64, w: f64, h: f64) -> NSRect {
    let main_height = NSScreen::screens(mtm).firstObject().map_or(h + y, |screen| screen.frame().size.height);
    NSRect::new(NSPoint::new(x, main_height - y - h), NSSize::new(w, h))
}

fn window_command(line: &Value) {
    let Some(mtm) = MainThreadMarker::new() else { return };
    if !line.get("on").and_then(|v| v.as_bool()).unwrap_or(true) {
        WINDOW.with(|w| if let Some((window, _)) = w.borrow().as_ref() { window.orderOut(None); });
        return;
    }
    let get = |k: &str, d: f64| line.get(k).and_then(|v| v.as_f64()).unwrap_or(d);
    let rect = cocoa_rect(mtm, get("x", 0.0), get("y", 0.0), get("w", 1280.0).max(1.0), get("h", 720.0).max(1.0));
    WINDOW.with(|w| {
        let mut slot = w.borrow_mut();
        let (window, _) = slot.get_or_insert_with(|| make_window(mtm, rect));
        window.setFrame_display(rect, true);
        window.setLevel(get("level", 0.0) as isize);
        // `above`: the number of a window to sit directly over (SingWS's audience window), so the ticker and QR windows that are higher stay over the picture.
        let above = get("above", 0.0) as isize;
        if above > 0 { window.orderWindow_relativeTo(NSWindowOrderingMode::Above, above); } else { window.orderFront(None); }
    });
}

/// A frame from Chromium: its IOSurface becomes the layer's contents (no copy), cropped to the part that is the page.
fn show_frame(picture: &Picture) {
    WINDOW.with(|w| {
        let Some((_, view)) = w.borrow().as_ref().map(|(window, view)| (window.clone(), view.clone())) else { return };
        let (x, y, width, height) = picture.visible;
        let crop = NSRect::new(NSPoint::new(x, 1.0 - y - height), NSSize::new(width, height));
        // SAFETY: the view's own layer, on the main thread; the layer retains the surface it is given.
        unsafe {
            let layer: *mut AnyObject = msg_send![&*view, layer];
            if layer.is_null() { return; }
            let _: () = msg_send![layer, setContents: picture.surface as *mut AnyObject];
            let _: () = msg_send![layer, setContentsRect: crop];
        }
    });
}

fn finish() -> ! {
    // Chromium saves KaraFun's sign-in and closes the page as the handle goes (its Drop).
    CHROME.with(|c| drop(c.borrow_mut().take()));
    std::process::exit(0);
}

fn handle(line: Value) {
    match line.get("cmd").and_then(|c| c.as_str()) {
        Some("quit") => finish(),
        Some("reload") => cef_host::send("Page.reload", json!({})),
        Some("window") => { cef_host::watch(true); window_command(&line) }
        Some("stats") => say(json!({"type": "helper", "frames": cef_host::pacing()})),
        Some("refresh") => cef_host::set_refresh(line.get("rate").and_then(|v| v.as_i64()).unwrap_or(60) as i32),
        Some("screencast") => {
            if line.get("on").and_then(|v| v.as_bool()).unwrap_or(true) {
                let (w, h) = (line.get("width").and_then(|v| v.as_i64()).unwrap_or(1280), line.get("height").and_then(|v| v.as_i64()).unwrap_or(720));
                let (w, h) = cef_host::limit_page(w, h);   // never above 720p
                cef_host::send("Emulation.setDeviceMetricsOverride", json!({"width": w, "height": h, "deviceScaleFactor": 1, "mobile": false}));
                cef_host::send("Page.startScreencast", json!({}));
            } else {
                cef_host::send("Page.stopScreencast", json!({}));
            }
        }
        _ => match script_for(&line) {
            Some(script) => cef_host::eval(&script),
            None => say(json!({"type": "helper", "error": "unknown command", "cmd": line.get("cmd")})),
        },
    }
}

fn main() {
    // One of Chromium's own helper processes (page, graphics, network): runs as that and exits.
    cef_host::run_helper();
    if std::env::args().nth(1).as_deref() == Some("installed") {
        // Exit 0 when Chromium's framework is here, 3 when it is not (the app then asks before running `get`).
        std::process::exit(if cef_host::installed() { 0 } else { 3 });
    }
    if std::env::args().nth(1).as_deref() == Some("get") {
        match cef_host::get(|text| say(json!({"type": "progress", "text": text}))) {
            Ok(()) => say(json!({"type": "got"})),
            Err(error) => { say(json!({"type": "failed", "error": error})); std::process::exit(1); }
        }
        return;
    }
    // Before any other thread exists (Chromium's memory manager goes in with its framework).
    cef_host::preload();
    let Some(mtm) = MainThreadMarker::new() else { std::process::exit(1) };
    let app = NSApplication::sharedApplication(mtm);
    app.setActivationPolicy(NSApplicationActivationPolicy::Accessory);
    if !cef_host::installed() {
        say(json!({"type": "helper", "state": "needs_browser", "error": "Chromium's framework is not here yet (run: singws-karafun-display get)"}));
        std::process::exit(3);
    }
    let profile = arg("--profile").map(PathBuf::from).unwrap_or_else(|| cef_host::folder().join("Profile"));
    let launched = cef_host::Chrome::launch(&profile, URL, vec![BRIDGE.to_owned()], |message| say(message), |picture| show_frame(picture));
    match launched {
        Ok(chrome) => CHROME.with(|c| *c.borrow_mut() = Some(chrome)),
        Err(error) => { say(json!({"type": "helper", "state": "failed", "error": format!("Chromium could not start: {error}")})); std::process::exit(2); }
    }
    say(json!({"type": "helper", "state": "started", "profile": profile.display().to_string()}));
    std::thread::spawn(|| {
        for line in std::io::stdin().lock().lines() {
            let Ok(line) = line else { break };
            match serde_json::from_str::<Value>(&line) {
                Ok(value) => cef_host::on_main(move || handle(value)),
                Err(_) => say(json!({"type": "helper", "error": "not JSON", "line": line})),
            }
        }
        cef_host::on_main(|| finish());   // the host is gone: close Chromium and end
    });
    app.run();
}
