//! `singws-relaymsg`: the host's outgoing relay messages (`playback_tick`, `state`, `queue`, `chat`) built from a snapshot of the app's inputs.
//!
//! Counterpart of `_publish_host_state_ws` and the builders it calls (`_shell_queue_summary`, `_queue_signature`, `_shell_waitlist_summary`,
//! `_shell_chat_summary`, `_shell_chat_message` in 0.2.18.1.py). Pure functions over `serde_json::Value`; key order is the Python insertion order.
//!
//! Python semantics that are copied on purpose: `bool(x)` truthiness, `str(x or "")`, `int(x or 0)` (incl. its ValueErrors), character (code point)
//! slicing, a stable `sort(reverse=True)`, and the "an exception inside the builder leaves the dict partly filled" behaviour of the chat summary.
//! What Rust cannot do exactly (`str.casefold`, `str.title`, minting ids, a library lookup) is supplied by the host as input data.
//! A value of a kind we do not model makes the op answer `{"unsupported": reason}`; the shadow skips such a comparison.
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

/// `Raise`: Python would raise here (the app's own try/except applies). `Unsupp`: a case this crate does not model.
#[derive(Debug, Clone, PartialEq)]
pub enum E {
    Raise,
    Unsupp(String),
}
type R<T> = Result<T, E>;

fn unsupp<T>(why: &str) -> R<T> {
    Err(E::Unsupp(why.to_string()))
}

/// `str.isspace` (what `str.strip` removes); same set as singws-logpack.
pub fn py_space(c: char) -> bool {
    matches!(c as u32, 0x09..=0x0d | 0x1c..=0x20 | 0x85 | 0xa0 | 0x1680 | 0x2000..=0x200a | 0x2028 | 0x2029 | 0x202f | 0x205f | 0x3000)
}

pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

static NULL: Value = Value::Null;

fn get<'a>(v: &'a Value, k: &str) -> &'a Value {
    v.get(k).unwrap_or(&NULL)
}

/// `str(v)`.
fn py_str(v: &Value) -> R<String> {
    match v {
        Value::String(s) => Ok(s.clone()),
        Value::Null => Ok("None".into()),
        Value::Bool(b) => Ok(if *b { "True" } else { "False" }.into()),
        Value::Number(n) if n.is_i64() || n.is_u64() => Ok(n.to_string()),
        _ => unsupp("str() of a float, list or object"),
    }
}

/// `str(v or "")`.
fn s_or(v: &Value) -> R<String> {
    if truthy(v) {
        py_str(v)
    } else {
        Ok(String::new())
    }
}

/// `int(v or 0)`.
fn int_or0(v: &Value) -> R<i64> {
    if !truthy(v) {
        return Ok(0);
    }
    match v {
        Value::Bool(_) => Ok(1),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i)
            } else if let Some(f) = n.as_f64() {
                if f.is_finite() && f.abs() < 9.0e15 {
                    Ok(f.trunc() as i64)
                } else {
                    unsupp("huge number")
                }
            } else {
                unsupp("huge number")
            }
        }
        Value::String(s) => {
            let t = s.trim_matches(py_space);
            if t.contains('_') || !t.is_ascii() {
                return unsupp("int() of a string with underscores or non-ASCII characters");
            }
            let digits = t.strip_prefix(['+', '-']).unwrap_or(t);
            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
                return Err(E::Raise);
            }
            t.parse::<i64>().or_else(|_| unsupp("huge integer string"))
        }
        _ => Err(E::Raise),
    }
}

/// `int(v)` with no `or 0`: None, "" and containers raise.
fn int_strict(v: &Value) -> R<i64> {
    match v {
        Value::Null | Value::Array(_) | Value::Object(_) => Err(E::Raise),
        Value::String(s) if s.is_empty() => Err(E::Raise),
        _ => int_or0(v),
    }
}

/// `float(v or 0)` inside a try/except that falls back to 0.0.
fn float_or0_safe(v: &Value) -> f64 {
    if !truthy(v) {
        return 0.0;
    }
    match v {
        Value::Bool(_) => 1.0,
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(s) => s.trim_matches(py_space).parse::<f64>().ok().filter(|f| !f.is_nan()).unwrap_or(0.0),
        _ => 0.0,
    }
}

