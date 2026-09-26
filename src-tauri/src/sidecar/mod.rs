//! Sidecar lifecycle modules (ADR-0020 §1 + §10 + §14).
//! Layout: docs/code-notes/tauri-host.md#module-layout-orchestrator-files

// Pure UI-rate-limiting predicate (not sidecar supervision).
pub(crate) mod bubble_coalesce;
// Durable tee for the child's raw stdout/stderr (release has no terminal).
pub(crate) mod child_log;
pub(crate) mod handle;
pub(crate) mod lifecycle;
pub(crate) mod shutdown;
pub(crate) mod spawn;
pub(crate) mod supervisor;
pub(crate) mod worker_init;
pub(crate) mod worker_supervisor;
pub(crate) mod ws;

// Re-exports so historical `crate::state::*` paths keep resolving
// (create-first split, AGENTS.md E1).
pub(crate) use handle::SidecarHandle;
pub(crate) use shutdown::{send_fire_and_forget_frame, shutdown_sidecar_for_exit};

// C-TEST-5: sibling test modules. `spawn_tests` is declared inside
// `spawn.rs` (not here) so `use super::*` resolves to `spawn`.
#[cfg(test)]
mod lifecycle_tests;
#[cfg(test)]
mod supervisor_tests;
#[cfg(test)]
mod worker_supervisor_tests;
#[cfg(test)]
mod ws_tests;
