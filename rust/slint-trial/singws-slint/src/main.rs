slint::include_modules!();
use slint::{ModelRc, SharedString, VecModel};
use singws_slint::{control, panels};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::{Duration, SystemTime};

#[derive(Default)]
struct Ui { chat_tab: i32, host_key: String, chat_sure: bool, chat_val: serde_json::Value, music_query: String, eq_bgm: Vec<f64>, eq_kar: Vec<f64>, eq_bgm_on: bool, eq_kar_on: bool, cutoff: i64, lib_query: String, singer_search: String, selected: String, song_query: String, setting_query: String }

fn now_epoch() -> i64 { SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0) }

/// Decode a thumbnail data address to a file in the temp dir and load it (the file name comes from the ref, which `valid_media_ref` limits to safe characters).
fn media_image(uri: &str, media_ref: &str) -> Option<slint::Image> {
    if uri.is_empty() { return None; }
    let bytes = control::decode_data_uri(uri)?;
    let dir = std::env::temp_dir().join("singws-slint-media");
    std::fs::create_dir_all(&dir).ok()?;
    let name: String = media_ref.chars().map(|c| if c.is_ascii_alphanumeric() { c } else { '_' }).collect();
    let path = dir.join(format!("{name}.jpg"));
    if !path.exists() { std::fs::write(&path, bytes).ok()?; }
    slint::Image::load_from_path(&path).ok()
}

fn apply_chat(app: &App, ui: &Ui, c: &control::Control) {
    let v = c.view();
    let tab = ["group", "dm", "host"][ui.chat_tab.clamp(0, 2) as usize];
    let now = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    // test hook (like the Qt shell's --chat-demo): show this summary instead of the app's
    let demo = std::env::var("SLINT_TRIAL_CHAT_DEMO").ok().and_then(|p| std::fs::read_to_string(p).ok()).and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok());
    let d = panels::chat_data(demo.as_ref().unwrap_or(&v.chat), tab, &ui.host_key, panels::local_utc_offset_min(now));
    app.set_chat_status(d.status.into());
    app.set_chat_tabs(ModelRc::new(VecModel::from(d.tab_labels.iter().map(|t| SharedString::from(t.as_str())).collect::<Vec<_>>())));
    // pictures: ask the app for up to 6 missing thumbnails (newest first), draw those that arrived
    if v.enabled { for it in d.items.iter().rev().filter(|i| !i.media_ref.is_empty() && c.media_uri(&i.media_ref).is_empty()).take(6) { c.request_media(&it.media_ref); } }
    app.set_chat_items(ModelRc::new(VecModel::from(d.items.iter().map(|i| {
        let pic = if i.media_ref.is_empty() { None } else { media_image(&c.media_uri(&i.media_ref), &i.media_ref) };
        ChatEntry { header: i.header, key: i.key.as_str().into(), text: i.text.as_str().into(), mine: i.mine, has_picture: pic.is_some(), picture: pic.unwrap_or_default() }
    }).collect::<Vec<_>>())));
    app.set_chat_tab(ui.chat_tab); app.set_chat_hint(d.hint.into());
    app.set_chat_can_say(v.enabled && ((tab == "group" && d.group_enabled) || (tab == "host" && !ui.host_key.is_empty())));
    app.set_chat_sure(ui.chat_sure);
}

fn apply_control(app: &App, c: &control::Control) {
    let v = c.view();
    app.set_bg_track(v.bg_track.as_str().into()); app.set_bg_playing(v.bg_playing); app.set_bg_volume(v.bg_volume as f32);
    app.set_c_status(v.status.into()); app.set_c_enabled(v.enabled); app.set_c_login_error(v.login_error.into());
    app.set_c_now(v.now.into()); app.set_c_clock(v.clock.into()); app.set_c_playing(v.playing); app.set_c_result(v.result_text.into()); app.set_c_result_kind(v.result_kind.into());
    app.set_c_logged_in(v.logged_in);
}

