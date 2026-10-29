//! Reads `path<TAB>tag_artist<TAB>tag_title` lines from stdin, writes `artist<TAB>title` per line.
//! For the fuzz parity test against track_display.py (`tools/track_display_parity.py`).
//! Newlines inside fields are escaped as the two characters `\n` (and a backslash as `\\`).
use singws_cdg::track_display::display_from_path;
use std::io::{BufRead, Write};

fn unesc(s: &str) -> String {
    let mut out = String::new();
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '\\' {
            match it.next() {
                Some('n') => out.push('\n'),
                Some('r') => out.push('\r'),
                Some('t') => out.push('\t'),
                Some(o) => out.push(o),
                None => {}
            }
        } else {
            out.push(c);
        }
    }
    out
}
fn esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\n', "\\n").replace('\r', "\\r").replace('\t', "\\t")
}

fn main() {
    let stdin = std::io::stdin();
    let mut out = std::io::BufWriter::new(std::io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        let mut f = line.split('\t');
        let p = unesc(f.next().unwrap_or(""));
        let a = unesc(f.next().unwrap_or(""));
        let t = unesc(f.next().unwrap_or(""));
        let (ra, rt) = display_from_path(&p, &a, &t);
        writeln!(out, "{}\t{}", esc(&ra), esc(&rt)).unwrap();
    }
}
