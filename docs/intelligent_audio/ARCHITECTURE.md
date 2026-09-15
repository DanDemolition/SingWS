# Intelligent Audio — architecture and safety map (Prompt 1)

Status: **discovery only. No production code changed.**
Written 2026-09-15 against the `SingWS-2.0` worktree. Plan of record:
`docs/intelligent_audio/ROADMAP.md`, merged into `docs/2.0/plan.md`.
Target: Apple Silicon, macOS 15+ only.

Line numbers refer to `0.2.18.1.py` unless another file is named. They drift;
search by symbol.

## 1. Current-state data flow

### Karaoke (CDG+MP3 and MP4)

- `KaraokeApp._ensure_mpv_karaoke_core()` loads `mpv_playback_iina.MpvPlaybackPlugin`,
  which wraps `native/mpv_bridge/bridge.mm` (`libsingws_mpv_bridge.dylib`): one
  in-process libmpv core renders once into a shared texture shown by two NSViews
  (output + preview).
- `mpv_karaoke_transport.MpvKaraokeTransport` is the only local karaoke transport
  (`start/stop/seek/pause/resume`, `query_times_ns`, `engine_at_end`,
  `set_modifiers` for key/tempo, `fade_out`, `cdg_sectors_remaining`,
  `cdg_generation`). It polls the bridge from a Qt timer (`_poll`).
- **Audio samples never touch Python.** Decoding, the af chain
  (DSP from `mpv_audio_filters.build_af_chain` → rubberband key change), and
  Core Audio output all run inside libmpv's own threads.
- Output device: selected by display label, resolved by the bridge against mpv's
  `audio-device-list` (`_mpv_selected_audio_output_name`).
- End of song: mpv EOS → `_on_karaoke_ended` → `QTimer.singleShot(0, _handle_media_end_safe)`.

### Background music (BGM) and soundboard

- `BackgroundMusicPlayer` (QObject) → `bass_background_engine.BassBackgroundEngine`
  (BASS via ctypes; two decks, native log gain slides, per-deck normalization).
  `libmpv_background_engine.LibmpvBackgroundEngine` is recovery only.
- `bass_soundboard_engine.py` shares the BASS runtime.
- `BASS_Init` binds **one output device process-wide**
  (`BassBackgroundEngine._bass_init_device`); switching device re-inits.
- Master bus/EQ: `singws_master_audio.py`, `singws_eq.py`.

### Transition decisions (the real control loop)

- `KaraokeApp.time_update_timer` → `update_time_left()` (≈500 ms tick, **GUI thread**).
  Every tick it: updates seek UI; runs the optional 3.2 s duration-only BGM
  pre-start (`karaoke_bgm_crossfade_enabled`, default off);
  `_maybe_trim_end_silence()`; EOS fallback (within 200 ms for 2 ticks);
  stalled-position fallback.
- `_prefire_bgm_at_verified_audio_end()` fades BGM under a cached, verified dead
  audio tail while visuals continue.
- Master switch: setting `seamless_transitions_enabled` (default True).
- **The level gate is dead on mpv**: `_read_level_db()` returns None because mpv
  has no metering tap. `engine_at_end()` (mpv `eof-reached`) substitutes.
- BGM resume after karaoke: `resume_reason` handling near line 57413.

### Cached analysis

- `transition_analysis.py`: `TransitionAnalysis` record (versioned, keyed by
  path + size + mtime), `TransitionAnalysisCache` (`~/SingWS/transition-analysis.json`
  + JSONL checkpoint), `audio_boundaries_from_envelope`,
  `estimate_fade_out_from_envelope`, `calculate_effective_karaoke_end`,
  `analyze_cdg_visual_bytes`, `analyze_mp4_visual_offline`,
  `select_bgm_crossfade_seconds`, `PreparedSourceIdentity`.
- Loudness: `loudness.json` + checkpoint, EBU R128 via `libmpv_media_jobs.py`
  in an **isolated, reniced helper subprocess** (`_loudness_lower_priority`,
  `AnalysisHelperError`). Never falls back to an in-process mpv core.
- Batch: `AnalyzeLibraryPreparationWorker` → `AnalyzeLibraryWorker` ×4 →
  `AnalyzeLibraryParallelCoordinator`; pauses while karaoke is active.
