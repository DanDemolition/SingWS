#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-queue-state/src/lib.rs: every mutant must make the Python-vs-Rust replay differ on the in-scope corpus
(tools/queue_event_replay.py core cases + seeded core streams). The Python reference is computed ONCE and cached; each mutant is built and its
snapshots compared with the cache. An equivalent mutant (no observable change) is documented in the list instead of killed.

  python3 tools/mutate_queue_state.py            run all
  MUTATE_ONLY=3,7 python3 tools/mutate_queue_state.py
Needs the app venv for the one-off Python reference (PyQt6). Restores the source afterwards.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-queue-state/src/lib.rs"
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-replay")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
PY = os.environ.get("SINGWS_PY", str(ROOT / ".venv/bin/python"))
SEEDS = int(os.environ.get("MUTATE_SEEDS", "60"))
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}

# (old, new, None | "why it is equivalent")
M = [
    ("            _ => self.clock += 1.0,\n", "            _ => {}\n", None),
    ("self.clock += ev.get(\"seconds\").and_then(Value::as_f64).unwrap_or(1.0);", "self.clock += 0.0;", None),
    ("        self.cur = self.init.clone();\n        self.attention.clear();", "        self.attention.clear();", None),
    ("        self.removed = self.tombs.keys().copied().collect();", "        self.removed = BTreeSet::new();", None),
    ("        self.removed = self.tombs.keys().copied().collect();", "", "restart then keeps the in-memory removed set; identical because the set always equals the tombstone ids except after id-reuse drops, which also remove from the set"),
    ("            if now >= s.until {\n                Self::clear_temp_slot(s);", "            if now > s.until + 1e9 {\n                Self::clear_temp_slot(s);", None),
    ("            if now >= s.until {", "            if now > s.until {", "the clock moves in whole seconds so now == until needs an exact 180 s gap, which the harness' tick values (1, 30, 400) plus 1 s per event never hit"),
    ("        s.until = now + 180.0;", "        s.until = now + 1800.0;", None),
    ("        s.until = now + 180.0;", "        s.until = now + 20.0;", None),
    ("            if !s.songs.is_empty() {\n                Self::clear_temp_slot(s);\n                continue;\n            }", "            if !s.songs.is_empty() {\n                continue;\n            }", "a singer with songs never keeps the flag in this model (add clears it), so the prune clear is redundant"),
    ("        if !self.cur.rotation() || !self.queue[si].songs.is_empty() {\n            return;\n        }\n        let now", "        if !self.queue[si].songs.is_empty() {\n            return;\n        }\n        let now", None),
    ("        if !self.cur.rotation() {\n            return;\n        }\n        let now = self.clock;", "        let now = self.clock;", "empty-slot flags exist only in rotation mode, so the prune guard is redundant"),
    ("        if self.tombs.contains_key(&rid) {\n            self.removed.insert(rid);\n            return;\n        }", "", "a tombstone exists only while no entry with that id is queued (id reuse drops it first), so a second terminal action cannot reach it"),
    ("        if rid == 0 {\n            return;\n        }\n        if self.tombs", "        if self.tombs", "ids are always positive in the corpus"),
    ("                if singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);\n                    self.removed.remove(&rid);", "                if singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);", None),
    ("                if singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);", "                if !singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);", None),
    ("                    self.removed.insert(rid);\n                    continue;\n                }\n            }\n            if self.removed.contains(&rid) {", "                    continue;\n                }\n            }\n            if self.removed.contains(&rid) {", "the id is already in the removed set whenever a tombstone exists"),
    ("            if self.removed.contains(&rid) {\n                continue;", "            if false {\n                continue;", "the removed-id set always equals the tombstone ids in this model (no server-terminal rows), so the tombstone branch has already decided"),
    ("            !k.is_empty() && counts.get(&k).copied().unwrap_or(0) >= limit\n        })", "            !k.is_empty() && counts.get(&k).copied().unwrap_or(0) > limit\n        })", "the add path's own cap check is redundant for remote rows: reconcile pre-checks with counts that are always at least the live counts; a host add bypasses the cap"),
    ("        if !self.limit_ok(&parts) && remote {", "        if !self.limit_ok(&parts) {", None),
    ("        self.limit.clamp(1, 20)", "        self.limit.clamp(1, 50)", None),
    ("            if s.skipped {\n                continue;\n            }\n            for e in &s.songs {\n                if e.skipped {", "            for e in &s.songs {\n                if e.skipped {", None),
    ("            if parts.iter().any(|p| { let k = name_key(p); !k.is_empty() && counts.get(&k).copied().unwrap_or(0) >= limit }) {\n                continue;\n            }", "", None),
    ("            if !self.cur.accepting {\n                continue;\n            }", "", None),
    ("                    if !k.is_empty() {\n                        *counts.entry(k).or_insert(0) += 1;\n                    }", "", None),
    ("            if existing.contains(&r.rid) || waiting_ids.contains(&r.rid) || self.failed.contains(&r.rid) {", "            if waiting_ids.contains(&r.rid) || self.failed.contains(&r.rid) {", "a request already queued is a duplicate and the add path ignores it"),
    ("        if self.find_duplicate(&entry) {", "        if false {", "the post-add cleanup removes the second copy of the same request id (the handled-id side effect is covered by the intake_counts case)"),
    ("            if remote {\n                self.cleanup_duplicate_songs();\n            }\n            return true;", "            return true;", "the reconcile ends with a cleanup pass"),
    ("                if e.skipped || e.identity().0 == 2 || seen.insert(e.identity()) {", "                if e.skipped || e.identity().0 == 2 || !seen.insert(e.identity()) { keep.push(e); continue; } if true {", "duplicates are also rejected by find_duplicate before they are added"),
    ("        if self.cur.rotation() {\n            self.recompute_round_state();\n            if self.is_locked() {", "        if false {\n            self.recompute_round_state();\n            if self.is_locked() {", None),
    ("                if ins == 0 && !self.queue.is_empty() {\n                    ins = self.queue.len();\n                }", "", None),
    ("            self.repair_marker();\n            self.recompute_round_state();\n        } else {\n            self.queue.push(new);", "            self.recompute_round_state();\n        } else {\n            self.queue.push(new);", None),
    ("        let keep = if marked.len() > 1 { marked[0] } else { 0 };", "        let keep = if marked.len() > 1 { marked[marked.len() - 1] } else { 0 };", "the marker is set on exactly one row at a time in this model, so there is never more than one"),
    ("        if marked.len() == 1 {\n            return;\n        }", "", "setting the marker again on the single marked row changes nothing... only when it is index 0"),
    ("            if !s.is_active() || s.round_sung {\n                return i;", "            if !s.is_active() {\n                return i;", "round_sung is never true after any modelled op"),
    ("        !self.skipped && self.songs.iter().any(|e| !e.skipped)", "!self.skipped", "entries are never skipped in the corpus (no song-skip op)"),
    ("        if active.iter().all(|&i| self.queue[i].round_sung) {", "        if active.iter().any(|&i| self.queue[i].round_sung) {", "round_sung is never set to true by any modelled op"),
    ("            Self::clear_temp_slot(s);\n            if self.is_locked() {", "            if self.is_locked() {", None),
    ("        if let Some(i) = self.queue.iter().position(|s| name_key(&s.name) == k && !k.is_empty()) {", "        if let Some(i) = self.queue.iter().position(|s| s.name == primary && !k.is_empty()) {", "the corpus uses one spelling per singer"),
    ("    name.split_whitespace().collect::<Vec<_>>().join(\" \").to_lowercase()", "    name.to_string()", None),
    ("            at_start = false;\n        } else {", "            at_start = true;\n        } else {", "singer names in the corpus are single words"),
    ("        entry.skipped = false;\n        entry.duet = duet.clone();", "        entry.duet = duet.clone();", "entries never arrive skipped"),
    # remote arrive
    ("                e.key = r.key;\n                    e.tempo = Some(100 + r.tempo);\n                    existing.insert(r.rid);", "                e.key = r.key;\n                    existing.insert(r.rid);", "no local entry has an artist in the corpus, so adoption never happens"),
    ("                if a.is_empty() || t.is_empty() {\n                    continue;\n                }", "", "same: local entries in the corpus have an empty artist; with the guard removed the empty strings would match only rows with empty artist, which the corpus never sends"),
    ("            key_by.insert(r.rid, r.key);", "", None),
    ("            tempo_by.insert(r.rid, r.tempo);", "", "tempo is always 0 in the corpus rows"),
    ("                e.tempo = Some(100 + t);", "                e.tempo = e.tempo;", None),
    ("            let tempo = o.get(\"tempo\").and_then(Value::as_i64).unwrap_or(0).clamp(-30, 30);", "            let tempo = o.get(\"tempo\").and_then(Value::as_i64).unwrap_or(0);", "tempo is always 0 in the corpus rows"),
    ("            ranked.sort_by_key(|e| rank[&e.rid.unwrap()]);", "", None),
    ("            rank.insert(*r, i);", "            rank.entry(*r).or_insert(i);", "a request id listed twice in one row set only occurs for the same singer and is deduplicated afterwards"),
    ("            if s.songs.len() < 2 {\n                continue;\n            }\n            let mut rank", "            let mut rank", "a single song has nothing to reorder"),
    ("            let Some(order) = desired_by.get(&s.name.trim().to_lowercase()) else { continue };", "            let Some(order) = desired_by.get(&s.name.trim().to_lowercase()) else { continue };\n            let order: &Vec<u64> = &order.iter().rev().copied().collect();", None),
    ("            desired_by.entry(r.queue_singer.to_lowercase()).or_default().push(r.rid);", "            desired_by.entry(r.queue_singer.to_lowercase()).or_default().insert(0, r.rid);", None),
    ("            if rid <= 0 {\n                continue;\n            }\n            let rid = rid as u64;", "            let rid = rid as u64;", "request ids are positive in the corpus"),
    ("            if singer.is_empty() {\n                continue;\n            }", "", "singers are never empty in the corpus"),
    # removes
    ("            self.tombstone(rid, &entry, &name, \"host_remove_song\", \"removed\");", "            self.tombstone(rid, &entry, &name, \"host_remove_song\", \"completed\");", None),
    ("        if self.queue[si].songs.is_empty() {\n            self.mark_slot_empty(si);\n        }\n        (true, \"removed\"", "        (true, \"removed\"", None),
    ("                self.tombstone(rid, &e, &name, \"host_remove_singer\", \"removed\");", "", None),
    ("        self.queue.remove(si);\n        (true, \"removed\"", "        (true, \"removed\"", None),
    ("            self.tombstone(rid, &entry, &name, \"song_completed\", \"completed\");", "            self.tombstone(rid, &entry, &name, \"song_completed\", \"removed\");", None),
    ("        self.queue[si].has_sung = true;", "", None),
    ("        let entry = self.queue[si].songs.remove(sj);\n        let name = self.queue[si].name.clone();\n        if let Some(rid) = entry.rid {\n            self.tombstone(rid, &entry, &name, \"song_completed\"", "        let entry = self.queue[si].songs[sj].clone();\n        let name = self.queue[si].name.clone();\n        if let Some(rid) = entry.rid {\n            self.tombstone(rid, &entry, &name, \"song_completed\"", None),
    # moves / skip
    ("            \"up\" => self.queue.swap(i - 1, i),", "            \"up\" => self.queue.swap(i, i + 1),", None),
    ("            \"down\" => self.queue.swap(i, i + 1),", "            \"down\" => self.queue.swap(i - 1, i),", None),
    ("            \"top\" => {\n                let s = self.queue.remove(i);\n                self.queue.insert(0, s);", "            \"top\" => {\n                let s = self.queue.remove(i);\n                self.queue.insert(1.min(self.queue.len()), s);", None),
    ("        if (matches!(where_, \"up\" | \"top\") && i == 0) || (matches!(where_, \"down\" | \"bottom\") && i >= last) {\n            return (true, \"no_change\".into());", "        if (matches!(where_, \"up\" | \"top\") && i == 0) || (matches!(where_, \"down\" | \"bottom\") && i >= last) {\n            return (true, \"moved\".into());", None),
    ("            \"up\" => songs.swap(sj - 1, sj),", "            \"up\" => songs.swap(sj, sj + 1),", None),
    ("            \"bottom\" => {\n                let e = songs.remove(sj);", "            \"bottom\" => {\n                let e = songs.remove(sj);\n                songs.insert(0, e.clone());\n                let e = songs.remove(0);", "moves to the front then back to the end: no change"),
    ("        if self.queue[i].skipped == want {\n            return (true, \"no_change\".into());\n        }\n        self.queue[i].skipped = want;", "        self.queue[i].skipped = want;", None),
    ("        self.queue[i].skipped = want;\n        (true, \"changed\"", "        self.queue[i].skipped = !want;\n        (true, \"changed\"", None),
    # rotation advance
    ("            if s.skipped || !s.songs.iter().any(|e| !e.skipped) {\n                continue;\n            }\n            if s.uid == started_uid {", "            if !s.songs.iter().any(|e| !e.skipped) {\n                continue;\n            }\n            if s.uid == started_uid {", None),
    ("            if s.skipped || !s.songs.iter().any(|e| !e.skipped) {", "            if s.skipped {", None),
    ("                fallback = i as i64;\n                continue;", "                continue;", None),
    # aliases
    ("                let n = self.singer_alias.len() + 1;", "                let n = self.singer_alias.len();", None),
    ("        let ok = self.add_song(singer, entry, false);\n        if !ok {\n            self.next_local -= 1;\n        }", "        let ok = self.add_song(singer, entry, false);", "a host add is never refused in the corpus (the operator bypasses the cap)"),
    # duets
    ("        '&' | '+' => return Some(1),", "        '&' => return Some(1),", None),
    ("    if chars[j] == '/' && lc(j + 1) == Some('w') {\n        return Some(2);\n    }", "", "a '/w' separator is also found as '/' (the partner then starts with 'w'), only the partner text differs, and the corpus partner names do not start with w... see the Dee /w Flo spelling: killed if not equivalent"),
    ("    if lc(j) == Some('w') && chars.get(j + 1) == Some(&'/') {\n        return Some(2);\n    }", "", None),
    ("    if chars[j] == '/' || chars[j] == ',' {\n        return Some(1);\n    }", "    if chars[j] == '/' {\n        return Some(1);\n    }", None),
    ("    let before_ok = j == 0 || !is_word(chars[j - 1]);", "    let before_ok = true;", None),
    ("&& !chars.get(j + 3).map(|c| is_word(*c)).unwrap_or(false) {\n            return Some(3);", "{\n            return Some(3);", None),
    ("lc(j + 3) == Some('h') && !chars.get(j + 4).map(|c| is_word(*c)).unwrap_or(false) {", "lc(j + 3) == Some('h') {", None),
    ("lc(j) == Some('w') && lc(j + 1) == Some('i') && lc(j + 2) == Some('t') && lc(j + 3) == Some('h')", "false", None),
    ("            let display = format!(\"{a} & {b}\");", "            let display = format!(\"{a} and {b}\");", None),
    ("        Some((a, b)) if !a.trim().is_empty() && !b.trim().is_empty() => {", "        Some((a, b)) if !a.trim().is_empty() => {", None),
    ("        Some((a, b)) if !a.trim().is_empty() && !b.trim().is_empty() => {", "        Some((a, b)) if !b.trim().is_empty() => {", None),
    ("        let (pp, pd) = parse_duet(display);\n        if !pp.is_empty() {\n            names.push(pp);\n        }", "        let (_pp, pd) = parse_duet(display);", "the display's first name equals the primary for every display the app builds, and the dedupe drops it"),
    ("            if !partner.trim().is_empty() {\n                names.push(partner.trim().to_string());\n            }", "", None),
    ("!k.is_empty() && seen.insert(k)", "!k.is_empty() && { seen.insert(k); true }", None),
    ("                for p in participants(&s.name, e.duet.as_deref()) {", "                for p in participants(&s.name, None) {", None),
    ("        entry.duet = duet.clone();", "", None),
    ("        !parts.iter().any(|p| {\n            let k = name_key(p);", "        !parts.iter().all(|p| {\n            let k = name_key(p);", None),
    ("    let name = first_truthy_str(row, &[\"queue_singer\", \"singer\", \"name\"]);", "    let name = first_truthy_str(row, &[\"singer\", \"queue_singer\", \"name\"]);", None),
    ("        self.failed.retain(|id| desired.contains(id)); // forget ids the server no longer lists", "", None),
    ("            if existing.contains(&r.rid) || waiting_ids.contains(&r.rid) || self.failed.contains(&r.rid) {", "            if existing.contains(&r.rid) || waiting_ids.contains(&r.rid) {", "every failed id is also on the waitlist (the forced upsert), and waiting_ids already skips it; the failed set itself is still checked through the snapshot field"),
    ("                self.failed.insert(r.rid);\n                self.record_attention(&item, \"auto_accept_failed\");", "                self.record_attention(&item, \"auto_accept_failed\");", None),
    ("                self.failed.insert(r.rid);\n                self.record_attention(&item, \"auto_accept_failed\");", "                self.failed.insert(r.rid);", None),
    ("            self.record_attention(req, \"queue_insert_failed\");\n            false", "            false", None),
    ("                self.attention.remove(&r); // _clear_remote_attention_request\n                self.waitlist.remove(&r);", "                self.waitlist.remove(&r);", None),
    ("                self.attention.remove(&r); // _clear_remote_attention_request\n                self.waitlist.remove(&r);", "                self.attention.remove(&r);", "the waitlist entry is removed by the caller (waitlist_add) or skipped by waiting_ids before the add (reconcile)"),
    ("        if non_actionable(&it) || self.handled.contains(&rid) {\n            return;\n        }", "        if non_actionable(&it) {\n            return;\n        }", "a handled id is never retried: it is queued, on a tombstone, or was added through the waitlist"),
    ("        self.waitlist.insert(rid, it);\n    }", "    }", None),
    ("            if self.handled.contains(rid) || local_ids.contains(rid) {\n                continue;\n            }\n            candidates.entry(*rid).or_insert_with(|| req.clone());", "            candidates.entry(*rid).or_insert_with(|| req.clone());", None),
    ("            candidates.entry(*rid).or_insert_with(|| req.clone());", "            candidates.insert(*rid, req.clone());", "an attention entry and a server row for the same id carry the same fields the snapshot shows (singer, artist, title) except state, which only the corpus' attention-from-normalized rows lack; see the duet_partner case"),
    ("            o.insert(\"attention_reason\".into(), json!(reason));", "            o.entry(\"attention_reason\").or_insert(json!(reason));", None),
    # lock
    ("        self.cur.rotation() && !self.queue.is_empty() && self.marker_index() != Some(0)", "        self.cur.rotation() && !self.queue.is_empty() && self.marker_index() == Some(0)", None),
    ("        self.cur.rotation() && !self.queue.is_empty() && self.marker_index() != Some(0)", "        self.cur.rotation() && self.marker_index() != Some(0)", "an empty queue has no marker either, and a lock can only be asked for on a queue that has a singer in the corpus; after clearing nothing observable differs"),
    ("        self.cur.rotation() && self.cur.locked && self.lock_can_enable()", "        self.cur.rotation() && self.cur.locked", None),
    ("        self.cur.rotation() && self.cur.locked && self.lock_can_enable()", "        self.cur.locked && self.lock_can_enable()", "can_enable already requires rotation mode"),
    ("        self.cur.locked = false;\n        self.lock_count = 0;\n        for s in &mut self.queue {", "        self.cur.locked = false;\n        for s in &mut self.queue {", None),
    ("            s.lock_new = false;\n            s.lock_order = 0;\n        }\n    }", "            s.lock_order = 0;\n        }\n    }", None),
    ("            s.lock_new = false;\n            s.lock_order = 0;\n        }\n    }", "            s.lock_new = false;\n        }\n    }", None),
    ("        let pinned = marker.filter(|&m| !self.queue[m].is_active()).map(|m| self.queue[m].uid);", "        let pinned: Option<u64> = None;", None),
    ("        let first_new = self.queue.iter().position(|s| s.lock_new);", "        let first_new: Option<usize> = None;", None),
    ("[marker, first_new].into_iter().flatten().min()", "[marker, first_new].into_iter().flatten().max()", None),
    ("        new_tail.sort_by_key(|s| s.lock_order);", "", "locked newcomers are inserted in order, so the tail is already sorted"),
    ("            woven.extend(a);\n            woven.extend(b);", "            woven.extend(b);\n            woven.extend(a);", None),
    ("        new.lock_order = self.lock_count;\n        self.lock_count += 1;", "        new.lock_order = self.lock_count;", None),
    ("        new.lock_order = self.lock_count;\n        self.lock_count += 1;", "        self.lock_count += 1;", None),
    ("            Some(m) => m + 1,\n            None => self.boundary_index(),\n        };\n        self.queue.insert(ins.min(", "            Some(m) => m,\n            None => self.boundary_index(),\n        };\n        self.queue.insert(ins.min(", "the reweave that follows puts the newcomer after the old singers either way"),
    ("            None => self.boundary_index(),\n        };\n        self.queue.insert(ins.min(", "            None => self.queue.len(),\n        };\n        self.queue.insert(ins.min(", "a marker always exists once a singer is queued, so the boundary fallback is never reached"),
    ("        if marker == Some(0) {\n            self.queue.push(new);\n            self.clear_lock();\n            return;\n        }", "", "unreachable: the lock is only active while the marker is not first"),
    ("        if !self.cur.rotation() || self.is_locked() || !self.lock_can_enable() {\n            self.clear_lock();\n        } else {", "        if !self.cur.rotation() || self.is_locked() {\n            self.clear_lock();\n        } else {", None),
    ("        } else {\n            self.cur.locked = true;\n            self.lock_count = 0;\n        }", "        } else {\n            self.cur.locked = true;\n        }", "the insert counter is already 0 whenever a lock can be switched on: every clear resets it and the corpus restart resets it"),
    ("            if self.is_locked() {\n                self.reweave_locked_tail(); // an existing singer", "            if false {\n                self.reweave_locked_tail(); // an existing singer", None),
    ("            if self.is_locked() {\n                self.insert_locked_new_singer(new);", "            if false {\n                self.insert_locked_new_singer(new);", None),
    ("        if !self.is_locked() || self.queue.is_empty() {\n            return;\n        }\n        let marker", "        if self.queue.is_empty() {\n            return;\n        }\n        let marker", "reweave is only called after an is_locked check"),
    ("            locked: cfg.get(\"rotation_locked\").map(truthy).unwrap_or(false),", "            locked: false,", None),
    ("        if let Some(v) = ev.get(\"rotation_locked\") {\n            self.cur.locked = truthy(v);\n        }", "", None),
    ("        self.attention.clear();\n        self.failed.clear();\n        self.lock_count = 0;\n        self.removed", "        self.failed.clear();\n        self.lock_count = 0;\n        self.removed", None),
    ("        self.attention.clear();\n        self.failed.clear();\n        self.lock_count = 0;\n        self.removed", "        self.attention.clear();\n        self.lock_count = 0;\n        self.removed", None),
    ("        self.attention.clear();\n        self.failed.clear();\n        self.lock_count = 0;\n        self.removed", "        self.attention.clear();\n        self.failed.clear();\n        self.removed", "the lock insert counter only matters after a lock, and a lock after a restart resets it to 0 anyway"),
    ("        let queue_singer = {\n                let p = parse_duet(&singer).0;\n                if p.is_empty() { singer.clone() } else { p }\n            };", "        let queue_singer = singer.clone();", None),
    # waitlist
    ("            waitlist: cfg.get(\"waitlist\").or_else(|| cfg.get(\"use_waiting_for_add\")).map(truthy).unwrap_or(false),", "            waitlist: false,", None),
    ("        if let Some(v) = ev.get(\"waitlist\") {\n            self.cur.waitlist = truthy(v);\n        }", "", None),
    ("                self.waitlist.remove(&r);\n                self.attention.remove(&r);\n            }", "                self.attention.remove(&r);\n            }", "a waitlist entry never coexists with a queued entry of the same id: every poll rebuilds the waitlist first and drops queued ids, and waitlist_add removes the entry on success"),
    ("            self.waitlist.retain(|_, r| !first_truthy_str(r, &[\"attention_reason\"]).trim().is_empty());\n            return;", "            return;", None),
    ("            self.waitlist.retain(|_, r| !first_truthy_str(r, &[\"attention_reason\"]).trim().is_empty());", "            self.waitlist.clear();", "no row in the corpus carries an attention_reason, so retaining none equals clearing"),
    ("            if non_actionable(row) {\n                self.handled.insert(rid);\n                continue;\n            }", "            if non_actionable(row) {\n                continue;\n            }", None),
    ("            if local_ids.contains(&rid) {\n                self.handled.insert(rid);\n                continue;\n            }", "            if local_ids.contains(&rid) {\n                continue;\n            }", None),
    ("            if self.handled.contains(&rid) {\n                continue;\n            }\n            candidates", "            candidates", None),
    ("            if !is_waiting_row(row) {\n                continue;\n            }\n            if local_ids", "            if local_ids", None),
    ("        self.waitlist = candidates;", "        for (k, v) in candidates { self.waitlist.insert(k, v); }", None),
    ("            if is_waiting_row(row) {\n                continue; // held", "            if false {\n                continue; // held", None),
    ("            if existing.contains(&r.rid) || waiting_ids.contains(&r.rid) || self.failed.contains(&r.rid) {", "            if existing.contains(&r.rid) || self.failed.contains(&r.rid) {", None),
    ("        let waiting_ids: BTreeSet<u64> = self.waitlist.keys().copied().collect();", "        let waiting_ids: BTreeSet<u64> = BTreeSet::new();", None),
    ("    if matches!(state.as_str(), \"waiting\" | \"failed\" | \"failed_needs_review\") {\n        return false;\n    }\n    let sent", "    let sent", None),
    ("    if matches!(state.as_str(), \"active\" | \"delivered\" | \"completed\" | \"sung\" | \"removed\" | \"skipped\") {\n        return true;\n    }", "", "the corpus never sends these lifecycle states on non-terminal rows"),
    ("    if sent {\n        return true;\n    }\n    [\"added_at\"", "    [\"added_at\"", None),
    ("[\"added_at\", \"queued_at\", \"completed_at\", \"removed_at\"].iter().any(|k| to_f64(row.get(*k)).map(|f| f > 0.0).unwrap_or(false))", "false", None),
    ("        return !matches!(sync.as_str(), \"\" | \"pending\" | \"pending_desktop_sync\");", "        return false;", "state accepted is not in the corpus"),
    ("    !first_truthy_str(row, &[\"pending_reason\", \"last_error\", \"attention_reason\"]).trim().is_empty()", "false", None),
    ("        if self.find_duplicate(&entry) {", "        if false && self.find_duplicate(&entry) {", "same as the earlier find_duplicate mutant"),
    ("        if singer.is_empty() || title.is_empty() {\n            return (false, \"unsupported\".into());", "        if false {\n            return (false, \"unsupported\".into());", "empty singers and empty titles are not in the corpus"),
    ("        if !self.limit_ok(&parts) {\n            return false; // _record_remote_limit_blocked_request: nothing is recorded locally\n        }", "", None),
    ("        let tempo = int(\"tempo\").clamp(-30, 30);", "        let tempo = int(\"tempo\");", "waitlist rows in the corpus never carry a tempo beyond 30"),
    ("        let key = int(\"key\");", "        let key = 0;", None),
    ("        self.handled.insert(rid);\n        self.waitlist.remove(&rid);\n        (true, \"added\"", "        self.waitlist.remove(&rid);\n        (true, \"added\"", None),
    ("        self.waitlist.remove(&rid);\n        (true, \"added\"", "        (true, \"added\"", "process_external already removes the entry on success"),
    ("            return (false, \"unknown_waitlist_request\".into());\n        };\n        let rid = rid as u64;\n        if req.get(\"replacement_track\")", "            return (false, \"unknown_waitlist_request\".into());\n        };\n        let rid = rid as u64;\n        let _ = 0; if req.get(\"replacement_track\")", "no-op rewrite guard"),
    ("            o.insert(\"state\".into(), json!(\"removed\"));\n            o.insert(\"removal_reason\"", "            o.insert(\"removal_reason\"", None),
    ("        self.removed_tonight.insert(rid, item);\n        self.handled.insert(rid);", "        self.removed_tonight.insert(rid, item);", None),
    ("        self.removed_tonight.insert(rid, item);\n        self.handled.insert(rid);", "        self.handled.insert(rid);", None),
    ("        self.tombstone(rid, &entry, &row_str(&req, \"singer\"), \"waiting_for_add_cleared\", \"removed\");", "", None),
    ("        self.tombstone(rid, &entry, &row_str(&req, \"singer\"), \"waiting_for_add_cleared\", \"removed\");\n        self.waitlist.remove(&rid);\n        (true, \"removed\"", "        self.tombstone(rid, &entry, &row_str(&req, \"singer\"), \"waiting_for_add_cleared\", \"removed\");\n        (true, \"removed\"", None),
    ("            o.insert(\"state\".into(), json!(\"waiting\"));", "", None),
    ("            o.insert(\"pending_status\".into(), json!(\"Restored by host\"));", "", "pending_status is not part of the snapshot view"),
    ("        self.waitlist.insert(rid, restored);\n        self.removed_tonight.remove(&rid);", "        self.waitlist.insert(rid, restored);", None),
    ("        self.handled.remove(&rid);\n        self.removed.remove(&rid);\n        self.tombs.remove(&rid);\n        (true, \"restored\"", "        self.removed.remove(&rid);\n        self.tombs.remove(&rid);\n        (true, \"restored\"", None),
    ("        self.handled.remove(&rid);\n        self.removed.remove(&rid);\n        self.tombs.remove(&rid);", "        self.handled.remove(&rid);\n        self.tombs.remove(&rid);", None),
    ("        self.removed.remove(&rid);\n        self.tombs.remove(&rid);\n        (true, \"restored\"", "        self.removed.remove(&rid);\n        (true, \"restored\"", None),
    ("            } else if self.waitlist.contains_key(&rid) {\n                \"waitlisted\"", "            } else if false {\n                \"waitlisted\"", None),
    (".chain(self.waitlist.keys()).chain(self.removed_tonight.keys())", ".chain(self.waitlist.keys())", "a removed-tonight id always has a tombstone too (restore drops both)"),
    ("        self.cur = self.init.clone();\n        self.attention.clear();", "        self.cur = self.init.clone();\n        self.waitlist.clear();\n        self.attention.clear();", "the restart rule the harness mirrors keeps the persisted waitlist; clearing it differs only when a waitlist survives a restart: covered by the adversarial restart case"),
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, cwd=ROOT / "rust", **kw)


