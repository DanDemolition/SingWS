# Metal for the video bridge: inventory, measurements, recommendation (2026-11-12)

Status: investigation only. No shipping file changed, nothing installed, nothing launched against `~/SingWS`. Every run used a scratch `SINGWS_HOME`.

## Short answer

1. **libmpv here has no Metal render API.** Pinned mpv 0.41.0 (`native/mpv_runtime/versions.env`) is built with `-Dgl=enabled -Dvulkan=disabled`; `mpvapi.rs` already uses
   `MPV_RENDER_API_TYPE_OPENGL` (and `sw` for the silence scanner). libmpv's render API only offers OpenGL and SW in this build. So the realistic first step is
   what the brief guessed: mpv renders through GL into a texture, we share it (IOSurface-backed) and a **Metal** presenter draws it into the CAMetalLayer views.
2. **That change would not buy much CPU on this Mac, because the picture path is not where the CPU goes.** Measured below: the GL/driver part is ~7% of the
   process's CPU (about 2-3% of one core). The big cost is **software decoding of the background video** (`hwdec=no`), about 21% of a core, and it is the
   same at 720p and 1080p output.
3. **Recommended next step is therefore not Metal first.** It is hardware decode of the background clip (`videotoolbox-copy`), which measured 33% -> 13% of a core,
   behind a setting, plus an Intel-Mac measurement before deciding whether Metal is worth building at all.

## 1. Inventory: what a Metal presenter must replace

Both bridges implement the same picture path (`native/mpv_bridge/bridge.mm`, `rust/crates/singws-video/src/render.rs`; shader GLSL is in `shaders.rs`, "extracted verbatim").
Items that are GL today and what each becomes:

| Piece | Today | With Metal |
|---|---|---|
| mpv render context | `mpv_render_context_create` type `opengl` in a master CGL context (`create_render_context`) | unchanged (still GL; no Metal API in libmpv) |
| Shared texture | GL texture + FBO (`tex/fbo`, `cdg_tex/cdg_fbo`, `bg_tex/bg_fbo`, `prev_bg_tex`) sampled by the other contexts via CGL sharegroup | GL texture backed by an `IOSurface` (CGLTexImageIOSurface2D), wrapped as an `MTLTexture`. Needs care: GL writes and Metal reads need an explicit hand-off |
| Fences | `glFenceSync` after each render, `glWaitSync` in each consumer (`fence`, `bg_fence`) | consumer is Metal: GL must `glFlush` + fence/`glFinish` on the producer before the Metal command buffer samples; IOSurface has no automatic ordering. This is the riskiest part (the 2026-10-10 frozen-background bug was a missing fence) |
| Compositor shader | GLSL 150 in `shaders.rs` (modes: plain, side fill 1 = colour, 2 = blur, background underlay + dissolve via `prev_bg_tex`, uniforms `cdgSidefill`, scale/uvScale/uvOffset) | port to MSL; blur and fill-colour logic are straightforward, but parity must be pixel-checked (the existing 22-image compare tools do that) |
| Views | `NSOpenGLView` subclass per surface (output, preview, rotation, spotlight underlays), `flushBuffer` per frame | `NSView` with `CAMetalLayer`, `presentDrawable`; hide/show/resize/backing-size handling is reimplemented (and the per-view cache used by the render thread) |
| Render thread | private serial queue owns all GL; main thread only caches geometry | Metal needs no `CGLLockContext`; a render-thread design is simpler. The queue still owns the GL producer |
| `grab_frame` / `grab_cdg` | `glReadPixels` from retained FBO | read back the IOSurface (CPU map) or keep the GL read; low frequency |
| Background dissolve | two background textures, shader mix | same in MSL |
| Surface ordering | native NSViews stack by creation order (AGENTS rule 6) | unchanged requirement; the new views must be created at the same point. Verify by screenshot |
| Windows later | n/a | Windows would need a D3D path anyway; keep the presenter behind the existing thin shim, do not spread Metal into core.rs |

## 2. libmpv capabilities (what is in the pinned build)

- `strings` on `singws_libmpv.2.dylib` shows `opengl`, `sw`, `videotoolbox-copy`, `cocoa-cb`; configuration `-Dvulkan=disabled`, `-Dvideotoolbox-gl=enabled`, `-Dvideotoolbox-pl=disabled`,
  `-Dswift-build=enabled -Dmacos-cocoa-cb=enabled`.
- No libmpv Metal render API exists in mpv 0.41 for the render-API users; Vulkan (MoltenVK) is disabled and would be a much bigger change than Metal presentation.
- Hardware decode: `hwdec=auto-safe` for the karaoke core. For the **background** core `render.rs` forces `hwdec=no` with a comment: VideoToolbox could not export several
  older H.264 VJ-loop formats into the secondary render context on Intel. Keep that in mind (below).

