"""KaraFun songs through KaraFun's web player (karafun_web.py) instead of the KaraFun Mac app: the part that knows SingWS's show.

Off unless Settings > KaraFun > "Use KaraFun's web player" (karafun_web_player). The hooks in 0.2.18.1.py are small and each begins with
"is the web player on and is this song its?": a song with no KaraFun song id, or the setting off, takes the KaraFun-app path exactly as before.

What this does, in the show's own terms:
  start      play the song's KaraFun id by search ("artist title") through the web player; the entry shows "Starting KaraFun..." and fails
             (with the notification the app path uses) if the player is not ready and signed in, or never starts within 30 s
  follow     the player's status (twice a second) keeps the entry's display clock, duration and pause state true, so the seek bar,
             time left and the end rules read it like any song; the song's real length replaces an estimate
  end        the song ends when KaraFun stopped at its end, the song is gone, another one is on, or it sat at its end 2.5 s: then the
             normal completion path runs (_finish_external_karafun_playback("complete"))
  controls   pause/resume, seek, key and tempo go to the player; stop (and any finish) stops it and takes the picture off the audience
  picture    the page is streamed as JPEG frames (about 30 a second are shown) to the audience window and the preview
The player's own queue is emptied before each song (karafun_bridge.js), so only SingWS's song is ever in it.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from typing import Optional

NEVER_STARTED_AFTER_S = 60.0
SAT_AT_END_S = 2.5
END_MARGIN_S = 1.5
SEEK_END_GUARD_S = 8.0
FRAME_INTERVAL_MS = 33


def song_id_from_entry(entry, song_path: str = "") -> Optional[int]:
    """KaraFun's own id for a queue entry ("kf_3250037" in its provider id or its karafun_streaming: path), if it has one."""
    candidates = []
    if isinstance(entry, dict):
        candidates += [entry.get("provider_track_id"), entry.get("path")]
    candidates.append(song_path)
    for value in candidates:
        match = re.search(r"(?:^|[:/])kf_(\d+)$", str(value or "").strip())
        if match:
            return int(match.group(1))
    return None


