//! A JSON writer whose output is byte-identical to Python's `json.dumps(value, **style)`.
//!
//! The app's stores (settings, singer history, queue, caches) are plain JSON written by Python. A Rust implementation may only take
//! over a file if it writes exactly what Python would, otherwise an upgrade silently rewrites the whole file in a different layout
//! (and `_JSON_WRITE_DIGESTS`-style change detection, diffs and rollbacks all become noise). This crate is that guarantee, proven
//! by a differential test against Python (`test_store_roundtrip.py`) and a round trip of every live store file.
//!
//! Compatibility rules reproduced from CPython's `json.encoder`:
//! * object key order is preserved (serde_json `preserve_order`); `sort_keys` sorts by Unicode code point;
//! * `ensure_ascii` escapes everything outside space..`~` (so DEL becomes `\u007f`) with surrogate pairs above U+FFFF; without it only
//!   `"`, `\`, and control characters below U+0020 are escaped (short forms for \b \f \n \r \t, `\u00XX` otherwise);
//! * floats use Python's `repr`: shortest round-trip digits, fixed notation for 1e-4 <= |x| < 1e16, otherwise `d.ddde+XX`/`e-XX` with a
//!   sign and at least two exponent digits; integral floats keep `.0`; `-0.0` stays `-0.0`;
//! * `indent=None` uses `", "` and `": "` separators, an integer indent puts one item per line, `separators=(",", ":")` is compact;
//!   empty containers are `[]` and `{}` in every mode.
//!
//! Known limits (all reported as errors, never silently altered): integers outside i64/u64 (serde_json would turn them into floats),
//! and the non-standard tokens NaN/Infinity (Python emits them, JSON parsers reject them).

use serde_json::Value;

pub mod settings;

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Style {
    /// `None` = single line (`json.dumps(indent=None)`), `Some(n)` = n-space indentation.
    #[serde(default)]
    pub indent: Option<usize>,
    /// `separators=(",", ":")`; only meaningful with `indent=None` in the app (with an indent Python uses `","` + newline regardless).
    #[serde(default)]
    pub compact: bool,
    #[serde(default = "yes")]
    pub ensure_ascii: bool,
    #[serde(default)]
    pub sort_keys: bool,
    /// Append `"\n"` like `_save_json_atomic` does.
    #[serde(default)]
    pub trailing_newline: bool,
}

fn yes() -> bool {
    true
}

impl Default for Style {
    fn default() -> Self {
        Style { indent: None, compact: false, ensure_ascii: true, sort_keys: false, trailing_newline: false }
    }
}

impl Style {
    /// What `_save_json_atomic` writes for everything except tracks.json: `json.dumps(value, indent=2) + "\n"`.
    pub fn app_default() -> Self {
        Style { indent: Some(2), trailing_newline: true, ..Style::default() }
    }
    /// What `_save_json_atomic` writes for tracks.json: `json.dumps(value, separators=(",", ":")) + "\n"`.
    pub fn app_tracks() -> Self {
        Style { compact: true, trailing_newline: true, ..Style::default() }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum StoreError {
    Parse(String),
    /// A value Python could write but this crate cannot reproduce exactly.
    Unsupported(String),
}

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StoreError::Parse(m) => write!(f, "parse: {m}"),
            StoreError::Unsupported(m) => write!(f, "unsupported: {m}"),
        }
    }
}

impl std::error::Error for StoreError {}

