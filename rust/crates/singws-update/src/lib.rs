//! The app's update decision rules (0.2.18.1.py `_version_key`, `_is_newer_version`, `_preferred_update_asset`,
//! `GitHubUpdateWorker._release_info` / `_manifest_info`) as pure functions. No network, no files, no clock.
//!
//! Python quirks reproduced on purpose: the version key splits on every character that is not an ASCII digit (so Unicode digits are
//! separators), numbers are unbounded, a run of more than 4300 digits fails `int()` in Python 3.11+ and counts as 0, the key is padded
//! to 4 parts and cut to 6, `str.strip()` uses Python's whitespace set, and `str(x or "")` of a non-string JSON value is not modelled
//! (the oracle corpus feeds strings, null or missing).
use serde_json::{json, Map, Value};
use std::cmp::Ordering;

/// One numeric part of a version: digits without leading zeros (so length then text orders like the integer).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Num(String);

impl Ord for Num {
    fn cmp(&self, o: &Self) -> Ordering {
        self.0.len().cmp(&o.0.len()).then_with(|| self.0.cmp(&o.0))
    }
}
impl PartialOrd for Num {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}

fn num(digits: &str) -> Num {
    if digits.len() > 4300 {
        return Num("0".into()); // int() raises ValueError above the default max_str_digits; the app falls back to 0
    }
    let t = digits.trim_start_matches('0');
    Num(if t.is_empty() { "0".into() } else { t.into() })
}

fn lstrip_v(s: &str) -> &str {
    s.trim_start_matches(|c| c == 'v' || c == 'V')
}

pub fn version_key(value: &str) -> Vec<Num> {
    let mut parts: Vec<Num> = lstrip_v(value).split(|c: char| !c.is_ascii_digit()).filter(|p| !p.is_empty()).map(num).collect();
    while parts.len() < 4 {
        parts.push(Num("0".into()));
    }
    parts.truncate(6);
    parts
}

pub fn is_newer(remote: &str, current: &str) -> bool {
    version_key(remote) > version_key(current)
}

