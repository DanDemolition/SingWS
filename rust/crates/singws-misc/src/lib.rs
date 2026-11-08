//! Small pure rules of the app, each proven against the app's own Python by a tools/*_parity.py script.
use serde_json::Value;

pub type R<T> = Result<T, String>;

/// Python `float(v)` for a JSON value: numbers and booleans convert; null, lists and mappings raise (None); text is declined.
fn to_float(v: &Value) -> R<Option<f64>> {
    match v {
        Value::Number(n) => Ok(n.as_f64()),
        Value::Bool(b) => Ok(Some(*b as i64 as f64)),
        Value::String(_) => Err("decline: a number given as text".into()),
        _ => Ok(None),
    }
}

/// Python `int(v)` for the error code: numbers truncate, booleans are 0/1, null/lists/mappings raise (None), text is declined.
fn to_int(v: &Value) -> R<Option<i64>> {
    match v {
        Value::Bool(b) => Ok(Some(*b as i64)),
        Value::Number(n) => Ok(n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64))),
        Value::String(_) => Err("decline: a number given as text".into()),
        _ => Ok(None),
    }
}

/// `LocationFixCollector` (0.2.18.1.py): decides when a CoreLocation search is finished.
#[derive(Default)]
pub struct LocationFixCollector {
    pub best: Option<Value>,
    pub best_accuracy: Option<f64>,
    pub transient_error: String,
    pub fatal_error: String,
}

impl LocationFixCollector {
    pub const GOOD_ACCURACY_M: f64 = 100.0;
    pub const USABLE_ACCURACY_M: f64 = 3000.0;
    pub const MAX_AGE_S: f64 = 30.0;

    /// Returns true when the search can stop now (a good fix).
    pub fn add_fix(&mut self, obj: &Value, accuracy: &Value, age_seconds: &Value) -> R<bool> {
        let (Some(accuracy), Some(age)) = (to_float(accuracy)?, to_float(age_seconds)?) else { return Ok(false) };
        if accuracy < 0.0 || age > Self::MAX_AGE_S {
            return Ok(false);
        }
        if self.best.is_none() || accuracy < self.best_accuracy.unwrap_or(f64::INFINITY) {
            self.best = Some(obj.clone());
            self.best_accuracy = Some(accuracy);
        }
        Ok(self.best_accuracy.unwrap_or(f64::INFINITY) <= Self::GOOD_ACCURACY_M)
    }

    /// Returns true when the error is final. `text` is `str(text)` already (the caller declines what it cannot render).
    pub fn add_error(&mut self, code: &Value, text: &str) -> R<bool> {
        let transient = matches!(to_int(code)?, Some(0) | Some(2));
        if transient {
            self.transient_error = text.to_string();
            return Ok(false);
        }
        self.fatal_error = text.to_string();
        Ok(true)
    }

    /// (fix, error text)
    pub fn result(&self) -> (Option<Value>, String) {
        if let (Some(best), Some(acc)) = (&self.best, self.best_accuracy) {
            if acc <= Self::USABLE_ACCURACY_M {
                return (Some(best.clone()), String::new());
            }
        }
        if !self.fatal_error.is_empty() {
            return (None, self.fatal_error.clone());
        }
        if let Some(acc) = self.best_accuracy {
            return (None, format!("Only a rough location was available (about {} m).", acc.trunc() as i64));
        }
        if !self.transient_error.is_empty() {
            return (None, self.transient_error.clone());
        }
        (None, "Timed out waiting for device location.".to_string())
    }
}

// ---- `_local_audio_output_priority(item)`: prefer the live headphone jack, then built-in speakers. Python `casefold()` is the identity on ASCII; other text is declined.
pub fn local_audio_output_priority(name: &Value, kind: &Value) -> R<(i64, String)> {
    let text = |v: &Value| -> R<String> {
        match v {
            Value::Null => Ok(String::new()),
            Value::String(s) if s.is_ascii() => Ok(s.to_lowercase()),
            Value::String(_) => Err("decline: casefold of non-ASCII text".into()),
            _ => Err("decline: a field that is not text".into()),
        }
    };
    let (name, kind) = (text(name)?, text(kind)?);
    let has = |tokens: &[&str]| tokens.iter().any(|t| name.contains(t));
    if kind == "display" || has(&["airplay", "apple tv", "bravia", "television", "hdmi"]) {
        return Ok((99, name));
    }
    if has(&["external headphones", "headphone", "headset"]) {
        return Ok((0, name));
    }
    if has(&["macbook", "built-in", "built in", "internal speakers"]) {
        return Ok((1, name));
    }
    Ok((2, name))
}

fn py_strip(s: &str) -> &str {
    s.trim_matches(|c: char| matches!(c, '\t' | '\n' | '\u{0b}' | '\u{0c}' | '\r' | '\u{1c}'..='\u{1f}' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}'))
}

fn text_field(v: Option<&Value>) -> R<String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Bool(false)) => Ok(String::new()),
        Some(_) => Err("decline: a field that is not text".into()),
    }
}

/// `_queue_entry_artist_title_for_tombstone(entry)`: (artist, title).
pub fn tombstone_artist_title(entry: &Value) -> R<(String, String)> {
    let Value::Object(e) = entry else { return Ok((String::new(), String::new())) };
    let artist = py_strip(&text_field(e.get("artist"))?).to_string();
    let title = py_strip(&text_field(e.get("title"))?).to_string();
    if !artist.is_empty() || !title.is_empty() {
        return Ok((artist, title));
    }
    let display = py_strip(&text_field(e.get("display_name"))?).to_string();
    for sep in ["\u{2022}", "-"] {
        if let Some((l, r)) = display.split_once(sep) {
            return Ok((py_strip(l).to_string(), py_strip(r).to_string()));
        }
    }
    Ok((String::new(), String::new()))
}

