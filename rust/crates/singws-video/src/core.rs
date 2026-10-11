//! The portable half of the bridge: one libmpv core, its serial control queue, event handling and the remembered tempo / key /
//! DSP-chain / audio-delay state. Port of `BridgeRenderer` minus everything that touches OpenGL or AppKit (those arrive in V3+).
//! Behaviour is the Objective-C bridge's, line for line; the comments name the show-found reasons so they are not "tidied" away.
use crate::audio_device::device_for_label;
use crate::bridge_log;
use crate::flags::{Flags, TEXTURE_H, TEXTURE_W};
use crate::mpvapi::{self, Handle, Mpv, MpvEventEndFile, MpvEventLogMessage};
use crate::render::Renderer;
use std::ffi::CStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::JoinHandle;
use std::time::Duration;


type Job = Box<dyn FnOnce(&Arc<Inner>) + Send>;

struct State {
    tempo_percent: i32,
    semitones: i32,
    dsp_chain: String,
    audio_delay: f64,
    pending_audio: Option<String>,
    serial: u64,
    /// The DSP half of the `af` chain that the live stretch filter was built with; Some = the labelled stretch filter is in the chain.
    stretch_dsp: Option<String>,
}


/// Which mpv audio filter carries key/tempo: "signalsmith" (default) or "rubberband" (SINGWS_KEY_ENGINE=rubberband).
fn stretch_engine() -> &'static str {
    match std::env::var("SINGWS_KEY_ENGINE").ok().as_deref() {
        Some("rubberband") => "rubberband",
        _ => "signalsmith",
    }
}

pub struct Inner {
    api: &'static Mpv,
    mpv: OnceLock<Handle>,
    flags: Arc<Flags>,
    renderer: OnceLock<Arc<Renderer>>,
    play_when_loaded: AtomicBool,
    state: Mutex<State>,
    tx: Mutex<Option<Sender<Job>>>,
}

pub struct Player {
    inner: Arc<Inner>,
    threads: Mutex<Vec<JoinHandle<()>>>,
}

const TW: f64 = TEXTURE_W as f64;
const TH: f64 = TEXTURE_H as f64;

fn f64_bits(v: f64) -> u64 {
    v.to_bits()
}

