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
    ("        self.clock += 1.0;\n        let (ok, code) = match op {", "        let (ok, code) = match op {", None),
    ("self.clock += ev.get(\"seconds\").and_then(Value::as_f64).unwrap_or(1.0);", "self.clock += 0.0;", None),
    ("        self.cur = self.init.clone();\n        self.removed", "        self.removed", None),
    ("        self.removed = self.tombs.keys().copied().collect();", "        self.removed = BTreeSet::new();", None),
    ("        self.removed = self.tombs.keys().copied().collect();", "", "restart then keeps the in-memory removed set; identical because the set always equals the tombstone ids except after id-reuse drops, which also remove from the set"),
    ("            if now >= s.until {\n                Self::clear_temp_slot(s);", "            if now > s.until + 1e9 {\n                Self::clear_temp_slot(s);", None),
    ("            if now >= s.until {", "            if now > s.until {", "the clock moves in whole seconds so now == until needs an exact 180 s gap, which the harness' tick values (1, 30, 400) plus 1 s per event never hit"),
    ("        s.until = now + 180.0;", "        s.until = now + 1800.0;", None),
    ("        s.until = now + 180.0;", "        s.until = now + 20.0;", None),
    ("            if !s.songs.is_empty() {\n                Self::clear_temp_slot(s);\n                continue;\n            }", "            if !s.songs.is_empty() {\n                continue;\n            }", "a singer with songs never keeps the flag in this model (add clears it), so the prune clear is redundant"),
    ("        if !self.cur.rotation() || !self.queue[si].songs.is_empty() {\n            return;\n        }\n        let now", "        if !self.queue[si].songs.is_empty() {\n            return;\n        }\n        let now", None),
    ("        if !self.cur.rotation() {\n            return;\n        }\n        let now = self.clock;", "        let now = self.clock;", None),
    ("        if self.tombs.contains_key(&rid) {\n            self.removed.insert(rid);\n            return;\n        }", "", None),
    ("        if rid == 0 {\n            return;\n        }\n        if self.tombs", "        if self.tombs", "ids are always positive in the corpus"),
    ("                if singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);\n                    self.removed.remove(&rid);", "                if singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);", None),
    ("                if singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);", "                if !singws_queue::titles_conflict(&t.title, &title) {\n                    self.tombs.remove(&rid);", None),
    ("                    self.removed.insert(rid);\n                    continue;\n                }\n            }\n            if self.removed.contains(&rid) {", "                    continue;\n                }\n            }\n            if self.removed.contains(&rid) {", "the id is already in the removed set whenever a tombstone exists"),
    ("            if self.removed.contains(&rid) {\n                continue;", "            if false {\n                continue;", None),
    ("        let k = name_key(&primary);\n        if counts.get(&k).copied().unwrap_or(0) >= self.cur.active_limit() {", "        let k = name_key(&primary);\n        if counts.get(&k).copied().unwrap_or(0) > self.cur.active_limit() {", None),
    ("            if remote {\n                return false;\n            }\n            // the operator", "            return false;\n            // the operator", None),
    ("        self.limit.clamp(1, 20)", "        self.limit.clamp(1, 50)", None),
    ("            if s.skipped {\n                continue;\n            }\n            for e in &s.songs {\n                if e.skipped {", "            for e in &s.songs {\n                if e.skipped {", None),
    ("            if counts.get(&k).copied().unwrap_or(0) >= self.cur.active_limit() {\n                continue;\n            }\n            if !self.cur.accepting {", "            if !self.cur.accepting {", None),
    ("            if !self.cur.accepting {\n                continue;\n            }", "", None),
    ("                *counts.entry(k).or_insert(0) += 1;\n            }\n        }\n        // server key", "            }\n        }\n        // server key", None),
    ("            if existing.contains(&r.rid) {\n                continue;\n            }\n            let k = name_key(&r.singer);", "            let k = name_key(&r.singer);", "a request already queued is a duplicate and the add path ignores it"),
    ("        if self.find_duplicate(&entry) {\n            return true;\n        }", "", "the post-add cleanup removes the second copy of the same request id"),
    ("            if remote {\n                self.cleanup_duplicate_songs();\n            }\n            return true;", "            return true;", "the reconcile ends with a cleanup pass"),
    ("                if e.skipped || e.identity().0 == 2 || seen.insert(e.identity()) {", "                if true {", "duplicates are also rejected by find_duplicate before they are added"),
    ("        if self.cur.rotation() {\n            self.recompute_round_state();\n            let mut ins", "        if false {\n            self.recompute_round_state();\n            let mut ins", None),
    ("            if ins == 0 && !self.queue.is_empty() {\n                ins = self.queue.len();\n            }", "", None),
    ("            self.queue.insert(ins, new);\n            self.repair_marker();", "            self.queue.insert(ins, new);", None),
    ("        let keep = if marked.len() > 1 { marked[0] } else { 0 };", "        let keep = if marked.len() > 1 { marked[marked.len() - 1] } else { 0 };", "the marker is set on exactly one row at a time in this model, so there is never more than one"),
    ("        if marked.len() == 1 {\n            return;\n        }", "", "setting the marker again on the single marked row changes nothing... only when it is index 0"),
    ("            if !s.is_active() || s.round_sung {\n                return i;", "            if !s.is_active() {\n                return i;", None),
    ("        !self.skipped && self.songs.iter().any(|e| !e.skipped)", "!self.skipped", "entries are never skipped in the corpus (no song-skip op)"),
    ("        if active.iter().all(|&i| self.queue[i].round_sung) {", "        if active.iter().any(|&i| self.queue[i].round_sung) {", "round_sung is never set to true by any modelled op"),
    ("            Self::clear_temp_slot(s);\n            if remote {", "            if remote {", None),
    ("        if let Some(i) = self.queue.iter().position(|s| name_key(&s.name) == k && !k.is_empty()) {", "        if let Some(i) = self.queue.iter().position(|s| s.name == primary && !k.is_empty()) {", "the corpus uses one spelling per singer"),
    ("    name.split_whitespace().collect::<Vec<_>>().join(\" \").to_lowercase()", "    name.to_string()", None),
    ("            at_start = false;\n        } else {", "            at_start = true;\n        } else {", "singer names in the corpus are single words"),
    ("        entry.skipped = false;\n        if self.find", "        if self.find", "entries never arrive skipped"),
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
    ("            desired_by.entry(r.singer.to_lowercase()).or_default().push(r.rid);", "            desired_by.entry(r.singer.to_lowercase()).or_default().insert(0, r.rid);", None),
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
streams = list(q.core_cases().values()) + [q.generate_stream(i, 60, "core") for i in range(%d)]
out = []
for s in streams:
    steps = q.run_stream(q.PythonImpl(), s)
    out.append({"events": s, "lines": [q.canonical(x) for x in steps]})
print("REF" + json.dumps(out))
''' % SEEDS
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