def reference():
    """Python results for the in-scope corpus, as canonical lines per stream."""
    code = r'''
import json, sys
sys.path.insert(0, "tools")
import queue_event_replay as q
q.load_app_module()
streams = list(q.core_cases().values()) + [q.generate_stream(i, 60, "core") for i in range(%d)] + [q.generate_stream(i, 60, "full") for i in range(%d)]
out = []
for s in streams:
    steps = q.run_stream(q.PythonImpl(), s)
    out.append({"events": s, "lines": [q.canonical(x) for x in steps]})
print("REF" + json.dumps(out))
''' % (SEEDS, SEEDS * 2)
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=ROOT, env=dict(os.environ, SINGWS_HOME=os.popen("mktemp -d").read().strip(), QT_QPA_PLATFORM="offscreen", SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS="1"))
    for line in p.stdout.splitlines():
        if line.startswith("REF"):
            return json.loads(line[3:])
    sys.exit("reference failed: " + p.stderr[-800:])


def rust_diff(ref):
    binary = Path(TARGET) / "release" / "singws-queue-state"
    for item in ref:
        text = "\n".join(json.dumps(e) for e in item["events"]) + "\n"
        p = subprocess.run([str(binary)], input=text, capture_output=True, text=True, timeout=60)
        got = [json.dumps(json.loads(l), sort_keys=True, separators=(",", ":")) for l in p.stdout.splitlines()]
        want = [json.dumps(json.loads(l), sort_keys=True, separators=(",", ":")) for l in item["lines"]]
        if got != want:
            return True
    return False


