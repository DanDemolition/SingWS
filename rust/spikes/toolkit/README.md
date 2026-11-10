# Toolkit spike (2026-11-10)
Same prototype in Slint and Dioxus desktop: hero card (live playback state, polled 1 s from `status_snapshot.json`), Ticker tab with a checkbox (re-renders the ticker text through `singws-uimodel`), Waitlist tab (rows from `waitlist_snapshot.json`). Both call only `core/` which uses `singws-uimodel`.
Separate workspace: not in `rust/Cargo.toml`, nothing the app ships depends on it. Synthetic fixtures in `fixtures/`.

    cd rust/spikes/toolkit && SINGWS_HOME=$PWD/fixtures cargo run --release -p slint-proto    # or dioxus-proto; SPIKE_TAB=1 opens Waitlist
Results and recommendation: `docs/design/stage-e-no-python-no-qt.md`, section "Toolkit spike results".
