# Stage E, Step 3: the "no-Python host" spike (2026-10-31)

Tool: `rust/spike_host.sh [--arch arm64|x86_64|both] [--out DIR] [--no-build]` plus `singws-shell --selfcheck` (`crates/singws-shell/src/selfcheck.rs`, `qml/SelfCheck.qml`).
It builds a throwaway `SingWS Spike <arch>.app` (bundle id `com.singws.spike`, own `SINGWS_HOME`, signed with the local "SingWS Local Code Signing"
identity), launches it through LaunchServices (`open -n -W --arch`), and writes `report.md` + `selfcheck-<arch>.json`. It never touches `/Applications` or `~/SingWS`.
No app code, no setting. The only existing-code change is that the shell now links `singws-bgm-ffi` (so `singws-stretch` builds with the shell).

## Answer

**Feasible, for the parts measured: one Rust process hosts the Qt Quick shell, the Rust video bridge and the Rust BGM engine, on arm64 and on x86_64, and a
locally signed bundle keeps a stable code identity across rebuilds.** Not measured: anything needing TCC grants (Accessibility, Screen Recording, Location,
AppleScript automation): that is item (e), pending `singws-mac`. So "do grants stick" is answered only for the *identity* half (designated requirement), not
for an actual grant.

## Evidence (run 2026-10-31, report in the session scratch; re-run to regenerate)

Host: Apple M1 Max, macOS 27.2. **The dev Mac is Apple Silicon, not Intel** (AGENTS.md "This machine" is out of date): arm64 runs natively and x86_64 runs under
**Rosetta**. So the x86_64 result is *not* an Intel-hardware result (Intel GPU/driver untested; same caveat as before).

| Check | arm64 | x86_64 (Rosetta) |
|---|---|---|
| bundle strict-verifies, signed by local identity | yes | yes |
| `libmpv` loaded by the bridge in-process | OK | OK |
| bridge created on two Qt Quick windows' `winId` (QNSView) | OK | OK |
| picture path: `visual_ready`, `present_count` (frames presented to the Qt views) | true, 184 in ~3.5 s | true, 182 |
| mpv position advancing | 3520 ms at the probe | 3480 ms |
| BGM engine, headless, ~5 s paced like a sound-card callback (generated 440 Hz MP3) | 409 blocks, rms 0.0839, 0 silent blocks, 0 underruns, 0 gap frames, 0 dropped commands, position 4.36 s | identical figures |
| clean teardown (`bridge_destroy`, thread join, exit code 0) | yes | yes |
| (e) `singws-mac` `ax_is_trusted` / `run_applescript` | **pending singws-mac** (stub) | same |

TCC / signing:
- Designated requirement with the local identity is `identifier "com.singws.spike" and certificate root = H"5a2f...4380"` and is **identical after the bundle
  changed** (new version string, re-signed); with ad hoc signing it is `cdhash H"..."` and changes every build. TCC keys grants to the code requirement, so a
  grant given to a bundle signed with the local identity *should* survive rebuilds, ad hoc builds would need a re-grant each time. This is inferred from the
  requirement, **not** observed with a real grant.
- `TCC.db` cannot be read from here (needs Full Disk Access): the report says so; the run needed no TCC permission by design (headless audio, no capture, no AX) and
  I saw no prompt, but I did not watch the screen the whole time.

## Findings that matter for stages 6/7

1. **`winId` must be a real, shown window.** First attempts handed the bridge a `QNSView` that was hidden (`window_visible=false`) and the bridge reported
   `view-hidden` forever with `present_count=0`, with no error to the caller. A QML `Window` nested in a plain `Item` is not shown; a root `Window` is. The bridge
   silently doing nothing on a hidden view is a trap for the real host (log line only).
2. **Qt has no QML `winId` property**; the host needs a small native lookup (`singws_window_native_id` in `cpp/screens.cpp`) to give the bridge the NSView. Same
   thing PyQt's `QWidget.winId()` does for the Python app.
3. **The bridge looks for `singws_libmpv.2.dylib` beside the *executable* (`Contents/MacOS`) or in `MacOS/Frameworks`, not `Contents/Frameworks`** when linked into
   an app binary (its `own_dir()` is "the dylib's folder", which is the executable here). The spike sets `SINGWS_LIBMPV`; a real bundle needs either that env, a
   copy next to the executable, or an `own_dir` fallback to `../Frameworks`.
4. `grab_frame` fails with hardware-decoded video (`videotoolbox` frames not convertible by mpv's screenshot): informational only, the report marks it so. The
   DAW-preview path that uses it for MP4 may be affected in a no-Python host; not investigated.
5. A loose text file (`SHA256SUMS`) in `Contents/Frameworks` breaks strict signing ("main executable failed strict validation"); the spike removes it. Packaging
   the real runtime must copy only dylibs.
6. Bundle size: 195 MB (arm64) / 202 MB (x86_64), mostly macdeployqt's Qt frameworks.

## Not verified

- Whether the picture *looks* right on screen (no screenshot: `screencapture` could raise a Screen Recording prompt; presented-frame counts only).
- Real sound-card output (headless engine only); the render-thread mode; CDG; two Qt versions together (the shell's 6.12 vs PyQt's 6.9).
- Real Intel hardware; a real TCC grant surviving a rebuild; AX / AppleScript / ScreenCaptureKit from the Rust host (needs `singws-mac`).

## Re-run

    rust/spike_host.sh --arch both --out "$(mktemp -d)"   # ~4 min the first time (builds both targets); opens two small windows for ~10 s
