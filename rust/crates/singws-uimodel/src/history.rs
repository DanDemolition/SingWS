//! Panel 2 of the Rust-owned UI: SINGER HISTORY, read-only. Reproduces, from `singer_history.json`, what the PyQt Singer History page shows:
//! the store normalisation (`_normalize_singer_history_store`, singers part), the singer directory (`SingerHistoryDirectoryBuildWorker`), one
//! singer's song rows (`SingerHistorySongsBuildWorker` + `_filter_singer_history_songs`), the header summary and the "last sang" labels.
//! Anything Python would do that this module cannot match exactly is DECLINED (an `Err("decline: ...")`), never approximated.

use crate::casefold_table::CASEFOLD_EXCEPTIONS;
use serde_json::Value;
use singws_dbw::pyval::{R, decline, get, is_py_space, py_lower, py_or, py_str, py_str_or_empty, py_strip, truthy};

// ---- Python int() ------------------------------------------------------------------------------------------------------------------

/// What `int(value or 0)` (or `or 100`) does: a number, or Python raising (`Raise`), or something this port will not guess (`Decline`).
pub enum IntOutcome {
    Value(i64),
    Raise,
    Decline(String),
}

fn parse_py_int_text(s: &str) -> IntOutcome {
    if !s.is_ascii() {
        return IntOutcome::Decline("non-ASCII integer text".into());
    }
    let t = s.trim_matches(|c| matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c'));
    let (neg, digits) = match t.as_bytes().first() {
        Some(b'+') => (false, &t[1..]),
        Some(b'-') => (true, &t[1..]),
        _ => (false, t),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') || digits.contains("__") {
        return IntOutcome::Raise;
    }
    let clean: String = digits.chars().filter(|c| *c != '_').collect();
    if !clean.chars().all(|c| c.is_ascii_digit()) {
        return IntOutcome::Raise;
    }
    match clean.parse::<i64>() {
        Ok(v) => IntOutcome::Value(if neg { -v } else { v }),
        Err(_) => IntOutcome::Decline("integer outside 64 bits".into()),
    }
}

pub fn int_or(v: &Value, default: i64) -> IntOutcome {
    if !truthy(v) {
        return IntOutcome::Value(default);
    }
    match v {
        Value::Bool(_) => IntOutcome::Value(1),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                IntOutcome::Value(i)
            } else if n.is_u64() {
                IntOutcome::Decline("integer outside 64 bits".into())
            } else {
                match n.as_f64() {
                    Some(f) if f.is_finite() => {
                        let t = f.trunc();
                        if t >= 9_223_372_036_854_775_808.0 || t < -9_223_372_036_854_775_808.0 {
                            IntOutcome::Decline("integer outside 64 bits".into())
                        } else {
                            IntOutcome::Value(t as i64)
                        }
                    }
                    _ => IntOutcome::Raise,
                }
            }
        }
        Value::String(s) => parse_py_int_text(s),
        _ => IntOutcome::Raise,
    }
}

/// `try: int(x or 0) except Exception: 0`
fn int_try(v: &Value) -> R<i64> {
    match int_or(v, 0) {
        IntOutcome::Value(i) => Ok(i),
        IntOutcome::Raise => Ok(0),
        IntOutcome::Decline(w) => decline(w),
    }
}

/// `int(x or default)` with no handler: Python would raise out of the whole normalisation, so this is a decline.
fn int_strict(v: &Value, default: i64) -> R<i64> {
    match int_or(v, default) {
        IntOutcome::Value(i) => Ok(i),
        IntOutcome::Raise => decline("Python would raise on this value"),
        IntOutcome::Decline(w) => decline(w),
    }
}

// ---- disc brands (module-level helpers of the app) ---------------------------------------------------------------------------------

const BRAND_ALIASES: &[(&str, &[&str])] = &[
    ("KV", &["KV", "KARAOKE VERSION", "KARAOKEVERSION", "KARAOKE-VERSION", "KARAOKE_VERSION"]),
    ("ZM", &["ZM", "ZOOM", "ZDL", "ZOOM DL", "ZOOMDL", "ZOOM KARAOKE"]),
    ("SC", &["SC", "SOUNDCHOICE", "SOUND CHOICE", "SOUND-CHOICE", "SOUND_CHOICE"]),
    ("CC", &["CC"]),
    ("CB", &["CB", "CHARTBUSTER", "CHART BUSTER", "CHART-BUSTER", "CHART_BUSTER"]),
    ("PT", &["PT", "PARTY TYME", "PARTYTYME", "PARTY-TYME", "PARTY_TYME", "PARTY TIME", "PARTYTIME"]),
    ("SF", &["SF", "SUNFLY", "SUN FLY", "SUN-FLY", "SUN_FLY"]),
    ("SBI", &["SBI", "SBI KARAOKE"]),
    ("KARAFUN", &["KARAFUN", "KARA FUN", "KARA-FUN", "KARA_FUN"]),
    ("ME", &["ME", "MR ENTERTAINER", "MRENTERTAINER", "MR-ENTERTAINER"]),
];

/// `re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())` for ASCII text.
fn brand_key(s: &str) -> String {
    s.to_ascii_uppercase().chars().filter(|c| c.is_ascii_uppercase() || c.is_ascii_digit()).collect()
}

/// alias keys per canonical brand, in the app's dict order (canonical first, then its aliases; duplicates collapse into a set)
fn alias_keys(canonical: &str, aliases: &[&str]) -> Vec<String> {
    let mut keys: Vec<String> = Vec::new();
    for a in std::iter::once(&canonical).chain(aliases.iter()) {
        let k = brand_key(a);
        if !k.is_empty() && !keys.contains(&k) {
            keys.push(k);
        }
    }
    keys
}

