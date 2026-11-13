slint::include_modules!();
fn main() {
    let o = Overlay::new().unwrap();
    let s = spike_core::load(&spike_core::home(), true);
    o.set_ticker_text(s.ticker_text.into());
    let (x, y, w, h): (i32, i32, i32, i32) = std::env::var("OVERLAY_RECT").ok().and_then(|v| { let p: Vec<i32> = v.split(',').filter_map(|n| n.parse().ok()).collect(); (p.len() == 4).then(|| (p[0], p[1], p[2], p[3])) }).unwrap_or((100, 600, 1280, 80));
    o.set_strip_w(w as f32);
    o.window().set_position(slint::LogicalPosition::new(x as f32, y as f32));
    o.window().set_size(slint::LogicalSize::new(w as f32, h as f32));
    o.run().unwrap();
}
