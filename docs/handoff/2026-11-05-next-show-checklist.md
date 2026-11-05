# Next show: what to look for (2026-11-05)

After the show, on the Mac that ran it (Intel: run `after_show.sh` in the rehearsal folder, it makes a zip):

    python3 tools/show_checklist.py ~/Downloads/singws-show-*.zip

It prints one line per item: **PASS**, **FAIL**, **NOT EXERCISED** (the show never reached that path: silence, not evidence), **MANUAL** (the log cannot tell; the line says what to do).
`--strict` exits 3 when anything was not exercised; `--json` for scripts. The fuller report is still `tools/show_report.py` (same inputs).

| Item | What a good show looks like | Where it came from |
|---|---|---|
| Clean run | no traceback, FREEZE_DETECTED or ERROR line | always |
| Audio end rule | at least one `[END-AUDIO] audio runs to the end of the file` | aac7e52 |
| Fallback pre-start | `[CROSSFADE] BG pre-start fired (~5.0s remaining)` | 1.0.2.9 (`bgm_fallback_prestart_sec`) |
| Verified-end fade | `BG fade started at verified audio end`, no failures | 1.0.2.x |
| Fade-in floor | `fade-in start floor -40 dB` | 1.0.2.8 |
| End-of-song voice guard | `kept its place` lines; ask about blips | 1.0.2.8 |
| Queue-state + update shadows | `RAN, AGREED` | 1.0.2.8 and the slices B-E rebuild |
| Transport drives | media_end / karafun_end agree every time | 1.0.1.x |
| KaraFun watchdog drive | at least one decision (still has no clean show) | open |
| CDG seek guard | **cannot be seen in a log** (it logs nothing when it clamps) | open |

Things only the operator can answer (the tool prints them): dead air at song ends and which kind of song, blips at CDG song ends, fade-in timing, any seek that jumped to the end, anything wrong on the audience screen or ticker.

If it was clean, record it: `python3 tools/gate_ledger.py add <log> --all --label "show <date> <mac>"`.

Known gap: `cdg_seek_guard.clamp_seek` writes no log line, so a show can never prove the guard fired. Adding one would also need the same line in the Rust protocol twin (`karaoke_proto.rs`), because the transport parity compares log lines; not done.