/// `karaoke_brand_match_info(value)["canonical"]`
fn brand_canonical(value: &str) -> String {
    let key = brand_key(value.trim());
    if key.is_empty() {
        return String::new();
    }
    // exact alias key (a later canonical brand's alias overwrites an earlier one in the app's dict)
    let mut exact: Option<&str> = None;
    for (canonical, aliases) in BRAND_ALIASES {
        if alias_keys(canonical, aliases).contains(&key) {
            exact = Some(canonical);
        }
    }
    if let Some(c) = exact {
        return c.to_string();
    }
    // prefix candidates: max by (confidence, alias length, canonical, alias key) like Python's tuple max
    let mut best: Option<(u8, usize, String, String)> = None;
    for (canonical, aliases) in BRAND_ALIASES {
        for ak in alias_keys(canonical, aliases) {
            if ak.is_empty() || !key.starts_with(&ak) {
                continue;
            }
            let remainder = &key[ak.len()..];
            let long_alias = ak.len() >= 4;
            let coded_suffix = remainder.chars().next().is_some_and(|c| c.is_ascii_digit());
            let separator_free_text = long_alias && !remainder.is_empty();
            if remainder.is_empty() || coded_suffix || separator_free_text {
                let confidence = if coded_suffix { 94 } else if separator_free_text { 90 } else { 88 };
                let cand = (confidence, ak.len(), canonical.to_string(), ak.clone());
                if best.as_ref().is_none_or(|b| cand > *b) {
                    best = Some(cand);
                }
            }
        }
    }
    best.map(|b| b.2).unwrap_or_default()
}

/// `_disc_brand_value_segments(raw)`: the raw text plus its pieces after splitting on " - " and bracket/slash characters.
fn brand_segments(raw: &str) -> R<Vec<String>> {
    let raw = py_strip(raw);
    if raw.is_empty() {
        return Ok(Vec::new());
    }
    let mut values = vec![raw.to_string()];
    // `stem = Path(raw).stem if any(ch in raw for ch in ("/", "\\")) else raw` (POSIX path semantics: only "/" separates; "." parts and empty parts vanish;
    // the stem drops the last ".suffix" unless the dot is the first or last character)
    let stem: String = if raw.contains(['/', '\\']) {
        let name = raw.split('/').filter(|p| !p.is_empty() && *p != ".").next_back().unwrap_or("");
        match name.rfind('.') {
            Some(i) if i > 0 && i < name.len() - 1 => name[..i].to_string(),
            _ => name.to_string(),
        }
    } else {
        raw.to_string()
    };
    let raw = stem.as_str();
    // re.split(r"\s+-\s+|[|/\\()\[\]{}]+", stem)
    let chars: Vec<char> = raw.chars().collect();
    let mut parts: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut i = 0;
    let is_sep = |c: char| matches!(c, '|' | '/' | '\\' | '(' | ')' | '[' | ']' | '{' | '}');
    while i < chars.len() {
        let c = chars[i];
        if is_sep(c) {
            parts.push(std::mem::take(&mut cur));
            while i < chars.len() && is_sep(chars[i]) {
                i += 1;
            }
            continue;
        }
        if is_py_space(c) {
            // whitespace run followed by '-' and whitespace run: a " - " separator
            let mut j = i;
            while j < chars.len() && is_py_space(chars[j]) {
                j += 1;
            }
            if j < chars.len() && chars[j] == '-' {
                let mut k = j + 1;
                if k < chars.len() && is_py_space(chars[k]) {
                    while k < chars.len() && is_py_space(chars[k]) {
                        k += 1;
                    }
                    parts.push(std::mem::take(&mut cur));
                    i = k;
                    continue;
                }
            }
        }
        cur.push(c);
        i += 1;
    }
    parts.push(cur);
    for part in parts {
        let p = py_strip(&part).to_string();
        if !p.is_empty() && !values.contains(&p) {
            values.push(p);
        }
    }
    Ok(values)
}

/// `canonical_disc_brand(value, allow_unknown=True)`
fn canonical_disc_brand(value: &Value) -> R<String> {
    let raw_full = py_str_or_empty(value)?;
    let raw = py_strip(&raw_full);
    if raw.is_empty() {
        return Ok(String::new());
    }
    if !raw.is_ascii() {
        return decline("non-ASCII brand text");
    }
    for seg in brand_segments(raw)? {
        let c = brand_canonical(&seg);
        if !c.is_empty() {
            return Ok(c);
        }
    }
    Ok(raw.to_ascii_uppercase())
}

/// `normalize_disc_priority(value, max_items=10)` joined by ", "
fn disc_priority_text(value: &Value) -> R<String> {
    let parts: Vec<Value> = match value {
        Value::String(s) => s.split(',').map(|p| Value::String(p.to_string())).collect(),
        Value::Array(a) => a.clone(),
        _ => Vec::new(),
    };
    let mut out: Vec<String> = Vec::new();
    for p in parts {
        let c = canonical_disc_brand(&p)?;
        if c.is_empty() || out.contains(&c) {
            continue;
        }
        out.push(c);
        if out.len() >= 10 {
            break;
        }
    }
    Ok(out.join(", "))
}

// ---- normalised store ----------------------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct Song {
    pub key: String,
    pub artist: String,
    pub title: String,
    pub songid: String,
    pub disc_id: String,
    pub song_type: String,
    pub duet_display: String,
    pub path: String,
    pub provider: String,
    pub provider_track_id: String,
    pub provider_url: String,
    pub local_reference_path: String,
    pub authorization_requirement: String,
    pub availability_status: String,
    pub last_key: i64,
    pub last_tempo_percent: i64,
    pub play_count: i64,
    pub first_performed_at: i64,
    pub last_performed_at: i64,
    pub updated_at: i64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Singer {
    pub key: String,
    pub name: String,
    pub preferred_disc_priority: String,
    pub updated_at: i64,
    pub last_seen_at: i64,
    pub total_performances: i64,
    pub unique_song_count: i64,
    pub songs: Vec<Song>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Store {
    pub singers: Vec<Singer>,
}

/// `fold(value)` of `_normalize_history_song_key`
fn fold_key_part(value: &Value) -> R<String> {
    let text = py_lower(py_strip(&py_str_or_empty(value)?))?;
    let text: String = text.chars().filter(|c| !matches!(c, '\'' | '\u{2019}' | '`' | '\u{b4}')).collect();
    // re.sub(r"[^a-z0-9]+", " ", text) then collapse whitespace and strip
    let mut out = String::new();
    let mut pending_space = false;
    for c in text.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            if pending_space && !out.is_empty() {
                out.push(' ');
            }
            pending_space = false;
            out.push(c);
        } else {
            pending_space = true;
        }
    }
    Ok(out)
}

