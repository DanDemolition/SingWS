//! Batch driver: stdin {"batch":[{"op":"locfix","events":[["fix",obj,accuracy,age],["error",code,text],..]},..]} -> answers.
use serde_json::{json, Value};
use singws_misc::LocationFixCollector;
use std::io::Read;

/// A scripted run of the announcement box: events [["cmd", action, args, now, env], ["tick", now, playing, env]] -> per event [ok, message, effects].
fn announce_script(r: &Value) -> Value {
    use singws_misc::announce::{AnnounceBox, Env};
    let env_of = |e: &Value| Env {
        engine_master: e["master"].as_f64(),
        engine_closed: e["closed"].as_bool().unwrap_or(false),
        soundboard_volume: e["volume"].as_f64().unwrap_or(1.0),
        load_fails: e["load_fails"].as_bool().unwrap_or(false),
    };
    let mut b = AnnounceBox::default();
    let mut out = vec![];
    for ev in r["events"].as_array().cloned().unwrap_or_default() {
        b.effects.clear();
        match ev[0].as_str().unwrap_or("") {
            "cmd" => match b.handle(ev[1].as_str().unwrap_or(""), &ev[2], ev[3].as_f64().unwrap_or(0.0), &env_of(&ev[4])) {
                Ok((ok, msg)) => out.push(json!([ok, msg, b.effects.clone()])),
                Err(why) => return json!({"declined": why}),
            },
            _ => {
                b.tick(ev[1].as_f64().unwrap_or(0.0), ev[2].as_bool().unwrap_or(false), &env_of(&ev[3]));
                out.push(json!([true, "tick", b.effects.clone()]));
            }
        }
    }
    json!({"v": out})
}

fn one(r: &Value) -> Value {
    match r["op"].as_str().unwrap_or("") {
        "locfix" => {
            let mut c = LocationFixCollector::default();
            let mut stops = vec![];
            for e in r["events"].as_array().cloned().unwrap_or_default() {
                let res = match e[0].as_str().unwrap_or("") {
                    "fix" => c.add_fix(&e[1], &e[2], &e[3]),
                    _ => c.add_error(&e[1], e[2].as_str().unwrap_or("")),
                };
                match res {
                    Ok(b) => stops.push(b),
                    Err(why) => return json!({"declined": why}),
                }
            }
            let (fix, err) = c.result();
            json!({"stops": stops, "fix": fix, "error": err})
        }
        "audio_priority" => match singws_misc::local_audio_output_priority(&r["name"], &r["kind"]) {
            Ok((n, t)) => json!({"v": [n, t]}),
            Err(e) => json!({"declined": e}),
        },
        "tombstone" => match singws_misc::tombstone_artist_title(&r["entry"]) {
            Ok((a, t)) => json!({"v": [a, t]}),
            Err(e) => json!({"declined": e}),
        },
        "next_active" => json!({"v": singws_misc::next_active_entry_after(r["songs"].as_array().map(|a| a.as_slice()).unwrap_or(&[]), r["index"].as_u64().map(|i| i as usize))}),
        "request_ts" => match singws_misc::remote_request_timestamp(&r["req"]) {
            Ok(f) => json!({"v": f}),
            Err(e) => json!({"declined": e}),
        },
        "audio_path" => match singws_misc::resolve_karaoke_audio_path(&r["song_info"]) {
            Ok(p) => json!({"v": p}),
            Err(e) => json!({"declined": e}),
        },
        "track_row" => json!({"v": singws_misc::track_from_search_row(&r["row"])}),
        "singer_name" => json!({"v": singws_misc::capitalize_singer_name(r["name"].as_str().unwrap_or(""))}),
        "voice_key" => json!({"v": singws_misc::voice::cache_key(r["path"].as_str().unwrap_or(""), r["size"].as_u64().unwrap_or(0), r["mtime"].as_i64().unwrap_or(0))}),
        "voice_usable" => json!({"v": singws_misc::voice::copy_is_usable(r["exists"].as_bool().unwrap_or(false), r["size"].as_u64().unwrap_or(0))}),
        "voice_args" => json!({"v": singws_misc::voice::afconvert_args(r["src"].as_str().unwrap_or(""), r["tmp"].as_str().unwrap_or(""))}),
        "voice_ok" => json!({"v": singws_misc::voice::conversion_ok(r["code"].as_i64().unwrap_or(0), r["exists"].as_bool().unwrap_or(false), r["size"].as_u64().unwrap_or(0))}),
        "voice_prune" => {
            let list = |k: &str| -> Vec<(String, f64)> { r[k].as_array().map(|a| a.iter().map(|e| (e[0].as_str().unwrap_or("").to_string(), e[1].as_f64().unwrap_or(0.0))).collect()).unwrap_or_default() };
            json!({"v": singws_misc::voice::prune_plan(&list("wavs"), &list("tmps"), r["now"].as_f64().unwrap_or(0.0))})
        }
        "announce" => announce_script(r),
        "mp3_pair" => json!({"v": singws_misc::find_mp3_pair_for_cdg(r["path"].as_str().unwrap_or(""))}),
        other => json!({"declined": format!("unknown op {other}")}),
    }
}

fn main() {
    let mut s = String::new();
    std::io::stdin().read_to_string(&mut s).unwrap();
    let v: Value = serde_json::from_str(&s).unwrap();
    let out: Vec<Value> = v["batch"].as_array().map(|a| a.iter().map(one).collect()).unwrap_or_default();
    println!("{}", Value::Array(out));
}
