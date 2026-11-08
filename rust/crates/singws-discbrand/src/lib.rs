//! The disc / brand rules of 0.2.18.1.py (lines ~4681-5016): which karaoke company a disc id belongs to, the priority list a request is resolved with, and the picker list.
//! Text is handled like Python's `str`: `.upper()` is the full Unicode mapping, `.strip()` and `\s` use Python's whitespace set, sorting is by code point. Values that are not text
//! (numbers, lists) are DECLINED by the callers; non-ASCII digits in the picker rule are declined (Python's `isdigit` differs from Rust's classes).
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

pub type R<T> = Result<T, String>;

pub const ALIASES: &[(&str, &[&str])] = &[
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

pub fn is_py_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\u{0b}' | '\u{0c}' | '\r' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{1f}' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// `re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())`
pub fn brand_key(value: &str) -> String {
    value.to_uppercase().chars().filter(|c| c.is_ascii_uppercase() || c.is_ascii_digit()).collect()
}

/// `_disc_brand_prefix_key`
pub fn prefix_key(value: &str) -> String {
    let key = brand_key(value);
    let letters: String = key.chars().take_while(|c| c.is_ascii_uppercase()).collect();
    if !letters.is_empty() { letters } else { key.chars().take(8).collect() }
}

/// The name of a POSIX path as `pathlib.PurePosixPath(raw).name`: empty and "." components are dropped.
fn path_name(raw: &str) -> String {
    raw.split('/').filter(|p| !p.is_empty() && *p != ".").last().unwrap_or("").to_string()
}

/// `Path(raw).stem`
fn path_stem(raw: &str) -> String {
    let name = path_name(raw);
    match name.rfind('.') {
        Some(i) if i > 0 && i < name.len() - 1 => name[..i].to_string(),
        _ => name,
    }
}

/// `re.split(r"\s+-\s+|[|/\\()\[\]{}]+", text)`
fn split_segments(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let is_sep = |c: char| matches!(c, '|' | '/' | '\\' | '(' | ')' | '[' | ']' | '{' | '}');
    let mut parts = vec![];
    let (mut start, mut i) = (0usize, 0usize);
    while i < chars.len() {
        let mut matched: Option<usize> = None;
        if is_py_space(chars[i]) {
            let mut j = i;
            while j < chars.len() && is_py_space(chars[j]) {
                j += 1;
            }
            if j < chars.len() && chars[j] == '-' {
                let mut k = j + 1;
                while k < chars.len() && is_py_space(chars[k]) {
                    k += 1;
                }
                if k > j + 1 {
                    matched = Some(k);
                }
            }
        } else if is_sep(chars[i]) {
            let mut j = i;
            while j < chars.len() && is_sep(chars[j]) {
                j += 1;
            }
            matched = Some(j);
        }
        match matched {
            Some(end) => {
                parts.push(chars[start..i].iter().collect::<String>());
                start = end;
                i = end;
            }
            None => i += 1,
        }
    }
    parts.push(chars[start..].iter().collect());
    parts
}

/// `_disc_brand_value_segments`
pub fn value_segments(value: &str) -> Vec<String> {
    let raw = py_strip(value).to_string();
    if raw.is_empty() {
        return vec![];
    }
    let mut values = vec![raw.clone()];
    let stem = if raw.contains('/') || raw.contains('\\') { path_stem(&raw) } else { raw.clone() };
    for part in split_segments(&stem) {
        let part = py_strip(&part).to_string();
        if !part.is_empty() && !values.contains(&part) {
            values.push(part);
        }
    }
    values
}

#[derive(Debug, Clone, PartialEq)]
pub struct MatchInfo {
    pub raw: String,
    pub normalized: String,
    pub canonical: String,
    pub confidence: i64,
    pub matched_alias: String,
    pub match_type: &'static str,
}

fn alias_keys() -> Vec<(&'static str, Vec<String>)> {
    ALIASES.iter().map(|(c, al)| (*c, std::iter::once(*c).chain(al.iter().copied()).map(brand_key).filter(|k| !k.is_empty()).collect())).collect()
}

fn canonical_by_key(key: &str) -> Option<&'static str> {
    // later definitions overwrite earlier ones, exactly as the dict loop in Python does
    let mut found = None;
    for (canonical, keys) in alias_keys() {
        if keys.iter().any(|k| k == key) {
            found = Some(canonical);
        }
    }
    found
}

