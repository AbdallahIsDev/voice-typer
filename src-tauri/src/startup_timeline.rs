//! Launch-timeline epoch markers for the Python sidecar's startup log.
//!
//! Host-parity with the Electron main process (ADR-0020 migration
//! follow-up): the sidecar's `voice_typer/server/startup_timeline.py`
//! derives the one-line "[STARTUP] Launch timeline: …" attribution
//! from two environment markers stamped by the host that spawned it:
//!
//! - [`BOOT_EPOCH_ENV`] — epoch milliseconds at host process start
//!   (Electron sets it at main-bundle eval; the Tauri host records it
//!   as the first statement of `main`).
//! - [`SPAWN_EPOCH_ENV`] — epoch milliseconds immediately before the
//!   Python sidecar process is spawned (fresh on EVERY spawn,
//!   including supervisor respawns — mirrors Electron's
//!   `start-python.ts`, which re-stamps it per spawn).
//!
//! The Python side treats absent markers as "skip the line"
//! (standalone / non-host launches), so both markers are additive: any
//! launch that doesn't go through this module behaves exactly as
//! before. Zero Python changes required.
//!
//! Values are milliseconds since the Unix epoch as decimal strings —
//! the names and unit MUST match `startup_timeline.py`'s
//! `BOOT_EPOCH_ENV` / `SPAWN_EPOCH_ENV` (the cross-language contract
//! is pinned by the literal-name assertions in
//! `sidecar/spawn_tests.rs`).

use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

/// Env marker: epoch ms at host process start (see module docs).
pub(crate) const BOOT_EPOCH_ENV: &str = "VOICE_TYPER_BOOT_EPOCH_MS";

/// Env marker: epoch ms immediately before the sidecar spawn.
pub(crate) const SPAWN_EPOCH_ENV: &str = "VOICE_TYPER_SPAWN_EPOCH_MS";

/// Host boot time, recorded once. `main` calls [`record_boot_epoch`]
/// as its first statement; the `get_or_init` fallback in
/// [`boot_epoch_ms`] keeps any other entry path (tests, future
/// embedders) from producing an empty marker.
static BOOT_EPOCH_MS: OnceLock<String> = OnceLock::new();

/// Record the host process boot time. Call ONCE, as early in `main`
/// as practical — the marker's meaning is "host process start", so
/// every statement that runs before it inflates the measured host-boot
/// phase.
pub(crate) fn record_boot_epoch() {
    BOOT_EPOCH_MS.get_or_init(epoch_ms_string);
}

/// Both timeline markers for the sidecar child env, in the same
/// `(key, value)` pair shape `env_allowlist::passthrough_env_allowlist`
/// returns (accepted by both `tauri_plugin_shell::process::Command::
/// envs` and `tokio::process::Command::envs`).
///
/// The spawn marker is read at CALL time — call this immediately
/// before `.spawn()` so the measured "backend init" phase stays honest.
pub(crate) fn sidecar_timeline_envs() -> Vec<(String, String)> {
    vec![
        (BOOT_EPOCH_ENV.to_string(), boot_epoch_ms().to_string()),
        (SPAWN_EPOCH_ENV.to_string(), epoch_ms_string()),
    ]
}

fn boot_epoch_ms() -> &'static str {
    BOOT_EPOCH_MS.get_or_init(epoch_ms_string)
}

/// Current epoch milliseconds as a decimal string.
fn epoch_ms_string() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis().to_string())
        // A clock set before 1970 (CMOS reset, VM migration) yields
        // Err; "0" keeps the marker well-formed so the Python side
        // still parses it (its max(0.0, now - epoch) clamps the delta
        // to 0 — the line degrades to "0s", never to "skipped").
        .unwrap_or_else(|_| "0".to_string())
}
