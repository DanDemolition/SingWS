# Rust video bridge (roadmap item 2): inventory and plan

Written 2026-10-11 (milestone V1, inspection only: no code, no app change). Why: the macOS-only Objective-C bridge
(`native/mpv_bridge/bridge.mm`, 1,956 lines) is the main thing a Windows port would have to replace, and the operator wants
the realtime/render code in Rust (see `RUST_MIGRATION_PLAN.md`). Item 4 of the roadmap stands: **mpv/libmpv stays the decoder and
the renderer of the picture**; Rust owns the control logic, the compositor and the presentation.

## What exists (inspected)

* Python adapter `mpv_playback_iina.py` (510 lines) loads `libsingws_mpv_bridge.dylib` with ctypes and uses **37 exported C functions**
  (`singws_bridge_*`). `mpv_karaoke_transport.py` sits above it. Nothing else in the app talks to the bridge.
* `bridge.mm` is one libmpv core (render API, OpenGL) drawing each frame once into a shared texture that two or four native
  `NSOpenGLView`s sample (output, preview, rotation underlay, spotlight), plus a second libmpv core for the background video loop.

### The 37 exports, by plane

| Plane | Exports | Platform-specific? |
|---|---|---|
| Lifecycle | `create(outputView, previewView, ...)`, `destroy`, `refresh_views`, `set_log_callback`, `set_render_thread`, `render_thread_active`, `present_count` | create/refresh take NSView pointers |
| Transport | `load(video, audio)`, `play`, `pause`, `stop`, `seek`, `position`, `duration`, `is_playing`, `at_end`, `visual_ready` | **No** (pure mpv control) |
| Audio | `set_volume`, `set_device`, `set_tempo`, `set_key`, `set_dsp_chain`, `set_audio_delay` | **No** (mpv properties + the `af` composition) |
| Picture options | `set_sidefill`, `begin_transition` | compositor |
| Background video | `load_background`, `background_at_end`, `background_position`, `background_paused`, `set_background_opacity`, `stop_background` | second mpv core + compositor |
| Hosts | `set_rotation_host`, `set_rotation_spotlight` | NSView ordering (macOS) |
| Capture | `grab_frame`, `free_frame` | GL readback |
| Analysis | `scan_silence`, `scanner_shutdown` | **No** (its own mpv core, no views) |

Roughly half of `bridge.mm` is pure mpv control logic (load sequence, events, properties, filter chain, silence scan): portable to Rust
unchanged. The other half is OpenGL + AppKit: the shared texture and fence between GL contexts, the compositor shaders (CDG fit,
side fill / ambient blur, pillarbox measurement, background crossfade), the NSOpenGLView subclass (`hitTest`, `viewDidMoveToWindow`,
reshape), the main-queue vs private render-queue modes (`karaoke_render_thread`), and the rotation/spotlight host ordering.

### Hard-won behaviour the Rust version must reproduce (each cost a show or a session)
* Load order: complete `stop` before configuring the next file; `audio-files` cleared, then `loadfile ... replace` async with a serial
  number; for CDG force `demuxer-lavf-format=cdg` + probescore 1 and attach the MP3 only after FILE_LOADED; MP4 stretched with
  `video-aspect-override=16:9`, CDG not.
* All mpv commands/property writes off the GUI thread on one serial control queue (mpv can hold its core lock for hundreds of ms).
* Tempo/key/dsp-chain/audio-delay are remembered and re-applied after every load (`loadfile` resets them); mpv has ONE `af` property,
  composed in `applyAudioFilters`.
* Playback buffering (`audio-buffer=1.0`, `demuxer-readahead-secs=10`, 256 MiB cache), `hwdec=auto-safe`, `video-sync=audio`.
* GPU fence after each render so the consumer GL contexts see a finished texture (Apple Silicon stale-frame fix); no `glFinish`.
* CDG capture reads the retained 300x216 texture (libmpv `screenshot-raw` of a CDG FBO is black).
* Native views stack by creation order (AGENTS rule 6); the transition/ticker/rotation surfaces depend on how the bridge attaches.
* Render-thread mode (default on): one private serial queue owns ALL GL work; main-thread state lives in a per-view cache.

## Design

