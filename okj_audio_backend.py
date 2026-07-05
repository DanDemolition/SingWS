"""
okj_audio_backend.py
--------------------
Python/PyGObject port of OpenKJ's MediaBackend audio chain (from
src/mediabackend.cpp, OpenKJ 2.1.39-unstable).

Reproduces the parts that matter for live performance:

  * scaletempo-based speed change (no pitch shift), applied via seek-with-rate
    - INSTANT_RATE_CHANGE on GStreamer >= 1.18 (zero-cost, no flush)
    - flushing accurate seek fallback otherwise
  * scaletempo stride/search auto-tuning per rate (SoundTouch TDStretch logic,
    copied from OpenKJ's optimize_scaletempo_for_rate)
  * live key change via the SoundTouch "pitch" element (semitones -> ratio),
    with tempo held at 1.0 so pitch and speed stay independent
  * 10-band EQ, volume, fade volume, downmix capsfilter — same element order
  * seek() that preserves the current playback rate

Requirements (macOS / Homebrew):
    brew install gstreamer pygobject3
    ("pitch" lives in gst-plugins-bad, "scaletempo" in gst-plugins-good;
     the monolithic homebrew gstreamer formula includes both)

Usage:
    from okj_audio_backend import AudioBackend
    be = AudioBackend()
    be.load("/path/to/song.mp3")
    be.play()
    be.set_key_change(+2)      # semitones, live
    be.set_tempo(110)          # percent, live
    be.seek_ms(63_000)         # rate-preserving seek
"""

import math

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib

Gst.init(None)

# Semitone ratios, same constants OpenKJ uses (STUP / STDN)
_STUP = 1.0594630943592952645618252949461  # 2^(1/12)
_STDN = 0.94387431268169349664191315666784  # 2^(-1/12)


def pitch_ratio_for_semitones(semitones: int) -> float:
    """Port of MediaBackend::getPitchForSemitone()."""
    if semitones > 0:
        return math.pow(_STUP, semitones)
    if semitones < 0:
        return 1 - ((100 - (math.pow(_STDN, abs(semitones)) * 100)) / 100)
    return 1.0


def optimize_scaletempo_for_rate(scaletempo: Gst.Element, rate: float) -> None:
    """Port of optimize_scaleTempo_for_rate() from gstreamerhelper.cpp.

    Retunes scaletempo's stride/search windows per playback rate, using the
    auto-tuning curve from SoundTouch's TDStretch. This is what keeps large
    speed changes clean and cheap.
    """
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


