//! Live runner (dev Mac only, never during a show): builds KaraFun scripts with this crate and runs them through `singws-mac`'s AppleScript runner against the
//! REAL KaraFun, so the Rust-built scripts and the Rust search ladder can be compared with what the Python app does on the same KaraFun state.
//!
//!   singws-karafun-live probe                                 read-only: window state, playback menu state, Apple-events preflight, monitor probe
//!   singws-karafun-live search  <query> <title> <artist>      ONE exact search (types the query into KaraFun's search field)
//!   singws-karafun-live ladder  <artist> <title>              the whole search ladder for a queue entry (types each query in turn until a row is accepted)
use singws_karafun::ladder::{self, Answer, Request};
use singws_karafun::pure::search_queries_for_entry;
use singws_karafun::{click_point_script, double_click_script, search_diag_script, search_script, static_script};
use singws_mac::run_applescript;
use std::time::Duration;

fn run(lines: &[String], secs: u64) -> Answer {
    // the app runs a search with an 18 s limit; SINGWS_LIVE_TIMEOUT overrides it when KaraFun is slow to answer
    let secs = std::env::var("SINGWS_LIVE_TIMEOUT").ok().and_then(|v| v.parse().ok()).unwrap_or(secs);
    let r = run_applescript(&lines.join("\n"), Duration::from_secs(secs));
    Answer { ok: r.ok, text: r.out.trim().to_string(), error: r.err, abort: None }
}


