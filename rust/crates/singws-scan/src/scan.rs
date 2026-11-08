//! `_build_library_scan_result` (0.2.18.1.py) in FULL mode (and the preserve-outside-roots variant of it), plus the mass-deletion guard,
//! the physical-file dedupe and the result bookkeeping. Quick mode is declined: it depends on the previous scan's directory signatures,
//! the age of tracks.json and per-file reuse rules that this port does not reproduce.
//!
//! Input (JSON): { roots, quick_mode, old_tracks, settings: {filename_format, karaoke_library_locations, karaoke_scan_roots,
//! karaoke_library_locations_migrated, karaoke_folder.., karaoke_scan_dir_sigs, _scan_preserve_outside_roots}, cwd? }.
//! Output (JSON): the same dictionary the Python function returns (scan_time excepted: it is only this call's own wall time).

use crate::locations::*;
use crate::names::*;
use crate::pyfs::*;
use serde_json::{Map, Value, json};
use std::collections::{HashMap, HashSet};
use std::os::unix::fs::MetadataExt;

pub(crate) fn utf8(path: &std::path::Path) -> R<String> {
    path.to_str().map(str::to_string).ok_or_else(|| "decline: a path that is not valid UTF-8".to_string())
}

/// `os.stat` seen as Python's `int(st_mtime)`: CPython builds the float as `sec + nsec * 1e-9` in double arithmetic.
pub(crate) fn mtime_int(m: &std::fs::Metadata) -> i64 {
    (m.mtime() as f64 + m.mtime_nsec() as f64 * 1e-9).trunc() as i64
}

pub(crate) fn mtime_ns(m: &std::fs::Metadata) -> i128 {
    m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128
}

#[cfg(target_os = "macos")]
pub(crate) fn dev_of(m: &std::fs::Metadata) -> i64 {
    m.dev() as u32 as i32 as i64 // dev_t is a signed 32-bit integer on macOS
}
#[cfg(not(target_os = "macos"))]
pub(crate) fn dev_of(m: &std::fs::Metadata) -> i64 {
    m.dev() as i64
}

/// `_scan_dir_sig`: [mtime_ns, size] of the directory.
pub(crate) fn dir_sig(path: &str) -> Option<Value> {
    let m = std::fs::metadata(path).ok()?;
    Some(json!([mtime_ns(&m) as i64, m.len() as i64]))
}

/// The directories and files `os.walk(top)` yields, in its order: top-down, entries in readdir order, a directory is
/// reported before its children, symlinked directories are listed but not entered, a directory that cannot be read is skipped silently.
fn walk(top: &str, out: &mut Vec<(String, Vec<String>)>) -> R<()> {
    let Ok(rd) = std::fs::read_dir(top) else { return Ok(()) };
    let mut dirs: Vec<String> = Vec::new();
    let mut files: Vec<String> = Vec::new();
    for entry in rd {
        let Ok(entry) = entry else { continue };
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            // A name that is not UTF-8 is a file Python would store with surrogate escapes; refuse only if it could be a track.
            let lower = entry.file_name().to_string_lossy().to_ascii_lowercase();
            if lower.ends_with(".mp4") || lower.ends_with(".cdg") || lower.ends_with(".zip") {
                return decline("a media file name that is not valid UTF-8");
            }
            // It could still be a directory holding tracks.
            if entry.path().is_dir() {
                return decline("a directory name that is not valid UTF-8");
            }
            continue;
        };
        let is_dir = std::fs::metadata(entry.path()).map(|m| m.is_dir()).unwrap_or(false); // DirEntry.is_dir() follows symlinks
        if is_dir { dirs.push(name) } else { files.push(name) }
    }
    out.push((top.to_string(), files));
    for d in dirs {
        let child = format!("{}/{}", top.trim_end_matches('/'), d);
        let is_link = std::fs::symlink_metadata(&child).map(|m| m.file_type().is_symlink()).unwrap_or(false);
        if !is_link {
            walk(&child, out)?;
        }
    }
    Ok(())
}

fn py_str(v: &Value) -> R<String> {
    text_or_empty(Some(v))
}

