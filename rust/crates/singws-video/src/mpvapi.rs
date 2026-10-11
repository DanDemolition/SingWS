//! Minimal libmpv client API, loaded at run time (`dlopen`) from the pinned, patched runtime that ships next to the bridge
//! (`singws_libmpv.2.dylib`). No link-time dependency and no headers: the layouts below are the ones in
//! `native/mpv_bridge/include/mpv/client.h`.
use std::ffi::{c_char, c_double, c_int, c_void, CStr, CString};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub const FORMAT_STRING: c_int = 1;
pub const FORMAT_FLAG: c_int = 3;
pub const FORMAT_INT64: c_int = 4;
pub const FORMAT_DOUBLE: c_int = 5;
pub const FORMAT_NODE: c_int = 6;
pub const FORMAT_NODE_ARRAY: c_int = 7;
pub const FORMAT_NODE_MAP: c_int = 8;

pub const EVENT_NONE: c_int = 0;
pub const EVENT_LOG_MESSAGE: c_int = 2;
pub const EVENT_END_FILE: c_int = 7;
pub const EVENT_FILE_LOADED: c_int = 8;
pub const EVENT_VIDEO_RECONFIG: c_int = 17;
pub const EVENT_PROPERTY_CHANGE: c_int = 22;

pub const RENDER_PARAM_API_TYPE: c_int = 1;
pub const RENDER_PARAM_OPENGL_INIT_PARAMS: c_int = 2;
pub const RENDER_PARAM_OPENGL_FBO: c_int = 3;
pub const RENDER_PARAM_FLIP_Y: c_int = 4;
pub const RENDER_UPDATE_FRAME: u64 = 1;

#[repr(C)]
pub struct RenderParam {
    pub ty: c_int,
    pub data: *mut c_void,
}

#[repr(C)]
pub struct OpenglInitParams {
    pub get_proc_address: unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_void,
    pub get_proc_address_ctx: *mut c_void,
}

#[repr(C)]
pub struct OpenglFbo {
    pub fbo: c_int,
    pub w: c_int,
    pub h: c_int,
    pub internal_format: c_int,
}

pub type RenderUpdateFn = unsafe extern "C" fn(*mut c_void);

pub const END_FILE_EOF: c_int = 0;
pub const END_FILE_ERROR: c_int = 4;

#[repr(C)]
pub struct MpvEvent {
    pub event_id: c_int,
    pub error: c_int,
    pub reply_userdata: u64,
    pub data: *mut c_void,
}

#[repr(C)]
pub struct MpvEventLogMessage {
    pub prefix: *const c_char,
    pub level: *const c_char,
    pub text: *const c_char,
    pub log_level: c_int,
}

#[repr(C)]
pub struct MpvEventEndFile {
    pub reason: c_int,
    pub error: c_int,
    pub playlist_entry_id: i64,
    pub playlist_insert_id: i64,
    pub playlist_insert_num_entries: c_int,
}

#[repr(C)]
pub union NodeValue {
    pub string: *mut c_char,
    pub flag: c_int,
    pub int64: i64,
    pub double: c_double,
    pub list: *mut NodeList,
    pub ba: *mut c_void,
}

#[repr(C)]
pub struct Node {
    pub u: NodeValue,
    pub format: c_int,
}

#[repr(C)]
pub struct NodeList {
    pub num: c_int,
    pub values: *mut Node,
    pub keys: *mut *mut c_char,
}

#[repr(C)]
pub struct MpvEventProperty {
    pub name: *const c_char,
    pub format: c_int,
    pub data: *mut c_void,
}

#[repr(C)]
pub struct ByteArray {
    pub data: *mut c_void,
    pub size: usize,
}

pub type WakeupFn = unsafe extern "C" fn(*mut c_void);

/// An initialized client handle. mpv's client API is thread-safe (except that only one thread may call `wait_event`).
#[derive(Clone, Copy)]
pub struct Handle(pub *mut c_void);
unsafe impl Send for Handle {}
unsafe impl Sync for Handle {}

