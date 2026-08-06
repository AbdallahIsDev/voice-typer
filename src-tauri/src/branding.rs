//! Cross-language brand constant for the Voice Typer Tauri host.
//!
//! The brand literal `"Voice Typer"` was previously inlined at
//! multiple Rust sites (`tray.rs::TRAY_TOOLTIP`, `tray.rs::empty_menu`'s
//! placeholder label, `migrate::electron_userdata_candidates`'s defensive third probe).
//! Each inline literal was a drift hazard — a future rename would
//! have to find + touch every site, and there was no compiler-enforced
//! single source of truth.
//!
//! This module is the Rust mirror of:
//!   - `voice_typer/server/branding.py::APP_NAME` (Python canonical)
//!   - `voice_typer/client/src/main/branding.ts::APP_NAME` (TS mirror)
//!
//! All three constants MUST stay byte-for-byte identical. Cross-language
//! parity is enforced by `scripts/build/sync_versions.py` (which
//! synchronizes version strings) — a future `scripts/build/sync_branding.py`
//! extension can grep this Rust constant + the Python + TS counterparts
//! and assert equality. The unit test below is a smoke test that the
//! Rust constant is the expected literal; a CI failure here surfaces a
//! drift before the cross-language sync script even runs.
//!
//! # Why a separate module (not inline in `main.rs`)?
//!
//! Module-level `pub const` is the canonical Rust pattern for a
//! process-global constant. It's `const`-evaluable (usable in `const`
//! contexts like `const TRAY_TOOLTIP: &str = crate::branding::APP_NAME;`
//! — a `use` import would NOT work in const context), zero-cost (no
//! allocation, no indirection), and visible to all modules via the
//! `crate::branding::APP_NAME` path.

/// The user-visible product name. Used for tray tooltips, toast
/// notifications, and the legacy Electron userData directory name probe
/// (where some ancient builds used the human-readable capitalized name
/// with a space).
///
/// **Do NOT use this for filesystem paths** — use the lowercase slug
/// `voice-typer` (see `platform::paths::config_dir`) for directory
/// names. The brand name happens to be a valid directory name on most
/// filesystems, but the canonical on-disk identifier is the slug.
///
/// Visibility: `pub(crate)` — every caller lives inside this crate
/// (`tray.rs::TRAY_TOOLTIP`, `tray.rs::empty_menu`'s placeholder
/// label, `sidecar/supervisor.rs`'s restart prompt). Demoted from
/// `pub` (which would expose the constant on the crate's public
/// surface) because no external crate links against `voice-typer`
/// (it's a binary crate, not a library), and a tighter visibility
/// surfaces unintended cross-module couplings at compile time rather
/// than letting them slip through as silent API growth.
pub(crate) const APP_NAME: &str = "Voice Typer";


#[cfg(test)]
#[path = "branding_tests.rs"]
mod branding_tests;
