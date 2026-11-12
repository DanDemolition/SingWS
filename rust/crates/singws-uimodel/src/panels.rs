//! Toolkit-free loaders for the Ticker, Waitlist and Status tabs (moved here from the Slint trial so any UI can call them): read the same snapshot files the Qt shell reads (`queue.json`, `settings.json`, `waitlist_snapshot.json`)
//! and hand the text to the UI. Every RULE lives in `singws-uimodel` (`ticker`, `waitlist`); what is here is the file reading and the status wording, which is the same
//! as `singws-shell`'s `ticker_panel.rs` / `waitlist_panel.rs`. When Slint is adopted these two functions should move into `singws-uimodel` and the Qt shell call them too.
use serde_json::Value;
use crate::{status, ticker, waitlist};
use std::time::SystemTime;

pub const STALE_SECS: u64 = 120;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct TickerView { pub text: String, pub message: String, pub singers: Vec<String>, pub status: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct WaitRow { pub text: String, pub kind: String, pub first_time: bool }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct WaitSection { pub key: String, pub title: String, pub helper: String, pub rows: Vec<WaitRow> }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct WaitView { pub sections: Vec<WaitSection>, pub status: String }

pub fn home() -> String {
    std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default()
}

fn read_json(path: &str, missing: Value) -> Result<Value, String> {
    match std::fs::read_to_string(path) {
        Ok(t) => serde_json::from_str::<Value>(&t).map_err(|e| format!("{path}: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(missing),
        Err(e) => Err(format!("{path}: {e}")),
    }
}

pub fn ticker_view(dir: &str) -> TickerView {
    let load = || -> Result<_, String> {
        let queue = read_json(&format!("{dir}/queue.json"), Value::Array(vec![]))?;
        let settings = read_json(&format!("{dir}/settings.json"), Value::Object(Default::default()))?;
        ticker::view(&queue, &settings, &Value::Null)
    };
    match load() {
        Ok(v) => {
            let n = v.singers.len();
            let status = if n == 0 && v.message.is_empty() { "Nothing to show: nobody is waiting in the queue".to_string() } else { format!("{n} singer{} in the ticker   •   what the audience sees", if n == 1 { "" } else { "s" }) };
            TickerView { text: v.text, message: v.message, singers: v.singers, status }
        }
        Err(e) => TickerView { status: format!("Ticker unavailable: {e}"), ..Default::default() },
    }
}

pub fn waitlist_view(dir: &str, now: SystemTime) -> WaitView {
    let path = format!("{dir}/waitlist_snapshot.json");
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return WaitView { status: "No waitlist snapshot yet. Turn on \"waitlist_snapshot\" in the app's settings; it then writes the file whenever the waitlist changes.".into(), ..Default::default() },
        Err(e) => return WaitView { status: format!("Waitlist unavailable: {e}"), ..Default::default() },
    };
    let age = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| now.duration_since(t).ok()).map(|d| d.as_secs());
    let snapshot: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => return WaitView { status: format!("Waitlist unavailable: the snapshot is not valid JSON ({e})"), ..Default::default() },
    };
    match waitlist::sections_from_snapshot(&snapshot) {
        Ok(secs) => {
            let n: usize = secs.iter().map(|s| s.rows.len()).sum();
            let mut status = if n == 0 { "Nobody is waiting".to_string() } else { format!("{n} request{}", if n == 1 { "" } else { "s" }) };
            match age {
                Some(a) if a > STALE_SECS => status.push_str(&format!("   •   snapshot is {}m old, the app may be closed", a / 60)),
                Some(a) => status.push_str(&format!("   •   updated {a}s ago")),
                None => {}
            }
            let sections = secs.iter().map(|s| WaitSection {
                key: s.key.into(), title: s.title.into(), helper: s.helper.unwrap_or("").into(),
                rows: s.rows.iter().map(|r| WaitRow { text: r.text.clone(), kind: r.status_kind.clone(), first_time: r.first_time_singer }).collect(),
            }).collect();
            WaitView { sections, status }
        }
        Err(e) => WaitView { status: format!("Waitlist unavailable: {e}"), ..Default::default() },
    }
}


// ---- Rotation tab ----------------------------------------------------------------------------------------------------------------------
use crate::{history, rotation, settings_view};

