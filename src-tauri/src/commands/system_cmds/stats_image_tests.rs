//! Unit tests for the `save_stats_image` pure core (C-TEST-5 sibling
//! file): PNG data-URL validation (MIME prefix, size cap, DECODED
//! signature), filename sanitization (traversal neutralization,
//! whitespace collapse, hidden-file strip), and the Downloads
//! non-collision probe. The command body itself needs a live Tauri
//! runtime and is covered by the bridge-parity + contract pins in
//! `tests/tauri/`.

#![allow(clippy::unwrap_used, clippy::expect_used)]

use base64::Engine as _;

use super::{
    decode_png_data_url, non_colliding_png_path, safe_png_stem, PNG_DATA_URL_PREFIX,
    PNG_SIGNATURE,
};

/// A minimal valid PNG header (signature + IHDR chunk magic) — enough
/// to satisfy the signature check without embedding a real image.
fn png_header_payload() -> Vec<u8> {
    let mut bytes = PNG_SIGNATURE.to_vec();
    bytes.extend_from_slice(b"\x00\x00\x00\x0dIHDR");
    bytes
}

fn png_data_url() -> String {
    format!(
        "{PNG_DATA_URL_PREFIX}{}",
        base64::engine::general_purpose::STANDARD.encode(png_header_payload())
    )
}

// ── decode_png_data_url ─────────────────────────────────────────

#[test]
fn test_valid_png_data_url_decodes_to_signature_bytes() {
    let decoded = decode_png_data_url(&png_data_url()).expect("valid payload must decode");
    assert_eq!(&decoded[..4], &PNG_SIGNATURE);
}

#[test]
fn test_wrong_mime_prefix_is_rejected() {
    assert!(decode_png_data_url("data:image/jpeg;base64,AAAA").is_none());
    assert!(decode_png_data_url("data:image/png ;base64,AAAA").is_none());
    // A bare base64 blob (no prefix) is not a data URL.
    let raw = base64::engine::general_purpose::STANDARD.encode(png_header_payload());
    assert!(decode_png_data_url(&raw).is_none());
}

#[test]
fn test_empty_base64_payload_is_rejected() {
    assert!(decode_png_data_url(PNG_DATA_URL_PREFIX).is_none());
}

#[test]
fn test_non_png_bytes_are_rejected_by_signature() {
    // Valid base64, wrong magic bytes (the MIME prefix alone must not
    // be trusted — same reason Electron validated the decoded bytes).
    let fake = base64::engine::general_purpose::STANDARD.encode(b"GIF89a_not_a_png");
    assert!(decode_png_data_url(&format!("{PNG_DATA_URL_PREFIX}{fake}")).is_none());
}

#[test]
fn test_oversized_base64_is_rejected_before_decoding() {
    // One byte over the cap: the length check must fire on the
    // base64 string length, not the decoded size.
    let oversized = "A".repeat(super::MAX_PNG_DATA_URL_BYTES + 1);
    assert!(decode_png_data_url(&format!("{PNG_DATA_URL_PREFIX}{oversized}")).is_none());
    // Exactly the cap is accepted shape-wise (the signature check may
    // still reject `AAAA...`, but not because of the cap).
    let at_cap = "A".repeat(super::MAX_PNG_DATA_URL_BYTES);
    assert!(decode_png_data_url(&format!("{PNG_DATA_URL_PREFIX}{at_cap}")).is_none());
}

#[test]
fn test_invalid_base64_is_rejected() {
    assert!(decode_png_data_url(&format!("{PNG_DATA_URL_PREFIX}not!base64!")).is_none());
}

// ── safe_png_stem ───────────────────────────────────────────────

#[test]
fn test_default_name_passes_through() {
    assert_eq!(safe_png_stem(Some("voice-typer-stats")), "voice-typer-stats");
    assert_eq!(safe_png_stem(None), "voice-typer-stats");
    assert_eq!(safe_png_stem(Some("")), "voice-typer-stats");
}

#[test]
fn test_png_suffix_is_peeled() {
    assert_eq!(safe_png_stem(Some("voice-typer-stats.png")), "voice-typer-stats");
    assert_eq!(safe_png_stem(Some("share.PNG")), "share");
}

#[test]
fn test_path_traversal_is_neutralized() {
    // `..` segments and separators must not survive.
    let stem = safe_png_stem(Some("../../evil"));
    assert!(!stem.contains(".."), "traversal survived: {stem}");
    assert!(!stem.contains('/'), "separator survived: {stem}");
    assert!(!stem.contains('\\'), "separator survived: {stem}");
    // A Windows drive-colon name is neutralized too.
    assert!(!safe_png_stem(Some("C:\\temp\\x.png")).contains(':'));
}

#[test]
fn test_whitespace_collapses_and_hidden_names_are_stripped() {
    assert_eq!(safe_png_stem(Some("  my  stats  ")), "my-stats");
    assert!(!safe_png_stem(Some(".hidden")).starts_with('.'));
    assert_eq!(safe_png_stem(Some("---")), "voice-typer-stats");
}

#[test]
fn test_long_names_are_capped_at_80_chars() {
    let long = "x".repeat(200);
    assert_eq!(safe_png_stem(Some(&long)).len(), 80);
}

// ── non_colliding_png_path ──────────────────────────────────────

#[test]
fn test_fresh_dir_uses_the_plain_name() {
    let dir = tempfile_dir();
    assert_eq!(
        non_colliding_png_path(&dir, "voice-typer-stats"),
        dir.join("voice-typer-stats.png")
    );
}

#[test]
fn test_existing_files_get_numbered_suffixes() {
    let dir = tempfile_dir();
    std::fs::write(dir.join("voice-typer-stats.png"), b"old").unwrap();
    std::fs::write(dir.join("voice-typer-stats (1).png"), b"old").unwrap();
    assert_eq!(
        non_colliding_png_path(&dir, "voice-typer-stats"),
        dir.join("voice-typer-stats (2).png")
    );
}

fn tempfile_dir() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "vt-stats-image-tests-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock after epoch")
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir).expect("create temp dir");
    dir
}
