"""Compatibility transport for SingWS's experimental macOS mpv engine."""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget


class MpvVideoHost(QWidget):
    """Stable native QWidget used as the parent for an mpv Metal window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        # Qt promotes every ANCESTOR of a native widget to native as well
        # unless this is set. Without it, creating this host silently turned
        # video_area and the whole VideoWindow into native Cocoa windows --
        # exactly the nested native backing stores the video-window code already
        # names as "the common factor in the Intel macOS
        # QPaintDevice::devicePixelRatio() crashes", and why it is careful never
        # to call video_area.winId(). It then promoted them through here anyway.
        #
        # 13 of the 15 packaged-app crash reports on this machine are that one
        # null dereference inside QBackingStore::flush, spanning v0.4.3.4
        # through v0.4.3.7, and the promotion is permanent for the session --
        # which is why one of them happened while the app sat idle between
        # singers with nothing playing at all.
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setStyleSheet("background: black;")


class MpvKaraokeTransport(QObject):
    """Expose the current transport API while mpv owns audio and video."""

    started = pyqtSignal()
    ended = pyqtSignal()
    playback_hung = pyqtSignal()
    visual_stalled = pyqtSignal(str, int)

    def __init__(
        self,
        plugin,
        *,
        audio_path: str,
        video_path: str | None,
        mode: str,
        parent=None,
    ):
        super().__init__(parent)
        self.plugin = plugin
        self.audio_path = str(audio_path or "")
        self.video_path = str(video_path or "")
        self.mode = str(mode or "audio").lower()
        self.duration_seconds = 0.0
        self.start_delay_ms = 0
        self.max_video_height = 0
        self.eq = None
        self.master = None
        self.normalize_gain_db = 0.0
        self._tempo_ratio = 1.0
        self._pitch_semitones = 0.0
        self._video_offset_warned = False
        self._volume = 1.0
        self._stopped = False
        self._started_emitted = False
        self._ended_emitted = False
        self._playing_polls_without_visual = 0
        self._visual_stall_emitted = False
        self._loop = None
        # Rust karaoke audio (roadmap item 3): mpv stays the picture master with its audio on a null output, and the Rust voice is slaved to
        # mpv's clock. None = mpv plays the sound itself, exactly as before.
        self._voice = None
        self._voice_log = print
        self._voice_norm = 1.0
        self._voice_phase = ""          # "" | "starting" | "voice_wait" | "seeking" | "running"
        self._voice_deadline = 0.0
        self._voice_origin = 0.0
        self._user_paused = False
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self._vtimer = QTimer(self)      # fast steps while the picture and the voice are being started together
        self._vtimer.setInterval(10)
        self._vtimer.timeout.connect(self._voice_step)

    def attach_voice(self, voice, log=print):
        """Use the Rust karaoke voice for the SOUND of this song (call before start()). The picture stays on mpv."""
        self._voice = voice
        self._voice_log = log or print

    def voice_active(self) -> bool:
        return self._voice is not None

    def _drop_voice(self, reason: str):
        """Give the song back to mpv's own audio (the voice could not start or broke): never leave a show without sound."""
        voice, self._voice = self._voice, None
        self._voice_phase = ""
        self._vtimer.stop()
        try:
            self._voice_log(f"[KARAOKE-RUST] falling back to mpv audio for this song: {reason}")
        except Exception:
            pass
        if voice is not None:
            try:
                voice.stop()
            except Exception:
                pass
        try:
            self.plugin.setShadowAudio(False)
        except Exception:
            pass

    def _voice_load(self, start_seconds: float) -> bool:
        voice = self._voice
        path = self.audio_path or self.video_path
        try:
            voice.load(path, norm_gain=self._voice_norm, paused=True, volume=self._volume)
            voice.set_key_semitones(self._pitch_semitones)
            voice.set_tempo_percent(self._tempo_ratio * 100.0)
            if float(start_seconds or 0.0) > 0.0:
                voice.seek_seconds(float(start_seconds))
            return True
        except Exception as exc:
            self._drop_voice(f"could not open {path!r}: {exc}")
            return False

    def start(self, start_seconds: float = 0.0):
        source = self.video_path if self.mode in {"cdg", "mp4"} else self.audio_path
        audio = self.audio_path if self.mode == "cdg" else None
        if not source:
            raise RuntimeError("mpv source path is empty")
        voice_on = False
        if self._voice is not None:
            voice_on = self._voice_load(start_seconds)
        self._user_paused = False
        ok = self.plugin.loadSingWSMedia(
            source,
            audio,
            autoplay=not voice_on,
            semitones=int(round(self._pitch_semitones)),
            tempo_percent=int(round(self._tempo_ratio * 100.0)),
        )
        if not ok:
            if voice_on:
                self._drop_voice("the picture failed to load")
            raise RuntimeError(self.plugin.errorString() or "mpv media load failed")
        if voice_on:
            self._voice_phase = "starting"
            self._voice_deadline = time.monotonic() + 4.0
            self._vtimer.start()
        if float(start_seconds or 0.0) > 0.0:
            self.plugin.seekMedia(int(float(start_seconds) * 1000.0))
        self.plugin.setVolume(self._volume)
        self._stopped = False
        self._started_emitted = False
        self._ended_emitted = False
        self._playing_polls_without_visual = 0
        self._visual_stall_emitted = False
        self._timer.start()

    def stop(self):
        self._stopped = True
        self._timer.stop()
        self._vtimer.stop()
        self._voice_phase = ""
        if self._voice is not None:
            try:
                self._voice.stop()
            except Exception:
                pass
        self.plugin.stopMedia()

    def seek(self, seconds: float):
        target = max(0.0, float(seconds))
        if self._voice is not None and self._voice_phase in ("running", "seeking", "voice_wait"):
            # Pause both, move both, and start them together again once the voice's source is ready (it takes a moment to refill).
            try:
                self._voice.pause()
                self.plugin.pauseMedia()
                self.plugin.seekMedia(int(target * 1000.0))
                self._voice.seek_seconds(target)
            except Exception as exc:
                self._drop_voice(f"seek failed: {exc}")
                self.plugin.playMedia()
                return
            self._voice_phase = "seeking"
            self._voice_deadline = time.monotonic() + 3.0
            self._vtimer.start()
            return
        self.plugin.seekMedia(int(target * 1000.0))

    def pause(self):
        self._user_paused = True
        self.plugin.pauseMedia()
        if self._voice is not None:
            try:
                self._voice.pause()
            except Exception:
                pass

    def resume(self):
        self._user_paused = False
        if self._voice is not None and self._voice_phase == "running":
            try:
                self._voice.play()
            except Exception:
                pass
        if self._voice is not None and self._voice_phase in ("starting", "voice_wait", "seeking"):
            return  # the start sequence is still running; it will play both when it is ready
        self.plugin.playMedia()

    def is_paused(self) -> bool:
        # A coordinated seek pauses the audible engine while the video
        # followers catch up. That is not a user-visible pause state.
        try:
            if self.plugin.isSeekHolding():
                return False
        except AttributeError:
            pass
        if self._voice is not None and self._voice_phase in ("starting", "voice_wait", "seeking") and not self._user_paused:
            return False
        return not bool(self.plugin.isPlaying())

    def position_seconds(self) -> float:
        return max(0.0, float(self.plugin.positionMs()) / 1000.0)

    def query_times_ns(self):
        duration = int(self.plugin.durationMs())
        position = int(self.plugin.positionMs())
        return (
            duration * 1_000_000 if duration > 0 else None,
            position * 1_000_000 if position >= 0 else None,
        )

    def engine_at_end(self):
        """mpv's own eof-reached, for the host's end-of-song fallbacks.

        Those fallbacks otherwise gate on an audio level tap this engine does
        not have, so they treated "no metering" as "silent" and could stop a
        song whose audio was still playing. Returns None if the plugin cannot
        answer, which the host reads as "unknown, use the old behaviour".
        """
        try:
            fn = getattr(self.plugin, "atEnd", None)
            return bool(fn()) if callable(fn) else None
        except Exception:
            return None

    def set_modifiers(self, tempo_ratio: float, semitones: float):
        self._tempo_ratio = max(0.5, min(2.0, float(tempo_ratio or 1.0)))
        self._pitch_semitones = float(semitones or 0.0)
        if hasattr(self.plugin, "setTempoRatio"):
            self.plugin.setTempoRatio(self._tempo_ratio)
        if self._voice is not None:
            # the sound is the voice's: key and tempo are two separate inputs there. mpv keeps only the tempo (its clock paces the picture).
            try:
                self._voice.set_key_semitones(self._pitch_semitones)
                self._voice.set_tempo_percent(self._tempo_ratio * 100.0)
            except Exception:
                pass
        elif hasattr(self.plugin, "setPitchSemitones"):
            self.plugin.setPitchSemitones(self._pitch_semitones)

    def set_volume(self, value: float):
        self._volume = max(0.0, min(2.0, float(value)))
        self.plugin.setVolume(self._volume)
        if self._voice is not None:
            try:
                self._voice.set_volume(self._volume)
            except Exception:
                pass

    def apply_audio_spec(self, spec: dict):
        """The host's audio chain (normalization, karaoke EQ, master chain) for the Rust voice. mpv's own `af` chain is not used in this mode."""
        voice = self._voice
        if voice is None or not isinstance(spec, dict):
            return
        try:
            gain_db = float(spec.get("normalize_gain_db", 0.0) or 0.0)
            self._voice_norm = 10.0 ** (gain_db / 20.0)
            voice.set_norm_gain(self._voice_norm)
            voice.set_eq_gains(bool(spec.get("eq_enabled")), spec.get("eq_gains_db") or [])
            params = spec.get("master_params")
            if params:
                # the host passes the stage settings it derived; the chain needs every parameter, so fill the rest from the defaults
                from singws_master_audio import DEFAULT_PARAMS
                params = {**DEFAULT_PARAMS, **dict(params)}
            voice.set_master_params(bool(spec.get("master_enabled")), params)
        except Exception as exc:
            try:
                self._voice_log(f"[KARAOKE-RUST] audio chain not applied: {exc}")
            except Exception:
                pass

    def _voice_step(self):
        """Start the picture and the sound together (at the beginning of a song and after a seek): the voice first, because its sound takes
        a moment to come out (the stretcher's own delay), then the picture as soon as the voice's clock begins to move."""
        voice = self._voice
        if voice is None or self._stopped or not self._voice_phase or self._voice_phase == "running":
            self._vtimer.stop()
            return
        now = time.monotonic()
        try:
            if self._user_paused:
                return
            if self._voice_phase in ("starting", "seeking"):
                loaded = int(self.plugin.durationMs()) > 0
                if loaded and voice.is_ready():
                    self._voice_origin = voice.position_seconds()
                    voice.slave_to(self.position_seconds(), 0.0)
                    voice.play()
                    self._voice_phase = "voice_wait"
                    self._voice_deadline = now + 0.7
                elif now > self._voice_deadline:
                    self._drop_voice("the voice was not ready in time")
                    self.plugin.playMedia()
            elif self._voice_phase == "voice_wait":
                if voice.position_seconds() > self._voice_origin + 0.002 or now > self._voice_deadline:
                    self.plugin.playMedia()
                    self._voice_phase = "running"
                    self._vtimer.stop()
        except Exception as exc:
            self._drop_voice(f"start sequence failed: {exc}")
            try:
                self.plugin.playMedia()
            except Exception:
                pass

    def _voice_follow(self):
        """Report the picture's clock to the voice (which extrapolates between reports and trims its own tempo to stay on it)."""
        voice = self._voice
        if voice is None or self._voice_phase != "running":
            return
        try:
            playing = bool(self.plugin.isPlaying())
            voice.slave_to(self.position_seconds(), self._tempo_ratio if playing else 0.0)
        except Exception:
            pass

    def set_loop(self, start_seconds: float, end_seconds: float):
        start = max(0.0, float(start_seconds))
        end = max(start, float(end_seconds))
        self._loop = (start, end) if end > start else None

    def set_visual_timer_interval_ms(self, value: int):
        self._timer.setInterval(max(16, min(250, int(value or 50))))

    def set_video_offset_ms(self, value: int):
        """CDG visual-timing calibration, forwarded when the backend can apply it.

        This used to be an unconditional no-op, which is why CDG lyrics ran
        about 750ms out on mpv and the Display tab's fine tuning had no effect:
        the host called this on every song start and on every slider move, and
        it silently discarded the value.

        The in-process (IINA) backend owns both audio and video in one core, so
        it maps this straight onto mpv's ``audio-delay``. The follower-based
        backend still cannot -- there mpv chases SingWS's audio clock and has
        nothing to offset -- so it keeps the old behaviour, but says so.
        """
        try:
            plugin = self.plugin
        except Exception:
            plugin = None
        if plugin is not None and hasattr(plugin, "setVideoOffsetMs"):
            plugin.setVideoOffsetMs(value)
            return
        try:
            warned = self._video_offset_warned
        except Exception:
            warned = False
        if int(value or 0) and not warned:
            try:
                self._video_offset_warned = True
            except Exception:
                pass
            print(
                f"[MPV] WARNING this backend cannot apply the {int(value):+d}ms "
                "CDG visual offset; lyrics will run early/late and the Display "
                "tab fine tuning has no effect"
            )

    def fade_out(self, duration_ms: int, on_finished=None) -> bool:
        duration_ms = max(0, int(duration_ms or 0))
        if self._voice is not None and duration_ms > 0:
            # one smooth slide inside the engine instead of 40 ms steps from here
            try:
                self._voice.slide_volume(0.0, duration_ms)
                self._volume = 0.0
                if callable(on_finished):
                    QTimer.singleShot(duration_ms, on_finished)
                return True
            except Exception:
                pass
        if duration_ms <= 0:
            self.set_volume(0.0)
            if callable(on_finished):
                QTimer.singleShot(0, on_finished)
            return True
        start_volume = self._volume
        steps = max(1, duration_ms // 40)
        state = {"step": 0}
        timer = QTimer(self)
        timer.setInterval(max(10, duration_ms // steps))

        def tick():
            state["step"] += 1
            remaining = max(0.0, 1.0 - state["step"] / steps)
            self.set_volume(start_volume * remaining)
            if state["step"] >= steps:
                timer.stop()
                timer.deleteLater()
                if callable(on_finished):
                    on_finished()

        timer.timeout.connect(tick)
        timer.start()
        return True

    def cdg_sectors_remaining(self):
        return None

    def cdg_generation(self):
        return None

    def _poll(self):
        if self._stopped:
            return
        self._voice_follow()
        if self._loop is not None and self.position_seconds() >= self._loop[1]:
            self.seek(self._loop[0])
        visuals_ready = True
        try:
            visuals_ready = bool(self.plugin.visualsReady())
        except Exception:
            pass
        playing = bool(self.plugin.isPlaying() and self.plugin.positionMs() >= 40)
        if not self._started_emitted:
            if playing and not visuals_ready:
                self._playing_polls_without_visual += 1
            else:
                self._playing_polls_without_visual = 0
            # Playback identity and queue state follow the audible clock. A
            # native drawable that is late (or temporarily detached while the
            # show window opens) must not leave Play locked in "starting"
            # forever after sound has begun. At the normal 50ms interval this
            # bounded fallback commits after one second; the host still keeps
            # the video surface hidden until its independent readiness path.
            visual_timeout = self._playing_polls_without_visual >= max(
                4, int(round(1000 / max(1, self._timer.interval())))
            )
            if playing and (visuals_ready or visual_timeout):
                self._started_emitted = True
                self.started.emit()
        # A CDG can have healthy audio while the graphics demux/render path has
        # failed.  Detect that independently of the one-second audible-start
        # fallback so the host never reveals a permanently black native child.
        # This is deliberately report-only: reloading the shared mpv core here
        # would interrupt the singer's audio.  The host keeps the last safe
        # painted/background surface visible and gives the operator a warning.
        if (
            self.mode == "cdg"
            and playing
            and not visuals_ready
            and not self._visual_stall_emitted
            and int(self.plugin.positionMs()) >= 5000
        ):
            self._visual_stall_emitted = True
            self.visual_stalled.emit(self.video_path, int(self.plugin.positionMs()))
        if not self._ended_emitted and self.plugin.atEnd():
            self._ended_emitted = True
            self._timer.stop()
            self._vtimer.stop()
            if self._voice is not None:
                try:
                    self._voice.stop()
                except Exception:
                    pass
            self.ended.emit()
