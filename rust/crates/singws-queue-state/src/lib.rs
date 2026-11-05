//! Stage B queue/request state machine (shadow/parity model).
//!
//! A port of the host app's queue rules, driven by the event schema of `tools/queue_event_replay.py` and producing the SAME canonical
//! snapshot (docs/design/stage-b-queue-replay.md). Pure: no I/O, no real clock (a logical clock advances 1 s per event, as in the Python harness).
//!
//! DECLARED SCOPE (everything else is "not modelled"; the Python app stays authoritative):
//!   modelled   classic and rotation queues, host add / remove song / remove singer / move / skip / rotation_advance / complete,
//!              remote arrivals (admission via `singws-queue::titles_conflict`, tombstones, removed-id memory, singer match by name,
//!              song limit, accepting, duplicate request ids, wire-order song ordering), empty rotation slots with expiry, restart.
//!              the waitlist: `waitlist_add` / `waitlist_remove` / `waitlist_restore`, `waiting` / failed rows (rebuilt from every arrival exactly like
//!              `_set_waiting_for_add_requests`), `use_waiting_for_add`, handled ids, the removed-tonight list (+ `waitlist_evening_key`).
//!              rotation lock (`rotation_lock` op, `rotation_locked` setting), duet singers ("A & B", both names count against the cap) and the
//!              attention list / failed-id set a late duet failure fills.
//!   NOT        rows with a server-terminal state, `sort_order` / order timestamps, singer ids / sessions, deferred
//!              adds, rename-merge. Waitlist: stale-by-timestamp purge, replacement tracks, library-match failures (`_unmatched_request_sigs`), the song-info
//!              edit/replace dialogs. The lock being cleared by playback.
//!              Such input answers `{"ok":false,"code":"unsupported"}` or is ignored exactly as documented per op, so a parity run on it
//!              is expected to differ and the Python comparer reports where.
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};

pub fn merged_config(events: &[Value]) -> Value {
    let mut m = Map::new();
    for ev in events {
        if ev.get("op").and_then(Value::as_str) == Some("config") {
            for (k, v) in ev.as_object().unwrap() {
                if k != "op" {
                    m.insert(k.clone(), v.clone());
                }
            }
        }
    }
    Value::Object(m)
}

#[derive(Clone, Debug)]
struct Settings {
    mode: String,
    limit: i64,
    accepting: bool,
    waitlist: bool,
    locked: bool,
    defer: bool,
}

impl Settings {
    fn from(cfg: &Value) -> Settings {
        Settings {
            mode: cfg.get("queue_mode").and_then(Value::as_str).unwrap_or("rotation").to_string(),
            limit: cfg.get("limit_pending_max").and_then(Value::as_i64).unwrap_or(20),
            accepting: cfg.get("requests_accepting").and_then(Value::as_bool).unwrap_or(true),
            waitlist: cfg.get("waitlist").or_else(|| cfg.get("use_waiting_for_add")).map(truthy).unwrap_or(false),
            locked: cfg.get("rotation_locked").map(truthy).unwrap_or(false),
            defer: cfg.get("defer").map(truthy).unwrap_or(false),
        }
    }
    fn rotation(&self) -> bool {
        self.mode.trim().to_lowercase() == "rotation"
    }
    fn active_limit(&self) -> i64 {
        self.limit.clamp(1, 20)
    }
}

#[derive(Clone, Debug)]
struct Entry {
    rid: Option<u64>,
    local: Option<usize>,
    artist: String,
    title: String,
    song_info: String,
    duet: Option<String>,
    key: i64,
    tempo: Option<i64>,
    skipped: bool,
    /// `request_order` (the server's sort position as the app stores it; None for host-local entries).
    order: Option<f64>,
    /// `singer_id` / `singer_uid` and `singer_session_id` the request row carried (remote adds only).
    sid: String,
    session: i64,
}

impl Entry {
    fn alias(&self) -> String {
        match (self.rid, self.local) {
            (Some(r), _) => format!("R{r}"),
            (None, Some(l)) => format!("L{l}"),
            _ => String::new(),
        }
    }
    fn identity(&self) -> (u8, u64) {
        match (self.rid, self.local) {
            (Some(r), _) => (0, r),
            (None, Some(l)) => (1, l as u64),
            _ => (2, 0),
        }
    }
}

#[derive(Clone, Debug)]
struct Singer {
    uid: u64,
    name: String,
    skipped: bool,
    has_sung: bool,
    round_sung: bool,
    marker: bool,
    temp_empty: bool,
    until: f64,
    lock_new: bool,
    lock_order: i64,
    /// Order metadata (`host_order_updated_at`, `singer_order_updated_at`, `order_revision`, `last_order_source`).
    host_ts: i64,
    singer_ts: i64,
    order_rev: i64,
    order_src: String,
    /// `server_singer_id` / `server_singer_session_id`.
    server_id: String,
    server_session: i64,
    /// `created_at` (the logical clock when the row was made; 0 = predates the field, so it counts as oldest).
    created: f64,
    songs: Vec<Entry>,
}

impl Singer {
    fn is_active(&self) -> bool {
        !self.skipped && self.songs.iter().any(|e| !e.skipped)
    }
}

#[derive(Clone, Debug)]
struct Tomb {
    status: String,
    reason: String,
    singer: String,
    artist: String,
    title: String,
}

/// One request held while a song plays (`_deferred_remote_adds`): the resolved payload the flush will add.
#[derive(Clone, Debug)]
struct Deferred {
    rid: u64,
    singer: String,
    entry: Entry,
    item: Value,
    request_time: f64,
}

#[derive(Clone, Debug)]
struct Pending {
    ids: Vec<u64>,
    host: i64,
    rev: i64,
}

#[derive(Clone, Debug, Default)]
struct OrderMeta {
    host: i64,
    singer: i64,
    rev: i64,
    source: String,
}

#[derive(Clone)]
pub struct State {
    init: Settings,
    cur: Settings,
    clock: f64,
    queue: Vec<Singer>,
    tombs: BTreeMap<u64, Tomb>,
    removed: BTreeSet<u64>,
    handled: BTreeSet<u64>,
    waitlist: BTreeMap<u64, Value>,
    removed_tonight: BTreeMap<u64, Value>,
    attention: BTreeMap<u64, Value>,
    failed: BTreeSet<u64>,
    /// `_remote_terminal_titles`: the title a server-terminal row carried (memory only; a restart forgets it).
    terminal_titles: BTreeMap<u64, String>,
    /// `_queue_revision`: a counter the order-conflict rules read, so it is modelled (bumped by tombstones, local removal by the server, waitlist restore, order records).
    /// `_singer_rename_aliases`: retired name key -> surviving name (memory only).
    aliases: BTreeMap<String, String>,
    deferred: Vec<Deferred>,
    /// `karaoke_playing`: the one playback fact the deferral rule reads (an input of the model; nothing is played).
    playing: bool,
    queue_rev: i64,
    /// `_pending_remote_order_syncs`: singer key -> (request ids, host timestamp, revision).
    pending: BTreeMap<String, Pending>,
    lock_count: i64,
    next_uid: u64,
    singer_alias: HashMap<u64, usize>,
    next_local: usize,
}

const ROUTE_OK: (bool, &str) = (true, "");

fn capitalize(name: &str) -> String {
    let mut out = String::new();
    let mut at_start = true;
    for ch in name.chars() {
        if ch.is_whitespace() {
            at_start = true;
            out.push(ch);
        } else if at_start {
            out.extend(ch.to_uppercase());
            at_start = false;
        } else {
            out.push(ch);
        }
    }
    out
}

/// `_queue_limit_name_key`: whitespace collapsed, casefolded.
fn name_key(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

fn is_word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Length of a duet separator at `j` (`&`, `+`, `/w`, `w/`, `/`, `,`, whole-word `and` / `with`, any case), in the regex's alternation order.
fn sep_len_at(chars: &[char], j: usize) -> Option<usize> {
    let lc = |k: usize| chars.get(k).map(|c| c.to_lowercase().next().unwrap_or(*c));
    match chars.get(j)? {
        '&' | '+' => return Some(1),
        _ => {}
    }
    if chars[j] == '/' && lc(j + 1) == Some('w') {
        return Some(2);
    }
    if lc(j) == Some('w') && chars.get(j + 1) == Some(&'/') {
        return Some(2);
    }
    if chars[j] == '/' || chars[j] == ',' {
        return Some(1);
    }
    let before_ok = j == 0 || !is_word(chars[j - 1]);
    if before_ok {
        if lc(j) == Some('a') && lc(j + 1) == Some('n') && lc(j + 2) == Some('d') && !chars.get(j + 3).map(|c| is_word(*c)).unwrap_or(false) {
            return Some(3);
        }
        if lc(j) == Some('w') && lc(j + 1) == Some('i') && lc(j + 2) == Some('t') && lc(j + 3) == Some('h') && !chars.get(j + 4).map(|c| is_word(*c)).unwrap_or(false) {
            return Some(4);
        }
    }
    None
}

/// `re.split(r"\s*(?:&|\+|/w|w/|/|,|\band\b|\bwith\b)\s*", text, maxsplit=1, flags=I)`: the text before and after the first separator.
fn split_first_sep(text: &str) -> Option<(String, String)> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    for i in 0..n {
        let mut j = i;
        while j < n && chars[j].is_whitespace() {
            j += 1;
        }
        if let Some(len) = sep_len_at(&chars, j) {
            let mut e = j + len;
            while e < n && chars[e].is_whitespace() {
                e += 1;
            }
            return Some((chars[..i].iter().collect(), chars[e..].iter().collect()));
        }
    }
    None
}

/// `_parse_duet_singer`: (primary, duet display) with whitespace collapsed; no duet when either side is empty.
fn parse_duet(raw: &str) -> (String, Option<String>) {
    let name = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    if name.is_empty() {
        return (String::new(), None);
    }
    match split_first_sep(&name) {
        Some((a, b)) if !a.trim().is_empty() && !b.trim().is_empty() => {
            let (a, b) = (a.trim().to_string(), b.trim().to_string());
            let display = format!("{a} & {b}");
            (a, Some(display))
        }
        _ => (name, None),
    }
}

/// `_duet_participants_from_display`: the names a song counts against, deduplicated by the limit key.
fn participants(primary: &str, display: Option<&str>) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    if !primary.trim().is_empty() {
        names.push(primary.trim().to_string());
    }
    let display = display.unwrap_or("").trim();
    if !display.is_empty() {
        let (pp, pd) = parse_duet(display);
        if !pp.is_empty() {
            names.push(pp);
        }
        let source = pd.unwrap_or_else(|| display.to_string());
        if let Some((_, partner)) = split_first_sep(&source) {
            if !partner.trim().is_empty() {
                names.push(partner.trim().to_string());
            }
        }
    }
    let mut seen = BTreeSet::new();
    names.into_iter().filter(|n| { let k = name_key(n); !k.is_empty() && seen.insert(k) }).collect()
}

/// `_remote_request_participants`: queue_singer, else singer, parsed like a typed name.
fn req_participants(row: &Value) -> Vec<String> {
    let name = first_truthy_str(row, &["queue_singer", "singer", "name"]);
    let (primary, display) = parse_duet(name.trim());
    let primary = if primary.is_empty() { name.trim().to_string() } else { primary };
    participants(&primary, display.as_deref())
}


