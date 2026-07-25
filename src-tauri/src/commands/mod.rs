//! Tauri command handler modules (ADR-0020 §6 + §7 + §10 + MIG-1.1 + MIG-1.2 + CR-33).

// GT-21: structured error type. See `errors.rs` for the migration plan.
pub(crate) mod errors;

pub(crate) mod sidecar_cmds;
pub(crate) mod export;
pub(crate) mod bubble;
pub(crate) mod system_cmds;
// CR-066: `paste` was extracted from `sidecar_cmds::paste_text` per
// CR-52 (325-LOC god function split into a focused paste module).
// GT-E3-1 (note): `paste_text` + `paste.rs` are dead in PRODUCTION
// traffic (Python sidecar does its own paste internally). Retained
// because tests source-grep the signature + DevTools uses. See
// review.md GT-E3-1 for the full deletion plan.
pub(crate) mod paste;

// CR-5: `dispatch_inner` + `DispatchArgs` are `pub(crate)` (NOT Tauri
// commands — they are the allowlist-bypass inner function the tray
// menu click handler uses). Re-exported with crate visibility because
// `crate::tray` imports them via `use crate::commands::{...}`.
//
// GT-E3-3: the 5 `pub use` re-export blocks for the `#[tauri::command]`
// functions that used to live here were DEAD — `main.rs` imports each
// command directly from its submodule, so the `pub use` re-exports had
// no caller. Both the re-exports and the `#[allow(unused_imports)]`
// annotations are deleted here; `cargo check` confirms `generate_handler!`
// still resolves every command via the direct submodule imports.
pub(crate) use sidecar_cmds::{dispatch_inner, DispatchArgs};
