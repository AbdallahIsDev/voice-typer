//! `server_started` / `worker_started` stdout-handshake parsing + the
//! shutting-down loop short-circuit.

use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};

/// True when `shutting_down` is `Some(flag)` and set. `None` (cold-start)
/// is false. Pure so unit tests need no real sidecar.
pub(crate) fn is_shutting_down(shutting_down: Option<&AtomicBool>) -> bool {
    match shutting_down {
        Some(flag) => flag.load(Ordering::SeqCst),
        None => false,
    }
}

/// Parse `{"event":"server_started","port":N}` from a stdout line.
/// Port uses `u16::try_from` (never `as u16`) so out-of-range values
/// become `None` instead of silently wrapping.
pub(crate) fn parse_server_started(line: &str) -> Option<u16> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("event").and_then(|e| e.as_str()) == Some("server_started") {
        handshake_port(&v)
    } else {
        None
    }
}

/// Parse the ML worker's distinct handshake event
/// `{"event":"worker_started","port":N,"protocol":1}`. Distinct name so
/// the host never mistakes a worker bind for a second sidecar.
pub(crate) fn parse_worker_started(line: &str) -> Option<u16> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("event").and_then(|e| e.as_str()) == Some("worker_started") {
        handshake_port(&v)
    } else {
        None
    }
}

/// Shared port extract: `u16::try_from` + reject 0 (a real bind never
/// reports 0; port 0 would make the WS client dial an OS-assigned port).
fn handshake_port(v: &Value) -> Option<u16> {
    v.get("port")
        .and_then(|p| p.as_u64())
        .and_then(|p| u16::try_from(p).ok())
        .filter(|p| *p != 0)
}
