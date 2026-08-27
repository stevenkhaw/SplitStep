use std::sync::Mutex;

use crate::sidecar::Sidecar;

/// The one running sidecar, or none while the chooser is up.
///
/// A Mutex rather than a channel because every consumer -- quit, the health
/// poller, Change library -- only ever needs "is there one, and what port".
/// There is no stream of events to carry.
#[derive(Default)]
pub struct AppState {
    pub sidecar: Mutex<Option<Sidecar>>,
}