/// `karaoke_brand_match_info`
pub fn match_info(value: &str) -> MatchInfo {
    let raw = py_strip(value).to_string();
    let key = brand_key(&raw);
    let mut info = MatchInfo { raw, normalized: key.clone(), canonical: String::new(), confidence: 0, matched_alias: String::new(), match_type: "unknown" };
    if key.is_empty() {
        info.match_type = "empty";
        return info;
    }
    if let Some(known) = canonical_by_key(&key) {
        info.canonical = known.to_string();
        info.confidence = 100;
        info.matched_alias = key;
        info.match_type = "alias_exact";
        return info;
    }
    let mut best: Option<(i64, usize, &str, String)> = None;
    for (canonical, keys) in alias_keys() {
        for alias in keys {
            if alias.is_empty() || !key.starts_with(&alias) {
                continue;
            }
            let remainder = &key[alias.len()..];
            let long_alias = alias.len() >= 4;
            let coded_suffix = remainder.chars().next().is_some_and(|c| c.is_ascii_digit());
            let free_text = long_alias && !remainder.is_empty();
            if remainder.is_empty() || coded_suffix || free_text {
                let confidence = if coded_suffix { 94 } else if free_text { 90 } else { 88 };
                let cand = (confidence, alias.len(), canonical, alias.clone());
                if best.as_ref().map_or(true, |b| (cand.0, cand.1, cand.2, &cand.3) > (b.0, b.1, b.2, &b.3)) {
                    best = Some(cand);
                }
            }
        }
    }
    if let Some((confidence, _, canonical, alias)) = best {
        info.canonical = canonical.to_string();
        info.confidence = confidence;
        info.matched_alias = alias;
        info.match_type = "alias_prefix";
    }
    info
}

/// `canonical_disc_brand(value, allow_unknown)`
pub fn canonical_brand(value: &str, allow_unknown: bool) -> String {
    let raw = py_strip(value);
    if raw.is_empty() {
        return String::new();
    }
    for segment in value_segments(raw) {
        let info = match_info(&segment);
        if !info.canonical.is_empty() {
            return info.canonical;
        }
    }
    if allow_unknown { raw.to_uppercase() } else { String::new() }
}

/// `normalize_disc_priority` for a list of texts (already split when it came from a comma text).
pub fn normalize_priority(parts: &[String], max_items: usize) -> Vec<String> {
    let mut out: Vec<String> = vec![];
    for raw in parts {
        let p = canonical_brand(raw, true);
        if p.is_empty() || out.contains(&p) {
            continue;
        }
        out.push(p);
        if out.len() >= max_items {
            break;
        }
    }
    out
}

pub fn disc_value_matches_brand(value: &str, brand: &str) -> bool {
    let canonical = canonical_brand(brand, true);
    if canonical.is_empty() {
        return false;
    }
    for segment in value_segments(value) {
        let info = match_info(&segment);
        if info.canonical == canonical {
            return true;
        }
        if info.canonical.is_empty() && brand_key(&segment).starts_with(&brand_key(&canonical)) {
            return true;
        }
    }
    false
}

fn text_of(v: Option<&Value>) -> R<String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => Err("decline: a field that is not text".into()),
    }
}

/// `disc_brand_candidate_values(track)` (a dict of text fields)
pub fn candidate_values(track: &Map<String, Value>) -> R<Vec<String>> {
    let mut values: Vec<String> = vec![];
    for key in ["disc_id", "discid", "DiscID", "Vendor", "vendor"] {
        let v = py_strip(&text_of(track.get(key))?).to_string();
        if !v.is_empty() && !values.contains(&v) {
            values.push(v);
        }
    }
    for key in ["display", "filename", "file", "path"] {
        let mut v = py_strip(&text_of(track.get(key))?).to_string();
        if v.is_empty() {
            continue;
        }
        if key == "path" {
            v = path_name(&v);
        }
        for segment in value_segments(&v) {
            if !segment.is_empty() && !values.contains(&segment) {
                values.push(segment);
            }
        }
    }
    Ok(values)
}