/// `_normalize_history_song_key(artist, title, songid, path, provider, provider_track_id)`; arguments are raw JSON values (Null when absent)
pub fn song_key(artist: &Value, title: &Value, songid: &Value, path: &Value, provider: &Value, provider_track_id: &Value) -> R<String> {
    let provider_key = fold_key_part(provider)?;
    let provider_track_key = fold_key_part(provider_track_id)?;
    let artist_key = fold_key_part(artist)?;
    let title_key = fold_key_part(title)?;
    if !provider_key.is_empty() && provider_key != "local" && !provider_track_key.is_empty() {
        return Ok([provider_key, provider_track_key, artist_key, title_key].join("|"));
    }
    if !artist_key.is_empty() || !title_key.is_empty() {
        return Ok([artist_key, title_key].join("|"));
    }
    fold_key_part(py_or(&[path, songid]))
}

fn text_field(v: &Value) -> R<String> {
    Ok(py_strip(&py_str_or_empty(v)?).to_string())
}

fn merge(existing: &Song, incoming: &Song) -> Song {
    let mut merged = existing.clone();
    let newer = if incoming.updated_at >= existing.updated_at { incoming } else { existing };
    macro_rules! text {
        ($f:ident) => {
            if !newer.$f.is_empty() {
                merged.$f = newer.$f.clone();
            } else if merged.$f.is_empty() && !incoming.$f.is_empty() {
                merged.$f = incoming.$f.clone();
            }
        };
    }
    text!(artist);
    text!(title);
    text!(songid);
    text!(disc_id);
    text!(song_type);
    text!(duet_display);
    text!(path);
    text!(provider);
    text!(provider_track_id);
    text!(provider_url);
    text!(local_reference_path);
    text!(authorization_requirement);
    text!(availability_status);
    // integers are never None or "", so the newer record's values always win
    merged.last_key = newer.last_key;
    merged.last_tempo_percent = newer.last_tempo_percent;
    merged.play_count = 0.max(existing.play_count).max(incoming.play_count);
    merged.first_performed_at = if existing.first_performed_at != 0 && incoming.first_performed_at != 0 {
        existing.first_performed_at.min(incoming.first_performed_at)
    } else if existing.first_performed_at != 0 {
        existing.first_performed_at
    } else {
        incoming.first_performed_at
    };
    merged.last_performed_at = existing.last_performed_at.max(incoming.last_performed_at);
    merged.updated_at = existing.updated_at.max(incoming.updated_at);
    merged
}

fn normalize_song(song_key_raw: &str, sv: &Value) -> R<Option<Song>> {
    let mut artist = text_field(get(sv, "artist"))?;
    let mut title = text_field(get(sv, "title"))?;
    if (artist.is_empty() || title.is_empty()) && song_key_raw.contains('|') {
        let parts: Vec<&str> = song_key_raw.split('|').collect();
        if artist.is_empty() && !parts.is_empty() {
            artist = py_strip(parts[0]).to_string();
        }
        if title.is_empty() && parts.len() > 1 {
            title = py_strip(parts[1]).to_string();
        }
    }
    let path_arg = match sv.get("path") {
        Some(p) => p.clone(),
        None => Value::String(song_key_raw.to_string()),
    };
    let skey = song_key(
        &Value::String(artist.clone()),
        &Value::String(title.clone()),
        get(sv, "songid"),
        &path_arg,
        get(sv, "provider"),
        get(sv, "provider_track_id"),
    )?;
    if skey.is_empty() {
        return Ok(None);
    }
    let provider_raw = match sv.get("provider") {
        Some(p) => p.clone(),
        None => Value::String("local".into()),
    };
    let provider = py_lower(py_strip(&py_str(py_or(&[&provider_raw, &Value::String("local".into())]))?))?;
    Ok(Some(Song {
        key: skey,
        artist,
        title,
        songid: text_field(get(sv, "songid"))?,
        disc_id: text_field(get(sv, "disc_id"))?,
        song_type: py_upper_ascii(&text_field(get(sv, "song_type"))?)?,
        duet_display: text_field(get(sv, "duet_display"))?,
        path: text_field(get(sv, "path"))?,
        provider,
        provider_track_id: text_field(get(sv, "provider_track_id"))?,
        provider_url: text_field(get(sv, "provider_url"))?,
        local_reference_path: text_field(get(sv, "local_reference_path"))?,
        authorization_requirement: text_field(get(sv, "authorization_requirement"))?,
        availability_status: text_field(get(sv, "availability_status"))?,
        last_key: int_strict(get(sv, "last_key"), 0)?,
        last_tempo_percent: int_strict(get(sv, "last_tempo_percent"), 100)?,
        play_count: int_strict(get(sv, "play_count"), 0)?,
        first_performed_at: int_strict(get(sv, "first_performed_at"), 0)?,
        last_performed_at: int_strict(get(sv, "last_performed_at"), 0)?,
        updated_at: int_strict(get(sv, "updated_at"), 0)?,
    }))
}

