//! The settings migrations of `KaraokeApp.__init__` (0.2.18.1.py, from "Merge defaults" up to the DEFAULTS fill), in the same order, with the
//! same Python semantics for the odd values a hand-edited or very old `settings.json` can hold: `bool(x)`, `int(x or 0)` inside
//! `try/except`, `float(x)`, `str(x or "off").strip().lower()`.
//!
//! `migrate(settings)` changes the map in place and returns whether anything changed (the app's `changed` flag, which decides whether the
//! file is saved). `Err("decline: ...")` means a value Python could handle but this port cannot reproduce exactly (an integer beyond 64 bits,
//! non-ASCII numeric text): the caller then keeps Python's answer. The CDG timing engine is permanently "mpv" in the app
//! (`_cdg_timing_engine`), so the engine-split migration takes that branch.

use serde_json::{Map, Value};
use singws_dbw::pyval::{decline, is_py_space, py_float_from_str, truthy, R};

const FFMPEG_CDG_BASE_OFFSET_MS: i64 = 750;
const MPV_CDG_BASE_OFFSET_MS: i64 = -50;
const LEGACY_SHARED_BASE_MS: i64 = 600;
const EVIDENCE_FLAGS: [&str; 10] = [
    "queue_shadow", "plan_shadow", "settings_shadow", "store_shadow", "wire_shadow", "publish_live_state",
    "transport_drive_media_end", "transport_drive_watchdog", "start_shadow", "plan_shadow_live",
];

pub type Settings = Map<String, Value>;

fn flag(s: &Settings, key: &str) -> bool {
    s.get(key).map(truthy).unwrap_or(false)
}

fn remove(s: &mut Settings, key: &str) -> bool {
    s.shift_remove(key).is_some()
}

/// `int(text)` for ASCII text: only the six ASCII whitespace characters are stripped, one optional sign, digits with single underscores
/// between digits. `Ok(None)` is a ValueError.
fn py_int_from_str(s: &str) -> R<Option<i64>> {
    if !s.is_ascii() {
        return decline("non-ASCII numeric text");
    }
    let t = s.trim_matches(|c| matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c'));
    let (neg, digits) = match t.as_bytes().first() {
        Some(b'-') => (true, &t[1..]),
        Some(b'+') => (false, &t[1..]),
        _ => (false, t),
    };
    let b = digits.as_bytes();
    if b.is_empty() || !b[0].is_ascii_digit() || !b[b.len() - 1].is_ascii_digit() {
        return Ok(None);
    }
    let mut value: i128 = 0;
    let mut prev_us = false;
    for &c in b {
        if c == b'_' {
            if prev_us {
                return Ok(None);
            }
            prev_us = true;
            continue;
        }
        if !c.is_ascii_digit() {
            return Ok(None);
        }
        prev_us = false;
        value = value * 10 + (c - b'0') as i128;
        if value > (1i128 << 70) {
            return decline("integer outside 64 bits");
        }
    }
    let value = if neg { -value } else { value };
    if value > i64::MAX as i128 || value < i64::MIN as i128 {
        return decline("integer outside 64 bits");
    }
    Ok(Some(value as i64))
}

/// `int(v)` where a Python exception is caught by the caller (`Ok(None)`).
fn py_int(v: &Value) -> R<Option<i64>> {
    match v {
        Value::Bool(b) => Ok(Some(*b as i64)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                return Ok(Some(i));
            }
            if n.as_u64().is_some() {
                return decline("integer outside 64 bits");
            }
            let f = n.as_f64().unwrap_or(f64::NAN);
            if !f.is_finite() {
                return Ok(None);
            }
            let t = f.trunc();
            if t >= 9_223_372_036_854_775_808.0 || t < -9_223_372_036_854_775_808.0 {
                return decline("integer outside 64 bits");
            }
            Ok(Some(t as i64))
        }
        Value::String(s) => py_int_from_str(s),
        Value::Null | Value::Array(_) | Value::Object(_) => Ok(None),
    }
}

/// `int(value or 0)` inside `try: ... except Exception: 0`.
fn int_or_zero(v: Option<&Value>) -> R<i64> {
    match v {
        None => Ok(0),
        Some(x) if !truthy(x) => Ok(0),
        Some(x) => Ok(py_int(x)?.unwrap_or(0)),
    }
}

/// `float(v)` where a Python exception is caught by the caller.
fn py_float(v: &Value) -> R<Option<f64>> {
    match v {
        Value::Bool(b) => Ok(Some(if *b { 1.0 } else { 0.0 })),
        Value::Number(n) => Ok(Some(n.as_f64().unwrap_or(f64::NAN))),
        Value::String(s) => py_float_from_str(s),
        Value::Null | Value::Array(_) | Value::Object(_) => Ok(None),
    }
}

fn clamp3000(x: i64) -> i64 {
    x.max(-3000).min(3000)
}

fn int_value(x: i64) -> Value {
    Value::from(x)
}

