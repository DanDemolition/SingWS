"""GStreamer-backed karaoke transport (OpenKJ architecture port).

Drop-in replacement for PythonKaraokeTransport built on the OpenKJ media
chain (see okj_audio_backend.py / OKJ_INTEGRATION.md):

  * ALL audio DSP runs in native GStreamer elements — key change via the
    SoundTouch ``pitch`` element (a property set, zero cost live), speed via
    ``scaletempo`` + INSTANT_RATE_CHANGE seeks, EQ via ``equalizer-10bands``.
    No PCM ever flows through Python.
  * CDG frames come from okj_cdg.CdgReader: change-driven (no pixel change ->
    no frame -> no render) and presented against the pipeline clock, not a
    wall-clock timer.
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

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
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
    """Wraps okj_cdg.CdgReader with the generation/sectors surface the host
    expects from the old CdgDecoder."""

    def __init__(self, path: str):
        from okj_cdg import CdgReader, PACKETS_PER_SECOND

        self.reader = CdgReader(path)
        self.packets_per_second = int(PACKETS_PER_SECOND)
        self.generation = 0
        self.duration_seconds = self.reader.total_duration_ms() / 1000.0
        self._presented_pkt_idx = -1

    def seek_seconds(self, seconds: float):
        self.reader.seek_ms(int(max(0.0, seconds) * 1000))
        self.generation += 1
        self._presented_pkt_idx = -1

    def sectors_remaining(self, seconds: float) -> float:
        n = self.reader._n_packets
        packet = max(0, min(n, int(seconds * self.packets_per_second)))
        return max(0.0, (n - packet) / 4.0)

    def frame_for_position_ms(self, pos_ms: int):
        """Advance the reader to cover pos_ms; return a new RGB ndarray only
        when the visible frame actually changed (change-driven rendering)."""
        r = self.reader
        moved = False
        while r.current_frame_position_ms() + r.current_frame_duration_ms() <= pos_ms:
            if not r.move_to_next_frame():
                break
            moved = True
        if not moved and self._presented_pkt_idx == r._cur_pkt_idx:
            return None
        self._presented_pkt_idx = r._cur_pkt_idx
        return r.current_frame_rgb()


class GstKaraokeTransport(QObject):
    """GStreamer/OpenKJ karaoke transport. See module docstring."""

    frame_ready = pyqtSignal(QImage)
    ended = pyqtSignal()
    playback_hung = pyqtSignal()

    def __init__(
        self,
        audio_path: str,
        video_path: str | None = None,
        mode: str = "audio",
        duration_seconds: float = 0.0,
        probe_duration_on_init: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.Gst = _ensure_gst()
        self.audio_path = str(audio_path or "")
        self.video_path = str(video_path or "") or None
        self.mode = str(mode or "audio").lower()
        self.duration_seconds = float(duration_seconds or 0.0)

        # Host-facing attributes (same names/semantics as the old transport).
        self.max_video_height = 720
        self.eq = None            # GraphicEQ mirrored onto equalizer-10bands
        self.last_level_db = None
        self.last_level_ts = 0.0
        self.tempo_ratio = 1.0
        self.semitones = 0.0
        self.video_offset_seconds = 0.0
        self.visual_timer_interval_ms = 15

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
        self._started_monotonic = 0.0

        self.cdg = _CdgAdapter(self.video_path) if self.mode == "cdg" and self.video_path else None
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
        self._started_monotonic = time.monotonic()

        self.pipeline.set_state(Gst.State.PAUSED)
        # Block for preroll (bounded): a song start is allowed a moment of
        # setup; everything after runs on the pipeline clock.
        self.pipeline.get_state(4 * Gst.SECOND)
        if self._pending_start_seconds > 0.0:
            self._do_seek(self._pending_start_seconds)
        if self.cdg is not None:
            self.cdg.seek_seconds(self._pending_start_seconds)
        self._apply_normalize_gain()
        self._apply_modifiers_initial()
        self.pipeline.set_state(Gst.State.PLAYING)
        self.timer.start()
        _diag(
            f"[GST-KARAOKE] started mode={self.mode} start={self._pending_start_seconds:.3f}s "
            f"pitch_element={int(self.pitch is not None)} file={os.path.basename(self.audio_path)!r}"
        )

    def stop(self):
        self._stopped = True
        try:
            self.timer.stop()
        except Exception:
            pass
        try:
            self.pipeline.set_state(self.Gst.State.NULL)
        except Exception:
            pass

    def pause(self):
        self._paused = True
        try:
            self.pipeline.set_state(self.Gst.State.PAUSED)
        except Exception:
            pass

    def resume(self):
        self._paused = False
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
        ok, pos = self.pipeline.query_position(self.Gst.Format.TIME)
        okd, dur = self.pipeline.query_duration(self.Gst.Format.TIME)
        if not okd or dur <= 0:
            dur = int(self.duration_seconds * NS_PER_SECOND) if self.duration_seconds else None
        return (pos if ok and pos >= 0 else None), (dur if dur and dur > 0 else None)

    # ----------------------------------------------------------- modifiers
    def set_modifiers(self, tempo_ratio: float, semitones: float):
        tempo_ratio = max(0.5, min(2.0, float(tempo_ratio or 1.0)))
        semitones = max(-24.0, min(24.0, float(semitones or 0.0)))
        tempo_changed = abs(tempo_ratio - self.tempo_ratio) > 1e-6
        self.semitones = semitones
        if self.pitch is not None:
            try:
                self.pitch.set_property("pitch", pitch_ratio_for_semitones(semitones))
            except Exception as e:
                _diag(f"[GST-KARAOKE] pitch set failed: {e}")
        elif semitones:
            _diag("[GST-KARAOKE] key change requested but pitch element unavailable")
        if tempo_changed:
            self.tempo_ratio = tempo_ratio
            self._apply_tempo_rate()

    def _apply_modifiers_initial(self):
        if self.pitch is not None and self.semitones:
            self.pitch.set_property("pitch", pitch_ratio_for_semitones(self.semitones))
        if abs(self.tempo_ratio - 1.0) > 1e-6:
            self._apply_tempo_rate()

    def _apply_tempo_rate(self):
        """Live speed change without pitch change (OpenKJ setTempo port):
        INSTANT_RATE_CHANGE when supported, flushing rate seek otherwise."""
        Gst = self.Gst
        try:
            optimize_scaletempo_for_rate(self.scaletempo, self.tempo_ratio)
        except Exception:
            pass
        if Gst.version()[:2] >= (1, 18):
            ev = Gst.Event.new_seek(
                self.tempo_ratio,
                Gst.Format.TIME,
                Gst.SeekFlags.INSTANT_RATE_CHANGE,
                Gst.SeekType.NONE,
                -1,
                Gst.SeekType.NONE,
                -1,
            )
            if self.pipeline.send_event(ev):
                return
        ok, curpos = self.pipeline.query_position(Gst.Format.TIME)
        if not ok:
            curpos = 0
        self.pipeline.send_event(
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
        _diag(f"[GST-KARAOKE] ended reason={reason}")
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
        rgb = self.cdg.frame_for_position_ms(display_pos_ms)
        if rgb is None:
            return
        h, w, _ = rgb.shape
        image = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self._video_frames_delivered += 1
        self._video_source_size = f"{w}x{h}"
        self.frame_ready.emit(image)

    def _pull_video_frame(self):
        sample = self.appsink.emit("try-pull-sample", 0)
        if sample is None:
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
        self.frame_ready.emit(image)

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
                "decoder": "gstreamer",
                "hardware_acceleration": "vtdec" if self.mode == "mp4" else "n/a",
                "hardware_acceleration_checked": self.mode == "mp4",
                "source_size": self._video_source_size,
                "output_size": self._video_output_size,
                "delivered_fps": self._video_frames_delivered / elapsed,
                "fps": 0.0,
                "queue_size": 0,
                "max_buffered_frames": 4,
                "dropped_frames": self._video_frames_dropped,
            },
        }