/// Python's `repr(float)` for a finite f64.
pub fn py_float_repr(x: f64) -> String {
    if x == 0.0 {
        return if x.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    // Rust's `{:e}` yields the shortest digits that round-trip, e.g. "1.2345e-5" or "5e-324". Only its LENGTH is trusted: when two
    // candidates of that length are equally close to the true value (e.g. 900719925474099.25 -> ...99.2 or ...99.3) CPython's dtoa
    // picks the even digit while Rust's shortest printer rounds up. The correctly rounded n-digit value from the exact formatter
    // is always within the round-trip interval when any n-digit value is, and breaks ties the way CPython does.
    let shortest = format!("{:e}", x.abs());
    let n = shortest.split_once('e').expect("LowerExp always has an exponent").0.chars().filter(|c| c.is_ascii_digit()).count();
    let sci = format!("{:.*e}", n - 1, x.abs());
    let (mantissa, exp) = sci.split_once('e').expect("LowerExp always has an exponent");
    let exp10: i32 = exp.parse().expect("exponent is an integer");
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let sign = if x < 0.0 { "-" } else { "" };
    // Python's repr switches to exponent form when the decimal point position is > 16 or < -3.
    let decpt = exp10 + 1;
    if decpt > 16 || decpt < -3 {
        let mut s = String::from(sign);
        s.push_str(&digits[..1]);
        if digits.len() > 1 {
            s.push('.');
            s.push_str(&digits[1..]);
        }
        s.push('e');
        s.push(if exp10 < 0 { '-' } else { '+' });
        s.push_str(&format!("{:02}", exp10.abs()));
        return s;
    }
    let mut s = String::from(sign);
    if decpt <= 0 {
        s.push_str("0.");
        for _ in 0..(-decpt) {
            s.push('0');
        }
        s.push_str(&digits);
    } else if (decpt as usize) >= digits.len() {
        s.push_str(&digits);
        for _ in 0..(decpt as usize - digits.len()) {
            s.push('0');
        }
        s.push_str(".0");
    } else {
        s.push_str(&digits[..decpt as usize]);
        s.push('.');
        s.push_str(&digits[decpt as usize..]);
    }
    s
}

fn write_string(out: &mut String, s: &str, ensure_ascii: bool) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if ensure_ascii && !(' '..='~').contains(&c) => {
                let mut units = [0u16; 2];
                for u in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{:04x}", u));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_value(out: &mut String, v: &Value, st: &Style, depth: usize) -> Result<(), StoreError> {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                out.push_str(&i.to_string());
            } else if let Some(u) = n.as_u64() {
                out.push_str(&u.to_string());
            } else if let Some(f) = n.as_f64() {
                if !f.is_finite() {
                    return Err(StoreError::Unsupported("non-finite float".into()));
                }
                out.push_str(&py_float_repr(f));
            } else {
                return Err(StoreError::Unsupported(format!("number {n}")));
            }
        }
        Value::String(s) => write_string(out, s, st.ensure_ascii),
        Value::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return Ok(());
            }
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                separator(out, st, depth + 1, i == 0);
                write_value(out, item, st, depth + 1)?;
            }
            close(out, st, depth, ']');
        }
        Value::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return Ok(());
            }
            out.push('{');
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            if st.sort_keys {
                entries.sort_by(|a, b| a.0.cmp(b.0));
            }
            for (i, (k, val)) in entries.into_iter().enumerate() {
                separator(out, st, depth + 1, i == 0);
                write_string(out, k, st.ensure_ascii);
                out.push_str(if st.indent.is_none() && st.compact { ":" } else { ": " });
                write_value(out, val, st, depth + 1)?;
            }
            close(out, st, depth, '}');
        }
    }
    Ok(())
}

fn separator(out: &mut String, st: &Style, depth: usize, first: bool) {
    match st.indent {
        Some(n) => {
            if !first {
                out.push(',');
            }
            out.push('\n');
            for _ in 0..(n * depth) {
                out.push(' ');
            }
        }
        None => {
            if !first {
                out.push_str(if st.compact { "," } else { ", " });
            }
        }
    }
}

fn close(out: &mut String, st: &Style, depth: usize, ch: char) {
    if let Some(n) = st.indent {
        out.push('\n');
        for _ in 0..(n * depth) {
            out.push(' ');
        }
    }
    out.push(ch);
}

/// `json.dumps(value, **style)` (plus the optional trailing newline), byte for byte.
pub fn dumps(value: &Value, style: &Style) -> Result<String, StoreError> {
    let mut out = String::new();
    write_value(&mut out, value, style, 0)?;
    if style.trailing_newline {
        out.push('\n');
    }
    Ok(out)
}

/// Reject big integers before parsing: serde_json would quietly turn them into f64 and change their digits.
pub fn reject_big_integers(text: &str) -> Result<(), StoreError> {
    let bytes = text.as_bytes();
    let mut i = 0;
    let mut in_str = false;
    while i < bytes.len() {
        let b = bytes[i];
        if in_str {
            if b == b'\\' {
                i += 1;
            } else if b == b'"' {
                in_str = false;
            }
            i += 1;
            continue;
        }
        if b == b'"' {
            in_str = true;
            i += 1;
        } else if b == b'-' || b.is_ascii_digit() {
            let start = i;
            i += 1;
            let mut is_int = true;
            while i < bytes.len() && matches!(bytes[i], b'0'..=b'9' | b'.' | b'e' | b'E' | b'+' | b'-') {
                if matches!(bytes[i], b'.' | b'e' | b'E') {
                    is_int = false;
                }
                i += 1;
            }
            if is_int {
                let tok = &text[start..i];
                if tok.parse::<i64>().is_err() && tok.parse::<u64>().is_err() {
                    return Err(StoreError::Unsupported(format!("integer outside 64 bits: {tok}")));
                }
            }
        } else {
            i += 1;
        }
    }
    Ok(())
}

/// Parse `text` and write it back in `style`. For a file Python wrote in that style the result equals the input.
pub fn roundtrip(text: &str, style: &Style) -> Result<String, StoreError> {
    reject_big_integers(text)?;
    let value: Value = serde_json::from_str(text).map_err(|e| StoreError::Parse(e.to_string()))?;
    dumps(&value, style)
}

