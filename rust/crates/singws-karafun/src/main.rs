//! stdin: {"op":"search_script","query":..,"safe_title":..,"safe_artist":..,"require_exact_title":bool,"resolve_only":bool}
//!        {"op":"match_title","title":..}   {"op":"literal","value":..}     or  {"batch":[ ...requests... ]}
//! stdout: {"lines":[...]} | {"title":..} | {"literal":..}   (a batch answers with a list)
use serde_json::{json, Value};
use singws_karafun::pure::{self, Duration, Verdict};
use singws_karafun::{applescript_literal, match_title, search_script};
use std::io::Read;

fn answer(v: &Value) -> Value {
    let s = |k: &str| v[k].as_str().unwrap_or("");
    match v["op"].as_str().unwrap_or("search_script") {
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
