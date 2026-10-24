#!/usr/bin/env python3
"""Mutation test for singws-uimodel's rotation module: each mutant must be killed by the Rust tests or by tools/rotation_parity.py.
Equivalent mutants carry a reason. Exit 1 when a non-equivalent mutant survives."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-uimodel/src/rotation.rs"
ENV = dict(os.environ, CARGO_TARGET_DIR=os.path.expanduser("~/.cache/singws-rust-target"))
PY = os.path.expanduser("~/.venvs/singws/.venv-build-arm64/bin/python")
M = [
    ("map.entry(\"skipped\").or_insert(Value::Bool(false));\n        let songs", "let songs", None),
    ("m.entry(\"skipped\").or_insert(Value::Bool(false));\n                    new_songs.push(Value::Object(m));\n                }\n                Value::Array(a)", "new_songs.push(Value::Object(m));\n                }\n                Value::Array(a)", None),
    ("m.insert(\"key\".into(), a.get(1).cloned().unwrap_or(Value::from(0)));", "m.insert(\"key\".into(), Value::from(0));", None),
    ("if a.len() >= 3 {", "if a.len() >= 2 {", None),
    ("if seen.contains(&key) {", "if false {", None),
    ("if key.is_empty() {\n            continue;\n        }\n        if seen", "if seen", None),
    ("if !artist.is_empty() && !title.is_empty() { format!(\"{artist} • {title}\") } else if !artist.is_empty() { artist.to_string() }", "if !artist.is_empty() && !title.is_empty() { format!(\"{artist} • {title}\") } else if !artist.is_empty() { title.to_string() }", None),
    ("if !artist.is_empty() && !title.is_empty() {\n        return Ok(format!(\"{artist} • {title}\"));", "if !artist.is_empty() || !title.is_empty() {\n        return Ok(format!(\"{artist} • {title}\"));", None),
    ("if !title.is_empty() {\n        return Ok(title);", "if title.is_empty() {\n        return Ok(title);", None),
    ("py_lower(&display)? != \"unknown\"", "py_lower(&display)? != \"unknownx\"", None),
    ("if parts.last().is_some_and(|p| py_lower(p).map(|l| l == \"karafun\").unwrap_or(false)) {", "if false {", None),
    ("if parts.len() >= 2 {\n            return Ok(format!(\"{} • {}\", parts[0], parts[1]));", "if parts.len() >= 3 {\n            return Ok(format!(\"{} • {}\", parts[0], parts[1]));", None),
    ("if let Some(Some(disp)) = row {", "if let Some(Some(_disp)) = row {\n                let disp = String::from(\"\");", None),
    ("if py_lower(&tp)? == lowered {", "if tp == path {", None),
    ("None => Ok(basename(path).to_string()).map(|b| display_from_track_text(&b, true)),", "None => Ok(path.to_string()),", None),
    ("let artist = parts.first().copied().unwrap_or(\"\");\n    let title = parts.get(1).copied().unwrap_or(\"\");", "let artist = parts.first().copied().unwrap_or(\"\");\n    let title = parts.get(2).copied().unwrap_or(\"\");", None),
    ("if truthy(get(singer, \"skipped\")) {\n            continue;", "if false {\n            continue;", None),
    ("if !active_id.is_empty() && py_str_or_empty(get(singer, \"singer_id\"))? == active_id {", "if false {", None),
    ("let active_id = if inp.playing { inp.active_singer_id } else { \"\" };", "let active_id = inp.active_singer_id;", None),
    ("songs.iter().find(|s| !truthy(get(s, \"skipped\")))", "songs.iter().find(|_s| true)", None),
    ("let singer_display_value = if truthy(duet_display) { duet_display } else { get(singer, \"name\") };", "let singer_display_value = get(singer, \"name\");", None),
    ("name = format!(\"{name}  •  Rotation start\");", "name = format!(\"{name}  Rotation start\");", None),
    ("let text = format!(\"{num}. {singer_display}  •  {name}\");", "let text = format!(\"{num} {singer_display}  •  {name}\");", None),
    ("let count_label = format!(\"{count} SINGER{}\", if count != 1 { \"S\" } else { \"\" });", "let count_label = format!(\"{count} SINGER{}\", if count > 1 { \"S\" } else { \"\" });", None),
    ("if !cand.is_empty() && casefold(cand)? != now_key {", "if !cand.is_empty() {", None),
    ("if lineup.len() >= 2 {", "if lineup.len() >= 3 {", "only the first two lineup entries are ever read, so collecting a third changes nothing observable"),
    ("if casefold(py_strip(&r.singer))? == now_key {\n            continue;", "if false {\n            continue;", None),
    ("now_strip = if r.song.is_empty() { r.singer.clone() } else { format!(\"{} - {}\", r.singer, r.song) };", "now_strip = r.singer.clone();", None),
    ("} else if !inp.now_singing.is_empty() && inp.now_singing != \"Singer Rotation\" {", "} else if !inp.now_singing.is_empty() {", None),
    ("let next_detail = first.1.split('\\n').next().unwrap_or(\"\");", "let next_detail = first.1.as_str();", "the detail is only ever one line here (the artist line is never produced), so splitting on a newline cannot change it"),
    ("if !live_title.is_empty() && inp.playing {", "if !live_title.is_empty() {", None),
    ("format!(\"{} — {}\", inp.now_singing, live_title)", "format!(\"{} - {}\", inp.now_singing, live_title)", None),
    ("if rotation_start_name.is_empty() { \"SINGER ROTATION\".to_string() }", "if rotation_start_name.is_empty() { \"SINGER ROTATION \".to_string() }", None),
    ("return decline(\"an empty song_info list (Python would raise)\"),", "return Ok(View::default()),", None),
]


def run(cmd, timeout=900):
    return subprocess.run(cmd, cwd=ROOT / "rust", env=ENV, capture_output=True, text=True, timeout=timeout)


def main():
    text = SRC.read_text()
    survivors, equivalents = [], []
    try:
        for n, (old, new, reason) in enumerate(M):
            if old not in text:
                print(f"[{n:02d}] PATTERN NOT FOUND {old[:60]!r}")
                survivors.append(n)
                continue
            SRC.write_text(text.replace(old, new, 1))
            try:
                r = run(["cargo", "test", "-p", "singws-uimodel", "--lib", "--quiet"], timeout=300)
            except subprocess.TimeoutExpired:
                print(f"[{n:02d}] killed by unit tests (hang)")
                continue
            if r.returncode != 0:
                print(f"[{n:02d}] killed by {'build' if 'could not compile' in r.stderr else 'unit tests'}  {old[:50]!r}")
                continue
            run(["cargo", "build", "--release", "-p", "singws-uimodel"])
            p = subprocess.run([PY, str(ROOT / "tools/rotation_parity.py"), "--cases", "40"], capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                print(f"[{n:02d}] killed by parity  {old[:50]!r}")
            elif reason:
                equivalents.append(n)
                print(f"[{n:02d}] EQUIVALENT ({reason[:60]})")
            else:
                survivors.append(n)
                print(f"[{n:02d}] SURVIVED  {old[:70]!r}")
    finally:
        SRC.write_text(text)
        run(["cargo", "build", "--release", "-p", "singws-uimodel"])
    print(f"equivalent: {len(equivalents)}  survivors: {survivors}")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
