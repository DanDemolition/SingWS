//! `fast_mp3_duration_from_zip` and the MP3 header heuristics under it (`fast_mp3_duration_from_stream`, `_skip_id3v2`,
//! `_parse_mpeg_frame_header`, `_find_xing_vbri`) of 0.2.18.1.py.
//!
//! The ZIP is read through the `zip` crate. Python's `zipfile` decodes names with CP437 unless the UTF-8 flag is set; this port reads
//! the raw name bytes and DECLINES any archive with a non-ASCII member name, so the two can never decode a name differently.

use crate::pyfs::*;
use serde_json::{Value, json};
use std::io::Read;

/// Cursor over a byte buffer with `io.BytesIO` semantics (seeking past the end is allowed, reads then return nothing).
struct Cur<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Cur<'a> {
    fn read(&mut self, n: usize) -> &'a [u8] {
        let start = self.pos.min(self.data.len());
        let end = self.pos.saturating_add(n).min(self.data.len());
        let got = &self.data[start..end];
        if self.pos <= self.data.len() {
            self.pos += got.len();
        }
        got
    }
    /// `_read_exact`: the bytes, or None when fewer than `n` (or none) were available.
    fn read_exact(&mut self, n: usize) -> Option<&'a [u8]> {
        let b = self.read(n);
        if !b.is_empty() && b.len() == n { Some(b) } else { None }
    }
}

struct Header {
    layer: u8,
    bitrate_kbps: u32,
    sr: u32,
}

