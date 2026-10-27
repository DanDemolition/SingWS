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

/// One singer as the pick loop of `play_next_file` sees it: the singer's own skipped flag and the kind of each queued song, one character each:
/// `d` a dict entry that is not skipped, `s` a dict entry that is skipped, `t` an older tuple/list entry (never skipped); plus, for a rotation, the marker and the
/// lock fields (`rotation_lock_new`, and `rotation_lock_order` as the whole number the app reads it as).
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct PickSinger {
    #[serde(default)]
    pub skipped: bool,
    pub songs: String,
    #[serde(default)]
    pub marker: bool,
    #[serde(default)]
    pub lock_new: bool,
    #[serde(default)]
    pub lock_order: i64,
}

impl PickSinger {
    fn active(&self) -> bool {
        !self.skipped && self.songs.chars().any(|c| c != 's')
    }
    fn first_playable(&self) -> Option<usize> {
        self.songs.chars().position(|c| c != 's')
    }
}

/// What the pick loop of `play_next_file` ends with.
#[derive(Debug, Clone, PartialEq)]
pub struct Pick {
    /// the singer the app goes on with (index into the queue as it was before the loop), `None` when nobody is left
    pub singer: Option<usize>,
    /// that singer's first song that is not skipped; `None` inside a picked singer means there is none (the app then starts nothing)
    pub song: Option<usize>,
    /// the queue after the loop, as indices into the old one (the picked singer is no longer in it, except in the all-skipped case)
    pub queue: Vec<usize>,
    /// the loop cleared the rotation lock (a locked newcomer reached the top)
    pub lock_cleared: bool,
}

fn marker_index(q: &[usize], s: &[PickSinger]) -> Option<usize> {
    q.iter().position(|&i| s[i].marker)
}

/// `_is_rotation_locked()`: rotation mode, the saved lock, a non-empty queue and the marker not on the top singer (no marker counts as "not on the top").
fn is_locked(q: &[usize], s: &[PickSinger], mode: bool, setting: bool) -> bool {
    mode && setting && !q.is_empty() && marker_index(q, s) != Some(0)
}

fn reweave_indices(q: &[usize], s: &[PickSinger], lock_new: &[bool]) -> Vec<usize> {
    let facts: Vec<LockSinger> = q.iter().map(|&i| LockSinger { active: s[i].active(), marker: s[i].marker, lock_new: lock_new[i], lock_order: s[i].lock_order }).collect();
    reweave(&facts).into_iter().map(|k| q[k]).collect()
}

/// The pick loop of `play_next_file`, step by step as the app runs it:
/// * in rotation mode, a locked rotation whose top singer is a locked newcomer is unlocked first (the lock and every singer's lock fields are cleared);
/// * then up to `len` times: the front singer is taken; a skipped singer goes to the bottom and is forgotten; a singer who is not
///   skipped and has a song that is not skipped is picked; a singer who is not skipped but has nothing playable goes to the bottom and is forgotten; after each move to the
///   bottom a locked rotation is reweaved (which changes who is in front next);
/// * a singer who was skipped or had nothing playable is forgotten, so when nobody qualifies nothing starts and the queue is back in its old order (until 1.0.2.1 the
///   app kept a skipped singer in that spot, started their song and queued them twice).
pub fn pick_loop(s: &[PickSinger], mode: bool, setting_locked: bool) -> Pick {
    let n = s.len();
    let mut q: Vec<usize> = (0..n).collect();
    let mut setting = setting_locked;
    let mut lock_new: Vec<bool> = s.iter().map(|x| x.lock_new).collect();
    let mut cleared = false;
    if mode {
        let marker_was_top = marker_index(&q, s) == Some(0);
        if is_locked(&q, s, mode, setting) && (lock_new[q[0]] || marker_was_top) {
            setting = false;
            cleared = true;
            lock_new.iter_mut().for_each(|x| *x = false);
        }
    }
    let mut held: Option<usize> = None;
    for _ in 0..n {
        let me = q.remove(0);
        held = Some(me);
        if s[me].skipped {
            q.push(me);
            if is_locked(&q, s, mode, setting) {
                q = reweave_indices(&q, s, &lock_new);
            }
            held = None; // forgotten, like the singer who has nothing playable (the app used to keep a skipped singer here; fixed in 1.0.2.1)
            continue;
        }
        if s[me].active() {
            break;
        }
        q.push(me);
        if is_locked(&q, s, mode, setting) {
            q = reweave_indices(&q, s, &lock_new);
        }
        held = None;
    }
    Pick { singer: held, song: held.and_then(|i| s[i].first_playable()), queue: q, lock_cleared: cleared }
}