- BPM/first beat: `phrase_detect.py`, `_BpmDetectWorker`.
- Design contract already written: `docs/transition-analysis-architecture.md`.

### Logging

- `setup_logging()`: `QueueHandler` → `queue.SimpleQueue` → `QueueListener`
  (file + console). Non-blocking for callers, but **unbounded**.
- `_diag()` is the diagnostic call used across playback code. `PerfStats` and
  `_perf_log_if_slow` measure GUI-tick cost (`ui_update_time_left`).

## 2. What must never be blocked

Because samples stay native, "the audio callback" for SingWS means:

1. **libmpv's internal audio/decoder threads** — only reachable through the
   bridge. Any analysis tap added to `bridge.mm` must be lock-free and
   preallocated.
2. **BASS mixing/output threads** — no Python callbacks (`SYNCPROC`/`DSPPROC`)
   may be added that do work; a Python DSPPROC would take the GIL on BASS's
   audio thread.
3. **The Qt GUI thread** — it *is* the transition controller
   (`update_time_left`), the karaoke transport poller, and the BGM command path.
   A GUI stall delays EOS handling and fades. Already monitored by
   `_perf_log_if_slow`.
4. **The render threads** (`RenderThread*` classes, show-screen VFX) — visual only,
   but a stall is visible to the room.

## 3. Recommended insertion points

| Need | Insertion point | Notes |
|---|---|---|
| Phase 0 instrumentation | `update_time_left`, `_maybe_trim_end_silence`, `_prefire_bgm_at_verified_audio_end`, `_handle_media_end_safe`, `_on_karaoke_ended`, `BackgroundMusicPlayer.fade_in` / crossfade start | Emit typed events to a bounded in-memory ring; a single writer thread serializes JSONL. Do not reuse the unbounded log queue for high-rate events. |
| Program-audio level (fixes dead level gate) | `bridge.mm` + mpv af chain | Cheapest: add a lavfi `astats`/`ebur128` metadata stage to the composed af chain and expose last value via a bridge getter polled by `MpvKaraokeTransport._poll`. Keeps karaoke-only signal. |
| Program audio for classification | Same bridge tap (downsampled ring) — **not** a Core Audio process tap initially | A process tap captures the whole SingWS process (karaoke + BGM + soundboard mixed). Useful for "is anything audible", not for karaoke-only reasoning. |
| Cached track analysis | `transition_analysis.py` + existing helper subprocess | Extend `TransitionAnalysis`; bump version; lazy upgrade already supported. |
| Missing CDG lyric API | `MpvKaraokeTransport` | `cdg_lyrics_finished()` is called by host code (line ~38596) but is not implemented — confirmed 2026-09-15. Implement from cached `CdgVisualAnalysis`, not packet counts. |
| Transition observer | New pure module (no Qt) fed by the Phase 0 events; owned by `KaraokeApp` but with no reference to transport/BGM objects | Makes "Observer cannot control playback" testable by construction. |
| USB mic inputs | Separate native helper (Swift, AVAudioEngine input) sending activity frames over a local socket/pipe | Keeps capture off both BASS and mpv; helper crash = mic evidence disappears. |
| Vocal effects | Same Swift helper family, separate process from analysis | Per roadmap Prompt 10. |

## 4. Reusable code

- `transition_analysis.py` (records, cache, boundaries, CDG/MP4 visual analysis, crossfade selection) — covered by `test_transition_analysis.py`.
- Isolated analysis helper + checkpointed caches (`libmpv_media_jobs.py`, `test_analysis_helper_transport.py`).
- BASS two-deck crossfade with generation counters and preload identity.
- `PreparedSourceIdentity` for stale-event rejection.
- `PerfStats` / `_perf_log_if_slow` for stall attribution.
- `tools/show_cycle_simulation.py`, `tools/singws_perf_harness.py` — seeds for the replay harness.
- `mac_keep_awake.py` already uses PyObjC (a path to SoundAnalysis prototyping).

## 5. Risks

- **Monolith**: `KaraokeApp` is ~38k lines (19632–57777). Transition logic is
  spread across timer ticks, flags on `self`, and `QTimer.singleShot` hops.
  Extract only the controller seam Phase 2 needs.
