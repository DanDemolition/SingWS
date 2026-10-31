//! QObject exposed to QML as `ChatPanel`: draws the app's room chat summary (the relay's `chat` message, held by the shared ControlPanel) with the toolkit-free rules in
//! `singws_uimodel::chat`. Stateless and READ-ONLY: it turns JSON into rows; speaking goes through the `chat.say` command like every other host action.

#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!("cxx-qt-lib/qstring.h");
        type QString = cxx_qt_lib::QString;
    }

    extern "RustQt" {
        #[qobject]
        #[qml_element]
        type ChatPanel = super::ChatPanelRust;

        /// The Chat tab for `chat_json` (the summary, or "null" before the app has sent one) as JSON. `current` is "group" | "dm" | "host"; `utc_offset_min` the local time zone.
        #[qinvokable]
        fn render(self: &ChatPanel, chat_json: &QString, current: &QString, utc_offset_min: i32) -> QString;

        /// `--chat-demo FILE` (screenshots and rehearsal without an app): the file's text, or "null" when it cannot be read.
        #[qinvokable]
        #[cxx_name = "readDemo"]
        fn read_demo(self: &ChatPanel, path: &QString) -> QString;
    }
}

use cxx_qt_lib::QString;
use serde_json::Value;
use singws_uimodel::chat;

#[derive(Default)]
pub struct ChatPanelRust;

/// The Chat tab as JSON text; text that is not JSON counts as "no summary yet".
pub fn render_json(chat_json: &str, current: &str, utc_offset_min: i64) -> String {
    let summary: Value = serde_json::from_str(chat_json).unwrap_or(Value::Null);
    chat::view(&summary, current, utc_offset_min).to_string()
}

impl qobject::ChatPanel {
    fn read_demo(&self, path: &QString) -> QString {
        QString::from(std::fs::read_to_string(path.to_string()).unwrap_or_else(|_| "null".into()).as_str())
    }

    fn render(&self, chat_json: &QString, current: &QString, utc_offset_min: i32) -> QString {
        QString::from(render_json(&chat_json.to_string(), &current.to_string(), utc_offset_min as i64).as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_summary_becomes_the_tab_and_junk_becomes_an_empty_one() {
        let v: Value = serde_json::from_str(&render_json(r#"{"group_enabled":true,"unread":{"group":2},"group":[{"id":1,"from":"Ann","text":"hi","at":1793405109}]}"#, "dm", 0)).unwrap();
        assert_eq!(v["tabs"][0]["label"], "Everyone (2 new)");
        assert_eq!(v["group"][0]["stamp"], "12:05:09 AM");
        for junk in ["", "null", "nope", "[1]", "\"x\""] {
            let v: Value = serde_json::from_str(&render_json(junk, "group", 0)).unwrap();
            assert_eq!(v["group"], serde_json::json!([]), "{junk}");
            assert!(v["status"].as_str().unwrap().starts_with("Room chat is off"), "{junk}");
        }
    }
}
