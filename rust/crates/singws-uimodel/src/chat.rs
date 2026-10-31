//! The Chat tab's text, as a toolkit-free view model over the app's `chat` summary (`KaraokeApp._shell_chat_summary`, sent in the relay's `chat` message).
//! Equal to the pieces of the app's Chat page that decide what a row says: the tags and time stamp `_render_room_chat` draws for a message (HOST / GROUP /
//! PRIVATE ... / HELD / REMOVED / BLOCKED / REPORTED), the tab labels of `_chat_tab_labels`, and the Host chat time stamp of `_render_host_chat_transcript`.
//! READ-ONLY and safe by construction: the summary carries no URLs or media ids (a picture or GIF is the word "photo" / "gif"), and this module never builds markup.
//! Anything malformed degrades to empty values; nothing here can panic.

use serde_json::{Value, json};

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn i(v: &Value, k: &str) -> i64 {
    v.get(k).and_then(Value::as_i64).unwrap_or(0)
}

fn arr<'a>(v: &'a Value, k: &str) -> &'a [Value] {
    v.get(k).and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])
}

/// `time.strftime("%I:%M:%S %p")` (`seconds`) or `("%I:%M %p")` of a local time `utc_offset_min` minutes from UTC; "" when `at` is 0 (the app prints no stamp then).
pub fn stamp(at: i64, utc_offset_min: i64, seconds: bool) -> String {
    if at <= 0 {
        return String::new();
    }
    let local = at + utc_offset_min * 60;
    let day = local.rem_euclid(86_400);
    let (h, m, sec) = (day / 3600, day % 3600 / 60, day % 60);
    let h12 = if h % 12 == 0 { 12 } else { h % 12 };
    let ampm = if h < 12 { "AM" } else { "PM" };
    if seconds { format!("{h12:02}:{m:02}:{sec:02} {ampm}") } else { format!("{h12:02}:{m:02} {ampm}") }
}

/// What `_render_room_chat` writes between the sender and the time: the tags, in its order. `thread` is the private thread the message is in (names of its two
/// singers), `None` for the Everyone room.
pub fn tags(m: &Value, thread: Option<(&str, &str)>) -> Vec<String> {
    let mut out = Vec::new();
    let host = m.get("host").and_then(Value::as_bool).unwrap_or(false);
    let status = s(m, "status");
    if host {
        out.push("HOST".to_string());
    }
    match thread {
        Some((a, b)) if host => out.push(format!("PRIVATE ↔ {a} / {b}")),
        Some((a, b)) => {
            let from = s(m, "from");
            out.push(format!("PRIVATE → {}", if from == a { b } else { a }));
        }
        None => out.push("GROUP".to_string()),
    }
    if status == "held" {
        out.push(format!("HELD ({})", s(m, "flag")));
    }
    if status == "removed" {
        out.push("REMOVED".to_string());
    }
    if status == "blocked" {
        out.push("BLOCKED BY RECIPIENT (they never saw it)".to_string());
    }
    let reports = i(m, "reports");
    if reports > 0 {
        out.push(format!("REPORTED ×{reports}"));
    }
    out
}

fn attachment_label(m: &Value) -> &'static str {
    match m.get("attachment").and_then(Value::as_str) {
        Some("photo") => "[photo]",
        Some("gif") => "[GIF]",
        _ => "",
    }
}

/// The opaque thumbnail reference of a picture/GIF row (`photo:<digits>` or `gif:<letters, digits, - _ up to 64>`), "" for anything else or a removed row.
pub fn media_ref(m: &Value) -> String {
    let r = s(m, "media_ref");
    if s(m, "status") == "removed" { return String::new(); }
    let ok = match r.split_once(':') {
        Some(("photo", id)) => !id.is_empty() && id.len() <= 12 && id.chars().all(|c| c.is_ascii_digit()),
        Some(("gif", id)) => !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'),
        _ => false,
    };
    if ok { r } else { String::new() }
}

