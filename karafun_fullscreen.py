"""Targeted fullscreen requests for KaraFun's separate audience renderer."""


def renderer_fullscreen_script(*, request=True):
    # Send the AX request once. Repeating it during a Space animation can
    # restart that animation; some KaraFun versions reject it altogether.
    return [
        'tell application "System Events"',
        'set requested to false',
        'set lastState to "NO_DUAL_RENDERER"',
        'repeat 30 times',
        'set matches to every application process whose name contains "KaraFun"',
        'if (count of matches) > 0 then',
        'tell item 1 of matches',
        'repeat with candidateWindow in windows',
        'if name of candidateWindow is "Dual Renderer" then',
        # KaraFun's player command is distinct from macOS's host-window
        # fullscreen command. Its title also verifies the custom player state.
        'try',
        'set playerMenu to menu 1 of menu bar item "View" of menu bar 1',
        'if exists menu item "Exit Player Full Screen" of playerMenu then',
        *([
            'set frontmost to true',
            'set value of attribute "AXMinimized" of candidateWindow to false',
            'perform action "AXRaise" of candidateWindow',
        ] if request else []),
        'return "FULLSCREEN"',
        'end if',
        *([
            'if not requested and (exists menu item "Expand Player to Full Screen" of playerMenu) then',
            'if enabled of menu item "Expand Player to Full Screen" of playerMenu then',
            'set requested to true',
            'set frontmost to true',
            'perform action "AXRaise" of candidateWindow',
            'click menu item "Expand Player to Full Screen" of playerMenu',
            'end if',
            'end if',
        ] if request else []),
        'end try',
        'try',
        'if value of attribute "AXFullScreen" of candidateWindow then',
        *([
            'set frontmost to true',
            'set value of attribute "AXMinimized" of candidateWindow to false',
            'perform action "AXRaise" of candidateWindow',
        ] if request else []),
        'return "FULLSCREEN"',
        'end if',
        'set lastState to "WINDOWED"',
        'on error errText number errNumber',
        'if not requested then return "AX_STATE_ERROR|" & errNumber & "|" & errText',
        'set lastState to "WINDOWED"',
        'end try',
        *([
            'if not requested then',
            'set requested to true',
            'try',
            'set frontmost to true',
            'set value of attribute "AXMinimized" of candidateWindow to false',
            'perform action "AXRaise" of candidateWindow',
            'set value of attribute "AXFullScreen" of candidateWindow to true',
            'end try',
            'end if',
        ] if request else []),
        'end if',
        'end repeat',
        'end tell',
        'end if',
        'delay 0.1',
        'end repeat',
        'return lastState',
        'end tell',
    ]


def renderer_click_target_script():
    # Re-resolve immediately before the gesture. Never click an old display
    # centre, the host window, or a renderer already entering fullscreen.
    return [
        'tell application "System Events"',
        'set matches to every application process whose name contains "KaraFun"',
        'if (count of matches) is 0 then return "NO_APP"',
        'tell item 1 of matches',
        'repeat with candidateWindow in windows',
        'if name of candidateWindow is "Dual Renderer" then',
        'try',
        'if exists menu item "Exit Player Full Screen" of menu 1 of menu bar item "View" of menu bar 1 then return "FULLSCREEN"',
        'end try',
        'if value of attribute "AXFullScreen" of candidateWindow then return "FULLSCREEN"',
        'set frontmost to true',
        'set value of attribute "AXMinimized" of candidateWindow to false',
        'perform action "AXRaise" of candidateWindow',
        'set p to position of candidateWindow',
        'set s to size of candidateWindow',
        'return "CLICK|" & ((item 1 of p) + ((item 1 of s) div 2)) & "|" & ((item 2 of p) + ((item 2 of s) div 2))',
        'end if',
        'end repeat',
        'return "NO_DUAL_RENDERER"',
        'end tell',
        'end tell',
    ]