fn char_prefix(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn tail<T: Clone>(v: &[T], n: usize) -> Vec<T> {
    v[v.len().saturating_sub(n)..].to_vec()
}

/// `settings.get("user", settings.get("tenant", ""))`.
fn user_of(settings: &Value) -> Value {
    if let Some(u) = settings.get("user") {
        return u.clone();
    }
    settings.get("tenant").cloned().unwrap_or_else(|| json!(""))
}

// ---------------------------------------------------------------- playback_tick and state

pub fn tick(req: &Value) -> R<Value> {
    let pb = get(req, "playback");
    let f = |k: &str, d: Value| pb.get(k).cloned().unwrap_or(d);
    Ok(json!({
        "type": "playback_tick",
        "position_seconds": f("position_seconds", json!(0.0)),
        "duration_seconds": f("duration_seconds", json!(0.0)),
        "remaining_seconds": f("remaining_seconds", json!(0.0)),
        "is_playing": f("is_playing", json!(false)),
        "updated_at": get(req, "updated_at").clone(),
    }))
}

pub fn state(req: &Value) -> R<Value> {
    let st = get(req, "state");
    let playback = st.get("playback").cloned().unwrap_or_else(|| json!({}));
    let mut m = Map::new();
    m.insert("type".into(), json!("state"));
    m.insert("user".into(), user_of(get(req, "settings")));
    m.insert("app_version".into(), get(req, "app_version").clone());
    m.insert("connected".into(), json!(true));
    m.insert("updated_at".into(), get(req, "updated_at").clone());
    m.insert("rotation".into(), st.get("rotation").cloned().unwrap_or_else(|| json!({})));
    m.insert("playback".into(), playback);
    m.insert("bg_playing".into(), json!(truthy(get(st, "bg_playing"))));
    m.insert("bg_volume".into(), st.get("bg_volume").cloned().unwrap_or_else(|| json!(0.0)));
    m.insert("bg_index".into(), st.get("bg_index").cloned().unwrap_or_else(|| json!(-1)));
    m.insert("bg_track".into(), st.get("bg_track").cloned().unwrap_or_else(|| json!("")));
    if let Some(sig) = req.get("queue_sig").filter(|v| !v.is_null()) {
        m.insert("queue_sig".into(), sig.clone());
    }
    Ok(Value::Object(m))
}

// ---------------------------------------------------------------- queue

fn arr<'a>(v: &'a Value) -> &'a [Value] {
    v.as_array().map(|a| a.as_slice()).unwrap_or(&[])
}

