//! stdin: {"op":"search_script","query":..,"safe_title":..,"safe_artist":..,"require_exact_title":bool,"resolve_only":bool}
//!        {"op":"match_title","title":..}   {"op":"literal","value":..}     or  {"batch":[ ...requests... ]}
//! stdout: {"lines":[...]} | {"title":..} | {"literal":..}   (a batch answers with a list)
use serde_json::{json, Value};
use singws_karafun::ladder::{self, Answer};
use singws_karafun::monitor::{self, Monitor, Poll};
use singws_karafun::pure::{self, Duration, Verdict};
use singws_karafun::{adjust_menu_script, apply_key_tempo_script, applescript_literal, click_point_script, double_click_script, handoff_script, match_title, search_diag_script,
                     search_script, seek_script, set_key_tempo_script, static_script};
use std::io::Read;

fn answer(v: &Value) -> Value {
    let s = |k: &str| v[k].as_str().unwrap_or("");
    let i = |k: &str| v[k].as_i64().unwrap_or(0);
    match v["op"].as_str().unwrap_or("search_script") {
        "monitor" => run_monitor(v),
        "monitor_replay" => run_monitor_replay(v),
        "ladder" => {
            let queries: Vec<String> = v["queries"].as_array().map(|a| a.iter().map(|q| q.as_str().unwrap_or("").to_string()).collect()).unwrap_or_default();
            let mut answers = v["answers"].as_array().cloned().unwrap_or_default().into_iter();
            let mut next = |_: &str| match answers.next() {
                Some(x) => Answer { ok: x["ok"].as_bool().unwrap_or(false), text: x["text"].as_str().unwrap_or("").to_string(), error: x["error"].as_str().unwrap_or("").to_string() },
                None => Answer { ok: false, text: String::new(), error: "answers exhausted".to_string() },
            };
            let o = ladder::run(&queries, s("safe_title"), s("safe_artist"), &mut next);
            if let Some(why) = o.declined {
                return json!({"decline": why});
            }
            json!({"found": o.found, "selected_query": o.selected_query, "last_error": o.last_error, "raised": o.raised,
                   "log": o.log, "requests": o.requests, "sleeps": o.sleeps})
        }
        "script" => match s("name") {
            "search_diag" => json!({"lines": search_diag_script(s("safe_title"))}),
            "apply_key_tempo" => json!({"lines": apply_key_tempo_script(
                v["key_delta"].as_i64().unwrap_or(0), v["tempo_delta"].as_i64().unwrap_or(0), s("key_item"), s("tempo_item"))}),
            "handoff" => json!({"lines": handoff_script(i("x"), i("y"), i("width"), i("height"))}),
            "click_point" => json!({"lines": click_point_script((i("x"), i("y")))}),
            "adjust_menu" => json!({"lines": adjust_menu_script(s("key_item"), i("key_steps"), s("tempo_item"), i("tempo_steps"))}),
            "set_key_tempo" => json!({"lines": set_key_tempo_script(i("requested_key"), i("requested_tempo"))}),
            "seek" => json!({"lines": seek_script(v["count"].as_i64().unwrap_or(0), s("item"))}),
            "double_click" => json!({"lines": double_click_script(v["x"].as_i64().unwrap_or(0), v["y"].as_i64().unwrap_or(0))}),
            name => match static_script(name) {
                Some(lines) => json!({"lines": lines}),
                None => json!({"error": "unknown script"}),
            },
        },
        "match_title" => json!({"title": match_title(s("title"))}),
        "literal" => json!({"literal": applescript_literal(s("value"))}),
        "duration" => match pure::normalize_duration_seconds(&v["value"]) {
            Duration::Seconds(n) => json!({"seconds": n}),
            Duration::None => json!({"seconds": null}),
            Duration::Decline => json!({"decline": "unicode digits"}),
        },
        "remaining" => json!({"remaining": pure::fallback_remaining(&v["duration"], &v["elapsed_wall"], &v["seek_offset"])}),
        "needs_attention" => json!({"needs_attention": pure::adjustment_needs_attention(
            v["requested_key"].as_i64().unwrap_or(0), v["requested_tempo"].as_i64().unwrap_or(100), s("message"), v["already_applied"].as_bool().unwrap_or(false))}),
        "clean_part" => json!({"clean": pure::clean_search_part(s("value"))}),
        "entry" => {
            let (a, t) = pure::entry_artist_title(s("artist"), s("title"), s("display_name"), s("display"));
            json!({"artist": a, "title": t})
        }
        "queries" => json!({"queries": pure::search_queries_for_entry(s("artist"), s("title"), s("display_name"), s("display"))}),
        "classify" => match pure::classify_search_result(s("candidate"), s("safe_artist"), s("query")) {
            Verdict::Accepted(parts) => json!({"verdict": "accepted", "parts": parts}),
            Verdict::RejectedArtistUnconfirmed(kind) => json!({"verdict": "rejected", "kind": kind}),
            Verdict::NoMatch(err) => json!({"verdict": "no_match", "error": err}),
        },
        _ => json!({"lines": search_script(
            s("query"), s("safe_title"), s("safe_artist"),
            v["require_exact_title"].as_bool().unwrap_or(false), v["resolve_only"].as_bool().unwrap_or(false))}),
    }
}