1. **Drop-in ABI.** New crate `rust/crates/singws-video` builds `libsingws_video_bridge.dylib` exporting the **same 37 functions with the
   same signatures and semantics**. `mpv_playback_iina.py` and everything above it stay unchanged. Selected by a setting
   (`video_bridge`: `"objc"` default until proven, then `"rust"`), read at launch; the Objective-C bridge stays in the build until the
   Rust one has run real shows (AGENTS: do not remove the old path before the replacement is proven).
2. **libmpv via dlopen.** Own minimal FFI (client API + render API, OpenGL) loaded with `libloading` from the bundled
   `singws_libmpv.2.dylib`; no `libmpv-sys` (it would want headers and would not follow our pinned/patched runtime).
3. **Three layers inside the crate:**
   * `core`: portable mpv control (everything in the Transport/Audio/Analysis rows), the control queue, events, the `af` composer.
   * `compositor`: GLSL ported as-is (via `glow`), shared texture + fence, CDG fit/side fill, background crossfade, pillarbox measure.
   * `presenter` trait: how a texture reaches a window. **macOS presenter first**: a small Objective-C shim (~300 lines, built by the
     `cc` crate, no logic in it) that creates the `NSOpenGLView`s, makes contexts current, flushes, reports geometry and does `hitTest`;
     Rust owns everything else. Rationale: AppKit subclassing from Rust (`objc2`) is the riskiest piece and the shim is thin and testable.
     A Windows presenter (WGL or ANGLE) is a later, separate implementation of the same trait.
4. **Honest limits:** libmpv's render API gives us OpenGL (deprecated on macOS but supported, and what the current bridge uses); a Metal
   path is out of scope. Video decode stays hardware-accelerated inside mpv.

## Milestones (each stops and reports; nothing replaces playback until V6 passes)

* **V1 (done here):** inventory + this plan.
* **V2 control plane, headless:** `core` in Rust with the C ABI for the Transport/Audio/Analysis rows, running mpv with `vo=null`.
  Proof: `scan_silence` parity on real files against the current bridge (same function, no views); load/seek/position/at_end/duration
  and the af-chain composition compared against the Objective-C bridge on real CDG+MP3 and MP4 files (log + property diff); control
  queue ordering test (load, then immediate seek/tempo/key).
* **V3 render plane:** render context + shared texture + fence + a presenter that draws the plain picture into the output/preview
  views. Proof: on-screen A/B against the Objective-C bridge with `tools/render_thread_probe.py`-style screenshots, pixel diff on the
  same CDG and MP4 at the same seek point (the 2026-10-10 method: 0 of 844,800 pixels differed).
* **V4 compositor:** CDG fit + side fill + ambient blur, pillarbox measurement, MP4 stretch, background video (second core) with
  opacity/crossfade, `begin_transition`, `grab_frame` (CDG from the retained texture).
* **V5 hosts + threading:** rotation/spotlight underlay hosts, `refresh_views`, render-thread mode (private queue, per-view cache,
  CGL locking as today). Proof: the freeze test from `render_thread_probe.py` (5-6 s main-thread freeze must keep drawing), resize,
  hide/show, 45 s soak.
* **V6 integration + rehearsal:** setting `video_bridge`, bundle in both specs, scratch launch, then the operator's checklist on the
  real app (CDG + background video, singer transition, rotation screen, KaraFun capture still unaffected, resize/hide the audience
  window), then the Intel Mac (never run the render thread there beyond one show), then one real show per architecture.
* **V7:** flip the default to `"rust"`; the Objective-C bridge stays one release, then goes. Windows presenter is separate work.

## Rules for this item
* Additive until V6: no app behaviour change, no installer change. Rendering and window ordering cannot be validated by unit tests
  (AGENTS rules 4 and 6): every visual milestone needs screenshots, and nothing is installed for a show before a real-app rehearsal.
* The C ABI is the contract: any export whose behaviour differs from the Objective-C one is a bug in the Rust one.
* Do not change deliberate behaviour (e.g. MP4 stretched to 16:9) on inference (AGENTS rule 5).

## First action when the operator says go for V2
Create `rust/crates/singws-video` (core + the `scan_silence` export), a parity tool `tools/video_bridge_parity.py` that loads either
dylib through the same ctypes surface, and run it on a handful of real files.