/// Answers the worker's questions from the real KaraFun. `kind` is the question; `calls` are the calls the worker has recorded so far (the last ones say what is being asked).
fn live_answer(kind: &str, calls: &[serde_json::Value]) -> serde_json::Value {
    use serde_json::json;
    let last = |name: &str| calls.iter().rev().find(|c| c[0] == name).cloned();
    let script_for = |label: &str| -> Option<Vec<String>> {
        if let Some(rest) = label.strip_prefix("kt-script:") {
            let mut it = rest.split(':');
            let (k, t) = (it.next()?.parse().ok()?, it.next()?.parse().ok()?);
            return Some(singws_karafun::set_key_tempo_script(k, t));
        }
        Some(match label {
            "search-script" => {
                let c = last("search_script")?;
                let kw = &c[2];
                search_script(kw["query"].as_str()?, kw["safe_title"].as_str()?, kw["safe_artist"].as_str()?, true, false)
            }
            "diag-script" => search_diag_script(last("search_diag_script")?[1][0].as_str()?),
            "probe-script" => static_script("start_probe")?,
            "ready-script" => static_script("renderer_ready")?,
            "click-script" => {
                let p = &last("click_point_script")?[1][0];
                click_point_script((p[0].as_i64()?, p[1].as_i64()?))
            }
            _ => return None,
        })
    };
    let native = |x: i64, y: i64, n: u32| -> bool {
        std::process::Command::new("python3").arg("tools/native_click.py").arg(x.to_string()).arg(y.to_string()).arg(n.to_string()).status().map(|s| s.success()).unwrap_or(false)
    };
    let triple = |a: Answer| json!([a.ok, a.text, a.error]);
    match kind {
        "running" => json!(std::process::Command::new("pgrep").args(["-x", "KaraFun"]).output().map(|o| o.status.success()).unwrap_or(false)),
        "open" => {
            let up = std::process::Command::new("pgrep").args(["-x", "KaraFun"]).output().map(|o| o.status.success()).unwrap_or(false);
            json!(up || std::process::Command::new("open").args(["-a", "KaraFun"]).status().map(|s| s.success()).unwrap_or(false))
        }
        "wait" => {
            for _ in 0..30 {
                if run(&static_script("main_window_state").unwrap_or_default(), 10).text.trim() == "READY" {
                    return json!("");
                }
                std::thread::sleep(Duration::from_secs(1));
            }
            json!("KaraFun main window not ready")
        }
        "preflight" => {
            let a = run(&static_script("preflight").unwrap_or_default(), 10);
            json!([a.ok && a.text.starts_with("AUTHORIZED"), if a.error.is_empty() { a.text } else { a.error }])
        }
        // the audio-route guard needs the app's output list: not checked by the live runner (it is proven by the machine twin)
        "audio" => json!([true, "not checked by the live runner"]),
        "clock" => {
            let raw = last("clock_seconds").and_then(|c| c[1][0].as_str().map(String::from)).unwrap_or_default();
            let mut it = raw.split(':').filter_map(|n| n.trim().parse::<f64>().ok());
            match (it.next(), it.next()) { (Some(m), Some(s)) => json!(m * 60.0 + s), _ => json!(0.0) }
        }
        "apply_duration" => json!(false),
        "session_script" | "diag" => {
            let c = last("session_script").unwrap_or_default();
            let label = c[1][0].as_str().unwrap_or("").to_string();
            let secs = c[2]["timeout"].as_f64().unwrap_or(10.0) as u64;
            match script_for(&label) {
                Some(lines) => triple(run(&lines, secs)),
                None => json!([true, "", ""]), // labels for window moves of the managed hand-off: nothing to do without the app
            }
        }
        "menu_state" => json!(run(&static_script("playback_menu_state").unwrap_or_default(), 10).text.trim().to_string()),
        "call:raise_results" => { run(&static_script("raise_results_window").unwrap_or_default(), 4); json!(null) }
        "call:native_double_click" => {
            let c = calls.last().cloned().unwrap_or_default();
            if let (Some(x), Some(y)) = (c[1][0].as_i64(), c[1][1].as_i64()) {
                println!("  [live] native double-click at ({x},{y}): {}", native(x, y, 2));
            }
            json!(null)
        }
        s if s.starts_with("call:") => json!(null),
        "activate" if std::env::var("SINGWS_LIVE_NATIVE").is_ok() => {
            let p = &last("activate_result").unwrap_or_default()[1][0];
            let (x, y) = (p[0].as_i64().unwrap_or(0), p[1].as_i64().unwrap_or(0));
            run(&static_script("raise_results_window").unwrap_or_default(), 4);
            std::thread::sleep(Duration::from_millis(350));
            json!([native(x, y, 2), ""])
        }
        "activate" => {
            let p = &last("activate_result").unwrap_or_default()[1][0];
            let a = run(&double_click_script(p[0].as_i64().unwrap_or(0), p[1].as_i64().unwrap_or(0)), 15);
            json!([a.ok && a.text.contains("DOUBLE_CLICKED"), if a.error.is_empty() && !a.text.contains("DOUBLE_CLICKED") { a.text } else { a.error }])
        }
        "press" => {
            let a = run(&static_script("press_play").unwrap_or_default(), 15);
            let t = a.text.trim().to_string();
            let parts: Vec<&str> = t.split('|').collect();
            if !a.ok {
                json!([false, a.error])
            } else if t == "ALREADY_PLAYING" || t == "MENU_PLAY" {
                json!([true, ""])
            } else if parts.len() == 3 && (parts[0] == "PLAY" || parts[0] == "PLAY_NEXT") {
                let (x, y) = (parts[1].parse::<f64>().unwrap_or(0.0) as i64, parts[2].parse::<f64>().unwrap_or(0.0) as i64);
                println!("  [live] pressing play control {} at ({x},{y})", parts[0]);
                { let ok = native(x, y, 1); json!([ok, if ok { "" } else { "Could not click the KaraFun play control" }]) }
            } else {
                json!([false, parts.last().copied().unwrap_or("KaraFun did not expose a play control")])
            }
        }
        "mouse_click" | "handoff_complete" | "event_wait" => json!(true),
        "in_progress" => json!(false),
        "frontmost" => json!(""),
        _ => json!(null),
    }
}

