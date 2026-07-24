"""FFmpeg/Qt background-music engine.

Recovery backend for background music when BASS cannot initialize. Mirrors the
public API of ``bass_background_engine.BassBackgroundEngine`` (decks, master
volume, crossfades, normalize gains, meter, EQ/master DSP hooks) so
``BackgroundMusicPlayer`` can use either engine through the same call sites,
without requiring GStreamer.

Audio path: one ffmpeg subprocess per deck decodes to float32 stereo PCM into
a bounded buffer; a pull-mode ``QAudioSink`` feeder mixes the decks with
per-deck gain ramps, applies the optional EQ and master processors
(``configure_stream``/``process_f32_array``, same contract as the BASS DSP
callbacks), then the master-volume ramp. Tests construct the engine with
``create_sink=False`` and pump ``mix_block`` directly, so the mixing logic is
exercised without audio hardware.
"""

from __future__ import annotations

import os
import math
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from python_karaoke_transport import (
    _ffmpeg_path,
    _probe_duration_seconds,
    match_qt_audio_device,
)


class FfmpegBackgroundError(RuntimeError):
    pass


SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_FRAME = CHANNELS * 4
# Per-deck decoded-audio cushion. Big enough to ride out UI hitches, small
# enough that a seek/stop discards little work.
DECK_BUFFER_FRAMES = SAMPLE_RATE * 4
_READ_FRAMES = 8192


class _DeckReader(threading.Thread):
    """Decode one file with ffmpeg into the deck's bounded frame buffer."""

    def __init__(self, deck: "_Deck", sample_rate: int):
        super().__init__(daemon=True)
        self.deck = deck
        self.sample_rate = int(sample_rate)
        self.stop_event = threading.Event()
        self.process = None

    def run(self):
        deck = self.deck
        command = [
            _ffmpeg_path("ffmpeg"),
            "-hide_banner", "-loglevel", "error", "-nostdin",
            "-ss", f"{max(0.0, float(deck.start_seconds)):.3f}",
            "-i", deck.path,
            "-vn", "-map", "a:0?",
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ac", str(CHANNELS), "-ar", str(self.sample_rate),
            "-",
        ]
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except Exception:
            with deck.cond:
                deck.eof = True
                deck.cond.notify_all()
            return
        chunk_bytes = _READ_FRAMES * BYTES_PER_FRAME
        try:
            while not self.stop_event.is_set():
                data = self.process.stdout.read(chunk_bytes)
                if not data:
                    break
                frames = np.frombuffer(data, dtype=np.float32)
                usable = (frames.size // CHANNELS) * CHANNELS
                if usable <= 0:
                    continue
                block = frames[:usable].reshape(-1, CHANNELS).copy()
                with deck.cond:
                    while (
                        not self.stop_event.is_set()
                        and deck.buffered_frames >= DECK_BUFFER_FRAMES
                    ):
                        deck.cond.wait(0.1)
                    if self.stop_event.is_set():
                        return
                    deck.blocks.append(block)
                    deck.buffered_frames += block.shape[0]
                    deck.cond.notify_all()
        except Exception:
            pass
        finally:
            with deck.cond:
                deck.eof = True
                deck.cond.notify_all()
            try:
                if self.stop_event.is_set():
                    self.process.kill()
                if self.process.stdout is not None:
                    self.process.stdout.close()
                self.process.wait(timeout=0.3)
            except Exception:
                pass

    def stop(self):
        self.stop_event.set()
        try:
            if self.process is not None:
                self.process.kill()
        except Exception:
            pass
        with self.deck.cond:
            self.deck.cond.notify_all()


@dataclass
class _Deck:
    path: str
    norm_gain: float = 1.0
    start_seconds: float = 0.0
    duration_seconds: float = 0.0
    # Decoded-but-unmixed audio (list of (n, 2) float32 arrays).
    blocks: list = field(default_factory=list)
    buffered_frames: int = 0
    consumed_frames: int = 0
    eof: bool = False
    # Deck gain ramp (the crossfade slides these; norm_gain multiplies on top).
    gain_current: float = 1.0
    gain_target: float = 1.0
    gain_ramp_frames: int = 0
    last_take_frames: int = 0
    reader: _DeckReader | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)

    def position_seconds(self, sample_rate: int) -> float:
        return float(self.start_seconds) + float(self.consumed_frames) / float(sample_rate)


