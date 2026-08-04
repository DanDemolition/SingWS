"""
mpv_playback.py — SingWS karaoke playback on mpv, childed-Metal architecture.

Implements SingWS's established 17-method playback contract, so
`MpvPlaybackAdapter` and every existing call site in the app
(`_play_karaoke_media`, `play_cdg_mp3_dual`, `play_mp4`, `play_mp3`, transport, status tick)
keep working unchanged.

    attach / loadSingWSMedia / playMedia / pauseMedia / stopMedia / seekMedia
    positionMs / durationMs / isPlaying / atEnd
    setVolume / setAudioDevice / setVideoStretch / setCdgOutputSidefill
    errorString / version / audioDescription

ARCHITECTURE (validated 2026-07-28 on an M4 mini and an Intel iMac / Radeon Pro 5300):

  * ALL audible sound comes from a headless `mpv --idle --no-video` in ITS OWN PROCESS, driven
    over a unix-socket JSON IPC. Nothing the GUI does — window churn, fullscreen, queue edits —
    can reach the audio path.
  * Video is TWO independent mpv instances, each rendering with `vo=gpu-next` into its OWN
    borderless window, childed over the app's output and preview widgets via `addChildWindow`.
    This is the only way to get Metal on macOS: mpv refuses `--wid` (its macvk context makes its
    own surface), and the libmpv render API is OpenGL-only.
  * The two video instances are muted followers (`ao=null`, `mute=yes` on the same audio file, so
    each still has a realtime master clock) chased to the engine with a proportional speed skew.

Key change is rubberband on the ENGINE only (followers are muted — pitching silence is waste).
Tempo is `speed` on the engine AND both followers, or the video drifts away from the audio.
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import glob
import json
import locale
import os
import platform
import queue
import socket
import subprocess
import shutil
import sys
import threading
import time

# Must be set before `import mpv`. In a PyInstaller macOS app, native binaries
# live in Contents/Frameworks (normally also exposed as sys._MEIPASS). Put the
# bundle first so the laptop never needs Homebrew; retain source-run locations
# afterward for development on Apple Silicon and Intel.
_RUNTIME_DIRS = []
if getattr(sys, "frozen", False):
    _meipass = str(getattr(sys, "_MEIPASS", "") or "")
    if _meipass:
        _RUNTIME_DIRS.append(_meipass)
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _RUNTIME_DIRS.extend((
        _exe_dir,
        os.path.normpath(os.path.join(_exe_dir, "..", "Frameworks")),
    ))
    # Homebrew's mpv uses Vulkan on top of MoltenVK for its macOS gpu-next
    # windows. The build bundles both the loader and MoltenVK; point the loader
    # at the bundle-relative ICD manifest so a clean Mac does not need Homebrew.
    _bundle_icd = os.path.normpath(os.path.join(
        _exe_dir, "..", "Resources", "vulkan", "icd.d", "MoltenVK_icd.json"
    ))
    if os.path.isfile(_bundle_icd):
        os.environ["VK_ICD_FILENAMES"] = _bundle_icd
_RUNTIME_DIRS.extend((
    os.path.dirname(os.path.abspath(__file__)),
    "/opt/homebrew/lib",
    "/usr/local/lib",
))
_RUNTIME_DIRS = list(dict.fromkeys(p for p in _RUNTIME_DIRS if p))

_DEFAULTS = [os.path.expanduser("~/lib"), "/usr/local/lib", "/usr/lib"]
os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(
    _RUNTIME_DIRS + _DEFAULTS
)

# mpv's official macOS embedding marker. Without it, every new macOS video
# output runs `NSApp.applicationIconImage = mpvIcon`, replacing the host
# application's Dock icon whenever CDG/MP4 playback starts. Bundle mode tells
# the embedded libmpv instances to leave SingWS's NSApplication identity
# alone. The separate headless audio process explicitly removes this variable
# from its child environment below.
os.environ["MPVBUNDLE"] = "true"

import mpv as libmpv  # noqa: E402
from PyQt6.QtCore import QEventLoop, QPoint, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

VERSION = "mpv-childed-metal 2.3"

# Reproduce the retired native CDG renderer's side-fill without replacing
# mpv's decoder or renderer. CDG is a 300x216 command surface whose intended
# visible center is 288x192 (6px/12px border crop). The old renderer sampled
# the outer border once per row and extended that color beside the untouched
# center image. This tiny lavfi graph performs the same operation and produces
# a near-16:9 340x192 frame. It is installed only on the output follower; the
# preview remains the normal 4:3 CDG image.
CDG_OUTPUT_SIDEFILL_FILTER = (
    "lavfi=["
    "split=2[main][edge];"
    "[main]crop=288:192:6:12[center];"
    "[edge]crop=1:192:0:12,"
    "scale=26:192:flags=neighbor,"
    "split=2[left][right];"
    # Scaling the one-pixel edge strip otherwise propagates a degenerate
    # sample-aspect ratio through hstack. Image files ignore that metadata,
    # but mpv honors it and displays the whole frame as a narrow vertical
    # strip. The assembled pixels are square, so reset SAR explicitly.
    "[left][center][right]hstack=inputs=3,setsar=1"
    "]"
)

# --------------------------------------------------------------------------- objc trampoline
_objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
_objc.objc_getClass.restype = ctypes.c_void_p
_objc.objc_getClass.argtypes = [ctypes.c_char_p]
_objc.sel_registerName.restype = ctypes.c_void_p
_objc.sel_registerName.argtypes = [ctypes.c_char_p]
_objc.object_getClassName.restype = ctypes.c_char_p
_objc.object_getClassName.argtypes = [ctypes.c_void_p]

NS_WINDOW_ABOVE = 1


class NSPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class NSSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class NSRect(ctypes.Structure):
    _fields_ = [("origin", NSPoint), ("size", NSSize)]


# A 4-double struct comes back in registers on arm64, but through a hidden pointer on x86_64 —
# where objc_msgSend_stret is required. Intel Macs are a supported target, so pick per arch.
_IS_X86 = platform.machine() in ("x86_64", "i386")


def _msg_rect(receiver, selector: bytes, argtypes=(), *args) -> NSRect:
    fnptr = _objc.objc_msgSend_stret if _IS_X86 else _objc.objc_msgSend
    fn = ctypes.cast(
        fnptr, ctypes.CFUNCTYPE(NSRect, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
    )
    return fn(receiver, _objc.sel_registerName(selector), *args)


def view_screen_rect(view_id: int):
    """The host view's TRUE rect in AppKit screen coordinates, straight from AppKit.

    This exists because Qt cannot be trusted here: the app reports isFullScreen()==True while
    QWidget.geometry() still returns the small windowed size (also seen with the
    earlier renderer, so it is not an mpv-specific quirk). The views themselves are laid out
    correctly — the ticker renders in the right place — so AppKit knows the truth even when Qt
    does not. Using it also drops the primary-screen coordinate flip, which was only ever an
    approximation across multiple displays."""
    try:
        view = ctypes.c_void_p(int(view_id))
        win = _msg(view, b"window")
        if not win:
            return None
        bounds = _msg_rect(view, b"bounds")
        in_window = _msg_rect(view, b"convertRect:toView:",
                              (NSRect, ctypes.c_void_p), bounds, None)
        on_screen = _msg_rect(win, b"convertRectToScreen:", (NSRect,), in_window)
        w, h = float(on_screen.size.width), float(on_screen.size.height)
        if w <= 1 or h <= 1:
            return None
        return (float(on_screen.origin.x), float(on_screen.origin.y), w, h)
    except Exception:
        return None


def _msg(receiver, selector: bytes, restype=ctypes.c_void_p, argtypes=(), *args):
    fn = ctypes.cast(
        _objc.objc_msgSend,
        ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes),
    )
    return fn(receiver, _objc.sel_registerName(selector), *args)


def ns_windows() -> list:
    out = []
    try:
        app = _msg(_objc.objc_getClass(b"NSApplication"), b"sharedApplication")
        wins = _msg(app, b"windows")
        for i in range(int(_msg(wins, b"count", restype=ctypes.c_ulong))):
            out.append(_msg(wins, b"objectAtIndex:", ctypes.c_void_p, (ctypes.c_ulong,), i))
    except Exception:
        pass
    return out


def ns_window_title(win) -> str:
    try:
        s = _msg(win, b"title")
        if not s:
            return ""
        raw = _msg(s, b"UTF8String", ctypes.c_char_p)
        return raw.decode("utf-8", "replace") if raw else ""
    except Exception:
        return ""


def ns_class_name(obj) -> str:
    try:
        raw = _objc.object_getClassName(ctypes.c_void_p(obj))
        return raw.decode("utf-8", "replace") if raw else ""
    except Exception:
        return ""


def ns_window_of_view(view_id: int):
    """Qt's winId() is an NSView*; walk up to its NSWindow."""
    return _msg(ctypes.c_void_p(int(view_id)), b"window")