pub struct Mpv {
    _lib: libloading::Library,
    create: unsafe extern "C" fn() -> *mut c_void,
    initialize: unsafe extern "C" fn(*mut c_void) -> c_int,
    terminate_destroy: unsafe extern "C" fn(*mut c_void),
    set_option_string: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int,
    set_property: unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *mut c_void) -> c_int,
    set_property_string: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int,
    get_property: unsafe extern "C" fn(*mut c_void, *const c_char, c_int, *mut c_void) -> c_int,
    command: unsafe extern "C" fn(*mut c_void, *const *const c_char) -> c_int,
    command_async: unsafe extern "C" fn(*mut c_void, u64, *const *const c_char) -> c_int,
    wait_event: unsafe extern "C" fn(*mut c_void, c_double) -> *mut MpvEvent,
    wakeup: unsafe extern "C" fn(*mut c_void),
    set_wakeup_callback: unsafe extern "C" fn(*mut c_void, Option<WakeupFn>, *mut c_void),
    request_log_messages: unsafe extern "C" fn(*mut c_void, *const c_char) -> c_int,
    error_string: unsafe extern "C" fn(c_int) -> *const c_char,
    free: unsafe extern "C" fn(*mut c_void),
    free_node_contents: unsafe extern "C" fn(*mut Node),
    observe_property: unsafe extern "C" fn(*mut c_void, u64, *const c_char, c_int) -> c_int,
    set_property_async: unsafe extern "C" fn(*mut c_void, u64, *const c_char, c_int, *mut c_void) -> c_int,
    command_ret: unsafe extern "C" fn(*mut c_void, *const *const c_char, *mut Node) -> c_int,
    render_context_create: unsafe extern "C" fn(*mut *mut c_void, *mut c_void, *mut RenderParam) -> c_int,
    render_context_set_update_callback: unsafe extern "C" fn(*mut c_void, Option<RenderUpdateFn>, *mut c_void),
    render_context_update: unsafe extern "C" fn(*mut c_void) -> u64,
    render_context_render: unsafe extern "C" fn(*mut c_void, *mut RenderParam) -> c_int,
    render_context_free: unsafe extern "C" fn(*mut c_void),
}

fn cstr(s: &str) -> CString {
    CString::new(s.replace('\0', "")).unwrap()
}

/// Directory this shared library was loaded from (the bundle's Frameworks folder when packaged).
fn own_dir() -> Option<PathBuf> {
    #[repr(C)]
    struct DlInfo {
        fname: *const c_char,
        fbase: *mut c_void,
        sname: *const c_char,
        saddr: *mut c_void,
    }
    extern "C" {
        fn dladdr(addr: *const c_void, info: *mut DlInfo) -> c_int;
    }
    let mut info = DlInfo { fname: std::ptr::null(), fbase: std::ptr::null_mut(), sname: std::ptr::null(), saddr: std::ptr::null_mut() };
    let here = own_dir as *const c_void;
    unsafe {
        if dladdr(here, &mut info) == 0 || info.fname.is_null() {
            return None;
        }
        let path = PathBuf::from(CStr::from_ptr(info.fname).to_string_lossy().into_owned());
        path.parent().map(Path::to_path_buf)
    }
}

fn candidates() -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(p) = std::env::var("SINGWS_LIBMPV") {
        if !p.is_empty() {
            out.push(PathBuf::from(p));
        }
    }
    if let Some(dir) = own_dir() {
        out.push(dir.join("singws_libmpv.2.dylib"));
        out.push(dir.join("Frameworks").join("singws_libmpv.2.dylib"));
    }
    out
}

impl Mpv {
    fn open() -> Result<Mpv, String> {
        let mut errors = Vec::new();
        for path in candidates() {
            match unsafe { libloading::Library::new(&path) } {
                Ok(lib) => return unsafe { Mpv::bind(lib) },
                Err(e) => errors.push(format!("{}: {e}", path.display())),
            }
        }
        Err(format!("libmpv not found ({})", errors.join("; ")))
    }