/// The unlocked, classic-mode answer: the singer and the song (kept for the simple callers and tests).
pub fn pick(singers: &[PickSinger]) -> Option<(usize, Option<usize>)> {
    let p = pick_loop(singers, false, false);
    p.singer.map(|i| (i, p.song))
}

/// One singer's rotation state as `_commit_pending_start` sees it: the same `skipped` / `songs` facts as [`PickSinger`] (a singer is ACTIVE when not skipped and holding a
/// song that is not skipped), plus the flags the commit changes. `round_sung` may be absent (the key is missing until the app first normalizes the queue).
#[derive(Debug, Clone, PartialEq, Deserialize, serde::Serialize)]
pub struct RotSinger {
    #[serde(default)]
    pub skipped: bool,
    pub songs: String,
    #[serde(default)]
    pub round_sung: Option<bool>,
    #[serde(default)]
    pub marker: bool,
    #[serde(default)]
    pub has_sung: bool,
    #[serde(default)]
    pub lock_new: bool,
    #[serde(default)]
    pub lock_order: i64,
}

impl RotSinger {
    fn active(&self) -> bool {
        !self.skipped && self.songs.chars().any(|c| c != 's')
    }
}

/// The queue after a song has started and the app commits it: the singer is marked as having sung (and, in rotation mode, as having sung this round), goes to the
/// bottom; in rotation mode the marker follows when it was on the top singer, a missing or doubled marker is repaired (the first one wins; none: the first singer), the
/// missing `round_sung` keys are filled with false, and a round in which every active singer has sung starts over. `queue` is the queue WITHOUT the singer.
/// A locked rotation (`setting_locked` and the marker not on the top) reweaves its tail right after the singer is added, before the marker handling. Each returned row is
/// the singer plus the index it had in `queue` (the started singer has index `queue.len()`).
pub fn after_start(queue: &[RotSinger], singer: &RotSinger, rotation_mode: bool, marker_was_top: bool, setting_locked: bool) -> Vec<(usize, RotSinger)> {
    let mut me = singer.clone();
    me.has_sung = true;
    if rotation_mode {
        me.round_sung = Some(true);
    }
    // each row keeps the index it had in `queue` followed by the singer (index queue.len())
    let mut q: Vec<(usize, RotSinger)> = queue.iter().cloned().enumerate().collect();
    q.push((queue.len(), me));
    if !rotation_mode {
        return q;
    }
    let marker_at = |q: &[(usize, RotSinger)]| q.iter().position(|(_, s)| s.marker);
    if setting_locked && marker_at(&q) != Some(0) {
        let facts: Vec<LockSinger> = q.iter().map(|(_, s)| LockSinger { active: s.active(), marker: s.marker, lock_new: s.lock_new, lock_order: s.lock_order }).collect();
        let order = reweave(&facts);
        q = order.into_iter().map(|k| q[k].clone()).collect();
    }
    if marker_was_top {
        for (_, s) in q.iter_mut() {
            s.marker = false;
        }
        // the marker goes to the started singer wherever the reweave left them (not to whoever is last)
        let started = queue.len();
        if let Some((_, me)) = q.iter_mut().find(|(i, _)| *i == started) {
            me.marker = true;
        }
    }
    // repair: exactly one marker; several: keep the first; none: the first singer
    let marked: Vec<usize> = q.iter().enumerate().filter(|(_, (_, s))| s.marker).map(|(i, _)| i).collect();
    if marked.len() != 1 {
        let keep = marked.first().copied().unwrap_or(0);
        for (i, (_, s)) in q.iter_mut().enumerate() {
            s.marker = i == keep;
        }
    }
    // round state
    if q.iter().any(|(_, s)| s.active()) {
        for (_, s) in q.iter_mut() {
            if s.round_sung.is_none() {
                s.round_sung = Some(false);
            }
        }
        if q.iter().filter(|(_, s)| s.active()).all(|(_, s)| s.round_sung == Some(true)) {
            for (_, s) in q.iter_mut().filter(|(_, s)| s.active()) {
                s.round_sung = Some(false);
            }
        }
    }
    q
}

/// One singer as the rotation lock sees it: active (not skipped and holding a song that is not skipped), the rotation marker, and the lock's own fields.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct LockSinger {
    #[serde(default)]
    pub active: bool,
    #[serde(default)]
    pub marker: bool,
    /// `rotation_lock_new`: a singer who signed up while the rotation was locked
    #[serde(default)]
    pub lock_new: bool,
    /// `int(rotation_lock_order or 0)`
    #[serde(default)]
    pub lock_order: i64,
}