class FfmpegBackgroundEngine:
    backend_name = "FFmpeg-Qt"

    def __init__(
        self,
        output_name: str | None = None,
        sample_rate: int = SAMPLE_RATE,
        create_sink: bool = True,
    ):
        self.sample_rate = int(sample_rate)
        self.output_name = str(output_name or "")
        self.primary: _Deck | None = None
        self.secondary: _Deck | None = None
        self.master_volume = 1.0
        self._master_current = 1.0
        self._master_ramp_frames = 0
        self._master_ramp_start = 1.0
        self._master_ramp_total_frames = 0
        self._master_ramp_elapsed_frames = 0
        self._eq = None
        self._eq_configured = False
        self._master_proc = None
        self._master_proc_configured = False
        self._meter = 0.0
        self._playing = False
        self._lock = threading.RLock()
        self._closed = False
        self.audio_sink = None
        self._feeder = None
        # Sanity-check the decoder up front so a missing ffmpeg fails engine
        # construction (letting the host fall back) instead of every deck.
        _ffmpeg_path("ffmpeg")
        if create_sink:
            self._create_sink()

    # ------------------------------------------------------------------ sink

    def _create_sink(self):
        try:
            from PyQt6.QtCore import QIODevice
            from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

            fmt = QAudioFormat()
            fmt.setSampleRate(self.sample_rate)
            fmt.setChannelCount(CHANNELS)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
            device = None
            if self.output_name:
                device = match_qt_audio_device(
                    QMediaDevices.audioOutputs(), self.output_name
                )
            if device is not None and not device.isNull():
                self.audio_sink = QAudioSink(device, fmt)
            else:
                self.audio_sink = QAudioSink(fmt)
            self.audio_sink.setBufferSize(
                int(self.sample_rate * BYTES_PER_FRAME * 0.2)
            )
            self._feeder = _MixFeeder(self)
            self._feeder.open(QIODevice.OpenModeFlag.ReadOnly)
            self.audio_sink.start(self._feeder)
            # The engine idles until load()/play(); no need to burn the device.
            self.audio_sink.suspend()
        except Exception as exc:
            raise FfmpegBackgroundError(f"QAudioSink unavailable: {exc}") from exc

    def _sink_set_running(self, running: bool):
        sink = self.audio_sink
        if sink is None:
            return
        try:
            if running:
                sink.resume()
            else:
                sink.suspend()
        except Exception:
            pass

    # ----------------------------------------------------------------- decks

    def _make_deck(self, path: str, volume: float, norm_gain: float = 1.0,
                   start_seconds: float = 0.0) -> _Deck:
        if not Path(path).exists():
            raise FfmpegBackgroundError(f"missing file: {path}")
        deck = _Deck(
            path=str(path),
            norm_gain=self._norm_factor(norm_gain),
            start_seconds=max(0.0, float(start_seconds)),
            duration_seconds=_probe_duration_seconds(str(path)),
        )
        deck.gain_current = deck.gain_target = self._gain(volume)
        deck.reader = _DeckReader(deck, self.sample_rate)
        deck.reader.start()
        return deck

    def _free_deck(self, deck: _Deck | None):
        if deck is None or deck.reader is None:
            return
        deck.reader.stop()

    def _gain(self, value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    def _norm_factor(self, value: float) -> float:
        try:
            return max(0.05, min(4.0, float(value)))
        except Exception:
            return 1.0

    def _set_deck_volume(self, deck: _Deck | None, volume: float):
        if deck is None:
            return
        with self._lock:
            deck.gain_current = deck.gain_target = self._gain(volume)
            deck.gain_ramp_frames = 0

    def _slide_deck_volume(self, deck: _Deck | None, volume: float, duration_ms: int):
        if deck is None:
            return
        with self._lock:
            deck.gain_target = self._gain(volume)
            deck.gain_ramp_frames = max(
                0, int(self.sample_rate * float(duration_ms) / 1000.0)
            )
            if deck.gain_ramp_frames == 0:
                deck.gain_current = deck.gain_target

    # ------------------------------------------------------------------- API

    def load(self, path: str, paused: bool = True, volume: float | None = None):
        self.stop()
        if volume is not None:
            self.master_volume = self._gain(volume)
            self._master_current = self.master_volume
            self._master_ramp_frames = 0
        # Deck creation probes duration (blocking); build it outside the lock
        # so the audio thread's mix_block never waits on ffprobe.
        deck = self._make_deck(path, 1.0)
        with self._lock:
            self.primary = deck
        self.set_master_volume(self.master_volume)
        if not paused:
            self.play()

    def play(self) -> bool:
        with self._lock:
            if self.primary is None:
                return False
            self._playing = True
        self._sink_set_running(True)
        return True

    def pause(self) -> bool:
        with self._lock:
            self._playing = False
        self._sink_set_running(False)
        return True

    def stop(self):
        with self._lock:
            primary, secondary = self.primary, self.secondary
            self.primary = None
            self.secondary = None
            self._playing = False
            self._meter = 0.0
        self._free_deck(secondary)
        self._free_deck(primary)
        self._sink_set_running(False)

    def close(self):
        self.stop()
        if self._closed:
            return
        self._closed = True
        sink = self.audio_sink
        self.audio_sink = None
        if sink is not None:
            try:
                sink.stop()
            except Exception:
                pass

    def _effective_master(self) -> float:
        return self._gain(self.master_volume)

    def set_normalize_gain(self, factor: float):
        self.set_primary_normalize_gain(factor)

    def set_primary_normalize_gain(self, factor: float):
        with self._lock:
            if self.primary is None:
                return
            self.primary.norm_gain = self._norm_factor(factor)
            self._set_deck_volume(self.primary, 1.0)

    def set_secondary_normalize_gain(self, factor: float):
        with self._lock:
            if self.secondary is None:
                return
            self.secondary.norm_gain = self._norm_factor(factor)
            self._set_deck_volume(self.secondary, 0.0)

    def set_master_volume(self, volume: float):
        with self._lock:
            self.master_volume = self._gain(volume)
            self._master_current = self.master_volume
            self._master_ramp_frames = 0
            self._master_ramp_start = self.master_volume
            self._master_ramp_total_frames = 0
            self._master_ramp_elapsed_frames = 0

    def slide_master_volume(self, volume: float, duration_ms: int):
        with self._lock:
            self._master_ramp_start = self._master_current
            self.master_volume = self._gain(volume)
            total_frames = max(
                0, int(self.sample_rate * float(duration_ms) / 1000.0)
            )
            self._master_ramp_frames = total_frames
            self._master_ramp_total_frames = total_frames
            self._master_ramp_elapsed_frames = 0
            if total_frames == 0:
                self._master_current = self.master_volume

    def fade_settle_delay_ms(self) -> int:
        """Return enough time for Qt's queued audio to play the fade tail."""
        sink = self.audio_sink
        if sink is None:
            return 0
        try:
            buffer_bytes = max(0, int(sink.bufferSize()))
            buffered_ms = math.ceil(
                (buffer_bytes * 1000.0)
                / float(max(1, self.sample_rate * BYTES_PER_FRAME))
            )
            return max(40, min(300, buffered_ms + 30))
        except Exception:
            return 230

    def set_eq(self, eq) -> None:
        with self._lock:
            self._eq = eq
            self._eq_configured = False

    # Native DX8 compressor compatibility shim: the FFmpeg engine has no DX8
    # FX; the full master chain (set_master_processor) covers compression.
    def set_master_compressor(self, params: dict | None) -> None:
        return None

    def set_master_processor(self, processor) -> None:
        with self._lock:
            if processor is self._master_proc:
                return
            self._master_proc = processor
            self._master_proc_configured = False

    def start_crossfade(self, path: str, duration_ms: int, norm_gain: float = 1.0) -> bool:
        with self._lock:
            if self.primary is None or self.secondary is not None:
                return False
        # Build outside the lock (ffprobe blocks), then attach if still valid.
        deck = self._make_deck(path, 0.0, norm_gain=norm_gain)
        with self._lock:
            if self.primary is None or self.secondary is not None:
                self._free_deck(deck)
                return False
            self.secondary = deck
            self._slide_deck_volume(self.primary, 0.0, duration_ms)
            self._slide_deck_volume(deck, 1.0, duration_ms)
        self.play()
        return True

    def complete_crossfade(self) -> bool:
        with self._lock:
            if self.secondary is None:
                return False
            old = self.primary
            self.primary = self.secondary
            self.secondary = None
            self._set_deck_volume(self.primary, 1.0)
        self._free_deck(old)
        return True

    def cancel_crossfade(self):
        with self._lock:
            old = self.secondary
            self.secondary = None
            if self.primary is not None:
                self._set_deck_volume(self.primary, 1.0)
        self._free_deck(old)

    def get_times(self) -> tuple[float, float]:
        with self._lock:
            deck = self.primary
            if deck is None:
                return 0.0, 0.0
            return (
                min(deck.position_seconds(self.sample_rate),
                    deck.duration_seconds or float("inf")),
                float(deck.duration_seconds or 0.0),
            )

    def seek(self, seconds: float) -> bool:
        with self._lock:
            old = self.primary
            if old is None:
                return False
            target = max(0.0, float(seconds or 0.0))
            if old.duration_seconds > 0.0:
                target = min(target, max(0.0, old.duration_seconds - 0.001))
            # Replace the deck outright: the old reader can only ever write
            # into the discarded deck, so a seek never surfaces stale audio.
            deck = _Deck(
                path=old.path,
                norm_gain=old.norm_gain,
                start_seconds=target,
                duration_seconds=old.duration_seconds,
            )
            deck.gain_current = old.gain_current
            deck.gain_target = old.gain_target
            deck.gain_ramp_frames = old.gain_ramp_frames
            deck.reader = _DeckReader(deck, self.sample_rate)
            self.primary = deck
        self._free_deck(old)
        deck.reader.start()
        return True

    def source_ended(self) -> bool:
        with self._lock:
            deck = self.primary
            if deck is None:
                return True
            with deck.cond:
                drained = deck.eof and deck.buffered_frames <= 0
            pos, dur = self.get_times()
        return drained or bool(dur > 0.0 and pos >= max(0.0, dur - 0.02))

    def is_playing(self) -> bool:
        with self._lock:
            return bool(self._playing and self.primary is not None)

    def is_paused(self) -> bool:
        with self._lock:
            return bool(not self._playing and self.primary is not None)

    def meter_level(self) -> float:
        with self._lock:
            return float(self._meter)

    # ------------------------------------------------------------------- mix

    def _deck_take(self, deck: _Deck, frames: int) -> np.ndarray:
        """Pop up to ``frames`` decoded frames and apply the deck gain ramp."""
        parts = []
        needed = frames
        with deck.cond:
            while needed > 0 and deck.blocks:
                block = deck.blocks[0]
                if block.shape[0] <= needed:
                    parts.append(block)
                    deck.blocks.pop(0)
                    needed -= block.shape[0]
                else:
                    parts.append(block[:needed])
                    deck.blocks[0] = block[needed:]
                    needed = 0
            taken = frames - needed
            deck.buffered_frames -= taken
            deck.consumed_frames += taken
            deck.last_take_frames = taken
            deck.cond.notify_all()
        if not parts:
            return np.zeros((frames, CHANNELS), dtype=np.float32)
        audio = np.concatenate(parts) if len(parts) > 1 else parts[0].copy()
        # Per-block linear gain ramp toward the slide target.
        n = audio.shape[0]
        if deck.gain_ramp_frames > 0:
            take = min(n, deck.gain_ramp_frames)
            end = deck.gain_current + (
                (deck.gain_target - deck.gain_current)
                * (take / float(deck.gain_ramp_frames))
            )
            env = np.linspace(deck.gain_current, end, take, dtype=np.float32)
            gains = np.full(n, deck.gain_target, dtype=np.float32)
            gains[:take] = env
            deck.gain_current = float(end)
            deck.gain_ramp_frames -= take
            if deck.gain_ramp_frames <= 0:
                deck.gain_current = deck.gain_target
            audio = audio * (gains[:, None] * deck.norm_gain)
        else:
            audio = audio * (deck.gain_current * deck.norm_gain)
        if n < frames:
            audio = np.vstack(
                [audio, np.zeros((frames - n, CHANNELS), dtype=np.float32)]
            )
        return audio

    def mix_block(self, frames: int) -> np.ndarray:
        """Produce the next ``frames`` of output (float32, shape (frames, 2)).

        Called by the QAudioSink feeder on the Qt audio thread, and directly
        by tests. Returns silence when idle so the sink never starves.
        """
        frames = max(1, int(frames))
        with self._lock:
            if not self._playing or self.primary is None:
                self._meter *= 0.8
                return np.zeros((frames, CHANNELS), dtype=np.float32)
            mixed = self._deck_take(self.primary, frames)
            meter_src = mixed
            if self.secondary is not None:
                sec = self._deck_take(self.secondary, frames)
                meter_src = sec
                mixed = mixed + sec
            # Meter tracks the incoming/current deck after its own gain but
            # before master volume, mirroring the BASS VOLPAN meter.
            rms = float(np.sqrt(np.mean(np.square(meter_src)))) if meter_src.size else 0.0
            self._meter = min(1.0, rms)
            eq = self._eq
            if eq is not None:
                try:
                    if not self._eq_configured:
                        eq.configure_stream(self.sample_rate, CHANNELS)
                        self._eq_configured = True
                    processed = eq.process_f32_array(mixed)
                    if processed is not mixed:
                        mixed = np.asarray(processed, dtype=np.float32)
                except Exception:
                    pass
            proc = self._master_proc
            if proc is not None:
                try:
                    if not self._master_proc_configured:
                        proc.configure_stream(self.sample_rate, CHANNELS)
                        self._master_proc_configured = True
                    processed = proc.process_f32_array(mixed)
                    if processed is not mixed:
                        mixed = np.asarray(processed, dtype=np.float32)
                except Exception:
                    pass
            # Master-volume ramp (slide_master_volume), then hard clip. Advance
            # only across decoded frames: otherwise a slow decoder can consume
            # the entire fade-in while the sink is still receiving silence.
            active_frames = max(
                int(getattr(self.primary, "last_take_frames", 0) or 0),
                int(getattr(self.secondary, "last_take_frames", 0) or 0)
                if self.secondary is not None else 0,
            )
            if self._master_ramp_frames > 0 and active_frames > 0:
                take = min(active_frames, self._master_ramp_frames)
                start_index = self._master_ramp_elapsed_frames
                total = max(1, self._master_ramp_total_frames)
                progress = (
                    np.arange(start_index + 1, start_index + take + 1, dtype=np.float32)
                    / float(total)
                )
                progress = np.clip(progress, 0.0, 1.0)
                curve = 0.5 - (0.5 * np.cos(np.pi * progress))
                ramp = self._master_ramp_start + (
                    (self.master_volume - self._master_ramp_start) * curve
                )
                env = np.full(frames, self._master_current, dtype=np.float32)
                env[:take] = ramp
                self._master_current = float(ramp[-1])
                self._master_ramp_elapsed_frames += take
                self._master_ramp_frames -= take
                if self._master_ramp_frames <= 0:
                    self._master_current = self.master_volume
                    if active_frames > take:
                        env[take:active_frames] = self.master_volume
                mixed = mixed * env[:, None]
            else:
                mixed = mixed * self._master_current
            return np.clip(mixed, -1.0, 1.0).astype(np.float32, copy=False)


def _make_mix_feeder_base():
    try:
        from PyQt6.QtCore import QIODevice
        return QIODevice
    except Exception:
        return object


class _MixFeeder(_make_mix_feeder_base()):
    """Pull-mode QIODevice: Qt's audio thread reads mixed PCM on demand."""

    def __init__(self, engine: FfmpegBackgroundEngine):
        super().__init__()
        self.engine = engine

    def readData(self, maxlen: int) -> bytes:
        frames = max(1, min(int(maxlen) // BYTES_PER_FRAME, _READ_FRAMES))
        try:
            return self.engine.mix_block(frames).tobytes()
        except Exception:
            return bytes(frames * BYTES_PER_FRAME)

    def writeData(self, data) -> int:
        return 0

    def bytesAvailable(self) -> int:
        try:
            return _READ_FRAMES * BYTES_PER_FRAME + int(super().bytesAvailable())
        except Exception:
            return _READ_FRAMES * BYTES_PER_FRAME

    def isSequential(self) -> bool:
        return True
