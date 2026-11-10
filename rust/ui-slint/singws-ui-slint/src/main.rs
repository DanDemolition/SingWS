//! Slint host window, first slice: Ticker and Waitlist pages, read-only, refreshed every 3 s like the Qt shell. `SINGWS_UI_TAB=1` opens on the Waitlist.
slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use std::time::{Duration, SystemTime};
use singws_ui_slint::{home, ticker_page, waitlist_page};

fn refresh(app: &App, dir: &str) {
    let t = ticker_page::load(dir);
    app.set_ticker_text(t.text.into());
    app.set_ticker_message(t.message.into());
    app.set_ticker_status(t.status.into());
    app.set_singers(ModelRc::new(VecModel::from(t.singers.iter().map(|s| SharedString::from(s.as_str())).collect::<Vec<_>>())));
    let w = waitlist_page::load(dir, SystemTime::now());
    app.set_waitlist_status(w.status.into());
    let items: Vec<ListItem> = w
        .items
        .iter()
        .map(|i| ListItem { header: i.header, section: i.section.as_str().into(), title: i.title.as_str().into(), helper: i.helper.as_str().into(), text: i.text.as_str().into(), first_time: i.first_time, tag: i.tag.as_str().into() })
        .collect();
    app.set_waitlist(ModelRc::new(VecModel::from(items)));
}

fn main() {
    let dir = home();
    let app = App::new().unwrap();
    refresh(&app, &dir);
    if let Ok(t) = std::env::var("SINGWS_UI_TAB") {
        app.set_start_tab(t.parse().unwrap_or(0));
    }
    let timer = slint::Timer::default();
    let weak = app.as_weak();
    timer.start(slint::TimerMode::Repeated, Duration::from_secs(3), move || {
        if let Some(a) = weak.upgrade() {
            refresh(&a, &dir);
        }
    });
    app.run().unwrap();
}