class KaraFunWebHost:
    def __init__(self, app, service_factory=None, diag=None):
        self.app = app
        self._diag_fn = diag or (lambda text: None)
        self._factory = service_factory
        self.service = None
        self.state = ("idle", "")
        self.asked: Optional[dict] = None
        self.status: dict = {}
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[bytes] = None
        self._frame_timer = None
        self._frames_shown = False
        self._sat_at_end_since: Optional[float] = None
        self._song_open = False          # a song was handed to the web player and has not been finished by the app yet

    # ---- settings and the service
    def enabled(self) -> bool:
        return sys.platform == "darwin" and bool(self.app.settings.get("karafun_web_player", False))

    def _diag(self, text: str) -> None:
        try:
            self._diag_fn(text)
        except Exception:
            pass

    def ensure_service(self) -> None:
        """Start the player's set-up (browser, helper, sign-in) once; safe to call again (UI thread)."""
        if self.service is None:
            if self._factory is not None:
                factory = self._factory
            else:
                import karafun_web
                factory = karafun_web.KaraFunWebService
            self.service = factory(self._service_state, on_message=self._on_message, on_frame=self._on_frame, on_log=self._log,
                                   credentials=self._saved_login)
        if self.service.state in ("idle", "error"):
            self.service.start()

    def _saved_login(self):
        """(email, password) saved in the Keychain from Settings, or None. The password is never kept in settings.json."""
        import karafun_credentials
        email = str(self.app.settings.get("karafun_web_email", "") or "").strip()
        password = karafun_credentials.load(email) if email else None
        return (email, password) if email and password else None

    def shutdown(self) -> None:
        self._stop_frames()
        service, self.service = self.service, None
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass

    def _log(self, text: str) -> None:
        self._diag(f"[KARAFUN-WEB] {text}")

    def _service_state(self, state: str, detail: str) -> None:
        self.state = (state, detail)
        self._log(f"state={state} {detail}".rstrip())
        self.app._run_on_ui_thread(lambda: self._state_changed(state, detail))

    def _state_changed(self, state: str, detail: str) -> None:
        asked = self.asked
        if asked is None or asked.get("heard"):
            return
        if state == "ready" and not asked.get("sent"):
            self._send_play()
        elif state == "error":
            self._fail(detail)
        # "sign_in" is also what the player says for the few seconds before it has logged itself in from its saved profile: wait for "ready"

    # ---- a song
    def can_start(self, entry, song_path: str = "") -> bool:
        return self.enabled() and song_id_from_entry(entry, song_path) is not None

    def start(self, entry: dict, key: int = 0, tempo_percent=None, song_path: str = "") -> bool:
        song_id = song_id_from_entry(entry, song_path)
        if not self.enabled() or song_id is None:
            return False
        app = self.app
        artist, title = app._karafun_entry_artist_title(entry)
        try:
            tempo = int(tempo_percent) if tempo_percent is not None else 100
        except (TypeError, ValueError):
            tempo = 100
        try:      # the app's own, proven query ladder (artist+title, title+artist, title, artist: cleaned of qualifiers)
            ladder = list(app._karafun_search_queries_for_entry(entry))
        except Exception:
            ladder = []
        self.asked = {"id": song_id, "entry": entry, "title": title, "artist": artist, "queries": ladder, "query": f"{artist} {title}".strip(), "key": int(key or 0),
                      "tempo": tempo, "at": time.monotonic(), "heard": False, "sent": False,
                      "active": getattr(app, "_active_external_karafun", None)}
        self._sat_at_end_since = None
        self._song_open = True
        entry["karafun_pending_at"] = int(time.time())
        entry["karafun_key_current"], entry["karafun_tempo_current"] = 0, 100
        app._set_karafun_entry_status(entry, "launching", message="Starting KaraFun...")
        self._log(f"start id={song_id} title={title!r} key={key} tempo={tempo}")
        self.ensure_service()
        state = self.service.state
        if state == "ready":
            self._send_play()
        elif state == "error":
            self._fail(self.state[1] or "KaraFun's web player could not start")
        # preparing / starting / sign_in (logging itself in): played as soon as it is ready (_state_changed), or failed after 30 s (_follow's watchdog timer)
        self._arm_watchdog()
        return True

    def _send_play(self) -> None:
        asked = self.asked
        if asked is None or self.service is None or self.service.web is None:
            return
        asked["sent"] = True
        asked["sent_at"] = time.monotonic()
        self.service.web.play(asked["id"], asked["query"], asked.get("queries"), asked.get("artist", ""), asked.get("title", ""))

    def _arm_watchdog(self) -> None:
        asked = self.asked
        if asked is None:
            return
        from PyQt6.QtCore import QTimer

        def _check():
            if self.asked is asked and not asked["heard"]:
                if time.monotonic() - asked["at"] >= NEVER_STARTED_AFTER_S:
                    if self.service is not None and self.service.state == "sign_in":
                        self._fail("KaraFun's web player is not signed in: sign in to KaraFun in your browser (Settings > KaraFun)")
                    else:
                        self._fail("KaraFun's web player never started the song")
                else:
                    QTimer.singleShot(1000, _check)
        QTimer.singleShot(1000, _check)

    def _entry_current(self) -> Optional[dict]:
        asked = self.asked
        if asked is None:
            return None
        if self.app._karafun_active_entry() is not asked["entry"]:
            return None
        return asked["entry"]

    def _fail(self, why: str) -> None:
        asked, self.asked = self.asked, None
        if asked is None:
            return
        entry = asked["entry"]
        self._stop_frames()
        try:
            if self.service is not None and self.service.web is not None:
                self.service.web.stop()
        except Exception:
            pass
        if self.app._karafun_active_entry() is not entry:
            return
        entry["karafun_submission_state"] = "karafun_failed"
        entry.pop("karafun_pending_at", None)
        entry["karafun_submission_error"] = why[:240]
        self.app._set_karafun_entry_status(entry, "failed", message=f"KaraFun automation failed: {why}", notify=False)
        self._log(f"failed title={asked['title']!r} error={why[:240]!r}")
        self.app._show_processing_notification(f"KaraFun automation failed: {why} Press Stop in SingWS to return the song to the queue.", level="error")
        self.app._schedule_save_data(0)
        self.app.update_queue_display()

    # ---- what the page says (reader thread -> UI thread)
    def _on_message(self, message: dict) -> None:
        self.app._run_on_ui_thread(lambda: self._handle(message))

    def _handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "status":
            self.status = message
            self._follow()
        elif kind == "error":
            text = str(message.get("message") or "")
            self._log(f"page error: {text}")
            asked = self.asked
            if asked is not None and not asked["heard"] and text.startswith("Play"):
                self._fail(text)
        elif kind in ("log", "ready"):
            self._log(f"page: {message.get('message') or kind}")

    def _follow(self) -> None:
        asked = self.asked
        if asked is None:
            return
        entry = self._entry_current()
        if entry is None:
            return
        status = self.status
        song = status.get("song") or {}
        current = song.get("id")
        playing = bool(status.get("playing"))
        try:
            position, duration = float(status.get("position") or 0.0), float(status.get("duration") or 0.0)
        except (TypeError, ValueError):
            position = duration = 0.0
        if not asked["heard"]:
            if playing and current == asked["id"]:
                self._started(entry, asked, position, duration)
            return
        # The display clock follows the player (the seek bar and time left read these entry fields).
        now = time.monotonic()
        if duration > 0:
            entry["karafun_display_duration"] = duration
        if playing:
            entry.pop("karafun_paused_at", None)
            entry["karafun_display_started_at"] = now - position
        elif current == asked["id"] and not entry.get("karafun_paused_at"):
            entry["karafun_paused_at"] = now
            entry["karafun_display_started_at"] = now - position
        at_end = duration > 0 and position >= duration - END_MARGIN_S
        if not at_end:
            self._sat_at_end_since = None
        elif self._sat_at_end_since is None:
            self._sat_at_end_since = now
        if current is None:
            why = "gone from the player"
        elif current != asked["id"]:
            why = "the player went on to another song"
        elif at_end and not playing:
            why = "stopped at the end"
        elif self._sat_at_end_since is not None and now - self._sat_at_end_since > SAT_AT_END_S:
            why = "at the end"
        else:
            return
        self._complete(entry, asked, why)

    def _started(self, entry: dict, asked: dict, position: float, duration: float) -> None:
        app = self.app
        asked["heard"] = True
        now = time.monotonic()
        entry["karafun_playback_clock_started_at"] = now - position
        entry["karafun_display_started_at"] = now - position
        entry["karafun_play_started_at"] = time.time()
        if duration > 0:
            entry["karafun_display_duration"] = duration
            try:
                app._apply_verified_duration(entry, int(round(duration)), source="karafun_result")
            except Exception:
                pass
        entry["karafun_submission_state"] = "karafun_queued"
        entry["karafun_submitted_at"] = int(time.time())
        entry.pop("karafun_pending_at", None)
        app._set_karafun_entry_status(entry, "playing", message="Playing in KaraFun")
        self._log(f"started after {now - asked['at']:.1f}s title={asked['title']!r} duration={duration:.0f}s")
        try:
            app._fade_bg_for_external_karafun()
        except Exception:
            pass
        web = self.service.web if self.service is not None else None
        if web is not None:
            if asked["key"]:
                web.set_key(asked["key"])
            if asked["tempo"] != 100:
                web.set_tempo(asked["tempo"])
        entry["karafun_key_current"], entry["karafun_tempo_current"] = asked["key"], asked["tempo"]
        app._karafun_show_key_tempo(asked["key"], asked["tempo"])
        self._start_frames()
        app._show_processing_notification(f"KaraFun queued and started: {asked['title']}", level="success")

    def _complete(self, entry: dict, asked: dict, why: str) -> None:
        app = self.app
        active = asked.get("active")
        if getattr(app, "_active_external_karafun", None) is not active:
            self.asked = None
            return
        self._log(f"song ended ({why})")
        self.asked = None
        self._diag(f"[KARAFUN] completion event received reason=web_{why.replace(' ', '_')} remaining=0 idle=1")
        try:
            shadow = app._transport_shadow()
            if shadow is not None:
                shadow.app_auto_complete(active.get("_shadow_id"))
        except Exception:
            pass
        app._finish_external_karafun_playback("complete", expected_active=active)

    # ---- controls
    def active(self) -> bool:
        return self.asked is not None and self.service is not None and self.service.web is not None and self._entry_current() is not None

    def toggle_pause(self) -> bool:
        if not self.active() or not self.asked["heard"]:
            return False
        web = self.service.web
        (web.pause if self.status.get("playing") else web.resume)()
        return True

    def seek_to(self, target_sec: float) -> bool:
        if not self.active() or not self.asked["heard"]:
            return False
        try:
            duration = float(self.status.get("duration") or 0.0)
        except (TypeError, ValueError):
            return False
        if duration <= 0:
            return False
        target = max(0.0, min(float(target_sec), duration - SEEK_END_GUARD_S))
        self.service.web.seek(target)
        entry = self.asked["entry"]
        now = time.monotonic()
        entry["karafun_display_started_at"] = (entry.get("karafun_paused_at") or now) - target
        self._log(f"seek to {target:.0f}s")
        return True

    def apply_key_tempo(self, entry: dict, target_key: int, target_tempo: int):
        """The app's live key/tempo sync (worker thread): the player's own queue entry takes both, exactly."""
        if self.service is not None and self.service.web is not None:
            self.service.web.set_key(int(target_key))
            self.service.web.set_tempo(int(target_tempo))
        entry["karafun_key_current"], entry["karafun_tempo_current"] = int(target_key), int(target_tempo)
        self._log(f"live key/tempo {int(target_key):+d}/{int(target_tempo)}%")
        return int(target_key), int(target_tempo)

    def on_finish(self) -> bool:
        """Any end of the KaraFun song (complete, stop, return to queue): the player stops and the picture leaves the audience screen.

        Returns True when a web-player song was open, so the app can bring its idle slideshow back. This must not depend on `asked`: a song that ends by itself
        clears `asked` before the app's finish runs, and the picture stream and the player would otherwise be left running (the audience kept KaraFun's page).
        """
        was_open = self._song_open or self.asked is not None
        self._song_open = False
        self.asked = None
        self._sat_at_end_since = None
        if not was_open:
            return False
        self._stop_frames()
        try:
            if self.service is not None and self.service.web is not None:
                self.service.web.stop()
        except Exception:
            pass
        return True

    # ---- the picture
    def _audience_size(self):
        vw = getattr(self.app, "video_window", None)
        try:
            size = vw.video_area.size() if vw is not None else None
            if size is not None and size.width() > 100 and size.height() > 100:
                return min(int(size.width()), 1920), min(int(size.height()), 1080)
        except Exception:
            pass
        return 1280, 720

    def _start_frames(self) -> None:
        if self.service is None or self.service.web is None:
            return
        from PyQt6.QtCore import QTimer
        app = self.app
        try:
            app._set_mpv_hosts_visible(False)   # a retained mpv view can cover Qt's painted audience and preview
        except Exception:
            pass
        # Always captured at full HD: a smaller window was blown up to the TV and looked pixelated; Qt scales it down where the window is smaller.
        self.service.web.audience(True)
        self.service.web.start_screencast(1920, 1080, 92)
        if self._frame_timer is None:
            self._frame_timer = QTimer(app)
            self._frame_timer.setInterval(FRAME_INTERVAL_MS)
            self._frame_timer.timeout.connect(self._paint_latest)
        self._frame_timer.start()

    def _stop_frames(self) -> None:
        timer = self._frame_timer
        if timer is not None:
            timer.stop()
        try:
            if self.service is not None and self.service.web is not None:
                self.service.web.stop_screencast()
        except Exception:
            pass
        with self._frame_lock:
            self._latest_frame = None
        if self._frames_shown:
            self._frames_shown = False
            from PyQt6.QtGui import QImage
            vw = getattr(self.app, "video_window", None)
            if vw is not None:
                vw.video_area.set_karaoke_frame(QImage())
                vw.idle = True
            preview = getattr(self.app, "preview_window", None)
            if preview is not None:
                preview.video_area.set_karaoke_frame(QImage())

    def _on_frame(self, jpeg: bytes) -> None:
        with self._frame_lock:
            self._latest_frame = jpeg           # only the newest is kept: a slow paint never queues old pictures

    def _paint_latest(self) -> None:
        with self._frame_lock:
            data, self._latest_frame = self._latest_frame, None
        if not data:
            return
        from PyQt6.QtGui import QImage
        frame = QImage.fromData(data, "JPEG")
        if frame.isNull():
            return
        vw = getattr(self.app, "video_window", None)
        preview = getattr(self.app, "preview_window", None)
        if vw is None or preview is None:
            return
        vw.idle = False
        vw.video_area.set_karaoke_frame(frame, stretch_fill=True)
        preview.force_black = False
        preview.video_area.set_karaoke_frame(frame)
        self._frames_shown = True