/// `_queue_signature`: sha1 of the compact JSON of [singer_id, name, skipped, marker, [[entry_id, skipped]...]] per dict singer, first 16 hex.
pub fn queue_sig(queue: &Value) -> R<String> {
    let mut parts = Vec::new();
    for singer in arr(queue) {
        if !singer.is_object() {
            continue;
        }
        let mut songs = Vec::new();
        for e in arr(get(singer, "songs")) {
            let skipped = e.get("is_dict").map(truthy).unwrap_or(false) && truthy(get(e, "skipped"));
            songs.push(json!([py_str(get(e, "entry_id"))?, skipped]));
        }
        parts.push(json!([py_str(get(singer, "singer_id"))?, s_or(get(singer, "name"))?, truthy(get(singer, "skipped")), truthy(get(singer, "rotation_marker")), songs]));
    }
    let text = serde_json::to_string(&Value::Array(parts)).map_err(|_| E::Raise)?;
    let digest = Sha1::digest(text.as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    Ok(hex[..16].to_string())
}

/// `_shell_queue_summary`: at most 200 singers (counted before the dict filter), 25 songs each.
pub fn queue_rows(queue: &Value) -> R<Value> {
    let mut rows = Vec::new();
    for singer in arr(queue).iter().take(200) {
        if !singer.is_object() {
            continue;
        }
        let mut songs = Vec::new();
        for e in arr(get(singer, "songs")).iter().take(25) {
            let skipped = e.get("is_dict").map(truthy).unwrap_or(false) && truthy(get(e, "skipped"));
            songs.push(json!({"entry_id": py_str(get(e, "entry_id"))?, "artist": py_str(get(e, "artist"))?, "title": py_str(get(e, "title"))?, "skipped": skipped}));
        }
        rows.push(json!({
            "singer_id": py_str(get(singer, "singer_id"))?,
            "name": s_or(get(singer, "name"))?,
            "skipped": truthy(get(singer, "skipped")),
            "marker": truthy(get(singer, "rotation_marker")),
            "songs": songs,
        }));
    }
    Ok(Value::Array(rows))
}

fn compact_wait(row: &Value, section: &str) -> R<Value> {
    if !row.is_object() {
        return Err(E::Raise);
    }
    Ok(json!({"request_id": int_or0(get(row, "request_id"))?, "singer": s_or(get(row, "singer"))?, "artist": s_or(get(row, "artist"))?,
              "title": s_or(get(row, "title"))?, "status": s_or(get(row, "status_label"))?, "first_time": truthy(get(row, "first_time_singer")), "section": section}))
}

/// `_shell_waitlist_summary` from the resolved rows: `rows` of the waitlist section, `removed` = [{removed_at, row}] for every dict record tonight.
/// A Python exception inside one half leaves that half empty, as in the app.
pub fn waitlist(w: &Value) -> R<Value> {
    let waiting: R<Vec<Value>> = arr(get(w, "rows")).iter().take(40).map(|r| compact_wait(r, "waitlist")).collect();
    let mut removed: Vec<&Value> = arr(get(w, "removed")).iter().collect();
    removed.sort_by(|a, b| float_or0_safe(get(b, "removed_at")).partial_cmp(&float_or0_safe(get(a, "removed_at"))).unwrap_or(std::cmp::Ordering::Equal));
    let removed: R<Vec<Value>> = removed.iter().take(20).map(|r| compact_wait(get(r, "row"), "removed")).collect();
    let mut out = Map::new();
    out.insert("waiting".into(), Value::Array(match waiting {
        Ok(v) => v,
        Err(E::Raise) => vec![],
        Err(e) => return Err(e),
    }));
    out.insert("removed".into(), Value::Array(match removed {
        Ok(v) => v,
        Err(E::Raise) => vec![],
        Err(e) => return Err(e),
    }));
    Ok(Value::Object(out))
}

pub fn queue(req: &Value) -> R<Value> {
    let q = get(req, "queue");
    let sig = queue_sig(q)?;
    Ok(json!({"type": "queue", "user": user_of(get(req, "settings")), "queue_sig": sig, "singers": queue_rows(q)?, "waitlist": waitlist(get(req, "waitlist"))?,
              "updated_at": get(req, "updated_at").clone()}))
}

// ---------------------------------------------------------------- chat

const TEXT_MAX: usize = 120;
const GROUP_ROWS: usize = 25;
const THREADS: usize = 10;
const THREAD_ROWS: usize = 5;

/// `^https://(media[0-9]*|i)\.giphy\.com/` via `re.match`.
fn giphy_preview(s: &str) -> bool {
    let Some(rest) = s.strip_prefix("https://") else { return false };
    let host_rest = if let Some(r) = rest.strip_prefix("media") {
        r.trim_start_matches(|c: char| c.is_ascii_digit())
    } else if let Some(r) = rest.strip_prefix('i') {
        r
    } else {
        return false;
    };
    host_rest.starts_with(".giphy.com/")
}

/// `^[A-Za-z0-9_-]{1,64}$` via `re.match`: `$` also matches just before one trailing newline.
fn gif_id_ok(s: &str) -> bool {
    let body = s.strip_suffix('\n').unwrap_or(s);
    let n = body.chars().count();
    (1..=64).contains(&n) && body.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

struct Ctx<'a> {
    host_name: &'a Value,
    fold: &'a Value,
}

impl Ctx<'_> {
    fn fold(&self, s: &str) -> R<(String, String)> {
        if s.is_empty() {
            return Ok((String::new(), String::new()));
        }
        match self.fold.get(s).and_then(|v| v.as_array()) {
            Some(a) if a.len() == 2 && a[0].is_string() && a[1].is_string() => Ok((a[0].as_str().unwrap().into(), a[1].as_str().unwrap().into())),
            _ => unsupp("string missing from the fold table"),
        }
    }

    /// `_shell_chat_message`.
    fn message(&self, m: &Value, host_text: bool) -> R<Value> {
        if !m.is_object() {
            return Err(E::Raise);
        }
        let status_raw = s_or(get(m, "status"))?;
        let status = if matches!(status_raw.as_str(), "held" | "removed" | "blocked") { status_raw } else { String::new() };
        let (mine, sender);
        if host_text {
            mine = get(m, "direction") != &json!("in");
            let name = self.host_name_or("Host");
            sender = if mine { if truthy(&name) { py_str(&name)? } else { "Host".to_string() } } else { s_or(get(m, "singer"))? };
        } else {
            mine = truthy(get(m, "host")) || s_or(get(m, "from_key"))? == "@host";
            let f = get(m, "from");
            sender = if truthy(f) { py_str(f)? } else { s_or(get(m, "from_key"))? };
        }
        let kind = s_or(get(m, "kind"))?;
        let gif = get(m, "gif").as_object().map(|_| get(m, "gif"));
        let attachment = if kind == "image" && int_or0(get(m, "media_id"))? != 0 {
            "photo"
        } else if kind == "gif" && gif.map(truthy).unwrap_or(false) && giphy_preview(&s_or(get(gif.unwrap(), "preview"))?) {
            "gif"
        } else {
            ""
        };
        let text = if status == "removed" { String::new() } else { char_prefix(&s_or(get(m, "message"))?, TEXT_MAX) };
        let mut media_ref = String::new();
        if status != "removed" {
            if attachment == "photo" {
                media_ref = format!("photo:{}", int_or0(get(m, "media_id"))?);
            } else if attachment == "gif" && gif_id_ok(&s_or(get(gif.unwrap(), "id"))?) {
                media_ref = format!("gif:{}", py_str(get(gif.unwrap(), "id"))?);
            }
        }
        Ok(json!({"id": int_or0(get(m, "id"))?, "from": sender, "host": mine, "at": int_or0(get(m, "created_at"))?, "text": text,
                  "attachment": attachment, "media_ref": media_ref, "status": status, "reports": int_or0(get(m, "reports"))?,
                  "flag": char_prefix(&s_or(get(m, "flag"))?, 24)}))
    }

    /// `self.settings.get("host_chat_name", "Host")` (the host passes the key only when present).
    fn host_name_or(&self, d: &str) -> Value {
        if self.host_name.is_null() { json!(d) } else { self.host_name.clone() }
    }
}

fn bump(out: &mut Map<String, Value>, group: &str, key: &str, by: i64) {
    if let Some(Value::Object(o)) = out.get_mut(group) {
        let cur = o.get(key).and_then(|v| v.as_i64()).unwrap_or(0);
        o.insert(key.into(), json!(cur + by));
    }
}

