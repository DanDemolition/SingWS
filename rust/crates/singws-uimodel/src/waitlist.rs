//! The host's "Waiting for add" page as a toolkit-free view model: which requests belong on it, the rows' fields and three-line text, their order, and the
//! sections. Equal to `KaraokeApp._waiting_for_add_row`, `_waiting_for_add_row_text`, `_waiting_for_add_order_key`, `_waiting_for_add_sections`,
//! `_pending_acceptance_rows`, `_is_waiting_for_add_request` / `_remote_request_is_non_actionable`, `_waiting_for_add_status_kind` / `_label`,
//! `_waiting_for_add_reason_text`, the version / duration / time helpers and `_waiting_for_add_request_id`.
//!
//! Two questions are NOT answered here because they need live app state: "is this singer on their first turn tonight" and "does this request already match an
//! accepted queue entry". The caller passes both as inputs (`first_time_names`, `accepted_ids`), exactly where the app consults `_is_first_time_waitlist_singer` and
//! `_request_matches_accepted_queue_item`. Anything Python would do that this module cannot reproduce exactly (non-ASCII numerals, exotic ISO-8601 forms, Unicode
//! case tables that differ between releases, `str()` of a container) is declined (`Err("decline: ...")`); Python exceptions the app does not catch are `Err("raise: ...")`.

use crate::history::{clock_text, localtime};
use serde_json::{Map, Value, json};
use singws_dbw::pyval::{R, decline, get, py_float_from_str, py_lower, py_str, py_str_or_empty, py_strip, truthy};
use std::collections::HashSet;

/// `a or b or ... or last`, owned
fn or_chain(operands: &[&Value], last: Value) -> Value {
    operands.iter().find(|v| truthy(v)).map(|v| (*v).clone()).unwrap_or(last)
}

/// `v or 0`
fn or_zero(v: &Value) -> Value {
    or_chain(&[v], Value::from(0))
}

fn raise<T>(why: &str) -> R<T> {
    Err(format!("raise: {why}"))
}

// ---- Python scalars ------------------------------------------------------------------------------------------------------------------

/// `value in (None, "")`
fn none_or_empty(v: &Value) -> bool {
    v.is_null() || v.as_str() == Some("")
}

/// `float(v)`: `Ok(Some)` when Python gets a number, `Ok(None)` when it raises (ValueError/TypeError), declined where the text is not reproducible.
fn py_float(v: &Value) -> R<Option<f64>> {
    match v {
        Value::Bool(b) => Ok(Some(if *b { 1.0 } else { 0.0 })),
        Value::Number(n) => Ok(n.as_f64()),
        Value::String(s) => py_float_from_str(s),
        _ => Ok(None),
    }
}

/// `int(v)`: `Ok(None)` when Python raises. Integers beyond i128 and non-ASCII numerals are declined.
pub(crate) fn py_int(v: &Value) -> R<Option<i128>> {
    match v {
        Value::Bool(b) => Ok(Some(*b as i128)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(Some(i as i128))
            } else if let Some(u) = n.as_u64() {
                Ok(Some(u as i128))
            } else {
                let f = n.as_f64().unwrap_or(f64::NAN);
                if !f.is_finite() {
                    return Ok(None);
                }
                if f.abs() >= 9.0e18 {
                    return decline("integer beyond 64 bits"); // an integer literal that large reaches us as a rounded float
                }
                Ok(Some(f.trunc() as i128))
            }
        }
        Value::String(s) => {
            let t = py_strip(s);
            if !t.is_ascii() {
                return decline("non-ASCII numeral text");
            }
            let (neg, digits) = match t.strip_prefix('-') {
                Some(r) => (true, r),
                None => (false, t.strip_prefix('+').unwrap_or(t)),
            };
            let b = digits.as_bytes();
            if b.is_empty() {
                return Ok(None);
            }
            let mut clean = String::new();
            for (i, &c) in b.iter().enumerate() {
                if c == b'_' {
                    if !(i > 0 && i + 1 < b.len() && b[i - 1].is_ascii_digit() && b[i + 1].is_ascii_digit()) {
                        return Ok(None);
                    }
                } else if c.is_ascii_digit() {
                    clean.push(c as char);
                } else {
                    return Ok(None);
                }
            }
            match clean.parse::<i128>() {
                Ok(n) => Ok(Some(if neg { -n } else { n })),
                Err(_) => decline("integer beyond 128 bits"),
            }
        }
        _ => Ok(None),
    }
}

/// `int(float(v))` for the app's timestamp reads: `Ok(None)` on any exception.
fn int_of_float(v: &Value) -> R<Option<i128>> {
    match py_float(v)? {
        Some(f) if f.is_finite() => {
            if f.abs() > 1e30 {
                return decline("huge float to int");
            }
            Ok(Some(f.trunc() as i128))
        }
        _ => Ok(None),
    }
}

/// `str(v).strip().isdigit()` for the values the request time reads (ASCII digits only; a non-ASCII digit is declined)
fn str_is_digits(v: &Value) -> R<bool> {
    let s = py_str(v)?;
    let t = py_strip(&s);
    if t.chars().any(|c| !c.is_ascii() && c.is_numeric()) {
        return decline("non-ASCII digit");
    }
    Ok(!t.is_empty() && t.bytes().all(|b| b.is_ascii_digit()))
}

// ---- ISO-8601 (datetime.fromisoformat) -----------------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct Iso {
    pub year: i32,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    pub micro: u32,
    /// UTC offset in seconds when the text carried one
    pub offset: Option<i64>,
}

fn days_in_month(y: i32, m: u32) -> u32 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ => {
            if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 { 29 } else { 28 }
        }
    }
}

fn digits(s: &[u8]) -> Option<u32> {
    if s.is_empty() || !s.iter().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some(s.iter().fold(0u32, |a, b| a * 10 + (b - b'0') as u32))
}

