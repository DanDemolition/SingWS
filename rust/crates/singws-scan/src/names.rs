//! `parse_filename_stem`, `_display_for_track` and `_song_type_for_name` of 0.2.18.1.py.

use crate::pyfs::{py_strip, stem_of};

pub const DEFAULT_FILENAME_FORMAT: &str = "disc-artist-title";

/// `parse_filename_stem(stem, fmt)` -> (artist, title, disc_id). Separator is `" - "`; every piece is stripped.
pub fn parse_filename_stem(stem: &str, fmt: &str) -> (String, String, String) {
    let stem = py_strip(stem);
    if stem.is_empty() {
        return (String::new(), String::new(), String::new());
    }
    let parts: Vec<&str> = stem.split(" - ").map(py_strip).collect();
    let n = parts.len();
    let j = |a: usize, b: usize| parts[a..b].join(" - ");
    let s = |x: &str| x.to_string();

    if fmt == "artist-title" {
        return if n >= 2 { (s(parts[0]), j(1, n), String::new()) } else { (String::new(), s(stem), String::new()) };
    }
    if fmt == "title-artist" {
        return if n >= 2 { (j(1, n), s(parts[0]), String::new()) } else { (String::new(), s(stem), String::new()) };
    }
    if n < 3 {
        if n == 2 {
            return match fmt {
                "title-artist-disc" => (s(parts[1]), s(parts[0]), String::new()),
                "disc-artist-title" => (s(parts[1]), String::new(), s(parts[0])),
                "disc-title-artist" => (String::new(), s(parts[1]), s(parts[0])),
                _ => (s(parts[0]), s(parts[1]), String::new()),
            };
        }
        return (String::new(), s(stem), String::new());
    }
    match fmt {
        "artist-title-disc" => (s(parts[0]), j(1, n - 1), s(parts[n - 1])),
        "disc-artist-title" => (s(parts[1]), j(2, n), s(parts[0])),
        "disc-title-artist" => (j(2, n), s(parts[1]), s(parts[0])),
        "title-artist-disc" => (s(parts[n - 2]), j(0, n - 2), s(parts[n - 1])),
        // Unknown format: best-effort default (n >= 3 here).
        _ => (s(parts[0]), j(1, n - 1), s(parts[n - 1])),
    }
}

/// `_display_for_track`: `Artist - Title - Disc`, the title falling back to the file's stem.
pub fn display_for_track(path: &str, title: &str, artist: &str, disc_id: &str) -> String {
    let base_name = path.rsplit('/').next().unwrap_or(path);
    let title = py_strip(if title.is_empty() { stem_of(base_name) } else { title });
    let artist = py_strip(artist);
    let disc = py_strip(disc_id);
    let mut base = if artist.is_empty() { title.to_string() } else { format!("{artist} - {title}") };
    if !disc.is_empty() {
        base = format!("{base} - {disc}");
    }
    base
}

/// `_song_type_for_name`: "mp4" | "cdg" | "mp3g" by extension (case-insensitive).
pub fn song_type_for_name(name: &str) -> Option<&'static str> {
    let lower = name.to_ascii_lowercase();
    if lower.ends_with(".mp4") {
        Some("mp4")
    } else if lower.ends_with(".cdg") {
        Some("cdg")
    } else if lower.ends_with(".zip") {
        Some("mp3g")
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(stem: &str, fmt: &str) -> (String, String, String) {
        parse_filename_stem(stem, fmt)
    }

    #[test]
    fn four_layouts() {
        let t = |a: &str, b: &str, c: &str| (a.to_string(), b.to_string(), c.to_string());
        assert_eq!(p("KV - Linkin Park - In The End", "disc-artist-title"), t("Linkin Park", "In The End", "KV"));
        assert_eq!(p("Linkin Park - In The End - KV", "artist-title-disc"), t("Linkin Park", "In The End", "KV"));
        assert_eq!(p("KV - In The End - Linkin Park", "disc-title-artist"), t("Linkin Park", "In The End", "KV"));
        assert_eq!(p("In The End - Linkin Park - KV", "title-artist-disc"), t("Linkin Park", "In The End", "KV"));
    }

    #[test]
    fn extras_are_absorbed_by_the_documented_field() {
        let t = |a: &str, b: &str, c: &str| (a.to_string(), b.to_string(), c.to_string());
        assert_eq!(p("KV - A - T1 - T2", "disc-artist-title"), t("A", "T1 - T2", "KV"));
        assert_eq!(p("KV - T - A1 - A2", "disc-title-artist"), t("A1 - A2", "T", "KV"));
        assert_eq!(p("T1 - T2 - A - KV", "title-artist-disc"), t("A", "T1 - T2", "KV"));
        assert_eq!(p("A - T1 - T2 - KV", "artist-title-disc"), t("A", "T1 - T2", "KV"));
        assert_eq!(p("a - b - c", "no-such-format"), t("a", "b", "c"));
    }

    #[test]
    fn short_names_keep_what_they_can() {
        let t = |a: &str, b: &str, c: &str| (a.to_string(), b.to_string(), c.to_string());
        assert_eq!(p("only", "disc-artist-title"), t("", "only", ""));
        assert_eq!(p("KV - X", "disc-artist-title"), t("X", "", "KV"));
        assert_eq!(p("KV - X", "disc-title-artist"), t("", "X", "KV"));
        assert_eq!(p("T - A", "title-artist-disc"), t("A", "T", ""));
        assert_eq!(p("A - T", "artist-title-disc"), t("A", "T", ""));
        assert_eq!(p("A - T - U", "artist-title"), t("A", "T - U", ""));
        assert_eq!(p("T - A - B", "title-artist"), t("A - B", "T", ""));
        assert_eq!(p("   ", "artist-title"), t("", "", ""));
    }

    #[test]
    fn display_falls_back_to_the_file_stem() {
        assert_eq!(display_for_track("/m/x y.cdg", "", "", ""), "x y");
        assert_eq!(display_for_track("/m/x.cdg", "T", "A", "D"), "A - T - D");
        assert_eq!(display_for_track("/m/x.cdg", "T", "", "D"), "T - D");
    }

    #[test]
    fn types() {
        assert_eq!(song_type_for_name("A.MP4"), Some("mp4"));
        assert_eq!(song_type_for_name("A.Zip"), Some("mp3g"));
        assert_eq!(song_type_for_name("A.mp3"), None);
    }
}
