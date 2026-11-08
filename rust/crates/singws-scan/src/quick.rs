//! The QUICK (incremental) branch of `_build_library_scan_result` (0.2.18.1.py, `if quick_mode:` ... before the mass-deletion guard): directory signatures decide which
//! folders are re-read, every cached file is verified with one stat, unchanged tracks are reused as they were, and the track list keeps the old order with new files sorted by
//! normalized file name. The previous scan's state comes in as data (`old_tracks`, `settings.karaoke_scan_dir_sigs`) and the age of tracks.json as `tracks_baseline_ns`
//! (Python reads it with `TRACKS_PATH.stat()`), so the result depends only on the request and the disk.
//!
//! Anything this port cannot reproduce exactly is DECLINED (never guessed): numbers hidden in text, a non-UTF-8 media or directory name.
use crate::locations::*;
use crate::names::*;
use crate::pyfs::*;
use crate::scan::{dev_of, dir_sig, mtime_int, mtime_ns};
use serde_json::{Map, Value, json};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::os::unix::fs::MetadataExt;

pub(crate) struct Ctx<'a> {
    pub env: &'a Env,
    pub roots: &'a [String],
    pub fmt: &'a str,
    pub locations: &'a [Location],
    pub old_tracks: &'a [Value],
    pub old_by_path: &'a HashMap<String, Value>,
    pub old_duration_by_path: &'a HashMap<String, Value>,
    pub stored_sigs: &'a Map<String, Value>,
    pub baseline_ns: i128,
}

pub(crate) struct Out {
    pub new_tracks: Vec<Value>,
    pub files_seen: i64,
    pub tracks_added: i64,
    pub zip_count: i64,
    pub mp4_count: i64,
    pub cdg_count: i64,
    pub tracks_reused: i64,
    pub tracks_changed: i64,
    pub changed_paths: BTreeSet<String>,
    pub dir_sigs: Map<String, Value>,
}

/// A Python dict that keeps insertion order, keeps a key's place when its value is replaced, and moves a key to the end when it is removed and set again.
#[derive(Default)]
struct OrdMap {
    slots: Vec<Option<(String, Value)>>,
    index: HashMap<String, usize>,
}

impl OrdMap {
    fn get(&self, k: &str) -> Option<&Value> {
        self.index.get(k).and_then(|&i| self.slots[i].as_ref()).map(|(_, v)| v)
    }
    fn contains(&self, k: &str) -> bool {
        self.index.contains_key(k)
    }
    fn insert(&mut self, k: String, v: Value) {
        match self.index.get(&k) {
            Some(&i) => self.slots[i] = Some((k, v)),
            None => {
                self.index.insert(k.clone(), self.slots.len());
                self.slots.push(Some((k, v)));
            }
        }
    }
    fn remove(&mut self, k: &str) {
        if let Some(i) = self.index.remove(k) {
            self.slots[i] = None;
        }
    }
    fn keys(&self) -> Vec<String> {
        self.slots.iter().flatten().map(|(k, _)| k.clone()).collect()
    }
}

/// Python's `int(value)` for a JSON value: numbers truncate, booleans are 0/1, null/lists/mappings raise (None), text is DECLINED (it may or may not parse).
fn py_int(v: &Value) -> R<Option<i128>> {
    match v {
        Value::Bool(b) => Ok(Some(*b as i128)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                return Ok(Some(i as i128));
            }
            if let Some(u) = n.as_u64() {
                return Ok(Some(u as i128));
            }
            let f = n.as_f64().unwrap_or(f64::NAN);
            Ok(if f.is_finite() { Some(f.trunc() as i128) } else { None })
        }
        Value::String(_) => decline("a number given as text"),
        _ => Ok(None),
    }
}

fn present(v: Option<&Value>) -> Option<&Value> {
    match v {
        None | Some(Value::Null) => None,
        s => s,
    }
}

/// `_old_track_unchanged(old_track, mtime, mtime_ns, size)`; `stat` is None when the file could not be stat-ed (Python then passes None and `int(None)` raises: False).
fn old_track_unchanged(old: Option<&Value>, stat: Option<(i64, i128, i64)>) -> R<bool> {
    let Some(Value::Object(o)) = old else { return Ok(false) };
    let (Some(old_mtime), Some(old_size)) = (present(o.get("scan_mtime")), present(o.get("scan_size"))) else { return Ok(false) };
    let Some((mtime, mtime_ns, size)) = stat else { return Ok(false) };
    let matches = match present(o.get("scan_mtime_ns")) {
        Some(ns) => py_int(ns)? == Some(mtime_ns),
        None => py_int(old_mtime)? == Some(mtime as i128),
    };
    Ok(matches && py_int(old_size)? == Some(size as i128))
}

