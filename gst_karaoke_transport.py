"""GStreamer-backed karaoke transport (OpenKJ architecture port).

Drop-in replacement for PythonKaraokeTransport built on the OpenKJ media
chain (see okj_audio_backend.py / OKJ_INTEGRATION.md):

  * ALL audio DSP runs in native GStreamer elements — key change via the
    SoundTouch ``pitch`` element (a property set, zero cost live), speed via
    ``scaletempo`` + flushing rate seeks (INSTANT_RATE_CHANGE is deliberately
    NOT used — see _apply_tempo_rate), EQ via ``equalizer-10bands``.
    No PCM ever flows through Python.
  * CDG frames come from cdg_native.CdgFileReader (hardened libCDG port,
    tolerant of damaged rips): change-driven (no pixel change -> no frame ->
    no render), presented against the pipeline clock, and emitted as
    Indexed8 QImages so palette expansion happens in Qt's C code.
  * MP4 video decodes through GStreamer (VideoToolbox via vtdec where
    available) into an appsink; frames are paced by the pipeline clock
    (appsink sync=True) and emitted as QImage via frame_ready, so the
    existing video windows and DAW preview keep working unchanged.
  * RMS level for the host's end-silence logic comes from the ``level``
    element (last_level_db / last_level_ts, same fields the host already
    reads). The DECISION logic stays in the host.
  * A position watchdog emits playback_hung when the pipeline is PLAYING
    but the clock freezes for ~5s (OpenKJ's stalled-player trigger).

The GStreamer bus is POLLED from the Qt visual timer tick — no GLib main
loop is run, so there is no Qt/GLib event-loop bridging to go wrong.

Host-facing contract matches PythonKaraokeTransport:
  signals  frame_ready(QImage), ended()  (+ new: playback_hung())
  methods  start/stop/pause/resume/is_paused/seek/query_times_ns/
           set_modifiers/set_loop/clear_loop/set_video_offset_ms/
           set_visual_timer_interval_ms/cdg_sectors_remaining/
           cdg_generation/diagnostics
  attrs    max_video_height, eq, master, normalize_gain_db,
           last_level_db, last_level_ts
"""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QImage

NS_PER_SECOND = 1_000_000_000

_GST = None
_GST_IMPORT_ERROR = None


def _repo_soundtouch_plugin_dir() -> str | None:
    """The self-built soundtouch plugin (pitch element) for Homebrew GStreamer.

    Homebrew's monolithic gstreamer formula ships without soundtouch; the
    official GStreamer.framework (bundled x86_64/universal builds) has it.
    See native/gst-soundtouch/build_gst_soundtouch.sh.
    """
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parent / "native" / "gst-soundtouch")
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "gst-soundtouch")
    for c in candidates:
        try:
            if (c / "libgstsoundtouch.dylib").exists():
                return str(c)
        except Exception:
            pass
    return None


def _ensure_gst():
    """Import + init GStreamer lazily so importing this module is harmless in
    tests (SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS) and on hosts without gi."""
    global _GST, _GST_IMPORT_ERROR
    if _GST is not None:
        return _GST
    if os.environ.get("SINGWS_SKIP_GSTREAMER_INIT_FOR_TESTS"):
        raise RuntimeError("GStreamer init skipped for tests")
    try:
        plugin_dir = _repo_soundtouch_plugin_dir()
        if plugin_dir:
            existing = os.environ.get("GST_PLUGIN_PATH", "")
            if plugin_dir not in existing.split(os.pathsep):
                os.environ["GST_PLUGIN_PATH"] = (
                    plugin_dir + (os.pathsep + existing if existing else "")
                )
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        if not Gst.is_initialized():
            Gst.init(None)
        _GST = Gst
        return Gst
    except Exception as e:  # pragma: no cover - environment dependent
        _GST_IMPORT_ERROR = e
        raise


# Semitone ratios, same constants OpenKJ uses (STUP / STDN).
_STUP = 1.0594630943592952645618252949461
_STDN = 0.94387431268169349664191315666784


def pitch_ratio_for_semitones(semitones: float) -> float:
    if semitones > 0:
        return math.pow(_STUP, semitones)
    if semitones < 0:
        return 1 - ((100 - (math.pow(_STDN, abs(semitones)) * 100)) / 100)
    return 1.0


def optimize_scaletempo_for_rate(scaletempo, rate: float) -> None:
    """SoundTouch TDStretch auto-tuning curve (OpenKJ gstreamerhelper.cpp)."""
    AUTOSEQ_TEMPO_LOW, AUTOSEQ_TEMPO_TOP = 0.5, 2.0
    AUTOSEQ_AT_MIN, AUTOSEQ_AT_MAX = 90.0, 40.0
    AUTOSEEK_AT_MIN, AUTOSEEK_AT_MAX = 20.0, 15.0
    seq_k = (AUTOSEQ_AT_MAX - AUTOSEQ_AT_MIN) / (AUTOSEQ_TEMPO_TOP - AUTOSEQ_TEMPO_LOW)
    seq_c = AUTOSEQ_AT_MIN - seq_k * AUTOSEQ_TEMPO_LOW
    seek_k = (AUTOSEEK_AT_MAX - AUTOSEEK_AT_MIN) / (AUTOSEQ_TEMPO_TOP - AUTOSEQ_TEMPO_LOW)
    seek_c = AUTOSEEK_AT_MIN - seek_k * AUTOSEQ_TEMPO_LOW
    seq = max(AUTOSEQ_AT_MAX, min(AUTOSEQ_AT_MIN, seq_c + seq_k * rate))
    seek = max(AUTOSEEK_AT_MAX, min(AUTOSEEK_AT_MIN, seek_c + seek_k * rate))
    scaletempo.set_property("stride", int(seq + 0.5))
    scaletempo.set_property("search", int(seek + 0.5))


