"""Compatibility transport for SingWS's experimental macOS mpv engine."""

from __future__ import annotations

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
        self._loop = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)

    def start(self, start_seconds: float = 0.0):
        source = self.video_path if self.mode in {"cdg", "mp4"} else self.audio_path
        audio = self.audio_path if self.mode == "cdg" else None
        if not source:
            raise RuntimeError("mpv source path is empty")
        ok = self.plugin.loadSingWSMedia(
            source,
            audio,
            autoplay=True,
            semitones=int(round(self._pitch_semitones)),
            tempo_percent=int(round(self._tempo_ratio * 100.0)),
        )
        if not ok:
            raise RuntimeError(self.plugin.errorString() or "mpv media load failed")
        if float(start_seconds or 0.0) > 0.0:
            self.plugin.seekMedia(int(float(start_seconds) * 1000.0))
        self.plugin.setVolume(self._volume)
        self._stopped = False
        self._started_emitted = False
        self._ended_emitted = False
        self._playing_polls_without_visual = 0
        self._timer.start()

    def stop(self):
        self._stopped = True
        self._timer.stop()
        self.plugin.stopMedia()

    def seek(self, seconds: float):
        self.plugin.seekMedia(max(0, int(float(seconds) * 1000.0)))

    def pause(self):
        self.plugin.pauseMedia()

    def resume(self):
        self.plugin.playMedia()

    def is_paused(self) -> bool:
        # A coordinated seek pauses the audible engine while the video
        # followers catch up. That is not a user-visible pause state.
        try:
            if self.plugin.isSeekHolding():
                return False
        except AttributeError:
            pass
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
        if hasattr(self.plugin, "setPitchSemitones"):
            self.plugin.setPitchSemitones(self._pitch_semitones)

    def set_volume(self, value: float):
        self._volume = max(0.0, min(2.0, float(value)))
        self.plugin.setVolume(self._volume)

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
        if self._loop is not None and self.position_seconds() >= self._loop[1]:
            self.seek(self._loop[0])
        if not self._started_emitted:
            visuals_ready = True
            try:
                visuals_ready = bool(self.plugin.visualsReady())
            except Exception:
                pass
            playing = bool(self.plugin.isPlaying() and self.plugin.positionMs() >= 40)
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
        if not self._ended_emitted and self.plugin.atEnd():
            self._ended_emitted = True
            self._timer.stop()
            self.ended.emit()
