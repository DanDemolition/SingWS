//! State shared between the control core (core.rs) and the renderer (render.rs).
use std::sync::atomic::{AtomicBool, AtomicU64};

pub struct Flags {
    pub loading: AtomicBool,
    pub is_cdg: AtomicBool,
    pub has_frame: AtomicBool,
    pub stopping: AtomicBool,
    /// f64 bits: fraction of the shared texture the (pillarboxed) picture occupies.
    pub span_x: AtomicU64,
}

pub const TEXTURE_W: i32 = 1920;
pub const TEXTURE_H: i32 = 1080;
pub const CDG_W: i32 = 300;
pub const CDG_H: i32 = 216;
