//! Live runner (dev Mac only, never during a show): builds KaraFun scripts with this crate and runs them through `singws-mac`'s AppleScript runner against the
//! REAL KaraFun, so the Rust-built scripts and the Rust search ladder can be compared with what the Python app does on the same KaraFun state.
//!
//!   singws-karafun-live probe                                 read-only: window state, playback menu state, Apple-events preflight, monitor probe
//!   singws-karafun-live search  <query> <title> <artist>      ONE exact search (types the query into KaraFun's search field)
//!   singws-karafun-live ladder  <artist> <title>              the whole search ladder for a queue entry (types each query in turn until a row is accepted)
use singws_karafun::ladder::{self, Answer, Request};
use singws_karafun::pure::search_queries_for_entry;
use singws_karafun::{search_diag_script, search_script, static_script};
use singws_mac::run_applescript;
use std::time::Duration;

fn run(lines: &[String], secs: u64) -> Answer {
    // the app runs a search with an 18 s limit; SINGWS_LIVE_TIMEOUT overrides it when KaraFun is slow to answer
    let secs = std::env::var("SINGWS_LIVE_TIMEOUT").ok().and_then(|v| v.parse().ok()).unwrap_or(secs);
    let r = run_applescript(&lines.join("\n"), Duration::from_secs(secs));
    Answer { ok: r.ok, text: r.out.trim().to_string(), error: r.err }
}

fn main() {
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
            println!("found={:?} selected_query={:?} raised={:?}", o.found, o.selected_query, o.raised);
        }
        _ => eprintln!("usage: singws-karafun-live probe | search <query> <title> <artist> | ladder <artist> <title>"),
    }
}
