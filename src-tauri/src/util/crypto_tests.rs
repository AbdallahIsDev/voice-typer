#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation,
    clippy::assertions_on_constants
)] // const invariant pins with descriptive runtime messages

//! Unit tests for `util::crypto` (token + session-id generation).
//!
//! Moved verbatim from `util_tests.rs` when the token/session-id
//! concern was split into its own submodule — tests move with their
//! code. No test logic changed; `use super::*;` now resolves to the
//! `util::crypto` module because this file is declared via
//! `#[cfg(test)] #[path = "crypto_tests.rs"] mod crypto_tests;` inside
//! `util/crypto.rs`.

use super::*;

//generate_token (ADR-0020 §3) ──────────────────────────

#[test]
fn test_generate_token_is_64_char_hex() {
    // ADR-0020 §3: 32 random bytes hex-encoded → 64 hex chars.
    let token = generate_token();
    assert_eq!(token.len(), 64, "token must be 64 hex chars (32 bytes * 2)");
    assert!(
        token.chars().all(|c| c.is_ascii_hexdigit()),
        "token must be valid hex, got: {}",
        token
    );
}

#[test]
fn test_generate_token_is_unique_across_calls() {
    // Two consecutive tokens must differ (vanishingly unlikely with
    // the thread-local RNG (`rand::rng()` in 0.9, was `thread_rng()`
    // in 0.8), but guards against a regression that e.g. seeds a
    // fixed value or reuses a buffer without clearing).
    let t1 = generate_token();
    let t2 = generate_token();
    let t3 = generate_token();
    assert_ne!(t1, t2, "tokens must be unique: t1={} t2={}", t1, t2);
    assert_ne!(t2, t3, "tokens must be unique: t2={} t3={}", t2, t3);
    assert_ne!(t1, t3, "tokens must be unique: t1={} t3={}", t1, t3);
}

//session_id ─────────────────────────────────────────

#[test]
fn test_session_id_is_8_char_lowercase_hex() {
    // The cross-process log-correlation session ID must be
    // 8 lowercase hex chars (matching the Python side's `_session_id`
    // shape + the `[0-9a-f]{8}` validation regex in
    // `voice_typer/server/log/__init__.py`).
    let sid = session_id();
    assert_eq!(sid.len(), 8, "session id must be 8 chars: {}", sid);
    assert!(
        sid.chars().all(|c| c.is_ascii_hexdigit()),
        "session id must be hex: {}",
        sid
    );
    assert!(
        sid.chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit()),
        "session id must be lowercase hex: {}",
        sid
    );
}

#[test]
fn test_session_id_is_stable_per_process() {
    // The session ID is generated ONCE per process (cached in a
    // `OnceLock`) — every call returns the same value, so a respawned
    // sidecar + all Rust log lines share the same join key.
    let a = session_id();
    let b = session_id();
    assert_eq!(a, b, "session_id must be stable across calls");
}
