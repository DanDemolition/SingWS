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
    ('        if !s.skipped {\n            if let Some(j) = first_playable(s) {', '        if true {\n            if let Some(j) = first_playable(s) {', None),
    ('    let first_playable = |s: &PickSinger| s.songs.chars().position(|c| c != \'s\');', '    let first_playable = |s: &PickSinger| s.songs.chars().position(|c| c == \'d\');', None),
    ('    if last.skipped { Some((singers.len() - 1, first_playable(last))) } else { None }', '    if !last.skipped { Some((singers.len() - 1, first_playable(last))) } else { None }', None),
    ('    if last.skipped { Some((singers.len() - 1, first_playable(last))) } else { None }', '    None', None),
    ('Some((singers.len() - 1, first_playable(last)))', 'Some((0, first_playable(last)))', None),
    ('Some((singers.len() - 1, first_playable(last)))', 'Some((singers.len() - 1, None))', None),
    ('"song": p.and_then(|x| x.1)}', '"song": p.map(|_| 0)}', None),
    ("!matches!(c, 'd' | 's' | 't')", "!matches!(c, 'd' | 's')", None),
    ('    let last = singers.last()?;', '    let last = singers.first()?;', None),
]


def cargo_test():
    return subprocess.run(["cargo", "test", "-p", "singws-start", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True).returncode


def differential():
    b = subprocess.run(["cargo", "build", "--release", "-p", "singws-dsp-ffi", "--quiet"], cwd=ROOT / "rust", env=ENV, capture_output=True, text=True)
    if b.returncode != 0:
        return 1  # does not compile: counts as caught
    return subprocess.run([PY, "-m", "unittest", "test_start_shadow", "test_start_shadow_wiring"], cwd=ROOT, env=ENV, capture_output=True, text=True).returncode


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
