# Song-end shadow (opt-in, default off)

Question it answers on a real show: **at the song position where the app starts the background music at the end of a native karaoke song, would the Rust model of that decision
(`singws-songend`) have started it at the same place, for the same reason?** Python always decides; this only compares and logs.

## Switch
`"songend_shadow": true` in `~/SingWS/settings.json` (default **false**, AGENTS rule 9: instrumentation near live playback stays off until asked for). The Rust helper must be bundled
(`rust/build_songend.sh`, called by both installer build scripts); without it the shadow is skipped silently. Launch line: `[SONGEND-SHADOW] enabled available=1`.

## What the app does when it is on
* `_start_mpv_karaoke_transport` (after the audio and visual end floors are armed): `note_song_start` (mode only).
* The three places where the app really starts the music at a song's end note the FIRST start of the song, then hand over to a low-priority thread:
  `verified_end` (the verified-audio-end fade), `prestart` (the generic pre-start window) and `handoff:<reason>` (`_start_bg_with_fade`, the post-EOS / early-end path).
* The thread rebuilds an IDEALISED run of the song from the facts the app holds at that instant (settings the model reads, the song length, the audio end and duration the scans
  produced, the visual end) and drives the Rust model through it in `--lines` mode: a steady clock, a tick every 500 ms, scan answers delivered after 40 ms.
  The position of the model's first `bg:fade_in` is compared with the app's (tolerance 1.5 s, and the reason category must match).
* Songs with `karaoke_trim_verified_tail` off (the legacy meter detector) and KaraFun songs are not compared.

## Log lines (tag `SONGEND-SHADOW`, numbers only, never names/paths/ids)
`enabled available=0/1` (launch), `first check clean (...)` (once), `MISMATCH song N: app=<s>s (<reason>) rust=<s>s (<reason>)`, `skipped: <why>`.
A MISMATCH of several seconds with reason `handoff:karaoke_end_overlap` is a GUI stall that made the app miss the pre-start window (the idealised run has no stalls): that is the
model working, not a bug. Anything else is a finding: read the song's `[END-AUDIO]`/`[CROSSFADE]` lines.

## Proof
* `tools/songend_predict_parity.py` (also `test_songend_shadow.DriverMatchesTheHarness`): for every uneventful scenario of the song-end parity corpus, the REAL Python code (harness, fakes)
  and the driver + Rust model start the music at the same position for the same reason (136 of 151 identical, the other 15 never start the music before the song ends, in both; 0 different).
* End to end through the real `update_time_left` with the shadow on: normal songs compare clean; a song whose ticks were stalled over the pre-start window is flagged.
* NOT seen in a real show. The 4 hooks are `_songend_shadow_note` calls (a settings lookup when off).
