//! The settings-store mechanics, mirroring the Python adapter (`_load_json_file`, the `DEFAULTS` fill loop in the startup path, and
//! `_save_json_atomic`): parse with a dict-or-default rule, add missing keys in `DEFAULTS` order, serialize with the app's layout, and
//! write atomically behind a content-digest gate.
//!
//! The one-off migrations (`*_migrated` flags and their value rewrites) intentionally stay in Python for now; they operate on the
//! parsed dictionary, so they run between `parse_settings` and `fill_defaults` unchanged.
//!
//! Safety stance for the app's real files: this module never runs against a live profile in tests (every test uses a private temp dir),
//! and in the app Stage D1 is SHADOW ONLY: the Rust answer is compared with Python's, never written.

use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::fs;
use std::hash::{Hash, Hasher};
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{Map, Value};

use crate::{dumps, Style, StoreError};

/// Why settings text could not be used; the app treats both as "start from `{}`" (and prints a message).
#[derive(Debug, PartialEq, Eq)]
pub enum LoadIssue {
    Parse(String),
    /// Valid JSON but not an object (`expected_type=dict`).
    NotObject,
    /// Python could read it but Rust cannot reproduce it exactly (integers beyond 64 bits).
    Unsupported(String),
}

/// `_load_json_file(path, {}, expected_type=dict)` applied to already-read text.
pub fn parse_settings(text: &str) -> Result<Map<String, Value>, LoadIssue> {
    crate::reject_big_integers(text).map_err(|e| match e {
        StoreError::Unsupported(m) => LoadIssue::Unsupported(m),
        StoreError::Parse(m) => LoadIssue::Parse(m),
    })?;
    match serde_json::from_str::<Value>(text) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(_) => Err(LoadIssue::NotObject),
        Err(e) => Err(LoadIssue::Parse(e.to_string())),
    }
}

/// `for k, v in DEFAULTS.items(): if k not in settings: settings[k] = v`. Existing keys keep their position and value; missing keys
/// are appended in `defaults` order. Returns the keys that were added.
pub fn fill_defaults(settings: &mut Map<String, Value>, defaults: &Map<String, Value>) -> Vec<String> {
    let mut added = Vec::new();
    for (k, v) in defaults {
        if !settings.contains_key(k) {
            settings.insert(k.clone(), v.clone());
            added.push(k.clone());
        }
    }
    added
}

/// `json.dumps(settings, indent=2) + "\n"`, the layout `_save_json_atomic` uses for every store except tracks.json.
pub fn serialize(settings: &Map<String, Value>) -> Result<String, StoreError> {
    dumps(&Value::Object(settings.clone()), &Style::app_default())
}

/// `_JSON_WRITE_DIGESTS`: remembers the payload last written per path so an unchanged file is not rewritten. Only equality matters, so
/// a process-local 64-bit hash replaces Python's SHA-1.
#[derive(Default)]
pub struct WriteGate {
    digests: HashMap<String, u64>,
}

fn digest(payload: &str) -> u64 {
    let mut h = DefaultHasher::new();
    payload.hash(&mut h);
    h.finish()
}

impl WriteGate {
    pub fn new() -> Self {
        Self::default()
    }
    /// `_JSON_WRITE_DIGESTS.get(key) == digest and path.exists()` means skip.
    pub fn should_write(&self, key: &str, payload: &str, file_exists: bool) -> bool {
        !(file_exists && self.digests.get(key) == Some(&digest(payload)))
    }
    pub fn record(&mut self, key: &str, payload: &str) {
        self.digests.insert(key.to_string(), digest(payload));
    }
    pub fn forget(&mut self, key: &str) {
        self.digests.remove(key);
    }
}

static TMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// How durability is requested, injectable so tests can observe the ORDER of events (the property that matters on a power cut).
pub trait Syncer {
    /// Flush the file's data to the drive. Must run after the payload is fully written and BEFORE the rename.
    fn sync_file(&self, file: &fs::File) -> std::io::Result<()>;
    /// Flush the directory entry so the rename itself survives. Runs AFTER the rename; failures are non-fatal (the data is already safe).
    fn sync_dir(&self, dir: &Path) -> std::io::Result<()>;
}