/// `_rotation_reweave_locked_tail` for a locked, non-empty rotation: the new order of the queue as indices into the old one. Everything before the first of (the marker, the
/// first locked newcomer) stays; from there the existing singers keep their order and the locked newcomers (sorted by their signup order, ties keep queue order) are woven in
/// after each of them, one for one. A marked singer with nothing to sing is pinned to the front of the tail.
pub fn reweave(q: &[LockSinger]) -> Vec<usize> {
    let n = q.len();
    let marker_idx = q.iter().position(|s| s.marker);
    let pinned = marker_idx.filter(|&i| !q[i].active);
    let first_new = q.iter().position(|s| s.lock_new);
    let Some(tail_start) = [marker_idx, first_new].into_iter().flatten().min() else {
        return (0..n).collect();
    };
    let mut order: Vec<usize> = (0..tail_start).collect();
    let mut new_tail: Vec<usize> = Vec::new();
    let mut old_tail: Vec<usize> = Vec::new();
    for i in tail_start..n {
        if Some(i) == pinned {
            continue;
        }
        if q[i].lock_new {
            new_tail.push(i);
        } else {
            old_tail.push(i);
        }
    }
    new_tail.sort_by_key(|&i| q[i].lock_order); // stable, like Python's sort
    if let Some(p) = pinned {
        order.push(p);
    }
    for k in 0..old_tail.len().max(new_tail.len()) {
        if let Some(&i) = old_tail.get(k) {
            order.push(i);
        }
        if let Some(&i) = new_tail.get(k) {
            order.push(i);
        }
    }
    order
}

/// Python's `str.strip()` whitespace: Unicode White_Space plus U+001C..U+001F (which Python also counts as whitespace and Rust does not).
fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// The artist and title published when a song starts (`play_next_file`, "immutable display payload"): the entry's own artist and title when BOTH are non-empty after
/// stripping, otherwise the library's display name (`fallback`, which the app looks up); then the text is split on the FIRST " • " into artist and title, both stripped; a
/// text without that separator is all artist and the title stays empty.
pub fn display_payload(entry_artist: &str, entry_title: &str, fallback: Option<&str>) -> Option<(String, String)> {
    let (a, t) = (py_strip(entry_artist), py_strip(entry_title));
    let shown: String = if !a.is_empty() && !t.is_empty() { format!("{a} • {t}") } else { fallback?.to_string() };
    Some(match shown.split_once(" • ") {
        Some((x, y)) => (py_strip(x).to_string(), py_strip(y).to_string()),
        None => (py_strip(&shown).to_string(), String::new()),
    })
}


/// What reaches `_start_karaoke_transport` for each wrapper (`_play_mp4`, `_play_cdg`, `_play_mp3`): which path is the audio and which the picture, the mode name, the key as a
/// whole number (`int(semitones or 0)`), and which of start / loop / duration the wrapper forwards. `_play_mp4` forwards no duration, `_play_mp3` forwards neither loop nor
/// duration (the transport's defaults, `None`, apply), `_play_cdg` forwards all three.
#[derive(Debug, Clone, PartialEq)]
pub struct TransportArgs {
    pub audio_path: String,
    pub video_path: Option<String>,
    pub mode: &'static str,
    pub semitones: i64,
    pub start_seconds: Option<f64>,
    pub loop_seconds: Option<f64>,
    pub duration_seconds: Option<f64>,
}

pub fn transport_args(kind: &str, song: &str, cdg: &str, mp3: &str, semitones: f64, start: Option<f64>, loop_s: Option<f64>, duration: Option<f64>) -> Option<TransportArgs> {
    let key = semitones.trunc() as i64;
    Some(match kind {
        "mp4" => TransportArgs { audio_path: song.into(), video_path: Some(song.into()), mode: "mp4", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: None },
        "cdg" => TransportArgs { audio_path: mp3.into(), video_path: Some(cdg.into()), mode: "cdg", semitones: key, start_seconds: start, loop_seconds: loop_s, duration_seconds: duration },
        "mp3" => TransportArgs { audio_path: song.into(), video_path: None, mode: "audio", semitones: key, start_seconds: start, loop_seconds: None, duration_seconds: None },
        _ => return None,
    })
}

/// Python truthiness of an optional number: `None` and zero are falsy.
fn truthy(x: Option<f64>) -> bool {
    matches!(x, Some(v) if v != 0.0)
}