    unsafe fn bind(lib: libloading::Library) -> Result<Mpv, String> {
        macro_rules! sym {
            ($name:literal) => {
                *lib.get($name).map_err(|e| format!("{}: {e}", String::from_utf8_lossy($name)))?
            };
        }
        Ok(Mpv {
            create: sym!(b"mpv_create"),
            initialize: sym!(b"mpv_initialize"),
            terminate_destroy: sym!(b"mpv_terminate_destroy"),
            set_option_string: sym!(b"mpv_set_option_string"),
            set_property: sym!(b"mpv_set_property"),
            set_property_string: sym!(b"mpv_set_property_string"),
            get_property: sym!(b"mpv_get_property"),
            command: sym!(b"mpv_command"),
            command_async: sym!(b"mpv_command_async"),
            wait_event: sym!(b"mpv_wait_event"),
            wakeup: sym!(b"mpv_wakeup"),
            set_wakeup_callback: sym!(b"mpv_set_wakeup_callback"),
            request_log_messages: sym!(b"mpv_request_log_messages"),
            error_string: sym!(b"mpv_error_string"),
            free: sym!(b"mpv_free"),
            free_node_contents: sym!(b"mpv_free_node_contents"),
            observe_property: sym!(b"mpv_observe_property"),
            set_property_async: sym!(b"mpv_set_property_async"),
            command_ret: sym!(b"mpv_command_ret"),
            render_context_create: sym!(b"mpv_render_context_create"),
            render_context_set_update_callback: sym!(b"mpv_render_context_set_update_callback"),
            render_context_update: sym!(b"mpv_render_context_update"),
            render_context_render: sym!(b"mpv_render_context_render"),
            render_context_free: sym!(b"mpv_render_context_free"),
            _lib: lib,
        })
    }

