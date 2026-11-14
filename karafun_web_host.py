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
RECOVER_START_WITHIN_S = 150.0     # a restarted player may need to sign itself in again before it plays
MAX_RECOVERIES = 3
SAT_AT_END_S = 2.5
END_MARGIN_S = 1.5
SEEK_END_GUARD_S = 8.0
KARAFUN_PAGE_WIDTH, KARAFUN_PAGE_HEIGHT = 1280, 720   # the page is drawn at 720p (the display program never goes above it); the window scales it up to the TV
PICTURE_REPLACE_MS = 200    # how often the picture windows are put back over their windows (moved, resized, or the window was clicked to the front)


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
        self._picture_timer = None
        self._picture_placed = None
        self._preview_placed = None
        self._frames_shown = False
        self._sat_at_end_since: Optional[float] = None
        self._song_open = False          # a song was handed to the web player and has not been finished by the app yet

    # ---- settings and the service
    def enabled(self) -> bool:
        """The web player is the only way KaraFun songs play (macOS for now)."""
        return sys.platform == "darwin"


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
            self.service = factory(self._service_state, on_message=self._on_message, on_log=self._log,
                                   credentials=self._saved_login)
        if self.service.state in ("idle", "error", "needs_browser"):
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

    _browser_declined = False      # the user said no to downloading Chromium this session: songs fail at once instead of asking again
    _asking_browser = False

    def _ask_for_browser(self) -> None:
        """Chromium is not on this Mac: ask once before downloading it (about 150 MB from the Chromium project into ~/SingWS)."""
        if self._browser_declined:
            self._fail("KaraFun's player needs Chromium, and the download was declined")
            return
        if self._asking_browser or self.service is None:
            return
        self._asking_browser = True
        from PyQt6.QtWidgets import QMessageBox
        try:
            answer = QMessageBox.question(
                self.app, "KaraFun player needs Chromium",
                "KaraFun songs play in Chromium, a lightweight browser that is not on this Mac yet.\n\n"
                "Download it now? It is about 150 MB (400 MB on disk) from the Chromium project, saved in your SingWS folder, once.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        except Exception as error:
            self._log(f"could not ask about Chromium: {error}")
            answer = None
        finally:
            self._asking_browser = False
        if answer == QMessageBox.StandardButton.Yes and self.service is not None:
            self._log("the user agreed to download Chromium")
            self.service.start(download=True)
        else:
            self._browser_declined = True
            self._fail("KaraFun's player needs Chromium, and the download was declined")

    def _state_changed(self, state: str, detail: str) -> None:
        if state == "needs_browser":
            self._ask_for_browser()
            return
        asked = self.asked
        if asked is None:
            return
        if state == "error" and asked.get("heard") and self._song_open:
            self._recover(detail)              # the player went away DURING the song: restart it and carry on
            return
        if asked.get("heard"):
            return
        if state == "ready" and not asked.get("sent"):
            self._send_play()
        elif state == "error":
            self._fail(detail)
        # "sign_in" is also what the player says for the few seconds before it has logged itself in from its saved profile: wait for "ready"

    def _recover(self, why: str) -> None:
        """The player's browser died while a song was playing (closed by hand, crashed, killed). Restart it and resume the song where it was, with the
        key and tempo it had; give up (the normal failed-song path) after MAX_RECOVERIES. Never starts a second song: it replays the SAME one."""
        asked = self.asked
        if asked is None:
            return
        entry = self._entry_current()
        tries = int(asked.get("recoveries", 0))
        if entry is None or tries >= MAX_RECOVERIES or self.service is None:
            self._fail(f"KaraFun's player stopped and could not be restarted ({why})")
            return
        resume = float(asked.get("last_position") or 0.0)
        asked.update({"recoveries": tries + 1, "heard": False, "sent": False, "resume_at": resume, "at": time.monotonic(),
                      "limit": RECOVER_START_WITHIN_S})
        asked["key"] = int(entry.get("karafun_key_current", asked["key"]) or 0)
        asked["tempo"] = int(entry.get("karafun_tempo_current", asked["tempo"]) or 100)
        self._sat_at_end_since = None
        self._log(f"the player stopped during the song ({why}); restarting it and resuming at {resume:.0f}s (attempt {tries + 1}/{MAX_RECOVERIES})")
        self._stop_frames()
        try:
            self.app._set_karafun_entry_status(entry, "launching", message="Restarting KaraFun's player...")
            self.app._show_processing_notification("KaraFun's player stopped: restarting it and resuming the song.", level="warning")
        except Exception:
            pass
        try:
            self.service.stop()
            self.service.start()
        except Exception as error:
            self._fail(f"KaraFun's player could not be restarted: {error}")
            return
        self._arm_watchdog()

    # ---- a song
    def _song_id(self, entry, song_path: str = "") -> Optional[int]:
        """The KaraFun id of the song, or 0 for a KaraFun song added by hand with no id (the page then finds it by title and artist); None if it is not a KaraFun song."""
        song_id = song_id_from_entry(entry, song_path)
        if song_id is not None:
            return song_id
        try:
            return 0 if self.app._is_external_karafun_entry(entry) else None
        except Exception:
            return None

    def can_start(self, entry, song_path: str = "") -> bool:
        return self.enabled() and self._song_id(entry, song_path) is not None

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
                if time.monotonic() - asked["at"] >= asked.get("limit", NEVER_STARTED_AFTER_S):
                    s = self.status or {}
                    if (s.get("song") or {}).get("id") == asked["id"] and (s.get("playing") or s.get("moving")):
                        entry = self._entry_current()
                        if entry is not None:         # it IS playing: the start was just not noticed in time; treat it as started, never fail a song that is sounding
                            self._log("the song was playing but its start was not noticed; treating it as started")
                            self._started(entry, asked, float(s.get("position") or 0.0), float(s.get("duration") or 0.0))
                            return
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

    def _stop_stray(self) -> None:
        now = time.monotonic()
        if now - getattr(self, "_stray_stopped_at", 0.0) < 4.0:
            return
        self._stray_stopped_at = now
        self._log("the player was playing a song SingWS did not ask for; stopped it")
        try:
            if self.service is not None and self.service.web is not None:
                self.service.web.stop()
        except Exception:
            pass

    def _last_status_summary(self) -> str:
        s = self.status or {}
        song = s.get("song") or {}
        return (f"last status: playing={s.get('playing')} moving={s.get('moving')} state={s.get('state')} song_id={song.get('id')} "
                f"position={s.get('position')} duration={s.get('duration')} queue={s.get('queue')}")

    def _fail(self, why: str) -> None:
        asked, self.asked = self.asked, None
        if asked is None:
            return
        entry = asked["entry"]
        self._log(f"giving up on the song: {why[:120]} | {self._last_status_summary()}")
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
            if self.asked is None and not self._song_open and (message.get("playing") or message.get("moving")):
                self._stop_stray()          # audio nobody asked for (KaraFun's own queue starting itself): never leave it playing under the show
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
        playing = bool(status.get("playing") or status.get("moving"))      # the page's own state can lag or miss; a position that moves IS playing
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
        if position > 0:
            asked["last_position"] = position
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
        resume = float(asked.pop("resume_at", 0.0) or 0.0)
        if resume > 3.0 and web is not None:
            target = max(0.0, min(resume, (duration - SEEK_END_GUARD_S) if duration > 0 else resume))
            web.seek(target)
            self._log(f"resumed the song at {target:.0f}s after restarting the player")
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
    def _route_sound(self) -> None:
        """Play the player's sound on the output SingWS itself uses, not whatever macOS made the default: an AirPlay display flips the system default to
        the TV, and SingWS already resolves its output without following it (a pinned device, else the headphone jack / built-in speakers)."""
        name = ""
        try:
            name = str(self.app._safe_local_audio_output_name() or "")
        except Exception:
            settings = getattr(self.app, "settings", {}) or {}
            if str(settings.get("audio_output_id", "default") or "default") != "default":
                name = str(settings.get("audio_output_name", "") or "")
        try:
            self._log(f"player sound goes to: {name or 'the system default'}")
            self.service.web.output(name)
        except Exception:
            pass

    def _audience_rect(self):
        """The audience picture's place in global points from the top left of the main screen (Qt's own), and the number of the audience window so the
        picture window can sit directly over it (the ticker and QR windows above that stay over the picture). None without an audience window."""
        vw = getattr(self.app, "video_window", None)
        area = getattr(vw, "video_area", None) if vw is not None else None
        if area is None:
            return None
        try:
            if not vw.isVisible() or vw.isMinimized():
                return None                 # a hidden audience window keeps its old place: the picture must not be drawn there
        except Exception:
            pass
        from PyQt6.QtCore import QPoint
        top_left = area.mapToGlobal(QPoint(0, 0))
        return (top_left.x(), top_left.y(), area.width(), area.height()), _ns_window_number(vw)

    def _preview_rect(self):
        """The host preview pane's place in global points (same frame of reference as the audience's) and the main window's number, so a second picture window
        can sit directly over the main window, over that pane. None while the pane is not on screen (another tab, minimised, hidden)."""
        preview = getattr(self.app, "preview_window", None)
        area = getattr(preview, "video_area", None) if preview is not None else None
        if area is None:
            return None
        try:
            if not area.isVisible() or not self.app.isVisible() or self.app.isMinimized():
                return None
            if area.width() < 20 or area.height() < 20:
                return None
        except Exception:
            return None
        from PyQt6.QtCore import QPoint
        top_left = area.mapToGlobal(QPoint(0, 0))
        return (top_left.x(), top_left.y(), area.width(), area.height()), _ns_window_number(self.app)

    def _place_preview(self, force: bool = False) -> None:
        try:
            if self.service is None or self.service.web is None:
                return
            placed = self._preview_rect()
            if placed is None:
                if self._preview_placed is not None or force:
                    self._preview_placed = None
                    self.service.web.window(False, id="preview")
                return
            if not force and placed == self._preview_placed:
                self.service.web.order("preview", placed[1])      # the main window comes to the front when clicked: put the picture over it again
                return
            (x, y, w, h), above = placed
            self._preview_placed = placed
            self.service.web.window(True, x, y, w, h, 0, above, id="preview")
        except Exception as error:
            self._log(f"could not place the preview picture: {error!r}")

    def _place_picture(self, force: bool = False) -> None:
        self._place_preview(force)
        try:
            if self.service is None or self.service.web is None:
                return
            placed = self._audience_rect()
            if placed is None:
                if self._picture_placed is not None or force:
                    self._picture_placed = None
                    self.service.web.window(False)      # the audience window is hidden or minimised: no loose picture over other windows
                return
            if not force and placed == self._picture_placed:
                self.service.web.order("audience", placed[1])
                return
            (x, y, w, h), above = placed
            self._picture_placed = placed
            self.service.web.window(True, x, y, w, h, 0, above)
        except Exception as error:
            self._log(f"could not place the picture: {error!r}")

    def _start_frames(self) -> None:
        if self.service is None or self.service.web is None:
            return
        from PyQt6.QtCore import QTimer
        app = self.app
        try:
            app._set_mpv_hosts_visible(False)   # a retained mpv view can cover the picture
        except Exception:
            pass
        web = self.service.web
        web.audience(True)
        self._route_sound()
        web.start_screencast(KARAFUN_PAGE_WIDTH, KARAFUN_PAGE_HEIGHT)
        try:
            vw = getattr(app, "video_window", None)
            rate = int(round(float(vw.screen().refreshRate()))) if vw is not None and vw.screen() is not None else 0
            if rate > 0:
                web.refresh(rate)               # the page is drawn at the audience screen's own pace
            if vw is not None:
                vw.idle = False                 # the picture window covers it: no slideshow drawing underneath
        except Exception:
            pass
        self._place_picture(force=True)
        self._frames_shown = True
        if self._picture_timer is None:
            self._picture_timer = QTimer(app)
            self._picture_timer.setInterval(PICTURE_REPLACE_MS)
            self._picture_timer.timeout.connect(self._place_picture)
            try:
                # A click on SingWS's main or audience window brings it in front of the picture: put the picture back at once, not at the next tick.
                from PyQt6.QtGui import QGuiApplication
                gui = QGuiApplication.instance()
                if gui is not None:
                    gui.focusWindowChanged.connect(lambda _window: self._place_picture() if self._frames_shown else None)
                    gui.applicationStateChanged.connect(lambda _state: self._place_picture() if self._frames_shown else None)
            except Exception:
                pass
        self._picture_timer.start()

    def _stop_frames(self) -> None:
        timer = self._picture_timer
        if timer is not None:
            timer.stop()
        self._picture_placed = None
        self._preview_placed = None
        try:
            if self.service is not None and self.service.web is not None:
                self.service.web.stop_screencast()
                self.service.web.window(False)
                self.service.web.window(False, id="preview")
        except Exception:
            pass
        if self._frames_shown:
            self._frames_shown = False
            vw = getattr(self.app, "video_window", None)
            if vw is not None:
                vw.idle = True


def _ns_window_number(widget) -> int:
    """The macOS window number of a Qt window (0 if it cannot be found): [[view window] windowNumber], so the picture window can be ordered directly over it."""
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        send.restype = ctypes.c_void_p
        window = send(int(widget.winId()), objc.sel_registerName(b"window"))
        if not window:
            return 0
        send.restype = ctypes.c_long
        return int(send(window, objc.sel_registerName(b"windowNumber")))
    except Exception:
        return 0
