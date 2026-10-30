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

    /// `QRect::intersects` as Qt 6.11 behaves (found by probing it, see `tools/window_rules_parity.py`): a null rectangle (width and height both 0) intersects
    /// nothing; otherwise both rectangles are normalised (a negative size is turned around: x=10, w=-3 covers 7..=9) and they intersect when
    /// `a.left <= b.right && b.left <= a.right` on both axes, with `right = x + w - 1` left as it is for a zero size. So a rectangle of width 0 (and a
    /// height) still "touches" a screen its left edge lies inside of, which matters for a saved window rectangle with a size of 0.
    pub fn intersects(&self, o: &Rect) -> bool {
        if (self.w == 0 && self.h == 0) || (o.w == 0 && o.h == 0) {
            return false;
        }
        fn span(p: i32, len: i32) -> (i64, i64) {
            let (p, len) = (p as i64, len as i64);
            let end = p + len - 1;
            if end < p - 1 { (end + 1, p - 1) } else { (p, end) }
        }
        let (l1, r1) = span(self.x, self.w);
        let (l2, r2) = span(o.x, o.w);
        let (t1, b1) = span(self.y, self.h);
        let (t2, b2) = span(o.y, o.h);
        l1 <= r2 && l2 <= r1 && t1 <= b2 && t2 <= b1
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

/// A value of the saved `karaoke_window_pos` dictionary as Python's `dict.get` sees it: the key may be missing, hold `None` (JSON `null`) or a number.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Val {
    Absent,
    Null,
    Int(i64),
}

impl Val {
    /// `int(d.get(key, default))`: a missing key takes the default, a `null` is a TypeError (the app swallows it and sets no geometry).
    fn plain(self, default: i64) -> Option<i64> {
        match self {
            Val::Absent => Some(default),
            Val::Null => None,
            Val::Int(n) => Some(n),
        }
    }

    /// `int(d.get(key, default) or default)`: missing, `null` and 0 all take the default.
    fn or_default(self, default: i64) -> i64 {
        match self {
            Val::Int(n) if n != 0 => n,
            _ => default,
        }
    }

    /// `int(d.get(key, 0) or 0)`.
    fn or_zero(self) -> i64 {
        self.or_default(0)
    }
}

/// The saved audience-window geometry (`settings["karaoke_window_pos"]`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct SavedPos {
    pub x: Val,
    pub y: Val,
    pub width: Val,
    pub height: Val,
}

fn clamp_i32(n: i64) -> i32 {
    n.clamp(i32::MIN as i64, i32::MAX as i64) as i32
}

/// The two places that restore the window without a first-run default (the show-window construction and `toggle_karaoke_window` when the window is hidden):
/// `QRect(int(get("x",100)), int(get("y",100)), int(get("width",640)), int(get("height",520)))` through `_rect_on_attached_screen`. `None` when a field is
/// `null` (the app's `int(None)` raises and no geometry is set). A stored 0 is kept here (it is only turned into the default in `place_video_window`).
pub fn restore_rect(saved: &SavedPos, screens: &[Rect], primary: Option<Rect>) -> Option<Rect> {
    // `if saved_pos:` is false for an empty dictionary
    if [saved.x, saved.y, saved.width, saved.height].iter().all(|v| *v == Val::Absent) {
        return None;
    }
    let rect = Rect::new(
        clamp_i32(saved.x.plain(100)?),
        clamp_i32(saved.y.plain(100)?),
        clamp_i32(saved.width.plain(640)?),
        clamp_i32(saved.height.plain(520)?),
    );
    Some(rect_on_attached_screen(rect, screens, primary))
}

/// The first-run size of the audience window and its margin from the screen corner (`_place_video_window_bottom_right`).
pub const DEFAULT_SIZE: (i32, i32) = (480, 360);

/// What `_place_video_window_bottom_right` does to the window.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub struct Placement {
    /// `setGeometry(...)` (the saved rectangle, guarded), when the app sets one.
    pub geometry: Option<Rect>,
    /// `resize(480, 360)` (first run only).
    pub resize: Option<(i32, i32)>,
    /// `move(x, y)` (first run only).
    pub move_to: Option<(i32, i32)>,
    /// `show()` + `raise_()`: only when the window is not visible yet.
    pub show: bool,
}