fn dirname(path: &str) -> String {
    match path.rfind('/') {
        None => String::new(),
        Some(i) => {
            let head = &path[..=i];
            if head.chars().all(|c| c == '/') { head.to_string() } else { head.trim_end_matches('/').to_string() }
        }
    }
}

fn basename(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

fn stat_of(path: &str) -> Option<std::fs::Metadata> {
    std::fs::metadata(path).ok()
}

pub(crate) fn quick_scan(c: &Ctx) -> R<Out> {
    let env = c.env;
    let root_keys: Vec<String> = c.roots.iter().map(|r| scanned_key(r, env)).collect::<R<_>>()?;
    let under_any_root = |path: &str| -> R<bool> {
        let p = scanned_key(path, env)?;
        Ok(root_keys.iter().any(|rn| is_under(&p, rn)))
    };

    let mut tracks_by_path = OrdMap::default();
    for t in c.old_tracks {
        if let Value::Object(o) = t {
            if o.get("path").map(truthy).unwrap_or(false) {
                let p = normalize_scanned_path(&text_or_empty(o.get("path"))?, env)?;
                tracks_by_path.insert(p, t.clone());
            }
        }
    }
    for path in tracks_by_path.keys() {
        if basename(&path).starts_with('.') {
            tracks_by_path.remove(&path);
        }
    }
    let mut under_root_paths: BTreeSet<String> = BTreeSet::new();
    for p in tracks_by_path.keys() {
        if under_any_root(&p)? {
            under_root_paths.insert(p);
        }
    }
    let mut known_dirs: HashSet<String> = HashSet::new();
    for r in c.roots {
        known_dirs.insert(normalize_scanned_path(r, env)?);
    }
    for p in &under_root_paths {
        known_dirs.insert(normalize_scanned_path(&dirname(p), env)?);
    }
    for p in c.stored_sigs.keys() {
        if under_any_root(p)? {
            known_dirs.insert(normalize_scanned_path(p, env)?);
        }
    }

    let mut dir_sigs: Map<String, Value> = Map::new();
    for (p, sig) in c.stored_sigs {
        let mut within = false;
        for root in c.roots {
            if path_is_within_library(p, root, env)? {
                within = true;
                break;
            }
        }
        if !within {
            dir_sigs.insert(p.clone(), sig.clone());
        }
    }

    let no_dir_cache = c.stored_sigs.is_empty();
    let mut changed_dirs: BTreeSet<String> = BTreeSet::new();
    let mut removed_dirs: BTreeSet<String> = BTreeSet::new();
    let mut sorted_known: Vec<String> = known_dirs.iter().cloned().collect();
    sorted_known.sort();
    for directory in &sorted_known {
        let Some(sig) = dir_sig(directory) else {
            removed_dirs.insert(directory.clone());
            continue;
        };
        dir_sigs.insert(directory.clone(), sig.clone());
        let new0 = sig[0].as_i64().unwrap_or(0) as i128;
        let new1 = sig[1].as_i64().unwrap_or(0) as i128;
        match present(c.stored_sigs.get(directory)) {
            None => {
                if no_dir_cache && c.baseline_ns != 0 && new0 <= c.baseline_ns {
                    continue;
                }
                changed_dirs.insert(directory.clone());
            }
            Some(old_sig) => {
                let same = match old_sig {
                    Value::String(_) => return decline("a stored directory signature given as text"),
                    Value::Array(a) if a.len() >= 2 => match (py_int(&a[0])?, py_int(&a[1])?) {
                        (Some(x), Some(y)) => x == new0 && y == new1,
                        _ => false,
                    },
                    _ => false,
                };
                if !same {
                    changed_dirs.insert(directory.clone());
                }
            }
        }
    }

    // One stat per cached file: a replaced file does not change its directory's mtime.
    for path in &under_root_paths {
        let old_track = tracks_by_path.get(path).cloned().or_else(|| c.old_by_path.get(path).cloned());
        let parent = normalize_scanned_path(&dirname(path), env)?;
        match stat_of(path) {
            None => {
                changed_dirs.insert(parent);
            }
            Some(m) => {
                if !old_track_unchanged(old_track.as_ref(), Some((mtime_int(&m), mtime_ns(&m), m.len() as i64)))? {
                    changed_dirs.insert(parent);
                }
            }
        }
    }

    let mut by_len: Vec<String> = removed_dirs.iter().cloned().collect();
    by_len.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()));
    for directory in &by_len {
        let dn = scanned_key(directory, env)?;
        for path in tracks_by_path.keys() {
            let pn = scanned_key(&path, env)?;
            if pn.starts_with(&format!("{dn}/")) {
                tracks_by_path.remove(&path);
            }
        }
    }

    let mut st = Walk {
        c,
        known_dirs: &known_dirs,
        under_root_paths: &under_root_paths,
        tracks_by_path,
        dir_sigs,
        scanned_dirs: HashSet::new(),
        files_seen: 0,
        zip: 0,
        mp4: 0,
        cdg: 0,
        reused: 0,
        changed: 0,
        changed_paths: BTreeSet::new(),
    };
    for directory in &changed_dirs {
        st.scan_directory(directory, false)?;
    }

    let mut new_tracks: Vec<Value> = Vec::new();
    let mut emitted: HashSet<String> = HashSet::new();
    for old in c.old_tracks {
        let Value::Object(o) = old else { continue };
        let p = text_or_empty(o.get("path"))?;
        if !p.is_empty() && st.tracks_by_path.contains(&p) && !emitted.contains(&p) {
            new_tracks.push(st.tracks_by_path.get(&p).cloned().unwrap_or(Value::Null));
            emitted.insert(p);
        }
    }
    let mut rest: Vec<(String, String)> = Vec::new();
    for p in st.tracks_by_path.keys() {
        rest.push((singws_dbw::pyval::normalize_text(basename(&p))?, p));
    }
    rest.sort_by(|a, b| a.0.cmp(&b.0));
    for (_, p) in rest {
        if !emitted.contains(&p) {
            new_tracks.push(st.tracks_by_path.get(&p).cloned().unwrap_or(Value::Null));
            emitted.insert(p);
        }
    }
    let mut tracks_added = 0i64;
    for t in &new_tracks {
        if let Value::Object(o) = t {
            if under_any_root(&text_or_empty(o.get("path"))?)? {
                tracks_added += 1;
            }
        }
    }
    let mut reused = st.reused;
    if changed_dirs.is_empty() && removed_dirs.is_empty() {
        reused = tracks_added;
    }
    Ok(Out {
        new_tracks,
        files_seen: st.files_seen,
        tracks_added,
        zip_count: st.zip,
        mp4_count: st.mp4,
        cdg_count: st.cdg,
        tracks_reused: reused,
        tracks_changed: st.changed,
        changed_paths: st.changed_paths,
        dir_sigs: st.dir_sigs,
    })
}