/// The real thing. On macOS Rust's `File::sync_all` issues `fcntl(F_FULLFSYNC)`, which asks the drive to flush its own cache too; plain
/// `fsync(2)` on macOS only hands the data to the drive, so a power cut can still lose it. (Measured on the dev Mac: about 10 ms.)
pub struct DurableSync;

impl Syncer for DurableSync {
    fn sync_file(&self, file: &fs::File) -> std::io::Result<()> {
        file.sync_all()
    }
    fn sync_dir(&self, dir: &Path) -> std::io::Result<()> {
        fs::File::open(dir)?.sync_all()
    }
}

/// `tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=...)` + write + `os.replace`: the destination is either the old file or
/// the complete new one, never a partial write, and the final file is mode 0600 (what mkstemp gives Python). A failed write removes
/// its temp file and leaves the destination untouched.
pub fn write_atomic(path: &Path, payload: &str) -> std::io::Result<()> {
    write_atomic_with(path, payload, &DurableSync)
}

/// `write_atomic` with the durability steps made explicit: write, flush file to the drive (error = the save failed and the old file is
/// untouched), rename, then flush the directory (best effort). A power cut therefore leaves either the complete old file or the
/// complete new one, never an empty or partial file, and a completed save is not lost.
pub fn write_atomic_with(path: &Path, payload: &str, syncer: &dyn Syncer) -> std::io::Result<()> {
    let dir = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(dir)?;
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("store");
    let tmp = dir.join(format!(".{name}.{}-{}.tmp", std::process::id(), TMP_COUNTER.fetch_add(1, Ordering::Relaxed)));
    let result = (|| {
        let mut opts = fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut f = opts.open(&tmp)?;
        f.write_all(payload.as_bytes())?;
        f.flush()?;
        syncer.sync_file(&f)?;
        fs::rename(&tmp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
        return result;
    }
    let _ = syncer.sync_dir(dir);
    result
}

#[derive(Debug, PartialEq, Eq)]
pub enum SaveOutcome {
    Written,
    /// The payload equals what was last written to this path and the file still exists.
    Skipped,
}

/// `_save_json_atomic(path, settings)`: serialize, skip if unchanged, else write atomically and remember the digest.
pub fn save(gate: &mut WriteGate, path: &Path, settings: &Map<String, Value>) -> Result<SaveOutcome, String> {
    let payload = serialize(settings).map_err(|e| e.to_string())?;
    let key = path.to_string_lossy().to_string();
    if !gate.should_write(&key, &payload, path.exists()) {
        return Ok(SaveOutcome::Skipped);
    }
    match write_atomic(path, &payload) {
        Ok(()) => {
            gate.record(&key, &payload);
            Ok(SaveOutcome::Written)
        }
        Err(e) => {
            gate.forget(&key);
            Err(e.to_string())
        }
    }
}

/// JSON bridge for the Python shadow hook. Requests: `{"op":"parse","text":..}`, `{"op":"fill_defaults","settings":{..},"defaults":{..}}`,
/// `{"op":"serialize","settings":{..}}`. Responses are JSON objects; key order inside settings is preserved.
pub fn settings_bridge(request_json: &str) -> Result<String, String> {
    let req: Value = serde_json::from_str(request_json).map_err(|e| format!("request: {e}"))?;
    let op = req.get("op").and_then(Value::as_str).ok_or("missing op")?;
    let obj = |name: &str| -> Result<Map<String, Value>, String> {
        req.get(name).and_then(Value::as_object).cloned().ok_or_else(|| format!("missing object `{name}`"))
    };
    let out = match op {
        "parse" => {
            let text = req.get("text").and_then(Value::as_str).ok_or("missing text")?;
            match parse_settings(text) {
                Ok(map) => serde_json::json!({ "ok": true, "settings": Value::Object(map) }),
                Err(LoadIssue::Parse(m)) => serde_json::json!({ "ok": false, "issue": "parse", "detail": m }),
                Err(LoadIssue::NotObject) => serde_json::json!({ "ok": false, "issue": "not_object" }),
                Err(LoadIssue::Unsupported(m)) => serde_json::json!({ "ok": false, "issue": "unsupported", "detail": m }),
            }
        }
        "fill_defaults" => {
            let mut settings = obj("settings")?;
            let added = fill_defaults(&mut settings, &obj("defaults")?);
            serde_json::json!({ "settings": Value::Object(settings), "added": added })
        }
        "serialize" => {
            let payload = serialize(&obj("settings")?).map_err(|e| e.to_string())?;
            serde_json::json!({ "payload": payload })
        }
        other => return Err(format!("unknown op `{other}`")),
    };
    serde_json::to_string(&out).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn map(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    /// A private temp directory; every test that touches the filesystem uses one, never the real profile.
    struct TempDir(std::path::PathBuf);
    impl TempDir {
        fn new(tag: &str) -> Self {
            let p = std::env::temp_dir().join(format!("singws-store-test-{tag}-{}-{}", std::process::id(), TMP_COUNTER.fetch_add(1, Ordering::Relaxed)));
            fs::create_dir_all(&p).unwrap();
            assert!(!p.starts_with(std::env::var("HOME").unwrap_or_default() + "/SingWS"), "tests must never touch the live profile");
            TempDir(p)
        }
        fn path(&self, name: &str) -> std::path::PathBuf {
            self.0.join(name)
        }
    }
    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn parse_follows_the_load_json_file_rules() {
        assert_eq!(parse_settings(r#"{"a":1,"b":[true]}"#).unwrap(), map(json!({"a":1,"b":[true]})));
        assert_eq!(parse_settings("[1,2]"), Err(LoadIssue::NotObject));
        assert_eq!(parse_settings("\"text\""), Err(LoadIssue::NotObject));
        assert_eq!(parse_settings("null"), Err(LoadIssue::NotObject));
        assert!(matches!(parse_settings("{"), Err(LoadIssue::Parse(_))));
        assert!(matches!(parse_settings(""), Err(LoadIssue::Parse(_))));
        assert!(matches!(parse_settings(r#"{"big":18446744073709551616}"#), Err(LoadIssue::Unsupported(_))));
    }

    #[test]
    fn fill_defaults_keeps_existing_values_and_order_and_appends_missing_in_defaults_order() {
        let mut s = map(json!({"zeta": 1, "alpha": "mine", "bg_volume": 0.3}));
        let defaults = map(json!({"alpha": "default", "bg_volume": 0.8, "new_b": true, "zeta": 0, "new_a": [1]}));
        let added = fill_defaults(&mut s, &defaults);
        assert_eq!(added, vec!["new_b", "new_a"]);
        assert_eq!(s.keys().collect::<Vec<_>>(), vec!["zeta", "alpha", "bg_volume", "new_b", "new_a"]);
        assert_eq!(s["alpha"], json!("mine"));
        assert_eq!(s["bg_volume"], json!(0.3));
        // idempotent: a second fill adds nothing
        assert!(fill_defaults(&mut s, &defaults).is_empty());
    }

    #[test]
    fn a_falsy_saved_value_is_not_replaced_by_the_default() {
        let mut s = map(json!({"a": false, "b": 0, "c": "", "d": null, "e": []}));
        let defaults = map(json!({"a": true, "b": 5, "c": "x", "d": 1, "e": [1]}));
        assert!(fill_defaults(&mut s, &defaults).is_empty());
        assert_eq!(s["d"], Value::Null);
    }

    #[test]
    fn serialize_is_the_apps_layout() {
        let s = map(json!({"b": [1, 2.5], "a": "é", "c": {}}));
        assert_eq!(serialize(&s).unwrap(), "{\n  \"b\": [\n    1,\n    2.5\n  ],\n  \"a\": \"\\u00e9\",\n  \"c\": {}\n}\n");
        assert_eq!(serialize(&Map::new()).unwrap(), "{}\n");
    }

    #[test]
    fn write_gate_skips_only_an_unchanged_payload_for_an_existing_file() {
        let mut g = WriteGate::new();
        assert!(g.should_write("k", "p1", true));
        g.record("k", "p1");
        assert!(!g.should_write("k", "p1", true), "same payload, file present: skip");
        assert!(g.should_write("k", "p1", false), "file deleted behind our back: rewrite");
        assert!(g.should_write("k", "p2", true), "changed payload: write");
        assert!(g.should_write("other", "p1", true), "digests are per path");
        g.forget("k");
        assert!(g.should_write("k", "p1", true));
    }

    #[test]
    fn save_writes_atomically_skips_repeats_and_rewrites_after_a_change_or_deletion() {
        let dir = TempDir::new("save");
        let path = dir.path("settings.json");
        let mut gate = WriteGate::new();
        let mut s = map(json!({"a": 1}));
        assert_eq!(save(&mut gate, &path, &s).unwrap(), SaveOutcome::Written);
        assert_eq!(fs::read_to_string(&path).unwrap(), "{\n  \"a\": 1\n}\n");
        assert_eq!(save(&mut gate, &path, &s).unwrap(), SaveOutcome::Skipped);
        s.insert("b".into(), json!(true));
        assert_eq!(save(&mut gate, &path, &s).unwrap(), SaveOutcome::Written);
        fs::remove_file(&path).unwrap();
        assert_eq!(save(&mut gate, &path, &s).unwrap(), SaveOutcome::Written);
        let leftovers: Vec<_> = fs::read_dir(&dir.0).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string()).collect();
        assert_eq!(leftovers, vec!["settings.json"], "no temp files left behind");
    }

    /// Records what the destination looked like at each durability step, to prove the ORDER: data is flushed after it is fully written
    /// and before the rename (destination still old), and the directory is flushed after the rename (destination already new).
    struct Recording {
        dest: std::path::PathBuf,
        events: std::cell::RefCell<Vec<String>>,
        fail_file: bool,
        fail_dir: bool,
    }
    impl Syncer for Recording {
        fn sync_file(&self, file: &fs::File) -> std::io::Result<()> {
            let dest = fs::read_to_string(&self.dest).unwrap_or_else(|_| "<none>".into());
            self.events.borrow_mut().push(format!("file(len={}, dest={})", file.metadata().unwrap().len(), dest.trim()));
            if self.fail_file { Err(std::io::Error::new(std::io::ErrorKind::Other, "EIO")) } else { Ok(()) }
        }
        fn sync_dir(&self, _dir: &Path) -> std::io::Result<()> {
            let dest = fs::read_to_string(&self.dest).unwrap_or_else(|_| "<none>".into());
            self.events.borrow_mut().push(format!("dir(dest={})", dest.trim()));
            if self.fail_dir { Err(std::io::Error::new(std::io::ErrorKind::Other, "ENOTSUP")) } else { Ok(()) }
        }
    }
    fn recording(dest: &Path, fail_file: bool, fail_dir: bool) -> Recording {
        Recording { dest: dest.to_path_buf(), events: Default::default(), fail_file, fail_dir }
    }

    #[test]
    fn durability_steps_happen_in_the_order_that_survives_a_power_cut() {
        let dir = TempDir::new("order");
        let path = dir.path("settings.json");
        fs::write(&path, "OLD").unwrap();
        let rec = recording(&path, false, false);
        write_atomic_with(&path, "NEW-CONTENT", &rec).unwrap();
        assert_eq!(*rec.events.borrow(), vec!["file(len=11, dest=OLD)".to_string(), "dir(dest=NEW-CONTENT)".to_string()]);
        assert_eq!(fs::read_to_string(&path).unwrap(), "NEW-CONTENT");
    }

    #[test]
    fn a_failed_data_flush_fails_the_save_and_leaves_the_old_file_untouched() {
        let dir = TempDir::new("eio");
        let path = dir.path("settings.json");
        fs::write(&path, "OLD").unwrap();
        let rec = recording(&path, true, false);
        assert!(write_atomic_with(&path, "NEW", &rec).is_err());
        assert_eq!(fs::read_to_string(&path).unwrap(), "OLD");
        assert_eq!(rec.events.borrow().len(), 1, "the directory is not flushed after a failed save");
        let names: Vec<_> = fs::read_dir(&dir.0).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string()).collect();
        assert_eq!(names, vec!["settings.json"], "temp file removed");
    }

    #[test]
    fn a_failed_directory_flush_does_not_fail_a_save_whose_data_is_already_safe() {
        let dir = TempDir::new("dirfail");
        let path = dir.path("settings.json");
        let rec = recording(&path, false, true);
        write_atomic_with(&path, "NEW", &rec).unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "NEW");
    }

    #[test]
    fn the_real_syncer_works_on_a_real_file_and_directory() {
        let dir = TempDir::new("real");
        let path = dir.path("settings.json");
        write_atomic(&path, "{}\n").unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "{}\n");
        let f = fs::File::open(&path).unwrap();
        DurableSync.sync_file(&f).unwrap();
        DurableSync.sync_dir(&dir.0).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn the_written_file_is_private_like_pythons_mkstemp() {
        use std::os::unix::fs::PermissionsExt;
        let dir = TempDir::new("mode");
        let path = dir.path("settings.json");
        write_atomic(&path, "{}\n").unwrap();
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn a_failed_write_leaves_the_destination_untouched_and_cleans_up() {
        let dir = TempDir::new("fail");
        let path = dir.path("settings.json");
        fs::write(&path, "ORIGINAL").unwrap();
        // make the rename fail by turning the destination into a non-empty directory
        let blocked = dir.path("blocked.json");
        fs::create_dir_all(blocked.join("child")).unwrap();
        assert!(write_atomic(&blocked, "new").is_err());
        assert_eq!(fs::read_to_string(&path).unwrap(), "ORIGINAL");
        let names: Vec<_> = fs::read_dir(&dir.0).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string()).collect();
        assert!(names.iter().all(|n| !n.ends_with(".tmp")), "temp file removed after a failure: {names:?}");
        // and the gate does not remember a payload that never landed
        let mut gate = WriteGate::new();
        assert!(save(&mut gate, &blocked, &map(json!({"a": 1}))).is_err());
        assert!(gate.should_write(&blocked.to_string_lossy(), &serialize(&map(json!({"a": 1}))).unwrap(), true));
    }

    #[cfg(unix)]
    #[test]
    fn after_a_failed_save_the_same_payload_is_rewritten_like_pythons_digest_pop() {
        use std::os::unix::fs::PermissionsExt;
        let dir = TempDir::new("forget");
        let path = dir.path("settings.json");
        let mut gate = WriteGate::new();
        let p1 = map(json!({"a": 1}));
        assert_eq!(save(&mut gate, &path, &p1).unwrap(), SaveOutcome::Written);
        // a later, different save cannot create its temp file (directory made read-only)
        fs::set_permissions(&dir.0, fs::Permissions::from_mode(0o500)).unwrap();
        let failed = save(&mut gate, &path, &map(json!({"a": 2})));
        fs::set_permissions(&dir.0, fs::Permissions::from_mode(0o700)).unwrap();
        if failed.is_ok() {
            return; // running with privileges that ignore directory permissions (root): nothing to observe
        }
        assert_eq!(fs::read_to_string(&path).unwrap(), "{\n  \"a\": 1\n}\n", "a failed save never damages the existing file");
        // Python pops the digest on failure, so identical content is written again instead of being skipped
        assert_eq!(save(&mut gate, &path, &p1).unwrap(), SaveOutcome::Written);
    }

    #[test]
    fn creates_missing_parent_directories_like_the_app_does() {
        let dir = TempDir::new("mkdir");
        let path = dir.0.join("nested").join("deeper").join("settings.json");
        write_atomic(&path, "{}\n").unwrap();
        assert!(path.exists());
    }

    #[test]
    fn bridge_ops_round_trip_with_key_order() {
        let out = settings_bridge(r#"{"op":"parse","text":"{\"z\":1,\"a\":{\"k\":[1.5]}}"}"#).unwrap();
        assert_eq!(out, r#"{"ok":true,"settings":{"z":1,"a":{"k":[1.5]}}}"#);
        let out = settings_bridge(r#"{"op":"fill_defaults","settings":{"z":1},"defaults":{"a":2,"z":9}}"#).unwrap();
        assert!(out.contains(r#""added":["a"]"#) && out.contains(r#""settings":{"z":1,"a":2}"#));
        assert!(settings_bridge(r#"{"op":"serialize","settings":{"a":1}}"#).unwrap().contains("{\\n  \\\"a\\\": 1\\n}\\n"));
        assert!(settings_bridge(r#"{"op":"parse","text":"[1]"}"#).unwrap().contains("not_object"));
        assert!(settings_bridge(r#"{"op":"nope"}"#).is_err());
        assert!(settings_bridge("{").is_err());
    }
}
