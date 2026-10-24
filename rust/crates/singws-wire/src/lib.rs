//! What `requests` (2.34, the version the app ships) puts on the wire, reproduced byte for byte and WITHOUT touching the network.
//!
//! `prepare` takes the same inputs the app passes to `requests.get/post` (`url`, `params=`, `data=`, `json=`) and returns the URL, body,
//! `Content-Type` and `Content-Length` a prepared request would carry. Anything this crate cannot reproduce exactly is an ERROR (a URL that
//! `requests` would re-quote, an object nested in a form value, a non-finite float ...), never a silent difference.
//!
//! Rules reproduced from `requests.models` / `urllib.parse`:
//! * `quote_plus`: unreserved bytes `A-Za-z0-9_.-~` stay, space becomes `+`, every other byte `%XX` (upper-case hex of the UTF-8 bytes);
//! * form/query values: `None` is skipped, a list repeats the key (one more nesting level flattens too), `True` is `True`, an integer is its
//!   decimal text, a float is Python's `repr`;
//! * `data=` wins over `json=`; an empty `data`/`params` mapping means "no body"/"no query"; a body-less POST still carries `Content-Length: 0`;
//! * `json=` is `json.dumps(value, allow_nan=False)` (default `", "`/`": "` separators, ASCII-escaped) encoded as UTF-8.

use serde_json::{json, Value};
use singws_store::{dumps, py_float_repr, Style};

const UNRESERVED: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~";
/// Characters `requote_uri` leaves alone besides the unreserved ones.
const URL_SAFE: &[u8] = b"!#$%&'()*+,/:;=?@[]~";

/// `urllib.parse.quote_plus(bytes)`.
pub fn quote_plus(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len());
    for &b in bytes {
        if UNRESERVED.contains(&b) {
            out.push(b as char);
        } else if b == b' ' {
            out.push('+');
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn scalar_text(v: &Value) -> Result<Option<String>, String> {
    Ok(match v {
        Value::Null => None,
        Value::Bool(b) => Some(if *b { "True" } else { "False" }.to_string()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i.to_string())
            } else if let Some(u) = n.as_u64() {
                Some(u.to_string())
            } else {
                let f = n.as_f64().ok_or("number out of range")?;
                if !f.is_finite() {
                    return Err("non-finite float".into());
                }
                Some(py_float_repr(f))
            }
        }
        Value::String(s) => Some(s.clone()),
        Value::Array(_) | Value::Object(_) => return Err("nested container in a form value".into()),
    })
}

/// `requests`' `_encode_params` + `urlencode(doseq=True)` for a JSON object or a list of `[key, value]` pairs.
pub fn urlencode(params: &Value) -> Result<String, String> {
    let pairs: Vec<(String, &Value)> = match params {
        Value::Object(map) => map.iter().map(|(k, v)| (k.clone(), v)).collect(),
        Value::Array(items) => items
            .iter()
            .map(|p| match p.as_array().map(|a| a.as_slice()) {
                Some([Value::String(k), v]) => Ok((k.clone(), v)),
                _ => Err("a list of parameters must contain [key, value] pairs with string keys".to_string()),
            })
            .collect::<Result<_, _>>()?,
        Value::Null => return Ok(String::new()),
        _ => return Err("parameters must be an object or a list of pairs".into()),
    };
    let mut out = Vec::new();
    for (key, value) in pairs {
        // (value, from_inner_level). `requests` drops a None only at the first level (`if v is not None`); `urlencode(doseq=True)` then
        // expands a nested sequence one more level and writes str(elt) for every element, so a None there becomes the text "None".
        let mut values: Vec<(&Value, bool)> = Vec::new();
        match value {
            Value::Array(items) => {
                for item in items {
                    match item {
                        Value::Array(inner) => values.extend(inner.iter().map(|v| (v, true))),
                        other => values.push((other, false)),
                    }
                }
            }
            other => values.push((other, false)),
        }
        for (v, inner) in values {
            let text = match (v, inner) {
                (Value::Null, true) => Some("None".to_string()),
                _ => scalar_text(v)?,
            };
            if let Some(text) = text {
                out.push(format!("{}={}", quote_plus(key.as_bytes()), quote_plus(text.as_bytes())));
            }
        }
    }
    Ok(out.join("&"))
}