/// `_dedupe_library_tracks(tracks, trust_cached_identity=False, location_priority=...)`.
fn dedupe(tracks: Vec<Value>, priority: &HashMap<String, i64>, env: &Env, trust: bool) -> R<Vec<Value>> {
    let mut result: Vec<Value> = Vec::new();
    let mut seen_paths: HashSet<String> = HashSet::new();
    let mut seen_files: HashMap<(i64, u64), usize> = HashMap::new();
    for track in tracks {
        let Value::Object(mut item) = track else { continue };
        if !item.get("path").map(truthy).unwrap_or(false) {
            continue;
        }
        let raw = py_str(&item["path"])?;
        let path = normalize_scanned_path(&raw, env)?;
        item.insert("path".into(), Value::String(path.clone()));
        let path_key = scanned_key(&path, env)?;
        let mut physical: Option<(i64, u64)> = None;
        let cached = if trust {
            match (item.get("scan_device"), item.get("scan_inode")) {
                (Some(d), Some(i)) if !d.is_null() && !i.is_null() => Some((d.as_i64(), i.as_u64())),
                _ => None,
            }
        } else {
            None
        };
        if let Some((d, i)) = cached {
            // trust_cached_identity: the stored device and inode are the identity; a value int() cannot read leaves it unknown (no stat)
            physical = d.zip(i);
        } else if let Ok(m) = std::fs::metadata(&path) {
            physical = Some((dev_of(&m), m.ino()));
            item.insert("scan_device".into(), json!(dev_of(&m)));
            item.insert("scan_inode".into(), json!(m.ino()));
        }
        if seen_paths.contains(&path_key) {
            continue;
        }
        if let Some(pk) = physical {
            if let Some(&idx) = seen_files.get(&pk) {
                let prio = |t: &Value| -> R<i64> {
                    match t.get("library_location_id") {
                        None | Some(Value::Null) => Ok(0),
                        Some(Value::String(s)) => Ok(*priority.get(s).unwrap_or(&0)),
                        Some(_) => decline("a library_location_id that is not text"),
                    }
                };
                let existing_priority = prio(&result[idx])?;
                let item_value = Value::Object(item);
                let item_priority = prio(&item_value)?;
                if item_priority > existing_priority {
                    let old_path = py_str(&result[idx]["path"])?;
                    seen_paths.remove(&scanned_key(&old_path, env)?);
                    result[idx] = item_value;
                    seen_paths.insert(path_key);
                }
                continue;
            }
            seen_files.insert(pk, result.len());
        }
        seen_paths.insert(path_key);
        result.push(Value::Object(item));
    }
    Ok(result)
}

