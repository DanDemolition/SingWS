# Stage E step 1: `singws-mac`, the platform shim (built 2026-10-31, branch `claude/eager-rhodes-a4af7f`, local only)

What was built, what it proved, and the TCC (privacy permission) questions it was meant to answer. Anything marked **UNVERIFIED**
needs the operator at the Mac; nothing here was installed, signed with the local certificate, or run under `/Applications`.

## What exists

* `rust/crates/singws-mac` — a ~110-line Objective-C shim (`shim/mac_shim.m`, compiled by `build.rs` with `cc`, like `singws-video`) behind a C ABI,
  safe Rust wrappers in `src/lib.rs`, and the CLI `singws-mac-probe`.
  * `keep_awake_begin/end/active` — the same `NSProcessInfo` activity options as `mac_keep_awake.py` (idle system sleep + idle display sleep + user initiated).
  * `frontmost_app_name` — `NSWorkspace.frontmostApplication.localizedName`, what `_karafun_frontmost_app_name` reads.
  * `ax_is_trusted` — `AXIsProcessTrustedWithOptions` with the prompt option **off**; never prompts.
  * `run_applescript(source, timeout) -> (ok, out, err)` — in-process `NSAppleScript`, stdout stripped, empty script is `(false, "", "empty_script")`, timeout gives
    `(false, "", "timeout")`. Scripts are serialized by a lock: running two `NSAppleScript`s concurrently in one process returned the OTHER script's result in a unit test.
  * `location_status` / `device_location_once` — reads the CoreLocation authorization status; requests a fix only when already authorized (status 3 or 4); otherwise declines
    with `not authorized: status N`. It never calls `requestWhenInUseAuthorization`.
* `singws-mac-probe` (text; `--json` for the shadow; `--hold SECONDS` to hold keep-awake for `pmset`; `--applescript SRC`; `--location`).
* Shadow: `platform_shadow.py`, setting `platform_shadow` (DEFAULT **True** since 2026-10-31, operator; logs `skipped` once per launch while the probe is not bundled). Once at launch, after the app's own keep-awake set-up, a daemon thread runs
  `singws-mac-probe --json` (low priority) and logs `[PLATFORM-SHADOW] MISMATCH ...` only when frontmost app / AX trust / keep-awake differ from Python's own reads.
  Each distinct difference is logged once per session. Python's answers are always the ones used; every call is wrapped.
  Find the probe by `SINGWS_MAC_PROBE_BIN`, next to the app, `Frameworks/`, or `native/singws_mac/<arch>/` (**the specs do not bundle it yet**: no installer was built).

## Verified (this Mac, Apple Silicon, 2026-10-31, no permission prompt shown)

* `cargo test --release -p singws-mac`: 8 tests pass (status names, request gate, empty script, `return 1+1`, script error, timeout, keep-awake begin/idempotent/end, location declines without a grant).
* `python3 tools/platform_parity.py`: ALL EQUAL — `return 1+1` equals `osascript`; error script fails in both; stdout stripped; `pmset -g assertions` shows both
  `PreventUserIdleSystemSleep` and `PreventUserIdleDisplaySleep` named "singws-mac-probe hold" while held and neither after release (process still alive); frontmost app equals `lsappinfo`; `--json` shape.
* `test_platform_shadow.py` (18 tests) and `tools/mutate_platform.py` (see the report for the final count).

## TCC identity: what we know

1. **Observed, here:** `singws-mac-probe` reported `ax_is_trusted: true` when launched from the Claude desktop app's session. The probe has no grant of its own;
   macOS attributes a child process to its *responsible process* (the app that launched it), so the probe inherited the parent's Accessibility trust. **Consequence:** the
   `ax_trusted` comparison is only meaningful when the probe is launched from the real app (then the responsible process is SingWS, equal to Python's answer by construction) —
   it says nothing about whether a Rust binary could hold a grant by itself. Do not read a green `ax_trusted` from a terminal/agent run as evidence.
2. **Observed, here:** `cargo build` produces an *ad-hoc, linker-signed* binary: `Signature=adhoc`, `flags=adhoc,linker-signed`, `Identifier=singws_mac_probe-<hash>`,
   designated requirement `cdhash H"..."`, no Info.plist, no team id. For an ad-hoc binary the designated requirement **is** the code hash, and the identifier carries a build hash
   too, so every rebuild is a *different* identity to TCC. (Inference from the requirement, not a TCC experiment.)
3. **UNVERIFIED (needs the operator):** whether a TCC grant (Accessibility, Automation/Apple Events, Location) given to the probe survives a rebuild when it is signed with the
   local self-signed certificate and a fixed `--identifier`. The expected answer is yes (the requirement becomes `identifier "..." and certificate leaf = H"..."`), but TCC has
   historically also keyed on path; this has not been tried. Procedure for the owner: sign the probe `codesign --force -s "<local cert>" --identifier com.singws.mac-probe`,
   run it from the Terminal, add it in System Settings > Privacy & Security > Accessibility, run `singws-mac-probe` (expect `ax_is_trusted: true` even with the parent's trust
   removed — test by launching through `launchctl asuser` or a LaunchAgent so the responsible process is the probe itself), rebuild, re-sign, run again.
4. **UNVERIFIED:** NSAppleScript to *System Events* from the probe: the first run will show an Automation prompt naming the probe's responsible process. `tools/platform_parity.py --system-events`
   does exactly that and is opt-in for this reason (it was NOT run here).
5. **UNVERIFIED:** CoreLocation from a bare command-line binary. A tool without an Info.plist / `NSLocationUsageDescription` is generally not offered the Location prompt at all
   (status stays `not_determined`; here: `location_status: 0 (not_determined)`). The one-shot fix may only work from inside an app bundle, which is the case the shell/app would be.
   The probe and the shadow never request authorization, so they cannot test this; the owner must run a bundled build with `--location` after granting.
6. **UNVERIFIED:** both architectures. Only arm64 was built and run (the dev Mac in AGENTS.md says Intel, but this session's Mac reported `arm64`); the x86_64 slice has not been
   compiled (`cargo build --release --target x86_64-apple-darwin -p singws-mac` should work; not tried). Screen-recording grant (ScreenCaptureKit) is not part of this shim at all.

## Risks noted

* `NSAppleScript` is documented as main-thread-oriented; it ran correctly on a worker thread in the probe, but a Qt/AppKit host with an active main run loop was not tested.
* The shim holds one process-wide keep-awake token (`g_activity`); a host that also uses `mac_keep_awake.py` would hold two assertions, harmless but redundant.
* `frontmost_app_name` from a process with no run loop: worked here (returned the correct app, matches `lsappinfo`); NSWorkspace may lag if called immediately after a focus change.
