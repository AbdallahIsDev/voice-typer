//! `server_started` stdout-handshake parsing + the shutting-down loop
//! short-circuit — extracted from the former single-file
//! `sidecar/spawn.rs` (EO-33 split).

use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};

/// Pure form of the shutting-down check used by both spawn loops.
/// Returns `true` when `shutting_down` is `Some(flag)` AND the flag is
/// set (`load(SeqCst) == true`). Returns `false` when `shutting_down`
/// is `None` (cold-start path — no flag to check) or when the flag is
/// not yet set.
///
/// Extracted as a pure helper so it can be unit-tested without
/// spawning a real sidecar process.
pub(crate) fn is_shutting_down(shutting_down: Option<&AtomicBool>) -> bool {
    match shutting_down {
        Some(flag) => flag.load(Ordering::SeqCst),
        None => false,
    }
}

/// Shared stdout-line parser used by both the release-path
/// (`spawn_sidecar_release`) and dev-mode-path (`spawn_sidecar_dev_mode`)
/// stdout-reading loops. Returns the port if `line` is the
/// `{"event":"server_started","port":N}` JSON line, else `None`.
///
/// the port field is parsed via `u16::try_from(p).ok()` instead
/// of `p as u16`. The previous `as u16` cast silently truncated any port
/// value above 65535 (e.g. a corrupted `port: 70000` JSON would wrap to
/// `70000_u32 as u16 = 4464`). `try_from` returns `Err` for out-of-range
/// values, which `.ok()` maps to `None`.
pub(crate) fn parse_server_started(line: &str) -> Option<u16> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("event").and_then(|e| e.as_str()) == Some("server_started") {
        v.get("port")
            .and_then(|p| p.as_u64())
            // try_from instead of truncating `as u16`.
            .and_then(|p| u16::try_from(p).ok())
            // reject port 0. A sidecar that has successfully
            // bound a real port never reports 0 in its `server_started`
            // handshake (the value comes from `socket.getsockname()[1]`
            // AFTER bind succeeds). A `port: 0` is therefore always a
            // bug (uninitialized field, JSON-schema drift, or a
            // hostile/malformed input). Returning `None` here forces the
            // spawn loop to time out and surface a clear error rather
            // than handing a `0` back to `reconnect_ws` which would
            // then attempt to dial `127.0.0.1:0` and get an OS-assigned
            // unrelated connection (or an EADDRNOTAVAIL on platforms
            // that reject port 0 for connect).
            .filter(|p| *p != 0)
    } else {
        None
    }
}