/// the time-of-day part: `HH`, `HH:MM`, `HH:MM:SS[.f]`, or the basic forms `HHMM`, `HHMMSS[.f]`; returns (h, m, s, micro)
fn parse_clock(t: &[u8]) -> Option<(u32, u32, u32, u32)> {
    let (main, frac) = match t.iter().position(|&b| b == b'.' || b == b',') {
        Some(i) => (&t[..i], Some(&t[i + 1..])),
        None => (t, None),
    };
    let (h, m, s) = if main.contains(&b':') {
        let parts: Vec<&[u8]> = main.split(|&b| b == b':').collect();
        if parts.iter().any(|p| p.len() != 2) || parts.len() > 3 {
            return None;
        }
        (digits(parts[0])?, parts.get(1).map(|p| digits(p)).unwrap_or(Some(0))?, parts.get(2).map(|p| digits(p)).unwrap_or(Some(0))?)
    } else {
        match main.len() {
            2 => (digits(main)?, 0, 0),
            4 => (digits(&main[..2])?, digits(&main[2..])?, 0),
            6 => (digits(&main[..2])?, digits(&main[2..4])?, digits(&main[4..])?),
            _ => return None,
        }
    };
    let micro = match frac {
        None => 0,
        Some(f) => {
            // a fraction needs seconds before it
            if f.is_empty() || !f.iter().all(|b| b.is_ascii_digit()) || (main.contains(&b':') && main.split(|&b| b == b':').count() != 3) || (!main.contains(&b':') && main.len() != 6) {
                return None;
            }
            let mut v = 0u32;
            for i in 0..6 {
                v = v * 10 + f.get(i).map(|b| (b - b'0') as u32).unwrap_or(0);
            }
            v
        }
    };
    Some((h, m, s, micro))
}

fn parse_offset(t: &[u8]) -> Option<i64> {
    // sign already removed; HH, HH:MM, HHMM, HH:MM:SS, HHMMSS (+ optional fraction on the seconds, which only matters to the value)
    // unlike a time of day the fields are not range-checked ("+05:60" is +06:00), only the total must stay inside a day
    let (h, m, s, _us) = parse_clock(t)?;
    let total = (h * 3600 + m * 60 + s) as i64;
    if total >= 86400 {
        return None;
    }
    Some(total)
}

/// `datetime.fromisoformat(text)` (Python 3.11+), as far as the app can see it: a date (`YYYY-MM-DD` or `YYYYMMDD`), optionally one separator character and a clock,
/// optionally `Z` or a UTC offset. `Ok(None)` is a ValueError. Week dates and the `24:00` form are declined.
pub fn from_isoformat(text: &str) -> R<Option<Iso>> {
    let b = text.as_bytes();
    let extended = b.len() >= 10 && b[4] == b'-' && b[7] == b'-';
    let (date_len, ymd) = if extended {
        match (digits(&b[..4]), digits(&b[5..7]), digits(&b[8..10])) {
            (Some(y), Some(m), Some(d)) => (10, Some((y, m, d))),
            _ => (10, None),
        }
    } else if b.len() >= 8 && digits(&b[..8]).is_some() {
        (8, Some((digits(&b[..4]).unwrap(), digits(&b[4..6]).unwrap(), digits(&b[6..8]).unwrap())))
    } else {
        (0, None)
    };
    let Some((y, m, d)) = ymd else {
        // week dates ("2026-W43-1", "2026W431") are valid Python input this module does not parse
        if text.is_ascii() && b.len() >= 7 && digits(&b[..4]).is_some() && (b[4] == b'W' || (b[4] == b'-' && b[5] == b'W')) {
            return decline("ISO week date");
        }
        return Ok(None);
    };
    if y < 1 || !(1..=12).contains(&m) || d < 1 || d > days_in_month(y as i32, m) {
        return Ok(None);
    }
    if !text.is_char_boundary(date_len) {
        return Ok(None);
    }
    let mut it = Iso { year: y as i32, month: m, day: d, hour: 0, minute: 0, second: 0, micro: 0, offset: None };
    if b.len() == date_len {
        return Ok(Some(it));
    }
    // one separator character of any width, then the clock
    let rest = &text[date_len..];
    let sep_len = rest.chars().next().map(|c| c.len_utf8()).unwrap_or(0);
    if rest.starts_with(['+', '-']) {
        return decline("an offset sign as the date/time separator"); // CPython finds the offset first and reads the rest oddly
    }
    let tail = &rest[sep_len..];
    if tail.is_empty() {
        return Ok(None);
    }
    let tb = tail.as_bytes();
    let (clock, tz) = match tb.iter().position(|&c| c == b'+' || c == b'-') {
        Some(i) => (&tb[..i], Some(&tb[i..])),
        None => (tb, None),
    };
    if tz.is_some() && matches!(clock.last(), Some(b'.') | Some(b',')) {
        return decline("empty fraction before an offset");
    }
    if tz.is_some() && clock.len() >= 3 && clock.iter().all(|b| b.is_ascii_digit()) && !matches!(clock.len(), 4 | 6) {
        return decline("CPython reads odd digit counts before an offset leniently"); // "T200+05:00" is 20:00
    }
    let Some((h, mi, s, us)) = parse_clock(clock) else { return Ok(None) };
    if h == 24 {
        return decline("24:00");
    }
    if h > 23 || mi > 59 || s > 59 {
        return Ok(None);
    }
    it.hour = h;
    it.minute = mi;
    it.second = s;
    it.micro = us;
    if let Some(z) = tz {
        let sign = if z[0] == b'-' { -1 } else { 1 };
        let Some(o) = parse_offset(&z[1..]) else { return Ok(None) };
        it.offset = Some(sign * o);
    }
    Ok(Some(it))
}

fn iso_clock(it: &Iso) -> String {
    clock_text(&crate::history::Local { year: it.year, month0: it.month - 1, day: it.day, hour: it.hour, minute: it.minute })
}

