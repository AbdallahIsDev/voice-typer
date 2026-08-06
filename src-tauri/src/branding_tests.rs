//! Unit tests for `branding` (moved verbatim from the inline
//! `#[cfg(test)] mod tests` block to satisfy C-TEST-5 — tests must
//! live in a sibling file, not inline in the production source).
//!
//! No test logic changed — the `use super::*;` path still resolves
//! to the parent module (`branding`) because the parent file
//! declares this module via `#[cfg(test)] mod branding_tests;`.

use super::*;

/// Smoke test: assert `APP_NAME` is the expected literal.
///
/// This is a forward-looking drift detector — if a future change
/// accidentally renames the constant (e.g. to "VoiceTyper" without
/// the space, or to a marketing rebrand), this test fails before
/// the cross-language `sync_branding.py` script ever runs.
///
/// The Python + TS counterparts (`branding.py::APP_NAME` and
/// `branding.ts::APP_NAME`) MUST be updated in lockstep — a future
/// CI step can grep all three constants and assert equality.
#[test]
fn app_name_is_voice_typer() {
    assert!(!APP_NAME.is_empty(), "APP_NAME must not be empty");
    assert_eq!(APP_NAME, "Voice Typer");
}
