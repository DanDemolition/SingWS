//! The loudness cache rules (0.2.18.1.py `_loudness_*`), as pure functions. Anything whose Python behaviour depends on text that `float()`/`int()` may or may not parse, on NaN
//! literals Python's JSON accepts, or on a repr this port does not model, is DECLINED (`Err`), never guessed.
use serde_json::{json, Map, Value};

pub type R<T> = Result<T, String>;

const TARGET_LUFS: f64 = -16.0;
const MAX_GAIN_DB: f64 = 12.0;
const MIN_GAIN_DB: f64 = -18.0;
const TRUE_PEAK_HEADROOM_DB: f64 = 1.0;
pub const FAILURE_CACHE_VERSION: i64 = 3;

fn decline<T>(why: &str) -> R<T> {
    Err(format!("decline: {why}"))
}

/// Python `float(v)` for a JSON value: numbers and booleans convert; null, lists and mappings raise (None); text is declined.
fn to_float(v: &Value) -> R<Option<f64>> {
    match v {
        Value::Number(n) => Ok(n.as_f64()),
        Value::Bool(b) => Ok(Some(*b as i64 as f64)),
        Value::String(_) => decline("a number given as text"),
        _ => Ok(None),
    }
}

/// Python `int(v)`: numbers truncate, booleans are 0/1, null/lists/mappings raise (None), text is declined.
fn to_int(v: &Value) -> R<Option<i128>> {
    match v {
        Value::Bool(b) => Ok(Some(*b as i128)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                return Ok(Some(i as i128));
            }
            if let Some(u) = n.as_u64() {
                return Ok(Some(u as i128));
            }
            Ok(n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i128))
        }
        Value::String(_) => decline("a number given as text"),
        _ => Ok(None),
    }
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

/// `_loudness_gain_from_lufs(integrated_lufs, peak_db)`. A NaN is declined (Python's min/max order matters for it).
pub fn gain_from_lufs(lufs: f64, peak_db: Option<f64>) -> R<f64> {
    if lufs.is_nan() || peak_db.map_or(false, f64::is_nan) {
        return decline("NaN");
    }
    let mut gain = TARGET_LUFS - lufs;
    gain = MIN_GAIN_DB.max(MAX_GAIN_DB.min(gain));
    if let Some(p) = peak_db {
        gain = gain.min(-TRUE_PEAK_HEADROOM_DB - p);
    }
    Ok(MIN_GAIN_DB.max(MAX_GAIN_DB.min(gain)))
}

/// The entry is a different file when its recorded mtime or size no longer matches the file: `int(entry.get("mtime", 0)) != sig[0] or int(entry.get("size", 0)) != sig[1]`.
fn signature_differs(entry: &Map<String, Value>, sig: (i64, i64)) -> R<bool> {
    let m = to_int(entry.get("mtime").unwrap_or(&json!(0)))?;
    let Some(m) = m else { return decline("int(None) raises") };
    if m != sig.0 as i128 {
        return Ok(true);
    }
    let s = to_int(entry.get("size").unwrap_or(&json!(0)))?;
    let Some(s) = s else { return decline("int(None) raises") };
    Ok(s != sig.1 as i128)
}

#[derive(Debug, PartialEq)]
pub struct Info {
    pub lufs: f64,
    pub peak_db: Option<f64>,
    pub gain_db: f64,
    pub gain_linear: f64,
    pub mode: &'static str,
}

/// `loudness_info_cached`: `sig` is the file's (mtime, size), None when it cannot be stat-ed.
pub fn info_cached(entry: &Value, sig: Option<(i64, i64)>) -> R<Option<Info>> {
    let Value::Object(e) = entry else { return Ok(None) };
    if let Some(sig) = sig {
        if signature_differs(e, sig)? {
            return Ok(None);
        }
    }
    let Some(i) = e.get("i") else { return Ok(None) };
    let Some(lufs) = to_float(i)? else { return Ok(None) };
    let mut peak_db = None;
    if let Some(p) = e.get("peak_db") {
        if !p.is_null() {
            peak_db = to_float(p)?;
        }
    }
    let mode = if e.get("mode") == Some(&json!("fast")) { "fast" } else { "full" };
    let mut gain_db = gain_from_lufs(lufs, peak_db)?;
    if mode == "fast" {
        gain_db = gain_db.min(0.0);
    }
    Ok(Some(Info { lufs, peak_db, gain_db, gain_linear: std::hint::black_box(10f64).powf(gain_db / 20.0), mode }))
}

/// `loudness_gain_db_cached`.
pub fn gain_db_cached(entry: &Value, sig: Option<(i64, i64)>) -> R<Option<f64>> {
    let Value::Object(e) = entry else { return Ok(None) };
    if let Some(sig) = sig {
        if signature_differs(e, sig)? {
            return Ok(None);
        }
    }
    let Some(i) = e.get("i") else { return Ok(None) };
    let peak = match e.get("peak_db") {
        None | Some(Value::Null) => None,
        Some(p) => match to_float(p)? {
            Some(f) => Some(f),
            None => return Ok(None),
        },
    };
    let Some(lufs) = to_float(i)? else { return Ok(None) };
    let gain = gain_from_lufs(lufs, peak)?;
    Ok(Some(if e.get("mode") == Some(&json!("fast")) { gain.min(0.0) } else { gain }))
}

fn reason_of(e: &Map<String, Value>) -> R<String> {
    match e.get("reason") {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => decline("a reason that is not text"),
    }
}

