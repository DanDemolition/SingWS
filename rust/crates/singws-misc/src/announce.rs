//! `announcement_box.py`: the rules of the announcement box. A phone-recorded clip arrives in base64 chunks, is checked, waits armed, plays once, and the music is ducked while it plays.
//! Pure: the box answers each command with (ok, message) and a list of EFFECTS the caller carries out (load the clip into a pad, play, close the pad, slide the music volume).
//! Python's `wave` module is the reference for the WAV check; spellings it accepts that are not modelled here (extensible format) are DECLINED, never guessed.
use serde_json::{json, Value};

pub const MAX_SECONDS: f64 = 30.0;
pub const MAX_BYTES: usize = 3_000_000;
pub const MAX_CHUNKS: i64 = 400;
pub const MIN_RATE: i64 = 8000;
pub const MAX_RATE: i64 = 48000;
pub const HOLD_SECONDS: f64 = 900.0;
pub const DUCK_FACTOR: f64 = 0.5;
pub const DUCK_IN_MS: i64 = 200;
pub const DUCK_OUT_MS: i64 = 700;
pub const PEAK_LIMIT: f64 = 32767.0 * 0.995;

type Answer = Result<(bool, String), String>;   // Err = declined (a spelling this port does not model)

fn le16(b: &[u8], i: usize) -> Option<u16> {
    Some(u16::from_le_bytes([*b.get(i)?, *b.get(i + 1)?]))
}
fn le32(b: &[u8], i: usize) -> Option<u32> {
    Some(u32::from_le_bytes([*b.get(i)?, *b.get(i + 1)?, *b.get(i + 2)?, *b.get(i + 3)?]))
}

