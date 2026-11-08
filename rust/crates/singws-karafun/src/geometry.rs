//! KaraFun preview geometry (0.2.18.1.py): `karafun_preview_pane_rect` (where KaraFun's built-in preview is, from the accessibility probe text), `karafun_fill_region` (crop to 16:9) and
//! `karafun_preview_region_changed`. Pure functions; tools/karafun_geometry_parity.py runs the REAL Python functions (read by AST) against these on a large corpus.
//! Not modelled (declined, never guessed): a number Python's `float()` would read in a way Rust cannot (underscores, non-ASCII digits) and NaN/infinity anywhere in the input.
use crate::monitor::py_splitlines;
use crate::py_strip;

pub const OVERLAY_MARGIN: f64 = 14.0;
pub const FILL_ZOOM_OUT: f64 = 1.08;

pub type Rect = (f64, f64, f64, f64);

#[derive(Debug, PartialEq)]
pub enum Pane {
    None,
    Found { window: Rect, region: Rect },
    Decline,
}

/// Python `float(text)` for the plain cases. `Err(true)` = a form this port does not model (decline), `Err(false)` = Python would raise ValueError.
pub(crate) fn py_float(s: &str) -> Result<f64, bool> {
    let t = py_strip(s);
    if t.chars().any(|c| !c.is_ascii()) || t.contains('_') {
        return Err(true);
    }
    match t.parse::<f64>() {
        Ok(v) if v.is_finite() => Ok(v),
        Ok(_) => Err(true), // inf / nan: comparisons with them are not modelled
        Err(_) => {
            let low = t.to_ascii_lowercase();
            // Python also accepts these spellings; Rust's parser accepts them too but report as decline above. Anything else is a ValueError.
            if matches!(low.trim_start_matches(['+', '-']), "inf" | "infinity" | "nan") { Err(true) } else { Err(false) }
        }
    }
}

struct Elem {
    role: String,
    x: f64,
    y: f64,
    w: f64,
    h: f64,
}

pub fn preview_pane_rect(probe_text: &str) -> Pane {
    let mut window: Option<Rect> = None;
    let mut elements: Vec<Elem> = Vec::new();
    for line in py_splitlines(probe_text) {
        let parts: Vec<&str> = py_strip(line).split('|').collect();
        if parts[0] == "WINDOW" && parts.len() == 5 {
            let mut v = [0.0f64; 4];
            let mut skip = false;
            for (i, p) in parts[1..5].iter().enumerate() {
                match py_float(p) {
                    Ok(f) => v[i] = f,
                    Err(true) => return Pane::Decline,
                    Err(false) => {
                        skip = true;
                        break;
                    }
                }
            }
            if !skip {
                window = Some((v[0], v[1], v[2], v[3]));
            }
        } else if parts[0] == "E" && parts.len() >= 7 {
            let n = parts.len();
            let mut v = [0.0f64; 4];
            let mut skip = false;
            for (i, p) in parts[n - 4..].iter().enumerate() {
                match py_float(p) {
                    Ok(f) => v[i] = f,
                    Err(true) => return Pane::Decline,
                    Err(false) => {
                        skip = true;
                        break;
                    }
                }
            }
            if !skip {
                elements.push(Elem { role: parts[1].to_string(), x: v[0], y: v[1], w: v[2], h: v[3] });
            }
        }
    }
    let Some((wx, wy, ww, wh)) = window else { return Pane::None };
    let _ = wh;
    if elements.is_empty() {
        return Pane::None;
    }
    let splitters: Vec<&Elem> = elements.iter().filter(|e| e.role == "AXSplitter" && e.h >= 300.0 && e.w <= 6.0 && e.x > wx + 0.3 * ww).collect();
    if splitters.is_empty() {
        return Pane::None;
    }
    // Python max(..., key=x): the first element with the greatest x (later ones replace only when strictly greater)
    let mut splitter = splitters[0];
    for e in &splitters[1..] {
        if e.x > splitter.x {
            splitter = e;
        }
    }
    let left = splitter.x + 1.0f64.max(splitter.w);
    let top = splitter.y;
    let right = wx + ww;
    let mut queue_header: Option<f64> = None;
    for e in elements.iter().filter(|e| e.role == "AXPopUpButton" && e.x >= left - 2.0 && e.y > top + 60.0) {
        if queue_header.map_or(true, |q| e.y < q) {
            queue_header = Some(e.y);
        }
    }
    let mut overlay_min: Option<f64> = None;
    for e in elements.iter().filter(|e| {
        ["AXImage", "AXStaticText", "AXButton", "AXSlider"].contains(&e.role.as_str()) && e.x >= left - 2.0 && e.y > top + 60.0 && queue_header.map_or(true, |q| e.y < q)
    }) {
        if overlay_min.map_or(true, |m| e.y < m) {
            overlay_min = Some(e.y);
        }
    }
    let bottom = if let Some(m) = overlay_min {
        m - OVERLAY_MARGIN
    } else if let Some(q) = queue_header {
        q - 8.0
    } else {
        return Pane::None;
    };
    let (width, height) = (right - left, bottom - top);
    if width < 200.0 || height < 80.0 {
        return Pane::None;
    }
    Pane::Found { window: (wx, wy, ww, wh), region: (left - wx, top - wy, width, height) }
}

