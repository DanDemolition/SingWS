"""KaraFun web player host whose song logic runs in Rust (`karafun_web_engine = "rust"`).

`singws-karafun-svc --drive` (rust/crates/singws-karafun-svc, driver.rs + runtime.rs) owns the display program, the sign-in service, the song machine and their
timers. This class is the thin app side: it sends what the app asks for (start, pause, seek, key/tempo, finish) as JSON lines, and carries out the effects the
Rust machines answer with (queue-entry fields, notifications, the background music fade, the picture windows). The picture windows, the sound routing and the
Chromium question stay Python's (they need Qt), so this class inherits them from KaraFunWebHost; only the song logic is replaced.

Calls are synchronous: the effects that come back BEFORE the answer are carried out on the calling thread, so after `start()` returns the entry looks exactly as
it does with the Python host. Anything the process says later (statuses, timers) is carried out on the UI thread.
The Python host stays the default; this one is used only when the setting asks for it and the program is found (see `make_host`).
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import karafun_web
from karafun_web_host import END_MARGIN_S, KaraFunWebHost

_SVC_NAME = "singws-karafun-svc"
CALL_TIMEOUT_S = 8.0


def find_svc() -> Optional[Path]:
    """The driver program: SINGWS_KARAFUN_SVC, the app bundle, native/, or a cargo build."""
    import platform
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"
    here = Path(__file__).resolve().parent
    candidates = [os.environ.get("SINGWS_KARAFUN_SVC", "")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [str(Path(meipass) / _SVC_NAME), str(Path(meipass) / "Frameworks" / _SVC_NAME)]
    candidates += [str(here / "Frameworks" / _SVC_NAME), str(here.parent / "Frameworks" / _SVC_NAME), str(here / "native" / "singws_karafun_svc" / arch / _SVC_NAME)]
    candidates += [str(Path.home() / ".cache" / d / kind / _SVC_NAME) for d in ("singws-rust-target-karafun-svc", "singws-rust-target") for kind in ("release", "debug")]
    candidates += [str(here / "rust" / "target" / kind / _SVC_NAME) for kind in ("release", "debug")]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    return None


def make_host(app, diag=None):
    """The Rust-driven host when the program and the display program are both here, else None (the caller keeps the Python host)."""
    svc, display = find_svc(), karafun_web.find_display_helper()
    if svc is None or display is None:
        return None
    return KaraFunWebHostRust(app, diag=diag, svc_path=svc, display_path=display)


class _RustWeb(karafun_web.KaraFunWeb):
    """The same command methods the window code calls (`window`, `order`, `audience`, `start_screencast`, `refresh`, `output`, `stop_screencast`, ...), sent to
    the display program through the driver instead of through a pipe of this object's own."""

    def __init__(self, host):
        super().__init__(helper_path=Path(os.devnull))
        self._host = host

    def command(self, name: str, _proc=None, **fields) -> None:
        self._host._send({"op": "display", "command": {"cmd": name, **fields}})

    def close(self) -> None:
        pass


class _RustService:
    """What the inherited code reads from `host.service`: the sign-in state and the web object."""

    def __init__(self, host):
        self._host = host
        self.state = "idle"
        self._web = _RustWeb(host)

    @property
    def web(self):
        return self._web if self.state not in ("idle",) else None

    def start(self, download: bool = False) -> None:
        self._host._retry_service()

    def stop(self) -> None:
        pass


