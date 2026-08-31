# Ticker covering host controls — 2026-08-31

## Finding and scope

The installed normal app was version 0.4.6.5, executable SHA-256
`9adb6b0c305c03bd150a3e0c085b475f699590e7f8c9853d274f66cff1740dff`.
WindowServer showed its detached ticker at floating level 3, while the host and
audience windows were at level 0. Its bounds overlapped the host's bottom
controls. The application-active guard cannot distinguish the host from the
audience window. A native-window reproduction visibly covered the host button.
The initial desktop captures caught other foreground apps, not the reported
SingWS overlap; do not describe those captures as visual proof of the fault.

## Small change

`DetachedPainterTicker` keeps the isolated top-level painter and existing Qt
transient owner. On macOS it matches the audience NSWindow's level and orders
only the ticker immediately above that window, instead of globally raising a
floating tool window. It does not modify mpv's native view hierarchy or order
the audience/host window. The ticker ignores input so mouse events reach the
underlying window. A minimized/hidden audience or failed ordering hides the
strip until a later repair can restore it safely.

Only `0.2.18.1.py` changes in production relative to the preceding installed
candidate. No audio, queue, history, server, or saved geometry changes.

## Verification

307 regressions passed:

```sh
SINGWS_HOME=$(mktemp -d) QT_QPA_PLATFORM=offscreen ./qtvenv/bin/python -m unittest \
  test_ticker_window_ordering test_ticker_and_qr test_recent_regressions \
  test_performance_safety test_karafun_monitor_safety test_karafun_lifecycle
```

The seven new ordering regressions cover owner-relative ordering, auxiliary
fullscreen level inheritance, missing native windows, minimize, hide/restore,
and failure that must not leave a blocking strip. Existing ticker tests also
verify input transparency. The old source assertion requiring a global raise
was updated to require the owner-relative repair.

An isolated Cocoa harness loaded the actual painter/ticker class from the
source, with scratch `SINGWS_HOME` and no host/audio/server. Eight native checks
passed: matching window level, native mouse transparency, host focus preserved,
ticker visible, movement, hide, restore, and host focus after restore.
Screenshots visibly confirmed host controls cover the ticker when overlapping
and the ticker remains with the audience when windows are separated. Window
movement was captured after allowing Cocoa to apply queued geometry changes.

The harness, logs and screenshots are in the private local install receipt
`../local-installs/20260831-112154/`; desktop images include unrelated apps and
must not be published. The normal app and profile were backed up there too.

## Build and remaining checks

The candidate uses the normal `SingWS-x86_64.spec`, normal `com.singws.app`
identity, normal profile and server connection. Build output is isolated under
`/private/tmp/singws-local-install-20260831-112154/`; no public installer or
release is changed. The build command uses the pinned `.venv-universal` runtime,
`SINGWS_MPV_FRAMEWORKS` pointing to the repo's `native_dual_view/Frameworks`, and
scratch `SINGWS_HOME`/`PYINSTALLER_CONFIG_DIR`, then runs:

```sh
python -m PyInstaller --noconfirm --workpath "$BUILD_ROOT/build" \
  --distpath "$BUILD_ROOT/dist" "$BUILD_ROOT/source/SingWS-x86_64.spec"
```

Installed and launched the normal app at 11:27 PDT. Executable SHA-256:
`2af5d6fe2909af944ea820172d981858285da621e025ff31f4640d261462edfe`.
Frozen code matches the tested source; native library loads, 432 Intel
architecture checks, 840 minimum-macOS checks, and strict deep signing passed.
The installed ticker is now at level 0 alongside the audience/host windows.

The actual host screenshot shows the full bottom toolbar above the ticker.
An ordinary CGEvent mouse click on Settings at its visible bottom-toolbar
position opened Settings; its native close button then dismissed it without
edits. Earlier process-targeted and AX-text click attempts did not open it and
are not counted as successful mouse checks. The successful screen-coordinate
click and screenshot are recorded in the private receipt. No new Python error,
ticker sync failure, or SingWS native crash report appeared. Server settings
and history content remain unchanged. Requests are closed and playback is idle.
An additional installed-app screenshot confirmed the audience artwork and
ticker remain visible when the audience window is brought forward. The host
was raised afterwards; the operator switched to another app, whose focus was
left alone.

No external display/fullscreen KaraFun playback was exercised by this ticker
test; the operator's full-song/end/next-video rehearsal remains necessary.
