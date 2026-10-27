#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-start/src/lib.rs: every mutant must be caught by the crate's own tests OR by the Python differential test
(test_start_shadow and test_start_shadow_wiring, which needs the release dylib rebuilt for each mutant). Equivalent mutants are documented in the list."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-start/src/lib.rs"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target"), QT_QPA_PLATFORM="offscreen", SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS="1")
PY = str(ROOT / ".venv/bin/python")
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
]


def cargo_test():
    return subprocess.run(["cargo", "test", "-p", "singws-start", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True).returncode


def differential():
    b = subprocess.run(["cargo", "build", "--release", "-p", "singws-dsp-ffi", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True)
    if b.returncode != 0:
        return 1  # does not compile: counts as caught
    return subprocess.run([PY, "-m", "unittest", "test_start_shadow", "test_start_shadow_wiring", "test_start_shadow_transport", "test_start_shadow_confirmed", "test_rotation_lock_reweave"], cwd=ROOT, env=ENV, capture_output=True, text=True).returncode


def main():
    original = SRC.read_text()
    survivors, killed = [], 0
    try:
        for n, (old, new, note) in enumerate(M):
            if n < FIRST:
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
    print(f"killed {killed} of {len(M)}; survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
