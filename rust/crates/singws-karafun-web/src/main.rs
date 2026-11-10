//! KaraFun's web player in a windowless Google Chrome, driven by the host (SingWS) over a pipe.
//!
//! Run: `singws-karafun-web [--profile DIR] [--chrome PATH]` (profile default: ~/SingWS/Chrome-KaraFun, which keeps the sign-in).
//!
//! stdin, one JSON object per line, `{"cmd": ...}`:
//!   play {id, query}      search KaraFun for `query` ("Artist Title") and play song `id` now (KaraFun's own queue is emptied first)
//!   pause | resume | stop
//!   seek {seconds} | key {semitones} | tempo {percent} | volume {volume}   (volume 0-100)
//!   login {email, password}   sign in with the account's own login form call (no browser); the password is never logged
//!   top {id}   read KaraFun's chart `id` (7 = Top US) in full; answers {"type":"top","songs":[{title,artist,seconds}]}
//!   signin | signout      (KaraFun's own: sign-in opens karafun.com/connect in the browser)
//!   audience {on}         lyrics-only picture with a click shield
//!   screencast {on, width, height, quality}   stream the page as JPEG frames (the page is sized width x height)
//!   reload | quit
//! stdout, one JSON object per line: everything the page says (`ready`, `status` twice a second, `log`, `error`, ...), `{"type":"frame",
//! "jpeg":"<base64>"}` per picture frame, and `{"type":"helper",...}` lines from this program. Messages for people go to stderr.
//! stdin closing ends the program (and Chrome with it).
//!
//! The page script is /karafun_bridge.js at the repository's root (by SingWS 2's author): it reaches KaraFun's own player the way its
//! buttons do. KaraFun has not approved this and can change its site at any time.
mod chrome;

use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::PathBuf;

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

/// The page call a host command stands for (None: not one of ours, or missing what it needs).
fn script_for(line: &Value) -> Option<String> {
    let number = |key: &str| line.get(key).and_then(|v| v.as_f64());
    Some(match line.get("cmd")?.as_str()? {
        "play" => {
            let id = line.get("id")?.as_u64()?;
            // `query` is JSON already, so quotes in a title can't break out of the call.
            let query = line.get("query").filter(|q| q.is_string()).cloned().unwrap_or(Value::Null);
            format!("singwsPlay({id}, {query})")
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
            // Both are JSON strings already, so nothing in a password can break out of the call. Never logged.
            let email = line.get("email").filter(|v| v.is_string())?;
            let password = line.get("password").filter(|v| v.is_string())?;
            format!("singwsLogin({email}, {password})")
        }
        "audience" => format!("singwsAudience({})", line.get("on").and_then(|v| v.as_bool()).unwrap_or(true)),
        _ => return None,
    })
}

fn main() {
    let profile = arg("--profile").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join("SingWS").join("Chrome-KaraFun")
    });
    let Some(binary) = arg("--chrome").map(PathBuf::from).or_else(chrome::find) else {
        say(json!({"type": "helper", "state": "failed", "error": "Google Chrome is not installed"}));
        std::process::exit(2);
    };
    let _ = std::fs::create_dir_all(&profile);
    let chrome = match chrome::Chrome::launch(
        &binary, &profile, URL, vec![BRIDGE.to_owned()],
        |message| say(message),
        |jpeg| say(json!({"type": "frame", "jpeg": jpeg})),
    ) {
        Ok(chrome) => chrome,
        Err(error) => {
            say(json!({"type": "helper", "state": "failed", "error": format!("Chrome could not start: {error}")}));
            std::process::exit(2);
        }
    };
    say(json!({"type": "helper", "state": "started", "chrome": binary.display().to_string(), "profile": profile.display().to_string()}));

    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            say(json!({"type": "helper", "error": "not JSON", "line": line}));
            continue;
        };
        match value.get("cmd").and_then(|c| c.as_str()) {
            Some("quit") => break,
            Some("reload") => chrome.send("Page.reload", json!({})),
            Some("screencast") => {
                if value.get("on").and_then(|v| v.as_bool()).unwrap_or(true) {
                    let (w, h) = (value.get("width").and_then(|v| v.as_i64()).unwrap_or(1280), value.get("height").and_then(|v| v.as_i64()).unwrap_or(720));
                    let quality = value.get("quality").and_then(|v| v.as_i64()).unwrap_or(80);
                    chrome.send("Emulation.setDeviceMetricsOverride", json!({"width": w, "height": h, "deviceScaleFactor": 1, "mobile": false}));
                    chrome.send("Page.startScreencast", json!({"format": "jpeg", "quality": quality, "maxWidth": w, "maxHeight": h, "everyNthFrame": 1}));
                } else {
                    chrome.send("Page.stopScreencast", json!({}));
                }
            }
            _ => match script_for(&value) {
                Some(script) => chrome.eval(&script),
                None => say(json!({"type": "helper", "error": "unknown command", "cmd": value.get("cmd")})),
            },
        }
    }
    // The host is gone (or said quit): Chrome goes with it (its Drop closes it).
    drop(chrome);
}