    pub fn create(&self) -> Option<Handle> {
        let h = unsafe { (self.create)() };
        (!h.is_null()).then_some(Handle(h))
    }
    pub fn initialize(&self, h: Handle) -> c_int {
        unsafe { (self.initialize)(h.0) }
    }
    pub fn terminate_destroy(&self, h: Handle) {
        unsafe { (self.terminate_destroy)(h.0) }
    }
    pub fn set_option(&self, h: Handle, name: &str, value: &str) -> c_int {
        let (n, v) = (cstr(name), cstr(value));
        unsafe { (self.set_option_string)(h.0, n.as_ptr(), v.as_ptr()) }
    }
    pub fn set_string(&self, h: Handle, name: &str, value: &str) -> c_int {
        let (n, v) = (cstr(name), cstr(value));
        unsafe { (self.set_property_string)(h.0, n.as_ptr(), v.as_ptr()) }
    }
    pub fn set_flag(&self, h: Handle, name: &str, value: bool) -> c_int {
        let n = cstr(name);
        let mut v: c_int = value as c_int;
        unsafe { (self.set_property)(h.0, n.as_ptr(), FORMAT_FLAG, &mut v as *mut c_int as *mut c_void) }
    }
    pub fn set_double(&self, h: Handle, name: &str, value: f64) -> c_int {
        let n = cstr(name);
        let mut v = value;
        unsafe { (self.set_property)(h.0, n.as_ptr(), FORMAT_DOUBLE, &mut v as *mut f64 as *mut c_void) }
    }
    /// `audio-files` = [] (the load path clears it before every `loadfile`).
    pub fn clear_audio_files(&self, h: Handle) -> c_int {
        let n = cstr("audio-files");
        let mut list = NodeList { num: 0, values: std::ptr::null_mut(), keys: std::ptr::null_mut() };
        let mut node = Node { u: NodeValue { list: &mut list }, format: FORMAT_NODE_ARRAY };
        unsafe { (self.set_property)(h.0, n.as_ptr(), FORMAT_NODE, &mut node as *mut Node as *mut c_void) }
    }
    pub fn get_double(&self, h: Handle, name: &str) -> Result<f64, c_int> {
        let n = cstr(name);
        let mut v = 0.0f64;
        let r = unsafe { (self.get_property)(h.0, n.as_ptr(), FORMAT_DOUBLE, &mut v as *mut f64 as *mut c_void) };
        if r >= 0 { Ok(v) } else { Err(r) }
    }
    pub fn get_flag(&self, h: Handle, name: &str) -> Result<bool, c_int> {
        let n = cstr(name);
        let mut v: c_int = 0;
        let r = unsafe { (self.get_property)(h.0, n.as_ptr(), FORMAT_FLAG, &mut v as *mut c_int as *mut c_void) };
        if r >= 0 { Ok(v != 0) } else { Err(r) }
    }
    pub fn get_int64(&self, h: Handle, name: &str) -> Result<i64, c_int> {
        let n = cstr(name);
        let mut v = 0i64;
        let r = unsafe { (self.get_property)(h.0, n.as_ptr(), FORMAT_INT64, &mut v as *mut i64 as *mut c_void) };
        if r >= 0 { Ok(v) } else { Err(r) }
    }
    pub fn get_string(&self, h: Handle, name: &str) -> Option<String> {
        let n = cstr(name);
        let mut p: *mut c_char = std::ptr::null_mut();
        let r = unsafe { (self.get_property)(h.0, n.as_ptr(), FORMAT_STRING, &mut p as *mut *mut c_char as *mut c_void) };
        if r < 0 || p.is_null() {
            return None;
        }
        let s = unsafe { CStr::from_ptr(p).to_string_lossy().into_owned() };
        unsafe { (self.free)(p as *mut c_void) };
        Some(s)
    }
    /// `audio-device-list` as (name, description) pairs.
    pub fn audio_devices(&self, h: Handle) -> Option<Vec<(String, String)>> {
        let n = cstr("audio-device-list");
        let mut node = Node { u: NodeValue { int64: 0 }, format: 0 };
        let r = unsafe { (self.get_property)(h.0, n.as_ptr(), FORMAT_NODE, &mut node as *mut Node as *mut c_void) };
        if r < 0 {
            return None;
        }
        let mut out = Vec::new();
        unsafe {
            if node.format == FORMAT_NODE_ARRAY && !node.u.list.is_null() {
                let list = &*node.u.list;
                for i in 0..list.num as isize {
                    let entry = &*list.values.offset(i);
                    if entry.format != FORMAT_NODE_MAP || entry.u.list.is_null() {
                        continue;
                    }
                    let map = &*entry.u.list;
                    let (mut name, mut description) = (String::new(), String::new());
                    for j in 0..map.num as isize {
                        let v = &*map.values.offset(j);
                        if v.format != 1 {
                            continue;
                        }
                        let text = if v.u.string.is_null() { String::new() } else { CStr::from_ptr(v.u.string).to_string_lossy().into_owned() };
                        let key = CStr::from_ptr(*map.keys.offset(j)).to_string_lossy();
                        if key == "name" {
                            name = text;
                        } else if key == "description" {
                            description = text;
                        }
                    }
                    out.push((name, description));
                }
            }
            (self.free_node_contents)(&mut node);
        }
        Some(out)
    }
    pub fn command(&self, h: Handle, args: &[&str]) -> c_int {
        let owned: Vec<CString> = args.iter().map(|a| cstr(a)).collect();
        let mut ptrs: Vec<*const c_char> = owned.iter().map(|c| c.as_ptr()).collect();
        ptrs.push(std::ptr::null());
        unsafe { (self.command)(h.0, ptrs.as_ptr()) }
    }
    pub fn command_async(&self, h: Handle, userdata: u64, args: &[&str]) -> c_int {
        let owned: Vec<CString> = args.iter().map(|a| cstr(a)).collect();
        let mut ptrs: Vec<*const c_char> = owned.iter().map(|c| c.as_ptr()).collect();
        ptrs.push(std::ptr::null());
        unsafe { (self.command_async)(h.0, userdata, ptrs.as_ptr()) }
    }
    /// The returned event is valid until the next `wait_event` on this handle. Only ONE thread may call this per handle.
    pub fn wait_event(&self, h: Handle, timeout: f64) -> *mut MpvEvent {
        unsafe { (self.wait_event)(h.0, timeout) }
    }
    pub fn wakeup(&self, h: Handle) {
        unsafe { (self.wakeup)(h.0) }
    }
    pub fn request_log_messages(&self, h: Handle, level: &str) {
        let l = cstr(level);
        unsafe { (self.request_log_messages)(h.0, l.as_ptr()) };
    }
    pub fn error_string(&self, code: c_int) -> String {
        unsafe { CStr::from_ptr((self.error_string)(code)).to_string_lossy().into_owned() }
    }
    /// Creates the OpenGL render context. The caller's GL context must be current.
    pub fn render_context_create_gl(&self, h: Handle, init: &mut OpenglInitParams) -> Result<*mut c_void, c_int> {
        let mut api_type = b"opengl\0".to_vec();
        let mut params = [
            RenderParam { ty: RENDER_PARAM_API_TYPE, data: api_type.as_mut_ptr() as *mut c_void },
            RenderParam { ty: RENDER_PARAM_OPENGL_INIT_PARAMS, data: init as *mut OpenglInitParams as *mut c_void },
            RenderParam { ty: 0, data: std::ptr::null_mut() },
        ];
        let mut ctx: *mut c_void = std::ptr::null_mut();
        let r = unsafe { (self.render_context_create)(&mut ctx, h.0, params.as_mut_ptr()) };
        if r >= 0 { Ok(ctx) } else { Err(r) }
    }
    pub fn render_set_update_callback(&self, rc: *mut c_void, cb: Option<RenderUpdateFn>, ctx: *mut c_void) {
        unsafe { (self.render_context_set_update_callback)(rc, cb, ctx) }
    }
    pub fn render_update(&self, rc: *mut c_void) -> u64 {
        unsafe { (self.render_context_update)(rc) }
    }
    pub fn render_into_fbo(&self, rc: *mut c_void, fbo: c_int, w: c_int, h: c_int) -> c_int {
        let mut target = OpenglFbo { fbo, w, h, internal_format: 0x8058 /* GL_RGBA8 */ };
        let mut flip: c_int = 1;
        let mut params = [
            RenderParam { ty: RENDER_PARAM_OPENGL_FBO, data: &mut target as *mut OpenglFbo as *mut c_void },
            RenderParam { ty: RENDER_PARAM_FLIP_Y, data: &mut flip as *mut c_int as *mut c_void },
            RenderParam { ty: 0, data: std::ptr::null_mut() },
        ];
        unsafe { (self.render_context_render)(rc, params.as_mut_ptr()) }
    }
    pub fn render_free(&self, rc: *mut c_void) {
        unsafe { (self.render_context_free)(rc) }
    }
    pub fn observe(&self, h: Handle, userdata: u64, name: &str, format: c_int) {
        let n = cstr(name);
        unsafe { (self.observe_property)(h.0, userdata, n.as_ptr(), format) };
    }
    pub fn set_flag_async(&self, h: Handle, userdata: u64, name: &str, value: bool) -> c_int {
        let n = cstr(name);
        let mut v: c_int = value as c_int;
        unsafe { (self.set_property_async)(h.0, userdata, n.as_ptr(), FORMAT_FLAG, &mut v as *mut c_int as *mut c_void) }
    }
    pub fn set_wakeup_callback(&self, h: Handle, cb: WakeupFn, ctx: *mut c_void) {
        unsafe { (self.set_wakeup_callback)(h.0, Some(cb), ctx) };
    }
    /// `screenshot-raw video`: (BGRA bytes, w, h, stride) of the picture as decoded.
    pub fn screenshot_raw(&self, h: Handle) -> Result<Option<(Vec<u8>, i64, i64, i64)>, c_int> {
        let args = [cstr("screenshot-raw"), cstr("video")];
        let mut ptrs: Vec<*const c_char> = args.iter().map(|a| a.as_ptr()).collect();
        ptrs.push(std::ptr::null());
        let mut node = Node { u: NodeValue { int64: 0 }, format: 0 };
        let r = unsafe { (self.command_ret)(h.0, ptrs.as_ptr(), &mut node) };
        if r < 0 {
            return Err(r);
        }
        let mut out = None;
        unsafe {
            if node.format == FORMAT_NODE_MAP && !node.u.list.is_null() {
                let map = &*node.u.list;
                let (mut w, mut hh, mut stride) = (0i64, 0i64, 0i64);
                let mut data: Option<(*const u8, usize)> = None;
                for i in 0..map.num as isize {
                    let key = *map.keys.offset(i);
                    if key.is_null() {
                        continue;
                    }
                    let v = &*map.values.offset(i);
                    match CStr::from_ptr(key).to_bytes() {
                        b"w" if v.format == FORMAT_INT64 => w = v.u.int64,
                        b"h" if v.format == FORMAT_INT64 => hh = v.u.int64,
                        b"stride" if v.format == FORMAT_INT64 => stride = v.u.int64,
                        b"data" if v.format == 9 && !v.u.ba.is_null() => {
                            let ba = &*(v.u.ba as *const ByteArray);
                            data = Some((ba.data as *const u8, ba.size));
                        }
                        _ => {}
                    }
                }
                if let Some((p, n)) = data {
                    let need = (stride * hh) as usize;
                    if w > 0 && hh > 0 && stride > 0 && n >= need {
                        out = Some((std::slice::from_raw_parts(p, need).to_vec(), w, hh, stride));
                    }
                }
            }
            (self.free_node_contents)(&mut node);
        }
        Ok(out)
    }
    pub fn set_wakeup_callback_none(&self, h: Handle) {
        unsafe { (self.set_wakeup_callback)(h.0, None, std::ptr::null_mut()) };
    }
}

static API: OnceLock<Result<Mpv, String>> = OnceLock::new();

/// The process-wide libmpv, loaded on first use.
pub fn api() -> Result<&'static Mpv, &'static str> {
    API.get_or_init(Mpv::open).as_ref().map_err(|e| e.as_str())
}