/// `float(x)`; None when Python would raise (None, empty or non-numeric string, containers).
fn py_float(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        _ => None,
    }
}

/// `int(x)`; 0 when Python would raise (the callers wrap it in try/except returning 0).
fn py_int0(v: Option<&Value>) -> i64 {
    match v {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)).unwrap_or(0),
        Some(Value::String(s)) => s.trim().parse::<i64>().unwrap_or(0),
        Some(Value::Bool(b)) => *b as i64,
        _ => 0,
    }
}

/// `_coerce_order_timestamp`: milliseconds; seconds (below 1e11) are scaled; anything unusable is 0.
fn coerce_ts(v: Option<&Value>) -> i64 {
    let raw = match v {
        None | Some(Value::Null) => return 0,
        Some(Value::String(s)) if s.is_empty() => return 0,
        Some(x) => match py_float(x) {
            Some(f) => f,
            None => return 0,
        },
    };
    if raw <= 0.0 {
        return 0;
    }
    let raw = if raw < 100_000_000_000.0 { raw * 1000.0 } else { raw };
    if raw.is_finite() { raw as i64 } else { 0 }
}

fn coerce_i(v: i64) -> i64 {
    coerce_ts(Some(&Value::from(v)))
}

/// `_remote_order_meta_from_req`.
fn order_meta_of(row: &Value) -> OrderMeta {
    let src = first_truthy_str(row, &["last_order_source", "order_source"]);
    let src = if src.is_empty() { "server".to_string() } else { src.trim().to_lowercase() };
    let source = if matches!(src.as_str(), "host" | "singer" | "server") { src } else { "server".to_string() };
    let rev_v = ["order_revision", "revision"].iter().filter_map(|k| row.get(*k)).find(|v| truthy(v));
    OrderMeta { host: coerce_ts(row.get("host_order_updated_at")), singer: coerce_ts(row.get("singer_order_updated_at")), rev: py_int0(rev_v), source }
}

/// `_incoming_order_timestamp`.
fn incoming_ts(m: &OrderMeta) -> i64 {
    match m.source.as_str() {
        "singer" => m.singer,
        "host" => m.host,
        _ => m.host.max(m.singer),
    }
}

/// The `request_order` `_add_song_to_queue` stores for a remote add: float(sort_order or queue_position or request id).
fn add_request_order(meta: &Value, rid: Option<u64>) -> f64 {
    let fallback = rid.unwrap_or(0) as f64;
    match ["sort_order", "queue_position"].iter().filter_map(|k| meta.get(*k)).find(|v| truthy(v)) {
        Some(v) => py_float(v).unwrap_or(fallback),
        None => fallback,
    }
}

/// Python truthiness of a JSON value (`bool(x)`).
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `a or b or ""`: the first truthy value (the empty string if none), as the app's normalised request row stores it.
fn first_truthy_value(row: &Value, keys: &[&str]) -> Value {
    for k in keys {
        if let Some(v) = row.get(*k) {
            if truthy(v) {
                return v.clone();
            }
        }
    }
    Value::String(String::new())
}

/// `str(x)` for the JSON values a request row carries.
fn pystr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => if *b { "True".into() } else { "False".into() },
        other => other.to_string(),
    }
}

/// `str(row.get(key, ""))`.
fn row_str(row: &Value, key: &str) -> String {
    row.get(key).map(pystr).unwrap_or_default()
}

/// `str(a or b or ... or "")`: the first truthy value, as text.
fn first_truthy_str(row: &Value, keys: &[&str]) -> String {
    for k in keys {
        if let Some(v) = row.get(*k) {
            if truthy(v) {
                return pystr(v);
            }
        }
    }
    String::new()
}

/// `float(x or 0)`; None when Python would raise.
fn to_f64(v: Option<&Value>) -> Option<f64> {
    match v {
        None => Some(0.0),
        Some(x) if !truthy(x) => Some(0.0),
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.trim().parse::<f64>().ok(),
        Some(Value::Bool(b)) => Some(if *b { 1.0 } else { 0.0 }),
        _ => None,
    }
}

/// `int(req.get("request_id") or req.get("id") or 0)`.
fn request_id_of(row: &Value) -> i64 {
    for k in ["request_id", "id"] {
        if let Some(v) = row.get(k) {
            if truthy(v) {
                return match v {
                    Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
                    Value::String(s) => s.trim().parse::<i64>().unwrap_or(0),
                    Value::Bool(b) => *b as i64,
                    _ => 0,
                };
            }
        }
    }
    0
}

fn state_of(row: &Value, keys: &[&str]) -> String {
    first_truthy_str(row, keys).trim().to_lowercase()
}

/// `_remote_request_is_non_actionable`: the server row has left host-actionable intake.
fn non_actionable(row: &Value) -> bool {
    if !row.is_object() {
        return false;
    }
    let state = state_of(row, &["state", "status"]);
    if matches!(state.as_str(), "waiting" | "failed" | "failed_needs_review") {
        return false;
    }
    let sent = row.get("sent").map(truthy).unwrap_or(false) || row.get("delivered").map(truthy).unwrap_or(false);
    if state == "accepted" {
        if sent {
            return true;
        }
        let sync = first_truthy_str(row, &["desktop_sync_status"]).trim().to_lowercase();
        return !matches!(sync.as_str(), "" | "pending" | "pending_desktop_sync");
    }
    if matches!(state.as_str(), "active" | "delivered" | "completed" | "sung" | "removed" | "skipped") {
        return true;
    }
    if sent {
        return true;
    }
    ["added_at", "queued_at", "completed_at", "removed_at"].iter().any(|k| to_f64(row.get(*k)).map(|f| f > 0.0).unwrap_or(false))
}

/// `_is_waiting_for_add_request`.
fn is_waiting_row(row: &Value) -> bool {
    if !row.is_object() || non_actionable(row) {
        return false;
    }
    let state = state_of(row, &["state"]);
    if matches!(state.as_str(), "waiting" | "failed" | "failed_needs_review") {
        return true;
    }
    !first_truthy_str(row, &["pending_reason", "last_error", "attention_reason"]).trim().is_empty()
}

/// `_waitlist_evening_key`: `(now - 6 h).strftime("%Y-%m-%d")` for a LOCAL wall-clock time given as seconds since 1970-01-01 00:00 local
/// (the caller adds the UTC offset; the model has no clock or timezone of its own). Used to decide whether a saved removed-tonight list
/// still belongs to this evening.
pub fn waitlist_evening_key(local_epoch_secs: i64) -> String {
    let days = (local_epoch_secs - 6 * 3600).div_euclid(86_400);
    // civil-from-days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}")
}

/// `_load_waitlist_removed_tonight`: a saved list is kept only when its evening and tenant match the current ones.
pub fn removed_tonight_still_valid(saved_evening: &str, saved_tenant: &str, now_evening: &str, tenant: &str) -> bool {
    saved_evening == now_evening && saved_tenant == tenant
}

impl State {
    pub fn new(init: &Value) -> State {
        let s = Settings::from(init);
        State {
            init: s.clone(),
            cur: s,
            clock: 1_700_000_000.0,
            queue: Vec::new(),
            tombs: BTreeMap::new(),
            removed: BTreeSet::new(),
            handled: BTreeSet::new(),
            waitlist: BTreeMap::new(),
            removed_tonight: BTreeMap::new(),
            attention: BTreeMap::new(),
            failed: BTreeSet::new(),
            terminal_titles: BTreeMap::new(),
            aliases: BTreeMap::new(),
            deferred: Vec::new(),
            playing: false,
            queue_rev: 0,
            pending: BTreeMap::new(),
            lock_count: 0,
            next_uid: 1,
            singer_alias: HashMap::new(),
            next_local: 0,
        }
    }

    /// Apply one event and return the output line `{"result":..,"snapshot":..}`.
    pub fn step(&mut self, ev: &Value) -> String {
        let (ok, code) = self.apply(ev);
        self.refresh_aliases();
        json!({"result": {"ok": ok, "code": code}, "snapshot": self.snapshot()}).to_string()
    }

    fn refresh_aliases(&mut self) {
        for s in &self.queue {
            if !self.singer_alias.contains_key(&s.uid) {
                let n = self.singer_alias.len() + 1;
                self.singer_alias.insert(s.uid, n);
            }
        }
    }

    pub fn apply(&mut self, ev: &Value) -> (bool, String) {
        let op = ev.get("op").and_then(Value::as_str).unwrap_or("");
        let known = [
            "config", "tick", "add_song", "remote_arrive", "remove_song", "remove_singer", "move_singer", "move_song", "skip_singer",
            "rotation_advance", "complete_song", "waitlist_add", "waitlist_remove", "waitlist_restore", "rotation_lock", "restart", "load", "set_playing", "flush_deferred", "rename_singer", "rename_merge",
        ];
        if !known.contains(&op) {
            return (false, "unknown_op".into());
        }
        match ev.get("now").and_then(Value::as_f64) {
            Some(now) if op != "load" => self.clock = now, // live shadow: the app's own clock
            _ => self.clock += 1.0,
        }
        let (ok, code) = match op {
            "load" => self.op_load(ev),
            "config" => self.op_config(ev),
            "tick" => {
                self.clock += ev.get("seconds").and_then(Value::as_f64).unwrap_or(1.0);
                ROUTE_OK.into_pair()
            }
            "add_song" => self.op_add_song(ev),
            "remote_arrive" => self.op_remote_arrive(ev),
            "remove_song" => self.op_remove_song(ev),
            "remove_singer" => self.op_remove_singer(ev),
            "move_singer" => self.op_move_singer(ev),
            "move_song" => self.op_move_song(ev),
            "skip_singer" => self.op_skip_singer(ev),
            "rotation_advance" => self.op_rotation_advance(ev),
            "complete_song" => self.op_complete_song(ev),
            "restart" => self.op_restart(),
            "rotation_lock" => self.op_rotation_lock(),
            "waitlist_add" => self.op_waitlist_add(ev),
            "waitlist_remove" => self.op_waitlist_remove(ev),
            "waitlist_restore" => self.op_waitlist_restore(ev),
            "set_playing" => {
                self.playing = ev.get("playing").map(truthy).unwrap_or(false);
                (true, format!("playing={}", self.playing as i32))
            }
            "flush_deferred" => self.op_flush_deferred(ev),
            "rename_singer" => self.op_rename_singer(ev),
            "rename_merge" => self.op_rename_merge(ev),
            _ => (false, "unsupported".into()),
        };
        (ok, code)
    }

    // ------------------------------------------------------------------------------------------------ helpers
    fn singer_by_ref(&self, r: &str) -> Option<usize> {
        if let Some(n) = r.strip_prefix('S').and_then(|x| x.parse::<usize>().ok()) {
            return self.queue.iter().position(|s| self.singer_alias.get(&s.uid) == Some(&n));
        }
        self.queue.iter().position(|s| s.name == r)
    }

    fn entry_by_ref(&self, r: &str) -> Option<(usize, usize)> {
        for (si, s) in self.queue.iter().enumerate() {
            for (sj, e) in s.songs.iter().enumerate() {
                if e.alias() == r {
                    return Some((si, sj));
                }
            }
        }
        None
    }

