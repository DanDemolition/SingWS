#!/usr/bin/env python3
"""Mutation test for the host relay message builders: rust/crates/singws-relaymsg (lib.rs, main.rs), relay_msg_shadow.py and the hook in 0.2.18.1.py's
`_publish_host_state_ws`. A mutant is killed when the crate's unit tests, tools/relay_msg_parity.py (Rust files) or test_relay_msg_shadow.py (Python files)
fail. Equivalent mutants carry a note. Run it ALONE (it rewrites source files and rebuilds per mutant).

  python3 tools/mutate_relay_msg.py [--only 3,7]     CARGO_TARGET_DIR defaults to ~/.cache/singws-rust-target-relay
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "rust/crates/singws-relaymsg"
TARGET = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-relay"))
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, QT_QPA_PLATFORM="offscreen")
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + ":" + ENV["PATH"]
PY = os.environ.get("SINGWS_PYTHON", "/Users/daniel/Documents/SingWS/.venv/bin/python")
LIB, MAIN, SHADOW, APP = CRATE / "src/lib.rs", CRATE / "src/main.rs", ROOT / "relay_msg_shadow.py", ROOT / "0.2.18.1.py"

M = [
    # --- Python value semantics
    (LIB, "0x09..=0x0d | 0x1c..=0x20", "0x09..=0x0d | 0x1d..=0x20", None),
    (LIB, "| 0x3000)", ")", None),
    (LIB, "n.as_f64().map(|f| f != 0.0).unwrap_or(true)", "n.as_f64().map(|f| f != 1.0).unwrap_or(true)", None),
    (LIB, "Value::String(s) => !s.is_empty(),", "Value::String(s) => true,", None),
    (LIB, "Value::Array(a) => !a.is_empty(),", "Value::Array(a) => true,", None),
    (LIB, "Value::Object(o) => !o.is_empty(),", "Value::Object(o) => true,", None),
    (LIB, 'Value::Null => Ok("None".into()),', 'Value::Null => Ok("null".into()),', None),
    (LIB, 'Ok(if *b { "True" } else { "False" }.into())', 'Ok(if *b { "true" } else { "false" }.into())', None),
    (LIB, "    if truthy(v) {\n        py_str(v)\n    } else {", "    if !truthy(v) {\n        py_str(v)\n    } else {", None),
    (LIB, "Value::Bool(_) => Ok(1),", "Value::Bool(_) => Ok(0),", None),
    (LIB, "if f.is_finite() && f.abs() < 9.0e15 {\n                    Ok(f.trunc() as i64)", "if f.is_finite() && f.abs() < 9.0e15 {\n                    Ok(f.round() as i64)", None),
    (LIB, "if t.contains('_') || !t.is_ascii() {", "if t.contains('_') {", None),
    (LIB, "            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {\n                return Err(E::Raise);", "            if !digits.bytes().all(|b| b.is_ascii_digit()) {\n                return Err(E::Raise);", None),
    (LIB, "let t = s.trim_matches(py_space);\n            if t.contains", "let t = s.as_str();\n            if t.contains", None),
    (LIB, "        _ => Err(E::Raise),\n    }\n}\n\n/// `int(v)` with no", "        _ => Ok(0),\n    }\n}\n\n/// `int(v)` with no", None),
    (LIB, "Value::Null | Value::Array(_) | Value::Object(_) => Err(E::Raise),", "Value::Array(_) | Value::Object(_) => Err(E::Raise),", None),
    (LIB, "Value::String(s) if s.is_empty() => Err(E::Raise),", "", None),
    (LIB, "Value::Bool(_) => 1.0,", "Value::Bool(_) => 0.0,", None),
    (LIB, ".parse::<f64>().ok().filter(|f| !f.is_nan()).unwrap_or(0.0)", ".parse::<f64>().ok().unwrap_or(0.0)", None),
    (LIB, "s.chars().take(n).collect()", "s.chars().take(n + 1).collect()", None),
    (LIB, "v[v.len().saturating_sub(n)..].to_vec()", "v[..v.len().min(n)].to_vec()", None),
    (LIB, 'if let Some(u) = settings.get("user") {', 'if let Some(u) = settings.get("tenant") {', None),
    # --- tick and state
    (LIB, 'f("position_seconds", json!(0.0))', 'f("position_seconds", json!(0))', None),
    (LIB, 'f("duration_seconds", json!(0.0))', 'f("duration_seconds", json!(1.0))', None),
    (LIB, 'f("remaining_seconds", json!(0.0))', 'f("remaining_seconds", json!(0))', None),
    (LIB, 'f("is_playing", json!(false))', 'f("is_playing", json!(true))', None),
    (LIB, 'm.insert("connected".into(), json!(true));', 'm.insert("connected".into(), json!(false));', None),
    (LIB, 'st.get("rotation").cloned().unwrap_or_else(|| json!({}))', 'st.get("rotation").cloned().unwrap_or_else(|| json!([]))', None),
    (LIB, 'st.get("playback").cloned().unwrap_or_else(|| json!({}))', 'st.get("playback").cloned().unwrap_or_else(|| json!([]))', None),
    (LIB, 'st.get("bg_volume").cloned().unwrap_or_else(|| json!(0.0))', 'st.get("bg_volume").cloned().unwrap_or_else(|| json!(0))', None),
    (LIB, 'st.get("bg_index").cloned().unwrap_or_else(|| json!(-1))', 'st.get("bg_index").cloned().unwrap_or_else(|| json!(0))', None),
    (LIB, 'st.get("bg_track").cloned().unwrap_or_else(|| json!(""))', 'st.get("bg_track").cloned().unwrap_or_else(|| json!("-"))', None),
    (LIB, 'm.insert("bg_playing".into(), json!(truthy(get(st, "bg_playing"))));', 'm.insert("bg_playing".into(), get(st, "bg_playing").clone());', None),
    (LIB, 'req.get("queue_sig").filter(|v| !v.is_null())', 'req.get("queue_sig")', None),
    (LIB, 'm.insert("app_version".into(), get(req, "app_version").clone());', 'm.insert("app_version".into(), json!(""));', None),
    # --- queue
    (LIB, "        if !singer.is_object() {\n            continue;\n        }\n        let mut songs = Vec::new();\n        for e in arr(get(singer, \"songs\")) {", "        let mut songs = Vec::new();\n        for e in arr(get(singer, \"songs\")) {", None),
    (LIB, 'let skipped = e.get("is_dict").map(truthy).unwrap_or(false) && truthy(get(e, "skipped"));\n            songs.push(json!([', 'let skipped = truthy(get(e, "skipped"));\n            songs.push(json!([', None),
    (LIB, "truthy(get(singer, \"skipped\")), truthy(get(singer, \"rotation_marker\")), songs]));", "truthy(get(singer, \"rotation_marker\")), truthy(get(singer, \"skipped\")), songs]));", None),
    (LIB, "Ok(hex[..16].to_string())", "Ok(hex[..15].to_string())", None),
    (LIB, "for singer in arr(queue).iter().take(200) {", "for singer in arr(queue).iter().take(201) {", None),
    (LIB, "for singer in arr(queue).iter().take(200) {", "for singer in arr(queue).iter().filter(|s| s.is_object()).take(200) {", None),
    (LIB, 'for e in arr(get(singer, "songs")).iter().take(25) {', 'for e in arr(get(singer, "songs")).iter().take(26) {', None),
    (LIB, 'let skipped = e.get("is_dict").map(truthy).unwrap_or(false) && truthy(get(e, "skipped"));\n            songs.push(json!({', 'let skipped = truthy(get(e, "skipped"));\n            songs.push(json!({', None),
    (LIB, '"marker": truthy(get(singer, "rotation_marker")),', '"marker": truthy(get(singer, "skipped")),', None),
    # --- waitlist
    (LIB, "if !row.is_object() {\n        return Err(E::Raise);", "if false {\n        return Err(E::Raise);", None),
    (LIB, '"title": s_or(get(row, "title"))?, "status": s_or(get(row, "status_label"))?', '"title": s_or(get(row, "title"))?, "status": s_or(get(row, "status"))?', None),
    (LIB, '"first_time": truthy(get(row, "first_time_singer"))', '"first_time": truthy(get(row, "first_time"))', None),
    (LIB, 'arr(get(w, "rows")).iter().take(40)', 'arr(get(w, "rows")).iter().take(41)', None),
    (LIB, "removed.iter().take(20)", "removed.iter().take(21)", None),
    (LIB, "float_or0_safe(get(b, \"removed_at\")).partial_cmp(&float_or0_safe(get(a, \"removed_at\")))", "float_or0_safe(get(a, \"removed_at\")).partial_cmp(&float_or0_safe(get(b, \"removed_at\")))", None),
    (LIB, "        Err(E::Raise) => vec![],\n        Err(e) => return Err(e),\n    }));\n    out.insert(\"removed\"", "        Err(E::Raise) => return Err(E::Raise),\n        Err(e) => return Err(e),\n    }));\n    out.insert(\"removed\"", None),
    (LIB, '"waitlist": waitlist(get(req, "waitlist"))?', '"waitlist": json!({})', None),
    (LIB, 'let sig = queue_sig(q)?;', 'let sig = String::new();', None),
    # --- chat message shaping
    (LIB, 'm.insert("x".into()', 'm.insert("x".into()', "placeholder, never matches"),
    (LIB, '"held" | "removed" | "blocked"', '"held" | "removed"', None),
    (LIB, 'mine = get(m, "direction") != &json!("in");', 'mine = get(m, "direction") == &json!("in");', None),
    (LIB, 'mine = truthy(get(m, "host")) || s_or(get(m, "from_key"))? == "@host";', 'mine = truthy(get(m, "host"));', None),
    (LIB, 'mine = truthy(get(m, "host")) || s_or(get(m, "from_key"))? == "@host";', 'mine = s_or(get(m, "from_key"))? == "@host";', None),
    (LIB, "sender = if truthy(f) { py_str(f)? } else { s_or(get(m, \"from_key\"))? };", "sender = s_or(f)?;", None),
    (LIB, 'if truthy(&name) { py_str(&name)? } else { "Host".to_string() }', 'py_str(&name)?', None),
    (LIB, 'let attachment = if kind == "image" && int_or0(get(m, "media_id"))? != 0 {', 'let attachment = if kind == "image" {', None),
    (LIB, 'else if kind == "gif" && gif.map(truthy).unwrap_or(false) && giphy_preview(', 'else if kind == "gif" && giphy_preview(', "an empty gif dict has no preview, so giphy_preview already rejects it"),
    (LIB, 'let text = if status == "removed" { String::new() }', 'let text = if status == "blocked" { String::new() }', None),
    (LIB, "char_prefix(&s_or(get(m, \"message\"))?, TEXT_MAX)", "char_prefix(&s_or(get(m, \"message\"))?, TEXT_MAX + 1)", None),
    (LIB, 'if status != "removed" {\n            if attachment', 'if true {\n            if attachment', None),
    (LIB, 'media_ref = format!("photo:{}", int_or0(get(m, "media_id"))?);', 'media_ref = format!("photo:{}", int_or0(get(m, "id"))?);', None),
    (LIB, 'attachment == "gif" && gif_id_ok(', 'attachment == "gif" && true || gif_id_ok(', None),
    (LIB, 'char_prefix(&s_or(get(m, "flag"))?, 24)', 'char_prefix(&s_or(get(m, "flag"))?, 25)', None),
    (LIB, 'r.trim_start_matches(|c: char| c.is_ascii_digit())', 'r', None),
    (LIB, "} else if let Some(r) = rest.strip_prefix('i') {", "} else if let Some(r) = rest.strip_prefix('j') {", None),
    (LIB, 'host_rest.starts_with(".giphy.com/")', 'host_rest.starts_with(".giphy.com")', None),
    (LIB, "let body = s.strip_suffix('\\n').unwrap_or(s);", "let body = s;", None),
    (LIB, "(1..=64).contains(&n)", "(1..=65).contains(&n)", None),
    (LIB, "(1..=64).contains(&n)", "(0..=64).contains(&n)", None),
    (LIB, "c.is_ascii_alphanumeric() || c == '_' || c == '-'", "c.is_ascii_alphanumeric() || c == '_'", None),
    # --- chat summary
    (LIB, "const GROUP_ROWS: usize = 25;", "const GROUP_ROWS: usize = 26;", None),
    (LIB, "const THREADS: usize = 10;", "const THREADS: usize = 11;", None),
    (LIB, "const THREAD_ROWS: usize = 5;", "const THREAD_ROWS: usize = 6;", None),
    (LIB, 'out.insert("attention".into(), json!(arr(get(c, "attention_ids")).len()));', 'out.insert("attention".into(), json!(0));', None),
    (LIB, 'bump(out, "attention_by", if channel == json!("dm") { "dm" } else { "group" }, 1);', 'bump(out, "attention_by", "group", 1);', None),
    (LIB, 'bump(out, "attention_by", if channel == json!("dm") { "dm" } else { "group" }, 1);', 'bump(out, "attention_by", if channel == json!("dm") { "group" } else { "dm" }, 1);', None),
    (LIB, '.unwrap_or(Value::Null),\n            None => return unsupp("attention id is not an integer"),', '.unwrap_or(json!("dm")),\n            None => return unsupp("attention id is not an integer"),', None),
    (LIB, "ordered.sort_by_key(|(k, _)| *k);", "", None),
    (LIB, 'ch(m) != json!("dm") && ch(m) != json!("host")', 'ch(m) != json!("dm")', None),
    (LIB, 'ch(m) != json!("dm") && ch(m) != json!("host")', 'ch(m) != json!("host")', None),
    (LIB, 'if s_or(get(m, "from_key"))? == "@host" && thread.contains(\'|\') {', 'if thread.contains(\'|\') {', None),
    (LIB, 'if s_or(get(m, "from_key"))? == "@host" && thread.contains(\'|\') {', 'if s_or(get(m, "from_key"))? == "@host" {', None),
    (LIB, "if a.is_empty() || b.is_empty() {", "if a.is_empty() && b.is_empty() {", None),
    (LIB, "let key = if a <= b { (a, b) } else { (b, a) };", "let key = (a, b);", None),
    (LIB, "for (_, m) in &insertion {\n            if get(m, \"from_key\")", "for (_, m) in ordered.iter() {\n            if get(m, \"from_key\")", None),
    (LIB, 'Ok(if key.is_empty() { String::new() } else { ctx.fold(key)?.1 })', 'Ok(key.to_string())', None),
    (LIB, "at = at.max(int_or0(get(x, \"created_at\"))?);", "at = at.min(int_or0(get(x, \"created_at\"))?);", None),
    (LIB, "dm.sort_by(|x, y| y.0.cmp(&x.0));", "dm.sort_by(|x, y| x.0.cmp(&y.0));", None),
    (LIB, "dm.sort_by(|x, y| y.0.cmp(&x.0));", "dm.sort_by(|x, y| y.0.cmp(&x.0).then(std::cmp::Ordering::Greater));", "an unstable tie order is a different result only when two threads share `at`; the rust sort is stable by construction"),
    (LIB, 'set_nested(out, "more", "dm", json!(dm.len().saturating_sub(THREADS)));', 'set_nested(out, "more", "dm", json!(dm.len().saturating_sub(THREADS + 1)));', None),
    (LIB, 'let key = ctx.fold(&raw)?.0;', 'let key = raw;', None),
    (LIB, 'let raw = if truthy(sk) { py_str(sk)? } else { s_or(get(msg, "singer"))? };', 'let raw = s_or(get(msg, "singer"))?;', None),
    (LIB, 'let key = ctx.fold(&s_or(get(m, "thread"))?)?.0;', 'let key = s_or(get(m, "thread"))?;', None),
    (LIB, ".chain(media.iter().map(|(k, _)| k.clone()).filter(|k| !k.is_empty()))", ".chain(media.iter().map(|(k, _)| k.clone()))", None),
    (LIB, "entries.push((int_or0(get(m, \"created_at\"))?, 1, int_or0(get(m, \"id\"))?, m, false));", "entries.push((int_or0(get(m, \"created_at\"))?, 0, int_or0(get(m, \"id\"))?, m, false));", None),
    (LIB, "entries.sort_by_key(|e| (e.0, e.1, e.2));", "entries.sort_by_key(|e| (e.0, e.2));", None),
    (LIB, "entries.sort_by_key(|e| (e.0, e.1, e.2));", "entries.sort_by_key(|e| (e.0, e.1));", None),
    (LIB, 'get(m, "direction") == &json!("in") && !truthy(get(m, "read"))', 'get(m, "direction") == &json!("in")', None),
    (LIB, 'get(m, "direction") == &json!("in") && !truthy(get(m, "read"))', '!truthy(get(m, "read"))', None),
    (LIB, 'if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") && !read.contains(&id) {', 'if s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") && !read.contains(&id) {', None),
    (LIB, 'if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") && !read.contains(&id) {', 'if !truthy(get(m, "host")) && get(m, "status") != &json!("removed") && !read.contains(&id) {', None),
    (LIB, 'if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") && !read.contains(&id) {', 'if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && !read.contains(&id) {', None),
    (LIB, 'if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") && !read.contains(&id) {', 'if !truthy(get(m, "host")) && s_or(get(m, "from_key"))? != "@host" && get(m, "status") != &json!("removed") {', None),
    (LIB, "if let Some(m) = g.iter().rev().find(|m| truthy(get(m, \"singer\"))) {", "if let Some(m) = g.iter().find(|m| truthy(get(m, \"singer\"))) {", None),
    (LIB, "} else if let Some(m) = md.iter().rev().find(|m| {", "} else if let Some(m) = md.iter().find(|m| {", None),
    (LIB, '!(fk.is_null() || fk == &json!("") || fk == &json!("@host")) && truthy(get(m, "from"))', '!(fk.is_null() || fk == &json!("")) && truthy(get(m, "from"))', None),
    (LIB, '!(fk.is_null() || fk == &json!("") || fk == &json!("@host")) && truthy(get(m, "from"))', '!(fk == &json!("") || fk == &json!("@host")) && truthy(get(m, "from"))', None),
    (LIB, 'let singer = if name.is_empty() { key.clone() } else { name };', 'let singer = name;', None),
    (LIB, "let at = entries.last().map(|e| e.0).unwrap_or(0);", "let at = entries.first().map(|e| e.0).unwrap_or(0);", None),
    (LIB, "host.sort_by(|x, y| y.0.cmp(&x.0));", "host.sort_by(|x, y| x.0.cmp(&y.0));", None),
    (LIB, 'set_nested(out, "more", "host", json!(host.len().saturating_sub(THREADS)));', 'set_nested(out, "more", "host", json!(0));', None),
    (LIB, 'set_nested(out, "unread", "host", json!(host.iter().map(|h| h.1).sum::<i64>()));', 'set_nested(out, "unread", "host", json!(host.iter().take(THREADS).map(|h| h.1).sum::<i64>()));', None),
    (LIB, '        Ok(()) | Err(E::Raise) => Ok(Value::Object(out)),', '        Ok(()) => Ok(Value::Object(out)),\n        Err(E::Raise) => Ok(json!({})),', None),
    (LIB, 'out.insert("reachable".into(), Value::Null);\n    out.insert("group_enabled".into(), json!(false));', 'out.insert("reachable".into(), json!(true));\n    out.insert("group_enabled".into(), json!(false));', "chat_fill's first statement overwrites `reachable` before anything can fail, so the default is never visible (pinned by a unit test of the visible behaviour)"),
    (LIB, 'out.insert("unread".into(), json!({"group": 0, "dm": 0, "host": 0}));', 'out.insert("unread".into(), json!({"group": 0, "dm": 0}));', None),
    (LIB, 'out.insert("more".into(), json!({"dm": 0, "host": 0}));', 'out.insert("more".into(), json!({"host": 0, "dm": 0}));', None),
    (LIB, 'Some("tick") => tick(req),', 'Some("tick") => state(req),', None),
    (LIB, 'Some("queue") => queue(req),', 'Some("queue") => chat(req),', None),
    (LIB, 'if s.is_empty() {\n            return Ok((String::new(), String::new()));', 'if s.is_empty() {\n            return unsupp("empty");', None),
    # --- the CLI
    (MAIN, "serde_json::Value::Array(items) => serde_json::Value::Array(items.iter().map(singws_relaymsg::build).collect()),", "serde_json::Value::Array(items) => serde_json::Value::Array(items.iter().rev().map(singws_relaymsg::build).collect()),", None),
    (MAIN, "std::process::exit(2);", "std::process::exit(0);", None),
    # --- the Python shadow
    (SHADOW, 'return bool((settings or {}).get("relay_msg_shadow", True))', 'return bool((settings or {}).get("relay_msg_shadow", False))', None),
    (SHADOW, "INTERVAL_S = 30.0", "INTERVAL_S = 0.0", None),
    (SHADOW, "if t - _last_started < INTERVAL_S:", "if t - _last_started > INTERVAL_S:", None),
    (SHADOW, "                _last_started = t\n", "", None),
    (SHADOW, "if not enabled(app.settings) or _helper() is None:", "if _helper() is None:", None),
    (SHADOW, "if not enabled(app.settings) or _helper() is None:", "if not enabled(app.settings):", None),
    (SHADOW, "        _helper_cache.append(find_helper())\n", "        _helper_cache.append(find_helper())\n        _helper_cache.clear()\n", None),
    (SHADOW, "if len(rooms) > MAX_CHAT_ROWS or len(chat_messages) > MAX_CHAT_ROWS:", "if len(rooms) > MAX_CHAT_ROWS:", None),
    (SHADOW, "if len(rooms) > MAX_CHAT_ROWS or len(chat_messages) > MAX_CHAT_ROWS:", "if len(chat_messages) > MAX_CHAT_ROWS:", None),
    (SHADOW, "if len(records) > MAX_REMOVED:", "if len(records) > MAX_REMOVED * 100:", None),
    (SHADOW, "if si < 200 and ei < 25:", "if True:", None),
    (SHADOW, "if si < 200 and ei < 25:", "if si < 200:", None),
    (SHADOW, "if si < 200 and ei < 25:", "if ei < 25:", None),
    (SHADOW, "            rows.append(None)\n            continue", "            continue", None),
    (SHADOW, '"skipped": bool(entry.get("skipped", False)) if is_dict else False})', '"skipped": False})', None),
    (SHADOW, '"is_dict": is_dict, "entry_id"', '"is_dict": True, "entry_id"', None),
    (SHADOW, '"rotation_marker": bool(singer.get("rotation_marker", False))', '"rotation_marker": False', None),
    (SHADOW, "if section.get(\"key\") == \"waitlist\":\n                rows = [_wait_row(r) for r in section.get(\"rows\", [])]", "if section.get(\"key\") == \"waitlist\":\n                rows = rows + [_wait_row(r) for r in section.get(\"rows\", [])]", None),
    (SHADOW, "records = [r for r in (getattr(app, \"_waiting_for_add_removed_tonight\", {}) or {}).values() if isinstance(r, dict)]", "records = list((getattr(app, \"_waiting_for_add_removed_tonight\", {}) or {}).values())", None),
    (SHADOW, '                row = None\n            removed.append', '                continue\n            removed.append', None),
    (SHADOW, 'removed.append({"removed_at": _plain(r.get("removed_at")), "row": row})', 'removed.append({"removed_at": None, "row": row})', None),
    (SHADOW, "fold = {s: [s.casefold(), s.title()] for s in strings}", "fold = {s: [s.lower(), s.title()] for s in strings}", None),
    (SHADOW, "fold = {s: [s.casefold(), s.title()] for s in strings}", "fold = {s: [s.casefold(), s.capitalize()] for s in strings}", None),
    (SHADOW, '            strings.update(p for p in v.split("|") if p)', "            pass", None),
    (SHADOW, 'for f in ("thread", "from_key", "to_key"):', 'for f in ("thread", "from_key"):', None),
    (SHADOW, 'for f in ("singer_key", "singer"):', 'for f in ("singer_key",):', None),
    (SHADOW, 'pairs = [[k, _pick(m, ROOM_FIELDS)] for k, m in rooms.items()]', 'pairs = [[k, _pick(m, ROOM_FIELDS)] for k, m in sorted(rooms.items())]', None),
    (SHADOW, '"room_settings": {"group_enabled": bool(settings.get("group_enabled")), "dm_enabled": bool(settings.get("dm_enabled"))}', '"room_settings": {"group_enabled": True, "dm_enabled": True}', None),
    (SHADOW, '"new": {"group": _plain(new.get("group", 0)), "dm": _plain(new.get("dm", 0))}', '"new": {"group": 0, "dm": 0}', None),
    (SHADOW, '"media_read": [_plain(i) for i in (getattr(app, "_host_media_read", None) or ())]', '"media_read": []', None),
    (SHADOW, '"host_chat_name": _plain(app.settings.get("host_chat_name"))', '"host_chat_name": None', None),
    (SHADOW, 'out["gif"] = ({k: _plain(gif[k]) for k in ("preview", "id") if k in gif} if isinstance(gif, dict) else _plain(gif))', 'out["gif"] = {}', None),
    (SHADOW, 'ROOM_FIELDS = ("id", "channel", "thread", "from_key", "to_key", "from", "kind", "media_id", "message", "status", "host", "created_at", "reports", "flag")', 'ROOM_FIELDS = ("id", "channel", "thread", "from_key", "to_key", "from", "kind", "message", "status", "host", "created_at", "reports", "flag")', None),
    (SHADOW, 'HOST_TEXT_FIELDS = ("id", "singer_key", "singer", "direction", "read", "message", "status", "created_at", "reports", "flag", "kind", "media_id", "host", "from", "from_key")', 'HOST_TEXT_FIELDS = ("id", "singer_key", "singer", "direction", "message", "status", "created_at", "reports", "flag", "kind", "media_id", "host", "from", "from_key")', None),
    (SHADOW, 'for k in ("user", "tenant") if k in app.settings}', 'for k in ("user",) if k in app.settings}', None),
    (SHADOW, '"state": {k: _plain(state[k]) for k in _STATE_KEYS if isinstance(state, dict) and k in state}', '"state": {}', None),
    (SHADOW, '"queue_sig": msg.get("queue_sig")}', '"queue_sig": None}', None),
    (SHADOW, '"updated_at": at}\n            elif kind == "state"', '"updated_at": 0}\n            elif kind == "state"', None),
    (SHADOW, '"app_version": app_version,', '"app_version": "",', None),
    (SHADOW, 'req = {"op": "tick", "playback": _plain((state or {}).get("playback", {}) if isinstance(state, dict) else {}), "updated_at": at}', 'req = {"op": "tick", "playback": {}, "updated_at": at}', None),
    (SHADOW, 'expected = json.dumps(msg, ensure_ascii=False)', 'expected = json.dumps(msg, ensure_ascii=True)', "the comparison parses both texts, so the escaping style does not change the result"),
    (SHADOW, "    if \"unsupported\" in rust:\n        return None", "    if \"unsupported\" in rust:\n        return 'unsupported'", None),
    (SHADOW, "if type(a) is not type(b):", "if False:", None),
    (SHADOW, "        if list(a) != list(b):", "        if False:", None),
    (SHADOW, 'return f"{path}.<len>", len(a), len(b)', "return None", None),
    (SHADOW, "key=lambda t: (-int(t.get(\"at\", 0)), str(t.get(\"key\", \"\")))", "key=lambda t: (0, 0)", None),
    (SHADOW, 'if kind == "chat" and isinstance(msg, dict)', 'if isinstance(msg, dict)', "only the chat message has chat.host"),
    (SHADOW, "if not isinstance(out, list) or len(out) != len(requests):", "if not isinstance(out, list):", None),
    (SHADOW, "if proc.returncode != 0:", "if proc.returncode == 99:", None),
    (SHADOW, "preexec_fn=lambda: os.nice(10))", "preexec_fn=None)", None),
    (SHADOW, "            if d in _seen:\n                continue", "            if False:\n                continue", None),
    (SHADOW, "            _seen.add(d)\n            say(", "            say(", None),
    (SHADOW, "if len(_seen) >= MAX_LOGGED:", "if len(_seen) >= MAX_LOGGED * 100:", None),
    (SHADOW, "if helper is None or not batch:", "if helper is None:", None),
    (SHADOW, "    except Unsnapshottable:\n        return None\n    except Exception:  # noqa: BLE001\n        return None", "    except Unsnapshottable:\n        raise", None),
    (SHADOW, "    if rec is None:\n        return None\n    try:\n        batch", "    if rec is None:\n        return None\n    if True:\n        batch", "the try only guards the same exceptions the outer wrapper in the app swallows too; the unit test for finish uses a recorder that raises"),
    # --- the hook in the app
    (APP, '    "relay_msg_shadow": True,', '    "relay_msg_shadow": False,', None),
    (APP, "            shadow_rec = relay_msg_shadow.Recorder.start(self)\n", "            shadow_rec = None\n", None),
    (APP, "                worker = shadow_rec.wrap(worker)  # forwards", "                pass  # forwards", None),
    (SHADOW, "                result = worker.send_json(msg, *args, **kwargs)\n                rec.add(msg)", "                rec.add(msg)\n                result = worker.send_json(msg, *args, **kwargs)", None),
    (SHADOW, "                rec.add(msg)\n                return result", "                return result", None),
    (SHADOW, "                return result\n\n            def __getattr__", "                return None\n\n            def __getattr__", None),
    (SHADOW, "                return getattr(worker, name)", "                raise AttributeError(name)", None),
    (APP, "                relay_msg_shadow.finish(shadow_rec, state, APP_VERSION, logging.info)", "                relay_msg_shadow.finish(shadow_rec, state, '', logging.info)", None),
    (APP, "                relay_msg_shadow.finish(shadow_rec, state, APP_VERSION, logging.info)", "                relay_msg_shadow.finish(shadow_rec, {}, APP_VERSION, logging.info)", None),
    (APP, "                relay_msg_shadow.finish(shadow_rec, state, APP_VERSION, logging.info)", "                pass", None),
]
M = [m for m in M if m[3] != "placeholder, never matches"]


def tests_for(path):
    if path in (SHADOW, APP):
        return [[PY, "-m", "unittest", "test_relay_msg_shadow"]]
    return [["cargo", "test", "--release", "-p", "singws-relaymsg", "--offline"], ["cargo", "build", "--release", "-p", "singws-relaymsg", "--offline"],
            [PY, "tools/relay_msg_parity.py", "--cases", "150", "--no-real"]]


def run_all(path):
    for cmd in tests_for(path):
        cwd = ROOT / "rust" if cmd[0] == "cargo" else ROOT
        env = dict(ENV, SINGWS_HOME=tempfile.mkdtemp(prefix="mutate_relaymsg_home_"))
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=1500)
        out = p.stdout + p.stderr
        if p.returncode != 0:
            if "error[E" in out or "could not compile" in out:
                return "invalid"
            return "killed"
    return "survived"


def main():
    only = None
    if "--only" in sys.argv:
        only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")}
    survivors, invalid = [], []
    for n, (path, old, new, note) in enumerate(M, 1):
        if only and n not in only:
            continue
        original = path.read_text()
        if original.count(old) != 1:
            print(f"#{n}: pattern found {original.count(old)} times in {path.name}: {old[:60]!r}")
            invalid.append(n)
            continue
        path.write_text(original.replace(old, new))
        try:
            verdict = run_all(path)
        finally:
            path.write_text(original)
        tag = {"killed": "killed", "survived": "SURVIVED" + (f" (documented equivalent: {note})" if note else ""), "invalid": "INVALID (does not compile)"}[verdict]
        print(f"#{n} {path.name}: {tag}   {old[:50]!r} -> {new[:40]!r}", flush=True)
        if verdict == "survived" and not note:
            survivors.append(n)
        if verdict == "invalid":
            invalid.append(n)
    subprocess.run(["cargo", "build", "--release", "-p", "singws-relaymsg", "--offline"], cwd=ROOT / "rust", env=ENV, capture_output=True)
    print(f"\n{len(M)} mutants; undocumented survivors: {survivors or 'none'}; invalid: {invalid or 'none'}")
    return 1 if survivors or invalid else 0


if __name__ == "__main__":
    sys.exit(main())
