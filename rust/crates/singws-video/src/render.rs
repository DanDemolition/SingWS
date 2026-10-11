//! The picture path (macOS, main-thread mode): one libmpv render context draws each frame once into a shared texture; the output and the
//! preview view each draw that texture with the one shader. Port of `renderFrame` / `presentViewNow` / the context setup in `bridge.mm`.
//! The views and GL contexts themselves live in the thin Objective-C shim (shim/video_shim.m). V3 covers the output + preview views;
//! the background video, rotation/spotlight underlays, frame grab and the private render thread arrive in V4-V5 (their textures and
//! uniforms already exist here so the shader's bindings are complete).
use crate::bridge_log;
use crate::flags::{Flags, CDG_H, CDG_W, TEXTURE_H, TEXTURE_W};
use crate::gl::*;
use crate::mpvapi::{self, Handle, Mpv, OpenglInitParams};
use crate::shaders;
use std::ffi::{c_char, c_int, c_void, CString};
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicI64, AtomicPtr, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, Weak};

extern "C" {
    fn shim_master_create() -> *mut c_void;
    fn shim_master_make_current(m: *mut c_void);
    fn shim_master_lock(m: *mut c_void);
    fn shim_master_unlock(m: *mut c_void);
    fn shim_master_destroy(m: *mut c_void);
    fn shim_view_create(master: *mut c_void, parent: usize, cb: Option<ViewEventFn>, user: *mut c_void) -> *mut c_void;
    fn shim_view_destroy(v: *mut c_void);
    fn shim_view_state(v: *mut c_void, w: *mut c_int, h: *mut c_int) -> c_int;
    fn shim_view_make_current(v: *mut c_void);
    fn shim_view_update(v: *mut c_void);
    fn shim_view_lock(v: *mut c_void);
    fn shim_view_unlock(v: *mut c_void);
    fn shim_view_flush(v: *mut c_void);
    fn shim_view_set_needs_display(v: *mut c_void);
    fn shim_view_attach(v: *mut c_void) -> c_int;
    fn shim_view_refresh(v: *mut c_void, parent: usize) -> c_int;
    fn shim_view_create_behind(master: *mut c_void, host: usize, cb: Option<ViewEventFn>, user: *mut c_void) -> *mut c_void;
    fn shim_view_set_ignores_mouse(v: *mut c_void, on: c_int);
    fn shim_view_set_lock_update(v: *mut c_void, on: c_int);
    fn shim_view_set_alpha(v: *mut c_void, a: f64);
    fn shim_view_set_hidden(v: *mut c_void, hidden: c_int);
    fn shim_view_is_hidden(v: *mut c_void) -> c_int;
    fn shim_view_animate_alpha(v: *mut c_void, alpha: f64, seconds: f64, done: extern "C" fn(*mut c_void), ctx: *mut c_void);
    fn shim_view_probe(v: *mut c_void, w: *mut c_int, h: *mut c_int) -> c_int;
    fn shim_view_update_locked(v: *mut c_void);
    fn shim_queue_create() -> *mut c_void;
    fn shim_queue_async(q: *mut c_void, f: extern "C" fn(*mut c_void), ctx: *mut c_void);
    fn shim_queue_sync(q: *mut c_void, f: extern "C" fn(*mut c_void), ctx: *mut c_void);
    fn shim_queue_after_ms(q: *mut c_void, ms: c_int, f: extern "C" fn(*mut c_void), ctx: *mut c_void);
    fn shim_queue_is_current(q: *mut c_void) -> c_int;
    fn shim_queue_release(q: *mut c_void);
    fn shim_timer_start(seconds: f64, f: extern "C" fn(*mut c_void), ctx: *mut c_void) -> *mut c_void;
    fn shim_timer_stop(t: *mut c_void);
    fn shim_dispatch_main(f: extern "C" fn(*mut c_void), ctx: *mut c_void);
    fn shim_dispatch_main_after_ms(ms: c_int, f: extern "C" fn(*mut c_void), ctx: *mut c_void);
}

type ViewEventFn = unsafe extern "C" fn(*mut c_void, *mut c_void, c_int);

// ---- running closures on the main queue ----------------------------------------------------------------------------------------

extern "C" fn run_boxed(ctx: *mut c_void) {
    let f: Box<Box<dyn FnOnce() + Send>> = unsafe { Box::from_raw(ctx as *mut Box<dyn FnOnce() + Send>) };
    f();
}
fn dispatch_main(f: impl FnOnce() + Send + 'static) {
    let boxed: Box<Box<dyn FnOnce() + Send>> = Box::new(Box::new(f));
    unsafe { shim_dispatch_main(run_boxed, Box::into_raw(boxed) as *mut c_void) };
}
fn dispatch_main_after(ms: i32, f: impl FnOnce() + Send + 'static) {
    let boxed: Box<Box<dyn FnOnce() + Send>> = Box::new(Box::new(f));
    unsafe { shim_dispatch_main_after_ms(ms, run_boxed, Box::into_raw(boxed) as *mut c_void) };
}

/// Set by `singws_bridge_set_render_thread` (or the SINGWS_RENDER_THREAD env var), read once when a renderer is created.
pub static RENDER_THREAD_REQUESTED: AtomicBool = AtomicBool::new(false);

fn render_thread_wanted() -> bool {
    RENDER_THREAD_REQUESTED.load(Ordering::SeqCst)
        || std::env::var("SINGWS_RENDER_THREAD").map(|v| !v.is_empty() && v != "0").unwrap_or(false)
}

fn dispatch_queue(q: *mut c_void, f: impl FnOnce() + Send + 'static) {
    let boxed: Box<Box<dyn FnOnce() + Send>> = Box::new(Box::new(f));
    unsafe { shim_queue_async(q, run_boxed, Box::into_raw(boxed) as *mut c_void) };
}
fn dispatch_queue_after(q: *mut c_void, ms: i32, f: impl FnOnce() + Send + 'static) {
    let boxed: Box<Box<dyn FnOnce() + Send>> = Box::new(Box::new(f));
    unsafe { shim_queue_after_ms(q, ms, run_boxed, Box::into_raw(boxed) as *mut c_void) };
}

/// What the render thread may know about a view without touching AppKit (render-thread mode); refreshed on the main thread.
struct ViewCache {
    presentable: AtomicBool,
    w: AtomicI32,
    h: AtomicI32,
}

const KIND_OUTPUT: usize = 0;
const KIND_PREVIEW: usize = 1;
#[allow(dead_code)]
const KIND_ROTATION: usize = 2;
#[allow(dead_code)]
const KIND_SPOTLIGHT: usize = 3;
const KIND_NAMES: [&str; 4] = ["output", "preview", "rotation", "spotlight"];

struct Uniforms {
    scale: GLint,
    uv_scale: GLint,
    uv_offset: GLint,
    texture: GLint,
    sidefill: GLint,
    panel: GLint,
    bg_texture: GLint,
    bg_opacity: GLint,
    prev_bg_texture: GLint,
    prev_bg_opacity: GLint,
    bg_mix: GLint,
}

struct GlState {
    tex: GLuint,
    fbo: GLuint,
    cdg_tex: GLuint,
    cdg_fbo: GLuint,
    bg_tex: GLuint,
    bg_fbo: GLuint,
    prev_bg_tex: GLuint,
    program: GLuint,
    u: Uniforms,
    vao: [GLuint; 4],
    fence: GLsync,
    /// Fence for the BACKGROUND texture (the old bridge had none: consumer contexts could keep sampling a stale copy of it).
    bg_fence: GLsync,
}
unsafe impl Send for GlState {}

