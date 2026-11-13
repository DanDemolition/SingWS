//! `singws-karafun-svc`: one JSON replay request on stdin -> one JSON answer (see `singws_karafun_svc::handle`).
//! `--batch`: one request per line, one answer per line (for the parity and mutation tools).
use std::io::{BufRead, Read};

fn answer(text: &str) -> serde_json::Value {
    match serde_json::from_str::<serde_json::Value>(text) {
        Ok(v) => match v.get("kind").and_then(|k| k.as_str()) { Some("host") => singws_karafun_svc::host::handle(&v), Some("cred") => cred(&v), Some("match") => singws_karafun_svc::matching::handle(&v), Some("top100") => singws_karafun_svc::top100::handle(&v), _ => singws_karafun_svc::handle(&v) },
        Err(e) => serde_json::json!({"error": format!("bad request: {e}")}),
    }
}

fn main() {
    if std::env::args().any(|a| a == "--batch") {
        for line in std::io::stdin().lock().lines().map_while(Result::ok) {
            if !line.trim().is_empty() {
                println!("{}", answer(&line));
            }
        }
        return;
    }
    let mut text = String::new();
    let _ = std::io::stdin().read_to_string(&mut text);
    println!("{}", answer(&text));
}

/// Test vehicle for the credentials module: ONLY services named `SingWS-TEST-...` are accepted (never the real saved login), and the Keychain never asks the user.
fn cred(v: &serde_json::Value) -> serde_json::Value {
    use serde_json::json;
    use singws_karafun_svc::credentials as c;
    let op = v.get("op").and_then(|x| x.as_str()).unwrap_or("");
    let s = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    match op {
        "encode" => return json!({"stored": c::encode(&s("password"))}),
        "decode" => {
            let hex = s("stored_hex");
            let bytes: Vec<u8> = (0..hex.len() / 2).map(|i| u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).unwrap_or(0)).collect();
            return json!({"password": c::decode(&bytes)});
        }
        "clean_email" => return json!({"email": c::clean_email(&s("email"))}),
        _ => {}
    }
    let service = s("service");
    if !service.starts_with("SingWS-TEST-") || service == c::SERVICE {
        return json!({"error": "refused: only SingWS-TEST-* services may be used by this tool"});
    }
    match op {
        "tool_save" => return json!({"ok": c::tool::save(&service, &s("email"), &s("password"))}),
        "tool_load" => return json!({"password": c::tool::load(&service, &s("email"))}),
        "tool_delete" => {
            c::tool::delete(&service, &s("email"));
            return json!({"ok": true});
        }
        "tool_delete_all" => {
            c::tool::delete_all(&service);
            return json!({"ok": true});
        }
        _ => {}
    }
    #[cfg(target_os = "macos")]
    {
        let _lock = security_framework::os::macos::keychain::SecKeychain::disable_user_interaction();
        match op {
            "save" => json!({"ok": c::keychain::save(&service, &s("email"), &s("password"))}),
            "load" => json!({"password": c::keychain::load(&service, &s("email"))}),
            "status" => {
                use security_framework::passwords::get_generic_password;
                match get_generic_password(&service, &c::clean_email(&s("email")).unwrap_or_default()) {
                    Ok(d) => json!({"found": true, "len": d.len()}),
                    Err(e) => json!({"found": false, "code": e.code(), "message": e.to_string()}),
                }
            }
            "delete" => {
                c::keychain::delete(&service, &s("email"));
                json!({"ok": true})
            }
            _ => json!({"declined": "unknown op"}),
        }
    }
    #[cfg(not(target_os = "macos"))]
    json!({"declined": "macOS only"})
}