impl Inner {
    fn handle(&self) -> Option<Handle> {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return None; // the core is being / has been freed
        }
        self.mpv.get().copied()
    }

    /// Fire-and-forget mpv work from the caller's thread. Serial, so ordering against a pending load is preserved; async, so the
    /// GUI thread never waits on the core (mpv can hold its core lock for hundreds of ms while an MP3 and a CDG open).
    fn on_control(self: &Arc<Self>, job: Job) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if std::env::var_os("SINGWS_MPV_SYNC_LOAD").is_some() {
            job(self);
            return;
        }
        if let Some(tx) = self.tx.lock().unwrap().as_ref() {
            let _ = tx.send(job);
        }
    }

    fn set_option(&self, h: Handle, name: &str, value: &str) -> bool {
        let r = self.api.set_option(h, name, value);
        if r < 0 {
            bridge_log!("[bridge] option {} failed: {}", name, self.api.error_string(r));
        }
        r >= 0
    }

    /// mpv has ONE `af` property: key (rubberband) and the SingWS DSP chain share it, so neither writes it directly. The DSP stages
    /// must precede rubberband (libmpv warns that a speed-changing filter before libavfilter stages can desynchronize audio).
    /// Callers are on the control queue, which owns `dsp_chain` / `semitones`.
    fn apply_audio_filters(&self) {
        let Some(h) = self.handle() else { return };
        if self.flags.loading.load(Ordering::SeqCst) {
            return;
        }
        let (mut chain, semitones, tempo, dsp_len, live_dsp) = {
            let s = self.state.lock().unwrap();
            (s.dsp_chain.clone(), s.semitones, s.tempo_percent, s.dsp_chain.len(), s.stretch_dsp.clone())
        };
        let pitch = 2f64.powf(semitones as f64 / 12.0);
        // Once the labelled stretch filter is running with this same DSP half, a key change goes straight to the running filter. Rebuilding
        // the whole `af` graph restarts the stretcher mid-song, which is audible as a click (measured: Signalsmith itself is click-free for
        // abrupt key and tempo jumps). The filter then stays in the chain (at pitch 1.0 for "no key change") until the DSP half changes
        // (next song) so that returning to 0 / 100% does not click either.
        if live_dsp.as_deref() == Some(chain.as_str()) {
            let r = self.api.command(h, &["af-command", "stretch", "set-pitch", &format!("{:.6}", pitch)]);
            if r >= 0 {
                bridge_log!("[bridge] af key={} live pitch-scale={:.6} (chain kept)", semitones, pitch);
                return;
            }
            bridge_log!("[bridge] live pitch change failed ({}); rebuilding the chain", self.api.error_string(r));
        }
        // The stretch filter takes the key ONLY from `pitch-scale` and the tempo ONLY from mpv's `speed`, so the two never touch.
        let dsp_only = chain.clone();
        let engine = stretch_engine();
        let mut stretch_used = false;
        if semitones != 0 || tempo != 100 {
            if !chain.is_empty() {
                chain.push(',');
            }
            chain.push_str(&format!("@stretch:{}=pitch-scale={:.6}", engine, pitch));
            stretch_used = true;
        }
        let mut r = self.api.set_string(h, "af", &chain);
        if r < 0 && stretch_used && engine == "signalsmith" {
            // A libmpv built before the Signalsmith filter existed: fall back to rubberband rather than lose the DSP chain too.
            bridge_log!("[bridge] signalsmith filter unavailable ({}); using rubberband", self.api.error_string(r));
            chain = dsp_only.clone();
            if !chain.is_empty() {
                chain.push(',');
            }
            chain.push_str(&format!("@stretch:rubberband=pitch-scale={:.6}", pitch));
            r = self.api.set_string(h, "af", &chain);
        }
        self.state.lock().unwrap().stretch_dsp = if r >= 0 && stretch_used { Some(dsp_only) } else { None };
        if r < 0 {
            bridge_log!("[bridge] af chain rejected ({}): {}", self.api.error_string(r), chain);
        } else {
            bridge_log!(
                "[bridge] af key={} dsp={} chars -> {}",
                semitones,
                dsp_len,
                if chain.is_empty() { "(passthrough)" } else { chain.as_str() }
            );
        }
    }

    fn measure_pillarbox(&self, reason: &str) {
        let Some(h) = self.handle() else { return };
        let dw = self.api.get_int64(h, "dwidth");
        let dh = self.api.get_int64(h, "dheight");
        match (dw, dh) {
            (Ok(w), Ok(hh)) if w > 0 && hh > 0 => {
                let target = TW / TH;
                let span = ((w as f64 / hh as f64) / target).clamp(0.05, 1.0);
                let previous = f64::from_bits(self.flags.span_x.swap(f64_bits(span), Ordering::SeqCst));
                if (previous - span).abs() > 1e-4 {
                    // FILE_LOADED measures from the control queue, so the redraw request hops to the thread AppKit allows.
                    if let Some(r) = self.renderer.get() {
                        r.needs_display_async();
                    }
                }
                bridge_log!("[bridge] picture {}x{} span={:.5} panel={:.5} at {}", w, hh, span, (1.0 - span) / 2.0, reason);
            }
            (rw, rh) => {
                let text = |r: &Result<i64, i32>| match r {
                    Ok(_) => "success".to_string(),
                    Err(e) => self.api.error_string(*e),
                };
                bridge_log!(
                    "[bridge] picture geometry unavailable at {} ({}/{}) span kept={:.5}",
                    reason,
                    text(&rw),
                    text(&rh),
                    f64::from_bits(self.flags.span_x.load(Ordering::SeqCst))
                );
            }
        }
    }

    /// GUI-thread half of a load: state only, no mpv call that can block on the core lock.
    fn prepare_for_load(&self, video: &str) {
        self.flags.loading.store(true, Ordering::SeqCst);
        self.play_when_loaded.store(false, Ordering::SeqCst);
        self.flags.has_frame.store(false, Ordering::SeqCst);
        let cdg = std::path::Path::new(video).extension().map(|e| e.eq_ignore_ascii_case("cdg")).unwrap_or(false);
        self.flags.is_cdg.store(cdg, Ordering::SeqCst);
        // Until FILE_LOADED reports the real dwidth/dheight assume the format's nominal geometry rather than carrying the
        // previous song's over.
        let target = TW / TH;
        let nominal = if cdg { 300.0 / 216.0 } else { target };
        self.flags.span_x.store(f64_bits(nominal / target), Ordering::SeqCst);
        // CDG packets update tiles on a retained canvas that merely hiding the surface does not erase: clear both retained targets
        // before the new decoder may draw, so the previous song's lyrics cannot show through the next song's first packets.
        if let Some(r) = self.renderer.get() {
            r.clear_for_load();
        }
    }

    fn init_core(self: &Arc<Self>, events: &mut Vec<JoinHandle<()>>) -> Option<Handle> {
        let h = self.api.create()?;
        let vo = if self.renderer.get().is_some() {
            "libmpv".to_string()
        } else {
            std::env::var("SINGWS_BRIDGE_VO").unwrap_or_else(|_| "null".to_string())
        };
        self.set_option(h, "config", "no");
        self.set_option(h, "vo", &vo);
        if let Ok(ao) = std::env::var("SINGWS_BRIDGE_AO") {
            if !ao.is_empty() {
                self.set_option(h, "ao", &ao);
            }
        }
        self.set_option(h, "keep-open", "yes");
        self.set_option(h, "video-sync", "audio");
        self.set_option(h, "hwdec", "auto-safe");
        self.set_option(h, "audio-display", "no");
        // Match the proven stable SingWS mpv backend: in-process property/GPU activity can occasionally hold the core lock for
        // over 400 ms, so the former 400 ms audio buffer had no safety margin at all.
        self.set_option(h, "cache", "yes");
        self.set_option(h, "demuxer-max-bytes", "256MiB");
        self.set_option(h, "audio-buffer", "1.0");
        self.set_option(h, "demuxer-readahead-secs", "10");
        self.set_option(h, "input-default-bindings", "no");
        self.set_option(h, "pause", "yes");
        self.api.request_log_messages(h, "warn");
        let r = self.api.initialize(h);
        if r < 0 {
            bridge_log!("[bridge] mpv_initialize failed: {}", self.api.error_string(r));
            self.api.terminate_destroy(h);
            return None;
        }
        bridge_log!("[bridge] playback buffering audio=1.0s readahead=10s cache=256MiB");
        if let Some(r) = self.renderer.get() {
            if !r.create_render_context(h) {
                self.api.terminate_destroy(h);
                return None;
            }
        }
        let _ = self.mpv.set(h);
        let me = Arc::clone(self);
        events.push(std::thread::Builder::new().name("singws-video-events".into()).spawn(move || me.event_loop()).ok()?);
        Some(h)
    }

    fn run_load(self: &Arc<Self>, video: &str, audio: Option<&str>, events: &mut Vec<JoinHandle<()>>) -> bool {
        let h = match self.handle() {
            Some(h) => {
                // mpv's loadfile returns before the previous file has stopped, and audio-files and several playback properties are
                // file-local: completing the cheap stop first keeps adjacent loads from inheriting each other's state.
                let r = self.api.command(h, &["stop"]);
                if r < 0 {
                    bridge_log!("[bridge] pre-load stop failed: {}", self.api.error_string(r));
                }
                h
            }
            None => match self.init_core(events) {
                Some(h) => h,
                None => return false,
            },
        };
        self.api.set_flag(h, "pause", true);
        // The bundled FFmpeg gives a few valid CDG files a very low probe score. A threshold of 1 with automatic detection is
        // unsafe (ordinary CDG packet bytes can false-positive as another raw format: LG126-03 and SF113-03 reported bogus
        // durations and no frame), so force ONLY the graphics input to the CDG demuxer. For CDG the MP3 is attached after
        // FILE_LOADED, otherwise the forced demuxer would also apply to --audio-files.
        let cdg = self.flags.is_cdg.load(Ordering::SeqCst);
        let main_format = if cdg {
            "cdg".to_string()
        } else {
            std::path::Path::new(video).extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default()
        };
        let log_err = |what: &str, r: i32| {
            if r < 0 {
                bridge_log!("[bridge] {} failed: {}", what, self.api.error_string(r));
            }
        };
        log_err("demuxer probe threshold", self.api.set_string(h, "demuxer-lavf-probescore", if cdg { "1" } else { "26" }));
        log_err("demuxer format", self.api.set_string(h, "demuxer-lavf-format", &main_format));
        // Ordinary MP4 karaoke is deliberately stretched to the 16:9 shared texture (operator decision); CDG keeps its aspect.
        log_err("video aspect override", self.api.set_string(h, "video-aspect-override", if cdg { "no" } else { "16:9" }));
        let serial = {
            let mut s = self.state.lock().unwrap();
            s.pending_audio = audio.filter(|a| !a.is_empty()).map(str::to_string);
            s.serial += 1;
            s.serial
        };
        let r = self.api.clear_audio_files(h);
        if r < 0 {
            bridge_log!("[bridge] audio-files failed: {}", self.api.error_string(r));
            self.flags.loading.store(false, Ordering::SeqCst);
            return false;
        }
        bridge_log!("[bridge] load queued serial={} video={} audio={}", serial, video, audio.filter(|a| !a.is_empty()).unwrap_or("(internal)"));
        let r = self.api.command_async(h, serial, &["loadfile", video, "replace"]);
        if r < 0 {
            self.flags.loading.store(false, Ordering::SeqCst);
            bridge_log!("[bridge] loadfile rejected: {}", self.api.error_string(r));
        }
        r >= 0
    }

    fn on_file_loaded(self: &Arc<Self>) {
        let Some(h) = self.handle() else { return };
        self.flags.loading.store(false, Ordering::SeqCst);
        if let Some(format) = self.api.get_string(h, "file-format") {
            bridge_log!("[bridge] detected media format={}", format);
        }
        // File-local tuning only after the replacement file owns the playback state, then honour the Play request made while
        // loadfile was pending. All on the control queue: these property writes used to block the GUI thread at exactly the
        // moment the show is between singers.
        self.on_control(Box::new(|me: &Arc<Inner>| {
            let Some(h) = me.handle() else { return };
            let external = me.state.lock().unwrap().pending_audio.take();
            if me.flags.is_cdg.load(Ordering::SeqCst) {
                if let Some(audio) = external.filter(|a| !a.is_empty()) {
                    let audio_format = std::path::Path::new(&audio).extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
                    let fr = me.api.set_string(h, "demuxer-lavf-format", &audio_format);
                    let ar = if fr < 0 { fr } else { me.api.command(h, &["audio-add", &audio, "select"]) };
                    if ar < 0 {
                        bridge_log!("[bridge] external audio attach failed: {}", me.api.error_string(ar));
                    } else {
                        bridge_log!("[bridge] external audio attached format={} file={}", audio_format, audio);
                    }
                }
            }
            me.measure_pillarbox("file_loaded");
            let (tempo, semitones) = {
                let s = me.state.lock().unwrap();
                (s.tempo_percent, s.semitones)
            };
            if !me.flags.loading.load(Ordering::SeqCst) {
                me.api.set_double(h, "speed", tempo as f64 / 100.0);
            }
            // Re-composes key AND the DSP chain: without the DSP half the EQ/master bus would survive only until the next song.
            me.apply_audio_filters();
            // loadfile resets audio-delay, so the CDG calibration must be re-asserted or it would apply only to the first song.
            if !me.flags.loading.load(Ordering::SeqCst) {
                let delay = me.state.lock().unwrap().audio_delay;
                me.api.set_double(h, "audio-delay", delay);
                bridge_log!("[bridge] audio-delay={:.3}s (CDG visual lead)", delay);
            }
            let should_play = me.play_when_loaded.load(Ordering::SeqCst);
            me.api.set_flag(h, "pause", !should_play);
            let serial = me.state.lock().unwrap().serial;
            bridge_log!("[bridge] media loaded serial={} play={} tempo={} key={}", serial, should_play as i32, tempo, semitones);
        }));
    }

    fn event_loop(self: Arc<Self>) {
        let Some(h) = self.handle() else { return };
        let (mut last, mut repeated): (String, u32) = (String::new(), 0);
        while !self.flags.stopping.load(Ordering::SeqCst) {
            let e = self.api.wait_event(h, 0.1);
            if e.is_null() {
                continue;
            }
            let ev = unsafe { &*e };
            match ev.event_id {
                mpvapi::EVENT_NONE => {}
                mpvapi::EVENT_LOG_MESSAGE => {
                    let m = ev.data as *const MpvEventLogMessage;
                    if m.is_null() {
                        continue;
                    }
                    let (level, text) = unsafe {
                        let level = if (*m).level.is_null() { String::new() } else { CStr::from_ptr((*m).level).to_string_lossy().into_owned() };
                        let text = if (*m).text.is_null() { String::new() } else { CStr::from_ptr((*m).text).to_string_lossy().into_owned() };
                        (level, text)
                    };
                    let current = format!("{level}\n{text}");
                    if current == last {
                        repeated += 1;
                    } else {
                        if repeated > 3 {
                            bridge_log!("[mpv] previous message repeated {} times total", repeated);
                        }
                        last = current;
                        repeated = 1;
                    }
                    // Enough immediate context for diagnosis, then only logarithmic progress for a decoder flooding one fault.
                    if repeated <= 3 || repeated == 10 || repeated == 100 || repeated == 1000 {
                        bridge_log!("[mpv/{}] {}{}", level, text, if repeated > 3 { " (repeated)" } else { "" });
                    }
                }
                mpvapi::EVENT_FILE_LOADED => self.on_file_loaded(),
                mpvapi::EVENT_VIDEO_RECONFIG => {
                    // Headless stand-in for "a frame was drawn"; with a renderer has_frame is set by the real render.
                    if self.renderer.get().is_none() {
                        self.flags.has_frame.store(true, Ordering::SeqCst);
                    }
                    // The event that actually carries dwidth/dheight for a CDG.
                    self.on_control(Box::new(|me: &Arc<Inner>| me.measure_pillarbox("video_reconfig")));
                }
                mpvapi::EVENT_END_FILE if self.flags.loading.load(Ordering::SeqCst) => {
                    let end = ev.data as *const MpvEventEndFile;
                    if !end.is_null() && unsafe { (*end).reason } == mpvapi::END_FILE_ERROR {
                        self.flags.loading.store(false, Ordering::SeqCst);
                        bridge_log!("[bridge] load ended with error: {}", self.api.error_string(unsafe { (*end).error }));
                    }
                }
                _ => {}
            }
        }
    }
}

