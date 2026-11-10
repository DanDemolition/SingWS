//! Toolkit-free glue for the Slint host window. Ports `ticker_panel.rs` and `waitlist_panel.rs` of the Qt shell: READ-ONLY, same files (`queue.json`, `settings.json`,
//! `waitlist_snapshot.json` in `SINGWS_HOME` or `~/SingWS`), all rules from `singws-uimodel`. Nothing here writes show data.
pub mod control;
pub mod ticker_page;
pub mod waitlist_page;

/// The data folder: `SINGWS_HOME`, else `~/SingWS` (what the app and the Qt shell use)
pub fn home() -> String {
    std::env::var("SINGWS_HOME").ok().or_else(|| std::env::var("HOME").ok().map(|h| format!("{h}/SingWS"))).unwrap_or_default()
}
