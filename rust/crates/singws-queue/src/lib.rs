//! Pure queue / request rules. No I/O, no clock; everything a decision needs arrives as an argument.
//!
//! Rules modelled (AGENTS.md "Queue and server synchronization rules", mirrored from the Python app):
//! * a request the host removed or completed stays dead: a server row carrying its id is ignored (tombstone or known
//!   terminal id) unless the row is *clearly a different song* (the id was reused after a server-side reset);
//! * request ids are permanent: a row for an existing id updates that request, it never creates a second one;
//! * duplicates keep the oldest copy (smallest id / earliest accepted);
//! * a remote reorder may only permute ids the host already has.
//!
//! Stage B runs `admit` in SHADOW mode against the Python decision; `Queue` is the executable statement of the invariants
//! (property-tested here) and the model a later `queue_drive` step will own.

use std::collections::{BTreeMap, BTreeSet};

pub type RequestId = u64;

/// Lowercase ASCII letters and digits only (`re.sub(r"[^a-z0-9]+", "", s.lower())` in the app), so spacing and
/// punctuation drift ("Rocket Man" vs "Rocketman") cannot conflict.
pub fn norm_title(s: &str) -> String {
    s.to_lowercase().chars().filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit()).collect()
}

/// True only when two titles are clearly DIFFERENT songs (`_tombstone_signature_conflicts`). Missing data never conflicts.
pub fn titles_conflict(old: &str, new: &str) -> bool {
    let (a, b) = (norm_title(old), norm_title(new));
    if a.is_empty() || b.is_empty() {
        return false;
    }
    !(a == b || a.contains(&b) || b.contains(&a))
}

