//! Stage E live control (design: docs/design/stage-e-live-control.md). The shell is a second "browser" of the server's host-control relay: it logs in with the
//! host PIN, asks for a 60-second websocket token, joins the tenant's room as `role=browser`, sends INTENTS (`command` messages) and shows what the app reports
//! (`state`, `queue`, `ack`, `error`). It never writes the app's files and holds no API key.

pub mod client;
pub mod protocol;
pub mod session;
pub mod view;

pub use client::{Config, Handle, Output, SendError, spawn};
pub use protocol::{browser_url, Ack, Incoming, QueueMsg, SingerRow, SongRow, StateMsg, build_command, new_request_id, parse_incoming, validate_command};
pub use session::{Session, SessionError, TokenInfo};
pub use view::{ViewModel, humanize};
