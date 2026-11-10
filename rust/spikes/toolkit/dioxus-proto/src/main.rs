use dioxus::prelude::*;
use std::time::Duration;

const CSS: &str = r#"
body{margin:0;background:#14101f;color:#fff;font-family:-apple-system,system-ui,sans-serif}
.hero{background:#2a1f4d;border-radius:14px;margin:12px;padding:12px}
.k{color:#b6ff3b;font-size:12px;font-weight:800}.n{font-size:26px;font-weight:800}
.row2{display:flex;justify-content:space-between;color:#cfc6ee}.left{color:#b6ff3b;font-size:18px;font-weight:800}
.tabs{display:flex;margin:0 12px;border-bottom:1px solid #333}.tab{padding:10px 16px;cursor:default;color:#cfc6ee}.tab.on{background:#231a3d;color:#fff;font-weight:700}
.pane{padding:12px}.bar{background:#0b0816;border-radius:8px;padding:14px 10px;font-size:22px;overflow:hidden;white-space:nowrap;margin-top:10px}
.card{background:#231a3d;border-radius:10px;padding:8px;margin:4px 0;display:flex;gap:10px;font-size:12px}.st{color:#b6ff3b;width:130px}.sec{color:#b6ff3b;font-weight:800;font-size:15px;margin-top:6px}
.txt{white-space:pre-line}
"#;

fn main() {
    let t0 = std::time::Instant::now();
    eprintln!("main_entered_ms={}", t0.elapsed().as_millis());
    dioxus::LaunchBuilder::desktop().with_cfg(dioxus::desktop::Config::new().with_window(dioxus::desktop::WindowBuilder::new().with_title("SingWS toolkit spike (Dioxus)").with_inner_size(dioxus::desktop::LogicalSize::new(900.0, 600.0)))).launch(app);
}

fn app() -> Element {
    let dir = spike_core::home();
    let mut custom = use_signal(|| true);
    let mut tab = use_signal(|| std::env::var("SPIKE_TAB").ok().and_then(|t| t.parse::<u8>().ok()).unwrap_or(0));
    let mut snap = use_signal(|| spike_core::load(&dir, true));
    // live state: poll the snapshot files once a second
    use_future(move || { let dir = spike_core::home(); async move { loop { tokio::time::sleep(Duration::from_secs(1)).await; let s = spike_core::load(&dir, custom()); if *snap.peek() != s { snap.set(s); } } } });
    let s = snap();
    rsx! {
        style { "{CSS}" }
        if let Some(h) = s.hero.clone() {
            div { class: "hero", div { class: "k", "{h.kicker}" } div { class: "n", "{h.singer}" } div { class: "row2", span { "{h.subtitle}" } span { class: "left", "{h.remaining}" } } }
        }
        div { class: "tabs",
            div { class: if tab() == 0 { "tab on" } else { "tab" }, onclick: move |_| tab.set(0), "Ticker" }
            div { class: if tab() == 1 { "tab on" } else { "tab" }, onclick: move |_| tab.set(1), "Waitlist" }
        }
        div { class: "pane",
            if tab() == 0 {
                label { input { r#type: "checkbox", checked: custom(), onchange: { let d = dir.clone(); move |e: Event<FormData>| { custom.set(e.checked()); snap.set(spike_core::load(&d, e.checked())); } } } " Show the custom message" }
                div { class: "bar", "{s.ticker_text}" }
            } else {
                div { "{s.waitlist_status}" }
                for sec in s.waitlist.iter() {
                    div { class: "sec", "{sec.title}" }
                    for r in sec.rows.iter() { div { key: "{r.id}", class: "card", div { class: "st", "{r.status}" } div { class: "txt", "{r.text}" } } }
                }
            }
        }
    }
}