## V2 results (2026-10-11) - DONE, additive, nothing in the app imports it yet
New crate `rust/crates/singws-video` (`libsingws_video.dylib`; libmpv loaded at run time via `libloading`, device-name folding via `unicode-normalization`; both runtime-only, MIT/Apache). It exports **all 37 `singws_bridge_*` functions, the exact set the Objective-C bridge exports** (checked with `nm` against the ObjC dylib, in the test suite). Implemented and ported line for line: the load sequence (stop first, `audio-files` cleared, async `loadfile`, CDG demuxer forcing, MP3 attached after FILE_LOADED, MP4 16:9 stretch), the serial control queue, the event loop (log de-duplication, FILE_LOADED tuning, VIDEO_RECONFIG pillarbox measure), the remembered tempo/key/DSP chain/audio delay and the `af` composer (DSP stages then rubberband), position/duration/at_end/is_playing semantics (audio-pts first, "not ready" while loading), volume clamp, output-device resolution that **refuses the system default** when the saved output is missing, and the silence scanner (own window-less core). Picture-surface functions (views, background video, transitions, rotation hosts, frame grab, render thread) exist with the right signatures but are no-ops until V3-V5; `visual_ready` is a headless stand-in (VIDEO_RECONFIG seen).
**Proof:** `tools/video_bridge_parity.py` runs one scenario through a dylib with real Qt windows (the Objective-C bridge needs them and must be called from the Qt main thread) at volume 0: a real MP3+G (PM Dawn, 261.5 s) then a real MP4, play/pause/seek/tempo/key/DSP chain/audio delay/device refusal/at_end/stop/destroy, plus silence scans of three real files. **Objective-C vs Rust: PARITY OK** - durations identical to the millisecond (261,493 / 172,849 ms), positions within 17 ms, play/pause/at-end flags identical, the 23 bridge-authored log lines identical in order (af chains, audio-delay, device refusal, "media loaded"), and the three silence scans identical to 3 decimals (lead 7.303 / trail 251.140, 7.139 / 165.708, 0 / 203.259). `test_rust_video_bridge.py` (6 tests, subprocess-isolated, skips without the dylib): ABI set, scan on a synthetic file with known lead/trail, missing-file scan, transport + remembered settings + af chain + device refusal, no core left behind after destroy, failed create returns null. Rust unit tests 6 pass; the crate also builds for x86_64 (macOS 12.3).
**Bug found by the parity run and fixed:** `shutdown()` once skipped `terminate_destroy` (the `handle()` accessor returns None once stopping is set), leaking the core; libmpv keeps a process-wide ffmpeg log hook on the first core, so every later silence scan silently reported "no silence". Regression test added.
**Not done / not proven:** no picture, no GL, no views (V3); Intel hardware; the libmpv dlopen path inside a packaged app (the dylib looks next to itself and in `Frameworks/`; only the dev path via `SINGWS_LIBMPV` was exercised); the build script and spec entries (V6). **Next: V3 render plane** (render context + shared texture + fence + a presenter that draws the plain picture into the output and preview views, A/B by screenshots against the Objective-C bridge).

