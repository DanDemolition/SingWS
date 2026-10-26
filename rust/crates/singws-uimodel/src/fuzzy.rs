//! The typo-tolerant fallback of `song_index.search_songs`: when the strict substring search finds NOTHING, the app scans the table and accepts a song when every
//! query word is (a) a raw substring of its search string, (b) equal to one of its canonical tokens (number words, punctuation, `&` folded), or (c) within a
//! few typos of one (Levenshtein 1 for words of 3-5 letters, 2 for longer ones, 0 for shorter).
//!
//! One deliberate difference: Python stops the scan after 0.25 s (so on a very large library its answer depends on how fast the machine is); this port takes
//! an optional time budget, which the shell sets generously and the parity test leaves off. The scan cap (150,000 rows, in table order) is the same.

use crate::music::py_split;
use singws_dbw::pyval::py_lower;
use std::collections::HashSet;

pub const SCAN_CAP: i64 = 150_000;

const NUMWORDS: &[(&str, &str)] = &[
    ("zero", "0"), ("one", "1"), ("two", "2"), ("three", "3"), ("four", "4"), ("five", "5"), ("six", "6"), ("seven", "7"), ("eight", "8"), ("nine", "9"), ("ten", "10"),
    ("eleven", "11"), ("twelve", "12"), ("thirteen", "13"), ("fourteen", "14"), ("fifteen", "15"), ("sixteen", "16"), ("seventeen", "17"), ("eighteen", "18"),
    ("nineteen", "19"), ("twenty", "20"), ("thirty", "30"), ("forty", "40"), ("fifty", "50"), ("sixty", "60"), ("seventy", "70"), ("eighty", "80"), ("ninety", "90"),
    ("hundred", "100"),
];

/// `_canon_token`: lowercased, `&` -> " and ", runs of [a-z0-9] kept (everything else, including accented letters, separates), number words to digits, joined.
pub fn canon_token(token: &str) -> Result<String, String> {
    let t = py_lower(token)?.replace('&', " and ");
    let mut out = String::new();
    let mut run = String::new();
    let mut flush = |run: &mut String, out: &mut String| {
        if !run.is_empty() {
            match NUMWORDS.iter().find(|(w, _)| *w == run.as_str()) {
                Some((_, d)) => out.push_str(d),
                None => out.push_str(run),
            }
            run.clear();
        }
    };
    for c in t.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            run.push(c);
        } else {
            flush(&mut run, &mut out);
        }
    }
    flush(&mut run, &mut out);
    Ok(out)
}

/// `_bounded_levenshtein` (code points; early exit once a whole row exceeds `maxd`, returning `maxd + 1`).
pub fn bounded_levenshtein(a: &str, b: &str, maxd: usize) -> usize {
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let (la, lb) = (a.len(), b.len());
    if la.abs_diff(lb) > maxd {
        return maxd + 1;
    }
    if la == 0 {
        return lb;
    }
    if lb == 0 {
        return la;
    }
    let mut prev: Vec<usize> = (0..=lb).collect();
    for i in 1..=la {
        let mut cur = vec![0usize; lb + 1];
        cur[0] = i;
        let ca = a[i - 1];
        let mut row_min = cur[0];
        for j in 1..=lb {
            let cost = usize::from(ca != b[j - 1]);
            cur[j] = (prev[j] + 1).min(cur[j - 1] + 1).min(prev[j - 1] + cost);
            if cur[j] < row_min {
                row_min = cur[j];
            }
        }
        if row_min > maxd {
            return maxd + 1;
        }
        prev = cur;
    }
    prev[lb]
}

/// `_fuzzy_threshold`: how many typos to tolerate for a canonical word of this many characters.
pub fn threshold(word: &str) -> usize {
    match word.chars().count() {
        0..=2 => 0,
        3..=5 => 1,
        _ => 2,
    }
}

