//! Token + session-id generation (ADR-0020 §3).
//!
//! Split out of the former catch-all `util.rs` so the crypto-adjacent
//! helpers (bearer token, cross-process log-correlation session id,
//! and the hand-rolled `hex` encoder they share) live in one focused
//! module. `generate_token` / `session_id` are re-exported from
//! `crate::util` so every existing `crate::util::generate_token` /
//! `crate::util::session_id` path keeps resolving; the `hex` module
//! has no callers outside this module (it stays reachable as
//! `crate::util::crypto::hex::encode`).

use crate::util::TOKEN_BYTES;
use rand::RngCore;

pub(crate) fn generate_token() -> String {
    let mut bytes = [0u8; TOKEN_BYTES];
    rand::rng().fill_bytes(&mut bytes);
    hex::encode(&bytes)
}

/// Per-process, non-secret session ID (8 lowercase hex chars)
/// shared between the Rust host and the Python sidecar for log
/// correlation (crash-report matching, cross-process log joins).
/// Generated once per process, cached in a `OnceLock`, and passed to
/// the sidecar via the `VOICE_TYPER_SESSION_ID` env var so both log
/// streams carry the same join key. Unlike `generate_token` (which is
/// regenerated per respawn), the session ID is stable for the whole
/// host lifetime so a respawned sidecar still correlates to the same
/// host session.
static SESSION_ID: std::sync::OnceLock<String> = std::sync::OnceLock::new();

/// Return the process-wide session ID, generating it on first call.
pub(crate) fn session_id() -> &'static str {
    SESSION_ID.get_or_init(|| {
        let mut bytes = [0u8; 4];
        rand::rng().fill_bytes(&mut bytes);
        hex::encode(&bytes)
    })
}

pub(crate) mod hex {
    /// Writes each byte directly into the pre-allocated String
    /// buffer via `core::fmt::Write`. The `expect` is safe (the
    /// `fmt::Write` impl for `String` is infallible — it never returns
    /// `Err`).
    pub fn encode(bytes: &[u8]) -> String {
        use std::fmt::Write;
        let mut s = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            // Rationale: String's `fmt::Write` impl never errors —
            // `std::fmt::Write::write_str` for `String` unconditionally
            // returns `Ok(())` because the underlying `Vec<u8>` push
            // cannot fail (it aborts on OOM rather than returning Err).
            //(: was `.unwrap()` with a `SAFETY:` comment —
            // switched to `.expect` with a `Rationale:` prefix since
            // this is not an `unsafe` block and `SAFETY:` is reserved
            // for `unsafe` rationale.)
            #[allow(clippy::expect_used)] // fmt::Write for String is infallible (rationale above)
            write!(s, "{:02x}", b).expect("fmt::Write for String is infallible");
        }
        s
    }
}

// Sibling test module — tests live in `crypto_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "crypto_tests.rs"]
mod crypto_tests;
