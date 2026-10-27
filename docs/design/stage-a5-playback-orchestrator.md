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
**Finding (2026-10-26, FIXED in 1.0.2.1 at the operator's request: a skipped singer is now forgotten in the loop, so nothing starts and nobody is queued twice):** when every singer was skipped, the loop ends holding the last singer it looked at and, because the skipped branch
`continue`s without clearing `singer`, that SKIPPED singer's next song starts. (A last singer who is not skipped but has nothing playable is forgotten, so nothing starts.)
`test_when_every_singer_is_skipped_nothing_starts_and_the_queue_is_untouched` now pins the fixed behavior and the Rust rule models it.

## Step 4 (built): the artist and title published for the song that started (`display_payload`, `"op":"display"`)
The text the TV, the singer cards and the server announce: the entry's own artist and title when both are non-empty after stripping, otherwise the library's display name
(looked up by the app, passed in), split on the FIRST " • " into artist and title, both stripped (no separator: all artist, empty title). Stripping is Python's (it also strips
U+001C..U+001F, which Rust's `trim` does not). Tested against the REAL method with 600 random names (`DisplayWiring`) and mutation-tested.

## Step 5 (built): the rotation after a started song is committed (`after_start`, `"op":"after_start"`)
When the transport reports the song really started, `_commit_pending_start` marks the singer as having sung, appends them to the bottom and, in rotation mode, moves the
marker when it was on the top singer, repairs a missing or doubled marker (first wins, none = first singer), fills missing `round_sung` keys and restarts a round in which every
active singer has sung. The app compares the three flags per singer (marker, round_sung, has_sung) with Rust over the queue it had just before the commit. A locked rotation (tail
reweave) is not compared. Tested against the REAL method and its commit callback over 500 random rotations (`RotationWiring`).
**Finding (2026-10-26, same bug, FIXED in 1.0.2.1):** in that case the pick loop has already appended the retained singer to the bottom, so the
commit appends the SAME singer record again: the queue then holds that singer twice (one record, two rows; `test_the_all_skipped_quirk_puts_the_same_singer_in_the_queue_twice`).
The shadow still logs `NOTE singer_already_in_queue` (and skips the comparison) as a safety net; it should now never appear.

## Step 6 (built): the arguments that reach the transport (`transport_args`, `zip_args`, `"op":"transport"` / `"op":"zip_args"`)
Every start route ends in `_start_karaoke_transport(audio_path, video_path, mode, semitones, start_seconds, loop_seconds, duration_seconds)`. The three wrappers forward different
subsets, which is the whole rule: `_play_mp4` -> audio and picture are the same file, mode `mp4`, **no duration**; `_play_cdg` -> audio is the MP3, picture the CDG, mode `cdg`,
start + loop + duration; `_play_mp3` -> mode `audio`, no picture, **neither loop nor duration** (the transport's defaults apply). The key is `int(semitones or 0)` (truncated toward
zero). Each wrapper hands its own raw arguments to the shadow just before the call (`_stash_transport_shadow`), and `_start_karaoke_transport` compares what it actually received
with Rust's answer on entry; a direct call with no wrapper in front of it is simply not compared.
`play_mp3g_zip` merges what it is called with and what an earlier not-yet-extracted call stashed for the same ZIP (`_mp3g_pending_*`): start = the call's if positive else the stash's
(else 0.0), loop = the call's if truthy else the stash as is, duration = `float(call or stash or 0.0)`. That merge is its own comparison (`check_zip_args`, after the values are computed).
Only plain finite numbers and text are modelled; anything else is skipped, never reported. Tested against the REAL chain (`play_next_file` -> `play_*` -> transport recorder) over
300 random starts, and `play_mp3g_zip` over 400 random calls/stashes (`test_start_shadow_transport.py`); mutation-tested (27 mutants, one documented equivalent).

## Next steps (not built)
3. Confirmed-start bookkeeping (what is published to the screen and the server once the transport accepts).
4. Only after steps 1-3 are CLEAN in real shows: a Rust orchestrator that produces the whole start plan, still compared with Python before it may drive.
