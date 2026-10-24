# Dry-run show checklist: live controls from the Rust shell (nothing is applied)

Goal of the dry run: prove, on a real show, that the Rust shell can reach the app through your real server and that the app validates every command correctly, **without changing anything**. In `dry_run` the app checks each command
(known singer or song, fresh list, not expired, not a duplicate), writes a `[SHELL-CMD]` line saying what it WOULD do, answers `dry_run: would ...`, and changes no queue and plays nothing. Transport commands from the shell (`source: shell`) are logged and not executed.
The web remote page is not affected at all.

## Before the show (Intel Mac = the app; any Mac = the shell)

1. **Install the build that contains this feature on the show Mac.** It is built and gated but NOT installed anywhere (hashes are in `docs/verification/2026-10-23-rust-transport-shadow-a0.md`, updated by the gate run). Back up the installed app first (see the Intel checklist).
2. **Settings on the show Mac** (app closed, back up `~/SingWS/settings.json` first). Set exactly:
   `"shell_commands": "dry_run"` (and, as before, the shadow flags you want recorded). Leave `apply` for a later show. Nothing else is needed on the show Mac: no new password, no firewall change.
3. **Check production once** (a harmless command, so do it before doors open): start the app, open the shell's Controls tab on another Mac, connect (server address, tenant, host PIN). Expected: "Connected. App <version> is online" and the rotation list. Then press **Play/pause** once with nothing playing.
   Expected banner: *"Dry run: the app checked it and changed nothing"*. If the tab never says the app is online, production's relay is not forwarding the new `queue` message or the state; stop here and tell me (nothing was changed on the show Mac).
4. In the app's log (`~/SingWS/logs/singws_YYYY-MM-DD.log`) you should now see `[SHELL-CMD] ready mode=dry_run ...` at launch and one `[SHELL-CMD] id=... mode=dry_run ok=True message=dry_run: would play_pause` line per press.

## During the show: what to press in the shell (each press is one dry-run command)

Do each of these a few times at natural moments, then do the same thing in the app by hand if you actually want it to happen (the shell changes nothing):

* a singer **up** and **down**, and **Skip / Unskip** a singer, while a song is playing
* **Remove** a singer (two clicks) and **Remove** a song; do the same removal in the app afterwards if you want it gone
* a song **up / down** inside one singer
* **Next**, **Stop**, **Restart**, **Key +/-**, **Tempo +/-** between songs (they are logged, not executed)
* one deliberate **stale edit**: press a button, and while its result is still on screen change the queue in the app (add a request or move someone), then press another edit on the old list. Expected banner: *"The list changed before your edit arrived. It was refreshed; check it and try again"*, and the list updates by itself.
* once: close the shell mid-show and reopen it; it should reconnect with the PIN you type (the PIN is never saved)

You can also let someone else watch the shell on a tablet's browser (the existing web remote page) at the same time; the two do not interfere.

## After the show

1. On the show Mac, quit SingWS. Copy `~/SingWS/logs/singws_YYYY-MM-DD.log` and `~/SingWS/settings.json` to the dev Mac (unmodified).
2. On the dev Mac: `python3 tools/shadow_report.py /path/to/singws_YYYY-MM-DD.log --settings /path/to/settings.json`
   Read the **SHELL-COMMANDS** row:
   * `CLEAN`: at least one command, no defect. The detail line lists how many commands, and the outcomes (`dry_run`, `stale_view`, `unknown_entry`, `expired` ...).
   * `NOT EXERCISED`: the setting was on but nothing reached the app (the shell never connected, or production's relay did not forward the commands).
   * `ERROR`: an app-side failure, clock skew (the two Macs' clocks differ by more than a minute), a malformed command, or a command that was APPLIED although the setting said dry_run. Send me the lines it prints.
   * Normal refusals (`stale_view`, `unknown_singer`, `unknown_entry`, `expired`) are counted but are not errors.
3. Tell me anything that looked wrong on screen, especially: a button that did nothing, a banner that said success when it should not have, a list that did not refresh, a connection that dropped and did not come back.

## What this does NOT prove (so do not skip these later)

* `apply` mode on a real queue (the edits are proven identical to the buttons on 1,200 random cases and end to end on loopback, but a real show has not seen one yet);
* the shell's `wss://` connection to the REAL server: the TLS code is proven against a private CA on loopback and the public-roots path is built in, but only step 3 above shows that wskar.com's certificate is accepted. The shell binary to use is the universal build from `rust/build_shell.sh` (it includes TLS);
* the Intel build of the Qt shell on real Intel hardware (it has run under Rosetta only);
* the button handlers still use their own code; only after a dry run and an `apply` show will they be moved onto the shared methods.

## Turning it off again

Set `"shell_commands": "off"` (or delete the key) in `~/SingWS/settings.json` with the app closed. Off refuses every command from the shell and stops publishing the queue; nothing else in the app changes.
