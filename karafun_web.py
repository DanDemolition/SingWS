"""Python client of the singws-karafun-web helper: KaraFun through its web player (karafun.com/web) in a windowless Google Chrome.

The helper (rust/crates/singws-karafun-web) owns Chrome and the page (DevTools pipe, the page script karafun_bridge.js); this module only starts
it, turns its JSON lines into callbacks, and sends it JSON-line commands. Native code owns the player; Python owns the show.

Callbacks (they run on this module's reader thread): `on_message(dict)` for what the page says (`ready`, `status` twice a second with signedIn,
plan, playing, state, position, duration, song, queue; `log`; `error`; `fullscreen`; `raise`; and `helper` lines from the program itself),
`on_frame(bytes)` for each picture frame (a JPEG) while the screencast is on, `on_log(str)` for this module's own lines.

Google Chrome must be installed: Apple's web view cannot run KaraFun's player (its song database needs a cross-origin isolated page, which
KaraFun asks for with a header WebKit does not support). KaraFun has not approved driving its site and can change it at any time.
Nothing in SingWS uses this yet; tools/karafun_web_probe.py runs it by hand.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

_HELPER_NAME = "singws-karafun-web"


def find_chrome() -> Optional[Path]:
    """Where Google Chrome is, if it is installed (system or per-user Applications)."""
    app = Path("Google Chrome.app/Contents/MacOS/Google Chrome")
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if (base / app).is_file():
            return base / app
    return None


def find_helper() -> Optional[Path]:
    """The helper binary: SINGWS_KARAFUN_WEB, the app bundle's Frameworks, native/singws_karafun_web/<arch>, or a cargo build."""
    import platform
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    here = Path(__file__).resolve().parent
    candidates = [os.environ.get("SINGWS_KARAFUN_WEB", "")]
    candidates += [str(here / "Frameworks" / _HELPER_NAME), str(here.parent / "Frameworks" / _HELPER_NAME),
                   str(here / "native" / "singws_karafun_web" / arch / _HELPER_NAME)]
    candidates += [str(Path.home() / ".cache" / d / "release" / _HELPER_NAME) for d in ("singws-rust-target-kfweb", "singws-rust-target")]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    return None


# ---- the browser: installed Chrome if there is one, else Chrome for Testing downloaded once into ~/SingWS
# Chrome for Testing is published on these hosts only (the index says where each version is).
_CFT_HOSTS = ("storage.googleapis.com", "edgedl.me.gvt1.com", "dl.google.com")
_CFT_INDEX = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"


def _platform_key() -> str:
    import platform
    return "mac-arm64" if platform.machine() in ("arm64", "aarch64") else "mac-x64"


def browser_root() -> Path:
    """Where downloaded browsers live (~/SingWS/Chrome-for-Testing, or under SINGWS_HOME)."""
    home = os.environ.get("SINGWS_HOME")
    return (Path(home) if home else Path.home() / "SingWS") / "Chrome-for-Testing"


def _cft_binary(folder: Path) -> Path:
    return folder / f"chrome-{_platform_key()}" / "Google Chrome for Testing.app" / "Contents" / "MacOS" / "Google Chrome for Testing"