/// `_place_video_window_bottom_right`: the startup placement of the audience window.
///
/// * a saved rectangle with a positive width and height (`int(width or 0) > 0`) is restored through the attached-screen guard, with `or` defaults: a saved
///   `x` or `y` of 0 becomes 100 and a missing width/height becomes 640/520 (so a window saved at x=0 comes back at x=100). A field that cannot be read
///   leaves the geometry unset, the window is still shown.
/// * otherwise, unless the window is already fullscreen or maximised, it is resized to 480x360 and moved to the bottom right of the screen under the
///   cursor (the primary screen when the cursor is on none), 24 px from the edges and never closer than 10 px to them. `cursor_screen` and `primary` are the
///   screens' AVAILABLE rectangles. With no screen at all it is only resized.
pub fn place_video_window(
    saved: Option<&SavedPos>,
    fullscreen_or_maximized: bool,
    already_visible: bool,
    cursor_screen: Option<Rect>,
    primary: Option<Rect>,
    screens: &[Rect],
) -> Placement {
    let mut out = Placement::default();
    if let Some(sp) = saved
        && sp.width.or_zero() > 0
        && sp.height.or_zero() > 0
    {
        let rect = Rect::new(
            clamp_i32(sp.x.or_default(100)),
            clamp_i32(sp.y.or_default(100)),
            clamp_i32(sp.width.or_default(640)),
            clamp_i32(sp.height.or_default(520)),
        );
        out.geometry = Some(rect_on_attached_screen(rect, screens, primary));
        out.show = !already_visible;
        return out;
    }
    if fullscreen_or_maximized {
        return out;
    }
    let (vw, vh) = DEFAULT_SIZE;
    out.resize = Some((vw, vh));
    let Some(avail) = cursor_screen.or(primary) else { return out };
    let (sx, sy, sw, sh) = (avail.x, avail.y, avail.w, avail.h);
    let (pad_x, pad_y) = (24, 24);
    let mut x = sx + sw - vw - pad_x;
    let mut y = sy + sh - vh - pad_y;
    x = (sx + 10).max(x.min(sx + sw - vw - 10));
    y = (sy + 10).max(y.min(sy + sh - vh - 10));
    out.move_to = Some((x, y));
    out.show = !already_visible;
    out
}

/// `QRect::contains(QPoint)` (the right and bottom edges are inside).
pub fn rect_contains_point(r: &Rect, px: i32, py: i32) -> bool {
    r.w > 0 && r.h > 0 && px >= r.x && px <= r.x + r.w - 1 && py >= r.y && py <= r.y + r.h - 1
}

/// `QApplication.screenAt(point)`: the first screen (in Qt's order) whose geometry contains the point.
pub fn screen_at(screens: &[Rect], px: i32, py: i32) -> Option<usize> {
    screens.iter().position(|s| rect_contains_point(s, px, py))
}