def ensure_renderer_fullscreen(host, on_complete, is_current):
    """One AX request, at most one renderer gesture, then read-only verification."""
    import threading

    def deliver(result):
        if is_current():
            on_complete(result)

    def verify_after_click(attempt=0):
        if is_current():
            def verified(result):
                # macOS can report WINDOWED for a short period after the
                # fullscreen gesture while the window is moving into its
                # Space.  Confirm once more without sending another gesture;
                # otherwise a slow three-display transition is misreported as
                # a failure even when it finishes normally.
                if str(result or "").strip() == "WINDOWED" and attempt == 0 and is_current():
                    timer = threading.Timer(
                        1.0,
                        lambda: host._run_on_ui_thread(lambda: verify_after_click(1)),
                    )
                    timer.daemon = True
                    timer.start()
                    return
                deliver(result)

            if not host._karafun_run_window_script(
                renderer_fullscreen_script(request=False), on_complete=verified, timeout=6
            ):
                deliver("VERIFY_START_FAILED")

    def target_ready(result):
        if not is_current():
            return
        parts = str(result).split("|")
        if len(parts) != 3 or parts[0] != "CLICK":
            deliver(result)
            return
        try:
            point = (int(parts[1]), int(parts[2]))
        except ValueError:
            deliver("INVALID_RENDERER_BOUNDS")
            return

        def click():
            if not is_current():
                return
            ok = host._macos_native_double_click(*point)
            host._run_on_ui_thread(verify_after_click if ok else lambda: deliver("CLICK_FAILED"))

        threading.Thread(target=click, name="karafun-fullscreen-gesture", daemon=True).start()

    def requested(result):
        if not is_current():
            return
        if result != "WINDOWED":
            deliver(result)
            return
        if not host._karafun_run_window_script(
            renderer_click_target_script(), on_complete=target_ready, timeout=5
        ):
            deliver("TARGET_START_FAILED")

    if is_current() and not host._karafun_run_window_script(
        renderer_fullscreen_script(), on_complete=requested, timeout=6
    ):
        deliver("REQUEST_START_FAILED")


def renderer_raise_script():
    """Reveal the existing audience window without changing its fullscreen state."""
    return [
        'tell application "System Events"',
        'set matches to every application process whose name contains "KaraFun"',
        'if (count of matches) is 0 then return "NO_APP"',
        'tell item 1 of matches',
        'repeat with candidateWindow in windows',
        'if name of candidateWindow is "Dual Renderer" then',
        'set frontmost to true',
        'set value of attribute "AXMinimized" of candidateWindow to false',
        'perform action "AXRaise" of candidateWindow',
        'return "RAISED"',
        'end if',
        'end repeat',
        'return "NO_DUAL_RENDERER"',
        'end tell',
        'end tell',
    ]


def renderer_windowed_script(x, y, width, height):
    """Place only the audience renderer; report ready only after bounds agree."""
    x, y, width, height = map(int, (x, y, width, height))
    if width <= 0 or height <= 0:
        raise ValueError("Audience display must have positive dimensions")
    return [
        'tell application "System Events"',
        'set matches to every application process whose name contains "KaraFun"',
        'if (count of matches) is 0 then return "NO_APP"',
        'tell item 1 of matches',
        'set outputWindow to missing value',
        'repeat with candidateWindow in windows',
        'if name of candidateWindow is "Dual Renderer" then set outputWindow to candidateWindow',
        'end repeat',
        'if outputWindow is missing value then return "NO_DUAL_RENDERER"',
        'set frontmost to true',
        'set value of attribute "AXMinimized" of outputWindow to false',
        'perform action "AXRaise" of outputWindow',
        'set customExitRequested to false',
        'try',
        'set playerMenu to menu 1 of menu bar item "View" of menu bar 1',
        'if exists menu item "Exit Player Full Screen" of playerMenu then',
        'click menu item "Exit Player Full Screen" of playerMenu',
        'set customExitRequested to true',
        'end if',
        'end try',
        'if not customExitRequested then',
        'try',
        'if value of attribute "AXFullScreen" of outputWindow then set value of attribute "AXFullScreen" of outputWindow to false',
        'end try',
        'end if',
        'set positioned to false',
        'repeat 40 times',
        'set stillFullscreen to false',
        'try',
        'if exists menu item "Exit Player Full Screen" of menu 1 of menu bar item "View" of menu bar 1 then set stillFullscreen to true',
        'end try',
        'try',
        'if value of attribute "AXFullScreen" of outputWindow then set stillFullscreen to true',
        'end try',
        'if not stillFullscreen then',
        'if not positioned then',
        f'set position of outputWindow to {{{x}, {y}}}',
        f'set size of outputWindow to {{{width}, {height}}}',
        'perform action "AXRaise" of outputWindow',
        'set positioned to true',
        'else',
        'set p to position of outputWindow',
        'set s to size of outputWindow',
        # Tolerate rounding of logical screen coordinates, not a misplaced or
        # materially undersized window. Do not reveal the desktop on failure.
        f'if (item 1 of p) >= {x - 2} and (item 1 of p) <= {x + 2} and (item 2 of p) >= {y - 2} and (item 2 of p) <= {y + 2} then',
        f'if (item 1 of s) >= {width - 2} and (item 1 of s) <= {width + 2} and (item 2 of s) >= {height - 2} and (item 2 of s) <= {height + 2} then',
        'if not (value of attribute "AXMinimized" of outputWindow) then return "WINDOWED_READY"',
        'end if',
        'end if',
        'end if',
        'end if',
        'delay 0.1',
        'end repeat',
        'return "WINDOWED_PLACEMENT_FAILED"',
        'end tell',
        'end tell',
    ]
