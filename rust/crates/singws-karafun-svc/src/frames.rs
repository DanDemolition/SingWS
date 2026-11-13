//! W6 (the part that can be proved offline): the frame hand-off rules of `karafun_web_host.py` (`_on_frame`, `_paint_latest`, `_start_frames`, `_stop_frames`),
//! transcribed from the Python (the oracle). Pure: the caller says what exists (windows, a decodable picture, the player's helper) and gets the ordered effects.
//! NOT here (needs a real screen or the live app): painting the picture into the audience window, its look, the 30 fps timing on real hardware.
use serde_json::{json, Value};

pub const FRAME_INTERVAL_MS: i64 = 33;
pub const SCREENCAST: (i64, i64, i64) = (1280, 720, 85);

#[derive(Debug, Default, Clone)]
pub struct Frames {
    latest: Option<i64>,
    shown: bool,
    timer_made: bool,
}

pub struct Scene {
    pub has_web: bool,
    pub vw: bool,
    pub preview: bool,
    pub decodes: bool,
}

impl Frames {
    pub fn new() -> Self {
        Self::default()
    }

    /// `_on_frame`: only the newest picture is kept.
    pub fn on_frame(&mut self, id: i64) -> Vec<Value> {
        self.latest = Some(id);
        vec![]
    }

    /// `_paint_latest` (the timer tick).
    pub fn paint(&mut self, s: &Scene) -> Vec<Value> {
        let Some(id) = self.latest.take() else { return vec![] };
        if !s.decodes {
            return vec![];
        }
        if !(s.vw && s.preview) {
            return vec![]; // the picture was taken and is dropped
        }
        self.shown = true;
        vec![json!(["vw_idle", false]), json!(["vw_frame", id, true]), json!(["preview_force_black", false]), json!(["preview_frame", id])]
    }

    pub fn start(&mut self, s: &Scene) -> Vec<Value> {
        if !s.has_web {
            return vec![];
        }
        let mut o = vec![json!(["hide_mpv_hosts"]), json!(["audience", true]), json!(["screencast", true, SCREENCAST.0, SCREENCAST.1, SCREENCAST.2])];
        if !self.timer_made {
            self.timer_made = true;
            o.push(json!(["timer_new", FRAME_INTERVAL_MS]));
        }
        o.push(json!(["timer_start"]));
        o
    }

    pub fn stop(&mut self, s: &Scene) -> Vec<Value> {
        let mut o = Vec::new();
        if self.timer_made {
            o.push(json!(["timer_stop"]));
        }
        if s.has_web {
            o.push(json!(["screencast", false]));
        }
        self.latest = None;
        if self.shown {
            self.shown = false;
            if s.vw {
                o.push(json!(["vw_frame_clear"]));
                o.push(json!(["vw_idle", true]));
            }
            if s.preview {
                o.push(json!(["preview_frame_clear"]));
            }
        }
        o
    }
}

pub fn handle(req: &Value) -> Value {
    let mut f = Frames::new();
    let mut trace = Vec::new();
    let empty = vec![];
    for e in req.get("events").and_then(|x| x.as_array()).unwrap_or(&empty) {
        let b = |k: &str| e.get(k).and_then(|x| x.as_bool()).unwrap_or(true);
        let s = Scene { has_web: b("has_web"), vw: b("vw"), preview: b("preview"), decodes: b("decodes") };
        let out = match e.get("ev").and_then(|x| x.as_str()).unwrap_or("") {
            "frame" => f.on_frame(e.get("id").and_then(|x| x.as_i64()).unwrap_or(0)),
            "paint" => f.paint(&s),
            "start" => f.start(&s),
            "stop" => f.stop(&s),
            o => return json!({"declined": format!("unknown event {o}")}),
        };
        trace.push(Value::Array(out));
    }
    json!({"trace": trace})
}