pub struct Renderer {
    api: &'static Mpv,
    flags: Arc<Flags>,
    master: *mut c_void,
    gl: Mutex<GlState>,
    render_ctx: AtomicPtr<c_void>,
    output: AtomicPtr<c_void>,
    preview: AtomicPtr<c_void>,
    rotation: AtomicPtr<c_void>,
    spotlight: AtomicPtr<c_void>,
    rotation_host: AtomicUsize,
    spotlight_host: AtomicUsize,
    spotlight_revision: AtomicU64,
    last_rotation_present: Mutex<Option<std::time::Instant>>,
    /// Render-thread mode: the private serial queue that owns ALL OpenGL work (null = the main queue, the long-standing behaviour).
    gl_queue: *mut c_void,
    cache: [ViewCache; 4],
    cache_timer: AtomicPtr<c_void>,
    render_queued: AtomicBool,
    present_count: AtomicU64,
    sidefill: AtomicI32,
    transitioning: AtomicBool,
    transition_serial: AtomicU64,
    last_skip: [AtomicI32; 4],
    // background animation (a second libmpv core, muted, software decode) and its single-decoder crossfade
    bg_core: Mutex<Option<Handle>>,
    bg_render_ctx: AtomicPtr<c_void>,
    bg_render_queued: AtomicBool,
    bg_event_queued: AtomicBool,
    bg_has_frame: AtomicBool,
    prev_bg_has_frame: AtomicBool,
    bg_at_end: AtomicBool,
    bg_paused: AtomicBool,
    bg_position_ms: AtomicI64,
    bg_opacity: AtomicU64,
    prev_bg_opacity: AtomicU64,
    bg_mix: AtomicU64,
    weak: Weak<Renderer>,
    cb_ctx: *mut Weak<Renderer>,
}
unsafe impl Send for Renderer {}
unsafe impl Sync for Renderer {}

fn compile(ty: GLenum, source: &str) -> GLuint {
    unsafe {
        let shader = glCreateShader(ty);
        let c = CString::new(source).unwrap();
        let ptr = c.as_ptr();
        glShaderSource(shader, 1, &ptr, std::ptr::null());
        glCompileShader(shader);
        let mut ok: GLint = 0;
        glGetShaderiv(shader, GL_COMPILE_STATUS, &mut ok);
        if ok == 0 {
            let mut log = [0 as c_char; 2048];
            glGetShaderInfoLog(shader, 2048, std::ptr::null_mut(), log.as_mut_ptr());
            bridge_log!("[bridge] shader error: {}", std::ffi::CStr::from_ptr(log.as_ptr()).to_string_lossy());
            glDeleteShader(shader);
            return 0;
        }
        shader
    }
}

fn make_program() -> GLuint {
    let (v, f) = (compile(GL_VERTEX_SHADER, shaders::VERTEX), compile(GL_FRAGMENT_SHADER, shaders::FRAGMENT));
    if v == 0 || f == 0 {
        return 0;
    }
    unsafe {
        let p = glCreateProgram();
        glAttachShader(p, v);
        glAttachShader(p, f);
        glLinkProgram(p);
        glDeleteShader(v);
        glDeleteShader(f);
        let mut ok: GLint = 0;
        glGetProgramiv(p, GL_LINK_STATUS, &mut ok);
        if ok == 0 {
            let mut log = [0 as c_char; 2048];
            glGetProgramInfoLog(p, 2048, std::ptr::null_mut(), log.as_mut_ptr());
            bridge_log!("[bridge] link error: {}", std::ffi::CStr::from_ptr(log.as_ptr()).to_string_lossy());
            glDeleteProgram(p);
            return 0;
        }
        p
    }
}

fn uniform(program: GLuint, name: &str) -> GLint {
    let c = CString::new(name).unwrap();
    unsafe { glGetUniformLocation(program, c.as_ptr()) }
}

/// A texture with the given filter, clamped; `fbo` optionally wraps it. Returns (texture, fbo, framebuffer-complete).
unsafe fn make_texture(w: i32, h: i32, filter: GLint, with_fbo: bool) -> (GLuint, GLuint, bool) {
    let mut tex: GLuint = 0;
    glGenTextures(1, &mut tex);
    glBindTexture(GL_TEXTURE_2D, tex);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, std::ptr::null());
    if !with_fbo {
        return (tex, 0, true);
    }
    let mut fbo: GLuint = 0;
    glGenFramebuffers(1, &mut fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
    let status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    (tex, fbo, status == GL_FRAMEBUFFER_COMPLETE)
}

unsafe extern "C" fn get_proc(_ctx: *mut c_void, name: *const c_char) -> *mut c_void {
    dlsym(-2isize as *mut c_void, name) // RTLD_DEFAULT
}

unsafe extern "C" fn render_wake(ctx: *mut c_void) {
    if let Some(r) = (*(ctx as *const Weak<Renderer>)).upgrade() {
        r.schedule_render();
    }
}

unsafe extern "C" fn bg_render_wake(ctx: *mut c_void) {
    if let Some(r) = (*(ctx as *const Weak<Renderer>)).upgrade() {
        r.schedule_bg_render();
    }
}

unsafe extern "C" fn bg_event_wake(ctx: *mut c_void) {
    if let Some(r) = (*(ctx as *const Weak<Renderer>)).upgrade() {
        r.schedule_bg_events();
    }
}

extern "C" fn cache_tick(ctx: *mut c_void) {
    if let Some(r) = unsafe { (*(ctx as *const Weak<Renderer>)).upgrade() } {
        if !r.flags.stopping.load(Ordering::SeqCst) {
            r.refresh_all_caches();
        }
    }
}

unsafe extern "C" fn view_event(user: *mut c_void, view: *mut c_void, kind: c_int) {
    let Some(r) = (*(user as *const Weak<Renderer>)).upgrade() else { return };
    match kind {
        0 => r.present(view),
        2 => r.native_view_attached(view),
        _ => {
            // Geometry changes only matter in render-thread mode: refresh what the render thread knows, then redraw.
            if !r.gl_queue.is_null() && !r.flags.stopping.load(Ordering::SeqCst) {
                r.refresh_view_cache(view);
                r.present(view);
            }
        }
    }
}