/// What the Python poll loop knows about one non-terminal server row before deciding.
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RowFacts {
    /// Title of the host tombstone that matches this row (by id, else by song key); `None` = no tombstone.
    pub tombstone_title: Option<String>,
    /// The id is in the session's removed/terminal id set.
    pub id_removed: bool,
    /// Title remembered when the server reported this id terminal, if any.
    pub terminal_title: Option<String>,
    /// Title carried by the server row.
    pub row_title: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Admit {
    /// No tombstone and not a known terminal id: process the row normally.
    Accept,
    /// Tombstone exists but the row is clearly another song: retire the tombstone, treat as a new request.
    AcceptIdReuse,
    /// The host removed this request: ignore the row and re-push the removal.
    RejectTombstone,
    /// Known terminal id with no live tombstone, same/unknown song: a stale row must not resurrect it.
    RejectTerminalStale,
    /// Known terminal id reused by a clearly different song: accept.
    AcceptTerminalReuse,
}

impl Admit {
    pub fn accepts(self) -> bool {
        matches!(self, Admit::Accept | Admit::AcceptIdReuse | Admit::AcceptTerminalReuse)
    }
}

/// The poll-row admission decision, in the same order as the app: tombstone first, then the terminal-id set.
pub fn admit(f: &RowFacts) -> Admit {
    if let Some(t) = &f.tombstone_title {
        return if titles_conflict(t, &f.row_title) { Admit::AcceptIdReuse } else { Admit::RejectTombstone };
    }
    if f.id_removed {
        return match &f.terminal_title {
            Some(t) if !t.is_empty() && titles_conflict(t, &f.row_title) => Admit::AcceptTerminalReuse,
            _ => Admit::RejectTerminalStale,
        };
    }
    Admit::Accept
}

/// JSON bridge for the Python shadow hook: `RowFacts` in, `{"admit":"..","accepts":bool}` out.
pub fn admit_json(facts_json: &str) -> Result<String, String> {
    let facts: RowFacts = serde_json::from_str(facts_json).map_err(|e| format!("facts: {e}"))?;
    let a = admit(&facts);
    serde_json::to_string(&serde_json::json!({ "admit": a, "accepts": a.accepts() })).map_err(|e| e.to_string())
}

/// The older of two request ids wins (ids are epoch-millisecond timestamps).
pub fn keep_oldest(a: RequestId, b: RequestId) -> RequestId {
    a.min(b)
}

/// A remote reorder is legal only if it is a permutation of the ids the host already holds (`None` = pinned/empty slot).
pub fn reorder_is_permutation(before: &[Option<RequestId>], after: &[Option<RequestId>]) -> bool {
    let mut b: Vec<RequestId> = before.iter().flatten().copied().collect();
    let mut a: Vec<RequestId> = after.iter().flatten().copied().collect();
    b.sort_unstable();
    a.sort_unstable();
    a == b
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
pub enum Lifecycle {
    Pending,
    Accepted,
    Singing,
    Completed,
    Removed,
}

impl Lifecycle {
    pub fn is_terminal(self) -> bool {
        matches!(self, Lifecycle::Completed | Lifecycle::Removed)
    }
}

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Request {
    pub id: RequestId,
    pub title: String,
    pub state: Lifecycle,
    /// Order in which the request was first accepted; never changes (oldest-wins tie-break).
    pub accepted_seq: u64,
}

#[derive(Clone, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub enum Event {
    /// Host adds a request (or a server row for a new id arrives and is admitted).
    ServerRow { id: RequestId, title: String },
    HostRemove { id: RequestId },
    HostComplete { id: RequestId },
    /// App restart: state is persisted and restored; nothing may change.
    Restart,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Change {
    Inserted(RequestId),
    /// An existing request was refreshed in place; never a second object for the same id.
    Updated(RequestId),
    Ignored(RequestId, Admit),
    Retired(RequestId),
    Terminal(RequestId, Lifecycle),
}

#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Queue {
    pub requests: BTreeMap<RequestId, Request>,
    /// Host tombstones: id -> title of the request the host removed/completed.
    pub tombstones: BTreeMap<RequestId, String>,
    next_seq: u64,
}

impl Queue {
    pub fn apply(&mut self, e: &Event) -> Vec<Change> {
        match e {
            Event::ServerRow { id, title } => {
                let facts = RowFacts {
                    tombstone_title: self.tombstones.get(id).cloned(),
                    id_removed: false,
                    terminal_title: None,
                    row_title: title.clone(),
                };
                let verdict = admit(&facts);
                if !verdict.accepts() {
                    return vec![Change::Ignored(*id, verdict)];
                }
                let mut out = Vec::new();
                if verdict == Admit::AcceptIdReuse {
                    self.tombstones.remove(id);
                    self.requests.remove(id); // the dead request is replaced by the clearly different new one
                    out.push(Change::Retired(*id));
                }
                match self.requests.get_mut(id) {
                    Some(r) => {
                        // A live request is refreshed in place; a terminal one never comes back to life from a row.
                        if !r.state.is_terminal() {
                            r.title = title.clone();
                        }
                        out.push(Change::Updated(*id));
                    }
                    None => {
                        self.next_seq += 1;
                        self.requests.insert(*id, Request { id: *id, title: title.clone(), state: Lifecycle::Pending, accepted_seq: self.next_seq });
                        out.push(Change::Inserted(*id));
                    }
                }
                out
            }
            Event::HostRemove { id } | Event::HostComplete { id } => {
                let state = if matches!(e, Event::HostRemove { .. }) { Lifecycle::Removed } else { Lifecycle::Completed };
                let title = self.requests.get(id).map(|r| r.title.clone()).unwrap_or_default();
                self.tombstones.entry(*id).or_insert(title);
                if let Some(r) = self.requests.get_mut(id) {
                    r.state = state;
                }
                vec![Change::Terminal(*id, state)]
            }
            Event::Restart => {
                let json = serde_json::to_string(self).expect("queue serializes");
                *self = serde_json::from_str(&json).expect("queue restores");
                Vec::new()
            }
        }
    }

    pub fn live_ids(&self) -> BTreeSet<RequestId> {
        self.requests.values().filter(|r| !r.state.is_terminal()).map(|r| r.id).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn facts(tomb: Option<&str>, removed: bool, term: Option<&str>, row: &str) -> RowFacts {
        RowFacts { tombstone_title: tomb.map(String::from), id_removed: removed, terminal_title: term.map(String::from), row_title: row.into() }
    }

    #[test]
    fn title_conflict_matches_the_app() {
        assert!(!titles_conflict("Rocket Man", "Rocketman"));
        assert!(!titles_conflict("Risk It All", "Risk It All (Karaoke)"));
        assert!(!titles_conflict("", "Anything"));
        assert!(!titles_conflict("Anything", ""));
        assert!(!titles_conflict("!!!", "Song")); // punctuation-only normalises to empty: incomplete data, removal stands
        assert!(titles_conflict("Old Title", "New Title"));
        assert!(titles_conflict("Return To Sender", "Sometimes Love Just Ain't Enough"));
    }

    #[test]
    fn tombstone_rejects_same_song_and_accepts_a_clearly_different_one() {
        assert_eq!(admit(&facts(Some("Risk It All"), false, None, "risk it all")), Admit::RejectTombstone);
        assert_eq!(admit(&facts(Some("Risk It All"), false, None, "")), Admit::RejectTombstone);
        assert_eq!(admit(&facts(Some("Old"), false, None, "Brand New Song")), Admit::AcceptIdReuse);
    }

    #[test]
    fn terminal_id_without_tombstone_is_stale_unless_the_title_clearly_changed() {
        assert_eq!(admit(&facts(None, true, Some("Song A"), "Song A")), Admit::RejectTerminalStale);
        assert_eq!(admit(&facts(None, true, None, "Song A")), Admit::RejectTerminalStale);
        assert_eq!(admit(&facts(None, true, Some(""), "Song B")), Admit::RejectTerminalStale);
        assert_eq!(admit(&facts(None, true, Some("Song A"), "Totally Other")), Admit::AcceptTerminalReuse);
    }

    #[test]
    fn unknown_ids_are_accepted() {
        assert_eq!(admit(&facts(None, false, None, "x")), Admit::Accept);
        assert!(Admit::Accept.accepts() && !Admit::RejectTombstone.accepts() && !Admit::RejectTerminalStale.accepts());
    }

    #[test]
    fn tombstone_takes_priority_over_the_terminal_set() {
        assert_eq!(admit(&facts(Some("Same"), true, Some("Same"), "Same")), Admit::RejectTombstone);
    }

    #[test]
    fn oldest_wins_and_reorders_must_be_permutations() {
        assert_eq!(keep_oldest(1790974569900, 1790974569756), 1790974569756);
        assert!(reorder_is_permutation(&[None, Some(3), Some(2)], &[None, Some(2), Some(3)]));
        assert!(!reorder_is_permutation(&[Some(1)], &[Some(1), Some(2)]));
        assert!(!reorder_is_permutation(&[Some(1), Some(2)], &[Some(1)]));
    }

    #[test]
    fn json_bridge_round_trips() {
        let out = admit_json(r#"{"tombstone_title":"A","id_removed":false,"terminal_title":null,"row_title":"A"}"#).unwrap();
        assert!(out.contains("RejectTombstone") && out.contains("\"accepts\":false"));
        assert!(admit_json("{").is_err());
    }

    // ---- executable invariants ------------------------------------------------------------------------------------

    #[test]
    fn host_removal_is_never_undone_by_a_same_song_row_even_across_a_restart() {
        let mut q = Queue::default();
        q.apply(&Event::ServerRow { id: 10, title: "Song".into() });
        q.apply(&Event::HostRemove { id: 10 });
        q.apply(&Event::Restart);
        let c = q.apply(&Event::ServerRow { id: 10, title: "song (karaoke)".into() });
        assert_eq!(c, vec![Change::Ignored(10, Admit::RejectTombstone)]);
        assert!(q.live_ids().is_empty());
        assert_eq!(q.requests[&10].state, Lifecycle::Removed);
    }

    #[test]
    fn a_clearly_different_song_may_reuse_a_removed_id_and_gets_a_fresh_request() {
        let mut q = Queue::default();
        q.apply(&Event::ServerRow { id: 10, title: "Old".into() });
        q.apply(&Event::HostComplete { id: 10 });
        let c = q.apply(&Event::ServerRow { id: 10, title: "Another Tune".into() });
        assert_eq!(c, vec![Change::Retired(10), Change::Inserted(10)]);
        assert_eq!(q.requests[&10].state, Lifecycle::Pending);
        assert!(!q.tombstones.contains_key(&10));
    }

    #[test]
    fn a_row_for_an_existing_id_updates_in_place_and_keeps_the_original_order() {
        let mut q = Queue::default();
        q.apply(&Event::ServerRow { id: 5, title: "A".into() });
        q.apply(&Event::ServerRow { id: 6, title: "B".into() });
        let seq = q.requests[&5].accepted_seq;
        assert_eq!(q.apply(&Event::ServerRow { id: 5, title: "A".into() }), vec![Change::Updated(5)]);
        assert_eq!(q.requests.len(), 2);
        assert_eq!(q.requests[&5].accepted_seq, seq);
    }

    // Deterministic xorshift so the property test needs no dependency and is reproducible.
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
    }

    #[test]
    fn property_random_event_streams_keep_every_invariant() {
        let titles = ["Alpha", "Bravo Song", "Charlie", "Delta Dawn", "Echo"];
        for seed in 1..=300u64 {
            let mut rng = Rng(seed.wrapping_mul(0x9E3779B97F4A7C15) | 1);
            let mut q = Queue::default();
            // Per id: the first time the host killed it and the title it had then.
            let mut killed: BTreeMap<RequestId, String> = BTreeMap::new();
            let mut ever_seen: BTreeSet<RequestId> = BTreeSet::new();
            for _ in 0..200 {
                let id = 1 + rng.next() % 6;
                let title = titles[(rng.next() % titles.len() as u64) as usize].to_string();
                let before_ids: Vec<RequestId> = q.requests.keys().copied().collect();
                let before_terminal: Vec<RequestId> =
                    q.requests.values().filter(|r| r.state.is_terminal()).map(|r| r.id).collect();
                let event = match rng.next() % 5 {
                    0 | 1 => Event::ServerRow { id, title: title.clone() },
                    2 => Event::HostRemove { id },
                    3 => Event::HostComplete { id },
                    _ => Event::Restart,
                };
                let changes = q.apply(&event);
                match &event {
                    Event::HostRemove { id } | Event::HostComplete { id } => {
                        let t = q.tombstones.get(id).cloned().unwrap_or_default();
                        killed.entry(*id).or_insert(t);
                    }
                    Event::ServerRow { id, .. } => {
                        // I1: while the tombstone stands, a same-song row never revives the request.
                        if let Some(dead_title) = q.tombstones.get(id) {
                            assert!(!q.live_ids().contains(id), "seed {seed}: host-removed id {id} came back (killed as {dead_title:?})");
                        }
                        ever_seen.insert(*id);
                    }
                    Event::Restart => assert!(changes.is_empty(), "seed {seed}: a restart changed state"),
                }
                // I2: ids never change and never disappear except through an explicit id-reuse retire.
                for old in &before_ids {
                    let retired = changes.contains(&Change::Retired(*old));
                    assert!(q.requests.contains_key(old) || retired, "seed {seed}: id {old} vanished");
                    if let Some(r) = q.requests.get(old) {
                        assert_eq!(r.id, *old, "seed {seed}: id changed");
                    }
                }
                // I1b: a terminal request stays terminal unless the id was explicitly retired for a different song.
                for id in &before_terminal {
                    let retired = changes.contains(&Change::Retired(*id));
                    assert!(retired || q.requests.get(id).map(|r| r.state.is_terminal()).unwrap_or(false),
                        "seed {seed}: terminal request {id} came back to life");
                }
                // One object per id by construction (map key == request.id), and accepted_seq is unique.
                let seqs: BTreeSet<u64> = q.requests.values().map(|r| r.accepted_seq).collect();
                assert_eq!(seqs.len(), q.requests.len(), "seed {seed}: duplicate acceptance order");
                for (k, r) in &q.requests {
                    assert_eq!(*k, r.id);
                }
            }
            let _ = (killed, ever_seen);
        }
    }

    #[test]
    fn property_oldest_duplicate_wins_for_any_pair_and_order() {
        let mut rng = Rng(0xC0FFEE);
        for _ in 0..1000 {
            let (a, b) = (1_790_000_000_000 + rng.next() % 1_000_000, 1_790_000_000_000 + rng.next() % 1_000_000);
            assert_eq!(keep_oldest(a, b), keep_oldest(b, a));
            assert!(keep_oldest(a, b) <= a && keep_oldest(a, b) <= b);
        }
    }
}
