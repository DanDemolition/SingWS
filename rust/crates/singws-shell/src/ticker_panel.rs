//! QObject exposed to QML as `TickerPanel`: a READ-ONLY look at the text the audience ticker shows. The queue comes from `queue.json` and the two ticker keys from
//! `settings.json` (reloaded on every refresh); all rules live in the toolkit-free `singws-uimodel::ticker`, which is proven equal to the app's own ticker text.
//! The app's "now singing" parts are not on disk, so a custom message that uses them shows the same text the app would show with nobody singing.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        #[qproperty(QString, text)]
        #[qproperty(QString, message)]
        #[qproperty(QString, singers_json, cxx_name = "singersJson")]
        #[qproperty(QString, status)]
        type TickerPanel = super::TickerPanelRust;

        #[qinvokable]
        fn refresh(self: Pin<&mut TickerPanel>);
    }
}

use core::pin::Pin;
use cxx_qt::CxxQtType;
use cxx_qt_lib::QString;
use serde_json::Value;
use singws_uimodel::ticker;

pub struct TickerPanelRust {
    text: QString,
    message: QString,
    singers_json: QString,
    status: QString,
    dir: String,
}

impl Default for TickerPanelRust {
    fn default() -> Self {
        let dir = std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default();
        TickerPanelRust { text: QString::from(""), message: QString::from(""), singers_json: QString::from("[]"), status: QString::from(""), dir }
    }
}

fn read_json(path: &str, missing: Value) -> Result<Value, String> {
    match std::fs::read_to_string(path) {
        Ok(t) => serde_json::from_str::<Value>(&t).map_err(|e| format!("{path}: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(missing),
        Err(e) => Err(format!("{path}: {e}")),
    }
}

/// (ticker text, custom message, singer names) for the files in `dir`
pub fn load(dir: &str) -> Result<(String, String, Vec<String>), String> {
    let queue = read_json(&format!("{dir}/queue.json"), Value::Array(vec![]))?;
    let settings = read_json(&format!("{dir}/settings.json"), Value::Object(Default::default()))?;
    let v = ticker::view(&queue, &settings, &Value::Null)?;
    Ok((v.text, v.message, v.singers))
}

impl qobject::TickerPanel {
    fn refresh(mut self: Pin<&mut Self>) {
        let dir = self.rust().dir.clone();
        match load(&dir) {
            Ok((text, message, singers)) => {
                let n = singers.len();
                self.as_mut().set_text(QString::from(text.as_str()));
                self.as_mut().set_message(QString::from(message.as_str()));
                self.as_mut().set_singers_json(QString::from(Value::from(singers).to_string().as_str()));
                let status = if n == 0 && message.is_empty() { "Nothing to show: nobody is waiting in the queue".to_string() } else { format!("{n} singer{} in the ticker   •   what the audience sees", if n == 1 { "" } else { "s" }) };
                self.as_mut().set_status(QString::from(status.as_str()));
            }
            Err(e) => {
                self.as_mut().set_text(QString::from(""));
                self.as_mut().set_message(QString::from(""));
                self.as_mut().set_singers_json(QString::from("[]"));
                self.as_mut().set_status(QString::from(format!("Ticker unavailable: {e}").as_str()));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> String {
        let d = std::env::temp_dir().join(format!("singws-ticker-panel-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.to_string_lossy().to_string()
    }

    #[test]
    fn the_text_is_the_numbered_singers_with_the_custom_message_in_front() {
        let d = dir("a");
        std::fs::write(format!("{d}/queue.json"), r#"[{"name":"Ann","songs":[{"title":"x"}]},{"name":"Skip","skipped":true,"songs":[{}]},{"name":"Bo","songs":[{"title":"y"}]}]"#).unwrap();
        std::fs::write(format!("{d}/settings.json"), r#"{"ticker_custom_enabled":true,"ticker_custom_message":"Tip your host"}"#).unwrap();
        let (text, message, singers) = load(&d).unwrap();
        assert_eq!((text.as_str(), message.as_str(), singers), ("Tip your host   |   1. Ann   |   2. Bo", "Tip your host", vec!["Ann".to_string(), "Bo".to_string()]));
    }

    #[test]
    fn missing_files_mean_an_empty_ticker_and_broken_files_an_error() {
        let d = dir("b");
        assert_eq!(load(&d).unwrap(), (String::new(), String::new(), vec![]));
        std::fs::write(format!("{d}/queue.json"), "{not json").unwrap();
        assert!(load(&d).is_err());
    }
}
