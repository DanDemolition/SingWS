"""High-quality CDG karaoke transport backed by libmpv.

Lab-only experiment. The desktop app does not select this for live playback:
field testing showed laggy show-screen startup and blurry output, and macOS
libmpv embedding can still abort inside AppKit. The motivation was video
quality: the GStreamer path decodes CDG to a 300x216 image and lets Qt upscale
it, while mpv can decode CDG through ffmpeg's cdgraphics decoder and scale with
its own resampler.

Design (validated against real CDG+MP3 pairs):

  * ONE libmpv instance. The MP3 is the main file (audio is the master clock,
    so a video hiccup can never freeze the audio/lyric sync), and the .cdg is
    attached as an external video track (video-add ... select, with the cdg
    demuxer forced only for that open).
  * Video is SOFTWARE-rendered into CPU RGBA buffers via libmpv's sw render
    API and emitted as QImage through frame_ready — exactly like the
    GStreamer transport — so the host's existing Qt compositing (ticker,
    request QR, preview window, next-up overlay, DAW snapshots) keeps working
    unchanged. Rendering at the output height means mpv does the heavy upscale
    with its resampler instead of Qt.
  * Audio DSP has the SAME scope as the GStreamer karaoke path: 10-band
    graphic EQ (mpv `equalizer` filters), loudness-normalization gain (mpv
    `volume`), key (mpv `rubberband` pitch-scale) and tempo (mpv `speed`,
    pitch-corrected). The master processor is NOT applied here — matching
    GstKaraokeTransport, which also does not run the master chain on karaoke.

Host-facing contract mirrors GstKaraokeTransport:
  signals  frame_ready(QImage), ended(), playback_hung()
  methods  start/stop/pause/resume/is_paused/seek/query_times_ns/
           set_modifiers/set_loop/clear_loop/set_video_offset_ms/
           set_visual_timer_interval_ms/cdg_sectors_remaining/
           cdg_generation/diagnostics
  attrs    max_video_height, eq, master, normalize_gain_db,
           last_level_db, last_level_ts
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QImage

NS_PER_SECOND = 1_000_000_000

# libmpv software-render param type IDs (render.h). python-mpv 1.0.8 predates
# these names in its param table, so we build the render-param array by hand.
_MPV_RENDER_PARAM_SW_SIZE = 17
_MPV_RENDER_PARAM_SW_FORMAT = 18
_MPV_RENDER_PARAM_SW_STRIDE = 19
_MPV_RENDER_PARAM_SW_POINTER = 20

# Same ISO 10-band centre frequencies as singws_eq.GraphicEQ.
_EQ_BANDS_HZ = (31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)

# Frame-rate cap for the CDG video track (see _attach_cdg_video): the
# cdgraphics decoder floods mpv with hundreds of fps and stalls presentation
# without this. CDG lyric highlights are low-motion, so 30 fps is ample.
CDG_VIDEO_FPS_CAP = 30

_STUP = 1.0594630943592952645618252949461  # semitone up ratio (OpenKJ)

_mpv = None
_MPV_IMPORT_ERROR = None
_SHARED_PLAYER = None
_SHARED_RENDER_CTX = None
_ACTIVE_TRANSPORT = None


def _diag(message: str) -> None:
    try:
        import logging
        logging.info(message)
    except Exception:
        try:
            print(message)
        except Exception:
            pass


def _libmpv_candidate_dirs() -> list[str]:
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(str(meipass))
        dirs.append(str(Path(meipass) / "lib"))
    # Homebrew (Apple Silicon / Intel) and common bundle spots.
    dirs += ["/opt/homebrew/lib", "/usr/local/lib"]
    try:
        dirs.append(str(Path(__file__).resolve().parent / "vendor" / "mpv"))
    except Exception:
        pass
    return [d for d in dirs if d]


def _preload_libmpv() -> None:
    names = ("libmpv.2.dylib", "libmpv.dylib", "libmpv.so.2", "libmpv.so", "mpv-2.dll", "libmpv-2.dll")
    for d in _libmpv_candidate_dirs():
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                try:
                    ctypes.CDLL(p, mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                    return
                except Exception:
                    continue


def _ensure_mpv():
    """Import python-mpv lazily, after making libmpv findable. Raises on any
    failure so the caller can fall back to the GStreamer transport."""
    global _mpv, _MPV_IMPORT_ERROR
    if _mpv is not None:
        return _mpv
    if not (
        os.environ.get("SINGWS_ENABLE_EXPERIMENTAL_MPV_CDG") == "1"
        or os.environ.get("SINGWS_RUN_MPV_INTEGRATION") == "1"
    ):
        raise RuntimeError("experimental mpv CDG renderer disabled")
    if os.environ.get("SINGWS_SKIP_MPV_INIT_FOR_TESTS"):
        raise RuntimeError("mpv init skipped for tests")
    try:
        # python-mpv resolves libmpv at import time via ctypes, so the search
        # path must be set BEFORE `import mpv`.
        extra = os.pathsep.join(_libmpv_candidate_dirs())
        if extra:
            for var in ("DYLD_FALLBACK_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH"):
                existing = os.environ.get(var, "")
                merged = extra + (os.pathsep + existing if existing else "")
                os.environ[var] = merged
        # Belt-and-suspenders for packaged builds: macOS often ignores DYLD_*
        # changes made after process start, so explicitly dlopen libmpv by full
        # path (RTLD_GLOBAL). Once loaded, python-mpv's by-name lookup resolves
        # to this handle. Best effort; harmless if it's already findable.
        _preload_libmpv()
        import mpv as _mpv_mod
        _mpv = _mpv_mod
        return _mpv
    except Exception as e:  # pragma: no cover - environment dependent
        _MPV_IMPORT_ERROR = e
        raise


def mpv_available() -> bool:
    """True if libmpv + the cdgraphics decoder can be loaded here."""
    try:
        _ensure_mpv()
        return True
    except Exception:
        return False


def pitch_scale_for_semitones(semitones: float) -> float:
    return math.pow(_STUP, float(semitones))


def _shared_on_end_file(event):
    # Runs on mpv's event thread. Dispatch to whichever transport currently
    # owns the shared embedded core; the transport only sets flags here.
    t = _ACTIVE_TRANSPORT
    if t is not None:
        try:
            t._on_end_file(event)
        except Exception:
            pass


class MpvCdgTransport(QObject):
    """libmpv-backed CDG transport. See module docstring."""

    frame_ready = pyqtSignal(QImage)
    ended = pyqtSignal()
    playback_hung = pyqtSignal()

    def __init__(
        self,
        audio_path: str,
        video_path: str | None = None,
        mode: str = "cdg",
        duration_seconds: float = 0.0,
        probe_duration_on_init: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.mpv = _ensure_mpv()
        self.audio_path = str(audio_path or "")
        self.video_path = str(video_path or "") or None
        self.mode = str(mode or "cdg").lower()
        self.duration_seconds = float(duration_seconds or 0.0)

        # Host-facing attributes (same names/semantics as GstKaraokeTransport).
        self.max_video_height = 720
        self.eq = None
        self.master = None
        self.last_level_db = None
        self.last_level_ts = 0.0
        self.tempo_ratio = 1.0
        self.semitones = 0.0
        self.video_offset_seconds = 0.0
        self.visual_timer_interval_ms = 15

        self._normalize_gain_db = 0.0
        self._eq_last_applied = None
        self._loop_bounds = None
        self._paused = False
        self._stopped = False
        self._eof = False
        self._eos_emitted = False
        self._pending_start_seconds = 0.0
        self._cdg_generation = 0
        self._render_w = 0
        self._render_h = 0
        self._frames_delivered = 0
        self._af_applied = None
        self._master_warned = False

        # Watchdog (OpenKJ stalled-player trigger).
        self._watchdog_last_pos_ms = -1
        self._watchdog_hung_cycles = 0
        self._watchdog_last_check = 0.0
        self._started_monotonic = 0.0

        self.player = None
        self._render_ctx = None
        self._render_param_t = None

        # CDG metadata (duration / sectors-remaining / final frame) reuses the
        # GStreamer transport's hardened reader adapter — for METADATA ONLY;
        # mpv does the actual rendering. Keeps end-silence-trim behaviour at
        # parity with the GStreamer path.
        self.cdg = None
        try:
            from gst_karaoke_transport import _CdgAdapter
            if self.video_path:
                self.cdg = _CdgAdapter(self.video_path)
                self.duration_seconds = max(self.duration_seconds, self.cdg.duration_seconds)
        except Exception as e:
            _diag(f"[MPV-CDG] metadata adapter unavailable: {e}")
            self.cdg = None

        self.timer = QTimer(self)
        self.timer.setInterval(self.visual_timer_interval_ms)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------- lifecycle
    def start(self, start_seconds: float = 0.0):
        global _SHARED_PLAYER, _SHARED_RENDER_CTX, _ACTIVE_TRANSPORT
        Mpv = self.mpv
        self._pending_start_seconds = max(0.0, float(start_seconds or 0.0))
        self._stopped = False
        self._paused = False
        self._eof = False
        self._eos_emitted = False
        self._started_monotonic = time.monotonic()

        # locale guard: libmpv needs LC_NUMERIC="C".
        try:
            import locale
            locale.setlocale(locale.LC_NUMERIC, "C")
        except Exception:
            pass

        opts = dict(
            vo="libmpv",
            keep_open="no",
            hwdec="no",              # CDG is tiny; software decode is trivial.
            background="color",      # blend CDG alpha onto black (real players do).
            audio_file_auto="no",
            # High-quality software scaling of the 300x216 CDG.
            sws_scaler="lanczos",
            cache="yes",
            demuxer_readahead_secs=10,
            loglevel="warn",
            log_handler=self._on_mpv_log,
            # Embedded/libmpv mode: SingWS owns all UI. On macOS, mpv's native
            # media-control/Touch Bar hooks can abort when repeated instances
            # are created inside a Qt process, so keep those platform hooks off.
            media_controls="no",
            input_media_keys="no",
            input_default_bindings="no",
            input_builtin_bindings="no",
            macos_app_activation_policy="prohibited",
        )
        # Audio output. There is NO mpv driver literally named "auto" — auto
        # selection is what happens when `ao` is left unset, so we must not pass
        # ao="auto" (that fails to initialize and cascades into the video-add
        # failing and the song ending instantly). Only the silent test mode
        # pins ao="null".
        if os.environ.get("SINGWS_MPV_SILENT"):
            opts["ao"] = "null"
        # macOS libmpv/AppKit can abort when multiple libmpv cores are created
        # and destroyed inside the same Qt process. Keep one embedded core and
        # hand ownership to the active transport for each song.
        if _SHARED_PLAYER is None:
            _SHARED_PLAYER = Mpv.MPV(**opts)
            try:
                _SHARED_PLAYER.event_callback("end-file")(_shared_on_end_file)
            except Exception as e:
                _diag(f"[MPV-CDG] end-file callback registration failed: {e}")
            # The libmpv VO only initializes once a render context exists, so it
            # MUST be created before loadfile — otherwise no video is decoded.
            _SHARED_RENDER_CTX = self.mpv.MpvRenderContext(_SHARED_PLAYER, "sw")
        self.player = _SHARED_PLAYER
        self._render_ctx = _SHARED_RENDER_CTX
        _ACTIVE_TRANSPORT = self

        try:
            self.player.command("stop")
        except Exception:
            pass

        # MP3 as main (audio master clock).
        self.player.loadfile(str(self.audio_path), mode="replace")
        # Wait (bounded) for the audio open so the cdg video-add attaches to the
        # right file and the clock exists.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if self._get("duration") is not None:
                break
            time.sleep(0.02)

        if self.video_path:
            self._attach_cdg_video()

        if self._pending_start_seconds > 0.0:
            self.seek(self._pending_start_seconds)

        self._apply_speed()
        self._apply_af(force=True)

        self.player.pause = False
        self.timer.start()
        _diag(
            f"[MPV-CDG] started mode={self.mode} start={self._pending_start_seconds:.3f}s "
            f"tempo={self.tempo_ratio:.3f} key={self.semitones:+.1f} "
            f"file={os.path.basename(self.audio_path)!r}"
        )

    def _attach_cdg_video(self):
        try:
            self.player["demuxer-lavf-format"] = "cdg"
        except Exception as e:
            _diag(f"[MPV-CDG] cdg format force failed: {e}")
        try:
            self.player.command("video-add", str(self.video_path), "select")
        except Exception as e:
            _diag(f"[MPV-CDG] cdg video-add failed: {e}")
        finally:
            try:
                self.player["demuxer-lavf-format"] = ""
            except Exception:
                pass
        # Cap the CDG video frame rate. ffmpeg's cdgraphics decoder emits a new
        # frame per graphics change — CDG streams 300 packets/sec, so the
        # opening lyric sweep floods mpv with ~hundreds of fps. mpv then drops
        # hundreds of frames and STALLS video presentation for ~5s early in the
        # song (audio keeps playing; the host's hung-watchdog then ends the
        # song). Capping to 30 fps — plenty for CDG's low-motion lyric
        # highlight — eliminates the flood: no drops, no stall, realtime.
        try:
            self.player["vf"] = f"fps={CDG_VIDEO_FPS_CAP}"
        except Exception as e:
            _diag(f"[MPV-CDG] fps-cap vf set failed: {e}")

    def stop(self):
        global _ACTIVE_TRANSPORT
        self._stopped = True
        try:
            self.timer.stop()
        except Exception:
            pass
        if _ACTIVE_TRANSPORT is self:
            _ACTIVE_TRANSPORT = None
        # The shared render context/player intentionally stay alive for process
        # lifetime; repeated libmpv core teardown is unsafe on macOS/AppKit.
        p = self.player
        self._render_ctx = None
        self.player = None
        if p is not None:
            try:
                p.command("stop")
            except Exception:
                pass

    def pause(self):
        self._paused = True
        if self.player is not None:
            try:
                self.player.pause = True
            except Exception:
                pass

    def resume(self):
        self._paused = False
        if self.player is not None:
            try:
                self.player.pause = False
            except Exception:
                pass

    def is_paused(self) -> bool:
        return bool(self._paused)

    def seek(self, seconds: float):
        seconds = max(0.0, float(seconds or 0.0))
        if self.player is not None:
            try:
                self.player.command("seek", f"{seconds:.3f}", "absolute+exact")
            except Exception as e:
                _diag(f"[MPV-CDG] seek failed: {e}")
        self._cdg_generation += 1
        if self.cdg is not None:
            try:
                self.cdg.seek_seconds(seconds)
            except Exception:
                pass

    # -------------------------------------------------------------- position
    def position_seconds(self) -> float:
        pos = self._get("time-pos")
        try:
            return float(pos) if pos is not None else -1.0
        except Exception:
            return -1.0

    def query_times_ns(self):
        """(duration_ns, position_ns) — GstKaraokeTransport's order, which the
        host's progress bar and seek mapping rely on."""
        dur = self._get("duration")
        pos = self._get("time-pos")
        try:
            dur_ns = int(float(dur) * NS_PER_SECOND) if dur else None
        except Exception:
            dur_ns = None
        if not dur_ns and self.duration_seconds:
            dur_ns = int(self.duration_seconds * NS_PER_SECOND)
        try:
            pos_ns = int(float(pos) * NS_PER_SECOND) if pos is not None else None
        except Exception:
            pos_ns = None
        return dur_ns, pos_ns

    # ----------------------------------------------------------- modifiers
    def set_modifiers(self, tempo_ratio: float, semitones: float):
        tempo_ratio = max(0.5, min(2.0, float(tempo_ratio or 1.0)))
        semitones = max(-24.0, min(24.0, float(semitones or 0.0)))
        tempo_changed = abs(tempo_ratio - self.tempo_ratio) > 1e-6
        key_changed = abs(semitones - self.semitones) > 1e-6
        self.tempo_ratio = tempo_ratio
        self.semitones = semitones
        if tempo_changed:
            self._apply_speed()
        if key_changed:
            self._apply_af()
        if key_changed or tempo_changed:
            _diag(
                f"[MPV-CDG] modifiers tempo={tempo_ratio:.3f} key={semitones:+.1f} "
                f"key_path=rubberband_pitch tempo_path=mpv_speed"
            )

    def _apply_speed(self):
        if self.player is None:
            return
        try:
            self.player["speed"] = float(self.tempo_ratio)
        except Exception as e:
            _diag(f"[MPV-CDG] speed set failed: {e}")

    def _build_af(self) -> str:
        parts = []
        eq = self.eq
        try:
            if eq is not None and eq.enabled() and not eq.is_flat():
                gains = list(eq.gains_db())
                for freq, gain in zip(_EQ_BANDS_HZ, gains):
                    g = max(-24.0, min(24.0, float(gain)))
                    if abs(g) > 1e-3:
                        parts.append(f"equalizer=f={freq}:t=o:w=1:g={g:.2f}")
        except Exception:
            pass
        gain_db = float(self._normalize_gain_db or 0.0)
        if abs(gain_db) > 0.05:
            parts.append(f"volume=volume={gain_db:.2f}dB")
        if abs(self.semitones) > 1e-6:
            parts.append(f"rubberband=pitch-scale={pitch_scale_for_semitones(self.semitones):.6f}")
        return ",".join(parts)

    def _apply_af(self, force: bool = False):
        if self.player is None:
            return
        af = self._build_af()
        if not force and af == self._af_applied:
            return
        self._af_applied = af
        try:
            self.player["af"] = af
        except Exception as e:
            _diag(f"[MPV-CDG] af set failed ({af[:60]}...): {e}")

    def _mirror_eq(self):
        """Rebuild the af when the host's GraphicEQ changes (cheap gains
        compare), mirroring GstKaraokeTransport._mirror_eq."""
        eq = self.eq
        try:
            if eq is None:
                gains = None
            else:
                gains = list(eq.gains_db()) if eq.enabled() else None
        except Exception:
            gains = None
        target = gains if gains is not None else [0.0] * len(_EQ_BANDS_HZ)
        if self._eq_last_applied == target:
            return
        self._eq_last_applied = list(target)
        self._apply_af()

    # -------------------------------------------------------- gain staging
    @property
    def normalize_gain_db(self) -> float:
        return self._normalize_gain_db

    @normalize_gain_db.setter
    def normalize_gain_db(self, db):
        try:
            self._normalize_gain_db = float(db or 0.0)
        except Exception:
            self._normalize_gain_db = 0.0
        self._apply_af()

    # ---------------------------------------------------------------- loops
    def set_loop(self, start, end):
        try:
            a, b = float(start), float(end)
        except Exception:
            return
        if b > a >= 0.0:
            self._loop_bounds = (a, b)

    def clear_loop(self):
        self._loop_bounds = None

    # ------------------------------------------------------------------ CDG
    def cdg_sectors_remaining(self):
        if self.cdg is None:
            return None
        pos = self.position_seconds()
        if pos < 0:
            return None
        try:
            return self.cdg.sectors_remaining(pos)
        except Exception:
            return None

    def cdg_generation(self):
        return self._cdg_generation

    # ------------------------------------------------------------- offsets
    def set_video_offset_ms(self, ms) -> None:
        # mpv keeps A/V in sync internally, so the CDG present offset the
        # GStreamer path uses does not apply; store it for diagnostics only.
        try:
            self.video_offset_seconds = float(ms) / 1000.0
        except Exception:
            self.video_offset_seconds = 0.0

    def set_visual_timer_interval_ms(self, interval_ms: int):
        try:
            interval = max(15, min(80, int(interval_ms)))
        except Exception:
            interval = 15
        self.visual_timer_interval_ms = interval
        try:
            self.timer.setInterval(interval)
        except Exception:
            pass

    # ------------------------------------------------------ mpv callbacks
    def _on_mpv_log(self, level, prefix, text):
        try:
            _diag(f"[MPV-CDG/{prefix}] {level}: {str(text).rstrip()}")
        except Exception:
            pass

    def _on_end_file(self, event):
        # Runs on mpv's event thread: only set a flag; _tick (GUI thread)
        # emits the Qt signal.
        try:
            reason = getattr(getattr(event, "data", None), "reason", None)
            reason = int(reason) if reason is not None else None
        except (TypeError, ValueError):
            reason = None
        # 0 = EOF, 4 = ERROR (stop/replace do not count as a real end).
        if reason in (0, 4):
            self._eof = True

    def _get(self, name):
        p = self.player
        if p is None:
            return None
        try:
            return p._get_property(name)
        except Exception:
            return None

    # ---------------------------------------------------------------- tick
    def _tick(self):
        if self._stopped or self.player is None:
            return
        if self._eof and not self._eos_emitted:
            self._eos_emitted = True
            _diag("[MPV-CDG] end-of-file")
            self.ended.emit()
            return

        pos = self.position_seconds()
        if pos >= 0 and self._loop_bounds is not None:
            a, b = self._loop_bounds
            if pos >= b:
                self.seek(a)

        self._render_frame_if_ready()
        self._mirror_eq()
        self._watchdog(pos)

    def _target_render_size(self):
        vw = self._get("video-params/w") or self._get("width")
        vh = self._get("video-params/h") or self._get("height")
        try:
            vw = int(vw); vh = int(vh)
        except Exception:
            vw, vh = 300, 216
        if vw <= 0 or vh <= 0:
            vw, vh = 300, 216
        # Cap at ~4K height. The host drives max_video_height from the physical
        # (device-pixel-ratio-aware) output height so the on-screen result is a
        # sharp 1:1 on HiDPI/Retina rather than a soft upscale of a 1080p frame.
        h = max(240, min(2160, int(self.max_video_height or 720)))
        w = int(round(h * vw / vh))
        w += w % 2
        return w, h

    def _render_frame_if_ready(self):
        ctx = self._render_ctx
        if ctx is None:
            return
        try:
            if not ctx.update():
                return
        except Exception:
            pass
        w, h = self._target_render_size()
        if w <= 0 or h <= 0:
            return
        stride = w * 4
        buf = (ctypes.c_char * (stride * h))()
        size_arr = (ctypes.c_int * 2)(w, h)
        stride_val = ctypes.c_size_t(stride)
        fmt = ctypes.c_char_p(b"rgb0")
        MpvRenderParam = self.mpv.MpvRenderParam
        params = (MpvRenderParam * 5)()
        params[0].type_id = _MPV_RENDER_PARAM_SW_SIZE
        params[0].data = ctypes.cast(size_arr, ctypes.c_void_p)
        params[1].type_id = _MPV_RENDER_PARAM_SW_FORMAT
        params[1].data = ctypes.cast(fmt, ctypes.c_void_p)
        params[2].type_id = _MPV_RENDER_PARAM_SW_STRIDE
        params[2].data = ctypes.cast(ctypes.byref(stride_val), ctypes.c_void_p)
        params[3].type_id = _MPV_RENDER_PARAM_SW_POINTER
        params[3].data = ctypes.cast(buf, ctypes.c_void_p)
        params[4].type_id = 0
        params[4].data = None
        try:
            self.mpv._mpv_render_context_render(ctx._handle, params)
        except Exception as e:
            _diag(f"[MPV-CDG] render failed: {e}")
            return
        # rgb0 (little-endian) maps to Qt's RGBX8888. Copy so the QImage owns
        # its bytes after buf is freed.
        image = QImage(bytes(buf), w, h, stride, QImage.Format.Format_RGBX8888).copy()
        self._render_w, self._render_h = w, h
        self._frames_delivered += 1
        self.frame_ready.emit(image)

    def _watchdog(self, pos: float):
        now = time.monotonic()
        if now - self._watchdog_last_check < 1.0:
            return
        self._watchdog_last_check = now
        if self._paused or self._stopped or self._eos_emitted:
            self._watchdog_hung_cycles = 0
            return
        pos_ms = int(pos * 1000) if pos >= 0 else -1
        if pos_ms == self._watchdog_last_pos_ms and pos_ms > 10:
            self._watchdog_hung_cycles += 1
            if self._watchdog_hung_cycles >= 5:
                self._watchdog_hung_cycles = 0
                _diag("[MPV-CDG] playback hung (position frozen ~5s)")
                self.playback_hung.emit()
        else:
            self._watchdog_hung_cycles = 0
        self._watchdog_last_pos_ms = pos_ms

    # ---------------------------------------------------------- diagnostics
    def diagnostics(self) -> dict:
        return {
            "engine": "mpv",
            "mode": self.mode,
            "tempo_ratio": self.tempo_ratio,
            "semitones": self.semitones,
            "normalize_gain_db": self._normalize_gain_db,
            "af": self._af_applied,
            "render_size": f"{self._render_w}x{self._render_h}",
            "frames_delivered": self._frames_delivered,
            "position_s": self.position_seconds(),
            "duration_s": self.duration_seconds,
        }
