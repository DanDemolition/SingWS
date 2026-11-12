# KaraFun web player: moving its Python into Rust (plan, 2026-11-10)

Goal: the web player (`karafun_web_player`, shipped OFF by default in 1.0.3.3) runs with no Python, like the rest of the Rust migration. Same method as every other
port (see `stage-e-karafun-rust-plan.md`): Python is the oracle; the Rust twin is proven OFFLINE first; then it runs as a log-only shadow in real shows; then it drives
behind a flag after one clean show per decision (project gate policy). Nothing here changes how the Python player works today. The plan below is now largely BUILT OFFLINE: see "Final status of the port" at the end.

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


## Final status of the port (2026-11-12)

Everything below lives in ONE crate, `rust/crates/singws-karafun-svc` (library plus a JSON CLI, `--batch` for the tools), branch `claude/karafun-web-rust-4a4a02`. **Nothing calls it: the Python still runs the web player, no installer contains it, no setting selects it.** Every module is a pure decision function (time, files, network and the Keychain are passed in or scripted), proven against its real oracle and then mutation-tested with `tools/mutate_karafun_svc.py` (scratch copy; survivors are documented with the reason, a mutant that loops or declines where the original did not counts as killed).

| Milestone | Module | Oracle | Proof |
|---|---|---|---|
| W1 service state machine | `lib.rs` | real `KaraFunWebService` | `tools/karafun_service_parity.py`, 12,000 identical |
| W2 song lifecycle | `host.rs` | real `KaraFunWebHost` | `tools/karafun_host_parity.py`, 8,000+ identical |
| W3 browser provisioning (rules only, scripted network) | `browser.rs` | real `ensure_browser` | `tools/karafun_browser_parity.py`, 7,500 identical; found and fixed two Python defects (a version escaping the browser folder; a file named like the version) |
| W4 saved login | `credentials.rs` | real `karafun_credentials` on a TEST-ONLY Keychain service | `tools/karafun_credentials_parity.py`, 1,565 identical |
| W5 Top 100 | `top100.rs` | real `karafun_top100` | `tools/karafun_top100_parity.py`, 12,000 identical |
| W6 frame hand-off (offline part) | `frames.rs` | real `KaraFunWebHost` frame methods | `tools/karafun_frames_parity.py`, 1,500 identical |
| W7 matching | `matching.rs` + generated `match_tables.rs` | the REAL JavaScript of `karafun_bridge.js` run in Node (`tools/karafun_match_oracle.js`) | `tools/karafun_match_parity.py`, 20,000 identical |

Dependencies added: `security-framework` (macOS only, W4) and `unicode-normalization` (W7); both were already in the workspace lock. The only Python app code changed: the two `ensure_browser` fixes.

### What still must be wired before the Python can be retired
1. **A runtime around the pure functions.** The Rust has no I/O: something must own the helper process (`singws-karafun-web`), the clock, the Keychain, the files and HTTP, and feed events in. The pieces exist elsewhere in the workspace (`singws-http`, `singws-mac`, the shell).
2. **The download itself** (W3 decides, it does not fetch): index fetch, archive, `ditto`, `--version`, through `singws-http`.
3. **The Keychain hand-over (W4 finding).** An item is readable without a prompt only by the program that created it. A login saved by the Python app (through `/usr/bin/security`) cannot be read by the Security framework from another program without a dialog, and a framework-made item makes `security -w` hang on a dialog. While Python and Rust coexist, use the `tool` backend (drives `/usr/bin/security`, identical to Python); the native backend needs a one-time approval or a re-save of the login.
4. **W6, the part that needs a real screen:** painting the picture into the audience and preview windows through the Rust shell's display window, its look, and real frame timing. Only the hand-off rules are proven.
5. **The page script stays JavaScript** (`karafun_bridge.js`, compiled into the helper); only its matching rules have a Rust twin, as a consistency check.
6. **Intel hardware** has never run the web player; the real TV has not been rehearsed.

### Safe order for wiring it in (shadow first, nothing driving)
1. **Shadow, log-only, default on once clean** (`karafun_web_shadow`, like every other shadow in this repo): feed the Rust machines the SAME events the Python sees (service messages, status messages, the song start/finish calls, frame callbacks) on a low-priority thread and log a MISMATCH (field names only, never the song text or any login) when the effects differ. Start with W2 (song lifecycle) and W1 (service), the two that run in every show; then W7 (matching, compared with what the page script chose), W5 (what would be sent and when, without sending) and W6 (hand-off decisions).
2. Read `grep KARAFUN-WEB-SHADOW` after a rehearsal and a real show; put each decision in `tools/gate_ledger.py` (one clean show per decision).
3. **Drive behind a flag**, narrowest first: W5 (Top 100 upload, no show risk), W7 matching, then W3/W4 (provisioning and login, using the `tool` Keychain backend), and last W2/W1 (`karafun_web_engine = python | shadow | rust`, falling back to Python on any Rust trouble).
4. **W6 last**, together with the Rust shell's audience window, looked at on the real screen (AGENTS.md rules 4 and 6).
5. Retire the Python only after the Rust has driven a clean real show with the Python as the fallback.
