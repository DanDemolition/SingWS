//! Stage A5 (playback orchestrator, step 1): the routing decision `play_next_file` makes between "this singer's next song was picked" and "a play_* function is
//! called". Pure: the caller passes what it already knows at each point of the Python chain and gets the route Python must take. Evaluation ORDER and every
//! quirk are copied from the app (case-sensitive `.endswith` in the dispatch but `.lower()` for the ZIP preparation check; a tuple of the wrong length raises;
//! a non-string `song_info` raises). A fact the chain has not evaluated yet is `None`; if the route depends on it the answer is `need:<field>`, which a shadow
//! reports as a disagreement (it means the two chains diverged).
//!
//! SHADOW ONLY: nothing here starts playback.

use serde::Deserialize;

/// One element of a tuple/list `song_info`: a string, or anything else together with Python's `str(x)` of it.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Item {
    S(String),
    O(String),
}

impl Item {
    fn text(&self) -> &str {
        match self {
            Item::S(s) | Item::O(s) => s,
        }
    }
    fn is_str(&self) -> bool {
        matches!(self, Item::S(_))
    }
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Info {
    Str(String),
    Seq(Vec<Item>),
    Other(bool),
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Input {
    /// `_is_external_karafun_entry(entry)`
    #[serde(default)]
    pub external: bool,
    pub info: Info,
    /// `bool(primary_path)`
    #[serde(default)]
    pub has_primary: bool,
    /// `os.path.exists(primary_path)`, known once the chain got past the external check
    #[serde(default)]
    pub primary_exists: Option<bool>,
    /// both extracted files of an MP3+G ZIP are ready (`zip_cache.cached_paths_if_ready`)
    #[serde(default)]
    pub zip_cached: Option<bool>,
    /// `is_mp3g_zip(song_info)`
    #[serde(default)]
    pub mp3g: Option<bool>,
    /// `os.path.exists(<base>.mp3)` for a lone `.cdg`
    #[serde(default)]
    pub pair: Option<bool>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Route {
    ExternalKarafun,
    RejectMissingFile,
    ZipPrepare,
    CdgMp3Dual,
    Mp4,
    Mp3,
    Mp3gZip,
    RejectUnsupportedTuple,
    RejectInvalidZip,
    RejectMissingCdgAudioPair,
    RejectUnsupportedFormat,
    /// Python raises on this input (a tuple of the wrong length, an empty tuple, a `song_info` that is not text or a sequence)
    Error,
    /// the answer depends on a fact the caller has not supplied
    Need(&'static str),
}

impl Route {
    pub fn name(&self) -> String {
        match self {
            Route::ExternalKarafun => "external_karafun".into(),
            Route::RejectMissingFile => "reject_missing_file".into(),
            Route::ZipPrepare => "zip_prepare".into(),
            Route::CdgMp3Dual => "cdg_mp3_dual".into(),
            Route::Mp4 => "mp4".into(),
            Route::Mp3 => "mp3".into(),
            Route::Mp3gZip => "mp3g_zip".into(),
            Route::RejectUnsupportedTuple => "reject_unsupported_tuple".into(),
            Route::RejectInvalidZip => "reject_invalid_zip".into(),
            Route::RejectMissingCdgAudioPair => "reject_missing_cdg_audio_pair".into(),
            Route::RejectUnsupportedFormat => "reject_unsupported_format".into(),
            Route::Error => "error".into(),
            Route::Need(f) => format!("need:{f}"),
        }
    }
}

pub fn route(i: &Input) -> Route {
    if i.external {
        return Route::ExternalKarafun;
    }
    if i.has_primary {
        match i.primary_exists {
            Some(false) => return Route::RejectMissingFile,
            Some(true) => {}
            None => return Route::Need("primary_exists"),
        }
    }
    // an MP3+G ZIP that is not extracted yet is "preparation": the queue is restored and the start is retried later (the check uses `.lower()`)
    if let Info::Str(s) = &i.info {
        if s.to_lowercase().ends_with(".zip") {
            match i.zip_cached {
                Some(true) => {}
                Some(false) => return Route::ZipPrepare,
                None => return Route::Need("zip_cached"),
            }
        }
    }
    match &i.info {
        Info::Seq(items) => {
            let Some(first) = items.first() else { return Route::Error };
            if first.is_str() && first.text().ends_with(".cdg") {
                // `cdg_path, mp3_path = song_info` raises unless there are exactly two
                return if items.len() == 2 { Route::CdgMp3Dual } else { Route::Error };
            }
            if first.is_str() && first.text().ends_with(".mp4") {
                return Route::Mp4;
            }
            let shown = first.text();
            if shown.ends_with(".mp4") {
                Route::Mp4
            } else if shown.ends_with(".mp3") {
                Route::Mp3
            } else {
                Route::RejectUnsupportedTuple
            }
        }
        Info::Str(s) => {
            if s.ends_with(".mp4") {
                Route::Mp4
            } else if s.ends_with(".mp3") {
                Route::Mp3
            } else if s.ends_with(".zip") {
                match i.mp3g {
                    Some(true) => Route::Mp3gZip,
                    Some(false) => Route::RejectInvalidZip,
                    None => Route::Need("mp3g"),
                }
            } else if s.ends_with(".cdg") {
                match i.pair {
                    Some(true) => Route::CdgMp3Dual,
                    Some(false) => Route::RejectMissingCdgAudioPair,
                    None => Route::Need("pair"),
                }
            } else {
                Route::RejectUnsupportedFormat
            }
        }
        Info::Other(_) => Route::Error,
    }
}

/// What the Rust audio engine looks like to `_karaoke_rust_voice_for_song`: absent, a headless stand-in, or running.
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Engine {
    None,
    Headless,
    Running,
}

/// The facts `_karaoke_rust_voice_for_song` gathers before it decides who plays the sound of the song about to start.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct EngineInput {
    /// the setting `karaoke_audio_engine` is "rust" (any case, surrounding spaces ignored; an empty value means "rust")
    pub want_rust: bool,
    /// `plugin.supportsShadowAudio()` (False when the call raises)
    pub supports_shadow_audio: bool,
    pub engine: Engine,
    /// building the voice raised
    #[serde(default)]
    pub voice_error: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct EngineAnswer {
    /// the Rust voice plays the song's sound (mpv keeps the picture and its clock); false = mpv plays the sound
    pub voice: bool,
    /// why a wanted voice is not used: `bridge_unsupported`, `engine_not_running`, `error`; empty when it is used or was not wanted
    pub reason: &'static str,
    /// the value passed to `plugin.setShadowAudio`; None when the bridge does not support it (the call is not made)
    pub set_shadow_audio: Option<bool>,
}

pub fn engine_route(i: &EngineInput) -> EngineAnswer {
    let (voice, reason) = if !i.want_rust {
        (false, "")
    } else if !i.supports_shadow_audio {
        (false, "bridge_unsupported")
    } else if i.engine != Engine::Running {
        (false, "engine_not_running")
    } else if i.voice_error {
        (false, "error")
    } else {
        (true, "")
    };
    EngineAnswer { voice, reason, set_shadow_audio: if i.supports_shadow_audio { Some(voice) } else { None } }
}

/// JSON in, JSON out. `{"op":"engine", ...}` is an [`EngineInput`] and answers `{"voice":..,"reason":"..","set_shadow_audio":null|bool}`; anything else is an
/// [`Input`] (the start route) and answers `{"route":"<name>"}`.
pub fn start_json(request: &str) -> Result<String, String> {
    let v: serde_json::Value = serde_json::from_str(request).map_err(|e| e.to_string())?;
    if v.get("op").and_then(serde_json::Value::as_str) == Some("engine") {
        let input: EngineInput = serde_json::from_value(v).map_err(|e| e.to_string())?;
        let a = engine_route(&input);
        return Ok(serde_json::json!({"voice": a.voice, "reason": a.reason, "set_shadow_audio": a.set_shadow_audio}).to_string());
    }
    let input: Input = serde_json::from_value(v).map_err(|e| e.to_string())?;
    Ok(serde_json::json!({"route": route(&input).name()}).to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &str) -> Info {
        Info::Str(v.into())
    }
    fn input(info: Info) -> Input {
        Input { external: false, info, has_primary: true, primary_exists: Some(true), zip_cached: None, mp3g: None, pair: None }
    }
    fn seq(items: &[&str]) -> Info {
        Info::Seq(items.iter().map(|x| Item::S((*x).into())).collect())
    }

    #[test]
    fn external_karafun_wins_before_anything_is_looked_at() {
        let mut i = input(s("/lib/missing.mp4"));
        i.external = true;
        i.primary_exists = Some(false);
        assert_eq!(route(&i), Route::ExternalKarafun);
    }

    #[test]
    fn a_missing_primary_file_is_refused_but_only_when_there_is_a_primary_path() {
        let mut i = input(s("/lib/a.mp4"));
        i.primary_exists = Some(false);
        assert_eq!(route(&i), Route::RejectMissingFile);
        i.has_primary = false; // no primary path: the existence fact is not consulted at all
        assert_eq!(route(&i), Route::Mp4);
        let mut j = input(s("/lib/a.mp4"));
        j.primary_exists = None;
        assert_eq!(route(&j), Route::Need("primary_exists"));
    }

    #[test]
    fn plain_files_route_by_their_exact_lower_case_extension() {
        assert_eq!(route(&input(s("/lib/a.mp4"))), Route::Mp4);
        assert_eq!(route(&input(s("/lib/a.mp3"))), Route::Mp3);
        for odd in ["/lib/a.MP4", "/lib/a.Mp3", "/lib/a.avi", "/lib/a", "", "/lib/a.mp4 "] {
            assert_eq!(route(&input(s(odd))), Route::RejectUnsupportedFormat, "{odd:?}");
        }
    }

    #[test]
    fn a_zip_that_is_not_extracted_is_preparation_and_the_check_ignores_case_but_the_dispatch_does_not() {
        let mut z = input(s("/lib/a.zip"));
        z.zip_cached = Some(false);
        assert_eq!(route(&z), Route::ZipPrepare);
        z.zip_cached = Some(true);
        z.mp3g = Some(true);
        assert_eq!(route(&z), Route::Mp3gZip);
        z.mp3g = Some(false);
        assert_eq!(route(&z), Route::RejectInvalidZip);
        z.mp3g = None;
        assert_eq!(route(&z), Route::Need("mp3g"));
        let mut up = input(s("/lib/A.ZIP"));
        up.zip_cached = Some(false);
        assert_eq!(route(&up), Route::ZipPrepare, "the preparation check lower-cases");
        up.zip_cached = Some(true);
        assert_eq!(route(&up), Route::RejectUnsupportedFormat, "the dispatch is case-sensitive, so a cached A.ZIP is refused (a quirk kept on purpose)");
        let mut n = input(s("/lib/a.zip"));
        n.zip_cached = None;
        assert_eq!(route(&n), Route::Need("zip_cached"));
        let mut not_zip = input(s("/lib/a.mp4"));
        not_zip.zip_cached = Some(false);
        assert_eq!(route(&not_zip), Route::Mp4, "zip facts are only read for zip files");
    }

    #[test]
    fn a_lone_cdg_needs_its_mp3_next_to_it() {
        let mut c = input(s("/lib/a.cdg"));
        c.pair = Some(true);
        assert_eq!(route(&c), Route::CdgMp3Dual);
        c.pair = Some(false);
        assert_eq!(route(&c), Route::RejectMissingCdgAudioPair);
        c.pair = None;
        assert_eq!(route(&c), Route::Need("pair"));
    }

    #[test]
    fn tuples_and_lists_follow_the_older_entry_shapes() {
        assert_eq!(route(&input(seq(&["/l/a.cdg", "/l/a.mp3"]))), Route::CdgMp3Dual);
        assert_eq!(route(&input(seq(&["/l/a.cdg"]))), Route::Error, "unpacking one item raises");
        assert_eq!(route(&input(seq(&["/l/a.cdg", "/l/a.mp3", "x"]))), Route::Error, "unpacking three items raises");
        assert_eq!(route(&input(seq(&["/l/a.mp4", "/l/b.mp4"]))), Route::Mp4);
        assert_eq!(route(&input(seq(&["/l/a.mp4"]))), Route::Mp4);
        assert_eq!(route(&input(seq(&["/l/a.mp3", "x"]))), Route::Mp3);
        assert_eq!(route(&input(seq(&["/l/a.zip", "x"]))), Route::RejectUnsupportedTuple, "a zip inside a tuple is not played");
        assert_eq!(route(&input(seq(&["/l/a.CDG", "/l/a.mp3"]))), Route::RejectUnsupportedTuple, "case-sensitive");
        assert_eq!(route(&input(seq(&[]))), Route::Error, "an empty tuple raises");
        let other = |t: &str| Info::Seq(vec![Item::O(t.into()), Item::S("y".into())]);
        assert_eq!(route(&input(other("None"))), Route::RejectUnsupportedTuple);
        assert_eq!(route(&input(other("/odd/a.mp4"))), Route::Mp4, "a non-text first item is judged by its str()");
        assert_eq!(route(&input(other("/odd/a.cdg"))), Route::RejectUnsupportedTuple, "but never takes the cdg branch, which needs a real string");
    }

    #[test]
    fn something_that_is_neither_text_nor_a_sequence_raises() {
        assert_eq!(route(&input(Info::Other(true))), Route::Error);
    }

    #[test]
    fn the_json_glue_round_trips_and_rejects_nonsense() {
        let ok = start_json(r#"{"external":false,"info":{"str":"/l/a.mp4"},"has_primary":true,"primary_exists":true}"#).unwrap();
        assert_eq!(ok, r#"{"route":"mp4"}"#);
        let t = start_json(r#"{"info":{"seq":[{"s":"/l/a.cdg"},{"s":"/l/a.mp3"}]},"has_primary":false}"#).unwrap();
        assert_eq!(t, r#"{"route":"cdg_mp3_dual"}"#);
        let e = start_json(r#"{"info":{"other":true}}"#).unwrap();
        assert_eq!(e, r#"{"route":"error"}"#);
        assert!(start_json("{}").is_err());
        assert!(start_json("not json").is_err());
        assert!(start_json(r#"{"info":{"str":5}}"#).is_err());
    }

    fn eng(want: bool, supports: bool, engine: Engine, err: bool) -> EngineAnswer {
        engine_route(&EngineInput { want_rust: want, supports_shadow_audio: supports, engine, voice_error: err })
    }

    #[test]
    fn the_rust_voice_plays_only_when_wanted_supported_and_the_engine_is_running() {
        assert_eq!(eng(true, true, Engine::Running, false), EngineAnswer { voice: true, reason: "", set_shadow_audio: Some(true) });
        assert_eq!(eng(true, true, Engine::Running, true), EngineAnswer { voice: false, reason: "error", set_shadow_audio: Some(false) });
        assert_eq!(eng(true, true, Engine::Headless, false), EngineAnswer { voice: false, reason: "engine_not_running", set_shadow_audio: Some(false) });
        assert_eq!(eng(true, true, Engine::None, false), EngineAnswer { voice: false, reason: "engine_not_running", set_shadow_audio: Some(false) });
        assert_eq!(eng(true, false, Engine::Running, false), EngineAnswer { voice: false, reason: "bridge_unsupported", set_shadow_audio: None });
        assert_eq!(eng(false, true, Engine::Running, false), EngineAnswer { voice: false, reason: "", set_shadow_audio: Some(false) }, "not wanted: mpv plays, and the bridge is told so");
        assert_eq!(eng(false, false, Engine::None, false), EngineAnswer { voice: false, reason: "", set_shadow_audio: None });
    }

    #[test]
    fn the_engine_op_is_routed_by_the_json_glue() {
        let r = start_json(r#"{"op":"engine","want_rust":true,"supports_shadow_audio":true,"engine":"running"}"#).unwrap();
        assert_eq!(r, r#"{"reason":"","set_shadow_audio":true,"voice":true}"#);
        let r = start_json(r#"{"op":"engine","want_rust":true,"supports_shadow_audio":false,"engine":"none","voice_error":false}"#).unwrap();
        assert_eq!(r, r#"{"reason":"bridge_unsupported","set_shadow_audio":null,"voice":false}"#);
        assert!(start_json(r#"{"op":"engine","want_rust":true}"#).is_err());
        assert!(start_json(r#"{"op":"engine","want_rust":true,"supports_shadow_audio":true,"engine":"weird"}"#).is_err());
        // the default op is still the start route
        assert_eq!(start_json(r#"{"info":{"str":"/l/a.mp3"}}"#).unwrap(), r#"{"route":"mp3"}"#);
    }

    #[test]
    fn every_combination_of_inputs_answers_without_panicking() {
        let infos = [s("/l/a.mp4"), s("/l/a.mp3"), s("/l/a.zip"), s("/l/A.ZIP"), s("/l/a.cdg"), s("/l/a.txt"), s(""), seq(&["/l/a.cdg", "/l/a.mp3"]), seq(&[]), Info::Other(false)];
        let tri = [None, Some(true), Some(false)];
        let mut n = 0;
        for info in &infos {
            for ext in [false, true] {
                for hp in [false, true] {
                    for pe in tri {
                        for zc in tri {
                            for mp in tri {
                                for pr in tri {
                                    let i = Input { external: ext, info: info.clone(), has_primary: hp, primary_exists: pe, zip_cached: zc, mp3g: mp, pair: pr };
                                    let _ = route(&i).name();
                                    n += 1;
                                }
                            }
                        }
                    }
                }
            }
        }
        assert_eq!(n, 10 * 2 * 2 * 81);
    }
}