/// `_best_unknown_disc_brand_value`
pub fn best_unknown(candidates: &[String]) -> String {
    for v in candidates {
        let key = brand_key(v);
        let letters = key.chars().take_while(|c| c.is_ascii_uppercase()).count();
        if (1..=10).contains(&letters) && key.chars().nth(letters).is_some_and(|c| c.is_ascii_digit()) {
            return v.clone();
        }
    }
    candidates.first().cloned().unwrap_or_default()
}

pub fn track_matches_brand(track: &Map<String, Value>, brand: &str) -> R<bool> {
    Ok(candidate_values(track)?.iter().any(|v| disc_value_matches_brand(v, brand)))
}

/// `effective_disc_priority`
pub fn effective_priority(host: &[String], singer_raw: &str, singer_override: &[String], max_items: usize) -> Value {
    let host = normalize_priority(host, max_items);
    let override_ = normalize_priority(singer_override, max_items);
    let mut selected_raw = py_strip(singer_raw).to_string();
    let mut selected = canonical_brand(&selected_raw, false);
    let mut source = if !selected_raw.is_empty() { "request" } else { "" };
    if selected.is_empty() && selected_raw.is_empty() && !override_.is_empty() {
        selected = override_[0].clone();
        selected_raw = selected.clone();
        source = "singer_history";
    }
    let effective: Vec<String> = if !selected.is_empty() { std::iter::once(selected.clone()).chain(host.iter().filter(|p| **p != selected).cloned()).collect() } else { host.clone() };
    json!({"host": host, "singer_raw": selected_raw, "singer_normalized": selected, "singer_source": source, "effective": effective, "unknown_ignored": !selected_raw.is_empty() && selected.is_empty()})
}

/// `library_brand_choices(tracks)`; track entries that are not objects are skipped (Python's AttributeError is swallowed per track).
pub fn library_choices(tracks: &[Value]) -> R<Vec<String>> {
    let known: Vec<&str> = ALIASES.iter().map(|a| a.0).collect();
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    for t in tracks {
        let Value::Object(o) = t else { continue };
        let disc = py_strip(&text_of(o.get("disc_id"))?).to_uppercase();
        if disc.is_empty() {
            continue;
        }
        if !disc.is_ascii() {
            return Err("decline: a non-ASCII disc id (isdigit is not modelled)".into());
        }
        let canonical = canonical_brand(&disc, true);
        if canonical.is_empty() {
            continue;
        }
        if !known.contains(&canonical.as_str()) && (canonical != disc || disc.chars().any(|c| c.is_ascii_digit())) {
            continue;
        }
        *counts.entry(canonical).or_insert(0) += 1;
    }
    let mut rows: Vec<(String, i64)> = counts.into_iter().collect();
    rows.sort_by(|a, b| (-a.1, &a.0).cmp(&(-b.1, &b.0)));
    Ok(rows.into_iter().map(|r| r.0).collect())
}

/// `request_preferred_disc_brand(req)`
pub fn request_preferred(req: &Map<String, Value>) -> R<String> {
    for key in ["preferred_brand", "preferred_version", "preferred_disc", "preferred_disc_id", "preferred_disc_priority", "brand", "version", "disc_id", "discid"] {
        let mut value = req.get(key).cloned().unwrap_or(Value::Null);
        if let Value::Array(a) = &value {
            value = a.first().cloned().unwrap_or(Value::Null);
        }
        let mut text = py_strip(&text_of(Some(&value))?).to_string();
        if key == "preferred_disc_priority" && text.contains(',') {
            text = text.split(',').map(py_strip).find(|p| !p.is_empty()).unwrap_or("").to_string();
        }
        if !text.is_empty() {
            return Ok(text);
        }
    }
    Ok(String::new())
}