/// `str.upper()`; only ASCII is reproduced (the app's song types are ZIP, CDG, KFN, MP4 ...)
fn py_upper_ascii(s: &str) -> R<String> {
    if !s.is_ascii() {
        return decline("non-ASCII song type");
    }
    Ok(s.to_ascii_uppercase())
}

/// `_normalize_singer_history_store(raw)["singers"]`
pub fn normalize(raw: &Value) -> R<Store> {
    let mut store = Store::default();
    let empty = serde_json::Map::new();
    let singers = raw.get("singers").and_then(Value::as_object).unwrap_or(&empty);
    for (raw_key, rv) in singers {
        let norm_key = py_lower(py_strip(raw_key))?;
        if norm_key.is_empty() || !rv.is_object() {
            continue;
        }
        let name_value = match rv.get("name") {
            Some(n) => n.clone(),
            None => Value::String(raw_key.clone()),
        };
        let name = py_strip(&py_str_or_empty(&name_value)?).to_string();
        let mut songs: Vec<Song> = Vec::new();
        if let Some(song_map) = rv.get("songs").and_then(Value::as_object) {
            for (sk, sv) in song_map {
                if !sv.is_object() {
                    continue;
                }
                let Some(song) = normalize_song(sk, sv)? else { continue };
                if let Some(pos) = songs.iter().position(|s| s.key == song.key) {
                    let merged = merge(&songs[pos], &song);
                    songs[pos] = merged;
                } else {
                    songs.push(song);
                }
            }
        }
        let total: i64 = songs.iter().map(|s| s.play_count.max(0)).sum();
        let singer = Singer {
            key: norm_key.clone(),
            name,
            preferred_disc_priority: disc_priority_text(rv.get("preferred_disc_priority").unwrap_or(&Value::String(String::new())))?,
            updated_at: int_try(get(rv, "updated_at"))?,
            last_seen_at: int_try(get(rv, "last_seen_at"))?,
            total_performances: total,
            unique_song_count: songs.len() as i64,
            songs,
        };
        if let Some(pos) = store.singers.iter().position(|s| s.key == norm_key) {
            store.singers[pos] = singer; // same position, new value, like a Python dict
        } else {
            store.singers.push(singer);
        }
    }
    Ok(store)
}

/// Read `singer_history.json` (read-only) and normalise it.
pub fn load(path: &std::path::Path) -> R<Store> {
    let bytes = std::fs::read(path).map_err(|e| format!("singer history unavailable: {e}"))?;
    let text = singws_dbw::index::decode_ignore(&bytes);
    load_text(&text)
}

pub fn load_text(text: &str) -> R<Store> {
    singws_store::reject_big_integers(text).map_err(|e| format!("decline: {e}"))?;
    let raw: Value = serde_json::from_str(text).map_err(|e| format!("json: {e}"))?;
    normalize(&raw)
}

// ---- local time and "last sang" labels ---------------------------------------------------------------------------------------------

#[derive(Debug, PartialEq, Clone, Copy)]
pub struct Local {
    pub year: i32,
    pub month0: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
}

unsafe extern "C" {
    fn tzset();
}

/// `datetime.fromtimestamp(ts)`: local time, `None` where Python raises (outside years 1..=9999 or an unusable timestamp)
pub fn localtime(ts: i64) -> Option<Local> {
    let t: libc::time_t = ts as libc::time_t;
    if t as i64 != ts {
        return None;
    }
    // SAFETY: tzset and localtime_r are given valid pointers to locals; localtime_r is thread-safe.
    let tm = unsafe {
        tzset();
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&t, &mut tm).is_null() {
            return None;
        }
        tm
    };
    let year = tm.tm_year + 1900;
    if !(1..=9999).contains(&year) {
        return None;
    }
    Some(Local { year, month0: tm.tm_mon as u32, day: tm.tm_mday as u32, hour: tm.tm_hour as u32, minute: tm.tm_min as u32 })
}

const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

fn clock_text(l: &Local) -> String {
    let h12 = if l.hour % 12 == 0 { 12 } else { l.hour % 12 };
    let t = format!("{:02}:{:02} {}", h12, l.minute, if l.hour < 12 { "AM" } else { "PM" });
    t.trim_start_matches('0').to_string()
}

/// `_history_song_last_sang_text(timestamp, now=...)`; `now_ts` is the current time as epoch seconds
pub fn last_sang_text(timestamp: i64, now_ts: i64) -> String {
    let (Some(p), Some(c)) = (localtime(timestamp), localtime(now_ts)) else {
        return "Last sang date unavailable".into();
    };
    let time_text = clock_text(&p);
    if (p.year, p.month0, p.day) == (c.year, c.month0, c.day) {
        return format!("Last sang tonight at {time_text}");
    }
    let date = if p.year == c.year {
        format!("{} {:02}", MONTHS[p.month0 as usize], p.day)
    } else {
        format!("{} {:02}, {}", MONTHS[p.month0 as usize], p.day, p.year)
    };
    format!("Last sang {date} at {time_text}")
}

/// `_format_history_timestamp(ts)`
pub fn format_timestamp(ts: i64) -> String {
    if ts <= 0 {
        return "Never".into();
    }
    match localtime(ts) {
        Some(l) => format!("{} {:02}, {}", MONTHS[l.month0 as usize], l.day, l.year),
        None => "Never".into(),
    }
}

// ---- directory, songs, summary -------------------------------------------------------------------------------------------------------

#[derive(Debug, PartialEq, Clone)]
pub struct Row {
    pub text: String,
    pub key: String,
    pub selectable: bool,
}

#[derive(Debug, PartialEq)]
pub struct Directory {
    pub rows: Vec<Row>,
    pub total_matches: usize,
    pub shown: usize,
}

