//! Panel 6 of the Rust-owned UI: the two 10-band equalisers (background music and karaoke), READ-ONLY, from `settings.json` exactly as the app reads them in
//! `KaraokeApp._ensure_eq_engines`: the first ten entries of `eq_bgm` / `eq_karaoke` are the gains in dB (a shorter list leaves the rest at 0, an empty or non-list value
//! leaves all at 0); every entry goes through Python's `float()`, so a number or a true/false is fine and anything else (null, text, a list) makes the app give up on BOTH
//! equalisers for the session; an equaliser is on when its `*_enabled` setting is true OR its gains are not flat (the app switches such a one on by itself).
//! A text entry that Python would still accept ("3.5") is declined here rather than guessed.

use serde_json::Value;
use singws_dbw::pyval::truthy;

pub const BANDS_HZ: [f64; 10] = [31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0];

#[derive(Debug, Clone, PartialEq)]
pub struct Eq {
    pub gains: [f64; 10],
    pub enabled: bool,
    /// every |gain| < 0.05 dB (the app's `is_flat`)
    pub flat: bool,
    /// switched on only because the gains are not flat (the setting itself says off)
    pub auto_enabled: bool,
}

fn gains(settings: &Value, key: &str) -> Result<[f64; 10], String> {
    let mut out = [0.0f64; 10];
    let Some(list) = settings.get(key).and_then(Value::as_array) else { return Ok(out) };
    for (i, v) in list.iter().take(10).enumerate() {
        out[i] = match v {
            Value::Number(n) => {
                let g = n.as_f64().ok_or("a gain is not a finite number")?;
                // far beyond any real equaliser: the app's own filter maths overflows somewhere past about 12,000 dB and makes both unavailable; not reproduced, declined instead
                if g.abs() > 1000.0 {
                    return Err(format!("{key}[{i}] is far outside any real gain (declined)"));
                }
                g
            }
            Value::Bool(b) => f64::from(u8::from(*b)),
            Value::String(_) => return Err(format!("{key}[{i}] is text (declined: Python's float() would parse some text)")),
            _ => return Err(format!("{key}[{i}] is not a number (the app disables both equalisers)")),
        };
    }
    Ok(out)
}

fn one(settings: &Value, list_key: &str, enabled_key: &str) -> Result<Eq, String> {
    let g = gains(settings, list_key)?;
    let flat = g.iter().all(|x| x.abs() < 0.05);
    let setting_on = settings.get(enabled_key).is_some_and(truthy);
    Ok(Eq { gains: g, enabled: setting_on || !flat, flat, auto_enabled: !setting_on && !flat })
}

/// (background music, karaoke). The app builds both in one step, so one bad entry in either list makes both unavailable.
pub fn read(settings: &Value) -> Result<(Eq, Eq), String> {
    let obj = settings.as_object().ok_or("settings.json is not an object")?;
    let _ = obj;
    let kar = one(settings, "eq_karaoke", "eq_karaoke_enabled");
    let bgm = one(settings, "eq_bgm", "eq_bgm_enabled");
    match (bgm, kar) {
        (Ok(b), Ok(k)) => Ok((b, k)),
        (Err(e), _) | (_, Err(e)) => Err(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_first_ten_entries_are_the_gains_and_a_short_list_leaves_zeros() {
        let (b, k) = read(&json!({"eq_bgm": [5, 4.5, 3], "eq_karaoke": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]})).unwrap();
        assert_eq!(b.gains, [5.0, 4.5, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]);
        assert_eq!(k.gains, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], "entries beyond the tenth are ignored");
    }

    #[test]
    fn empty_missing_and_non_list_values_mean_all_zero_and_off() {
        for s in [json!({}), json!({"eq_bgm": [], "eq_karaoke": "flat"}), json!({"eq_bgm": null, "eq_karaoke": 5})] {
            let (b, k) = read(&s).unwrap();
            assert_eq!((b.gains, b.enabled, b.flat), ([0.0; 10], false, true));
            assert_eq!((k.gains, k.enabled, k.flat), ([0.0; 10], false, true));
        }
    }

    #[test]
    fn enabled_by_the_setting_or_switched_on_because_the_gains_are_not_flat() {
        let (b, k) = read(&json!({"eq_bgm": [0.04, -0.04], "eq_bgm_enabled": false, "eq_karaoke": [0, 0.05], "eq_karaoke_enabled": 0})).unwrap();
        assert_eq!((b.flat, b.enabled, b.auto_enabled), (true, false, false), "0.04 dB is still flat");
        assert_eq!((k.flat, k.enabled, k.auto_enabled), (false, true, true), "0.05 dB is not flat, so the app switches it on");
        let (b, _) = read(&json!({"eq_bgm": [0, 0], "eq_bgm_enabled": true})).unwrap();
        assert_eq!((b.flat, b.enabled, b.auto_enabled), (true, true, false), "on by setting even when flat");
        let (b, _) = read(&json!({"eq_bgm": [3], "eq_bgm_enabled": true})).unwrap();
        assert!(b.enabled && !b.auto_enabled);
        for truthy_value in [json!(1), json!("yes"), json!([0]), json!(0.5)] {
            assert!(read(&json!({"eq_bgm_enabled": truthy_value})).unwrap().0.enabled, "{truthy_value} is truthy in Python");
        }
        for falsy in [json!(0), json!(""), json!([]), json!(null), json!(false), json!(0.0)] {
            assert!(!read(&json!({"eq_bgm_enabled": falsy})).unwrap().0.enabled, "{falsy} is falsy in Python");
        }
    }

    #[test]
    fn booleans_count_as_numbers_and_anything_else_disables_both_equalisers() {
        let (b, _) = read(&json!({"eq_bgm": [true, false]})).unwrap();
        assert_eq!(&b.gains[..2], &[1.0, 0.0]);
        for bad in [json!([null]), json!([[1]]), json!([{"a": 1}]), json!([1, 2, null])] {
            assert!(read(&json!({"eq_bgm": bad.clone()})).is_err(), "{bad}");
            assert!(read(&json!({"eq_karaoke": bad})).is_err(), "one bad list makes BOTH unavailable");
        }
        assert!(read(&json!({"eq_bgm": ["3.5"]})).is_err(), "text is declined");
        assert!(read(&json!({"eq_bgm": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, null]})).is_ok(), "an unreadable entry beyond the tenth is never looked at");
        assert!(read(&json!({"eq_bgm": [1000.0]})).is_ok());
        assert!(read(&json!({"eq_bgm": [1000.5]})).is_err() && read(&json!({"eq_bgm": [-1e30]})).is_err(), "absurd gains are declined");
    }

    #[test]
    fn a_file_that_is_not_an_object_is_an_error() {
        assert!(read(&json!([1])).is_err());
    }
}