/// `_next_active_entry_after(singer, entry)` over the singer's song list and the entry's index in it: the index of the next song that is not a skipped object.
pub fn next_active_entry_after(songs: &[Value], entry_index: Option<usize>) -> Option<usize> {
    let after = entry_index? + 1;
    for (i, song) in songs.iter().enumerate().skip(after) {
        if let Value::Object(o) = song {
            if truthy(o.get("skipped")) {
                continue;
            }
        }
        return Some(i);
    }
    None
}

fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ => if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 { 29 } else { 28 },
    }
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

/// The strict subset of `datetime.fromisoformat(...).timestamp()` this port reproduces: `YYYY-MM-DD[T| ]HH:MM[:SS[.f{1,6}]]` WITH a `+HH:MM` / `-HH:MM` offset (a naive time
/// depends on the machine's time zone and is declined, and so is every other spelling Python accepts). `Ok(None)` = Python raises ValueError.
fn iso_timestamp(text: &str) -> R<Option<f64>> {
    let b = text.as_bytes();
    let digits = |s: usize, n: usize| -> Option<i64> {
        if s + n > b.len() || !b[s..s + n].iter().all(u8::is_ascii_digit) { return None }
        std::str::from_utf8(&b[s..s + n]).ok()?.parse().ok()
    };
    // shape check first: anything unusual is declined, never guessed
    let shaped = b.len() >= 16 && b[4] == b'-' && b[7] == b'-' && (b[10] == b'T' || b[10] == b' ') && b[13] == b':';
    if !shaped || !text.is_ascii() {
        return Err("decline: an ISO spelling outside the modelled subset".into());
    }
    let (Some(y), Some(mo), Some(d), Some(h), Some(mi)) = (digits(0, 4), digits(5, 2), digits(8, 2), digits(11, 2), digits(14, 2)) else { return Err("decline: an ISO spelling outside the modelled subset".into()) };
    let mut pos = 16;
    let (mut sec, mut micro) = (0i64, 0i64);
    if pos < b.len() && b[pos] == b':' {
        let Some(s) = digits(pos + 1, 2) else { return Err("decline: an ISO spelling outside the modelled subset".into()) };
        sec = s;
        pos += 3;
        if pos < b.len() && b[pos] == b'.' {
            let start = pos + 1;
            let mut end = start;
            while end < b.len() && b[end].is_ascii_digit() { end += 1 }
            let n = end - start;
            if n == 0 || n > 6 { return Err("decline: an ISO spelling outside the modelled subset".into()) }
            let frac: i64 = text[start..end].parse().unwrap();
            micro = frac * 10i64.pow((6 - n) as u32);
            pos = end;
        }
    }
    // offset
    if pos + 6 != b.len() || !(b[pos] == b'+' || b[pos] == b'-') || b[pos + 3] != b':' {
        return Err("decline: an ISO spelling outside the modelled subset".into());
    }
    let (Some(oh), Some(om)) = (digits(pos + 1, 2), digits(pos + 4, 2)) else { return Err("decline: an ISO spelling outside the modelled subset".into()) };
    if y < 1 || !(1..=12).contains(&mo) || d < 1 || d > days_in_month(y, mo) || h > 23 || mi > 59 || sec > 59 || oh > 23 || om > 59 {
        return Ok(None);
    }
    let sign = if b[pos] == b'-' { -1 } else { 1 };
    let local = days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + sec;
    let utc = local - sign * (oh * 3600 + om * 60);
    Ok(Some(((utc * 1_000_000 + micro) as f64) / 1e6))
}

/// `_remote_request_timestamp(req)`: the first usable timestamp among the request's time fields, else 0.0.
pub fn remote_request_timestamp(req: &Value) -> R<f64> {
    let Value::Object(r) = req else { return Ok(0.0) };
    for key in ["received_at_server", "server_received_at", "waitlisted_at", "attention_at", "requested_at_client", "requested_at", "created_at", "submitted_at", "received_at", "request_time", "ts", "time"] {
        let Some(value) = r.get(key) else { continue };
        match value {
            Value::Null => continue,
            Value::String(s) if s.is_empty() => continue,
            Value::Bool(b) => {
                let f = *b as i64 as f64;
                if f > 1e9 { return Ok(f) }
                continue;
            }
            Value::Number(n) => {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if f > 1e9 { return Ok(f) }
                continue;
            }
            Value::String(s) => {
                let t = py_strip(s);
                if t.is_empty() { continue }
                if !s.is_ascii() || s.contains('_') { return Err("decline: text whose float() form is not modelled".into()) }
                let lower = t.to_ascii_lowercase();
                let as_float: Option<f64> = match lower.trim_start_matches(['+', '-']) {
                    "inf" | "infinity" => Some(if lower.starts_with('-') { f64::NEG_INFINITY } else { f64::INFINITY }),
                    "nan" => Some(f64::NAN),
                    _ if lower.chars().all(|c| c.is_ascii_digit() || matches!(c, '.' | 'e' | '+' | '-')) && lower.chars().any(|c| c.is_ascii_digit()) => lower.parse::<f64>().ok(),
                    _ => None,
                };
                if let Some(f) = as_float {
                    if f > 1e9 { return Ok(f) }
                    continue;
                }
                // not a float: the ISO route (all 'Z' become +00:00)
                let iso = t.replace('Z', "+00:00");
                if !iso.chars().next().is_some_and(|c| c.is_ascii_digit()) {
                    continue; // fromisoformat needs a digit to start: ValueError
                }
                match iso_timestamp(&iso)? {
                    Some(ts) => return Ok(ts),
                    None => continue,
                }
            }
            _ => continue,
        }
    }
    Ok(0.0)
}
