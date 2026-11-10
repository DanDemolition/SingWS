//! Slint host window, first slice: Ticker and Waitlist pages, read-only, refreshed every 3 s like the Qt shell. `SINGWS_UI_TAB=1` opens on the Waitlist.
slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use std::time::{Duration, SystemTime};
use serde_json::Value;
use singws_ui_slint::{control, home, ticker_page, waitlist_page};
use std::{cell::RefCell, rc::Rc};

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
        .map(|i| ListItem { header: i.header, section: i.section.as_str().into(), title: i.title.as_str().into(), helper: i.helper.as_str().into(), text: i.text.as_str().into(), first_time: i.first_time, tag: i.tag.as_str().into(), id: i.request_id as i32, singer: i.singer.as_str().into(), artist: i.artist.as_str().into(), song: i.song.as_str().into(), key: i.key as i32, tempo: i.tempo as i32 })
        .collect();
    app.set_waitlist(ModelRc::new(VecModel::from(items)));
}

fn show_control(app: &App, c: &control::Controller) {
    let st = c.state();
    app.set_conn_status(st.status.into());
    // SINGWS_UI_FORCE_LIVE=1 shows the buttons without a connection (layout check only: every press then answers "not connected")
    app.set_live(st.enabled || std::env::var("SINGWS_UI_FORCE_LIVE").is_ok());
    let (text, kind) = st.result.unwrap_or_default();
    app.set_result_text(text.into());
    app.set_result_kind(kind.into());
}

fn main() {
    let dir = home();
    let app = App::new().unwrap();
    let ctl = Rc::new(RefCell::new(control::Controller::new()));
    let (url, user) = control::load_saved(&control::config_path());
    app.set_server_url(url.into());
    app.set_tenant(user.into());
    refresh(&app, &dir);
    show_control(&app, &ctl.borrow());
    if let Ok(t) = std::env::var("SINGWS_UI_TAB") {
        app.set_start_tab(t.parse().unwrap_or(0));
    }
    {
        let c = ctl.clone();
        app.on_connect(move |url, user, pin| c.borrow_mut().connect(&url, &user, &pin));
        let c = ctl.clone();
        app.on_disconnect(move || c.borrow_mut().disconnect());
        let c = ctl.clone();
        app.on_wl_add(move |id, who| c.borrow().send(&format!("Add {who}"), "waitlist.add", control::request_args(id as i64)));
        let c = ctl.clone();
        app.on_wl_remove(move |id, who| c.borrow().send(&format!("Remove {who}"), "waitlist.remove", control::request_args(id as i64)));
        let c = ctl.clone();
        app.on_wl_restore(move |id, who| c.borrow().send(&format!("Restore {who}"), "waitlist.restore", control::request_args(id as i64)));
        let c = ctl.clone();
        app.on_wl_edit(move |id, singer, artist, title, key, tempo| {
            let args = control::edit_args(id as i64, &singer, &artist, &title, key as i64, tempo as i64);
            c.borrow().send(&format!("Edit {singer}"), "waitlist.edit", args)
        });
        let c = ctl.clone();
        app.on_save_message(move |m| c.borrow().send("Save ticker message", "settings.set", control::setting_args("ticker_custom_message", Value::from(m.as_str()))));
        let c = ctl.clone();
        app.on_set_message_shown(move |on| c.borrow().send(if on { "Show the ticker message" } else { "Hide the ticker message" }, "settings.set", control::setting_args("ticker_custom_enabled", Value::from(on))));
    }
    // files every 3 s like the Qt shell; the connection state is cheap, so it is read four times a second
    let files = slint::Timer::default();
    let weak = app.as_weak();
    let d = dir.clone();
    files.start(slint::TimerMode::Repeated, Duration::from_secs(3), move || {
        if let Some(a) = weak.upgrade() {
            refresh(&a, &d);
        }
    });
    let conn = slint::Timer::default();
    let weak = app.as_weak();
    let c = ctl.clone();
    conn.start(slint::TimerMode::Repeated, Duration::from_millis(250), move || {
        if let Some(a) = weak.upgrade() {
            show_control(&a, &c.borrow());
        }
    });
    app.run().unwrap();
}