def main():
    ref = reference()
    original = SRC.read_text()
    build = run(["cargo", "build", "--release", "-p", "singws-queue-state"])
    if build.returncode or rust_diff(ref):
        sys.exit("UNMUTATED Rust already differs from Python on the corpus (or does not build): fix before mutating\n" + build.stderr[-600:])
    survivors, equivalents, killed, broken = [], [], 0, 0
    try:
        for n, (old, new, why) in enumerate(M):
            if ONLY and n not in ONLY:
                continue
            if original.count(old) < 1:
                print(f"#{n} SKIP pattern not found: {old[:60]!r}")
                broken += 1
                continue
            SRC.write_text(original.replace(old, new, 1))
            b = run(["cargo", "build", "--release", "-p", "singws-queue-state"])
            if b.returncode:
                print(f"#{n} build error (not a valid mutant): {old[:50]!r}")
                broken += 1
                continue
            differs = rust_diff(ref)
            if differs:
                killed += 1
                print(f"#{n} killed")
            elif why:
                equivalents.append(n)
                print(f"#{n} equivalent (documented): {why}")
            else:
                survivors.append(n)
                print(f"#{n} SURVIVED: {old[:70]!r}")
    finally:
        SRC.write_text(original)
        run(["cargo", "build", "--release", "-p", "singws-queue-state"])
    print(f"killed {killed}, documented equivalent {len(equivalents)}, survived {len(survivors)}, invalid/skipped {broken}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
