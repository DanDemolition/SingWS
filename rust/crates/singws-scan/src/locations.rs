//! Library roots and locations: `_normalize_library_location_path`, `_normalize_scanned_library_path`, `_migrate_library_locations`
//! (the part the scan reads), `_collapse_overlapping_library_roots`, `_library_location_for_path`, `_path_is_within_library`.

use crate::pyfs::*;
use serde_json::Value;
use sha1::{Digest, Sha1};

pub struct Env {
    pub cwd: String,
}

pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(value or "")` for a path-like field: text stays, null/false/empty become "", anything else would need Python's `str()` and is declined.
pub fn text_or_empty(v: Option<&Value>) -> R<String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Bool(false)) => Ok(String::new()),
        Some(Value::Number(n)) if n.as_f64() == Some(0.0) => Ok(String::new()),
        Some(Value::Array(a)) if a.is_empty() => Ok(String::new()),
        Some(Value::Object(o)) if o.is_empty() => Ok(String::new()),
        Some(_) => decline("a path field that is not text"),
    }
}

/// `_normalize_library_location_path`: expanduser, abspath, realpath, normpath.
pub fn normalize_location_path(path: &str, env: &Env) -> R<String> {
    let value = expanduser(py_strip(path))?;
    if value.is_empty() {
        return Ok(String::new());
    }
    let abs = abspath(&value, &env.cwd);
    Ok(normpath(&realpath(&abs, &env.cwd)?))
}

/// `_normalize_scanned_library_path`: expanduser, abspath, normpath (no symlink resolution).
pub fn normalize_scanned_path(path: &str, env: &Env) -> R<String> {
    let value = expanduser(py_strip(path))?;
    if value.is_empty() {
        return Ok(String::new());
    }
    Ok(normpath(&abspath(&value, &env.cwd)))
}

/// `_scanned_library_path_key` (`os.path.normcase` is the identity on POSIX).
pub fn scanned_key(path: &str, env: &Env) -> R<String> {
    normalize_scanned_path(path, env)
}

pub fn location_key(path: &str, env: &Env) -> R<String> {
    normalize_location_path(path, env)
}

pub fn is_under(key: &str, root_key: &str) -> bool {
    key == root_key || (key.starts_with(root_key) && key[root_key.len()..].starts_with('/'))
}

/// `_path_is_within_library`.
pub fn path_is_within_library(path: &str, root: &str, env: &Env) -> R<bool> {
    let p = location_key(path, env)?;
    let r = location_key(root, env)?;
    Ok(!p.is_empty() && !r.is_empty() && is_under(&p, &r))
}

#[derive(Clone, Debug)]
pub struct Location {
    pub id: String,
    pub path: String,
}

fn sha1_16(identity: &str) -> String {
    let digest = Sha1::digest(identity.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect::<String>()[..16].to_string()
}

/// The locations `_migrate_library_locations(settings)` returns (id and path are all the scan reads).
pub fn migrate_locations(settings: &Value, env: &Env) -> R<Vec<Location>> {
    let get = |k: &str| settings.get(k).cloned().unwrap_or(Value::Null);
    let already_migrated = truthy(&get("karaoke_library_locations_migrated"));
    let mut candidates: Vec<Value> = Vec::new();
    for key in ["karaoke_library_locations", "karaoke_scan_roots"] {
        let v = get(key);
        if !truthy(&v) {
            continue; // `x or []`
        }
        match v {
            Value::Array(a) => candidates.extend(a),
            other => candidates.push(other),
        }
    }
    if !already_migrated {
        for key in ["karaoke_folder", "karaoke_library_folder", "library_folder", "library_path"] {
            let v = get(key);
            if truthy(&v) {
                candidates.push(v);
            }
        }
    }
    let mut out: Vec<Location> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for candidate in &candidates {
        let (path_value, id_value) = match candidate {
            Value::Object(o) => (o.get("path").cloned().unwrap_or(Value::String(String::new())), o.get("id").cloned()),
            other => (other.clone(), None),
        };
        let raw = text_or_empty(Some(&path_value))?;
        let path = if already_migrated { normalize_scanned_path(&raw, env)? } else { normalize_location_path(&raw, env)? };
        let identity = if already_migrated { scanned_key(&path, env)? } else { location_key(&path, env)? };
        if identity.is_empty() || seen.contains(&identity) {
            continue;
        }
        seen.push(identity.clone());
        let id = match id_value {
            Some(v) if truthy(&v) => match v {
                Value::String(s) => s,
                _ => return decline("a location id that is not text"),
            },
            _ => format!("library-{}", sha1_16(&identity)),
        };
        out.push(Location { id, path });
    }
    Ok(out)
}

/// `_collapse_overlapping_library_roots`.
pub fn collapse_roots(roots: &[String], env: &Env) -> R<Vec<String>> {
    let mut unique: Vec<String> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for root in roots {
        let normalized = normalize_location_path(root, env)?;
        let key = scanned_key(&normalized, env)?;
        if !normalized.is_empty() && !seen.contains(&key) {
            seen.push(key);
            unique.push(normalized);
        }
    }
    let mut keyed: Vec<(usize, String, String)> = Vec::new();
    for v in unique {
        let key = scanned_key(&v, env)?;
        keyed.push((key.chars().count(), v.clone(), key));
    }
    // sorted(key=(len(key), value)): the length counts characters; strings compare by code point (= UTF-8 byte order).
    keyed.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    let mut kept: Vec<String> = Vec::new();
    let mut kept_keys: Vec<String> = Vec::new();
    for (_, root, key) in keyed {
        if kept_keys.iter().any(|parent| is_under(&key, parent)) {
            continue;
        }
        kept_keys.push(key);
        kept.push(root);
    }
    Ok(kept)
}

/// `_library_location_for_path`: the matching location with the longest path (first wins a tie).
pub fn location_for_path<'a>(path: &str, locations: &'a [Location], env: &Env) -> R<Option<&'a Location>> {
    let path_key = scanned_key(path, env)?;
    let mut best: Option<&Location> = None;
    for l in locations {
        let root_key = scanned_key(&l.path, env)?;
        if !path_key.is_empty() && !root_key.is_empty() && is_under(&path_key, &root_key) {
            if best.map(|b| l.path.chars().count() > b.path.chars().count()).unwrap_or(true) {
                best = Some(l);
            }
        }
    }
    Ok(best)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn env() -> Env {
        Env { cwd: "/".into() }
    }

    #[test]
    fn sha1_id_matches_known_digest() {
        // hashlib.sha1(b"abc").hexdigest() = a9993e364706816aba3e25717850c26c9cd0d89d
        assert_eq!(sha1_16("abc"), "a9993e364706816a");
    }

    #[test]
    fn under_needs_a_separator() {
        assert!(is_under("/a/b", "/a"));
        assert!(is_under("/a", "/a"));
        assert!(!is_under("/ab", "/a"));
    }

    #[test]
    fn locations_dedupe_and_take_legacy_keys_until_migrated() {
        let s = json!({"karaoke_library_locations": [{"path": "/tmp/zz-none", "id": "keep"}], "karaoke_folder": "/tmp/zz-none"});
        let l = migrate_locations(&s, &env()).unwrap();
        assert_eq!(l.len(), 1);
        assert_eq!(l[0].id, "keep");
        let s = json!({"karaoke_library_locations_migrated": true, "karaoke_library_locations": ["/m/a", "/m/a/"], "karaoke_folder": "/m/b"});
        let l = migrate_locations(&s, &env()).unwrap();
        assert_eq!(l.iter().map(|x| x.path.as_str()).collect::<Vec<_>>(), vec!["/m/a"]);
        assert!(l[0].id.starts_with("library-"));
    }

    #[test]
    fn collapse_drops_children_and_orders_by_key_length() {
        let roots = vec!["/m/a/b".to_string(), "/m/a".to_string(), "/m/c".to_string(), "/m/a".to_string()];
        assert_eq!(collapse_roots(&roots, &env()).unwrap(), vec!["/m/a".to_string(), "/m/c".to_string()]);
    }

    #[test]
    fn non_text_ids_are_declined() {
        let s = json!({"karaoke_library_locations": [{"path": "/m", "id": 5}]});
        assert!(migrate_locations(&s, &env()).is_err());
    }
}
