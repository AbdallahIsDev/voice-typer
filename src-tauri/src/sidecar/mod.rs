//! Sidecar lifecycle modules (ADR-0020 §1 + §10 + §14).

// DT-53: `bubble_coalesce` extracted from `supervisor.rs` — pure UI-
// rate-limiting predicate with nothing to do with sidecar supervision.
// The supervisor module now owns ONLY respawn/backoff logic.
pub(crate) mod bubble_coalesce;
pub(crate) mod spawn;
pub(crate) mod supervisor;
pub(crate) mod ws;