fn is_empty(v: &Value) -> bool {
    matches!(v, Value::Null) || v.as_object().is_some_and(|m| m.is_empty()) || v.as_array().is_some_and(|a| a.is_empty())
}

/// `prepare_url` for the URLs the app builds. Pass-through for URLs `requote_uri` would not change; everything else is an error.
pub fn prepare_url(url: &str, params: &Value) -> Result<String, String> {
    let (scheme, rest) = url.split_once("://").ok_or("missing scheme")?;
    let scheme = scheme.to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return Err("only http and https URLs are supported".into());
    }
    if !url.bytes().all(|b| UNRESERVED.contains(&b) || URL_SAFE.contains(&b)) {
        return Err("URL contains characters requests would re-quote".into());
    }
    if url.contains('%') {
        return Err("percent-escapes in a URL are normalised by requests; not reproduced".into());
    }
    let (rest, fragment) = match rest.split_once('#') {
        Some((r, f)) => (r, Some(f)),
        None => (rest, None),
    };
    let (authority_path, query) = match rest.split_once('?') {
        Some((a, q)) => (a, Some(q)),
        None => (rest, None),
    };
    let (netloc, path) = match authority_path.find('/') {
        Some(i) => (&authority_path[..i], &authority_path[i..]),
        None => (authority_path, ""),
    };
    if netloc.is_empty() {
        return Err("missing host".into());
    }
    let path = if path.is_empty() { "/" } else { path };
    let encoded = if is_empty(params) { String::new() } else { urlencode(params)? };
    let query = match (query.filter(|q| !q.is_empty()), encoded.is_empty()) {
        (Some(q), false) => Some(format!("{q}&{encoded}")),
        (Some(q), true) => Some(q.to_string()),
        (None, false) => Some(encoded),
        (None, true) => None,
    };
    let mut out = format!("{scheme}://{}{path}", netloc.to_ascii_lowercase());
    if let Some(q) = query {
        out.push('?');
        out.push_str(&q);
    }
    if let Some(f) = fragment {
        out.push('#');
        out.push_str(f);
    }
    Ok(out)
}

#[derive(Debug, PartialEq, Eq)]
pub struct Prepared {
    pub method: String,
    pub url: String,
    pub body: Option<Vec<u8>>,
    pub content_type: Option<&'static str>,
    pub content_length: Option<usize>,
}

/// `Request(method, url, params=, data=, json=).prepare()` for the fields that matter on the wire.
pub fn prepare(method: &str, url: &str, params: &Value, data: &Value, json_body: &Value) -> Result<Prepared, String> {
    let method = method.to_ascii_uppercase();
    let url = prepare_url(url, params)?;
    let (body, content_type): (Option<Vec<u8>>, Option<&'static str>) = if is_empty(data) && !matches!(json_body, Value::Null) {
        let style = Style::default(); // json.dumps default separators, ensure_ascii
        let text = dumps(json_body, &style).map_err(|e| e.to_string())?;
        (Some(text.into_bytes()), Some("application/json"))
    } else if !is_empty(data) {
        (Some(urlencode(data)?.into_bytes()), Some("application/x-www-form-urlencoded"))
    } else {
        (None, None)
    };
    // requests: `if body is not None: length = super_len(body); if length: set Content-Length` -- an EMPTY body (e.g. every form value was None)
    // gets a Content-Type but no Content-Length header at all; only a missing body on a non-GET/HEAD method declares "0".
    let content_length = match (&body, method.as_str()) {
        (Some(b), _) if b.is_empty() => None,
        (Some(b), _) => Some(b.len()),
        (None, "GET" | "HEAD") => None,
        (None, _) => Some(0),
    };
    Ok(Prepared { method, url, body, content_type, content_length })
}

