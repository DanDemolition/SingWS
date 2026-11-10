slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use std::rc::Rc;
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
    eprintln!("startup_to_run_ms={}", t0.elapsed().as_millis());
    app.run().unwrap();
}
