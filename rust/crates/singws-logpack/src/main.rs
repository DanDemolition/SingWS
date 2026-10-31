//! `singws-logpack`: the Rust log redactor and last-show package builder, for tools and the app's log-only `logpack_shadow`.
//!
//!   redact                       stdin text (UTF-8, lossy) -> redacted text on stdout
//!   build                        stdin JSON {"days","now","files":[{"name","mtime","text"}],"extras":[{"name","text"}]} -> result JSON
//!   build-dir DIR [--days N] [--now T] [--extra FILE]...   the same, reading DIR the way the app does (universal newlines, lossy UTF-8)
//!
//! Result JSON: {"text","window","used","show_lines","slice","error","extras"}; `extras` holds each extra file's redacted text.
//! Reads only what it is given. Never prints the input except as the redacted result the caller asked for.
use serde_json::{json, Value};
use singws_logpack::{build, sanitize, universal_newlines, LogFile};
use std::io::{Read, Write};

fn result(files: &[LogFile], days: i64, now: f64, extras: &[String]) -> Value {
    let p = build(files, days, now, !extras.is_empty());
    json!({
        "text": p.text, "window": p.window, "used": p.used, "show_lines": p.show_lines, "slice": [p.slice.0, p.slice.1, p.slice.2], "error": p.error,
        "extras": extras.iter().map(|e| sanitize(e)).collect::<Vec<_>>(),
    })
}

fn read_stdin() -> Vec<u8> {
    let mut buf = Vec::new();
    let _ = std::io::stdin().read_to_end(&mut buf);
    buf
}

fn mtime_of(path: &std::path::Path) -> Option<f64> {
    let d = std::fs::metadata(path).ok()?.modified().ok()?.duration_since(std::time::UNIX_EPOCH).ok()?;
    Some(d.as_secs() as f64 + d.subsec_nanos() as f64 * 1e-9)
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let out = std::io::stdout();
    match args.first().map(String::as_str) {
        Some("redact") => {
            let text = String::from_utf8_lossy(&read_stdin()).into_owned();
            let _ = out.lock().write_all(sanitize(&text).as_bytes());
        }
        Some("build") => {
            let Ok(v) = serde_json::from_slice::<Value>(&read_stdin()) else {
                eprintln!("bad json");
                std::process::exit(2);
            };
            let files: Vec<LogFile> = v["files"]
                .as_array()
                .map(|a| a.iter().map(|f| LogFile { name: f["name"].as_str().unwrap_or("").into(), mtime: f["mtime"].as_f64().unwrap_or(0.0), text: f["text"].as_str().map(String::from) }).collect())
                .unwrap_or_default();
            let extras: Vec<String> = v["extras"].as_array().map(|a| a.iter().map(|e| e["text"].as_str().unwrap_or("").to_string()).collect()).unwrap_or_default();
            let _ = writeln!(out.lock(), "{}", result(&files, v["days"].as_i64().unwrap_or(3), v["now"].as_f64().unwrap_or(0.0), &extras));
        }
        Some("build-dir") => {
            let Some(dir) = args.get(1) else {
                eprintln!("usage: build-dir DIR [--days N] [--now T] [--extra FILE]...");
                std::process::exit(2);
            };
            let flag = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1));
            let days = flag("--days").and_then(|v| v.parse().ok()).unwrap_or(3);
            let now = flag("--now").and_then(|v| v.parse().ok()).unwrap_or(0.0);
            let read = |p: &std::path::Path| std::fs::read(p).ok().map(|b| universal_newlines(&String::from_utf8_lossy(&b)));
            let mut files = Vec::new();
            for e in std::fs::read_dir(dir).into_iter().flatten().flatten() {
                let path = e.path();
                if !std::fs::metadata(&path).map(|m| m.is_file()).unwrap_or(false) {
                    continue;
                }
                let (Some(mtime), Some(text)) = (mtime_of(&path), read(&path)) else { continue };
                files.push(LogFile { name: e.file_name().to_string_lossy().into_owned(), mtime, text: Some(text) });
            }
            let extras: Vec<String> = args.iter().enumerate().filter(|(_, a)| *a == "--extra").filter_map(|(i, _)| args.get(i + 1)).filter_map(|p| read(std::path::Path::new(p))).collect();
            let _ = writeln!(out.lock(), "{}", result(&files, days, now, &extras));
        }
        _ => {
            eprintln!("usage: singws-logpack redact | build | build-dir DIR [--days N] [--now T] [--extra FILE]...");
            std::process::exit(2);
        }
    }
}
