"""Python client of the singws-karafun-display program: KaraFun through its web player (karafun.com/web), run by Chromium (CEF) inside that program.

The program (rust/crates/singws-karafun-display) owns Chromium and the page (the page script karafun_bridge.js) and draws the picture straight on the
graphics chip in a native window of its own (no JPEG, no copy); this module only starts it, turns its JSON lines into callbacks, and sends it JSON-line
commands, including `window` (where the picture goes) and `output` (which sound device the player uses). Native code owns the player; Python owns the show.

Callbacks (they run on this module's reader thread): `on_message(dict)` for what the page says (`ready`, `status` twice a second with signedIn,
plan, playing, state, position, duration, song, queue; `log`; `error`; `fullscreen`; `raise`; and `helper` lines from the program itself),
`on_log(str)` for this module's own lines.

Chromium's framework is fetched once (about 125 MB, `singws-karafun-display get`) after the user says yes; Apple's web view cannot run KaraFun's
player (its song database needs a cross-origin isolated page, which KaraFun asks for with a header WebKit does not support). KaraFun has not
approved driving its site and can change it at any time. tools/karafun_web_probe.py runs it by hand.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# ---- the player's program: singws-karafun-display runs KaraFun's web player in Chromium (CEF) inside itself and draws it on the graphics chip.
# Chromium's framework (about 125 MB) is fetched once by that program (`get`), only after the user agreed; never Google Chrome.
_DISPLAY_NAME = "singws-karafun-display"


class BrowserMissing(RuntimeError):
    """Chromium's framework is not on this Mac yet; the caller asks the user before calling ensure_browser(allow_download=True)."""


