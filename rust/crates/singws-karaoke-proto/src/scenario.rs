//! Scripted scenario runner: fake picture + fake voice + a 10 ms world, the protocol machine on top. The Python twin is `tools/karaoke_transport_parity.py`
//! (it drives the real `MpvKaraokeTransport` with fake plugin/voice objects and a virtual clock). Both write the same log of `[ms, text]` lines.
use crate::karaoke_proto::{AudioSpec, Event, Picture, Protocol, StartArgs, Voice};
use serde_json::{Value, json};
use std::cell::RefCell;
use std::rc::Rc;

const BASE_US: i64 = 1_000_000_000; // the machine's clock starts at 1000 s so no deadline is ever 0.0
const GRID_US: i64 = 10_000;

struct W {
    t_us: i64,
    log: Vec<(i64, String)>,
    p_dur_ms: i64,
    p_dur_on_load: i64,
    p_pos_us: i64,
    p_playing: bool,
    p_end: bool,
    p_end_ms: Option<i64>,
    p_visuals: bool,
    p_load_ok: bool,
    p_tempo_pct: i64,
    p_offset_supported: bool,
    p_seek_holding: bool,
    v_ready: bool,
    v_pos_us: i64,
    v_playing: bool,
    v_moves: bool,
    v_resync: Option<(u32, u32, f32, f32, f32, i32)>,
    v_duration: f64,
    v_load_error: Option<String>,
}

type Shared = Rc<RefCell<W>>;

fn b(x: bool) -> &'static str {
    if x { "true" } else { "false" }
}

impl W {
    fn note(&mut self, s: String) {
        let ms = self.t_us / 1000;
        self.log.push((ms, s));
    }
}

struct Pic(Shared);
impl Picture for Pic {
    fn duration_ms(&self) -> i64 { self.0.borrow().p_dur_ms }
    fn position_ms(&self) -> i64 { self.0.borrow().p_pos_us / 1000 }
    fn is_playing(&self) -> bool { self.0.borrow().p_playing }
    fn at_end(&self) -> bool { self.0.borrow().p_end }
    fn visuals_ready(&self) -> bool { self.0.borrow().p_visuals }
    fn play(&mut self) { let mut w = self.0.borrow_mut(); w.p_playing = true; w.note("p.play".into()) }
    fn pause(&mut self) { let mut w = self.0.borrow_mut(); w.p_playing = false; w.note("p.pause".into()) }
    fn seek_ms(&mut self, ms: i64) { let mut w = self.0.borrow_mut(); w.p_pos_us = ms * 1000; w.note(format!("p.seek {ms}")) }
    fn stop(&mut self) { let mut w = self.0.borrow_mut(); w.p_playing = false; w.note("p.stop".into()) }
    fn set_shadow_audio(&mut self, on: bool) { self.0.borrow_mut().note(format!("p.shadow {}", b(on))) }
    fn load(&mut self, source: &str, audio: Option<&str>, autoplay: bool, semitones: i32, tempo_percent: i32) -> bool {
        let mut w = self.0.borrow_mut();
        w.note(format!("p.load {source} {} autoplay={} semi={semitones} tempo={tempo_percent}", audio.filter(|a| !a.is_empty()).unwrap_or("-"), b(autoplay)));
        w.p_dur_ms = w.p_dur_on_load;
        w.p_playing = autoplay;
        w.p_pos_us = 0;
        w.p_tempo_pct = tempo_percent as i64;
        w.p_load_ok
    }
    fn set_volume(&mut self, v: f64) { self.0.borrow_mut().note(format!("p.volume {v:.3}")) }
    fn set_tempo_ratio(&mut self, r: f64) {
        let mut w = self.0.borrow_mut();
        w.p_tempo_pct = (r * 100.0).round_ties_even() as i64;
        w.note(format!("p.tempo {r:.3}"))
    }
    fn set_pitch_semitones(&mut self, k: f64) { self.0.borrow_mut().note(format!("p.pitch {k:.3}")) }
    fn set_video_offset_ms(&mut self, v: i32) -> bool {
        let mut w = self.0.borrow_mut();
        if w.p_offset_supported {
            w.note(format!("p.offset {v}"));
        }
        w.p_offset_supported
    }
    fn is_seek_holding(&self) -> bool { self.0.borrow().p_seek_holding }
}