pub fn build_scan_result(req: &Value) -> R<Value> {
    let started = std::time::Instant::now();
    let quick_mode = req.get("quick_mode").map(truthy).unwrap_or(false);
    let cwd = match req.get("cwd").and_then(Value::as_str) {
        Some(c) => c.to_string(),
        None => utf8(&std::env::current_dir().map_err(|e| e.to_string())?)?,
    };
    let env = Env { cwd };
    let settings = req.get("settings").cloned().unwrap_or_else(|| json!({}));
    let old_tracks: Vec<Value> = match req.get("old_tracks") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(_) => return decline("old_tracks is not a list"),
    };

    let mut requested_roots: Vec<String> = Vec::new();
    match req.get("roots") {
        None | Some(Value::Null) => {}
        Some(Value::Array(a)) => {
            for r in a {
                if !truthy(r) {
                    continue;
                }
                let Value::String(s) = r else { return decline("a root that is not text") };
                requested_roots.push(normalize_location_path(s, &env)?);
            }
        }
        Some(_) => return decline("roots is not a list"),
    }
    let mut available: Vec<String> = Vec::new();
    let mut unavailable: Vec<String> = Vec::new();
    for r in &requested_roots {
        if !r.is_empty() && std::path::Path::new(r).is_dir() { available.push(r.clone()) } else { unavailable.push(r.clone()) }
    }
    let roots = collapse_roots(&available, &env)?;
    let locations = migrate_locations(&settings, &env)?;
    let preserve_outside = settings.get("_scan_preserve_outside_roots").map(truthy).unwrap_or(false) || roots.len() != requested_roots.len();

    // old_by_path keys (the paths the previous library knew)
    let mut old_paths: HashSet<String> = HashSet::new();
    for t in &old_tracks {
        if let Value::Object(o) = t {
            if o.get("path").map(truthy).unwrap_or(false) {
                old_paths.insert(normalize_scanned_path(&py_str(&o["path"])?, &env)?);
            }
        }
    }
    let mut old_by_path: HashMap<String, Value> = HashMap::new();
    let mut old_duration_by_path: HashMap<String, Value> = HashMap::new();
    for t in &old_tracks {
        if let Value::Object(o) = t {
            if o.get("path").map(truthy).unwrap_or(false) {
                let p = normalize_scanned_path(&py_str(&o["path"])?, &env)?;
                old_by_path.insert(p.clone(), t.clone());
                if o.get("duration").map(truthy).unwrap_or(false) {
                    old_duration_by_path.insert(p, o["duration"].clone());
                }
            }
        }
    }
    let fmt = match settings.get("filename_format") {
        None | Some(Value::Null) => DEFAULT_FILENAME_FORMAT.to_string(),
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::String(_)) => DEFAULT_FILENAME_FORMAT.to_string(),
        Some(v) if !truthy(v) => DEFAULT_FILENAME_FORMAT.to_string(),
        Some(_) => return decline("filename_format is not text"),
    };

    let root_keys: Vec<String> = roots.iter().map(|r| scanned_key(r, &env)).collect::<R<_>>()?;
    let under_any_root = |path: &str| -> R<bool> {
        let p = scanned_key(path, &env)?;
        Ok(root_keys.iter().any(|rn| is_under(&p, rn)))
    };

    let mut new_tracks: Vec<Value> = Vec::new();
    if preserve_outside {
        if !roots.is_empty() {
            for t in &old_tracks {
                if let Value::Object(o) = t {
                    let p = py_str(o.get("path").unwrap_or(&Value::Null))?;
                    if !p.is_empty() && !under_any_root(&p)? {
                        new_tracks.push(t.clone());
                    }
                }
            }
        } else {
            new_tracks = old_tracks.clone();
        }
    }
    let mut seen: HashSet<String> = HashSet::new();
    for t in &new_tracks {
        if let Value::Object(o) = t {
            if o.get("path").map(truthy).unwrap_or(false) {
                seen.insert(py_str(&o["path"])?);
            }
        }
    }

    let stored_sigs: Map<String, Value> = match settings.get("karaoke_scan_dir_sigs") {
        Some(Value::Object(o)) if !o.is_empty() => o.clone(),
        _ => Map::new(),
    };
    let mut dir_sigs: Map<String, Value> = Map::new();
    for (p, sig) in &stored_sigs {
        let mut within = false;
        for root in &roots {
            if path_is_within_library(p, root, &env)? {
                within = true;
                break;
            }
        }
        if !within {
            dir_sigs.insert(p.clone(), sig.clone());
        }
    }

    let (mut files_seen, mut tracks_added, mut zip_count, mut mp4_count, mut cdg_count) = (0i64, 0i64, 0i64, 0i64, 0i64);
    let (mut tracks_reused, mut tracks_changed) = (0i64, 0i64);
    let mut changed_paths: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    if quick_mode {
        let baseline_ns = req.get("tracks_baseline_ns").and_then(Value::as_i64).unwrap_or(0) as i128;
        let q = crate::quick::quick_scan(&crate::quick::Ctx {
            env: &env,
            roots: &roots,
            fmt: &fmt,
            locations: &locations,
            old_tracks: &old_tracks,
            old_by_path: &old_by_path,
            old_duration_by_path: &old_duration_by_path,
            stored_sigs: &stored_sigs,
            baseline_ns,
        })?;
        new_tracks = q.new_tracks;
        files_seen = q.files_seen;
        tracks_added = q.tracks_added;
        zip_count = q.zip_count;
        mp4_count = q.mp4_count;
        cdg_count = q.cdg_count;
        tracks_reused = q.tracks_reused;
        tracks_changed = q.tracks_changed;
        changed_paths = q.changed_paths;
        dir_sigs = q.dir_sigs;
    } else {
        for scan_root in &roots {
            let mut listing: Vec<(String, Vec<String>)> = Vec::new();
            walk(scan_root, &mut listing)?;
            for (root, files) in listing {
                let root_abs = normalize_scanned_path(&root, &env)?;
                if let Some(sig) = dir_sig(&root_abs) {
                    dir_sigs.insert(root_abs, sig);
                }
                for name in files {
                    files_seen += 1;
                    let full_path = normalize_scanned_path(&format!("{}/{}", root.trim_end_matches('/'), name), &env)?;
                    if name.starts_with('.') || seen.contains(&full_path) {
                        continue;
                    }
                    let Some(song_type) = song_type_for_name(&name) else { continue };
                    match song_type {
                        "mp4" => mp4_count += 1,
                        "cdg" => cdg_count += 1,
                        _ => zip_count += 1,
                    }
                    seen.insert(full_path.clone());
                    let (artist, title, disc_id) = parse_filename_stem(stem_of(&name), &fmt);
                    let mut track = Map::new();
                    track.insert("path".into(), json!(full_path));
                    track.insert("type".into(), json!(song_type));
                    track.insert("artist".into(), json!(artist));
                    track.insert("title".into(), json!(title));
                    track.insert("disc_id".into(), json!(disc_id));
                    track.insert("duration".into(), Value::Null);
                    if let Ok(m) = std::fs::metadata(&full_path) {
                        track.insert("scan_mtime".into(), json!(mtime_int(&m)));
                        track.insert("scan_mtime_ns".into(), json!(mtime_ns(&m) as i64));
                        track.insert("scan_size".into(), json!(m.len() as i64));
                        track.insert("scan_device".into(), json!(dev_of(&m)));
                        track.insert("scan_inode".into(), json!(m.ino()));
                    }
                    if let Some(loc) = location_for_path(&full_path, &locations, &env)? {
                        track.insert("library_location_id".into(), json!(loc.id));
                    }
                    let display = display_for_track(&full_path, &artist_title_of(&track).1, &artist_title_of(&track).0, &disc_of(&track));
                    track.insert("display".into(), json!(display));
                    new_tracks.push(Value::Object(track));
                    tracks_added += 1;
                }
            }
        }
    }

    // Mass-deletion guard: a root that scanned completely empty while the old library had tracks under it keeps those records.
    let mut suspect: Vec<String> = Vec::new();
    if !old_tracks.is_empty() {
        let mut new_keys: HashSet<String> = HashSet::new();
        for t in &new_tracks {
            if let Value::Object(o) = t {
                if o.get("path").map(truthy).unwrap_or(false) {
                    new_keys.insert(scanned_key(&py_str(&o["path"])?, &env)?);
                }
            }
        }
        for root in &roots {
            let root_key = scanned_key(root, &env)?;
            if root_key.is_empty() || new_keys.iter().any(|k| is_under(k, &root_key)) {
                continue;
            }
            let mut old_under: Vec<Value> = Vec::new();
            for t in &old_tracks {
                if let Value::Object(o) = t {
                    if o.get("path").map(truthy).unwrap_or(false) && is_under(&scanned_key(&py_str(&o["path"])?, &env)?, &root_key) {
                        old_under.push(t.clone());
                    }
                }
            }
            if old_under.is_empty() {
                continue;
            }
            suspect.push(root.clone());
            new_tracks.extend(old_under);
            for (p, sig) in &stored_sigs {
                if path_is_within_library(p, root, &env)? {
                    dir_sigs.insert(p.clone(), sig.clone());
                }
            }
        }
    }

    let mut priority: HashMap<String, i64> = HashMap::new();
    for l in &locations {
        priority.insert(l.id.clone(), scanned_key(&l.path, &env)?.chars().count() as i64);
    }
    let new_tracks = dedupe(new_tracks, &priority, &env, quick_mode)?;

    let mut new_paths: HashSet<String> = HashSet::new();
    for t in &new_tracks {
        if let Value::Object(o) = t {
            if o.get("path").map(truthy).unwrap_or(false) {
                new_paths.insert(py_str(&o["path"])?);
            }
        }
    }
    let mut added: Vec<String> = new_paths.difference(&old_paths).cloned().collect();
    let mut removed: Vec<String> = old_paths.difference(&new_paths).cloned().collect();
    added.sort();
    removed.sort();
    let added_count = added.len() as i64;
    let removed_count = removed.len() as i64;
    let mut index_changed_set: std::collections::BTreeSet<String> = added.iter().cloned().collect();
    index_changed_set.extend(changed_paths.iter().cloned());
    let index_changed: Vec<String> = index_changed_set.into_iter().collect();
    let reindex_needed = (!quick_mode && !roots.is_empty()) || added_count > 0 || removed_count > 0 || tracks_changed > 0;
    let mut summary = format!("Added: {} • Removed: {} • Total: {}", thousands(added_count), thousands(removed_count), thousands(new_tracks.len() as i64));
    if !suspect.is_empty() {
        summary.push_str(&format!(" • ⚠ {} location(s) looked empty; songs preserved", suspect.len()));
    }
    let overlapping = (available.len() as i64 - roots.len() as i64).max(0);
    Ok(json!({
        "ok": true,
        "quick_mode": quick_mode,
        "roots": roots,
        "requested_roots": requested_roots,
        "unavailable_roots": unavailable,
        "overlapping_roots_skipped": overlapping,
        "tracks": new_tracks,
        "dir_sigs": dir_sigs,
        "tracks_added": tracks_added,
        "zip_count": zip_count,
        "mp4_count": mp4_count,
        "cdg_count": cdg_count,
        "tracks_reused": tracks_reused,
        "tracks_changed": tracks_changed,
        "files_seen": files_seen,
        "added_count": added_count,
        "removed_count": removed_count,
        "index_changed_paths": index_changed,
        "index_removed_paths": removed,
        "reindex_needed": reindex_needed,
        "suspect_empty_roots": suspect,
        "summary_text": summary,
        "scan_time": started.elapsed().as_secs_f64(),
    }))
}