pub fn directory(store: &Store, search: &str, render_limit: usize, song_scan_limit: usize) -> R<Directory> {
    let search = py_lower(py_strip(search))?;
    let render_limit = render_limit.max(1);
    let song_scan_limit = song_scan_limit.max(1);
    let mut singers: Vec<(&Singer, String)> = Vec::new();
    for s in &store.singers {
        singers.push((s, py_lower(py_strip(&s.name))?));
    }
    singers.sort_by(|a, b| a.1.cmp(&b.1)); // stable, like list.sort
    let mut total_matches = 0usize;
    let mut matched: Vec<(&Singer, String)> = Vec::new();
    for (s, _) in &singers {
        let name = if s.name.is_empty() { py_strip(&s.key).to_string() } else { py_strip(&s.name).to_string() };
        if !search.is_empty() {
            let haystack = py_lower(&format!("{} {}", name, s.preferred_disc_priority))?;
            if !haystack.contains(&search) {
                let mut song_match = false;
                for (idx, song) in s.songs.iter().enumerate() {
                    if idx >= song_scan_limit {
                        break;
                    }
                    let text = py_lower(&format!("{} {} {} {}", song.artist, song.title, song.disc_id, song.duet_display))?;
                    if text.contains(&search) {
                        song_match = true;
                        break;
                    }
                }
                if !song_match {
                    continue;
                }
            }
        }
        total_matches += 1;
        if matched.len() < render_limit {
            matched.push((s, name));
        }
    }
    let mut rows = Vec::new();
    for (s, name) in &matched {
        let pref = py_strip(&s.preferred_disc_priority);
        let mut lines = vec![name.clone(), format!("{} plays  ·  {} songs", s.total_performances, s.unique_song_count)];
        if !pref.is_empty() {
            lines.push(format!("Prefers: {pref}"));
        }
        rows.push(Row { text: lines.join("\n"), key: s.key.clone(), selectable: true });
    }
    let shown = total_matches.min(matched.len());
    if total_matches > matched.len() {
        rows.push(Row {
            text: format!("Showing {} of {} singers. Refine search to narrow results.", matched.len(), total_matches),
            key: String::new(),
            selectable: false,
        });
    }
    Ok(Directory { rows, total_matches, shown })
}

/// `str.casefold()`: the lowercase mapping of each character unless the generated exception table says otherwise.
pub fn casefold(s: &str) -> R<String> {
    if let Some(c) = s.chars().find(|c| singws_dbw::pyval::unicode_skew_risk(*c)) {
        return decline(format!("Unicode table skew risk: U+{:04X}", c as u32));
    }
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match CASEFOLD_EXCEPTIONS.binary_search_by_key(&c, |(k, _)| *k) {
            Ok(i) => out.push_str(CASEFOLD_EXCEPTIONS[i].1),
            Err(_) => out.extend(c.to_lowercase()),
        }
    }
    Ok(out)
}

fn song_display(s: &Song, now_ts: i64) -> String {
    let artist = if s.artist.is_empty() { "Unknown Artist" } else { &s.artist };
    let title = if s.title.is_empty() { "Unknown Song" } else { &s.title };
    let mut meta: Vec<String> = Vec::new();
    if !s.duet_display.is_empty() {
        meta.push(format!("Duet: {}", s.duet_display));
    }
    if !s.disc_id.is_empty() {
        meta.push(s.disc_id.clone());
    }
    if !s.song_type.is_empty() {
        meta.push(s.song_type.clone());
    }
    meta.push(format!("Key {:+}", s.last_key));
    meta.push(format!("Tempo {}%", s.last_tempo_percent));
    meta.push(last_sang_text(s.last_performed_at, now_ts));
    format!("{artist} • {title}\n{}", meta.join("  ·  "))
}

#[derive(Debug, PartialEq)]
pub struct SongsView {
    pub rows: Vec<Row>,
    pub total: usize,
    pub shown: usize,
    pub total_unfiltered: usize,
    pub filter_active: bool,
    pub meta: String,
}

/// `_singer_history_song_meta_text(shown_total, shown)`
pub fn song_meta_text(shown_total: usize, shown: Option<usize>, total_unfiltered: usize, filtered: bool) -> String {
    let suffix = match shown {
        Some(s) if s != shown_total => format!(" ({s} shown)"),
        _ => String::new(),
    };
    if filtered { format!("{shown_total} of {total_unfiltered} entries{suffix}") } else { format!("{shown_total} entries{suffix}") }
}

pub fn songs_view(store: &Store, singer_key: &str, query: &str, render_limit: usize, now_ts: i64) -> R<SongsView> {
    // The app looks the key up with `.get(key, {})`: a non-empty key that is not in the store behaves like a singer with no songs, and only an
    // empty key means "nothing selected".
    let empty = Singer {
        key: singer_key.to_string(),
        name: String::new(),
        preferred_disc_priority: String::new(),
        updated_at: 0,
        last_seen_at: 0,
        total_performances: 0,
        unique_song_count: 0,
        songs: Vec::new(),
    };
    let found = store.singers.iter().find(|s| s.key == singer_key);
    if singer_key.is_empty() {
        return Ok(SongsView { rows: vec![], total: 0, shown: 0, total_unfiltered: 0, filter_active: false, meta: song_meta_text(0, None, 0, false) });
    }
    let singer = found.unwrap_or(&empty);
    let query = casefold(py_strip(query))?;
    let total_unfiltered = singer.songs.len();
    let tokens: Vec<&str> = query.split(is_py_space).filter(|t| !t.is_empty()).collect();
    let mut songs: Vec<&Song> = Vec::new();
    for s in &singer.songs {
        if tokens.is_empty() {
            songs.push(s);
            continue;
        }
        let mut parts: Vec<&str> = vec![&s.key];
        for f in [&s.artist, &s.title, &s.disc_id, &s.song_type, &s.duet_display] {
            if !f.is_empty() {
                parts.push(f);
            }
        }
        let blob = casefold(&parts.join(" "))?;
        if tokens.iter().all(|t| blob.contains(t)) {
            songs.push(s);
        }
    }
    let filter_active = songs.len() != total_unfiltered || !query.is_empty();
    if songs.is_empty() {
        return Ok(SongsView { rows: vec![], total: 0, shown: 0, total_unfiltered, filter_active, meta: song_meta_text(0, None, total_unfiltered, filter_active) });
    }
    let render_limit = render_limit.max(1);
    let mut keyed: Vec<((i64, String, String), &Song)> = Vec::new();
    for s in &songs {
        keyed.push(((-s.last_performed_at, py_lower(&s.artist)?, py_lower(&s.title)?), s));
    }
    keyed.sort_by(|a, b| a.0.cmp(&b.0));
    let total = keyed.len();
    let mut rows: Vec<Row> = keyed
        .iter()
        .take(render_limit)
        .map(|(_, s)| Row { text: song_display(s, now_ts), key: s.key.clone(), selectable: true })
        .collect();
    let shown = total.min(render_limit);
    if total > render_limit {
        rows.push(Row {
            text: format!("Showing {render_limit} of {total} songs. Use search or edit history to narrow this list."),
            key: String::new(),
            selectable: false,
        });
    }
    let meta = song_meta_text(total, Some(shown), total_unfiltered, filter_active);
    Ok(SongsView { rows, total, shown, total_unfiltered, filter_active, meta })
}