impl Player {
    /// Equivalent of `singws_bridge_create` minus the views: initialises the core and starts the first load inline.
    pub fn create(video: &str, audio: Option<&str>, views: Option<(usize, usize)>) -> Option<Box<Player>> {
        let api = mpvapi::api().map_err(|e| bridge_log!("[bridge] {}", e)).ok()?;
        let flags = Arc::new(Flags {
            loading: AtomicBool::new(false),
            is_cdg: AtomicBool::new(false),
            has_frame: AtomicBool::new(false),
            stopping: AtomicBool::new(false),
            span_x: std::sync::atomic::AtomicU64::new(f64_bits((300.0 / 216.0) / (TW / TH))),
        });
        // With views the picture path is live (vo=libmpv + the renderer); without them the core runs headless (vo=null).
        let renderer = match views {
            Some((out, prev)) if out != 0 && prev != 0 => Some(Renderer::new(Arc::clone(&flags), out, prev)?),
            _ => None,
        };
        let inner = Arc::new(Inner {
            api,
            mpv: OnceLock::new(),
            flags,
            renderer: OnceLock::new(),
            play_when_loaded: AtomicBool::new(false),
            state: Mutex::new(State { tempo_percent: 100, semitones: 0, dsp_chain: String::new(), audio_delay: 0.0, pending_audio: None, serial: 0, stretch_dsp: None }),
            tx: Mutex::new(None),
        });
        if let Some(r) = renderer {
            let _ = inner.renderer.set(r);
        }
        let (tx, rx): (Sender<Job>, Receiver<Job>) = mpsc::channel();
        *inner.tx.lock().unwrap() = Some(tx);
        let worker = Arc::clone(&inner);
        let control = std::thread::Builder::new()
            .name("singws-video-control".into())
            .spawn(move || {
                while let Ok(job) = rx.recv() {
                    job(&worker);
                }
            })
            .ok()?;
        let player = Box::new(Player { inner, threads: Mutex::new(vec![control]) });
        if !player.load(video, audio) {
            player.shutdown();
            return None;
        }
        Some(player)
    }