#[derive(Debug, Default, Clone, PartialEq)]
pub struct RotRow { pub number: String, pub singer: String, pub combined: String, pub duet: bool }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct RotationData { pub rows: Vec<RotRow>, pub title: String, pub count: String, pub now: String, pub next: String, pub status: String }

/// Same inputs as the Qt shell's `rotation_panel.rs`, but stateless (the files are re-read each call).
pub fn rotation_view(dir: &str) -> RotationData {
    let queue_path = std::env::var("SINGWS_QUEUE").unwrap_or_else(|_| format!("{dir}/queue.json"));
    let queue = match rotation::load_queue(std::path::Path::new(&queue_path)) { Ok(q) => q, Err(e) => return RotationData { status: format!("Rotation unavailable: {e}"), ..Default::default() } };
    let tracks = rotation::load_tracks(std::path::Path::new(&format!("{dir}/tracks.json"))).unwrap_or_default();
    let db = std::path::PathBuf::from(format!("{dir}/singws.db"));
    let live = rotation::load_live_state(std::path::Path::new(&format!("{dir}/live_state.json")));
    let (now, playing, active_id, live_title) = match &live { Some(l) => (l.singer.clone(), l.playing, l.singer_id.clone(), l.title.clone()), None => (String::new(), false, String::new(), String::new()) };
    let inp = rotation::Inputs { db: Some(db.as_path()), tracks: &tracks, now_singing: if now.trim().is_empty() { "Singer Rotation" } else { &now }, playing, active_singer_id: &active_id, live_title: &live_title };
    match rotation::view(&queue, &inp) {
        Ok(v) => {
            let note = if live.is_some() { "Live from the app" } else { "As of the app's last save (live stage state off)" };
            RotationData {
                status: if v.rows.is_empty() { format!("Nobody is waiting. {note}") } else { note.to_string() },
                rows: v.rows.iter().map(|r| RotRow { number: r.number.clone(), singer: r.singer.clone(), combined: r.combined.clone(), duet: r.duet }).collect(),
                title: v.title_label, count: v.count_label, now: v.now_strip, next: v.next_strip,
            }
        }
        Err(e) => RotationData { status: format!("Could not build the rotation: {e}"), ..Default::default() },
    }
}

// ---- Singer history tab ----------------------------------------------------------------------------------------------------------------
#[derive(Debug, Default, Clone, PartialEq)]
pub struct HistRow { pub key: String, pub title: String, pub detail: String, pub selectable: bool }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct HistoryData { pub singers: Vec<HistRow>, pub status: String, pub selected: String, pub name: String, pub summary: String, pub songs: Vec<HistRow>, pub song_meta: String }

fn hist_rows(rows: &[history::Row]) -> Vec<HistRow> {
    rows.iter().map(|r| { let mut l = r.text.splitn(2, '\n'); HistRow { key: r.key.clone(), title: l.next().unwrap_or("").into(), detail: l.next().unwrap_or("").into(), selectable: r.selectable } }).collect()
}

/// `selected` is the singer key the user picked ("" or unknown: the first real singer, like the Qt page).
pub fn history_view(dir: &str, singer_search: &str, selected: &str, song_query: &str, now_epoch: i64) -> HistoryData {
    let path = std::env::var("SINGWS_HISTORY").unwrap_or_else(|_| format!("{dir}/singer_history.json"));
    let store = match history::load(std::path::Path::new(&path)) { Ok(s) => s, Err(e) => return HistoryData { status: format!("Singer history unavailable: {e}"), ..Default::default() } };
    let d = match history::directory(&store, singer_search, 700, 500) { Ok(d) => d, Err(e) => return HistoryData { status: format!("Could not list singers: {e}"), ..Default::default() } };
    let singers = hist_rows(&d.rows);
    let key = if singers.iter().any(|r| r.selectable && r.key == selected) { selected.to_string() } else { singers.iter().find(|r| r.selectable).map(|r| r.key.clone()).unwrap_or_default() };
    let mut out = HistoryData { status: format!("{} singer{}", d.total_matches, if d.total_matches == 1 { "" } else { "s" }), singers, selected: key.clone(), name: "Select a singer".into(), summary: "No singer selected.".into(), song_meta: "0 entries".into(), ..Default::default() };
    if !key.is_empty() {
        let (name, summary) = history::details(&store, &key);
        out.name = name; out.summary = summary;
        match history::songs_view(&store, &key, song_query, 500, now_epoch) {
            Ok(v) => { out.songs = hist_rows(&v.rows); out.song_meta = v.meta; }
            Err(e) => out.song_meta = format!("Could not list songs: {e}"),
        }
    }
    out
}

