//! `voice_audio_fallback.py`: the pure rules around converting a sound the Rust engine cannot open. The converter itself (`afconvert`) and the disk stay in Python.
use sha1::{Digest, Sha1};

pub const KEEP_FILES: usize = 12;
pub const WAV_HEADER_BYTES: u64 = 44;
pub const STALE_TMP_SECONDS: f64 = 3600.0;
pub const AFCONVERT: &str = "/usr/bin/afconvert";

/// The cache file's name stem: the first 20 hex digits of sha1 of "<resolved path>|<size>|<mtime seconds>".
pub fn cache_key(resolved_path: &str, size: u64, mtime_seconds: i64) -> String {
    let mut h = Sha1::new();
    h.update(format!("{resolved_path}|{size}|{mtime_seconds}").as_bytes());
    let hex: String = h.finalize().iter().map(|b| format!("{b:02x}")).collect();
    hex[..20].to_string()
}

/// An existing copy is used when it is larger than a bare WAV header.
pub fn copy_is_usable(exists: bool, size: u64) -> bool {
    exists && size > WAV_HEADER_BYTES
}

/// The command line handed to the system converter.
pub fn afconvert_args(src: &str, tmp: &str) -> Vec<String> {
    vec![AFCONVERT.into(), "-f".into(), "WAVE".into(), "-d".into(), "LEI16".into(), src.into(), tmp.into()]
}

/// A conversion counts as done only when the converter exited 0 and left a file larger than a bare header.
pub fn conversion_ok(returncode: i64, tmp_exists: bool, tmp_size: u64) -> bool {
    returncode == 0 && tmp_exists && tmp_size > WAV_HEADER_BYTES
}

/// `_prune`: which cached copies to delete. `wavs` = (name, mtime) of "*.wav" files (hidden ones excluded by the caller's listing rule is repeated here), `tmps` = (name, mtime)
/// of ".*.tmp.wav" files. Newest KEEP_FILES kept (ties keep their listing order, like Python's stable sort); temporary files older than an hour are removed.
pub fn prune_plan(wavs: &[(String, f64)], tmps: &[(String, f64)], now: f64) -> Vec<String> {
    let mut files: Vec<&(String, f64)> = wavs.iter().filter(|(n, _)| !n.starts_with('.')).collect();
    files.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));   // stable: equal times keep order
    let mut gone: Vec<String> = files.iter().skip(KEEP_FILES).map(|(n, _)| n.clone()).collect();
    for (n, m) in tmps {
        if now - m > STALE_TMP_SECONDS {
            gone.push(n.clone());
        }
    }
    gone
}