/// `datetime.timestamp()` of a parsed text (aware: exact; naive: the machine's local zone, via mktime)
fn iso_timestamp(it: &Iso) -> R<f64> {
    let y = it.year as i64 - (it.month <= 2) as i64;
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (it.month as i64 + 9) % 12;
    let doy = (153 * mp + 2) / 5 + it.day as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    let clock = it.hour as i64 * 3600 + it.minute as i64 * 60 + it.second as i64;
    if let Some(off) = it.offset {
        let secs = days * 86400 + clock - off;
        return Ok(((secs as i128 * 1_000_000 + it.micro as i128) as f64) / 1e6);
    }
    unsafe extern "C" {
        fn tzset();
    }
    // SAFETY: tzset/mktime get valid pointers to locals
    let secs = unsafe {
        tzset();
        let mut tm: libc::tm = std::mem::zeroed();
        tm.tm_year = it.year - 1900;
        tm.tm_mon = it.month as i32 - 1;
        tm.tm_mday = it.day as i32;
        tm.tm_hour = it.hour as i32;
        tm.tm_min = it.minute as i32;
        tm.tm_sec = it.second as i32;
        tm.tm_isdst = -1;
        libc::mktime(&mut tm) as i64
    };
    Ok(secs as f64 + it.micro as f64 / 1e6)
}

fn z_to_offset(text: &str) -> String {
    text.replace('Z', "+00:00")
}

/// `datetime.fromtimestamp(stamp).strftime("%I:%M %p").lstrip("0")`; `None` where Python raises
fn stamp_clock(stamp: i128) -> Option<String> {
    let s = i64::try_from(stamp).ok()?;
    localtime(s).map(|l| clock_text(&l))
}

// ---- the helpers ---------------------------------------------------------------------------------------------------------------------

const REQUEST_TIME_KEYS: [&str; 8] = ["request_time", "requested_at_client", "requested_at", "created_at", "submitted_at", "received_at", "ts", "time"];
const SERVER_TIME_KEYS: [&str; 4] = ["received_at_server", "server_received_at", "received_at", "ts"];

/// `_waiting_for_add_request_time_text`
pub fn request_time_text(req: &Value) -> R<String> {
    for key in REQUEST_TIME_KEYS {
        let value = get(req, key);
        if none_or_empty(value) {
            continue;
        }
        let numeric = matches!(value, Value::Bool(_) | Value::Number(_)) || str_is_digits(value)?;
        if numeric {
            if let Some(stamp) = int_of_float(value)? {
                if stamp > 100000 {
                    if let Some(t) = stamp_clock(stamp) {
                        return Ok(t);
                    }
                } else {
                    continue;
                }
            }
        }
        let text = py_strip(&py_str(value)?).to_string();
        if text.is_empty() {
            continue;
        }
        return match from_isoformat(&z_to_offset(&text))? {
            Some(it) => Ok(iso_clock(&it)),
            None => Ok(text),
        };
    }
    Ok("Unknown time".into())
}

/// `_waiting_for_add_server_time_text`
pub fn server_time_text(req: &Value) -> R<String> {
    for key in SERVER_TIME_KEYS {
        let value = get(req, key);
        if none_or_empty(value) {
            continue;
        }
        let text = py_strip(&py_str(value)?).to_string();
        if text.is_empty() {
            continue;
        }
        if let Some(it) = from_isoformat(&z_to_offset(&text))? {
            return Ok(iso_clock(&it));
        }
        if let Some(stamp) = int_of_float(&Value::String(text.clone()))? {
            if stamp > 100000 {
                if let Some(t) = stamp_clock(stamp) {
                    return Ok(t);
                }
            }
        }
        return Ok(text);
    }
    Ok("Unknown time".into())
}

/// `_format_waiting_duration`
fn format_duration(v: &Value) -> R<String> {
    if none_or_empty(v) {
        return Ok(String::new());
    }
    match int_of_float(v)? {
        Some(secs) => {
            if secs <= 0 {
                Ok(String::new())
            } else {
                Ok(format!("{}:{:02}", secs / 60, secs % 60))
            }
        }
        None => Ok(py_strip(&py_str_or_empty(v)?).to_string()),
    }
}

fn dict_truthy(v: &Value) -> bool {
    v.is_object() && truthy(v)
}

/// `_waiting_for_add_duration_text`
pub fn duration_text(req: &Value) -> R<String> {
    let replacement = get(req, "replacement_track");
    for source in [replacement, req] {
        if !source.is_object() {
            continue;
        }
        for key in ["duration_secs", "duration", "length_secs", "len_secs"] {
            let t = format_duration(get(source, key))?;
            if !t.is_empty() {
                return Ok(t);
            }
        }
    }
    Ok("Length unknown".into())
}

fn py_upper_ascii(s: &str) -> R<String> {
    if !s.is_ascii() {
        return decline("non-ASCII upper()");
    }
    Ok(s.to_ascii_uppercase())
}

/// `os.path.splitext(p)[1]`
fn splitext_ext(p: &str) -> &str {
    let base_start = p.rfind('/').map(|i| i + 1).unwrap_or(0);
    let base = &p[base_start..];
    let lead = base.len() - base.trim_start_matches('.').len();
    match base[lead..].rfind('.') {
        Some(i) => &base[lead + i..],
        None => "",
    }
}

/// `_waiting_for_add_version_text`
pub fn version_text(req: &Value) -> R<String> {
    let replacement = get(req, "replacement_track");
    let rep_ok = dict_truthy(replacement);
    let source = if rep_ok { "Replacement" } else { "Requested" };
    let track = if rep_ok { replacement } else { req };
    let mut pieces: Vec<String> = Vec::new();
    for key in [
        "selected_version", "selected_brand", "selected_disc_id", "selected_source", "disc_id", "discid", "songid", "song_id", "brand", "source", "version",
    ] {
        let value = py_strip(&py_str_or_empty(get(track, key))?).to_string();
        if !value.is_empty() {
            let low = py_lower(&value)?;
            if low != "unknown" && low != "none" && !pieces.contains(&value) {
                pieces.push(value);
            }
        }
    }
    let path = py_strip(&py_str_or_empty(get(track, "path"))?).to_string();
    if !path.is_empty() {
        let ext = py_upper_ascii(splitext_ext(&path).trim_matches('.'))?;
        if !ext.is_empty() && !pieces.contains(&ext) {
            pieces.push(ext);
        }
    }
    if pieces.is_empty() {
        return Ok(source.into());
    }
    Ok(format!("{source}: {}", pieces.iter().take(3).cloned().collect::<Vec<_>>().join(" / ")))
}