// ---- Settings viewer -------------------------------------------------------------------------------------------------------------------
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SettingRow { pub key: String, pub value: String, pub hidden: bool }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SettingsData { pub rows: Vec<SettingRow>, pub status: String }

pub fn settings_view_data(dir: &str, query: &str) -> SettingsData {
    let r = std::fs::read_to_string(format!("{dir}/settings.json")).map_err(|e| e.to_string()).and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string())).and_then(|v| settings_view::rows(&v, query));
    match r {
        Ok(rows) => {
            let n = rows.len();
            let hidden = rows.iter().filter(|r| r.hidden).count();
            SettingsData { status: if n == 0 { "No settings match".into() } else { format!("{n} setting{}   \u{2022}   {hidden} hidden (keys, PINs and tokens are never shown)", if n == 1 { "" } else { "s" }) },
                rows: rows.into_iter().map(|r| SettingRow { key: r.key, value: r.value, hidden: r.hidden }).collect() }
        }
        Err(e) => SettingsData { status: format!("Settings unavailable: {e}"), ..Default::default() },
    }
}

// ---- Library tab -----------------------------------------------------------------------------------------------------------------------
#[derive(Debug, Default, Clone, PartialEq)]
pub struct LibRow { pub id: i64, pub artist: String, pub title: String, pub left: String, pub right: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct LibraryData { pub rows: Vec<LibRow>, pub status: String }

/// Typo-tolerant search over the song index (same calls and wording as the Qt shell's `library_panel.rs`); `db` is the `singws.db` path.
pub fn library_view(db: &str, query: &str) -> LibraryData {
    let path = std::path::Path::new(db);
    match crate::search_with_fuzzy(path, query, 500, Some(std::time::Duration::from_millis(1500))) {
        Ok(res) => {
            let providers = crate::providers_for(path, &res.rows.iter().map(|r| r.id).collect::<Vec<_>>());
            let rows: Vec<LibRow> = res.rows.iter().map(|r| {
                let (left, right) = match crate::rows::library_row(r, providers.get(&r.id).map(String::as_str)) {
                    Ok(x) => (x.left, x.right),
                    Err(_) => (if r.artist.is_empty() { r.title.clone() } else { format!("{} \u{2022} {}", r.artist, r.title) }, crate::format_duration(r.duration_secs)),
                };
                LibRow { id: r.id, artist: r.artist.clone(), title: r.title.clone(), left, right }
            }).collect();
            let n = rows.len();
            let status = if query.trim().is_empty() { "Type to search your library".to_string() } else if n == 0 { "No songs match".into() }
                else if res.fuzzy_used { format!("{n} song{} (closest matches to what you typed)", if n == 1 { "" } else { "s" }) }
                else { format!("{n} song{}", if n == 1 { "" } else { "s" }) };
            LibraryData { rows, status }
        }
        Err(e) => LibraryData { status: format!("Library unavailable: {e}"), ..Default::default() },
    }
}

pub fn db_path(dir: &str) -> String { std::env::var("SINGWS_DB").unwrap_or_else(|_| format!("{dir}/singws.db")) }

// ---- Music tab -------------------------------------------------------------------------------------------------------------------------
use crate::{eq_view, music};

#[derive(Debug, Default, Clone, PartialEq)]
pub struct MusicRow { pub index: i64, pub filename: String, pub path: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct MusicData { pub rows: Vec<MusicRow>, pub count: String, pub info: String, pub status: String }

/// "Background music on   •   autoplay when idle on   •   volume 65%" from the three plain settings (same wording as the Qt shell).
fn music_info(dir: &str) -> String {
    let s: Value = std::fs::read_to_string(format!("{dir}/settings.json")).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null);
    let onoff = |k: &str| s.get(k).and_then(Value::as_bool).map(|b| if b { "on" } else { "off" });
    let mut parts = Vec::new();
    if let Some(v) = onoff("bg_enabled") { parts.push(format!("Background music {v}")); }
    if let Some(v) = onoff("bg_autoplay_on_idle") { parts.push(format!("autoplay when idle {v}")); }
    if let Some(v) = s.get("bg_volume").and_then(Value::as_f64) { parts.push(format!("volume {}%", (v * 100.0).round() as i64)); }
    parts.join("   \u{2022}   ")
}

/// `cutoff` is the live player's current track index (rows at or before it are hidden, as in the app); -1 shows everything.
pub fn music_view(dir: &str, query: &str, cutoff: i64) -> MusicData {
    let info = music_info(dir);
    let path = format!("{dir}/bg_playlist.json");
    if !std::path::Path::new(&path).exists() {
        return MusicData { count: "0 tracks".into(), info, status: "No background-music playlist saved yet".into(), ..Default::default() };
    }
    let r = std::fs::read_to_string(&path).map_err(|e| e.to_string()).and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string()))
        .and_then(|raw| music::parse_playlist(&raw)).and_then(|rows| music::view_from(&rows, query, cutoff));
    match r {
        Ok(v) => MusicData {
            status: if v.total == 0 { "The playlist is empty".into() } else if v.shown == 0 { "No tracks match".into() } else { String::new() },
            rows: v.rows.iter().map(|r| MusicRow { index: r.index as i64, filename: r.filename.clone(), path: r.path.clone() }).collect(), count: v.count_label, info,
        },
        Err(e) => MusicData { count: "0 tracks".into(), info, status: format!("The playlist could not be read: {e}"), ..Default::default() },
    }
}