struct Vo(Shared);
impl Voice for Vo {
    fn is_ready(&self) -> bool { self.0.borrow().v_ready }
    fn position(&self) -> f64 { self.0.borrow().v_pos_us as f64 / 1e6 }
    fn play(&mut self) { let mut w = self.0.borrow_mut(); w.v_playing = true; w.note("v.play".into()) }
    fn pause(&mut self) { let mut w = self.0.borrow_mut(); w.v_playing = false; w.note("v.pause".into()) }
    fn stop(&mut self) { let mut w = self.0.borrow_mut(); w.v_playing = false; w.note("v.stop".into()) }
    fn seek(&mut self, s: f64) {
        let mut w = self.0.borrow_mut();
        w.v_ready = false;
        w.v_pos_us = (s * 1e6).round_ties_even() as i64;
        w.note(format!("v.seek {s:.3}"))
    }
    fn slave_to(&mut self, p: f64, r: f64) { self.0.borrow_mut().note(format!("v.slave {p:.3} {r:.3}")) }
    fn resync_info(&self) -> Option<(u32, u32, f32, f32, f32, i32)> { self.0.borrow().v_resync }
    fn duration(&self) -> f64 { self.0.borrow().v_duration }
    fn load(&mut self, path: &str, norm_gain: f64, volume: f64) -> Result<(), String> {
        let mut w = self.0.borrow_mut();
        if let Some(e) = w.v_load_error.clone() {
            return Err(e);
        }
        w.note(format!("v.load {path} norm={norm_gain:.3} paused vol={volume:.3}"));
        Ok(())
    }
    fn set_key_semitones(&mut self, k: f64) { self.0.borrow_mut().note(format!("v.key {k:.3}")) }
    fn set_tempo_percent(&mut self, t: f64) { self.0.borrow_mut().note(format!("v.tempo {t:.3}")) }
    fn set_volume(&mut self, v: f64) { self.0.borrow_mut().note(format!("v.volume {v:.3}")) }
    fn slide_volume(&mut self, v: f64, ms: i64) { self.0.borrow_mut().note(format!("v.slide {v:.3} {ms}")) }
    fn set_norm_gain(&mut self, g: f64) { self.0.borrow_mut().note(format!("v.norm {g:.3}")) }
    fn set_eq_gains(&mut self, on: bool, g: &[f64]) { self.0.borrow_mut().note(format!("v.eq {} {}", b(on), g.len())) }
    fn set_master_params(&mut self, on: bool, has: bool) { self.0.borrow_mut().note(format!("v.master {} {}", b(on), b(has))) }
}

fn f(v: &Value, k: &str, d: f64) -> f64 {
    v.get(k).and_then(Value::as_f64).unwrap_or(d)
}
fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn world_set(w: &mut W, o: &Value) {
    let Some(m) = o.as_object() else { return };
    for (k, v) in m {
        match k.as_str() {
            "voice_ready" => w.v_ready = v.as_bool().unwrap(),
            "voice_pos" => w.v_pos_us = (v.as_f64().unwrap() * 1e6).round_ties_even() as i64,
            "voice_moves" => w.v_moves = v.as_bool().unwrap(),
            "picture_duration_ms" => w.p_dur_ms = v.as_i64().unwrap(),
            "picture_pos_ms" => w.p_pos_us = v.as_i64().unwrap() * 1000,
            "picture_playing" => w.p_playing = v.as_bool().unwrap(),
            "at_end" => w.p_end = v.as_bool().unwrap(),
            "visuals" => w.p_visuals = v.as_bool().unwrap(),
            "seek_holding" => w.p_seek_holding = v.as_bool().unwrap(),
            "resync" => {
                let a = v.as_array().unwrap();
                w.v_resync = Some((a[0].as_u64().unwrap() as u32, a[1].as_u64().unwrap() as u32, a[2].as_f64().unwrap() as f32, a[3].as_f64().unwrap() as f32, a[4].as_f64().unwrap() as f32, a[5].as_i64().unwrap() as i32));
            }
            other => panic!("unknown world key {other}"),
        }
    }
}