/// `_migrate_rust_evidence_defaults` (module level in 0.2.18.1.py).
fn migrate_rust_evidence_defaults(s: &mut Settings) -> bool {
    let mut changed = false;
    if !flag(s, "rust_evidence_defaults_migrated") {
        for key in EVIDENCE_FLAGS {
            s.insert(key.into(), Value::Bool(true));
        }
        let is_off = match s.get("shell_commands") {
            None => true,
            Some(v) if !truthy(v) => true,
            Some(Value::String(t)) => t.trim_matches(is_py_space).eq_ignore_ascii_case("off"),
            Some(_) => false,
        };
        if is_off {
            s.insert("shell_commands".into(), Value::String("dry_run".into()));
        }
        s.insert("rust_evidence_defaults_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "waitlist_snapshot_default_on_migrated") {
        s.insert("waitlist_snapshot".into(), Value::Bool(true));
        s.insert("waitlist_snapshot_default_on_migrated".into(), Value::Bool(true));
        changed = true;
    }
    changed
}

pub fn migrate(s: &mut Settings) -> R<bool> {
    let mut changed = false;
    for key in ["performance_mode", "safe_mode", "mp4_max_height"] {
        if remove(s, key) {
            changed = true;
        }
    }
    if !flag(s, "transport_shadow_default_on_migrated") {
        s.insert("transport_shadow".into(), Value::Bool(true));
        s.insert("transport_shadow_default_on_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if migrate_rust_evidence_defaults(s) {
        changed = true;
    }
    if !flag(s, "performance_debug_default_migrated") {
        if s.get("performance_debug_enabled").map(truthy).unwrap_or(true) {
            s.insert("performance_debug_enabled".into(), Value::Bool(false));
        }
        s.insert("performance_debug_default_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "show_safe_stall_diagnostics_migrated") {
        s.insert("stall_event_attribution".into(), Value::Bool(false));
        s.insert("show_safe_stall_diagnostics_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "stall_stack_capture_off_migrated") {
        s.insert("stall_stack_capture".into(), Value::Bool(false));
        s.insert("stall_stack_capture_safe_migrated".into(), Value::Bool(true));
        s.insert("stall_stack_capture_off_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "ffmpeg_cdg_timing_migrated") {
        let raw = s.get("cdg_timing_offset_ms").or_else(|| s.get("video_timing_offset_ms"));
        let old_total = int_or_zero(raw)?;
        let v = if old_total != 0 { old_total - FFMPEG_CDG_BASE_OFFSET_MS } else { 0 };
        s.insert("cdg_timing_offset_ms".into(), int_value(v));
        s.insert("ffmpeg_cdg_timing_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "ffmpeg_cdg_750_baseline_migrated") {
        let saved = int_or_zero(s.get("cdg_timing_offset_ms"))?;
        if saved == 150 {
            s.insert("cdg_timing_offset_ms".into(), int_value(0));
        }
        s.insert("ffmpeg_cdg_750_baseline_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "cdg_timing_engine_split_migrated") {
        let saved = int_or_zero(s.get("cdg_timing_offset_ms"))?;
        if saved != 0 {
            // the engine is always "mpv" (KaraokeApp._cdg_timing_engine)
            s.insert("cdg_timing_offset_mpv_ms".into(), int_value(clamp3000(LEGACY_SHARED_BASE_MS + saved - MPV_CDG_BASE_OFFSET_MS)));
            s.insert("cdg_timing_offset_ms".into(), int_value(0));
        }
        s.insert("cdg_timing_engine_split_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "mpv_cdg_minus_50_baseline_migrated") {
        if s.contains_key("cdg_timing_offset_mpv_ms") {
            let saved = int_or_zero(s.get("cdg_timing_offset_mpv_ms"))?;
            s.insert("cdg_timing_offset_mpv_ms".into(), int_value(clamp3000(saved - 100)));
        }
        s.insert("mpv_cdg_minus_50_baseline_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "mpv_mp4_100_baseline_migrated") {
        if s.contains_key("mp4_timing_offset_mpv_ms") {
            let saved = int_or_zero(s.get("mp4_timing_offset_mpv_ms"))?;
            s.insert("mp4_timing_offset_mpv_ms".into(), int_value(clamp3000(saved - 100)));
        }
        s.insert("mpv_mp4_100_baseline_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "mpv_mp4_zero_baseline_migrated") {
        if flag(s, "mpv_mp4_100_baseline_migrated") && s.contains_key("mp4_timing_offset_mpv_ms") {
            let saved = int_or_zero(s.get("mp4_timing_offset_mpv_ms"))?;
            s.insert("mp4_timing_offset_mpv_ms".into(), int_value(clamp3000(saved + 100)));
        }
        s.insert("mpv_mp4_zero_baseline_migrated".into(), Value::Bool(true));
        changed = true;
    }
    if !flag(s, "end_silence_threshold_2_5_migrated") {
        let old = match s.get("end_silence_trim_threshold_sec") {
            None => 6.0,
            Some(v) => py_float(v)?.unwrap_or(6.0),
        };
        if old == 5.0 || old == 6.0 {
            s.insert("end_silence_trim_threshold_sec".into(), Value::from(2.5));
        }
        s.insert("end_silence_threshold_2_5_migrated".into(), Value::Bool(true));
        changed = true;
    }
    for key in ["karafun_transparent_handoff", "karafun_windowed_reveal", "karafun_transparent_rehearsal_reset"] {
        if remove(s, key) {
            changed = true;
        }
    }
    Ok(changed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn run(v: Value) -> (Value, bool) {
        let mut m = v.as_object().unwrap().clone();
        let c = migrate(&mut m).unwrap();
        (Value::Object(m), c)
    }

    #[test]
    fn a_fresh_profile_gets_every_marker_and_the_safe_defaults() {
        let (v, changed) = run(json!({}));
        assert!(changed);
        assert_eq!(v["transport_shadow"], json!(true));
        assert_eq!(v["shell_commands"], json!("dry_run"));
        assert_eq!(v["cdg_timing_offset_ms"], json!(0));
        assert_eq!(v["stall_stack_capture"], json!(false));
        assert!(v["end_silence_threshold_2_5_migrated"].as_bool().unwrap());
    }

    #[test]
    fn a_calibrated_old_cdg_offset_is_carried_through_every_baseline() {
        // old total 900 -> fine 150 (900-750) -> the 750 baseline zeroes exactly 150 -> nothing left for the mpv split
        let (v, _) = run(json!({"cdg_timing_offset_ms": 900}));
        assert_eq!(v["cdg_timing_offset_ms"], json!(0));
        assert!(v.get("cdg_timing_offset_mpv_ms").is_none());
        // old total 850 -> fine 100 -> mpv key 600+100+50 = 750 -> minus 100 = 650
        let (v, _) = run(json!({"cdg_timing_offset_ms": 850}));
        assert_eq!(v["cdg_timing_offset_mpv_ms"], json!(650));
        assert_eq!(v["cdg_timing_offset_ms"], json!(0));
    }

    #[test]
    fn mp4_offset_goes_down_100_then_back_up_100_in_the_same_pass() {
        let (v, _) = run(json!({"mp4_timing_offset_mpv_ms": 40}));
        assert_eq!(v["mp4_timing_offset_mpv_ms"], json!(40));
    }

    #[test]
    fn only_the_legacy_end_silence_thresholds_move_and_a_none_counts_as_six() {
        assert_eq!(run(json!({"end_silence_trim_threshold_sec": 5})).0["end_silence_trim_threshold_sec"], json!(2.5));
        assert_eq!(run(json!({"end_silence_trim_threshold_sec": 4.0})).0["end_silence_trim_threshold_sec"], json!(4.0));
        assert_eq!(run(json!({"end_silence_trim_threshold_sec": null})).0["end_silence_trim_threshold_sec"], json!(2.5));
        // an absent key reads the default 6.0, a legacy value, so a fresh profile gets 2.5 written
        assert_eq!(run(json!({})).0["end_silence_trim_threshold_sec"], json!(2.5));
    }

    #[test]
    fn a_marked_profile_is_left_alone() {
        let all = json!({
            "transport_shadow_default_on_migrated": true, "rust_evidence_defaults_migrated": true, "waitlist_snapshot_default_on_migrated": true,
            "performance_debug_default_migrated": true, "show_safe_stall_diagnostics_migrated": true, "stall_stack_capture_off_migrated": true,
            "ffmpeg_cdg_timing_migrated": true, "ffmpeg_cdg_750_baseline_migrated": true, "cdg_timing_engine_split_migrated": true,
            "mpv_cdg_minus_50_baseline_migrated": true, "mpv_mp4_100_baseline_migrated": true, "mpv_mp4_zero_baseline_migrated": true,
            "end_silence_threshold_2_5_migrated": true, "transport_shadow": false, "cdg_timing_offset_ms": 77,
        });
        let (v, changed) = run(all.clone());
        assert!(!changed);
        assert_eq!(v, all);
    }

    #[test]
    fn retired_and_obsolete_keys_are_removed_without_disturbing_order() {
        let (v, changed) = run(json!({"a": 1, "safe_mode": true, "b": 2, "karafun_windowed_reveal": 1, "c": 3}));
        assert!(changed);
        let keys: Vec<&String> = v.as_object().unwrap().keys().collect();
        assert_eq!(&keys[..3], &["a", "b", "c"]);
    }

    #[test]
    fn python_int_text_rules() {
        assert_eq!(py_int_from_str(" 12 ").unwrap(), Some(12));
        assert_eq!(py_int_from_str("1_0").unwrap(), Some(10));
        assert_eq!(py_int_from_str("1__0").unwrap(), None);
        assert_eq!(py_int_from_str("_1").unwrap(), None);
        assert_eq!(py_int_from_str("1e3").unwrap(), None);
        assert_eq!(py_int_from_str("0x10").unwrap(), None);
        assert_eq!(py_int_from_str("-7").unwrap(), Some(-7));
        assert_eq!(py_int_from_str("007").unwrap(), Some(7));
        assert!(py_int_from_str("99999999999999999999999").is_err());
    }
}