// ---- EQ tab ----------------------------------------------------------------------------------------------------------------------------
#[derive(Debug, Default, Clone, PartialEq)]
pub struct EqOne { pub gains: Vec<f64>, pub enabled: bool, pub flat: bool, pub auto_enabled: bool }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct EqData { pub bands_hz: Vec<f64>, pub bgm: EqOne, pub karaoke: EqOne, pub status: String }

pub fn eq_data(dir: &str) -> EqData {
    let r = std::fs::read_to_string(format!("{dir}/settings.json")).map_err(|e| e.to_string())
        .and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string())).and_then(|v| eq_view::read(&v));
    let one = |e: &eq_view::Eq| EqOne { gains: e.gains.to_vec(), enabled: e.enabled, flat: e.flat, auto_enabled: e.auto_enabled };
    match r {
        Ok((b, k)) => EqData { bands_hz: eq_view::BANDS_HZ.iter().map(|x| *x as f64).collect(), bgm: one(&b), karaoke: one(&k), status: String::new() },
        Err(e) => EqData { status: format!("The equalisers cannot be shown: {e}"), ..Default::default() },
    }
}

// ---- Chat tab --------------------------------------------------------------------------------------------------------------------------
use crate::chat;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct ChatItem { pub header: bool, pub key: String, pub text: String, pub mine: bool, pub media_ref: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct ChatData { pub status: String, pub tab_labels: Vec<String>, pub items: Vec<ChatItem>, pub group_enabled: bool, pub hint: String }

/// The machine's UTC offset in minutes (what the Qt page gets from the JS clock), for the message time stamps.
pub fn local_utc_offset_min(now: i64) -> i64 {
    // SAFETY: localtime_r writes into the zeroed tm we pass; `now` is a plain integer.
    unsafe {
        let t: libc::time_t = now as libc::time_t;
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&t, &mut tm).is_null() { return 0; }
        (tm.tm_gmtoff as i64) / 60
    }
}

