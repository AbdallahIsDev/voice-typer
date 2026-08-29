//! `server_started` stdout-handshake parsing + the shutting-down loop
//! short-circuit — extracted from the former single-file
//! `sidecar/spawn.rs`.

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
        handshake_port(&v)
    } else {
        None
    }
}

/// Worker-handshake stdout parser (Phase 2b — runtime-pack split §7.3).
/// Mirrors [`parse_server_started`] but for the ML worker's DISTINCT
/// event name: `{"event":"worker_started","port":N,"protocol":1}`
/// (see `voice_typer/worker/_ws_server.py` `_WORKER_STARTED_EVENT`).
///
/// The worker deliberately does NOT emit `server_started` (that name
/// is reserved for the slim-core sidecar, which the host already
/// listens for) — a distinct event name lets the host's stdout parser
/// route the worker's bind info to the worker-spawn code path instead
/// of mistaking it for a second sidecar.
///
/// Returns the port if `line` is the worker's `worker_started` JSON
/// line, else `None`. Port validation (range + non-zero) is shared
/// with [`parse_server_started`] via [`handshake_port`].
pub(crate) fn parse_worker_started(line: &str) -> Option<u16> {
    let v: Value = serde_json::from_str(line.trim()).ok()?;
    if v.get("event").and_then(|e| e.as_str()) == Some("worker_started") {
        handshake_port(&v)
    } else {
        None
    }
}

/// Extract the `port` field from a parsed handshake JSON object.
/// Shared by [`parse_server_started`] and [`parse_worker_started`].
///
/// The port is parsed via `u16::try_from(p).ok()` instead of `p as
/// u16` — the previous `as u16` cast silently truncated any port value
/// above 65535 (e.g. a corrupted `port: 70000` JSON would wrap to
/// `70000_u32 as u16 = 4464`). `try_from` returns `Err` for out-of-range
/// values, which `.ok()` maps to `None`.
///
/// Port 0 is rejected: a process that has successfully bound a real
/// port never reports 0 in its handshake (the value comes from
/// `socket.getsockname()[1]` AFTER bind succeeds). A `port: 0` is
/// therefore always a bug (uninitialized field, JSON-schema drift, or
/// hostile/malformed input). Returning `None` here forces the spawn
/// loop to time out and surface a clear error rather than handing a
/// `0` back to the WS client which would then attempt to dial
/// `127.0.0.1:0` and get an OS-assigned unrelated connection (or an
/// EADDRNOTAVAIL on platforms that reject port 0 for connect).
fn handshake_port(v: &Value) -> Option<u16> {
    v.get("port")
        .and_then(|p| p.as_u64())
        .and_then(|p| u16::try_from(p).ok())
        .filter(|p| *p != 0)
}
