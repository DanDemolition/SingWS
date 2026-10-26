//! Panel 5 of the Rust-owned UI: a READ-ONLY look at `settings.json`. There is no Python counterpart to match (the app edits settings through its own dialogs);
//! the rules here are about SAFETY: a value is never shown when its key names a secret (API key, PIN, token, password, ...), and nested lists and objects
//! are summarised by size, never printed, because a secret can hide inside them.

use serde_json::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub key: String,
    pub value: String,
    pub hidden: bool,
}

/// Words that mark a key as holding a secret. A key is split on every non-alphanumeric character and compared word by word, so `api_key` and `host_controls_pin`
/// are hidden while `monkey_mode` and `shipping` are not. Over-hiding is accepted (`ui_queue_pin_current` is a UI toggle that is hidden too).
const SECRET_WORDS: &[&str] = &[
    "key", "keys", "secret", "secrets", "token", "tokens", "pin", "pins", "pass", "password", "passwd", "passphrase", "auth", "authorization", "cookie", "cookies",
    "credential", "credentials", "license", "licence", "hmac", "salt", "apikey",
];

pub fn is_secret_key(key: &str) -> bool {
    key.to_lowercase().split(|c: char| !c.is_alphanumeric()).any(|w| SECRET_WORDS.contains(&w))
}

fn clip(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut t: String = s.chars().take(max).collect();
        t.push('…');
        t
    }
}

pub fn display(v: &Value) -> String {
    match v {
        Value::Null => "none".into(),
        Value::Bool(b) => if *b { "on" } else { "off" }.into(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => if s.is_empty() { "(empty)".into() } else { clip(s, 120) },
        Value::Array(a) => format!("list of {}", a.len()),
        Value::Object(o) => format!("{} fields", o.len()),
    }
}

/// The rows for the search text (case-insensitive substring of the key; empty text shows all), in the file's own order.
pub fn rows(settings: &Value, query: &str) -> Result<Vec<Row>, String> {
    let obj = settings.as_object().ok_or("settings.json is not an object")?;
    let q = query.trim().to_lowercase();
    Ok(obj
        .iter()
        .filter(|(k, _)| q.is_empty() || k.to_lowercase().contains(&q))
        .map(|(k, v)| {
            let hidden = is_secret_key(k);
            Row { key: k.clone(), value: if hidden { "hidden".into() } else { display(v) }, hidden }
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn secret_keys_are_recognised_by_whole_words() {
        for k in ["api_key", "host_controls_pin", "relay_token", "smtp_password", "Authorization", "session-cookie", "license.key", "apiKey", "ui_queue_pin_current", "client_secret"] {
            assert!(is_secret_key(k), "{k} must be hidden");
        }
        for k in ["monkey_mode", "shipping", "base_url", "ticker_size_index", "bg_volume", "keyboard_shortcuts", "pinned_singers", "passenger_count"] {
            assert!(!is_secret_key(k), "{k} must be shown");
        }
    }

    #[test]
    fn a_hidden_value_never_reaches_the_row_even_when_it_is_nested_or_long() {
        let s = json!({"api_key": "sk-live-123456", "host_controls_pin": 4321, "tokens": {"a": "zzz"}, "base_url": "https://wskar.com", "nested": {"api_key": "inner-secret"}});
        let r = rows(&s, "").unwrap();
        let all = r.iter().map(|x| format!("{}={}", x.key, x.value)).collect::<Vec<_>>().join("|");
        for leak in ["sk-live", "4321", "zzz", "inner-secret"] {
            assert!(!all.contains(leak), "{leak} leaked: {all}");
        }
        assert_eq!(r[0], Row { key: "api_key".into(), value: "hidden".into(), hidden: true });
        assert_eq!(r[3], Row { key: "base_url".into(), value: "https://wskar.com".into(), hidden: false });
        assert_eq!(r[4].value, "1 fields", "a nested object is summarised by size, never printed");
    }

    #[test]
    fn values_are_shown_plainly_and_long_text_is_clipped() {
        let s = json!({"a": true, "b": false, "c": 0.65, "d": 4, "e": "", "f": null, "g": [1, 2, 3], "h": "x".repeat(300)});
        let r = rows(&s, "").unwrap();
        let v: Vec<&str> = r.iter().map(|x| x.value.as_str()).collect();
        assert_eq!(&v[..7], ["on", "off", "0.65", "4", "(empty)", "none", "list of 3"]);
        assert_eq!(r[7].value.chars().count(), 121);
        assert!(r[7].value.ends_with('…'));
    }

    #[test]
    fn the_search_is_a_case_insensitive_key_substring_and_keeps_file_order() {
        let s = json!({"bg_volume": 0.5, "Ticker_Size": 3, "bg_enabled": true, "other": 1});
        let keys = |q: &str| rows(&s, q).unwrap().into_iter().map(|r| r.key).collect::<Vec<_>>();
        assert_eq!(keys("BG_"), vec!["bg_volume", "bg_enabled"]);
        assert_eq!(keys(" ticker "), vec!["Ticker_Size"]);
        assert_eq!(keys("").len(), 4);
        assert!(keys("zzz").is_empty());
    }

    #[test]
    fn a_file_that_is_not_an_object_is_an_error() {
        assert!(rows(&json!([1]), "").is_err());
    }
}
