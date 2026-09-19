//! Rust brand constant. C-BRAND-1: do not hardcode the display name.
//! Mirrors `voice_typer/server/branding.py` and renderer `branding.ts`.
//! Filesystem paths use the slug (`voice-typer`), not this string.

/// User-visible product name (tray/toasts/legacy userData probes).
pub(crate) const APP_NAME: &str = "Voice Typer";

#[cfg(test)]
#[path = "branding_tests.rs"]
mod branding_tests;