impl Renderer {
    /// Creates the master GL context, the shared textures/program and the two views on `output_view` / `preview_view`.
    pub fn new(flags: Arc<Flags>, output_view: usize, preview_view: usize) -> Option<Arc<Renderer>> {
        let api = mpvapi::api().ok()?;
        let master = unsafe { shim_master_create() };
        if master.is_null() {
            bridge_log!("[bridge] could not create the OpenGL context");
            return None;
        }
        let (gl, ok) = unsafe {
            shim_master_make_current(master);
            shim_master_lock(master);
            let (tex, fbo, a) = make_texture(TEXTURE_W, TEXTURE_H, GL_LINEAR, true);
            // CDG is natively 300x216. Render it at that exact size so libmpv cannot soften the palette pixels while scaling into the
            // 1080p video surface; presentation performs the one and only enlargement.
            let (cdg_tex, cdg_fbo, b) = make_texture(CDG_W, CDG_H, GL_NEAREST, true);
            let (bg_tex, bg_fbo, c) = make_texture(TEXTURE_W, TEXTURE_H, GL_LINEAR, true);
            let (prev_bg_tex, _, _) = make_texture(TEXTURE_W, TEXTURE_H, GL_LINEAR, false);
            let program = make_program();
            let u = Uniforms {
                scale: uniform(program, "scale"),
                uv_scale: uniform(program, "uvScale"),
                uv_offset: uniform(program, "uvOffset"),
                texture: uniform(program, "frameTexture"),
                sidefill: uniform(program, "cdgSidefill"),
                panel: uniform(program, "cdgPanel"),
                bg_texture: uniform(program, "backgroundTexture"),
                bg_opacity: uniform(program, "backgroundOpacity"),
                prev_bg_texture: uniform(program, "previousBackgroundTexture"),
                prev_bg_opacity: uniform(program, "previousBackgroundOpacity"),
                bg_mix: uniform(program, "backgroundCrossfadeMix"),
            };
            shim_master_unlock(master);
            (
                GlState { tex, fbo, cdg_tex, cdg_fbo, bg_tex, bg_fbo, prev_bg_tex, program, u, vao: [0; 4], fence: std::ptr::null_mut(), bg_fence: std::ptr::null_mut() },
                a && b && c && program != 0,
            )
        };
        if !ok {
            bridge_log!("[bridge] OpenGL setup failed (framebuffer or shader)");
            unsafe { shim_master_destroy(master) };
            return None;
        }
        let render_mode = render_thread_wanted();
        let renderer = Arc::new_cyclic(|weak: &Weak<Renderer>| Renderer {
            api,
            flags,
            master,
            gl: Mutex::new(gl),
            render_ctx: AtomicPtr::new(std::ptr::null_mut()),
            output: AtomicPtr::new(std::ptr::null_mut()),
            preview: AtomicPtr::new(std::ptr::null_mut()),
            rotation: AtomicPtr::new(std::ptr::null_mut()),
            spotlight: AtomicPtr::new(std::ptr::null_mut()),
            rotation_host: AtomicUsize::new(0),
            spotlight_host: AtomicUsize::new(0),
            spotlight_revision: AtomicU64::new(0),
            last_rotation_present: Mutex::new(None),
            gl_queue: if render_mode { unsafe { shim_queue_create() } } else { std::ptr::null_mut() },
            cache: std::array::from_fn(|_| ViewCache { presentable: AtomicBool::new(false), w: AtomicI32::new(1), h: AtomicI32::new(1) }),
            cache_timer: AtomicPtr::new(std::ptr::null_mut()),
            render_queued: AtomicBool::new(false),
            present_count: AtomicU64::new(0),
            sidefill: AtomicI32::new(0),
            transitioning: AtomicBool::new(false),
            transition_serial: AtomicU64::new(0),
            last_skip: std::array::from_fn(|_| AtomicI32::new(-1)),
            bg_core: Mutex::new(None),
            bg_render_ctx: AtomicPtr::new(std::ptr::null_mut()),
            bg_render_queued: AtomicBool::new(false),
            bg_event_queued: AtomicBool::new(false),
            bg_has_frame: AtomicBool::new(false),
            prev_bg_has_frame: AtomicBool::new(false),
            bg_at_end: AtomicBool::new(false),
            bg_paused: AtomicBool::new(false),
            bg_position_ms: AtomicI64::new(0),
            bg_opacity: AtomicU64::new(0f64.to_bits()),
            prev_bg_opacity: AtomicU64::new(0f64.to_bits()),
            bg_mix: AtomicU64::new(1f64.to_bits()),
            weak: weak.clone(),
            cb_ctx: Box::into_raw(Box::new(weak.clone())),
        });
        let out = unsafe { shim_view_create(master, output_view, Some(view_event), renderer.cb_ctx as *mut c_void) };
        let prev = unsafe { shim_view_create(master, preview_view, Some(view_event), renderer.cb_ctx as *mut c_void) };
        if out.is_null() || prev.is_null() {
            bridge_log!("[bridge] could not attach the output/preview views");
            unsafe {
                shim_view_destroy(out);
                shim_view_destroy(prev);
            }
            return None;
        }
        renderer.output.store(out, Ordering::SeqCst);
        renderer.preview.store(prev, Ordering::SeqCst);
        bridge_log!("[bridge] render thread mode={}", if render_mode { "ON (private render queue)" } else { "off (main thread)" });
        if render_mode {
            for v in [out, prev] {
                unsafe { shim_view_set_lock_update(v, 1) };
            }
            renderer.refresh_all_caches();
            let t = unsafe { shim_timer_start(0.1, cache_tick, renderer.cb_ctx as *mut c_void) };
            renderer.cache_timer.store(t, Ordering::SeqCst);
        }
        Some(renderer)
    }

    pub fn present_count(&self) -> u64 {
        self.present_count.load(Ordering::SeqCst)
    }

    /// Called from the core once mpv is initialised (vo=libmpv). The master GL context must be current on this thread.
    pub fn create_render_context(&self, mpv: Handle) -> bool {
        let mut ok = false;
        self.on_gl_sync(|| ok = self.create_render_context_gl(mpv));
        ok
    }

    fn create_render_context_gl(&self, mpv: Handle) -> bool {
        unsafe {
            shim_master_make_current(self.master);
            let mut init = OpenglInitParams { get_proc_address: get_proc, get_proc_address_ctx: std::ptr::null_mut() };
            match self.api.render_context_create_gl(mpv, &mut init) {
                Ok(rc) => {
                    self.render_ctx.store(rc, Ordering::SeqCst);
                    self.api.render_set_update_callback(rc, Some(render_wake), self.cb_ctx as *mut c_void);
                    true
                }
                Err(e) => {
                    bridge_log!("[bridge] render context failed: {}", self.api.error_string(e));
                    false
                }
            }
        }
    }

