//! The WHOLE of `KaraokeApp._automate_karafun_search_and_play` (0.2.18.1.py), orchestration included: the guards, the intake, the worker thread (started inline), the setup, the
//! search ladder, the stretch before the activation, the activation/start flow with the REAL nested closures (once-only BGM fade and early hand-off, the watcher thread, the native
//! double-click), the exception-to-result rule, and `_finish` run on the (inline) UI thread. Every stretch is the module that was proven alone against the real Python statements;
//! this file only chains them on ONE world (one scripted answer list, one clock, one entry, one session counter) the way the method does.
//! tools/karafun_worker_parity.py runs the real method (tools/karafun_worker_replay.py) against it.
use crate::finish;
use crate::flow::{self, World};
use crate::intake;
use crate::ladder::{self, Answer, Request};
use crate::prelude;
use crate::setup;
use serde_json::{json, Map, Value};

pub struct Scenario {
    pub settings: Value,
    pub entry: Map<String, Value>,
    pub artist: String,
    pub title: String,
    pub key: Value,
    pub tempo: Value,
    pub request_uid: String,
    pub queries: Vec<String>,
    pub end_session_at: Option<i64>,
    pub capture_session: Option<bool>,
    pub handoff_error: Option<String>,
    pub needs_attention: bool,
    pub launch_error: Option<String>,
    pub active_ok: bool,
}