    fn marker_index(&self) -> Option<usize> {
        self.queue.iter().position(|s| s.marker)
    }

    fn boundary_index(&self) -> usize {
        for (i, s) in self.queue.iter().enumerate() {
            if !s.is_active() || s.round_sung {
                return i;
            }
        }
        self.queue.len()
    }

    fn recompute_round_state(&mut self) {
        if !self.cur.rotation() {
            return;
        }
        let active: Vec<usize> = (0..self.queue.len()).filter(|&i| self.queue[i].is_active()).collect();
        if active.is_empty() {
            return;
        }
        if active.iter().all(|&i| self.queue[i].round_sung) {
            for i in active {
                self.queue[i].round_sung = false;
            }
        }
    }

    fn repair_marker(&mut self) {
        if !self.cur.rotation() || self.queue.is_empty() {
            return;
        }
        let marked: Vec<usize> = (0..self.queue.len()).filter(|&i| self.queue[i].marker).collect();
        if marked.len() == 1 {
            return;
        }
        let keep = if marked.len() > 1 { marked[0] } else { 0 };
        for s in &mut self.queue {
            s.marker = false;
        }
        self.queue[keep].marker = true;
    }

    fn clear_temp_slot(s: &mut Singer) {
        s.temp_empty = false;
        s.until = 0.0;
    }

    fn prune_expired_slots(&mut self) {
        if !self.cur.rotation() {
            return;
        }
        let now = self.clock;
        for i in (0..self.queue.len()).rev() {
            let s = &mut self.queue[i];
            if !s.songs.is_empty() {
                Self::clear_temp_slot(s);
                continue;
            }
            if !s.temp_empty {
                continue;
            }
            if now >= s.until {
                Self::clear_temp_slot(s);
            }
        }
    }

    /// `_remove_local_remote_request_by_id`: drop every queued copy of a request id. Afterwards EVERY empty singer row (not only the one that
    /// lost a song) is kept: in rotation mode it is (re)marked as a temporary empty slot with a fresh 180 s expiry; the row is never deleted.
    fn remove_local_by_id(&mut self, rid: u64) {
        let mut removed = 0;
        for si in (0..self.queue.len()).rev() {
            let before = self.queue[si].songs.len();
            self.queue[si].songs.retain(|e| e.rid != Some(rid));
            removed += before - self.queue[si].songs.len();
            if self.queue[si].songs.is_empty() {
                self.mark_slot_empty(si);
            }
        }
        if removed > 0 {
            self.queue_rev += 1;
        }
    }

    /// `_record_host_singer_order_change` (via `_sync_remote_singer_order`): stamp the singer as host-ordered now, bump the revision and
    /// remember the singer's request-id order until the server echoes it.
    fn record_order(&mut self, si: usize) {
        if si >= self.queue.len() {
            return;
        }
        self.queue_rev += 1;
        let rev = self.queue_rev;
        let now = (self.clock * 1000.0) as i64;
        let s = &mut self.queue[si];
        let prev = coerce_i(s.host_ts).max(coerce_i(s.singer_ts));
        let stamp = if now <= prev { prev + 1 } else { now };
        s.host_ts = stamp;
        s.order_rev = rev;
        s.order_src = "host".into();
        let key = s.name.trim().to_lowercase();
        let ids: Vec<u64> = s.songs.iter().filter_map(|e| e.rid).collect();
        if !key.is_empty() && !ids.is_empty() {
            self.pending.insert(key, Pending { ids, host: stamp, rev });
        }
    }

    fn mark_slot_empty(&mut self, si: usize) {
        if !self.cur.rotation() || !self.queue[si].songs.is_empty() {
            return;
        }
        let now = self.clock;
        let s = &mut self.queue[si];
        s.temp_empty = true;
        s.until = now + 180.0;
    }

    fn tombstone(&mut self, rid: u64, entry: &Entry, singer: &str, reason: &str, status: &str) {
        if rid == 0 {
            return;
        }
        if self.tombs.contains_key(&rid) {
            self.removed.insert(rid);
            return;
        }
        self.queue_rev += 1;
        let (artist, title) = (entry.artist.trim().to_string(), entry.title.trim().to_string());
        self.tombs.insert(rid, Tomb { status: status.into(), reason: reason.into(), singer: singer.trim().into(), artist, title });
        self.removed.insert(rid);
    }

    fn live_counts(&self) -> HashMap<String, i64> {
        let mut c = HashMap::new();
        for s in &self.queue {
            if s.skipped {
                continue;
            }
            for e in &s.songs {
                if e.skipped {
                    continue;
                }
                for p in participants(&s.name, e.duet.as_deref()) {
                    let k = name_key(&p);
                    if !k.is_empty() {
                        *c.entry(k).or_insert(0) += 1;
                    }
                }
            }
        }
        c
    }

    /// `_pending_request_counts_by_singer(include_deferred=True)`: held requests count against their participants (once per request id; `exclude` is the request being decided).
    fn add_deferred_counts(&self, counts: &mut HashMap<String, i64>, exclude: Option<u64>) {
        let mut seen = BTreeSet::new();
        for d in &self.deferred {
            if Some(d.rid) == exclude || !seen.insert(d.rid) {
                continue;
            }
            for p in req_participants(&d.item) {
                let k = name_key(&p);
                if !k.is_empty() {
                    *counts.entry(k).or_insert(0) += 1;
                }
            }
        }
    }

    /// `_check_queue_song_limit` with fresh live counts: true when no participant is at the cap.
    fn limit_ok(&mut self, parts: &[String], exclude: Option<u64>) -> bool {
        self.cleanup_duplicate_songs();
        let mut counts = self.live_counts();
        self.add_deferred_counts(&mut counts, exclude);
        let limit = self.cur.active_limit();
        !parts.iter().any(|p| {
            let k = name_key(p);
            !k.is_empty() && counts.get(&k).copied().unwrap_or(0) >= limit
        })
    }

    fn cleanup_duplicate_songs(&mut self) {
        for s in &mut self.queue {
            if s.songs.len() < 2 {
                continue;
            }
            let mut seen = BTreeSet::new();
            let mut keep = Vec::new();
            for e in s.songs.drain(..) {
                if e.skipped || e.identity().0 == 2 || seen.insert(e.identity()) {
                    keep.push(e);
                }
            }
            s.songs = keep;
        }
    }

    fn find_duplicate(&self, e: &Entry) -> bool {
        if e.identity().0 == 2 {
            return false;
        }
        self.queue.iter().any(|s| s.songs.iter().any(|x| !x.skipped && x.identity() == e.identity()))
    }

    /// `_add_song_to_queue` (no duets). `remote` = the call carried remote_meta.
    fn add_song(&mut self, singer_name: &str, mut entry: Entry, remote: bool) -> bool {
        self.prune_expired_slots();
        let (mut primary, duet) = parse_duet(&capitalize(singer_name));
        if primary.is_empty() {
            return false;
        }
        if remote {
            primary = self.resolve_alias(&primary); // a retired name attaches to the surviving singer
        }
        entry.skipped = false;
        entry.duet = duet.clone();
        if self.find_duplicate(&entry) {
            if let Some(r) = entry.rid {
                self.handled.insert(r); // _mark_waiting_for_add_duplicate_accepted: handled, and off the waitlist and the attention list
                self.waitlist.remove(&r);
                self.attention.remove(&r);
            }
            return true;
        }
        let parts = participants(&primary, duet.as_deref());
        if !self.limit_ok(&parts, if remote { entry.rid } else { None }) && remote {
            return false; // the operator's own add may exceed the cap
        }
        let k = name_key(&primary);
        // _queue_singer_match_index: a server singer id first (any row, whatever its name), then the same name with the same session, then the name alone
        let by_id = if remote && !entry.sid.is_empty() { self.queue.iter().position(|s| s.server_id == entry.sid.trim()) } else { None };
        let by_session = if by_id.is_none() && remote && entry.session > 0 && !k.is_empty() { self.queue.iter().position(|s| name_key(&s.name) == k && s.server_session == entry.session) } else { None };
        let matched = by_id.or(by_session).or_else(|| if k.is_empty() { None } else { self.queue.iter().position(|s| name_key(&s.name) == k) });
        if let Some(i) = matched {
            let s = &mut self.queue[i];
            if remote && entry.session > 0 {
                s.server_session = entry.session;
            }
            let rsid = entry.sid.trim().to_string();
            if !rsid.is_empty() && s.server_id.is_empty() {
                s.server_id = rsid;
            }
            s.songs.push(entry);
            Self::clear_temp_slot(s);
            if self.is_locked() {
                self.reweave_locked_tail(); // an existing singer stays in their slot; keep the locked newcomers interleaved
            }
            self.record_order(i);
            if remote {
                self.cleanup_duplicate_songs();
            }
            return true;
        }
        let uid = self.next_uid;
        self.next_uid += 1;
        let new_uid = uid;
        let (nsid, nses) = (entry.sid.trim().to_string(), entry.session);
        let mut new = Singer { uid, name: primary, skipped: false, has_sung: false, round_sung: false, marker: false, temp_empty: false, until: 0.0, lock_new: false, lock_order: 0, host_ts: 0, singer_ts: 0, order_rev: 0, order_src: String::new(), server_id: String::new(), server_session: 0, created: self.clock, songs: vec![entry] };
        new.server_id = nsid;
        if remote && nses > 0 {
            new.server_session = nses;
        }
        if self.cur.rotation() {
            self.recompute_round_state();
            if self.is_locked() {
                self.insert_locked_new_singer(new); // woven into the NEXT rotation, never ahead of the active singers
            } else {
                let mut ins = match self.marker_index() {
                    Some(m) => m,
                    None => self.boundary_index(),
                };
                if ins == 0 && !self.queue.is_empty() {
                    ins = self.queue.len();
                }
                self.queue.insert(ins, new);
            }
            self.repair_marker();
            self.recompute_round_state();
        } else {
            self.queue.push(new);
        }
        if let Some(i) = self.queue.iter().position(|s| s.uid == new_uid) {
            self.record_order(i);
        }
        true
    }

    // ------------------------------------------------------------------------------------------------ rotation lock
    /// `_rotation_lock_can_enable`: rotation mode, a non-empty queue, and the marked singer is not already next.
    fn lock_can_enable(&self) -> bool {
        self.cur.rotation() && !self.queue.is_empty() && self.marker_index() != Some(0)
    }

    /// `_is_rotation_locked`: the saved setting only counts while the lock can be enabled (the setting itself stays set).
    fn is_locked(&self) -> bool {
        self.cur.rotation() && self.cur.locked && self.lock_can_enable()
    }

    fn clear_lock(&mut self) {
        self.cur.locked = false;
        self.lock_count = 0;
        for s in &mut self.queue {
            s.lock_new = false;
            s.lock_order = 0;
        }
    }