/// One message row. `thread`: see [`tags`]; `host_chat` rows are the Host chat transcript (no tags, stamp without seconds, "(removed)" in place of the text).
pub fn row(m: &Value, thread: Option<(&str, &str)>, host_chat: bool, utc_offset_min: i64) -> Value {
    let removed = s(m, "status") == "removed";
    let body = if removed && host_chat { "(removed)".to_string() } else { s(m, "text") };
    json!({
        "id": i(m, "id"),
        "who": s(m, "from"),
        "mine": m.get("host").and_then(Value::as_bool).unwrap_or(false),
        "tags": if host_chat { Vec::new() } else { tags(m, thread) },
        "stamp": stamp(i(m, "at"), utc_offset_min, !host_chat),
        "body": body,
        // the app shows "(removed)" alone for a removed Host chat picture, but still draws the attachment in the room views
        "attachment": if removed && host_chat { "" } else { attachment_label(m) },
        "media_ref": media_ref(m),
        "status": s(m, "status"),
    })
}

/// `_chat_tab_labels` for one tab: "Everyone (2 new)". The tab being looked at (`current`) shows no "new" count for the room tabs, like the app; Host chat always does.
pub fn tab_label(key: &str, unread: i64, current: &str) -> String {
    let name = match key {
        "group" => "Everyone",
        "dm" => "Private",
        _ => "Host chat",
    };
    let show = unread > 0 && (key == "host" || key != current);
    if show { format!("{name} ({unread} new)") } else { name.to_string() }
}