/// `loudness_failed_cached`. `zip_single_mp3`: for a `.zip` path, `Some(true)` when the archive is readable and holds exactly one `.mp3` member, `Some(false)` when readable with
/// another count, `None` when it cannot be read.
pub fn failed_cached(entry: &Value, sig: Option<(i64, i64)>, path_is_zip: bool, zip_single_mp3: Option<bool>) -> R<bool> {
    let Value::Object(e) = entry else { return Ok(false) };
    if !truthy(e.get("failed")) {
        return Ok(false);
    }
    let Some(sig) = sig else { return Ok(false) };
    if signature_differs(e, sig)? {
        return Ok(false);
    }
    let reason = reason_of(e)?;
    let version = to_int(e.get("failure_version").unwrap_or(&json!(0)))?;
    let Some(version) = version else { return decline("int(None) raises") };
    if version < FAILURE_CACHE_VERSION as i128 && (reason == "no measurable loudness" || reason.starts_with("Turbo helper failed:")) {
        return Ok(false);
    }
    if path_is_zip && reason == "ZIP does not contain exactly one readable MP3" && zip_single_mp3 == Some(true) {
        return Ok(false);
    }
    Ok(true)
}

/// The record `_loudness_mark_failed` stores (`now` = int(time.time())).
pub fn failure_entry(sig: (i64, i64), reason: &str, now: i64) -> Value {
    let cut: String = reason.chars().take(200).collect();
    json!({"failed": true, "failure_version": FAILURE_CACHE_VERSION, "operator_review": true, "review_status": "needs_review", "reason": cut, "mtime": sig.0, "size": sig.1, "at": now})
}

/// Python `str.splitlines()`.
pub fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let b: Vec<(usize, char)> = s.char_indices().collect();
    let mut i = 0;
    while i < b.len() {
        let (pos, c) = b[i];
        if matches!(c, '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            out.push(&s[start..pos]);
            let mut next = pos + c.len_utf8();
            if c == '\r' && i + 1 < b.len() && b[i + 1].1 == '\n' {
                next += 1;
                i += 1;
            }
            start = next;
        }
        i += 1;
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

fn has_nonfinite_literal(text: &str) -> bool {
    text.contains("NaN") || text.contains("Infinity")
}

/// `_loudness_load_cache`: the cache file's text (None = absent), then each checkpoint's text (None = absent) in the order Python reads them. Returns the resulting mapping.
pub fn replay(cache_text: Option<&str>, checkpoints: &[Option<&str>]) -> R<Map<String, Value>> {
    let mut cache: Map<String, Value> = Map::new();
    if let Some(text) = cache_text {
        let body = if text.is_empty() { "{}" } else { text };
        match serde_json::from_str::<Value>(body) {
            Ok(Value::Object(o)) => {
                for (k, v) in o {
                    cache.insert(k, v);
                }
            }
            Ok(_) => {}
            Err(_) => {
                if has_nonfinite_literal(body) {
                    return decline("a JSON NaN/Infinity literal");
                }
                // Python's json.loads raised: the whole load stops here (the checkpoints are not read)
                return Ok(cache);
            }
        }
    }
    for cp in checkpoints {
        let Some(text) = cp else { continue };
        for line in py_splitlines(text) {
            let row: Value = match serde_json::from_str(line) {
                Ok(v) => v,
                Err(_) => {
                    if has_nonfinite_literal(line) {
                        return decline("a JSON NaN/Infinity literal");
                    }
                    continue;
                }
            };
            let Value::Object(r) = &row else { continue };
            let Some(p) = r.get("path") else { continue };
            let path = match p {
                Value::String(s) => s.clone(),
                Value::Number(n) if n.is_i64() || n.is_u64() => n.to_string(),
                Value::Bool(b) => (if *b { "True" } else { "False" }).to_string(),
                Value::Null => "None".to_string(),
                _ => return decline("a checkpoint path whose str() is not modelled"),
            };
            let Some(entry) = r.get("entry") else { continue };
            if !path.is_empty() && entry.is_object() {
                cache.insert(path, entry.clone());
            }
        }
    }
    Ok(cache)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_nan_loudness_is_declined_not_guessed() {
        assert!(gain_from_lufs(f64::NAN, None).is_err());
        assert!(gain_from_lufs(-14.0, Some(f64::NAN)).is_err());
        assert_eq!(gain_from_lufs(-14.0, Some(-30.0)).unwrap(), -2.0);
    }

    #[test]
    fn checkpoint_paths_given_as_numbers_follow_python_str() {
        let cache = replay(None, &[Some("{\"path\": 5, \"entry\": {\"x\": 1}}\n{\"path\": 18446744073709551615, \"entry\": {\"z\": 3}}\n{\"path\": true, \"entry\": {}}")]).unwrap();
        assert!(cache.contains_key("5") && cache.contains_key("18446744073709551615") && cache.contains_key("True"));
        let too_big = replay(None, &[Some("{\"path\": 18446744073709551616, \"entry\": {}}")]);
        assert!(too_big.is_err(), "a JSON integer beyond u64 is a float here: str() of it is not modelled");
        let negative = replay(None, &[Some("{\"path\": -7, \"entry\": {}}")]).unwrap();
        assert!(negative.contains_key("-7"));
    }

    #[test]
    fn a_json_nan_literal_in_the_cache_file_is_declined_even_alone() {
        assert!(replay(Some("{\"/a\": {\"i\": NaN}}"), &[]).is_err());
        assert!(replay(Some("{\"/a\": {\"i\": Infinity}}"), &[]).is_err());
        assert!(replay(Some("{bad"), &[]).unwrap().is_empty());
    }
}
