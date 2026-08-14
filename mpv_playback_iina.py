"""SingWS macOS playback contract backed by one native libmpv render core.

The native bridge and its two child views persist between media loads. New
tracks replace media inside the same libmpv core, avoiding repeated GPU/audio
initialization and eliminating the previous-frame flash between songs.
"""
from __future__ import annotations

import ctypes
import locale
import os
import sys
import threading
from pathlib import Path

VERSION = "native-libmpv-shared-frame 0.1"


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    source_root = Path(__file__).resolve().parent
    native_root = source_root / "native" / "mpv_bridge"
    if (native_root / "libsingws_mpv_bridge.dylib").is_file():
        return native_root
    return source_root


_BRIDGE_LOG_CB = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
# ctypes does not retain the trampoline, so a module-level reference is what
# keeps the native side from calling into freed memory.
_bridge_log_thunk = None
_bridge_log_target = None


def _install_bridge_log(api, log):
    """Route the native bridge's diagnostics into the SingWS log.

    They were written to stderr, which a bundled .app discards -- so bridge
    load failures, window-transition holds and per-view present skips left no
    trace at all in singws_*.log.
    """
    global _bridge_log_thunk, _bridge_log_target
    if not getattr(api, "has_log_callback", False):
        return
    _bridge_log_target = log
    if _bridge_log_thunk is not None:
        return  # already installed; only the target changes

    def _sink(text):
        fn = _bridge_log_target
        if fn is None:
            return
        try:
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            fn(str(text))
        except Exception:
            pass  # a logging failure must never propagate into native code

    _bridge_log_thunk = _BRIDGE_LOG_CB(_sink)
    api.lib.singws_bridge_set_log_callback(_bridge_log_thunk)


class _BridgeApi:
    def __init__(self):
        path = _runtime_root() / "libsingws_mpv_bridge.dylib"
        if not path.is_file():
            raise RuntimeError(f"native bridge missing: {path}")
        self.lib = ctypes.CDLL(str(path), mode=os.RTLD_LAZY | os.RTLD_LOCAL)
        L = self.lib
        L.singws_bridge_create.restype = ctypes.c_void_p
        L.singws_bridge_create.argtypes = [ctypes.c_size_t, ctypes.c_size_t,
                                           ctypes.c_char_p, ctypes.c_char_p]
        L.singws_bridge_load.restype = ctypes.c_int
        L.singws_bridge_load.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                         ctypes.c_char_p]
        L.singws_bridge_destroy.argtypes = [ctypes.c_void_p]
        for name in ("play", "pause", "stop"):
            getattr(L, f"singws_bridge_{name}").argtypes = [ctypes.c_void_p]
        L.singws_bridge_seek.argtypes = [ctypes.c_void_p, ctypes.c_int64]
        for name in ("position", "duration"):
            fn = getattr(L, f"singws_bridge_{name}")
            fn.argtypes = [ctypes.c_void_p]; fn.restype = ctypes.c_int64
        for name in ("is_playing", "at_end", "visual_ready"):
            fn = getattr(L, f"singws_bridge_{name}")
            fn.argtypes = [ctypes.c_void_p]; fn.restype = ctypes.c_int
        L.singws_bridge_set_volume.argtypes = [ctypes.c_void_p, ctypes.c_double]
        L.singws_bridge_set_device.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.singws_bridge_set_tempo.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.singws_bridge_set_key.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.singws_bridge_set_dsp_chain.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.singws_bridge_set_audio_delay.argtypes = [ctypes.c_void_p, ctypes.c_double]
        L.singws_bridge_set_sidefill.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.singws_bridge_load_background.restype = ctypes.c_int
        L.singws_bridge_load_background.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
        L.singws_bridge_stop_background.argtypes = [ctypes.c_void_p]
        L.singws_bridge_background_at_end.argtypes = [ctypes.c_void_p]
        L.singws_bridge_background_at_end.restype = ctypes.c_int
        L.singws_bridge_background_position.argtypes = [ctypes.c_void_p]
        L.singws_bridge_background_position.restype = ctypes.c_int64
        L.singws_bridge_background_paused.argtypes = [ctypes.c_void_p]
        L.singws_bridge_background_paused.restype = ctypes.c_int
        L.singws_bridge_set_background_opacity.argtypes = [ctypes.c_void_p, ctypes.c_double]
        L.singws_bridge_refresh_views.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
        ]
        L.singws_bridge_begin_transition.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.singws_bridge_grab_frame.restype = ctypes.c_void_p
        L.singws_bridge_grab_frame.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ]
        L.singws_bridge_free_frame.argtypes = [ctypes.c_void_p]
        L.singws_bridge_scan_silence.restype = ctypes.c_int
        L.singws_bridge_scan_silence.argtypes = [
            ctypes.c_char_p, ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),
            ctypes.POINTER(ctypes.c_double),
        ]
        L.singws_bridge_scanner_shutdown.argtypes = []
        # Optional: a dylib built before the log callback existed still loads.
        self.has_log_callback = hasattr(L, "singws_bridge_set_log_callback")
        if self.has_log_callback:
            L.singws_bridge_set_log_callback.argtypes = [_BRIDGE_LOG_CB]
            L.singws_bridge_set_log_callback.restype = None


