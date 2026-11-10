slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use singws_slint::panels;
use std::time::{Duration, SystemTime};

fn apply(app: &App, dir: &str) {
    let t = panels::ticker_view(dir);
    app.set_ticker_text(t.text.into());
    app.set_ticker_message(t.message.into());
    app.set_ticker_status(t.status.into());
    let singers: Vec<SingerItem> = t.singers.iter().enumerate().map(|(i, n)| SingerItem { number: format!("{}.", i + 1).into(), name: n.as_str().into() }).collect();
    app.set_singers(ModelRc::new(VecModel::from(singers)));

    let w = panels::waitlist_view(dir, SystemTime::now());
    app.set_wait_status(w.status.into());
    let mut items = Vec::new();
    for s in &w.sections {
        items.push(WaitItem { is_header: true, key: s.key.as_str().into(), title: format!("{} ({})", s.title, s.rows.len()).into(), helper: s.helper.as_str().into(), text: SharedString::new(), first_time: false });
        for r in &s.rows {
            items.push(WaitItem { is_header: false, key: s.key.as_str().into(), title: SharedString::new(), helper: SharedString::new(), text: r.text.as_str().into(), first_time: r.first_time });
        }
    }
    app.set_wait_items(ModelRc::new(VecModel::from(items)));
}

fn main() {
    let dir = panels::home();
    let app = App::new().unwrap();
    if let Ok(t) = std::env::var("SLINT_TRIAL_TAB") { app.set_tab(t.parse().unwrap_or(0)); }
    apply(&app, &dir);
    // the Qt shell's pages refresh every 3 s while visible
    let timer = slint::Timer::default();
    let weak = app.as_weak();
    let d = dir.clone();
    timer.start(slint::TimerMode::Repeated, Duration::from_secs(3), move || { if let Some(a) = weak.upgrade() { apply(&a, &d); } });
    app.run().unwrap();
}