class AudioBackend:
    """Karaoke audio backend mirroring OpenKJ's audio bin.

    Chain (same order as mediabackend.cpp buildPipeline):
      uridecodebin -> queue -> audioconvert -> audioresample -> scaletempo
        -> level -> equalizer-10bands -> audiopanorama -> audioconvert
        -> capsfilter(stereo/mono) -> [audioconvert -> pitch] -> queue
        -> volume -> fadevolume -> audioconvert -> autoaudiosink
    """

    def __init__(self, enable_pitch_shift: bool = True):
        self.pipeline = Gst.Pipeline.new("okj-audio")
        self.playback_rate = 1.0
        self.key_change = 0
        self._eq_levels = [0.0] * 10   # OpenKJ: std::array<int,10> m_eqLevels
        self._eq_bypass = False        # OpenKJ: m_bypass

        self._decoder = Gst.ElementFactory.make("uridecodebin", "decoder")
        self._decoder.connect("pad-added", self._on_pad_added)
        self.pipeline.add(self._decoder)

        self.audio_bin = Gst.Bin.new("audioBin")

        def mk(factory, name):
            el = Gst.ElementFactory.make(factory, name)
            if el is None:
                raise RuntimeError(
                    f"GStreamer element '{factory}' not found — check your "
                    f"gst-plugins install (good/bad)."
                )
            self.audio_bin.add(el)
            return el

        q_in = mk("queue", "queueMainAudio")
        conv_in = mk("audioconvert", "aConvInput")
        resample = mk("audioresample", "audioResample")
        resample.set_property("sinc-filter-mode", 1)
        resample.set_property("quality", 10)
        self.scaletempo = mk("scaletempo", "scaleTempo")
        # ReplayGain normalization — evens out loudness between tracks/brands.
        # OpenKJ runs this pre-scaletempo with album-mode off.
        self.rgvolume = mk("rgvolume", "rgVolume")
        self.rgvolume.set_property("album-mode", False)
        level = mk("level", "level")
        self.equalizer = mk("equalizer-10bands", "equalizer")
        panorama = mk("audiopanorama", "audioPanorama")
        panorama.set_property("method", 1)  # simple (cheap) panning
        conv_post = mk("audioconvert", "aConvPostPanorama")
        self.caps_filter = mk("capsfilter", "fltrPostPanorama")
        self._caps_stereo = Gst.Caps.from_string("audio/x-raw,channels=2")
        self._caps_mono = Gst.Caps.from_string("audio/x-raw,channels=1")
        self.caps_filter.set_property("caps", self._caps_stereo)

        last = None
        for a, b in zip(
            [q_in, conv_in, resample, self.rgvolume, self.scaletempo, level,
             self.equalizer, panorama, conv_post],
            [conv_in, resample, self.rgvolume, self.scaletempo, level,
             self.equalizer, panorama, conv_post, self.caps_filter],
        ):
            a.link(b)
        last = self.caps_filter

        # --- pitch shifter (SoundTouch "pitch"; OpenKJ's cross-platform path) ---
        self.pitch = None
        if enable_pitch_shift:
            self.pitch = Gst.ElementFactory.make("pitch", "pitch")
            if self.pitch is not None:
                conv_pre_pitch = mk("audioconvert", "aConvPrePitchShift")
                self.audio_bin.add(self.pitch)
                last.link(conv_pre_pitch)
                conv_pre_pitch.link(self.pitch)
                # tempo stays 1.0 forever: speed is scaletempo's job.
                self.pitch.set_property("pitch", 1.0)
                self.pitch.set_property("tempo", 1.0)
                last = self.pitch

        q_end = mk("queue", "queueEndAudio")
        self.volume = mk("volume", "volumeElement")
        self.fade_volume = mk("volume", "faderVolumeElement")
        conv_end = mk("audioconvert", "aConvEnd")
        sink = mk("autoaudiosink", "audioSink")

        last.link(q_end)
        q_end.link(self.volume)
        self.volume.link(self.fade_volume)
        self.fade_volume.link(conv_end)
        conv_end.link(sink)

        ghost_pad = Gst.GhostPad.new("sink", q_in.get_static_pad("sink"))
        ghost_pad.set_active(True)
        self.audio_bin.add_pad(ghost_pad)
        self.pipeline.add(self.audio_bin)

        self._gst_1_18 = Gst.version()[:2] >= (1, 18)

        # ---------------- monitoring: RMS level + hung-playback watchdog ----
        # RMS comes from the `level` element's bus messages (OpenKJ's
        # GST_MESSAGE_ELEMENT handler). We expose the RAW value so the app's
        # own silence logic can decide when/how to overlap break music —
        # this backend does not impose OpenKJ's cutoff behavior.
        self.current_rms: float = 0.0          # 0.0 (silence) .. ~1.0
        self.on_rms = None                     # callback(rms: float), optional
        self.on_playback_hung = None           # callback(), optional
        self.cdg_final_frame_ms: int = -1      # set from CdgReader if desired

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::element", self._on_element_message)

        self._watchdog_last_pos = -1
        self._watchdog_hung_cycles = 0
        GLib.timeout_add(1000, self._watchdog_tick)

    # ---------------------------------------------------- level / watchdog
    def _on_element_message(self, _bus, msg):
        s = msg.get_structure()
        if s is None or s.get_name() != "level":
            return
        # Port of OpenKJ's RMS handling: average linear RMS across channels.
        rms_db_values = s.get_value("rms")
        if not rms_db_values:
            return
        total = 0.0
        for rms_db in rms_db_values:
            total += math.pow(10, rms_db / 20)
        self.current_rms = total / len(rms_db_values)
        if self.on_rms:
            self.on_rms(self.current_rms)

    def is_silent(self, threshold: float = 0.001) -> bool:
        """OpenKJ's isSilent() equivalent — but YOUR logic decides what to
        do with it. threshold 0.001 ~= -60 dBFS."""
        return self.current_rms < threshold

    def cdg_lyrics_finished(self, position_ms: int) -> bool:
        """OpenKJ's CDG end-of-track gate: True once playback has passed the
        final visible CDG frame. Feed cdg_final_frame_ms from
        CdgReader.position_of_final_frame_ms(). Combine with your own
        silence detector to avoid cutting quiet outros with lyrics
        still on screen."""
        return 0 < self.cdg_final_frame_ms <= position_ms

    def _watchdog_tick(self) -> bool:
        """Port of OpenKJ's position watchdog in timerSlow_timeout():
        playing but position frozen for 5 consecutive seconds -> hung.
        This is the 'OpenKJ stalled, start the backup player' trigger."""
        _, state, _ = self.pipeline.get_state(0)
        if state == Gst.State.PLAYING:
            pos = self.position_ms()
            if pos == self._watchdog_last_pos and pos > 10:
                self._watchdog_hung_cycles += 1
                if self._watchdog_hung_cycles >= 5:
                    self._watchdog_hung_cycles = 0
                    if self.on_playback_hung:
                        self.on_playback_hung()
            else:
                self._watchdog_hung_cycles = 0
            self._watchdog_last_pos = pos
        else:
            self._watchdog_hung_cycles = 0
        return True  # keep timer alive

    # ------------------------------------------------------------- fader
    # Port of audiofader.cpp: fades a DEDICATED volume element (separate
    # from the user's volume knob) in CUBIC scale, which sounds perceptually
    # linear. cubic->linear conversion: linear = cubic^3.

    def _fade_set_cubic(self, cubic: float):
        self.fade_volume.set_property("volume", max(0.0, min(1.0, cubic)) ** 3)

    def _fade_get_cubic(self) -> float:
        return self.fade_volume.get_property("volume") ** (1.0 / 3.0)

    def fade_out(self, duration_s: float = 4.0, then_pause: bool = False,
                 on_done=None):
        """Smooth cubic fade to silence (default matches your 4s crossfade)."""
        self._start_fade(target=0.0, duration_s=duration_s,
                         then_pause=then_pause, on_done=on_done)

    def fade_in(self, duration_s: float = 4.0, on_done=None):
        """Smooth cubic fade to full."""
        self._start_fade(target=1.0, duration_s=duration_s,
                         then_pause=False, on_done=on_done)

    def fade_out_immediate(self):
        self._fade_generation += 1
        self._fade_set_cubic(0.0)

    def fade_in_immediate(self):
        self._fade_generation += 1
        self._fade_set_cubic(1.0)

    _fade_generation = 0

    def _start_fade(self, target, duration_s, then_pause, on_done):
        self._fade_generation += 1
        gen = self._fade_generation
        start = self._fade_get_cubic()
        if abs(start - target) < 1e-4:
            if on_done:
                on_done()
            return
        step_ms = 100  # OpenKJ's fader timer interval
        steps = max(1, int(duration_s * 1000 / step_ms))
        delta = (target - start) / steps
        state = {"i": 0}

        def tick():
            if gen != self._fade_generation:
                return False  # superseded by a newer fade
            state["i"] += 1
            cur = start + delta * state["i"]
            self._fade_set_cubic(cur)
            if state["i"] >= steps:
                self._fade_set_cubic(target)
                if then_pause and target == 0.0:
                    self.pause()
                if on_done:
                    on_done()
                return False
            return True

        GLib.timeout_add(step_ms, tick)

    # ------------------------------------------------------------------ util
    def _on_pad_added(self, _decoder, pad):
        caps = pad.get_current_caps()
        if caps and caps.to_string().startswith("audio/"):
            sink = self.audio_bin.get_static_pad("sink")
            if not sink.is_linked():
                pad.link(sink)

    # --------------------------------------------------------------- control
    def load(self, path_or_uri: str):
        uri = path_or_uri if "://" in path_or_uri else Gst.filename_to_uri(path_or_uri)
        self.pipeline.set_state(Gst.State.NULL)
        self._decoder.set_property("uri", uri)
        self.pipeline.set_state(Gst.State.PAUSED)

    def play(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def pause(self):
        self.pipeline.set_state(Gst.State.PAUSED)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)

    def position_ms(self) -> int:
        ok, pos = self.pipeline.query_position(Gst.Format.TIME)
        return pos // Gst.MSECOND if ok else -1

    def duration_ms(self) -> int:
        ok, dur = self.pipeline.query_duration(Gst.Format.TIME)
        return dur // Gst.MSECOND if ok else -1

    # ------------------------------------------------------------- key/pitch
    def set_key_change(self, semitones: int):
        """Live key change, exactly like OpenKJ's setPitchShift().

        Just a property set on a running pipeline — no seek, no flush,
        effectively zero cost. Range typically -12..+12.
        """
        self.key_change = semitones
        if self.pitch is None:
            raise RuntimeError("pitch element unavailable (gst-plugins-bad missing?)")
        self.pitch.set_property("pitch", pitch_ratio_for_semitones(semitones))

    # ----------------------------------------------------------------- tempo
    def set_tempo(self, percent: int):
        """Live speed change without pitch change (OpenKJ setTempo port).

        percent: 100 = normal, 80 = slower, 125 = faster.
        Uses INSTANT_RATE_CHANGE when available: no flush, no audible gap.
        """
        self.playback_rate = percent / 100.0
        optimize_scaletempo_for_rate(self.scaletempo, self.playback_rate)

        if self._gst_1_18:
            ev = Gst.Event.new_seek(
                self.playback_rate,
                Gst.Format.TIME,
                Gst.SeekFlags.INSTANT_RATE_CHANGE,
                Gst.SeekType.NONE, Gst.CLOCK_TIME_NONE,
                Gst.SeekType.NONE, Gst.CLOCK_TIME_NONE,
            )
            if self.pipeline.send_event(ev):
                return

        # Fallback: flushing accurate seek to current position at new rate
        ok, curpos = self.pipeline.query_position(Gst.Format.TIME)
        if not ok:
            curpos = 0
        self.pipeline.send_event(Gst.Event.new_seek(
            self.playback_rate,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekType.SET, curpos,
            Gst.SeekType.NONE, 0,
        ))

    # ------------------------------------------------------------------ seek
    def seek_ms(self, position_ms: int):
        """Rate-preserving flushing seek (OpenKJ setPosition equivalent)."""
        self.pipeline.seek(
            self.playback_rate,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            Gst.SeekType.SET, position_ms * Gst.MSECOND,
            Gst.SeekType.NONE, Gst.CLOCK_TIME_NONE,
        )

    # ------------------------------------------------------------- misc knobs
    def set_volume(self, vol_0_to_100: int):
        self.volume.set_property("volume", vol_0_to_100 / 100.0)

    def set_downmix(self, mono: bool):
        self.caps_filter.set_property(
            "caps", self._caps_mono if mono else self._caps_stereo)

    # ------------------------------------------------------------------- EQ
    # Port of OpenKJ's EQ system (MediaBackend::setEqLevel / setEqBypass).
    # equalizer-10bands center frequencies (Hz), for labeling UI sliders:
    #   29, 59, 119, 237, 474, 947, 1889, 3770, 7523, 15011
    # Gain range per band: -24.0 .. +12.0 dB.

    def set_eq_level(self, band_0_to_9: int, level_db: float):
        """Set one band's gain. Remembered even while bypassed, applied
        live otherwise — exact OpenKJ behavior."""
        self._eq_levels[band_0_to_9] = level_db
        if not self._eq_bypass:
            self.equalizer.set_property(f"band{band_0_to_9}", float(level_db))

    def set_eq_bypass(self, bypass: bool):
        """Toggle EQ. Bypass zeroes all bands without losing settings;
        un-bypass restores the remembered curve. Live, no interruption."""
        for band in range(10):
            self.equalizer.set_property(
                f"band{band}", 0.0 if bypass else float(self._eq_levels[band]))
        self._eq_bypass = bypass

    def get_eq_levels(self) -> list:
        return list(self._eq_levels)


if __name__ == "__main__":
    import sys
    be = AudioBackend()
    be.load(sys.argv[1])
    be.play()
    be.set_key_change(2)
    be.set_tempo(90)
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        be.stop()
