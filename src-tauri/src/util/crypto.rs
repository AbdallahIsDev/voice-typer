//! Token + session-id helpers (never logged; ADR-0020 §3).

use crate::util::TOKEN_BYTES;
use rand::RngCore;

pub(crate) fn generate_token() -> String {
    let mut bytes = [0u8; TOKEN_BYTES];
    rand::rng().fill_bytes(&mut bytes);
    hex::encode(&bytes)
}

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
    pub fn encode(bytes: &[u8]) -> String {
        use std::fmt::Write;
        let mut s = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            #[allow(clippy::expect_used)] // fmt::Write for String is infallible
            write!(s, "{:02x}", b).expect("fmt::Write for String is infallible");
        }
        s
    }
}

// Sibling test module: tests live in `crypto_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "crypto_tests.rs"]
mod crypto_tests;
