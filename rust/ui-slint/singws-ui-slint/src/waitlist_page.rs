//! Waitlist page: the waiting-for-add requests from `waitlist_snapshot.json` (written by the app when its `waitlist_snapshot` setting is on), reloaded on every refresh.
//! Sections and row text come from `singws-uimodel::waitlist`. Read-only: no Add/Remove/Edit (those need the app's command channel, a later slice); the request's key and
//! tempo are shown as a tag, read the way the app's Edit dialog reads them.
use serde_json::Value;
use singws_uimodel::waitlist;
use std::time::SystemTime;

/// older than this the status says the snapshot may be stale (the app is closed, or the setting was turned off)
pub const STALE_SECS: u64 = 120;

/// One line of the flat list the page draws: a section header, or a request row
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Item {
    pub header: bool,
    /// section key: pending_acceptance | waitlist | removed
    pub section: String,
    pub title: String,
    pub helper: String,
    pub text: String,
    pub first_time: bool,
    /// e.g. "key +3   tempo -4", empty when both are 0
    pub tag: String,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct WaitlistView {
    pub items: Vec<Item>,
    pub status: String,
}

/// key and tempo as the app's own Edit dialog reads them (`int(req.get("key") or req.get("key_change") or 0)`, `int(req.get("tempo") or 0)`); not a whole number counts as 0
pub fn key_tempo(snapshot: &Value, request_id: i64) -> (i64, i64) {
    let raw = snapshot.get("waiting").and_then(Value::as_object).and_then(|m| m.get(&request_id.to_string()));
    let whole = |v: Option<&Value>| v.and_then(Value::as_i64).filter(|n| *n != 0);
    let key = raw.and_then(|r| whole(r.get("key")).or_else(|| whole(r.get("key_change")))).unwrap_or(0);
    let tempo = raw.and_then(|r| whole(r.get("tempo"))).unwrap_or(0);
    (key, tempo)
}

fn tag(key: i64, tempo: i64) -> String {
    let mut parts = vec![];
    if key != 0 {
        parts.push(format!("key {key:+}"));
    }
    if tempo != 0 {
        parts.push(format!("tempo {tempo:+}"));
    }
    parts.join("   ")
}

fn unavailable(status: String) -> WaitlistView {
    WaitlistView { items: vec![], status }
}

/// The page for the snapshot in `dir`; `now` is the clock the file's age is measured against
pub fn load(dir: &str, now: SystemTime) -> WaitlistView {
    let path = format!("{dir}/waitlist_snapshot.json");
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            return unavailable("No waitlist snapshot yet. Turn on \"waitlist_snapshot\" in the app's settings; it then writes the file whenever the waitlist changes.".into());
        }
        Err(e) => return unavailable(format!("Waitlist unavailable: {e}")),
    };
    let age = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| now.duration_since(t).ok()).map(|d| d.as_secs());
    let snapshot: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return unavailable(format!("Waitlist unavailable: the snapshot is not valid JSON ({e})")),
    };
    let sections = match waitlist::sections_from_snapshot(&snapshot) {
        Ok(s) => s,
        Err(e) => return unavailable(format!("Waitlist unavailable: {e}")),
    };
    let mut items = vec![];
    let mut rows = 0usize;
    for s in &sections {
        items.push(Item { header: true, section: s.key.to_string(), title: format!("{} ({})", s.title, s.rows.len()), helper: s.helper.unwrap_or("").to_string(), ..Default::default() });
        for r in &s.rows {
            rows += 1;
            let (k, t) = key_tempo(&snapshot, r.request_id);
            items.push(Item { section: s.key.to_string(), text: r.text.clone(), first_time: r.first_time_singer, tag: tag(k, t), ..Default::default() });
        }
    }
    let mut status = if rows == 0 { "Nobody is waiting".to_string() } else { format!("{rows} request{}", if rows == 1 { "" } else { "s" }) };
    match age {
        Some(a) if a > STALE_SECS => status.push_str(&format!("   •   snapshot is {}m old, the app may be closed", a / 60)),
        Some(a) => status.push_str(&format!("   •   updated {a}s ago")),
        None => {}
    }
    WaitlistView { items, status }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::time::Duration;

    fn dir(name: &str) -> String {
        let d = std::env::temp_dir().join(format!("singws-slint-waitlist-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.to_string_lossy().to_string()
    }

    const SNAP: &str = r#"{"version":1,"pending_acceptance":null,"waiting":{"5":{"request_id":5,"singer":"Ann","artist":"Queen","title":"Radio","request_time":1792000000,"key":3},
        "6":{"request_id":6,"singer":"Bo","artist":"A","title":"B"}},"show_removed":false,"removed":{},"local_ids":[6],"accepted_ids":[],"pending_accepted_ids":[],"first_time_names":["Ann"]}"#;

    #[test]
    fn the_snapshot_becomes_a_header_and_rows_with_a_status() {
        let d = dir("a");
        std::fs::write(format!("{d}/waitlist_snapshot.json"), SNAP).unwrap();
        let v = load(&d, SystemTime::now());
        assert_eq!(v.items.len(), 2, "one header and the one waiting request (6 is queued already)");
        assert!(v.items[0].header && v.items[0].section == "waitlist" && v.items[0].title.ends_with("(1)"));
        assert!(v.items[1].text.contains("Ann") && v.items[1].first_time);
        assert_eq!(v.items[1].tag, "key +3");
        assert!(v.status.starts_with("1 request   •   updated "), "{}", v.status);
    }

    #[test]
    fn an_old_snapshot_is_called_stale() {
        let d = dir("b");
        std::fs::write(format!("{d}/waitlist_snapshot.json"), SNAP).unwrap();
        let later = SystemTime::now() + Duration::from_secs(600);
        assert!(load(&d, later).status.contains("10m old"), "{}", load(&d, later).status);
    }

    #[test]
    fn a_missing_or_broken_snapshot_says_so_instead_of_failing() {
        let d = dir("c");
        let v = load(&d, SystemTime::now());
        assert!(v.items.is_empty() && v.status.contains("waitlist_snapshot"));
        std::fs::write(format!("{d}/waitlist_snapshot.json"), "{nope").unwrap();
        assert!(load(&d, SystemTime::now()).status.contains("not valid JSON"));
        std::fs::write(format!("{d}/waitlist_snapshot.json"), "[1]").unwrap();
        assert!(load(&d, SystemTime::now()).status.starts_with("Waitlist unavailable"));
        std::fs::write(format!("{d}/waitlist_snapshot.json"), r#"{"waiting":{},"removed":{}}"#).unwrap();
        assert!(load(&d, SystemTime::now()).status.starts_with("Nobody is waiting"));
    }

    #[test]
    fn key_and_tempo_are_read_like_the_edit_dialog_reads_them() {
        let snap = json!({"waiting": {"5": {"key": 0, "key_change": 3, "tempo": -4}, "6": {"key": "x", "tempo": 2.5}, "7": {"key": -2}}});
        assert_eq!(key_tempo(&snap, 5), (3, -4), "key 0 falls through to key_change");
        assert_eq!(key_tempo(&snap, 6), (0, 0), "not a whole number counts as 0");
        assert_eq!(key_tempo(&snap, 7), (-2, 0));
        assert_eq!(key_tempo(&snap, 99), (0, 0));
        assert_eq!((tag(0, 0), tag(-2, 5)), (String::new(), "key -2   tempo +5".to_string()));
    }
}