def _diag(message: str) -> None:
    try:
        import logging

        logging.info(message)
    except Exception:
        try:
            print(message)
        except Exception:
            pass


class _CdgAdapter:
    """Wraps cdg_native.CdgFileReader (hardened, dependency-free libCDG port)
    with the generation/sectors surface the host expects.

    Frames come out as Format_Indexed8 QImages with a 16-entry color table —
    Qt expands the palette in C when painting, so Python never touches
    individual pixels on the render path."""

    def __init__(self, path: str, sidefill: bool = False):
        from cdg_native import (
            CdgFileReader,
            CDG_PACKETS_PER_SECOND,
            CROP_W,
            CROP_H,
        )

        self.reader = CdgFileReader(path, sidefill=bool(sidefill))
        self.packets_per_second = int(CDG_PACKETS_PER_SECOND)
        self._crop_w = int(getattr(self.reader, "width", CROP_W))
        self._crop_h = int(getattr(self.reader, "height", CROP_H))
        self.sidefill = bool(sidefill)
        self.generation = 0
        self.duration_seconds = self.reader.total_duration_ms() / 1000.0
        self._presented_idx = -1
        self._cached_image_idx = -1
        self._cached_image = QImage()
        # Optional diagnostics (off by default): SINGWS_CDG_DEBUG=1 rate-limits
        # a once-per-second publish summary; SINGWS_CDG_SNAPSHOT_DIR saves each
        # published native frame as PNG for offline artifact comparison.
        self._debug = os.environ.get("SINGWS_CDG_DEBUG", "").strip() == "1"
        self._snapshot_dir = os.environ.get("SINGWS_CDG_SNAPSHOT_DIR", "").strip()
        self._dbg_last_log = 0.0
        self._dbg_published = 0

    def seek_seconds(self, seconds: float):
        self.reader.seek(int(max(0.0, seconds) * 1000))
        self.generation += 1
        self._presented_idx = -1
        self._cached_image_idx = -1
        self._cached_image = QImage()
        if self._debug:
            _diag(
                f"[CDG-DEBUG] seek rebuild to {seconds:.3f}s "
                f"packet={self.reader._next_idx} gen={self.generation}"
            )

    def sectors_remaining(self, seconds: float) -> float:
        n = self.reader._total_packets
        packet = max(0, min(n, int(seconds * self.packets_per_second)))
        return max(0.0, (n - packet) / 4.0)

    def frame_for_position_ms(self, pos_ms: int, force: bool = False):
        """Apply every CDG packet due at or before pos_ms; return a new
        Indexed8 QImage only when the visible frame actually changed unless
        force=True.

        Root cause note (2026-07-10): this path must NEVER present decoder
        state from beyond pos_ms. It previously stepped move_to_next_frame(),
        an iterator that decodes ahead to the NEXT visible change and stamps
        frames with a one-display-tick (16.7ms) duration — a contract meant
        for the appsrc path, where GStreamer holds each frame until its pts.
        Presented directly, the "current" frame expired 16ms into any idle gap
        between lyric lines, so the loop fetched the next change frame and
        showed the upcoming line's first packet batch (partial tiles, wipes,
        palette swaps) seconds early — the intermittent line-start artifacts.
        advance_to_position_ms() decodes exactly the packets due and no
        further, so a partially/future-rendered state can't be published.
        """
        r = self.reader
        changed = r.advance_to_position_ms(pos_ms)
        if not changed and self._presented_idx == r._cur_idx and not force:
            return None
        self._presented_idx = r._cur_idx
        if self._debug:
            self._debug_note_publish(pos_ms, r)
        if self._cached_image_idx == r._cur_idx and not self._cached_image.isNull():
            return self._cached_image
        frame = r.current_frame()
        plane = self._crop_w * self._crop_h
        image = QImage(
            frame[:plane], self._crop_w, self._crop_h, self._crop_w,
            QImage.Format.Format_Indexed8,
        )
        pal = frame[plane:]
        image.setColorTable(
            [int.from_bytes(pal[i * 4:(i + 1) * 4], "little") for i in range(16)]
        )
        self._cached_image_idx = r._cur_idx
        self._cached_image = image.copy()
        if self._snapshot_dir:
            self._debug_save_snapshot(pos_ms, r._cur_idx)
        return self._cached_image

    # -- diagnostics (SINGWS_CDG_DEBUG / SINGWS_CDG_SNAPSHOT_DIR only) -------

    def _debug_note_publish(self, pos_ms: int, r):
        self._dbg_published += 1
        now = time.monotonic()
        if now - self._dbg_last_log < 1.0:  # rate-limited: ~1 line/sec
            return
        self._dbg_last_log = now
        _diag(
            f"[CDG-DEBUG] publish pos={pos_ms}ms packet={r._next_idx}"
            f"/{r._total_packets} frames_published={self._dbg_published} "
            f"gen={self.generation}"
        )

    def _debug_save_snapshot(self, pos_ms: int, packet_idx: int):
        try:
            os.makedirs(self._snapshot_dir, exist_ok=True)
            self._cached_image.save(
                os.path.join(
                    self._snapshot_dir,
                    f"cdg-{int(pos_ms):07d}ms-p{int(packet_idx):07d}.png",
                )
            )
        except Exception as e:
            _diag(f"[CDG-DEBUG] snapshot save failed: {e}")
            self._snapshot_dir = ""  # don't retry every frame