    pub fn load(&self, video: &str, audio: Option<&str>) -> bool {
        let inner = &self.inner;
        if video.is_empty() {
            return false;
        }
        // The first load of a session creates the core inline (its caller needs the pass/fail); every later song change goes
        // to the control queue.
        if inner.handle().is_none() {
            inner.prepare_for_load(video);
            let mut events = Vec::new();
            let ok = inner.run_load(video, audio, &mut events);
            self.threads.lock().unwrap().extend(events);
            return ok;
        }
        inner.prepare_for_load(video);
        let (v, a) = (video.to_string(), audio.map(str::to_string));
        if std::env::var_os("SINGWS_MPV_SYNC_LOAD").is_some() {
            let mut events = Vec::new();
            return inner.run_load(video, audio, &mut events);
        }
        inner.on_control(Box::new(move |me: &Arc<Inner>| {
            let mut events = Vec::new();
            me.run_load(&v, a.as_deref(), &mut events);
        }));
        // Real load failures still surface: mpv reports END_FILE/ERROR in the event loop and run_load logs what it rejects.
        true
    }

    pub fn set_paused(&self, paused: bool) {
        let inner = &self.inner;
        if inner.handle().is_none() {
            return;
        }
        inner.play_when_loaded.store(!paused, Ordering::SeqCst);
        if inner.flags.loading.load(Ordering::SeqCst) && !paused {
            return;
        }
        inner.on_control(Box::new(move |me: &Arc<Inner>| {
            if let Some(h) = me.handle() {
                me.api.set_flag(h, "pause", paused);
            }
        }));
    }