fn main() {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input).expect("stdin");
    let v: Value = serde_json::from_str(&input).expect("json");
    if let Some(batch) = v["batch"].as_array() {
        println!("{}", Value::Array(batch.iter().map(answer).collect()));
    } else {
        println!("{}", answer(&v));
    }
}

/// A whole scripted run of the completion monitor: every poll consumes one event, the clock advances by the probe time and the sleeps.
fn run_monitor(v: &Value) -> Value {
    let c = &v["cfg"];
    let f = |k: &str| c[k].as_f64().unwrap_or(0.0);
    let b = |k: &str| c[k].as_bool().unwrap_or(false);
    let cfg = monitor::Config {
        title: c["title"].as_str().unwrap_or("").to_string(),
        duration: c["duration"].as_i64().unwrap_or(0),
        duration_estimated: b("duration_estimated"),
        playback_assumed: b("playback_assumed"),
        handoff_timed_out_before_play: b("handoff_timed_out_before_play"),
        controlled_start: b("controlled_start"),
        capture_session: b("capture_session"),
        recovery_delay: f("recovery_delay"),
        handoff_recovery_delay: f("handoff_recovery_delay"),
        alert_delay: f("alert_delay"),
    };
    let now0 = v["now"].as_f64().unwrap_or(100.0);
    let started = v["started"].as_f64().unwrap_or(now0);
    let (mut m, mut log) = match Monitor::new(cfg, now0, started, v["seek_offset"].as_f64().unwrap_or(0.0)) {
        Ok(x) => x,
        Err(why) => return json!({"decline": why}),
    };
    let display_times = v["display_times"].as_array().and_then(|a| Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?)));
    let press_ok = v["press_ok"].as_bool().unwrap_or(true);
    let press_error = v["press_error"].as_str().unwrap_or("").to_string();
    let mut now = now0;
    let seek_offset = v["seek_offset"].as_f64().unwrap_or(0.0);
    let mut kt_busy = false;
    let mut calls: Vec<String> = Vec::new();
    let mut kt_reads = 0;
    let mut polls = 0;
    let events = v["events"].as_array().cloned().unwrap_or_default();
    let mut handoff_left = v["handoff_polls"].as_u64().unwrap_or(0);
    let mut i = 0;
    loop {
        if handoff_left > 0 {
            // the loop waits (0.25 s) while the show-screen hand-off is running; no probe, no event consumed
            handoff_left -= 1;
            polls += 1;
            let out = m.poll(&Poll { now_before: now, handoff_in_progress: true, ..Poll::default() });
            now += out.sleep;
            continue;
        }
        let ended = i >= events.len();
        let (elapsed, raw) = if ended { (0.0, Value::Null) } else { (events[i]["elapsed"].as_f64().unwrap_or(0.0), events[i]["raw"].clone()) };
        i += 1;
        polls += 1;
        let poll = Poll {
            now_before: now,
            handoff_in_progress: false,
            probe_elapsed: elapsed,
            ok: !raw.is_null(),
            raw: raw.as_str().unwrap_or("").to_string(),
            session_ended: ended,
            status_is_manual: false,
            display_times,
            kt_running: false,
            kt_dirty: false,
            kt_busy,
            seek_offset,
            press_result: (press_ok, press_error.clone()),
        };
        let out = m.poll(&poll);
        if let Some(why) = out.declined {
            return json!({"decline": why});
        }
        log.extend(out.log);
        calls.extend(out.calls);
        if out.kt_read_started {
            kt_reads += 1;
            kt_busy = true; // the replay never runs the read thread, so the entry stays busy (like the test harness)
        }
        now += elapsed;
        // a real run ends by itself when the events run out; this bound only stops a broken build from looping forever
        if out.finished || polls > events.len() + 3 {
            break;
        }
        now += out.sleep;
    }
    let e = &m.entry;
    json!({"log": log, "calls": calls, "now": now, "polls": polls, "kt_reads": kt_reads,
           "entry": {"display_started_at": e.display_started_at, "display_duration": e.display_duration, "last_playing_ts": e.last_playing_ts, "paused_at": e.paused_at}})
}

