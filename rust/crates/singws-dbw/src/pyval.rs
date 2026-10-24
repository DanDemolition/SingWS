//! The small slice of Python semantics `song_index.py` leans on: truthiness, `or` chains, `str()`, `int(float(x))`, `str.lower/strip/split`,
//! and the fuzzy identity key. Anything Python would do that this module cannot reproduce exactly returns `Err("decline: ...")`; the caller
//! then leaves the database untouched and the app keeps using Python.

use serde_json::Value;

pub type R<T> = Result<T, String>;

pub fn decline<T>(why: impl AsRef<str>) -> R<T> {
    Err(format!("decline: {}", why.as_ref()))
}

/// `str.isspace()` for one character: Unicode White_Space plus the four ASCII separators Python also counts (U+001C..U+001F).
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// `" ".join(s.split())`
pub fn py_split_join(s: &str) -> String {
    s.split(is_py_space).filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `bool(value)`
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i != 0
            } else if let Some(u) = n.as_u64() {
                u != 0
            } else {
                n.as_f64().map(|f| f != 0.0).unwrap_or(true)
            }
        }
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `a or b or c ...`: the first truthy operand, otherwise the LAST operand (even when falsy).
pub fn py_or<'a>(operands: &[&'a Value]) -> &'a Value {
    for v in operands {
        if truthy(v) {
            return v;
        }
    }
    operands.last().copied().expect("py_or needs at least one operand")
}

/// `dict.get(key)` on a JSON object (a missing key and a non-object both give None, like the app's guarded callers).
pub fn get<'a>(track: &'a Value, key: &str) -> &'a Value {
    static NULL: Value = Value::Null;
    track.get(key).unwrap_or(&NULL)
}

/// `str(value)` for the JSON types whose Python repr is unambiguous. Containers are declined.
pub fn py_str(v: &Value) -> R<String> {
    match v {
        Value::Null => Ok("None".into()),
        Value::Bool(b) => Ok(if *b { "True" } else { "False" }.into()),
        Value::String(s) => Ok(s.clone()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.to_string())
            } else if let Some(u) = n.as_u64() {
                Ok(u.to_string())
            } else {
                match n.as_f64() {
                    Some(f) if f.is_finite() => Ok(singws_store::py_float_repr(f)),
                    _ => decline("non-finite number"),
                }
            }
        }
        Value::Array(_) | Value::Object(_) => decline("str() of a JSON container"),
    }
}

/// `str(value or "")`
pub fn py_str_or_empty(v: &Value) -> R<String> {
    if truthy(v) { py_str(v) } else { Ok(String::new()) }
}

/// `float(s)` for text: Python strips ASCII whitespace, allows single underscores between digits, and accepts inf/nan spellings. Returns
/// `Ok(None)` when Python would raise ValueError, `Ok(Some(f))` otherwise (f may be non-finite). Non-ASCII text is declined because
/// Python also accepts other scripts' decimal digits.
pub fn py_float_from_str(s: &str) -> R<Option<f64>> {
    // For ASCII text CPython strips only the six ASCII whitespace characters; for anything else it first maps every Unicode space and
    // decimal digit to ASCII, which is not reproduced here.
    if !s.is_ascii() {
        return decline("non-ASCII numeric text");
    }
    let t = s.trim_matches(|c| matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c'));
    let b = t.as_bytes();
    let mut cleaned = String::with_capacity(t.len());
    for (i, &c) in b.iter().enumerate() {
        if c == b'_' {
            let ok = i > 0 && i + 1 < b.len() && b[i - 1].is_ascii_digit() && b[i + 1].is_ascii_digit();
            if !ok {
                return Ok(None);
            }
        } else {
            cleaned.push(c as char);
        }
    }
    if cleaned.is_empty() {
        return Ok(None);
    }
    Ok(cleaned.parse::<f64>().ok())
}

/// `int(float(v))` inside the app's `try/except Exception: pass`: `Ok(None)` when Python would raise and move on (bad text, container,
/// inf, nan), `Ok(Some(i))` otherwise. A result outside i64 is declined (SQLite binding would fail in Python too).
pub fn py_int_of_float(v: &Value) -> R<Option<i64>> {
    let f = match v {
        Value::Bool(b) => {
            if *b { 1.0 } else { 0.0 }
        }
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i as f64
            } else if let Some(u) = n.as_u64() {
                u as f64
            } else {
                n.as_f64().unwrap_or(f64::NAN)
            }
        }
        Value::String(s) => match py_float_from_str(s)? {
            Some(f) => f,
            None => return Ok(None),
        },
        Value::Null | Value::Array(_) | Value::Object(_) => return Ok(None),
    };
    if !f.is_finite() {
        return Ok(None);
    }
    let t = f.trunc();
    // 2^63 is exactly representable; anything at or beyond it (or below -2^63) cannot be an i64.
    if t >= 9_223_372_036_854_775_808.0 || t < -9_223_372_036_854_775_808.0 {
        return decline("integer outside 64 bits");
    }
    Ok(Some(t as i64))
}

