# Changelog — SingWS (desktop player)

## Unreleased

### Added
- **Phrase-Aligned Song Start** — start a song at a musical phrase (4/8/16 bars
  in, or a hand-placed marker) instead of only at the file start, to skip long
  intros and land on a verse/chorus.
  - Right-click a queued song → **Phrase Start** submenu (Beginning / 4 / 8 /
    16 Bars / Custom…). BPM-derived offsets use embedded ID3 BPM when present.
  - **Phrase Start dialog** with a rendered waveform, vertical labeled marker
    lines (4 Bar / 8 Bar / 16 Bar / Custom / Suggested), a position slider, and
    click-to-preview, plus **▶ Preview / ⏸ Pause / ⏹ Stop** controls (preview
    no longer plays on with no way to stop it; closing the dialog stops it too).
  - **Automatic BPM detection** from the audio (pure numpy/scipy tempo
    estimation — no tags or typing needed), cached per song. The 4/8/16-bar
    starts and the Custom dialog now fill in tempo on their own; the dialog also
    **pre-selects a suggested start** so it's near plug-and-play.
  - **Suggested** intro-skip point from a lightweight energy/onset heuristic
    (numpy/scipy, snapped to the nearest bar).
  - **Section detection** — structural boundaries (self-similarity matrix +
    Foote novelty, pure numpy/scipy, no new dependencies) shown as labeled
    waveform markers for the transitions (Intro→Verse, Verse→Chorus,
    Chorus→Verse, Instrumental→Vocal). Boundaries are reliable; section labels
    are best-effort **estimates** the host can rename/correct.
  - Markers persist per song and are **reused automatically**; **JSON
    export/import** for backup; **cloud backup/restore per tenant** (pushes on
    change, pulls on startup) so markers survive a reinstall / new machine.
  - Playback reuses the existing gap/overlap-free transport, so lyrics, CDG,
    MP4 video and audio stay in sync after the jump, and key/tempo changes are
    preserved.
- **Rotation Lock** (Rotation mode only) — a **Lock** button in the queue
  controls. While locked, new singers are woven into the **next** rotation
  instead of cutting into the current one; existing singers adding another song
  keep their slot. The lock auto-clears when the next rotation starts, and when
  switching Queue Mode back to Classic. State is saved in settings (survives
  restart). Covers manual host adds, remote/web requests, and Singer History
  adds. Classic mode is unchanged.
- **Host song-limit override** — the operator can add a 3rd+ song for a singer
  past the per-singer cap; public/web/Singer-History requests stay capped.
- **Intro Loop** (between-songs filler, opt-in) — when a song ends, the next
  queued song's intro auto-loops a chosen number of bars (4/8/16) starting at
  its phrase point (reuses the auto-BPM / phrase markers) instead of background
  music; hitting **Play/Next** releases the loop and that same track continues
  past the loop and plays through. Settings toggle + bar selector; a `LOOPING`
  badge shows while held. Default OFF — Classic playback is unchanged.
  - **Beat-aligned & accurate**: loops now snap to a detected **beat grid**
    (start on a downbeat, span exactly N bars) using improved, octave-hardened
    tempo + beat-phase analysis (pure numpy/scipy) — so they sit on the groove
    instead of drifting. Bar-derived phrase starts are beat-aligned too.
  - **Analyze Library** (Library Tools) — batch-detect tempo + beat grid for
    every song and cache it (incremental, resumable, cancellable progress), so
    loops/starts are accurate and instant. "Re-analyze All" forces a full pass.

### Fixed
- **Analyze Library progress window** — opens on top of the main window (was
  hidden behind it); clicking Analyze again while a pass runs resurfaces the
  existing window instead of spawning duplicates; a corrupt/locked file now
  times out (~60s) and is skipped instead of hanging the whole batch.
- **Rotation Lock logic** (ported from the 0.3.1.0 build) — locked newcomers
  are woven in **behind** returning singers (no longer cut ahead); the lock
  only engages while the marked next-rotation singer isn't already next (button
  disabled with a tooltip otherwise; no safe gap → newcomers append like
  unlocked rotation); a stale saved lock in that state clears itself.

### Notes
- Pairs with the SingWS-Server marker-sync endpoints for cross-machine markers.
- Tests: `test_phrase_markers.py`, `test_phrase_detect.py`,
  `test_rotation_lock.py`, plus the existing rotation/regression suites.
  Run with `SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 .venv/bin/python -m pytest`.
