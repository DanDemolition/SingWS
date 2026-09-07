# Rotation release 0.4.7.1 verification

## Changes

Transparent full-screen CDG/MP4 underlay using the existing mpv frame/clock. There is no separate lyrics preview or second decoder. Every 30 seconds, a brief neon sweep, particle burst, and staggered queue pulse spotlights the next singer without covering the QR code or continuous specials ticker. Purple/green stage design, names-only queue and large QR remain in place.

Implementation: `0.2.18.1.py`, `mpv_playback_iina.py`, `native/mpv_bridge/bridge.mm`, architecture specs and `assets/rotation-stage-purple.png`. Earlier authorized fixes include `network_lifecycle.py` and `karafun_fullscreen.py`.

## Build

Run from repository root. Intel first:

```sh
SINGWS_MPV_FRAMEWORKS="$PWD/native_dual_view/Frameworks-intel-release" SINGWS_MPV_BRIDGE="$PWD/native_dual_view/Frameworks-intel-release/libsingws_mpv_bridge.dylib" arch -x86_64 .venv-build-intel-rosetta/bin/python -m PyInstaller --noconfirm --distpath local-installs/20260906-release-0.4.7.1/intel --workpath local-installs/20260906-release-0.4.7.1/build-intel SingWS-x86_64.spec
SINGWS_MPV_FRAMEWORKS=/private/tmp/singws-repair-runtime .venv-repair/bin/python -m PyInstaller --noconfirm --distpath local-installs/20260906-release-0.4.7.1/arm64 --workpath local-installs/20260906-release-0.4.7.1/build-arm64 SingWS-arm64.spec
```

Stage outside iCloud with `ditto --norsrc --noextattr`, clear xattrs, remove only dangling QtFFmpeg symlinks, then sign using `codesign --force --deep --sign - --entitlements SingWS.entitlements APP`. Both staged bundles passed strict signing, architecture verification and minimum OS checks (Intel 12.0, ARM 12.3). Both DMGs passed `hdiutil verify`.

## Tests and launch

```sh
SINGWS_HOME=$(mktemp -d) arch -x86_64 .venv-build-intel-rosetta/bin/python -m pytest -q test_rotation_tv_design.py test_model_view_qa.py test_show_screen_vfx.py
SINGWS_HOME=$(mktemp -d) DYLD_LIBRARY_PATH=/private/tmp/singws-repair-runtime .venv-repair/bin/python -m unittest test_libmpv_background_engine test_phrase_detect test_transition_analysis test_no_gstreamer_guard
open /Applications/SingWS.app
```

35 UI tests and 99 native-media tests passed. The broader root suite passed 1085 tests initially; its failures were isolated to architecture-specific source-runtime mismatches, test-double assumptions fixed here, and a countdown timing test that passed on rerun. Source native-media tests on Intel resolve the ARM source Frameworks directory; this does not reflect the packaged Intel runtime.

Intel app launched with scratch data under Rosetta and exited successfully via SINGWS_SMOKE_EXIT_MS=9000. ARM app installed and launched using normal profile, connected to server; idle rotation screen rendered. Location permission dialog remains for operator choice. No live queue entries were added for testing.

Final CDG visual probe captures the queue spotlight rising, holding, and settling at 720p, plus 1080p and overflowing-queue states. The transparent CDG underlay, QR code, queue, and ticker remain visible throughout. Physical Intel/venue-TV rehearsal remains unverified.
