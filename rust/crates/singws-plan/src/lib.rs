//! Pure transition planning, ported from `transition_analysis.py` operation-for-operation so results are bit-identical.
//! No I/O and no clock: every function takes its metadata as an argument.
//!
//! Stage C runs this in SHADOW mode against the Python answers; it never drives playback.
//!
//! Note on `audio_boundaries`: the Python version also "bridges" quiet gaps inside the loud range, but that loop only mutates a
//! local list that never reaches the return value (the result is computed from the first/last confirmed windows). It is omitted
//! here on purpose; the real-cache parity run confirms the observable behaviour is unchanged.

pub const ANALYSIS_VERSION: i64 = 1;
pub const DEFAULT_HOP_SECONDS: f64 = 0.1;
const MIN_DB: f64 = -96.0;

fn finite(v: f64, default: f64) -> f64 {
    if v.is_finite() { v } else { default }
}

fn d_version() -> i64 { ANALYSIS_VERSION }
fn d_hop() -> f64 { DEFAULT_HOP_SECONDS }

/// The subset of `TransitionAnalysis` the planning rules read (field names match `TransitionAnalysis.to_dict`).
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Record {
    #[serde(default = "d_version")]
    pub analysis_version: i64,
    #[serde(default)]
    pub duration: f64,
    #[serde(default = "d_hop")]
    pub hop_seconds: f64,
    #[serde(default)]
    pub audio_start: Option<f64>,
    #[serde(default)]
    pub audio_end: Option<f64>,
    #[serde(default)]
    pub fade_start: Option<f64>,
    #[serde(default)]
    pub fade_confidence: f64,
    #[serde(default)]
    pub visual_start: Option<f64>,
    #[serde(default)]
    pub visual_end: Option<f64>,
    #[serde(default)]
    pub visual_confidence: f64,
    #[serde(default)]
    pub effective_karaoke_end: Option<f64>,
    #[serde(default)]
    pub safe_for_early_completion: bool,
}

impl Record {
    /// `TransitionAnalysis.is_valid` with the default 0.5 s duration tolerance.
    pub fn is_valid(&self) -> bool {
        if self.analysis_version != ANALYSIS_VERSION {
            return false;
        }
        if !self.duration.is_finite() || self.duration <= 0.0 || !self.hop_seconds.is_finite() || self.hop_seconds <= 0.0 {
            return false;
        }
        let limit = self.duration + 0.5_f64.max(0.0);
        for v in [self.audio_start, self.audio_end, self.fade_start, self.visual_start, self.visual_end, self.effective_karaoke_end]
            .into_iter()
            .flatten()
        {
            if !v.is_finite() || v < 0.0 || v > limit {
                return false;
            }
        }
        if let (Some(a), Some(b)) = (self.audio_start, self.audio_end) {
            if a > b {
                return false;
            }
        }
        if let (Some(a), Some(b)) = (self.visual_start, self.visual_end) {
            if a > b {
                return false;
            }
        }
        let known: Vec<f64> = [self.audio_end, self.visual_end].into_iter().flatten().collect();
        if let (Some(eff), false) = (self.effective_karaoke_end, known.is_empty()) {
            let hi = known.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            if eff + 1e-6 < hi {
                return false;
            }
        }
        if self.safe_for_early_completion {
            match (self.audio_end, self.visual_end, self.effective_karaoke_end) {
                (Some(_), Some(_), Some(eff)) => {
                    if eff >= self.duration {
                        return false;
                    }
                }
                _ => return false,
            }
        }
        for c in [self.fade_confidence, self.visual_confidence] {
            if !c.is_finite() || !(0.0..=1.0).contains(&c) {
                return false;
            }
        }
        true
    }
}

fn clean(envelope: &[f64]) -> Vec<f64> {
    envelope.iter().map(|v| finite(*v, MIN_DB)).collect()
}

