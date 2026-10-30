#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-start/src/lib.rs: every mutant must be caught by the crate's own tests OR by the Python differential test
(test_start_shadow and test_start_shadow_wiring, which needs the release dylib rebuilt for each mutant). Equivalent mutants are documented in the list.

The second list, PM, mutates the Python GLUE of the live whole-start-plan shadow (the plan_* methods of start_shadow.py and the hooks in 0.2.18.1.py); each of those must be
caught by test_start_shadow_plan_live and its neighbours. MUTATE_FIRST counts across both lists (Rust first); MUTATE_ONLY=py runs only the glue mutants, =rust only the Rust ones."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-start/src/lib.rs"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), QT_QPA_PLATFORM="offscreen", SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS="1")
PY = str(ROOT / ".venv/bin/python")
IDS = {int(x) for x in os.environ.get("MUTATE_IDS", "").split(",") if x}   # run only these glue mutants (indices as printed)
FIRST = int(os.environ.get("MUTATE_FIRST", "0"))   # skip the mutants before this index (to run only the newest ones)

M = [
    ('    if i.external {\n        return Route::ExternalKarafun;', '    if false {\n        return Route::ExternalKarafun;', None),
    ('            Some(false) => return Route::RejectMissingFile,', '            Some(true) => return Route::RejectMissingFile,', None),
    ('            None => return Route::Need("primary_exists"),', '            None => {}', None),
    ('    if i.has_primary {\n        match i.primary_exists {', '    if true {\n        match i.primary_exists {', None),
    ('        if s.to_lowercase().ends_with(".zip") {', '        if s.ends_with(".zip") {', None),
    ('                Some(false) => return Route::ZipPrepare,', '                Some(true) => return Route::ZipPrepare,', None),
    ('                None => return Route::Need("zip_cached"),', '                None => {}', None),
    ('            let Some(first) = items.first() else { return Route::Error };', '            let Some(first) = items.first() else { return Route::Mp4 };', None),
    ('            if first.is_str() && first.text().ends_with(".cdg") {', '            if first.text().ends_with(".cdg") {', None),
    ('                return if items.len() == 2 { Route::CdgMp3Dual } else { Route::Error };', '                return if items.len() >= 2 { Route::CdgMp3Dual } else { Route::Error };', None),
    ('                return if items.len() == 2 { Route::CdgMp3Dual } else { Route::Error };', '                return Route::CdgMp3Dual;', None),
    ('            if first.is_str() && first.text().ends_with(".mp4") {\n                return Route::Mp4;\n            }\n', '', "an unlisted non-text .mp4 first item is judged by its str() on the next line and gives mp4 as well"),
    ('            } else if shown.ends_with(".mp3") {\n                Route::Mp3\n            } else {\n                Route::RejectUnsupportedTuple', '            } else if shown.ends_with(".mp3") {\n                Route::Mp4\n            } else {\n                Route::RejectUnsupportedTuple', None),
    ('                Route::RejectUnsupportedTuple\n            }\n        }\n        Info::Str', '                Route::RejectUnsupportedFormat\n            }\n        }\n        Info::Str', None),
    ('            if s.ends_with(".mp4") {\n                Route::Mp4', '            if s.ends_with(".mp3") {\n                Route::Mp4', None),
    ('            } else if s.ends_with(".mp3") {\n                Route::Mp3\n            } else if s.ends_with(".zip") {', '            } else if s.ends_with(".mp3") {\n                Route::Mp4\n            } else if s.ends_with(".zip") {', None),
    ('                    Some(true) => Route::Mp3gZip,', '                    Some(true) => Route::RejectInvalidZip,', None),
    ('                    Some(false) => Route::RejectInvalidZip,', '                    Some(false) => Route::Mp3gZip,', None),
    ('                    None => Route::Need("mp3g"),', '                    None => Route::Mp3gZip,', None),
    ('                    Some(true) => Route::CdgMp3Dual,\n                    Some(false) => Route::RejectMissingCdgAudioPair,', '                    Some(true) => Route::RejectMissingCdgAudioPair,\n                    Some(false) => Route::CdgMp3Dual,', None),
    ('                    None => Route::Need("pair"),', '                    None => Route::CdgMp3Dual,', None),
    ('            } else {\n                Route::RejectUnsupportedFormat\n            }\n        }\n        Info::Other', '            } else {\n                Route::RejectInvalidZip\n            }\n        }\n        Info::Other', None),
    ('        Info::Other(_) => Route::Error,', '        Info::Other(_) => Route::RejectUnsupportedFormat,', None),
    ('            } else if s.ends_with(".cdg") {', '            } else if s.to_lowercase().ends_with(".cdg") {', None),
    # ---- engine route
    ('    let (voice, reason) = if !i.want_rust {\n        (false, "")', '    let (voice, reason) = if i.want_rust {\n        (false, "")', None),
    ('    } else if !i.supports_shadow_audio {\n        (false, "bridge_unsupported")', '    } else if i.supports_shadow_audio {\n        (false, "bridge_unsupported")', None),
    ('    } else if i.engine != Engine::Running {', '    } else if i.engine == Engine::None {', None),
    ('    } else if i.engine != Engine::Running {', '    } else if i.engine == Engine::Running {', None),
    ('    } else if i.voice_error {\n        (false, "error")', '    } else if false {\n        (false, "error")', None),
    ('        (false, "bridge_unsupported")', '        (false, "engine_not_running")', None),
    ('        (false, "engine_not_running")\n    } else if i.voice_error', '        (false, "bridge_unsupported")\n    } else if i.voice_error', None),
    ('        (true, "")\n    };', '        (false, "")\n    };', None),
    ('set_shadow_audio: if i.supports_shadow_audio { Some(voice) } else { None } }', 'set_shadow_audio: Some(voice) }', None),
    ('set_shadow_audio: if i.supports_shadow_audio { Some(voice) } else { None } }', 'set_shadow_audio: if i.supports_shadow_audio { Some(true) } else { None } }', None),
    ('    if v.get("op").and_then(serde_json::Value::as_str) == Some("engine") {', '    if v.get("op").and_then(serde_json::Value::as_str) == Some("route") {', None),
    # ---- who sings next
    ("singers.iter().any(|s| s.songs.chars().any(|c| !matches!(c, 'd' | 's' | 't')))", "singers.iter().any(|s| s.songs.chars().any(|c| !matches!(c, 'd' | 's')))", None),
    ("let bad = |s: &RotSinger| s.songs.chars().any(|c| !matches!(c, 'd' | 's' | 't'));", "let bad = |s: &RotSinger| s.songs.chars().any(|c| !matches!(c, 'd' | 's'));", None),
    # ---- display payload
    ('    let shown: String = if !a.is_empty() && !t.is_empty() {', '    let shown: String = if !a.is_empty() || !t.is_empty() {', None),
    ('    let (a, t) = (py_strip(entry_artist), py_strip(entry_title));', '    let (a, t) = (entry_artist, entry_title);', None),
    ('    let (a, t) = (py_strip(entry_artist), py_strip(entry_title));', '    let (a, t) = (py_strip(entry_artist), entry_title);', None),
    ('    Some(match shown.split_once(" • ") {', '    Some(match shown.rsplit_once(" • ") {', None),
    ('        Some((x, y)) => (py_strip(x).to_string(), py_strip(y).to_string()),', '        Some((x, y)) => (x.to_string(), py_strip(y).to_string()),', None),
    ('        Some((x, y)) => (py_strip(x).to_string(), py_strip(y).to_string()),', '        Some((x, y)) => (py_strip(x).to_string(), y.to_string()),', None),
    ('        None => (py_strip(&shown).to_string(), String::new()),', '        None => (shown.clone(), String::new()),', None),
    ('        None => (py_strip(&shown).to_string(), String::new()),', '        None => (py_strip(&shown).to_string(), py_strip(&shown).to_string()),', None),
    ("    c.is_whitespace() || ('\\u{1c}'..='\\u{1f}').contains(&c)", "    c.is_whitespace()", None),
    ("('\\u{1c}'..='\\u{1f}')", "('\\u{1c}'..='\\u{1e}')", None),
    ('fallback?.to_string() };', 'fallback.unwrap_or("").to_string() };', None),
    ('        return Ok(serde_json::json!({"artist": artist, "title": title}).to_string());', '        return Ok(serde_json::json!({"artist": title, "title": artist}).to_string());', None),
    # ---- rotation after a start
    ('    me.has_sung = true;', '    me.has_sung = false;', None),
    ('        me.round_sung = Some(true);', '        me.round_sung = Some(false);', None),
    ('    if !rotation_mode {\n        return q;\n    }', '    if false {\n        return q;\n    }', None),
    ('    if marked.len() != 1 {', '    if marked.len() > 1 {', None),
    ('    if marked.len() != 1 {', '    if marked.len() < 1 {', None),
    ('    let keep = marked.first().copied().unwrap_or(0);'.replace('    let','        let'), '        let keep = marked.last().copied().unwrap_or(0);', None),
    ('            if s.round_sung.is_none() {\n                s.round_sung = Some(false);', '            if s.round_sung.is_none() {\n                s.round_sung = Some(true);', None),
    ('        let after = after_start(&queue, &singer, flag("rotation_mode"), flag("marker_was_top"), flag("setting_locked"));', '        let after = after_start(&queue, &singer, flag("marker_was_top"), flag("rotation_mode"), flag("setting_locked"));', None),
    # ---- the rotation lock
    ('    let pinned = marker_idx.filter(|&i| !q[i].active);', '    let pinned = marker_idx.filter(|&i| q[i].active);', None),
    ('    let Some(tail_start) = [marker_idx, first_new].into_iter().flatten().min() else {', '    let Some(tail_start) = [marker_idx, first_new].into_iter().flatten().max() else {', None),
    ('    new_tail.sort_by_key(|&i| q[i].lock_order); // stable, like Python\'s sort', '    new_tail.sort_by_key(|&i| -q[i].lock_order);', None),
    ('    if let Some(p) = pinned {\n        order.push(p);\n    }', '', None),
    ('        if Some(i) == pinned {\n            continue;\n        }', '', None),
    ('        if let Some(&i) = old_tail.get(k) {\n            order.push(i);\n        }\n        if let Some(&i) = new_tail.get(k) {\n            order.push(i);\n        }', '        if let Some(&i) = new_tail.get(k) {\n            order.push(i);\n        }\n        if let Some(&i) = old_tail.get(k) {\n            order.push(i);\n        }', None),
    ('    mode && setting && !q.is_empty() && marker_index(q, s) != Some(0)', '    mode && setting && !q.is_empty() && marker_index(q, s) == Some(0)', None),
    ('    mode && setting && !q.is_empty() && marker_index(q, s) != Some(0)', '    mode && !q.is_empty() && marker_index(q, s) != Some(0)', None),
    ('(lock_new[q[0]] || marker_was_top) {', '(lock_new[q[0]]) {', "a locked rotation never has the marker on top, so marker_was_top is false whenever the lock is on"),
    ('            setting = false;\n            cleared = true;', '            cleared = true;', "the same step clears every newcomer flag, and a reweave with no newcomers changes nothing, so a lock left on after the clear is invisible"),
    ('            lock_new.iter_mut().for_each(|x| *x = false);', '', "once the setting is off no reweave runs again, so the cleared newcomer flags are never read"),
    ('        if s[me].skipped {\n            q.push(me);\n            if is_locked(&q, s, mode, setting) {\n                q = reweave_indices(&q, s, &lock_new);\n            }\n            held = None;', '        if s[me].skipped {\n            q.push(me);\n            held = None;', None),
    ('        q.push(me);\n        if is_locked(&q, s, mode, setting) {\n            q = reweave_indices(&q, s, &lock_new);\n        }\n        held = None;', '        q.push(me);\n        held = None;', None),
    ('        held = None;\n    }', '    }', None),
    ('    if setting_locked && marker_at(&q) != Some(0) {', '    if false && marker_at(&q) != Some(0) {', None),
    ('    if setting_locked && marker_at(&q) != Some(0) {', '    if setting_locked && marker_at(&q) == Some(0) {', None),
    ('        if let Some((_, me)) = q.iter_mut().find(|(i, _)| *i == started) {', '        if let Some((_, me)) = q.last_mut() {', None),
    ('    Pick { singer: held, song: held.and_then(|i| s[i].first_playable()), queue: q, lock_cleared: cleared }', '    Pick { singer: held, song: held.and_then(|i| s[i].first_playable()), queue: q, lock_cleared: false }', None),
    # ---- pick loop (current code)
    ('            held = None; // forgotten, like the singer who has nothing playable (the app used to keep a skipped singer here; fixed in 1.0.2.1)\n            continue;', '            continue;', None),
    ('        if s[me].active() {\n            break;', '        if !s[me].active() {\n            break;', None),
    ("    fn active(&self) -> bool {\n        !self.skipped && self.songs.chars().any(|c| c != 's')\n    }\n    fn first_playable", "    fn active(&self) -> bool {\n        !self.skipped || self.songs.chars().any(|c| c != 's')\n    }\n    fn first_playable", None),
    ("        self.songs.chars().position(|c| c != 's')\n    }\n}\n\n/// What the pick loop", "        self.songs.chars().position(|c| c == 'd')\n    }\n}\n\n/// What the pick loop", None),
    ('    Pick { singer: held, song: held.and_then(|i| s[i].first_playable()), queue: q, lock_cleared: cleared }', '    Pick { singer: held, song: held.map(|_| 0), queue: q, lock_cleared: cleared }', None),
    ('"song": p.song, "queue": p.queue,', '"song": p.song, "queue": Vec::<usize>::new(),', None),
    ('"queue": p.queue, "lock_cleared": p.lock_cleared}', '"queue": p.queue, "lock_cleared": false}', None),
    ('    p.singer.map(|i| (i, p.song))', '    p.singer.map(|i| (i, None))', None),
    # ---- rotation commit (current code)
    ('    if q.iter().any(|(_, s)| s.active()) {', '    if true {', None),
    ('.all(|(_, s)| s.round_sung == Some(true)) {', '.any(|(_, s)| s.round_sung == Some(true)) {', None),
    ('            for (_, s) in q.iter_mut().filter(|(_, s)| s.active()) {', '            for (_, s) in q.iter_mut() {', None),
    ("    fn active(&self) -> bool {\n        !self.skipped && self.songs.chars().any(|c| c != 's')\n    }\n}\n\n/// The queue after a song", "    fn active(&self) -> bool {\n        !self.skipped || self.songs.chars().any(|c| c != 's')\n    }\n}\n\n/// The queue after a song", None),
    # ---- transport arguments
    ('"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: None }', '"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: duration }', None),
    ('"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s,', '"mp4" => TransportArgs { audio_path: song.into(), video_path: None, mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s,', None),
    ('"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s,', '"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: None, loop_seconds: loop_s,', None),
    ('"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s,', '"mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: None,', None),
    ('"cdg" => TransportArgs { audio_path: mp3.into(), video_path: Some(cdg.into()),', '"cdg" => TransportArgs { audio_path: cdg.into(), video_path: Some(mp3.into()),', None),
    ('mode: "cdg", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: duration }', 'mode: "cdg", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: None }', None),
    ('mode: "cdg", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: duration }', 'mode: "cdg", semitones: key, start_seconds: start, loop_seconds: None, duration_seconds: duration }', None),
    ('mode: "cdg", semitones: key, start_seconds: start,', 'mode: "cdg", semitones: key, start_seconds: Some(0.0),', None),
    ('"mp3" => TransportArgs { audio_path: song.into(), video_path: None, mode: "audio", semitones: key, start_seconds: start, loop_seconds: None, duration_seconds: None }', '"mp3" => TransportArgs { audio_path: song.into(), video_path: None, mode: "audio", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: None }', None),
    ('"mp3" => TransportArgs { audio_path: song.into(), video_path: None, mode: "audio", semitones: key, start_seconds: start, loop_seconds: None, duration_seconds: None }', '"mp3" => TransportArgs { audio_path: song.into(), video_path: None, mode: "audio", semitones: key, start_seconds: start, loop_seconds: None, duration_seconds: duration }', None),
    ('mode: "audio", semitones: key,', 'mode: "mp3", semitones: key,', None),
    ('"mp3" => TransportArgs { audio_path: song.into(), video_path: None,', '"mp3" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()),', None),
    ('    let key = semitones.trunc() as i64;', '    let key = semitones.round() as i64;', None),
    ('    let key = semitones.trunc() as i64;', '    let key = semitones.floor() as i64;', None),
    ('    matches!(x, Some(v) if v != 0.0)', '    x.is_some()', None),
    ('    let mut eff_start = if truthy(start) { start.unwrap_or(0.0) } else { 0.0 };', '    let mut eff_start = start.unwrap_or(0.0);', "a start that is zero or none is replaced by the stash and the stash defaults to zero, so only a negative start (handled by the <= 0 test) differs"),
    ('    if eff_start <= 0.0 {', '    if eff_start < 0.0 {', None),
    ('    if eff_start <= 0.0 {', '    if eff_start > 0.0 {', None),
    ('        eff_start = pend_start.unwrap_or(0.0);', '        eff_start = 0.0;', None),
    ('    let eff_loop = if truthy(loop_s) { loop_s } else { pend_loop };', '    let eff_loop = if loop_s.is_some() { loop_s } else { pend_loop };', None),
    ('    let eff_loop = if truthy(loop_s) { loop_s } else { pend_loop };', '    let eff_loop = loop_s;', None),
    ('    let eff_loop = if truthy(loop_s) { loop_s } else { pend_loop };', '    let eff_loop = if truthy(loop_s) { loop_s } else { None };', None),
    ('    let eff_duration = if truthy(duration) {', '    let eff_duration = if duration.is_some() {', None),
    ('    } else if truthy(pend_duration) {\n        pend_duration.unwrap_or(0.0)', '    } else if truthy(pend_duration) {\n        0.0', None),
    ('let t = transport_args(&kind, &text("song"), &text("cdg"), &text("mp3"), key, num("start"), num("loop"), num("duration")).ok_or("unknown kind")?;', 'let t = transport_args(&kind, &text("song"), &text("mp3"), &text("cdg"), key, num("start"), num("loop"), num("duration")).ok_or("unknown kind")?;', None),
    ('let t = transport_args(&kind, &text("song"), &text("cdg"), &text("mp3"), key, num("start"), num("loop"), num("duration")).ok_or("unknown kind")?;', 'let t = transport_args(&kind, &text("song"), &text("cdg"), &text("mp3"), key, num("start"), num("duration"), num("loop")).ok_or("unknown kind")?;', None),
    ('zip_args(num("start"), num("loop"), num("duration"), num("pend_start"), num("pend_loop"), num("pend_duration"))', 'zip_args(num("start"), num("loop"), num("duration"), num("pend_loop"), num("pend_start"), num("pend_duration"))', None),
    # ---- the confirmed-start payload
    ('        _ => name.map(str::to_string),\n    };', '        _ => None,\n    };', None),
    ('        Some(d) if !d.is_empty() => Some(d.to_string()),', '        Some(d) => Some(d.to_string()),', None),
    ('        Some(d) if !d.is_empty() => Some(d.to_string()),', '        Some(_) => name.map(str::to_string),', None),
    ('        perf_singer_name: if singer_name.is_empty() { display_raw.clone() } else { Some(singer_name.clone()) },', '        perf_singer_name: display_raw.clone(),', None),
    ('        perf_singer_name: if singer_name.is_empty() { display_raw.clone() } else { Some(singer_name.clone()) },', '        perf_singer_name: Some(singer_name.clone()),', None),
    ('        perf_singer_name: if singer_name.is_empty() { display_raw.clone() } else { Some(singer_name.clone()) },', '        perf_singer_name: if singer_name.is_empty() { None } else { Some(singer_name.clone()) },', None),
    ('        singer_display: display_raw.unwrap_or_default(),', '        singer_display: singer_name.clone(),', None),
    ('        eta_hold: duration.max(0),', '        eta_hold: duration,', None),
    ('        eta_hold: duration.max(0),', '        eta_hold: duration.max(1),', None),
    ('        Info::Seq(items) => items.first()?.text().to_string(),', '        Info::Seq(items) => items.last()?.text().to_string(),', None),
    ('        countdown_artist: py_strip(entry_artist).to_string(),', '        countdown_artist: artist.clone(),', None),
    ('        countdown_artist: py_strip(entry_artist).to_string(),', '        countdown_artist: entry_artist.to_string(),', None),
    ('        countdown_title: py_strip(entry_title).to_string(),', '        countdown_title: title.clone(),', None),
    ('        countdown_title: py_strip(entry_title).to_string(),', '        countdown_title: entry_title.to_string(),', None),
    ('            Some(c) if !c.is_empty() => c.to_string(),\n            _ => "internal".to_string(),', '            Some(c) => c.to_string(),\n            _ => "internal".to_string(),', None),
    ('            _ => "internal".to_string(),', '            _ => "manual".to_string(),', None),
    ('    let singer_name = name.unwrap_or("").to_string();', '    let singer_name = name.unwrap_or("x").to_string();', None),
    ('"singer_id": echo("singer_id"), "request_id": echo("request_id"),', '"singer_id": echo("request_id"), "request_id": echo("singer_id"),', None),
    ('"perf_request_id": echo("remote_request_id"), "perf_song_path": c.song_path, "perf_key": echo("key"), "perf_tempo": echo("tempo_percent"),', '"perf_request_id": echo("request_id"), "perf_song_path": c.song_path, "perf_key": echo("key"), "perf_tempo": echo("tempo_percent"),', None),
    ('"perf_key": echo("key"), "perf_tempo": echo("tempo_percent"),', '"perf_key": echo("tempo_percent"), "perf_tempo": echo("key"),', None),
    ('"ctx_singer": c.singer_display, "ctx_artist": c.artist, "ctx_title": c.title,', '"ctx_singer": c.singer_name, "ctx_artist": c.artist, "ctx_title": c.title,', None),
    ('"ctx_artist": c.artist, "ctx_title": c.title,', '"ctx_artist": c.title, "ctx_title": c.artist,', None),
    ('"countdown_singer": c.singer_display,', '"countdown_singer": c.singer_name,', None),
    ('"perf_song_path": c.song_path,', '"perf_song_path": c.title,', None),
    ('"song_path": c.song_path, "artist": c.artist, "title": c.title,\n            "singer_id"', '"song_path": c.song_path, "artist": c.title, "title": c.artist,\n            "singer_id"', None),
    # ---- when a song counts as sung
    ('        ("confirmed", false) => PerfStep { action: "set", pending: true, ..none(true) },', '        ("confirmed", false) => PerfStep { action: "set", pending: false, ..none(false) },', None),
    ('        ("confirmed", true) => PerfStep { action: "overwritten", pending: true, ..none(true) },', '        ("confirmed", true) => PerfStep { action: "set", pending: true, ..none(true) },', None),
    ('("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: has_request_id, record_history: true,', '("media_end", true) => PerfStep { action: "commit", pending: true, complete_remote: has_request_id, record_history: true,', None),
    ('("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: has_request_id, record_history: true,', '("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: true, record_history: true,', None),
    ('("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: has_request_id, record_history: true,', '("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: has_request_id, record_history: has_request_id,', None),
    ('("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: has_request_id, record_history: true, reason: "song_completed",', '("media_end", true) => PerfStep { action: "commit", pending: false, complete_remote: has_request_id, record_history: true, reason: "stop_playback",', None),
    ('("stop", true) => PerfStep { action: "discard", pending: false, tombstone: has_request_id,', '("stop", true) => PerfStep { action: "discard", pending: false, tombstone: true,', None),
    ('("stop", true) => PerfStep { action: "discard", pending: false, tombstone: has_request_id,', '("stop", true) => PerfStep { action: "discard", pending: false, tombstone: false,', None),
    ('("stop", true) => PerfStep { action: "discard", pending: false, tombstone: has_request_id,', '("stop", true) => PerfStep { action: "discard", pending: true, tombstone: has_request_id,', None),
    ('("stop", true) => PerfStep { action: "discard", pending: false, tombstone: has_request_id, reason: "stop_playback",', '("stop", true) => PerfStep { action: "discard", pending: false, tombstone: has_request_id, record_history: true, reason: "stop_playback",', None),
    ('tombstone_reason: if has_request_id { "host_stopped:stop_playback" } else { "" }', 'tombstone_reason: "host_stopped:stop_playback"', None),
    ('tombstone_reason: if has_request_id { "host_stopped:stop_playback" } else { "" }', 'tombstone_reason: if has_request_id { "stop_playback" } else { "" }', None),
    ('        ("media_end" | "stop", false) => none(false),', '        ("media_end" | "stop", false) => PerfStep { action: "discard", ..none(false) },', None),
    ('        ("media_end" | "stop", false) => none(false),', '        ("media_end", false) => none(false),', None),
    ('        let p = performance_step(flag("pending"), event, flag("has_request_id")).ok_or("unknown event")?;', '        let p = performance_step(flag("has_request_id"), event, flag("pending")).ok_or("unknown event")?;', None),
    # ---- the whole start plan
    ('    let Some(entry) = plan.pick.singer.and_then(|i| if plan.pick.song.is_some() { singers[i].entry.as_ref() } else { None }) else { return plan };', '    let Some(entry) = plan.pick.singer.and_then(|i| singers[i].entry.as_ref()) else { return plan };', "the pick loop only picks a singer who has a playable song, so a picked singer always has one"),
    ('    let r = route(&entry.route);\n    plan.route = Some(r.name());', '    let r = route(&entry.route);\n    plan.route = None;', None),
    ('        Route::Mp4 => "mp4",\n        Route::Mp3 => "mp3",\n        Route::CdgMp3Dual | Route::Mp3gZip => "cdg",', '        Route::Mp4 => "mp3",\n        Route::Mp3 => "mp3",\n        Route::CdgMp3Dual | Route::Mp3gZip => "cdg",', None),
    ('        Route::Mp4 => "mp4",\n        Route::Mp3 => "mp3",\n        Route::CdgMp3Dual | Route::Mp3gZip => "cdg",', '        Route::Mp4 => "mp4",\n        Route::Mp3 => "mp3",\n        Route::CdgMp3Dual => "cdg",', None),
    ('        Route::CdgMp3Dual | Route::Mp3gZip => "cdg",\n        _ => return plan,', '        Route::CdgMp3Dual | Route::Mp3gZip => "cdg",\n        _ => {}', None),
    ('    plan.engine = engine.map(engine_route);', '    plan.engine = None;', None),
    ('    plan.transport = transport_args(kind, &entry.song, &entry.cdg, &entry.mp3, entry.semitones, entry.start, entry.loop_s, entry.duration);', '    plan.transport = transport_args(kind, &entry.song, &entry.cdg, &entry.mp3, entry.semitones, entry.start, None, entry.duration);', None),
    ('    plan.transport = transport_args(kind, &entry.song, &entry.cdg, &entry.mp3, entry.semitones, entry.start, entry.loop_s, entry.duration);', '    plan.transport = transport_args(kind, &entry.song, &entry.cdg, &entry.mp3, 0.0, entry.start, entry.loop_s, entry.duration);', None),
    ('    plan.display = display_payload(&entry.artist, &entry.title, entry.fallback.as_deref());', '    plan.display = display_payload(&entry.artist, &entry.title, None);', None),
    ('    let pick = pick_loop(&picks, rotation_mode, setting_locked);\n    let mut plan', '    let pick = pick_loop(&picks, false, setting_locked);\n    let mut plan', None),
    ('        let p = start_plan(&singers, flag("rotation_mode"), flag("setting_locked"), engine.as_ref());', '        let p = start_plan(&singers, flag("setting_locked"), flag("rotation_mode"), engine.as_ref());', "pick_loop acts on the two flags only when both are true, so swapping them changes nothing"),
    ('"display": p.display.as_ref().map(|(a, b)| serde_json::json!({"artist": a, "title": b})),', '"display": p.display.as_ref().map(|(a, b)| serde_json::json!({"artist": b, "title": a})),', None),
    ('"singer": p.pick.singer, "song": p.pick.song, "queue": p.pick.queue, "lock_cleared": p.pick.lock_cleared,\n            "route"', '"singer": p.pick.singer, "song": p.pick.song, "queue": p.pick.queue, "lock_cleared": false,\n            "route"', None),
]

