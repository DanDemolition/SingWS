//! Ticker page: the text the audience ticker shows, from `queue.json` + the two ticker keys of `settings.json` (reloaded on every refresh).
//! The app's "now singing" parts are not on disk, so a custom message that uses them shows what the app shows with nobody singing.
use serde_json::Value;
use singws_uimodel::ticker;

#[derive(Debug, Clone, PartialEq, Default)]
pub struct TickerView {
    pub text: String,
    pub message: String,
    pub singers: Vec<String>,
    pub status: String,
}

fn read_json(path: &str, missing: Value) -> Result<Value, String> {
    match std::fs::read_to_string(path) {
        Ok(t) => serde_json::from_str::<Value>(&t).map_err(|e| format!("{path}: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(missing),
        Err(e) => Err(format!("{path}: {e}")),
    }
}

/// The page for the files in `dir`; a broken file gives an empty page and a status that says why (never a panic)
pub fn load(dir: &str) -> TickerView {
    let loaded = (|| {
        let queue = read_json(&format!("{dir}/queue.json"), Value::Array(vec![]))?;
        let settings = read_json(&format!("{dir}/settings.json"), Value::Object(Default::default()))?;
        ticker::view(&queue, &settings, &Value::Null)
    })();
    match loaded {
        Ok(v) => {
            let n = v.singers.len();
            let status = if n == 0 && v.message.is_empty() {
                "Nothing to show: nobody is waiting in the queue".to_string()
            } else {
                format!("{n} singer{} in the ticker   •   what the audience sees", if n == 1 { "" } else { "s" })
            };
            TickerView { text: v.text, message: v.message, singers: v.singers, status }
        }
        Err(e) => TickerView { status: format!("Ticker unavailable: {e}"), ..Default::default() },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> String {
        let d = std::env::temp_dir().join(format!("singws-slint-ticker-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.to_string_lossy().to_string()
    }

    #[test]
    fn the_text_is_the_numbered_singers_with_the_custom_message_in_front() {
        let d = dir("a");
        std::fs::write(format!("{d}/queue.json"), r#"[{"name":"Ann","songs":[{"title":"x"}]},{"name":"Skip","skipped":true,"songs":[{}]},{"name":"Bo","songs":[{"title":"y"}]}]"#).unwrap();
        std::fs::write(format!("{d}/settings.json"), r#"{"ticker_custom_enabled":true,"ticker_custom_message":"Tip your host"}"#).unwrap();
        let v = load(&d);
        assert_eq!((v.text.as_str(), v.message.as_str(), v.singers), ("Tip your host   |   1. Ann   |   2. Bo", "Tip your host", vec!["Ann".to_string(), "Bo".to_string()]));
        assert!(v.status.starts_with("2 singers"));
    }

    #[test]
    fn missing_files_mean_an_empty_ticker_and_broken_files_an_error_status() {
        let d = dir("b");
        let v = load(&d);
        assert_eq!((v.text.as_str(), v.singers.len()), ("", 0));
        assert!(v.status.starts_with("Nothing to show"));
        std::fs::write(format!("{d}/queue.json"), "{not json").unwrap();
        let v = load(&d);
        assert!(v.status.starts_with("Ticker unavailable") && v.text.is_empty());
    }
}