/// `audio_boundaries_from_envelope` with its default threshold/confirmation parameters made explicit.
pub fn audio_boundaries(envelope: &[f64], hop: f64, threshold_db: f64, confirmation_seconds: f64) -> (Option<f64>, Option<f64>) {
    let hop = finite(hop, 0.0);
    if hop <= 0.0 {
        return (None, None);
    }
    let values = clean(envelope);
    if values.is_empty() {
        return (None, None);
    }
    let threshold = finite(threshold_db, -55.0);
    let required = ((confirmation_seconds.max(0.0) / hop).ceil() as i64).max(1);
    let loud: Vec<bool> = values.iter().map(|v| *v > threshold).collect();

    let mut first: Option<i64> = None;
    let mut run: i64 = 0;
    for (index, active) in loud.iter().enumerate() {
        run = if *active { run + 1 } else { 0 };
        if run >= required {
            first = Some(index as i64 - run + 1);
            break;
        }
    }
    let Some(first) = first else { return (None, None) };

    let mut last: Option<i64> = None;
    run = 0;
    for index in (0..loud.len()).rev() {
        run = if loud[index] { run + 1 } else { 0 };
        if run >= required {
            last = Some(index as i64 + run - 1);
            break;
        }
    }
    let Some(last) = last else { return (None, None) };
    if last < first {
        return (None, None);
    }
    let len = values.len() as f64;
    (Some(first as f64 * hop), Some((len * hop).min((last + 1) as f64 * hop)))
}

/// `estimate_fade_out_from_envelope`: a sustained, mostly descending outro fade, or `(None, 0.0)`.
pub fn estimate_fade_out(envelope: &[f64], hop: f64, minimum_fade_seconds: f64) -> (Option<f64>, f64) {
    let hop = finite(hop, 0.0);
    let values = clean(envelope);
    if hop <= 0.0 || values.len() < 3 {
        return (None, 0.0);
    }
    let (_start, audio_end) = audio_boundaries(&values, hop, -55.0, 0.3);
    let Some(audio_end) = audio_end else { return (None, 0.0) };
    let end_index = (values.len() as i64).min(((audio_end / hop).ceil() as i64).max(1));
    let required = ((minimum_fade_seconds.max(0.5) / hop).ceil() as i64).max(3);
    if end_index < required {
        return (None, 0.0);
    }
    let lo = (end_index - (12.0 / hop) as i64).max(0);
    let hi = end_index - required + 1; // exclusive, like range()
    let mut start_index = lo;
    while start_index < hi {
        let section = &values[start_index as usize..end_index as usize];
        if (section.len() as i64) >= required && section[0] - section[section.len() - 1] >= 8.0 {
            let downward = section.windows(2).filter(|w| w[1] <= w[0] + 1.0).count();
            let ratio = downward as f64 / ((section.len() as i64 - 1).max(1)) as f64;
            if ratio >= 0.75 {
                return (Some(start_index as f64 * hop), 1.0_f64.min(0.65 + (ratio - 0.75) * 1.4));
            }
        }
        start_index += 1;
    }
    (None, 0.0)
}

/// `bgm_audio_window`: content bounds for a background track.
pub fn bgm_audio_window(record: Option<&Record>, duration: f64) -> (f64, f64) {
    let duration = finite(duration, 0.0).max(0.0);
    let Some(r) = record else { return (0.0, duration) };
    if !r.is_valid() || (r.duration - duration).abs() > 1.0 {
        return (0.0, duration);
    }
    let margin = 0.25_f64.min(0.05_f64.max(r.hop_seconds));
    let start = (r.audio_start.unwrap_or(0.0) - margin).max(0.0);
    let end = match r.audio_end {
        Some(e) => duration.min(e + margin),
        None => duration,
    };
    if end <= start {
        return (0.0, duration);
    }
    (start, end)
}