fn set_nested(out: &mut Map<String, Value>, group: &str, key: &str, v: Value) {
    if let Some(Value::Object(o)) = out.get_mut(group) {
        o.insert(key.into(), v);
    }
}

fn sort_rooms(rooms: &Value) -> R<Vec<(i64, &Value)>> {
    let mut v = Vec::new();
    for pair in arr(rooms) {
        let key = pair.get(0).and_then(|k| k.as_i64());
        match (key, pair.get(1)) {
            (Some(k), Some(m)) => v.push((k, m)),
            _ => return unsupp("room message key is not an integer"),
        }
    }
    Ok(v)
}

fn chat_fill(out: &mut Map<String, Value>, c: &Value) -> R<()> {
    let ctx = Ctx { host_name: get(c, "host_chat_name"), fold: get(c, "fold") };
    let rs = get(c, "room_settings");
    out.insert("reachable".into(), get(c, "reachable").clone());
    out.insert("group_enabled".into(), json!(truthy(get(rs, "group_enabled"))));
    out.insert("dm_enabled".into(), json!(truthy(get(rs, "dm_enabled"))));
    let new = get(c, "new");
    set_nested(out, "unread", "group", json!(int_or0(get(new, "group"))?));
    set_nested(out, "unread", "dm", json!(int_or0(get(new, "dm"))?));
    out.insert("attention".into(), json!(arr(get(c, "attention_ids")).len()));
    let insertion = sort_rooms(get(c, "rooms"))?; // dict insertion order
    for mid in arr(get(c, "attention_ids")) {
        let channel = match mid.as_i64() {
            Some(id) => insertion.iter().find(|(k, _)| *k == id).map(|(_, m)| get(m, "channel").clone()).unwrap_or(Value::Null),
            None => return unsupp("attention id is not an integer"),
        };
        bump(out, "attention_by", if channel == json!("dm") { "dm" } else { "group" }, 1);
    }
    let mut ordered: Vec<(i64, &Value)> = insertion.iter().filter(|(_, m)| m.is_object()).cloned().collect();
    ordered.sort_by_key(|(k, _)| *k);
    if insertion.iter().any(|(_, m)| !m.is_object()) {
        return unsupp("non-dict room message");
    }
    let ch = |m: &Value| get(m, "channel").clone();
    let mut group = Vec::new();
    for (_, m) in ordered.iter().filter(|(_, m)| ch(m) != json!("dm") && ch(m) != json!("host")) {
        group.push(ctx.message(m, false)?);
    }
    out.insert("group".into(), Value::Array(tail(&group, GROUP_ROWS)));

    // private threads, in the order each thread was first seen
    let mut threads: Vec<((String, String), Vec<&Value>)> = Vec::new();
    for (_, m) in ordered.iter().filter(|(_, m)| ch(m) == json!("dm")) {
        let thread = s_or(get(m, "thread"))?;
        let (a, b) = if s_or(get(m, "from_key"))? == "@host" && thread.contains('|') {
            let (a, b) = thread.split_once('|').unwrap();
            (a.to_string(), b.to_string())
        } else {
            (s_or(get(m, "from_key"))?, s_or(get(m, "to_key"))?)
        };
        if a.is_empty() || b.is_empty() {
            continue;
        }
        let key = if a <= b { (a, b) } else { (b, a) };
        match threads.iter_mut().find(|(k, _)| *k == key) {
            Some((_, v)) => v.push(m),
            None => threads.push((key, vec![m])),
        }
    }
    let display = |key: &str| -> R<String> {
        for (_, m) in &insertion {
            if get(m, "from_key") == &json!(key) && truthy(get(m, "from")) {
                return py_str(get(m, "from"));
            }
        }
        Ok(if key.is_empty() { String::new() } else { ctx.fold(key)?.1 })
    };
    let mut dm: Vec<(i64, Value)> = Vec::new();
    for ((a, b), msgs) in &threads {
        let mut at = i64::MIN;
        for x in msgs {
            at = at.max(int_or0(get(x, "created_at"))?);
        }
        let shaped: R<Vec<Value>> = msgs.iter().map(|x| ctx.message(x, false)).collect();
        dm.push((at, json!({"a": a, "b": b, "a_name": display(a)?, "b_name": display(b)?, "at": at, "messages": tail(&shaped?, THREAD_ROWS)})));
    }
    dm.sort_by(|x, y| y.0.cmp(&x.0));
    out.insert("dm".into(), Value::Array(dm.iter().take(THREADS).map(|(_, v)| v.clone()).collect()));
    set_nested(out, "more", "dm", json!(dm.len().saturating_sub(THREADS)));

    // Host chat: text rows by singer key + picture rows (channel "host") by thread key
    let mut grouped: Vec<(String, Vec<&Value>)> = Vec::new();
    for msg in arr(get(c, "chat_messages")) {
        if !msg.is_object() {
            return Err(E::Raise);
        }
        let sk = get(msg, "singer_key");
        let raw = if truthy(sk) { py_str(sk)? } else { s_or(get(msg, "singer"))? };
        let key = ctx.fold(&raw)?.0;
        if key.is_empty() {
            continue;
        }
        match grouped.iter_mut().find(|(k, _)| *k == key) {
            Some((_, v)) => v.push(msg),
            None => grouped.push((key, vec![msg])),
        }
    }
    let mut media: Vec<(String, Vec<&Value>)> = Vec::new();
    for (_, m) in ordered.iter().filter(|(_, m)| ch(m) == json!("host")) {
        let key = ctx.fold(&s_or(get(m, "thread"))?)?.0;
        match media.iter_mut().find(|(k, _)| *k == key) {
            Some((_, v)) => v.push(m),
            None => media.push((key, vec![m])),
        }
    }
    let read: Vec<i64> = arr(get(c, "media_read")).iter().filter_map(|v| v.as_i64()).collect();
    let mut keys: Vec<String> = grouped.iter().map(|(k, _)| k.clone()).chain(media.iter().map(|(k, _)| k.clone()).filter(|k| !k.is_empty())).collect();
    keys.sort();
    keys.dedup();
    let mut host: Vec<(i64, i64, Value)> = Vec::new();
    for key in &keys {
        let g: &[&Value] = grouped.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_slice()).unwrap_or(&[]);
        let md: &[&Value] = media.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_slice()).unwrap_or(&[]);
        let mut entries: Vec<(i64, i64, i64, &Value, bool)> = Vec::new();
        for m in g {
            entries.push((int_or0(get(m, "created_at"))?, 0, int_or0(get(m, "id"))?, m, true));
        }
        for m in md {
            entries.push((int_or0(get(m, "created_at"))?, 1, int_or0(get(m, "id"))?, m, false));
        }
        entries.sort_by_key(|e| (e.0, e.1, e.2));
        let mut unread = g.iter().filter(|m| get(m, "direction") == &json!("in") && !truthy(get(m, "read"))).count() as i64;
        for m in md {
            let id = match m.get("id") {
                Some(v) => int_strict(v)?,
                None => return Err(E::Raise),
            };
            if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") && !read.contains(&id) {
                unread += 1;
            }
        }
        // _host_chat_singer_name
        let mut name = String::new();
        if let Some(m) = g.iter().rev().find(|m| truthy(get(m, "singer"))) {
            name = py_str(get(m, "singer"))?;
        } else if let Some(m) = md.iter().rev().find(|m| {
            let fk = get(m, "from_key");
            !(fk.is_null() || fk == &json!("") || fk == &json!("@host")) && truthy(get(m, "from"))
        }) {
            name = py_str(get(m, "from"))?;
        }
        let singer = if name.is_empty() { key.clone() } else { name };
        let at = entries.last().map(|e| e.0).unwrap_or(0);
        let shaped: R<Vec<Value>> = entries.iter().map(|e| ctx.message(e.3, e.4)).collect();
        host.push((at, unread, json!({"key": key, "singer": singer, "unread": unread, "at": at, "messages": tail(&shaped?, THREAD_ROWS)})));
    }
    host.sort_by(|x, y| y.0.cmp(&x.0));
    out.insert("host".into(), Value::Array(host.iter().take(THREADS).map(|(_, _, v)| v.clone()).collect()));
    set_nested(out, "more", "host", json!(host.len().saturating_sub(THREADS)));
    set_nested(out, "unread", "host", json!(host.iter().map(|h| h.1).sum::<i64>()));
    Ok(())
}

