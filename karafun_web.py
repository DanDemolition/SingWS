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
