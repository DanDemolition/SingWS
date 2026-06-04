# SingWS Performance Audit - 2026-05-31

## Scope

Static audit plus local micro-benchmarks were run against the current workspace.
The local library database is small (`/Users/daniel/SingWS/singws.db`, about
176 KB), and no MP4/CDG/BGM media files were available in the workspace, so full
before/after live playback metrics require a representative karaoke library.

## Ranked Findings

1. High impact: overlapping search workers can compete with playback.
   Rapid typing could start a new `SongSearchThread` while the previous SQLite
   LIKE/fuzzy search was still running. Stale results were ignored, but stale
   workers still consumed CPU. Fixed by coalescing pending searches so only one
   DB worker runs at a time.

2. High impact on CDG-heavy playback: unchanged CDG frames were emitted every
   15 ms. Even when the CDG bitmap had not changed, the transport emitted
   `frame_ready`, causing unnecessary UI/video-window repaint work. Fixed by
   adding a decoder generation counter and emitting only on actual bitmap
   changes.

3. Medium impact: read-only DB lookups could run schema setup.
   `find_by_artist_title()` and `find_by_path()` called `init_schema()`, which
   can perform write/migration work. Those paths are used as fast lookup helpers
   during UI/network operations. Fixed by keeping them read-only and gracefully
   skipping the normalized fallback on older DBs until an explicit rebuild.

4. Medium impact: EQ stream configuration repeated on audio hot paths.
   Karaoke PCM queueing and BASS BGM DSP callback rechecked/reconfigured the EQ
   stream format on every audio block even though stream format is stable. Fixed
   by caching configuration in the karaoke path and moving BASS EQ configuration
   out of the DSP callback.

5. Medium residual risk: BASS BGM EQ still executes Python/Numpy/Scipy from a
   BASS DSP callback. The callback has a flat/disabled fast path and now avoids
   repeated stream config, but active EQ still crosses Python from an audio
   thread. Longer-term fix: move EQ DSP native-side or use BASS-native filters.

6. Medium residual risk: many timers remain active in the UI.
   Important recurring timers include karaoke transport at 15 ms, ticker frame
   timers, time-left/background timers at 250-500 ms, auto-save at 60 s, and
   network polling when configured. No broad timer rewrite was done in this pass.

7. Medium residual risk: MP4 playback uses separate ffmpeg audio/video
   subprocesses. This gives isolation and hardware-accelerated/downscaled video,
   but process count and pipe throughput increase during MP4 playback.

## Metrics Collected

Search micro-benchmark after fixes, 20 runs each:

- `a`: 150 results, average 1.20 ms, max 1.51 ms
- `love`: 13 results, average 7.22 ms, max 25.61 ms
- `beatles`: 0 results, average 8.06 ms, max 10.48 ms
- `zzzzmisspell`: 0 results, average 3.81 ms, max 4.40 ms
- process RSS: 20.9 MB start, 22.5 MB end
- process threads: 1

EQ micro-benchmark after fixes, 1000 blocks of 4096 stereo float32 frames:

- disabled EQ: 0.0002 ms/block
- enabled flat EQ: 0.0001 ms/block
- enabled +3 dB on all bands: 0.2383 ms/block
- process RSS: 102.8 MB
- process threads: 1

Baseline single-run search before fixes:

- `a`: 150 results, 4.2 ms
- `love`: 13 results, 5.6 ms
- `beatles`: 0 results, 7.4 ms
- `zzzzmisspell`: 0 results, 3.8 ms
- process RSS: 18.9 MB
- process threads: 1

## Verification

Commands run:

- `python3 -m py_compile 0.2.18.1.py python_karaoke_transport.py song_index.py singws_eq.py bass_background_engine.py`
- `/Users/daniel/Documents/SingWS/venv/bin/python -m pytest -q test_singws_sanity.py test_bass_init_once.py`

Result:

- Syntax check passed.
- Tests passed: 2 passed, 1 PyGI deprecation warning.

## Live Playback Metrics Not Yet Collected

The following require representative local media and an interactive playback run:

- MP4 playback CPU/RAM/thread/process count/render timing/audio timing
- CDG playback CPU/RAM/thread/process count/render timing/audio timing
- BGM playback CPU/RAM/thread/process count/audio timing
- network polling under real server conditions

Recommended next step: run a scripted playback session with known MP4, CDG+MP3,
and BGM files while sampling `psutil`, process children, QTimer jitter, frame
emit intervals, and audio queue depth.
