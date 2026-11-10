# KaraFun web player: moving its Python into Rust (plan, 2026-11-10)

Goal: the web player (`karafun_web_player`, shipped OFF by default in 1.0.3.3) runs with no Python, like the rest of the Rust migration. Same method as every other
port (see `stage-e-karafun-rust-plan.md`): Python is the oracle; the Rust twin is proven OFFLINE first; then it runs as a log-only shadow in real shows; then it drives
behind a flag after one clean show per decision (project gate policy). Nothing here changes how the Python player works today. NOT started: this is the plan.

## What exists today

| Piece | Language | Size | Notes |
|---|---|---|---|
| `rust/crates/singws-karafun-web` (helper: Chrome over its DevTools pipe, screencast, command dispatch) | **Rust (done)** | 366 lines | embeds the page script |
| `karafun_bridge.js` (talks to KaraFun's own player objects; matching rules `choose`) | JavaScript | 307 | runs INSIDE Chrome: it stays JavaScript |
| `karafun_web.py` `KaraFunWeb` (helper client), `ensure_browser` (find/download/verify Chrome for Testing), `KaraFunWebService` (sign-in order, reload-once, 30 s rule) | Python | 434 | |
| `karafun_web_host.py` `KaraFunWebHost` (song start, status following, controls, end detection, frames to the audience, slideshow hand-back) | Python | 453 | Qt painting of frames stays with the window owner |
| `karafun_credentials.py` (saved login in the Keychain via the `security` tool) | Python | 66 | |
| `karafun_top100.py` (read the chart, once-a-day rule, upload) | Python | 129 | |
| hooks and Settings card in `0.2.18.1.py` | Python | ~66 lines | glue; goes with the Settings port |

## What is NOT ported, on purpose
- `karafun_bridge.js`: it runs in the browser. Its matching rules mirror SingWS's own KaraFun search rules, so they get a JS-vs-Rust PARITY check (W7), not a port.
- Painting frames into the audience/preview windows: it belongs to whoever owns those windows. The Rust shell already has the karaoke display window (`stage-e-karaoke-display-window.md`); the frame hand-off is wired when the shell drives KaraFun (W6), not before.
- The Settings card, until the Settings dialog is ported.

## Milestones (each is one small, reviewable step; stop after each)

| # | Step | Proof before anything is wired | Risk |
|---|---|---|---|
| W1 | **Service state machine** (`KaraFunWebService._message`: statuses from the page -> states ready / sign_in; the 30 s signed-out rule; saved login tried once; browser page only after the login had 30 s; reload once after a saved-login sign-in; 8 s settle) as a pure Rust function `step(state, event, now) -> (state, actions)` in `singws-karafun-web` | replay harness drives the REAL Python service with scripted message streams and a fake clock (the `monitor`/`flow` method); thousands of scenarios IDENTICAL | low |
| W2 | **Song lifecycle** (`KaraFunWebHost`: start gating, "heard" start, pause/resume clock, end by stopped-at-end / gone / another song / sat-at-end 2.5 s, never-started 60 s, seek clamp 8 s, key/tempo apply, finish) as a pure Rust machine | the same replay method over the REAL Python host with a fake app; mutation run with `tools/mutate_rust_file.py`, every survivor documented | medium: show-critical, so shadow first |
| W3 | **Browser provisioning** (`ensure_browser`: dedicated Chrome for Testing first, installed Chrome as fallback; index parsing, the three allowed hosts, size and `--version` checks, unpack with `ditto`) | pure rules by parity (platform key, index parsing, host allow-list, folder pick); the download itself by a real-download test; uses the existing `singws-http` (no new dependency expected) | low |
| W4 | **Keychain** through the Security framework instead of the `security` tool, keeping the stored format `h:<hex>` so EXISTING saved logins still load | round-trip tests against a TEST-ONLY service name (never the real one: a test run once wiped the operator's login); a cross-check that Rust reads what Python wrote and the reverse | low, but secrets: never printed or logged |
| W5 | **Top 100** (once-a-day rule, chart request via the helper's `top` command, the upload to `/api/v1/top100_sync.php`) | pure `is_due` by parity; upload against a scratch PHP server like the existing test | low |
| W6 | **Frames to the audience window** through the Rust shell's display window instead of Qt widgets | on-screen look (AGENTS rule 4: look at it), part of the Python-free shell work | medium |
| W7 | **Matching parity**: JavaScript `choose` (run in Node) against the Rust accept/reject rule already in `singws-karafun` (`pure.rs`) over the same generated cases (qualifiers, `&`/`and`, accents, unique-title fallback) | parity tool, 5,000+ cases | low |

Order: W7 and W5 first (small, no show risk), then W1, W4, W3, W2, last W6. W2 is the only one that touches the live path, so it gets the shadow stage: a log-only
`karafun_web_shadow` (default on once offline parity is clean) that logs a MISMATCH with field names only, like every other shadow.

## Switches (all default to today's behaviour)
`karafun_web_engine = python | shadow | rust` (default `python`), read at launch. `rust` falls back to Python on any Rust trouble, as the other ports do.

## Gate before driving
Offline parity IDENTICAL, mutation survivors documented, one clean real show or rehearsal with the shadow on, the matching gate in the ledger (`tools/gate_ledger.py`).

## Honest unknowns
- Whether KaraFun changes its web player (it has not approved this): the JavaScript, not the Rust, is where that would break.
- The Rust shell cannot show the picture yet without W6; until then the Python host stays the audience-window owner.
- Intel hardware has never run the web player (Rosetta only).
