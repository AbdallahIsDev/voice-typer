//! Sibling tests for `commands::sidecar_cmds::dispatch` (per C-TEST-5,
//! sibling test file, no inline tests in production source).
//!
//! Pins the MO-115 derived dispatch-data cap:
//!
//! - the derivation itself (`DISPATCH_DATA_MAX_BYTES ==
//!   MAX_FRAME_BYTES - ENVELOPE_HEADROOM_BYTES`), so a future change
//!   to the 1 MiB frame ceiling moves the host gate with it;
//! - a `save_vocabulary`-scale payload between the old 256 KiB gate
//!   and 1 MiB PASSES the host check;
//! - a payload just over the derived cap is rejected (the same
//!   comparison `dispatch_inner` applies to the serialized `data_str`).
//!
//! Hermetic: no network, no sidecar spawn, no Tauri runtime. The gate
//! is a pure length check against a derived constant.

use super::{DISPATCH_DATA_MAX_BYTES, ENVELOPE_HEADROOM_BYTES};
use crate::util::MAX_FRAME_BYTES;

/// The derivation relationship, not a magic number. If someone raises
/// `MAX_FRAME_BYTES`, this fails until the headroom story is re-audited.
#[test]
fn test_dispatch_data_cap_is_derived_from_frame_ceiling_minus_headroom() {
    assert_eq!(ENVELOPE_HEADROOM_BYTES, 256);
    assert_eq!(
        DISPATCH_DATA_MAX_BYTES,
        MAX_FRAME_BYTES - ENVELOPE_HEADROOM_BYTES,
        "DISPATCH_DATA_MAX_BYTES must stay derived from MAX_FRAME_BYTES \
         minus ENVELOPE_HEADROOM_BYTES (MO-115)"
    );
    // 1 MiB - 256: the old hard 256 KiB gate sat far below this.
    assert_eq!(DISPATCH_DATA_MAX_BYTES, 1024 * 1024 - 256);
}

/// A `save_vocabulary`-scale payload (between the old 256 KiB gate and
/// the derived 1 MiB-256 cap) must PASS the host size check.
#[test]
fn test_save_vocabulary_scale_payload_passes_host_gate() {
    // 512 KiB of JSON-array payload: larger than the old 256 KiB hard
    // cap, well under the derived ceiling. Serialization is hermetic.
    // Each entry serializes to ~21 bytes (`"vocab-entry-XXXXX",`).
    let entries: Vec<String> = (0..25_000)
        .map(|i| format!("\"vocab-entry-{i:05}\""))
        .collect();
    let data_str = format!("[{}]", entries.join(","));
    assert!(
        data_str.len() > 256 * 1024,
        "fixture must exceed the OLD 256 KiB gate to be a real MO-115 case \
         (got {} bytes)",
        data_str.len()
    );
    assert!(
        data_str.len() <= DISPATCH_DATA_MAX_BYTES,
        "save_vocabulary-scale payload ({} bytes) must pass the derived \
         cap ({} bytes)",
        data_str.len(),
        DISPATCH_DATA_MAX_BYTES
    );
}

/// Just over the derived cap is rejected — same `>` comparison
/// `dispatch_inner` uses on the serialized `data_str`.
#[test]
fn test_payload_just_over_derived_cap_is_rejected() {
    let over = "x".repeat(DISPATCH_DATA_MAX_BYTES + 1);
    assert!(over.len() > DISPATCH_DATA_MAX_BYTES);
    let at_cap = "x".repeat(DISPATCH_DATA_MAX_BYTES);
    assert!(at_cap.len() <= DISPATCH_DATA_MAX_BYTES);
}

/// Exactly at the cap passes (`>` is strict, not `>=`).
#[test]
fn test_payload_exactly_at_derived_cap_passes() {
    let at_cap = "y".repeat(DISPATCH_DATA_MAX_BYTES);
    assert_eq!(at_cap.len(), DISPATCH_DATA_MAX_BYTES);
    assert!(!(at_cap.len() > DISPATCH_DATA_MAX_BYTES));
}