fn artist_title_of(t: &Map<String, Value>) -> (String, String) {
    (t["artist"].as_str().unwrap_or("").to_string(), t["title"].as_str().unwrap_or("").to_string())
}

fn disc_of(t: &Map<String, Value>) -> String {
    t["disc_id"].as_str().unwrap_or("").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn scratch(tag: &str) -> PathBuf {
        let p = std::fs::canonicalize(std::env::temp_dir())
            .unwrap()
            .join(format!("singws-scan-test-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn touch(p: &std::path::Path) {
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(p, b"x").unwrap();
    }

    fn scan(root: &std::path::Path, old: Value, settings: Value) -> Value {
        build_scan_result(&json!({"roots": [root.to_str().unwrap()], "old_tracks": old, "settings": settings, "cwd": "/"})).unwrap()
    }

    fn paths(r: &Value) -> Vec<String> {
        let mut v: Vec<String> = r["tracks"].as_array().unwrap().iter().map(|t| t["path"].as_str().unwrap().to_string()).collect();
        v.sort();
        v
    }

    #[test]
    fn finds_media_skips_hidden_and_other_files_and_fills_the_track() {
        let d = scratch("basic");
        touch(&d.join("KV - Abba - Dancing Queen.CDG"));
        touch(&d.join("sub/Abba - Waterloo - SC.zip"));
        touch(&d.join("sub/clip.MP4"));
        touch(&d.join(".hidden.cdg"));
        touch(&d.join("._junk.cdg"));
        touch(&d.join("notes.txt"));
        touch(&d.join(".dot/inside.cdg"));
        let r = scan(&d, json!([]), json!({}));
        let root = d.to_str().unwrap();
        assert_eq!(paths(&r), vec![format!("{root}/.dot/inside.cdg"), format!("{root}/KV - Abba - Dancing Queen.CDG"), format!("{root}/sub/Abba - Waterloo - SC.zip"), format!("{root}/sub/clip.MP4")]);
        assert_eq!((r["cdg_count"].as_i64(), r["zip_count"].as_i64(), r["mp4_count"].as_i64()), (Some(2), Some(1), Some(1)));
        assert_eq!(r["files_seen"], 7);
        let t = r["tracks"].as_array().unwrap().iter().find(|t| t["type"] == "cdg" && t["path"].as_str().unwrap().contains("KV")).unwrap();
        assert_eq!((t["artist"].as_str(), t["title"].as_str(), t["disc_id"].as_str()), (Some("Abba"), Some("Dancing Queen"), Some("KV")));
        assert_eq!(t["display"], "Abba - Dancing Queen - KV");
        assert!(t["duration"].is_null() && t["scan_size"] == 1 && t["library_location_id"].is_null());
        assert!(r["dir_sigs"].as_object().unwrap().contains_key(root));
        assert!(r["dir_sigs"].as_object().unwrap().contains_key(&format!("{root}/.dot")));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn the_filename_format_setting_changes_the_split() {
        let d = scratch("fmt");
        touch(&d.join("Abba - Waterloo - SC.zip"));
        let r = scan(&d, json!([]), json!({"filename_format": "artist-title-disc"}));
        assert_eq!(r["tracks"][0]["disc_id"], "SC");
        assert_eq!(r["tracks"][0]["artist"], "Abba");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn quick_mode_with_nothing_to_scan_is_an_empty_quick_result() {
        let r = build_scan_result(&json!({"roots": [], "quick_mode": true})).unwrap();
        assert_eq!(r["quick_mode"], json!(true));
        assert_eq!(r["tracks"], json!([]));
        assert_eq!(r["reindex_needed"], json!(false));
    }

    #[test]
    fn a_quick_update_text_signature_it_cannot_read_is_declined_not_guessed() {
        let dir = std::env::temp_dir().join(format!("scan_quick_decl_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let root = std::fs::canonicalize(&dir).unwrap().to_str().unwrap().to_string();
        let e = build_scan_result(&json!({"roots": [root.clone()], "quick_mode": true, "settings": {"karaoke_scan_dir_sigs": {root: "12"}}})).unwrap_err();
        std::fs::remove_dir_all(&dir).ok();
        assert!(e.starts_with("decline:"), "{e}");
    }

    #[test]
    fn added_and_removed_are_counted_against_the_old_library() {
        let d = scratch("diff");
        touch(&d.join("a - b - c.cdg"));
        let root = d.to_str().unwrap();
        let old = json!([{"path": format!("{root}/gone - x - y.cdg")}, {"path": format!("{root}/a - b - c.cdg")}]);
        let r = scan(&d, old, json!({}));
        assert_eq!((r["added_count"].as_i64(), r["removed_count"].as_i64()), (Some(0), Some(1)));
        assert_eq!(r["index_removed_paths"], json!([format!("{root}/gone - x - y.cdg")]));
        assert_eq!(r["reindex_needed"], true);
        assert!(r["summary_text"].as_str().unwrap().starts_with("Added: 0 • Removed: 1 • Total: 1"));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn an_empty_root_keeps_the_old_tracks_under_it() {
        let d = scratch("guard");
        let root = d.to_str().unwrap();
        let old = json!([{"path": format!("{root}/x/old.cdg"), "title": "kept"}]);
        let r = scan(&d, old, json!({"karaoke_scan_dir_sigs": {format!("{root}/x"): [1, 2]}}));
        assert_eq!(r["suspect_empty_roots"], json!([root]));
        assert_eq!(r["tracks"][0]["title"], "kept");
        assert_eq!(r["dir_sigs"][&format!("{root}/x")], json!([1, 2]));
        assert!(r["summary_text"].as_str().unwrap().contains("1 location(s) looked empty"));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn a_missing_root_turns_on_preserving_tracks_outside_the_roots() {
        let d = scratch("preserve");
        touch(&d.join("here.cdg"));
        let root = d.to_str().unwrap();
        let missing = format!("{root}/nope");
        let req = json!({"roots": [root, missing], "old_tracks": [{"path": "/elsewhere/keep.cdg"}], "settings": {}, "cwd": "/"});
        let r = build_scan_result(&req).unwrap();
        assert_eq!(r["unavailable_roots"], json!([missing]));
        assert!(paths(&r).contains(&"/elsewhere/keep.cdg".to_string()) || r["tracks"].as_array().unwrap().iter().any(|t| t["path"] == "/elsewhere/keep.cdg"));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn overlapping_roots_are_collapsed_and_a_hard_link_is_one_song() {
        let d = scratch("dedupe");
        touch(&d.join("a/one - two - KV.cdg"));
        fs::hard_link(d.join("a/one - two - KV.cdg"), d.join("a/copy - two - KV.cdg")).unwrap();
        let root = d.to_str().unwrap();
        let req = json!({"roots": [format!("{root}/a"), root], "old_tracks": [], "settings": {}, "cwd": "/"});
        let r = build_scan_result(&req).unwrap();
        assert_eq!(r["roots"], json!([root]));
        assert_eq!(r["overlapping_roots_skipped"], 1);
        assert_eq!(r["tracks"].as_array().unwrap().len(), 1, "same inode, one entry");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn symlinked_directories_are_listed_but_not_entered() {
        let d = scratch("links");
        touch(&d.join("real/song - a - b.cdg"));
        std::os::unix::fs::symlink(d.join("real"), d.join("shortcut")).unwrap();
        std::os::unix::fs::symlink(d.join("missing.cdg"), d.join("broken.cdg")).unwrap();
        let r = scan(&d, json!([]), json!({}));
        let root = d.to_str().unwrap();
        let got = paths(&r);
        assert!(got.contains(&format!("{root}/real/song - a - b.cdg")));
        assert!(!got.iter().any(|p| p.contains("shortcut")));
        let broken = r["tracks"].as_array().unwrap().iter().find(|t| t["path"].as_str().unwrap().ends_with("broken.cdg")).unwrap();
        assert!(broken.get("scan_mtime").is_none() && broken.get("scan_inode").is_none(), "an unstattable file keeps no stat fields");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn location_ids_come_from_the_settings_and_the_longest_location_wins() {
        let d = scratch("loc");
        touch(&d.join("inner/x - y - z.cdg"));
        let root = d.to_str().unwrap();
        let settings = json!({"karaoke_library_locations_migrated": true,
            "karaoke_library_locations": [{"path": root, "id": "outer"}, {"path": format!("{root}/inner"), "id": "inner"}]});
        let r = build_scan_result(&json!({"roots": [root], "old_tracks": [], "settings": settings, "cwd": "/"})).unwrap();
        assert_eq!(r["tracks"][0]["library_location_id"], "inner");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn mtime_is_truncated_the_way_python_does_it() {
        use std::time::{Duration, UNIX_EPOCH};
        let d = scratch("mtime");
        let f = d.join("a - b - c.cdg");
        touch(&f);
        // 1700000000.999999999 rounds UP to 1700000001.0 as a double, so Python's int() says 1700000001
        let t = UNIX_EPOCH + Duration::new(1_700_000_000, 999_999_999);
        fs::File::options().write(true).open(&f).unwrap().set_modified(t).unwrap();
        let r = scan(&d, json!([]), json!({}));
        assert_eq!(r["tracks"][0]["scan_mtime"], 1_700_000_001i64);
        assert_eq!(r["tracks"][0]["scan_mtime_ns"], 1_700_000_000_999_999_999i64);
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn an_unchanged_library_still_asks_for_a_reindex() {
        let d = scratch("same");
        touch(&d.join("a - b - c.cdg"));
        let root = d.to_str().unwrap();
        let r = scan(&d, json!([{"path": format!("{root}/a - b - c.cdg")}]), json!({}));
        assert_eq!((r["added_count"].as_i64(), r["removed_count"].as_i64()), (Some(0), Some(0)));
        assert_eq!(r["reindex_needed"], true, "a full scan always rebuilds the index");
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn a_path_listed_twice_in_the_old_library_survives_once() {
        let d = scratch("dupold");
        let r = build_scan_result(&json!({"roots": [], "old_tracks": [{"path": "/x/a.cdg"}, {"path": "/x/a.cdg"}], "settings": {"_scan_preserve_outside_roots": true}, "cwd": "/"})).unwrap();
        assert_eq!(r["tracks"].as_array().unwrap().len(), 1);
        let _ = fs::remove_dir_all(&d);
    }
}