/// JSON bridge for the Python harness: `{"text": "...", "style": {...}}` in, the rewritten text out (as plain UTF-8, not JSON).
pub fn roundtrip_bridge(text: &str, style_json: &str) -> Result<String, StoreError> {
    let style: Style = serde_json::from_str(style_json).map_err(|e| StoreError::Parse(format!("style: {e}")))?;
    roundtrip(text, &style)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn float_repr_matches_cpython_for_the_edge_cases() {
        // Expected strings are CPython's repr() output.
        for (x, want) in [
            (0.0, "0.0"), (-0.0, "-0.0"), (1.0, "1.0"), (100.0, "100.0"), (0.5, "0.5"), (-2.5, "-2.5"),
            (0.0001, "0.0001"), (0.00001, "1e-05"), (0.000123, "0.000123"), (0.0000123, "1.23e-05"),
            (1e15, "1000000000000000.0"), (1e16, "1e+16"), (1.5e16, "1.5e+16"), (123456789012345680.0, "1.2345678901234568e+17"),
            (1e22, "1e+22"), (1.5e300, "1.5e+300"), (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"),
            (0.1, "0.1"), (0.30000000000000004, "0.30000000000000004"), (1234.5678, "1234.5678"),
            (9007199254740993.0, "9007199254740992.0"), (1e-7, "1e-07"), (12345678901234567.0, "1.2345678901234568e+16"),
            // exact ties between two shortest candidates: CPython picks the even last digit
            (900719925474099.25, "900719925474099.2"), (0.125, "0.125"), (2.5, "2.5"),
        ] {
            assert_eq!(py_float_repr(x), want, "{x:e}");
        }
    }

    #[test]
    fn string_escaping_follows_ensure_ascii() {
        let v = json!("a\"b\\c\n\r\t\u{8}\u{c}\u{1}\u{7f}é日😀");
        let ascii = dumps(&v, &Style::default()).unwrap();
        assert_eq!(ascii, "\"a\\\"b\\\\c\\n\\r\\t\\b\\f\\u0001\\u007f\\u00e9\\u65e5\\ud83d\\ude00\"");
        let utf8 = dumps(&v, &Style { ensure_ascii: false, ..Style::default() }).unwrap();
        assert_eq!(utf8, "\"a\\\"b\\\\c\\n\\r\\t\\b\\f\\u0001\u{7f}é日😀\"");
    }

    #[test]
    fn layouts_match_python_dumps() {
        let v = json!({"b": [1, 2, {}], "a": {"x": [], "y": null, "z": 1.5}, "é": true});
        assert_eq!(dumps(&v, &Style::default()).unwrap(), r#"{"b": [1, 2, {}], "a": {"x": [], "y": null, "z": 1.5}, "\u00e9": true}"#);
        assert_eq!(dumps(&v, &Style { compact: true, ..Style::default() }).unwrap(), r#"{"b":[1,2,{}],"a":{"x":[],"y":null,"z":1.5},"\u00e9":true}"#);
        assert_eq!(
            dumps(&v, &Style { indent: Some(2), sort_keys: true, ..Style::default() }).unwrap(),
            "{\n  \"a\": {\n    \"x\": [],\n    \"y\": null,\n    \"z\": 1.5\n  },\n  \"b\": [\n    1,\n    2,\n    {}\n  ],\n  \"\\u00e9\": true\n}"
        );
        assert_eq!(dumps(&json!([]), &Style::app_default()).unwrap(), "[]\n");
        assert_eq!(dumps(&json!({}), &Style::app_tracks()).unwrap(), "{}\n");
    }

    #[test]
    fn key_order_and_unknown_fields_survive_a_round_trip() {
        // json.dumps(indent=2) puts every array item on its own line, even short ones
        let text = "{\n  \"zeta\": 1,\n  \"alpha\": {\n    \"future_field\": [\n      true,\n      null\n    ],\n    \"a\": 0.1\n  },\n  \"mid\": \"x\"\n}\n";
        assert_eq!(roundtrip(text, &Style::app_default()).unwrap(), text);
    }

    #[test]
    fn numbers_keep_their_kind() {
        let text = "[1, 1.0, -0.0, 1e-05, 1e+16, 9223372036854775807, -9223372036854775808, 18446744073709551615]";
        assert_eq!(roundtrip(text, &Style::default()).unwrap(), text);
        // "1e2" is a float in Python (100.0), not an integer
        assert_eq!(roundtrip("[1e2, 5E-1]", &Style::default()).unwrap(), "[100.0, 0.5]");
    }

    #[test]
    fn unsupported_values_are_errors_not_silent_changes() {
        assert!(matches!(roundtrip("[18446744073709551616]", &Style::default()), Err(StoreError::Unsupported(_))));
        assert!(matches!(roundtrip("[-9223372036854775809]", &Style::default()), Err(StoreError::Unsupported(_))));
        assert!(matches!(roundtrip("[NaN]", &Style::default()), Err(StoreError::Parse(_))));
        assert!(matches!(roundtrip("{", &Style::default()), Err(StoreError::Parse(_))));
        // digits inside strings are not numbers
        assert!(roundtrip(r#"["99999999999999999999999"]"#, &Style::default()).is_ok());
    }

    #[test]
    fn bridge_parses_the_style() {
        assert_eq!(roundtrip_bridge("[1,2]", r#"{"indent":2,"trailing_newline":true}"#).unwrap(), "[\n  1,\n  2\n]\n");
        assert_eq!(roundtrip_bridge("{\"a\": [1]}", r#"{"compact":true}"#).unwrap(), "{\"a\":[1]}");
        assert!(roundtrip_bridge("[1]", "{").is_err());
    }
}
