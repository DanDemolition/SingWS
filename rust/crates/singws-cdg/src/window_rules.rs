//! Display-window placement rules lifted from the app so the Rust display window can follow them. Pure functions of rectangles: no Qt.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

impl Rect {
    pub fn new(x: i32, y: i32, w: i32, h: i32) -> Self {
        Rect { x, y, w, h }
    }

    /// `QRect::intersects` for rectangles with a positive size (anything else is not asked by the rules below and answers false).
    pub fn intersects(&self, o: &Rect) -> bool {
        if self.w <= 0 || self.h <= 0 || o.w <= 0 || o.h <= 0 {
            return false;
        }
        let (r1, b1, r2, b2) = (self.x + self.w - 1, self.y + self.h - 1, o.x + o.w - 1, o.y + o.h - 1);
        self.x <= r2 && o.x <= r1 && self.y <= b2 && o.y <= b1
    }
}

/// `_rect_on_attached_screen`: keep a saved show-window rectangle on a screen that exists now. If it touches any screen's available area it is
/// returned unchanged; otherwise it is shrunk to fit and centred in the primary screen's available area (`None` primary: unchanged).
pub fn rect_on_attached_screen(rect: Rect, screens: &[Rect], primary: Option<Rect>) -> Rect {
    if screens.iter().any(|s| s.intersects(&rect)) {
        return rect;
    }
    let Some(avail) = primary else { return rect };
    let width = rect.w.min(avail.w);
    let height = rect.h.min(avail.h);
    Rect {
        x: avail.x + 0.max((avail.w - width).div_euclid(2)),
        y: avail.y + 0.max((avail.h - height).div_euclid(2)),
        w: width.max(1),
        h: height.max(1),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kept_when_touching_a_screen() {
        let screens = [Rect::new(0, 0, 1920, 1080)];
        let r = Rect::new(1900, 1000, 800, 600);
        assert_eq!(rect_on_attached_screen(r, &screens, Some(screens[0])), r);
    }

    #[test]
    fn moved_to_the_primary_when_off_every_screen() {
        let screens = [Rect::new(0, 0, 1920, 1080)];
        let r = Rect::new(3000, 100, 800, 600);
        assert_eq!(rect_on_attached_screen(r, &screens, Some(screens[0])), Rect::new(560, 240, 800, 600));
        // larger than the screen: shrunk
        let big = Rect::new(5000, 0, 4000, 3000);
        assert_eq!(rect_on_attached_screen(big, &screens, Some(screens[0])), Rect::new(0, 0, 1920, 1080));
    }

    #[test]
    fn edge_touching_is_not_intersecting() {
        assert!(!Rect::new(0, 0, 10, 10).intersects(&Rect::new(10, 0, 5, 5)));
        assert!(Rect::new(0, 0, 10, 10).intersects(&Rect::new(9, 9, 5, 5)));
    }
}