/// The Chat tab as a flat list (a header per conversation, then its rows). `chat_json` is the `chat` member of the control view model (null until the app sends one);
/// `tab` is "group" | "dm" | "host"; `host_key` the Host chat conversation the operator picked.
pub fn chat_data(chat_json: &Value, tab: &str, host_key: &str, utc_offset_min: i64) -> ChatData {
    if chat_json.is_null() {
        return ChatData { status: "Waiting for the app to send the chat. It does when remote control (shell_commands) is on and the chat page has loaded.".into(), tab_labels: vec!["Everyone".into(), "Private".into(), "Host chat".into()], ..Default::default() };
    }
    let v = chat::view(chat_json, tab, utc_offset_min);
    let g = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let rows_of = |rows: &Value, out: &mut Vec<ChatItem>| {
        for r in rows.as_array().cloned().unwrap_or_default() {
            let who = r["who"].as_str().unwrap_or("");
            let tags: Vec<&str> = r["tags"].as_array().map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            let stamp = r["stamp"].as_str().unwrap_or("");
            let body = r["body"].as_str().unwrap_or("");
            let att = r["attachment"].as_str().unwrap_or("");
            let head = format!("{who}{}{}", if tags.is_empty() { String::new() } else { format!("   {}", tags.join(" \u{b7} ")) }, if stamp.is_empty() { String::new() } else { format!("   {stamp}") });
            let text = format!("{head}\n{body}{}", if att.is_empty() { String::new() } else { format!("{}{att}", if body.is_empty() { "" } else { " " }) });
            out.push(ChatItem { header: false, key: String::new(), text, mine: r["mine"].as_bool().unwrap_or(false), media_ref: r["media_ref"].as_str().unwrap_or("").into() });
        }
    };
    let mut items = Vec::new();
    match tab {
        "group" => rows_of(&v["group"], &mut items),
        "dm" => for t in v["dm"].as_array().cloned().unwrap_or_default() {
            items.push(ChatItem { header: true, text: t["title"].as_str().unwrap_or("").into(), ..Default::default() });
            rows_of(&t["rows"], &mut items);
        },
        _ => for t in v["host"].as_array().cloned().unwrap_or_default() {
            let unread = t["unread"].as_i64().unwrap_or(0);
            let key = t["key"].as_str().unwrap_or("").to_string();
            let title = format!("{}{}{}", t["title"].as_str().unwrap_or(""), if unread > 0 { format!("   \u{2022}   {unread} new") } else { String::new() }, if key.is_empty() { "" } else { "   (click to talk to this singer)" });
            items.push(ChatItem { header: true, mine: !key.is_empty() && key == host_key, key, text: title, ..Default::default() });
            rows_of(&t["rows"], &mut items);
        },
    }
    let group_enabled = v["group_enabled"].as_bool().unwrap_or(false);
    let more = v["more"][tab].as_str().unwrap_or("");
    let base = match tab {
        "dm" => "Private conversations are read-only here; speak in them from the app.".to_string(),
        "host" => if host_key.is_empty() { "Click a singer above to talk to them.".into() } else { "Talking to this singer only.".into() },
        _ => if group_enabled { "Everyone in the room will see this.".into() } else { "Everyone chat is off in the web dashboard.".into() },
    };
    ChatData {
        status: g("status"),
        tab_labels: v["tabs"].as_array().map(|a| a.iter().map(|t| t["label"].as_str().unwrap_or("").to_string()).collect()).unwrap_or_default(),
        items, group_enabled,
        hint: if tab != "group" && !more.is_empty() { format!("{base}  {more}.") } else { base },
    }
}

// ---- Status tab ------------------------------------------------------------------------------------------------------------------------
pub const STATUS_STALE_SECS: i64 = 300;