## 3. Measurements (Apple Silicon dev Mac, macOS 27, M1 Max)

Harness: `tools/video_picture_cost.py` (new, standalone). It loads the real Rust dylib (default bridge), opens an output window and a 426x240 preview window, plays a real library CDG zip
(SC17631 PM Dawn) with volume 0, optionally a background clip at opacity 0.6 (an H.264 720p30 `VJ Loops H264 test/farkedpattern.mp4`), warms up 4 s, then measures 16 s of process CPU
(psutil) and device GPU utilisation (`ioreg` "Device Utilization %", whole-GPU, includes the desktop). Render thread on (the app default). Numbers are % of ONE core.

| Case | Process CPU | GPU util (whole device) |
|---|---|---|
| CDG only, 1280x720 output | **4.4%** | 33-40% |
| CDG + bg video (software decode), 1280x720 | **~34-37%** | 43-49% |
| CDG + bg video, 1920x1080 | **38%** | 56-62% |
| CDG + bg video, `hwdec=videotoolbox` | 18% | 48-53% |
| CDG + bg video, `hwdec=videotoolbox-copy` | **12.7%** | 41-48% |

`sample` of the 720p CDG + bg run (6 s, non-idle leaf samples, ~2.2 s CPU): ffmpeg/H.264 decode ~21%, other libmpv ~16%, libsystem_pthread (decoder thread sync) ~22%,
**OpenGL / GL driver ~7.5%**, kernel ~7.5%. Going from 720p to 1080p output added only ~1 point of CPU, which also says the compositing/present cost is small.

Reading: the CDG picture path alone is 4.4% of a core. Whatever Metal saves is a fraction of the ~2.5% GL share. A Metal rewrite cannot meaningfully answer "I can't afford CPU slowdowns" on Apple Silicon.

Caveats, said plainly: one Mac, one clip, no sound, the harness windows are not the app's audience window (no Qt Quick surfaces, ticker, KaraFun capture), GPU utilisation is device-wide and noisy, and the operator's
real loops are 70 Mbit Photo-JPEG `.mov`, which VideoToolbox cannot decode at all (those stay software). **The Intel Mac was not measured**; its GL driver and GPU are exactly the unknown that could change the
conclusion, so measure there before any Metal decision (command below).

## 4. Recommended plan (each step behind a setting, default unchanged until proven)

- **M0 (cheap, do first): measure on the Intel Mac** with `tools/video_picture_cost.py` (arguments in the file header; use the x86_64 libmpv in the Intel app bundle and `SINGWS_BRIDGE_AO=null`). If GL is again
  only a few percent, Metal is not worth the live-show risk.
- **M1: optional hardware decode for the background core.** New setting (e.g. `bg_video_hwdec`: `no` default, `videotoolbox-copy`). Prototype patch kept at `metal-video-bridge-assets/bg-hwdec-env.patch` (an env override of the
  `hwdec` option in `render.rs`, used for the numbers above; it was reverted from the tree). Must fall back to `no` on any load failure (the existing Intel comment says zero-copy `videotoolbox` failed there; `-copy` is the
  safer variant but is unproven on Intel). Files VideoToolbox cannot decode simply keep decoding in software.
- **M2 (only if M0 says GL is expensive on Intel): Metal presenter spike**, standalone first: mpv GL -> IOSurface-backed texture -> MSL fragment shader -> CAMetalLayer, with the fence/hand-off tested under the
  existing freeze and soak probes (`render_thread_probe.py`, `video_bridge_bgloop.py`), then pixel-compared against the GL bridge with the 22-image tools. New dylib symbol set must stay identical (37-function C ABI).
- **M3: wire behind a `video_presenter` setting (`gl` default, `metal`)**, on-screen rehearsal on this Mac then the Intel Mac, CDG + MP4 + bg video, rotation/spotlight underlays, window hide/show/resize, ticker still on top
  (rule 6), then a show. Never change surface creation order mid-show.

## 5. What was not done

No Metal spike was built: the inventory says it is viable but the measured gain is small, and building it would add risk without a number to justify it. No change to `0.2.18.1.py`, the installed app, or settings.
To reproduce: build `cargo build --release -p singws-video` into a private `CARGO_TARGET_DIR`, then run the harness with `QT_QPA_PLATFORM=cocoa`, signed Qt plugins, `SINGWS_LIBMPV`, `SINGWS_BRIDGE_AO=null`,
`SINGWS_RENDER_THREAD=1`, `SINGWS_HOME=$(mktemp -d)`. The harness opens two small windows on screen for ~25 s with no sound.