- **GUI-thread controller**: 500 ms granularity and GUI stalls bound transition
  precision. Observer timestamps must use `time.monotonic()` plus the mpv
  playhead, not tick time.
- **Device binding**: BASS is process-wide, mpv is per-core. Putting output on a
  USB mixer while also capturing from it (Signature 10) needs both engines and the
  capture helper on the same Core Audio device to avoid clock drift.
- **Unbounded log queue** under event bursts.
- **Packaging**: arm64 build currently pins `LSMinimumSystemVersion` 12.3
  (SciPy wheel floor). Moving to 15.0 is a free simplification. Intel spec,
  `build_singws_mac_intel.sh`, `test_karafun_intel.py`, and x86_64 frameworks
  belong to 1.x only.
- **mpv frameworks**: `native/mpv_bridge/README.md` still describes x86_64-only
  IINA frameworks, while arm64 builds ship. Confirm where arm64 frameworks come from.
  GPL obligations are flagged as unresolved in that README.
- **Sleep/wake & device loss**: BASS re-init path exists; mpv device loss path not mapped yet.
- **Test environment**: the linked shell used for this audit is a Linux VM, not
  macOS; only pure-Python tests run there.

## 6. Proposed typed event/state model (not implemented)

```python
@dataclass(frozen=True)
class PlaybackEvent:
    t_mono: float            # time.monotonic()
    generation: int          # karaoke/BGM generation token
    kind: Literal[
        "karaoke_start", "karaoke_position", "karaoke_eos", "karaoke_stop",
        "karaoke_seek", "karaoke_skip", "lyrics_final", "audio_tail_verified",
        "bgm_start_requested", "bgm_started", "fade_start", "fade_end",
        "underrun", "gui_stall", "mic_activity", "mic_lost"]
    track_sig: str | None    # path hash + size + mtime, never singer name
    media: Literal["cdg", "mp4", "audio", None]
    playhead_s: float | None
    data: Mapping[str, float | int | str | bool]
```

Observer states: `Idle → KaraokePlaying → Ending(evidence) → Handoff → BgmPlaying`,
with `Uncertain` and `Bypassed(reason)` reachable from any state. Proposals are
`Proposal(action, at_playhead_s, confidence, reasons)` and are only logged.

## 7. Dependency recommendation

| Option | Verdict |
|---|---|
| Existing numpy/scipy + mpv lavfi | Use for Phases 0–2 and key detection. No new deps. |
| Apple SoundAnalysis (built-in) | First candidate for classification (Prompt 5). No bundle cost; needs PyObjC or Swift helper. |
| Core ML | Path for stems or custom models. |
| Core Audio process tap | Useful for whole-app output sensing; not karaoke-specific. |
| ONNX Runtime | Not needed unless Core ML conversion fails. |
| Essentia | Avoid: AGPL, heavy build; numpy/scipy covers key detection. |

## 8. Staged file-level plan with rollback points

1. **Platform floor** — `SingWS-arm64.spec` min 15.0, `build_singws_mac_arm64.sh` verify max 15.0; mark Intel scripts 1.x-only. Rollback: revert two files.
2. **Phase 0** — new `transition_events.py` (event types, bounded ring, writer thread) + call sites listed in §3; flag `ia_instrumentation_enabled`. Rollback: flag off / revert call sites.
3. **Level tap** — `bridge.mm` + `mpv_audio_filters.py` + transport getter. Rollback: chain omits stage.
4. **Phase 1 completion** — `transition_analysis.py` version bump, `cdg_lyrics_finished()` on transport. Rollback: version ignore.
5. **Phase 2 observer** — new `transition_observer.py` (pure), replay harness under `tests/`. Rollback: setting Off.
6. Later phases per merged plan.

## Baseline tests (this session)

Run in the linked Linux VM with system Python 3.10 (no PyQt6):
`SINGWS_HOME=$(mktemp -d) python3 -m unittest test_transition_analysis test_analysis_helper_transport`
→ 48 ran, 46 passed, 2 errors (`ModuleNotFoundError: PyQt6`, environment).
The full suite must be run on macOS per `AGENTS.md` ("Running the tests").