    /// `_rotation_reweave_locked_tail`: from the marker (or the first locked newcomer) on, old singers and locked newcomers alternate,
    /// old first, a marked singer with no active songs pinned in front.
    fn reweave_locked_tail(&mut self) {
        if !self.is_locked() || self.queue.is_empty() {
            return;
        }
        let marker = self.marker_index();
        let pinned = marker.filter(|&m| !self.queue[m].is_active()).map(|m| self.queue[m].uid);
        let first_new = self.queue.iter().position(|s| s.lock_new);
        let Some(tail_start) = [marker, first_new].into_iter().flatten().min() else { return };
        let mut tail: Vec<Singer> = self.queue.split_off(tail_start);
        let mut pinned_singer = None;
        if let Some(uid) = pinned {
            if let Some(pos) = tail.iter().position(|s| s.uid == uid) {
                pinned_singer = Some(tail.remove(pos));
            }
        }
        let (mut new_tail, old_tail): (Vec<Singer>, Vec<Singer>) = tail.into_iter().partition(|s| s.lock_new);
        new_tail.sort_by_key(|s| s.lock_order);
        let mut woven: Vec<Singer> = Vec::new();
        woven.extend(pinned_singer);
        let (mut old_it, mut new_it) = (old_tail.into_iter(), new_tail.into_iter());
        loop {
            let (a, b) = (old_it.next(), new_it.next());
            if a.is_none() && b.is_none() {
                break;
            }
            woven.extend(a);
            woven.extend(b);
        }
        self.queue.extend(woven);
    }

    /// `_rotation_insert_locked_new_singer`.
    fn insert_locked_new_singer(&mut self, mut new: Singer) {
        new.lock_new = true;
        new.lock_order = self.lock_count;
        self.lock_count += 1;
        let marker = self.marker_index();
        if marker == Some(0) {
            self.queue.push(new);
            self.clear_lock();
            return;
        }
        let ins = match marker {
            Some(m) => m + 1,
            None => self.boundary_index(),
        };
        self.queue.insert(ins.min(self.queue.len()), new);
        self.reweave_locked_tail();
    }

    /// The Lock/Unlock button (`toggle_rotation_lock_button`).
    fn op_rotation_lock(&mut self) -> (bool, String) {
        if !self.cur.rotation() || self.is_locked() || !self.lock_can_enable() {
            self.clear_lock();
        } else {
            self.cur.locked = true;
            self.lock_count = 0;
        }
        (true, format!("lock={}", self.cur.locked as i32))
    }

    /// `_record_remote_attention_request` + the forced waitlist upsert: a request the app could not add stays visible to the host.
    fn record_attention(&mut self, item: &Value, reason: &str) {
        let rid = request_id_of(item);
        if rid <= 0 {
            return;
        }
        let rid = rid as u64;
        let mut it = item.clone();
        if let Some(o) = it.as_object_mut() {
            o.insert("attention_reason".into(), json!(reason));
        }
        self.attention.insert(rid, it.clone());
        if non_actionable(&it) || self.handled.contains(&rid) {
            return;
        }
        if self.queue.iter().any(|s| s.songs.iter().any(|e| e.rid == Some(rid))) || self.deferred.iter().any(|d| d.rid == rid) {
            self.handled.insert(rid); // a held (deferred) request counts as accepted too
            self.waitlist.remove(&rid);
            self.attention.remove(&rid);
            return;
        }
        self.waitlist.insert(rid, it);
    }

    /// `process_external_request` for one request row (cap, then the remote add). Err carries nothing; the caller records what it needs.
    fn process_external(&mut self, req: &Value, singer: &str, mut entry: Entry, allow_defer: bool) -> bool {
        entry.order = Some(add_request_order(req, entry.rid));
        entry.sid = ["singer_id", "singer_uid"].iter().filter_map(|k| req.get(*k)).find(|v| !matches!(v, Value::Null) && !matches!(v, Value::String(t) if t.is_empty())).map(pystr).unwrap_or_default();
        entry.session = py_int0(req.get("singer_session_id")).max(0); // only a positive session is stored on the entry
        let parts = req_participants(req);
        if !self.limit_ok(&parts, entry.rid) {
            return false; // _record_remote_limit_blocked_request: nothing is recorded locally
        }
        self.apply_resolved(req, singer, entry, allow_defer)
    }

    /// `_apply_resolved_remote_add`: hold the request while a song plays (when the setting is on), else add it. (Its duplicate test never fires for a row with a
    /// request id: the request's own id is removed from the accepted-id set first.)
    fn apply_resolved(&mut self, req: &Value, singer: &str, entry: Entry, allow_defer: bool) -> bool {
        let rid = entry.rid;
        let parts = req_participants(req);
        if !self.limit_ok(&parts, rid) {
            return false;
        }
        if allow_defer && self.cur.defer && self.playing {
            if let Some(r) = rid {
                // _queue_deferred_remote_add: one payload per id, ordered by the time it was requested (stable)
                self.deferred.retain(|d| d.rid != r);
                self.deferred.push(Deferred { rid: r, singer: singer.to_string(), entry, item: req.clone(), request_time: self.clock });
                self.deferred.sort_by(|a, b| a.request_time.partial_cmp(&b.request_time).unwrap_or(std::cmp::Ordering::Equal));
                return true;
            }
            return false;
        }
        if self.add_song(singer, entry, true) {
            if let Some(r) = rid {
                self.attention.remove(&r); // _clear_remote_attention_request
                self.waitlist.remove(&r);
            }
            true
        } else {
            self.record_attention(req, "queue_insert_failed");
            false
        }
    }

    /// `_flush_deferred_remote_adds`: apply every held request (the list itself is rewritten only at the END, so requests already added still count as held while the
    /// next one is checked against the cap).
    fn op_flush_deferred(&mut self, ev: &Value) -> (bool, String) {
        let force = ev.get("force").map(truthy).unwrap_or(false);
        if self.playing && !force {
            return (true, "flushed=0".into());
        }
        let pending = self.deferred.clone();
        if pending.is_empty() {
            return (true, "flushed=0".into());
        }
        let mut added = 0;
        let mut remaining = Vec::new();
        for d in pending {
            if self.apply_resolved(&d.item, &d.singer, d.entry.clone(), false) {
                added += 1;
            } else {
                remaining.push(d);
            }
        }
        self.deferred = remaining;
        (true, format!("flushed={added}"))
    }

    // ------------------------------------------------------------------------------------------------ ops
    fn op_config(&mut self, ev: &Value) -> (bool, String) {
        if let Some(v) = ev.get("queue_mode").and_then(Value::as_str) {
            self.cur.mode = v.into();
        }
        if let Some(v) = ev.get("limit_pending_max").and_then(Value::as_i64) {
            self.cur.limit = v;
        }
        if let Some(v) = ev.get("requests_accepting").and_then(Value::as_bool) {
            self.cur.accepting = v;
        }
        if let Some(v) = ev.get("waitlist") {
            self.cur.waitlist = truthy(v);
        }
        if let Some(v) = ev.get("rotation_locked") {
            self.cur.locked = truthy(v);
        }
        if let Some(v) = ev.get("defer") {
            self.cur.defer = truthy(v);
        }
        ROUTE_OK.into_pair()
    }

    fn op_add_song(&mut self, ev: &Value) -> (bool, String) {
        let singer = ev.get("singer").and_then(Value::as_str).unwrap_or("");
        let artist = ev.get("artist").and_then(Value::as_str).unwrap_or("");
        let title = ev.get("title").and_then(Value::as_str).unwrap_or("");
        let key = ev.get("key").and_then(Value::as_i64).unwrap_or(0);
        self.next_local += 1;
        let entry = Entry { rid: None, local: Some(self.next_local), artist: String::new(), title: title.into(), song_info: format!("/lib/{artist}-{title}.zip"), duet: None, key, tempo: None, skipped: false, order: None, sid: String::new(), session: 0 };
        // artist is stored from the track dict, which carries the event artist; the harness' local entries use it for the song path only
        let mut entry = entry;
        entry.artist = artist.into();
        let ok = self.add_song(singer, entry, false);
        if !ok {
            self.next_local -= 1;
        }
        (ok, if ok { "added" } else { "refused" }.into())
    }