/// What Python's `wave.open` + the box's checks say about `data`: Ok((rate, seconds, peak)) or Err(code); Decline for spellings not modelled.
pub enum Wav {
    Good(u32, f64, i64),
    Refused(&'static str),
    Declined(&'static str),
}

pub fn check_wav(data: &[u8]) -> Wav {
    // _Chunk(file): name (4 bytes) then size (4); a short name is end-of-file, a short size is the same
    let name = |at: usize| data.get(at..at + 4);
    if name(0).is_none() {
        return Wav::Refused("bad_wav");           // EOFError
    }
    if name(0) != Some(b"RIFF") {
        return Wav::Refused("bad_wav");
    }
    if le32(data, 4).is_none() {
        return Wav::Refused("bad_wav");
    }
    if data.get(8..12) != Some(b"WAVE") {
        return Wav::Refused("bad_wav");           // "not a WAVE file" (also a short read)
    }
    let mut pos = 12usize;
    let (mut rate, mut channels, mut width) = (0u32, 0u16, 0usize);
    let mut fmt_read = false;
    let (mut data_at, mut data_size) = (None::<usize>, 0usize);
    loop {
        let Some(nm) = name(pos) else { break };   // EOFError: no more chunks
        let Some(size) = le32(data, pos + 4) else { break };   // a short size is EOFError too
        let size = size as usize;
        let body = pos + 8;
        match nm {
            b"fmt " => {
                // chunk.read(14): short chunk -> EOFError (bad_wav)
                let avail = data.len().saturating_sub(body).min(size);
                if avail < 14 {
                    return Wav::Refused("bad_wav");
                }
                let tag = le16(data, body).unwrap();
                channels = le16(data, body + 2).unwrap();
                rate = le32(data, body + 4).unwrap();
                if tag != 1 {
                    return if tag == 0xFFFE { Wav::Declined("extensible format") } else { Wav::Refused("bad_wav") };
                }
                if avail < 16 {
                    return Wav::Refused("bad_wav");
                }
                let bits = le16(data, body + 14).unwrap() as usize;
                width = (bits + 7) / 8;
                if width == 0 || channels == 0 {
                    return Wav::Refused("bad_wav");
                }
                fmt_read = true;
            }
            b"data" => {
                if !fmt_read {
                    return Wav::Refused("bad_wav");
                }
                data_at = Some(body);
                data_size = size;
                break;
            }
            _ => {}
        }
        // chunk.skip(): to the end of the chunk, plus the pad byte of an odd size
        pos = body.saturating_add(size).saturating_add(size & 1);
        if pos > data.len() {
            break;
        }
    }
    let (Some(at), true) = (data_at, fmt_read) else { return Wav::Refused("bad_wav") };
    let framesize = channels as usize * width;
    let frames = data_size / framesize;
    let avail = data.len().saturating_sub(at).min(frames * framesize);
    if channels != 1 || width != 2 {
        return Wav::Refused("wav_must_be_mono_16bit");
    }
    if !(MIN_RATE..=MAX_RATE).contains(&(rate as i64)) {
        return Wav::Refused("bad_sample_rate");
    }
    if frames == 0 {
        return Wav::Refused("empty_clip");
    }
    let seconds = frames as f64 / rate as f64;
    if seconds > MAX_SECONDS {
        return Wav::Refused("clip_too_long");
    }
    let pcm = &data[at..at + avail];
    let mut peak = 0i64;
    for p in pcm.chunks_exact(2) {
        let v = i16::from_le_bytes([p[0], p[1]]) as i64;
        peak = peak.max(v.abs());
    }
    if peak as f64 >= PEAK_LIMIT {
        return Wav::Refused("clip_would_distort");
    }
    Wav::Good(rate, seconds, peak)
}

/// Python `base64.b64decode(s, validate=True)` (strict): only the alphabet, padding only at the end and exactly as needed.
pub fn b64_strict(s: &str) -> Option<Vec<u8>> {
    if !s.is_ascii() {
        return None;
    }
    let b = s.as_bytes();
    let pad = b.iter().rev().take_while(|c| **c == b'=').count();
    if pad > 2 {
        return None;
    }
    let body = &b[..b.len() - pad];
    if body.iter().any(|c| !(c.is_ascii_alphanumeric() || *c == b'+' || *c == b'/')) {
        return None;
    }
    let rem = body.len() % 4;
    let need = match rem { 0 => 0, 2 => 2, 3 => 1, _ => return None };
    if pad != need {
        return None;
    }
    let val = |c: u8| -> u32 {
        match c { b'A'..=b'Z' => (c - b'A') as u32, b'a'..=b'z' => (c - b'a') as u32 + 26, b'0'..=b'9' => (c - b'0') as u32 + 52, b'+' => 62, _ => 63 }
    };
    let mut out = Vec::with_capacity(body.len() / 4 * 3 + 3);
    for q in body.chunks(4) {
        let mut n = 0u32;
        for (i, c) in q.iter().enumerate() {
            n |= val(*c) << (18 - 6 * i as u32);
        }
        out.push((n >> 16) as u8);
        if q.len() > 2 {
            out.push((n >> 8) as u8);
        }
        if q.len() > 3 {
            out.push(n as u8);
        }
    }
    Some(out)
}

/// Python truthiness of a JSON value (`x or ""`).
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(args.get(key) or "")` for the values the app can get: text, or something falsy. Other values would print differently in Python: declined.
fn text_arg(args: &Value, key: &str) -> Result<String, String> {
    match args.get(key) {
        None => Ok(String::new()),
        Some(v) if !truthy(v) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => Err("decline: a non-text value for a text argument".into()),
    }
}

/// `int(args.get(key))`: numbers and booleans convert, null/lists/mappings raise TypeError (None); text is declined.
fn int_arg(args: &Value, key: &str) -> Result<Option<i64>, String> {
    match args.get(key) {
        None | Some(Value::Null) | Some(Value::Array(_)) | Some(Value::Object(_)) => Ok(None),
        Some(Value::Bool(b)) => Ok(Some(*b as i64)),
        Some(Value::Number(n)) => Ok(Some(match n.as_i64() {
            Some(i) => i,
            None => {
                let f = n.as_f64().unwrap_or(0.0);
                if f.abs() >= 9.0e18 { if f > 0.0 { i64::MAX } else { i64::MIN } } else { f.trunc() as i64 }
            }
        })),
        Some(Value::String(_)) => Err("decline: a number given as text".into()),
    }
}

#[derive(Default)]
struct Building {
    id: String,
    chunks: i64,
    next: i64,
    bytes: usize,
    parts: Vec<u8>,
}
struct Armed {
    id: String,
    wav: Vec<u8>,
    at: f64,
    seconds: f64,
}

/// What the caller reports about the audio engine at the moment of a call.
pub struct Env {
    /// None = no engine (or one without pads). Some(master volume).
    pub engine_master: Option<f64>,
    pub engine_closed: bool,
    pub soundboard_volume: f64,
    pub load_fails: bool,
}

#[derive(Default)]
pub struct AnnounceBox {
    building: Option<Building>,
    armed: Option<Armed>,
    channel: bool,
    duck: Option<(f64, f64)>,   // (level before, level ducked to)
    pub effects: Vec<Value>,
}

impl AnnounceBox {
    fn expire(&mut self, now: f64) {
        if let Some(a) = &self.armed {
            if now - a.at > HOLD_SECONDS {
                self.armed = None;
            }
        }
    }

    pub fn handle(&mut self, action: &str, args: &Value, now: f64, env: &Env) -> Answer {
        let r = match action {
            "announce.begin" => self.begin(args),
            "announce.chunk" => self.chunk(args),
            "announce.end" => self.end(args, now),
            "announce.play" => self.play(args, now, env),
            "announce.discard" => {
                self.building = None;
                self.armed = None;
                return Ok((true, "discarded".into()));
            }
            _ => return Ok((false, "unknown_announce_command".into())),
        };
        match r {
            Ok(Ok(ok)) => Ok(ok),
            Ok(Err(code)) => {
                self.building = None;                 // every refusal drops a clip being built
                Ok((false, code))
            }
            Err(declined) => Err(declined),
        }
    }

    fn begin(&mut self, args: &Value) -> Result<Result<(bool, String), String>, String> {
        self.building = None;
        self.armed = None;
        let id: String = text_arg(args, "id")?.chars().take(64).collect();
        let (chunks, rate) = (int_arg(args, "chunks")?, int_arg(args, "rate")?);
        let (Some(chunks), Some(rate)) = (chunks, rate) else { return Ok(Err("bad_begin".into())) };
        if id.is_empty() || !(1..=MAX_CHUNKS).contains(&chunks) || !(MIN_RATE..=MAX_RATE).contains(&rate) {
            return Ok(Err("bad_begin".into()));
        }
        self.building = Some(Building { id, chunks, ..Default::default() });
        Ok(Ok((true, "ready".into())))
    }

    fn chunk(&mut self, args: &Value) -> Result<Result<(bool, String), String>, String> {
        let id = text_arg(args, "id")?;
        let Some(b) = self.building.as_mut() else { return Ok(Err("no_clip_in_progress".into())) };
        if id != b.id {
            return Ok(Err("no_clip_in_progress".into()));
        }
        let index = int_arg(args, "index")?;
        let data = text_arg(args, "data")?;
        let (Some(index), Some(piece)) = (index, b64_strict(&data)) else { return Ok(Err("bad_chunk".into())) };
        if index != b.next || index >= b.chunks {
            return Ok(Err("chunk_out_of_order".into()));
        }
        b.bytes += piece.len();
        if b.bytes > MAX_BYTES {
            return Ok(Err("clip_too_big".into()));
        }
        b.parts.extend_from_slice(&piece);
        b.next += 1;
        Ok(Ok((true, index.to_string())))
    }

    fn end(&mut self, args: &Value, now: f64) -> Result<Result<(bool, String), String>, String> {
        let id = text_arg(args, "id")?;
        let Some(b) = self.building.as_ref() else { return Ok(Err("no_clip_in_progress".into())) };
        if id != b.id {
            return Ok(Err("no_clip_in_progress".into()));
        }
        if b.next != b.chunks {
            return Ok(Err("clip_incomplete".into()));
        }
        let b = self.building.take().unwrap();
        match check_wav(&b.parts) {
            Wav::Good(_rate, seconds, _peak) => {
                self.armed = Some(Armed { id: b.id, wav: b.parts, at: now, seconds });
                Ok(Ok((true, format!("armed {seconds:.1}s"))))
            }
            Wav::Refused(code) => Ok(Err(code.into())),
            Wav::Declined(why) => Err(format!("decline: {why}")),
        }
    }

    fn play(&mut self, args: &Value, now: f64, env: &Env) -> Result<Result<(bool, String), String>, String> {
        self.expire(now);
        let id = text_arg(args, "id")?;
        match &self.armed {
            Some(a) if a.id == id => {}
            _ => return Ok(Err("no_clip_armed".into())),
        }
        if env.engine_master.is_none() {
            return Ok(Err("audio_engine_not_running".into()));
        }
        let a = self.armed.take().unwrap();                // one play per clip, whether or not it works
        self.close_channel(env);
        let vol = env.soundboard_volume.clamp(0.0, 1.0);
        self.effects.push(json!(["load", a.wav.len(), vol]));
        if env.load_fails {
            self.effects.push(json!(["close"]));
            return Ok(Err("could_not_play: boom".into()));
        }
        self.effects.push(json!(["play"]));
        self.channel = true;
        self.duck_music(env);
        let _ = a.seconds;
        Ok(Ok((true, "playing".into())))
    }

    fn duck_music(&mut self, env: &Env) {
        if let Some(before) = env.engine_master {
            if self.duck.is_none() && before > 0.0 {
                let ducked = before * DUCK_FACTOR;
                self.effects.push(json!(["slide", ducked, DUCK_IN_MS]));
                self.duck = Some((before, ducked));
            }
        }
    }

    fn unduck_music(&mut self, env: &Env) {
        let Some((before, ducked)) = self.duck.take() else { return };
        // only if nothing else moved the music since; never fight it
        if !env.engine_closed {
            if let Some(now_level) = env.engine_master {
                if (now_level - ducked).abs() < 1e-6 {
                    self.effects.push(json!(["slide", before, DUCK_OUT_MS]));
                }
            }
        }
    }

    fn close_channel(&mut self, env: &Env) {
        self.unduck_music(env);
        if self.channel {
            self.channel = false;
            self.effects.push(json!(["close"]));
        }
    }

    /// The timer: drop an expired armed clip; release the pad once the clip has finished playing.
    pub fn tick(&mut self, now: f64, playing: bool, env: &Env) {
        self.expire(now);
        if self.channel && !playing {
            self.close_channel(env);
        }
    }
}