fn main() {
    // This is a command-line process (no Cocoa run loop): use the osascript runner, see singws_mac::run_applescript. SINGWS_APPLESCRIPT_RUNNER=inprocess overrides.
    if std::env::var_os("SINGWS_APPLESCRIPT_RUNNER").is_none() {
        std::env::set_var("SINGWS_APPLESCRIPT_RUNNER", "osascript");
    }
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("probe") => {
            for name in ["preflight", "main_window_state", "playback_menu_state", "monitor_probe"] {
                let lines = static_script(name).unwrap_or_else(|| {
                    // monitor_probe is a builder of the app's monitor; it is exposed by name through the same table
                    Vec::new()
                });
                if lines.is_empty() {
                    println!("{name}: (not in the static table)");
                    continue;
                }
                let a = run(&lines, if name == "monitor_probe" { 40 } else { 10 });
                println!("{name}: ok={} out={:?} err={:?}", a.ok, a.text.chars().take(160).collect::<String>(), a.error.chars().take(120).collect::<String>());
            }
        }
        Some("search") if args.len() >= 4 => {
            let lines = search_script(&args[1], &args[2], &args[3], true, false);
            let a = run(&lines, 18);
            println!("ok={} out={:?} err={:?}", a.ok, a.text, a.error);
        }
        Some("ladder") if args.len() >= 3 => {
            let (artist, title) = (&args[1], &args[2]);
            let queries = search_queries_for_entry(artist, title, "", "");
            println!("queries: {queries:?}");
            let o = ladder::run_with(&queries, title, artist, &mut |req| match req {
                Request::Search { query } => run(&search_script(query, title, artist, true, false), 18),
                Request::Diag => run(&search_diag_script(title), 20),
            });
            for l in &o.log {
                println!("{l}");
            }
            println!("found={:?} selected_query={:?} raised={:?} declined={:?}", o.found, o.selected_query, o.raised, o.declined);
        }
        Some("play") if args.len() >= 3 => {
            // ONE song, for real: ladder -> double-click the result -> the Rust completion monitor over the real probe, until it finishes (cap: SINGWS_LIVE_PLAY_CAP seconds).
            use singws_karafun::monitor::{Config, Monitor, Poll};
            let (artist, title) = (&args[1], &args[2]);
            let queries = search_queries_for_entry(artist, title, "", "");
            let o = ladder::run_with(&queries, title, artist, &mut |req| match req {
                Request::Search { query } => run(&search_script(query, title, artist, true, false), 18),
                Request::Diag => run(&search_diag_script(title), 20),
            });
            for l in &o.log {
                println!("{l}");
            }
            if o.found.is_empty() {
                println!("no match: {:?}", o.raised);
                return;
            }
            let parts: Vec<&str> = o.found.split('|').collect();
            let (x, y): (i64, i64) = (parts[1].parse().unwrap_or(0), parts[2].parse().unwrap_or(0));
            let dur = parts.get(3).map(|d| { let mut it = d.split(':').filter_map(|n| n.parse::<i64>().ok()); let m = it.next().unwrap_or(0); m * 60 + it.next().unwrap_or(0) }).unwrap_or(0);
            println!("found {} ; double-clicking ({x},{y}); duration {dur}s", o.found);
            let a = run(&double_click_script(x, y), 15);
            println!("double_click ok={} out={:?} err={:?}", a.ok, a.text, a.error);
            let t0 = std::time::Instant::now();
            let now = |t0: &std::time::Instant| t0.elapsed().as_secs_f64();
            let cfg = Config { title: title.clone(), duration: dur, duration_estimated: false, playback_assumed: false, handoff_timed_out_before_play: false,
                               controlled_start: true, capture_session: false, recovery_delay: 12.0, handoff_recovery_delay: 6.0, alert_delay: 40.0 };
            let (mut m, log) = match Monitor::new(cfg, now(&t0), now(&t0), 0.0) { Ok(x) => x, Err(e) => { println!("monitor declined: {e}"); return; } };
            for l in log { println!("{l}"); }
            let cap: f64 = std::env::var("SINGWS_LIVE_PLAY_CAP").ok().and_then(|v| v.parse().ok()).unwrap_or(dur as f64 + 90.0);
            let probe = static_script("monitor_probe").unwrap_or_default();
            let mut passes = 0;
            loop {
                let before = now(&t0);
                if before > cap { println!("cap reached at {before:.0}s, stopping the loop (song left as is)"); break; }
                let a = run(&probe, 40);
                let elapsed = now(&t0) - before;
                passes += 1;
                let mut press = (true, String::new());
                let mut out = m.poll(&Poll { now_before: before, probe_elapsed: elapsed, ok: a.ok, raw: a.text.clone(), seek_offset: 0.0, press_result: press.clone(), ..Default::default() });
                if out.calls.iter().any(|c| c == "press_play") {
                    let r = run(&static_script("press_play").unwrap_or_default(), 15);
                    println!("pressed play ok={} out={:?}", r.ok, r.text);
                    press = (r.ok, r.error);
                    out = m.poll(&Poll { now_before: before, probe_elapsed: elapsed, ok: a.ok, raw: a.text.clone(), seek_offset: 0.0, press_result: press, ..Default::default() });
                }
                if passes % 10 == 1 || !out.calls.is_empty() || out.finished {
                    println!("pass {passes} t={:.0}s probe={:?} calls={:?}", now(&t0), a.text.replace('\n', " / "), out.calls);
                }
                for l in &out.log { println!("{l}"); }
                if out.finished { println!("monitor finished after {:.0}s, {passes} passes", now(&t0)); break; }
                if out.sleep > 0.0 { std::thread::sleep(Duration::from_secs_f64(out.sleep)); }
            }
        }
        Some("run") if args.len() >= 3 => {
            // The WHOLE worker (intake, setup, ladder, prelude, flow, finish), proven identical to the app's Python on scripted worlds, against the REAL KaraFun.
            use serde_json::json;
            let (artist, title) = (args[1].clone(), args[2].clone());
            let fast = std::env::var("SINGWS_LIVE_FAST").map(|v| v != "0").unwrap_or(true);
            let sc = singws_karafun::worker::Scenario {
                settings: json!({"karafun_auto_queue_enabled": true, "karafun_manage_show_screen": false, "karafun_dual_renderer_capture": false,
                                 "karafun_fast_start_enabled": fast, "karafun_auto_key_tempo": true}),
                entry: serde_json::from_value(json!({"singer": "Live Test", "artist": artist, "title": title})).unwrap(),
                artist: artist.clone(), title: title.clone(), key: json!(0), tempo: json!(100), request_uid: "live-test-1".into(), queries: vec![],
                end_session_at: None, capture_session: Some(false), handoff_error: None, needs_attention: false, launch_error: None, active_ok: true,
            };
            let o = singws_karafun::worker::run_live(&sc, Box::new(live_answer));
            for l in o.world["log"].as_array().cloned().unwrap_or_default() {
                println!("{}", l.as_str().unwrap_or(""));
            }
            for c in o.world["calls"].as_array().cloned().unwrap_or_default() {
                println!("  call {}", c.to_string().chars().take(160).collect::<String>());
            }
            println!("returned={} declined={} order_error={} final_clock={}", o.returned, o.world["declined"], o.world["order_error"], o.world["final_clock"]);
            println!("entry status/message: {} / {}", o.entry.get("status").unwrap_or(&json!(null)), o.entry.get("karafun_message").or(o.entry.get("message")).unwrap_or(&json!(null)));
            println!("entry keys: {:?}", o.entry.keys().collect::<Vec<_>>());
        }
        _ => eprintln!("usage: singws-karafun-live probe | search <query> <title> <artist> | ladder <artist> <title>"),
    }
}
