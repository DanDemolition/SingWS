# AGENTS.md

## Project purpose

This repository contains SingWS, a Python/PyQt karaoke player application.

The long-term goal is to improve the playback engine while preserving the existing UI and behavior wherever possible.

Current engineering direction:
- Keep the application as a Python/PyQt app.
- Add native modules only where required for realtime audio performance.
- For pitch/time processing, prefer open-source and free solutions.
- Do not introduce paid/proprietary SDK dependencies.
- Favor Signalsmith Stretch for realtime pitch/time DSP work.
- Target macOS first unless the task explicitly says otherwise.

---

## High-level architecture rules

1. Python/PyQt owns:
   - UI
   - user interaction
   - views
   - app flow
   - non-realtime logic

2. Native code owns:
   - realtime audio processing
   - low-latency transport
   - sample-accurate timing
   - pitch/tempo DSP
   - output-device interaction if needed

3. Do not move UI logic into C++.

4. Do not rewrite the entire app to force a new architecture.

5. Preserve existing behavior unless the task explicitly asks to replace it.

6. Prefer adapter-style integration over invasive rewrites.

---

## Working style

Always work in small, reviewable steps.

For any non-trivial task:
1. Inspect the repo first.
2. Explain findings briefly.
3. Identify exact insertion points before editing.
4. Propose the smallest safe change.
5. Implement only that change.
6. Stop and report:
   - files changed
   - what was done
   - how to build/test
   - blockers / assumptions
   - next recommended step

Do not do a giant rewrite in one pass.

Do not silently refactor unrelated code.

Do not remove old code paths until the replacement path is proven and testable.

If a feature is risky, add it behind a flag, toggle, or alternate path first.

---

## Planning rules

For complex tasks, plan before coding.

When the task is architectural, ambiguous, or spans multiple files:
- inspect first
- summarize current structure
- produce a short implementation plan
- wait to execute the plan unless the user asked for direct implementation

When a task is large, break it into milestones.

Preferred milestone order for playback-engine work:
1. repo inspection
2. native module scaffolding
3. basic native playback path
4. transport timing
5. Signalsmith integration
6. pitch change
7. tempo change
8. seek/reset/preroll
9. Python UI wiring
10. video sync refinement
11. cleanup / tests / docs

---

## Audio-engine rules

The transport is the source of truth.

Audio is the master clock.
- Video must follow audible playback time.
- UI timers should follow engine-reported time, not guessed wall-clock timing.
- Decoder position is not the same as audible output time.

When implementing pitch/tempo DSP:
- keep pitch and tempo as separate controls
- allow key change without changing tempo
- allow tempo change without changing key
- expose both transport time and audible time to Python if possible

When implementing seek:
- reset DSP state on seek
- clear stale buffered state
- use preroll when required by the DSP engine
- avoid clicks/glitches during seek transitions when practical

When implementing latency-sensitive logic:
- prefer predictable timing over clever abstraction
- document any latency compensation clearly

---

## Signalsmith-specific guidance

Use Signalsmith Stretch as the preferred open-source pitch/time engine unless the task explicitly says otherwise.

Integrate Signalsmith behind a small backend interface so the DSP engine can be swapped later if needed.

Do not spread DSP-specific logic all over the codebase.

Preferred wrapper shape:
- NativeAudioEngine
- StretchProcessor / DSP adapter
- transport state separated from UI state

If Signalsmith is not yet integrated:
- first build a stub native engine with the final intended Python-facing API
- then wire real playback
- then add DSP
- then refine timing/sync

---

## Python/native bridge rules

Preferred bridge: pybind11.

Expose a small stable Python API.

Preferred Python-side engine surface:
- load(path)
- play()
- pause()
- stop()
- seek_seconds(value)
- set_pitch_semitones(value)
- set_tempo_ratio(value)
- current_transport_time()
- current_audible_time()

Do not expose unnecessary low-level C++ details to the Python UI.

Keep the Python-facing API clean and easy to test.

---

## Build and dependency rules

Minimize new dependencies.

Before adding any dependency:
- explain why it is needed
- explain whether it is runtime or build-time only
- prefer well-supported, lightweight, open-source dependencies

Do not add paid SDKs.

Do not add platform-specific complexity unless needed for the current milestone.

Target macOS first for native playback work, but avoid locking the codebase into unnecessary macOS-only abstractions when a thin platform layer would work.

---

## Code change rules

Make the smallest safe edits first.

Prefer:
- narrow diffs
- clear naming
- comments only where they add real value
- straightforward control flow

Avoid:
- broad file moves
- cosmetic cleanup unrelated to the task
- renaming large surfaces without strong justification
- speculative abstractions

If changing an existing file:
- preserve style already used in that file unless it is clearly harmful
- avoid mixing unrelated cleanup with functional changes

---

## Testing and verification

After each milestone, provide:
1. what changed
2. exact files changed
3. exact commands to build
4. exact commands to run
5. how to verify success
6. what is not finished yet

When possible, add the smallest practical verification:
- smoke test
- import test
- build test
- basic playback test
- simple regression test

Do not claim something works unless it has been verified or clearly marked unverified.

Be explicit about assumptions and blockers.

---

## Reporting format

Use this output structure for substantial tasks:

1. Repo findings
2. Plan
3. Changes made
4. Files changed
5. Build/test commands
6. Verification status
7. Blockers / assumptions
8. Next recommended step

Keep reports concise but specific.

---

## What to avoid

Do not:
- rewrite the whole app when the task only needs an incremental change
- replace current playback prematurely
- guess architecture details without inspecting the code
- claim realtime safety without reasoning about the audio path
- put heavy DSP work into the Python UI layer
- hide broken build steps
- continue making large changes after the requested milestone is complete

---

## Preferred approach for this repo

When asked to improve playback:
- first find the current playback and transport code
- preserve the current app structure
- add a native extension module incrementally
- keep Python as the application shell
- keep native code focused on performance-critical audio logic
- stop after each milestone and report clearly