def downloaded_chrome() -> Optional[Path]:
    """A Chrome for Testing already downloaded (the newest version folder), if any."""
    root = browser_root()
    if not root.is_dir():
        return None
    for folder in sorted((d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")), reverse=True):
        binary = _cft_binary(folder)
        if binary.is_file() and (folder / ".complete").is_file():
            return binary
    return None


def _default_open(url: str, timeout: float = 30):
    """urlopen with the same root certificates the rest of SingWS uses (certifi): a Python without its certificates installed
    (the python.org one, or a frozen app) cannot verify Google's otherwise."""
    import ssl
    import urllib.request
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()
    return urllib.request.urlopen(url, timeout=timeout, context=context)


def ensure_browser(progress: Callable[[int, int], None] = lambda done, total: None, *, urlopen=None, verify: bool = True) -> Path:
    """The browser KaraFun's player runs in: an installed Google Chrome, else the Chrome for Testing in ~/SingWS (downloaded now if it is not
    there yet: about 200 MB from Google's own site, checked that it runs and is the version asked for, unpacked into a folder of its own and used only by SingWS).
    `progress(bytes_done, bytes_total)` is called while it downloads. Raises RuntimeError (nothing half-installed is left behind)."""
    installed = find_chrome()
    if installed is not None:
        return installed
    have = downloaded_chrome()
    if have is not None:
        return have
    import shutil
    import tempfile
    opener = urlopen or _default_open
    try:
        with opener(_CFT_INDEX, timeout=30) as response:
            index = json.loads(response.read().decode("utf-8"))
        stable = index["channels"]["Stable"]
        version = str(stable["version"])
        url = next(d["url"] for d in stable["downloads"]["chrome"] if d["platform"] == _platform_key())
    except Exception as error:
        raise RuntimeError(f"could not find a browser to download: {error}") from error
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if not url.startswith("https://") or host not in _CFT_HOSTS:
        raise RuntimeError(f"the browser download address is not Google's ({host or url[:40]})")
    root = browser_root()
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".download-", dir=root))
    try:
        archive = work / "chrome.zip"
        total = done = 0
        with opener(url, timeout=60) as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                progress(done, total)
        if total and done != total:
            raise RuntimeError(f"the download stopped early ({done} of {total} bytes)")
        unpacked = work / "unpacked"
        unpacked.mkdir()
        # ditto keeps the app's links and permissions (Python's zipfile would lose them).
        result = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(unpacked)], capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError("the browser could not be unpacked: " + (result.stderr or "").strip()[:200])
        archive.unlink()
        app = unpacked / f"chrome-{_platform_key()}" / "Google Chrome for Testing.app"
        if not _cft_binary(unpacked).is_file():
            raise RuntimeError("the downloaded browser has no program in it")
        if verify:
            # Chrome for Testing is not signed by Google (only ad-hoc), so the check is: Google's own download host, the size, and
            # that the program runs and names itself and the version we asked for.
            try:
                said = subprocess.run([str(_cft_binary(unpacked)), "--version"], capture_output=True, text=True, timeout=60).stdout
            except Exception as error:
                raise RuntimeError(f"the downloaded browser would not run: {error}") from error
            if "Google Chrome for Testing" not in said or version not in said:
                raise RuntimeError("the downloaded browser is not the one asked for: " + said.strip()[:120])
        (unpacked / ".complete").write_text(version)
        final = root / version
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        unpacked.rename(final)
        return _cft_binary(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)


class KaraFunWeb:
    def __init__(self, profile_dir=None, *, on_message: Callable[[dict], None] = lambda m: None,
                 on_frame: Callable[[bytes], None] = lambda b: None, on_log: Callable[[str], None] = lambda s: None,
                 helper_path=None, chrome_path=None):
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.on_message, self.on_frame, self.on_log = on_message, on_frame, on_log
        self.helper_path = Path(helper_path) if helper_path else find_helper()
        self.chrome_path = Path(chrome_path) if chrome_path else None
        self._proc: Optional[subprocess.Popen] = None
        self._write_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self.running = False

    def start(self) -> None:
        if self._proc is not None:
            return
        if self.helper_path is None:
            raise RuntimeError("the singws-karafun-web helper was not found (build it: cargo build --release -p singws-karafun-web)")
        args = [str(self.helper_path)]
        if self.profile_dir is not None:
            args += ["--profile", str(self.profile_dir)]
        if self.chrome_path is not None:
            args += ["--chrome", str(self.chrome_path)]
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
            proc.wait(timeout=5)
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
                    if message.get("type") == "frame":
                        self.on_frame(base64.b64decode(message.get("jpeg") or ""))
                    else:
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
    def play(self, song_id: int, query: Optional[str] = None) -> None:
        """Search KaraFun for `query` ("Artist Title") and play song `song_id` now (KaraFun's own queue is emptied first)."""
        self.command("play", id=int(song_id), query=query)

    def pause(self) -> None: self.command("pause")
    def resume(self) -> None: self.command("resume")
    def stop(self) -> None: self.command("stop")
    def seek(self, seconds: float) -> None: self.command("seek", seconds=float(seconds))
    def set_key(self, semitones: int) -> None: self.command("key", semitones=int(semitones))
    def set_tempo(self, percent: int) -> None: self.command("tempo", percent=int(percent))
    def set_volume(self, volume: float) -> None: self.command("volume", volume=float(volume))
    def sign_in(self) -> None: self.command("signin")
    def sign_out(self) -> None: self.command("signout")
    def audience(self, on: bool = True) -> None: self.command("audience", on=bool(on))
    def reload(self) -> None: self.command("reload")

    def start_screencast(self, width: int, height: int, quality: int = 80) -> None:
        """Size the page like the audience picture and stream it as JPEG frames to `on_frame`."""
        self.command("screencast", on=True, width=int(width), height=int(height), quality=int(quality))

    def stop_screencast(self) -> None:
        self.command("screencast", on=False)


