//! Launch-timeline epoch markers for the Python sidecar's startup log.
//! Cross-language contract (names + unit) pinned by `sidecar/spawn_tests.rs`
//! against `voice_typer/server/startup_timeline.py`.

use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

/// Env marker: epoch ms at host process start.
pub(crate) const BOOT_EPOCH_ENV: &str = "VOICE_TYPER_BOOT_EPOCH_MS";

/// Env marker: epoch ms immediately before the sidecar spawn.
pub(crate) const SPAWN_EPOCH_ENV: &str = "VOICE_TYPER_SPAWN_EPOCH_MS";

/// Host boot time, recorded once. Absent markers = Python skips the line.
static BOOT_EPOCH_MS: OnceLock<String> = OnceLock::new();

/// Record host process boot time. Call ONCE as early in `main` as practical.
pub(crate) fn record_boot_epoch() {
    BOOT_EPOCH_MS.get_or_init(epoch_ms_string);
}

/// Both timeline markers as `(key, value)` pairs for the sidecar child env.
/// Spawn marker is read at CALL time — invoke immediately before `.spawn()`.
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
        // Clock before 1970 (CMOS reset): "0" keeps the marker parseable
        // (Python clamps the delta to 0 rather than skipping the line).
        .unwrap_or_else(|_| "0".to_string())
}