/// `select_bgm_crossfade_seconds`: bounded crossfade length plus the reason string the app logs.
pub fn select_crossfade(record: Option<&Record>, default_seconds: f64) -> (f64, &'static str) {
    let fallback = 2.0_f64.max(8.0_f64.min(finite(default_seconds, 5.0)));
    let Some(r) = record else { return (fallback, "metadata_unavailable") };
    if r.analysis_version != ANALYSIS_VERSION {
        return (fallback, "metadata_unavailable");
    }
    let duration = finite(r.duration, 0.0);
    let fade_confidence = finite(r.fade_confidence, 0.0);
    let audio_end = r.audio_end.filter(|v| v.is_finite());
    let fade_start = r.fade_start.filter(|v| v.is_finite());
    let Some(audio_end) = audio_end.filter(|_| duration > 0.0) else {
        return (fallback, "audio_boundary_unavailable");
    };
    let dead_tail = (duration - audio_end).max(0.0);
    if dead_tail >= 1.0 {
        return (fallback, "verified_dead_tail");
    }
    if let Some(fs) = fade_start {
        if fade_confidence >= 0.8 {
            let fade_length = (audio_end - fs).max(0.0);
            return (4.0_f64.max(8.0_f64.min(fade_length)), "natural_fade");
        }
    }
    if fade_confidence <= 0.2 {
        return (2.5, "hard_ending");
    }
    (fallback, "typical_ending")
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct EndOutcome {
    pub effective: Option<f64>,
    pub safe: bool,
    pub reason: &'static str,
    /// `Some` only on the verified path; the Python code leaves `safety_margin` untouched otherwise.
    pub safety_margin: Option<f64>,
}

/// `calculate_effective_karaoke_end`: a karaoke end is authorised only from independent audio AND visual evidence.
pub fn effective_karaoke_end(r: &Record, minimum_visual_confidence: f64, safety_margin: f64) -> EndOutcome {
    let audio_end = r.audio_end.filter(|v| v.is_finite());
    let visual_end = r.visual_end.filter(|v| v.is_finite());
    let duration = finite(r.duration, 0.0);
    let visual_confidence = finite(r.visual_confidence, 0.0);
    let margin = finite(safety_margin, 0.3).max(0.0);
    let Some(audio_end) = audio_end else {
        return EndOutcome { effective: None, safe: false, reason: "audio_end_unverified", safety_margin: None };
    };
    let Some(visual_end) = visual_end.filter(|_| visual_confidence >= minimum_visual_confidence) else {
        return EndOutcome { effective: None, safe: false, reason: "visual_end_unverified", safety_margin: None };
    };
    let raw = audio_end.max(visual_end) + margin;
    let effective = if duration > 0.0 { duration.min(raw) } else { raw };
    let safe = duration > 0.0 && effective < duration;
    EndOutcome {
        effective: Some(effective),
        safe,
        reason: if safe { "verified_audio_and_visual" } else { "normal_eos" },
        safety_margin: Some(margin),
    }
}

// ---- JSON bridge for the Python shadow hook ----------------------------------------------------------------------------

#[derive(serde::Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Request {
    Boundaries {
        envelope: Vec<f64>,
        #[serde(default = "d_hop")]
        hop: f64,
        #[serde(default = "d_threshold")]
        threshold_db: f64,
        #[serde(default = "d_confirm")]
        confirmation_seconds: f64,
    },
    Fade {
        envelope: Vec<f64>,
        #[serde(default = "d_hop")]
        hop: f64,
        #[serde(default = "d_min_fade")]
        minimum_fade_seconds: f64,
    },
    Valid { record: Record },
    Window { record: Option<Record>, duration: f64 },
    Crossfade { record: Option<Record>, #[serde(default = "d_default_fade")] default_seconds: f64 },
    EffectiveEnd {
        record: Record,
        #[serde(default = "d_min_visual")]
        minimum_visual_confidence: f64,
        #[serde(default = "d_margin")]
        safety_margin: f64,
    },
}

fn d_threshold() -> f64 { -55.0 }
fn d_confirm() -> f64 { 0.3 }
fn d_min_fade() -> f64 { 2.0 }
fn d_default_fade() -> f64 { 5.0 }
fn d_min_visual() -> f64 { 0.85 }
fn d_margin() -> f64 { 0.3 }

pub fn plan_json(request_json: &str) -> Result<String, String> {
    let req: Request = serde_json::from_str(request_json).map_err(|e| format!("request: {e}"))?;
    let out = match req {
        Request::Boundaries { envelope, hop, threshold_db, confirmation_seconds } => {
            let (s, e) = audio_boundaries(&envelope, hop, threshold_db, confirmation_seconds);
            serde_json::json!({ "start": s, "end": e })
        }
        Request::Fade { envelope, hop, minimum_fade_seconds } => {
            let (f, c) = estimate_fade_out(&envelope, hop, minimum_fade_seconds);
            serde_json::json!({ "fade_start": f, "confidence": c })
        }
        Request::Valid { record } => serde_json::json!({ "valid": record.is_valid() }),
        Request::Window { record, duration } => {
            let (s, e) = bgm_audio_window(record.as_ref(), duration);
            serde_json::json!({ "start": s, "end": e })
        }
        Request::Crossfade { record, default_seconds } => {
            let (s, reason) = select_crossfade(record.as_ref(), default_seconds);
            serde_json::json!({ "seconds": s, "reason": reason })
        }
        Request::EffectiveEnd { record, minimum_visual_confidence, safety_margin } => {
            serde_json::to_value(effective_karaoke_end(&record, minimum_visual_confidence, safety_margin)).map_err(|e| e.to_string())?
        }
    };
    serde_json::to_string(&out).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rec() -> Record {
        Record {
            analysis_version: 1, duration: 200.0, hop_seconds: 0.1, audio_start: Some(0.0), audio_end: Some(198.0), fade_start: None,
            fade_confidence: 0.0, visual_start: None, visual_end: None, visual_confidence: 0.0, effective_karaoke_end: None,
            safe_for_early_completion: false,
        }
    }

    /// 10 s of loud audio, a 5 s linear fade to -80 dB, then 5 s of silence (hop 0.1 s).
    fn fading_envelope() -> Vec<f64> {
        let mut v = vec![-10.0; 100];
        v.extend((0..50).map(|i| -10.0 - 1.4 * i as f64));
        v.extend(vec![-96.0; 50]);
        v
    }

    #[test]
    fn boundaries_need_a_confirmed_loud_run_at_each_edge() {
        let mut env = vec![-96.0; 10];
        env.extend(vec![-20.0; 100]);
        env.extend(vec![-96.0; 20]);
        let (s, e) = audio_boundaries(&env, 0.1, -55.0, 0.3);
        assert_eq!((s, e), (Some(10.0 * 0.1), Some(110.0 * 0.1)));
        // a 2-window blip (0.2 s) is shorter than the 0.3 s confirmation and is not content
        assert_eq!(audio_boundaries(&[-96.0, -10.0, -10.0, -96.0, -96.0], 0.1, -55.0, 0.3), (None, None));
        assert_eq!(audio_boundaries(&[], 0.1, -55.0, 0.3), (None, None));
        assert_eq!(audio_boundaries(&[-10.0; 50], 0.0, -55.0, 0.3), (None, None));
        assert_eq!(audio_boundaries(&[-96.0; 50], 0.1, -55.0, 0.3), (None, None));
    }

    #[test]
    fn non_finite_samples_count_as_silence_and_the_end_is_clamped_to_the_length() {
        let env = [f64::NAN, -10.0, -10.0, -10.0, -10.0];
        let (s, e) = audio_boundaries(&env, 0.1, -55.0, 0.3);
        assert_eq!(s, Some(0.1));
        assert_eq!(e, Some(5.0 * 0.1));
    }

    #[test]
    fn fade_is_found_in_a_sustained_descent_and_not_in_a_hard_cut() {
        // Pinned to the app's own answer for this envelope (transition_analysis.estimate_fade_out_from_envelope gives (1.3, 1.0)).
        // The heuristic searches the last 12 s before the audio end and treats flat loud audio as "not rising", so the earliest
        // qualifying window wins: 1.3 s here, although the audible fade starts at 10 s. Ported as-is; fixing it is a Python change.
        let (start, conf) = estimate_fade_out(&fading_envelope(), 0.1, 2.0);
        assert!((start.unwrap() - 1.3).abs() < 1e-9, "{start:?}");
        assert_eq!(conf, 1.0);
        let mut hard = vec![-10.0; 150];
        hard.extend(vec![-96.0; 30]);
        assert_eq!(estimate_fade_out(&hard, 0.1, 2.0), (None, 0.0));
        assert_eq!(estimate_fade_out(&[-10.0, -10.0], 0.1, 2.0), (None, 0.0));
    }

    #[test]
    fn window_defaults_to_the_whole_track_unless_the_record_matches() {
        let r = rec();
        assert_eq!(bgm_audio_window(None, 200.0), (0.0, 200.0));
        assert_eq!(bgm_audio_window(Some(&r), 205.0), (0.0, 205.0)); // duration differs by more than 1 s
        let (s, e) = bgm_audio_window(Some(&r), 200.0);
        assert_eq!(s, 0.0);
        assert!((e - 198.1).abs() < 1e-9); // audio_end 198.0 + 0.1 s margin (hop)
        let mut late = rec();
        late.audio_start = Some(3.0);
        assert!((bgm_audio_window(Some(&late), 200.0).0 - 2.9).abs() < 1e-9);
        let mut bad = rec();
        bad.analysis_version = 2;
        assert_eq!(bgm_audio_window(Some(&bad), 200.0), (0.0, 200.0));
    }

    #[test]
    fn crossfade_reasons_follow_the_app_policy() {
        assert_eq!(select_crossfade(None, 5.0), (5.0, "metadata_unavailable"));
        assert_eq!(select_crossfade(None, 99.0), (8.0, "metadata_unavailable")); // default is clamped to 2..8
        assert_eq!(select_crossfade(None, 0.1), (2.0, "metadata_unavailable"));
        let mut r = rec();
        r.audio_end = None;
        assert_eq!(select_crossfade(Some(&r), 5.0), (5.0, "audio_boundary_unavailable"));
        let mut r = rec();
        r.duration = 200.0;
        r.audio_end = Some(190.0); // 10 s of dead tail
        assert_eq!(select_crossfade(Some(&r), 5.0), (5.0, "verified_dead_tail"));
        let mut r = rec();
        r.audio_end = Some(199.5);
        r.fade_start = Some(193.0);
        r.fade_confidence = 0.9;
        assert_eq!(select_crossfade(Some(&r), 5.0), (6.5, "natural_fade"));
        r.fade_start = Some(199.0); // a short natural fade is still held to at least 4 s
        assert_eq!(select_crossfade(Some(&r), 5.0), (4.0, "natural_fade"));
        r.fade_start = None;
        r.fade_confidence = 0.1;
        assert_eq!(select_crossfade(Some(&r), 5.0), (2.5, "hard_ending"));
        r.fade_confidence = 0.5;
        assert_eq!(select_crossfade(Some(&r), 6.0), (6.0, "typical_ending"));
    }

    #[test]
    fn a_karaoke_end_needs_both_audio_and_visual_evidence() {
        let mut r = rec();
        r.audio_end = None;
        assert_eq!(effective_karaoke_end(&r, 0.85, 0.3).reason, "audio_end_unverified");
        let mut r = rec();
        r.audio_end = Some(190.0);
        let o = effective_karaoke_end(&r, 0.85, 0.3);
        assert_eq!((o.effective, o.safe, o.reason, o.safety_margin), (None, false, "visual_end_unverified", None));
        r.visual_end = Some(191.0);
        r.visual_confidence = 0.84; // just below the bar
        assert_eq!(effective_karaoke_end(&r, 0.85, 0.3).reason, "visual_end_unverified");
        r.visual_confidence = 0.9;
        let o = effective_karaoke_end(&r, 0.85, 0.3);
        assert_eq!((o.safe, o.reason), (true, "verified_audio_and_visual"));
        assert!((o.effective.unwrap() - 191.3).abs() < 1e-9); // the later of the two ends plus the margin
        assert_eq!(o.safety_margin, Some(0.3));
    }

    #[test]
    fn confidence_thresholds_are_inclusive_exactly_where_the_app_puts_them() {
        // visual confidence bar: >= 0.85 verifies
        let mut r = rec();
        r.audio_end = Some(190.0);
        r.visual_end = Some(191.0);
        r.visual_confidence = 0.85;
        assert!(effective_karaoke_end(&r, 0.85, 0.3).safe);
        r.visual_confidence = 0.8499999;
        assert!(!effective_karaoke_end(&r, 0.85, 0.3).safe);
        // natural fade: fade_confidence >= 0.8 with a fade start
        let mut f = rec();
        f.audio_end = Some(199.5);
        f.fade_start = Some(193.0);
        f.fade_confidence = 0.8;
        assert_eq!(select_crossfade(Some(&f), 5.0).1, "natural_fade");
        f.fade_confidence = 0.79;
        assert_eq!(select_crossfade(Some(&f), 5.0).1, "typical_ending");
        // hard ending: fade_confidence <= 0.2
        f.fade_confidence = 0.2;
        assert_eq!(select_crossfade(Some(&f), 5.0).1, "hard_ending");
        f.fade_confidence = 0.21;
        assert_eq!(select_crossfade(Some(&f), 5.0).1, "typical_ending");
        // dead tail: >= 1.0 s of silence at the end keeps the default
        let mut d = rec();
        d.audio_end = Some(199.0);
        assert_eq!(select_crossfade(Some(&d), 5.0).1, "verified_dead_tail");
        d.audio_end = Some(199.01);
        assert_ne!(select_crossfade(Some(&d), 5.0).1, "verified_dead_tail");
    }

    #[test]
    fn an_end_at_the_container_end_gives_no_early_trim() {
        let mut r = rec();
        r.audio_end = Some(199.9);
        r.visual_end = Some(199.9);
        r.visual_confidence = 1.0;
        let o = effective_karaoke_end(&r, 0.85, 0.3);
        assert_eq!((o.effective, o.safe, o.reason), (Some(200.0), false, "normal_eos"));
    }

    #[test]
    fn validity_rejects_corrupt_metadata() {
        assert!(rec().is_valid());
        let mut r = rec();
        r.duration = 0.0;
        assert!(!r.is_valid());
        let mut r = rec();
        r.audio_end = Some(500.0);
        assert!(!r.is_valid());
        let mut r = rec();
        r.audio_start = Some(10.0);
        r.audio_end = Some(5.0);
        assert!(!r.is_valid());
        let mut r = rec();
        r.fade_confidence = 1.5;
        assert!(!r.is_valid());
        let mut r = rec();
        r.safe_for_early_completion = true; // claims safety without evidence
        assert!(!r.is_valid());
        let mut r = rec();
        r.visual_end = Some(199.0);
        r.effective_karaoke_end = Some(150.0); // earlier than a known end
        assert!(!r.is_valid());
        let mut r = rec();
        r.analysis_version = 0;
        assert!(!r.is_valid());
    }

    #[test]
    fn json_bridge_round_trips_every_op() {
        let env = serde_json::to_string(&fading_envelope()).unwrap();
        let out = plan_json(&format!(r#"{{"op":"boundaries","envelope":{env}}}"#)).unwrap();
        assert!(out.contains("\"start\":0.0"), "{out}");
        assert!(plan_json(&format!(r#"{{"op":"fade","envelope":{env}}}"#)).unwrap().contains("fade_start"));
        let record = r#"{"duration":200.0,"audio_end":198.0,"audio_start":0.0}"#;
        assert!(plan_json(&format!(r#"{{"op":"valid","record":{record}}}"#)).unwrap().contains("true"));
        assert!(plan_json(&format!(r#"{{"op":"window","record":{record},"duration":200.0}}"#)).unwrap().contains("198.1"));
        assert!(plan_json(&format!(r#"{{"op":"crossfade","record":{record}}}"#)).unwrap().contains("verified_dead_tail"));
        assert!(plan_json(&format!(r#"{{"op":"effective_end","record":{record}}}"#)).unwrap().contains("visual_end_unverified"));
        assert!(plan_json(r#"{"op":"nope"}"#).is_err());
        assert!(plan_json("{").is_err());
    }
}