/// `karafun_fill_region(region, aspect)`: crop the left and right of a region wider than `aspect` (centred).
pub fn fill_region(region: Rect, aspect: f64) -> Rect {
    let (x, y, w, h) = region;
    if w <= 0.0 || h <= 0.0 {
        return region;
    }
    let target = if h * aspect * FILL_ZOOM_OUT < w { h * aspect * FILL_ZOOM_OUT } else { w };
    if w <= h * aspect + 1.0 {
        return region;
    }
    (x + (w - target) / 2.0, y, target, h)
}

/// `karafun_preview_region_changed(old, new, tolerance)`: True when the regions differ by more than `tolerance` in any value; a missing one counts as a change unless both are missing.
pub fn region_changed(old: Option<Rect>, new: Option<Rect>, tolerance: f64) -> bool {
    match (old, new) {
        (None, None) => false,
        (None, _) | (_, None) => true,
        (Some(a), Some(b)) => (a.0 - b.0).abs() > tolerance || (a.1 - b.1).abs() > tolerance || (a.2 - b.2).abs() > tolerance || (a.3 - b.3).abs() > tolerance,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PROBE: &str = "WINDOW|0|0|1728|1013\nE|AXSplitter||a|b|d|1000|40|4|600\nE|AXImage|x|0|0|1010|300|200|20\nE|AXPopUpButton|q|0|0|1010|700|100|20";

    #[test]
    fn finds_the_pane() {
        match preview_pane_rect(PROBE) {
            Pane::Found { region, .. } => assert!(region.2 >= 200.0 && region.3 >= 80.0),
            other => panic!("{other:?}"),
        }
    }
    #[test]
    fn no_window_no_elements_no_splitter() {
        assert_eq!(preview_pane_rect(""), Pane::None);
        assert_eq!(preview_pane_rect("WINDOW|0|0|1728|1013"), Pane::None);
        assert_eq!(preview_pane_rect("WINDOW|0|0|1728|1013\nE|AXButton||a|b|d|1|1|1|1"), Pane::None);
    }
    #[test]
    fn nan_is_declined() {
        assert_eq!(preview_pane_rect("WINDOW|0|0|nan|1013"), Pane::Decline);
    }
    #[test]
    fn fill_and_changed() {
        assert_eq!(fill_region((0.0, 0.0, 100.0, 100.0), 16.0 / 9.0), (0.0, 0.0, 100.0, 100.0));
        let r = fill_region((0.0, 0.0, 1566.0, 580.0), 16.0 / 9.0);
        assert!(r.2 < 1566.0 && r.0 > 0.0);
        assert!(!region_changed(None, None, 2.0));
        assert!(region_changed(None, Some((0.0, 0.0, 1.0, 1.0)), 2.0));
        assert!(!region_changed(Some((0.0, 0.0, 10.0, 10.0)), Some((1.0, 1.0, 11.0, 11.0)), 2.0));
        assert!(region_changed(Some((0.0, 0.0, 10.0, 10.0)), Some((3.0, 0.0, 10.0, 10.0)), 2.0));
    }
}
