//! The OpenGL 3.2 core calls the picture path uses, declared directly against the system framework (macOS). Same calls, same order as
//! `bridge.mm`; no wrapper crate.
#![allow(non_snake_case, non_upper_case_globals, clippy::too_many_arguments)]
use std::ffi::{c_char, c_void};

pub type GLuint = u32;
pub type GLint = i32;
pub type GLenum = u32;
pub type GLsizei = i32;
pub type GLfloat = f32;
pub type GLbitfield = u32;
pub type GLsync = *mut c_void;

pub const GL_TEXTURE_2D: GLenum = 0x0DE1;
pub const GL_TEXTURE_MIN_FILTER: GLenum = 0x2801;
pub const GL_TEXTURE_MAG_FILTER: GLenum = 0x2800;
pub const GL_TEXTURE_WRAP_S: GLenum = 0x2802;
pub const GL_TEXTURE_WRAP_T: GLenum = 0x2803;
pub const GL_LINEAR: GLint = 0x2601;
pub const GL_NEAREST: GLint = 0x2600;
pub const GL_CLAMP_TO_EDGE: GLint = 0x812F;
pub const GL_RGBA: GLenum = 0x1908;
pub const GL_RGBA8: GLint = 0x8058;
pub const GL_UNSIGNED_BYTE: GLenum = 0x1401;
pub const GL_FRAMEBUFFER: GLenum = 0x8D40;
pub const GL_COLOR_ATTACHMENT0: GLenum = 0x8CE0;
pub const GL_FRAMEBUFFER_COMPLETE: GLenum = 0x8CD5;
pub const GL_COLOR_BUFFER_BIT: GLbitfield = 0x4000;
pub const GL_VERTEX_SHADER: GLenum = 0x8B31;
pub const GL_FRAGMENT_SHADER: GLenum = 0x8B30;
pub const GL_COMPILE_STATUS: GLenum = 0x8B81;
pub const GL_LINK_STATUS: GLenum = 0x8B82;
pub const GL_TRIANGLE_STRIP: GLenum = 0x0005;
pub const GL_BLEND: GLenum = 0x0BE2;
pub const GL_SRC_ALPHA: GLenum = 0x0302;
pub const GL_ONE_MINUS_SRC_ALPHA: GLenum = 0x0303;
pub const GL_TEXTURE0: GLenum = 0x84C0;
pub const GL_TEXTURE1: GLenum = 0x84C1;
pub const GL_TEXTURE2: GLenum = 0x84C2;
pub const GL_SYNC_GPU_COMMANDS_COMPLETE: GLenum = 0x9117;
pub const GL_TIMEOUT_IGNORED: u64 = 0xFFFF_FFFF_FFFF_FFFF;

#[cfg(target_os = "macos")]
#[link(name = "OpenGL", kind = "framework")]
extern "C" {
    pub fn glGenTextures(n: GLsizei, textures: *mut GLuint);
    pub fn glBindTexture(target: GLenum, texture: GLuint);
    pub fn glTexParameteri(target: GLenum, pname: GLenum, param: GLint);
    pub fn glTexImage2D(target: GLenum, level: GLint, internalformat: GLint, width: GLsizei, height: GLsizei, border: GLint, format: GLenum, ty: GLenum, pixels: *const c_void);
    pub fn glGenFramebuffers(n: GLsizei, ids: *mut GLuint);
    pub fn glBindFramebuffer(target: GLenum, framebuffer: GLuint);
    pub fn glFramebufferTexture2D(target: GLenum, attachment: GLenum, textarget: GLenum, texture: GLuint, level: GLint);
    pub fn glCheckFramebufferStatus(target: GLenum) -> GLenum;
    pub fn glViewport(x: GLint, y: GLint, w: GLsizei, h: GLsizei);
    pub fn glClearColor(r: GLfloat, g: GLfloat, b: GLfloat, a: GLfloat);
    pub fn glClear(mask: GLbitfield);
    pub fn glFlush();
    pub fn glFenceSync(condition: GLenum, flags: GLbitfield) -> GLsync;
    pub fn glWaitSync(sync: GLsync, flags: GLbitfield, timeout: u64);
    pub fn glDeleteSync(sync: GLsync);
    pub fn glCreateShader(ty: GLenum) -> GLuint;
    pub fn glShaderSource(shader: GLuint, count: GLsizei, string: *const *const c_char, length: *const GLint);
    pub fn glCompileShader(shader: GLuint);
    pub fn glGetShaderiv(shader: GLuint, pname: GLenum, params: *mut GLint);
    pub fn glGetShaderInfoLog(shader: GLuint, max: GLsizei, length: *mut GLsizei, log: *mut c_char);
    pub fn glDeleteShader(shader: GLuint);
    pub fn glCreateProgram() -> GLuint;
    pub fn glAttachShader(program: GLuint, shader: GLuint);
    pub fn glLinkProgram(program: GLuint);
    pub fn glGetProgramiv(program: GLuint, pname: GLenum, params: *mut GLint);
    pub fn glGetProgramInfoLog(program: GLuint, max: GLsizei, length: *mut GLsizei, log: *mut c_char);
    pub fn glDeleteProgram(program: GLuint);
    pub fn glGetUniformLocation(program: GLuint, name: *const c_char) -> GLint;
    pub fn glUseProgram(program: GLuint);
    pub fn glGenVertexArrays(n: GLsizei, arrays: *mut GLuint);
    pub fn glBindVertexArray(array: GLuint);
    pub fn glUniform1i(location: GLint, v0: GLint);
    pub fn glUniform1f(location: GLint, v0: GLfloat);
    pub fn glUniform2f(location: GLint, v0: GLfloat, v1: GLfloat);
    pub fn glActiveTexture(texture: GLenum);
    pub fn glDrawArrays(mode: GLenum, first: GLint, count: GLsizei);
    pub fn glEnable(cap: GLenum);
    pub fn glDisable(cap: GLenum);
    pub fn glBlendFunc(sfactor: GLenum, dfactor: GLenum);
    /// The loader mpv's render API asks for (`dlsym` over the process, which includes the OpenGL framework).
    pub fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
}