pub fn run(c: &Value) -> Value {
    let w: Shared = Rc::new(RefCell::new(W {
        t_us: 0,
        log: Vec::new(),
        p_dur_ms: 0,
        p_dur_on_load: c.get("duration_ms").and_then(Value::as_i64).unwrap_or(200_000),
        p_pos_us: 0,
        p_playing: false,
        p_end: false,
        p_end_ms: c.get("end_ms").and_then(Value::as_i64),
        p_visuals: true,
        p_load_ok: c.get("picture_load_ok").and_then(Value::as_bool).unwrap_or(true),
        p_tempo_pct: 100,
        p_offset_supported: c.get("offset_supported").and_then(Value::as_bool).unwrap_or(false),
        p_seek_holding: false,
        v_ready: false,
        v_pos_us: 0,
        v_playing: false,
        v_moves: true,
        v_resync: None,
        v_duration: c.get("voice_duration").and_then(Value::as_f64).unwrap_or(200.0),
        v_load_error: c.get("voice_load_error").and_then(Value::as_str).map(str::to_string),
    }));
    let has_voice = c.get("voice").and_then(Value::as_bool).unwrap_or(true);
    let mut proto = Protocol::new(Pic(w.clone()), if has_voice { Some(Vo(w.clone())) } else { None }).parity_python();
    let sink = w.clone();
    proto.on_log = Some(Box::new(move |l: &str| {
        let line = if l.starts_with("print: ") { l.to_string() } else { format!("log:{l}") };
        sink.borrow_mut().note(line);
    }));
    let args = StartArgs {
        audio_path: s(c, "audio"),
        video_path: s(c, "video"),
        mode: c.get("mode").and_then(Value::as_str).unwrap_or("cdg").to_string(),
        start_seconds: 0.0,
        zones: c.get("zones").and_then(Value::as_array).map(|z| z.iter().map(|p| (p[0].as_f64().unwrap(), p[1].as_f64().unwrap())).collect()).unwrap_or_default(),
    };
    let steps = c["steps"].as_array().cloned().unwrap_or_default();
    let run_ms = c["run_ms"].as_i64().unwrap_or(1000);
    let mut t = 0i64;
    while t <= run_ms * 1000 {
        {
            let mut g = w.borrow_mut();
            g.t_us = t;
            if t > 0 {
                if g.p_playing {
                    g.p_pos_us += GRID_US * g.p_tempo_pct / 100;
                }
                if g.v_playing && g.v_moves {
                    g.v_pos_us += GRID_US;
                }
                if let Some(e) = g.p_end_ms {
                    if g.p_pos_us / 1000 >= e {
                        g.p_end = true;
                    }
                }
            }
        }
        for st in steps.iter().filter(|st| st["t"].as_i64().unwrap_or(0) * 1000 == t) {
            let now = (BASE_US + t) as f64 / 1e6;
            let op = st["op"].as_str().unwrap_or("world");
            match op {
                "world" => world_set(&mut w.borrow_mut(), &st["set"]),
                "start" => {
                    let mut a = args.clone();
                    a.start_seconds = f(st, "start", 0.0);
                    if let Err(e) = proto.start_full(now, &a) {
                        w.borrow_mut().note(format!("raised:{e}"));
                    }
                }
                "stop" => proto.stop(),
                "pause" => proto.pause(),
                "resume" => proto.resume(),
                "seek" => proto.seek(now, f(st, "seconds", 0.0)),
                "modifiers" => proto.set_modifiers(f(st, "tempo", 1.0), f(st, "semitones", 0.0)),
                "volume" => proto.set_volume(f(st, "value", 1.0)),
                "fade" => {
                    let ok = proto.fade_out(now, st["ms"].as_i64().unwrap_or(0), st.get("cb").and_then(Value::as_bool).unwrap_or(true));
                    w.borrow_mut().note(format!("ret fade {}", b(ok)));
                }
                "loop" => proto.set_loop(f(st, "start", 0.0), f(st, "end", 0.0)),
                "poll_ms" => proto.set_visual_timer_interval_ms(now, st["ms"].as_i64().unwrap_or(50)),
                "offset" => proto.set_video_offset_ms(st["ms"].as_i64().unwrap_or(0) as i32),
                "audio_spec" => proto.apply_audio_spec(&AudioSpec {
                    normalize_gain_db: f(st, "gain_db", 0.0),
                    eq_enabled: st.get("eq_enabled").and_then(Value::as_bool).unwrap_or(false),
                    eq_gains_db: st.get("eq_gains").and_then(Value::as_array).map(|a| a.iter().map(|x| x.as_f64().unwrap()).collect()).unwrap_or_default(),
                    master_enabled: st.get("master_enabled").and_then(Value::as_bool).unwrap_or(false),
                    has_master_params: st.get("master_params").and_then(Value::as_bool).unwrap_or(false),
                }),
                "query_paused" => {
                    let r = proto.is_paused();
                    w.borrow_mut().note(format!("q.is_paused {}", b(r)));
                }
                other => panic!("unknown op {other}"),
            }
        }
        let until = BASE_US + t;
        while let Some(due) = proto.next_due_us().filter(|d| *d <= until) {
            w.borrow_mut().t_us = due - BASE_US;
            let evs = proto.fire_next(until).unwrap_or_default();
            for ev in evs {
                let line = match ev {
                    Event::Started => "event:started".to_string(),
                    Event::Ended => "event:ended".to_string(),
                    Event::VisualStalled(p, ms) => format!("event:visual_stalled {p} {ms}"),
                    Event::FadeFinished => "event:fade_finished".to_string(),
                };
                w.borrow_mut().note(line);
            }
            let _ = due;
        }
        w.borrow_mut().t_us = t;
        t += GRID_US;
    }
    let g = w.borrow();
    Value::Array(g.log.iter().map(|(ms, s)| json!([ms, s])).collect())
}