## V3 results (2026-10-11) - DONE, additive, still not imported by the app
The picture path exists in Rust (macOS, main-thread mode): `render.rs` (master GL context, the shared 1920x1080 texture + a native 300x216 CDG texture + background textures, the shader program, the mpv OpenGL render context, the GPU fence, the output + preview views and their present logic) plus a thin Objective-C shim `shim/video_shim.m` (built by `build.rs` with the `cc` crate; build-time only) that holds the NSOpenGLViews/contexts and forwards drawRect / geometry / "attached to window" to Rust. The GLSL is extracted VERBATIM from `bridge.mm` (`shaders.rs`; do not edit by hand). Ported with the same reasons kept in comments: the fence + `glWaitSync` (Apple Silicon stale CDG frame fix), frames produced while a load is pending do not count as "has frame", clearing both retained targets before a load, the skip-reason log lines (`output view presenting / no-frame (black) / view-hidden ...`), MP4 stretched to every edge, CDG fit + preview crop from the measured pillarbox span, nearest filtering for the final CDG layer, side fill modes 1 and 2, `refresh_views` (reparenting + drawable reattach), `begin_transition` (hold, then present at the settled size), `set_sidefill`, `present_count`. With two views `create` runs `vo=libmpv`; with 0/0 it stays the V2 headless core, so the V2 tests are unchanged.
**Proof (this Mac, arm64, real windows, silent):** `tools/video_bridge_shots.py` (one process per dylib; screenshots after pause-then-exact-seek so both show the same frame; then a pixel diff). **Objective-C vs Rust: all 9 images pixel-identical (0 of 921,600 / 230,400 / 1,800,000 pixels differ, worst channel difference 0):** CDG at 45 s (output + preview), side fill mode 1, side fill mode 2 (ambient blur), after the windows are hidden and re-shown, after the output window is resized to 900x500 (begin_transition + refresh_views), and an MP4 at 30 s (output + preview). Lifecycle stress (`shots.py cycles`): 15 create/play/destroy cycles + 10 rapid CDG<->MP4 switches faster than a load completes, 15/15 created, 15/15 clean shutdowns, still alive and showing a picture, 0 tracebacks; RSS growth 155.7 MB vs 152.7 MB for the Objective-C bridge on the same run (libmpv's, not new). The headless parity (`video_bridge_parity.py`) still passes; its log comparison now ignores view lines only for the headless case.
**Things found and fixed on the way:** (1) harness bugs (Objective-C bridge must be called from Qt's main thread; ctypes signatures for `refresh_views`), (2) a first screenshot diff of a few hundred pixels was a karaoke highlight wipe one column apart because the harness paused after seeking: pause first, then seek, and the images are identical. **Known benign difference:** after the pre-load `stop`, mpv's `dwidth` can still answer with the OLD file's size for a moment (the Objective-C bridge has the same race with different timing); the span only matters for CDG side fill and is corrected at the next VIDEO_RECONFIG.
**Not done / not proven:** background video (a second core + crossfade, V4), `grab_frame` (V4), rotation/spotlight underlay hosts (V5), the private render thread + `-update` locking + per-view cache (V5), KaraFun capture is unaffected (not part of this bridge), Intel hardware, the packaged-app library path (the dylib looks next to itself, `Frameworks/`, or `SINGWS_LIBMPV`), `rust/build_video.sh` + spec entries + the `video_bridge` setting (V6). **Next: V4 compositor completion** = background video (second libmpv core + render context, opacity, outgoing-frame crossfade, the `sidefill 4/5` shader modes), `grab_frame` (CDG from the retained 300x216 texture, other video via screenshot-raw), compared on screen with the same screenshot tool.

## V4 results (2026-10-11) - DONE, additive, still not imported by the app
Background video and frame grab are in the Rust bridge: a second libmpv core (muted, `ao=null`, software decode, `keep-open=no`, `idle=yes`, 16:9 stretch) with its own render context drawing into a second shared texture; the retained outgoing frame (`glCopyTexSubImage2D` into the "previous" texture) for the single-decoder dissolve; `set_background_opacity` driving the mix exactly as in `bridge.mm`; observed `time-pos` / `pause` / `eof-reached` published as atomics (the getters never query the core, so Qt timers cannot block on it); the shader's background modes 4 and 5 in the draw path; `stop_background`; and `grab_frame` (CDG read from the retained 300x216 texture and flipped, other video via `screenshot-raw video`, malloc'd for the caller to `free_frame`).
**Proof:** the same screenshot tool, now with static 60 s clips (so frames are clock-independent): **17 of 17 images pixel-identical to the Objective-C bridge**, adding to V3's 9: a CDG over background A, the dissolve at mix 0.5 (the image is a genuine blend of two clips with the CDG keyed over it), the settled background B, and after `stop_background` (each for output AND preview). `grab_frame` of the CDG returns byte-identical data (same SHA-1, 300x216, stride 1200). The background getters agree (position advancing, not paused, not at end while playing; position 0 and at_end after stop). **Noted, not a difference:** `grab_frame` of the paused MP4 returns nothing in BOTH bridges (screenshot-raw has no picture there), so there is no behaviour to match.
**Next: V5** = rotation/spotlight underlay hosts, the private render thread (+ `-update` locking, per-view cache) and the freeze test.
