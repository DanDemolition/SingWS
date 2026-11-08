//! `audit_zip_duplicates` (duplicate_song_audit.py): exact-content duplicate review for MP3+G ZIPs. The central directory narrows the catalog (CRC and size of the one MP3 member);
//! SHA-256 of the MP3 and of the CDG member then decides, and only identical audio AND graphics are removal-eligible. Pure reading, no file is changed.
//! Declined (never guessed): non-ASCII paths or member names (Python's name decoding and `casefold()`), compression other than stored/deflate/deflate64.
use crate::pyfs::*;
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashMap};
use std::io::Read;

// ---- SHA-256 (FIPS 180-4), so the crate needs no new dependency
const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

pub struct Sha256 {
    h: [u32; 8],
    buf: Vec<u8>,
    len: u64,
}

impl Sha256 {
    pub fn new() -> Self {
        Sha256 { h: [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19], buf: Vec::with_capacity(64), len: 0 }
    }
    fn block(&mut self, b: &[u8]) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([b[i * 4], b[i * 4 + 1], b[i * 4 + 2], b[i * 4 + 3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let mut v = self.h;
        for i in 0..64 {
            let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
            let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
            let t1 = v[7].wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
            let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
            let t2 = s0.wrapping_add(maj);
            v = [t1.wrapping_add(t2), v[0], v[1], v[2], v[3].wrapping_add(t1), v[4], v[5], v[6]];
        }
        for i in 0..8 {
            self.h[i] = self.h[i].wrapping_add(v[i]);
        }
    }
    pub fn update(&mut self, data: &[u8]) {
        self.len += data.len() as u64;
        self.buf.extend_from_slice(data);
        while self.buf.len() >= 64 {
            let chunk: Vec<u8> = self.buf.drain(..64).collect();
            self.block(&chunk);
        }
    }
    pub fn hex(mut self) -> String {
        let bit_len = self.len * 8;
        self.buf.push(0x80);
        while self.buf.len() % 64 != 56 {
            self.buf.push(0);
        }
        self.buf.extend_from_slice(&bit_len.to_be_bytes());
        let rest = std::mem::take(&mut self.buf);
        for chunk in rest.chunks(64) {
            self.block(chunk);
        }
        self.h.iter().map(|x| format!("{x:08x}")).collect()
    }
}

struct Row {
    path: String,
    audio_idx: usize,
    cdg_idx: Option<usize>,
    audio_name: String,
}

struct Opened {
    names: Vec<String>,
    is_dir: Vec<bool>,
    crc: Vec<u32>,
    size: Vec<u64>,
}

fn open_dir(path: &str) -> R<Option<(zip::ZipArchive<std::fs::File>, Opened)>> {
    let Ok(file) = std::fs::File::open(path) else { return Ok(None) };
    let Ok(mut zf) = zip::ZipArchive::new(file) else { return Ok(None) };
    let mut o = Opened { names: vec![], is_dir: vec![], crc: vec![], size: vec![] };
    for i in 0..zf.len() {
        let Ok(f) = zf.by_index_raw(i) else { return Ok(None) };
        let raw = f.name_raw();
        if !raw.is_ascii() {
            return decline("a member name that is not ASCII");
        }
        if !matches!(f.compression(), zip::CompressionMethod::Stored | zip::CompressionMethod::Deflated | zip::CompressionMethod::Deflate64) {
            return decline("a compression method other than stored, deflate or deflate64");
        }
        let name = String::from_utf8_lossy(raw).to_string();
        o.is_dir.push(name.ends_with('/'));
        o.names.push(name);
        o.crc.push(f.crc32());
        o.size.push(f.size());
    }
    Ok(Some((zf, o)))
}

/// `_member(zf, suffix)`: the one non-directory member whose lower-cased name ends with `suffix`, else None.
fn member(o: &Opened, suffix: &str) -> Option<usize> {
    let rows: Vec<usize> = (0..o.names.len()).filter(|&i| !o.is_dir[i] && o.names[i].to_lowercase().ends_with(suffix)).collect();
    if rows.len() == 1 { Some(rows[0]) } else { None }
}

/// `_member_sha256(path, member_name)` by index: Err on anything Python's `zipfile` would raise for (bad CRC, damaged data, encryption).
fn member_sha(path: &str, idx: usize) -> Result<String, ()> {
    let file = std::fs::File::open(path).map_err(|_| ())?;
    let mut zf = zip::ZipArchive::new(file).map_err(|_| ())?;
    let mut f = zf.by_index(idx).map_err(|_| ())?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf).map_err(|_| ())?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.hex())
}

fn stem_of_name(name: &str) -> String {
    match name.rfind('.') {
        Some(i) if i > 0 => name[..i].to_string(),
        _ => name.to_string(),
    }
}

