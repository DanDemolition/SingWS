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
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicPtr, AtomicU64, Ordering};
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
    prev_bg_tex: GLuint,
    program: GLuint,
    u: Uniforms,
    vao_out: GLuint,
    vao_prev: GLuint,
    fence: GLsync,
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
    render_queued: AtomicBool,
    present_count: AtomicU64,
    sidefill: AtomicI32,
    transitioning: AtomicBool,
    transition_serial: AtomicU64,
    last_skip: [AtomicI32; 2],
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

unsafe extern "C" fn view_event(user: *mut c_void, view: *mut c_void, kind: c_int) {
    let Some(r) = (*(user as *const Weak<Renderer>)).upgrade() else { return };
    match kind {
        0 => r.present(view),
        2 => r.native_view_attached(view),
        _ => {} // geometry changes only matter in render-thread mode (V5)
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
            let (bg_tex, _bg_fbo, c) = make_texture(TEXTURE_W, TEXTURE_H, GL_LINEAR, true);
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
                GlState { tex, fbo, cdg_tex, cdg_fbo, bg_tex, prev_bg_tex, program, u, vao_out: 0, vao_prev: 0, fence: std::ptr::null_mut() },
                a && b && c && program != 0,
            )
        };
        if !ok {
            bridge_log!("[bridge] OpenGL setup failed (framebuffer or shader)");
            unsafe { shim_master_destroy(master) };
            return None;
        }
        let renderer = Arc::new_cyclic(|weak: &Weak<Renderer>| Renderer {
            api,
            flags,
            master,
            gl: Mutex::new(gl),
            render_ctx: AtomicPtr::new(std::ptr::null_mut()),
            output: AtomicPtr::new(std::ptr::null_mut()),
            preview: AtomicPtr::new(std::ptr::null_mut()),
            render_queued: AtomicBool::new(false),
            present_count: AtomicU64::new(0),
            sidefill: AtomicI32::new(0),
            transitioning: AtomicBool::new(false),
            transition_serial: AtomicU64::new(0),
            last_skip: [AtomicI32::new(-1), AtomicI32::new(-1)],
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
        bridge_log!("[bridge] render thread mode=off (main thread)");
        Some(renderer)
    }

    pub fn present_count(&self) -> u64 {
        self.present_count.load(Ordering::SeqCst)
    }

    /// Called from the core once mpv is initialised (vo=libmpv). The master GL context must be current on this thread.
    pub fn create_render_context(&self, mpv: Handle) -> bool {
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
        dispatch_main(move || {
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
    }

    /// GUI-thread half of a load: clear both retained karaoke targets so a stopped song cannot flash during the next decoder's
    /// startup, and CDG tiles of the previous song cannot show through the next one's first packets.
    pub fn clear_for_load(&self) {
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
        self.needs_display_all();
    }

    pub fn needs_display_all(&self) {
        for v in [self.output.load(Ordering::SeqCst), self.preview.load(Ordering::SeqCst)] {
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
        self.needs_display_all();
        self.present(out);
        self.present(prev);
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
        dispatch_main_after(settle, move || {
            let Some(r) = weak.upgrade() else { return };
            if r.flags.stopping.load(Ordering::SeqCst) || r.transition_serial.load(Ordering::SeqCst) != serial {
                return;
            }
            r.transitioning.store(false, Ordering::SeqCst);
            // CDG may have no new graphics packet after the resize: present the latest texture at settled geometry.
            r.present(r.output.load(Ordering::SeqCst));
            r.present(r.preview.load(Ordering::SeqCst));
            let out = r.output.load(Ordering::SeqCst);
            if !out.is_null() {
                unsafe { shim_view_set_needs_display(out) };
            }
            bridge_log!("[bridge] output transition released serial={}", serial);
        });
    }

    /// Draw the retained texture into one view.
    fn present(&self, view: *mut c_void) {
        if view.is_null() || self.flags.stopping.load(Ordering::SeqCst) {
            return;
        }
        let is_output = view == self.output.load(Ordering::SeqCst);
        let which = if is_output { 0 } else { 1 };
        let has_frame = self.flags.has_frame.load(Ordering::SeqCst);
        let bits = unsafe { shim_view_state(view, std::ptr::null_mut(), std::ptr::null_mut()) };
        // Why a present was skipped is the whole question when one window goes blank while the other keeps drawing, so name the
        // reason (one log line per view per reason change: this runs at frame rate).
        let state = if bits & 1 == 0 {
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
        };
        if self.last_skip[which].swap(state, Ordering::SeqCst) != state {
            const WHY: [&str; 7] = ["presenting", "no-gl-context", "window-transition", "view-hidden", "no-window", "window-not-visible", "no-frame (black)"];
            bridge_log!("[bridge] {} view {}", if is_output { "output" } else { "preview" }, WHY[state as usize]);
        }
        if state != 0 && state != 6 {
            return;
        }
        unsafe {
            shim_view_make_current(view);
            shim_view_update(view);
            shim_view_lock(view);
            let (mut w, mut h) = (1, 1);
            shim_view_state(view, &mut w, &mut h);
            {
                let gl = &mut *self.gl.lock().unwrap();
                // The fence was inserted by the master producer context: queue a server-side wait in this consumer context.
                if !gl.fence.is_null() {
                    glWaitSync(gl.fence, 0, GL_TIMEOUT_IGNORED);
                }
                glBindFramebuffer(GL_FRAMEBUFFER, 0);
                glViewport(0, 0, w, h);
                glClearColor(0.0, 0.0, 0.0, 1.0);
                glClear(GL_COLOR_BUFFER_BIT);
                if has_frame {
                    self.draw_picture(gl, is_output, w, h);
                }
            }
            shim_view_flush(view);
            shim_view_unlock(view);
        }
        self.present_count.fetch_add(1, Ordering::SeqCst);
    }

    /// The geometry and passes of `presentViewNow` for the output and preview views.
    unsafe fn draw_picture(&self, gl: &mut GlState, is_output: bool, w: i32, h: i32) {
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
        if !is_cdg {
            sx = 1.0;
            sy = 1.0;
        }
        let (mut uvx, mut uvy, mut uox, mut uoy) = (1.0f32, 1.0f32, 0.0f32, 0.0f32);
        // Where libmpv actually pillarboxed the picture inside the shared 16:9 texture (measured from dwidth/dheight at FILE_LOADED).
        let span = if is_cdg { 1.0 } else { f64::from_bits(self.flags.span_x.load(Ordering::SeqCst)).clamp(0.05, 1.0) };
        let panel_l = ((1.0 - span) / 2.0) as f32;
        let panel_r = panel_l + span as f32;
        if !is_output && is_cdg {
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
        let sidefill = if is_output && is_cdg { self.sidefill.load(Ordering::SeqCst) } else { 0 };
        glUseProgram(gl.program);
        let vao = if is_output { &mut gl.vao_out } else { &mut gl.vao_prev };
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
        glUniform1f(u.bg_opacity, 0.0); // background video: V4
        glActiveTexture(GL_TEXTURE2);
        glBindTexture(GL_TEXTURE_2D, gl.prev_bg_tex);
        glUniform1i(u.prev_bg_texture, 2);
        glUniform1f(u.prev_bg_opacity, 0.0);
        glUniform1f(u.bg_mix, 1.0);
        let background_active = false;
        if sidefill != 0 {
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
        let rc = self.render_ctx.swap(std::ptr::null_mut(), Ordering::SeqCst);
        unsafe {
            if !rc.is_null() {
                self.api.render_set_update_callback(rc, None, std::ptr::null_mut());
            }
            shim_master_make_current(self.master);
            if !rc.is_null() {
                self.api.render_free(rc);
            }
            let gl = &mut *self.gl.lock().unwrap();
            if !gl.fence.is_null() {
                glDeleteSync(gl.fence);
                gl.fence = std::ptr::null_mut();
            }
        }
    }

    /// Step 2 of shutdown (after the mpv core is destroyed): remove the views.
    pub fn destroy_views(&self) {
        for slot in [&self.output, &self.preview] {
            let v = slot.swap(std::ptr::null_mut(), Ordering::SeqCst);
            if !v.is_null() {
                unsafe { shim_view_destroy(v) };
            }
        }
    }
}

impl Drop for Renderer {
    fn drop(&mut self) {
        unsafe {
            shim_master_destroy(self.master);
            drop(Box::from_raw(self.cb_ctx));
        }
    }
}