/// `str.isspace()` of Python 3 for one character.
fn py_space(c: char) -> bool {
    matches!(c, '\t' | '\n' | '\u{0b}' | '\u{0c}' | '\r' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{1f}' | ' ' | '\u{85}' | '\u{a0}' | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}

pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// `str(v or "")` for strings, null and missing.
fn text(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

fn truthy_text(v: Option<&Value>) -> Option<String> {
    match v {
        Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
        _ => None,
    }
}

fn machine_terms(platform: &str, machine: &str) -> Vec<&'static str> {
    if platform != "darwin" {
        return vec![];
    }
    match machine.to_lowercase().as_str() {
        "arm64" | "aarch64" => vec!["arm64", "universal"],
        "x86_64" | "amd64" | "i386" | "i686" => vec!["x86_64", "intel", "universal"],
        _ => vec![],
    }
}

pub fn preferred_asset(assets: &[Value], platform: &str, machine: &str) -> Option<Value> {
    let dmg: Vec<&Value> = assets.iter().filter(|a| text(a.get("name")).to_lowercase().ends_with(".dmg")).collect();
    if dmg.is_empty() {
        return None;
    }
    for term in machine_terms(platform, machine) {
        for a in &dmg {
            if text(a.get("name")).to_lowercase().contains(term) {
                return Some((*a).clone());
            }
        }
    }
    Some(dmg[0].clone())
}

pub fn release_info(release: &Value, current: &str, platform: &str, machine: &str) -> Value {
    let tag = py_strip(&truthy_text(release.get("tag_name")).or_else(|| truthy_text(release.get("name"))).unwrap_or_default()).to_string();
    let version = lstrip_v(&tag).to_string();
    let assets: Vec<Value> = release.get("assets").and_then(Value::as_array).cloned().unwrap_or_default();
    let asset = preferred_asset(&assets, platform, machine);
    let available = !version.is_empty() && is_newer(&version, current) && asset.is_some();
    let name = truthy_text(release.get("name")).or_else(|| if tag.is_empty() { None } else { Some(tag.clone()) }).unwrap_or_else(|| version.clone());
    json!({
        "available": available, "current_version": current, "version": version, "tag": tag, "name": name,
        "html_url": text(release.get("html_url")), "published_at": text(release.get("published_at")),
        "asset": asset.unwrap_or_else(|| json!({})),
    })
}

pub fn manifest_info(manifest: &Value, current: &str, platform: &str, machine: &str) -> Value {
    let version = lstrip_v(py_strip(&text(manifest.get("version")))).to_string();
    let downloads = manifest.get("downloads").and_then(Value::as_object).cloned().unwrap_or_default();
    let mut keys: Vec<&str> = Vec::new();
    if platform == "darwin" {
        match machine.to_lowercase().as_str() {
            "arm64" | "aarch64" => keys = vec!["mac_arm64", "mac_universal"],
            "x86_64" | "amd64" | "i386" | "i686" => keys = vec!["mac_x86_64", "mac_universal"],
            _ => {}
        }
    }
    keys.push("mac_universal");
    let mut asset = Value::Object(Map::new());
    for key in keys {
        if let Some(Value::Object(item)) = downloads.get(key) {
            if let Some(url) = truthy_text(item.get("url")) {
                let sha = truthy_text(item.get("sha256"));
                asset = json!({
                    "name": truthy_text(item.get("filename")).unwrap_or_else(|| format!("SingWS-{version}.dmg")),
                    "browser_download_url": url,
                    "digest": sha.map(|s| format!("sha256:{s}")).unwrap_or_default(),
                });
                break;
            }
        }
    }
    let has_asset = asset.as_object().map(|o| !o.is_empty()).unwrap_or(false);
    let available = !version.is_empty() && is_newer(&version, current) && has_asset;
    json!({
        "available": available, "current_version": current, "version": version,
        "tag": if version.is_empty() { String::new() } else { format!("v{version}") },
        "name": truthy_text(manifest.get("name")).unwrap_or_else(|| format!("SingWS {version}")),
        "html_url": text(manifest.get("release_url")), "published_at": text(manifest.get("release_date")),
        "asset": asset, "source": "manifest",
    })
}

/// Request: `{"op": "version_key"|"is_newer"|"preferred_asset"|"release_info"|"manifest_info", ...}`; `platform` ("darwin"...) and `machine`
/// are inputs because the Python functions read them from the running machine.
pub fn handle(req: &Value) -> Value {
    let s = |k: &str| text(req.get(k));
    let platform = s("platform");
    let machine = s("machine");
    match req.get("op").and_then(Value::as_str).unwrap_or("") {
        "version_key" => json!({"key": version_key(&s("value")).iter().map(|n| n.0.clone()).collect::<Vec<_>>()}),
        "is_newer" => json!({"newer": is_newer(&s("remote"), &s("current"))}),
        "preferred_asset" => {
            let assets: Vec<Value> = req.get("assets").and_then(Value::as_array).cloned().unwrap_or_default();
            json!({"asset": preferred_asset(&assets, &platform, &machine)})
        }
        "release_info" => release_info(req.get("release").unwrap_or(&Value::Null), &s("current"), &platform, &machine),
        "manifest_info" => manifest_info(req.get("manifest").unwrap_or(&Value::Null), &s("current"), &platform, &machine),
        other => json!({"error": format!("unknown op {other}")}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_pad_cut_and_order() {
        assert!(is_newer("1.0.2.8", "1.0.2.7"));
        assert!(!is_newer("v1.0.2.7", "1.0.2.7"));
        assert!(is_newer("1.0.0.0.0.1", "1.0.0.0.0.0"));
        assert!(!is_newer("1.0.0.0.0.0.9", "1.0.0.0.0.0")); // 7th part is cut
        assert_eq!(version_key("").len(), 4);
        assert!(is_newer("10", "9.9.9.9"));
    }

    #[test]
    fn huge_digit_runs_count_as_zero() {
        assert_eq!(version_key(&"9".repeat(4301))[0], Num("0".into()));
        assert_eq!(version_key(&"9".repeat(4300))[0].0.len(), 4300);
    }

    #[test]
    fn asset_choice_by_machine() {
        let a = vec![json!({"name": "x-x86_64.dmg"}), json!({"name": "x-arm64.dmg"}), json!({"name": "notes.txt"})];
        assert_eq!(preferred_asset(&a, "darwin", "arm64").unwrap()["name"], "x-arm64.dmg");
        assert_eq!(preferred_asset(&a, "darwin", "AMD64").unwrap()["name"], "x-x86_64.dmg");
        assert_eq!(preferred_asset(&a, "darwin", "ppc").unwrap()["name"], "x-x86_64.dmg");
        assert!(preferred_asset(&[json!({"name": "a.zip"})], "darwin", "arm64").is_none());
    }
}