fn apply_library(app: &App, dir: &str, q: &str) {
    let l = panels::library_view(&panels::db_path(dir), q);
    app.set_lib_status(l.status.into());
    app.set_lib_rows(ModelRc::new(VecModel::from(l.rows.iter().map(|r| LibItem { id: r.id as i32, artist: r.artist.as_str().into(), title: r.title.as_str().into(), left: r.left.as_str().into(), right: r.right.as_str().into() }).collect::<Vec<_>>())));
}

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
    app.set_st_all_passed(st.all_passed);
    app.set_st_diag(ModelRc::new(VecModel::from(st.check_rows.iter().map(|c| DiagItem { ok: c.ok, label: c.label.as_str().into(), detail: c.detail.as_str().into() }).collect::<Vec<_>>())));
    match &st.hero {
        Some(h) => { app.set_hero_has(true); app.set_hero_live(h.kicker == "NOW SINGING"); app.set_hero_kicker(h.kicker.as_str().into()); app.set_hero_singer(h.singer.as_str().into()); app.set_hero_sub(h.subtitle.as_str().into()); app.set_hero_meta(h.meta.as_str().into()); app.set_hero_format(h.format.as_str().into()); app.set_hero_left(h.remaining.as_str().into()); }
        None => app.set_hero_has(false),
    }
    app.set_st_checks(ModelRc::new(VecModel::from(st.checks.iter().map(|(ok, t)| CheckItem { ok: *ok, text: t.as_str().into() }).collect::<Vec<_>>())));
    let r = panels::rotation_view(dir);
    app.set_rot_title(r.title.into()); app.set_rot_count(r.count.into()); app.set_rot_status(r.status.into()); app.set_rot_now(r.now.into()); app.set_rot_next(r.next.into());
    app.set_rot_rows(ModelRc::new(VecModel::from(r.rows.iter().map(|x| RotItem { number: x.number.as_str().into(), singer: x.singer.as_str().into(), combined: x.combined.as_str().into(), duet: x.duet }).collect::<Vec<_>>())));
    let h = panels::history_view(dir, &ui.singer_search, &ui.selected, &ui.song_query, now_epoch());
    ui.selected = h.selected.clone();
    app.set_h_status(h.status.into()); app.set_h_name(h.name.into()); app.set_h_summary(h.summary.into()); app.set_h_meta(h.song_meta.into());
    app.set_h_singers(ModelRc::new(VecModel::from(h.singers.iter().map(|x| HistItem { key: x.key.as_str().into(), title: x.title.as_str().into(), detail: x.detail.as_str().into(), selectable: x.selectable, selected: x.key == h.selected }).collect::<Vec<_>>())));
    app.set_h_songs(ModelRc::new(VecModel::from(h.songs.iter().map(|x| HistItem { key: x.key.as_str().into(), title: x.title.as_str().into(), detail: x.detail.as_str().into(), selectable: false, selected: false }).collect::<Vec<_>>())));
    let mu = panels::music_view(dir, &ui.music_query, if app.get_c_enabled() { ui.cutoff } else { -1 });
    app.set_mus_count(mu.count.into()); app.set_mus_info(mu.info.into()); app.set_mus_status(mu.status.into());
    app.set_mus_rows(ModelRc::new(VecModel::from(mu.rows.iter().map(|r| MusicItem { index: r.index as i32, filename: r.filename.as_str().into() }).collect::<Vec<_>>())));
    let eq = panels::eq_data(dir);
    app.set_eq_status(eq.status.clone().into());
    let bands = |g: &Vec<f64>| ModelRc::new(VecModel::from(eq.bands_hz.iter().zip(g.iter()).map(|(hz, g)| EqBand { label: (if *hz >= 1000.0 { format!("{}k", hz / 1000.0) } else { format!("{}", hz) }).into(), gain: *g as f32 }).collect::<Vec<_>>()));
    app.set_eq_bgm(bands(&eq.bgm.gains)); app.set_eq_kar(bands(&eq.karaoke.gains));
    app.set_eq_bgm_flat(eq.bgm.flat); app.set_eq_kar_flat(eq.karaoke.flat); app.set_eq_bgm_on(eq.bgm.enabled); app.set_eq_bgm_auto(eq.bgm.auto_enabled); app.set_eq_kar_on(eq.karaoke.enabled); app.set_eq_kar_auto(eq.karaoke.auto_enabled);
    ui.eq_bgm = eq.bgm.gains.clone(); ui.eq_kar = eq.karaoke.gains.clone(); ui.eq_bgm_on = eq.bgm.enabled; ui.eq_kar_on = eq.karaoke.enabled;
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
    // controls: PIN login + transport through singws-control (the PIN goes to the client and is cleared from the box)
    let ctl = Rc::new(RefCell::new(control::Control::new()));
    let (url, user) = control::load_saved();
    app.set_c_url(url.into()); app.set_c_user(user.into());
    apply_control(&app, &ctl.borrow());
    { let (w, c) = (app.as_weak(), ctl.clone());
      app.on_c_connect(move |u, n, pin| {
          let w2 = w.clone();
          c.borrow_mut().connect(&u, &n, &pin, move || { let w3 = w2.clone(); let _ = slint::invoke_from_event_loop(move || { if let Some(a) = w3.upgrade() { a.invoke_c_changed(); } }); });
          if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); }
      }); }
    { let (w, c) = (app.as_weak(), ctl.clone()); app.on_c_disconnect(move || { c.borrow_mut().disconnect(); if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); } }); }
    { let (w, c) = (app.as_weak(), ctl.clone()); app.on_c_send(move |label, cmd| { c.borrow().send(&label, &cmd); if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); } }); }
    { let (w, c, u) = (app.as_weak(), ctl.clone(), ui.clone()); app.on_c_changed(move || { if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); apply_chat(&a, &u.borrow(), &c.borrow()); } }); }
    apply_chat(&app, &ui.borrow(), &ctl.borrow());
    hook!(on_mus_search, music_query);
    { let (w, c, u) = (app.as_weak(), ctl.clone(), ui.clone());
      app.on_chat_set_tab(move |t| { { let mut u = u.borrow_mut(); u.chat_tab = t; u.chat_sure = false; } if let Some(a) = w.upgrade() { apply_chat(&a, &u.borrow(), &c.borrow()); } }); }
    { let (w, c, u) = (app.as_weak(), ctl.clone(), ui.clone());
      app.on_chat_pick(move |k| { { let mut u = u.borrow_mut(); u.host_key = k.to_string(); } if let Some(a) = w.upgrade() { apply_chat(&a, &u.borrow(), &c.borrow()); } }); }
    { let (w, c, u) = (app.as_weak(), ctl.clone(), ui.clone());
      app.on_chat_say(move |text| {
          let text = text.trim().to_string();
          if text.is_empty() { return; }
          { let mut u = u.borrow_mut();
            let group = u.chat_tab == 0;
            if group && !u.chat_sure { u.chat_sure = true; } // the Everyone send waits for a second tap, like the Qt page
            else {
                u.chat_sure = false;
                let mut args = serde_json::json!({"channel": if group { "group" } else { "host" }, "text": text});
                if !group { args["singer_key"] = serde_json::json!(u.host_key); }
                c.borrow().send_args(if group { "Say to Everyone" } else { "Say in Host chat" }, "chat.say", &args);
            } }
          if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); apply_chat(&a, &u.borrow(), &c.borrow()); }
      }); }
    { let (w, c) = (app.as_weak(), ctl.clone());
      app.on_mus_play(move |idx, name| { c.borrow().send_args(&format!("Play {name}"), "bg.play_track", &serde_json::json!({"index": idx as i64, "track": name.to_string()})); if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); } }); }
    { let (w, c) = (app.as_weak(), ctl.clone());
      app.on_mus_volume(move |v| { let v = (v * 100.0).round() / 100.0; c.borrow().send_args(&format!("Music volume {}%", (v * 100.0).round()), "set_bg_volume", &serde_json::json!({"value": v})); if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); } }); }
    // eq: gains come from the file; a drag sends the whole list like the Qt page (enabled = true), the button flips `enabled`
    fn eq_send(c: &control::Control, target: &str, gains: &[f64], enabled: bool, label: &str) {
        let g: Vec<f64> = gains.iter().map(|x| (x.clamp(-12.0, 12.0) * 10.0).round() / 10.0).collect();
        c.send_args(label, "eq.set", &serde_json::json!({"target": target, "gains": g, "enabled": enabled}));
    }
    { let (c, u) = (ctl.clone(), ui.clone());
      app.on_eq_changed_bgm(move |i, g| { let mut u = u.borrow_mut(); if let Some(x) = u.eq_bgm.get_mut(i as usize) { *x = g as f64; } eq_send(&c.borrow(), "bgm", &u.eq_bgm, true, "Background music EQ"); }); }
    { let (c, u) = (ctl.clone(), ui.clone());
      app.on_eq_changed_kar(move |i, g| { let mut u = u.borrow_mut(); if let Some(x) = u.eq_kar.get_mut(i as usize) { *x = g as f64; } eq_send(&c.borrow(), "karaoke", &u.eq_kar, true, "Karaoke EQ"); }); }
    app.set_eq_presets(ModelRc::new(VecModel::from(singws_uimodel::eq_view::PRESETS.iter().map(|(n, _)| SharedString::from(*n)).collect::<Vec<_>>())));
    { let c = ctl.clone();
      app.on_eq_preset(move |key, i| { if let Some((name, g)) = singws_uimodel::eq_view::PRESETS.get(i as usize) { eq_send(&c.borrow(), if key == "bgm" { "bgm" } else { "karaoke" }, g, true, &format!("{} preset {name}", if key == "bgm" { "Background music" } else { "Karaoke" })); } }); }
    { let (c, u) = (ctl.clone(), ui.clone());
      app.on_eq_toggle(move |key| { let u = u.borrow(); if key == "bgm" { eq_send(&c.borrow(), "bgm", &u.eq_bgm, !u.eq_bgm_on, "Background music EQ"); } else { eq_send(&c.borrow(), "karaoke", &u.eq_kar, !u.eq_kar_on, "Karaoke EQ"); } }); }
    // library: search on every edit (the index answers in milliseconds; a typo falls back to the fuzzy pass, at most 1.5 s)
    { let (w, d, u) = (app.as_weak(), dir.clone(), ui.clone());
      app.on_lib_search(move |t| { u.borrow_mut().lib_query = t.to_string(); if let Some(a) = w.upgrade() { apply_library(&a, &d, &t); } }); }
    { let (w, c) = (app.as_weak(), ctl.clone());
      app.on_lib_add(move |id, singer| { c.borrow().send_args(&format!("Add song for {singer}"), "queue.add_song", &serde_json::json!({"singer": singer.to_string(), "song_id": id as i64, "key": 0})); if let Some(a) = w.upgrade() { apply_control(&a, &c.borrow()); } }); }
    apply_library(&app, &dir, &std::env::var("SLINT_TRIAL_QUERY").unwrap_or_default());
    // the Qt shell's pages refresh every 3 s while visible
    let timer = slint::Timer::default();
    let (weak, d, u, c) = (app.as_weak(), dir.clone(), ui.clone(), ctl.clone());
    timer.start(slint::TimerMode::Repeated, Duration::from_secs(3), move || { if let Some(a) = weak.upgrade() { u.borrow_mut().cutoff = c.borrow().view().bg_index; apply(&a, &d, &mut u.borrow_mut()); apply_chat(&a, &u.borrow(), &c.borrow()); } });
    app.run().unwrap();
}