/// `_shell_chat_summary`: a partly filled dict when Python would have raised midway.
pub fn chat_summary(c: &Value) -> R<Value> {
    let mut out = Map::new();
    out.insert("reachable".into(), Value::Null);
    out.insert("group_enabled".into(), json!(false));
    out.insert("dm_enabled".into(), json!(false));
    out.insert("unread".into(), json!({"group": 0, "dm": 0, "host": 0}));
    out.insert("attention".into(), json!(0));
    out.insert("attention_by".into(), json!({"group": 0, "dm": 0}));
    out.insert("more".into(), json!({"dm": 0, "host": 0}));
    out.insert("group".into(), json!([]));
    out.insert("dm".into(), json!([]));
    out.insert("host".into(), json!([]));
    match chat_fill(&mut out, c) {
        Ok(()) | Err(E::Raise) => Ok(Value::Object(out)),
        Err(e) => Err(e),
    }
}

pub fn chat(req: &Value) -> R<Value> {
    Ok(json!({"type": "chat", "user": user_of(get(req, "settings")), "chat": chat_summary(get(req, "chat"))?, "updated_at": get(req, "updated_at").clone()}))
}

/// One request `{"op": "tick"|"state"|"queue"|"chat", ...}` -> the message, or `{"unsupported": reason}`.
pub fn build(req: &Value) -> Value {
    let r = match get(req, "op").as_str() {
        Some("tick") => tick(req),
        Some("state") => state(req),
        Some("queue") => queue(req),
        Some("chat") => chat(req),
        _ => return json!({"unsupported": "unknown op"}),
    };
    match r {
        Ok(v) => v,
        Err(E::Raise) => json!({"unsupported": "python would raise"}),
        Err(E::Unsupp(why)) => json!({"unsupported": why}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tick_defaults_and_order() {
        let m = build(&json!({"op": "tick", "playback": {"position_seconds": 1.5}, "updated_at": 7}));
        assert_eq!(serde_json::to_string(&m).unwrap(), r#"{"type":"playback_tick","position_seconds":1.5,"duration_seconds":0.0,"remaining_seconds":0.0,"is_playing":false,"updated_at":7}"#);
    }

    #[test]
    fn state_user_falls_back_to_tenant_and_queue_sig_is_last() {
        let m = build(&json!({"op": "state", "settings": {"tenant": "wsk"}, "app_version": "1", "updated_at": 2, "state": {}, "queue_sig": "abc"}));
        let keys: Vec<&String> = m.as_object().unwrap().keys().collect();
        assert_eq!(keys.last().unwrap().as_str(), "queue_sig");
        assert_eq!(m["user"], "wsk");
        assert_eq!(m["bg_index"], -1);
        let none = build(&json!({"op": "state", "settings": {"user": null, "tenant": "x"}, "state": {}, "queue_sig": null}));
        assert!(none["user"].is_null() && none.get("queue_sig").is_none());
    }

    #[test]
    fn queue_sig_matches_a_known_python_value() {
        // python: hashlib.sha1(json.dumps([["s1","Ann",False,False,[["local:e1",False]]]],separators=(",",":"),ensure_ascii=False).encode()).hexdigest()[:16]
        let q = json!([{"singer_id": "s1", "name": "Ann", "songs": [{"is_dict": true, "entry_id": "local:e1", "artist": "A", "title": "T"}]}]);
        assert_eq!(queue_sig(&q).unwrap(), "1ba07b5e0ed809bd");
        // unicode names are hashed as raw UTF-8 (ensure_ascii=False)
        let u = json!([{"singer_id": "s", "name": "Cé 名", "songs": []}]);
        assert_eq!(queue_sig(&u).unwrap(), "42a7edc061a398bd");
    }

    #[test]
    fn queue_limits_and_filters() {
        let singers: Vec<Value> = (0..205).map(|i| if i == 3 { Value::Null } else { json!({"singer_id": format!("s{i}"), "name": "n", "songs": []}) }).collect();
        let rows = queue_rows(&Value::Array(singers)).unwrap();
        assert_eq!(rows.as_array().unwrap().len(), 199, "200 taken, one non-dict dropped");
        let songs: Vec<Value> = (0..30).map(|i| json!({"is_dict": true, "entry_id": format!("e{i}"), "artist": "", "title": ""})).collect();
        let rows = queue_rows(&json!([{"singer_id": "s", "songs": songs}])).unwrap();
        assert_eq!(rows[0]["songs"].as_array().unwrap().len(), 25);
    }

    #[test]
    fn gif_regexes() {
        assert!(giphy_preview("https://media.giphy.com/x") && giphy_preview("https://media3.giphy.com/x") && giphy_preview("https://i.giphy.com/x"));
        assert!(!giphy_preview("http://media.giphy.com/x") && !giphy_preview("https://mediax.giphy.com/x") && !giphy_preview("https://media.giphy.com"));
        assert!(gif_id_ok("a-b_9") && gif_id_ok("abc\n") && !gif_id_ok("abc\n\n") && !gif_id_ok("") && !gif_id_ok(&"a".repeat(65)) && gif_id_ok(&"a".repeat(64)));
    }

    #[test]
    fn int_or0_follows_python() {
        assert_eq!(int_or0(&json!(" 12 ")).unwrap(), 12);
        assert_eq!(int_or0(&json!("")).unwrap(), 0);
        assert_eq!(int_or0(&json!(2.9)).unwrap(), 2);
        assert_eq!(int_or0(&json!("x")), Err(E::Raise));
        assert_eq!(int_or0(&json!([1])), Err(E::Raise));
        assert!(matches!(int_or0(&json!("1_0")), Err(E::Unsupp(_))));
    }

    #[test]
    fn waitlist_half_empties_when_a_row_raises() {
        let w = json!({"rows": [{"request_id": "x"}], "removed": [{"removed_at": 5, "row": {"request_id": 1, "singer": "A"}}]});
        let out = waitlist(&w).unwrap();
        assert_eq!(out["waiting"], json!([]));
        assert_eq!(out["removed"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn strip_uses_python_whitespace() {
        assert_eq!(int_or0(&json!("\u{1c}5\u{3000}")).unwrap(), 5);
        assert_eq!(int_or0(&json!("\u{2003} -7 \u{85}")).unwrap(), -7);
    }

    #[test]
    fn removed_at_floats_follow_python() {
        assert_eq!(float_or0_safe(&json!("inf")), f64::INFINITY);
        assert_eq!(float_or0_safe(&json!(" -1.5 ")), -1.5);
        assert_eq!(float_or0_safe(&json!("nan")), 0.0);
        assert_eq!(float_or0_safe(&json!("bad")), 0.0);
        assert_eq!(float_or0_safe(&json!(true)), 1.0);
        assert_eq!(float_or0_safe(&json!(null)), 0.0);
    }

    #[test]
    fn a_host_message_without_a_pipe_in_its_thread_uses_from_and_to() {
        let c = json!({"rooms": [[1, {"id": 1, "channel": "dm", "from_key": "@host", "thread": "", "to_key": "bob", "created_at": 5, "host": true}]], "fold": {"bob": ["bob", "Bob"], "@host": ["@host", "@Host"]}});
        let out = chat_summary(&c).unwrap();
        assert_eq!(out["dm"][0]["a"], "@host");
        assert_eq!(out["dm"][0]["b"], "bob");
    }

    #[test]
    fn display_name_falls_back_to_the_title_cased_key() {
        let c = json!({"rooms": [[1, {"id": 1, "channel": "dm", "from_key": "ann", "to_key": "bob", "created_at": 5}]], "fold": {"ann": ["ann", "Ann"], "bob": ["bob", "Bob"]}});
        let out = chat_summary(&c).unwrap();
        assert_eq!((out["dm"][0]["a_name"].clone(), out["dm"][0]["b_name"].clone()), (json!("Ann"), json!("Bob")));
    }

    #[test]
    fn a_message_that_makes_python_raise_leaves_the_summary_partly_filled() {
        let c = json!({"reachable": true, "room_settings": {"group_enabled": 1}, "new": {"group": "x"}});
        let out = chat_summary(&c).unwrap();
        assert_eq!((out["reachable"].clone(), out["group_enabled"].clone(), out["dm_enabled"].clone()), (json!(true), json!(true), json!(false)));
        assert_eq!(out["unread"], json!({"group": 0, "dm": 0, "host": 0}));
        assert_eq!(out["group"], json!([]));
    }

    fn fold_of(names: &[&str]) -> Value {
        let mut m = Map::new();
        for n in names {
            let title: String = n.split(' ').map(|w| { let mut c = w.chars(); c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default() }).collect::<Vec<_>>().join(" ");
            m.insert(n.to_string(), json!([n.to_lowercase(), title]));
        }
        Value::Object(m)
    }

    fn chat_with(rooms: Vec<Value>, texts: Vec<Value>, names: &[&str]) -> Value {
        let pairs: Vec<Value> = rooms.into_iter().map(|m| json!([m["id"].clone(), m])).collect();
        chat_summary(&json!({"rooms": pairs, "chat_messages": texts, "fold": fold_of(names)})).unwrap()
    }

    #[test]
    fn python_truthiness_and_str_of_plain_values() {
        assert!(!truthy(&json!([])) && !truthy(&json!({})) && truthy(&json!([0])) && truthy(&json!({"a": 1})));
        assert_eq!(s_or(&json!([])).unwrap(), "");
        assert_eq!(s_or(&json!({})).unwrap(), "");
        assert_eq!(py_str(&json!(null)).unwrap(), "None");
        assert_eq!((py_str(&json!(true)).unwrap(), py_str(&json!(false)).unwrap()), ("True".to_string(), "False".to_string()));
    }

    #[test]
    fn int_edge_cases() {
        assert!(matches!(int_or0(&json!("\u{e9}5")), Err(E::Unsupp(_))));
        assert_eq!(int_or0(&json!("+")), Err(E::Raise));
        assert_eq!(int_or0(&json!("-")), Err(E::Raise));
        assert_eq!(int_or0(&json!("+4")).unwrap(), 4);
        assert_eq!(int_strict(&json!(null)), Err(E::Raise));
        assert_eq!(int_strict(&json!("")), Err(E::Raise));
        assert_eq!(int_strict(&json!([])), Err(E::Raise));
        assert_eq!((int_strict(&json!(0)).unwrap(), int_strict(&json!(false)).unwrap(), int_strict(&json!("7")).unwrap()), (0, 0, 7));
    }

    #[test]
    fn a_non_dict_song_never_counts_as_skipped() {
        let a = json!([{"singer_id": "s", "songs": [{"is_dict": false, "entry_id": "", "artist": "", "title": "", "skipped": true}]}]);
        let b = json!([{"singer_id": "s", "songs": [{"is_dict": false, "entry_id": "", "artist": "", "title": "", "skipped": false}]}]);
        assert_eq!(queue_sig(&a).unwrap(), queue_sig(&b).unwrap());
        assert_eq!(queue_rows(&a).unwrap()[0]["songs"][0]["skipped"], false);
        let d = json!([{"singer_id": "s", "songs": [{"is_dict": true, "entry_id": "e", "artist": "", "title": "", "skipped": true}]}]);
        assert_eq!(queue_rows(&d).unwrap()[0]["songs"][0]["skipped"], true);
    }

    #[test]
    fn message_sender_falls_back_to_from_key_when_from_is_falsy() {
        let out = chat_with(vec![json!({"id": 1, "channel": "group", "from": "", "from_key": "ann", "message": "x"}), json!({"id": 2, "channel": "group", "from": 5, "from_key": "k"})], vec![], &[]);
        assert_eq!((out["group"][0]["from"].clone(), out["group"][1]["from"].clone()), (json!("ann"), json!("5")));
    }

    #[test]
    fn private_threads_use_the_pipe_only_for_host_messages_and_need_both_sides() {
        let out = chat_with(vec![json!({"id": 1, "channel": "dm", "from_key": "a", "to_key": "b", "thread": "x|y", "created_at": 1}),
                                 json!({"id": 2, "channel": "dm", "from_key": "ann", "to_key": "", "created_at": 2})], vec![], &["a", "b", "ann"]);
        assert_eq!(out["dm"].as_array().unwrap().len(), 1);
        assert_eq!((out["dm"][0]["a"].clone(), out["dm"][0]["b"].clone()), (json!("a"), json!("b")));
    }

    #[test]
    fn host_chat_threads_key_on_singer_key_then_singer() {
        let t = json!({"id": 1, "singer_key": "KeyName", "singer": "other", "direction": "in", "created_at": 3});
        let out = chat_with(vec![], vec![t], &["KeyName", "other"]);
        assert_eq!(out["host"][0]["key"], "keyname");
        assert_eq!(out["host"][0]["singer"], "other");
    }

    #[test]
    fn picture_rows_without_a_thread_make_no_thread() {
        let out = chat_with(vec![json!({"id": 1, "channel": "host", "thread": "", "from_key": "ann", "from": "Ann", "created_at": 1})], vec![], &[]);
        assert_eq!(out["host"], json!([]));
    }

    #[test]
    fn a_thread_lists_text_before_pictures_at_the_same_time_then_by_id() {
        let text = json!({"id": 50, "singer_key": "ann", "singer": "Ann", "direction": "in", "created_at": 10, "message": "t"});
        let media_late = json!({"id": 9, "channel": "host", "thread": "ann", "from_key": "ann", "from": "Ann", "created_at": 10, "message": "m9"});
        let media_early = json!({"id": 3, "channel": "host", "thread": "ann", "from_key": "ann", "from": "Ann", "created_at": 10, "message": "m3"});
        let out = chat_with(vec![media_late, media_early], vec![text.clone(), json!({"id": 51, "singer_key": "ann", "singer": "Ann", "direction": "in", "created_at": 10, "message": "t2"})], &["ann", "Ann"]);
        let order: Vec<String> = out["host"][0]["messages"].as_array().unwrap().iter().map(|m| m["text"].as_str().unwrap().to_string()).collect();
        assert_eq!(order, vec!["t", "t2", "m3", "m9"]);
    }

    #[test]
    fn host_unread_ignores_host_messages_and_names_come_from_real_singers() {
        let host_pic = json!({"id": 1, "channel": "host", "thread": "ann", "from_key": "@host", "from": "Hosty", "created_at": 1});
        let null_key = json!({"id": 2, "channel": "host", "thread": "ann", "from_key": null, "from": "Nobody", "created_at": 2});
        let out = chat_with(vec![host_pic, null_key], vec![], &["ann"]);
        assert_eq!(out["host"][0]["unread"], 1, "the @host picture is not unread, the null-key one is");
        assert_eq!(out["host"][0]["singer"], "ann", "neither @host nor a null from_key names the singer");
        let real = json!({"id": 3, "channel": "host", "thread": "ann", "from_key": "ann", "from": "Ann Real", "created_at": 3});
        let out = chat_with(vec![real], vec![], &["ann"]);
        assert_eq!(out["host"][0]["singer"], "Ann Real");
    }

    #[test]
    fn host_threads_are_newest_first_bounded_and_counted() {
        let rows: Vec<Value> = (1..=12).map(|i| json!({"id": i, "channel": "host", "thread": format!("s{i:02}"), "from_key": "x", "from": "X", "created_at": i * 10})).collect();
        let names: Vec<String> = (1..=12).map(|i| format!("s{i:02}")).collect();
        let refs: Vec<&str> = names.iter().map(|s| s.as_str()).collect();
        let out = chat_with(rows, vec![], &refs);
        assert_eq!(out["host"].as_array().unwrap().len(), 10);
        assert_eq!(out["host"][0]["at"], 120);
        assert_eq!(out["host"][9]["at"], 30);
        assert_eq!(out["more"]["host"], 2);
        assert_eq!(out["unread"]["host"], 12, "unread counts every thread, also the ones cut from the list");
    }

    #[test]
    fn the_chat_summary_always_sets_reachable_first() {
        // chat_fill's first statement copies `reachable`, so the default in chat_summary can never be seen
        let out = chat_summary(&json!({"reachable": false})).unwrap();
        assert_eq!(out["reachable"], false);
    }

    #[test]
    fn pictures_at_the_same_time_are_ordered_by_message_id_not_room_key() {
        let a = json!({"id": 9, "channel": "host", "thread": "ann", "from_key": "ann", "from": "Ann", "created_at": 10, "message": "id9"});
        let b = json!({"id": 3, "channel": "host", "thread": "ann", "from_key": "ann", "from": "Ann", "created_at": 10, "message": "id3"});
        // room keys 1 and 2 are the reverse of the message ids
        let c = json!({"rooms": [[1, a], [2, b]], "chat_messages": [], "fold": fold_of(&["ann"])});
        let out = chat_summary(&c).unwrap();
        let order: Vec<&str> = out["host"][0]["messages"].as_array().unwrap().iter().map(|m| m["text"].as_str().unwrap()).collect();
        assert_eq!(order, vec!["id3", "id9"]);
    }
}