/// `_waiting_for_add_request_id`
pub fn request_id(req: &Value) -> R<i64> {
    let v = or_chain(&[get(req, "request_id"), get(req, "id")], Value::from(0));
    match py_int(&v)? {
        Some(n) => i64::try_from(n).or_else(|_| decline("request id beyond 64 bits")),
        None => Ok(0),
    }
}

/// `_waiting_for_add_status_label`
pub fn status_label(kind: &str) -> R<&'static str> {
    let k = py_lower(py_strip(kind))?;
    Ok(match k.as_str() {
        "pending_acceptance" => "Pending Acceptance",
        "waitlist" => "Waitlisted",
        "needs_review" => "Needs Review",
        "sung" => "Sung",
        "removed" => "Removed",
        _ => "Active",
    })
}

fn state_of(req: &Value) -> R<String> {
    py_lower(py_strip(&py_str_or_empty(get(req, "state"))?))
}

/// `_waiting_for_add_status_kind`
pub fn status_kind(req: &Value) -> R<&'static str> {
    let state = state_of(req)?;
    // both are read in one try: if either raises, both are 0
    let comp = int_of_float(&or_zero(get(req, "completed_at")))?;
    let rem = int_of_float(&or_zero(get(req, "removed_at")))?;
    let (completed_at, removed_at) = match (comp, rem) {
        (Some(c), Some(r)) => (c, r),
        _ => (0, 0),
    };
    let has_err = !py_strip(&py_str_or_empty(&or_chain(&[get(req, "last_error"), get(req, "attention_reason")], Value::from("")))?).is_empty();
    if state == "removed" || state == "skipped" || removed_at > 0 {
        return Ok("removed");
    }
    if state == "completed" || state == "sung" || completed_at > 0 {
        return Ok("sung");
    }
    if state == "failed" || state == "failed_needs_review" || has_err {
        return Ok("waitlist");
    }
    if state == "waiting" || !py_strip(&py_str_or_empty(get(req, "pending_reason"))?).is_empty() {
        return Ok("waitlist");
    }
    Ok("active")
}

/// `_waiting_for_add_reason_text`
pub fn reason_text(req: &Value) -> R<String> {
    let reason = py_strip(&py_str_or_empty(&or_chain(&[get(req, "pending_reason"), get(req, "attention_reason")], Value::from("")))?).to_string();
    let status = py_strip(&py_str_or_empty(get(req, "pending_status"))?).to_string();
    if !status.is_empty() {
        return Ok(status);
    }
    if reason.starts_with("unsupported_format") {
        return Ok("Waiting because song format is unsupported".into());
    }
    let state = state_of(req)?;
    if state == "failed_needs_review" {
        return Ok("Needs Review".into());
    }
    if state == "failed" {
        return Ok("Waiting because song failed to sync".into());
    }
    if state == "waiting" {
        return Ok("Waiting for host to add".into());
    }
    Ok(match reason.as_str() {
        "failed_needs_review" => "Needs Review",
        "host_not_accepting" => "Waiting because requests are closed",
        "rotation_full" => "Waiting because rotation is full",
        "sync_failed" | "auto_accept_failed" => "Waiting because song failed to sync",
        "queue_insert_failed" => "Waiting because add confirmation was not received",
        "no_local_library_match" => "Waiting because song is not in the local library",
        "previous_auto_match_failure" => "Waiting because song failed to match earlier",
        "cdg_missing_mp3" => "Waiting because CDG audio is missing",
        "invalid_zip_format" => "Waiting because the ZIP format is invalid",
        _ => "Waiting for host to add",
    }
    .into())
}