/// The screen the app treats as the audience window's: `QApplication.screenAt(frame.center()) or vw.screen() or QApplication.primaryScreen()`
/// (KaraFun hand-off and restore). `screens` are the GEOMETRIES (not the available areas); `window_screen` and `primary` are indices into them.
pub fn screen_for_window(screens: &[Rect], frame: Rect, window_screen: Option<usize>, primary: Option<usize>) -> Option<usize> {
    // QRect::center(): (x1 + x2) / 2 with integer division that truncates toward zero, on the inclusive corners
    let cx = ((frame.x as i64 + (frame.x as i64 + frame.w as i64 - 1)) / 2) as i32;
    let cy = ((frame.y as i64 + (frame.y as i64 + frame.h as i64 - 1)) / 2) as i32;
    screen_at(screens, cx, cy).or(window_screen).or(primary)
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

    #[test]
    fn every_side_counts_for_intersection() {
        let o = Rect::new(100, 100, 50, 50); // covers 100..=149
        // one pixel of overlap on each side intersects, one pixel short does not
        assert!(Rect::new(50, 100, 51, 50).intersects(&o));
        assert!(!Rect::new(50, 100, 50, 50).intersects(&o));
        assert!(Rect::new(149, 100, 50, 50).intersects(&o));
        assert!(!Rect::new(150, 100, 50, 50).intersects(&o));
        assert!(Rect::new(100, 50, 50, 51).intersects(&o));
        assert!(!Rect::new(100, 50, 50, 50).intersects(&o));
        assert!(Rect::new(100, 149, 50, 50).intersects(&o));
        assert!(!Rect::new(100, 150, 50, 50).intersects(&o));
        // symmetric, and a rectangle that only crosses on one axis does not intersect
        assert!(o.intersects(&Rect::new(149, 149, 5, 5)));
        assert!(!Rect::new(100, 300, 50, 50).intersects(&o));
        assert!(!Rect::new(300, 100, 50, 50).intersects(&o));
    }

    #[test]
    fn null_rectangles_never_intersect_but_zero_widths_can() {
        let o = Rect::new(0, 0, 100, 100);
        // both sides zero: null
        assert!(!Rect::new(10, 10, 0, 0).intersects(&o));
        assert!(!o.intersects(&Rect::new(10, 10, 0, 0)));
        // one side zero: Qt's extent is x ..= x-1, which still overlaps a screen the left edge is inside of
        assert!(Rect::new(10, 10, 0, 5).intersects(&o));
        assert!(Rect::new(10, 10, 5, 0).intersects(&o));
        assert!(o.intersects(&Rect::new(10, 10, 0, 5)));
        assert!(!Rect::new(500, 10, 0, 5).intersects(&o));
        assert!(!Rect::new(10, 500, 5, 0).intersects(&o));
        // a negative size is turned around: x=102, w=-3 covers 99..=101 and touches 0..=99; x=103 covers 100..=102 and does not
        assert!(Rect::new(102, 10, -3, 5).intersects(&o));
        assert!(!Rect::new(103, 10, -3, 5).intersects(&o));
        assert!(Rect::new(10, 102, 5, -3).intersects(&o));
        assert!(!Rect::new(10, 103, 5, -3).intersects(&o));
        // and from the other side: x=3, w=-3 covers 0..=2; x=0, w=-3 covers -3..=-1
        assert!(Rect::new(3, 10, -3, 5).intersects(&o));
        assert!(!Rect::new(0, 10, -3, 5).intersects(&o));
        // a zero width at x spans x..=x-1: it touches when x <= the screen's right edge and x - 1 >= its left edge
        assert!(Rect::new(99, 10, 0, 5).intersects(&o) && !Rect::new(100, 10, 0, 5).intersects(&o));
        assert!(Rect::new(1, 10, 0, 5).intersects(&o) && !Rect::new(0, 10, 0, 5).intersects(&o));
    }

    #[test]
    fn placement_details() {
        let screens = [Rect::new(-1920, 0, 1920, 1080), Rect::new(0, 0, 1440, 900)];
        let primary = Some(screens[1]);
        // moved: centred inside the primary's own origin (not at 0,0)
        assert_eq!(rect_on_attached_screen(Rect::new(9000, 9000, 400, 300), &screens, Some(Rect::new(-1920, 0, 1920, 1080))), Rect::new(-1160, 390, 400, 300));
        // no primary: left as it is
        assert_eq!(rect_on_attached_screen(Rect::new(9000, 9000, 400, 300), &screens, None), Rect::new(9000, 9000, 400, 300));
        // a zero-sized rectangle off every screen gets a width of at least 1 but is centred with its real width
        assert_eq!(rect_on_attached_screen(Rect::new(5000, 0, 0, 10), &screens, primary), Rect::new(720, 445, 1, 10));
        assert_eq!(rect_on_attached_screen(Rect::new(5000, 0, 10, 0), &screens, primary), Rect::new(715, 450, 10, 1));
        // taller than the primary only: shrunk on that axis
        assert_eq!(rect_on_attached_screen(Rect::new(5000, 0, 400, 5000), &screens, primary), Rect::new(520, 0, 400, 900));
        // wider than the primary only
        assert_eq!(rect_on_attached_screen(Rect::new(5000, 0, 5000, 300), &screens, primary), Rect::new(0, 300, 1440, 300));
        // one screen touching is enough
        assert_eq!(rect_on_attached_screen(Rect::new(-10, 10, 5, 5), &screens, primary), Rect::new(-10, 10, 5, 5));
    }

    #[test]
    fn restore_uses_plain_defaults_and_the_guard() {
        let screens = [Rect::new(0, 0, 1920, 1080)];
        let p = Some(screens[0]);
        // an empty dictionary is falsy: nothing is restored; one key present is enough, the others take their defaults
        let none = SavedPos { x: Val::Absent, y: Val::Absent, width: Val::Absent, height: Val::Absent };
        assert_eq!(restore_rect(&none, &screens, p), None);
        let only_x = SavedPos { x: Val::Int(50), ..none };
        assert_eq!(restore_rect(&only_x, &screens, p), Some(Rect::new(50, 100, 640, 520)));
        // a stored 0 stays 0 here (only place_video_window turns it into 100)
        let zero = SavedPos { x: Val::Int(0), y: Val::Int(0), width: Val::Int(300), height: Val::Int(200) };
        assert_eq!(restore_rect(&zero, &screens, p), Some(Rect::new(0, 0, 300, 200)));
        // null anywhere: no geometry
        let null = SavedPos { x: Val::Null, ..zero };
        assert_eq!(restore_rect(&null, &screens, p), None);
        // saved on a projector that is gone: moved to the middle of the primary
        let gone = SavedPos { x: Val::Int(3000), y: Val::Int(100), width: Val::Int(800), height: Val::Int(600), };
        assert_eq!(restore_rect(&gone, &screens, p), Some(Rect::new(560, 240, 800, 600)));
    }

    #[test]
    fn startup_placement_restores_saved_with_or_defaults() {
        let screens = [Rect::new(0, 0, 1920, 1080)];
        let p = Some(screens[0]);
        let saved = SavedPos { x: Val::Int(0), y: Val::Int(0), width: Val::Int(640), height: Val::Int(480) };
        let pl = place_video_window(Some(&saved), false, false, p, p, &screens);
        assert_eq!(pl.geometry, Some(Rect::new(100, 100, 640, 480)), "a window saved at 0,0 comes back at 100,100");
        assert!(pl.show && pl.resize.is_none() && pl.move_to.is_none());
        // already visible: no show; fullscreen does not stop the restore
        assert!(!place_video_window(Some(&saved), true, true, p, p, &screens).show);
        assert!(place_video_window(Some(&saved), true, true, p, p, &screens).geometry.is_some());
        // zero or missing size: not a saved geometry, first-run placement instead
        for bad in [Val::Int(0), Val::Null, Val::Absent] {
            let s = SavedPos { width: bad, ..saved };
            assert!(place_video_window(Some(&s), false, false, p, p, &screens).resize.is_some());
            let s = SavedPos { height: bad, ..saved };
            assert!(place_video_window(Some(&s), false, false, p, p, &screens).resize.is_some());
        }
        // a negative size is "positive-checked" away too
        let neg = SavedPos { width: Val::Int(-5), ..saved };
        assert!(place_video_window(Some(&neg), false, false, p, p, &screens).resize.is_some());
        // saved rectangle on a screen that is gone is guarded
        let gone = SavedPos { x: Val::Int(5000), y: Val::Int(5000), width: Val::Int(400), height: Val::Int(300) };
        assert_eq!(place_video_window(Some(&gone), false, false, p, p, &screens).geometry, Some(Rect::new(760, 390, 400, 300)));
    }

    #[test]
    fn first_run_goes_bottom_right_of_the_cursor_screen() {
        let a = Rect::new(0, 25, 1440, 875); // available area under the menu bar
        let b = Rect::new(1440, 0, 1920, 1080);
        let pl = place_video_window(None, false, false, Some(b), Some(a), &[a, b]);
        assert_eq!(pl.resize, Some((480, 360)));
        assert_eq!(pl.move_to, Some((1440 + 1920 - 480 - 24, 1080 - 360 - 24)));
        assert!(pl.show && pl.geometry.is_none());
        // cursor on no screen: the primary
        let pl = place_video_window(None, false, true, None, Some(a), &[a, b]);
        assert_eq!(pl.move_to, Some((1440 - 480 - 24, 25 + 875 - 360 - 24)));
        assert!(!pl.show);
        // no screen at all: resized only, not moved or shown
        let pl = place_video_window(None, false, false, None, None, &[]);
        assert_eq!((pl.resize, pl.move_to, pl.show), (Some((480, 360)), None, false));
        // fullscreen or maximised: nothing happens
        assert_eq!(place_video_window(None, true, false, Some(a), Some(a), &[a]), Placement::default());
        // a screen smaller than the window plus margins: the clamp keeps it 10 px inside the top left
        let tiny = Rect::new(0, 0, 400, 300);
        assert_eq!(place_video_window(None, false, false, Some(tiny), Some(tiny), &[tiny]).move_to, Some((10, 10)));
        // exactly fitting: 24 px margin kept, lower clamp not reached
        let fit = Rect::new(100, 200, 600, 500);
        assert_eq!(place_video_window(None, false, false, Some(fit), Some(fit), &[fit]).move_to, Some((100 + 600 - 480 - 24, 200 + 500 - 360 - 24)));
    }

    #[test]
    fn screen_for_window_prefers_the_centre_then_the_window_then_the_primary() {
        let s = [Rect::new(0, 0, 1000, 800), Rect::new(1000, 0, 1000, 800)];
        // centre of a window straddling the two screens: (800+1200)/2... frame 800..1199 -> centre 999 (left screen)
        assert_eq!(screen_for_window(&s, Rect::new(800, 100, 400, 200), Some(1), Some(0)), Some(0));
        assert_eq!(screen_for_window(&s, Rect::new(801, 100, 400, 200), Some(0), Some(0)), Some(1));
        // centre on no screen: the window's own, then the primary
        assert_eq!(screen_for_window(&s, Rect::new(5000, 0, 10, 10), Some(1), Some(0)), Some(1));
        assert_eq!(screen_for_window(&s, Rect::new(5000, 0, 10, 10), None, Some(0)), Some(0));
        assert_eq!(screen_for_window(&s, Rect::new(5000, 0, 10, 10), None, None), None);
        // the right and bottom edge pixel belong to the screen, the next one does not
        assert_eq!(screen_at(&s, 999, 799), Some(0));
        assert_eq!(screen_at(&s, 1000, 800), None);
        assert_eq!(screen_at(&s, -1, 0), None);
        // negative centres truncate toward zero like QRect::center
        let n = [Rect::new(-1000, 0, 1000, 800)];
        assert_eq!(screen_for_window(&n, Rect::new(-1000, 0, 1, 1), None, None), Some(0));
    }
}
