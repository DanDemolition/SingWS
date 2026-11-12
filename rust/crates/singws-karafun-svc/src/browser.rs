//! W3: the decisions of `karafun_web.ensure_browser` / `_download_browser` / `downloaded_chrome` (which Chrome for Testing, where, what is checked), transcribed
//! from the Python (the oracle). Pure over a SCRIPTED world: the caller supplies what the network, `ditto` and the program's `--version` answered; this
//! module says what the Python would decide and in which order (index parsing, the https + three-hosts rule, progress calls, the size check, unpack, program
//! present, version check). It performs no I/O: a real download is the caller's job (the `singws-http` crate is the planned transport).
//! Anything whose Python spelling is not modelled (non-ASCII or odd characters in a URL, string-valued numbers, a version that is not a plain name) is DECLINED.
use serde_json::{json, Value};

pub const CFT_HOSTS: [&str; 3] = ["storage.googleapis.com", "edgedl.me.gvt1.com", "dl.google.com"];
pub const CHUNK: i64 = 1 << 20;

type R<T> = Result<T, String>;

pub fn platform_key(machine: &str) -> &'static str {
    if machine == "arm64" || machine == "aarch64" {
        "mac-arm64"
    } else {
        "mac-x64"
    }
}

/// `downloaded_chrome`: folders are `(name, is_dir, has_binary, has_complete)`; the newest (by name, as Python sorts strings) with program and `.complete`.
pub fn pick(folders: &[(String, bool, bool, bool)]) -> Option<String> {
    let mut names: Vec<&(String, bool, bool, bool)> = folders.iter().filter(|f| f.1 && !f.0.starts_with('.')).collect();
    names.sort_by(|a, b| b.0.as_bytes().cmp(a.0.as_bytes())); // UTF-8 byte order == code point order
    names.into_iter().find(|f| f.2 && f.3).map(|f| f.0.clone())
}

fn plain_url(url: &str) -> bool {
    url.chars().all(|c| ('!'..='~').contains(&c) && c != '\\' && c != '[' && c != ']')
}

/// The host as `urlparse(url).hostname` gives it (lower case), `None` when there is none. Declines URLs it does not model.
fn hostname(url: &str) -> R<Option<String>> {
    if !plain_url(url) {
        return Err("url with characters not modelled".into());
    }
    let rest = if let Some(r) = url.strip_prefix("//") {
        r
    } else {
        // "scheme://": letters first, then letters, digits, + - .
        let i = url.find(':').ok_or("no scheme and no //: not modelled")?;
        let scheme = &url[..i];
        let ok = !scheme.is_empty() && scheme.chars().next().unwrap().is_ascii_alphabetic() && scheme.chars().all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c));
        match url[i + 1..].strip_prefix("//") {
            Some(r) if ok => r,
            _ => return Err("url shape not modelled".into()),
        }
    };
    let end = rest.find(|c| c == '/' || c == '?' || c == '#').unwrap_or(rest.len());
    let netloc = &rest[..end];
    let hostinfo = match netloc.rfind('@') {
        Some(i) => &netloc[i + 1..],
        None => netloc,
    };
    let host = hostinfo.split(':').next().unwrap_or("").to_ascii_lowercase();
    Ok(if host.is_empty() { None } else { Some(host) })
}

fn py_strip(s: &str) -> R<&str> {
    if s.chars().any(|c| (c.is_control() && !matches!(c, '\n' | '\t' | '\r')) || !c.is_ascii()) {
        return Err("text with characters whose strip() is not modelled".into());
    }
    Ok(s.trim_matches(|c: char| c.is_ascii_whitespace()))
}

fn chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Index -> `(version, url)`; `Ok(None)` = the Python's "could not find a browser" failure (index_invalid).
fn parse_index(text: &str, key: &str) -> R<Option<(String, Value)>> {
    if text.contains("NaN") || text.contains("Infinity") {
        return Err("non-standard JSON constants".into());
    }
    let v: Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return Ok(None),
    };
    let stable = match v.get("channels").and_then(|c| c.get("Stable")) {
        Some(s) => s,
        None => return Ok(None),
    };
    let version = match stable.get("version") {
        Some(Value::String(s)) => s.clone(),
        Some(_) => return Err("version not a string".into()),
        None => return Ok(None),
    };
    let list = match stable.get("downloads").and_then(|d| d.get("chrome")).and_then(|c| c.as_array()) {
        Some(l) => l,
        None => return Ok(None),
    };
    for d in list {
        let obj = match d.as_object() {
            Some(o) => o,
            None => return Ok(None), // d["platform"] on a non-dict raises
        };
        let plat = match obj.get("platform") {
            Some(p) => p,
            None => return Ok(None),
        };
        if plat.as_str() == Some(key) {
            return match obj.get("url") {
                Some(u) => Ok(Some((version, u.clone()))),
                None => Ok(None),
            };
        }
    }
    Ok(None)
}

/// Python `int(s)` for a Content-Length header: plain ASCII digits with an optional minus; text with no digits is a ValueError; the rest is declined.
fn header_int(h: &str) -> R<Result<i64, ()>> {
    let t = h;
    let (neg, digits) = match t.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, t),
    };
    if !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()) {
        let n: i64 = digits.parse().map_err(|_| "too long".to_string())?;
        return Ok(Ok(if neg { -n } else { n }));
    }
    if t.chars().all(|c| c.is_ascii_alphabetic()) && !t.is_empty() {
        return Ok(Err(()));
    }
    Err("content-length text not modelled".into())
}