class KaraFunWebHostRust(KaraFunWebHost):
    def __init__(self, app, diag=None, svc_path=None, display_path=None):
        super().__init__(app, diag=diag)
        self._shadow = None                 # the Python-vs-Rust shadow compares two engines; here Rust is the only one
        self._svc_path, self._display_path = Path(svc_path), Path(display_path)
        self.service = _RustService(self)
        self._proc: Optional[subprocess.Popen] = None
        self._inbox: "queue.Queue[Optional[dict]]" = queue.Queue()
        self._lock = threading.RLock()       # one event is carried out at a time, in the order the process said it
        self._write_lock = threading.Lock()
        self._seq = 0
        self._entry = None                   # the entry of the latest start: the effects of a song change ITS fields even after `asked` is cleared
        self._world_sent = None
        self._browser_asked = False
        self._exited = False

    # ---- the process
    def _profile_dir(self) -> Path:
        home = os.environ.get("SINGWS_HOME")
        return (Path(home) if home else Path.home() / "SingWS") / "KaraFun-Display"

    def _email(self) -> str:
        try:
            return str(self.app.settings.get("karafun_web_email", "") or "").strip()
        except Exception:
            return ""

    def _ensure_proc(self) -> bool:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return True
        args = [str(self._svc_path), "--drive", "--display", str(self._display_path), "--profile", str(self._profile_dir())]
        email = self._email()
        if email:
            args += ["--email", email]
        try:
            self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as error:
            self._log(f"could not start the Rust driver: {error}")
            self._proc = None
            return False
        self._exited = False
        proc = self._proc
        self._log(f"the Rust driver runs the KaraFun player (pid {proc.pid})")
        threading.Thread(target=self._read, args=(proc,), name="karafun-rust-reader", daemon=True).start()
        return True

    def _read(self, proc) -> None:
        try:
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    self._inbox.put(ev)
                    self._schedule_drain()
        finally:
            if self._proc is proc:
                self._exited = True
                self._inbox.put({"ev": "exited"})
                self._schedule_drain()

    def _schedule_drain(self) -> None:
        try:
            self.app._run_on_ui_thread(self._drain)
        except Exception:
            self._drain()

    def _drain(self) -> None:
        # never wait for the lock: a call in progress (holding it) is already carrying out every event in order, and the reader thread must keep reading
        if not self._lock.acquire(blocking=False):
            return
        try:
            while True:
                try:
                    ev = self._inbox.get_nowait()
                except queue.Empty:
                    return
                self._handle_event(ev)
        finally:
            self._lock.release()

    def _send(self, message: dict) -> int:
        if not self._ensure_proc():
            return 0
        message = dict(message, t=time.monotonic())
        seq = 0
        if message.get("op") in ("start_song", "toggle_pause", "finish", "seek", "key_tempo"):
            with self._write_lock:
                self._seq += 1
                seq = message["seq"] = self._seq
        line = json.dumps(message) + "\n"
        with self._write_lock:
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (OSError, ValueError):
                self._log("the Rust driver is not answering")
                return 0
        return seq

    def _call(self, op: str, **fields) -> bool:
        """Send one request and wait for its answer, carrying out the effects that come before it on THIS thread (so the entry is up to date when we return)."""
        self._send_world()
        seq = self._send(dict(fields, op=op))
        if not seq:
            return False
        try:
            return self._wait_for(seq, op)
        finally:
            self._schedule_drain()      # whatever the process said after its answer

    def _wait_for(self, seq: int, op: str) -> bool:
        deadline = time.monotonic() + CALL_TIMEOUT_S
        with self._lock:
            while True:
                try:
                    ev = self._inbox.get(timeout=max(0.05, deadline - time.monotonic()))
                except queue.Empty:
                    self._log(f"the Rust driver did not answer {op} in {CALL_TIMEOUT_S:.0f}s")
                    return False
                if ev.get("ev") == "done" and ev.get("seq") == seq:
                    return bool(ev.get("ok"))
                self._handle_event(ev)
                if ev.get("ev") == "exited":
                    return False

    def _send_world(self) -> None:
        """What the song machine reads from the app: is the entry still the active one."""
        asked = self.asked
        if asked is None:
            return
        try:
            current = self.app._karafun_active_entry() is asked["entry"]
        except Exception:
            current = False
        same = getattr(self.app, "_active_external_karafun", None) is asked.get("active")
        world = (bool(current), bool(same))
        if world != self._world_sent:
            self._world_sent = world
            self._send({"op": "world", "entry_current": world[0], "active_same": world[1]})

    def shutdown(self) -> None:
        self._stop_frames()
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(json.dumps({"op": "shutdown", "t": time.monotonic()}) + "\n")
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    def ensure_service(self) -> None:
        self._ensure_proc()

    def _retry_service(self) -> None:
        self._send({"op": "retry_service"})

    # ---- what the process says
    def _handle_event(self, ev: dict) -> None:
        kind = ev.get("ev")
        try:
            if kind == "effect":
                self._apply(ev.get("effect") or [])
            elif kind == "state":
                self._state(str(ev.get("service") or "idle"), list(ev.get("host") or ["idle", ""]))
            elif kind == "status":
                self._status(ev.get("message") or {})
            elif kind == "exited":
                self.service.state = "error"
                self._log("the Rust driver stopped")
        except Exception as error:      # a fault in one effect must not lose the rest
            self._log(f"effect failed ({kind}): {error!r}")

    def _state(self, service: str, host: list) -> None:
        self.service.state = service
        self.state = (host[0], host[1] if len(host) > 1 else "")
        if service == "needs_browser":
            self._ask_for_browser()

    def _ask_for_browser(self) -> None:
        """Chromium is not on this Mac: ask once, download it with the display program, then tell the driver to start again."""
        if self._browser_declined:
            self._fail_local("KaraFun's player needs Chromium, and the download was declined")
            return
        if self._browser_asked:
            return
        self._browser_asked = True
        try:
            from PyQt6.QtWidgets import QMessageBox
            answer = QMessageBox.question(
                self.app, "KaraFun player needs Chromium",
                "KaraFun songs play in Chromium, a lightweight browser that is not on this Mac yet.\n\n"
                "Download it now? It is about 150 MB (400 MB on disk) from the Chromium project, saved in your SingWS folder, once.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
            yes = answer == QMessageBox.StandardButton.Yes
        except Exception as error:
            self._log(f"could not ask about Chromium: {error}")
            yes = False
        if not yes:
            self._browser_asked = False
            self._browser_declined = True
            self._fail_local("KaraFun's player needs Chromium, and the download was declined")
            return
        self._log("the user agreed to download Chromium")

        def work():
            try:
                karafun_web.ensure_browser(lambda text: self._log(f"download: {text}"), allow_download=True)
                self._browser_asked = False
                self._retry_service()
            except Exception as error:
                self._browser_asked = False
                why = f"Chromium could not be downloaded: {error}"       # `error` is gone once this block ends, before the UI thread runs the lambda
                self.app._run_on_ui_thread(lambda: self._fail_local(why))
        threading.Thread(target=work, name="karafun-chromium-download", daemon=True).start()

    def _fail_local(self, why: str) -> None:
        """A failure only the app side knows about (Chromium declined / not downloadable): the same marks the song machine sets when a song fails."""
        asked, self.asked = self.asked, None
        entry = self._entry
        self._call("finish")
        if asked is None or entry is None:
            return
        self._log(f"giving up on the song: {why[:120]}")
        self._stop_frames()
        if self.app._karafun_active_entry() is not entry:
            return
        entry["karafun_submission_state"] = "karafun_failed"
        entry.pop("karafun_pending_at", None)
        entry["karafun_submission_error"] = why[:240]
        self.app._set_karafun_entry_status(entry, "failed", message=f"KaraFun automation failed: {why}", notify=False)
        self.app._show_processing_notification(f"KaraFun automation failed: {why} Press Stop in SingWS to return the song to the queue.", level="error")
        self.app._schedule_save_data(0)
        self.app.update_queue_display()

    def _status(self, message: dict) -> None:
        self.status = message
        asked = self.asked
        if asked is None:
            return
        self._send_world()
        # the log-only transport shadow hears what the player shows, as with the Python host
        try:
            position, duration = float(message.get("position") or 0.0), float(message.get("duration") or 0.0)
        except (TypeError, ValueError):
            position = duration = 0.0
        if position > 0:
            asked["last_position"] = position
        if not asked.get("heard"):
            return
        playing = bool(message.get("playing") or message.get("moving"))
        at_end = duration > 0 and position >= duration - END_MARGIN_S
        if not at_end:
            self._sat_at_end_since = None
        elif self._sat_at_end_since is None:
            self._sat_at_end_since = time.monotonic()
            self._shadow_observe("end_clock")
        self._shadow_observe("playing" if playing else "idle", 2.0)

    def _apply(self, fx: list) -> None:
        if not fx:
            return
        name, app, entry = fx[0], self.app, self._entry
        arg = lambda i: fx[i] if len(fx) > i else None
        if name == "entry_set" and entry is not None:
            entry[arg(1)] = arg(2)
        elif name == "entry_wall" and entry is not None:
            entry[arg(1)] = time.time() if arg(1) == "karafun_play_started_at" else int(time.time())
        elif name == "entry_pop" and entry is not None:
            entry.pop(arg(1), None)
        elif name == "entry_status" and entry is not None:
            if arg(1) == "failed":
                self.asked = None
            if arg(3):
                app._set_karafun_entry_status(entry, arg(1), message=arg(2))
            else:
                app._set_karafun_entry_status(entry, arg(1), message=arg(2), notify=False)
        elif name == "notify":
            app._show_processing_notification(arg(1), level=arg(2))
        elif name == "verified_duration" and entry is not None:
            try:
                app._apply_verified_duration(entry, int(arg(1)), source="karafun_result")
            except Exception:
                pass
        elif name == "show_key_tempo":
            app._karafun_show_key_tempo(arg(1), arg(2))
        elif name == "fade_bg":
            try:
                app._fade_bg_for_external_karafun()
            except Exception:
                pass
        elif name == "start_frames":
            asked = self.asked
            if asked is not None:
                asked["heard"] = True
            self._shadow_observe("playing")
            self._start_frames()
        elif name == "start_frames_hidden":
            self._start_frames(show=False)
        elif name == "stop_frames":
            if self.asked is not None:
                self.asked["heard"] = False
            self._stop_frames()
        elif name == "save":
            app._schedule_save_data(0)
        elif name == "update_queue":
            app.update_queue_display()
        elif name == "log":
            self._log(str(arg(1)))
        elif name == "diag":
            self._diag(str(arg(1)))
        elif name == "finish_complete":
            self._finish_complete()
        elif name == "page_message":
            self._page_message(arg(1) or {})
        else:
            self._log(f"unknown effect from the Rust driver: {name}")

    def _finish_complete(self) -> None:
        asked, app = self.asked, self.app
        active = (asked or {}).get("active")
        self._shadow_observe("idle")
        self.asked = None
        try:
            shadow = app._transport_shadow()
            if shadow is not None and active is not None:
                shadow.app_auto_complete(active.get("_shadow_id"))
        except Exception:
            pass
        app._finish_external_karafun_playback("complete", expected_active=active)

    def _page_message(self, message: dict) -> None:
        kind = message.get("type")
        if kind in ("log", "ready"):
            self._log(f"page: {message.get('message') or kind}")
        elif kind == "helper" and message.get("state") == "window":
            try:
                self._picture_numbers[str(message.get("id"))] = int(message.get("number"))
            except (TypeError, ValueError):
                pass
            self._restack_overlays()
        elif kind == "helper" and message.get("state") == "no_frames":
            self._log("NO PICTURE FRAMES from the graphics chip 8 s after the picture was shown")
            try:
                self.app._show_processing_notification("KaraFun: the graphics chip is giving no picture. Send the log (see the KaraFun GPU test).", level="warning")
            except Exception:
                pass

    # ---- what the app asks for
    def start(self, entry: dict, key: int = 0, tempo_percent=None, song_path: str = "") -> bool:
        song_id = self._song_id(entry, song_path)
        if not self.enabled() or song_id is None:
            return False
        app = self.app
        artist, title = app._karafun_entry_artist_title(entry)
        try:
            tempo = int(tempo_percent) if tempo_percent is not None else 100
        except (TypeError, ValueError):
            tempo = 100
        try:
            ladder = list(app._karafun_search_queries_for_entry(entry))
        except Exception:
            ladder = []
        query = f"{artist} {title}".strip()
        self._entry = entry
        self.asked = {"id": song_id, "entry": entry, "title": title, "artist": artist, "queries": ladder, "query": query, "key": int(key or 0), "tempo": tempo,
                      "at": time.monotonic(), "heard": False, "sent": False, "active": getattr(app, "_active_external_karafun", None)}
        self._sat_at_end_since = None
        self._world_sent = None
        ok = self._call("start_song", song_id=song_id, enabled=True, key=int(key or 0), tempo=tempo, title=title,
                        play={"query": query, "queries": ladder, "artist": artist, "title": title})
        if not ok:
            self.asked = None
        return ok

    def active(self) -> bool:
        return self.asked is not None and self.service.web is not None and self._entry_current() is not None

    def toggle_pause(self) -> bool:
        return self._call("toggle_pause") if self.active() and self.asked.get("heard") else False

    def seek_to(self, target_sec: float) -> bool:
        if not (self.active() and self.asked.get("heard")):
            return False
        try:
            target = float(target_sec)
        except (TypeError, ValueError):
            return False
        ok = self._call("seek", target=target)
        if ok:
            try:
                shadow = self.app._transport_shadow()
                sid = (self.asked.get("active") or {}).get("_shadow_id") if self.asked else None
                duration = float(self.status.get("duration") or 0.0)
                if shadow is not None and sid:
                    shadow.note_seek(sid, float((self.asked or {}).get("last_position") or 0.0), max(0.0, min(target, duration - 8.0)), duration)
            except Exception:
                pass
        return ok

    def apply_key_tempo(self, entry: dict, target_key: int, target_tempo: int):
        self._entry = self._entry or entry
        self._call("key_tempo", key=int(target_key), tempo=int(target_tempo))
        entry["karafun_key_current"], entry["karafun_tempo_current"] = int(target_key), int(target_tempo)
        return int(target_key), int(target_tempo)

    def on_finish(self) -> bool:
        ok = self._call("finish")
        self.asked = None
        self._sat_at_end_since = None
        return ok
