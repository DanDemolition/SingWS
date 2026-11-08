#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-karafun/src/lib.rs (non-test code; script_lines.rs is generated and checked by gen_karafun_script_rs.py --check). Mutants are generated mechanically (comparison operators, connectives,
negations, numeric constants, true/false, min/max, +/-) in a detached COPY of the crate (the repository source is never touched). A mutant is KILLED when it
changes the Rust answers on the tools/karafun_script_parity.py corpus, where the REAL Python builder is the oracle (run ONCE). A mutant that does not
compile is reported separately. Survivors must be listed in SURVIVOR_NOTES (equivalent: why, or GAP: what the corpus lacks), otherwise the run fails.

  python3 tools/mutate_karafun.py          (MUTATE_ONLY=3,7 runs some; MUTATE_SEEDS=N random cases, default 1500)
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
REAL = ROOT / "rust/crates/singws-karafun"
WORK = Path(os.environ.get("MUTATE_WORKDIR") or os.path.expanduser("~/.cache/singws-karafun-mut-work"))
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-karafun-mut")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET)
ONLY = {int(x) for x in os.environ.get("MUTATE_ONLY", "").split(",") if x}
SEEDS = int(os.environ.get("MUTATE_SEEDS", "1500"))

import mutate_songend as ms  # noqa: E402  (code_spans, PATTERNS)

