# SingWS Audio Pipeline Audit - 2026-05-31

## Signal Paths

### Karaoke Playback

`PythonKaraokeTransport` starts ffmpeg audio decode to stereo float32 at 48 kHz,
optionally applies cached static loudness gain with ffmpeg's `volume` filter,
then runs Signalsmith pitch/tempo processing. The decoded PCM optionally passes
through the app-owned karaoke `GraphicEQ`, then is written to `QAudioSink`.

MP4 video is decoded by a separate ffmpeg raw-video reader. CDG graphics are
decoded in Python and emitted only when the CDG bitmap generation changes.

### BGM Playback

Preferred path is `BackgroundMusicPlayer` -> `BassBackgroundEngine`.
BASS decodes each track into source decks, BASSmix mixes/crossfades them, the
source deck has its own static LUFS normalization gain, and the mixer master is
reserved for the user's BGM volume and fade in/out. Optional BGM EQ is attached
once to the mixer output.

Fallback path is GStreamer `filesrc -> decodebin -> audioconvert ->
audioresample -> tee`. The main branch uses a single `volume` element containing
`user_volume * cached_static_loudness_gain`; the meter branch downmixes/resamples
to a low-rate appsink for the idle visualizer.

### Preview Playback

The visible karaoke preview uses the same Python karaoke frame signal path as
the main karaoke output. The soundboard preview/clip pads use one `QMediaPlayer`
and `QAudioOutput` per pad, with only the pad volume applied.

### Monitoring Playback

No separate audio-monitor chain was found. The code has BGM meter monitoring for
visual/silence detection, but it is not routed to audio output. The meter branch
is a low-rate analysis sink only.

## Bottlenecks / Duplicates Found

1. BGM normalization was effectively mixer-global in BASS.
   During crossfade, the incoming track's normalization could alter the outgoing
   track because normalization lived on the mixer master. Fixed by moving
   normalization to each BASS source deck.

2. BGM GStreamer fallback had a different normalization behavior.
   It now uses the same cached static LUFS gain in the single playback volume
   stage.

3. EQ disabled is faster because active EQ still runs Python/Numpy/Scipy in the
   BASS DSP callback. Stream reconfiguration was already moved out of the hot
   callback in the prior pass, but active EQ remains a residual CPU risk.

4. Karaoke normalization is a single ffmpeg `volume` filter before Signalsmith.
   No duplicate karaoke normalization pass was found.

5. BGM fallback has one redundant analysis branch by design: the meter branch.
   It is low-rate and used for visualizer/trailing silence detection, not audio
   output. BASS path uses `BASS_Mixer_ChannelGetLevelEx` instead.

## Implemented

- Cached loudness info now records integrated LUFS and sample peak.
- Applied gain is capped so the measured sample peak stays below -1 dBFS.
- BGM normalization is per-deck in BASS, so crossfades preserve each track's
  own correction.
- BGM crossfade creates the incoming deck with its normalization gain before
  the slide starts.
- GStreamer fallback applies static normalization in the existing `volume`
  element instead of adding a DSP branch.
- Diagnostics now log active BGM chain details:
  track name, deck, LUFS, peak, applied gain, mixer/fade stage, and EQ state.

## Residual Risk

- Existing cached loudness entries without `peak_db` still work, but they only
  use LUFS clamp until the file is re-analyzed.
- BGM EQ now attempts native BASS parametric EQ first. If native BASS FX are not
  available, the Python/Numpy DSP fallback is disabled by default; it can only be
  re-enabled explicitly with `SINGWS_ALLOW_PYTHON_BGM_EQ_DSP=1`.
- Live CPU/RAM/playback timing still needs representative MP4, CDG+MP3, BGM,
  preview, and soundboard clips.