class KaraFunWebService:
    """The whole set-up, automatic: nothing to choose or install by hand.

    start() runs on its own thread: it finds Google Chrome or downloads Chrome for Testing into ~/SingWS (a fixed folder, shown as progress),
    starts the helper with a profile folder that keeps the sign-in, and when KaraFun says it is not signed in it opens KaraFun's own sign-in
    ("Add a TV") in the user's browser, once. `on_state(state, detail)` says where it is, for a line of text in the settings:
      "preparing"  detail "Looking for a browser" / "Downloading the browser (37%)"
      "starting"   "Starting KaraFun's player"
      "ready"      "Signed in to <plan>"     (the player is up and signed in)
      "sign_in"    "Sign in to KaraFun in your browser, then come back"
      "error"      the reason (the caller can start() again to retry)
    The player's own messages and picture frames go to `on_message` and `on_frame` as in KaraFunWeb."""

    def __init__(self, on_state: Callable[[str, str], None], *, on_message: Callable[[dict], None] = lambda m: None,
                 on_frame: Callable[[bytes], None] = lambda b: None, on_log: Callable[[str], None] = lambda s: None,
                 profile_dir=None, make_web=None, find_browser=None):
        self.on_state, self.on_message, self.on_frame, self.on_log = on_state, on_message, on_frame, on_log
        home = os.environ.get("SINGWS_HOME")
        self.profile_dir = Path(profile_dir) if profile_dir else (Path(home) if home else Path.home() / "SingWS") / "Chrome-KaraFun"
        self._make_web = make_web or (lambda **kw: KaraFunWeb(self.profile_dir, **kw))
        self._find_browser = find_browser or ensure_browser
        self.web: Optional[KaraFunWeb] = None
        self.state = "idle"
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._signed_out_seen = 0
        self._sign_in_opened = False
        self._ready_told = False

    def _set(self, state: str, detail: str = "") -> None:
        self.state = state
        try:
            self.on_state(state, detail)
        except Exception as error:
            self.on_log(f"state callback error: {error!r}")

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._signed_out_seen = 0
            self._sign_in_opened = False
            self._ready_told = False
            self._thread = threading.Thread(target=self._run, name="karafun-web-setup", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        web, self.web = self.web, None
        if web is not None:
            web.close()
        self.state = "idle"

    def _progress(self, done: int, total: int) -> None:
        percent = f" ({done * 100 // total}%)" if total else ""
        self._set("preparing", f"Downloading the browser{percent}")

    def _run(self) -> None:
        try:
            self._set("preparing", "Looking for a browser")
            browser = self._find_browser(self._progress)
            self._set("starting", "Starting KaraFun's player")
            web = self._make_web(on_message=self._message, on_frame=self.on_frame, on_log=self.on_log, chrome_path=browser)
            self.web = web
            web.start()
        except Exception as error:
            self.on_log(f"set-up failed: {error}")
            self._set("error", str(error))

    def _message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "status":
            if message.get("signedIn"):
                self._signed_out_seen = 0
                if not self._ready_told or self.state != "ready":
                    self._ready_told = True
                    self._set("ready", "Signed in" + (f" ({message['plan']})" if message.get("plan") else ""))
            else:
                self._signed_out_seen += 1
                # A few statuses in a row, so a page still starting does not count as "signed out".
                if self._signed_out_seen == 4 and self.web is not None:
                    self._ready_told = False
                    self._set("sign_in", "Sign in to KaraFun in your browser, then come back")
                    if not self._sign_in_opened:
                        self._sign_in_opened = True
                        self.web.sign_in()
        elif kind == "error" and message.get("message") == "KaraFun's web player stopped":
            self._set("error", "KaraFun's player stopped")
        try:
            self.on_message(message)
        except Exception as error:
            self.on_log(f"message callback error: {error!r}")