class GstKaraokeTransport(QObject):
    """GStreamer/OpenKJ karaoke transport. See module docstring."""

    frame_ready = pyqtSignal(QImage)
    ended = pyqtSignal()
    started = pyqtSignal()
    playback_hung = pyqtSignal()

    def __init__(
        self,
        audio_path: str,
        video_path: str | None = None,
        mode: str = "audio",
        duration_seconds: float = 0.0,
        probe_duration_on_init: bool = False,
        cdg_sidefill: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.Gst = _ensure_gst()
        self.audio_path = str(audio_path or "")
        self.video_path = str(video_path or "") or None
        self.mode = str(mode or "audio").lower()
        self.duration_seconds = float(duration_seconds or 0.0)
        self.cdg_sidefill = bool(cdg_sidefill)

        # Host-facing attributes (same names/semantics as the old transport).
        self.max_video_height = 720
        self.eq = None            # GraphicEQ mirrored onto equalizer-10bands
        self.last_level_db = None
        self.last_level_ts = 0.0
        self.tempo_ratio = 1.0
        self.semitones = 0.0
        self.video_offset_seconds = 0.0
        self.visual_timer_interval_ms = 15
        self.start_delay_ms = 0
        self._start_preroll_deadline = 0.0
        self._start_not_before = 0.0
        self._start_finish_pending = False

        self._normalize_gain_db = 0.0
        self._master = None
        self._master_warned = False
        self._eq_last_applied = None
        self._loop_bounds = None
        self._paused = False
        self._stopped = False
        self._pending_start_seconds = 0.0
        self._eos_emitted = False
        self._watchdog_last_pos_ms = -1
        self._watchdog_hung_cycles = 0
        self._watchdog_last_check = 0.0
        self._last_visual_render_ms = 0.0
        self._visual_render_max_ms = 0.0
        self._video_frames_delivered = 0
        self._video_frames_dropped = 0
        self._video_source_size = ""
        self._video_output_size = ""
        self._video_decoder_name = ""
        self._audio_decoder_name = ""
        self._video_codec = ""
        self._audio_codec = ""
        self._last_video_image = QImage()
        self._last_video_frame_pts_ns = None
        self._last_audio_buffer_pts_ns = None
        self._video_eof_received = False
        self._video_eof_drained = False
        self._video_eof_hold_emitted = False
        self._video_eof_monotonic = 0.0
        self._audio_eof_received = False
        self._audio_eof_monotonic = 0.0
        self._started_monotonic = 0.0

        self.cdg = _CdgAdapter(self.video_path, sidefill=self.cdg_sidefill) if self.mode == "cdg" and self.video_path else None
        if self.cdg is not None:
            self.duration_seconds = max(self.duration_seconds, self.cdg.duration_seconds)

        self._build_pipeline()

        self.timer = QTimer(self)
        self.timer.setInterval(self.visual_timer_interval_ms)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ pipeline
    def _build_pipeline(self):
        Gst = self.Gst
        self.pipeline = Gst.Pipeline.new("singws-karaoke")
        self._decoder = Gst.ElementFactory.make("uridecodebin", "decoder")
        if self._decoder is None:
            raise RuntimeError("GStreamer element 'uridecodebin' not found")
        self._decoder.connect("pad-added", self._on_pad_added)
        self.pipeline.add(self._decoder)

        # --- audio bin: OpenKJ's element order, minus rgvolume (SingWS has
        # its own LUFS normalization feeding the volume element) and minus
        # panorama/mono downmix (no host control for either).
        self.audio_bin = Gst.Bin.new("audioBin")

        def mk(factory, name):
            el = Gst.ElementFactory.make(factory, name)
            if el is None:
                raise RuntimeError(f"GStreamer element '{factory}' not found")
            self.audio_bin.add(el)
            return el

        q_in = mk("queue", "queueMainAudio")
        conv_in = mk("audioconvert", "aConvInput")
        resample = mk("audioresample", "audioResample")
        try:
            resample.set_property("sinc-filter-mode", 1)
            resample.set_property("quality", 10)
        except Exception:
            pass
        self.scaletempo = mk("scaletempo", "scaleTempo")
        self.level = mk("level", "level")
        try:
            self.level.set_property("interval", 100 * Gst.MSECOND)
        except Exception:
            pass
        self.equalizer = mk("equalizer-10bands", "equalizer")

        chain = [q_in, conv_in, resample, self.scaletempo, self.level, self.equalizer]

        # SoundTouch pitch element: live key changes as a property set.
        self.pitch = Gst.ElementFactory.make("pitch", "pitch")
        if self.pitch is not None:
            conv_pre_pitch = mk("audioconvert", "aConvPrePitch")
            self.audio_bin.add(self.pitch)
            chain += [conv_pre_pitch, self.pitch]
            self.pitch.set_property("pitch", 1.0)
            self.pitch.set_property("tempo", 1.0)  # speed is scaletempo's job
        else:
            _diag("[GST-KARAOKE] pitch element unavailable — key changes disabled")

        q_end = mk("queue", "queueEndAudio")
        self.volume = mk("volume", "volumeElement")        # normalization gain
        self.fade_volume = mk("volume", "faderVolumeElement")
        conv_end = mk("audioconvert", "aConvEnd")
        sink = mk("autoaudiosink", "audioSink")
        self.audio_sink = sink
        chain += [q_end, self.volume, self.fade_volume, conv_end, sink]

        for a, b in zip(chain, chain[1:]):
            if not a.link(b):
                raise RuntimeError(
                    f"GStreamer link failed: {a.get_name()} -> {b.get_name()}"
                )

        ghost = Gst.GhostPad.new("sink", q_in.get_static_pad("sink"))
        ghost.set_active(True)
        self.audio_bin.add_pad(ghost)
        self.pipeline.add(self.audio_bin)

        # --- video bin (MP4 only): decode -> convert/scale -> RGB appsink.
        self.appsink = None
        if self.mode == "mp4":
            self.video_bin = Gst.Bin.new("videoBin")

            def mkv(factory, name):
                el = Gst.ElementFactory.make(factory, name)
                if el is None:
                    raise RuntimeError(f"GStreamer element '{factory}' not found")
                self.video_bin.add(el)
                return el

            vq = mkv("queue", "queueVideo")
            vconv = mkv("videoconvert", "videoConvert")
            vscale = mkv("videoscale", "videoScale")
            vcaps = mkv("capsfilter", "videoCaps")
            caps = "video/x-raw,format=RGBx"
            height = int(self.max_video_height or 0)
            if height > 0:
                caps += f",height=[1,{height}]"
            vcaps.set_property("caps", Gst.Caps.from_string(caps))
            self.appsink = mkv("appsink", "videoSink")
            self.appsink.set_property("sync", True)
            self.appsink.set_property("max-buffers", 4)
            self.appsink.set_property("drop", True)
            self.appsink.set_property("emit-signals", False)
            for a, b in zip([vq, vconv, vscale, vcaps], [vconv, vscale, vcaps, self.appsink]):
                if not a.link(b):
                    raise RuntimeError("GStreamer video link failed")
            vghost = Gst.GhostPad.new("sink", vq.get_static_pad("sink"))
            vghost.set_active(True)
            self.video_bin.add_pad(vghost)
            self.pipeline.add(self.video_bin)

        self.bus = self.pipeline.get_bus()
        try:
            self.pipeline.connect("deep-element-added", self._on_deep_element_added)
        except Exception:
            pass

        # Decoder EOS is branch-local: an MP4 video stream may finish before
        # its audio stream.  These probes deliberately record that fact but do
        # not stop the pipeline; only whole-pipeline EOS ends playback.
        self._install_stream_probe(q_in.get_static_pad("sink"), "audio")
        if self.mode == "mp4":
            self._install_stream_probe(vq.get_static_pad("sink"), "video")

    def _install_stream_probe(self, pad, stream: str):
        if pad is None:
            return
        try:
            flags = self.Gst.PadProbeType.BUFFER | self.Gst.PadProbeType.EVENT_DOWNSTREAM
            pad.add_probe(flags, self._on_stream_probe, str(stream))
        except Exception as e:
            _diag(f"[GST-KARAOKE] {stream} stream probe unavailable: {e}")

    def _on_stream_probe(self, _pad, info, stream: str):
        """Capture branch timestamps without letting a branch EOS own playback."""
        try:
            if info.type & self.Gst.PadProbeType.BUFFER:
                buf = info.get_buffer()
                if buf is not None and buf.pts != self.Gst.CLOCK_TIME_NONE:
                    if stream == "video":
                        self._last_video_frame_pts_ns = int(buf.pts)
                    else:
                        self._last_audio_buffer_pts_ns = int(buf.pts)
            if info.type & self.Gst.PadProbeType.EVENT_DOWNSTREAM:
                event = info.get_event()
                if event is not None and event.type == self.Gst.EventType.EOS:
                    now = time.monotonic()
                    if stream == "video":
                        self._video_eof_received = True
                        self._video_eof_monotonic = now
                    else:
                        self._audio_eof_received = True
                        self._audio_eof_monotonic = now
        except Exception:
            pass
        return self.Gst.PadProbeReturn.OK

    def _on_deep_element_added(self, _pipeline, _sub_bin, element):
        """Record the actual decoder selected by GStreamer/VideoToolbox."""
        try:
            factory = element.get_factory()
            if factory is None:
                return
            klass = str(factory.get_klass() or "")
            if "Decoder/Video" in klass:
                self._video_decoder_name = str(factory.get_name() or "")
            elif "Decoder/Audio" in klass:
                self._audio_decoder_name = str(factory.get_name() or "")
            elif "Parser/Video" in klass and not self._video_codec:
                self._video_codec = str(factory.get_name() or "")
            elif "Parser/Audio" in klass and not self._audio_codec:
                self._audio_codec = str(factory.get_name() or "")
        except Exception:
            pass

    def _reset_stream_eof_state(self, reason: str):
        self._video_eof_received = False
        self._video_eof_drained = False
        self._video_eof_hold_emitted = False
        self._video_eof_monotonic = 0.0
        self._audio_eof_received = False
        self._audio_eof_monotonic = 0.0
        if self.mode == "mp4":
            _diag(f"[GST-KARAOKE] stream lifecycle reset reason={reason} file={self.audio_path!r}")

    def _on_pad_added(self, _decoder, pad):
        caps = pad.get_current_caps()
        s = caps.to_string() if caps else ""
        try:
            if s.startswith("audio/"):
                sink = self.audio_bin.get_static_pad("sink")
                if sink is not None and not sink.is_linked():
                    pad.link(sink)
            elif s.startswith("video/") and self.appsink is not None:
                sink = self.video_bin.get_static_pad("sink")
                if sink is not None and not sink.is_linked():
                    pad.link(sink)
        except Exception as e:
            _diag(f"[GST-KARAOKE] pad link failed ({s[:40]}): {e}")

    # ------------------------------------------------------------- control
    def start(self, start_seconds: float = 0.0):
        Gst = self.Gst
        uri = Gst.filename_to_uri(self.audio_path)
        self._decoder.set_property("uri", uri)
        self._pending_start_seconds = max(0.0, float(start_seconds or 0.0))
        self._stopped = False
        self._paused = False
        self._eos_emitted = False
        self._reset_stream_eof_state("start")
        self._started_monotonic = time.monotonic()

        state_result = self.pipeline.set_state(Gst.State.PAUSED)
        if state_result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer failed to begin karaoke preroll")
        # Never wait for preroll on Qt's main thread. get_state(4s) was the
        # actual Play-button freeze on slower Intel Macs. Poll with a zero
        # timeout instead; the pipeline does its decoder/device setup on its
        # own streaming threads while Qt remains responsive. start_delay_ms
        # lets SingWS display its deterministic countdown over this preroll.
        now = time.monotonic()
        self._start_preroll_deadline = now + 4.0
        self._start_not_before = now + max(0, int(self.start_delay_ms or 0)) / 1000.0
        self._start_finish_pending = True
        QTimer.singleShot(0, self._finish_start_after_preroll)
        return True

    def _finish_start_after_preroll(self):
        if self._stopped or not self._start_finish_pending:
            return
        Gst = self.Gst
        now = time.monotonic()
        try:
            result, state, _pending = self.pipeline.get_state(0)
        except Exception as exc:
            self._start_finish_pending = False
            _diag(f"[GST-KARAOKE] nonblocking preroll status failed: {exc}")
            return
        if result == Gst.StateChangeReturn.FAILURE:
            self._start_finish_pending = False
            _diag(f"[GST-KARAOKE] preroll failed file={self.audio_path!r}")
            self.stop()
            self.playback_hung.emit()
            return
        preroll_ready = state == Gst.State.PAUSED or result == Gst.StateChangeReturn.NO_PREROLL
        if (not preroll_ready and now < self._start_preroll_deadline) or now < self._start_not_before:
            QTimer.singleShot(20, self._finish_start_after_preroll)
            return
        if not preroll_ready:
            _diag(f"[GST-KARAOKE] preroll timeout; attempting PLAYING file={self.audio_path!r}")
        self._start_finish_pending = False
        if self._pending_start_seconds > 0.0:
            self._do_seek(self._pending_start_seconds)
        if self.cdg is not None:
            self.cdg.seek_seconds(self._pending_start_seconds)
        self._apply_normalize_gain()
        self._apply_modifiers_initial()
        state_result = self.pipeline.set_state(Gst.State.PLAYING)
        if state_result == Gst.StateChangeReturn.FAILURE:
            _diag(f"[GST-KARAOKE] PLAYING transition failed file={self.audio_path!r}")
            self.stop()
            self.playback_hung.emit()
            return
        self.timer.start()
        self.started.emit()
        _diag(
            f"[GST-KARAOKE] started mode={self.mode} start={self._pending_start_seconds:.3f}s "
            f"pitch_element={int(self.pitch is not None)} cdg_sidefill={int(self.cdg_sidefill)} "
            f"cdg_backend=direct_qimage "
            f"file={self.audio_path!r} renderer=qt_qimage_appsink "
            f"video_decoder={self._video_decoder_name or 'pending'} video_caps={self._video_codec or 'pending'} "
            f"audio_decoder={self._audio_decoder_name or 'pending'} audio_codec={self._audio_codec or 'pending'}"
        )

    def stop(self):
        self._stopped = True
        self._start_finish_pending = False
        _diag(
            f"[GST-KARAOKE] shutdown requested mode={self.mode} file={self.audio_path!r} "
            f"renderer=qt_qimage_appsink video_eof={int(self._video_eof_received)} "
            f"audio_eof={int(self._audio_eof_received)} last_video_pts_ns={self._last_video_frame_pts_ns}"
        )
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            self.pipeline.set_state(self.Gst.State.NULL)
            _diag(
                f"[GST-KARAOKE] decoder/renderer shutdown complete mode={self.mode} file={self.audio_path!r} "
                f"video_decoder={self._video_decoder_name or 'unknown'} audio_decoder={self._audio_decoder_name or 'unknown'} "
                f"renderer=qt_qimage_appsink video_eof={int(self._video_eof_received)} "
                f"audio_eof={int(self._audio_eof_received)} queue={self._video_queue_depth()} "
                f"last_rendered_frame_pts_ns={self._last_video_frame_pts_ns}"
            )
        except Exception:
            pass

    def pause(self):
        self._paused = True
        _diag(f"[GST-KARAOKE] playback state=paused pos={self.position_seconds():.3f}s")
        try:
            self.pipeline.set_state(self.Gst.State.PAUSED)
        except Exception:
            pass

    def resume(self):
        self._paused = False
        _diag(f"[GST-KARAOKE] playback state=playing reason=resume pos={self.position_seconds():.3f}s")
        try:
            self.pipeline.set_state(self.Gst.State.PLAYING)
        except Exception:
            pass

    def is_paused(self) -> bool:
        return bool(self._paused)

    def seek(self, seconds: float):
        seconds = max(0.0, float(seconds or 0.0))
        self._do_seek(seconds)
        if self.cdg is not None:
            self.cdg.seek_seconds(seconds)

    def _do_seek(self, seconds: float):
        Gst = self.Gst
        self._reset_stream_eof_state("seek")
        self.pipeline.seek(
            self.tempo_ratio,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            Gst.SeekType.SET,
            int(seconds * NS_PER_SECOND),
            Gst.SeekType.NONE,
            -1,  # gint64 "none" (Gst.CLOCK_TIME_NONE overflows in PyGObject)
        )

    # ------------------------------------------------------------ position
    def position_seconds(self) -> float:
        ok, pos = self.pipeline.query_position(self.Gst.Format.TIME)
        if not ok or pos < 0:
            return -1.0
        return pos / NS_PER_SECOND

    def query_times_ns(self):
        """Return (duration_ns, position_ns) — the LEGACY transport's order,
        which the host's progress bar and seek mapping rely on. Returning
        (pos, dur) made the bar read full and dragging seek to ~0."""
        ok, pos = self.pipeline.query_position(self.Gst.Format.TIME)
        okd, dur = self.pipeline.query_duration(self.Gst.Format.TIME)
        if not okd or dur <= 0:
            dur = int(self.duration_seconds * NS_PER_SECOND) if self.duration_seconds else None
        return (dur if dur and dur > 0 else None), (pos if ok and pos >= 0 else None)

    # ----------------------------------------------------------- modifiers
    def set_modifiers(self, tempo_ratio: float, semitones: float):
        tempo_ratio = max(0.5, min(2.0, float(tempo_ratio or 1.0)))
        semitones = max(-24.0, min(24.0, float(semitones or 0.0)))
        tempo_changed = abs(tempo_ratio - self.tempo_ratio) > 1e-6
        key_changed = abs(semitones - self.semitones) > 1e-6
        self.semitones = semitones
        if self.pitch is not None:
            try:
                self.pitch.set_property("pitch", pitch_ratio_for_semitones(semitones))
            except Exception as e:
                _diag(f"[GST-KARAOKE] pitch set failed: {e}")
        elif semitones:
            _diag("[GST-KARAOKE] key change requested but pitch element unavailable")
        if key_changed or tempo_changed:
            _diag(
                f"[GST-KARAOKE] modifiers mode={self.mode} tempo={tempo_ratio:.3f} "
                f"key={semitones:+.1f} key_path="
                f"{'soundtouch_pitch_property' if self.pitch is not None else 'unavailable'} "
                f"tempo_path=scaletempo_rate_seek"
            )
        if tempo_changed:
            self.tempo_ratio = tempo_ratio
            self._apply_tempo_rate()

    def _apply_modifiers_initial(self):
        if self.pitch is not None and self.semitones:
            self.pitch.set_property("pitch", pitch_ratio_for_semitones(self.semitones))
        if abs(self.tempo_ratio - 1.0) > 1e-6:
            self._apply_tempo_rate()

    def _apply_tempo_rate(self):
        """Live speed change without pitch change.

        OpenKJ's scaletempo + flushing rate-seek path (see NOTEs below on why
        not SoundTouch's own tempo property and why not INSTANT_RATE_CHANGE)."""
        Gst = self.Gst
        # NOTE: routing tempo through the SoundTouch element's own `tempo`
        # property sounds cleaner but rescales downstream timestamps: the
        # pipeline position then advances at 1.0x while the song runs faster,
        # silently breaking CDG sync, the progress bar, and end-silence
        # timing (measured: position rate 1.00 at tempo 1.3). Tempo therefore
        # stays on scaletempo + rate seeks, which keep source-time positions.
        #
        # NOTE: INSTANT_RATE_CHANGE seeks must NOT be used here. scaletempo
        # does not consume the instant-rate multiplier (verified on GStreamer
        # 1.26/1.28: the INSTANT_RATE_CHANGE event passes through scaletempo
        # AND pitch untouched), so the multiplier reaches the audio sink and
        # GstAudioBaseSink honors it by resampling — speed and pitch change
        # together. Of the demuxers used here only qtdemux accepts the
        # instant-rate seek, which made MP4 tempo chipmunk while CDG/MP3
        # (whose pipelines reject it and fell through to the flushing seek)
        # stayed pitch-correct. The flushing rate seek keeps the rate inside
        # the segment where scaletempo consumes it: time-stretch, key kept.
        try:
            optimize_scaletempo_for_rate(self.scaletempo, self.tempo_ratio)
        except Exception:
            pass
        ok, curpos = self.pipeline.query_position(Gst.Format.TIME)
        if not ok:
            curpos = 0
        accepted = self.pipeline.send_event(
            Gst.Event.new_seek(
                self.tempo_ratio,
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                Gst.SeekType.SET,
                curpos,
                Gst.SeekType.NONE,
                0,
            )
        )
        _diag(
            f"[GST-KARAOKE] tempo applied mode={self.mode} tempo={self.tempo_ratio:.3f} "
            f"key={self.semitones:+.1f} path=scaletempo_rate_seek accepted={int(bool(accepted))} "
            f"pos={curpos / NS_PER_SECOND:.3f}s"
        )

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
        self._apply_normalize_gain()

    def _apply_normalize_gain(self):
        try:
            self.volume.set_property(
                "volume", max(0.0, min(10.0, 10.0 ** (self._normalize_gain_db / 20.0)))
            )
        except Exception:
            pass

    @property
    def master(self):
        return None  # per-buffer Python master chain never runs on this path

    @master.setter
    def master(self, value):
        self._master = value
        if value is not None and not self._master_warned:
            self._master_warned = True
            _diag(
                "[GST-KARAOKE] master audio processing is not applied on the "
                "GStreamer transport (no Python in the audio path); BGM master "
                "processing is unaffected"
            )

    # -------------------------------------------------------------- fades
    def fade_out(self, duration_s: float = 4.0, then_pause: bool = False, on_done=None):
        self._start_fade(0.0, duration_s, then_pause, on_done)

    def fade_in(self, duration_s: float = 4.0, on_done=None):
        self._start_fade(1.0, duration_s, False, on_done)

    _fade_generation = 0

    def _fade_set_cubic(self, cubic: float):
        try:
            self.fade_volume.set_property("volume", max(0.0, min(1.0, cubic)) ** 3)
        except Exception:
            pass

    def _fade_get_cubic(self) -> float:
        try:
            return float(self.fade_volume.get_property("volume")) ** (1.0 / 3.0)
        except Exception:
            return 1.0

    def _start_fade(self, target, duration_s, then_pause, on_done):
        """Cubic fade on a dedicated volume element (doesn't touch the
        normalization gain). Qt-timer driven, 100ms steps like OpenKJ."""
        self._fade_generation += 1
        gen = self._fade_generation
        start = self._fade_get_cubic()
        if abs(start - target) < 1e-4:
            if on_done:
                on_done()
            return
        step_ms = 100
        steps = max(1, int(float(duration_s) * 1000 / step_ms))
        delta = (target - start) / steps
        state = {"i": 0}

        def tick():
            if gen != self._fade_generation:
                return
            state["i"] += 1
            self._fade_set_cubic(start + delta * state["i"])
            if state["i"] >= steps:
                self._fade_set_cubic(target)
                if then_pause and target == 0.0:
                    self.pause()
                if on_done:
                    on_done()
                return
            QTimer.singleShot(step_ms, tick)

        QTimer.singleShot(step_ms, tick)

    # ---------------------------------------------------------------- loop
    def set_loop(self, start, end):
        try:
            a, b = float(start), float(end)
        except Exception:
            return
        if b > a >= 0.0:
            self._loop_bounds = (a, b)

    def clear_loop(self):
        self._loop_bounds = None

    # ----------------------------------------------------------------- CDG
    def cdg_sectors_remaining(self):
        if self.cdg is None:
            return None
        pos = self.position_seconds()
        if pos < 0:
            return None
        return self.cdg.sectors_remaining(pos)

    def cdg_generation(self):
        return self.cdg.generation if self.cdg is not None else None

    def cdg_final_frame_ms(self) -> int:
        """Position of the last visible CDG change; -1 until known (the
        reader learns it once it has scanned to EOF)."""
        if self.cdg is None:
            return -1
        try:
            return int(self.cdg.reader.position_of_final_frame_ms())
        except Exception:
            return -1

    def cdg_lyrics_finished(self, position_ms: int | None = None) -> bool:
        """OpenKJ's CDG end-of-track gate: True once playback passed the last
        visible CDG frame (or when unknown/not CDG, so it never blocks)."""
        final = self.cdg_final_frame_ms()
        if final <= 0:
            return True
        if position_ms is None:
            pos = self.position_seconds()
            if pos < 0:
                return False
            position_ms = int(pos * 1000)
        return position_ms >= final

    # ------------------------------------------------------------- offsets
    def set_video_offset_ms(self, ms) -> None:
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

    # ---------------------------------------------------------------- tick
    def _tick(self):
        if self._stopped:
            return
        t0 = time.perf_counter()
        try:
            self._drain_bus()
            pos = self.position_seconds()
            if pos >= 0:
                self._check_loop(pos)
                if self.cdg is not None:
                    self._present_cdg(pos)
            if self.appsink is not None:
                self._pull_video_frame()
            self._mirror_eq()
            self._watchdog(pos)
        except Exception as e:
            _diag(f"[GST-KARAOKE] tick failed: {e}")
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._last_visual_render_ms = elapsed_ms
            if elapsed_ms > self._visual_render_max_ms:
                self._visual_render_max_ms = elapsed_ms

    def _drain_bus(self):
        Gst = self.Gst
        while True:
            msg = self.bus.pop_filtered(
                Gst.MessageType.ELEMENT
                | Gst.MessageType.EOS
                | Gst.MessageType.ERROR
                | Gst.MessageType.WARNING
            )
            if msg is None:
                return
            if msg.type == Gst.MessageType.ELEMENT:
                s = msg.get_structure()
                if s is not None and s.get_name() == "level":
                    try:
                        rms_values = s.get_value("rms")
                        if rms_values:
                            self.last_level_db = float(max(rms_values))
                            self.last_level_ts = time.monotonic()
                    except Exception:
                        pass
            elif msg.type == Gst.MessageType.EOS:
                _diag(
                    f"[GST-KARAOKE] pipeline EOS file={self.audio_path!r} "
                    f"audio_eof={int(self._audio_eof_received)} audio_last_pts_ns={self._last_audio_buffer_pts_ns} "
                    f"video_eof={int(self._video_eof_received)} video_last_pts_ns={self._last_video_frame_pts_ns} "
                    f"video_hold={int(self._video_eof_hold_emitted)} queue={self._video_queue_depth()}"
                )
                self._emit_ended("eos")
            elif msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                _diag(f"[GST-KARAOKE] pipeline error: {err} debug={dbg!r}")
                self._emit_ended("error")
            elif msg.type == Gst.MessageType.WARNING:
                warn, dbg = msg.parse_warning()
                _diag(f"[GST-KARAOKE] pipeline warning: {warn}")

    def _emit_ended(self, reason: str):
        if self._eos_emitted or self._stopped:
            return
        self._eos_emitted = True
        _diag(
            f"[GST-KARAOKE] playback state=completed reason={reason} file={self.audio_path!r} "
            f"audio_eof_t={self._audio_eof_monotonic:.3f} audio_last_pts_ns={self._last_audio_buffer_pts_ns} "
            f"video_eof_t={self._video_eof_monotonic:.3f} video_last_pts_ns={self._last_video_frame_pts_ns} "
            f"last_rendered_frame_pts_ns={self._last_video_frame_pts_ns}"
        )
        try:
            self.timer.stop()
        except Exception:
            pass
        self.ended.emit()

    def _check_loop(self, pos: float):
        bounds = self._loop_bounds
        if bounds is None:
            return
        a, b = bounds
        if pos >= b:
            self.seek(a)

    def _present_cdg(self, pos: float):
        display_pos_ms = int(max(0.0, pos + self.video_offset_seconds) * 1000)
        image = self.cdg.frame_for_position_ms(display_pos_ms)
        if image is None:
            return
        self._video_frames_delivered += 1
        self._video_source_size = f"{image.width()}x{image.height()}"
        self._video_output_size = self._video_source_size
        self.frame_ready.emit(image)

    def _pull_video_frame(self):
        sample = self.appsink.emit("try-pull-sample", 0)
        if sample is None:
            # Appsink reaches EOS independently when a muxed video stream is
            # shorter than its audio stream.  Preserve the final decoded image
            # and reassert it once only after every queued sample is drained.
            # This is deliberately event-driven, not a timer-based duration
            # extension; audio/pipeline EOS remains the sole completion signal.
            try:
                video_eos = bool(self.appsink.get_property("eos"))
            except Exception:
                video_eos = False
            if video_eos and not self._video_eof_drained:
                self._video_eof_drained = True
                self._video_eof_received = True
                if not self._video_eof_monotonic:
                    self._video_eof_monotonic = time.monotonic()
                if not self._last_video_image.isNull():
                    self.frame_ready.emit(self._last_video_image)
                    self._video_eof_hold_emitted = True
                _diag(
                    f"[GST-KARAOKE] video EOS drained; holding last frame "
                    f"file={self.audio_path!r} pts_ns={self._last_video_frame_pts_ns} "
                    f"frames={self._video_frames_delivered} queue={self._video_queue_depth()} "
                    f"audio_eof={int(self._audio_eof_received)} renderer=qt_qimage_appsink"
                )
            return
        buf = sample.get_buffer()
        caps = sample.get_caps()
        s = caps.get_structure(0)
        w = s.get_value("width")
        h = s.get_value("height")
        ok, mapinfo = buf.map(self.Gst.MapFlags.READ)
        if not ok:
            return
        try:
            image = QImage(
                bytes(mapinfo.data), w, h, 4 * w, QImage.Format.Format_RGBX8888
            ).copy()
        finally:
            buf.unmap(mapinfo)
        self._video_frames_delivered += 1
        self._video_source_size = f"{w}x{h}"
        self._video_output_size = self._video_source_size
        self._last_video_image = image
        try:
            if buf.pts != self.Gst.CLOCK_TIME_NONE:
                self._last_video_frame_pts_ns = int(buf.pts)
        except Exception:
            pass
        self.frame_ready.emit(image)

    def _video_queue_depth(self) -> int:
        """Return appsink's buffered frame count where the runtime exposes it."""
        if self.appsink is None:
            return 0
        try:
            if self.appsink.find_property("current-level-buffers") is not None:
                return max(0, int(self.appsink.get_property("current-level-buffers") or 0))
        except Exception:
            pass
        return 0

    def _mirror_eq(self):
        """Mirror the host's GraphicEQ object (if attached) onto the native
        equalizer-10bands element. Cheap list compare; DSP stays native."""
        eq = self.eq
        try:
            if eq is None:
                gains = None
            else:
                gains = list(eq.gains_db()) if eq.enabled() else None
        except Exception:
            gains = None
        target = gains if gains is not None else [0.0] * 10
        if self._eq_last_applied == target:
            return
        self._eq_last_applied = list(target)
        for band in range(min(10, len(target))):
            try:
                self.equalizer.set_property(
                    f"band{band}", max(-24.0, min(12.0, float(target[band])))
                )
            except Exception:
                pass

    def _watchdog(self, pos: float):
        """OpenKJ's stalled-position watchdog: PLAYING but the clock frozen
        for ~5 consecutive seconds -> playback_hung."""
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
                _diag("[GST-KARAOKE] playback hung (position frozen ~5s)")
                self.playback_hung.emit()
        else:
            self._watchdog_hung_cycles = 0
        self._watchdog_last_pos_ms = pos_ms

    # ---------------------------------------------------------- diagnostics
    def diagnostics(self) -> dict:
        pos = self.position_seconds()
        elapsed = max(0.001, time.monotonic() - (self._started_monotonic or time.monotonic()))
        return {
            "media_type": self.mode.upper() if self.mode != "audio" else "MP3",
            "engine": "gstreamer",
            "position_seconds": pos if pos >= 0 else 0.0,
            "audible_position_seconds": pos if pos >= 0 else 0.0,
            "display_position_seconds": (pos + self.video_offset_seconds) if pos >= 0 else 0.0,
            "video_offset_ms": self.video_offset_seconds * 1000.0,
            "tempo_ratio": self.tempo_ratio,
            "semitones": self.semitones,
            "last_visual_render_ms": self._last_visual_render_ms,
            "visual_render_max_ms": self._visual_render_max_ms,
            "video": {
                "decoder": self._video_decoder_name or "gstreamer",
                "codec": self._video_codec,
                "renderer": "qt_qimage_appsink",
                "hardware_acceleration": "vtdec" if self.mode == "mp4" else "n/a",
                "hardware_acceleration_checked": self.mode == "mp4",
                "cdg_backend": "direct_qimage" if self.mode == "cdg" else "n/a",
                "source_size": self._video_source_size,
                "output_size": self._video_output_size,
                "delivered_fps": self._video_frames_delivered / elapsed,
                "fps": 0.0,
                "queue_size": self._video_queue_depth(),
                "max_buffered_frames": 4,
                "dropped_frames": self._video_frames_dropped,
                "eof_received": bool(self._video_eof_received),
                "eof_drained": bool(self._video_eof_drained),
                "holding_last_frame": bool(self._video_eof_hold_emitted),
                "last_frame_pts_ns": self._last_video_frame_pts_ns,
            },
            "audio": {
                "decoder": self._audio_decoder_name or "gstreamer",
                "codec": self._audio_codec,
                "eof_received": bool(self._audio_eof_received),
                "last_buffer_pts_ns": self._last_audio_buffer_pts_ns,
            },
        }