def ns_screen_index_of_window(win):
    """Return the NSScreen index used by mpv's macOS --screen option."""
    try:
        target = _msg(win, b"screen")
        if not target:
            return None
        screens = _msg(_objc.objc_getClass(b"NSScreen"), b"screens")
        count = int(_msg(screens, b"count", restype=ctypes.c_ulong))
        for index in range(count):
            screen = _msg(
                screens,
                b"objectAtIndex:",
                ctypes.c_void_p,
                (ctypes.c_ulong,),
                index,
            )
            if int(screen) == int(target):
                return index
    except Exception:
        pass
    return None


def attach_child(parent_win, child_win) -> bool:
    try:
        _msg(parent_win, b"addChildWindow:ordered:", ctypes.c_void_p,
             (ctypes.c_void_p, ctypes.c_long), ctypes.c_void_p(child_win), NS_WINDOW_ABOVE)
        return True
    except Exception:
        return False


def detach_child(child_win) -> bool:
    """Remove an NSWindow's existing child relationship, if any.

    orderOut: only changes visibility; it does not reliably clear parentWindow.
    A later addChildWindow: to that same parent can therefore be a no-op, leaving
    mpv's Metal surface associated with the display it occupied before the move.
    """
    try:
        parent_win = _msg(child_win, b"parentWindow")
        if parent_win:
            _msg(
                parent_win,
                b"removeChildWindow:",
                None,
                (ctypes.c_void_p,),
                ctypes.c_void_p(child_win),
            )
        return True
    except Exception:
        return False


def make_borderless(win):
    """Make mpv's native window a shadowless borderless video surface.

    This is applied exactly once, before the window is childed. The earlier
    experiment restyled an already-attached window while native fullscreen was
    also moving it through Spaces, which disturbed ordering. SingWS now owns
    fullscreen at the Qt parent and performs a settled reattach afterward.
    """
    try:
        # NSWindowStyleMaskBorderless == 0.
        _msg(win, b"setStyleMask:", None, (ctypes.c_ulong,), 0)
        _msg(win, b"setHasShadow:", None, (ctypes.c_bool,), False)
        _msg(win, b"setMovable:", None, (ctypes.c_bool,), False)
        _msg(win, b"setMovableByWindowBackground:", None, (ctypes.c_bool,), False)
        return True
    except Exception:
        return False


def set_ignores_mouse(win, flag: bool = True):
    """Let clicks fall THROUGH to the Qt widget underneath — otherwise the video surface eats
    every event and double-click-to-fullscreen never reaches the app."""
    try:
        _msg(win, b"setIgnoresMouseEvents:", None, (ctypes.c_bool,), bool(flag))
    except Exception:
        pass


def order_above(win, other_win):
    try:
        num = int(_msg(other_win, b"windowNumber", ctypes.c_long))
        _msg(win, b"orderWindow:relativeTo:", None,
             (ctypes.c_long, ctypes.c_long), NS_WINDOW_ABOVE, num)
    except Exception:
        pass


def order_out(win):
    try:
        _msg(win, b"orderOut:", None, (ctypes.c_void_p,), None)
    except Exception:
        pass


def set_window_rect(win, x, y, w, h) -> bool:
    """Size first, then origin — setContentSize: otherwise pins a different corner."""
    try:
        _msg(win, b"setContentSize:", None, (NSSize,), NSSize(float(w), float(h)))
        _msg(win, b"setFrameOrigin:", None, (NSPoint,), NSPoint(float(x), float(y)))
        return True
    except Exception:
        return False


def notify_window_delegate(win, selector_name: bytes, notification_name: bytes) -> bool:
    """Deliver one NSWindowDelegate notification to mpv's existing window."""
    try:
        delegate = _msg(win, b"delegate")
        if not delegate:
            return False
        selector = _objc.sel_registerName(selector_name)
        responds = _msg(
            delegate,
            b"respondsToSelector:",
            ctypes.c_bool,
            (ctypes.c_void_p,),
            ctypes.c_void_p(selector),
        )
        if not responds:
            return False
        name = _msg(
            _objc.objc_getClass(b"NSString"),
            b"stringWithUTF8String:",
            ctypes.c_void_p,
            (ctypes.c_char_p,),
            notification_name,
        )
        notification = _msg(
            _objc.objc_getClass(b"NSNotification"),
            b"notificationWithName:object:",
            ctypes.c_void_p,
            (ctypes.c_void_p, ctypes.c_void_p),
            ctypes.c_void_p(name),
            ctypes.c_void_p(win),
        )
        if not notification:
            return False
        _msg(
            delegate,
            selector_name,
            None,
            (ctypes.c_void_p,),
            ctypes.c_void_p(notification),
        )
        return True
    except Exception:
        return False


def notify_window_display_changed(win) -> tuple[bool, bool]:
    """Deliver the callbacks mpv misses when its hidden child changes screens.

    windowDidChangeScreen: updates mpv's display link and display identity.
    windowDidResize: then raises VO_EVENT_RESIZE | VO_EVENT_EXPOSE in mpv's
    Metal backend, forcing the existing gpu-next surface to redraw without
    changing geometry or creating another native window.
    """
    screen_changed = notify_window_delegate(
        win,
        b"windowDidChangeScreen:",
        b"NSWindowDidChangeScreenNotification",
    )
    resized = notify_window_delegate(
        win,
        b"windowDidResize:",
        b"NSWindowDidResizeNotification",
    )
    return screen_changed, resized


# --------------------------------------------------------------------------- engine process
_LIVE_ENGINES = []


def _reap_engines():
    for eng in list(_LIVE_ENGINES):
        try:
            eng.terminate()
        except Exception:
            pass


atexit.register(_reap_engines)  # `mpv --idle` NEVER exits on its own