    pub fn stop(&self) {
        let inner = &self.inner;
        if inner.handle().is_none() {
            return;
        }
        inner.flags.loading.store(false, Ordering::SeqCst);
        inner.play_when_loaded.store(false, Ordering::SeqCst);
        inner.on_control(Box::new(|me: &Arc<Inner>| {
            if let Some(h) = me.handle() {
                me.api.command_async(h, 3, &["stop"]);
            }
        }));
        inner.flags.has_frame.store(false, Ordering::SeqCst);
        if let Some(r) = inner.renderer.get() {
            r.needs_display_async();
        }
    }

    pub fn seek_ms(&self, ms: i64) {
        if self.inner.handle().is_none() {
            return;
        }
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            if let Some(h) = me.handle() {
                let value = format!("{:.3}", ms.max(0) as f64 / 1000.0);
                me.api.command_async(h, 4, &["seek", &value, "absolute+exact"]);
            }
        }));
    }

    fn double(&self, name: &str, fallback: f64) -> f64 {
        self.inner.handle().and_then(|h| self.inner.api.get_double(h, name).ok()).unwrap_or(fallback)
    }
    fn flag(&self, name: &str, fallback: bool) -> bool {
        self.inner.handle().and_then(|h| self.inner.api.get_flag(h, name).ok()).unwrap_or(fallback)
    }

    /// Report "not ready" until FILE_LOADED rather than making the Qt timers that poll these getters wait on mpv's core lock.
    pub fn position_ms(&self) -> i64 {
        if self.inner.flags.loading.load(Ordering::SeqCst) {
            return 0;
        }
        // CDG packets update sparsely, so the general time-pos can look frozen between graphics commands. External MP3 audio
        // is the authoritative, continuously advancing playback clock for MP3+G.
        let audio_pts = self.double("audio-pts", -1.0);
        let seconds = if audio_pts >= 0.0 { audio_pts } else { self.double("time-pos", 0.0) };
        (seconds * 1000.0).round() as i64
    }
    pub fn duration_ms(&self) -> i64 {
        if self.inner.flags.loading.load(Ordering::SeqCst) {
            return 0;
        }
        (self.double("duration", 0.0) * 1000.0).round() as i64
    }
    pub fn is_playing(&self) -> bool {
        if self.inner.flags.loading.load(Ordering::SeqCst) {
            return false;
        }
        self.inner.handle().is_some() && !self.flag("pause", true) && !self.flag("idle-active", true)
    }
    pub fn at_end(&self) -> bool {
        if self.inner.flags.loading.load(Ordering::SeqCst) {
            return false;
        }
        self.inner.handle().is_some() && self.flag("eof-reached", false)
    }
    pub fn visual_ready(&self) -> bool {
        self.inner.flags.has_frame.load(Ordering::SeqCst)
    }

    pub fn set_volume_percent(&self, value: f64) {
        if self.inner.handle().is_none() {
            return;
        }
        let v = value.clamp(0.0, 100.0);
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            if let Some(h) = me.handle() {
                me.api.set_double(h, "volume", v);
            }
        }));
    }

    pub fn set_audio_device(&self, name: &str) {
        if self.inner.handle().is_none() {
            return;
        }
        let requested = if name.is_empty() { "auto".to_string() } else { name.to_string() };
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            let Some(h) = me.handle() else { return };
            let resolved = if requested.is_empty() || requested.eq_ignore_ascii_case("auto") {
                Some("auto".to_string())
            } else {
                me.api.audio_devices(h).and_then(|devices| device_for_label(&devices, &requested))
            };
            let device = match resolved {
                Some(d) => {
                    bridge_log!("[audio] output requested={} resolved={}", requested, d);
                    d
                }
                None => {
                    bridge_log!("[audio] requested output unavailable: {}; refusing system default", requested);
                    // An implicit default can be the AirPlay TV. A deliberately invalid device fails silent instead of sending
                    // show audio to the display.
                    "singws/no-safe-local-output".to_string()
                }
            };
            me.api.set_string(h, "audio-device", &device);
        }));
    }

    pub fn set_tempo_percent(&self, percent: i32) {
        if self.inner.handle().is_none() {
            return;
        }
        let wanted = percent.clamp(25, 400);
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            let previous = {
                let mut s = me.state.lock().unwrap();
                let p = s.tempo_percent;
                s.tempo_percent = wanted;
                p
            };
            let Some(h) = me.handle() else { return };
            if me.flags.loading.load(Ordering::SeqCst) {
                return;
            }
            me.api.set_double(h, "speed", wanted as f64 / 100.0);
            bridge_log!("[bridge] tempo={}%", wanted);
            // Crossing to or from 100% adds or removes rubberband from the chain, so the chain has to be recomposed.
            if (previous == 100) != (wanted == 100) {
                me.apply_audio_filters();
            }
        }));
    }

    pub fn set_semitones(&self, semitones: i32) {
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            me.state.lock().unwrap().semitones = semitones;
            me.apply_audio_filters();
        }));
    }

    pub fn set_dsp_chain(&self, chain: &str) {
        // Copied here: the caller's buffer is Python-owned and the job outlives it.
        let copy = chain.to_string();
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            me.state.lock().unwrap().dsp_chain = copy;
            me.apply_audio_filters();
        }));
    }

    /// SingWS's CDG calibration is a VISUAL LEAD (display = audible + offset): a positive offset shows lyrics earlier, which is
    /// exactly mpv's `audio-delay` with the same sign.
    pub fn set_audio_delay(&self, seconds: f64) {
        let wanted = seconds.clamp(-3.0, 3.0);
        self.inner.on_control(Box::new(move |me: &Arc<Inner>| {
            me.state.lock().unwrap().audio_delay = wanted;
            let Some(h) = me.handle() else { return };
            if me.flags.loading.load(Ordering::SeqCst) {
                return;
            }
            let r = me.api.set_double(h, "audio-delay", wanted);
            if r < 0 {
                bridge_log!("[bridge] audio-delay rejected: {}", me.api.error_string(r));
            } else {
                bridge_log!("[bridge] audio-delay={:.3}s (CDG visual lead)", wanted);
            }
        }));
    }

    pub fn renderer(&self) -> Option<&Arc<Renderer>> {
        self.inner.renderer.get()
    }

    /// The current picture as BGRA: (bytes, width, height, stride). CDG comes from the retained 300x216 texture; other video via mpv's
    /// `screenshot-raw video` (no full-HD GPU readback of our own).
    pub fn grab_frame(&self) -> Option<(Vec<u8>, i32, i32, i32)> {
        let inner = &self.inner;
        let h = inner.handle()?;
        if inner.flags.is_cdg.load(Ordering::SeqCst) && inner.flags.has_frame.load(Ordering::SeqCst) {
            if let Some(r) = inner.renderer.get() {
                return r.grab_cdg().map(|b| (b, 300, 216, 1200));
            }
        }
        match inner.api.screenshot_raw(h) {
            Ok(Some((bytes, w, hh, stride))) => Some((bytes, w as i32, hh as i32, stride as i32)),
            Ok(None) => {
                bridge_log!("[bridge] screenshot-raw gave no usable image");
                None
            }
            Err(e) => {
                bridge_log!("[bridge] screenshot-raw failed: {}", inner.api.error_string(e));
                None
            }
        }
    }

    /// Fraction of the shared texture the picture occupies (used by the compositor from V4).
    pub fn picture_span(&self) -> f64 {
        f64::from_bits(self.inner.flags.span_x.load(Ordering::SeqCst))
    }

    pub fn shutdown(&self) {
        let inner = &self.inner;
        if inner.flags.stopping.swap(true, Ordering::SeqCst) {
            return;
        }
        // NOTE: `handle()` returns None once `stopping` is set, so read the raw handle here. (An earlier version used handle() and
        // leaked the core: the leaked core kept libmpv's process-wide ffmpeg log hook, which silenced the silence scanner.)
        let core = inner.mpv.get().copied();
        // Let in-flight jobs finish before the core is freed under them: drop the sender so the control thread drains and exits.
        *inner.tx.lock().unwrap() = None;
        let threads: Vec<JoinHandle<()>> = std::mem::take(&mut *self.threads.lock().unwrap());
        if let Some(h) = core {
            inner.api.wakeup(h);
        }
        for t in threads {
            let _ = t.join();
        }
        if let Some(r) = inner.renderer.get() {
            r.free_render_context(); // must precede destroying the core it renders for
        }
        if let Some(h) = core {
            inner.api.set_wakeup_callback_none(h);
            inner.api.terminate_destroy(h);
        }
        if let Some(r) = inner.renderer.get() {
            r.destroy_views();
        }
        bridge_log!("[bridge] clean shutdown");
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[allow(dead_code)]
pub fn settle() {
    std::thread::sleep(Duration::from_millis(1));
}