// ---- fuzzy identity key (song_index._identity_tokens / fuzzy_match_key / normalize_text) ----------------------------------------------

const NUMWORDS: &[(&str, &str)] = &[
    ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"), ("four", "4"), ("five", "5"), ("six", "6"), ("seven", "7"),
    ("eight", "8"), ("nine", "9"), ("ten", "10"), ("eleven", "11"), ("twelve", "12"), ("thirteen", "13"), ("fourteen", "14"),
    ("fifteen", "15"), ("sixteen", "16"), ("seventeen", "17"), ("eighteen", "18"), ("nineteen", "19"), ("twenty", "20"),
    ("thirty", "30"), ("forty", "40"), ("fifty", "50"), ("sixty", "60"), ("seventy", "70"), ("eighty", "80"), ("ninety", "90"),
    ("hundred", "100"),
];

fn numword(t: &str) -> &str {
    NUMWORDS.iter().find(|(w, _)| *w == t).map(|(_, d)| *d).unwrap_or(t)
}

/// `_BRACKETS_RE.sub(" ", text)` with `[\(\[\{][^\)\]\}]*[\)\]\}]`: an opener, then any run without a closer, then the first closer.
pub fn strip_brackets_pub(s: &str) -> String {
    strip_brackets(s)
}

fn strip_brackets(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let is_open = |c: char| matches!(c, '(' | '[' | '{');
    let is_close = |c: char| matches!(c, ')' | ']' | '}');
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        if is_open(chars[i]) {
            if let Some(off) = chars[i + 1..].iter().position(|c| is_close(*c)) {
                out.push(' ');
                i += off + 2;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Blocks where recent Unicode releases added cased letters (Latin Extended-D, Garay, Beria Erfe/Medefaidrin). Python's `str.lower()` uses
/// the Unicode tables of the interpreter that runs the app (3.12 = 15.0 ... 3.14 = 16.0) while Rust's are fixed at build time (Unicode 17
/// here), so letters added in between lowercase differently. Text containing them is declined instead of guessed.
const UNICODE_SKEW_RANGES: &[(u32, u32)] = &[(0xA7C0, 0xA7FF), (0x10D40, 0x10D8F), (0x16E40, 0x16EFF)];

pub fn unicode_skew_risk(c: char) -> bool {
    UNICODE_SKEW_RANGES.iter().any(|(lo, hi)| (*lo..=*hi).contains(&(c as u32)))
}

/// `str.lower()` followed by the app's NBSP replacement.
fn lower_nbsp(s: &str) -> R<String> {
    if let Some(c) = s.chars().find(|c| unicode_skew_risk(*c)) {
        return decline(format!("Unicode table skew risk: U+{:04X}", c as u32));
    }
    Ok(s.to_lowercase().replace('\u{a0}', " "))
}

pub fn identity_tokens(s: &str) -> R<Vec<String>> {
    let text = strip_brackets(&lower_nbsp(s)?).replace('&', " and ");
    let mut tokens: Vec<String> = Vec::new();
    let mut run = String::new();
    let flush = |run: &mut String, tokens: &mut Vec<String>| {
        if run.is_empty() {
            return;
        }
        match run.as_str() {
            "gonna" => {
                tokens.push(numword("going").to_string());
                tokens.push(numword("to").to_string());
            }
            "wanna" => {
                tokens.push(numword("want").to_string());
                tokens.push(numword("to").to_string());
            }
            other => tokens.push(numword(other).to_string()),
        }
        run.clear();
    };
    for c in text.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            run.push(c);
        } else {
            flush(&mut run, &mut tokens);
        }
    }
    flush(&mut run, &mut tokens);
    if tokens.len() > 1 && tokens[0] == "the" {
        tokens.remove(0);
    }
    Ok(tokens)
}

pub fn fuzzy_match_key(s: &str) -> R<String> {
    Ok(identity_tokens(s)?.concat())
}

pub fn normalize_text(s: &str) -> R<String> {
    Ok(py_split_join(&lower_nbsp(s)?))
}

/// `os.path.basename`
pub fn basename(p: &str) -> &str {
    p.rsplit('/').next().unwrap_or("")
}