fn keeper_key(path: &str) -> (i32, usize, usize, String) {
    let name = path.rsplit('/').next().unwrap_or(path);
    let stem = stem_of_name(name).to_lowercase();
    let hint = [" copy", "duplicate", " dup", "(1)", "_1"].iter().any(|t| stem.contains(t)) as i32;
    (hint, name.chars().count(), path.chars().count(), path.to_lowercase())
}

pub fn audit(paths: &[String]) -> R<Value> {
    let mut unique: Vec<String> = paths.iter().filter(|p| p.to_lowercase().ends_with(".zip")).cloned().collect();
    unique.sort();
    unique.dedup();
    if unique.iter().any(|p| !p.is_ascii()) {
        return decline("a path that is not ASCII (casefold is not modelled)");
    }
    let mut candidates: BTreeMap<(u32, u64), Vec<Row>> = BTreeMap::new();
    let mut order: Vec<(u32, u64)> = vec![];
    let mut unreadable = 0i64;
    for path in &unique {
        let Some((_zf, o)) = open_dir(path)? else {
            unreadable += 1;
            continue;
        };
        let Some(a) = member(&o, ".mp3") else {
            unreadable += 1;
            continue;
        };
        let cdg = member(&o, ".cdg");
        let key = (o.crc[a], o.size[a]);
        if !candidates.contains_key(&key) {
            order.push(key);
        }
        candidates.entry(key).or_default().push(Row { path: path.clone(), audio_idx: a, cdg_idx: cdg, audio_name: o.names[a].clone() });
    }
    // rows of every candidate set with more than one member, in the order Python's dict yields them
    let mut verify: Vec<&Row> = vec![];
    for key in &order {
        let rows = &candidates[key];
        if rows.len() > 1 {
            verify.extend(rows.iter());
        }
    }
    let mut audio_groups: BTreeMap<String, Vec<&Row>> = BTreeMap::new();
    for row in verify {
        match member_sha(&row.path, row.audio_idx) {
            Ok(d) => audio_groups.entry(d).or_default().push(row),
            Err(()) => unreadable += 1,
        }
    }
    let mut groups: Vec<(bool, String, Value, Vec<String>)> = vec![]; // (eligible, first path casefolded, json, paths)
    for (audio_digest, rows) in &audio_groups {
        if rows.len() < 2 {
            continue;
        }
        let mut cdg_order: Vec<String> = vec![];
        let mut cdg_groups: HashMap<String, Vec<&Row>> = HashMap::new();
        for row in rows {
            let digest = match row.cdg_idx {
                Some(ci) => match member_sha(&row.path, ci) {
                    Ok(d) => d,
                    Err(()) => {
                        unreadable += 1;
                        String::new()
                    }
                },
                None => String::new(),
            };
            if !cdg_groups.contains_key(&digest) {
                cdg_order.push(digest.clone());
            }
            cdg_groups.entry(digest).or_default().push(row);
        }
        let _ = rows.iter().map(|r| &r.audio_name).count();
        for digest in &cdg_order {
            let same = &cdg_groups[digest];
            if !digest.is_empty() && same.len() > 1 {
                let mut ordered: Vec<String> = same.iter().map(|r| r.path.clone()).collect();
                ordered.sort_by_key(|p| keeper_key(p));
                let j = json!({"kind": "identical_audio_cdg", "eligible": true, "audio_sha256": audio_digest, "cdg_sha256": digest, "keeper": ordered[0], "paths": ordered});
                groups.push((true, ordered[0].to_lowercase(), j, ordered));
            }
        }
        if cdg_order.len() > 1 {
            let mut ordered: Vec<String> = rows.iter().map(|r| r.path.clone()).collect();
            ordered.sort_by_key(|p| keeper_key(p));
            let j = json!({"kind": "identical_audio_different_cdg", "eligible": false, "audio_sha256": audio_digest, "cdg_sha256": "", "keeper": "", "paths": ordered});
            groups.push((false, ordered[0].to_lowercase(), j, ordered));
        }
    }
    groups.sort_by(|a, b| (!a.0, &a.1).cmp(&(!b.0, &b.1)));
    let out: Vec<Value> = groups.into_iter().map(|g| g.2).collect();
    Ok(json!({"groups": out, "scanned": unique.len(), "unreadable": unreadable, "cancelled": false}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sha(data: &[u8]) -> String {
        let mut h = Sha256::new();
        h.update(data);
        h.hex()
    }

    #[test]
    fn sha256_matches_the_standard_test_vectors() {
        assert_eq!(sha(b""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        assert_eq!(sha(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        assert_eq!(sha(&vec![b'a'; 1_000_000]), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
        assert_eq!(sha(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
    }

    #[test]
    fn the_keeper_prefers_a_plain_name_over_a_copy() {
        assert!(keeper_key("/a/Song.zip") < keeper_key("/a/Song copy.zip"));
        assert!(keeper_key("/a/Song.zip") < keeper_key("/a/Song (1).zip"));
        assert_eq!(stem_of_name("a.b.zip"), "a.b");
    }
}
