# Stage A5: the playback orchestrator, in small shadow steps (2026-10-26)

Goal (from the plan): Rust decides HOW a queued song is played (which engine, which route, which refusals), with Python as the oracle until real shows say the two
always agree. Nothing here starts playback from Rust; playback stays in the app until each step has clean show reports.

## Why not one big step
`play_next_file` is about 570 lines (lines ~59,987-60,558 of 0.2.18.1.py): intro-loop release, teardown gates, transactional queue rollback, the rotation marker,
the start countdown, duration probing, tempo preset, then the dispatch to `play_mp4`, `play_mp3`, `play_mp3g_zip` and `play_cdg_mp3_dual`, then the confirmed-start
bookkeeping. Most of it is state and timing, not decisions. The decisions are small and pure, so each becomes its own shadow, in the same style as the plan, queue,
settings, store, wire and transport shadows: a pure Rust crate, one `singws_*_json` entry point in `libsingws_dsp_ffi`, a Python hook that logs `MISMATCH`, a setting
that defaults ON through the evidence migration, and a report line. Python wins every disagreement.

## Step 1 (built): the start route (`singws-start`, `start_shadow.py`, setting `start_shadow`)
The chain between "this singer's next song was picked" and "a play_* function is called":
1. an external KaraFun entry goes to the KaraFun capture path;
2. a primary path that does not exist is refused (`reject_missing_file`) and the queue is restored;
3. an MP3+G ZIP that is not extracted yet is "preparation" (`zip_prepare`), judged with `.lower()`;
4. then the dispatch, with case-SENSITIVE `.endswith`: a tuple/list `song_info` (older entry shape) or a text path; `.cdg` needs its `.mp3` (text) or exactly two items
   (tuple, otherwise Python raises), `.zip` needs `is_mp3g_zip`, anything else is `reject_unsupported_*`.
Quirks copied on purpose: a cached `A.ZIP` passes the preparation check (lower case) but is refused by the dispatch (`reject_unsupported_format`); a tuple of the wrong
length or a `song_info` that is not text or a sequence raises in Python (`error`, which the app never reports as a route).
The app calls `start_shadow.check(route_about_to_take, song_info, facts_known_so_far...)` at each commit point; Rust walks the same chain on the same facts and must end
on the same route. A fact the chain has not evaluated yet is simply not passed; if Rust needs it the answer is `need:<fact>`, which is logged as a disagreement.
No extra file or zip reads happen for the shadow.

## Step 2 (built): who plays the song's sound (`engine_route` in `singws-start`, same entry point with `"op":"engine"`)
`_karaoke_rust_voice_for_song` decides, just before a song loads, whether the Rust audio engine plays the sound (mpv keeps the picture and its clock, "shadow audio")
or mpv plays everything: the setting `karaoke_audio_engine` ("rust" in any case, an empty value means rust), whether the video bridge supports shadow audio, whether the
Rust engine is running (absent or headless = not running), and whether building the voice raised. Answers: `voice`, `reason` (`bridge_unsupported`, `engine_not_running`,
`error`, or empty), and what the bridge is told (`setShadowAudio(voice)` only when the bridge supports it). The app calls `check_engine` at the end of that method with its own
answer; Rust must give the same one. Tested against the REAL method over 144 fact combinations (`EngineWiring` in test_start_shadow_wiring.py).

## Step 3 (built): who sings next (`pick` in `singws-start`, `"op":"pick"`)
The first half of `play_next_file`: the loop that pops singers from the front of the queue (a skipped singer, or one without an unskipped song, goes to the bottom) and then
takes the first unskipped song of the singer it stopped on. The app calls `check_pick` with the queue as it was before the loop (singers as `{skipped, songs:"dst.."}`), the
index of the singer it ended up with and the index of the song; Rust computes both from the facts alone. A locked rotation (which reweaves its tail during the loop) is not
compared yet.
**Finding (2026-10-26, reported to the operator, NOT changed):** when every singer is skipped, the loop ends holding the last singer it looked at and, because the skipped branch
`continue`s without clearing `singer`, that SKIPPED singer's next song starts. (A last singer who is not skipped but has nothing playable is forgotten, so nothing starts.)
`test_the_all_skipped_quirk_is_what_the_app_really_does` pins it; the Rust rule models it; changing the app is the operator's call.

## Step 4 (built): the artist and title published for the song that started (`display_payload`, `"op":"display"`)
The text the TV, the singer cards and the server announce: the entry's own artist and title when both are non-empty after stripping, otherwise the library's display name
(looked up by the app, passed in), split on the FIRST " • " into artist and title, both stripped (no separator: all artist, empty title). Stripping is Python's (it also strips
U+001C..U+001F, which Rust's `trim` does not). Tested against the REAL method with 600 random names (`DisplayWiring`) and mutation-tested.

## Next steps (not built)
3. Transport arguments: key, tempo, start offset, loop and duration the transport receives (`_start_karaoke_transport`).
4. Confirmed-start bookkeeping (what is published to the screen and the server once the transport accepts).
5. Only after steps 1-4 are CLEAN in real shows: a Rust orchestrator that produces the whole start plan, still compared with Python before it may drive.