# "L<line>:<old>-><new>" -> "equivalent: why" | "GAP: what the corpus lacks"
SURVIVOR_NOTES: dict = {
    'helpers.rs|let (native, restored) = (w.native, w.session_restored.unwrap_or(true)|true->false': 'equivalent: the helpers harness always sets the session flag, so the default is never read',
    'worker.rs|let key = io.result["requested_key"].as_i64().unwrap_or(0);|0->1': 'equivalent: intake always returns these fields, so the default is never read',
    'worker.rs|let tempo = io.result["requested_tempo"].as_i64().unwrap_or(100);|100->101': 'equivalent: intake always returns these fields, so the default is never read',
    'worker.rs|let needs_adjustment = io.result["needs_adjustment"].as_bool().unwrap_|false->true': 'equivalent: intake always returns these fields, so the default is never read',
    'worker.rs|return Answer { ok: false, text: String::new(), error: String::new(), |false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'worker.rs|let a = Answer { ok: v[0].as_bool().unwrap_or(false), text: v[1].as_st|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'worker.rs|Answer { ok: v[0].as_bool().unwrap_or(false), text: v[1].as_str().unwr|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'helpers.rs|Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|!=->==': 'equivalent: truthiness is only tested on keys the app sets to booleans (or the activation point list), never on a number, text or mapping',
    'helpers.rs|Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|true->false': 'equivalent: truthiness is only tested on keys the app sets to booleans (or the activation point list), never on a number, text or mapping',
    'helpers.rs|Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|0.0->1.0': 'equivalent: truthiness is only tested on keys the app sets to booleans (or the activation point list), never on a number, text or mapping',
    'helpers.rs|Some(Value::String(s)) => !s.is_empty(),|!->': 'equivalent: truthiness is only tested on keys the app sets to booleans (or the activation point list), never on a number, text or mapping',
    'helpers.rs|Some(Value::Object(o)) => !o.is_empty(),|!->': 'equivalent: truthiness is only tested on keys the app sets to booleans (or the activation point list), never on a number, text or mapping',
    'helpers.rs|self.w.ask("session_current").as_bool().unwrap_or(false)|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'helpers.rs|let (x, y) = self.point_ints().unwrap_or((0, 0));|0->1': 'equivalent: the activation point is always the 2-number list the app stored, so the fallback (0, 0) is never used',
    'helpers.rs|let mut w = World { answers, pos: 0, calls: vec![], log: vec![], sleep|0->1': 'equivalent: the start value of the fake clock is not observable; only differences and sleeps matter',
    'prelude.rs|if secs != 0.0 && secs > 0.0 {| > -> >= ': 'equivalent: secs is already known to be non-zero there, so > 0.0 and >= 0.0 agree',
    'prelude.rs|if w.ask("apply_duration").as_bool().unwrap_or(false) {|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'setup.rs|(v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_strin|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'setup.rs|let was_running = w.ask("running").as_bool().unwrap_or(false);|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'setup.rs|if !w.ask("open").as_bool().unwrap_or(false) {|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'setup.rs|let current = w.ask("session_current").as_bool().unwrap_or(false);|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'setup.rs|let mut w = World { answers, pos: 0, calls: vec![], log: vec![], sleep|100.0->99.0': 'equivalent: the start value of the fake clock is not observable; only sleeps and answers are compared',
    'finish.rs|Some(Value::Number(n)) if n.as_f64().map_or(false, |f| f != 0.0) => n.|false->true': 'equivalent: a JSON number always converts to f64, so the default is unreachable',
    'intake.rs|Value::Number(n) => n.as_f64().map_or(true, |f| f != 0.0),|true->false': 'equivalent: a JSON number always converts to f64, so the default is unreachable',
    'intake.rs|Value::Bool(b) => Int::Value(if *b { 1.0 } else { 0.0 }),|0.0->1.0': 'equivalent: the caller tests truthiness first (int(key or 0)), so False never reaches py_int; the tempo path maps False the same way only after the None check and a False tempo is covered by the 50 clamp (0 and 1 both clamp to 50)',
    'intake.rs|let mag = if trimmed.is_empty() { 0.0 } else if trimmed.len() > 18 { 1|0->1': 'equivalent: for 18 digits or fewer the parse cannot fail, so the fallback is unreachable',
    'intake.rs|let mag = if trimmed.is_empty() { 0.0 } else if trimmed.len() > 18 { 1| > -> >= ': 'equivalent: the value is only compared with bounds of at most 200, so an 18-digit number and 1e30 clamp identically',
    'intake.rs|let mag = if trimmed.is_empty() { 0.0 } else if trimmed.len() > 18 { 1|18->19': 'equivalent: the value is only compared with bounds of at most 200, so an 18- or 19-digit number and 1e30 clamp identically',
    'intake.rs|None => 0.0,|0.0->1.0': 'equivalent: the pending age is measured against a 30 s window from a clock reading near 1.7e9, so 0 and 1 are both far older',
    'intake.rs|Some(v) if !truthy(v) => 0.0,|0.0->1.0': 'equivalent: the pending age is measured against a 30 s window from a clock reading near 1.7e9, so 0 and 1 are both far older',
    'intake.rs|Some(Value::Bool(_)) => 1.0,|1.0->2.0': 'equivalent: the pending age is measured against a 30 s window from a clock reading near 1.7e9, so 1 and 2 are both far older',
    'intake.rs|Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),|0.0->1.0': 'equivalent: a JSON number always converts to f64, so the default is unreachable',
    # --- flow.rs (2026-11-08) ---
    'flow.rs|let ok = v[0].as_bool().unwrap_or(false);|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'flow.rs|self.ask("mouse_click").as_bool().unwrap_or(false)|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'flow.rs|(v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_strin|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'flow.rs|self.ask("in_progress").as_bool().unwrap_or(false)|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'flow.rs|Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|true->false': 'equivalent: a JSON number always converts to f64, so the default is unreachable',
    'flow.rs|Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|0.0->1.0': 'equivalent: the flow only tests numeric entry values it set itself to a clock reading (100 s or more on the fake clock), never 0 or 1',
    'flow.rs|Some(Value::String(s)) => !s.is_empty(),|!->': 'equivalent: the flow only tests booleans and numbers for truthiness',
    'flow.rs|Some(Value::Array(a)) => !a.is_empty(),|!->': 'equivalent: the flow only tests booleans and numbers for truthiness',
    'flow.rs|Some(Value::Object(o)) => !o.is_empty(),|!->': 'equivalent: the flow only tests booleans and numbers for truthiness',
    'flow.rs|let ok = w.ask("event_wait").as_bool().unwrap_or(false);|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'flow.rs|while w.in_progress() && w.t < deadline {| < -> <= ': "equivalent: needs the accumulated clock to equal the deadline exactly; 0.1 is not exactly representable so it steps over it (the 0.25 s loop's twin IS killed)",
    'flow.rs|let ready = w.ask("handoff_complete").as_bool().unwrap_or(false);|false->true': 'equivalent under the replay contract: the scripted world always answers with the right type, so this default is never read',
    'flow.rs|let (raised, raise_result, raise_error) = w.session_script("ready-scri|5->6': 'equivalent under the replay contract: the capture sub-branch of the managed path is unreachable while the capture setting does not change during the flow (the managed path requires capture off); a live-branch twin with the same text is killed',
    'flow.rs|if raised && py_strip(&raise_result) == "READY" {|&&->||': 'equivalent under the replay contract: the capture sub-branch of the managed path is unreachable while the capture setting does not change during the flow (the managed path requires capture off); a live-branch twin with the same text is killed',
    'flow.rs|if raised && py_strip(&raise_result) == "READY" {|==->!=': 'equivalent under the replay contract: the capture sub-branch of the managed path is unreachable while the capture setting does not change during the flow (the managed path requires capture off); a live-branch twin with the same text is killed',
    'flow.rs|let r = w.activate(point, false);|false->true': 'equivalent under the replay contract: the capture sub-branch of the managed path is unreachable while the capture setting does not change during the flow (the managed path requires capture off); a live-branch twin with the same text is killed',
    'flow.rs|activated = false;|false->true': 'equivalent under the replay contract: the capture sub-branch of the managed path is unreachable while the capture setting does not change during the flow (the managed path requires capture off); a live-branch twin with the same text is killed',
    'flow.rs|native_double_click_retried = true;|true->false': 'equivalent: the flag only matters on attempt 0, and from attempt 1 on `probe_attempt == 0` is already false',
    # --- readback.rs ---
    'readback.rs|return Err(chars.get(ds).map_or(false, |c| !c.is_ascii() && c.is_numer|&&->||': 'equivalent: parse_key_tempo declines any text holding a non-ASCII numeric character before this test can matter, so the inner check never decides',
    'readback.rs|return Err(chars.get(ds).map_or(false, |c| !c.is_ascii() && c.is_numer|!->': 'equivalent: parse_key_tempo declines any text holding a non-ASCII numeric character before this test can matter, so the inner check never decides',
    'readback.rs|let n: i64 = chars[ds..i].iter().collect::<String>().parse().unwrap_or|0->1': 'equivalent: at most 18 ASCII digits always parse as i64, so the unwrap_or default is never used',
    'readback.rs|if chars[s..].len() < key.len() || chars[s..s + key.len()] != key[..] | < -> <= ': 'equivalent: a KEY= at the very end is skipped here or fails to find digits right after (both continue)',
    'readback.rs|if i == ws || chars.len() < i + tempo.len() || chars[i..i + tempo.len(| < -> <= ': 'equivalent: a text that ends exactly where TEMPO= would start either skips here or finds no digits after it (both continue)',
    # --- monitor.rs ---
    "monitor.rs|let mut i = 0;|0->1": "equivalent in the monitor: lines are stripped and empty lines dropped afterwards, so an extra or missing empty line cannot change the result",
    "monitor.rs|if c == '\\r' && i + 1 < b.len() && b[i + 1].1 == '\\n' {|==->!=": "equivalent in the monitor: same reason (empty lines dropped, lines stripped)",
    "monitor.rs|if c == '\\r' && i + 1 < b.len() && b[i + 1].1 == '\\n' {|1->2": "equivalent in the monitor: same reason (empty lines dropped, lines stripped)",
    "monitor.rs|next += 1;|+= ->-= ": "equivalent in the monitor: a leftover separator at the start of a line is removed by the strip",
    "monitor.rs|i += 1;|1->2": "equivalent in the monitor: a skipped separator ends up inside the next line and is removed by the strip",
    "monitor.rs|if start < s.len() {| < -> <= ": "equivalent in the monitor: an extra empty last line is dropped",
    "monitor.rs|let started = if started <= 0.0 || started > now + 5.0 { now } else { |<=-><": "equivalent: a clock start of exactly 0.0 is falsy in the app (replaced by now) and never reaches this guard",
    "monitor.rs|idle_stop_count: 0,|0->1": "equivalent: the first poll cannot reach the idle-after-playback branch, so the initial value is overwritten before it is read",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {|<=-><": "equivalent: a normalised clock is at least 1, so current >= 0 and current > 0 agree",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {| > -> >= ": "equivalent: a normalised clock is at least 1, so total > 0 and total >= 0 agree",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {|0->1": "equivalent: a total of 1 can never show progress (current <= total = 1 cannot exceed an earlier current >= 1)",
    "monitor.rs|if total > 0 && 0 <= current && current <= total {|0->1": "equivalent: a normalised clock is at least 1, so current >= 0 and current >= 1 agree",
    "monitor.rs|let best = next_candidates.get(&total).copied().unwrap_or(-1);|1->2": "equivalent: the default only feeds max() with a clock value of at least 1",
    "monitor.rs|let clock_near_end = self.last_clock_remaining.map_or(false, |r| r <= |false->true": "equivalent: last_clock_remaining and last_clock_at are None together, so a default of true on one side is still gated by the other",
    "monitor.rs|let clock_near_end = self.last_clock_remaining.map_or(false, |r| r <= |false->true": "equivalent: last_clock_remaining and last_clock_at are None together, so a default of true on one side is still gated by the other",
    "monitor.rs|let remaining_kt = p.display_times.map_or(0.0, |(a, b)| b - a);|0.0->1.0": "equivalent: with no display times the remaining is 0 or 1, never above the 35 s threshold",
    # key: "<file>|<first 70 chars of the line>|<old>-><new>"
    "lib.rs|let mut out = String::with_capacity(value.len() + 2);|2->3": "equivalent: only a capacity hint of the output string",
    "lib.rs|let mut groups = 0;|0->1": "equivalent: with the counter starting at 1 the empty suffix at the very end matches, which cuts nothing (the text is stripped, so no earlier suffix is only whitespace)",
    "lib.rs|groups += 1;|1->2": "equivalent: the counter is only ever tested for being above zero",
    "lib.rs|return groups > 0 && chars[j..].is_empty();| > -> >= ": "equivalent: a match with no group at all is the empty suffix at the very end, which cuts nothing",
    "pure.rs|let mut it = text.splitn(2, '.');|2->3": "equivalent: a text with two dots reaches None either way (the number test fails, then the clock test fails)",
    "pure.rs|let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::M| > -> >= ": "equivalent: overflow guard for absurdly long digit runs; every such value ends as None",
    "pure.rs|let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::M|30->31": "equivalent: same overflow guard, only the bound moves",
    "pure.rs|let values: Vec<u128> = parts.iter().map(|p| if p.len() > 30 { u128::M|4->5": "equivalent: same overflow guard, only the 'huge' value moves; it is far above 86400 either way",
    "pure.rs|Value::Number(n) => n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(|0.0->1.0": "equivalent: a JSON number always has an f64 value, the fallback never runs",
    "pure.rs|Value::String(s) => s.trim().parse::<f64>().map(|f| f.trunc() as i64).|0->1": "not modelled: Python int() of an unparsable string raises; the app only passes numbers here",
    "pure.rs|_ => 0,|0->1": "not modelled: Python int() of None or a container raises; the app only passes numbers here",
    "pure.rs|Value::Number(n) => n.as_f64().unwrap_or(0.0),|0.0->1.0": "equivalent: a JSON number always has an f64 value, the fallback never runs",
    "ladder.rs|let mut out = String::with_capacity(s.len() + 2);|2->3": "equivalent: only a capacity hint of the output string",
    "ladder.rs|c if (c as u32) < 0x7f => out.push(c),| < -> <= ": "equivalent: 0x7f is caught by the control-character arm above it",
    "ladder.rs|text.contains(\"-1743\") || text.contains(\"not authorized to send apple |&&->||": "see the corpus: partial phrases are in the pool now (killed); kept for line shifts",
    "ladder.rs|if text.is_empty() || text.contains(\"-1743\") || text.contains(\"-25211\"|||->&&": "equivalent in the ladder: a permission error raises before this check and last_error is never empty",
    "ladder.rs|return false;|false->true": "equivalent in the ladder: last_error is never empty and permission errors raise before this check",
    "ladder.rs|let mut ok = false;|false->true": "equivalent: ok is always assigned by the first try before it is read",
    "ladder.rs|if parts.len() < 3 || ![\"FOUND\", \"TITLE_ONLY\", \"FIRST\"].contains(&part|||->&&": "equivalent: found is either empty or a valid accepted answer, so both halves agree",
    "pure.rs|_ => 0.0,|0.0->1.0": "not modelled: a string seek offset (Python float() would parse it); the app only passes numbers or None",
    "pure.rs|let mut in_space = false;|false->true": "equivalent: the whitespace collapse runs after the regex that already turned every non-alphanumeric run into one space, and the result is stripped",
    "pure.rs|in_space = true;|true->false": "equivalent: same reason, no run of spaces can reach the collapse",
    # --- geometry.rs (2026-11-08) ---
    # --- methods.rs (2026-11-08) ---
    'methods.rs|(v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_strin|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|self.ask("mouse_click").as_bool().unwrap_or(false)|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|self.ask("double_click").as_bool().unwrap_or(false)|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|let current = w.ask("session_current").as_bool().unwrap_or(false);|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|return (false, "declined".into());|false->true': 'equivalent: a declined case is excluded from every comparison, so the first element of its result is never read',
    'methods.rs|return (false, "declined".into(), Value::Object(entry));|false->true': 'equivalent: a declined case is excluded from every comparison, so the first element of its result is never read',
    'methods.rs|Some(Value::Number(n)) => n.as_f64().map_or(true, |f| f != 0.0),|true->false': 'equivalent: a JSON number always converts to f64, so the default is unreachable',
    'methods.rs|Some(Value::String(s)) => !s.is_empty(),|!->': 'equivalent: the methods only test booleans and numbers for truthiness',
    'methods.rs|Some(Value::Array(a)) => !a.is_empty(),|!->': 'equivalent: the methods only test booleans and numbers for truthiness',
    'methods.rs|Some(Value::Object(o)) => !o.is_empty(),|!->': 'equivalent: the methods only test booleans and numbers for truthiness',
    'methods.rs|(v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_strin|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|(v[0].as_bool().unwrap_or(false), v[1].as_str().unwrap_or("").to_strin|2->3': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|let (kd, td) = (a.parse::<i64>().unwrap_or(0), b.parse::<i64>().unwrap|0->1': 'equivalent: at most 18 ASCII digits always parse as i64, so the unwrap_or default is never used',
    'methods.rs|let (kd, td) = (a.parse::<i64>().unwrap_or(0), b.parse::<i64>().unwrap|0->1': 'equivalent: at most 18 ASCII digits always parse as i64, so the unwrap_or default is never used',
    'methods.rs|let capped = if duration - 8.0 < inp.target { duration - 8.0 } else { | < -> <= ': 'equivalent: at equality both branches give the same number',
    'methods.rs|let target = if capped > 0.0 { capped } else { 0.0 };| > -> >= ': 'equivalent: at capped == 0 both branches give 0.0',
    'methods.rs|let item = if steps > 0 { "Skip Forward 10s" } else { "Skip Back 10s" | > -> >= ': 'equivalent: steps == 0 returned earlier, so `steps > 0` and `steps >= 0` agree here',
    'methods.rs|done = d.parse::<i64>().unwrap_or(0);|0->1': 'equivalent: at most 18 ASCII digits always parse as i64, so the unwrap_or default is never used',
    'methods.rs|let moved = (done as f64) * 10.0 * (if steps > 0 { 1.0 } else { -1.0 }| > -> >= ': 'equivalent: steps == 0 returned earlier, so `steps > 0` and `steps >= 0` agree here',
    'methods.rs|let base = if py_truthy(entry.get("karafun_seek_offset_s")) { entry["k|0.0->1.0': 'equivalent: the value is only read when its entry field is truthy, and a truthy field is always a number, so the unwrap_or default is never used',
    'methods.rs|let started = entry["karafun_display_started_at"].as_f64().unwrap_or(0|0.0->1.0': 'equivalent: the value is only read when its entry field is truthy, and a truthy field is always a number, so the unwrap_or default is never used',
    'methods.rs|let reference = if py_truthy(entry.get("karafun_paused_at")) { entry["|0.0->1.0': 'equivalent: the value is only read when its entry field is truthy, and a truthy field is always a number, so the unwrap_or default is never used',
    'methods.rs|entry.insert("karafun_display_started_at".into(), json!(if reference <| < -> <= ': 'equivalent: at equality both branches give the same value',
    'methods.rs|let timeout = sc["timeout"].as_f64().unwrap_or(60.0);|60.0->61.0': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|if let Err(e) = wait_until_ready(&mut w, sc["was_running"].as_bool().u|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|no_entry: sc["no_entry"].as_bool().unwrap_or(false),|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|entry_changes: sc["entry_changes"].as_bool().unwrap_or(false),|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|target: sc["target"].as_f64().unwrap_or(0.0),|0.0->1.0': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|seek_running: sc["seek_running"].as_bool().unwrap_or(false),|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|let (k, t) = apply_key_tempo(&mut w, &mut entry, sc["target_key"].as_i|0->1': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|let (k, t) = apply_key_tempo(&mut w, &mut entry, sc["target_key"].as_i|100->101': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|native_double_click: sc["settings"]["karafun_native_double_click"].as_|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|change_session_on_resolve: sc["change_session_on_resolve"].as_bool().u|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|reuse_point: sc["reuse_point"].as_bool().unwrap_or(false),|false->true': 'equivalent under the replay contract: the scripted world / scenario always supplies a value of the right type, so this default is never read',
    'methods.rs|(Ok(a), Ok(b)) if a.is_finite() && b.is_finite() => (a, b),|&&->||': 'equivalent: every timeout in the scenarios is finite, so both operands are always finite',
    'methods.rs|let looks_nonascii = parts[1].chars().chain(parts[2].chars()).any(|c| |==->!=': 'equivalent for correctness: this guard only decides to DECLINE (decline more = fewer cases compared, never a wrong answer); the declined count in the parity report shows the effect',
    'methods.rs|let looks_nonascii = parts[1].chars().chain(parts[2].chars()).any(|c| |!->': 'equivalent for correctness: this guard only decides to DECLINE (decline more = fewer cases compared, never a wrong answer); the declined count in the parity report shows the effect',
    "methods.rs|if t.chars().any(|c| !c.is_ascii()) || t.contains('_') {|||->&&": 'equivalent: Rust f64 parsing never accepts a non-ASCII digit, so the text falls into the failure branch, whose looks_nonascii check declines exactly as the guard would',
}


FILES = ("lib.rs", "pure.rs", "ladder.rs", "monitor.rs", "readback.rs", "watchdog.rs", "startflow.rs", "flow.rs", "geometry.rs", "methods.rs", "prelude.rs", "setup.rs", "finish.rs", "intake.rs", "helpers.rs", "worker.rs", "machine.rs")


def generate(name, text):
    lines = text.split("\n")
    first = next(i for i, l in enumerate(lines) if l.startswith(("fn ", "pub(crate) fn ", "pub fn ", "pub enum ", "/// A value", "use ", "pub enum")))
    last = next((i for i, l in enumerate(lines) if l.startswith("#[cfg(test)]")), len(lines))
    out = []
    for ln in range(first, last):
        line = lines[ln]
        if line.strip().startswith(("//", "///", "use ", "static ")) or ("fn " in line and "{" in line and "(" in line and "if " not in line and "match" not in line):
            continue
        for a, b in ms.code_spans(line):
            seg = line[a:b]
            for pat, repl in ms.PATTERNS:
                for m in pat.finditer(seg):
                    s, e = a + m.start(), a + m.end()
                    tok = line[s:e]
                    if repl is None:
                        if "." in tok:
                            new = repr(float(tok) + 1.0) if float(tok) < 100 else repr(float(tok) - 1.0)
                        else:
                            new = str(int(tok) + 1)
                    else:
                        new = repl
                    if line[:s] + new + line[e:] != line:
                        out.append((name, ln + 1, s, e, new, line.strip()[:90]))
    return out


def setup():
    (WORK / "src").mkdir(parents=True, exist_ok=True)
    (WORK / "Cargo.toml").write_text(__import__("re").sub(r"# A live runner.*?\[dependencies\]\n", "[dependencies]\n", (REAL / "Cargo.toml").read_text(), flags=__import__("re").S).replace('singws-mac = { path = "../singws-mac", optional = true }\n', "") + "\n[workspace]\n")
    shutil.copy(ROOT / "rust/Cargo.lock", WORK / "Cargo.lock")
    for name in ("main.rs", "script_lines.rs", "casefold_table.rs", "builders.rs", "lib.rs", "pure.rs", "ladder.rs", "monitor.rs", "readback.rs", "watchdog.rs", "startflow.rs", "flow.rs", "prelude.rs", "setup.rs", "finish.rs", "intake.rs", "helpers.rs", "worker.rs", "machine.rs", "geometry.rs", "methods.rs"):
        shutil.copy(REAL / "src" / name, WORK / "src" / name)


def main():
    import karafun_script_parity as par
    release = Path(os.path.expanduser("~/.cache/singws-rust-target-karafun/release/singws-karafun"))
    real_run = par.run_rust
    captured = []

    def spy(binary, batch):
        resp = real_run(binary, batch)
        captured.append((batch, resp))
        return resp
    par.run_rust = spy
    assert par.main(["--seeds", str(SEEDS), "--binary", str(release)]) == 0, "the unmutated crate must equal the Python first"
    import karafun_ladder_parity as lp
    lp.run_rust = spy
    assert lp.main(["--seeds", str(SEEDS * 4), "--binary", str(release)]) == 0, "the ladder must equal the Python first"
    import karafun_monitor_parity as mpar
    mpar.run_rust = spy
    assert mpar.main(["--seeds", str(SEEDS * 5), "--binary", str(release)]) == 0, "the monitor must equal the Python first"
    import karafun_watchdog_parity as wdp
    wdp.mp.run_rust = spy
    assert wdp.main(["--seeds", str(SEEDS * 5), "--binary", str(release)]) == 0, "the watchdog must equal the Python first"
    import karafun_startflow_parity as sfp
    sfp.run_rust = spy
    assert sfp.main(["--binary", str(release)]) == 0, "startflow must equal the Python first"
    import karafun_flow_parity as flp
    flp.run_rust = spy
    assert flp.main(["--seeds", str(SEEDS * 3), "--binary", str(release)]) == 0, "the post-ladder flow must equal the Python first"
    import karafun_geometry_parity as gp
    gp.run_rust = spy
    assert gp.main(["--seeds", str(SEEDS), "--binary", str(release)]) == 0, "the geometry must equal the Python first"
    import karafun_methods_parity as mp2
    mp2.run_rust = spy
    assert mp2.main(["--seeds", str(SEEDS), "--binary", str(release)]) == 0, "the four methods must equal the Python first"
    import karafun_prelude_parity as ppar
    ppar.run_rust = spy
    assert ppar.main(["--seeds", str(SEEDS * 2), "--binary", str(release)]) == 0, "the prelude must equal the Python first"
    import karafun_setup_parity as spar
    spar.run_rust = spy
    assert spar.main(["--seeds", str(SEEDS * 2), "--binary", str(release)]) == 0, "the setup must equal the Python first"
    import karafun_finish_parity as fpar
    fpar.run_rust = spy
    assert fpar.main(["--seeds", str(SEEDS * 2), "--binary", str(release)]) == 0, "the finish must equal the Python first"
    import karafun_intake_parity as ipar
    ipar.run_rust = spy
    assert ipar.main(["--seeds", str(SEEDS * 2), "--binary", str(release)]) == 0, "the intake must equal the Python first"
    import karafun_helpers_parity as hpar
    hpar.run_rust = spy
    assert hpar.main(["--seeds", str(SEEDS * 2), "--binary", str(release)]) == 0, "the helpers must equal the Python first"
    import karafun_machine_parity as mpar
    mpar.run_rust = spy
    assert mpar.main(["--seeds", str(SEEDS * 4), "--binary", str(release)]) == 0, "the machine decisions must equal the Python first"
    import karafun_worker_parity as wpar
    wpar.run_rust = spy
    assert wpar.main(["--seeds", str(SEEDS * 2), "--binary", str(release)]) == 0, "the whole worker must equal the Python first"
    print(f"oracle ready: {sum(len(b) for b, _ in captured)} requests in {len(captured)} batches", flush=True)
    setup()
    originals = {n: (WORK / "src" / n).read_text() for n in FILES}
    muts = [m for n in FILES for m in generate(n, originals[n])]
    print(f"mutants: {len(muts)}", flush=True)
    binary = Path(TARGET) / "debug" / "singws-karafun"
    payloads = [(json.dumps({"batch": b}).encode(), r) for b, r in captured]

    def build():
        return subprocess.run(["cargo", "build"], capture_output=True, text=True, env=ENV, cwd=WORK)

    def agrees(report=False):
        for payload, resp in payloads:
            try:
                proc = subprocess.run([str(binary)], input=payload, capture_output=True, timeout=60)
            except subprocess.TimeoutExpired:
                return False  # a mutant that hangs is killed
            if proc.returncode != 0 or json.loads(proc.stdout) != resp:
                if report:
                    got = json.loads(proc.stdout) if proc.returncode == 0 else None
                    reqs = json.loads(payload)["batch"]
                    for q, a, b in zip(reqs, resp, got or []):
                        if a != b:
                            print("BASELINE DIFFERENCE:", json.dumps(q)[:300], "expected", json.dumps(a)[:200], "got", json.dumps(b)[:200], flush=True)
                            break
                    else:
                        print("BASELINE: exit", proc.returncode, flush=True)
                return False
        return True

    base = build()
    assert base.returncode == 0, base.stderr[-600:]
    assert agrees(True), "the unmutated crate must agree with its own baseline"
    survivors, noncompiling, killed = [], [], 0
    try:
        for n, (name, ln, s, e, new, snippet) in enumerate(muts, 1):
            if ONLY and n not in ONLY:
                continue
            src = WORK / "src" / name
            lines = originals[name].split("\n")
            old = lines[ln - 1][s:e]
            lines[ln - 1] = lines[ln - 1][:s] + new + lines[ln - 1][e:]
            src.write_text("\n".join(lines))
            b = build()
            if b.returncode != 0:
                noncompiling.append(n)
                print(f"#{n} {name} L{ln}: does not compile", flush=True)
                src.write_text(originals[name])
                continue
            ok = agrees()
            src.write_text(originals[name])
            if ok:
                key = f"{name}|{snippet[:70]}|{old}->{new}"
                note = SURVIVOR_NOTES.get(key)
                survivors.append((n, key, note, snippet))
                print(f"#{n} {name} L{ln} '{old}'->'{new}': SURVIVED {note or '(UNDOCUMENTED)'}   [{snippet}]", flush=True)
            else:
                killed += 1
                print(f"#{n} {name} L{ln} '{old}'->'{new}': killed", flush=True)
    finally:
        for name, text in originals.items():
            (WORK / "src" / name).write_text(text)
    undocumented = [x for x in survivors if not x[2]]
    print(f"mutants={len(muts)} killed={killed} survivors={len(survivors)} undocumented={len(undocumented)} non-compiling={len(noncompiling)}")
    for n, key, note, snippet in undocumented:
        print(f"  UNDOCUMENTED #{n} {key} [{snippet}]")
    return 1 if undocumented else 0


if __name__ == "__main__":
    sys.exit(main())