    fn op_remote_arrive(&mut self, ev: &Value) -> (bool, String) {
        self.prune_expired_slots();
        self.merge_duplicate_singers();
        let rows = ev.get("requests").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut local_ids: BTreeSet<u64> = self.queue.iter().flat_map(|s| s.songs.iter().filter_map(|e| e.rid)).collect();
        local_ids.extend(self.deferred.iter().map(|d| d.rid)); // the reconcile counts held requests as local
        self.set_waiting(&rows, &local_ids);
        let waiting_ids: BTreeSet<u64> = self.waitlist.keys().copied().collect();
        struct Req {
            rid: u64,
            singer: String,
            queue_singer: String,
            artist: String,
            title: String,
            key: i64,
            tempo: i64,
            sort: Option<Value>,
            meta: OrderMeta,
            session: Value,
        }
        let mut normalized: Vec<Req> = Vec::new();
        for row in &rows {
            let Some(o) = row.as_object() else { continue };
            let rid = o.get("request_id").and_then(Value::as_i64).unwrap_or(0);
            if rid <= 0 {
                continue;
            }
            let rid = rid as u64;
            let state = state_of(row, &["state"]);
            let stamp = |k: &str| to_f64(row.get(k)).map(|f| f.trunc() as i64).unwrap_or(0);
            let (removed_at, completed_at) = (stamp("removed_at"), stamp("completed_at"));
            if matches!(state.as_str(), "removed" | "completed" | "sung") || removed_at > 0 || completed_at > 0 {
                // The server already knows this request is done: the queued copy goes, the id is remembered as terminal together with the
                // title it carried. No tombstone is written or retired (that is host-action state), and this runs BEFORE the tombstone check.
                self.remove_local_by_id(rid);
                self.deferred.retain(|d| d.rid != rid); // _remove_deferred_remote_add_by_id
                self.removed.insert(rid);
                self.terminal_titles.insert(rid, row_str(row, "title"));
                continue;
            }
            let title = o.get("title").and_then(Value::as_str).unwrap_or("").trim().to_string();
            // admission, in the app's order: tombstone first, then the removed-id memory
            if let Some(t) = self.tombs.get(&rid).cloned() {
                if singws_queue::titles_conflict(&t.title, &title) {
                    self.tombs.remove(&rid);
                    self.removed.remove(&rid);
                } else {
                    self.removed.insert(rid);
                    continue;
                }
            }
            if self.removed.contains(&rid) {
                // Known-terminal id with no live tombstone: only a clear title conflict with the remembered terminal title (the id was
                // reused by a new request) lets the row through; otherwise it is a stale flip-back and is ignored.
                let tt = self.terminal_titles.get(&rid).cloned().unwrap_or_default();
                if !tt.is_empty() && singws_queue::titles_conflict(&tt, &row_str(row, "title")) {
                    self.removed.remove(&rid);
                } else {
                    continue;
                }
            }
            if is_waiting_row(row) {
                continue; // held for the host: the waitlist itself was rebuilt above
            }
            if non_actionable(row) && !local_ids.contains(&rid) {
                continue; // left host intake and not in the local queue: historical
            }
            let singer = o.get("singer").and_then(Value::as_str).unwrap_or("").trim().to_string();
            if singer.is_empty() {
                continue;
            }
            let key = o.get("key").and_then(Value::as_i64).unwrap_or(0);
            let tempo = o.get("tempo").and_then(Value::as_i64).unwrap_or(0).clamp(-30, 30);
            let queue_singer = {
                let p = parse_duet(&singer).0;
                if p.is_empty() { singer.clone() } else { p }
            };
            normalized.push(Req { rid, singer, queue_singer, artist: o.get("artist").and_then(Value::as_str).unwrap_or("").trim().to_string(), title, key, tempo, sort: o.get("sort_order").cloned(), meta: order_meta_of(row), session: first_truthy_value(row, &["singer_session_id", "session_id"]) });
        }
        
        let mut existing: BTreeSet<u64> = self.queue.iter().flat_map(|s| s.songs.iter().filter_map(|e| e.rid)).filter(|r| *r > 0).collect();
        // local entries without a request id adopt a matching row by (singer, artist, title)
        let mut pool: Vec<usize> = (0..normalized.len()).collect();
        for s in &mut self.queue {
            let sn = s.name.trim().to_lowercase();
            for e in &mut s.songs {
                if e.rid.is_some() {
                    continue;
                }
                let (a, t) = (e.artist.trim().to_lowercase(), e.title.trim().to_lowercase());
                if a.is_empty() || t.is_empty() {
                    continue;
                }
                if let Some(pos) = pool.iter().position(|&i| normalized[i].queue_singer.to_lowercase() == sn && normalized[i].artist.to_lowercase() == a && normalized[i].title.to_lowercase() == t) {
                    let r = &normalized[pool.remove(pos)];
                    e.rid = Some(r.rid);
                    e.key = r.key;
                    e.tempo = Some(100 + r.tempo);
                    existing.insert(r.rid);
                }
            }
        }
        self.cleanup_duplicate_songs();
        let desired: BTreeSet<u64> = normalized.iter().map(|r| r.rid).collect();
        self.failed.retain(|id| desired.contains(id)); // forget ids the server no longer lists
        let mut counts = self.live_counts();
        self.add_deferred_counts(&mut counts, None);
        for r in &normalized {
            if existing.contains(&r.rid) || waiting_ids.contains(&r.rid) || self.failed.contains(&r.rid) {
                continue;
            }
            let item = json!({"request_id": r.rid, "singer": r.singer, "queue_singer": r.queue_singer, "artist": r.artist, "title": r.title, "key": r.key,
                              "tempo": r.tempo, "sort_order": r.sort.clone().unwrap_or(Value::Null), "singer_session_id": r.session.clone(), "pending_reason": "", "pending_status": "", "last_error": ""});
            // the intake cap check sees the queue singer only (a duet partner is checked later, by the add itself)
            let parts = req_participants(&item);
            let limit = self.cur.active_limit();
            if parts.iter().any(|p| { let k = name_key(p); !k.is_empty() && counts.get(&k).copied().unwrap_or(0) >= limit }) {
                continue;
            }
            if !self.cur.accepting {
                continue;
            }
            let entry = Entry {
                rid: Some(r.rid),
                local: None,
                artist: r.artist.clone(),
                title: r.title.clone(),
                song_info: format!("/lib/{}-{}.zip", r.artist, r.title),
                duet: None,
                key: r.key,
                tempo: Some(100 + r.tempo),
                skipped: false,
                order: None,
                sid: String::new(),
                session: 0,
            };
            if self.process_external(&item, &r.singer, entry, true) {
                for p in parts {
                    let k = name_key(&p);
                    if !k.is_empty() {
                        *counts.entry(k).or_insert(0) += 1;
                    }
                }
            } else {
                self.failed.insert(r.rid);
                self.record_attention(&item, "auto_accept_failed");
            }
        }
        // server key/tempo win for every queued request in the payload (last row wins); the stored request_order follows sort_order (else the id)
        let mut key_by: HashMap<u64, i64> = HashMap::new();
        let mut tempo_by: HashMap<u64, i64> = HashMap::new();
        let mut sort_by: HashMap<u64, f64> = HashMap::new();
        for r in &normalized {
            key_by.insert(r.rid, r.key);
            tempo_by.insert(r.rid, r.tempo);
            sort_by.insert(r.rid, r.sort.as_ref().and_then(py_float).unwrap_or(r.rid as f64));
        }
        for s in &mut self.queue {
            for e in &mut s.songs {
                let Some(rid) = e.rid else { continue };
                if let Some(k) = key_by.get(&rid) {
                    e.key = *k;
                }
                let t = tempo_by.get(&rid).copied().unwrap_or_else(|| e.tempo.unwrap_or(100) - 100).clamp(-30, 30);
                e.tempo = Some(100 + t);
                e.order = Some(sort_by.get(&rid).copied().unwrap_or_else(|| e.order.unwrap_or(rid as f64)));
            }
        }
        let desired_ids: BTreeSet<u64> = normalized.iter().map(|r| r.rid).collect();
        let mut desired_by: BTreeMap<String, Vec<u64>> = BTreeMap::new();
        let mut meta_by: BTreeMap<String, OrderMeta> = BTreeMap::new();
        for r in &normalized {
            let k = r.queue_singer.to_lowercase();
            desired_by.entry(k.clone()).or_default().push(r.rid);
            // _merge_remote_order_meta: maxima of the stamps and revision, the latest row's source
            let m = meta_by.entry(k).or_default();
            m.host = m.host.max(r.meta.host);
            m.singer = m.singer.max(r.meta.singer);
            m.rev = m.rev.max(r.meta.rev);
            m.source = r.meta.source.clone();
        }
        // sort_order, when the server sent one for any row of the singer, re-sorts that singer's ids (others sort last, in wire order)
        for (k, ids) in desired_by.iter_mut() {
            let mut lookup: HashMap<u64, f64> = HashMap::new();
            for r in normalized.iter().filter(|r| r.queue_singer.to_lowercase() == *k) {
                let Some(raw) = &r.sort else { continue };
                if matches!(raw, Value::Null) || matches!(raw, Value::String(t) if t.is_empty()) {
                    continue;
                }
                if let Some(f) = py_float(raw) {
                    lookup.insert(r.rid, f);
                }
            }
            if lookup.is_empty() {
                continue;
            }
            let mut payload_rank: HashMap<u64, usize> = HashMap::new();
            for (i, id) in ids.iter().enumerate() {
                payload_rank.insert(*id, i);
            }
            let keyf = |id: &u64| -> (f64, u64) {
                match lookup.get(id) {
                    Some(f) => (*f, *id),
                    None => (f64::INFINITY, payload_rank.get(id).copied().unwrap_or(0) as u64),
                }
            };
            ids.sort_by(|a, b| {
                let (ka, kb) = (keyf(a), keyf(b));
                ka.0.partial_cmp(&kb.0).unwrap_or(std::cmp::Ordering::Equal).then(ka.1.cmp(&kb.1))
            });
        }
        // a pending host order (pushed, not yet echoed by the server) is kept against a stale echo and cleared by the real one
        let pkeys: Vec<String> = self.pending.keys().cloned().collect();
        for k in pkeys {
            let item = self.pending[&k].clone();
            let pending_ids: Vec<u64> = item.ids.iter().copied().filter(|i| desired_ids.contains(i)).collect();
            let server_ids = desired_by.get(&k).cloned().unwrap_or_default();
            let (ps, ss): (BTreeSet<u64>, BTreeSet<u64>) = (pending_ids.iter().copied().collect(), server_ids.iter().copied().collect());
            if !pending_ids.is_empty() && ps == ss {
                let meta = meta_by.get(&k).cloned().unwrap_or_default();
                if server_ids == pending_ids {
                    if meta.host != 0 && item.host != 0 && meta.host < item.host {
                        desired_by.insert(k.clone(), pending_ids);
                        continue;
                    }
                    self.pending.remove(&k);
                } else if !(meta.source == "singer" && incoming_ts(&meta) > item.host) {
                    desired_by.insert(k.clone(), pending_ids);
                }
            }
        }
        for si in 0..self.queue.len() {
            let k = self.queue[si].name.trim().to_lowercase();
            let Some(order) = desired_by.get(&k).cloned() else { continue };
            if order.is_empty() {
                continue;
            }
            let meta = meta_by.get(&k).cloned().unwrap_or_default();
            if self.queue[si].songs.len() < 2 {
                continue;
            }
            let (local_host, local_singer) = (coerce_i(self.queue[si].host_ts), coerce_i(self.queue[si].singer_ts));
            let local_rev = self.queue[si].order_rev;
            let in_ts = incoming_ts(&meta);
            let pending_ids: Vec<u64> = self.pending.get(&k).map(|p| p.ids.iter().copied().filter(|i| desired_ids.contains(i)).collect()).unwrap_or_default();
            let force_pending = !pending_ids.is_empty() && pending_ids.iter().collect::<BTreeSet<_>>() == order.iter().collect::<BTreeSet<_>>() && order == pending_ids;
            let allow = if force_pending {
                true
            } else if local_host > 0 {
                (meta.source == "singer" && in_ts > local_host)
                    || (meta.source == "host" && in_ts >= local_host)
                    || (meta.rev > local_rev && in_ts >= local_host.max(local_singer))
            } else if local_singer > 0 && in_ts != 0 && in_ts < local_singer {
                false
            } else {
                true
            };
            if !allow {
                continue;
            }
            let mut rank: HashMap<u64, usize> = HashMap::new();
            for (i, r) in order.iter().enumerate() {
                rank.insert(*r, i);
            }
            let s = &mut self.queue[si];
            let positions: Vec<usize> = (0..s.songs.len()).filter(|&p| s.songs[p].rid.map(|r| rank.contains_key(&r)).unwrap_or(false)).collect();
            let mut ranked: Vec<Entry> = positions.iter().map(|&p| s.songs[p].clone()).collect();
            ranked.sort_by_key(|e| rank[&e.rid.unwrap()]);
            for (p, e) in positions.into_iter().zip(ranked) {
                s.songs[p] = e;
            }
            // _apply_order_meta_to_singer
            if meta.host != 0 {
                s.host_ts = coerce_i(s.host_ts).max(meta.host);
            }
            if meta.singer != 0 {
                s.singer_ts = coerce_i(s.singer_ts).max(meta.singer);
            }
            if meta.rev != 0 {
                s.order_rev = s.order_rev.max(meta.rev);
            }
            if meta.host != 0 || meta.singer != 0 || meta.rev != 0 {
                s.order_src = meta.source.clone();
            }
        }
        self.merge_duplicate_singers();
        self.cleanup_duplicate_songs();
        ROUTE_OK.into_pair()
    }