/// `pick_by_disc_priority(matches, priority)`: the INDEX of the chosen track in `matches` (None when `matches` is empty: Python raises IndexError).
pub fn pick_by_priority(matches: &[Value], priorities: &[String]) -> R<Option<usize>> {
    if matches.is_empty() {
        return Ok(None);
    }
    let prios = normalize_priority(priorities, 10);
    if prios.is_empty() {
        return Ok(Some(0));
    }
    for prefix in &prios {
        for (i, m) in matches.iter().enumerate() {
            let Value::Object(o) = m else { return Err("decline: a match that is not a track".into()) };
            if track_matches_brand(o, prefix)? {
                return Ok(Some(i));
            }
        }
    }
    Ok(Some(0))
}

/// `build_disc_brand_scan_report`
pub fn scan_report(tracks: &[Value], max_unknowns: usize) -> R<Value> {
    let mut track_count = 0i64;
    let mut raw_stats: Vec<(String, Value)> = vec![]; // insertion order
    let mut canonical_counts: Vec<(String, i64)> = vec![];
    let mut unknown_stats: Vec<(String, i64, Vec<String>)> = vec![];
    for t in tracks {
        let Value::Object(o) = t else { continue };
        track_count += 1;
        let candidates = candidate_values(o)?;
        let mut chosen = String::new();
        let mut chosen_info: Option<MatchInfo> = None;
        for v in &candidates {
            let info = match_info(v);
            if !info.canonical.is_empty() {
                chosen = v.clone();
                chosen_info = Some(info);
                break;
            }
        }
        let info = match chosen_info {
            Some(i) => i,
            None => {
                chosen = best_unknown(&candidates);
                match_info(&chosen)
            }
        };
        let raw_key = if chosen.is_empty() { "(blank)".to_string() } else { chosen.clone() };
        match raw_stats.iter_mut().find(|(k, _)| *k == raw_key) {
            Some((_, v)) => v["count"] = json!(v["count"].as_i64().unwrap_or(0) + 1),
            None => raw_stats.push((raw_key.clone(), json!({"raw": raw_key, "normalized": info.normalized, "canonical": info.canonical, "confidence": info.confidence, "count": 1, "matched_alias": info.matched_alias, "match_type": info.match_type}))),
        }
        if !info.canonical.is_empty() {
            match canonical_counts.iter_mut().find(|(k, _)| *k == info.canonical) {
                Some((_, n)) => *n += 1,
                None => canonical_counts.push((info.canonical.clone(), 1)),
            }
        } else {
            let prefix = prefix_key(&chosen);
            if !prefix.is_empty() {
                let idx = match unknown_stats.iter().position(|u| u.0 == prefix) {
                    Some(i) => i,
                    None => {
                        unknown_stats.push((prefix.clone(), 0, vec![]));
                        unknown_stats.len() - 1
                    }
                };
                unknown_stats[idx].1 += 1;
                if !chosen.is_empty() && !unknown_stats[idx].2.contains(&chosen) && unknown_stats[idx].2.len() < 5 {
                    unknown_stats[idx].2.push(chosen.clone());
                }
            }
        }
    }
    let mut raw_rows: Vec<Value> = raw_stats.into_iter().map(|r| r.1).collect();
    raw_rows.sort_by(|a, b| (-a["count"].as_i64().unwrap_or(0), a["raw"].as_str().unwrap_or("")).cmp(&(-b["count"].as_i64().unwrap_or(0), b["raw"].as_str().unwrap_or(""))));
    raw_rows.truncate(200);
    let mut unk: Vec<(String, i64, Vec<String>)> = unknown_stats;
    unk.sort_by(|a, b| (-a.1, &a.0).cmp(&(-b.1, &b.0)));
    unk.truncate(max_unknowns);
    let mut cc = Map::new();
    for (k, n) in canonical_counts {
        cc.insert(k, json!(n));
    }
    Ok(json!({"track_count": track_count, "raw_values": raw_rows, "canonical_counts": Value::Object(cc), "unknown_prefixes": unk.into_iter().map(|u| json!({"prefix": u.0, "count": u.1, "examples": u.2})).collect::<Vec<_>>()}))
}
