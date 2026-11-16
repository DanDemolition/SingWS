//! W9: the RUNTIME of the web-player port: live sessions of the service machine and the host machine, driven one event at a time by the app (the shadow in
//! `karafun_web_shadow.py` today; the real driver later). The machines stay pure: a session only keeps one machine alive between calls and hands the ordered
//! actions of each event back as JSON. Requests (all JSON objects):
//!   {"op": "svc_new", "has_credentials": bool}        -> {"id": n}
//!   {"op": "svc_step", "id": n, "event": {...}}        -> {"actions": [...]}    (the replay event format of `svc_event`)
//!   {"op": "host_new"}                                 -> {"id": n}
//!   {"op": "host_step", "id": n, "event": {...}}       -> {"actions": [...]}    (the replay event format of `host_event`, with its "world")
//!   {"op": "drop", "id": n}                            -> {"ok": true}
//! Anything else, an unknown id, or an event that is not modelled answers {"err": reason} / {"declined": reason} and never panics. At most `MAX_SESSIONS` live at
//! once (the app makes a handful: one service and one host), so a runaway caller cannot grow memory.
use crate::host::{host_event, Host};
use crate::{svc_event, Machine};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

pub const MAX_SESSIONS: usize = 16;

#[derive(Default)]
struct Sessions {
    next: u64,
    svc: HashMap<u64, Machine>,
    host: HashMap<u64, Host>,
}

fn store() -> &'static Mutex<Sessions> {
    static STORE: OnceLock<Mutex<Sessions>> = OnceLock::new();
    STORE.get_or_init(|| Mutex::new(Sessions::default()))
}

pub fn session(req: &Value) -> Value {
    let mut st = match store().lock() {
        Ok(g) => g,
        Err(poisoned) => poisoned.into_inner(),
    };
    let op = req.get("op").and_then(|x| x.as_str()).unwrap_or("");
    let id = req.get("id").and_then(|x| x.as_u64());
    match op {
        "svc_new" | "host_new" => {
            if st.svc.len() + st.host.len() >= MAX_SESSIONS {
                return json!({"err": "too many sessions"});
            }
            st.next += 1;
            let n = st.next;
            if op == "svc_new" {
                st.svc.insert(n, Machine::new(req.get("has_credentials").and_then(|x| x.as_bool()).unwrap_or(false)));
            } else {
                // The live shadow is fed what Python really did, and Python tells the twin the player is not ready until it has SENT the play (the hold is Python's
                // delay), so the twin runs with no hold of its own. The hold rule is proven by tools/karafun_host_parity.py and takes over when Rust drives the player.
                let mut host = Host::new();
                host.hold_s = 0.0;
                st.host.insert(n, host);
            }
            json!({"id": n})
        }
        "svc_step" | "host_step" => {
            let (Some(id), Some(event)) = (id, req.get("event")) else { return json!({"err": "id and event are required"}) };
            let answer = if op == "svc_step" {
                match st.svc.get_mut(&id) {
                    Some(m) => svc_event(m, event),
                    None => return json!({"err": "unknown session"}),
                }
            } else {
                match st.host.get_mut(&id) {
                    Some(h) => host_event(h, event),
                    None => return json!({"err": "unknown session"}),
                }
            };
            match answer {
                Ok(actions) => json!({"actions": actions}),
                Err(reason) => json!({"declined": reason}),
            }
        }
        "drop" => {
            if let Some(id) = id {
                st.svc.remove(&id);
                st.host.remove(&id);
            }
            json!({"ok": true})
        }
        _ => json!({"err": format!("unknown op {op}")}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(req: Value) -> Value {
        session(&req)
    }

    #[test]
    fn a_service_session_answers_like_the_replay() {
        let id = ev(json!({"op": "svc_new", "has_credentials": true}))["id"].as_u64().unwrap();
        ev(json!({"op": "svc_step", "id": id, "event": {"ev": "run_begin", "t": 0.0}}));
        ev(json!({"op": "svc_step", "id": id, "event": {"ev": "browser_found", "t": 0.0}}));
        ev(json!({"op": "svc_step", "id": id, "event": {"ev": "web_made", "t": 0.0}}));
        let mut last = json!({});
        for i in 0..16 {
            last = ev(json!({"op": "svc_step", "id": id, "event": {"ev": "message", "t": i as f64 * 0.5, "message": {"type": "status", "signedIn": false}, "saved": {"result": "found"}}}));
        }
        let acts = last["actions"].to_string();
        assert!(acts.contains("\"login\""), "the 16th signed-out status signs in with the saved login: {acts}");
        ev(json!({"op": "drop", "id": id}));
        assert_eq!(ev(json!({"op": "svc_step", "id": id, "event": {"ev": "stop", "t": 9.0}}))["err"], "unknown session");
    }

    #[test]
    fn a_host_session_holds_a_song_then_plays_it_when_ready() {
        let id = ev(json!({"op": "host_new"}))["id"].as_u64().unwrap();
        let world = json!({"service_present": true, "service_state": "starting", "has_web": true, "entry_current": true, "active_same": true});
        let a = ev(json!({"op": "host_step", "id": id, "event": {"ev": "start", "t": 100.0, "song_id": 58970, "enabled": true, "key": 0, "tempo": 100, "title": "T", "world": world}}));
        assert!(!a["actions"].to_string().contains("web_play"), "held while the player starts: {a}");
        let ready = json!({"service_present": true, "service_state": "ready", "has_web": true, "entry_current": true, "active_same": true});
        let b = ev(json!({"op": "host_step", "id": id, "event": {"ev": "service", "t": 130.0, "state": "ready", "detail": "Signed in", "world": ready}}));
        assert!(b["actions"].to_string().contains("web_play"), "played as soon as the player is ready: {b}");
    }

    #[test]
    fn bad_requests_answer_errors_and_never_panic() {
        assert!(ev(json!({"op": "nope"}))["err"].is_string());
        assert!(ev(json!({"op": "svc_step"}))["err"].is_string());
        assert!(ev(json!({"op": "svc_step", "id": 999, "event": {}}))["err"].is_string());
        let id = ev(json!({"op": "svc_new"}))["id"].as_u64().unwrap();
        assert!(ev(json!({"op": "svc_step", "id": id, "event": {"ev": "bogus"}}))["declined"].is_string());
        ev(json!({"op": "drop", "id": id}));
    }
}
