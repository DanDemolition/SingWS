//! Stage B queue/request state machine (shadow/parity model).
//!
//! A port of the host app's queue rules, driven by the event schema of `tools/queue_event_replay.py` and producing the SAME canonical
//! snapshot (docs/design/stage-b-queue-replay.md). Pure: no I/O, no real clock (a logical clock advances 1 s per event, as in the Python harness).
//!
//! DECLARED SCOPE (everything else is "not modelled"; the Python app stays authoritative):
//!   modelled   classic and rotation queues (no lock), host add / remove song / remove singer / move / skip / rotation_advance / complete,
//!              remote arrivals (admission via `singws-queue::titles_conflict`, tombstones, removed-id memory, singer match by name,
//!              song limit, accepting, duplicate request ids, wire-order song ordering), empty rotation slots with expiry, restart.
//!   NOT        the waitlist (`waitlist_*` ops, `state: waiting` rows, `use_waiting_for_add`), duets ("A & B"), rotation lock, rows with a
//!              server-terminal state, `sort_order` / order timestamps, singer ids / sessions, deferred adds, rename-merge.
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
}

impl Settings {
    fn from(cfg: &Value) -> Settings {
        Settings {
            mode: cfg.get("queue_mode").and_then(Value::as_str).unwrap_or("rotation").to_string(),
            limit: cfg.get("limit_pending_max").and_then(Value::as_i64).unwrap_or(20),
            accepting: cfg.get("requests_accepting").and_then(Value::as_bool).unwrap_or(true),
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
    key: i64,
    tempo: Option<i64>,
    skipped: bool,
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

#[derive(Clone)]
pub struct State {
    init: Settings,
    cur: Settings,
    clock: f64,
    queue: Vec<Singer>,
    tombs: BTreeMap<u64, Tomb>,
    removed: BTreeSet<u64>,
    handled: BTreeSet<u64>,
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

fn is_duet(name: &str) -> bool {
    let lower = name.to_lowercase();
    ["&", "+", "/w", "w/", "/", ","].iter().any(|s| lower.contains(s))
        || lower.split(|c: char| !c.is_alphanumeric() && c != '_').any(|w| w == "and" || w == "with")
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
            "rotation_advance", "complete_song", "waitlist_add", "waitlist_remove", "waitlist_restore", "restart",
        ];
        if !known.contains(&op) {
            return (false, "unknown_op".into());
        }
        self.clock += 1.0;
        let (ok, code) = match op {
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
            _ => (false, "unsupported".into()), // waitlist_* : not modelled
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
                let k = name_key(&s.name);
                if !k.is_empty() {
                    *c.entry(k).or_insert(0) += 1;
                }
            }
        }
        c
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
        let primary = capitalize(singer_name);
        let primary = primary.split_whitespace().collect::<Vec<_>>().join(" ");
        if primary.is_empty() {
            return false;
        }
        entry.skipped = false;
        if self.find_duplicate(&entry) {
            if let Some(r) = entry.rid {
                self.handled.insert(r); // _mark_waiting_for_add_duplicate_accepted
            }
            return true;
        }
        self.cleanup_duplicate_songs();
        let counts = self.live_counts();
        let k = name_key(&primary);
        if counts.get(&k).copied().unwrap_or(0) >= self.cur.active_limit() {
            if remote {
                return false;
            }
            // the operator's own add may exceed the cap
        }
        if let Some(i) = self.queue.iter().position(|s| name_key(&s.name) == k && !k.is_empty()) {
            let s = &mut self.queue[i];
            s.songs.push(entry);
            Self::clear_temp_slot(s);
            if remote {
                self.cleanup_duplicate_songs();
            }
            return true;
        }
        let uid = self.next_uid;
        self.next_uid += 1;
        let new = Singer { uid, name: primary, skipped: false, has_sung: false, round_sung: false, marker: false, temp_empty: false, until: 0.0, songs: vec![entry] };
        if self.cur.rotation() {
            self.recompute_round_state();
            let mut ins = match self.marker_index() {
                Some(m) => m,
                None => self.boundary_index(),
            };
            if ins == 0 && !self.queue.is_empty() {
                ins = self.queue.len();
            }
            self.queue.insert(ins, new);
            self.repair_marker();
            self.recompute_round_state();
        } else {
            self.queue.push(new);
        }
        true
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
        ROUTE_OK.into_pair()
    }

    fn op_add_song(&mut self, ev: &Value) -> (bool, String) {
        let singer = ev.get("singer").and_then(Value::as_str).unwrap_or("");
        if is_duet(singer) {
            return (false, "unsupported".into());
        }
        let artist = ev.get("artist").and_then(Value::as_str).unwrap_or("");
        let title = ev.get("title").and_then(Value::as_str).unwrap_or("");
        let key = ev.get("key").and_then(Value::as_i64).unwrap_or(0);
        self.next_local += 1;
        let entry = Entry { rid: None, local: Some(self.next_local), artist: String::new(), title: title.into(), song_info: format!("/lib/{artist}-{title}.zip"), key, tempo: None, skipped: false };
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
        let rows = ev.get("requests").and_then(Value::as_array).cloned().unwrap_or_default();
        struct Req {
            rid: u64,
            singer: String,
            artist: String,
            title: String,
            key: i64,
            tempo: i64,
        }
        let mut normalized: Vec<Req> = Vec::new();
        for row in &rows {
            let Some(o) = row.as_object() else { continue };
            let rid = o.get("request_id").and_then(Value::as_i64).unwrap_or(0);
            if rid <= 0 {
                continue;
            }
            let rid = rid as u64;
            let state = o.get("state").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
            let terminal = matches!(state.as_str(), "removed" | "completed" | "sung") || o.get("removed_at").is_some() || o.get("completed_at").is_some();
            if terminal || state == "waiting" || state == "failed" || state == "failed_needs_review" {
                continue; // server-terminal and waitlist rows are not modelled
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
                continue; // terminal title memory is empty in this model: stale row ignored
            }
            if o.get("sent").and_then(Value::as_bool).unwrap_or(false) || o.get("delivered").and_then(Value::as_bool).unwrap_or(false) {
                continue; // non-actionable and not in the local queue (checked below only by id; kept simple)
            }
            let singer = o.get("singer").and_then(Value::as_str).unwrap_or("").trim().to_string();
            if singer.is_empty() {
                continue;
            }
            let key = o.get("key").and_then(Value::as_i64).unwrap_or(0);
            let tempo = o.get("tempo").and_then(Value::as_i64).unwrap_or(0).clamp(-30, 30);
            normalized.push(Req { rid, singer, artist: o.get("artist").and_then(Value::as_str).unwrap_or("").trim().to_string(), title, key, tempo });
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
                if let Some(pos) = pool.iter().position(|&i| normalized[i].singer.to_lowercase() == sn && normalized[i].artist.to_lowercase() == a && normalized[i].title.to_lowercase() == t) {
                    let r = &normalized[pool.remove(pos)];
                    e.rid = Some(r.rid);
                    e.key = r.key;
                    e.tempo = Some(100 + r.tempo);
                    existing.insert(r.rid);
                }
            }
        }
        self.cleanup_duplicate_songs();
        let mut counts = self.live_counts();
        for r in &normalized {
            if existing.contains(&r.rid) {
                continue;
            }
            let k = name_key(&r.singer);
            if counts.get(&k).copied().unwrap_or(0) >= self.cur.active_limit() {
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
                key: r.key,
                tempo: Some(100 + r.tempo),
                skipped: false,
            };
            if is_duet(&r.singer) {
                continue;
            }
            if self.add_song(&r.singer, entry, true) {
                *counts.entry(k).or_insert(0) += 1;
            }
        }
        // server key/tempo win for every queued request in the payload (last row wins); then wire order inside each singer
        let mut key_by: HashMap<u64, i64> = HashMap::new();
        let mut tempo_by: HashMap<u64, i64> = HashMap::new();
        for r in &normalized {
            key_by.insert(r.rid, r.key);
            tempo_by.insert(r.rid, r.tempo);
        }
        for s in &mut self.queue {
            for e in &mut s.songs {
                let Some(rid) = e.rid else { continue };
                if let Some(k) = key_by.get(&rid) {
                    e.key = *k;
                }
                let t = tempo_by.get(&rid).copied().unwrap_or_else(|| e.tempo.unwrap_or(100) - 100).clamp(-30, 30);
                e.tempo = Some(100 + t);
            }
        }
        let mut desired_by: HashMap<String, Vec<u64>> = HashMap::new();
        for r in &normalized {
            desired_by.entry(r.singer.to_lowercase()).or_default().push(r.rid);
        }
        for s in &mut self.queue {
            let Some(order) = desired_by.get(&s.name.trim().to_lowercase()) else { continue };
            if s.songs.len() < 2 {
                continue;
            }
            let mut rank: HashMap<u64, usize> = HashMap::new();
            for (i, r) in order.iter().enumerate() {
                rank.insert(*r, i);
            }
            let positions: Vec<usize> = (0..s.songs.len()).filter(|&p| s.songs[p].rid.map(|r| rank.contains_key(&r)).unwrap_or(false)).collect();
            let mut ranked: Vec<Entry> = positions.iter().map(|&p| s.songs[p].clone()).collect();
            ranked.sort_by_key(|e| rank[&e.rid.unwrap()]);
            for (p, e) in positions.into_iter().zip(ranked) {
                s.songs[p] = e;
            }
        }
        self.cleanup_duplicate_songs();
        ROUTE_OK.into_pair()
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
        let Some(started) = ev.get("singer").and_then(Value::as_str).and_then(|r| self.singer_by_ref(r)) else {
            return (false, "unknown_singer".into());
        };
        let started_uid = self.queue[started].uid;
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

    fn op_restart(&mut self) -> (bool, String) {
        self.cur = self.init.clone();
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
                               "key": e.key, "tempo_percent": e.tempo, "skipped": e.skipped})
                    })
                    .collect();
                json!({"id": format!("S{}", self.singer_alias.get(&s.uid).copied().unwrap_or(0)), "name": s.name, "skipped": s.skipped, "has_sung": s.has_sung,
                       "round_sung": s.round_sung, "rotation_marker": s.marker, "temporary_empty_slot": s.temp_empty, "songs": songs})
            })
            .collect();
        let mut tombs = Map::new();
        for (rid, t) in &self.tombs {
            tombs.insert(rid.to_string(), json!({"status": t.status, "removal_reason": t.reason, "removed_by": "host", "singer_name": t.singer, "artist": t.artist, "title": t.title}));
        }
        let mut life = Map::new();
        let ids: BTreeSet<u64> = in_queue.keys().chain(self.tombs.keys()).copied().collect();
        for rid in ids {
            let state = if in_queue.contains_key(&rid) {
                "in_queue"
            } else if matches!(self.tombs[&rid].status.as_str(), "completed" | "sung") {
                "completed"
            } else {
                "removed"
            };
            life.insert(rid.to_string(), json!({"state": state, "copies_in_queue": in_queue.get(&rid).map(Vec::len).unwrap_or(0)}));
        }
        json!({
            "queue": singers,
            "rotation": {"mode": self.cur.mode, "locked": false},
            "waitlist": {}, "removed_tonight": {}, "handled_waitlist_ids": self.handled.iter().collect::<Vec<_>>(),
            "tombstones": tombs, "removed_ids": self.removed.iter().collect::<Vec<_>>(),
            "deferred_remote_adds": 0, "lifecycle": life,
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
