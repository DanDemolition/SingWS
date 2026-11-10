slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use std::rc::Rc;
use slint::Model;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};

fn apply(app: &App, dir: &str, custom: bool) {
    let s = spike_core::load(dir, custom);
    app.set_ticker_text(s.ticker_text.into());
    app.set_waitlist_status(s.waitlist_status.into());
    let secs: Vec<SectionData> = s.waitlist.iter().map(|x| SectionData { title: x.title.as_str().into(), rows: ModelRc::new(VecModel::from(x.rows.iter().map(|r| RowData { status: SharedString::from(r.status.as_str()), text: SharedString::from(r.text.as_str()) }).collect::<Vec<_>>())) }).collect();
    app.set_sections(ModelRc::new(VecModel::from(secs)));
    if let Some(h) = s.hero { app.set_hero_kicker(h.kicker.into()); app.set_hero_singer(h.singer.into()); app.set_hero_sub(h.subtitle.into()); app.set_hero_left(h.remaining.into()); }
}
fn main() {
    let t0 = std::time::Instant::now();
    let dir = spike_core::home();
    let app = App::new().unwrap();
    let custom = Arc::new(AtomicBool::new(true));
    apply(&app, &dir, true);
    if let Ok(t) = std::env::var("SPIKE_TAB") { app.set_start_tab(t.parse().unwrap_or(0)); }
    { let (w, d, c) = (app.as_weak(), dir.clone(), custom.clone()); app.on_custom_toggled(move |on| { c.store(on, Ordering::Relaxed); if let Some(a) = w.upgrade() { apply(&a, &d, on); } }); }
    // live state: poll the snapshot files once a second (what the shell's refresh timer does)
    let timer = slint::Timer::default();
    { let (w, d, c) = (app.as_weak(), dir.clone(), custom.clone()); timer.start(slint::TimerMode::Repeated, std::time::Duration::from_secs(1), move || { if let Some(a) = w.upgrade() { apply(&a, &d, c.load(Ordering::Relaxed)); } }); }
    let db = std::env::var("SPIKE_DB").unwrap_or_default();
    let last = Rc::new(std::cell::RefCell::new(Vec::<i64>::new()));
    { let (w, db, last) = (app.as_weak(), db.clone(), last.clone()); app.on_search(move |q| {
        let (rows, ms, fuzzy) = spike_core::library_search(&db, q.as_str(), 20000);
        eprintln!("search {:?}: {} rows {} ms fuzzy={}", q.as_str(), rows.len(), ms, fuzzy);
        *last.borrow_mut() = rows.iter().map(|r| r.id).collect();
        if let Some(a) = w.upgrade() {
            a.set_lib_status(format!("{} songs in {} ms{}", rows.len(), ms, if fuzzy { " (typo-tolerant)" } else { "" }).into());
            a.set_songs(ModelRc::new(VecModel::from(rows.iter().map(|r| SongData { artist: r.artist.as_str().into(), title: r.title.as_str().into(), meta: r.meta.as_str().into() }).collect::<Vec<_>>())));
        } }); }
    { let last = last.clone(); app.on_add(move |i| { eprintln!("ADD clicked: song id {:?}", last.borrow().get(i as usize)); }); }
    if let Ok(q) = std::env::var("SPIKE_QUERY") { app.invoke_search(q.into()); }
    // chat: emoji, markdown (bold/italic/link/list/colour), a still picture, and an animated GIF driven by a timer
    let md = |t: &str| slint::private_unstable_api::re_exports::StyledText::from_markdown(t).unwrap_or_default();
    let still = {
        let mut b = slint::SharedPixelBuffer::<slint::Rgba8Pixel>::new(160, 90);
        for (i, px) in b.make_mut_slice().iter_mut().enumerate() { let (x, y) = ((i % 160) as u8, (i / 160) as u8); *px = slint::Rgba8Pixel { r: x.wrapping_mul(2), g: y.wrapping_mul(3), b: 180, a: 255 }; }
        slint::Image::from_rgba8(b)
    };
    // build a real GIF in memory, decode it back with the gif decoder the app would use
    let frames: Vec<slint::Image> = {
        use image::{AnimationDecoder, codecs::gif::{GifDecoder, GifEncoder}, Frame, RgbaImage};
        let mut bytes = Vec::new();
        { let mut enc = GifEncoder::new(&mut bytes); for f in 0..8u8 { let img = RgbaImage::from_fn(80, 80, |x, y| image::Rgba([f * 30, (x as u8).wrapping_add(f * 20), y as u8, 255])); enc.encode_frame(Frame::new(img)).unwrap(); } }
        GifDecoder::new(std::io::Cursor::new(bytes)).unwrap().into_frames().map(|f| { let r = f.unwrap().into_buffer(); let mut b = slint::SharedPixelBuffer::<slint::Rgba8Pixel>::new(r.width(), r.height()); b.make_mut_slice().copy_from_slice(unsafe { std::slice::from_raw_parts(r.as_raw().as_ptr() as *const slint::Rgba8Pixel, (r.width() * r.height()) as usize) }); slint::Image::from_rgba8(b) }).collect()
    };
    let mk = |who: &str, host: bool, text: &str, pic: Option<slint::Image>| ChatMsg { who: who.into(), host, body: md(text), has_image: pic.is_some(), pic: pic.unwrap_or_default() };
    let model = Rc::new(VecModel::from(vec![
        mk("Ann", false, "Hello \u{1F44B}\u{1F3A4} **loud and proud** \u{1F3B6}\u{1F525} _italic_ ~~gone~~", None),
        mk("Host", true, "Next up: <font color=\"#b6ff3b\">Cleo</font> - tip the DJ!\n\n- request at the QR\n- [singws.com](https://singws.com)", None),
        mk("Bo", false, "pic time \u{1F600}\u{1F44D}\u{2764}\u{FE0F}", Some(still)),
        mk("Cleo", false, "gif:", frames.first().cloned()),
    ]));
    app.set_chat(ModelRc::from(model.clone()));
    let gif_timer = slint::Timer::default();
    { let (m, fr) = (model.clone(), frames.clone()); let mut i = 0usize; gif_timer.start(slint::TimerMode::Repeated, std::time::Duration::from_millis(100), move || { i = (i + 1) % fr.len(); let mut d = m.row_data(3).unwrap(); d.pic = fr[i].clone(); m.set_row_data(3, d); }); }
    eprintln!("startup_to_run_ms={}", t0.elapsed().as_millis());
    app.run().unwrap();
}