def find_display_helper() -> Optional[Path]:
    """The display program: SINGWS_KARAFUN_DISPLAY, the app bundle's Frameworks, native/singws_karafun_display/<arch>, or a cargo build."""
    import platform
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    here = Path(__file__).resolve().parent
    candidates = [os.environ.get("SINGWS_KARAFUN_DISPLAY", "")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [str(Path(meipass) / _DISPLAY_NAME), str(Path(meipass) / "Frameworks" / _DISPLAY_NAME)]
    candidates += [str(here / "Frameworks" / _DISPLAY_NAME), str(here.parent / "Frameworks" / _DISPLAY_NAME),
                   str(here / "native" / "singws_karafun_display" / arch / _DISPLAY_NAME)]
    candidates += [str(Path.home() / ".cache" / d / "release" / _DISPLAY_NAME) for d in ("singws-rust-target-kfdisplay", "singws-rust-target")]
    candidates += [str(here / "rust" / "target" / kind / _DISPLAY_NAME) for kind in ("release", "debug")]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    return None


def display_installed(helper) -> bool:
    """Whether Chromium's framework is here (the program says: exit 0 yes, 3 no)."""
    try:
        return subprocess.run([str(helper), "installed"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def find_browser() -> Optional[Path]:
    """The display program, when it and Chromium's framework are both here; None otherwise."""
    helper = find_display_helper()
    return helper if helper is not None and display_installed(helper) else None


def _get_framework(helper, progress: Callable[[str], None]) -> None:
    """Run `helper get`: it downloads Chromium's framework (checked against its published checksum) and says how it is going."""
    proc = subprocess.Popen([str(helper), "get"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    failure = "the download did not finish"
    try:
        for raw in proc.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if message.get("type") == "progress":
                progress(str(message.get("text") or ""))
            elif message.get("type") == "failed":
                failure = str(message.get("error") or failure)
    finally:
        code = proc.wait()
    if code != 0:
        raise RuntimeError(failure)


def ensure_browser(progress: Callable[[str], None] = lambda text: None, *, allow_download: bool = False) -> Path:
    """The program KaraFun's player runs in. Chromium's framework already here: used as is. Without it nothing is downloaded unless `allow_download` (the
    user said yes): BrowserMissing is raised instead so the caller can ask. Raises RuntimeError with a plain reason otherwise."""
    helper = find_display_helper()
    if helper is None:
        raise RuntimeError("SingWS's KaraFun display program was not found (reinstall SingWS from the installer)")
    if display_installed(helper):
        return helper
    if not allow_download:
        raise BrowserMissing("KaraFun's player needs Chromium, which is not on this Mac yet")
    try:
        _get_framework(helper, progress)
    except RuntimeError as error:
        raise RuntimeError(f"Chromium could not be downloaded (internet needed once): {error}") from error
    if not display_installed(helper):
        raise RuntimeError("Chromium could not be set up after the download")
    return helper


class KaraFunWeb:
    def __init__(self, profile_dir=None, *, on_message: Callable[[dict], None] = lambda m: None,
                 on_log: Callable[[str], None] = lambda s: None, helper_path=None):
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.on_message, self.on_log = on_message, on_log
        self.helper_path = Path(helper_path) if helper_path else find_display_helper()
        self._proc: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self.running = False

    def start(self) -> None:
        if self._proc is not None:
            return
        if self.helper_path is None:
            raise RuntimeError("the singws-karafun-display program was not found (build it: cargo build --release -p singws-karafun-display)")
        args = [str(self.helper_path)]
        if self.profile_dir is not None:
            args += ["--profile", str(self.profile_dir)]
        self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.running = True
        self._reader = threading.Thread(target=self._read, name="karafun-web-reader", daemon=True)
        self._reader.start()
        self.on_log(f"started helper pid={self._proc.pid}")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self.running = False
        if proc is None:
            return
        try:
            self.command("quit", _proc=proc)
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass

    def _read(self) -> None:
        proc = self._proc
        try:
            for raw in proc.stdout:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                try:
                    self.on_message(message)
                except Exception as error:  # a callback's fault must not end the reader
                    self.on_log(f"callback error: {error!r}")
        finally:
            if self.running and self._proc is proc:
                self.running = False
                self.on_log("helper stopped")
                try:
                    self.on_message({"type": "error", "message": "KaraFun's web player stopped"})
                except Exception:
                    pass

    def command(self, name: str, _proc=None, **fields) -> None:
        proc = _proc or self._proc
        if proc is None or proc.stdin is None:
            return
        line = (json.dumps({"cmd": name, **fields}) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (OSError, ValueError):
                pass

    # ---- the player
    def play(self, song_id: int, query: Optional[str] = None, queries=None, artist: str = "", title: str = "") -> None:
        """Search KaraFun for `query` ("Artist Title"), then the `queries` ladder, and play the song that matches `song_id` or, failing that, `title` by `artist`
        (qualifiers like "(live)" ignored, as in SingWS's own KaraFun search). KaraFun's own queue is emptied first."""
        fields = {"id": int(song_id), "query": query}
        if queries:
            fields["queries"] = [str(q) for q in queries if str(q or "").strip()]
        if artist:
            fields["artist"] = str(artist)
        if title:
            fields["title"] = str(title)
        self.command("play", **fields)

    def pause(self) -> None: self.command("pause")
    def resume(self) -> None: self.command("resume")
    def stop(self) -> None: self.command("stop")
    def seek(self, seconds: float) -> None: self.command("seek", seconds=float(seconds))
    def set_key(self, semitones: int) -> None: self.command("key", semitones=int(semitones))
    def set_tempo(self, percent: int) -> None: self.command("tempo", percent=int(percent))
    def set_volume(self, volume: float) -> None: self.command("volume", volume=float(volume))
    def sign_in(self) -> None: self.command("signin")
    def top(self, chart_id: int = 7) -> None: self.command("top", id=int(chart_id))
    def login(self, email: str, password: str) -> None: self.command("login", email=str(email), password=str(password))
    def sign_out(self) -> None: self.command("signout")
    def audience(self, on: bool = True) -> None: self.command("audience", on=bool(on))
    def output(self, name: str = "") -> None: self.command("output", device=str(name or ""))   # route the player's sound to this output device ('' = system default)
    def reload(self) -> None: self.command("reload")

    def start_screencast(self, width: int, height: int, quality: int = 80) -> None:
        """Size the page (the program keeps it at or under 720p) and start drawing it in its picture window. `quality` is unused: there is no JPEG."""
        self.command("screencast", on=True, width=int(width), height=int(height))

    def window(self, on: bool = True, x: float = 0, y: float = 0, w: float = 1280, h: float = 720, level: int = 0, above: int = 0, id: str = "audience") -> None:
        """Show / place / hide a picture window: id "audience" (over SingWS's audience window) or "preview" (over the host preview pane), both showing the same
        picture (x, y from the top left of the main screen; `above` = window number to sit directly over)."""
        self.command("window", id=str(id), on=bool(on), x=float(x), y=float(y), w=float(w), h=float(h), level=int(level), above=int(above))

    def refresh(self, rate: int) -> None: self.command("refresh", rate=int(rate))

    def stats(self) -> None: self.command("stats")

    def stop_screencast(self) -> None:
        self.command("screencast", on=False)


RELOAD_SETTLE_S = 8.0            # seconds after the post-login reload during which the old page's statuses are ignored
SIGN_IN_AFTER_STATUSES = 60      # statuses come twice a second


class KaraFunWebService:
    """The whole set-up, automatic: nothing to choose or install by hand.

    start() runs on its own thread: it finds Chromium, or (when told the user agreed: start(download=True)) downloads it into ~/SingWS with progress,
    starts the helper with a profile folder that keeps the sign-in, and when KaraFun says it is not signed in it opens KaraFun's own sign-in
    ("Add a TV") in the user's browser, once. `on_state(state, detail)` says where it is, for a line of text in the settings:
      "preparing"  detail "Looking for a browser" / "Downloading Chromium (37%)"
      "needs_browser"  Chromium is not on this Mac: ask the user, then start(download=True)
      "starting"   "Starting KaraFun's player"
      "ready"      "Signed in to <plan>"     (the player is up and signed in)
      "sign_in"    "Sign in to KaraFun in your browser, then come back"
      "error"      the reason (the caller can start() again to retry)
    The player's own messages go to `on_message` as in KaraFunWeb."""

    def __init__(self, on_state: Callable[[str, str], None], *, on_message: Callable[[dict], None] = lambda m: None,
                 on_log: Callable[[str], None] = lambda s: None,
                 profile_dir=None, make_web=None, find_browser=None, credentials=None):
        self.on_state, self.on_message, self.on_log = on_state, on_message, on_log
        self._credentials = credentials          # () -> (email, password) or None: the saved login, if the user saved one
        self._login_tried = False
        self._reload_after_login = False
        self._reload_at = 0.0
        home = os.environ.get("SINGWS_HOME")
        self.profile_dir = Path(profile_dir) if profile_dir else (Path(home) if home else Path.home() / "SingWS") / "KaraFun-Display"
        self._make_web = make_web or (lambda **kw: KaraFunWeb(self.profile_dir, **kw))
        self._find_browser = find_browser or ensure_browser
        self.web: Optional[KaraFunWeb] = None
        self.state = "idle"
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._signed_out_seen = 0
        self._sign_in_opened = False
        self._ready_told = False
        self._allow_download = False

    def _set(self, state: str, detail: str = "") -> None:
        self.state = state
        try:
            self.on_state(state, detail)
        except Exception as error:
            self.on_log(f"state callback error: {error!r}")

    def start(self, download: bool = False) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._signed_out_seen = 0
            self._sign_in_opened = False
            self._login_tried = False
            self._reload_after_login = False
            self._ready_told = False
            self._allow_download = bool(download)
            self._thread = threading.Thread(target=self._run, name="karafun-web-setup", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        web, self.web = self.web, None
        if web is not None:
            web.close()
        self.state = "idle"

    def _progress(self, text: str) -> None:
        self._set("preparing", text or "Downloading Chromium")

    def _run(self) -> None:
        try:
            self._set("preparing", "Looking for a browser")
            try:
                browser = self._find_browser(self._progress, allow_download=self._allow_download)
            except BrowserMissing as missing:
                self._set("needs_browser", str(missing))
                return
            self._set("starting", "Starting KaraFun's player")
            web = self._make_web(on_message=self._message, on_log=self.on_log, helper_path=browser)
            self.web = web
            web.start()
        except Exception as error:
            self.on_log(f"set-up failed: {error}")
            self._set("error", str(error))

    def _open_sign_in_page(self) -> None:
        if self._sign_in_opened or self.web is None:
            return
        self._sign_in_opened = True
        self._set("sign_in", "Sign in to KaraFun in your browser, then come back")
        self.web.sign_in()

    def _message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "status":
            if message.get("signedIn") and self._login_tried and not self._reload_after_login and self.web is not None:
                # Signed in by the saved login: the page's search does not work until it has been loaded once more (found by testing), so reload it
                # once and call the player ready when it is signed in again.
                self._reload_after_login = True
                self._reload_at = time.monotonic()
                self._ready_told = False
                self.on_log("signed in with the saved login; reloading the player once")
                self._set("starting", "Starting KaraFun's player")
                self.web.command("reload")
                self._signed_out_seen = 0
            elif self._reload_after_login and time.monotonic() - self._reload_at < RELOAD_SETTLE_S:
                pass                       # statuses from the page that is being reloaded say nothing yet
            elif message.get("signedIn"):
                self._signed_out_seen = 0
                if not self._ready_told or self.state != "ready":
                    self._ready_told = True
                    self._set("ready", "Signed in" + (f" ({message['plan']})" if message.get("plan") else ""))
            else:
                self._signed_out_seen += 1
                # A few statuses in a row, so a page still starting does not count as "signed out".
                if self._signed_out_seen == 4 and self.web is not None:
                    self._ready_told = False
                    self._set("sign_in", "Signing in to KaraFun")
                # The profile signs itself in about 20 s after the page starts, so nothing is done about it for 30 s (twice a second).
                if self._signed_out_seen == SIGN_IN_AFTER_STATUSES and self.web is not None and not self._login_tried:
                    self._login_tried = True
                    saved = None
                    try:
                        saved = self._credentials() if self._credentials else None
                    except Exception as error:
                        self.on_log(f"saved login unreadable: {type(error).__name__}")
                    if saved:
                        self.on_log("signing in with the saved KaraFun login")
                        self.web.login(saved[0], saved[1])
                    else:
                        self._open_sign_in_page()
                # The saved login did not work (wrong or changed password; it can take up to ~40 s): the browser way, once.
                elif self._signed_out_seen == SIGN_IN_AFTER_STATUSES + 60 and self.web is not None:
                    self._open_sign_in_page()
        elif kind == "error" and message.get("message") == "KaraFun's web player stopped":
            self._set("error", "KaraFun's player stopped")
        elif kind == "error" and message.get("message") == "Chrome stopped" and self.web is not None and self.state not in ("idle", "error"):
            # The player's browser went away (closed by hand, crashed, killed): say so, so the host can restart it. (Our own stop() clears `web` first.)
            self._set("error", "The player's browser stopped")
        try:
            self.on_message(message)
        except Exception as error:
            self.on_log(f"message callback error: {error!r}")
