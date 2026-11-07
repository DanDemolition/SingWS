//! Key/tempo read back from KaraFun's Personalize panel (0.2.18.1.py): `karafun_confirmed_readback` and the parsing in `_karafun_read_key_tempo`.
//! tools/karafun_script_parity.py runs the REAL Python (the module function; the method against a fake host) on the same inputs.
use crate::py_space;

pub type Pair = (i64, i64);

/// `karafun_confirmed_readback(known, first, second)`: the (key, tempo) to show, or None to leave the display alone. A read that agrees with what SingWS
/// set changes nothing; a disagreeing read is only believed when a second, later read says exactly the same.
pub fn confirmed_readback(known: Option<Pair>, first: Option<Pair>, second: Option<Pair>) -> Option<Pair> {
    let first = first?;
    if known == Some(first) {
        return None;
    }
    let second = second?;
    if first == second { Some(first) } else { None }
}

/// Result of parsing: a value, nothing, or a value this port refuses to guess (digits beyond 64 bits or Unicode digits).
#[derive(Debug, PartialEq)]
pub enum Parsed {
    Pair(Pair),
    None,
    Decline,
}

fn signed_digits(chars: &[char], mut i: usize) -> Result<(i64, usize), bool> {
    // [+-]?\d+ ; Err(true) = decline, Err(false) = no match here
    let start = i;
    let mut neg = false;
    if i < chars.len() && (chars[i] == '+' || chars[i] == '-') {
        neg = chars[i] == '-';
        i += 1;
    }
    let ds = i;
    while i < chars.len() && chars[i].is_ascii_digit() {
        i += 1;
    }
    if i == ds {
        // a Unicode digit would match Python's \d: do not guess
        return Err(chars.get(ds).map_or(false, |c| !c.is_ascii() && c.is_numeric()));
    }
    if i - ds > 18 {
        return Err(true);
    }
    let _ = start;
    let n: i64 = chars[ds..i].iter().collect::<String>().parse().unwrap_or(0);
    Ok((if neg { -n } else { n }, i))
}

/// The tail of `_karafun_read_key_tempo`: `re.search(r"KEY=([+-]?\d+)\s+TEMPO=([+-]?\d+)%", text)` -> (key, 100 + tempo), else None.
pub fn parse_key_tempo(text: &str) -> Parsed {
    let chars: Vec<char> = text.chars().collect();
    if chars.iter().any(|c| !c.is_ascii() && (c.is_numeric() || py_space(*c)) && c.is_numeric()) {
        return Parsed::Decline;
    }
    let key: Vec<char> = "KEY=".chars().collect();
    let tempo: Vec<char> = "TEMPO=".chars().collect();
    for s in 0..chars.len() {
        if chars[s..].len() < key.len() || chars[s..s + key.len()] != key[..] {
            continue;
        }
        let (k, mut i) = match signed_digits(&chars, s + key.len()) {
            Ok(x) => x,
            Err(true) => return Parsed::Decline,
            Err(false) => continue,
        };
        let ws = i;
        while i < chars.len() && py_space(chars[i]) {
            i += 1;
        }
        if i == ws || chars.len() < i + tempo.len() || chars[i..i + tempo.len()] != tempo[..] {
            continue;
        }
        let (t, j) = match signed_digits(&chars, i + tempo.len()) {
            Ok(x) => x,
            Err(true) => return Parsed::Decline,
            Err(false) => continue,
        };
        if j < chars.len() && chars[j] == '%' {
            return Parsed::Pair((k, 100 + t));
        }
    }
    Parsed::None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_lone_disagreeing_read_is_not_believed() {
        assert_eq!(confirmed_readback(Some((0, 100)), Some((2, 95)), None), None);
        assert_eq!(confirmed_readback(Some((0, 100)), Some((2, 95)), Some((2, 95))), Some((2, 95)));
        assert_eq!(confirmed_readback(Some((0, 100)), Some((2, 95)), Some((2, 96))), None);
        assert_eq!(confirmed_readback(Some((2, 95)), Some((2, 95)), None), None);
        assert_eq!(confirmed_readback(None, None, None), None);
    }

    #[test]
    fn the_panel_text() {
        assert_eq!(parse_key_tempo("KEY=+2 TEMPO=-5%"), Parsed::Pair((2, 95)));
        assert_eq!(parse_key_tempo("KEY=0\tTEMPO=0%"), Parsed::Pair((0, 100)));
        assert_eq!(parse_key_tempo("KEY= TEMPO=5%"), Parsed::None);
        assert_eq!(parse_key_tempo("junk KEY=1 TEMPO=2% KEY=3 TEMPO=4%"), Parsed::Pair((1, 102)));
        assert_eq!(parse_key_tempo("KEY=1TEMPO=2%"), Parsed::None);
    }
}
