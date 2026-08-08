//! Sidecar lifecycle modules (ADR-0020 §1 + §10 + §14).

// `bubble_coalesce` extracted from `supervisor.rs` — pure UI-
// rate-limiting predicate with nothing to do with sidecar supervision.
// The supervisor module now owns ONLY respawn/backoff logic.
pub(crate) mod bubble_coalesce;
pub(crate) mod spawn;
pub(crate) mod supervisor;
pub(crate) mod ws;

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