SHADOW = ROOT / "start_shadow.py"
APP = ROOT / "0.2.18.1.py"
_ARM = "_plan_shadow.plan_arm(_plan_pick_facts, queue_snapshot, self.queue, singer, first_non_skipped_idx, _pick_mode, _pick_setting_locked,"
_ARGS = "song_info, primary_path, key, phrase_start,\n                                      intro_loop_seconds, current_song_dur, entry, (cached_cdg"
_PLAN_FIN = '            if self.plan_off or not self.available:\n                return None\n            route, rf'
_SLOW = "            if time.perf_counter() - started > PLAN_SLOW_SECONDS:"
_ROUTE_KEYS = '            for k in ("primary_exists", "zip_cached", "mp3g", "pair"):'
_ENTRY_END = '"semitones": semitones, "start": start, "loop": loop, "duration": duration}'
_WANT = '"singer": plan["singer"], "song": plan["song"], "queue": plan["queue"], "lock_cleared": plan["lock_cleared"], "route": route}'
_UNPL = 'UNPLANNED_ROUTES = ("external_karafun", "reject_missing_file", "zip_prepare")'
_STARTR = 'START_ROUTES = ("mp4", "mp3", "cdg_mp3_dual", "mp3g_zip")'
_FACTS_CHECK = "if facts is None or len(facts) != len(queue_snapshot):"
_IDX_CHECK = "if id(singer) not in index or any(id(x) not in index for x in queue_after):"
_OFF_ARM = "            if self.plan_off or not self.available:\n                return\n            if len(queue_snapshot)"
_STARTED = '            if plan is not None and plan["route"] in START_ROUTES:\n                self.plan_finish(plan, artist, title, fallback)'
_ROUTE_REJECT = "            if route not in START_ROUTES:\n                self._plan = None\n                self.plan_finish(plan, None, None, None)"
_TRANSPORT_SET = '                self._plan["transport"] = {k: observed.get(k) for k in TRANSPORT_KEYS}'
_GOT_NONE = '            if got is None:\n                return None\n            want = {"singer": plan'
_TRANSPORT_WANT = '            if route in START_ROUTES:\n                want["transport"]'
_MISMATCH = '            agree = have == want\n            self.checks += 1\n            if not agree:\n                self.mismatches += 1\n                self._say(f"MISMATCH plan'
_RETURN = '            return agree\n        except Exception as exc:\n            self._say(f"plan error'
_DECISION = '            else:\n                self._say("decision route=plan agree=1")\n            if self.checks % SUMMARY_EVERY == 0:'
_ROUTE_SET = '            plan["route"], plan["route_facts"] = route, dict(facts or {})'
_TRANSPORT_KW = '"semitones": semitones, "start_seconds": start_seconds,'
PM = [
    # ---- start_shadow.py: arming
    (SHADOW, _OFF_ARM, _OFF_ARM.replace("self.plan_off or not self.available", "not self.available"), None),
    (SHADOW, _OFF_ARM, _OFF_ARM.replace("self.plan_off or not self.available", "self.plan_off"), None),
    (SHADOW, "len(queue_snapshot) > PLAN_MAX_SINGERS", "len(queue_snapshot) >= PLAN_MAX_SINGERS", None),
    (SHADOW, _FACTS_CHECK, "if facts is None:", None),
    (SHADOW, _FACTS_CHECK, "if len(facts) != len(queue_snapshot):", None),
    (SHADOW, _IDX_CHECK, "if id(singer) not in index:", None),
    (SHADOW, _IDX_CHECK, "if any(id(x) not in index for x in queue_after):", None),
    (SHADOW, 'cdg, mp3 = song_info, song_info[:-4] + ".mp3"', 'cdg, mp3 = song_info, song_info[:-3] + ".mp3"', None),
    (SHADOW, 'cdg, mp3 = song_info, song_info[:-4] + ".mp3"', "cdg, mp3 = song_info, song_info", None),
    (SHADOW, 'elif isinstance(song_info, str) and song_info.endswith(".cdg"):', "elif isinstance(song_info, str):", None),
    (SHADOW, 'elif isinstance(song_info, str) and song_info.endswith(".zip") and zip_paths:', 'elif isinstance(song_info, str) and song_info.endswith(".cdg") and zip_paths:', None),
    (SHADOW, "cdg, mp3 = zip_paths", "cdg, mp3 = zip_paths[1], zip_paths[0]", None),
    (SHADOW, "if isinstance(song_info, (tuple, list)) and len(song_info) == 2 and all(isinstance(x, str) for x in song_info):", "if isinstance(song_info, (tuple, list)) and len(song_info) == 2:",
     "a two-item tuple with a non-text item reaches the later isinstance guard, which blanks cdg and mp3 the same way"),
    (SHADOW, 'if not (isinstance(cdg, str) and isinstance(mp3, str)):\n                cdg = mp3 = ""', 'if False:\n                cdg = mp3 = ""', "second line of defence behind the all(isinstance) test above it"),
    (SHADOW, 'primary = primary_path if isinstance(primary_path, str) else ""', "primary = primary_path", "a non-text primary path is not a start the plan models; no queue the app can build produces one for a route that starts"),
    (SHADOW, '"artist": str(entry.get("artist") or "").strip() if isinstance(entry, dict) else "",', '"artist": str(entry.get("artist") or "") if isinstance(entry, dict) else "",', None),
    (SHADOW, '"title": str(entry.get("title") or "").strip() if isinstance(entry, dict) else "", "route"', '"title": str(entry.get("title") or "") if isinstance(entry, dict) else "", "route"', None),
    # ---- start_shadow.py: observing
    (SHADOW, "            if route in UNPLANNED_ROUTES:", "            if False:", None),
    (SHADOW, _UNPL, _UNPL.replace('"external_karafun", ', ""), None),
    (SHADOW, _UNPL, _UNPL.replace('"reject_missing_file", ', ""), None),
    (SHADOW, _UNPL, _UNPL.replace(', "zip_prepare"', ""), None),
    (SHADOW, _STARTR, _STARTR.replace(', "mp3g_zip"', ""), None),
    (SHADOW, _STARTR, _STARTR.replace('"mp3", ', ""), None),
    (SHADOW, _ROUTE_SET, _ROUTE_SET.replace("dict(facts or {})", "{}"), None),
    (SHADOW, _ROUTE_SET, _ROUTE_SET + "\n            self._plan = None", None),
    (SHADOW, _ROUTE_REJECT, _ROUTE_REJECT.replace("route not in START_ROUTES", "False"), None),
    (SHADOW, _ROUTE_REJECT, _ROUTE_REJECT.replace("                self._plan = None\n", ""), "plan_started is not reached after a rejecting route, and the next plan_arm replaces a plan that was judged"),
    (SHADOW, _TRANSPORT_SET, _TRANSPORT_SET.replace('{k: observed.get(k) for k in TRANSPORT_KEYS}', "dict(observed)"), "the app passes exactly TRANSPORT_KEYS, so the filter changes nothing"),
    (SHADOW, _TRANSPORT_SET, "                pass", None),
    (SHADOW, _STARTED, _STARTED.replace(' and plan["route"] in START_ROUTES', ""), "a plan whose route is not a start route was judged and cleared in plan_route"),
    (SHADOW, _STARTED, _STARTED.replace("artist, title, fallback", "artist, artist, fallback"), None),
    (SHADOW, "            plan, self._plan = self._plan, None\n", "            plan = self._plan\n", "the next plan_arm replaces a plan that was judged"),
    # ---- start_shadow.py: the one call and its verdict
    (SHADOW, _PLAN_FIN, _PLAN_FIN.replace("self.plan_off or not self.available", "not self.available"), "plan_arm refuses to arm while plan_off, and plan_finish is only reached with an armed plan"),
    (SHADOW, _PLAN_FIN, _PLAN_FIN.replace("self.plan_off or not self.available", "self.plan_off"), "plan_arm refuses to arm when the library is not available"),
    (SHADOW, 'req_route = {"external": False,', 'req_route = {"external": True,', None),
    (SHADOW, '"has_primary": bool(rf.get("has_primary", bool(plan["primary"])))}', '"has_primary": True}', None),
    (SHADOW, _ROUTE_KEYS, _ROUTE_KEYS.replace('"primary_exists", ', ""), None),
    (SHADOW, _ROUTE_KEYS, _ROUTE_KEYS.replace('"zip_cached", ', ""), None),
    (SHADOW, _ROUTE_KEYS, _ROUTE_KEYS.replace('"mp3g", ', ""), None),
    (SHADOW, _ROUTE_KEYS, _ROUTE_KEYS.replace(', "pair"', ""), None),
    (SHADOW, "                    req_route[k] = bool(rf[k])", "                    req_route[k] = True", None),
    (SHADOW, _ENTRY_END, _ENTRY_END.replace('"semitones": semitones', '"semitones": 0'), None),
    (SHADOW, _ENTRY_END, _ENTRY_END.replace('"start": start', '"start": 0.0'), None),
    (SHADOW, _ENTRY_END, _ENTRY_END.replace('"loop": loop', '"loop": None'), "the app never starts a song with an intro loop (the retired phrase and intro-loop path), so loop is always None"),
    (SHADOW, _ENTRY_END, _ENTRY_END.replace('"duration": duration', '"duration": None'), None),
    (SHADOW, '            if fallback is not None:\n                entry["fallback"]', '            if False:\n                entry["fallback"]', None),
    (SHADOW, 'singers[plan["singer"]]["entry"] = entry', 'singers[0]["entry"] = entry', None),
    (SHADOW, '"rotation_mode": plan["rotation_mode"], "setting_locked"', '"rotation_mode": False, "setting_locked"', None),
    (SHADOW, '"setting_locked": plan["setting_locked"]}', '"setting_locked": False}', None),
    (SHADOW, _SLOW, _SLOW.replace(">", ">="), "a duration exactly equal to the limit cannot come from a real clock; the injected clock steps whole seconds"),
    (SHADOW, _SLOW, _SLOW.replace(">", "<"), None),
    (SHADOW, "                self.plan_off = True\n", "                self.plan_off = False\n", None),
    (SHADOW, "PLAN_SLOW_SECONDS = 0.025", "PLAN_SLOW_SECONDS = 1e9", None),
    (SHADOW, _GOT_NONE, '            want = {"singer": plan', None),
    (SHADOW, _WANT, _WANT.replace('"route": route}', '"route": plan["route"] or route}'), "plan[\"route\"] is route whenever this runs"),
    (SHADOW, _WANT, _WANT.replace('"song": plan["song"], ', ""), None),
    (SHADOW, _WANT, _WANT.replace('"queue": plan["queue"], ', ""), None),
    (SHADOW, _WANT, _WANT.replace('"lock_cleared": plan["lock_cleared"], ', ""), None),
    (SHADOW, _WANT, _WANT.replace('"singer": plan["singer"], ', ""), None),
    (SHADOW, _WANT, _WANT.replace('"route": route}', '"route": "mp4"}'), None),
    (SHADOW, _TRANSPORT_WANT, _TRANSPORT_WANT.replace("route in START_ROUTES", "True"), None),
    (SHADOW, _TRANSPORT_WANT, _TRANSPORT_WANT.replace("route in START_ROUTES", "False"), None),
    (SHADOW, '                want["display"] = {"artist": str(artist), "title": str(title)}', '                want["display"] = {"artist": str(title), "title": str(artist)}', None),
    (SHADOW, '                have["transport"], have["display"] = got.get("transport"), got.get("display")', '                have["transport"], have["display"] = got.get("display"), got.get("transport")', None),
    (SHADOW, '                have["transport"] = have["display"] = None\n', '                have["transport"] = have["display"] = 0\n', None),
    (SHADOW, _MISMATCH, _MISMATCH.replace("have == want", "True"), None),
    (SHADOW, '                self.mismatches += 1\n                self._say(f"MISMATCH plan', '                self._say(f"MISMATCH plan', None),
    (SHADOW, '                self._say("decision route=plan agree=1")', '                self._say("decision route=plan agree=0")', None),
    (SHADOW, _DECISION, '            elif "plan" not in self._seen:\n                self._seen.add("plan")\n                self._say("decision route=plan agree=1")\n            if self.checks % SUMMARY_EVERY == 0:', None),
    (SHADOW, '            self.checks += 1\n            if not agree:\n                self.mismatches += 1\n                self._say(f"MISMATCH plan', '            if not agree:\n                self.mismatches += 1\n                self._say(f"MISMATCH plan', None),
    (SHADOW, _RETURN, _RETURN.replace("return agree", "return None"), "the app does not read the return value; the tests read the log lines"),
    # ---- 0.2.18.1.py: the hooks
    (APP, 'def _start_shadow_for(settings, flag="start_shadow"):', 'def _start_shadow_for(settings, flag="plan_shadow_live"):', None),
    (APP, '            _plan = _start_shadow_for(self.settings, "plan_shadow_live")', "            _plan = _start_shadow_for(self.settings)", None),
    (APP, "                _plan.plan_route(route, song_info, facts)", "                _plan.plan_route(route, song_info, {})", None),
    (APP, '            _psh = _start_shadow_for(self.settings, "plan_shadow_live")', "            _psh = _start_shadow_for(self.settings)", None),
    (APP, "                _psh.plan_transport({" + '"audio_path": audio_path, "video_path": video_path, "mode": mode, ' + _TRANSPORT_KW,
     "                _psh.plan_transport({" + '"audio_path": audio_path, "video_path": video_path, "mode": mode, ' + _TRANSPORT_KW.replace("semitones: ", "semitones: ").replace('"semitones": semitones', '"semitones": 0'), None),
    (APP, '"loop_seconds": loop_seconds, "duration_seconds": duration_seconds})\n        except Exception:\n            pass\n        self._karaoke_played_this_session',
     '"loop_seconds": loop_seconds, "duration_seconds": None})\n        except Exception:\n            pass\n        self._karaoke_played_this_session', None),
    (APP, '            _plan_probe = _start_shadow_for(self.settings, "plan_shadow_live")', "            _plan_probe = _start_shadow_for(self.settings)", None),
    (APP, "            if _plan_probe is not None and not _plan_probe.plan_off:", "            if _plan_probe is not None:", "plan_arm refuses to arm once the shadow is off, so facts computed for it are never used"),
    (APP, "                _plan_pick_facts = _plan_probe.pick_facts(queue_snapshot)\n                _pick_mode = bool(self._is_rotation_mode())", "                _plan_pick_facts = _plan_probe.pick_facts(queue_snapshot)\n                _pick_mode = False", None),
    (APP, '                _pick_setting_locked = bool(self.settings.get("rotation_locked", False))\n        except Exception:\n            _plan_pick_facts = None',
     "                _pick_setting_locked = False\n        except Exception:\n            _plan_pick_facts = None", None),
    (APP, '            _plan_shadow = _start_shadow_for(self.settings, "plan_shadow_live")', "            _plan_shadow = _start_shadow_for(self.settings)", None),
    (APP, _ARM, _ARM.replace("queue_snapshot, self.queue,", "queue_snapshot, queue_snapshot,"), None),
    (APP, _ARM, _ARM.replace("first_non_skipped_idx,", "0,"), None),
    (APP, _ARM, _ARM.replace("_pick_mode, _pick_setting_locked,", "False, _pick_setting_locked,"), None),
    (APP, _ARM, _ARM.replace("_pick_mode, _pick_setting_locked,", "_pick_mode, False,"), None),
    (APP, '_pick_setting_locked and not bool(self.settings.get("rotation_locked", False)), song_info, primary_path', "False, song_info, primary_path", None),
    (APP, _ARGS, _ARGS.replace("song_info, primary_path,", "song_info, song_info,"), "primary_path is song_info for every text song_info and the plan only models text songs"),
    (APP, _ARGS, _ARGS.replace("primary_path, key,", "primary_path, 0,"), None),
    (APP, _ARGS, _ARGS.replace("key, phrase_start,", "key, 0.0,"), None),
    (APP, _ARGS, _ARGS.replace("current_song_dur, entry,", "0, entry,"), None),
    (APP, _ARGS, _ARGS.replace("current_song_dur, entry,", "current_song_dur, {},"), None),
    (APP, _ARGS, _ARGS.replace("intro_loop_seconds,", "0,"), None),
    (APP, "entry, (cached_cdg, cached_mp3) if _zip_cached else None)", "entry, None)", None),
    (APP, "entry, (cached_cdg, cached_mp3) if _zip_cached else None)", "entry, (cached_mp3, cached_cdg) if _zip_cached else None)", None),
    (APP, '            _plan_done = _start_shadow_for(self.settings, "plan_shadow_live")', "            _plan_done = _start_shadow_for(self.settings)", None),
    (APP, "_plan_done.plan_started(artist, title, _display_fallback)", "_plan_done.plan_started(artist, title, None)", None),
    (APP, "_plan_done.plan_started(artist, title, _display_fallback)", "_plan_done.plan_started(title, artist, _display_fallback)", None),
    (APP, '"transport_drive_media_end", "transport_drive_watchdog", "start_shadow", "plan_shadow_live")', '"transport_drive_media_end", "transport_drive_watchdog", "start_shadow")', None),
    (APP, '    "plan_shadow_live": True,', '    "plan_shadow_live": False,', None),
]