pub struct Outcome {
    pub returned: Value,
    pub entry: Map<String, Value>,
    pub world: Value,
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map_or(true, |f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn setting(sc: &Scenario, key: &str, default: bool) -> bool {
    match sc.settings.get(key) {
        Some(v) => truthy(v),
        None => default,
    }
}

fn finish_out(sc: &Scenario, w: &mut World, ok: bool, message: &str, entry: &Map<String, Value>, request_uid: &str, key: i64, tempo: i64, signature: &str) -> Map<String, Value> {
    w.call("ui_thread", vec![], json!({}));
    let current = w.session_current();
    let now = (1_700_000_000.0 + w.t).floor() as i64;
    let o = finish::run(&finish::Scenario {
        now,
        ok,
        message: message.to_string(),
        current,
        handoff_error: sc.handoff_error.clone(),
        needs_attention: sc.needs_attention,
        apple_err: ladder::is_apple_events_error(message),
        ax_err: ladder::is_accessibility_error(message),
        capture_session: sc.capture_session.unwrap_or(false),
        adjustment_signature: signature.to_string(),
        request_uid: request_uid.to_string(),
        requested_key: key,
        requested_tempo: tempo,
        title: sc.title.clone(),
        entry: entry.clone(),
    });
    if o.declined.is_some() {
        w.declined = o.declined.clone();
    }
    // finish's own fake records `activate_host` without arguments; the whole-method world records the session dict it was given
    let session = match w.session_restored {
        Some(r) => json!({"id": 1, "host_focus_restored": r}),
        None => json!({"id": 1}),
    };
    for mut c in o.calls {
        if c[0] == "activate_host" {
            c[1] = json!([session.clone()]);
        }
        w.calls.push(c);
    }
    w.log.extend(o.log);
    o.entry
}

pub fn run(sc: &Scenario, answers: &[Value]) -> Outcome {
    let mut w = World::new(answers, sc.end_session_at);
    w.unified = true;
    w.native = setting(sc, "karafun_native_double_click", false);
    let mut entry = sc.entry.clone();
    let returned = method(sc, &mut w, &mut entry);
    let world = json!({"calls": w.calls, "log": w.log, "sleeps": w.sleeps, "final_clock": w.t, "order_error": w.order_error, "declined": w.declined, "consumed": w.pos});
    Outcome { returned, entry, world }
}

fn method(sc: &Scenario, w: &mut World, entry: &mut Map<String, Value>) -> Value {
    if !setting(sc, "karafun_auto_queue_enabled", false) || !sc.active_ok {
        return json!({"returned": false});
    }
    // ---- intake (before the worker thread)
    let io = intake::run(&intake::Scenario { entry: entry.clone(), artist: sc.artist.clone(), title: sc.title.clone(), key: sc.key.clone(), tempo: sc.tempo.clone(),
                                              request_uid: sc.request_uid.clone(), queries: sc.queries.clone() });
    if let Some(d) = io.declined {
        w.declined = Some(d);
        return json!({});
    }
    // intake's own fake records set_status without `notify`; the whole-method world records the keyword it was called with (always None here)
    for c in io.calls {
        let mut c = c;
        if c[0] == "set_status" {
            if let Some(kw) = c[2].as_object_mut() {
                kw.insert("notify".into(), Value::Null);
            }
        }
        w.calls.push(c);
    }
    w.log.extend(io.log);
    *entry = io.entry;
    if let Some(r) = io.result.get("raised") {
        return json!({"raised": r});
    }
    if io.result.get("returned") == Some(&json!(false)) {
        return json!({"returned": false});
    }
    let key = io.result["requested_key"].as_i64().unwrap_or(0);
    let tempo = io.result["requested_tempo"].as_i64().unwrap_or(100);
    let signature = io.result["adjustment_signature"].as_str().unwrap_or("").to_string();
    let needs_adjustment = io.result["needs_adjustment"].as_bool().unwrap_or(false);
    let request_uid = io.result["request_uid"].as_str().unwrap_or("").to_string();
    let safe_title = io.result["safe_title"].as_str().unwrap_or("").to_string();
    let queries: Vec<String> = io.result["search_queries"].as_array().map(|a| a.iter().map(|x| x.as_str().unwrap_or("").to_string()).collect()).unwrap_or_default();

    // ---- the worker thread, started and run inline
    w.call("thread", vec![], json!({"thread_name": "karafun-search-play"}));
    let outcome = worker(sc, w, entry, key, tempo, &signature, needs_adjustment, &safe_title, &queries);
    let (ok, message) = match outcome {
        Ok(m) => (true, m),
        Err(m) => (false, m),
    };
    if w.declined.is_some() {
        return json!({});
    }
    *entry = finish_out(sc, w, ok, &message, entry, &request_uid, key, tempo, &signature);
    json!({"returned": true})
}

#[allow(clippy::too_many_arguments)]
fn worker(sc: &Scenario, w: &mut World, entry: &mut Map<String, Value>, key: i64, tempo: i64, signature: &str, needs_adjustment: bool, safe_title: &str, queries: &[String]) -> Result<String, String> {
    // setup
    let was_running = setup::setup(w, &setup::Scenario { entry: entry.clone(), launch_error: sc.launch_error.clone(), end_session_at: sc.end_session_at }, entry)?;
    // the search ladder
    let artist = crate::py_strip(&sc.artist).to_string();
    let lo = {
        let title = safe_title.to_string();
        let mut raised_by_session: Option<String> = None;
        let o = {
            let wr = &mut *w;
            let mut runner = |r: &Request| -> Answer {
                match r {
                    Request::Search { query } => {
                        wr.call("search_script", vec![], json!({"query": query, "require_exact_title": true, "safe_artist": artist, "safe_title": title}));
                        if wr.require().is_err() {
                            raised_by_session = Some("KaraFun launch cancelled: playback session ended".to_string());
                            return Answer { ok: false, text: String::new(), error: String::new(), abort: raised_by_session.clone() };
                        }
                        wr.call("session_script", vec![json!("search-script")], json!({"timeout": 18}));
                        let v = wr.ask("session_script");
                        let a = Answer { ok: v[0].as_bool().unwrap_or(false), text: v[1].as_str().unwrap_or("").to_string(), error: v[2].as_str().unwrap_or("").to_string(), abort: None };
                        if wr.require().is_err() {
                            return Answer { abort: Some("KaraFun launch cancelled: playback session ended".to_string()), ..a };
                        }
                        a
                    }
                    Request::Diag => {
                        wr.call("search_diag_script", vec![json!(title)], json!({}));
                        wr.call("session_script", vec![json!("diag-script")], json!({"timeout": 20}));
                        let v = wr.ask("diag");
                        Answer { ok: v[0].as_bool().unwrap_or(false), text: v[1].as_str().unwrap_or("").to_string(), error: v[2].as_str().unwrap_or("").to_string(), abort: None }
                    }
                }
            };
            ladder::run_with(queries, safe_title, &artist, &mut runner)
        };
        o
    };
    if let Some(d) = &lo.declined {
        w.declined = Some(d.clone());
        return Err("declined".to_string());
    }
    w.log.extend(lo.log.clone());
    for _ in 0..lo.sleeps {
        w.sleep(1.5);
    }
    if let Some(r) = lo.raised {
        return Err(r);
    }
    let found = lo.found;
    // between the ladder and the activation
    let queued = prelude::prelude(
        w,
        &prelude::Scenario {
            auto_key_tempo: setting(sc, "karafun_auto_key_tempo", true),
            found: found.clone(),
            last_error: lo.last_error.clone(),
            needs_adjustment,
            adjustment_signature: signature.to_string(),
            requested_key: key,
            requested_tempo: tempo,
            selected_query: lo.selected_query.clone(),
            entry: entry.clone(),
            end_session_at: sc.end_session_at,
        },
        entry,
    )?;
    // the activation and start
    let parts: Vec<String> = found.split('|').map(|s| s.to_string()).collect();
    flow::flow(
        w,
        &flow::Scenario {
            capture: setting(sc, "karafun_dual_renderer_capture", false),
            manage: setting(sc, "karafun_manage_show_screen", true),
            fast_enabled: setting(sc, "karafun_fast_start_enabled", true),
            parts,
            queued,
            needs_adjustment,
            adjustment_signature: signature.to_string(),
            requested_key: key,
            requested_tempo: tempo,
            was_running,
            selected_query: lo.selected_query,
            title: sc.title.clone(),
            entry: entry.clone(),
            end_session_at: sc.end_session_at,
        },
        entry,
    )
}