/// `ensure_browser` over a scripted world. `req` fields:
/// `existing` [[name,is_dir,has_binary,has_complete]], `machine`, `verify` (bool),
/// `index` {"error": text} | {"text": json text}, `download` {"error": text} | {"content_length": str|null, "bytes": n},
/// `ditto` {"rc": n, "stderr": text}, `binary_present` (bool), `version_check` {"error": text} | {"said": text}.
/// Answer: {"have": name} | {"ok": version, "progress": [[done, total]..]} | {"error": message} | {"error_kind": "index_invalid"} | {"raises": "ValueError"}.
pub fn ensure(req: &Value) -> R<Value> {
    let folders: Vec<(String, bool, bool, bool)> = req
        .get("existing")
        .and_then(|e| e.as_array())
        .map(|a| {
            a.iter()
                .map(|f| (f[0].as_str().unwrap_or("").to_string(), f[1].as_bool().unwrap_or(false), f[2].as_bool().unwrap_or(false), f[3].as_bool().unwrap_or(false)))
                .collect()
        })
        .unwrap_or_default();
    if let Some(name) = pick(&folders) {
        return Ok(json!({"have": name}));
    }
    let prefix = "the player's own browser is not on this Mac yet and could not be downloaded (internet needed once): ";
    let wrap = |m: String| Ok(json!({"error": format!("{prefix}{m}")}));
    let key = platform_key(req.get("machine").and_then(|m| m.as_str()).unwrap_or(""));
    let idx = req.get("index").cloned().unwrap_or(json!({}));
    if let Some(t) = idx.get("error").and_then(|e| e.as_str()) {
        return wrap(format!("could not find a browser to download: {}", py_strip_none(t)?));
    }
    let (version, url) = match parse_index(idx.get("text").and_then(|t| t.as_str()).unwrap_or(""), key)? {
        Some(x) => x,
        None => return Ok(json!({"error_kind": "index_invalid"})),
    };
    let url = match url {
        Value::String(s) => s,
        _ => return Err("url not a string".into()),
    };
    let host = hostname(&url)?;
    let shown = host.clone().unwrap_or_default();
    if !url.starts_with("https://") || !CFT_HOSTS.contains(&shown.as_str()) {
        return wrap(format!("the browser download address is not Google's ({})", if shown.is_empty() { chars(&url, 40) } else { shown }));
    }
    // Python: re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", version) and no ".." (the version names a folder under the browser root)
    let plain = version.chars().next().map_or(false, |c| c.is_ascii_alphanumeric()) && version.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-')) && !version.contains("..");
    if !plain {
        return wrap("the browser version is not a plain name".into());
    }
    let dl = req.get("download").cloned().unwrap_or(json!({}));
    if let Some(t) = dl.get("error").and_then(|e| e.as_str()) {
        return wrap(py_strip_none(t)?.to_string());
    }
    let total = match dl.get("content_length") {
        None | Some(Value::Null) => 0,
        Some(Value::String(s)) if s.is_empty() => 0,
        Some(Value::String(s)) => match header_int(s)? {
            Ok(n) => n,
            Err(()) => return Ok(json!({"raises": "ValueError"})),
        },
        Some(_) => return Err("content-length not a string".into()),
    };
    let bytes = dl.get("bytes").and_then(|b| b.as_i64()).unwrap_or(0);
    let mut progress = vec![];
    let mut done = 0;
    while done < bytes {
        done += (bytes - done).min(CHUNK);
        progress.push(json!([done, total]));
    }
    if total != 0 && done != total {
        return wrap(format!("the download stopped early ({done} of {total} bytes)"));
    }
    let dit = req.get("ditto").cloned().unwrap_or(json!({"rc": 0, "stderr": ""}));
    if dit.get("rc").and_then(|r| r.as_i64()).unwrap_or(0) != 0 {
        let e = dit.get("stderr").and_then(|s| s.as_str()).unwrap_or("");
        return wrap(format!("the browser could not be unpacked: {}", chars(py_strip(e)?, 200)));
    }
    if !req.get("binary_present").and_then(|b| b.as_bool()).unwrap_or(true) {
        return wrap("the downloaded browser has no program in it".into());
    }
    if req.get("verify").and_then(|v| v.as_bool()).unwrap_or(true) {
        let vc = req.get("version_check").cloned().unwrap_or(json!({}));
        if let Some(t) = vc.get("error").and_then(|e| e.as_str()) {
            return wrap(format!("the downloaded browser would not run: {}", py_strip_none(t)?));
        }
        let said = vc.get("said").and_then(|s| s.as_str()).unwrap_or("");
        if !said.contains("Google Chrome for Testing") || !said.contains(version.as_str()) {
            return wrap(format!("the downloaded browser is not the one asked for: {}", chars(py_strip(said)?, 120)));
        }
    }
    // a plain FILE with the version's name is removed first (the Python unlinks it), so the install goes through
    Ok(json!({"ok": version, "progress": progress}))
}

fn py_strip_none(s: &str) -> R<&str> {
    if !s.is_ascii() {
        return Err("non-ASCII error text".into());
    }
    Ok(s)
}

pub fn handle(req: &Value) -> Value {
    match req.get("op").and_then(|o| o.as_str()).unwrap_or("") {
        "platform_key" => json!({"key": platform_key(req.get("machine").and_then(|m| m.as_str()).unwrap_or(""))}),
        "ensure" => match ensure(req) {
            Ok(v) => v,
            Err(r) => json!({"declined": r}),
        },
        o => json!({"declined": format!("unknown op {o}")}),
    }
}