/// The whole Chat tab for `chat` (the summary). `current` is the open tab ("group" | "dm" | "host").
pub fn view(chat: &Value, current: &str, utc_offset_min: i64) -> Value {
    let unread = chat.get("unread").cloned().unwrap_or(Value::Null);
    let by = chat.get("attention_by").cloned().unwrap_or(Value::Null);
    let tabs: Vec<Value> = ["group", "dm", "host"]
        .iter()
        .map(|k| {
            let n = i(&unread, k);
            json!({"key": k, "unread": n, "attention": i(&by, k), "label": tab_label(k, n, current)})
        })
        .collect();
    let group_on = chat.get("group_enabled").and_then(Value::as_bool).unwrap_or(false);
    let dm_on = chat.get("dm_enabled").and_then(Value::as_bool).unwrap_or(false);
    let group: Vec<Value> = arr(chat, "group").iter().map(|m| row(m, None, false, utc_offset_min)).collect();
    let dm: Vec<Value> = arr(chat, "dm")
        .iter()
        .map(|t| {
            let (a, b) = (s(t, "a_name"), s(t, "b_name"));
            let (a, b) = (if a.is_empty() { s(t, "a") } else { a }, if b.is_empty() { s(t, "b") } else { b });
            json!({"title": format!("{a} ↔ {b}"), "rows": arr(t, "messages").iter().map(|m| row(m, Some((&a, &b)), false, utc_offset_min)).collect::<Vec<_>>()})
        })
        .collect();
    let host: Vec<Value> = arr(chat, "host")
        .iter()
        .map(|t| {
            let name = s(t, "singer");
            let name = if name.is_empty() { s(t, "key") } else { name };
            json!({"key": s(t, "key"), "title": name, "unread": i(t, "unread"), "rows": arr(t, "messages").iter().map(|m| row(m, None, true, utc_offset_min)).collect::<Vec<_>>()})
        })
        .collect();
    // the open tab's own count when the app sent the per-tab split (Host chat has none), else the total
    let attention = if by.is_object() { i(&by, current) } else { i(chat, "attention") };
    let more = chat.get("more").cloned().unwrap_or(Value::Null);
    let more_note = |k: &str| -> String {
        let n = i(&more, k);
        if n > 0 { format!("{n} more conversation{} in the app", if n == 1 { "" } else { "s" }) } else { String::new() }
    };
    let reachable = chat.get("reachable").and_then(Value::as_bool);
    let status = if reachable == Some(false) {
        "Room chat: could not reach the server".to_string()
    } else if !group_on && !dm_on {
        "Room chat is off. Turn it on in the web dashboard under \"Singer Chat & Screen Preview\".".to_string()
    } else if attention > 0 {
        format!("{attention} message{} attention in the app", if attention == 1 { " needs" } else { "s need" })
    } else {
        String::new()
    };
    json!({
        "tabs": tabs, "status": status, "group_enabled": group_on, "dm_enabled": dm_on,
        "group": group, "dm": dm, "host": host,
        "more": {"dm": more_note("dm"), "host": more_note("host")},
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_stamp_is_a_twelve_hour_clock_like_strftime() {
        // 2026-10-31 00:05:09 UTC
        let t = 1_793_405_109;
        assert_eq!(stamp(t, 0, true), "12:05:09 AM");
        assert_eq!(stamp(t, 0, false), "12:05 AM");
        assert_eq!(stamp(t, -420, true), "05:05:09 PM", "Pacific daylight time, the day before");
        assert_eq!(stamp(t + 12 * 3600, 0, true), "12:05:09 PM", "noon hour is 12 PM");
        assert_eq!(stamp(t + 13 * 3600, 0, false), "01:05 PM");
        assert_eq!(stamp(3600, -420, true), "06:00:00 PM", "the local time may fall before the epoch's midnight");
        assert_eq!(stamp(0, 0, true), "");
        assert_eq!(stamp(-5, 0, true), "");
    }

    #[test]
    fn tags_follow_the_apps_order() {
        let m = json!({"host": true, "status": "held", "flag": "link", "reports": 2, "from": "Host"});
        assert_eq!(tags(&m, None), ["HOST", "GROUP", "HELD (link)", "REPORTED ×2"]);
        assert_eq!(tags(&json!({"host": true, "from": "H"}), Some(("Ann", "Bo"))), ["HOST", "PRIVATE ↔ Ann / Bo"]);
        assert_eq!(tags(&json!({"from": "Ann"}), Some(("Ann", "Bo"))), ["PRIVATE → Bo"]);
        assert_eq!(tags(&json!({"from": "Bo", "status": "blocked"}), Some(("Ann", "Bo"))), ["PRIVATE → Ann", "BLOCKED BY RECIPIENT (they never saw it)"]);
        assert_eq!(tags(&json!({"status": "removed"}), None), ["GROUP", "REMOVED"]);
        assert_eq!(tags(&json!({"status": "held"}), None), ["GROUP", "HELD ()"], "the app prints the empty flag too");
        assert_eq!(tags(&json!({"reports": 0, "status": "weird"}), None), ["GROUP"]);
    }

    #[test]
    fn tab_labels_hide_the_count_on_the_open_room_tab_only() {
        assert_eq!(tab_label("group", 2, "dm"), "Everyone (2 new)");
        assert_eq!(tab_label("group", 2, "group"), "Everyone");
        assert_eq!(tab_label("dm", 0, "group"), "Private");
        assert_eq!(tab_label("host", 3, "host"), "Host chat (3 new)");
    }

    #[test]
    fn rows_carry_placeholders_and_never_markup() {
        let m = json!({"id": 4, "from": "Ann", "text": "<b>hi</b>", "attachment": "photo", "at": 1_793_405_109});
        let r = row(&m, None, false, 0);
        assert_eq!((r["body"].as_str(), r["attachment"].as_str(), r["stamp"].as_str()), (Some("<b>hi</b>"), Some("[photo]"), Some("12:05:09 AM")));
        assert_eq!(row(&json!({"attachment": "gif"}), None, false, 0)["attachment"], "[GIF]");
        assert_eq!(row(&json!({"attachment": "http://x"}), None, false, 0)["attachment"], "");
        let removed = json!({"status": "removed", "text": ""});
        assert_eq!(row(&removed, None, true, 0)["body"], "(removed)");
        let removed_pic = json!({"status": "removed", "attachment": "photo"});
        assert_eq!((row(&removed_pic, None, true, 0)["attachment"].clone(), row(&removed_pic, None, false, 0)["attachment"].clone()), (json!(""), json!("[photo]")));
        assert_eq!(row(&removed, None, false, 0)["body"], "");
        assert_eq!(row(&m, None, true, 0)["stamp"], "12:05 AM");
        assert_eq!(row(&m, None, true, 0)["tags"], json!([]));
    }

    #[test]
    fn the_view_names_the_conversations_and_says_why_it_is_empty() {
        let chat = json!({"reachable": true, "group_enabled": true, "dm_enabled": false, "attention": 2, "unread": {"group": 1, "dm": 0, "host": 4},
            "group": [{"id": 1, "from": "Ann", "text": "hi"}],
            "dm": [{"a": "ann", "b": "bo", "a_name": "Ann", "b_name": "", "messages": [{"id": 2, "from": "Ann", "text": "psst"}]}],
            "host": [{"key": "ann", "singer": "", "unread": 4, "messages": []}]});
        let v = view(&chat, "dm", 0);
        assert_eq!(v["tabs"][0]["label"], "Everyone (1 new)");
        assert_eq!(v["tabs"][2]["label"], "Host chat (4 new)");
        assert_eq!(v["dm"][0]["title"], "Ann ↔ bo", "an unnamed singer shows by key");
        assert_eq!(v["dm"][0]["rows"][0]["tags"], json!(["PRIVATE → bo"]));
        assert_eq!(v["host"][0]["title"], "ann");
        assert_eq!(v["status"], "2 messages need attention in the app");
        let one = json!({"group_enabled": true, "attention": 1});
        assert_eq!(view(&one, "group", 0)["status"], "1 message needs attention in the app");
        assert_eq!(view(&json!({"group_enabled": true, "attention": 0}), "group", 0)["status"], "");
        assert_eq!(view(&json!({"reachable": false}), "group", 0)["status"], "Room chat: could not reach the server");
        assert!(view(&json!({}), "group", 0)["status"].as_str().unwrap().starts_with("Room chat is off"));
        assert_eq!(view(&json!("garbage"), "group", 0)["group"], json!([]));
    }

    #[test]
    fn only_well_formed_media_refs_of_live_rows_pass() {
        let m = |r: &str, st: &str| json!({"media_ref": r, "status": st});
        for good in ["photo:77", "gif:g-1_X", &format!("gif:{}", "a".repeat(64))] {
            assert_eq!(media_ref(&m(good, "")), good);
        }
        assert_eq!(media_ref(&m("photo:5", "held")), "photo:5");
        for bad in ["photo:", "photo:x1", "photo:1234567890123", "gif:", "gif:a/b", "gif:../x", "gif:a b", &format!("gif:{}", "a".repeat(65)), "file:1", "photo", ""] {
            assert_eq!(media_ref(&m(bad, "")), "", "{bad}");
        }
        assert_eq!(media_ref(&m("photo:5", "removed")), "");
        assert_eq!(row(&m("photo:5", ""), None, false, 0)["media_ref"], "photo:5");
    }

    #[test]
    fn attention_is_per_tab_and_omitted_conversations_are_named() {
        let chat = json!({"group_enabled": true, "attention": 3, "attention_by": {"group": 1, "dm": 2},
            "unread": {"group": 0, "dm": 0, "host": 0}, "more": {"dm": 1, "host": 4}});
        let g = view(&chat, "group", 0);
        assert_eq!(g["status"], "1 message needs attention in the app");
        assert_eq!(g["tabs"][0]["attention"], 1);
        assert_eq!(g["tabs"][1]["attention"], 2);
        assert_eq!(g["tabs"][2]["attention"], 0);
        assert_eq!(view(&chat, "dm", 0)["status"], "2 messages need attention in the app");
        assert_eq!(view(&chat, "host", 0)["status"], "", "Host chat has nothing to moderate");
        assert_eq!(g["more"]["dm"], "1 more conversation in the app");
        assert_eq!(g["more"]["host"], "4 more conversations in the app");
        assert_eq!(view(&json!({"group_enabled": true}), "dm", 0)["more"]["dm"], "");
    }
}
