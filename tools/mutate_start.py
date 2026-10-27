#!/usr/bin/env python3
"""Mutation test for rust/crates/singws-start/src/lib.rs: every mutant must be caught by the crate's own tests OR by the Python differential test
(test_start_shadow and test_start_shadow_wiring, which needs the release dylib rebuilt for each mutant). Equivalent mutants are documented in the list."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-start/src/lib.rs"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.path.expanduser("~/.cache/singws-rust-target"), QT_QPA_PLATFORM="offscreen")
PY = str(ROOT / ".venv/bin/python")

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
]


def cargo_test():
    return subprocess.run(["cargo", "test", "-p", "singws-start", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True).returncode


def differential():
    b = subprocess.run(["cargo", "build", "--release", "-p", "singws-dsp-ffi", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True)
    if b.returncode != 0:
        return 1  # does not compile: counts as caught
    return subprocess.run([PY, "-m", "unittest", "test_start_shadow", "test_start_shadow_wiring", "test_rotation_lock_reweave"], cwd=ROOT, env=ENV, capture_output=True, text=True).returncode


def main():
    original = SRC.read_text()
    survivors, killed = [], 0
    try:
        for n, (old, new, note) in enumerate(M):
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
