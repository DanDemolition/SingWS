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
    click-to-preview.
  - **Suggested** intro-skip point from a lightweight energy/onset heuristic
    (numpy/scipy, snapped to the nearest bar). Generic suggestion only — not
    section-type labels.
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

### Notes
- Pairs with the SingWS-Server marker-sync endpoints for cross-machine markers.
- Tests: `test_phrase_markers.py`, `test_phrase_detect.py`,
  `test_rotation_lock.py`, plus the existing rotation/regression suites.
  Run with `SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS=1 .venv/bin/python -m pytest`.