    /// `_set_waiting_for_add_requests` (the part that changes modelled state): with the waitlist on, the pending list is REPLACED by the
    /// waiting rows of this poll that are not queued and not handled; with it off only entries carrying an `attention_reason` survive.
    fn set_waiting(&mut self, rows: &[Value], local_ids: &BTreeSet<u64>) {
        if !self.cur.waitlist {
            self.waitlist.retain(|_, r| !first_truthy_str(r, &["attention_reason"]).trim().is_empty());
            return;
        }
        let mut candidates: BTreeMap<u64, Value> = BTreeMap::new();
        for row in rows {
            if !row.is_object() {
                continue;
            }
            let mapped = self.alias_mapped(row);
            let row = &mapped;
            let rid = request_id_of(row);
            if rid <= 0 {
                continue;
            }
            let rid = rid as u64;
            let state = state_of(row, &["state"]);
            let ts = |k: &str| to_f64(row.get(k)).map(|f| f.trunc() as i64).unwrap_or(0);
            if matches!(state.as_str(), "removed" | "skipped" | "completed" | "sung") || ts("completed_at") > 0 || ts("removed_at") > 0 {
                continue; // terminal rows only feed the server-side purge
            }
            if non_actionable(row) {
                self.handled.insert(rid);
                continue;
            }
            if !is_waiting_row(row) {
                continue;
            }
            if local_ids.contains(&rid) {
                self.handled.insert(rid);
                continue;
            }
            if self.handled.contains(&rid) {
                continue;
            }
            candidates.insert(rid, row.clone());
        }
        for (rid, req) in &self.attention {
            // a failed add stays host-visible; a server row for the same id wins (setdefault)
            if self.handled.contains(rid) || local_ids.contains(rid) {
                continue;
            }
            candidates.entry(*rid).or_insert_with(|| req.clone());
        }
        self.waitlist = candidates;
    }

    // ------------------------------------------------------------------------------------------------ rename / merge
    /// `_resolve_singer_alias`: follow retired names to the surviving one.
    fn resolve_alias(&self, name: &str) -> String {
        if self.aliases.is_empty() {
            return name.to_string();
        }
        let mut name = name.to_string();
        let mut key = name_key(&name);
        let mut seen = BTreeSet::new();
        while self.aliases.contains_key(&key) && seen.insert(key.clone()) {
            name = self.aliases[&key].clone();
            key = name_key(&name);
        }
        name
    }

    /// `_alias_mapped_request`: the row with its singer mapped through the aliases (copy on write).
    fn alias_mapped(&self, row: &Value) -> Value {
        let singer = first_truthy_str(row, &["singer"]);
        if singer.is_empty() {
            return row.clone();
        }
        let mapped = self.resolve_alias(&singer);
        if mapped != singer {
            let mut r = row.clone();
            if let Some(o) = r.as_object_mut() {
                o.insert("singer".into(), json!(mapped));
            }
            return r;
        }
        row.clone()
    }

    /// `_record_singer_rename_alias`.
    fn record_alias(&mut self, old: &str, new: &str) {
        let (ok, nk) = (name_key(old), name_key(new));
        if ok.is_empty() || nk.is_empty() || ok == nk {
            return;
        }
        self.aliases.insert(ok.clone(), new.to_string());
        let keys: Vec<String> = self.aliases.keys().cloned().collect();
        for k in keys {
            if name_key(&self.aliases[&k]) == ok {
                self.aliases.insert(k, new.to_string());
            }
        }
    }

    /// `_apply_singer_rename_to_requests`: stored request rows (waitlist, attention) and held payloads follow the renamed singer.
    fn apply_rename_to_requests(&mut self, old: &str, new: &str) {
        let ok = name_key(old);
        if ok.is_empty() || ok == name_key(new) {
            return;
        }
        for store in [&mut self.waitlist, &mut self.attention] {
            for item in store.values_mut() {
                if name_key(&first_truthy_str(item, &["singer"])) == ok {
                    if let Some(o) = item.as_object_mut() {
                        o.insert("singer".into(), json!(new));
                    }
                }
            }
        }
        for d in &mut self.deferred {
            if name_key(&d.singer) == ok {
                d.singer = new.to_string(); // only the payload's own singer; the request row inside keeps its text
            }
        }
    }

    /// `_merge_duplicate_rotation_singers`: rows whose names match (case/space-insensitively) become one. The oldest row survives; its songs keep their order and the
    /// others' songs follow. The merged row keeps the first slot, or the LAST slot when any member already sang.
    fn merge_duplicate_singers(&mut self) -> usize {
        let keys: Vec<String> = self.queue.iter().map(|s| name_key(&s.name)).collect();
        let mut groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
        for (i, k) in keys.iter().enumerate() {
            if !k.is_empty() {
                groups.entry(k.clone()).or_default().push(i);
            }
        }
        if groups.values().all(|g| g.len() < 2) {
            return 0;
        }
        let mut emit_at: HashMap<String, usize> = HashMap::new();
        for (k, g) in &groups {
            let late = g.len() > 1 && g.iter().any(|&i| self.queue[i].round_sung || self.queue[i].has_sung);
            emit_at.insert(k.clone(), if late { *g.last().unwrap() } else { g[0] });
        }
        let mut merged = 0;
        let mut taken: Vec<Option<Singer>> = std::mem::take(&mut self.queue).into_iter().map(Some).collect();
        let mut out: Vec<Singer> = Vec::new();
        for i in 0..keys.len() {
            let k = &keys[i];
            if k.is_empty() {
                out.push(taken[i].take().unwrap());
                continue;
            }
            if emit_at[k] != i {
                continue;
            }
            let g = &groups[k];
            if g.len() == 1 {
                out.push(taken[i].take().unwrap());
                continue;
            }
            let mut best = 0;
            for (n, &gi) in g.iter().enumerate() {
                if taken[gi].as_ref().unwrap().created < taken[g[best]].as_ref().unwrap().created {
                    best = n;
                }
            }
            let mut survivor = taken[g[best]].take().unwrap();
            for (n, &gi) in g.iter().enumerate() {
                if n == best {
                    continue;
                }
                let dup = taken[gi].take().unwrap();
                survivor.songs.extend(dup.songs);
                survivor.has_sung |= dup.has_sung;
                survivor.round_sung |= dup.round_sung;
                survivor.skipped = survivor.skipped && dup.skipped;
                survivor.marker |= dup.marker;
                let created: Vec<f64> = [survivor.created, dup.created].into_iter().filter(|c| *c != 0.0).collect();
                if !created.is_empty() {
                    survivor.created = created.iter().cloned().fold(f64::INFINITY, f64::min);
                }
                if survivor.lock_order == 0 && dup.lock_order != 0 {
                    survivor.lock_order = dup.lock_order;
                }
                if dup.lock_new && !survivor.lock_new {
                    survivor.lock_new = true;
                }
                merged += 1;
            }
            out.push(survivor);
        }
        self.queue = out;
        if merged > 0 {
            self.cleanup_duplicate_songs();
            if self.cur.rotation() {
                self.repair_marker();
                self.recompute_round_state();
            }
        }
        merged
    }

    /// `_rename_rotation_singer` (a merge into an existing row is allowed): true when the rename happened.
    fn rename_singer(&mut self, si: usize, new_name: &str) -> bool {
        if si >= self.queue.len() {
            return false;
        }
        let old = self.queue[si].name.trim().to_string();
        let new = new_name.trim().to_string();
        if old.is_empty() || new.is_empty() || new == old {
            return false;
        }
        self.queue[si].name = new.clone();
        self.merge_duplicate_singers();
        // the surviving row's casing wins over what was typed
        let k = name_key(&new);
        let canonical = if k.is_empty() { new.clone() } else { self.queue.iter().find(|s| name_key(&s.name) == k).map(|s| s.name.clone()).unwrap_or_else(|| new.clone()) };
        self.record_alias(&old, &canonical);
        self.apply_rename_to_requests(&old, &canonical);
        true
    }

    fn op_rename_merge(&mut self, ev: &Value) -> (bool, String) {
        let Some(si) = ev.get("singer").and_then(Value::as_str).and_then(|r| self.singer_by_ref(r)) else {
            return (false, "unknown_singer".into());
        };
        let ok = self.rename_singer(si, ev.get("name").and_then(Value::as_str).unwrap_or(""));
        (ok, if ok { "renamed" } else { "not_renamed" }.into())
    }

    /// `shell_queue_rename_singer`: refuses an empty / over-long / control-character name and a name that belongs to ANOTHER row.
    fn op_rename_singer(&mut self, ev: &Value) -> (bool, String) {
        let Some(si) = ev.get("singer").and_then(Value::as_str).and_then(|r| self.singer_by_ref(r)) else {
            return (false, "unknown_singer".into());
        };
        let name = ev.get("name").and_then(Value::as_str).unwrap_or("").trim().to_string();
        if name.is_empty() || name.chars().count() > 60 || name.chars().any(|c| c.is_control()) {
            return (false, "bad_name".into());
        }
        let old = self.queue[si].name.trim().to_string();
        if name == old {
            return (true, "no_change".into());
        }
        let key = name_key(&name);
        if self.queue.iter().enumerate().any(|(i, o)| i != si && name_key(&o.name) == key) {
            return (false, "name_taken".into());
        }
        if !self.rename_singer(si, &name) {
            return (false, "not_renamed".into());
        }
        (true, "renamed".into())
    }

    fn op_waitlist_add(&mut self, ev: &Value) -> (bool, String) {
        let rid = ev.get("request_id").and_then(Value::as_i64).unwrap_or(0);
        let Some(req) = (rid > 0).then(|| self.waitlist.get(&(rid as u64)).cloned()).flatten() else {
            return (false, "unknown_waitlist_request".into());
        };
        let rid = rid as u64;
        if req.get("replacement_track").map(truthy).unwrap_or(false) {
            return (false, "unsupported".into());
        }
        let singer = row_str(&req, "singer").trim().to_string();
        let artist = row_str(&req, "artist").trim().to_string();
        let title = row_str(&req, "title").trim().to_string();
        if singer.is_empty() || title.is_empty() {
            return (false, "unsupported".into()); // the library-match failure path (_unmatched_request_sigs) is not modelled
        }
        let int = |k: &str| to_f64(req.get(k)).map(|f| f as i64).unwrap_or(0);
        let key = int("key");
        let tempo = int("tempo").clamp(-30, 30);
        let entry = Entry { rid: Some(rid), local: None, artist: artist.clone(), title: title.clone(), song_info: format!("/lib/{artist}-{title}.zip"), duet: None, key, tempo: Some(100 + tempo), skipped: false, order: None, sid: String::new(), session: 0 };
        if !self.process_external(&req, &singer, entry, true) {
            return (false, "not_added".into());
        }
        self.handled.insert(rid);
        self.waitlist.remove(&rid);
        (true, "added".into())
    }

    fn op_waitlist_remove(&mut self, ev: &Value) -> (bool, String) {
        let rid = ev.get("request_id").and_then(Value::as_i64).unwrap_or(0);
        let Some(req) = (rid > 0).then(|| self.waitlist.get(&(rid as u64)).cloned()).flatten() else {
            return (false, "unknown_waitlist_request".into());
        };
        let rid = rid as u64;
        let mut item = req.clone();
        if let Some(o) = item.as_object_mut() {
            o.insert("request_id".into(), json!(rid));
            o.insert("state".into(), json!("removed"));
            o.insert("removal_reason".into(), json!("waiting_for_add_cleared"));
            o.insert("_removed_tonight".into(), json!(true));
        }
        self.removed_tonight.insert(rid, item);
        self.handled.insert(rid);
        let entry = Entry { rid: Some(rid), local: None, artist: row_str(&req, "artist"), title: row_str(&req, "title"), song_info: String::new(), duet: None, key: 0, tempo: None, skipped: false, order: None, sid: String::new(), session: 0 };
        self.tombstone(rid, &entry, &row_str(&req, "singer"), "waiting_for_add_cleared", "removed");
        self.waitlist.remove(&rid);
        (true, "removed".into())
    }

