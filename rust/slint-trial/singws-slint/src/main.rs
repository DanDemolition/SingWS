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
    let st = panels::status_view(dir, std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0));
    app.set_st_note(st.note.into()); app.set_st_stale(st.stale); app.set_st_has(st.has);
    app.set_st_chips(ModelRc::new(VecModel::from(st.chips.iter().map(|c| ChipItem { text: c.text.as_str().into(), tone: c.tone.as_str().into() }).collect::<Vec<_>>())));
    app.set_st_status(st.status_text.into()); app.set_st_warn(st.warn_dot); app.set_st_location(st.location.into()); app.set_st_tooltip(st.tooltip.into());
    app.set_st_eta(st.eta.into()); app.set_st_summary(st.summary.into()); app.set_st_report(st.report_title.into());
    app.set_st_checks(ModelRc::new(VecModel::from(st.checks.iter().map(|(ok, t)| CheckItem { ok: *ok, text: t.as_str().into() }).collect::<Vec<_>>())));
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
