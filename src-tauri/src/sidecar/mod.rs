//! Sidecar lifecycle modules (ADR-0020 §1 + §10 + §14).

// `bubble_coalesce` extracted from `supervisor.rs` — pure UI-
// rate-limiting predicate with nothing to do with sidecar supervision.
// The supervisor module now owns ONLY respawn/backoff logic.
pub(crate) mod bubble_coalesce;
pub(crate) mod handle;
pub(crate) mod shutdown;
pub(crate) mod spawn;
pub(crate) mod supervisor;
pub(crate) mod ws;

// Process-management + shutdown-machinery split:
// `SidecarHandle` (child-process enum + Drop safety net) and the
// shutdown / fire-and-forget-frame helpers moved out of `state.rs`
// into the sidecar module so the shared-state module stays focused on
// `SidecarState` / `WorkerState` data. `state.rs` re-exports these
// names so existing `crate::state::SidecarHandle` /
// `crate::state::shutdown_sidecar_for_exit` /
// `crate::state::send_fire_and_forget_frame` imports keep resolving
// (create-first split — see AGENTS.md E1).
pub(crate) use handle::SidecarHandle;
pub(crate) use shutdown::{send_fire_and_forget_frame, shutdown_sidecar_for_exit};

// C-TEST-5: inline `#[cfg(test)] mod tests` blocks moved to sibling
// `<module>_tests.rs` files. The declarations below wire them in as
// test-only submodules of `sidecar`, mirroring the existing pattern at
// `commands/bubble/mod.rs` and `migrate/mod.rs`.
//
// NOTE: `spawn_tests` is NOT declared here — `spawn.rs` wires its own
// sibling test file via `#[cfg(test)] #[path = "spawn_tests.rs"]` so
// `use super::*` inside it resolves to `spawn` (its tests reference
// `spawn`'s internal items). Declaring it here too would compile the
// file twice, once as a child of `sidecar`, breaking every `super::*`
// lookup.
#[cfg(test)]
mod supervisor_tests;
#[cfg(test)]
mod ws_tests;