    fn op_waitlist_restore(&mut self, ev: &Value) -> (bool, String) {
        let rid = ev.get("request_id").and_then(Value::as_i64).unwrap_or(0);
        let Some(req) = (rid > 0).then(|| self.removed_tonight.get(&(rid as u64)).cloned()).flatten() else {
            return (false, "unknown_removed_request".into());
        };
        let rid = rid as u64;
        let mut restored = req;
        if let Some(o) = restored.as_object_mut() {
            for k in ["_removed_tonight", "removed_at", "removed_by", "removal_reason", "completed_at"] {
                o.remove(k);
            }
            o.insert("state".into(), json!("waiting"));
            o.insert("pending_status".into(), json!("Restored by host"));
            let reason = first_truthy_str(&Value::Object(o.clone()), &["pending_reason"]);
            o.insert("pending_reason".into(), json!(if reason.is_empty() { "host_restored".to_string() } else { reason }));
        }
        self.queue_rev += 1;
        self.waitlist.insert(rid, restored);
        self.removed_tonight.remove(&rid);
        self.handled.remove(&rid);
        self.removed.remove(&rid);
        self.tombs.remove(&rid);
        (true, "restored".into())
    }

    fn op_remove_song(&mut self, ev: &Value) -> (bool, String) {
        let Some((si, sj)) = ev.get("entry").and_then(Value::as_str).and_then(|r| self.entry_by_ref(r)) else {
            return (false, "unknown_entry".into());
        };
        let entry = self.queue[si].songs[sj].clone();
        let name = self.queue[si].name.clone();
        if let Some(rid) = entry.rid {
            self.tombstone(rid, &entry, &name, "host_remove_song", "removed");
        }
        self.queue[si].songs.remove(sj);
        if self.queue[si].songs.is_empty() {
            self.mark_slot_empty(si);
        }
        self.record_order(si);
        (true, "removed".into())
    }

    fn op_remove_singer(&mut self, ev: &Value) -> (bool, String) {
        let Some(si) = ev.get("singer").and_then(Value::as_str).and_then(|r| self.singer_by_ref(r)) else {
            return (false, "unknown_singer".into());
        };
        let name = self.queue[si].name.clone();
        for e in self.queue[si].songs.clone() {
            if let Some(rid) = e.rid {
                self.tombstone(rid, &e, &name, "host_remove_singer", "removed");
            }
        }
        self.queue.remove(si);
        (true, "removed".into())
    }

    fn op_move_singer(&mut self, ev: &Value) -> (bool, String) {
        let Some(i) = ev.get("singer").and_then(Value::as_str).and_then(|r| self.singer_by_ref(r)) else {
            return (false, "unknown_singer".into());
        };
        let where_ = ev.get("where").and_then(Value::as_str).unwrap_or("");
        let last = self.queue.len() - 1;
        if (matches!(where_, "up" | "top") && i == 0) || (matches!(where_, "down" | "bottom") && i >= last) {
            return (true, "no_change".into());
        }
        match where_ {
            "top" => {
                let s = self.queue.remove(i);
                self.queue.insert(0, s);
            }
            "bottom" => {
                let s = self.queue.remove(i);
                self.queue.push(s);
            }
            "up" => self.queue.swap(i - 1, i),
            "down" => self.queue.swap(i, i + 1),
            _ => return (false, "exception:unsupported_direction".into()),
        }
        (true, "moved".into())
    }

    fn op_move_song(&mut self, ev: &Value) -> (bool, String) {
        let Some((si, sj)) = ev.get("entry").and_then(Value::as_str).and_then(|r| self.entry_by_ref(r)) else {
            return (false, "unknown_entry".into());
        };
        let where_ = ev.get("where").and_then(Value::as_str).unwrap_or("");
        let songs = &mut self.queue[si].songs;
        let n = songs.len();
        if (matches!(where_, "up" | "top") && sj == 0) || (matches!(where_, "down" | "bottom") && sj + 1 >= n) {
            return (true, "no_change".into());
        }
        match where_ {
            "top" => {
                let e = songs.remove(sj);
                songs.insert(0, e);
            }
            "bottom" => {
                let e = songs.remove(sj);
                songs.push(e);
            }
            "up" => songs.swap(sj - 1, sj),
            "down" => songs.swap(sj, sj + 1),
            _ => return (false, "exception:unsupported_direction".into()),
        }
        self.record_order(si);
        (true, "moved".into())
    }

    fn op_skip_singer(&mut self, ev: &Value) -> (bool, String) {
        let Some(i) = ev.get("singer").and_then(Value::as_str).and_then(|r| self.singer_by_ref(r)) else {
            return (false, "unknown_singer".into());
        };
        let want = ev.get("skipped").and_then(Value::as_bool).unwrap_or(true);
        if self.queue[i].skipped == want {
            return (true, "no_change".into());
        }
        self.queue[i].skipped = want;
        (true, "changed".into())
    }

    fn op_rotation_advance(&mut self, ev: &Value) -> (bool, String) {
        // The replay keeps every alias it ever handed out, so the started singer may be one that has since left the queue (merged away or removed):
        // the selection then simply finds no "same singer" row. A display name is accepted too.
        let r = ev.get("singer").and_then(Value::as_str).unwrap_or("");
        let by_alias = r.strip_prefix('S').and_then(|x| x.parse::<usize>().ok()).and_then(|n| self.singer_alias.iter().find(|(_, v)| **v == n).map(|(u, _)| *u));
        let Some(started_uid) = by_alias.or_else(|| self.queue.iter().find(|s| s.name == r).map(|s| s.uid)) else {
            return (false, "unknown_singer".into());
        };
        let mut fallback: i64 = -1;
        for (i, s) in self.queue.iter().enumerate() {
            if s.skipped || !s.songs.iter().any(|e| !e.skipped) {
                continue;
            }
            if s.uid == started_uid {
                fallback = i as i64;
                continue;
            }
            return (true, format!("next_index={i}"));
        }
        (true, format!("next_index={fallback}"))
    }

    fn op_complete_song(&mut self, ev: &Value) -> (bool, String) {
        let Some((si, sj)) = ev.get("entry").and_then(Value::as_str).and_then(|r| self.entry_by_ref(r)) else {
            return (false, "unknown_entry".into());
        };
        let entry = self.queue[si].songs.remove(sj);
        let name = self.queue[si].name.clone();
        if let Some(rid) = entry.rid {
            self.tombstone(rid, &entry, &name, "song_completed", "completed");
        }
        self.queue[si].has_sung = true;
        (true, "completed".into())
    }