pub fn word_matches_tokens(word: &str, tokens: &[String], maxd: usize) -> bool {
    if maxd == 0 || word.is_empty() {
        return false;
    }
    let lw = word.chars().count();
    for t in tokens {
        if t.chars().count().abs_diff(lw) > maxd {
            continue;
        }
        if bounded_levenshtein(word, t, maxd) <= maxd {
            return true;
        }
    }
    false
}

/// One query word: the word itself, its canonical form and how many typos to allow.
pub struct Term {
    pub raw: String,
    pub canon: String,
    pub maxd: usize,
}

pub fn terms(words: &[&str]) -> Result<Vec<Term>, String> {
    words
        .iter()
        .map(|w| {
            let canon = canon_token(w)?;
            let maxd = threshold(&canon);
            Ok(Term { raw: w.to_string(), canon, maxd })
        })
        .collect()
}

/// `ss` is a song's stored search string; true when every term is satisfied exactly as the Python loop does.
pub fn row_matches(ss: &str, terms: &[Term]) -> Result<bool, String> {
    let ctokens: Vec<String> = py_split(ss).iter().map(|t| canon_token(t)).collect::<Result<_, _>>()?;
    let set: HashSet<&str> = ctokens.iter().map(String::as_str).collect();
    for t in terms {
        if ss.contains(t.raw.as_str()) {
            continue;
        }
        if !t.canon.is_empty() && set.contains(t.canon.as_str()) {
            continue;
        }
        if word_matches_tokens(&t.canon, &ctokens, t.maxd) {
            continue;
        }
        return Ok(false);
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_tokens_fold_punctuation_ampersands_and_number_words() {
        assert_eq!(canon_token("Twenty").unwrap(), "20");
        assert_eq!(canon_token("B.O.B.").unwrap(), "bob");
        assert_eq!(canon_token("Don't").unwrap(), "dont");
        assert_eq!(canon_token("&").unwrap(), "and");
        assert_eq!(canon_token("Café").unwrap(), "caf", "accented letters are separators, as in the app's [a-z0-9] regex");
        assert_eq!(canon_token("twenty-one").unwrap(), "201", "each run is mapped (20, 1) and then joined, exactly as Python does");
        assert_eq!(canon_token("---").unwrap(), "");
    }

    #[test]
    fn levenshtein_is_bounded_and_counts_code_points() {
        assert_eq!(bounded_levenshtein("kitten", "sitting", 3), 3);
        assert!(bounded_levenshtein("kitten", "sitting", 2) > 2);
        assert_eq!(bounded_levenshtein("", "abc", 5), 3);
        assert_eq!(bounded_levenshtein("abc", "abc", 0), 0);
        assert_eq!(bounded_levenshtein("é", "e", 1), 1);
        assert!(bounded_levenshtein("abcdef", "ab", 2) > 2, "a length gap beyond the bound exits early");
    }

    #[test]
    fn the_threshold_scales_with_the_word_length() {
        assert_eq!([threshold("ab"), threshold("abc"), threshold("abcde"), threshold("abcdef")], [0, 1, 1, 2]);
    }

    #[test]
    fn a_row_matches_by_substring_canonical_form_or_typo() {
        let ss = "matchbox 20 push";
        let one = |w: &str| row_matches(ss, &terms(&[w]).unwrap()).unwrap();
        assert!(one("matchbox"), "raw substring");
        assert!(one("twenty"), "number word");
        assert!(one("pussh"), "one typo in a 5-letter word");
        assert!(one("pu"), "a raw substring of \"push\" needs no fuzzy rule");
        assert!(!one("pq"), "words under 3 letters allow no typo");
        assert!(one("matchbxo"), "transposition = two edits, allowed at 8 letters");
        assert!(!one("zzzzzz"));
        assert!(row_matches(ss, &terms(&["matchbox", "push"]).unwrap()).unwrap());
        assert!(!row_matches(ss, &terms(&["matchbox", "zzzzzz"]).unwrap()).unwrap(), "every word must match");
    }
}