/// `_remote_request_is_non_actionable`
pub fn non_actionable(req: &Value) -> R<bool> {
    if !req.is_object() {
        return Ok(false);
    }
    let state = py_lower(py_strip(&py_str_or_empty(&or_chain(&[get(req, "state"), get(req, "status")], Value::from("")))?))?;
    if matches!(state.as_str(), "waiting" | "failed" | "failed_needs_review") {
        return Ok(false);
    }
    if state == "accepted" {
        if truthy(get(req, "sent")) || truthy(get(req, "delivered")) {
            return Ok(true);
        }
        let sync = py_lower(py_strip(&py_str_or_empty(get(req, "desktop_sync_status"))?))?;
        return Ok(!matches!(sync.as_str(), "" | "pending" | "pending_desktop_sync"));
    }
    if matches!(state.as_str(), "active" | "delivered" | "completed" | "sung" | "removed" | "skipped") {
        return Ok(true);
    }
    if truthy(get(req, "sent")) || truthy(get(req, "delivered")) {
        return Ok(true);
    }
    for key in ["added_at", "queued_at", "completed_at", "removed_at"] {
        let v = get(req, key);
        if let Some(f) = py_float(&or_zero(v))? {
            if f > 0.0 {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

/// `_is_waiting_for_add_request`
pub fn is_waiting_for_add(req: &Value) -> R<bool> {
    if !req.is_object() {
        return Ok(false);
    }
    let state = state_of(req)?;
    if non_actionable(req)? {
        return Ok(false);
    }
    if matches!(state.as_str(), "waiting" | "failed" | "failed_needs_review") {
        return Ok(true);
    }
    let any = or_chain(&[get(req, "pending_reason"), get(req, "last_error"), get(req, "attention_reason")], Value::from(""));
    Ok(!py_strip(&py_str_or_empty(&any)?).is_empty())
}

// ---- rows ----------------------------------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub request_id: i64,
    pub singer: String,
    pub artist: String,
    pub title: String,
    pub version: String,
    pub duration: String,
    pub first_time_singer: bool,
    pub request_time: String,
    pub server_received_time: String,
    pub queue_position: Value,
    pub status_kind: String,
    pub status_label: String,
    pub selectable: bool,
    pub text: String,
    order: (f64, i64),
    removed_at: f64,
}

fn or_default(s: String, d: &str) -> String {
    let t = py_strip(&s).to_string();
    if t.is_empty() { d.into() } else { t }
}

/// `_waiting_for_add_order_key(row)`: (time, request id); unparseable or missing times sort last
fn order_key(req: &Value, rid: i64) -> R<(f64, i64)> {
    let mut raw: Option<&Value> = None;
    for key in ["received_at_server", "request_time", "requested_at_client", "requested_at", "created_at", "submitted_at", "received_at", "ts", "time"] {
        let v = get(req, key);
        if !none_or_empty(v) {
            raw = Some(v);
            break;
        }
    }
    let mut order = f64::INFINITY;
    if let Some(v) = raw {
        match py_float(v)? {
            Some(f) => {
                if f.is_nan() {
                    return decline("NaN order value");
                }
                order = f;
            }
            None => {
                if !matches!(v, Value::Array(_) | Value::Object(_)) {
                    let text = py_strip(&py_str(v)?).to_string();
                    if let Some(it) = from_isoformat(&z_to_offset(&text))? {
                        order = iso_timestamp(&it)?;
                    }
                }
            }
        }
    }
    Ok((order, rid))
}

fn cmp_key(a: &(f64, i64), b: &(f64, i64)) -> std::cmp::Ordering {
    a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal).then(a.1.cmp(&b.1))
}

/// `_waiting_for_add_row(req, kind, selectable=...)` plus `_waiting_for_add_row_text(row)`; `first_time` is the answer to `_is_first_time_waitlist_singer(singer)`
pub fn build_row(req: &Value, kind: &str, selectable: bool, first_time: bool) -> R<Row> {
    let replacement = get(req, "replacement_track");
    let rep = replacement.is_object().then_some(replacement);
    let track_truthy = rep.is_some_and(truthy);
    let pick = |key: &str| -> R<String> {
        let a = if track_truthy { get(rep.unwrap(), key).clone() } else { Value::from("") };
        let v = or_chain(&[&a, get(req, key)], Value::from(""));
        Ok(py_strip(&py_str_or_empty(&v)?).to_string())
    };
    let artist = or_default(pick("artist")?, "Unknown artist");
    let title = or_default(pick("title")?, "Unknown song");
    let rid = request_id(req)?;
    let singer_raw = py_str_or_empty(get(req, "singer"))?;
    let singer = or_default(singer_raw.clone(), "Unknown singer");
    let needs_review = state_of(req)? == "failed_needs_review";
    let kind_s = if truthy(&Value::from(kind)) { kind.to_string() } else { "active".into() };
    let (status_kind_s, label) = if needs_review { ("needs_review".to_string(), status_label("needs_review")?) } else { (kind_s.clone(), status_label(kind)?) };
    let queue_position = req.get("queue_position").cloned().unwrap_or(Value::Null);
    let version = version_text(req)?;
    let duration = duration_text(req)?;
    let request_time = request_time_text(req)?;
    let server_time = server_time_text(req)?;
    let first = first_time;
    let mut status = label.to_string();
    if first {
        status = format!("{status} - First turn");
    }
    let mut queue_text = String::new();
    if !none_or_empty(&queue_position) {
        if let Some(n) = py_int(&queue_position)? {
            if n > 0 {
                queue_text = format!("    Queue: #{n}");
            }
        }
    }
    let text = format!(
        "Singer: {singer}    Status: {status}{queue_text}\nSong: {title}    Artist: {artist}\nVersion: {version}    Length: {duration}    Requested: {request_time}    Server: {server_time}"
    );
    let removed_at = match py_float(&or_zero(get(req, "removed_at")))? {
        Some(f) => f,
        None => f64::NAN, // float() raised: only matters (and then raises) when the removed section sorts
    };
    Ok(Row {
        request_id: rid,
        singer,
        artist,
        title,
        version,
        duration,
        first_time_singer: first,
        request_time,
        server_received_time: server_time,
        queue_position,
        status_kind: status_kind_s,
        status_label: label.to_string(),
        selectable,
        text,
        order: order_key(req, rid)?,
        removed_at,
    })
}

pub fn row_json(r: &Row) -> Value {
    json!({
        "request_id": r.request_id, "singer": r.singer, "artist": r.artist, "title": r.title, "version": r.version, "duration": r.duration,
        "first_time_singer": r.first_time_singer, "request_time": r.request_time, "server_received_time": r.server_received_time,
        "queue_position": r.queue_position, "status_kind": r.status_kind, "status_label": r.status_label, "selectable": r.selectable, "text": r.text,
    })
}

// ---- sections ------------------------------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct Section {
    pub key: &'static str,
    pub title: &'static str,
    pub helper: Option<&'static str>,
    pub rows: Vec<Row>,
}

pub struct Context<'a> {
    pub first_time_names: &'a HashSet<String>,
    pub accepted_ids: &'a HashSet<i64>,
    /// the accepted ids the Pending Acceptance rows are judged against: the app takes the accepted set MINUS the deferred ids (a deferred add is "accepted" for the
    /// waitlist but is exactly what the pending rows list)
    pub pending_accepted_ids: &'a HashSet<i64>,
    pub local_ids: &'a HashSet<i64>,
}

fn first_time_of(ctx: &Context, req: &Value) -> R<bool> {
    Ok(ctx.first_time_names.contains(&py_str_or_empty(get(req, "singer"))?))
}