fn parse_header(h: &[u8]) -> Option<Header> {
    if h.len() != 4 {
        return None;
    }
    let b = u32::from_be_bytes([h[0], h[1], h[2], h[3]]);
    if b & 0xFFE0_0000 != 0xFFE0_0000 {
        return None;
    }
    let ver_id = (b >> 19) & 3;
    let layer = (b >> 17) & 3;
    let br_id = ((b >> 12) & 0xF) as usize;
    let sr_id = (b >> 10) & 3;
    if ver_id == 1 || layer == 0 || br_id == 0 || br_id == 15 || sr_id == 3 {
        return None;
    }
    // 0 = MPEG2.5, 2 = MPEG2, 3 = MPEG1
    let layer_no: usize = match layer {
        1 => 3,
        2 => 2,
        _ => 1,
    };
    let base_sr = [44100u32, 48000, 32000][sr_id as usize];
    let sr = match ver_id {
        2 => base_sr / 2,
        0 => base_sr / 4,
        _ => base_sr,
    };
    const M1: [[u32; 15]; 3] = [
        [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
        [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
        [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    ];
    const M2_L1: [u32; 15] = [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256];
    const M2_L23: [u32; 15] = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];
    let bitrate_kbps = if ver_id == 3 { M1[layer_no - 1][br_id] } else if layer_no == 1 { M2_L1[br_id] } else { M2_L23[br_id] };
    if bitrate_kbps == 0 {
        return None;
    }
    Some(Header { layer: layer_no as u8, bitrate_kbps, sr })
}

fn be32(b: &[u8]) -> u32 {
    u32::from_be_bytes([b[0], b[1], b[2], b[3]])
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

/// `_find_xing_vbri`: Some(frames) when the Xing/Info or VBRI header carries a frame count (the only field the caller uses),
/// `Some(None)`-style distinctions are not needed: the caller checks `'frames' in x`.
fn find_xing_vbri(c: &mut Cur) -> Option<Option<u32>> {
    let hdr4 = c.read_exact(4)?;
    parse_header(hdr4)?;
    let rest = c.read_exact(200).unwrap_or(&[]);
    let mut scan = hdr4.to_vec();
    scan.extend_from_slice(rest);
    let ix = find(&scan, b"Xing").or_else(|| find(&scan, b"Info"));
    if let Some(ix) = ix {
        let flags_off = ix + 4;
        if flags_off + 4 <= scan.len() {
            let flags = be32(&scan[flags_off..]);
            let mut p = flags_off + 4;
            let mut frames = None;
            if flags & 1 != 0 && p + 4 <= scan.len() {
                frames = Some(be32(&scan[p..]));
                p += 4;
            }
            if flags & 2 != 0 && p + 4 <= scan.len() {
                p += 4;
            }
            let _ = p;
            return Some(frames); // {'type': 'xing', ...}
        }
    }
    if let Some(iv) = find(&scan, b"VBRI") {
        if iv + 26 <= scan.len() {
            let base = iv + 10;
            if base + 8 <= scan.len() {
                return Some(Some(be32(&scan[base + 4..])));
            }
        }
    }
    None
}

/// `fast_mp3_duration_from_stream(BytesIO(data), file_size_hint)`.
pub fn mp3_duration_from_bytes(data: &[u8], file_size_hint: Option<u64>) -> Option<i64> {
    let mut c = Cur { data, pos: 0 };
    // _skip_id3v2
    let hdr = c.read_exact(10);
    match hdr {
        Some(h) if &h[0..3] == b"ID3" => {
            let size = (((h[6] & 0x7f) as usize) << 21) | (((h[7] & 0x7f) as usize) << 14) | (((h[8] & 0x7f) as usize) << 7) | (h[9] & 0x7f) as usize;
            let footer = if h[5] & 0x10 != 0 { 10 } else { 0 };
            c.pos += size + footer;
        }
        Some(_) => c.pos -= 10,
        None => {} // a short read leaves the position at the end, as in Python
    }
    let start = c.pos;
    let hdr4 = c.read_exact(4)?;
    let info = parse_header(hdr4)?;
    c.pos = start;
    if let Some(Some(frames)) = find_xing_vbri(&mut c) {
        if info.sr != 0 {
            let spf: f64 = if info.layer == 3 || info.layer == 2 { 1152.0 } else { 384.0 };
            let secs = (frames as f64 * spf) / info.sr as f64;
            return Some((secs + 0.5) as i64);
        }
    }
    if let Some(hint) = file_size_hint.filter(|h| *h != 0) {
        let bytes_per_sec = (info.bitrate_kbps as u64 * 1000) / 8;
        if bytes_per_sec > 0 {
            return Some(((hint / bytes_per_sec) as i64).max(1));
        }
    }
    None
}

fn stat_of(path: &str) -> (i64, i64) {
    use std::os::unix::fs::MetadataExt;
    match std::fs::metadata(path) {
        Ok(m) => ((m.mtime() as f64 + m.mtime_nsec() as f64 * 1e-9).trunc() as i64, m.len() as i64),
        Err(_) => (0, 0),
    }
}

/// `fast_mp3_duration_from_zip(zip_path)` -> (seconds, cache_key) or (None, None). An archive Python's `zipfile` cannot open is
/// (None, None) there and here. Non-ASCII member names are declined.
pub fn duration_from_zip(zip_path: &str, cwd: &str) -> R<Value> {
    let zip_path = abspath(zip_path, cwd);
    let none = json!({ "duration": null, "cache_key": null });
    let Ok(file) = std::fs::File::open(&zip_path) else { return Ok(none) };
    let Ok(mut zf) = zip::ZipArchive::new(file) else { return Ok(none) };

    struct Member {
        idx: usize,
        name: String,
        lower: String,
        is_dir: bool,
        crc: u32,
        usize_: u64,
    }
    let mut members: Vec<Member> = Vec::new();
    for i in 0..zf.len() {
        let Ok(f) = zf.by_index_raw(i) else { return Ok(none) };
        let raw = f.name_raw();
        if !raw.is_ascii() {
            return decline("a member name that is not ASCII");
        }
        let name = String::from_utf8_lossy(raw).to_string();
        members.push(Member { idx: i, lower: name.to_lowercase(), is_dir: name.ends_with('/'), name, crc: f.crc32(), usize_: f.size() });
    }
    let mut cdg: Option<usize> = None;
    let mut mp3: Option<usize> = None;
    let mut candidate: Option<usize> = None;
    for (k, m) in members.iter().enumerate() {
        if m.is_dir {
            continue;
        }
        if m.lower.ends_with(".cdg") {
            cdg = Some(k);
        }
        if m.lower.ends_with(".mp3") {
            if candidate.is_none() {
                candidate = Some(k);
            }
            let stem = m.lower.rsplit_once('.').map(|x| x.0).unwrap_or(&m.lower);
            if members.iter().any(|o| o.lower.starts_with(stem) && o.lower.ends_with(".cdg")) {
                mp3 = Some(k);
            }
        }
    }
    if mp3.is_none() {
        mp3 = candidate;
    }
    let (mtime, size) = stat_of(&zip_path);
    let key = |m: &Member| json!({ "zip_mtime": mtime, "zip_size": size, "member": m.name, "crc": m.crc, "usize": m.usize_ });

    if let Some(k) = cdg {
        let m = &members[k];
        if m.usize_ > 0 {
            let dur = ((m.usize_ as f64 / 96.0) / 75.0) as i64;
            if dur > 0 {
                return Ok(json!({ "duration": dur, "cache_key": key(m) }));
            }
        }
    }
    if let Some(k) = mp3 {
        let m = &members[k];
        let Ok(mut mf) = zf.by_index(m.idx) else { return Ok(none) };
        let mut chunk: Vec<u8> = Vec::new();
        let hint = Some(m.usize_);
        let mut dur: Option<i64> = None;
        for (i, want) in [64 * 1024u64, 64 * 1024, 128 * 1024].iter().enumerate() {
            let mut part: Vec<u8> = Vec::new();
            if (&mut mf).take(*want).read_to_end(&mut part).is_err() {
                return Ok(none); // Python: BadZipFile (bad CRC) and friends escape to the outer except
            }
            if i > 0 && part.is_empty() {
                break; // `if more:` guard
            }
            chunk.extend_from_slice(&part);
            dur = mp3_duration_from_bytes(&chunk, hint);
            if dur.is_some() {
                break;
            }
        }
        if let Some(d) = dur.filter(|d| *d > 0) {
            return Ok(json!({ "duration": d, "cache_key": key(m) }));
        }
    }
    Ok(none)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame_header(version: u8, layer: u8, br: u8, sr: u8) -> [u8; 4] {
        // 11 sync bits, version(2), layer(2), no CRC, bitrate(4), sample rate(2), pad 0, private 0, channel mode 0
        let b: u32 = 0xFFE0_0000 | ((version as u32) << 19) | ((layer as u32) << 17) | (1 << 16) | ((br as u32) << 12) | ((sr as u32) << 10);
        b.to_be_bytes()
    }

    #[test]
    fn size_heuristic_uses_the_bitrate() {
        // MPEG1 layer 3, 128 kbps, 44.1 kHz; 1,600,000 bytes at 16,000 bytes/s = 100 s
        let mut data = frame_header(3, 1, 9, 0).to_vec();
        data.extend(vec![0u8; 400]);
        assert_eq!(mp3_duration_from_bytes(&data, Some(1_600_000)), Some(100));
        assert_eq!(mp3_duration_from_bytes(&data, Some(1)), Some(1));
        assert_eq!(mp3_duration_from_bytes(&data, None), None);
    }

    #[test]
    fn xing_frame_count_wins() {
        let mut data = frame_header(3, 1, 9, 0).to_vec();
        data.extend(vec![0u8; 32]);
        data.extend(b"Xing");
        data.extend(1u32.to_be_bytes()); // flags: frames present
        data.extend(1000u32.to_be_bytes());
        data.extend(vec![0u8; 200]);
        // 1000 frames * 1152 / 44100 = 26.12 -> 26
        assert_eq!(mp3_duration_from_bytes(&data, Some(5)), Some(26));
    }

    #[test]
    fn an_id3_tag_is_skipped_and_a_short_tail_gives_nothing() {
        let mut data = b"ID3\x04\x00\x00\x00\x00\x00\x05".to_vec();
        data.extend(vec![0u8; 5]);
        data.extend(frame_header(3, 1, 9, 0));
        data.extend(vec![0u8; 8]);
        assert_eq!(mp3_duration_from_bytes(&data, Some(160_000)), Some(10));
        assert_eq!(mp3_duration_from_bytes(&[0xFF, 0xFB], Some(10)), None);
    }

    #[test]
    fn invalid_headers_are_refused() {
        for h in [frame_header(1, 1, 9, 0), frame_header(3, 0, 9, 0), frame_header(3, 1, 0, 0), frame_header(3, 1, 15, 0), frame_header(3, 1, 9, 3)] {
            let mut d = h.to_vec();
            d.extend(vec![0u8; 300]);
            assert_eq!(mp3_duration_from_bytes(&d, Some(100_000)), None);
        }
    }

    #[test]
    fn the_sync_word_needs_all_eleven_bits() {
        let mut d = frame_header(3, 1, 9, 0).to_vec();
        d.extend(vec![0u8; 300]);
        assert_eq!(mp3_duration_from_bytes(&d, Some(160_000)), Some(10));
        d[1] &= !0x20; // clear the eleventh sync bit
        assert_eq!(mp3_duration_from_bytes(&d, Some(160_000)), None);
    }

    #[test]
    fn a_zip_with_a_non_ascii_member_name_is_declined() {
        use std::io::Write;
        let path = std::env::temp_dir().join(format!("singws-scan-zip-{}.zip", std::process::id()));
        {
            let mut z = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
            z.start_file("S\u{e5}ng.mp3", zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored)).unwrap();
            z.write_all(&[0u8; 20]).unwrap();
            z.finish().unwrap();
        }
        let e = duration_from_zip(path.to_str().unwrap(), "/").unwrap_err();
        assert!(e.starts_with("decline:"), "{e}");
        let _ = std::fs::remove_file(&path);
    }
}