/// Replay of a RECORDED run (the live shadow): every pass carries the inputs the real loop saw. Answers, per pass, what the machine would have done.
fn run_monitor_replay(v: &Value) -> Value {
    let c = &v["cfg"];
    let f = |k: &str| c[k].as_f64().unwrap_or(0.0);
    let b = |k: &str| c[k].as_bool().unwrap_or(false);
    let cfg = monitor::Config {
        title: c["title"].as_str().unwrap_or("").to_string(),
        duration: c["duration"].as_i64().unwrap_or(0),
        duration_estimated: b("duration_estimated"),
        playback_assumed: b("playback_assumed"),
        handoff_timed_out_before_play: b("handoff_timed_out_before_play"),
        controlled_start: b("controlled_start"),
        capture_session: b("capture_session"),
        recovery_delay: f("recovery_delay"),
        handoff_recovery_delay: f("handoff_recovery_delay"),
        alert_delay: f("alert_delay"),
    };
    let now0 = v["now"].as_f64().unwrap_or(0.0);
    let (mut m, _log) = match Monitor::new(cfg, now0, v["started"].as_f64().unwrap_or(now0), v["seek_offset"].as_f64().unwrap_or(0.0)) {
        Ok(x) => x,
        Err(why) => return json!({"decline": why}),
    };
    let mut out_passes: Vec<Value> = Vec::new();
    for p in v["passes"].as_array().cloned().unwrap_or_default() {
        let poll = Poll {
            now_before: p["now_before"].as_f64().unwrap_or(0.0),
            handoff_in_progress: false,
            probe_elapsed: p["probe_elapsed"].as_f64().unwrap_or(0.0),
            ok: p["ok"].as_bool().unwrap_or(false),
            raw: p["raw"].as_str().unwrap_or("").to_string(),
            session_ended: false,
            status_is_manual: p["status_is_manual"].as_bool().unwrap_or(false),
            display_times: p["display_times"].as_array().and_then(|a| Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?))),
            kt_running: p["kt_running"].as_bool().unwrap_or(false),
            kt_dirty: p["kt_dirty"].as_bool().unwrap_or(false),
            kt_busy: p["kt_busy"].as_bool().unwrap_or(false),
            seek_offset: p["seek_offset"].as_f64().unwrap_or(0.0),
            press_result: (p["press_ok"].as_bool().unwrap_or(true), p["press_error"].as_str().unwrap_or("").to_string()),
        };
        let out = m.poll(&poll);
        if let Some(why) = out.declined {
            return json!({"decline": why});
        }
        // the decisions that matter: which actions the loop took on this pass
        let mut events: Vec<&str> = Vec::new();
        if out.calls.iter().any(|c| c == "press_play") {
            events.push("press");
        }
        if out.calls.iter().any(|c| c.starts_with("status:Cannot read KaraFun playback status") || c.starts_with("status:KaraFun is not playing this song")) {
            events.push("alert");
        }
        if out.kt_read_started {
            events.push("kt");
        }
        if out.finished {
            events.push("complete");
        }
        out_passes.push(json!({"events": events}));
        if out.finished {
            break;
        }
    }
    json!({"passes": out_passes})
}