#[derive(Debug, Default, Clone, PartialEq)]
pub struct StatusChip { pub text: String, pub tone: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct StatusCheck { pub ok: bool, pub label: String, pub detail: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct StatusHero { pub kicker: String, pub singer: String, pub subtitle: String, pub meta: String, pub format: String, pub remaining: String }
#[derive(Debug, Default, Clone, PartialEq)]
pub struct StatusViewData {
    pub all_passed: bool, pub check_rows: Vec<StatusCheck>, pub hero: Option<StatusHero>,
    pub note: String, pub stale: bool, pub has: bool, pub chips: Vec<StatusChip>, pub status_text: String, pub warn_dot: bool, pub location: String,
    pub tooltip: String, pub eta: String, pub summary: String, pub report_title: String, pub checks: Vec<(bool, String)>,
}

/// The app's ETA line is a little HTML (coloured spans); Slint's `Text` has no rich text, so the tags are dropped and the words kept (a known gap, see the design doc).
pub fn strip_tags(html: &str) -> String {
    let mut out = String::new();
    let mut in_tag = false;
    for c in html.chars() {
        match c { '<' => in_tag = true, '>' => in_tag = false, _ if !in_tag => out.push(c), _ => {} }
    }
    out.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
}

pub fn status_view(dir: &str, now: i64) -> StatusViewData {
    let path = format!("{dir}/status_snapshot.json");
    let note = |t: String| StatusViewData { note: t, ..Default::default() };
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return note("No status snapshot yet. Turn on \"status_snapshot\" in the app's settings; it then writes the file whenever the header refreshes.".into()),
        Err(e) => return note(format!("Status unavailable: {e}")),
    };
    let snapshot: Value = match serde_json::from_str(&text) { Ok(v) => v, Err(e) => return note(format!("Status unavailable: the snapshot is not valid JSON ({e})")) };
    match status::view(&snapshot) {
        Ok(v) => {
            let age = (now - v.taken_at).max(0);
            StatusViewData {
                note: if age > STATUS_STALE_SECS { format!("snapshot is {}m old, the app may be closed", age / 60) } else { format!("updated {age}s ago") },
                stale: age > STATUS_STALE_SECS, has: true,
                chips: v.chips.iter().map(|c| StatusChip { text: c.text.clone(), tone: c.tone.to_string() }).collect(),
                status_text: v.status.text.clone(), warn_dot: v.status.dot_token == "warning", location: v.status.location.clone(), tooltip: v.status.tooltip.clone(),
                eta: strip_tags(&v.eta.html), summary: v.summary.clone(), report_title: v.report.title.clone(),
                checks: v.report.checks.iter().map(|c| (c.ok, format!("{}   {}", c.label, c.detail))).collect(),
                all_passed: v.report.all_passed,
                check_rows: v.report.checks.iter().map(|c| StatusCheck { ok: c.ok, label: c.label.to_string(), detail: c.detail.to_string() }).collect(),
                hero: v.hero.as_ref().map(|h| StatusHero { kicker: h.kicker.clone(), singer: h.singer.clone(), subtitle: h.subtitle.clone(), meta: h.meta.clone(), format: h.format_text.clone(), remaining: h.remaining.clone() }),
            }
        }
        Err(e) => note(format!("Status unavailable: {e}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const STATUS_FIXTURE: &str = r#"{"version":1,"taken_at":1792000000,"settings":{"venue_name":"Joe's","use_waiting_for_add":true,"queue_mode":"rotation","bg_video_enabled":false},"connection":"Connected","network_configured":true,"last":{"received":"","accepted":"","sync":"12:01:02","error":""},"attention":{"1":{}},"queue_rev":9,"queue":[{"name":"Ann","rotation_marker":false},{"name":"Bo","rotation_marker":true}],"total_secs":600,"locked":false,"looping":false,"diagnostics":{"local_name":"Speakers","screen_count":2,"karafun_path":null,"output_host":true,"preview_host":true,"show_ticker":true,"qr_url":"https://x/","bg_files":[]},"hero":{"kicker":"NOW SINGING","singer":"Ann","subtitle":"Radio Ga Ga · Queen","meta":"SC8671","format":"ZIP","remaining":"03:21"}}"#;
    fn dir(n: &str) -> String { let d = std::env::temp_dir().join(format!("singws-uimodel-panels-{}-{n}", std::process::id())); std::fs::create_dir_all(&d).unwrap(); d.to_string_lossy().into() }
    #[test]
    fn same_text_as_the_qt_shell_for_the_same_files() {
        let d = dir("t");
        std::fs::write(format!("{d}/queue.json"), r#"[{"name":"Ann","songs":[{"title":"x"}]},{"name":"Skip","skipped":true,"songs":[{}]},{"name":"Bo","songs":[{"title":"y"}]}]"#).unwrap();
        std::fs::write(format!("{d}/settings.json"), r#"{"ticker_custom_enabled":true,"ticker_custom_message":"Tip your host"}"#).unwrap();
        let v = ticker_view(&d);
        assert_eq!(v.text, "Tip your host   |   1. Ann   |   2. Bo");
        assert_eq!(v.singers, vec!["Ann", "Bo"]);
    }
    #[test]
    fn status_from_the_fixture_and_html_is_stripped() {
        assert_eq!(strip_tags("<span style='color:#fff'>SINGERS</span>&nbsp;3"), "SINGERS 3");
        let d = dir("s");
        std::fs::write(format!("{d}/status_snapshot.json"), STATUS_FIXTURE).unwrap();
        let v = status_view(&d, 1_792_000_020);
        assert_eq!(v.hero.as_ref().map(|h| h.singer.as_str()), Some("Ann"));
        assert!(v.has && v.note == "updated 20s ago" && v.location == "Joe's" && v.checks.len() == 7, "{v:?}");
    }
    #[test]
    fn missing_and_broken_files() {
        let d = dir("m");
        assert_eq!(ticker_view(&d).text, "");
        std::fs::write(format!("{d}/queue.json"), "{no").unwrap();
        assert!(ticker_view(&d).status.starts_with("Ticker unavailable"));
        assert!(waitlist_view(&d, SystemTime::now()).status.starts_with("No waitlist snapshot"));
    }

    #[test]
    fn rotation_history_settings_from_files() {
        let d = dir("r");
        std::fs::write(format!("{d}/queue.json"), r#"[{"name":"Ann","songs":[{"song_info":"/x/Queen - Radio.zip"}]},{"name":"Bo","songs":[{"song_info":"/x/ABBA - Waterloo.zip"}]}]"#).unwrap();
        let r = rotation_view(&d);
        assert_eq!(r.rows.len(), 2, "{r:?}");
        std::fs::write(format!("{d}/singer_history.json"), r#"{"singers":{"ann":{"name":"Ann","songs":{"queen|radio":{"artist":"Queen","title":"Radio","play_count":2}}}}}"#).unwrap();
        let h = history_view(&d, "", "", "", 1_800_000_000);
        assert_eq!(h.name, "Ann", "{h:?}");
        assert_eq!(h.songs.len(), 1);
        std::fs::write(format!("{d}/settings.json"), r#"{"venue_name":"Joe","api_key":"SECRET123"}"#).unwrap();
        let s = settings_view_data(&d, "");
        assert!(s.rows.iter().any(|r| r.key == "api_key" && r.hidden && !r.value.contains("SECRET123")), "{s:?}");
    }
    #[test]
    fn library_search_finds_a_typo_and_says_so() {
        let d = dir("lib");
        let db = format!("{d}/singws.db");
        let con = rusqlite::Connection::open(&db).unwrap();
        con.execute_batch("CREATE TABLE songs (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, artist TEXT, title TEXT, discid TEXT, duration_secs INTEGER, song_type TEXT, display TEXT, searchstring TEXT NOT NULL);").unwrap();
        con.execute("INSERT INTO songs (path, artist, title, discid, duration_secs, searchstring) VALUES ('/lib/SC1-01.zip','Queen','Radio Ga Ga','SC1',212,'queen radio ga ga')", []).unwrap();
        drop(con);
        let v = library_view(&db, "radio");
        assert_eq!((v.rows.len(), v.rows[0].left.as_str()), (1, "Queen \u{2022} Radio Ga Ga"), "{v:?}");
        assert_eq!(library_view(&db, "").status, "Type to search your library");
        assert_eq!(library_view(&db, "zzzzqq").status, "No songs match");
    }
    #[test]
    fn music_and_eq_from_files() {
        let d = dir("me");
        std::fs::write(format!("{d}/bg_playlist.json"), r#"[{"filename":"a.mp3","path":"/m/a.mp3"},{"filename":"b.mp3","path":"/m/b.mp3"}]"#).unwrap();
        std::fs::write(format!("{d}/settings.json"), r#"{"bg_enabled":true,"bg_volume":0.65,"eq_bgm":[0,0,3,0,0,0,0,0,0,0]}"#).unwrap();
        let m = music_view(&d, "", -1);
        assert_eq!((m.rows.len(), m.info.as_str()), (2, "Background music on   \u{2022}   volume 65%"), "{m:?}");
        assert_eq!(music_view(&d, "zzz", -1).status, "No tracks match");
        let e = eq_data(&d);
        assert_eq!((e.bgm.gains[2], e.bgm.enabled, e.bgm.auto_enabled, e.karaoke.flat), (3.0, true, true, true), "{e:?}");
    }
    #[test]
    fn chat_rows_headers_and_hints() {
        assert!(chat_data(&Value::Null, "group", "", 0).status.starts_with("Waiting for the app"));
        let c: Value = serde_json::from_str(r#"{"group_enabled":true,"dm_enabled":true,"unread":{"group":1},"group":[{"id":1,"from":"Ann","text":"hi all","at":1793405109}],"dm":[],"host":[{"key":"ann","singer":"Ann","unread":2,"messages":[{"id":2,"from":"Ann","text":"hello host","at":1793405109}]}]}"#).unwrap();
        let g = chat_data(&c, "group", "", 0);
        assert_eq!((g.items.len(), g.hint.as_str()), (1, "Everyone in the room will see this."));
        assert!(g.items[0].text.starts_with("Ann   GROUP   12:05:09 AM\nhi all"), "{:?}", g.items[0]);
        let h = chat_data(&c, "host", "ann", 0);
        assert!(h.items[0].header && h.items[0].mine && h.items[0].text.contains("2 new"), "{h:?}");
        assert_eq!(h.hint, "Talking to this singer only.");
    }
}
