//! Real-time-safe master bus for SingWS (Stage 2 of RUST_MIGRATION_PLAN.md).
//!
//! A line-by-line port of `singws_master_audio.MasterAudioProcessor`, which stays the reference
//! implementation: the tests compare this crate against it sample for sample. No allocation, no
//! locks and no unsafe code in the processing path.
#![forbid(unsafe_code)]

pub mod master;

pub use master::{MAX_CHANNELS, MasterProcessor, PARAM_COUNT, PARAM_NAMES, Params};