/// `_pending_acceptance_rows` for the deferred-adds list; the whole section is empty when the setting is off (the caller decides that)
pub fn pending_acceptance_rows(pending: &[Value], ctx: &Context) -> R<Vec<Row>> {
    let mut rows = Vec::new();
    for (i, item) in pending.iter().enumerate() {
        if !item.is_object() {
            continue;
        }
        let rid = py_int(&or_zero(get(item, "request_id")))?.unwrap_or(0);
        if rid <= 0 || ctx.local_ids.contains(&(rid as i64)) {
            continue;
        }
        let meta = get(item, "remote_meta");
        let mut req: Map<String, Value> = match meta {
            Value::Object(o) => o.clone(),
            m if !truthy(m) => Map::new(),
            _ => return decline("remote_meta is not an object"),
        };
        req.entry("request_id").or_insert(Value::from(rid as i64));
        for k in ["singer", "artist", "title"] {
            if !req.get(k).is_some_and(truthy) {
                req.insert(k.into(), get(item, k).clone());
            }
        }
        let rt = or_chain(&[get(item, "request_time")], get(item, "deferred_at").clone());
        req.entry("request_time").or_insert(rt);
        req.insert("queue_position".into(), Value::from(i as i64 + 1));
        let track = get(item, "track");
        if track.is_object() {
            for k in ["artist", "title"] {
                if !req.get(k).is_some_and(truthy) {
                    req.insert(k.into(), get(track, k).clone());
                }
            }
            for k in [
                "selected_version", "selected_brand", "selected_disc_id", "selected_source", "disc_id", "discid", "songid", "song_id", "brand", "source", "version",
                "duration_secs", "duration", "path",
            ] {
                if !req.get(k).is_some_and(truthy) && truthy(get(track, k)) {
                    req.insert(k.into(), get(track, k).clone());
                }
            }
        }
        let req = Value::Object(req);
        if ctx.pending_accepted_ids.contains(&request_id(&req)?) {
            continue;
        }
        rows.push(build_row(&req, "pending_acceptance", false, first_time_of(ctx, &req)?)?);
    }
    rows.sort_by(|a, b| cmp_key(&a.order, &b.order));
    Ok(rows)
}

/// `_waiting_for_add_sections`. `waiting` is the `_waiting_for_add_requests` mapping (request id text -> request), `removed` the removed-tonight mapping.
pub fn sections(pending_acceptance: Option<&[Value]>, waiting: &Value, show_removed: bool, removed: &Value, ctx: &Context) -> R<Vec<Section>> {
    let mut out = Vec::new();
    if let Some(p) = pending_acceptance {
        let rows = pending_acceptance_rows(p, ctx)?;
        if !rows.is_empty() {
            out.push(Section { key: "pending_acceptance", title: "Pending Acceptance", helper: Some("These requests will automatically be added when the current song finishes."), rows });
        }
    }
    let mut wait_rows = Vec::new();
    if let Some(map) = waiting.as_object() {
        for (rid, req) in map {
            let Some(rid_int) = py_int(&Value::String(rid.clone()))? else { continue };
            if rid_int <= 0 || ctx.local_ids.contains(&(rid_int as i64)) || ctx.accepted_ids.contains(&request_id(req)?) {
                continue;
            }
            wait_rows.push(build_row(req, "waitlist", true, first_time_of(ctx, req)?)?);
        }
    }
    wait_rows.sort_by(|a, b| cmp_key(&a.order, &b.order));
    if !wait_rows.is_empty() {
        out.push(Section { key: "waitlist", title: "Waitlist", helper: None, rows: wait_rows });
    }
    if show_removed {
        let mut rows = Vec::new();
        if let Some(map) = removed.as_object() {
            for req in map.values() {
                if req.is_object() {
                    rows.push(build_row(req, "removed", true, first_time_of(ctx, req)?)?);
                }
            }
        }
        if rows.iter().any(|r| r.removed_at.is_nan()) {
            return raise("float() of removed_at");
        }
        rows.sort_by(|a, b| b.removed_at.partial_cmp(&a.removed_at).unwrap_or(std::cmp::Ordering::Equal));
        if !rows.is_empty() {
            out.push(Section { key: "removed", title: "Removed Tonight", helper: None, rows });
        }
    }
    Ok(out)
}

