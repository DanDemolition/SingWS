//! Silence detection and boundary derivation.
//!
//! This mirrors, on purpose, what the app does today with libmpv:
//! `silencedetect=n=-55dB:d=0.3` after `apad=pad_dur=0.5`
//! (`libmpv_media_jobs._configure_karaoke_transition_job`), followed by
//! `_parse_karaoke_boundaries`. The detector is streaming; `boundaries()` is a
//! line-for-line port of the Python parser so the two can be compared directly.

/// One silence event, as `silencedetect` prints them.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SilenceEvent {
    pub is_start: bool,
    pub time_s: f64,
}

/// Streaming detector: a frame is silent when every channel is below the noise floor.
pub struct SilenceDetector {
    threshold: f32,
    min_run_frames: u64,
    rate: f64,
    frame_index: u64,
    run_start: u64,
    run_len: u64,
    reported: bool,
    events: Vec<SilenceEvent>,
}

impl SilenceDetector {
    pub fn new(noise_floor_db: f32, min_silence_s: f32, rate: u32) -> Self {
        Self {
            threshold: 10f32.powf(noise_floor_db / 20.0),
            min_run_frames: ((min_silence_s as f64) * rate as f64).round().max(1.0) as u64,
            rate: rate as f64,
            frame_index: 0,
            run_start: 0,
            run_len: 0,
            reported: false,
            events: Vec::new(),
        }
    }

    /// Feed interleaved samples.
    pub fn push(&mut self, interleaved: &[f32], channels: usize) {
        if channels == 0 {
            return;
        }
        for frame in interleaved.chunks_exact(channels) {
            let silent = frame.iter().all(|s| s.abs() < self.threshold);
            self.step(silent);
        }
    }

    /// Feed `frames` frames of digital silence (the synthetic tail pad).
    pub fn push_silence(&mut self, frames: u64) {
        for _ in 0..frames {
            self.step(true);
        }
    }

    fn step(&mut self, silent: bool) {
        if silent {
            if self.run_len == 0 {
                self.run_start = self.frame_index;
            }
            self.run_len += 1;
            if !self.reported && self.run_len >= self.min_run_frames {
                self.reported = true;
                self.events.push(SilenceEvent { is_start: true, time_s: self.run_start as f64 / self.rate });
            }
        } else {
            if self.reported {
                self.events.push(SilenceEvent { is_start: false, time_s: self.frame_index as f64 / self.rate });
            }
            self.run_len = 0;
            self.reported = false;
        }
        self.frame_index += 1;
    }

    /// Close an open silence run at end of stream and return every event.
    pub fn finish(mut self) -> Vec<SilenceEvent> {
        if self.reported {
            self.events.push(SilenceEvent { is_start: false, time_s: self.frame_index as f64 / self.rate });
        }
        self.events
    }
}

/// Port of `_parse_karaoke_boundaries`: `(audio_start, audio_end)` in seconds, or `(None, None)` when the
/// file is effectively silent. `duration` is the unpadded duration.
pub fn boundaries(events: &[SilenceEvent], duration: f64) -> (Option<f64>, Option<f64>) {
    let mut audio_start = 0.0;
    if let Some(first) = events.first() {
        if first.is_start && first.time_s <= 0.11 {
            let leading_end = events[1..].iter().find(|e| !e.is_start).map(|e| e.time_s);
            match leading_end {
                Some(end) if end < duration - 0.15 => audio_start = end,
                _ => return (None, None),
            }
        }
    }
    let mut audio_end = duration;
    if let Some(last_start) = events.iter().rposition(|e| e.is_start) {
        let trailing_start = events[last_start].time_s;
        let later_end = events[last_start + 1..].iter().rev().find(|e| !e.is_start).map(|e| e.time_s);
        match later_end {
            Some(end) if end < duration - 0.15 => {}
            _ => audio_end = duration.min(trailing_start),
        }
    }
    if audio_end < audio_start {
        return (None, None);
    }
    (Some(audio_start), Some(audio_end))
}
