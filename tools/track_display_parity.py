#!/usr/bin/env python3
"""Fuzz parity: rust/crates/singws-cdg track_display.rs against the app's track_display.py.

Real-looking file names (pack numbers, BPM, noise brackets, underscores, feat., tags) mixed with
random fragment soup, run through both; any difference is printed. Usage:
    python3 tools/track_display_parity.py [--n 20000] [--seed 1]
Needs `cargo build --release -p singws-cdg` (CARGO_TARGET_DIR respected).
"""
import argparse, os, random, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import track_display as py

def esc(s): return s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
def unesc(s):
    out, it = [], iter(s)
    for c in it:
        if c == "\\":
            n = next(it, "")
            out.append({"n": "\n", "r": "\r", "t": "\t"}.get(n, n))
        else:
            out.append(c)
    return "".join(out)

WORDS = ["Calvin Harris", "One Kiss", "don_t stop", "Lil_ Cheesecake", "no tears left to cry", "the", "of", "feat.", "ft", "F.", "featuring",
         "Ultimix 330", "Kwikmix", "ulti-remix", "ultimix", "(Ultimix By DJ X)", "[Official Video]", "(official music video)", "(Lyrics)", "lyric video",
         "(Radio Edit)", "(2011 Remaster)", "remastered 2009", "(Explicit)", "[HD]", "(4K)", "(126 bpm)", "[ 98BPM ]", "()", "[ ]", "01.", "(02)", "03 -",
         "1-800", "Beyoncé", "é", "e\u0301", "ÉLAN", "日本語", "Ünïcode", "\u00a0", "\u3000", "\x1c", "\u017f", "\u212a", "\u0131", "\u0130", "FT", "Ft", "vs.", "Vs", "VS.", "(single version)", "[Album  Version]", "(bpm)", "(x bpm)", "(a 99 BPM b)", "(120bpm", "(Ultimix", "(Kwikmix 5]", "(official lyrics video)", "(official visualizer)", "(Official Visualiser)", "(remaster 2011)", "(1999 remastered 2009)", "(RADIO  EDIT)", "AC_DC", "x_-_y", "Artist_-_Title",
         "126", "99", "1999", "2024", "&", "!", "'", "\"", "‘", "“", "—", "–", "-", "_", ",", "(", ")", "[", "]", ".", " ", "  ", "\t", "\n", "a", "B", "Z"]
EXTS = [".mp3", ".MP3", ".flac", ".m4a", ".aif", ".aiff", ".AIFF", ".ogg", ".opus", ".wma", ".mp4", ".alac", ".mp3\n", ".txt", ""]
SEPS = [" - ", " – ", " — ", "_-_", " -  ", "- ", " -", "  -  "]
TAGS = ["", "Unknown", "Various Artists", "Track 01", "audio  track 7", "http://x", "www.a.b", "Daft Punk", "get lucky (Official Video)", "NA", "<unknown>", "  ",
        "ARTIST", "title", "lower case title", "feat. x", "A_B"]

def gen(r):
    k = r.random()
    if k < 0.45:  # structured
        parts = []
        if r.random() < .5: parts.append(r.choice(["01.", "01 ", "(02)", "03_", "12)", "100.", "1000."]))
        a = r.choice(WORDS) + r.choice(["", " " + r.choice(WORDS)])
        t = r.choice(WORDS) + r.choice(["", " " + r.choice(WORDS), " " + r.choice(WORDS) + " " + r.choice(WORDS)])
        mid = [a] if r.random() < .9 else []
        if r.random() < .3: mid.append(r.choice(["Ultimix 330", "Kwikmix 12", "Greatest Hits", "00."]))
        mid.append(t)
        name = r.choice(SEPS).join(mid)
        if parts: name = parts[0] + r.choice([" ", ""]) + name
        if r.random() < .3: name += " " + r.choice(["(Ultimix By DJ X) 126", "[Official Audio]", "(128 BPM)", "(Radio Mix)", "(Remastered 2011)", "126"])
    else:  # soup
        name = "".join(r.choice(WORDS) for _ in range(r.randint(1, 9)))
    path = r.choice(["", "/Music/", "/a/b/", "C:/x/"]) + name + r.choice(EXTS)
    return path, r.choice(TAGS) if r.random() < .5 else "", r.choice(TAGS) if r.random() < .5 else ""

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20000); ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    r = random.Random(a.seed)
    # case-insensitivity quirks (Kelvin sign, long s, dotless/dotted i) the random mix rarely reaches in context
    fixed = [(n, '', '') for n in ['A - T (\u212awikmix 5) x.mp3', 'A - T (Offic\u0131al Video).mp3', 'A - T (Offic\u0130al Video).mp3', 'A - T ([\u017fingle Version]).mp3', 'A - T (4\u212a).mp3']]
    cases = fixed + [gen(r) for _ in range(a.n)]
    tgt = os.environ.get("CARGO_TARGET_DIR", os.path.expanduser("~/.cache/singws-rust-target-cdg"))
    exe = os.path.join(tgt, "release", "singws-track-display-cli")
    inp = "\n".join("\t".join(esc(x) for x in c) for c in cases) + "\n"
    out = subprocess.run([exe], input=inp, capture_output=True, text=True, check=True).stdout.split("\n")[:-1]
    assert len(out) == len(cases), (len(out), len(cases))
    bad = 0
    for c, line in zip(cases, out):
        ra, rt = (unesc(x) for x in line.split("\t"))
        pa, pt = py.display_from_path(*c)
        if (pa, pt) != (ra, rt):
            bad += 1
            if bad <= 15: print("DIFF", repr(c), "\n   py  ", (pa, pt), "\n   rust", (ra, rt))
    print(f"{len(cases)} cases, {bad} differ")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