    fn schedule_render(&self) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if self.render_queued.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
            return;
        }
        let weak = self.weak.clone();
        self.dispatch_gl(move || {
            if let Some(r) = weak.upgrade() {
                r.render_queued.store(false, Ordering::SeqCst);
                if !r.flags.stopping.load(Ordering::SeqCst) {
                    r.render_frame();
                }
            }
        });
    }

    /// mpv has a new frame: draw it once into the shared texture, then present it in both views.
    fn render_frame(&self) {
        let rc = self.render_ctx.load(Ordering::SeqCst);
        if rc.is_null() {
            return;
        }
        let flags = self.api.render_update(rc);
        if flags & mpvapi::RENDER_UPDATE_FRAME != 0 {
            unsafe {
                shim_master_make_current(self.master);
                shim_master_lock(self.master);
                {
                    let mut gl = self.gl.lock().unwrap();
                    let cdg = self.flags.is_cdg.load(Ordering::SeqCst);
                    let (fbo, w, h) = if cdg { (gl.cdg_fbo, CDG_W, CDG_H) } else { (gl.fbo, TEXTURE_W, TEXTURE_H) };
                    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
                    glViewport(0, 0, w, h);
                    // Keep the producer/consumer ordering on the GPU: glFlush alone only submits this context's work and does not
                    // guarantee another shared context sees the finished texture (Apple Silicon intermittently presented the cleared
                    // CDG texture). glWaitSync in present() fixes that without the GUI-thread stall of glFinish.
                    self.api.render_into_fbo(rc, fbo as c_int, w, h);
                    glBindFramebuffer(GL_FRAMEBUFFER, 0);
                    if !gl.fence.is_null() {
                        glDeleteSync(gl.fence);
                    }
                    gl.fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
                    glFlush();
                }
                shim_master_unlock(self.master);
            }
            // A frame produced while a load is pending belongs to the OUTGOING song (loadfile is asynchronous and mpv keeps rendering
            // the previous file until the replacement takes over); counting it as "we have a frame" once let the previous singer's
            // video play on over the next singer's audio (2026-08-09). prepare_for_load cleared has_frame; keep it clear until the new
            // file actually produces a picture.
            if !self.flags.loading.load(Ordering::SeqCst) {
                self.flags.has_frame.store(true, Ordering::SeqCst);
            }
        }
        self.present(self.output.load(Ordering::SeqCst));
        self.present(self.preview.load(Ordering::SeqCst));
        // The rotation screen is an additional full-display consumer of the same retained texture: keep its underlay fluid without
        // making every 60/120 Hz source callback pay for another OpenGL flush (the main lyrics output stays uncapped).
        let due = {
            let mut last = self.last_rotation_present.lock().unwrap();
            let now = std::time::Instant::now();
            if last.map(|t| now.duration_since(t) >= std::time::Duration::from_millis(33)).unwrap_or(true) {
                *last = Some(now);
                true
            } else {
                false
            }
        };
        if due {
            self.present(self.rotation.load(Ordering::SeqCst));
        }
        self.present(self.spotlight.load(Ordering::SeqCst));
    }

    /// GUI-thread half of a load: clear both retained karaoke targets so a stopped song cannot flash during the next decoder's
    /// startup, and CDG tiles of the previous song cannot show through the next one's first packets.
    pub fn clear_for_load(&self) {
        self.on_gl_sync(|| self.clear_for_load_gl());
        self.needs_display_all();
    }

    fn clear_for_load_gl(&self) {
        unsafe {
            shim_master_make_current(self.master);
            shim_master_lock(self.master);
            {
                let gl = self.gl.lock().unwrap();
                glClearColor(0.0, 0.0, 0.0, 1.0);
                glBindFramebuffer(GL_FRAMEBUFFER, gl.cdg_fbo);
                glViewport(0, 0, CDG_W, CDG_H);
                glClear(GL_COLOR_BUFFER_BIT);
                glBindFramebuffer(GL_FRAMEBUFFER, gl.fbo);
                glViewport(0, 0, TEXTURE_W, TEXTURE_H);
                glClear(GL_COLOR_BUFFER_BIT);
                glBindFramebuffer(GL_FRAMEBUFFER, 0);
                glFlush();
            }
            shim_master_unlock(self.master);
        }
    }

    pub fn needs_display_all(&self) {
        for v in [self.output.load(Ordering::SeqCst), self.preview.load(Ordering::SeqCst), self.rotation.load(Ordering::SeqCst)] {
            if !v.is_null() {
                unsafe { shim_view_set_needs_display(v) };
            }
        }
    }

    /// Same, from any thread (AppKit wants the main thread).
    pub fn needs_display_async(&self) {
        let weak = self.weak.clone();
        dispatch_main(move || {
            if let Some(r) = weak.upgrade() {
                if !r.flags.stopping.load(Ordering::SeqCst) {
                    r.needs_display_all();
                }
            }
        });
    }

    pub fn set_sidefill(&self, mode: i32) {
        let m = mode.clamp(0, 2);
        self.sidefill.store(m, Ordering::SeqCst);
        bridge_log!("[bridge] cdg fill mode={} (0 off, 1 background colour, 2 blur)", m);
        let out = self.output.load(Ordering::SeqCst);
        if !out.is_null() {
            unsafe { shim_view_set_needs_display(out) };
        }
    }

    fn native_view_attached(&self, view: *mut c_void) {
        if view.is_null() || self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if unsafe { shim_view_attach(view) } == 0 {
            return;
        }
        self.refresh_view_cache(view);
        unsafe { shim_view_set_needs_display(view) };
        self.present(view);
        let name = if view == self.output.load(Ordering::SeqCst) { "output" } else { "preview" };
        bridge_log!("[bridge] {} drawable reattached to window", name);
    }

    /// Re-present the retained texture after Qt hid/showed or replaced a host window (no mpv state changes).
    pub fn refresh_views(&self, output_host: usize, preview_host: usize) {
        let out = self.output.load(Ordering::SeqCst);
        let prev = self.preview.load(Ordering::SeqCst);
        if output_host != 0 && unsafe { shim_view_refresh(out, output_host) } & 1 != 0 {
            bridge_log!("[bridge] output view reparented to current host");
        } else if output_host == 0 {
            unsafe { shim_view_refresh(out, 0) };
        }
        if preview_host != 0 && unsafe { shim_view_refresh(prev, preview_host) } & 1 != 0 {
            bridge_log!("[bridge] preview view reparented to current host");
        } else if preview_host == 0 {
            unsafe { shim_view_refresh(prev, 0) };
        }
        self.refresh_all_caches();
        self.needs_display_all();
        for v in [out, prev, self.rotation.load(Ordering::SeqCst), self.spotlight.load(Ordering::SeqCst)] {
            self.present(v);
        }
        let bits = |v: *mut c_void| (unsafe { shim_view_state(v, std::ptr::null_mut(), std::ptr::null_mut()) } & 4 != 0) as i32;
        bridge_log!("[bridge] retained views refresh requested output_window={} preview_window={}", bits(out), bits(prev));
    }

    /// Hold the output while Qt resizes/moves the audience window, then present at the settled geometry.
    pub fn begin_transition(&self, duration_ms: i32) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        let serial = self.transition_serial.fetch_add(1, Ordering::SeqCst) + 1;
        self.transitioning.store(true, Ordering::SeqCst);
        let settle = duration_ms.max(100) + 80;
        bridge_log!("[bridge] output transition hold serial={} duration={}ms", serial, settle);
        let weak = self.weak.clone();
        self.dispatch_gl_after(settle, move || {
            let Some(r) = weak.upgrade() else { return };
            if r.flags.stopping.load(Ordering::SeqCst) || r.transition_serial.load(Ordering::SeqCst) != serial {
                return;
            }
            r.transitioning.store(false, Ordering::SeqCst);
            // CDG may have no new graphics packet after the resize: present the latest texture at settled geometry.
            for v in [&r.output, &r.preview, &r.rotation, &r.spotlight] {
                r.present(v.load(Ordering::SeqCst));
            }
            r.needs_display_async();
            bridge_log!("[bridge] output transition released serial={}", serial);
        });
    }

    fn f(&self, a: &AtomicU64) -> f64 {
        f64::from_bits(a.load(Ordering::SeqCst))
    }

    fn schedule_bg_render(&self) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if self.bg_render_queued.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
            return;
        }
        let weak = self.weak.clone();
        self.dispatch_gl(move || {
            let Some(r) = weak.upgrade() else { return };
            r.bg_render_queued.store(false, Ordering::SeqCst);
            let rc = r.bg_render_ctx.load(Ordering::SeqCst);
            if r.flags.stopping.load(Ordering::SeqCst) || rc.is_null() {
                return;
            }
            let uflags = r.api.render_update(rc);
            if uflags & mpvapi::RENDER_UPDATE_FRAME != 0 {
                unsafe {
                    shim_master_make_current(r.master);
                    shim_master_lock(r.master);
                    {
                        let mut gl = r.gl.lock().unwrap();
                        glBindFramebuffer(GL_FRAMEBUFFER, gl.bg_fbo);
                        glViewport(0, 0, TEXTURE_W, TEXTURE_H);
                        r.api.render_into_fbo(rc, gl.bg_fbo as c_int, TEXTURE_W, TEXTURE_H);
                        glBindFramebuffer(GL_FRAMEBUFFER, 0);
                        if !gl.bg_fence.is_null() {
                            glDeleteSync(gl.bg_fence);
                        }
                        gl.bg_fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
                        glFlush();
                    }
                    shim_master_unlock(r.master);
                }
                r.bg_has_frame.store(true, Ordering::SeqCst);
            }
            // The decorative decoder has its own continuous clock: present every background frame to BOTH surfaces here, otherwise
            // the host preview only updated when a lyric packet changed and its MP4 looked paused through static CDG spans.
            for v in [&r.output, &r.preview, &r.rotation, &r.spotlight] {
                r.present(v.load(Ordering::SeqCst));
            }
        });
    }

    fn schedule_bg_events(&self) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if self.bg_event_queued.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst).is_err() {
            return;
        }
        let weak = self.weak.clone();
        self.dispatch_gl(move || {
            let Some(r) = weak.upgrade() else { return };
            r.bg_event_queued.store(false, Ordering::SeqCst);
            let Some(h) = *r.bg_core.lock().unwrap() else { return };
            // (the handle is published right after mpv_initialize, so a wake that arrives earlier cannot belong to a live core)
            loop {
                let e = r.api.wait_event(h, 0.0);
                if e.is_null() {
                    break;
                }
                let ev = unsafe { &*e };
                match ev.event_id {
                    mpvapi::EVENT_NONE => break,
                    mpvapi::EVENT_PROPERTY_CHANGE => {
                        let p = ev.data as *const mpvapi::MpvEventProperty;
                        if !p.is_null() && unsafe { !(*p).data.is_null() } {
                            let (format, data) = unsafe { ((*p).format, (*p).data) };
                            if ev.reply_userdata == 101 && format == mpvapi::FORMAT_DOUBLE {
                                let secs = unsafe { *(data as *const f64) };
                                r.bg_position_ms.store((secs * 1000.0).round() as i64, Ordering::SeqCst);
                            } else if ev.reply_userdata == 102 && format == mpvapi::FORMAT_FLAG {
                                r.bg_paused.store(unsafe { *(data as *const c_int) } != 0, Ordering::SeqCst);
                            } else if ev.reply_userdata == 103 && format == mpvapi::FORMAT_FLAG {
                                r.bg_at_end.store(unsafe { *(data as *const c_int) } != 0, Ordering::SeqCst);
                            }
                        }
                    }
                    mpvapi::EVENT_FILE_LOADED => bridge_log!("[background] native animation loaded"),
                    mpvapi::EVENT_END_FILE => {
                        r.bg_at_end.store(true, Ordering::SeqCst);
                        bridge_log!("[background] native animation ended");
                    }
                    _ => {}
                }
            }
        });
    }

    /// Starts (or replaces) the muted background animation. The outgoing frame is retained for a true dissolve without a second decoder.
    pub fn load_background(&self, path: &str, opacity: f64) -> bool {
        if path.is_empty() || self.flags.stopping.load(Ordering::SeqCst) {
            return false;
        }
        if self.bg_has_frame.load(Ordering::SeqCst) && self.f(&self.bg_opacity) > 0.0 {
            self.on_gl_sync(|| unsafe {
                shim_master_make_current(self.master);
                shim_master_lock(self.master);
                {
                    let mut gl = self.gl.lock().unwrap();
                    glBindFramebuffer(GL_READ_FRAMEBUFFER, gl.bg_fbo);
                    glActiveTexture(GL_TEXTURE2);
                    glBindTexture(GL_TEXTURE_2D, gl.prev_bg_tex);
                    glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, TEXTURE_W, TEXTURE_H);
                    glBindTexture(GL_TEXTURE_2D, 0);
                    glActiveTexture(GL_TEXTURE0);
                    glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
                    if !gl.bg_fence.is_null() {
                        glDeleteSync(gl.bg_fence);
                    }
                    gl.bg_fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0);
                    glFlush();
                }
                shim_master_unlock(self.master);
            });
            self.prev_bg_has_frame.store(true, Ordering::SeqCst);
            self.prev_bg_opacity.store(self.bg_opacity.load(Ordering::SeqCst), Ordering::SeqCst);
            self.bg_mix.store(0f64.to_bits(), Ordering::SeqCst);
            bridge_log!("[background] outgoing frame retained for single-decoder crossfade");
        } else {
            self.prev_bg_has_frame.store(false, Ordering::SeqCst);
            self.prev_bg_opacity.store(0f64.to_bits(), Ordering::SeqCst);
            self.bg_mix.store(1f64.to_bits(), Ordering::SeqCst);
        }
        self.bg_opacity.store(opacity.clamp(0.0, 1.0).to_bits(), Ordering::SeqCst);
        self.bg_has_frame.store(false, Ordering::SeqCst);
        self.bg_at_end.store(false, Ordering::SeqCst);
        self.bg_position_ms.store(0, Ordering::SeqCst);
        self.bg_paused.store(false, Ordering::SeqCst);
        // Never hold the core mutex while creating the core: creation syncs onto the render queue, whose background-event handler takes
        // the same mutex (a lock-order inversion that hung the render-thread mode).
        let existing = *self.bg_core.lock().unwrap();
        let h = match existing {
            Some(h) => h,
            None => match self.create_bg_core() {
                Some(h) => {
                    *self.bg_core.lock().unwrap() = Some(h);
                    h
                }
                None => return false,
            },
        };
        // An EOF may leave pause asserted: every replacement animation must explicitly resume, without touching the karaoke core.
        self.api.set_flag_async(h, 91, "pause", false);
        let r = self.api.command_async(h, 90, &["loadfile", path, "replace"]);
        bridge_log!("[background] native animation queued file={} opacity={:.2}", path, self.f(&self.bg_opacity));
        r >= 0
    }

    fn create_bg_core(&self) -> Option<Handle> {
        let h = self.api.create()?;
        for (k, v) in [
            ("config", "no"),
            ("vo", "libmpv"),
            ("ao", "null"),
            ("mute", "yes"),
            // Decorative MP4s fill the 16:9 shared texture (their aspect may distort, but no edge is cropped or letterboxed).
            ("video-aspect-override", "16:9"),
            // Keep an embedded audio stream as mpv's timing master, routed to the null device and muted: disabling audio entirely
            // left some short AAC-backed VJ loops parked on their first rendered frame.
            // Software decode: VideoToolbox cannot export several older H.264 VJ-loop formats into this secondary render context on
            // Intel (hwaccel setup fails before a frame); these clips are decorative and muted, reliable beats fast.
            ("hwdec", "no"),
            ("pause", "no"),
            // keep-open would park a finished loop on its last frame with pause=yes; END_FILE / eof-reached must stay observable so
            // Python's shuffle bag can advance, while idle keeps this core alive.
            ("keep-open", "no"),
            ("idle", "yes"),
        ] {
            self.api.set_option(h, k, v);
        }
        self.api.set_wakeup_callback(h, bg_event_wake, self.cb_ctx as *mut c_void);
        if self.api.initialize(h) < 0 {
            self.api.terminate_destroy(h);
            return None;
        }
        // Publish the handle BEFORE anything can produce an event: mpv only calls the wakeup callback again once the app has drained its
        // queue, and the event job (which runs on the render queue, possibly while this function is still running) must find the core. An
        // earlier version stored the handle after this returned, so the first job found none, returned without draining, and mpv never
        // woke us again: position stuck at 0, "ended" never seen, the app never advanced to the next background file.
        *self.bg_core.lock().unwrap() = Some(h);
        self.api.observe(h, 101, "time-pos", mpvapi::FORMAT_DOUBLE);
        self.api.observe(h, 102, "pause", mpvapi::FORMAT_FLAG);
        self.api.observe(h, 103, "eof-reached", mpvapi::FORMAT_FLAG);
        let mut ok = false;
        self.on_gl_sync(|| unsafe {
            shim_master_make_current(self.master);
            let mut init = OpenglInitParams { get_proc_address: get_proc, get_proc_address_ctx: std::ptr::null_mut() };
            if let Ok(rc) = self.api.render_context_create_gl(h, &mut init) {
                self.bg_render_ctx.store(rc, Ordering::SeqCst);
                self.api.render_set_update_callback(rc, Some(bg_render_wake), self.cb_ctx as *mut c_void);
                ok = true;
            }
        });
        if !ok {
            *self.bg_core.lock().unwrap() = None;
            self.api.terminate_destroy(h);
            return None;
        }
        Some(h)
    }

    pub fn stop_background(&self) {
        self.bg_opacity.store(0f64.to_bits(), Ordering::SeqCst);
        self.bg_has_frame.store(false, Ordering::SeqCst);
        self.bg_at_end.store(false, Ordering::SeqCst);
        self.bg_position_ms.store(0, Ordering::SeqCst);
        self.bg_paused.store(false, Ordering::SeqCst);
        self.prev_bg_has_frame.store(false, Ordering::SeqCst);
        self.prev_bg_opacity.store(0f64.to_bits(), Ordering::SeqCst);
        self.bg_mix.store(1f64.to_bits(), Ordering::SeqCst);
        if let Some(h) = *self.bg_core.lock().unwrap() {
            self.api.command_async(h, 92, &["stop"]);
        }
        let out = self.output.load(Ordering::SeqCst);
        if !out.is_null() {
            unsafe { shim_view_set_needs_display(out) };
        }
    }

    /// Fade the animation (and drive the dissolve from the outgoing frame while one is retained).
    pub fn set_background_opacity(&self, opacity: f64) {
        let value = opacity.clamp(0.0, 1.0);
        self.bg_opacity.store(value.to_bits(), Ordering::SeqCst);
        if self.prev_bg_has_frame.load(Ordering::SeqCst) {
            let old = self.f(&self.prev_bg_opacity).max(0.001);
            let mix = (value / old).clamp(0.0, 1.0);
            self.bg_mix.store(mix.to_bits(), Ordering::SeqCst);
            if mix >= 0.999 {
                self.prev_bg_has_frame.store(false, Ordering::SeqCst);
                self.prev_bg_opacity.store(0f64.to_bits(), Ordering::SeqCst);
                self.bg_mix.store(1f64.to_bits(), Ordering::SeqCst);
            }
        }
        self.needs_display_all();
    }

    // UI timers read observed state: querying the decoder core synchronously could block the GUI while an animation opens or ends.
    pub fn background_at_end(&self) -> bool {
        self.bg_at_end.load(Ordering::SeqCst)
    }
    pub fn background_position_ms(&self) -> i64 {
        self.bg_position_ms.load(Ordering::SeqCst)
    }
    pub fn background_paused(&self) -> bool {
        self.bg_paused.load(Ordering::SeqCst)
    }

    /// The CDG picture as BGRA, top row first, read from the retained 300x216 texture (libmpv's screenshot-raw of a CDG FBO is black).
    pub fn grab_cdg(&self) -> Option<Vec<u8>> {
        let mut out = None;
        self.on_gl_sync(|| out = self.grab_cdg_gl());
        out
    }

    fn grab_cdg_gl(&self) -> Option<Vec<u8>> {
        let (w, h) = (CDG_W as usize, CDG_H as usize);
        let stride = w * 4;
        let mut out = vec![0u8; stride * h];
        unsafe {
            shim_master_make_current(self.master);
            shim_master_lock(self.master);
            {
                let gl = self.gl.lock().unwrap();
                glBindFramebuffer(GL_READ_FRAMEBUFFER, gl.cdg_fbo);
                glPixelStorei(GL_PACK_ALIGNMENT, 4);
                glReadPixels(0, 0, CDG_W, CDG_H, GL_BGRA, GL_UNSIGNED_BYTE, out.as_mut_ptr() as *mut c_void);
                glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
            }
            shim_master_unlock(self.master);
        }
        // OpenGL rows start at the lower-left; images start at the top.
        for y in 0..h / 2 {
            let (a, b) = out.split_at_mut((h - 1 - y) * stride);
            a[y * stride..(y + 1) * stride].swap_with_slice(&mut b[..stride]);
        }
        Some(out)
    }

    // ---- threading helpers -------------------------------------------------------------------------------------------------------

    /// Run GL work where it belongs: the private render queue in render-thread mode, otherwise the main queue (the old behaviour).
    fn dispatch_gl(&self, f: impl FnOnce() + Send + 'static) {
        if self.gl_queue.is_null() {
            dispatch_main(f)
        } else {
            dispatch_queue(self.gl_queue, f)
        }
    }
    fn dispatch_gl_after(&self, ms: i32, f: impl FnOnce() + Send + 'static) {
        if self.gl_queue.is_null() {
            dispatch_main_after(ms, f)
        } else {
            dispatch_queue_after(self.gl_queue, ms, f)
        }
    }
    /// Inline when render-thread mode is off or already on the render queue; otherwise synchronously on that queue. Never called FROM the
    /// render queue towards the main thread, so the two cannot deadlock.
    fn on_gl_sync<F: FnOnce()>(&self, f: F) {
        if self.gl_queue.is_null() || unsafe { shim_queue_is_current(self.gl_queue) } != 0 {
            f();
            return;
        }
        extern "C" fn tramp<F: FnOnce()>(ctx: *mut c_void) {
            let slot = unsafe { &mut *(ctx as *mut Option<F>) };
            if let Some(f) = slot.take() {
                f();
            }
        }
        let mut slot = Some(f);
        unsafe { shim_queue_sync(self.gl_queue, tramp::<F>, &mut slot as *mut Option<F> as *mut c_void) };
    }

    pub fn render_thread_active(&self) -> bool {
        !self.gl_queue.is_null()
    }

    fn kind_of(&self, view: *mut c_void) -> Option<usize> {
        if view.is_null() {
            return None;
        }
        [&self.output, &self.preview, &self.rotation, &self.spotlight].iter().position(|a| a.load(Ordering::SeqCst) == view)
    }

    /// Main thread only: capture what the render thread needs to know about a view (drawable? how big?).
    fn refresh_view_cache(&self, view: *mut c_void) {
        let Some(kind) = self.kind_of(view) else { return };
        let (mut w, mut h) = (1, 1);
        let ok = unsafe { shim_view_probe(view, &mut w, &mut h) } != 0;
        let c = &self.cache[kind];
        if w != c.w.load(Ordering::SeqCst) || h != c.h.load(Ordering::SeqCst) {
            c.w.store(w, Ordering::SeqCst);
            c.h.store(h, Ordering::SeqCst);
            // The drawable changed size: tell the context, serialized with the render thread's drawing.
            unsafe { shim_view_update_locked(view) };
        }
        c.presentable.store(ok, Ordering::SeqCst);
    }
    fn refresh_all_caches(&self) {
        for a in [&self.output, &self.preview, &self.rotation, &self.spotlight] {
            let v = a.load(Ordering::SeqCst);
            if !v.is_null() {
                self.refresh_view_cache(v);
            }
        }
    }

    /// Entry point for every present. Main-thread mode: draw now on the calling thread, as before. Render-thread mode: hand the draw to the
    /// render queue (or draw inline when already there), so AppKit callbacks and main-thread events never draw themselves.
    fn present(&self, view: *mut c_void) {
        if view.is_null() || self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if self.gl_queue.is_null() || unsafe { shim_queue_is_current(self.gl_queue) } != 0 {
            self.present_now(view);
            return;
        }
        let (weak, v) = (self.weak.clone(), view as usize);
        dispatch_queue(self.gl_queue, move || {
            if let Some(r) = weak.upgrade() {
                if !r.flags.stopping.load(Ordering::SeqCst) {
                    r.present_now(v as *mut c_void);
                }
            }
        });
    }

    /// Draw the retained texture into one view.
    fn present_now(&self, view: *mut c_void) {
        // A view replaced or destroyed while this draw was queued is no longer one of ours: skip it.
        let Some(kind) = self.kind_of(view) else { return };
        let render_mode = !self.gl_queue.is_null();
        let is_output = kind == KIND_OUTPUT;
        let has_frame = self.flags.has_frame.load(Ordering::SeqCst);
        // Why a present was skipped is the whole question when one window goes blank while the other keeps drawing, so name the
        // reason (one log line per view per reason change: this runs at frame rate). Render-thread mode must not read AppKit state
        // (hidden / window / visible are main-thread questions): it trusts the cache the main thread keeps.
        let state = if render_mode {
            if is_output && self.transitioning.load(Ordering::SeqCst) {
                2
            } else if !self.cache[kind].presentable.load(Ordering::SeqCst) {
                3
            } else if !has_frame {
                6
            } else {
                0
            }
        } else {
            let bits = unsafe { shim_view_state(view, std::ptr::null_mut(), std::ptr::null_mut()) };
            if bits & 1 == 0 {
                1
            } else if is_output && self.transitioning.load(Ordering::SeqCst) {
                2
            } else if bits & 2 != 0 {
                3
            } else if bits & 4 == 0 {
                4
            } else if bits & 8 == 0 {
                5
            } else if !has_frame {
                6
            } else {
                0
            }
        };
        if self.last_skip[kind].swap(state, Ordering::SeqCst) != state {
            const WHY: [&str; 7] = ["presenting", "no-gl-context", "window-transition", "view-hidden", "no-window", "window-not-visible", "no-frame (black)"];
            bridge_log!("[bridge] {} view {}", KIND_NAMES[kind], WHY[state as usize]);
        }
        if state != 0 && state != 6 {
            return;
        }
        unsafe {
            shim_view_make_current(view);
            if !render_mode {
                shim_view_update(view);
            }
            shim_view_lock(view);
            let (mut w, mut h) = (1, 1);
            if render_mode {
                w = self.cache[kind].w.load(Ordering::SeqCst).max(1);
                h = self.cache[kind].h.load(Ordering::SeqCst).max(1);
            } else {
                shim_view_state(view, &mut w, &mut h);
            }
            {
                let gl = &mut *self.gl.lock().unwrap();
                // The fence was inserted by the master producer context: queue a server-side wait in this consumer context.
                if !gl.fence.is_null() {
                    glWaitSync(gl.fence, 0, GL_TIMEOUT_IGNORED);
                }
                if !gl.bg_fence.is_null() {
                    glWaitSync(gl.bg_fence, 0, GL_TIMEOUT_IGNORED);
                }
                glBindFramebuffer(GL_FRAMEBUFFER, 0);
                glViewport(0, 0, w, h);
                glClearColor(0.0, 0.0, 0.0, 1.0);
                glClear(GL_COLOR_BUFFER_BIT);
                if has_frame {
                    self.draw_picture(gl, kind, w, h);
                }
            }
            shim_view_flush(view);
            shim_view_unlock(view);
        }
        self.present_count.fetch_add(1, Ordering::SeqCst);
    }

    /// The geometry and passes of `presentViewNow` for the output and preview views.
    unsafe fn draw_picture(&self, gl: &mut GlState, kind: usize, w: i32, h: i32) {
        let is_cdg = self.flags.is_cdg.load(Ordering::SeqCst);
        let frame_texture = if is_cdg { gl.cdg_tex } else { gl.tex };
        let sa = if is_cdg { CDG_W as f64 / CDG_H as f64 } else { TEXTURE_W as f64 / TEXTURE_H as f64 };
        let va = w as f64 / h as f64;
        let (mut sx, mut sy) = (1.0f32, 1.0f32);
        if va > sa {
            sx = (sa / va) as f32;
        } else {
            sy = (va / sa) as f32;
        }
        // MP4 karaoke deliberately fills every display edge (the core already stretched it into the shared 16:9 texture).
        // (The rotation and spotlight underlays keep the picture's aspect instead.)
        if !is_cdg && kind <= KIND_PREVIEW {
            sx = 1.0;
            sy = 1.0;
        }
        let (mut uvx, mut uvy, mut uox, mut uoy) = (1.0f32, 1.0f32, 0.0f32, 0.0f32);
        // Where libmpv actually pillarboxed the picture inside the shared 16:9 texture (measured from dwidth/dheight at FILE_LOADED).
        let span = if is_cdg { 1.0 } else { f64::from_bits(self.flags.span_x.load(Ordering::SeqCst)).clamp(0.05, 1.0) };
        let panel_l = ((1.0 - span) / 2.0) as f32;
        let panel_r = panel_l + span as f32;
        if kind == KIND_PREVIEW && is_cdg {
            // Discard the generated pillar bars, then letterbox what is left against the host's real aspect.
            uvx = span as f32;
            uox = panel_l;
            let ca = sa * span;
            if va > ca {
                sx = (ca / va) as f32;
            } else {
                sy = (va / ca) as f32;
            }
        }
        // Decorative fills belong to the main audience screen only.
        let mut sidefill = if kind == KIND_OUTPUT && is_cdg { self.sidefill.load(Ordering::SeqCst) } else { 0 };
        glUseProgram(gl.program);
        let vao = &mut gl.vao[kind];
        if *vao == 0 {
            glGenVertexArrays(1, vao);
        }
        glBindVertexArray(*vao);
        let u = &gl.u;
        glUniform2f(u.panel, panel_l, panel_r);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, frame_texture);
        glUniform1i(u.texture, 0);
        // Each CDG foreground pass leaves this texture in nearest mode: reset it before any decorative sampling, which must stay smooth.
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glActiveTexture(GL_TEXTURE1);
        glBindTexture(GL_TEXTURE_2D, gl.bg_tex);
        glUniform1i(u.bg_texture, 1);
        let (bg_opacity, prev_opacity, mix) = (self.f(&self.bg_opacity), self.f(&self.prev_bg_opacity), self.f(&self.bg_mix));
        let (bg_has, prev_has) = (self.bg_has_frame.load(Ordering::SeqCst), self.prev_bg_has_frame.load(Ordering::SeqCst));
        glUniform1f(u.bg_opacity, bg_opacity as f32);
        glActiveTexture(GL_TEXTURE2);
        glBindTexture(GL_TEXTURE_2D, gl.prev_bg_tex);
        glUniform1i(u.prev_bg_texture, 2);
        glUniform1f(u.prev_bg_opacity, if prev_has { prev_opacity as f32 } else { 0.0 });
        glUniform1f(u.bg_mix, mix as f32);
        // Both surfaces show what the room sees: the preview must represent the animation composite too.
        let background_active = kind <= KIND_PREVIEW && is_cdg && ((bg_has && bg_opacity > 0.0) || (prev_has && prev_opacity > 0.0));
        if background_active {
            // One full-texture GPU draw: stretch to every host edge without a crop, intermediate pixmap, decoder change or clock cost.
            glUniform2f(u.scale, 1.0, 1.0);
            glUniform2f(u.uv_scale, 1.0, 1.0);
            glUniform2f(u.uv_offset, 0.0, 0.0);
            glUniform1i(u.sidefill, 4);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
            uvx = span as f32;
            uox = panel_l;
            uvy = 1.0;
            uoy = 0.0;
            sx = 1.0;
            sy = 1.0;
            let ca = sa * span;
            if va > ca {
                sx = (ca / va) as f32;
            } else {
                sy = (va / ca) as f32;
            }
            glEnable(GL_BLEND);
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
            sidefill = 0;
        }
        else if sidefill != 0 {
            // Background pass: cover the actual host, not the bridge's fixed 16:9 texture. Crop only this decorative copy.
            let ca = sa * span;
            let (mut buvx, mut buvy, mut buox, mut buoy) = (span as f32, 1.0f32, panel_l, 0.0f32);
            if va > ca {
                buvy = (ca / va) as f32;
                buoy = (1.0 - buvy) * 0.5;
            } else {
                buvx = (span * (va / ca)) as f32;
                buox = panel_l + (span as f32 - buvx) * 0.5;
            }
            glUniform2f(u.scale, 1.0, 1.0);
            glUniform2f(u.uv_scale, buvx, buvy);
            glUniform2f(u.uv_offset, buox, buoy);
            glUniform1i(u.sidefill, sidefill);
            glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
            // Foreground pass: fit the complete native-aspect CDG picture over the full-bleed background.
            uvx = span as f32;
            uox = panel_l;
            uvy = 1.0;
            uoy = 0.0;
            sx = 1.0;
            sy = 1.0;
            if va > ca {
                sx = (ca / va) as f32;
            } else {
                sy = (va / ca) as f32;
            }
            glEnable(GL_BLEND);
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
            glUniform1i(u.sidefill, 3);
        }
        glUniform2f(u.scale, sx, sy);
        glUniform2f(u.uv_scale, uvx, uvy);
        glUniform2f(u.uv_offset, uox, uoy);
        glUniform1i(u.sidefill, if background_active { 5 } else { 0 });
        if sidefill != 0 && !background_active {
            glUniform1i(u.sidefill, 3);
        }
        // CDG is 300x216 palette artwork: a second bilinear resize blends lyric/outline pixels, so the final CDG layer samples nearest.
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, frame_texture);
        let filter = if is_cdg { GL_NEAREST } else { GL_LINEAR };
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        if sidefill != 0 || background_active {
            glDisable(GL_BLEND);
        }
        glActiveTexture(GL_TEXTURE2);
        glBindTexture(GL_TEXTURE_2D, 0);
        glActiveTexture(GL_TEXTURE1);
        glBindTexture(GL_TEXTURE_2D, 0);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, 0);
        glBindVertexArray(0);
        glUseProgram(0);
    }

    /// Step 1 of shutdown (before the mpv core is destroyed): stop callbacks, free the render context and the fence.
    pub fn free_render_context(&self) {
        self.on_gl_sync(|| self.free_render_context_gl());
    }

    fn free_render_context_gl(&self) {
        let rc = self.render_ctx.swap(std::ptr::null_mut(), Ordering::SeqCst);
        unsafe {
            if !rc.is_null() {
                self.api.render_set_update_callback(rc, None, std::ptr::null_mut());
            }
            shim_master_make_current(self.master);
            if !rc.is_null() {
                self.api.render_free(rc);
            }
            let brc = self.bg_render_ctx.swap(std::ptr::null_mut(), Ordering::SeqCst);
            if !brc.is_null() {
                self.api.render_set_update_callback(brc, None, std::ptr::null_mut());
                self.api.render_free(brc);
            }
            if let Some(h) = self.bg_core.lock().unwrap().take() {
                self.api.set_wakeup_callback_none(h);
                self.api.terminate_destroy(h);
            }
            let gl = &mut *self.gl.lock().unwrap();
            if !gl.fence.is_null() {
                glDeleteSync(gl.fence);
                gl.fence = std::ptr::null_mut();
            }
            if !gl.bg_fence.is_null() {
                glDeleteSync(gl.bg_fence);
                gl.bg_fence = std::ptr::null_mut();
            }
        }
    }

    /// Step 2 of shutdown (after the mpv core is destroyed): stop the cache timer and remove the views.
    pub fn destroy_views(&self) {
        let t = self.cache_timer.swap(std::ptr::null_mut(), Ordering::SeqCst);
        if !t.is_null() {
            unsafe { shim_timer_stop(t) };
        }
        for slot in [&self.output, &self.preview, &self.rotation, &self.spotlight] {
            self.destroy_view_slot(slot);
        }
    }

    /// Detach a view from its slot, wait out any draw already queued for it on the render queue, then destroy it.
    fn destroy_view_slot(&self, slot: &AtomicPtr<c_void>) {
        let v = slot.swap(std::ptr::null_mut(), Ordering::SeqCst);
        if !v.is_null() {
            self.on_gl_sync(|| {});
            unsafe { shim_view_destroy(v) };
        }
    }

    // ---- rotation / spotlight underlays (V5) -----------------------------------------------------------------------------------------

    /// The rotation screen's picture: an extra consumer of the same retained texture, directly behind Qt's translucent content view.
    pub fn set_rotation_host(&self, host: usize, enabled: bool) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        if host == 0 {
            let v = self.rotation.load(Ordering::SeqCst);
            if !v.is_null() {
                unsafe { shim_view_set_hidden(v, 1) };
            }
            return;
        }
        let current = self.rotation.load(Ordering::SeqCst);
        if current.is_null() || self.rotation_host.load(Ordering::SeqCst) != host {
            if !current.is_null() {
                self.destroy_view_slot(&self.rotation);
            }
            let created = unsafe { shim_view_create_behind(self.master, host, Some(view_event), self.cb_ctx as *mut c_void) };
            self.rotation.store(created, Ordering::SeqCst);
            if !created.is_null() && !self.gl_queue.is_null() {
                unsafe { shim_view_set_lock_update(created, 1) };
            }
        }
        let v = self.rotation.load(Ordering::SeqCst);
        if !self.gl_queue.is_null() {
            self.refresh_view_cache(v);
        }
        self.rotation_host.store(host, Ordering::SeqCst);
        if v.is_null() {
            return;
        }
        unsafe { shim_view_set_hidden(v, !enabled as c_int) };
        if enabled {
            *self.last_rotation_present.lock().unwrap() = None;
            unsafe { shim_view_set_needs_display(v) };
            self.present(v);
            self.present(self.spotlight.load(Ordering::SeqCst));
        }
    }

    /// The between-singers spotlight: a normal child view above the picture, faded by AppKit (Python only schedules the interval).
    pub fn set_spotlight_host(&self, host: usize, enabled: bool) {
        if self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        let revision = self.spotlight_revision.fetch_add(1, Ordering::SeqCst) + 1;
        if host == 0 {
            let v = self.spotlight.load(Ordering::SeqCst);
            if !v.is_null() {
                unsafe { shim_view_set_hidden(v, 1) };
            }
            return;
        }
        if self.spotlight.load(Ordering::SeqCst).is_null() || self.spotlight_host.load(Ordering::SeqCst) != host {
            self.destroy_view_slot(&self.spotlight);
            let created = unsafe { shim_view_create(self.master, host, Some(view_event), self.cb_ctx as *mut c_void) };
            self.spotlight.store(created, Ordering::SeqCst);
            if !created.is_null() {
                unsafe {
                    if !self.gl_queue.is_null() {
                        shim_view_set_lock_update(created, 1);
                    }
                    shim_view_set_ignores_mouse(created, 1);
                    shim_view_set_alpha(created, 0.0);
                }
                if !self.gl_queue.is_null() {
                    self.refresh_view_cache(created);
                }
            }
            self.spotlight_host.store(host, Ordering::SeqCst);
        }
        let v = self.spotlight.load(Ordering::SeqCst);
        if v.is_null() {
            return;
        }
        unsafe {
            if enabled {
                if shim_view_is_hidden(v) != 0 {
                    shim_view_set_alpha(v, 0.0);
                }
                shim_view_set_hidden(v, 0);
                self.present(v);
            }
            // AppKit owns this one opacity transition.
            let weak = self.weak.clone();
            let done: Box<Box<dyn FnOnce() + Send>> = Box::new(Box::new(move || {
                if let Some(r) = weak.upgrade() {
                    let cur = r.spotlight.load(Ordering::SeqCst);
                    if !enabled && !cur.is_null() && r.spotlight_revision.load(Ordering::SeqCst) == revision {
                        shim_view_set_hidden(cur, 1);
                    }
                }
            }));
            shim_view_animate_alpha(v, if enabled { 1.0 } else { 0.0 }, 0.35, run_boxed, Box::into_raw(done) as *mut c_void);
        }
    }
}

impl Drop for Renderer {
    fn drop(&mut self) {
        unsafe {
            if !self.gl_queue.is_null() {
                shim_queue_release(self.gl_queue);
            }
            shim_master_destroy(self.master);
            drop(Box::from_raw(self.cb_ctx));
        }
    }
}
