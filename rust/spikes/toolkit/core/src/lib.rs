//! Toolkit-neutral model for the spike: reads the same files the Qt shell reads and uses the SAME `singws-uimodel` rules. Both prototypes call only this.
use serde_json::Value;
use singws_uimodel::{status, ticker, waitlist};

#[derive(Clone, Default, Debug, PartialEq)]
pub struct Row { pub id: i64, pub status: String, pub kind: String, pub text: String }
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Section { pub title: String, pub rows: Vec<Row> }
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Hero { pub kicker: String, pub singer: String, pub subtitle: String, pub remaining: String, pub format: String }
#[derive(Clone, Default, Debug, PartialEq)]
pub struct Snapshot { pub ticker_text: String, pub waitlist: Vec<Section>, pub waitlist_status: String, pub hero: Option<Hero>, pub status_line: String }

pub fn home() -> String {
    std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default()
}
fn read(path: String) -> Value { std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null) }

/// `custom_on` is the interactive control: it overrides `ticker_custom_enabled`, exactly what a Settings toggle would do.
pub fn load(dir: &str, custom_on: bool) -> Snapshot {
    let queue = read(format!("{dir}/queue.json"));
    let mut settings = read(format!("{dir}/settings.json"));
    if !settings.is_object() { settings = Value::Object(Default::default()); }
    settings["ticker_custom_enabled"] = Value::Bool(custom_on);
    let ticker_text = ticker::view(&queue, &settings, &Value::Null).map(|v| v.text).unwrap_or_else(|e| format!("ticker unavailable: {e}"));
    let snap = read(format!("{dir}/waitlist_snapshot.json"));
    let (waitlist, waitlist_status) = match waitlist::sections_from_snapshot(&snap) {
        Ok(s) => {
            let n: usize = s.iter().map(|x| x.rows.len()).sum();
            (s.iter().map(|x| Section { title: x.title.into(), rows: x.rows.iter().map(|r| Row { id: r.request_id, status: r.status_label.clone(), kind: r.status_kind.clone(), text: r.text.clone() }).collect() }).collect(), format!("{n} requests"))
        }
        Err(e) => (vec![], format!("waitlist unavailable: {e}")),
    };
    let (hero, status_line) = match status::view(&read(format!("{dir}/status_snapshot.json"))) {
        Ok(v) => (v.hero.map(|h| Hero { kicker: h.kicker, singer: h.singer, subtitle: h.subtitle, remaining: h.remaining, format: h.format_text }), v.summary),
        Err(e) => (None, format!("status unavailable: {e}")),
    };
    Snapshot { ticker_text, waitlist, waitlist_status, hero, status_line }
}