_bridge_api = None
_bridge_api_lock = threading.Lock()


def _get_bridge_api() -> _BridgeApi:
    global _bridge_api
    with _bridge_api_lock:
        if _bridge_api is None:
            _bridge_api = _BridgeApi()
        return _bridge_api


def scan_silence(path, noise_db=-50.0, lead_minimum=0.5,
                 trail_minimum=0.4):
    """Return ``(lead_seconds, content_end_seconds, duration_seconds)``.

    A reusable hidden libmpv core performs one untimed, audio-only pass. It
    uses the exact IINA-derived codec/filter stack already bundled for
    playback, so no FFmpeg executable or subprocess is involved.
    """
    locale.setlocale(locale.LC_NUMERIC, "C")
    lead = ctypes.c_double()
    trail = ctypes.c_double()
    duration = ctypes.c_double()
    ok = _get_bridge_api().lib.singws_bridge_scan_silence(
        os.fsencode(str(path)), float(noise_db), float(lead_minimum),
        float(trail_minimum), ctypes.byref(lead), ctypes.byref(trail),
        ctypes.byref(duration),
    )
    if not ok:
        return None
    return float(lead.value), float(trail.value), float(duration.value)


class MpvPlaybackPlugin:
    """The same 17-method contract used by SingWS's stable mpv backend."""
    def __init__(self, log=print, preview_fast_profile=False):
        del preview_fast_profile
        self.log = log
        self.api = _get_bridge_api()
        _install_bridge_log(self.api, log)
        self._handle = None
        self._preview_id = 0
        self._output_id = 0
        self._preview_widget = None
        self._output_widget = None
        self._error = ""
        self._volume = 1.0
        self._device = "auto"
        self._loaded = False
        self._audio_only = False
        self._shutdown = False
        self._lock = threading.RLock()
        self._stretch = False
        self._sidefill = False
        self._tempo_percent = 100
        self._semitones = 0
        # SingWS DSP (normalize -> EQ -> master bus) as an mpv filter string.
        # The bridge composes it with the key filter into mpv's single "af"
        # property, so neither can clobber the other.
        self._dsp_chain = ""
        self._video_offset_ms = 0

    def attach(self, preview_widget, output_widget) -> bool:
        try:
            self._preview_widget = preview_widget
            self._output_widget = output_widget
            self._preview_id = int(preview_widget.winId())
            self._output_id = int(output_widget.winId())
            if not self._preview_id or not self._output_id:
                raise RuntimeError("Qt did not create native NSView hosts")
            self.log(f"[MPV-NATIVE] hosts attached output={self._output_id} preview={self._preview_id}")
            return True
        except Exception as exc:
            self._error = str(exc); return False

    def _destroy_bridge(self):
        handle, self._handle = self._handle, None
        if handle:
            self.api.lib.singws_bridge_destroy(handle)

    def loadSingWSMedia(self, source, audio_path=None, autoplay=True,
                        semitones=0, tempo_percent=100) -> bool:
        with self._lock:
            try:
                # libmpv requires the process numeric locale to use a dot as
                # the decimal separator. QApplication can restore the user's
                # Terminal locale after module import, so reassert this before
                # every native core creation (matching the stable backend).
                locale.setlocale(locale.LC_NUMERIC, "C")
                source_b = os.fsencode(str(source))
                audio_b = os.fsencode(str(audio_path)) if audio_path else None
                if self._handle:
                    if not self.api.lib.singws_bridge_load(
                            self._handle, source_b, audio_b):
                        raise RuntimeError("native libmpv media replacement failed")
                    handle = self._handle
                else:
                    handle = self.api.lib.singws_bridge_create(
                        self._output_id, self._preview_id, source_b, audio_b)
                    if not handle:
                        raise RuntimeError("native libmpv bridge creation failed")
                    self._handle = handle
                self.api.lib.singws_bridge_set_volume(handle, self._volume * 100.0)
                self.api.lib.singws_bridge_set_device(handle, self._device.encode())
                self._tempo_percent = int(tempo_percent or 100)
                self._semitones = int(semitones or 0)
                self.api.lib.singws_bridge_set_tempo(handle, self._tempo_percent)
                self.api.lib.singws_bridge_set_key(handle, self._semitones)
                # Re-assert the DSP chain: the bridge rebuilds "af" from key on
                # every FILE_LOADED, so a chain set for the previous song would
                # otherwise be dropped at the next one.
                self.api.lib.singws_bridge_set_dsp_chain(
                    handle, self._dsp_chain.encode())
                # loadfile resets audio-delay; re-assert the CDG calibration or
                # it would apply only to the first song of the session.
                self.api.lib.singws_bridge_set_audio_delay(
                    handle, self._video_offset_ms / 1000.0)
                self.api.lib.singws_bridge_set_sidefill(handle, int(self._sidefill))
                if autoplay:
                    self.api.lib.singws_bridge_play(handle)
                self._loaded = True
                self._audio_only = not audio_path and str(source).lower().endswith(
                    (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg"))
                self.log(f"[MPV-NATIVE] load source={source!r} key={semitones:+d} tempo={tempo_percent}%")
                return True
            except Exception as exc:
                self._error = str(exc); self._loaded = False
                self.log(f"[MPV-NATIVE] load failed: {exc}")
                return False

    def playMedia(self):
        if self._handle: self.api.lib.singws_bridge_play(self._handle)
    def pauseMedia(self):
        if self._handle: self.api.lib.singws_bridge_pause(self._handle)
    def stopMedia(self):
        self._loaded = False
        if self._handle: self.api.lib.singws_bridge_stop(self._handle)
    def seekMedia(self, ms):
        if self._handle: self.api.lib.singws_bridge_seek(self._handle, max(0, int(ms)))
    def positionMs(self) -> int:
        return int(self.api.lib.singws_bridge_position(self._handle)) if self._handle else 0
    def durationMs(self) -> int:
        return int(self.api.lib.singws_bridge_duration(self._handle)) if self._handle else 0
    def isPlaying(self) -> bool:
        return bool(self._loaded and self._handle and self.api.lib.singws_bridge_is_playing(self._handle))
    def visualsReady(self) -> bool:
        return bool(self._audio_only or (self._handle and self.api.lib.singws_bridge_visual_ready(self._handle)))
    def atEnd(self) -> bool:
        return bool(self._handle and self.api.lib.singws_bridge_at_end(self._handle))
    def setVolume(self, value) -> None:
        self._volume = max(0.0, min(2.0, float(value)))
        if self._handle: self.api.lib.singws_bridge_set_volume(self._handle, self._volume * 100.0)
    def setAudioDevice(self, name) -> None:
        self._device = str(name or "auto")
        if self._handle: self.api.lib.singws_bridge_set_device(self._handle, self._device.encode())
    def setTempoRatio(self, ratio) -> None:
        """Live tempo without pitch change. mpv's `speed` property."""
        try:
            self._tempo_percent = max(25, min(400, int(round(float(ratio or 1.0) * 100))))
        except (TypeError, ValueError):
            self._tempo_percent = 100
        if self._handle:
            self.api.lib.singws_bridge_set_tempo(self._handle, self._tempo_percent)

    def setPitchSemitones(self, semitones) -> None:
        """Live key without tempo change (rubberband, composed into `af`)."""
        try:
            self._semitones = int(semitones or 0)
        except (TypeError, ValueError):
            self._semitones = 0
        if self._handle:
            self.api.lib.singws_bridge_set_key(self._handle, self._semitones)

    def setAudioProcessing(self, spec: dict | None) -> None:
        """Apply the SingWS audio chain (normalize -> EQ -> master bus).

        mpv decodes and plays in process here, so the NumPy processors in
        singws_eq / singws_master_audio have no sample path to sit in. The same
        chain is expressed as mpv filters instead -- identical maths for
        normalization and the graphic EQ, an approximation for the master bus.
        See mpv_audio_filters for the fidelity notes.

        Key is deliberately excluded: the bridge owns composing it with this
        chain, so pushing it here too would apply rubberband twice.
        """
        from mpv_audio_filters import build_af_chain, describe_chain

        spec = dict(spec or {})
        chain = build_af_chain(
            semitones=0,
            normalize_gain_db=spec.get("normalize_gain_db", 0.0),
            eq_enabled=bool(spec.get("eq_enabled", False)),
            eq_gains_db=spec.get("eq_gains_db"),
            master_enabled=bool(spec.get("master_enabled", False)),
            master_params=spec.get("master_params"),
        )
        if chain == self._dsp_chain:
            return
        self._dsp_chain = chain
        if self._handle:
            self.api.lib.singws_bridge_set_dsp_chain(self._handle, chain.encode())
        self.log(f"[MPV-NATIVE] dsp chain = {describe_chain(chain)}")

    def setVideoOffsetMs(self, ms) -> None:
        """CDG visual-timing calibration.

        SingWS defines this as a visual lead -- ``display_position =
        audible_position + offset`` -- so a positive value shows lyrics
        earlier. mpv's ``audio-delay`` delays audio relative to video, which
        moves the picture ahead by the same amount: same sign, direct mapping.

        The follower-based backend cannot do this (mpv chases SingWS's audio
        clock there, so there is nothing to offset), which is why CDG ran about
        750ms out on mpv with the Settings slider doing nothing. One in-process
        core owning both streams makes it a single property.
        """
        try:
            self._video_offset_ms = max(-3000, min(3000, int(ms or 0)))
        except (TypeError, ValueError):
            self._video_offset_ms = 0
        if self._handle:
            self.api.lib.singws_bridge_set_audio_delay(
                self._handle, self._video_offset_ms / 1000.0)
        # Not "cdg": the host now trims MP4 through this same property too.
        self.log(f"[MPV-NATIVE] visual offset = {self._video_offset_ms:+d}ms")

    def setExternalAudioMaster(self, master) -> None:
        """Not supported on this backend.

        The stable backend runs mpv as muted video followers chasing SingWS's
        own audio clock. This one is a single in-process core that owns both
        audio and video, which is what makes its seek and tempo/key fast. There
        is no follower to hand an external clock to.

        Raising (rather than omitting the method) keeps the host's fallback
        deliberate and diagnosable instead of an AttributeError from an
        unguarded call site.
        """
        if master is None:
            return  # detach is a no-op; nothing was ever attached
        raise RuntimeError(
            "the native IINA backend owns its own audio clock; "
            "mpv-video mode needs the follower-based backend"
        )

    @staticmethod
    def supportsVideoStretch() -> bool:
        """This backend cannot stretch; the host hides the option rather than
        offering a control that silently does nothing."""
        return False

    def setVideoStretch(self, stretch) -> None:
        # Retained only for the cross-platform 17-method contract. The bridge
        # composites from a fixed shared texture and always preserves aspect,
        # so there is nothing to toggle. See supportsVideoStretch().
        self._stretch = False
    def setCdgOutputSidefill(self, enabled) -> None:
        # 0 off, 1 the disc's own background colour, 2 an ambient blur of the
        # picture. Bools still work: the colour fill is what True used to mean.
        try:
            mode = int(enabled)
        except (TypeError, ValueError):
            mode = 1 if enabled else 0
        self._sidefill = max(0, min(2, mode))
        if self._handle:
            self.api.lib.singws_bridge_set_sidefill(
                self._handle, int(self._sidefill))
    def refreshVideoViews(self) -> None:
        """Reparent and present after Qt recreates a native show-window host."""
        if self._handle:
            preview_id = int(self._preview_widget.winId())
            output_id = int(self._output_widget.winId())
            self._preview_id = preview_id
            self._output_id = output_id
            self.api.lib.singws_bridge_refresh_views(
                self._handle, output_id, preview_id)
    def loadBackgroundVideo(self, path, opacity=1.0) -> bool:
        if not self._handle:
            return False
        return bool(self.api.lib.singws_bridge_load_background(
            self._handle, os.fsencode(str(path)), max(0.0, min(1.0, float(opacity)))
        ))
    def stopBackgroundVideo(self) -> None:
        if self._handle:
            self.api.lib.singws_bridge_stop_background(self._handle)
    def backgroundVideoAtEnd(self) -> bool:
        return bool(self._handle and self.api.lib.singws_bridge_background_at_end(self._handle))
    def backgroundVideoPositionMs(self) -> int:
        return int(self.api.lib.singws_bridge_background_position(self._handle)) if self._handle else 0
    def backgroundVideoPaused(self) -> bool:
        return bool(self._handle and self.api.lib.singws_bridge_background_paused(self._handle))
    def setBackgroundVideoOpacity(self, opacity) -> None:
        if self._handle:
            self.api.lib.singws_bridge_set_background_opacity(
                self._handle, max(0.0, min(1.0, float(opacity))))
    def grabFrame(self):
        """Current picture as a QImage, or None.

        The DAW singer-screen preview's only frame source was the FFmpeg/Qt
        path used by the retired painter transport. Under mpv the picture is a shared GL
        texture in a native NSView: there is no QImage, and the host's
        QWidget.grab() fallback cannot capture a native child surface, so the
        preview was blank for the whole mpv path. This goes through libmpv's
        screenshot-raw, which never touches the render path.
        """
        if not self._handle:
            return None
        try:
            from PyQt6.QtGui import QImage
        except Exception:
            return None
        w = ctypes.c_int(0); h = ctypes.c_int(0); stride = ctypes.c_int(0)
        buf = None
        try:
            buf = self.api.lib.singws_bridge_grab_frame(
                self._handle, ctypes.byref(w), ctypes.byref(h), ctypes.byref(stride))
            if not buf or w.value <= 0 or h.value <= 0 or stride.value <= 0:
                return None
            raw = ctypes.string_at(buf, stride.value * h.value)
            # screenshot-raw hands back BGRA premultiplied; copy() detaches the
            # QImage from `raw` before the buffer is freed below.
            image = QImage(raw, w.value, h.value, stride.value,
                           QImage.Format.Format_ARGB32_Premultiplied).copy()
            return None if image.isNull() else image
        except Exception:
            return None
        finally:
            if buf:
                try:
                    self.api.lib.singws_bridge_free_frame(buf)
                except Exception:
                    pass

    def beginWindowTransition(self, duration_ms=1100):
        if self._handle:
            self.api.lib.singws_bridge_begin_transition(
                self._handle, max(100, int(duration_ms)))
    def errorString(self) -> str:
        return self._error
    def version(self) -> str:
        return VERSION
    def audioDescription(self) -> str:
        return "in-process IINA/libmpv core (shared-frame native views)"
    def shutdown(self):
        with self._lock:
            if self._shutdown: return
            self._shutdown = True; self._loaded = False; self._destroy_bridge()
            self.api.lib.singws_bridge_scanner_shutdown()
            self.log("[MPV-NATIVE] clean shutdown")
            # Detach the log sink last: anything the native side emits during
            # teardown above is still worth having, but calling back into a
            # tearing-down interpreter afterwards is not.
            global _bridge_log_target
            _bridge_log_target = None
            if getattr(self.api, "has_log_callback", False):
                self.api.lib.singws_bridge_set_log_callback(
                    ctypes.cast(None, _BRIDGE_LOG_CB))