    /// Replace the whole state with a snapshot taken from the running app (the live shadow resynchronises before every checked event, so changes
    /// made by operations this model does not see never count as disagreements).
    fn op_load(&mut self, ev: &Value) -> (bool, String) {
        let st = Settings::from(ev.get("settings").unwrap_or(&Value::Null));
        self.init = st.clone();
        self.cur = st;
        self.queue.clear();
        self.tombs.clear();
        self.removed.clear();
        self.handled.clear();
        self.waitlist.clear();
        self.removed_tonight.clear();
        self.attention.clear();
        self.failed.clear();
        self.terminal_titles = ev.get("terminal_titles").and_then(Value::as_object).map(|o| o.iter().filter_map(|(k, v)| Some((k.parse::<u64>().ok()?, v.as_str()?.to_string()))).collect()).unwrap_or_default();
        self.deferred.clear();
        self.aliases.clear();
        self.playing = false;
        self.queue_rev = ev.get("queue_revision").and_then(Value::as_i64).unwrap_or(0);
        self.pending = ev
            .get("pending_order_syncs")
            .and_then(Value::as_object)
            .map(|o| {
                o.iter()
                    .map(|(k, v)| {
                        let ids = v.get("request_ids").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
                        (k.clone(), Pending { ids, host: v.get("host_order_updated_at").and_then(Value::as_i64).unwrap_or(0), rev: v.get("order_revision").and_then(Value::as_i64).unwrap_or(0) })
                    })
                    .collect()
            })
            .unwrap_or_default();
        self.lock_count = 0;
        self.singer_alias.clear();
        self.next_uid = 1;
        self.next_local = 0;
        let f = |v: &Value, k: &str| v.get(k).and_then(Value::as_bool).unwrap_or(false);
        for sv in ev.get("queue").and_then(Value::as_array).cloned().unwrap_or_default() {
            let mut songs = Vec::new();
            for e in sv.get("songs").and_then(Value::as_array).cloned().unwrap_or_default() {
                let rid = e.get("rid").and_then(Value::as_u64);
                let local = if rid.is_none() {
                    self.next_local += 1;
                    Some(self.next_local)
                } else {
                    None
                };
                let text = |k: &str| e.get(k).and_then(Value::as_str).unwrap_or("").to_string();
                songs.push(Entry { rid, local, artist: text("artist"), title: text("title"), song_info: text("song_info"), duet: e.get("duet_display").and_then(Value::as_str).map(String::from), key: e.get("key").and_then(Value::as_i64).unwrap_or(0), tempo: e.get("tempo").and_then(Value::as_i64), skipped: f(&e, "skipped"), order: e.get("request_order").and_then(Value::as_f64), sid: String::new(), session: 0 });
            }
            let uid = self.next_uid;
            self.next_uid += 1;
            self.queue.push(Singer {
                uid,
                name: sv.get("name").and_then(Value::as_str).unwrap_or("").to_string(),
                skipped: f(&sv, "skipped"),
                has_sung: f(&sv, "has_sung"),
                round_sung: f(&sv, "round_sung"),
                marker: f(&sv, "rotation_marker"),
                temp_empty: f(&sv, "temp_empty"),
                until: sv.get("until").and_then(Value::as_f64).unwrap_or(0.0),
                lock_new: f(&sv, "rotation_lock_new"),
                lock_order: sv.get("rotation_lock_order").and_then(Value::as_i64).unwrap_or(0),
                host_ts: sv.get("host_order_updated_at").and_then(Value::as_i64).unwrap_or(0),
                singer_ts: sv.get("singer_order_updated_at").and_then(Value::as_i64).unwrap_or(0),
                order_rev: sv.get("order_revision").and_then(Value::as_i64).unwrap_or(0),
                order_src: sv.get("last_order_source").and_then(Value::as_str).unwrap_or("").to_string(),
                server_id: sv.get("server_singer_id").and_then(Value::as_str).unwrap_or("").to_string(),
                server_session: sv.get("server_singer_session_id").and_then(Value::as_i64).unwrap_or(0),
                created: sv.get("created_at").and_then(Value::as_f64).unwrap_or(0.0),
                songs,
            });
        }
        if let Some(t) = ev.get("tombstones").and_then(Value::as_object) {
            for (k, v) in t {
                if let Ok(rid) = k.parse::<u64>() {
                    let text = |f: &str| v.get(f).and_then(Value::as_str).unwrap_or("").to_string();
                    self.tombs.insert(rid, Tomb { status: text("status"), reason: text("reason"), singer: text("singer"), artist: text("artist"), title: text("title") });
                }
            }
        }
        self.removed = ev.get("removed").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
        self.handled = ev.get("handled").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_u64).collect()).unwrap_or_default();
        for (field, target) in [("waitlist", &mut self.waitlist), ("removed_tonight", &mut self.removed_tonight)] {
            if let Some(a) = ev.get(field).and_then(Value::as_array) {
                for row in a {
                    let rid = request_id_of(row);
                    if rid > 0 {
                        target.insert(rid as u64, row.clone());
                    }
                }
            }
        }
        ROUTE_OK.into_pair()
    }

    fn op_restart(&mut self) -> (bool, String) {
        self.cur = self.init.clone();
        self.attention.clear();
        self.failed.clear();
        self.terminal_titles.clear();
        self.queue_rev = 0;
        self.pending.clear();
        self.playing = false; // the deferred list itself is persisted
        self.aliases.clear();
        self.lock_count = 0;
        self.removed = self.tombs.keys().copied().collect();
        (true, "restarted".into())
    }

    // ------------------------------------------------------------------------------------------------ snapshot
    pub fn snapshot(&self) -> Value {
        let mut in_queue: BTreeMap<u64, Vec<String>> = BTreeMap::new();
        let singers: Vec<Value> = self
            .queue
            .iter()
            .map(|s| {
                let songs: Vec<Value> = s
                    .songs
                    .iter()
                    .map(|e| {
                        if let Some(r) = e.rid {
                            in_queue.entry(r).or_default().push(e.alias());
                        }
                        json!({"id": e.alias(), "remote_request_id": e.rid, "artist": e.artist, "title": e.title, "song_info": e.song_info,
                               "key": e.key, "tempo_percent": e.tempo, "skipped": e.skipped, "duet_display": e.duet, "request_order": e.order, "singer_session_id": e.session})
                    })
                    .collect();
                json!({"id": format!("S{}", self.singer_alias.get(&s.uid).copied().unwrap_or(0)), "name": s.name, "skipped": s.skipped, "has_sung": s.has_sung,
                       "round_sung": s.round_sung, "rotation_marker": s.marker, "temporary_empty_slot": s.temp_empty, "rotation_lock_new": s.lock_new,
                       "rotation_lock_order": s.lock_order, "host_order_updated_at": s.host_ts, "singer_order_updated_at": s.singer_ts,
                       "order_revision": s.order_rev, "last_order_source": s.order_src, "server_singer_id": s.server_id,
                       "server_singer_session_id": s.server_session, "created_at": s.created, "songs": songs})
            })
            .collect();
        let view = |m: &BTreeMap<u64, Value>| -> Value {
            let mut o = Map::new();
            for (rid, r) in m {
                o.insert(rid.to_string(), json!({"singer": row_str(r, "singer"), "artist": row_str(r, "artist"), "title": row_str(r, "title"), "state": row_str(r, "state")}));
            }
            Value::Object(o)
        };
        let mut tombs = Map::new();
        for (rid, t) in &self.tombs {
            tombs.insert(rid.to_string(), json!({"status": t.status, "removal_reason": t.reason, "removed_by": "host", "singer_name": t.singer, "artist": t.artist, "title": t.title}));
        }
        let mut life = Map::new();
        let ids: BTreeSet<u64> = in_queue.keys().chain(self.tombs.keys()).chain(self.waitlist.keys()).chain(self.removed_tonight.keys()).copied().collect();
        for rid in ids {
            let state = if in_queue.contains_key(&rid) {
                "in_queue"
            } else if let Some(t) = self.tombs.get(&rid) {
                if matches!(t.status.as_str(), "completed" | "sung") { "completed" } else { "removed" }
            } else if self.waitlist.contains_key(&rid) {
                "waitlisted"
            } else {
                "removed_tonight"
            };
            life.insert(rid.to_string(), json!({"state": state, "copies_in_queue": in_queue.get(&rid).map(Vec::len).unwrap_or(0)}));
        }
        json!({
            "queue": singers,
            "rotation": {"mode": self.cur.mode, "locked": self.is_locked(), "lock_setting": self.cur.locked, "lock_insert_count": self.lock_count},
            "attention": self.attention.iter().map(|(k, v)| (k.to_string(), json!(row_str(v, "attention_reason")))).collect::<Map<String, Value>>(),
            "unmatched_remote_ids": self.failed.iter().collect::<Vec<_>>(),
            "waitlist": view(&self.waitlist), "removed_tonight": view(&self.removed_tonight), "handled_waitlist_ids": self.handled.iter().collect::<Vec<_>>(),
            "tombstones": tombs, "removed_ids": self.removed.iter().collect::<Vec<_>>(),
            "rename_aliases": self.aliases.iter().map(|(k, v)| (k.clone(), json!(v))).collect::<Map<String, Value>>(),
            "queue_revision": self.queue_rev,
            "pending_order_syncs": self.pending.iter().map(|(k, p)| (k.clone(), json!({"request_ids": p.ids, "host_order_updated_at": p.host, "order_revision": p.rev, "last_order_source": "host"}))).collect::<Map<String, Value>>(),
            "terminal_titles": self.terminal_titles.iter().map(|(k, v)| (k.to_string(), json!(v))).collect::<Map<String, Value>>(),
            "deferred_remote_adds": self.deferred.len(), "deferred": self.deferred.iter().map(|d| d.rid).collect::<Vec<_>>(), "lifecycle": life,
        })
    }
}

trait IntoPair {
    fn into_pair(self) -> (bool, String);
}
impl IntoPair for (bool, &str) {
    fn into_pair(self) -> (bool, String) {
        (self.0, self.1.to_string())
    }
}

#[cfg(test)]
mod waitlist_tests {
    use super::*;

    #[test]
    fn evening_key_groups_after_midnight_with_the_previous_evening() {
        // 2026-11-02 05:59:59 local still belongs to the 11-01 evening; 06:00:00 starts 11-02
        let day = 20_759i64 * 86_400; // 2026-11-02 00:00 (days since 1970-01-01)
        assert_eq!(waitlist_evening_key(day + 5 * 3600 + 3599), "2026-11-01");
        assert_eq!(waitlist_evening_key(day + 6 * 3600), "2026-11-02");
        assert_eq!(waitlist_evening_key(day + 23 * 3600), "2026-11-02");
        // leap day and year boundary
        assert_eq!(waitlist_evening_key(951_782_400 + 7 * 3600), "2000-02-29"); // 2000-02-29 07:00
        assert_eq!(waitlist_evening_key(946_684_800 + 3600), "1999-12-31");
    }

    #[test]
    fn removed_tonight_needs_same_evening_and_tenant() {
        assert!(removed_tonight_still_valid("2026-11-01", "wsk", "2026-11-01", "wsk"));
        assert!(!removed_tonight_still_valid("2026-10-31", "wsk", "2026-11-01", "wsk"));
        assert!(!removed_tonight_still_valid("2026-11-01", "a", "2026-11-01", "b"));
    }

    #[test]
    fn waiting_rules() {
        assert!(is_waiting_row(&json!({"state": "waiting", "sent": true})));
        assert!(is_waiting_row(&json!({"state": "pending", "pending_reason": "x"})));
        assert!(!is_waiting_row(&json!({"state": "pending", "pending_reason": "  "})));
        assert!(!is_waiting_row(&json!({"state": "delivered", "pending_reason": "x"})));
        assert!(non_actionable(&json!({"state": "accepted", "sent": 1})));
        assert!(!non_actionable(&json!({"state": "accepted"})));
        assert!(non_actionable(&json!({"state": "pending", "added_at": 5})));
    }

    #[test]
    fn add_remove_restore_roundtrip() {
        let mut st = State::new(&json!({"waitlist": true}));
        let row = json!({"request_id": 9, "singer": "Ada", "artist": "B", "title": "T", "state": "waiting"});
        st.apply(&json!({"op": "remote_arrive", "requests": [row]}));
        assert_eq!(st.apply(&json!({"op": "waitlist_remove", "request_id": 9})), (true, "removed".into()));
        assert_eq!(st.apply(&json!({"op": "waitlist_remove", "request_id": 9})), (false, "unknown_waitlist_request".into()));
        assert_eq!(st.apply(&json!({"op": "waitlist_restore", "request_id": 9})), (true, "restored".into()));
        assert_eq!(st.apply(&json!({"op": "waitlist_add", "request_id": 9})), (true, "added".into()));
        assert!(st.snapshot()["waitlist"].as_object().unwrap().is_empty());
    }
}

#[cfg(test)]
mod duet_lock_tests {
    use super::*;

    fn d(s: &str) -> (String, Option<String>) {
        parse_duet(s)
    }

    #[test]
    fn duet_parsing_matches_the_apps_regex() {
        assert_eq!(d("Amber and Delores"), ("Amber".into(), Some("Amber & Delores".into())));
        assert_eq!(d("a  WITH  b"), ("a".into(), Some("a & b".into())));
        assert_eq!(d("Snow/Joe"), ("Sno".into(), Some("Sno & Joe".into()))); // 'w/' matches inside a word
        assert_eq!(d("Ada, Bob"), ("Ada".into(), Some("Ada & Bob".into())));
        assert_eq!(d("Ada /w Bob"), ("Ada".into(), Some("Ada & Bob".into())));
        assert_eq!(d("Ada & Bob & Cy"), ("Ada".into(), Some("Ada & Bob & Cy".into())));
        assert_eq!(d("Andrea"), ("Andrea".into(), None));
        assert_eq!(d("Sandy"), ("Sandy".into(), None));
        assert_eq!(d("Flo &"), ("Flo &".into(), None));
        assert_eq!(d("& Flo"), ("& Flo".into(), None));
        assert_eq!(d("   "), (String::new(), None));
    }

    #[test]
    fn participants_are_deduplicated_and_the_third_name_stays_whole() {
        assert_eq!(participants("Ada", Some("Ada & Bob")), vec!["Ada".to_string(), "Bob".to_string()]);
        assert_eq!(participants("Ada", Some("Ada & Bob & Cy")), vec!["Ada".to_string(), "Bob & Cy".to_string()]);
        assert_eq!(participants("ADA", Some("ada & ada")), vec!["ADA".to_string()]);
    }

    #[test]
    fn lock_weaves_new_singers_after_the_marker() {
        let mut st = State::new(&json!({"queue_mode": "rotation"}));
        for (i, n) in ["Ada", "Bob", "Cy"].iter().enumerate() {
            st.apply(&json!({"op": "add_song", "singer": n, "title": format!("t{i}")}));
        }
        st.apply(&json!({"op": "move_singer", "singer": "Cy", "where": "top"}));
        assert_eq!(st.apply(&json!({"op": "rotation_lock"})), (true, "lock=1".into()));
        st.apply(&json!({"op": "add_song", "singer": "Dee", "title": "x"}));
        st.apply(&json!({"op": "add_song", "singer": "Eve", "title": "y"}));
        let names: Vec<String> = st.snapshot()["queue"].as_array().unwrap().iter().map(|s| s["name"].as_str().unwrap().to_string()).collect();
        assert_eq!(names.len(), 5);
        assert_eq!(st.snapshot()["rotation"]["lock_insert_count"], 2);
        assert_eq!(st.apply(&json!({"op": "rotation_lock"})), (true, "lock=0".into()));
        assert_eq!(st.snapshot()["rotation"]["lock_insert_count"], 0);
    }
}
