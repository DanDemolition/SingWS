# Intel Mac show checklist (what to do after tonight's show, and for the next one)

The show machine is the Intel Mac. The arm64 build installed on the dev Mac (b6935310...) is NOT what the show runs. The Intel build is built and gated but not installed anywhere:

| File | SHA-256 |
|---|---|
| SingWS-1.0.1.4-x86_64-installer.dmg | c8a4fb6b196d54d5077e5b8e897f509e554ce1eb417021b1c8a293c66335feac |

## After tonight's show (whatever build the Intel Mac ran)

1. On the Intel Mac, quit SingWS. Copy two files to the dev Mac (AirDrop, USB or `scp`): the newest `~/SingWS/logs/singws_YYYY-MM-DD.log` and `~/SingWS/settings.json`.
   Do not edit them. (Log lines never contain hosts, keys or values; settings.json holds the shadow flags the report needs.)
2. On the dev Mac: `python3 tools/shadow_report.py /path/to/singws_YYYY-MM-DD.log --settings /path/to/settings.json`
   Send the output. Verdicts: CLEAN, PARTIAL, NOT EXERCISED, NOT ENABLED, DISAGREED, ERROR. `--require-all` exits 3 unless everything is CLEAN.
3. If it says NOT ENABLED for a row, that build or settings file did not have the flag on; it is not a failure of the rules.

## To install the Intel build on the Intel Mac (only when you decide to; install is never done without your OK)

1. Copy `SingWS-1.0.1.4-x86_64-installer.dmg` to the Intel Mac and check `shasum -a 256` against the table above.
2. Quit SingWS. Back up the current app: `ditto -c -k --sequesterRsrc --keepParent /Applications/SingWS.app ~/SingWS-app-backup-$(date +%Y%m%d).zip`.
3. Mount the DMG, `ditto /Volumes/<name>/SingWS.app /Applications/SingWS.app.incoming`, `codesign --verify --deep --strict` it, then swap it in and keep the old app renamed as a rollback.
4. Back up `~/SingWS/settings.json`, then set these keys to true (the app is closed): `transport_shadow`, `queue_shadow`, `plan_shadow`, `settings_shadow`, `store_shadow`, `wire_shadow`, `transport_drive_media_end`, `transport_drive_watchdog`, and `publish_live_state` if you want the live stage file.
   Python's answer always wins; these flags only log and compare.

## What a show must contain to turn rows from NOT EXERCISED to CLEAN

* a native CDG/MP4 song that finishes by itself (media end)
* a KaraFun song that finishes by itself (automatic end), and ideally one stopped early
* a host delete or complete of a server request (queue tombstone branch)
* background music playing across a song start (plan shadow)
* any skip or stop (transport shadow)