struct Walk<'a> {
    c: &'a Ctx<'a>,
    known_dirs: &'a HashSet<String>,
    under_root_paths: &'a BTreeSet<String>,
    tracks_by_path: OrdMap,
    dir_sigs: Map<String, Value>,
    scanned_dirs: HashSet<String>,
    files_seen: i64,
    zip: i64,
    mp4: i64,
    cdg: i64,
    reused: i64,
    changed: i64,
    changed_paths: BTreeSet<String>,
}

impl Walk<'_> {
    /// `_scan_directory_files(directory, recursive)`.
    fn scan_directory(&mut self, directory: &str, recursive: bool) -> R<()> {
        let env = self.c.env;
        let directory = normalize_scanned_path(directory, env)?;
        if self.scanned_dirs.contains(&directory) {
            return Ok(());
        }
        self.scanned_dirs.insert(directory.clone());
        let Ok(rd) = std::fs::read_dir(&directory) else { return Ok(()) };
        let mut entries: Vec<(String, bool)> = Vec::new(); // (name, is a real directory: no symlink following)
        for e in rd {
            let Ok(e) = e else { continue };
            let Some(name) = e.file_name().to_str().map(str::to_string) else {
                let lower = e.file_name().to_string_lossy().to_ascii_lowercase();
                if lower.ends_with(".mp4") || lower.ends_with(".cdg") || lower.ends_with(".zip") {
                    return decline("a media file name that is not valid UTF-8");
                }
                if e.path().is_dir() {
                    return decline("a directory name that is not valid UTF-8");
                }
                continue;
            };
            let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
            entries.push((name, is_dir));
        }
        if let Some(sig) = dir_sig(&directory) {
            self.dir_sigs.insert(directory.clone(), sig);
        }
        let mut current_files: HashSet<String> = HashSet::new();
        for (name, is_dir) in entries {
            if name.starts_with('.') {
                continue;
            }
            let joined = format!("{}/{}", directory.trim_end_matches('/'), name);
            if is_dir {
                let child = normalize_scanned_path(&joined, env)?;
                if let Some(sig) = dir_sig(&child) {
                    self.dir_sigs.insert(child.clone(), sig);
                }
                if recursive || !self.known_dirs.contains(&child) {
                    self.scan_directory(&child, true)?;
                }
                continue;
            }
            let Some(song_type) = song_type_for_name(&name) else { continue };
            let full_path = normalize_scanned_path(&joined, env)?;
            current_files.insert(full_path.clone());
            self.files_seen += 1;
            match song_type {
                "mp4" => self.mp4 += 1,
                "cdg" => self.cdg += 1,
                _ => self.zip += 1,
            }
            let old = self.c.old_by_path.get(&full_path).cloned();
            let (track, reused, changed_existing) = self.make_track(&joined, &name, &full_path, song_type, old.as_ref())?;
            self.tracks_by_path.insert(full_path.clone(), track);
            if reused {
                self.reused += 1;
            }
            if changed_existing {
                self.changed += 1;
                self.changed_paths.insert(full_path);
            }
        }
        for p in self.under_root_paths.clone() {
            if normalize_scanned_path(&dirname(&p), env)? == directory && !current_files.contains(&p) {
                self.tracks_by_path.remove(&p);
            }
        }
        Ok(())
    }

    /// `_make_scanned_track` with quick_mode on. Returns (track, reused, changed_existing).
    fn make_track(&self, _raw: &str, name: &str, full_path: &str, song_type: &str, old: Option<&Value>) -> R<(Value, bool, bool)> {
        let env = self.c.env;
        let stat = stat_of(full_path);
        let sig = stat.as_ref().map(|m| (mtime_int(m), mtime_ns(m), m.len() as i64));
        if old_track_unchanged(old, sig)? {
            let Some(Value::Object(o)) = old else { return Ok((Value::Null, false, false)) };
            let mut track = o.clone();
            track.insert("path".into(), json!(full_path));
            track.insert("type".into(), json!(song_type));
            if let Some((mt, ns, size)) = sig {
                track.insert("scan_mtime".into(), json!(mt));
                track.insert("scan_mtime_ns".into(), json!(ns as i64));
                track.insert("scan_size".into(), json!(size));
            }
            let missing = |k: &str, t: &Map<String, Value>| !t.get(k).map(truthy).unwrap_or(false);
            if missing("artist", &track) || missing("title", &track) || missing("display", &track) {
                let (artist, title, disc_id) = parse_filename_stem(stem_of(name), self.c.fmt);
                if missing("artist", &track) {
                    track.insert("artist".into(), json!(artist));
                }
                if missing("title", &track) {
                    track.insert("title".into(), json!(title));
                }
                if missing("disc_id", &track) {
                    track.insert("disc_id".into(), json!(disc_id));
                }
                let s = |k: &str| -> R<String> {
                    match track.get(k) {
                        None | Some(Value::Null) => Ok(String::new()),
                        Some(Value::String(s)) => Ok(s.clone()),
                        Some(_) => decline("a track field that is not text"),
                    }
                };
                let display = display_for_track(full_path, &s("title")?, &s("artist")?, &s("disc_id")?);
                track.insert("display".into(), json!(display));
            }
            if let Some(loc) = location_for_path(full_path, self.c.locations, env)? {
                track.insert("library_location_id".into(), json!(loc.id));
            }
            return Ok((Value::Object(track), true, false));
        }
        let (artist, title, disc_id) = parse_filename_stem(stem_of(name), self.c.fmt);
        let mut track = Map::new();
        track.insert("path".into(), json!(full_path));
        track.insert("type".into(), json!(song_type));
        track.insert("artist".into(), json!(artist));
        track.insert("title".into(), json!(title));
        track.insert("disc_id".into(), json!(disc_id));
        track.insert("duration".into(), self.c.old_duration_by_path.get(full_path).cloned().unwrap_or(Value::Null));
        if let Some(m) = &stat {
            track.insert("scan_mtime".into(), json!(mtime_int(m)));
            track.insert("scan_mtime_ns".into(), json!(mtime_ns(m) as i64));
            track.insert("scan_size".into(), json!(m.len() as i64));
            track.insert("scan_device".into(), json!(dev_of(m)));
            track.insert("scan_inode".into(), json!(m.ino()));
        }
        if let Some(loc) = location_for_path(full_path, self.c.locations, env)? {
            track.insert("library_location_id".into(), json!(loc.id));
        }
        let display = display_for_track(full_path, &title, &artist, &disc_id);
        track.insert("display".into(), json!(display));
        Ok((Value::Object(track), false, old.is_some()))
    }
}