def find_mpv_binary():
    bundled = [
        os.path.join(directory, "mpv") for directory in _RUNTIME_DIRS
    ]
    for cand in (
        os.environ.get("SINGWS_MPV"),
        *bundled,
        shutil.which("mpv"),
        "/opt/homebrew/bin/mpv",
        "/usr/local/bin/mpv",
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _sweep_stale_sockets():
    for path in glob.glob("/tmp/singws-mpv-*.sock"):
        try:
            pid = int(os.path.basename(path).split("-")[2])
            os.kill(pid, 0)
        except (IndexError, ValueError):
            continue
        except OSError:
            try:
                os.unlink(path)
            except OSError:
                pass


class MpvIpcClient:
    """Headless out-of-process mpv. Every read comes from a locally cached copy of observed
    properties, so a property read can NEVER block the caller (one once blocked the GUI 906ms)."""

    OBSERVED = ("audio-pts", "time-pos", "duration", "pause", "eof-reached",
                "aid", "audio-codec-name", "speed", "volume")

    def __init__(self, name: str, log=print):
        self.name = name
        self.log = log
        self.sock_path = f"/tmp/singws-mpv-{os.getpid()}-{name}.sock"
        self._cache = {}
        self._lock = threading.Lock()
        self._req_id = 0
        self._sock = None
        self._proc = None
        self._stop = threading.Event()
        self.ended = threading.Event()
        self._spawn()
        _LIVE_ENGINES.append(self)

    def _spawn(self):
        _sweep_stale_sockets()
        binary = find_mpv_binary()
        if not binary:
            raise RuntimeError("mpv binary not found (set SINGWS_MPV, or brew install mpv)")
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
        child_env = os.environ.copy()
        child_env.pop("MPVBUNDLE", None)
        self._proc = subprocess.Popen(
            [binary, "--idle=yes", "--no-video", "--no-terminal",
             # Headless audio helper: never register a second macOS Dock app.
             "--macos-app-activation-policy=prohibited",
             f"--input-ipc-server={self.sock_path}",
             # The library lives on a USB exFAT/fskit volume with slow metadata, and mpv's
             # demuxer-readahead-secs DEFAULTS TO 1 — that is what causes start-of-song skips.
             "--cache=yes", "--demuxer-readahead-secs=10",
             "--demuxer-max-bytes=256MiB", "--audio-buffer=1.0"],
            env=child_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self._sock is None:
            if os.path.exists(self.sock_path):
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self.sock_path)
                    self._sock = s
                except OSError:
                    pass
            if self._sock is None:
                time.sleep(0.05)
        if self._sock is None:
            raise RuntimeError(f"mpv engine '{self.name}' never opened {self.sock_path}")
        threading.Thread(target=self._reader, daemon=True).start()
        for i, prop in enumerate(self.OBSERVED, start=1):
            self.command("observe_property", i, prop)
        self.log(f"[MPV-ENGINE] {self.name} pid={self._proc.pid}")

    def _reader(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                ev = msg.get("event")
                if ev == "property-change":
                    with self._lock:
                        self._cache[msg.get("name")] = msg.get("data")
                elif ev == "end-file" and msg.get("reason") in ("eof", "error"):
                    # The engine owns the real clock, so it is authoritative on media end.
                    self.ended.set()
                elif ev == "start-file":
                    self.ended.clear()

    def command(self, *args):
        if self._sock is None:
            return
        with self._lock:
            self._req_id += 1
            rid = self._req_id
        try:
            self._sock.sendall(
                (json.dumps({"command": list(args), "request_id": rid}) + "\n").encode("utf-8")
            )
        except OSError:
            pass

    def set_property(self, name, value):
        self.command("set_property", name, value)

    def get(self, name, default=None):
        with self._lock:
            v = self._cache.get(name, default)
        return default if v is None else v

    def loadfile(self, path: str):
        self.ended.clear()
        with self._lock:
            self._cache.pop("duration", None)
            self._cache.pop("audio-pts", None)
            self._cache.pop("time-pos", None)
        self.command("loadfile", path, "replace")

    def stop(self):
        self.command("stop")
        self.ended.clear()
        with self._lock:
            self._cache.clear()

    def terminate(self):
        self._stop.set()
        try:
            self.command("quit")
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
        if self in _LIVE_ENGINES:
            _LIVE_ENGINES.remove(self)


# --------------------------------------------------------------------------- video follower
def _new_mpv(tag: str, **opts):
    # libmpv hard-aborts ("Non-C locale detected") if LC_NUMERIC != C. QApplication resets the
    # locale from the environment, so re-assert immediately before EVERY construction.
    locale.setlocale(locale.LC_NUMERIC, "C")
    return libmpv.MPV(**opts)


class MpvVideoFollower:
    """One muted mpv instance rendering Metal into its own borderless window, childed over a Qt
    host widget. Not a widget itself — it drives a foreign NSWindow."""

    def __init__(self, tag: str, host_widget, keepaspect: bool = True,
                 fast_profile: bool = False, log=print, always_visible: bool = False):
        self.tag = tag
        self.host = host_widget
        self.log = log
        # The preview area paints plain black when idle (PreviewWindow.force_black), and an idle
        # mpv window is also black — so the preview window never needs hiding. Skipping the
        # hide/show cycle removes the re-attach churn that show/hide transitions invite.
        self.always_visible = always_visible
        self.marker = f"singws-mpv-{tag}-{os.getpid()}"
        self.win = None
        self._before = set(ns_windows())
        self._last_rect = None
        self._pending_rebuild = False
        self._persistent = False
        self._hidden = False
        self._last_layout_clamp_diag = None
        self._visual_state_lock = threading.Lock()
        self._pending_visual_generation = 0
        self._active_visual_generation = 0
        self._ready_visual_generation = 0
        self._shutdown = False

        opts = dict(
            vo="gpu-next", hwdec="auto-safe", border="no", osc="no",
            input_default_bindings="no", input_vo_keyboard="no",
            keepaspect="yes" if keepaspect else "no",
            title=self.marker,
            # Otherwise mpv resizes its own window to the video's native size and a 300x216 CDG
            # shrinks the window out from under our positioning on every load.
            auto_window_resize="no",
            ao="null", mute="yes",
            # Keep the core alive with no file loaded. Combined with force-window (set AFTER the
            # first attach, see promote_persistent_window) this makes the video window live for
            # the whole app lifetime instead of being destroyed and rebuilt every song.
            idle="yes",
            # force-window creates the window at mpv's default size, visible, in the middle of
            # the screen — a big black rectangle flashing at launch until we can park it. Being
            # born 16x16 makes that flash a speck instead; we set the real frame on attach.
            geometry="16x16",
            # Clear force-window to black instead of mpv's grey tile pattern
            # while a new file replaces the stopped one.
            background="color", background_color="#FF000000",
            border_background="color",
            # macvk normally stops rendering an ordered-out window. During a
            # track replacement we intentionally keep this surface hidden
            # until the new stream is configured; it must still decode/render
            # offscreen or a readiness gate can never complete.
            force_render="yes",
            cache="yes", demuxer_readahead_secs=10, demuxer_max_bytes="256MiB",
        )
        if fast_profile:
            opts["profile"] = "fast"
        self.player = _new_mpv(tag, **opts)
        self.player.register_event_callback(self._on_mpv_event)
        # python-mpv property writes and load/stop commands are synchronous.
        # Never run them on Qt's GUI thread, and never let two threads operate
        # on the same libmpv handle concurrently during a restart.
        self._operation_queue = queue.Queue()
        self._operation_thread = threading.Thread(
            target=self._operation_loop,
            name=f"singws-mpv-{tag}-operations",
            daemon=True,
        )
        self._operation_thread.start()

    def _operation_loop(self):
        while True:
            item = self._operation_queue.get()
            if item is None:
                return
            label, func, args = item
            try:
                func(*args)
            except Exception as exc:
                self.log(f"[MPV] follower {self.tag} {label} failed: {exc}")

    def enqueue_operation(self, label, func, *args):
        if self._shutdown:
            return
        self._operation_queue.put((str(label), func, args))

    def begin_visual_load(self, generation):
        """Arm readiness for one replacement file before issuing loadfile."""
        with self._visual_state_lock:
            self._pending_visual_generation = int(generation)
            self._active_visual_generation = 0
            self._ready_visual_generation = 0

    def is_visual_ready(self, generation) -> bool:
        with self._visual_state_lock:
            return self._ready_visual_generation == int(generation)

    def _on_mpv_event(self, event):
        """Latch the new generation only from mpv's own file/VO events.

        A video-reconfig without usable output parameters can be the old
        stream tearing down (or the audio-only first stage of a CDG load), so
        only a configured video output marks the hidden surface revealable.
        """
        if self._shutdown:
            return
        event_id = event.event_id.value
        if event_id == libmpv.MpvEventID.START_FILE:
            with self._visual_state_lock:
                self._active_visual_generation = (
                    self._pending_visual_generation
                )
                self._ready_visual_generation = 0
            return
        if event_id != libmpv.MpvEventID.VIDEO_RECONFIG:
            return
        with self._visual_state_lock:
            generation = self._active_visual_generation
        if generation <= 0:
            return
        # python-mpv invokes this callback on its own event thread. Reading a
        # property here used to race the serialized operation worker during
        # active-track shutdown: terminate() cleared/destroyed the libmpv
        # handle while this callback was inside mpv_get_property(). Put the
        # readiness probe on the same worker as load/seek/terminate instead.
        self.enqueue_operation(
            "visual-ready-probe", self._probe_visual_ready, generation
        )

    def _probe_visual_ready(self, generation):
        if self._shutdown:
            return
        try:
            params = self.player._get_property("video-out-params")
            vid = self.player._get_property("vid")
            configured = bool(params) and vid not in (None, False, "no")
        except Exception:
            configured = False
        if not configured:
            return
        with self._visual_state_lock:
            if generation != self._active_visual_generation:
                return
            if self._ready_visual_generation == generation:
                return
            self._ready_visual_generation = generation
        self.log(
            f"[MPV-VIDEO] {self.tag} new visual configured "
            f"generation={generation}"
        )

    # -- window plumbing -------------------------------------------------------------------
    def find_window(self):
        """Identify mpv's NSWindow POSITIVELY by its unique title, and ONLY by that.

        There is deliberately no "any new window" fallback: mpv does not create its video window
        until the first media load, which can be many seconds after attach() runs, and a loose
        fallback grabs an unrelated Qt helper window instead — childing the wrong window while
        the real mpv windows float free. Returning None here just means "not born yet, keep
        polling", which is the correct behaviour."""
        for w in ns_windows():
            if w not in self._before and ns_window_title(w) == self.marker:
                return w
        return None

    def window_alive(self) -> bool:
        """mpv destroys its video window on stop and builds a new one on the next load, so a
        stored handle goes stale between songs. Messaging a freed NSWindow crashes the process."""
        return self.win is not None and self.win in ns_windows()

    def try_attach(self) -> bool:
        if self.win is not None:
            if self.window_alive():
                return True
            self.win = None          # stale handle: mpv tore it down, wait for the next one
            self._last_rect = None
        win = self.find_window()
        if win is None:
            return False
        parent = ns_window_of_view(self.host.winId())
        if not parent:
            return False
        # Restyle before childing. Doing this after addChildWindow changes the
        # child's ordering relationship and was the source of the old white-
        # border/fullscreen regression.
        make_borderless(win)
        if not attach_child(parent, win):
            return False
        self.win = win
        set_ignores_mouse(win, True)
        if not self.reposition(force=True):
            # Host not laid out yet, so we have nowhere to put it — park it out of sight rather
            # than leave mpv's default-sized window visible until the next tick.
            order_out(win)
            self._hidden = True
        self.log(f"[MPV-VIDEO] {self.tag} surface attached")
        self.promote_persistent_window()
        return True

    def promote_persistent_window(self):
        """Pin the window open for the app's lifetime.

        force-window CANNOT be set at construction — the window needs the main run loop before
        Qt's is up and it deadlocks. Setting it here is safe: Qt is running and the window already
        exists. Without it mpv destroys the VO window on every stop and builds a fresh one on the
        next load, which is what made a new song open fullscreen on the wrong screen, orphaned the
        preview, and forced a re-attach/re-stack/re-bind of the Metal layer every single song."""
        if self._persistent:
            return
        self._persistent = True

        def _set():
            try:
                self.player["force-window"] = "yes"
                self.log(f"[MPV-VIDEO] {self.tag} window pinned persistent")
            except Exception as exc:
                self._persistent = False
                self.log(f"[MPV-VIDEO] {self.tag} force-window failed: {exc}")

        self.enqueue_operation("force-window", _set)

    def host_rect(self):
        """Qt globals are top-left origin off the primary screen; AppKit frames are bottom-left.
        Valid across displays — other screens are offsets in the same global space."""
        try:
            # An unmapped/unlaid-out widget reports placeholder geometry, and mapToGlobal on it
            # returns nonsense — positioning from that is what made the surface oversized at
            # launch. Refuse to answer until the host is really on screen.
            if not self.host.isVisible():
                return None

            # AppKit is authoritative — see view_screen_rect. The Qt path below is only a
            # fallback for the case where the objc conversion fails outright.
            rect = view_screen_rect(self.host.winId())
            if rect is not None:
                x, y, w, h = rect

                # A child NSWindow is composited above every QWidget in its
                # parent window. After the borderless fullscreen transition,
                # AppKit can temporarily report the host NSView with the
                # top-level content height even though Qt has already laid it
                # out shorter to reserve the ticker row. If we accept that
                # oversized rectangle, MPV covers the ticker at the bottom.
                #
                # Qt's host height is the layout constraint we actually want.
                # Clamp only an AppKit *excess* (never enlarge a surface). With
                # a bottom ticker preserve the video's top edge, removing the
                # excess from the bottom; with a top ticker preserve its bottom
                # edge. Preview has no ticker ancestor and is unchanged.
                qt_h = float(self.host.height())
                ticker_position = None
                ancestor = self.host
                while ancestor is not None:
                    if hasattr(ancestor, "_ticker_position"):
                        ticker_position = str(
                            getattr(ancestor, "_ticker_position", "bottom")
                        ).lower()
                        break
                    try:
                        ancestor = ancestor.parent()
                    except Exception:
                        ancestor = None
                if (
                    ticker_position in ("top", "bottom")
                    and qt_h > 1.0
                    and h > qt_h + 0.5
                ):
                    excess = h - qt_h
                    if ticker_position == "bottom":
                        y += excess
                    h = qt_h
                    diag = (
                        round(excess, 1),
                        round(qt_h, 1),
                        ticker_position,
                    )
                    if diag != self._last_layout_clamp_diag:
                        self._last_layout_clamp_diag = diag
                        self.log(
                            f"[MPV-GEOM] {self.tag} clamped {excess:.0f}px "
                            f"AppKit overflow for {ticker_position} ticker "
                            f"(host height={qt_h:.0f})"
                        )
                return (x, y, w, h)

            tl = self.host.mapToGlobal(QPoint(0, 0))
            w, h = self.host.width(), self.host.height()
            screen = QApplication.primaryScreen()
            if screen is None or w <= 1 or h <= 1:
                return None
            return (float(tl.x()), float(screen.geometry().height() - (tl.y() + h)),
                    float(w), float(h))
        except Exception:
            return None

    def reposition(self, force: bool = False) -> bool:
        """Returns True if the frame actually moved — that is also how 'still dragging' is
        detected. Re-setting an unchanged frame every tick fights macOS's own child tracking."""
        if not self.window_alive():
            return False
        rect = self.host_rect()
        if rect is None:
            return False
        if not force and self._last_rect and all(
            abs(a - b) < 0.5 for a, b in zip(self._last_rect, rect)
        ):
            return False
        self._last_rect = rect
        set_window_rect(self.win, *rect)
        if os.environ.get("SINGWS_MPV_GEOM"):
            # Qt has been seen reporting fullscreen=True while still returning the WINDOWED
            # size, so log what we were told and what we applied, to tell a stale-geometry
            # problem apart from a Spaces/ordering one.
            try:
                win = self.host.window()
                self.log(
                    f"[MPV-GEOM] {self.tag} applied x={rect[0]:.0f} y={rect[1]:.0f} "
                    f"w={rect[2]:.0f} h={rect[3]:.0f} | host={self.host.width()}x{self.host.height()} "
                    f"topwin={win.width()}x{win.height()} fullscreen={win.isFullScreen()}"
                )
            except Exception:
                pass
        return True

    def target_screen_index(self):
        """Resolve the display containing the Qt host while on the GUI thread."""
        try:
            parent = ns_window_of_view(self.host.winId())
            return ns_screen_index_of_window(parent) if parent else None
        except Exception:
            return None

    def rebuild_surface(self, notify_screen_change: bool = True):
        """Crossing to another display leaves mpv rendering into a surface nobody can see: its
        mac backend never learns the window changed screens (no 'Metal layer changed' ever fires),
        so the CAMetalLayer stays bound to the original display. Reattach at the final geometry,
        then explicitly deliver mpv's own windowDidChangeScreen callback. Costs a flicker, so only
        ever call this once geometry has settled, never mid-drag."""
        if self.win is None:
            return
        # A stopped output surface is intentionally ordered out so the Qt idle
        # background/visualizer can paint. Reattaching it here would expose
        # mpv's persistent force-window black frame, while _hidden would still
        # say True and prevent hide() from ordering it out again. Remember the
        # rebuild and consume it after show() has reattached/repositioned the
        # surface on the new display.
        if self._hidden:
            self._last_rect = None
            self._pending_rebuild = True
            self.log(
                f"[MPV-VIDEO] {self.tag} surface rebuild deferred "
                f"(hidden; pending reveal)"
            )
            return
        parent = ns_window_of_view(self.host.winId())
        if not parent:
            return
        detach_child(self.win)
        order_out(self.win)
        attach_child(parent, self.win)
        set_ignores_mouse(self.win, True)
        self._last_rect = None
        self.reposition(force=True)
        self._pending_rebuild = False
        if notify_screen_change:
            screen_changed, resized = notify_window_display_changed(self.win)
            if screen_changed and resized:
                self.log(
                    f"[MPV-VIDEO] {self.tag} display callbacks delivered "
                    f"(screen-change + resize/expose)"
                )
            else:
                self.log(
                    f"[MPV-VIDEO] {self.tag} display callbacks incomplete "
                    f"(screen-change={screen_changed} resize={resized})"
                )
        self.log(f"[MPV-VIDEO] {self.tag} surface rebuilt (display change)")

    def hide(self):
        if self.window_alive() and not self._hidden:
            # Explicitly break the child relationship. orderOut: hides the
            # window but can leave parentWindow intact, making a later attach
            # to the same Qt window a no-op after a cross-display move.
            detach_child(self.win)
            order_out(self.win)
            self._hidden = True
            self._last_rect = None

    def show(self):
        """Reveal on the host display before restoring the child relationship.

        A hidden mpv window retains its previous NSScreen. Attaching it first
        and moving it second can leave the Metal layer bound to that old
        display. Move the detached NSWindow to the final AppKit rectangle and
        deliver mpv's display callbacks first, then attach it to Qt.
        """
        if self.window_alive() and self._hidden:
            parent = ns_window_of_view(self.host.winId())
            if parent:
                detach_child(self.win)
                rect = self.host_rect()
                if rect is not None:
                    set_window_rect(self.win, *rect)
                    self._last_rect = rect
                screen_changed, resized = notify_window_display_changed(self.win)
                attach_child(parent, self.win)
                set_ignores_mouse(self.win, True)
                self._hidden = False
                self.reposition(force=True)
                self._pending_rebuild = False
                screen_index = ns_screen_index_of_window(parent)
                self.log(
                    f"[MPV-VIDEO] {self.tag} revealed on screen={screen_index} "
                    f"(screen-change={screen_changed} resize={resized})"
                )
        return False

    # -- playback --------------------------------------------------------------------------
    def time(self):
        for prop in ("audio-pts", "time-pos"):
            try:
                v = self.player._get_property(prop)
                if isinstance(v, float):
                    return v
            except Exception:
                pass
        return None

    def stop(self):
        self.enqueue_operation("stop", self.player.command, "stop")

    def shutdown(self):
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self.player.unregister_event_callback(self._on_mpv_event)
        except (ValueError, AttributeError):
            pass
        # python-mpv's _mpv_terminate_destroy can wait indefinitely for the
        # macOS Metal VO. Never invoke it from Qt's GUI/closeEvent thread.
        # Queue it behind any in-flight operation so the libmpv handle still
        # has exactly one caller, then let the daemon worker finish (or be
        # reclaimed with the host process) without blocking application quit.
        self._operation_queue.put(
            ("terminate", self.player.terminate, ())
        )
        self._operation_queue.put(None)


# --------------------------------------------------------------------------- the plugin
class MpvPlaybackPlugin:
    """Satisfies SingWS's 17-method contract, so MpvPlaybackAdapter and every existing
    call site keeps working. Construct, then attach(preview_widget, output_widget)."""

    SKEW_CAP = 0.06        # >6% off real speed becomes audible/visible
    DEAD_ZONE = 0.050      # 50ms: close enough, stop correcting
    SEEK_THRESHOLD = 0.40  # non-CDG only: close a gap this big with one seek, not 20s of skew

    def __init__(self, log=print, preview_fast_profile=False):
        self.log = log
        self._error = ""
        self._engine = None
        self._out = None
        self._prev = None
        self._is_cdg = False
        self._audio_only = False
        self._cdg_output_sidefill = False
        self._tempo = 1.0
        self._volume = 1.0
        self._loaded = False
        self._media_generation = 0
        self._follower_state_lock = threading.Lock()
        self._followers_loading = set()
        self._stop_evt = threading.Event()
        self._pending_rebuild = set()
        # macOS native fullscreen moves the Qt window through a separate Space.
        # While that animation is active, AppKit already moves attached child
        # windows with their parent. Repositioning or orderOut/re-attaching the
        # mpv windows at the same time makes them jump back to stale windowed
        # geometry. VideoWindow brackets that animation through
        # beginWindowTransition(); only the newest transition may finish.
        self._window_transition_generation = 0
        self._window_transition_active = False
        self._transition_output_hidden = False
        self._shutdown = False
        self._preview_fast = preview_fast_profile
        self._attach_timer = None
        self._tick_timer = None
        self._settle_timer = None
        self._sync_thread = None
        # Start every visual track only after both followers are configured.
        # This prevents audio from running ahead while CDG/MP4 video loads,
        # whether the pipeline is cold or already warm.
        self._start_gate_generation = 0
        self._start_gate_started_at = 0.0
        self._screen_change_handlers = []
        self._engine = MpvIpcClient("karaoke", log=log)

    # -- contract: setup -------------------------------------------------------------------
    def attach(self, preview_widget, output_widget) -> bool:
        """Create both video instances and start childing their Metal windows over the app's
        existing host widgets. mpv's window does not exist until the first load, so the actual
        childing is done by a poll (force_window at construction DEADLOCKS the run loop)."""
        try:
            self._out = MpvVideoFollower("out", output_widget, keepaspect=True, log=self.log)
            self._prev = MpvVideoFollower("prev", preview_widget, keepaspect=True,
                                          fast_profile=self._preview_fast, log=self.log,
                                          always_visible=True)
        except Exception as exc:
            self._error = f"mpv video init failed: {exc}"
            return False

        self._attach_timer = QTimer()
        self._attach_timer.timeout.connect(self._poll_attach)
        self._attach_timer.start(50)   # tight: this interval is how long a new window is exposed

        self._tick_timer = QTimer()
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(120)

        self._settle_timer = QTimer()
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._settle)

        for follower in (self._out, self._prev):
            try:
                handle = follower.host.window().windowHandle()
                if handle is not None:
                    # PyQt's QWindow.screenChanged wrapper is not consistent
                    # across the macOS display transition about whether it
                    # supplies the QScreen argument. A lambda with positional
                    # defaults can consequently be invoked with the wrong
                    # arity and escape through sys.excepthook, whose modal
                    # crash dialog makes the GUI appear permanently hung.
                    #
                    # Use a closure whose public signature accepts either
                    # signal form, and retain it for the connection lifetime.
                    on_screen_changed = self._make_screen_changed_handler(
                        follower
                    )
                    self._screen_change_handlers.append(on_screen_changed)
                    handle.screenChanged.connect(on_screen_changed)
            except Exception:
                pass

        # Create the video windows NOW rather than waiting for the first load. Otherwise mpv
        # builds them mid-play at its own default geometry and they visibly jump into place.
        # Deferred by 400ms rather than done inline because force-window needs Qt's run loop to
        # already be spinning — setting it at construction deadlocks.
        QTimer.singleShot(400, self._create_windows_early)

        self._sync_thread = threading.Thread(
            target=self._sync_loop,
            name="singws-mpv-av-sync",
            daemon=True,
        )
        self._sync_thread.start()
        self.log(f"[MPV] {VERSION} attached")
        return True

    def _create_windows_early(self):
        for follower in (self._out, self._prev):
            if follower is not None:
                follower.promote_persistent_window()

    def _poll_attach(self):
        """Keeps running until BOTH windows are childed. mpv has no video window until the first
        load, so at startup this legitimately finds nothing for as long as it takes the user to
        press play — that is expected, not a failure."""
        done = True
        for follower in (self._out, self._prev):
            if follower is not None and not follower.try_attach():
                done = False
        if done:
            self._attach_timer.stop()

    def _rearm_attach(self):
        """mpv tears its window down on stop and builds a new one on the next load, so the poll
        has to come back to life every time a window dies."""
        if self._attach_timer is not None and not self._attach_timer.isActive():
            self._attach_timer.start(50)

    def _make_screen_changed_handler(self, follower):
        def on_screen_changed(*_args):
            self._queue_rebuild(follower)

        return on_screen_changed

    def _queue_rebuild(self, follower):
        # Only QUEUE it. On a fast drag the window is still in motion and may cross screens
        # again; rebuilding mid-flight lands on a stale rect and leaves the surface grey.
        self._pending_rebuild.add(follower)
        if self._window_transition_active:
            return
        self._settle_timer.start(300)

    def _settle(self):
        if self._window_transition_active:
            return
        pending, self._pending_rebuild = self._pending_rebuild, set()
        for follower in pending:
            try:
                follower.rebuild_surface()
            except Exception as exc:
                self.log(f"[MPV] rebuild failed: {exc}")

    def beginWindowTransition(self, duration_ms=1100):
        """Suspend geometry writes during a macOS native-window transition.

        A visible child Metal window resizes in several coarse steps while Qt
        recreates its native parent, which makes live karaoke look choppy even
        though the Qt/ticker transition is smooth. Hide only the output child
        as a short black curtain; force-render keeps it decoding offscreen.
        Once the animation settles, reveal it at the final AppKit rectangle.
        A generation token makes rapid or duplicated requests harmless.
        """
        self._window_transition_generation += 1
        generation = self._window_transition_generation
        self._window_transition_active = True
        self._transition_output_hidden = False
        try:
            self._settle_timer.stop()
        except Exception:
            pass
        try:
            if (
                self._loaded
                and not self._audio_only
                and self._out is not None
                and self._out.window_alive()
                and not self._out._hidden
            ):
                self._out.hide()
                self._transition_output_hidden = bool(self._out._hidden)
                if self._transition_output_hidden:
                    self.log(
                        f"[MPV-VIDEO] output curtain hidden "
                        f"generation={generation}"
                    )
        except Exception as exc:
            self.log(f"[MPV-VIDEO] output curtain hide failed: {exc}")
        self.log(f"[MPV-VIDEO] window transition begin generation={generation}")
        QTimer.singleShot(
            max(250, int(duration_ms)),
            lambda g=generation: self._finish_window_transition(g),
        )

    def _finish_window_transition(self, generation):
        if generation != self._window_transition_generation:
            return
        self._window_transition_active = False

        # screenChanged is also emitted while entering/leaving a fullscreen
        # Space. Discard its queued output rebuild and perform exactly one now,
        # after the host view has its final AppKit geometry.
        pending = set(self._pending_rebuild)
        self._pending_rebuild.clear()
        if self._out is not None:
            pending.add(self._out)
        for follower in pending:
            try:
                if (
                    follower is self._out
                    and self._transition_output_hidden
                ):
                    # show() moves the detached window to the final rectangle,
                    # refreshes its display binding, then attaches it. The
                    # current rendered frame appears in one clean step.
                    follower.show()
                else:
                    # AppKit delivers its own screen/fullscreen callbacks
                    # during a native-window transition. Only the manual
                    # windowed migration path needs the missing callback.
                    follower.rebuild_surface(notify_screen_change=False)
            except Exception as exc:
                self.log(f"[MPV] transition rebuild failed: {exc}")
        if self._transition_output_hidden:
            if self._out is not None and not self._out._hidden:
                self.log(
                    f"[MPV-VIDEO] output curtain revealed "
                    f"generation={generation}"
                )
            else:
                self.log(
                    f"[MPV-VIDEO] output curtain reveal deferred "
                    f"generation={generation}"
                )
        self._transition_output_hidden = False
        self.log(f"[MPV-VIDEO] window transition settled generation={generation}")

    def _tick(self):
        """Keep each Metal window pinned over its host, and mirror the host's visibility so the
        app's existing _hide_karaoke_widgets()/idle logic works without modification."""
        if self._window_transition_active:
            return
        moved = False
        for follower in (self._out, self._prev):
            if follower is None:
                continue
            try:
                if follower.win is not None and not follower.window_alive():
                    follower.win = None      # mpv rebuilt it; go find the new one
                    self._rearm_attach()
                    continue
                if follower.win is None:
                    continue
                # always_visible means "do not hide just because playback stopped" — it must
                # still track the host widget's real visibility, or at startup we position over
                # a window that has not been laid out yet and the surface lands oversized.
                visible = follower.host.isVisible() and (
                    follower.always_visible
                    or (self._loaded and not self._audio_only)
                )
                if not visible:
                    follower.hide()
                    continue
                rebuild_after_reveal = follower.show()  # no-op unless it was hidden
                moved |= follower.reposition()
                if rebuild_after_reveal:
                    # rebuild_surface() deliberately declined while the output
                    # was hidden. Queue it now that AppKit has attached the
                    # window to the new display; the settle timer keeps the
                    # Metal rebind out of the reveal/layout transition itself.
                    self._pending_rebuild.add(follower)
                    self._settle_timer.start(300)
            except Exception:
                pass
        if moved and self._pending_rebuild:
            self._settle_timer.start(300)   # still dragging: push the deadline out

    # -- contract: load --------------------------------------------------------------------
    def loadSingWSMedia(self, source, audio_path=None, autoplay=True,
                        semitones=0, tempo_percent=100) -> bool:
        gate_visual_start = False
        try:
            self._media_generation += 1
            generation = self._media_generation
            source = str(source or "")
            audio_path = str(audio_path or "") or None
            low = source.lower()
            self._is_cdg = low.endswith(".cdg")
            self._audio_only = not (self._is_cdg or low.endswith(
                (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".mpg", ".mpeg")))
            gate_visual_start = bool(autoplay and not self._audio_only)
            if gate_visual_start:
                self._start_gate_generation = generation
                self._start_gate_started_at = time.monotonic()
                self.log(
                    f"[MPV-START-GATE] holding generation={generation} "
                    "until both video surfaces are configured"
                )
            # For CDG the mp3 is the audio; for a video file the file itself carries it.
            engine_file = audio_path if (self._is_cdg and audio_path) else source

            self._tempo = max(0.25, min(4.0, float(tempo_percent or 100) / 100.0))
            self._apply_key(semitones)

            self._engine.set_property("speed", self._tempo)
            if gate_visual_start:
                # Pause before loadfile so even the first decoded audio packet
                # cannot escape while the video followers initialize.
                self._engine.set_property("pause", True)
            self._engine.loadfile(engine_file)

            if not self._audio_only:
                followers = [
                    f for f in (self._out, self._prev) if f is not None
                ]
                with self._follower_state_lock:
                    self._followers_loading = {f.tag for f in followers}
                for follower in followers:
                    if follower is None:
                        continue
                    target_screen = follower.target_screen_index()
                    follower.enqueue_operation(
                        "load",
                        self._load_follower,
                        follower,
                        source,
                        audio_path if self._is_cdg else None,
                        generation,
                        self._tempo,
                        target_screen,
                        gate_visual_start,
                    )
            else:
                with self._follower_state_lock:
                    self._followers_loading.clear()

            if not autoplay and not gate_visual_start:
                self._engine.set_property("pause", True)
            if gate_visual_start:
                QTimer.singleShot(
                    1400,
                    lambda g=generation:
                        self._release_start_gate(g, "safety timeout"),
                )
            self._loaded = True
            self._error = ""
            self.log(f"[MPV] load mode={'cdg' if self._is_cdg else ('audio' if self._audio_only else 'video')} "
                     f"key={semitones} tempo={tempo_percent}%")
            return True
        except Exception as exc:
            if gate_visual_start:
                self._start_gate_generation = 0
            self._error = f"mpv load failed: {exc}"
            self.log(f"[MPV] {self._error}")
            return False

    def _load_follower(
        self, follower, source, cdg_audio, generation, tempo, target_screen,
        start_paused=False,
    ):
        """Run serialized on the follower's worker.

        Synchronous libmpv operations can wait for a VO reconfiguration. They
        must never run on Qt's GUI thread. Generation checks abandon an old CDG
        load promptly when Restart/Play Next replaces it.
        """
        player = follower.player
        try:
            if generation != self._media_generation:
                return
            follower.begin_visual_load(generation)
            if target_screen is not None:
                player["screen"] = int(target_screen)
                self.log(
                    f"[MPV-VIDEO] {follower.tag} target screen={target_screen}"
                )
            # Always establish the filter before loadfile so a new MP4 cannot
            # inherit CDG side-fill, and a new CDG never exposes one 4:3 frame
            # before the filter graph is active.
            sidefill = bool(
                cdg_audio
                and follower is self._out
                and self._cdg_output_sidefill
            )
            self._set_follower_cdg_sidefill(follower, sidefill)
            player.speed = tempo
            if start_paused:
                player.pause = True
            if generation != self._media_generation:
                return
            if cdg_audio:
                # Load the MP3 as the MAIN file so there is a real clock — with a bare .cdg,
                # time-pos freezes on static screens because the cdgraphics decoder only emits
                # frames when the graphics CHANGE, and the app's watchdog then kills the song.
                player.loadfile(cdg_audio)
                # loadfile RETURNS BEFORE THE FILE IS OPEN. Setting demuxer-lavf-format while the
                # mp3 open is in flight poisons it (mp3 demuxed as CDG -> no audio track at all).
                # A playlist-entry-id poll is NOT enough — the id goes valid when loading starts.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if generation != self._media_generation:
                        return
                    try:
                        if player._get_property("audio-codec-name"):
                            break
                    except Exception:
                        pass
                    time.sleep(0.02)
                if generation != self._media_generation:
                    return
                try:
                    player["demuxer-lavf-format"] = "cdg"
                    player.command("video-add", source)
                finally:
                    try:
                        player["demuxer-lavf-format"] = ""
                    except Exception:
                        pass
            else:
                if generation != self._media_generation:
                    return
                player.loadfile(source)
        except Exception as exc:
            self.log(f"[MPV] follower {follower.tag} load failed: {exc}")
        finally:
            if generation == self._media_generation:
                with self._follower_state_lock:
                    self._followers_loading.discard(follower.tag)

    def _apply_key(self, semitones):
        try:
            n = int(semitones or 0)
        except Exception:
            n = 0
        # Rubberband is mpv's OWN built-in filter (ffmpeg here has no librubberband). Engine only:
        # the followers are muted, so pitching silence would just burn CPU.
        if n == 0:
            self._engine.set_property("af", "")
        else:
            self._engine.set_property("af", f"rubberband=pitch-scale={2 ** (n / 12.0):.6f}")

    def setPitchSemitones(self, semitones) -> None:
        """Apply a live key change without changing playback tempo."""
        self._apply_key(semitones)

    def setTempoRatio(self, ratio) -> None:
        """Apply live tempo to the audio master and both muted followers."""
        self._tempo = max(0.25, min(4.0, float(ratio or 1.0)))
        self._engine.set_property("speed", self._tempo)
        for follower in self._followers():
            follower.enqueue_operation(
                "tempo", setattr, follower.player, "speed", self._tempo
            )

    # -- contract: transport ---------------------------------------------------------------
    def playMedia(self):
        self._engine.set_property("pause", False)
        for f in self._followers():
            f.enqueue_operation("play", setattr, f.player, "pause", False)

    def pauseMedia(self):
        self._engine.set_property("pause", True)
        for f in self._followers():
            f.enqueue_operation("pause", setattr, f.player, "pause", True)

    def stopMedia(self):
        """Stop playback but KEEP the video windows. They are pinned open with force-window, so
        the handles stay valid across songs — no re-attach, no re-stack, no Metal re-bind, and a
        new song cannot open itself fullscreen on the wrong screen."""
        self._loaded = False
        self._media_generation += 1
        self._start_gate_generation = 0
        with self._follower_state_lock:
            self._followers_loading.clear()
        self._engine.stop()
        for f in self._followers():
            if not f.always_visible:
                f.hide()  # just order it out; the window itself survives
            f.stop()
        self._rearm_attach()   # harmless no-op while the windows are alive

    def seekMedia(self, ms):
        secs = max(0.0, float(ms) / 1000.0)
        self._engine.command("seek", secs, "absolute+exact")
        for f in self._followers():
            f.enqueue_operation(
                "seek", f.player.command, "seek", secs, "absolute+exact"
            )

    def positionMs(self) -> int:
        t = self._engine.get("audio-pts")
        if not isinstance(t, float):
            t = self._engine.get("time-pos")
        return int(t * 1000.0) if isinstance(t, float) else 0

    def durationMs(self) -> int:
        d = self._engine.get("duration")
        return int(d * 1000.0) if isinstance(d, float) else 0

    def isPlaying(self) -> bool:
        return bool(self._loaded and not self._engine.get("pause", False)
                    and not self._engine.ended.is_set())

    def visualsReady(self) -> bool:
        """Whether both hidden video followers configured the new generation."""
        if self._audio_only:
            return True
        generation = self._media_generation
        followers = self._followers()
        ready = bool(followers) and all(
            follower.is_visual_ready(generation) for follower in followers
        )
        if ready and self._start_gate_generation == generation:
            self._release_start_gate(generation, "visuals ready")
        return ready

    def _release_start_gate(self, generation, reason):
        """Release one coordinated visual start; stale timeouts are inert."""
        if (
            self._shutdown
            or int(generation) != self._start_gate_generation
        ):
            return
        self._start_gate_generation = 0
        # Queue video first. It can safely advance while still hidden; sending
        # the audio IPC command afterward avoids recreating the original
        # audio-head-start that made the opening CDG catch-up look choppy.
        for follower in self._followers():
            follower.enqueue_operation(
                "cold-start-release", setattr, follower.player, "pause", False
            )
        self._engine.set_property("pause", False)
        elapsed_ms = int(
            max(0.0, time.monotonic() - self._start_gate_started_at) * 1000
        )
        self.log(
            f"[MPV-START-GATE] released generation={generation} "
            f"reason={reason} elapsed={elapsed_ms}ms"
        )

    def atEnd(self) -> bool:
        return bool(self._engine.ended.is_set())

    # -- contract: settings ----------------------------------------------------------------
    def setVolume(self, value) -> None:
        self._volume = max(0.0, min(2.0, float(value)))
        self._engine.set_property("volume", self._volume * 100.0)

    def setAudioDevice(self, name) -> None:
        self._engine.set_property("audio-device", str(name or "auto"))

    def setVideoStretch(self, stretch) -> None:
        if self._out is not None:
            self._out.enqueue_operation(
                "aspect",
                self._out.player.__setitem__,
                "keepaspect",
                "no" if stretch else "yes",
            )

    def _set_follower_cdg_sidefill(self, follower, enabled) -> None:
        follower.player["vf"] = (
            CDG_OUTPUT_SIDEFILL_FILTER if enabled else ""
        )
        if follower is self._out:
            self.log(
                f"[MPV-VIDEO] CDG output side-fill "
                f"{'enabled' if enabled else 'disabled'}"
            )

    def setCdgOutputSidefill(self, enabled) -> None:
        enabled = bool(enabled)
        changed = enabled != self._cdg_output_sidefill
        self._cdg_output_sidefill = enabled
        # The normal load path applies this before the next file opens. Also
        # support changing the existing Settings checkbox during a CDG song;
        # the follower worker serializes the property write with load/seek.
        if (
            changed
            and self._loaded
            and self._is_cdg
            and self._out is not None
        ):
            self._out.enqueue_operation(
                "cdg-sidefill",
                self._set_follower_cdg_sidefill,
                self._out,
                enabled,
            )

    def errorString(self) -> str:
        return self._error

    def version(self) -> str:
        return VERSION

    def audioDescription(self) -> str:
        return f"out-of-process mpv engine ({find_mpv_binary() or 'mpv'})"

    # -- sync ------------------------------------------------------------------------------
    def _followers(self):
        return [f for f in (self._out, self._prev) if f is not None]

    def _sync_loop(self):
        """Proportional speed skew against the audio master. Deliberately NOT a coordinated
        paused start — the play button has to feel instant, so both followers run free from frame
        zero and converge within a few seconds."""
        while not self._stop_evt.is_set():
            try:
                with self._follower_state_lock:
                    followers_loading = bool(self._followers_loading)
                if self._loaded and not self._audio_only and not followers_loading:
                    master = self._engine.get("audio-pts")
                    if not isinstance(master, float):
                        master = self._engine.get("time-pos")
                    if isinstance(master, float):
                        for f in self._followers():
                            t = f.time()
                            if t is None:
                                continue
                            delta = master - t
                            # A cold start on slower hardware can leave video >1s behind, and at
                            # a 6% cap that takes ~20s to close. One seek fixes it — but NEVER on
                            # CDG, where a mid-stream seek corrupts tiles until the next redraw.
                            if not self._is_cdg and abs(delta) > self.SEEK_THRESHOLD:
                                try:
                                    f.enqueue_operation(
                                        "sync-seek",
                                        f.player.command,
                                        "seek",
                                        master,
                                        "absolute+exact",
                                    )
                                    f.enqueue_operation(
                                        "sync-speed",
                                        setattr,
                                        f.player,
                                        "speed",
                                        self._tempo,
                                    )
                                    continue
                                except Exception:
                                    pass
                            if abs(delta) < self.DEAD_ZONE:
                                speed = self._tempo
                            else:
                                skew = max(-self.SKEW_CAP, min(self.SKEW_CAP, delta * 0.5))
                                speed = self._tempo * (1.0 + skew)
                            try:
                                f.enqueue_operation(
                                    "sync-speed", setattr, f.player, "speed", speed
                                )
                            except Exception:
                                pass
            except Exception:
                pass
            # Wake immediately during shutdown instead of leaving a property
            # reader asleep for up to a second while libmpv is destroyed.
            self._stop_evt.wait(1.0)

    def shutdown(self):
        # closeEvent and QApplication.aboutToQuit both call this deliberately:
        # either path may be the only one reached for a particular macOS quit
        # gesture. Keep the duplicate call harmless.
        if self._shutdown:
            return
        self._shutdown = True
        self._start_gate_generation = 0
        self._stop_evt.set()
        sync_thread = self._sync_thread
        if (
            sync_thread is not None
            and sync_thread.is_alive()
            and sync_thread is not threading.current_thread()
        ):
            # _sync_loop only performs cached engine reads plus short follower
            # property reads. The stop event interrupts its wait immediately;
            # joining here guarantees it cannot enter mpv_get_property while
            # the follower handles below are being terminated.
            sync_thread.join(timeout=1.0)
            if sync_thread.is_alive():
                self.log("[MPV] A/V sync worker did not stop before shutdown")
        for t in (self._attach_timer, self._tick_timer, self._settle_timer):
            try:
                if t is not None:
                    t.stop()
            except Exception:
                pass
        followers = self._followers()
        for f in followers:
            f.shutdown()
        if self._engine is not None:
            self._engine.terminate()

        # macvk destroys its Metal VO on the follower worker, but part of that
        # teardown dispatches synchronously to AppKit's main queue. Returning
        # straight from closeEvent and stopping QApplication leaves both
        # workers stranded in vo_destroy; Python then finalizes live native
        # threads and macOS reports "Python quit unexpectedly."
        #
        # Keep a small nested Qt/AppKit event loop alive until both serialized
        # follower workers finish. This pumps the required main-queue work
        # without blocking the UI thread as a join() would.
        pending = [
            f for f in followers
            if f._operation_thread.is_alive()
        ]
        if pending:
            loop = QEventLoop()
            poll = QTimer()
            poll.setInterval(20)
            deadline = time.monotonic() + 4.0

            def _check_shutdown_workers():
                if (
                    not any(f._operation_thread.is_alive() for f in pending)
                    or time.monotonic() >= deadline
                ):
                    loop.quit()

            poll.timeout.connect(_check_shutdown_workers)
            poll.start()
            _check_shutdown_workers()
            if any(f._operation_thread.is_alive() for f in pending):
                loop.exec()
            poll.stop()
            still_running = [
                f.tag for f in pending if f._operation_thread.is_alive()
            ]
            if still_running:
                self.log(
                    f"[MPV] shutdown timeout waiting for followers: "
                    f"{', '.join(still_running)}"
                )
