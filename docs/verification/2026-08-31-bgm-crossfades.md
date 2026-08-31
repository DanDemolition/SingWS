# Background crossfade source verification — 2026-08-31

## Scope and installed state

Requested: smooth transitions between background tracks without dead air.
No queue/server/history changes in this work. Earlier uncommitted fixes remain
in the working tree. `/Applications/SingWS.app` is still 0.4.6.5, SHA-256:

`ecfcb0e0771c7ff6b33eee267528540117c85d923ac06acc29fc2122a40a0489`

No new app build, installation, physical-output audition, or full-show smoke
test is claimed. All tests used scratch SINGWS_HOME; native audio tests use
BASS device zero and a decode-only mixer, with no speaker output.

## Findings and changes

- The show log selected two-second `verified_dead_tail` transitions relative
  to file duration. Moliy / Shake It To The Max has duration 164.3s and cached
  audio end 161.7s; starting two seconds before EOF is already 0.6s into silence.
  Dorothy / TOMBSTONE TOWN and Jimin / Who each have 2.2s cached tails too.
  Scheduling now uses content end plus a small analysis margin and the
  engine's queued-audio lead. Dead tails no longer shorten configured overlap.
- Native BASS paired logarithmic slides have been replaced in the crossfade
  path by 65-node sine/cosine gain envelopes. Master fades stay unchanged.
  BASSmix multiplies the envelope by the source's normalization gain. Node
  positions use the mixer's format, and brief mixer locking starts both
  envelopes together. See the official [envelope API](https://www.un4seen.com/doc/bassmix/BASS_Mixer_ChannelSetEnvelope.html),
  [node format](https://www.un4seen.com/doc/bassmix/BASS_MIXER_NODE.html), and
  [channel lock](https://www.un4seen.com/doc/bass/BASS_ChannelLock.html).
- Next-source preparation now runs off the UI thread. Attachment remains on
  the owner thread, with primary identity, playlist target, and engine checks.
  Prepared sources remain paused: muting alone allowed their intros to be
  consumed before the crossfade. Starting a new source also attaches it paused
  until gain setup finishes, avoiding a brief unity-gain exposure.
- Known incoming leading silence is skipped without resetting the mixer
  buffer. Missing/invalid/old analysis uses the full track. Native completion
  checks envelope value plus buffered playback position; a paused transition
  cannot be completed by an elapsed wall-clock timer.
- Late ticks/manual Next cap the fade to outgoing content still available;
  EOF and already-detected silence use a short recovery fade. Short incoming
  files and canceled/failed transitions cannot leave native crossfade state
  stuck. Stop-after-current still suppresses automatic transitions.

## Verification

Results: 376 combined regression tests and 15 libmpv fallback tests passed
(391 total). `git diff --check` passed. The live August 31 log stayed at 1,114
lines and the installed executable hash remained unchanged.

Run from `/Users/Daniel/Documents/SingWS/SingWS`:

```sh
SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest \
  test_bgm_gapless test_transition_analysis test_bgm_master test_bass_fade_curve \
  test_bgm_volume_init test_performance_safety test_recent_regressions \
  test_karafun_lifecycle test_karafun_provider test_model_view_qa test_rotation_render_thread

SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./.venv-universal/bin/python -m unittest \
  test_libmpv_background_engine
```

`test_bgm_gapless` renders real native BASSmix output from synthetic PCM tracks.
Two different tones with matched normalized power stay within 0.4dB of steady
RMS over all 50ms windows across the overlap. The combined production scheduler
and native mixer test stays audible across an outgoing two-second silent tail
and an incoming one-second silent intro. This verifies the mechanics; it does
not establish subjective loudness consistency for every music recording.

Other cases: preloads remain at position zero, stale worker results are freed,
cancel and partial-envelope failure restore outgoing audio, short incoming
files finish, ended outgoing sources can advance, queued-audio/late/manual
scheduling works, missing metadata preserves unknown content, and pause or
stop-after-current cannot accidentally promote the next track.

## Build and remaining validation

The existing Intel build command is `./build_singws_mac_intel.sh` (not run for
this source milestone; it replaces generated build/dist output). Before
installation for a show, launch the rebuilt app with scratch data and the
actual output device. Play repeated track transitions including verified
tails/intros and unanalyzed tracks; test Next/Previous, pause/resume midfade,
reorder while preparing, stop-after-current, and karaoke interrupt/resume.
Earlier KaraFun/display fixes additionally need the full start → finish → next
local song video/ticker check documented in HANDOFF.md.

Limitations: unmeasured silent padding, unreadable/missing files, storage stalls
that outlast preparation, and GUI stalls spanning the entire scheduling window
can still delay a transition. This change does not promise seamless audio
under those conditions or introduce a new realtime mixer scheduler. The libmpv
fallback receives content offsets but retains its existing mix implementation.