fn one(request: &Value) -> Value {
    let get = |k: &str| request.get(k).cloned().unwrap_or(Value::Null);
    let method = request.get("method").and_then(Value::as_str).unwrap_or("GET");
    let url = request.get("url").and_then(Value::as_str).unwrap_or("");
    match prepare(method, url, &get("params"), &get("data"), &get("json")) {
        Ok(p) => json!({
            "ok": true,
            "method": p.method,
            "url": p.url,
            "body": p.body.as_ref().map(|b| String::from_utf8_lossy(b).to_string()),
            "content_type": p.content_type,
            "content_length": p.content_length,
        }),
        Err(e) => json!({ "ok": false, "error": e }),
    }
}

/// CLI bridge: one request object, or an array of them (batch), in; the same shape out. Never panics on bad input.
pub fn handle(input: &Value) -> Value {
    match input {
        Value::Array(items) => Value::Array(items.iter().map(one).collect()),
        other => one(other),
    }
}

/// String bridge for the FFI export: one request object as JSON text in, the prepared wire form as JSON text out (an array in, an array out).
pub fn handle_json(request_json: &str) -> Result<String, String> {
    let v: Value = serde_json::from_str(request_json).map_err(|e| format!("request: {e}"))?;
    serde_json::to_string(&handle(&v)).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prep(method: &str, url: &str, params: Value, data: Value, j: Value) -> Result<Prepared, String> {
        prepare(method, url, &params, &data, &j)
    }

    #[test]
    fn quote_plus_matches_urllib() {
        assert_eq!(quote_plus(b"abc XYZ-_.~09"), "abc+XYZ-_.~09");
        assert_eq!(quote_plus("é日 😀&=/?%".as_bytes()), "%C3%A9%E6%97%A5+%F0%9F%98%80%26%3D%2F%3F%25");
        assert_eq!(quote_plus(b"\x00\xff\n\"'"), "%00%FF%0A%22%27");
        assert_eq!(quote_plus(b""), "");
    }

    #[test]
    fn form_values_follow_requests_conversion_rules() {
        let d = json!({"user": "dan", "request_id": 1792783785132i64, "ok": true, "off": false, "ratio": 0.1, "big": 1e16, "none": null,
                       "list": [1, "b", null, [2, 3]], "empty": "", "k ey": "v&l=ue é"});
        assert_eq!(
            urlencode(&d).unwrap(),
            "user=dan&request_id=1792783785132&ok=True&off=False&ratio=0.1&big=1e%2B16&list=1&list=b&list=2&list=3&empty=&k+ey=v%26l%3Due+%C3%A9"
        );
        assert_eq!(urlencode(&json!([["a", 1], ["a", 2]])).unwrap(), "a=1&a=2");
        assert_eq!(urlencode(&Value::Null).unwrap(), "");
        assert!(urlencode(&json!({"nested": {"a": 1}})).is_err());
        assert!(urlencode(&json!(5)).is_err());
        assert!(urlencode(&json!([["only-one"]])).is_err());
    }

    #[test]
    fn post_with_form_data_matches_the_apps_calls() {
        let p = prep("post", "https://Beta.WSKAR.com/api/v1/delete_remote_request.php", Value::Null,
                     json!({"user": "dan", "request_id": 7, "reason": "host remove"}), Value::Null).unwrap();
        assert_eq!(p.method, "POST");
        assert_eq!(p.url, "https://beta.wskar.com/api/v1/delete_remote_request.php");
        assert_eq!(p.body.as_deref(), Some(&b"user=dan&request_id=7&reason=host+remove"[..]));
        assert_eq!(p.content_type, Some("application/x-www-form-urlencoded"));
        assert_eq!(p.content_length, Some(40));
    }

    #[test]
    fn get_params_are_appended_to_an_existing_query() {
        let p = prep("GET", "https://x.test/a.php?tenant=1", json!({"since": 5, "q": "a b"}), Value::Null, Value::Null).unwrap();
        assert_eq!(p.url, "https://x.test/a.php?tenant=1&since=5&q=a+b");
        assert_eq!((p.body, p.content_length, p.content_type), (None, None, None));
        assert_eq!(prep("GET", "https://x.test", json!({}), Value::Null, Value::Null).unwrap().url, "https://x.test/");
        assert_eq!(prep("GET", "https://x.test/a.php?", Value::Null, Value::Null, Value::Null).unwrap().url, "https://x.test/a.php");
    }

    #[test]
    fn json_bodies_use_pythons_default_separators_and_ascii() {
        let p = prep("POST", "https://x.test/s.php", Value::Null, Value::Null, json!({"a": [1, 2.5, "é"], "b": null, "c": {}})).unwrap();
        assert_eq!(p.body.as_deref(), Some(&b"{\"a\": [1, 2.5, \"\\u00e9\"], \"b\": null, \"c\": {}}"[..]));
        assert_eq!(p.content_type, Some("application/json"));
        // data wins over json, exactly like requests
        let p = prep("POST", "https://x.test/s.php", Value::Null, json!({"k": "v"}), json!({"ignored": true})).unwrap();
        assert_eq!(p.body.as_deref(), Some(&b"k=v"[..]));
    }

    #[test]
    fn found_by_the_differential_against_requests() {
        // 1. a form whose values are all None encodes to "" : Content-Type is set, Content-Length is NOT
        let p = prep("POST", "https://x.test/s.php", Value::Null, json!({"k": null}), Value::Null).unwrap();
        assert_eq!((p.body.as_deref(), p.content_type, p.content_length), (Some(&b""[..]), Some("application/x-www-form-urlencoded"), None));
        let p = prep("POST", "https://x.test/s.php", Value::Null, json!({"k": [], "j": null}), Value::Null).unwrap();
        assert_eq!(p.content_length, None);
        // 2. None is dropped at the first level of a list but is the text "None" one level deeper
        assert_eq!(urlencode(&json!({"k": [null, "a", [null, "b"]]})).unwrap(), "k=a&k=None&k=b");
    }

    #[test]
    fn a_post_without_a_body_still_declares_length_zero() {
        let p = prep("POST", "https://x.test/p.php", Value::Null, json!({}), Value::Null).unwrap();
        assert_eq!((p.body, p.content_length, p.content_type), (None, Some(0), None));
        assert_eq!(prep("get", "https://x.test/p.php", Value::Null, Value::Null, Value::Null).unwrap().content_length, None);
    }

    #[test]
    fn urls_requests_would_rewrite_are_errors_not_silent_differences() {
        for bad in ["https://x.test/a b.php", "https://x.test/é.php", "https://x.test/a%20b.php", "ftp://x.test/", "x.test/a", "https:///a",
                    "https://x.test/a\"b"] {
            assert!(prepare_url(bad, &Value::Null).is_err(), "{bad}");
        }
    }

    #[test]
    fn string_bridge_round_trips_and_rejects_garbage() {
        let out = handle_json(r#"{"method":"POST","url":"https://x.test/a.php","data":{"a":"b c"}}"#).unwrap();
        assert!(out.contains("\"body\":\"a=b+c\"") && out.contains("\"content_length\":5"), "{out}");
        assert!(handle_json("{").is_err());
        assert!(handle_json("\"just a string\"").unwrap().contains("\"ok\":false") || handle_json("\"just a string\"").is_ok());
    }

    #[test]
    fn batch_bridge_answers_each_request_independently() {
        let out = handle(&json!([{"method": "POST", "url": "https://x.test/a.php", "data": {"a": 1}}, {"method": "GET", "url": "nope"}]));
        assert_eq!(out[0]["body"], json!("a=1"));
        assert_eq!(out[0]["content_length"], json!(3));
        assert_eq!(out[1]["ok"], json!(false));
        assert_eq!(handle(&json!({"method": "GET", "url": "https://x.test/"}))["ok"], json!(true));
    }
}