/// (name, summary line) shown above one singer's songs, or the "no singer selected" pair.
pub fn details(store: &Store, singer_key: &str) -> (String, String) {
    if singer_key.is_empty() {
        return ("Select a singer".into(), "No singer selected.".into());
    }
    let blank = Singer {
        key: singer_key.to_string(),
        name: String::new(),
        preferred_disc_priority: String::new(),
        updated_at: 0,
        last_seen_at: 0,
        total_performances: 0,
        unique_song_count: 0,
        songs: Vec::new(),
    };
    let s = store.singers.iter().find(|s| s.key == singer_key).unwrap_or(&blank);
    let name = if s.name.is_empty() { s.key.clone() } else { s.name.clone() };
    let unique = if s.unique_song_count != 0 { s.unique_song_count } else { s.songs.len() as i64 };
    let mut summary = format!("{} total performances  ·  {} songs  ·  Last seen {}", s.total_performances, unique, format_timestamp(s.last_seen_at));
    let pref = py_strip(&s.preferred_disc_priority);
    if !pref.is_empty() {
        summary.push_str(&format!("  ·  Preferred brand {pref}"));
    }
    (name, summary)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn store(v: Value) -> Store {
        normalize(&v).unwrap()
    }

    fn song(artist: &str, title: &str, extra: Value) -> Value {
        let mut s = json!({"artist": artist, "title": title});
        for (k, v) in extra.as_object().unwrap() {
            s[k] = v.clone();
        }
        s
    }

    #[test]
    fn python_int_conversion_rules() {
        let v = |x: Value| match int_or(&x, 0) {
            IntOutcome::Value(i) => Some(i),
            IntOutcome::Raise => None,
            IntOutcome::Decline(_) => Some(i64::MIN),
        };
        assert_eq!(v(json!(null)), Some(0));
        assert_eq!(v(json!("")), Some(0));
        assert_eq!(v(json!("7")), Some(7));
        assert_eq!(v(json!(" -7 ")), Some(-7));
        assert_eq!(v(json!("1_0")), Some(10));
        assert_eq!(v(json!("1__0")), None);
        assert_eq!(v(json!("5.5")), None, "int('5.5') raises");
        assert_eq!(v(json!("abc")), None);
        assert_eq!(v(json!(5.9)), Some(5));
        assert_eq!(v(json!(-5.9)), Some(-5));
        assert_eq!(v(json!(true)), Some(1));
        assert_eq!(v(json!([1])), None);
        assert_eq!(v(json!("٣")), Some(i64::MIN), "non-ASCII digits are declined");
        assert!(matches!(int_or(&json!(null), 100), IntOutcome::Value(100)), "the default applies to falsy values");
        assert!(matches!(int_or(&json!(0), 100), IntOutcome::Value(100)), "int(0 or 100) is 100");
    }

    #[test]
    fn brands_canonicalise_like_the_app() {
        let b = |s: &str| disc_priority_text(&json!(s)).unwrap();
        assert_eq!(b("kv"), "KV");
        assert_eq!(b("Karaoke Version 12"), "KV");
        assert_eq!(b("zoom dl, SC, kv"), "ZM, SC, KV");
        assert_eq!(b("kv, KV, Karaoke Version"), "KV", "duplicates collapse");
        assert_eq!(b("mystery"), "MYSTERY", "unknown text is upper-cased");
        assert_eq!(b("SC - CC"), "SC", "the first known segment wins");
        assert_eq!(b("a,b,c,d,e,f,g,h,i,j,k,l"), "A, B, C, D, E, F, G, H, I, J", "ten at most");
        assert_eq!(b(" , ,"), "");
        assert_eq!(b("/lib/KV 12.zip"), "KV", "a path uses its stem");
        assert_eq!(b("dir/.kv"), "KV", "a leading dot is part of the name, not a suffix");
        assert_eq!(b("x/KV/."), "KV", "a '.' path component vanishes, so the name is KV");
        assert_eq!(b("a/b/"), "A/B/", "unknown text is upper-cased whole, whatever its stem");
        assert_eq!(disc_priority_text(&json!(["sc", "KV", "sc"])).unwrap(), "SC, KV");
        assert_eq!(disc_priority_text(&json!(5)).unwrap(), "");
        assert!(disc_priority_text(&json!("Ünï")).unwrap_err().starts_with("decline:"));
        assert_eq!(brand_canonical("KVX"), "KV".to_string().replace("KV", ""), "three letters after a short alias is not a brand match");
        assert_eq!(brand_canonical("KV12"), "KV", "a coded suffix is");
        assert_eq!(brand_canonical("KARAOKEVERSION"), "KV");
        assert_eq!(brand_canonical("KARAFUNPRO"), "KARAFUN", "a long alias accepts trailing text");
    }

    #[test]
    fn song_keys_fold_like_the_app() {
        let k = |a: &str, t: &str, p: &str, pid: &str| song_key(&json!(a), &json!(t), &json!(""), &json!(""), &json!(p), &json!(pid)).unwrap();
        assert_eq!(k("Guns N' Roses", "Sweet Child O’ Mine", "", ""), "guns n roses|sweet child o mine");
        assert_eq!(k("  A--B  ", "C!!", "", ""), "a b|c");
        assert_eq!(k("A", "B", "Spotify", "T1"), "spotify|t1|a|b");
        assert_eq!(k("A", "B", "local", "T1"), "a|b", "local provider keys do not use the track id");
        assert_eq!(k("A", "B", "spotify", ""), "a|b");
        assert_eq!(song_key(&json!(""), &json!(""), &json!("ID 7"), &json!("/x/y.zip"), &json!(""), &json!("")).unwrap(), "x y zip", "path wins over songid");
        assert_eq!(song_key(&json!(""), &json!(""), &json!("ID 7"), &json!(""), &json!(""), &json!("")).unwrap(), "id 7");
    }

    #[test]
    fn normalising_merges_duplicate_songs_and_recomputes_totals() {
        let s = store(json!({"singers": {
            " TJ ": {"name": " TJ ", "preferred_disc_priority": "kv", "total_performances": 999, "unique_song_count": 999, "songs": {
                "a|b": song("A", "B", json!({"play_count": 2, "last_performed_at": 100, "updated_at": 1, "disc_id": "KV 1", "last_key": 2})),
                "A|B ": song("a", "b", json!({"play_count": 5, "last_performed_at": 50, "updated_at": 9, "disc_id": "", "last_key": -1, "first_performed_at": 7})),
                "other": song("C", "D", json!({"play_count": -4})),
            }},
            "tj": {"name": "Replaced"},
        }}));
        assert_eq!(s.singers.len(), 1, "keys that normalise equal collapse; the later record replaces the earlier one");
        assert_eq!(s.singers[0].name, "Replaced");
        let t = store(json!({"singers": {"tj": {"name": "TJ", "songs": {
            "a|b": song("A", "B", json!({"play_count": 2, "last_performed_at": 100, "updated_at": 1, "disc_id": "KV 1", "last_key": 2, "first_performed_at": 30})),
            "A|B ": song("a", "b", json!({"play_count": 5, "last_performed_at": 50, "updated_at": 9, "disc_id": "", "last_key": -1, "first_performed_at": 7})),
            "other": song("C", "D", json!({"play_count": -4})),
        }}}}));
        let r = &t.singers[0];
        assert_eq!(r.songs.len(), 2);
        let m = &r.songs[0];
        assert_eq!((m.play_count, m.last_performed_at, m.first_performed_at, m.updated_at), (5, 100, 7, 9), "cumulative: max plays, latest sang, earliest first, newest update");
        assert_eq!((m.last_key, m.disc_id.as_str()), (-1, "KV 1"), "the newer record's key wins, an empty newer text keeps the older text");
        assert_eq!(r.total_performances, 5, "negative plays count as zero; the stored total is ignored");
        assert_eq!(r.unique_song_count, 2);
    }

    #[test]
    fn records_and_songs_the_app_skips_are_skipped() {
        let s = store(json!({"singers": {"": {"name": "x"}, "ok": {"songs": {"k": 5, "": {"title": ""}, "p|q": {"songid": ""}}}, "bad": 3}}));
        assert_eq!(s.singers.len(), 1);
        assert_eq!(s.singers[0].songs.len(), 1, "no usable key means no song, but 'p|q' supplies artist and title");
        assert_eq!((s.singers[0].songs[0].artist.as_str(), s.singers[0].songs[0].title.as_str()), ("p", "q"));
        assert_eq!(s.singers[0].name, "ok");
    }

    #[test]
    fn a_value_python_cannot_int_makes_the_whole_store_decline() {
        let r = normalize(&json!({"singers": {"a": {"songs": {"x": {"artist": "A", "title": "B", "last_key": "abc"}}}}}));
        assert!(r.unwrap_err().starts_with("decline:"));
        // but at the singer level the app swallows it
        let ok = normalize(&json!({"singers": {"a": {"updated_at": "abc", "last_seen_at": [1]}}})).unwrap();
        assert_eq!((ok.singers[0].updated_at, ok.singers[0].last_seen_at), (0, 0));
        assert!(load_text("[18446744073709551616]").unwrap_err().starts_with("decline:"));
        assert!(load_text("{").unwrap_err().starts_with("json:"));
    }

    fn sample() -> Store {
        store(json!({"singers": {
            "zed": {"name": "Zed", "songs": {"1": song("Straße Boys", "Hoch", json!({"last_performed_at": 10}))}},
            "amy": {"name": "Amy", "preferred_disc_priority": "kv", "songs": {
                "1": song("ABBA", "Waterloo", json!({"play_count": 3, "last_performed_at": 300, "disc_id": "KV 1", "song_type": "zip"})),
                "2": song("ABBA", "Chiquitita", json!({"play_count": 1, "last_performed_at": 300})),
                "3": song("Zz", "Top", json!({"play_count": 1, "last_performed_at": 900})),
            }},
            "bob": {"name": "bob", "songs": {}},
        }}))
    }

    #[test]
    fn the_directory_sorts_by_name_searches_names_brands_and_songs_and_caps_rows() {
        let s = sample();
        let d = directory(&s, "", 700, 500).unwrap();
        assert_eq!(d.rows.iter().map(|r| r.key.as_str()).collect::<Vec<_>>(), ["amy", "bob", "zed"], "case-insensitive name order");
        assert_eq!(d.rows[0].text, "Amy\n5 plays  ·  3 songs\nPrefers: KV");
        assert_eq!(d.rows[1].text, "bob\n0 plays  ·  0 songs");
        assert_eq!(directory(&s, "  KV ", 700, 500).unwrap().rows.len(), 1, "brand text is searchable");
        assert_eq!(directory(&s, "waterloo", 700, 500).unwrap().rows[0].key, "amy", "so are song artist/title/disc text");
        assert_eq!(directory(&s, "waterloo", 700, 0).unwrap().total_matches, 1, "a scan limit of 0 still reads one song");
        assert_eq!(directory(&s, "zz", 700, 1).unwrap().total_matches, 0, "songs past the scan limit are not searched");
        let capped = directory(&s, "", 2, 500).unwrap();
        assert_eq!((capped.total_matches, capped.shown, capped.rows.len()), (3, 2, 3));
        assert!(!capped.rows[2].selectable);
        assert_eq!(capped.rows[2].text, "Showing 2 of 3 singers. Refine search to narrow results.");
    }

    #[test]
    fn songs_are_filtered_by_every_token_sorted_newest_first_and_labelled() {
        let s = sample();
        let v = songs_view(&s, "amy", "", 500, 1_000).unwrap();
        let titles: Vec<_> = v.rows.iter().map(|r| r.text.lines().next().unwrap().to_string()).collect();
        assert_eq!(titles, ["Zz • Top", "ABBA • Chiquitita", "ABBA • Waterloo"], "newest first; equal times by artist then title");
        assert_eq!(v.meta, "3 entries");
        let f = songs_view(&s, "amy", " ABBA  kv ", 500, 1_000).unwrap();
        assert_eq!(f.rows.len(), 1);
        assert_eq!(f.meta, "1 of 3 entries");
        assert!(f.filter_active);
        let capped = songs_view(&s, "amy", "", 2, 1_000).unwrap();
        assert_eq!((capped.total, capped.shown, capped.rows.len()), (3, 2, 3));
        assert_eq!(capped.meta, "3 entries (2 shown)");
        assert_eq!(capped.rows[2].text, "Showing 2 of 3 songs. Use search or edit history to narrow this list.");
        let none = songs_view(&s, "amy", "nothing", 500, 1_000).unwrap();
        assert!(none.rows.is_empty());
        assert_eq!(none.meta, "0 of 3 entries");
        assert_eq!(songs_view(&s, "", "", 500, 1_000).unwrap().meta, "0 entries");
        assert_eq!(songs_view(&s, "ghost", "x", 500, 1_000).unwrap().meta, "0 of 0 entries", "an unknown non-empty key behaves like an empty singer");
        assert!(songs_view(&s, "zed", "STRASSE", 500, 1_000).unwrap().rows.len() == 1, "casefold maps ß to ss");
    }

    #[test]
    fn casefold_differs_from_lowercase_only_where_the_table_says() {
        assert_eq!(casefold("Straße").unwrap(), "strasse");
        assert_eq!(casefold("ΑΣ ς").unwrap(), "ασ σ", "final sigma folds to sigma, with no context rules");
        assert_eq!(casefold("İ").unwrap(), "i\u{307}");
        assert_eq!(casefold("ABC é").unwrap(), "abc é");
        assert!(casefold("\u{A7CE}").unwrap_err().starts_with("decline:"));
    }

    #[test]
    fn song_rows_show_artist_title_meta_in_the_apps_order() {
        let s = store(json!({"singers": {"a": {"songs": {"k": {"artist": "", "title": "", "songid": "id", "duet_display": "Al + Bo", "disc_id": "KV 1", "song_type": "cdg",
            "last_key": 3, "last_tempo_percent": 90, "last_performed_at": 0}}}}}));
        let v = songs_view(&s, "a", "", 5, 1_700_000_000).unwrap();
        let lines: Vec<&str> = v.rows[0].text.lines().collect();
        assert_eq!(lines[0], "Unknown Artist • Unknown Song");
        assert!(lines[1].starts_with("Duet: Al + Bo  ·  KV 1  ·  CDG  ·  Key +3  ·  Tempo 90%  ·  Last sang "), "{}", lines[1]);
    }

    #[test]
    fn dates_and_clock_labels() {
        assert_eq!(format_timestamp(0), "Never");
        assert_eq!(format_timestamp(-5), "Never");
        assert_eq!(format_timestamp(253402300800 * 2), "Never", "beyond year 9999 Python raises and the app says Never");
        assert_eq!(last_sang_text(253402300800 * 2, 1_700_000_000), "Last sang date unavailable");
        assert_eq!(clock_text(&Local { year: 2026, month0: 0, day: 1, hour: 0, minute: 5 }), "12:05 AM");
        assert_eq!(clock_text(&Local { year: 2026, month0: 0, day: 1, hour: 12, minute: 5 }), "12:05 PM");
        assert_eq!(clock_text(&Local { year: 2026, month0: 0, day: 1, hour: 9, minute: 7 }), "9:07 AM");
        assert_eq!(clock_text(&Local { year: 2026, month0: 0, day: 1, hour: 23, minute: 59 }), "11:59 PM");
        let now = 1_700_000_000;
        assert!(last_sang_text(now - 60, now).starts_with("Last sang tonight at ") || last_sang_text(now - 60, now).starts_with("Last sang "));
        assert!(last_sang_text(now - 86400 * 400, now).contains(", 20"), "another year shows the year");
    }

    #[test]
    fn details_follow_the_apps_summary_line() {
        let s = sample();
        let (name, summary) = details(&s, "amy");
        assert_eq!(name, "Amy");
        assert_eq!(summary, "5 total performances  ·  3 songs  ·  Last seen Never  ·  Preferred brand KV");
        assert_eq!(details(&s, ""), ("Select a singer".to_string(), "No singer selected.".to_string()));
        assert_eq!(details(&s, "ghost").0, "ghost");
    }
}
