slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use singws_slint::panels;
use std::cell::RefCell;
use std::rc::Rc;
use std::time::{Duration, SystemTime};

#[derive(Default)]
struct Ui { singer_search: String, selected: String, song_query: String, setting_query: String }

fn now_epoch() -> i64 { SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0) }

fn apply(app: &App, dir: &str, ui: &mut Ui) {
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
    let st = panels::status_view(dir, std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0));
    app.set_st_note(st.note.into()); app.set_st_stale(st.stale); app.set_st_has(st.has);
    app.set_st_chips(ModelRc::new(VecModel::from(st.chips.iter().map(|c| ChipItem { text: c.text.as_str().into(), tone: c.tone.as_str().into() }).collect::<Vec<_>>())));
    app.set_st_status(st.status_text.into()); app.set_st_warn(st.warn_dot); app.set_st_location(st.location.into()); app.set_st_tooltip(st.tooltip.into());
    app.set_st_eta(st.eta.into()); app.set_st_summary(st.summary.into()); app.set_st_report(st.report_title.into());
    app.set_st_checks(ModelRc::new(VecModel::from(st.checks.iter().map(|(ok, t)| CheckItem { ok: *ok, text: t.as_str().into() }).collect::<Vec<_>>())));
    let r = panels::rotation_view(dir);
    app.set_rot_title(r.title.into()); app.set_rot_count(r.count.into()); app.set_rot_status(r.status.into()); app.set_rot_now(r.now.into()); app.set_rot_next(r.next.into());
    app.set_rot_rows(ModelRc::new(VecModel::from(r.rows.iter().map(|x| RotItem { number: x.number.as_str().into(), singer: x.singer.as_str().into(), combined: x.combined.as_str().into(), duet: x.duet }).collect::<Vec<_>>())));
    let h = panels::history_view(dir, &ui.singer_search, &ui.selected, &ui.song_query, now_epoch());
    ui.selected = h.selected.clone();
    app.set_h_status(h.status.into()); app.set_h_name(h.name.into()); app.set_h_summary(h.summary.into()); app.set_h_meta(h.song_meta.into());
    app.set_h_singers(ModelRc::new(VecModel::from(h.singers.iter().map(|x| HistItem { key: x.key.as_str().into(), title: x.title.as_str().into(), detail: x.detail.as_str().into(), selectable: x.selectable, selected: x.key == h.selected }).collect::<Vec<_>>())));
    app.set_h_songs(ModelRc::new(VecModel::from(h.songs.iter().map(|x| HistItem { key: x.key.as_str().into(), title: x.title.as_str().into(), detail: x.detail.as_str().into(), selectable: false, selected: false }).collect::<Vec<_>>())));
    let st2 = panels::settings_view_data(dir, &ui.setting_query);
    app.set_set_status(st2.status.into());
    app.set_set_rows(ModelRc::new(VecModel::from(st2.rows.iter().map(|x| SettingItem { key: x.key.as_str().into(), value: x.value.as_str().into(), hidden: x.hidden }).collect::<Vec<_>>())));
    app.set_wait_items(ModelRc::new(VecModel::from(items)));
}

fn main() {
    let dir = panels::home();
    let app = App::new().unwrap();
    if let Ok(t) = std::env::var("SLINT_TRIAL_TAB") { app.set_tab(t.parse().unwrap_or(0)); }
    let ui = Rc::new(RefCell::new(Ui::default()));
    apply(&app, &dir, &mut ui.borrow_mut());
    // user input: update the state, then redraw
    macro_rules! hook { ($on:ident, $field:ident) => {{
        let (w, d, u) = (app.as_weak(), dir.clone(), ui.clone());
        app.$on(move |t| { u.borrow_mut().$field = t.to_string(); if let Some(a) = w.upgrade() { apply(&a, &d, &mut u.borrow_mut()); } });
    }}; }
    hook!(on_h_search, singer_search); hook!(on_h_pick, selected); hook!(on_h_song_search, song_query); hook!(on_set_search, setting_query);
    // the Qt shell's pages refresh every 3 s while visible
    let timer = slint::Timer::default();
    let (weak, d, u) = (app.as_weak(), dir.clone(), ui.clone());
    timer.start(slint::TimerMode::Repeated, Duration::from_secs(3), move || { if let Some(a) = weak.upgrade() { apply(&a, &d, &mut u.borrow_mut()); } });
    app.run().unwrap();
}