/// The sections for a `waitlist_snapshot.json` the app wrote: the raw requests plus the app's own answers (`local_ids`, `accepted_ids`, `pending_accepted_ids`,
/// `first_time_names`). `pending_acceptance` is null while the app's "deferred adds" setting is off (no such section); a missing `pending_accepted_ids` means the same
/// ids as `accepted_ids`.
pub fn sections_from_snapshot(snapshot: &Value) -> R<Vec<Section>> {
    if !snapshot.is_object() {
        return decline("the snapshot is not an object");
    }
    let id_set = |key: &str| -> HashSet<i64> { get(snapshot, key).as_array().map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default() };
    let names: HashSet<String> = get(snapshot, "first_time_names").as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default();
    let accepted = id_set("accepted_ids");
    let pending_accepted = if get(snapshot, "pending_accepted_ids").is_array() { id_set("pending_accepted_ids") } else { accepted.clone() };
    let local = id_set("local_ids");
    let ctx = Context { first_time_names: &names, accepted_ids: &accepted, pending_accepted_ids: &pending_accepted, local_ids: &local };
    let pending = get(snapshot, "pending_acceptance").as_array();
    sections(pending.map(|v| v.as_slice()), get(snapshot, "waiting"), get(snapshot, "show_removed").as_bool().unwrap_or(false), get(snapshot, "removed"), &ctx)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx<'a>(f: &'a HashSet<String>, a: &'a HashSet<i64>, l: &'a HashSet<i64>) -> Context<'a> {
        Context { first_time_names: f, accepted_ids: a, pending_accepted_ids: a, local_ids: l }
    }

    #[test]
    fn row_text_is_three_lines_with_the_queue_and_first_turn_markers() {
        let req = json!({"request_id": 7, "singer": "Ann", "artist": "Queen", "title": "Radio Ga Ga", "brand": "SC", "path": "/x/a.zip", "duration_secs": 245,
                         "requested_at": "2026-10-26T20:05:00", "received_at_server": "2026-10-26T20:05:03Z", "queue_position": 3, "state": "waiting"});
        let r = build_row(&req, "waitlist", true, true).unwrap();
        assert_eq!(
            r.text,
            concat!(
                "Singer: Ann    Status: Waitlisted - First turn    Queue: #3\n",
                "Song: Radio Ga Ga    Artist: Queen\n",
                "Version: Requested: SC / ZIP    Length: 4:05    Requested: 8:05 PM    Server: 8:05 PM"
            )
        );
        assert_eq!(r.status_kind, "waitlist");
    }

    #[test]
    fn a_replacement_track_wins_and_failed_needs_review_overrides_the_kind() {
        let req = json!({"request_id": "9", "singer": "", "artist": "Old", "title": "Old T", "state": "failed_needs_review",
                         "replacement_track": {"artist": "New", "title": "", "disc_id": "D1", "duration": "3:45"}});
        let r = build_row(&req, "waitlist", true, false).unwrap();
        assert_eq!((r.singer.as_str(), r.artist.as_str(), r.title.as_str()), ("Unknown singer", "New", "Old T"));
        assert_eq!(r.version, "Replacement: D1");
        assert_eq!(r.duration, "3:45", "text that is not a number is shown as typed");
        assert_eq!((r.status_kind.as_str(), r.status_label.as_str()), ("needs_review", "Needs Review"));
        assert_eq!(r.request_id, 9);
    }

    #[test]
    fn times_read_epoch_numbers_and_iso_text_and_fall_back_to_the_raw_text() {
        assert_eq!(server_time_text(&json!({"ts": "not a time"})).unwrap(), "not a time");
        assert_eq!(server_time_text(&json!({})).unwrap(), "Unknown time");
        assert_eq!(server_time_text(&json!({"received_at": "2026-01-02 13:07:09+02:00"})).unwrap(), "1:07 PM");
        assert_eq!(request_time_text(&json!({"request_time": 5})).unwrap(), "Unknown time", "small numbers are skipped");
        assert_eq!(request_time_text(&json!({"request_time": "20261026"})).unwrap().is_empty(), false);
        assert!(request_time_text(&json!({"ts": "2026-W43-1"})).is_err(), "week dates are declined, never guessed");
    }

    #[test]
    fn iso_parsing_covers_the_accepted_shapes() {
        for ok in ["2026-10-26", "20261026", "2026-10-26T20", "2026-10-26 20:05", "2026-10-26T20:05:07.123456", "2026-10-26T200507", "2026-10-26T20:05+05:30", "2026-10-26T20:05:07,5-0800"] {
            assert!(from_isoformat(ok).unwrap().is_some(), "{ok}");
        }
        for bad in ["", "2026-13-01", "2026-02-30", "2026-10-26T25:00", "2026-10-26T20:61", "2026-10-26T", "x", "2026-10-26T20:5", "0000-01-01"] {
            assert!(from_isoformat(bad).unwrap().is_none(), "{bad}");
        }
        assert!(from_isoformat("2024-02-29").unwrap().is_some());
        assert!(from_isoformat("2025-02-29").unwrap().is_none());
    }

    #[test]
    fn rows_order_by_time_then_id_and_unparseable_times_go_last() {
        let waiting = json!({
            "3": {"request_id": 3, "singer": "C", "request_time": "2026-10-26T21:00:00Z"},
            "1": {"request_id": 1, "singer": "A", "request_time": 1792000000},
            "2": {"request_id": 2, "singer": "B"},
            "4": {"request_id": 4, "singer": "D"},
        });
        let (f, a, l) = (HashSet::new(), HashSet::new(), HashSet::new());
        let s = sections(None, &waiting, false, &Value::Null, &ctx(&f, &a, &l)).unwrap();
        let ids: Vec<_> = s[0].rows.iter().map(|r| r.request_id).collect();
        assert_eq!(ids, [1, 3, 2, 4]);
    }

    #[test]
    fn accepted_local_and_non_positive_ids_stay_off_the_waitlist() {
        let waiting = json!({"1": {"request_id": 1}, "2": {"request_id": 2}, "3": {"request_id": 3}, "0": {"request_id": 5}, "x": {"request_id": 6}, "-4": {"request_id": 7}});
        let f = HashSet::new();
        let a: HashSet<i64> = [2].into();
        let l: HashSet<i64> = [1].into();
        let s = sections(None, &waiting, false, &Value::Null, &ctx(&f, &a, &l)).unwrap();
        assert_eq!(s[0].rows.iter().map(|r| r.request_id).collect::<Vec<_>>(), [3]);
    }

    #[test]
    fn the_removed_section_is_newest_first_and_only_when_shown() {
        let removed = json!({"a": {"request_id": 1, "removed_at": 100}, "b": {"request_id": 2, "removed_at": 300}, "c": {"request_id": 3, "removed_at": 300}});
        let (f, a, l) = (HashSet::new(), HashSet::new(), HashSet::new());
        let c = ctx(&f, &a, &l);
        assert!(sections(None, &Value::Null, false, &removed, &c).unwrap().is_empty());
        let s = sections(None, &Value::Null, true, &removed, &c).unwrap();
        assert_eq!(s[0].rows.iter().map(|r| r.request_id).collect::<Vec<_>>(), [2, 3, 1]);
        assert_eq!(s[0].rows[0].status_label, "Removed");
    }

    #[test]
    fn intake_rules_match_the_app_for_the_state_table() {
        assert!(!non_actionable(&json!({"state": "waiting", "sent": true})).unwrap());
        assert!(non_actionable(&json!({"state": "accepted", "sent": 1})).unwrap());
        assert!(!non_actionable(&json!({"state": "accepted", "desktop_sync_status": "pending"})).unwrap());
        assert!(non_actionable(&json!({"state": "accepted", "desktop_sync_status": "done"})).unwrap());
        assert!(non_actionable(&json!({"state": "", "added_at": 5})).unwrap());
        assert!(is_waiting_for_add(&json!({"state": "failed"})).unwrap());
        assert!(is_waiting_for_add(&json!({"pending_reason": "rotation_full"})).unwrap());
        assert!(!is_waiting_for_add(&json!({"state": "delivered", "pending_reason": "x"})).unwrap());
        assert_eq!(reason_text(&json!({"pending_reason": "rotation_full"})).unwrap(), "Waiting because rotation is full");
        assert_eq!(reason_text(&json!({"pending_reason": "unsupported_format_xyz"})).unwrap(), "Waiting because song format is unsupported");
        assert_eq!(status_kind(&json!({"state": "sung"})).unwrap(), "sung");
        assert_eq!(status_kind(&json!({"completed_at": 5})).unwrap(), "sung");
        assert_eq!(status_kind(&json!({"last_error": "x"})).unwrap(), "waitlist");
        assert_eq!(status_kind(&json!({})).unwrap(), "active");
    }

    #[test]
    fn pending_acceptance_rows_merge_the_deferred_item_into_the_request() {
        let pending = vec![
            json!({"request_id": 11, "singer": "Zed", "artist": "A", "title": "T", "track": {"disc_id": "D9", "path": "/a/b.mp3"}, "deferred_at": 1792000000}),
            json!({"request_id": 12, "remote_meta": {"singer": "Yu"}, "singer": "ignored", "track": {"title": "Tt"}}),
            json!("junk"),
            json!({"request_id": 0}),
        ];
        let (f, a, l) = (HashSet::new(), HashSet::new(), HashSet::new());
        let rows = pending_acceptance_rows(&pending, &ctx(&f, &a, &l)).unwrap();
        assert_eq!(rows.len(), 2);
        let r = rows.iter().find(|r| r.request_id == 11).unwrap();
        assert_eq!(r.version, "Requested: D9 / MP3");
        assert_eq!(r.queue_position, json!(1));
        assert!(!r.selectable);
        assert_eq!(rows.iter().find(|r| r.request_id == 12).unwrap().singer, "Yu");
    }

    #[test]
    fn the_first_usable_duration_key_in_the_apps_order_wins() {
        assert_eq!(duration_text(&json!({"duration": 100, "duration_secs": 200})).unwrap(), "3:20");
        assert_eq!(duration_text(&json!({"duration": 100, "replacement_track": {"len_secs": 61}})).unwrap(), "1:01", "the replacement track is read first");
    }

    #[test]
    fn the_file_type_is_not_listed_twice() {
        assert_eq!(version_text(&json!({"brand": "MP3", "path": "/a/b.mp3"})).unwrap(), "Requested: MP3");
        assert_eq!(version_text(&json!({"brand": "SC", "path": "/a/b.mp3"})).unwrap(), "Requested: SC / MP3");
    }

    #[test]
    fn accepted_rows_wait_for_the_desktop_unless_it_has_synced() {
        assert!(!non_actionable(&json!({"state": "accepted", "desktop_sync_status": "pending_desktop_sync"})).unwrap());
        assert!(non_actionable(&json!({"state": "accepted", "desktop_sync_status": "synced"})).unwrap());
    }

    #[test]
    fn leap_years_follow_the_century_rule() {
        assert!(from_isoformat("1900-02-29").unwrap().is_none());
        assert!(from_isoformat("2000-02-29").unwrap().is_some());
        assert!(from_isoformat("2100-02-29").unwrap().is_none());
    }

    #[test]
    fn offsets_move_the_instant_and_fractions_keep_six_digits() {
        let it = from_isoformat("1970-01-02T00:00:00+01:00").unwrap().unwrap();
        assert_eq!(iso_timestamp(&it).unwrap(), 86400.0 - 3600.0);
        let it = from_isoformat("1970-01-01T00:00:01.1234567Z".replace('Z', "+00:00").as_str()).unwrap().unwrap();
        assert_eq!(it.micro, 123456);
        assert_eq!(iso_timestamp(&it).unwrap(), 1.123456);
    }

    #[test]
    fn request_ids_read_numbers_in_text_like_python_int() {
        assert_eq!(request_id(&json!({"request_id": "1_0"})).unwrap(), 10);
        assert_eq!(request_id(&json!({"request_id": "_10"})).unwrap(), 0);
        assert_eq!(request_id(&json!({"request_id": " +7 "})).unwrap(), 7);
        assert_eq!(request_id(&json!({"id": 3.9})).unwrap(), 3);
    }

    #[test]
    fn splitext_follows_os_path() {
        assert_eq!(splitext_ext("/a/b.c/d"), "");
        assert_eq!(splitext_ext("/a/.hidden"), "");
        assert_eq!(splitext_ext("x.tar.gz"), ".gz");
        assert_eq!(splitext_ext("/a/..x.mp3"), ".mp3");
        assert_eq!(splitext_ext("file."), ".");
    }

    #[test]
    fn a_snapshot_draws_waiting_pending_and_removed_sections() {
        let snap = json!({
            "version": 1,
            "pending_acceptance": [{"request_id": 9, "singer": "Cy", "artist": "ABBA", "title": "SOS", "deferred_at": 1792000500}],
            "waiting": {"5": {"request_id": 5, "singer": "Ann", "artist": "Queen", "title": "Radio", "request_time": 1792000000}, "6": {"request_id": 6, "singer": "Bo", "artist": "A", "title": "B"}},
            "show_removed": true,
            "removed": {"3": {"request_id": 3, "singer": "Di", "artist": "X", "title": "Y", "removed_at": 300}},
            "local_ids": [6], "accepted_ids": [], "pending_accepted_ids": [], "first_time_names": ["Ann"]
        });
        let secs = sections_from_snapshot(&snap).unwrap();
        let keys: Vec<_> = secs.iter().map(|s| (s.key, s.rows.len())).collect();
        assert_eq!(keys, [("pending_acceptance", 1), ("waitlist", 1), ("removed", 1)]);
        assert_eq!(secs[1].rows[0].request_id, 5);
        assert!(secs[1].rows[0].first_time_singer);
        assert!(!secs[0].rows[0].first_time_singer);
    }

    #[test]
    fn accepted_waiting_requests_are_hidden_but_a_deferred_one_still_shows_as_pending() {
        let both = json!({"pending_acceptance": [{"request_id": 7, "singer": "Ann", "artist": "A", "title": "T"}], "waiting": {"7": {"request_id": 7, "singer": "Ann", "artist": "A", "title": "T"}},
            "removed": {}, "local_ids": [], "accepted_ids": [7], "pending_accepted_ids": [], "first_time_names": []});
        let keys: Vec<_> = sections_from_snapshot(&both).unwrap().iter().map(|s| s.key).collect();
        assert_eq!(keys, ["pending_acceptance"]);
        // without `pending_accepted_ids` the pending row is judged against `accepted_ids` as well
        let mut legacy = both.clone();
        legacy.as_object_mut().unwrap().remove("pending_accepted_ids");
        assert!(sections_from_snapshot(&legacy).unwrap().is_empty());
        // no deferred-adds list (setting off): no pending section at all
        let mut off = both.clone();
        off["pending_acceptance"] = Value::Null;
        assert!(sections_from_snapshot(&off).unwrap().is_empty());
        assert!(sections_from_snapshot(&json!([1])).is_err());
    }
}
