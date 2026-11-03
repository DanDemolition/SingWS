#!/usr/bin/env python3
"""Mutation check for rust/crates/singws-shell/src/karaoke_proto.rs. Every mutant must be caught by the parity run (tools/karaoke_transport_parity.py: a case becomes
DIFFERENT) or by the module's own unit tests (cargo test -p singws-karaoke-proto), or carry a note saying why it is equivalent. The file is restored afterwards.

    python3 tools/mutate_karaoke_proto.py                     # all
    MUTATE_FIRST=40 python3 tools/mutate_karaoke_proto.py     # only the newest ones
Uses CARGO_TARGET_DIR (default ~/.cache/singws-rust-target-karaoke-proto) and a Python with PyQt6 (default /Users/daniel/Documents/SingWS/.venv or ROOT/.venv)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "rust/crates/singws-shell/src/karaoke_proto.rs"
TARGET = os.environ.get("CARGO_TARGET_DIR") or os.path.expanduser("~/.cache/singws-rust-target-karaoke-proto")
ENV = dict(os.environ, CARGO_TARGET_DIR=TARGET, QT_QPA_PLATFORM="offscreen", SINGWS_HOME=tempfile.mkdtemp(prefix="singws-mut-"))
ENV["PATH"] = os.path.expanduser("~/.cargo/bin") + os.pathsep + ENV["PATH"]
PY = next(str(p) for p in (ROOT / ".venv/bin/python", Path("/Users/daniel/Documents/SingWS/.venv/bin/python")) if p.exists())
FIRST = int(os.environ.get("MUTATE_FIRST", "0"))
IDS = {int(x) for x in os.environ.get("MUTATE_IDS", "").split(",") if x}

M = []
def m(old, new, note=None):
    M.append((old, new, note))

# deadlines
m("self.deadline = now + 4.0;\n        self.hard_deadline = now + 20.0;", "self.deadline = now + 5.0;\n        self.hard_deadline = now + 20.0;")
m("self.hard_deadline = now + 20.0;", "self.hard_deadline = now + 25.0;")
m("self.deadline = now + 0.7;", "self.deadline = now + 0.8;")
m("self.deadline = now + 3.0;", "self.deadline = now + 4.0;")
m("self.hard_deadline = now + 10.0;", "self.hard_deadline = now + 15.0;")
m("            // `loaded_at` is deliberately NOT reset: the Python transport keeps the value from the start of the song.\n", "            self.loaded_at = None;\n")
m("self.loaded_at.unwrap_or(now) + 3.0", "self.loaded_at.unwrap_or(now) + 4.0")
m("if loaded && self.loaded_at.is_none() {", "if loaded {")
m("if loaded && ready {", "if ready {")
m("if loaded && ready {", "if loaded {")
m("} else if now > self.deadline && ((loaded", "} else if now >= self.deadline && ((loaded")
m("(loaded && now > self.loaded_at.unwrap_or(now) + 3.0) || now > self.hard_deadline", "(loaded && now > self.loaded_at.unwrap_or(now) + 3.0) || now > self.hard_deadline + 100.0")
m("(loaded && now > self.loaded_at.unwrap_or(now) + 3.0) || now > self.hard_deadline", "(loaded && now > self.loaded_at.unwrap_or(now) + 3.0)")
m("self.drop_voice(if loaded { \"the voice was not ready in time\" } else { \"the picture did not load in time\" });", "self.drop_voice(if loaded { \"the picture did not load in time\" } else { \"the voice was not ready in time\" });")
m("                    self.drop_voice(if loaded { \"the voice was not ready in time\" } else { \"the picture did not load in time\" });\n                    self.picture.play();", "                    self.drop_voice(if loaded { \"the voice was not ready in time\" } else { \"the picture did not load in time\" });")
m("v.slave_to(pos, 0.0);\n                    v.play();", "v.slave_to(pos, 1.0);\n                    v.play();")
m("v.slave_to(pos, 0.0);\n                    v.play();", "v.play();")
m("self.origin = v.position();", "self.origin = 0.0;")
m("v.position() > self.origin + 0.002", "v.position() > self.origin")
m("v.position() > self.origin + 0.002", "v.position() > self.origin + 0.5")
m("if moved || now > self.deadline {", "if moved {")
m("if moved || now > self.deadline {", "if now > self.deadline {")
m("if moved || now > self.deadline {", "if moved || now >= self.deadline {", "the deadlines are sums of inexact floats in both implementations, so `now` never equals one exactly")
m("                    self.picture.play();\n                    self.phase = Phase::Running;\n                    self.timer_stop(TimerKind::Voice);", "                    self.phase = Phase::Running;\n                    self.timer_stop(TimerKind::Voice);")
m("                    self.phase = Phase::Running;\n                    self.timer_stop(TimerKind::Voice);", "                    self.phase = Phase::VoiceWait;\n                    self.timer_stop(TimerKind::Voice);")
m("        if self.user_paused {\n            return;\n        }\n        match self.phase {", "        match self.phase {")
m("if self.voice.is_none() || self.stopped || matches!(self.phase, Phase::Idle | Phase::Running) {", "if self.voice.is_none() || matches!(self.phase, Phase::Idle | Phase::Running) {", "stop() already stops the voice timer and clears the phase, so `stopped` is a second guard")
m("if self.voice.is_none() || self.stopped || matches!(self.phase, Phase::Idle | Phase::Running) {\n            self.timer_stop(TimerKind::Voice);\n            return;", "if self.voice.is_none() || self.stopped || matches!(self.phase, Phase::Idle | Phase::Running) {\n            return;", "the voice timer keeps firing as a no-op: no command differs")
# start_full
m("let source = if mode == \"cdg\" || mode == \"mp4\" { a.video_path.as_str() } else { a.audio_path.as_str() };", "let source = if mode == \"cdg\" { a.video_path.as_str() } else { a.audio_path.as_str() };")
m("let source = if mode == \"cdg\" || mode == \"mp4\" { a.video_path.as_str() } else { a.audio_path.as_str() };", "let source = a.video_path.as_str();")
m("let mode = a.mode.to_lowercase();", "let mode = a.mode.clone();", "the scenarios use lower-case modes; Python lower-cases (kept as a documented gap, see the spike doc)")
m("let audio = if self.cdg { Some(a.audio_path.as_str()) } else { None };", "let audio = Some(a.audio_path.as_str());")
m("self.seek_gap = if self.cdg { a.zones.clone() } else { Vec::new() };", "self.seek_gap = a.zones.clone();")
m("self.seek_gap = if self.cdg { a.zones.clone() } else { Vec::new() };", "self.seek_gap = Vec::new();")
m("        let start_seconds = singws_cdg::clamp_seek(&self.seek_gap, a.start_seconds);", "        let start_seconds = a.start_seconds;")
m("        let start_seconds = singws_cdg::clamp_seek(&self.seek_gap, start_seconds);\n        self.user_paused = false;\n        if self.voice.is_some() {\n            self.begin_voice_start(now);", "        self.user_paused = false;\n        if self.voice.is_some() {\n            self.begin_voice_start(now);", "legacy start() used by the demo only; the parity runner goes through start_full (covered by the unit test of the CDG tail clamp)")
m("if source.is_empty() {", "if source.is_empty() && false {")
m("!voice_on, self.pitch.round_ties_even() as i32", "voice_on, self.pitch.round_ties_even() as i32")
m("self.pitch.round_ties_even() as i32, (self.tempo_ratio * 100.0).round_ties_even() as i32", "self.pitch.round() as i32, (self.tempo_ratio * 100.0).round_ties_even() as i32")
m("(self.tempo_ratio * 100.0).round_ties_even() as i32", "(self.tempo_ratio * 100.0).round() as i32")
m("(self.tempo_ratio * 100.0).round_ties_even() as i32", "(self.tempo_ratio * 100.0) as i32")
m("            if voice_on {\n                self.drop_voice(\"the picture failed to load\");\n            }", "")
m("self.drop_voice(\"the picture failed to load\");", "self.drop_voice(\"the picture did not load in time\");")
m("        if voice_on {\n            self.begin_voice_start(now);\n        }\n        self.finish_start(now, start_seconds);\n        Ok(())", "        self.finish_start(now, start_seconds);\n        Ok(())")
m("let path = if self.audio_path.is_empty() { self.video_path.clone() } else { self.audio_path.clone() };", "let path = self.audio_path.clone();")
m("let (norm, vol, key, tempo) = (self.voice_norm, self.volume, self.pitch, self.tempo_ratio * 100.0);", "let (norm, vol, key, tempo) = (1.0, self.volume, self.pitch, self.tempo_ratio * 100.0);")
m("let (norm, vol, key, tempo) = (self.voice_norm, self.volume, self.pitch, self.tempo_ratio * 100.0);", "let (norm, vol, key, tempo) = (self.voice_norm, 1.0, self.pitch, self.tempo_ratio * 100.0);")
m("let (norm, vol, key, tempo) = (self.voice_norm, self.volume, self.pitch, self.tempo_ratio * 100.0);", "let (norm, vol, key, tempo) = (self.voice_norm, self.volume, 0.0, self.tempo_ratio * 100.0);")
m("let (norm, vol, key, tempo) = (self.voice_norm, self.volume, self.pitch, self.tempo_ratio * 100.0);", "let (norm, vol, key, tempo) = (self.voice_norm, self.volume, self.pitch, 100.0);")
m("                    if start_seconds > 0.0 {\n                        v.seek(start_seconds);\n                    }", "                    v.seek(start_seconds);")
m("                    if start_seconds > 0.0 {\n                        v.seek(start_seconds);\n                    }", "")
m("        if start_seconds > 0.0 {\n            self.picture.seek_ms((start_seconds * 1000.0) as i64);\n        }\n        self.picture.set_volume(self.volume);", "        self.picture.seek_ms((start_seconds * 1000.0) as i64);\n        self.picture.set_volume(self.volume);")
m("        self.picture.set_volume(self.volume);\n        self.stopped = false;", "        self.stopped = false;")
m("        self.visual_stall_emitted = false;\n        if !self.follow_in_poll {", "        if !self.follow_in_poll {", "visual_stall_emitted is only reset on a second start in the same object; two_starts_in_a_row has a picture that never stalls")
m("        self.started_emitted = false;\n        self.ended_emitted = false;\n        self.playing_polls_without_visual = 0;\n        self.visual_stall_emitted", "        self.ended_emitted = false;\n        self.playing_polls_without_visual = 0;\n        self.visual_stall_emitted")
m("        self.started_emitted = false;\n        self.ended_emitted = false;\n        self.playing_polls_without_visual = 0;\n        self.visual_stall_emitted", "        self.started_emitted = false;\n        self.playing_polls_without_visual = 0;\n        self.visual_stall_emitted")
m("        self.user_paused = false;\n        let ok = self.picture.load(", "        let ok = self.picture.load(")
# drop_voice
m("            v.stop();\n        }\n        self.dropped = Some(reason.to_string());", "        }\n        self.dropped = Some(reason.to_string());")
m("        self.picture.set_shadow_audio(false);\n    }\n\n    pub fn stop", "    }\n\n    pub fn stop")
m("falling back to mpv audio for this song: {reason}", "falling back to mpv audio: {reason}")
m("could not open '{path}': {e}", "could not open {path}: {e}")
# stop
m("        self.stopped = true;\n        self.timer_stop(TimerKind::Poll);", "        self.timer_stop(TimerKind::Poll);", "every timer that reads `stopped` is stopped by stop() too")
m("        self.timer_stop(TimerKind::Poll);\n        self.timer_stop(TimerKind::Follow);\n        self.timer_stop(TimerKind::Voice);\n        self.phase = Phase::Idle;\n        if let Some(v) = self.voice.as_mut() {\n            v.stop();\n        }\n        self.picture.stop();", "        self.timer_stop(TimerKind::Follow);\n        self.timer_stop(TimerKind::Voice);\n        self.phase = Phase::Idle;\n        if let Some(v) = self.voice.as_mut() {\n            v.stop();\n        }\n        self.picture.stop();", "the poll handler checks `stopped` first, so a running poll timer is a no-op after stop")
m("        self.phase = Phase::Idle;\n        if let Some(v) = self.voice.as_mut() {\n            v.stop();\n        }\n        self.picture.stop();", "        if let Some(v) = self.voice.as_mut() {\n            v.stop();\n        }\n        self.picture.stop();")
m("        if let Some(v) = self.voice.as_mut() {\n            v.stop();\n        }\n        self.picture.stop();", "        self.picture.stop();")
# seek
m("let target = singws_cdg::clamp_seek(&self.seek_gap, seconds.max(0.0));", "let target = singws_cdg::clamp_seek(&self.seek_gap, seconds);")
m("let target = singws_cdg::clamp_seek(&self.seek_gap, seconds.max(0.0));", "let target = seconds.max(0.0);")
m("matches!(self.phase, Phase::Running | Phase::Seeking | Phase::VoiceWait) {", "matches!(self.phase, Phase::Running | Phase::Seeking) {")
m("matches!(self.phase, Phase::Running | Phase::Seeking | Phase::VoiceWait) {", "matches!(self.phase, Phase::Running) {")
m("matches!(self.phase, Phase::Running | Phase::Seeking | Phase::VoiceWait) {", "matches!(self.phase, Phase::Running | Phase::Seeking | Phase::VoiceWait | Phase::Starting) {")
m("            if let Some(v) = self.voice.as_mut() {\n                v.pause();\n            }\n            self.picture.pause();\n            self.picture.seek_ms", "            self.picture.pause();\n            self.picture.seek_ms")
m("            self.picture.pause();\n            self.picture.seek_ms((target * 1000.0) as i64);\n            if let Some", "            self.picture.seek_ms((target * 1000.0) as i64);\n            if let Some")
m("            if let Some(v) = self.voice.as_mut() {\n                v.seek(target);\n            }", "")
m("            self.phase = Phase::Seeking;\n            self.deadline", "            self.deadline")
m("            self.timer_start(TimerKind::Voice, now, 10_000, true);\n            return;", "            return;")
m("        self.picture.seek_ms((target * 1000.0) as i64);\n    }\n\n    pub fn pause", "    }\n\n    pub fn pause")
# pause/resume
m("        self.user_paused = true;\n        self.picture.pause();", "        self.picture.pause();")
m("        self.user_paused = true;\n        self.picture.pause();\n        if let Some(v) = self.voice.as_mut() {\n            v.pause();\n        }", "        self.user_paused = true;\n        self.picture.pause();")
m("        self.user_paused = false;\n        if let Some(v) = self.voice.as_mut() {\n            if self.phase == Phase::Running {", "        if let Some(v) = self.voice.as_mut() {\n            if self.phase == Phase::Running {")
m("            if self.phase == Phase::Running {\n                v.play();", "            if true {\n                v.play();")
m("if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) {\n            return;", "if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::Seeking) {\n            return;")
m("if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) {\n            return;", "if self.voice.is_some() && matches!(self.phase, Phase::VoiceWait | Phase::Seeking) {\n            return;")
m("if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) {\n            return;", "if self.voice.is_some() && matches!(self.phase, Phase::Starting | Phase::VoiceWait) {\n            return;")
m("        self.picture.play();\n    }\n\n    /// A coordinated seek", "    }\n\n    /// A coordinated seek")
# is_paused
m("        if self.picture.is_seek_holding() {\n            return false;\n        }", "")
m("matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) && !self.user_paused {\n            return false;", "matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) {\n            return false;")
m("matches!(self.phase, Phase::Starting | Phase::VoiceWait | Phase::Seeking) && !self.user_paused {", "matches!(self.phase, Phase::Starting | Phase::Seeking) && !self.user_paused {")
m("        !self.picture.is_playing()\n    }", "        self.picture.is_playing()\n    }")
m("(self.picture.position_ms() as f64 / 1000.0).max(0.0)", "(self.picture.position_ms() as f64 / 1000.0)", "position is never negative in the fakes (the bridge's clock can be): the rule is max(0)")
# modifiers
m("let ratio = if tempo_ratio == 0.0 { 1.0 } else { tempo_ratio };", "let ratio = tempo_ratio;")
m("self.tempo_ratio = ratio.clamp(0.5, 2.0);", "self.tempo_ratio = ratio.clamp(0.4, 2.0);")
m("self.tempo_ratio = ratio.clamp(0.5, 2.0);", "self.tempo_ratio = ratio.clamp(0.5, 2.5);")
m("        self.pitch = semitones;\n", "")
m("        self.picture.set_tempo_ratio(self.tempo_ratio);\n        let (k, t)", "        let (k, t)")
m("            v.set_key_semitones(k);\n            v.set_tempo_percent(t);\n        } else {\n            self.picture.set_pitch_semitones(k);\n        }", "            v.set_tempo_percent(t);\n        } else {\n            self.picture.set_pitch_semitones(k);\n        }")
m("            v.set_key_semitones(k);\n            v.set_tempo_percent(t);\n        } else {\n            self.picture.set_pitch_semitones(k);\n        }", "            v.set_key_semitones(k);\n        } else {\n            self.picture.set_pitch_semitones(k);\n        }")
m("        } else {\n            self.picture.set_pitch_semitones(k);\n        }", "        } else {\n        }")
m("            v.set_tempo_percent(t);\n        } else {\n            self.picture.set_pitch_semitones(k);\n        }", "            v.set_tempo_percent(t);\n            self.picture.set_pitch_semitones(k);\n        } else {\n            self.picture.set_pitch_semitones(k);\n        }")
# volume
m("self.volume = value.clamp(0.0, 2.0);", "self.volume = value.clamp(0.0, 3.0);")
m("self.volume = value.clamp(0.0, 2.0);", "self.volume = value.clamp(-1.0, 2.0);")
m("        self.picture.set_volume(self.volume);\n        let vol = self.volume;", "        let vol = self.volume;")
m("            v.set_volume(vol);", "            let _ = vol;")
# audio spec
m("let Some(v) = self.voice.as_mut() else { return };\n        self.voice_norm", "let Some(v) = self.voice.as_mut() else { return };\n        if spec.normalize_gain_db == 0.0 { return; }\n        self.voice_norm")
m("10f64.powf(spec.normalize_gain_db / 20.0)", "10f64.powf(spec.normalize_gain_db / 10.0)")
m("        v.set_norm_gain(self.voice_norm);\n", "")
m("        v.set_eq_gains(spec.eq_enabled, &spec.eq_gains_db);\n", "")
m("v.set_eq_gains(spec.eq_enabled, &spec.eq_gains_db);", "v.set_eq_gains(true, &spec.eq_gains_db);")
m("v.set_master_params(spec.master_enabled, spec.has_master_params);", "v.set_master_params(spec.master_enabled, true);")
m("v.set_master_params(spec.master_enabled, spec.has_master_params);", "v.set_master_params(true, spec.has_master_params);")
# loop
m("let end = end_seconds.max(start);", "let end = end_seconds;", "an end below the start gives no loop either way (end > start is false)")
m("let start = start_seconds.max(0.0);", "let start = start_seconds;", "a negative loop start only reaches seek(), which clamps to 0 itself")
m("self.looping = if end > start { Some((start, end)) } else { None };", "self.looping = Some((start, end));")
m("self.looping = if end > start { Some((start, end)) } else { None };", "self.looping = if end >= start { Some((start, end)) } else { None };", "end == start is then a loop that jumps at position >= start: only reachable with a zero-length loop; documented in the case loop_invalid_is_ignored? (killed if the case covers it)")
m("if self.position_seconds() >= end {", "if self.position_seconds() > end {")
m("                self.seek(now, start);", "                self.seek(now, end);")
# visual timer
m("let v = if value == 0 { 50 } else { value };", "let v = value;")
m("self.poll_interval_us = v.clamp(16, 250) * 1000;", "self.poll_interval_us = v.clamp(10, 250) * 1000;")
m("self.poll_interval_us = v.clamp(16, 250) * 1000;", "self.poll_interval_us = v.clamp(16, 300) * 1000;")
m("            self.timer_start(TimerKind::Poll, now, iv, true); // Qt restarts", "            let _ = iv; // Qt restarts")
m("        let iv = self.poll_interval_us;\n        self.timer_start(TimerKind::Poll, now, iv, true);\n    }\n\n    /// `MpvKaraokeTransport.start`", "    }\n\n    /// `MpvKaraokeTransport.start`")
# video offset
m("        if self.picture.set_video_offset_ms(value) {\n            return;\n        }", "        self.picture.set_video_offset_ms(value);")
m("if value != 0 && !self.video_offset_warned {", "if !self.video_offset_warned {")
m("if value != 0 && !self.video_offset_warned {", "if value != 0 {")
m("            self.video_offset_warned = true;\n", "")
m("{value:+}ms CDG", "{value}ms CDG")
# fade
m("        let ms = duration_ms.max(0);\n        if ms > 0 {", "        let ms = duration_ms;\n        if ms > 0 {", "a negative duration takes the `ms <= 0` branch either way")
m("v.slide_volume(0.0, ms);", "v.slide_volume(1.0, ms);")
m("                self.volume = 0.0;\n                if on_finished {\n                    self.timer_start(TimerKind::FadeEnd, now, ms * 1000, false);", "                if on_finished {\n                    self.timer_start(TimerKind::FadeEnd, now, ms * 1000, false);")
m("self.timer_start(TimerKind::FadeEnd, now, ms * 1000, false);", "self.timer_start(TimerKind::FadeEnd, now, 0, false);")
m("                if on_finished {\n                    self.timer_start(TimerKind::FadeEnd, now, ms * 1000, false);\n                }", "")
m("        if ms <= 0 {\n            self.set_volume(0.0);", "        if ms < 0 {\n            self.set_volume(0.0);")
m("        if ms <= 0 {\n            self.set_volume(0.0);\n            if on_finished {\n                self.timer_start(TimerKind::FadeEnd, now, 0, false);\n            }\n            return true;", "        if ms <= 0 {\n            self.set_volume(0.0);\n            return true;")
m("        if ms <= 0 {\n            self.set_volume(0.0);", "        if ms <= 0 {\n            self.volume = 0.0;")
m("let steps = (ms / 40).max(1);", "let steps = (ms / 50).max(1);")
m("let steps = (ms / 40).max(1);", "let steps = ms / 40;", "ms < 40 would divide by zero: only reachable with ms in 1..39, which fade_out_steps_500ms_and_tiny covers (5 ms)")
m("(ms / steps).max(10) * 1000", "(ms / steps).max(20) * 1000")
m("(ms / steps).max(10) * 1000", "(ms / steps) * 1000")
m("start_volume: self.volume, step: 0", "start_volume: 1.0, step: 0")
m("let remaining = (1.0 - f.step as f64 / f.steps as f64).max(0.0);", "let remaining = 1.0 - f.step as f64 / f.steps as f64;", "step never exceeds steps in the stepped fade: the max(0) is a guard (floating point)")
m("let (level, done, cb) = (f.start_volume * remaining, f.step >= f.steps, f.on_finished);", "let (level, done, cb) = (f.start_volume * remaining, f.step > f.steps, f.on_finished);")
m("            if cb {\n                events.push(Event::FadeFinished);\n            }", "")
m("            self.timer_stop(TimerKind::FadeStep);\n            self.fade = None;", "            self.fade = None;", "a leftover step timer finds no fade and returns")
# follow / resync
m("let commanded = if playing { self.tempo_ratio } else { 0.0 };", "let commanded = self.tempo_ratio;")
m("let commanded = if playing { self.tempo_ratio } else { 0.0 };", "let commanded = if playing { 1.0 } else { 0.0 };")
m("        if self.phase != Phase::Running || self.voice.is_none() {\n            return;\n        }\n        let playing", "        if self.voice.is_none() {\n            return;\n        }\n        let playing")
m("self.voice.as_mut().unwrap().slave_to(pos, rate);\n        self.log_resyncs();", "self.voice.as_mut().unwrap().slave_to(pos, rate);")
m("if resyncs != seen.0 && kind == 0 {", "if resyncs != seen.0 {")
m("if resyncs != seen.0 && kind == 0 {", "if resyncs == seen.0 && kind == 0 {")
m("} else if guarded != seen.1 && (kind == 1 || kind == 2) {", "} else if guarded != seen.1 {")
m("} else if guarded != seen.1 && (kind == 1 || kind == 2) {", "} else if guarded != seen.1 && kind == 1 {")
m("let why = if kind == 1 {", "let why = if kind == 2 {")
m("            self.resync_seen = Some((resyncs, guarded)); // first look at this song: remember where the counters are, say nothing\n            return;", "            return;")
m("            self.resync_seen = Some((resyncs, guarded)); // first look at this song: remember where the counters are, say nothing\n            return;", "            self.resync_seen = Some((resyncs, guarded));")
m("        self.resync_seen = Some((resyncs, guarded));\n    }", "    }")
m("song {here:.2}s -> {target:.2}s", "song {here:.2}s -> {master:.2}s")
m("let song = v.duration();", "let song = 0.0;")
# poll
m("        if self.stopped {\n            return;\n        }\n        if self.follow_in_poll {", "        if self.follow_in_poll {", "after stop() the poll timer is stopped as well, so the check is a guard (see the mutant that removes the stop)")
m("        if self.follow_in_poll {\n            self.follow(now);\n        }\n        if let Some((start, end))", "        if let Some((start, end))")
m("let playing = self.picture.is_playing() && self.picture.position_ms() >= 40;", "let playing = self.picture.is_playing() && self.picture.position_ms() >= 60;")
m("let playing = self.picture.is_playing() && self.picture.position_ms() >= 40;", "let playing = self.picture.is_playing();")
m("            if playing && !ready {\n                self.playing_polls_without_visual += 1;\n            } else {\n                self.playing_polls_without_visual = 0;\n            }", "            if playing && !ready {\n                self.playing_polls_without_visual += 1;\n            }")
m("            let limit = ((1000.0 / interval_ms).round_ties_even() as u32).max(4);", "            let limit = ((1000.0 / interval_ms).round_ties_even() as u32).max(5);")
m("            let limit = ((1000.0 / interval_ms).round_ties_even() as u32).max(4);", "            let limit = ((1000.0 / interval_ms).round_ties_even() as u32).max(2);", "the interval is clamped to 250 ms at most, so 1000/interval is never below 4 and the max(4) floor cannot bind")
m("(1000.0 / interval_ms).round_ties_even()", "(1000.0 / interval_ms).floor()", "1000/50, 1000/100, 1000/250 and 1000/16 round the same way in the scenarios except 62.5: the 16 ms case has 1000/16 = 62.5 -> even 62 vs floor 62")
m("if playing && (ready || self.playing_polls_without_visual >= limit) {", "if playing && ready {")
m("if playing && (ready || self.playing_polls_without_visual >= limit) {", "if playing && (ready || self.playing_polls_without_visual > limit) {")
m("                self.started_emitted = true;\n                events.push(Event::Started);", "                events.push(Event::Started);")
m("if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 5000 {", "if playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 5000 {")
m("if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 5000 {", "if self.cdg && playing && !ready && self.picture.position_ms() >= 5000 {")
m("if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 5000 {", "if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 4000 {")
m("if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() >= 5000 {", "if self.cdg && playing && !ready && !self.visual_stall_emitted && self.picture.position_ms() > 5000 {", "the world moves in 10 ms steps and the stall is judged at 50 ms polls: a position of exactly 5000 ms is hit in the 16 ms case only by luck")
m("            self.visual_stall_emitted = true;\n", "")
m("events.push(Event::VisualStalled(self.video_path.clone(), self.picture.position_ms()));", "events.push(Event::VisualStalled(self.audio_path.clone(), self.picture.position_ms()));")
m("        if !self.ended_emitted && self.picture.at_end() {\n            self.ended_emitted = true;", "        if self.picture.at_end() {\n            self.ended_emitted = true;", "the poll timer is stopped by the end itself, so the handler never runs a second time")
m("            self.ended_emitted = true;\n            self.timer_stop(TimerKind::Poll);", "            self.timer_stop(TimerKind::Poll);", "the poll timer is stopped by the end itself, so the handler never runs a second time")
m("            self.ended_emitted = true;\n            self.timer_stop(TimerKind::Poll);\n            self.timer_stop(TimerKind::Voice);", "            self.ended_emitted = true;\n            self.timer_stop(TimerKind::Voice);", "ended_emitted blocks a second Ended event, so a running poll timer after the end only repeats nothing")
m("            self.timer_stop(TimerKind::Poll);\n            self.timer_stop(TimerKind::Voice);\n            if let Some(v) = self.voice.as_mut() {\n                v.stop();\n            }\n            events.push(Event::Ended);", "            self.timer_stop(TimerKind::Poll);\n            self.timer_stop(TimerKind::Voice);\n            events.push(Event::Ended);")
m("            self.timer_stop(TimerKind::Voice);\n            if let Some(v) = self.voice.as_mut() {\n                v.stop();\n            }\n            events.push(Event::Ended);", "            if let Some(v) = self.voice.as_mut() {\n                v.stop();\n            }\n            events.push(Event::Ended);")
m("            events.push(Event::Ended);", "            self.phase = Phase::Idle;\n            events.push(Event::Ended);")
# timers
m("min_by_key(|&i| (self.timers[i].due, self.timers[i].interval, self.timers[i].seq))?", "min_by_key(|&i| (self.timers[i].due, -self.timers[i].interval, self.timers[i].seq))?")
m("self.timers[idx].due = due + interval;", "self.timers[idx].due = due + interval + 1000;")
m("TimerKind::Follow, now, 30_000, true", "TimerKind::Follow, now, 40_000, true", "demo-mode follow cadence; the parity runner uses follow_in_poll")
m("self.timer_start(TimerKind::Voice, now, 10_000, true);\n            return;", "self.timer_start(TimerKind::Voice, now, 20_000, true);\n            return;")
m("        self.loaded_at = None;\n        self.timer_start(TimerKind::Voice, now, 10_000, true);\n    }", "        self.loaded_at = None;\n        self.timer_start(TimerKind::Voice, now, 20_000, true);\n    }")
m("poll_interval_us: 50_000,", "poll_interval_us: 60_000,")
m("pub fn parity_python(mut self) -> Self {\n        self.follow_in_poll = true;\n        self.measure_rate = false;", "pub fn parity_python(mut self) -> Self {\n        self.follow_in_poll = true;\n        self.measure_rate = true;")
m("pub fn parity_python(mut self) -> Self {\n        self.follow_in_poll = true;", "pub fn parity_python(mut self) -> Self {\n        self.follow_in_poll = false;")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, env=ENV, cwd=kw.get("cwd", ROOT / "rust"))


def main():
    orig = SRC.read_text()
    survivors, killed, bad = [], 0, []
    try:
        for idx, (old, new, note) in enumerate(M):
            if idx < FIRST or (IDS and idx not in IDS):
                continue
            if orig.count(old) != 1:
                bad.append((idx, orig.count(old), old[:70]))
                print(f"#{idx} BAD PATTERN (found {orig.count(old)}x): {old[:70]!r}")
                continue
            SRC.write_text(orig.replace(old, new))
            b = run(["cargo", "build", "--release", "-q", "-p", "singws-karaoke-proto"])
            how = None
            if b.returncode != 0:
                how = "does not compile"
            else:
                p = run([PY, "-I", str(ROOT / "tools/karaoke_transport_parity.py")], cwd=ROOT)
                if p.returncode != 0:
                    names = [l.split()[1] for l in p.stdout.splitlines() if l.startswith("DIFFERENT")]
                    how = "parity: " + ", ".join(names[:2]) + (" ..." if len(names) > 2 else "")
                else:
                    t = run(["cargo", "test", "--release", "-q", "-p", "singws-karaoke-proto"])
                    if t.returncode != 0:
                        how = "unit tests"
            if how:
                killed += 1
                print(f"#{idx} killed ({how})")
            else:
                survivors.append((idx, old, new, note))
                print(f"#{idx} SURVIVED{' (documented equivalent)' if note else ''}: {old[:70]!r} -> {new[:70]!r}")
    finally:
        SRC.write_text(orig)
        run(["cargo", "build", "--release", "-q", "-p", "singws-karaoke-proto"])
    undoc = [s for s in survivors if not s[3]]
    print(f"\n{killed} killed, {len(survivors) - len(undoc)} documented equivalent, {len(undoc)} UNEXPLAINED survivors, {len(bad)} bad patterns")
    return 1 if undoc or bad else 0


if __name__ == "__main__":
    sys.exit(main())
