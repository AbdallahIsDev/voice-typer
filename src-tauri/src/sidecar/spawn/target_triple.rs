//! Target-triple table for `externalBin` + worker exe naming
//! (Phase 2a — runtime-pack split, plan-runtime-pack-split §4.4)
//! (ADR-0020 §4.1) — extracted from the former single-file
//! `sidecar/spawn.rs` (EO-33 split).

pub(crate) fn current_target_triple() -> String {
    target_triple_for(std::env::consts::ARCH, std::env::consts::OS)
}

/// Pure form of `current_target_triple` for unit testing — accepts
/// arch+os as args so tests can verify all (arch, os) combos without
/// running on each platform. Returns the same triple strings the
/// `tauri-plugin-shell` `externalBin` mechanism expects as the binary
/// name suffix (see ADR-0020 §4.1).
pub(crate) fn target_triple_for(arch: &str, os: &str) -> String {
    match (arch, os) {
        ("x86_64", "windows") => "x86_64-pc-windows-msvc".into(),
        ("aarch64", "windows") => "aarch64-pc-windows-msvc".into(),
        ("x86_64", "macos") => "x86_64-apple-darwin".into(),
        ("aarch64", "macos") => "aarch64-apple-darwin".into(),
        ("x86_64", "linux") => "x86_64-unknown-linux-gnu".into(),
        ("aarch64", "linux") => "aarch64-unknown-linux-gnu".into(),
        _ => format!("{}-unknown-{}", arch, os),
    }
}