def cargo_test():
    return subprocess.run(["cargo", "test", "-p", "singws-start", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True).returncode


def differential():
    b = subprocess.run(["cargo", "build", "--release", "-p", "singws-dsp-ffi", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True)
    if b.returncode != 0:
        return 1  # does not compile: counts as caught
    return subprocess.run([PY, "-m", "unittest", "test_start_shadow", "test_start_shadow_wiring", "test_start_shadow_transport", "test_start_shadow_confirmed", "test_start_shadow_performance", "test_rotation_lock_reweave", "test_start_shadow_plan"], cwd=ROOT, env=ENV, capture_output=True, text=True).returncode


def glue_tests():
    return subprocess.run([PY, "-m", "unittest", "test_start_shadow_plan_live", "test_start_shadow_plan", "test_start_shadow_wiring"], cwd=ROOT, env=ENV, capture_output=True, text=True).returncode


def run_glue(first, survivors):
    """The glue mutants: the mutated file is restored after every mutant."""
    killed = 0
    for k, (path, old, new, note) in enumerate(PM):
        n = len(M) + k
        if n < first or (IDS and n not in IDS):
            continue
        original = path.read_text()
        if original.count(old) != 1:
            print(f"[{n:03d}] PATTERN NOT UNIQUE/FOUND ({original.count(old)}): {old[:70]!r}")
            survivors.append(n)
            continue
        try:
            path.write_text(original.replace(old, new))
            code = glue_tests()
        finally:
            path.write_text(original)
        if code != 0:
            killed += 1
            print(f"[{n:03d}] killed by the glue tests")
        elif note:
            killed += 1
            print(f"[{n:03d}] EQUIVALENT ({note})")
        else:
            print(f"[{n:03d}] SURVIVED {path.name}: {old[:70]!r}")
            survivors.append(n)
    return killed


def main():
    original = SRC.read_text()
    survivors, killed = [], 0
    try:
        for n, (old, new, note) in enumerate(M):
            if n < FIRST or os.environ.get("MUTATE_ONLY") == "py":
                continue
            if original.count(old) != 1:
                print(f"[{n:02d}] PATTERN NOT UNIQUE/FOUND ({original.count(old)}): {old[:60]!r}")
                survivors.append(n)
                continue
            SRC.write_text(original.replace(old, new))
            if cargo_test() != 0:
                killed += 1
                print(f"[{n:02d}] killed by the crate tests")
                continue
            if differential() != 0:
                killed += 1
                print(f"[{n:02d}] killed by the differential test")
                continue
            if note:
                print(f"[{n:02d}] EQUIVALENT ({note})")
                killed += 1
            else:
                print(f"[{n:02d}] SURVIVED {old[:70]!r}")
                survivors.append(n)
    finally:
        SRC.write_text(original)
        subprocess.run(["cargo", "build", "--release", "-p", "singws-dsp-ffi", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True)
    if os.environ.get("MUTATE_ONLY") != "rust":
        killed += run_glue(FIRST, survivors)
    print(f"killed {killed} of {len(M) + len(PM)}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