/// `play_mp3g_zip` merges what the caller passed with what an earlier, not yet extracted call stashed for the same ZIP: the start offset when the caller's is not positive, the loop
/// and the duration when the caller's is falsy. The duration always becomes a float (0.0 when nothing is known).
pub fn zip_args(start: Option<f64>, loop_s: Option<f64>, duration: Option<f64>, pend_start: Option<f64>, pend_loop: Option<f64>, pend_duration: Option<f64>) -> (f64, Option<f64>, f64) {
    let mut eff_start = if truthy(start) { start.unwrap_or(0.0) } else { 0.0 };
    if eff_start <= 0.0 {
        eff_start = pend_start.unwrap_or(0.0);
    }
    let eff_loop = if truthy(loop_s) { loop_s } else { pend_loop };
    let eff_duration = if truthy(duration) {
        duration.unwrap_or(0.0)
    } else if truthy(pend_duration) {
        pend_duration.unwrap_or(0.0)
    } else {
        0.0
    };
    (eff_start, eff_loop, eff_duration)
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
    if v.get("op").and_then(serde_json::Value::as_str) == Some("display") {
        let text = |k: &str| v.get(k).and_then(serde_json::Value::as_str).map(str::to_string);
        let (a, t) = (text("artist").ok_or("artist is required")?, text("title").ok_or("title is required")?);
        let fallback = text("fallback");
        let (artist, title) = display_payload(&a, &t, fallback.as_deref()).ok_or("the display name is needed but was not given")?;
        return Ok(serde_json::json!({"artist": artist, "title": title}).to_string());
    }
    if v.get("op").and_then(serde_json::Value::as_str) == Some("transport") {
        let text = |k: &str| v.get(k).and_then(serde_json::Value::as_str).unwrap_or("").to_string();
        let num = |k: &str| v.get(k).and_then(serde_json::Value::as_f64);
        let kind = text("kind");
        let key = num("semitones").ok_or("semitones is required")?;
        let t = transport_args(&kind, &text("song"), &text("cdg"), &text("mp3"), key, num("start"), num("loop"), num("duration")).ok_or("unknown kind")?;
        return Ok(serde_json::json!({"audio_path": t.audio_path, "video_path": t.video_path, "mode": t.mode, "semitones": t.semitones,
            "start_seconds": t.start_seconds, "loop_seconds": t.loop_seconds, "duration_seconds": t.duration_seconds}).to_string());
    }
    if v.get("op").and_then(serde_json::Value::as_str) == Some("zip_args") {
        let num = |k: &str| v.get(k).and_then(serde_json::Value::as_f64);
        let (s, l, d) = zip_args(num("start"), num("loop"), num("duration"), num("pend_start"), num("pend_loop"), num("pend_duration"));
        return Ok(serde_json::json!({"start_seconds": s, "loop_seconds": l, "duration_seconds": d}).to_string());
    }
    if v.get("op").and_then(serde_json::Value::as_str) == Some("after_start") {
        let queue: Vec<RotSinger> = serde_json::from_value(v.get("queue").cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| e.to_string())?;
        let singer: RotSinger = serde_json::from_value(v.get("singer").cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| e.to_string())?;
        let bad = |s: &RotSinger| s.songs.chars().any(|c| !matches!(c, 'd' | 's' | 't'));
        if queue.iter().any(bad) || bad(&singer) {
            return Err("songs may only contain d, s and t".into());
        }
        let flag = |k: &str| v.get(k).and_then(serde_json::Value::as_bool).unwrap_or(false);
        let after = after_start(&queue, &singer, flag("rotation_mode"), flag("marker_was_top"), flag("setting_locked"));
        return Ok(serde_json::json!({
            "queue": after.iter().map(|(_, s)| serde_json::json!({"marker": s.marker, "round_sung": s.round_sung, "has_sung": s.has_sung})).collect::<Vec<_>>(),
            "order": after.iter().map(|(i, _)| *i).collect::<Vec<_>>(),
        }).to_string());
    }
    if v.get("op").and_then(serde_json::Value::as_str) == Some("reweave") {
        let q: Vec<LockSinger> = serde_json::from_value(v.get("queue").cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| e.to_string())?;
        return Ok(serde_json::json!({"order": reweave(&q)}).to_string());
    }
    if v.get("op").and_then(serde_json::Value::as_str) == Some("pick") {
        let singers: Vec<PickSinger> = serde_json::from_value(v.get("singers").cloned().unwrap_or(serde_json::Value::Null)).map_err(|e| e.to_string())?;
        if singers.iter().any(|s| s.songs.chars().any(|c| !matches!(c, 'd' | 's' | 't'))) {
            return Err("songs may only contain d, s and t".into());
        }
        let flag = |k: &str| v.get(k).and_then(serde_json::Value::as_bool).unwrap_or(false);
        let p = pick_loop(&singers, flag("rotation_mode"), flag("setting_locked"));
        return Ok(serde_json::json!({"singer": p.singer, "song": p.song, "queue": p.queue, "lock_cleared": p.lock_cleared}).to_string());
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

    fn q(spec: &[(&str, bool)]) -> Vec<PickSinger> {
        spec.iter().map(|(songs, skipped)| PickSinger { skipped: *skipped, songs: (*songs).into(), marker: false, lock_new: false, lock_order: 0 }).collect()
    }

    #[test]
    fn the_first_unskipped_singer_with_a_playable_song_goes_next_and_skipped_songs_are_passed_over() {
        assert_eq!(pick(&q(&[("d", false), ("d", false)])), Some((0, Some(0))));
        assert_eq!(pick(&q(&[("d", true), ("d", false)])), Some((1, Some(0))), "a skipped singer is passed over");
        assert_eq!(pick(&q(&[("", false), ("ss", false), ("sd", false)])), Some((2, Some(1))), "no songs, or only skipped songs, is passed over; the first unskipped song is taken");
        assert_eq!(pick(&q(&[("s", false), ("t", false)])), Some((1, Some(0))), "an older tuple entry is never skipped");
        assert_eq!(pick(&q(&[("sst", false)])), Some((0, Some(2))));
        assert_eq!(pick(&q(&[])), None);
    }

    #[test]
    fn when_nobody_qualifies_nothing_starts_and_nobody_is_kept() {
        assert_eq!(pick(&q(&[("dd", true), ("", false), ("ss", false)])), None);
        assert_eq!(pick(&q(&[("d", false), ("", false)])), Some((0, Some(0))), "a qualifying singer wins");
        assert_eq!(pick(&q(&[("dd", true), ("", false), ("ss", true)])), None, "skipped or nothing playable: nobody");
        assert_eq!(pick(&q(&[("ss", false), ("sd", true)])), None, "a skipped singer never sings, even as the last one looked at (fixed in 1.0.2.1)");
        assert_eq!(pick(&q(&[("d", true)])), None, "a single skipped singer does not sing");
        assert_eq!(pick(&q(&[("d", true), ("d", true)])), None, "all skipped: nothing starts");
        let all = pick_loop(&q(&[("d", true), ("d", true), ("d", true)]), false, false);
        assert_eq!(all.queue, vec![0, 1, 2], "every singer went to the bottom once, so the order is as it was");
    }

    #[test]
    fn the_pick_op_is_routed_by_the_json_glue_and_rejects_bad_shapes() {
        let r = start_json(r#"{"op":"pick","singers":[{"skipped":true,"songs":"d"},{"songs":"sd"}]}"#).unwrap();
        assert_eq!(r, r#"{"lock_cleared":false,"queue":[0],"singer":1,"song":1}"#, "the skipped singer went to the bottom; the picked one left the queue");
        assert_eq!(start_json(r#"{"op":"pick","singers":[{"skipped":true,"songs":"ss"}]}"#).unwrap(), r#"{"lock_cleared":false,"queue":[0],"singer":null,"song":null}"#, "a skipped singer is not picked");
        assert_eq!(start_json(r#"{"op":"pick","singers":[]}"#).unwrap(), r#"{"lock_cleared":false,"queue":[],"singer":null,"song":null}"#);
        assert!(start_json(r#"{"op":"pick","singers":[{"songs":"dx"}]}"#).is_err());
        assert!(start_json(r#"{"op":"pick"}"#).is_err());
        assert!(start_json(r#"{"op":"pick","singers":[{"skipped":true}]}"#).is_err());
    }

    #[test]
    fn the_published_artist_and_title_follow_the_entry_then_the_library_name() {
        let d = |a: &str, t: &str, f: Option<&str>| display_payload(a, t, f);
        assert_eq!(d("Abba", "Waterloo", Some("ignored")), Some(("Abba".into(), "Waterloo".into())));
        assert_eq!(d("  Abba ", "\tWaterloo\n", None), Some(("Abba".into(), "Waterloo".into())), "stripped, and the fallback is not needed");
        assert_eq!(d("", "Waterloo", Some("Queen • Bohemian")), Some(("Queen".into(), "Bohemian".into())), "one part empty: the library name");
        assert_eq!(d("  ", "  ", Some("Solo Artist")), Some(("Solo Artist".into(), "".into())), "no separator: all artist");
        assert_eq!(d("", "", Some("A • B • C")), Some(("A".into(), "B • C".into())), "only the first separator splits");
        assert_eq!(d("", "", Some("  A  •  B  ")), Some(("A".into(), "B".into())));
        assert_eq!(d("", "", Some("A•B")), Some(("A•B".into(), "".into())), "the separator needs its spaces");
        assert_eq!(d("", "", Some(" • ")), Some(("".into(), "".into())));
        assert_eq!(d("Abba • Live", "Waterloo", None), Some(("Abba".into(), "Live • Waterloo".into())), "a separator inside the artist moves the split");
        assert_eq!(d("\u{1f}Abba\u{1c}", "T", None), Some(("Abba".into(), "T".into())), "Python strips U+001C..U+001F; Rust's trim would not");
        assert_eq!(d("", "", None), None, "the fallback is needed");
    }

    #[test]
    fn the_display_op_is_routed_by_the_json_glue() {
        assert_eq!(start_json(r#"{"op":"display","artist":"A","title":"B"}"#).unwrap(), r#"{"artist":"A","title":"B"}"#);
        assert_eq!(start_json(r#"{"op":"display","artist":"","title":"B","fallback":"X • Y"}"#).unwrap(), r#"{"artist":"X","title":"Y"}"#);
        assert!(start_json(r#"{"op":"display","artist":"","title":""}"#).is_err());
        assert!(start_json(r#"{"op":"display","title":"B"}"#).is_err());
    }

    fn rs(songs: &str, skipped: bool, round: Option<bool>, marker: bool, has_sung: bool) -> RotSinger {
        RotSinger { skipped, songs: songs.into(), round_sung: round, marker, has_sung, lock_new: false, lock_order: 0 }
    }
    fn flags(q: &[(usize, RotSinger)]) -> Vec<(bool, Option<bool>, bool)> {
        q.iter().map(|(_, s)| (s.marker, s.round_sung, s.has_sung)).collect()
    }

    #[test]
    fn a_started_singer_goes_to_the_bottom_marked_as_sung() {
        let q = [rs("d", false, Some(false), true, false), rs("d", false, Some(false), false, false)];
        let me = rs("d", false, Some(false), false, false);
        let out = after_start(&q, &me, false, false, false);
        assert_eq!(flags(&out), vec![(true, Some(false), false), (false, Some(false), false), (false, Some(false), true)], "classic mode: only has_sung and the place change");
        let out = after_start(&q, &me, true, false, false);
        assert_eq!(flags(&out), vec![(true, Some(false), false), (false, Some(false), false), (false, Some(true), true)], "rotation mode: sung this round");
    }

    #[test]
    fn the_marker_follows_the_singer_when_it_was_on_the_top_and_is_repaired_otherwise() {
        let q = [rs("d", false, Some(false), false, false), rs("d", false, Some(false), false, false)];
        let me = rs("d", false, None, true, false);
        assert_eq!(flags(&after_start(&q, &me, true, true, false)).iter().map(|f| f.0).collect::<Vec<_>>(), vec![false, false, true], "moved to the singer");
        let two = [rs("d", false, Some(false), true, false), rs("d", false, Some(false), true, false)];
        assert_eq!(flags(&after_start(&two, &me, true, false, false)).iter().map(|f| f.0).collect::<Vec<_>>(), vec![true, false, false], "the first of several wins");
        let none = [rs("d", false, Some(false), false, false)];
        let plain = rs("d", false, None, false, false);
        assert_eq!(flags(&after_start(&none, &plain, true, false, false)).iter().map(|f| f.0).collect::<Vec<_>>(), vec![true, false], "no marker: the first singer");
        let one = [rs("d", false, Some(false), true, false), rs("d", false, Some(false), false, false)];
        assert_eq!(flags(&after_start(&one, &plain, true, false, false)).iter().map(|f| f.0).collect::<Vec<_>>(), vec![true, false, false], "one marker stays where it is");
        assert_eq!(flags(&after_start(&[], &plain, true, false, false)).iter().map(|f| f.0).collect::<Vec<_>>(), vec![true], "alone: marked");
    }

    #[test]
    fn in_a_locked_rotation_the_marker_follows_the_started_singer_even_when_the_reweave_moves_them() {
        // marker on the top singer is impossible while locked, so use marker_was_top = true with a marker elsewhere: the locked reweave puts the newcomer after the started singer
        let mut newcomer = rs("d", false, Some(false), false, false);
        newcomer.lock_new = true;
        newcomer.lock_order = 1;
        let q = [rs("d", false, Some(false), true, false), newcomer];
        let me = rs("d", false, None, false, false);
        let out = after_start(&q, &me, true, true, true);
        let started_row = out.iter().position(|(i, _)| *i == 2).unwrap();
        assert!(out[started_row].1.marker, "the started singer carries the marker");
        assert_eq!(out.iter().filter(|(_, s)| s.marker).count(), 1);
    }

    #[test]
    fn a_finished_round_starts_over_and_missing_flags_are_filled() {
        let q = [rs("d", false, Some(true), true, true), rs("s", false, Some(true), false, true), rs("d", true, Some(true), false, true)];
        let me = rs("d", false, None, false, false);
        let out = after_start(&q, &me, true, false, false);
        // active singers: 0 and the new one (1 has only skipped songs, 2 is skipped): everyone active has sung, so the round restarts for them
        assert_eq!(out.iter().map(|(_, s)| s.round_sung).collect::<Vec<_>>(), vec![Some(false), Some(true), Some(true), Some(false)], "inactive singers keep their flag");
        let open = [rs("d", false, Some(false), true, false)];
        let out = after_start(&open, &me, true, false, false);
        assert_eq!(out.iter().map(|(_, s)| s.round_sung).collect::<Vec<_>>(), vec![Some(false), Some(true)], "someone has not sung: no reset");
        let missing = [rs("d", false, None, true, false), rs("s", false, None, false, false)];
        let out = after_start(&missing, &rs("s", false, None, false, false), true, false, false);
        assert_eq!(out.iter().map(|(_, s)| s.round_sung).collect::<Vec<_>>(), vec![Some(false), Some(false), Some(true)], "nobody active holds a song? the first singer is active, so keys are filled");
        let nobody = [rs("s", false, None, true, false)];
        let out = after_start(&nobody, &rs("", false, None, false, false), true, false, false);
        assert_eq!(out.iter().map(|(_, s)| s.round_sung).collect::<Vec<_>>(), vec![None, Some(true)], "no active singer: nothing is normalized (the started singer keeps what it was given)");
    }

    #[test]
    fn the_after_start_op_is_routed_by_the_json_glue() {
        let r = start_json(r#"{"op":"after_start","rotation_mode":true,"marker_was_top":true,"queue":[{"songs":"d","round_sung":false,"marker":false}],"singer":{"songs":"d","marker":true}}"#).unwrap();
        assert_eq!(r, r#"{"order":[0,1],"queue":[{"has_sung":false,"marker":false,"round_sung":false},{"has_sung":true,"marker":true,"round_sung":true}]}"#);
        assert!(start_json(r#"{"op":"after_start","queue":[{"songs":"x"}],"singer":{"songs":"d"}}"#).is_err());
        assert!(start_json(r#"{"op":"after_start","queue":[]}"#).is_err());
    }

    fn ls(active: bool, marker: bool, lock_new: bool, order: i64) -> LockSinger {
        LockSinger { active, marker, lock_new, lock_order: order }
    }

    #[test]
    fn locked_newcomers_are_woven_in_after_the_existing_singers() {
        // head: 0 stays; the marker is singer 1; existing 1,2 and newcomers 3 (order 2), 4 (order 1)
        let q = [ls(true, false, false, 0), ls(true, true, false, 0), ls(true, false, false, 0), ls(true, false, true, 2), ls(true, false, true, 1)];
        assert_eq!(reweave(&q), vec![0, 1, 4, 2, 3], "old singers first, then newcomers by signup order, interleaved one for one");
        let q = [ls(true, true, false, 0), ls(true, false, false, 0), ls(true, false, true, 5)];
        assert_eq!(reweave(&q), vec![0, 2, 1], "the marker first, then the newcomer woven in right after it, then the other old singer");
    }

    #[test]
    fn a_marked_singer_with_nothing_to_sing_is_pinned_to_the_front_of_the_tail() {
        let q = [ls(true, false, false, 0), ls(false, true, false, 0), ls(true, false, false, 0), ls(true, false, true, 1)];
        assert_eq!(reweave(&q), vec![0, 1, 2, 3]);
        let q = [ls(true, false, true, 1), ls(false, true, false, 0), ls(true, false, false, 0)];
        assert_eq!(reweave(&q), vec![1, 2, 0], "the newcomer is the first of the tail, so the pinned marker goes first and the newcomer after the old singer");
    }

    #[test]
    fn with_no_marker_and_no_newcomer_nothing_moves_and_ties_keep_queue_order() {
        assert_eq!(reweave(&[ls(true, false, false, 0), ls(true, false, false, 0)]), vec![0, 1]);
        assert_eq!(reweave(&[]), Vec::<usize>::new());
        let q = [ls(true, false, true, 1), ls(true, false, true, 1), ls(true, false, true, 0)];
        assert_eq!(reweave(&q), vec![2, 0, 1], "ties keep queue order");
    }

    #[test]
    fn the_reweave_op_is_routed_by_the_json_glue() {
        let r = start_json(r#"{"op":"reweave","queue":[{"active":true},{"active":true,"marker":true},{"active":true,"lock_new":true,"lock_order":3}]}"#).unwrap();
        assert_eq!(r, r#"{"order":[0,1,2]}"#);
        assert!(start_json(r#"{"op":"reweave"}"#).is_err());
    }

    #[test]
    fn each_wrapper_forwards_its_own_arguments_to_the_transport() {
        let t = transport_args("mp4", "/l/a.mp4", "", "", 2.0, Some(1.5), Some(4.0), Some(99.0)).unwrap();
        assert_eq!((t.audio_path.as_str(), t.video_path.as_deref(), t.mode, t.semitones), ("/l/a.mp4", Some("/l/a.mp4"), "mp4", 2));
        assert_eq!((t.start_seconds, t.loop_seconds, t.duration_seconds), (Some(1.5), Some(4.0), None), "mp4 forwards no duration");
        let t = transport_args("cdg", "", "/l/a.cdg", "/l/a.mp3", -3.0, Some(0.0), None, Some(200.0)).unwrap();
        assert_eq!((t.audio_path.as_str(), t.video_path.as_deref(), t.mode, t.semitones), ("/l/a.mp3", Some("/l/a.cdg"), "cdg", -3));
        assert_eq!((t.start_seconds, t.loop_seconds, t.duration_seconds), (Some(0.0), None, Some(200.0)), "the picture is the cdg, the sound the mp3, duration is forwarded");
        let t = transport_args("mp3", "/l/a.mp3", "", "", 0.0, Some(7.0), Some(4.0), Some(99.0)).unwrap();
        assert_eq!((t.video_path.as_deref(), t.mode), (None, "audio"));
        assert_eq!((t.start_seconds, t.loop_seconds, t.duration_seconds), (Some(7.0), None, None), "mp3 drops loop and duration");
        assert!(transport_args("zip", "", "", "", 0.0, None, None, None).is_none());
    }

    #[test]
    fn the_key_is_truncated_toward_zero_like_pythons_int() {
        for (k, want) in [(2.9, 2), (-2.9, -2), (0.0, 0), (-0.5, 0), (12.0, 12)] {
            assert_eq!(transport_args("mp3", "a", "", "", k, None, None, None).unwrap().semitones, want, "{k}");
        }
    }

    #[test]
    fn the_zip_remembers_what_an_earlier_call_stashed_for_it() {
        assert_eq!(zip_args(Some(3.0), Some(2.0), Some(180.0), Some(9.0), Some(8.0), Some(7.0)), (3.0, Some(2.0), 180.0), "the caller's values win when set");
        assert_eq!(zip_args(Some(0.0), None, None, Some(9.0), Some(8.0), Some(7.0)), (9.0, Some(8.0), 7.0), "zero / none fall back to the stash");
        assert_eq!(zip_args(Some(-1.0), Some(0.0), Some(0.0), None, None, None), (0.0, None, 0.0), "a negative start is not kept; nothing stashed means zero / none / 0.0");
        assert_eq!(zip_args(None, Some(0.0), None, Some(0.0), Some(0.0), None), (0.0, Some(0.0), 0.0), "the stash is returned as is, even a falsy loop");
    }

    #[test]
    fn the_transport_ops_are_routed_by_the_json_glue() {
        let r = start_json(r#"{"op":"transport","kind":"cdg","cdg":"/a.cdg","mp3":"/a.mp3","semitones":2,"start":1.5,"duration":200}"#).unwrap();
        let v: serde_json::Value = serde_json::from_str(&r).unwrap();
        assert_eq!((v["mode"].as_str(), v["audio_path"].as_str(), v["video_path"].as_str(), v["semitones"].as_i64()), (Some("cdg"), Some("/a.mp3"), Some("/a.cdg"), Some(2)));
        assert!(v["loop_seconds"].is_null());
        assert!(start_json(r#"{"op":"transport","kind":"zip","semitones":0}"#).is_err());
        assert!(start_json(r#"{"op":"transport","kind":"mp3"}"#).is_err());
        let r = start_json(r#"{"op":"zip_args","start":0,"pend_start":4.5,"duration":null,"pend_duration":120}"#).unwrap();
        assert_eq!(r, r#"{"duration_seconds":120.0,"loop_seconds":null,"start_seconds":4.5}"#);
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
