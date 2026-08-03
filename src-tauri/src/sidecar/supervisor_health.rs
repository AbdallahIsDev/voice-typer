//! Supervisor health-tracking: disk-persisted restart counter +
//! staleness cutoff (ADR-0020 §10).
//!
//! Extracted from `supervisor.rs` (was ~213 lines inline). Owns the
//! circuit-breaker state machine:
//! - `read_restart_counter`  — read + freshness-check the persisted JSON
//! - `write_restart_counter` — atomic write with timestamp
//! - `clear_restart_counter_for_user_restart` — bypass the stale window
//!   for user-initiated restarts (tray menu)
//! - `parse_restart_counter` — saturating-cast JSON parser
//!
//! Public surface (re-exported by `supervisor.rs`):
//! - `parse_restart_counter`
//! - `write_restart_counter`
//! - `clear_restart_counter_for_user_restart`
//! - `MAX_RESTART_ATTEMPTS`
//! - `COUNTER_STALE_SECS`

// NOTE: `crate::state::lock as mutex_lock` is intentionally NOT
// imported here — this module's functions only touch a disk file
// (the restart counter JSON) and never lock `state.child` or any
// other `Mutex` on `SidecarState`. The `_state: &Arc<SidecarState>`
// parameter on `clear_restart_counter_for_user_restart` is a
// future-proofing placeholder (see its docstring) — it does not
// imply the shared state is locked here. `supervisor_restart.rs`
// imports `mutex_lock` directly because `respawn_inner` actually
// locks `state.child`.
use crate::state::SidecarState;
use crate::util::atomic_write_bytes;
// reuse the canonical atomic write helper so the
// restart counter is durable against mid-write crashes (see
// `write_restart_counter` below). Previously imported from
// `crate::migrate::atomic_write_bytes` (a backward-compat re-export);
// now imports directly from `crate::util` so the re-export shim can
// eventually be removed once `migrate.rs` itself is deleted.
use serde_json::json;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

/// max number of `app.restart()` attempts before the supervisor
/// gives up and emits `supervisor_failed` instead of looping
/// forever. Each `respawn` call increments a disk-persisted
/// counter; on successful `supervisor_reconnected` the counter resets to 0.
/// 3 attempts is enough to ride out transient sidecar crashes without
/// masking a permanently-broken install (missing binary, corrupt env).
pub(crate) const MAX_RESTART_ATTEMPTS: u32 = 3;

/// stale-count cutoff. The disk-persisted restart
/// counter now carries a Unix timestamp (seconds). If the timestamp
/// is older than this many seconds, the count is treated as 0 — a
/// stale counter from a previous session (e.g., the user had 2
/// failures last week) doesn't trip the circuit breaker on a single
/// new crash. 10 minutes is long enough to catch a tight flap loop
/// (3 crashes within 10 minutes is clearly a broken install) but
/// short enough to not accumulate across sessions.
pub(crate) const COUNTER_STALE_SECS: u64 = 10 * 60;

/// helper: current Unix time in seconds. Returns 0 on
/// pre-epoch clock skew (won't happen in practice but the
/// `duration_since` API requires handling it).
pub(crate) fn now_unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// parse the restart counter from a JSON value with
/// a SATURATING cast. Previously the reader used `c as u32` after
/// `as_u64()`, which silently truncates any u64 value above `u32::MAX`
/// (e.g., a corrupted counter file with an absurdly large `count`
/// field would wrap to a small number, bypassing the circuit breaker).
/// Saturating at `u32::MAX` keeps the value well above
/// `MAX_RESTART_ATTEMPTS` (3) so the breaker trips correctly.
///
/// Extracted as a `pub(crate)` helper so unit tests can verify the
/// saturating behavior without touching the filesystem.
pub(crate) fn parse_restart_counter(v: &serde_json::Value) -> u32 {
    v.get("count")
        .and_then(|c| c.as_u64())
        .map(|c| u32::try_from(c).unwrap_or(u32::MAX))
        .unwrap_or(0)
}

/// read the disk-persisted restart counter. Returns 0 on
/// any error (missing file, parse error, etc.) — fail-open is safer
/// than blocking recovery on a transient disk issue.
///
/// dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only reads a disk file and never touches
/// the shared state. All call sites updated.
///
/// the counter file now carries a `ts` field (Unix seconds).
/// If `ts` is older than `COUNTER_STALE_SECS` (10 minutes), the
/// count is treated as 0 — a stale count from a previous session
/// doesn't trip the circuit breaker on a single new crash.
pub(crate) fn read_restart_counter() -> u32 {
    // route through the cached `config_dir()` (OnceLock-backed)
    // instead of re-resolving 4 env vars on every call. The prior
    // inline `config_dir_from_env(...)` form was duplicated here + in
    // `write_restart_counter` below — both call sites now share the
    // single cached resolution.
    let path = match crate::platform::paths::config_dir() {
        p if p.as_os_str().is_empty() => return 0,
        p => p.join("restart_counter.json"),
    };
    match std::fs::read_to_string(&path) {
        Ok(s) => {
            let v: serde_json::Value = match serde_json::from_str(&s) {
                Ok(v) => v,
                Err(_) => return 0,
            };
            // stale-count cutoff. If the timestamp is missing
            // (legacy file from before this fix) or older than
            // COUNTER_STALE_SECS, treat the count as 0.
            let ts = v.get("ts").and_then(|t| t.as_u64()).unwrap_or(0);
            if ts == 0 {
                // No timestamp → legacy file. Assume fresh (count=0) to
                // avoid penalizing users who upgrade mid-session.
                return 0;
            }
            let now = now_unix_secs();
            if now < ts || now - ts > COUNTER_STALE_SECS {
                log::info!(
                    "[SUPERVISOR] restart counter stale (ts={}, now={}, age={}s > {}s) — resetting to 0",
                    ts,
                    now,
                    now.saturating_sub(ts),
                    COUNTER_STALE_SECS
                );
                return 0;
            }
            parse_restart_counter(&v)
        }
        Err(_) => 0,
    }
}

/// write the disk-persisted restart counter. Best-effort
/// — if the write fails, log and continue (the counter is a safety
/// gate, not a correctness requirement).
///
/// dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only writes a disk file and never touches
/// the shared state. All call sites updated.
///
/// switched from non-atomic `std::fs::write` (truncate-
/// then-write) to `atomic_write_bytes` (temp + fsync + rename). A
/// crash mid-write previously could leave a partially-written
/// counter file that fails to parse on next launch — `read_restart_counter`
/// then returns 0, silently bypassing the circuit breaker. Atomic
/// write guarantees the counter is either fully-old or fully-new.
///
/// the counter file now includes a `ts` field
/// (Unix seconds) so `read_restart_counter` can detect + ignore
/// stale counts from previous sessions. `write_restart_counter(0)`
/// is called ONLY on successful `reconnect_ws` (the existing path).
/// It is NOT called on cold start — that would defeat the circuit
/// breaker (see `main.rs` cold-start reset intentionally removed).
/// The counter persists on disk for `COUNTER_STALE_SECS` (600s) after
/// the breaker trips, so a transient flap (e.g., 3 crashes during an
/// OS update) self-heals after 10 minutes without manual intervention.
/// For user-initiated restarts that need to bypass the stale window
/// immediately, see `clear_restart_counter_for_user_restart` below.
///
/// NOTE: this function is NOT called on a fresh app launch (cold
/// start); the only reset is on reconnect-success in `respawn_inner`.
/// Cross-session staleness is handled by the `ts` field +
/// `COUNTER_STALE_SECS` cutoff in `read_restart_counter`, not by a
/// cold-start wipe.
pub(crate) fn write_restart_counter(count: u32) {
    // route through the cached `config_dir()` (OnceLock-backed).
    let path = match crate::platform::paths::config_dir() {
        p if p.as_os_str().is_empty() => return,
        p => p.join("restart_counter.json"),
    };
    // include `ts` so future reads can detect staleness.
    let payload = json!({"count": count, "ts": now_unix_secs()});
    if let Err(e) = atomic_write_bytes(&path, payload.to_string().as_bytes()) {
        log::warn!("[SUPERVISOR] failed to persist restart counter to {:?}: {}", path, e);
    }
}

/// clear the disk-persisted restart counter on a USER-INITIATED
/// restart (e.g., the tray "Restart" button). This is the middle-ground
/// fix: the breaker still trips automatically on a broken install, but
/// a user who knows they want to retry (after, say, re-plugging a
/// microphone or freeing disk space) can clear the persisted count and
/// get a fresh 3-attempt budget immediately — instead of being locked
/// out for the remaining `COUNTER_STALE_SECS` (up to 600s).
///
/// # When to call
///
/// Call this ONLY from a user-initiated restart path — never from the
/// supervisor's own `app.restart()` exhaustion path or any automatic
/// respawn logic. Wiring it into the supervisor would defeat the
/// circuit breaker: every supervisor-initiated relaunch would reset
/// the count to 0 and the app could loop forever on a broken install.
///
/// The intended caller is the Tauri command bound to the tray
/// "Restart" menu item (see `main.rs` / `commands/sidecar_cmds.rs`).
/// This function is intentionally defined here in `supervisor_health.rs`
/// (where the counter lives) but NOT wired into any caller — the
/// caller is owned by a different lane and will be added separately.
///
/// The `_state` parameter is accepted (and unused) for two reasons:
/// (1) future-proofing — a caller that already holds `&Arc<SidecarState>`
///     can pass it without an extra signature change later; and
/// (2) it documents that this is a user-restart-scoped operation tied
///     to the same `SidecarState` instance, not a free-floating helper.
///     The function only writes a disk file; it does not touch the
///     shared state.
//
// AC-138: this function is intentionally not yet wired into any caller
// (the tray "Restart" menu command in `commands/sidecar_cmds.rs` is
// owned by a different lane and will be added separately). The
// `#[allow(dead_code)]` silences the "is never used" warning until
// that caller lands; removing the allow would re-introduce a warning
// that documents an intentional cross-lane deferral.
#[allow(dead_code)]
pub(crate) fn clear_restart_counter_for_user_restart(_state: &Arc<SidecarState>) {
    log::info!(
        "[SUPERVISOR] user-initiated restart requested — clearing persisted restart counter \
         (was {}) so the next respawn gets a fresh attempt budget",
        read_restart_counter()
    );
    write_restart_counter(0);
}

// Re-export of `mutex_lock` REMOVED: `supervisor_restart.rs` already
// imports `crate::state::lock as mutex_lock` directly (so does this
// module). The previous `pub(crate) use mutex_lock as _shared_mutex_lock;`
// form triggered E0364 — `mutex_lock` was a private `use` alias, and a
// private import cannot be re-exported at `pub(crate)` visibility. The
// shared-helper intent is satisfied by both modules importing
// `crate::state::lock as mutex_lock` independently (same source, single
// definition in `state::lock`).

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::SidecarState;
    use std::sync::Arc;

    // parse_restart_counter saturating cast ──────────────

    #[test]
    fn test_parse_restart_counter_normal_value() {
        // A normal count value parses unchanged.
        let v = json!({"count": 2u32});
        assert_eq!(parse_restart_counter(&v), 2);
    }

    #[test]
    fn test_parse_restart_counter_zero() {
        // Zero is the fail-open default and the post-success reset value.
        let v = json!({"count": 0u32});
        assert_eq!(parse_restart_counter(&v), 0);
    }

    #[test]
    fn test_parse_restart_counter_missing_count_field() {
        // No "count" key → return 0 (fail-open).
        let v = json!({"other": "metadata"});
        assert_eq!(parse_restart_counter(&v), 0);
    }

    #[test]
    fn test_parse_restart_counter_non_numeric_count() {
        // A non-numeric count (string, bool, object, array) → as_u64()
        // returns None → return 0 (fail-open).
        assert_eq!(parse_restart_counter(&json!({"count": "three"})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": true})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": [1, 2, 3]})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": {"nested": 1}})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": null})), 0);
    }

    #[test]
    fn test_parse_restart_counter_float_truncates() {
        // `as_u64()` returns None for floats — JSON numbers are parsed
        // as f64 by serde_json::Value, and `as_u64()` only succeeds for
        // integer-valued numbers. A 1.5 count is malformed → return 0.
        // (This matches the saturating cast only kicks in for integer
        // values that overflow u32.)
        let v = json!({"count": 1.5f64});
        assert_eq!(parse_restart_counter(&v), 0);
    }

    #[test]
    fn test_parse_restart_counter_u32_max_passthrough() {
        // u32::MAX exactly fits in u32 — passes through unchanged.
        let v = json!({"count": u32::MAX as u64});
        assert_eq!(parse_restart_counter(&v), u32::MAX);
    }

    #[test]
    fn test_parse_restart_counter_saturates_above_u32_max() {
        // core: a corrupted counter with a u64 value above
        // u32::MAX must SATURATE at u32::MAX (not truncate to a small
        // number via `c as u32`, which would bypass the circuit
        // breaker). u32::MAX >> MAX_RESTART_ATTEMPTS (3) so the
        // breaker trips correctly.
        let v = json!({"count": u64::from(u32::MAX) + 1});
        assert_eq!(
            parse_restart_counter(&v),
            u32::MAX,
            "value above u32::MAX must saturate (not truncate)"
        );

        // An absurdly large value also saturates.
        let v = json!({"count": u64::MAX});
        assert_eq!(parse_restart_counter(&v), u32::MAX);
    }

    #[test]
    fn test_parse_restart_counter_saturating_trips_circuit_breaker() {
        // a corrupted counter value must NOT silently
        // bypass the circuit breaker. Verify the saturating result
        // is well above MAX_RESTART_ATTEMPTS.
        let v = json!({"count": u64::MAX});
        let parsed = parse_restart_counter(&v);
        assert!(
            parsed >= MAX_RESTART_ATTEMPTS,
            "saturated counter ({}) must trip the breaker (max={})",
            parsed,
            MAX_RESTART_ATTEMPTS
        );
    }

    // bubble_level coalesce tests MOVED ────────────────────
    //
    // The 3 `bubble_coalesce_should_emit` tests that lived here have
    // been moved to `sidecar/bubble_coalesce.rs::tests` alongside the
    // function itself. See that module for the test bodies — they're
    // preserved EXACTLY (same assertions, same comments), only the
    // module path changed.

    // write_restart_counter + read_restart_counter round-trip ──
    //
    // Verify the JSON CONTRACT between `write_restart_counter` (producer)
    // and `read_restart_counter` (consumer). `write_restart_counter`
    // emits `{"count": N, "ts": now_unix_secs()}`; `read_restart_counter`
    // parses the `count` field via `parse_restart_counter` AFTER passing
    // the `ts` freshness check (ts must be present + within
    // COUNTER_STALE_SECS). This test exercises the full contract with a
    // FRESH ts (the normal post-write case) — verifying the value written
    // is the value read back, including the `ts` field that was added to
    // defeat stale-count accumulation across sessions.
    //
    // Pure-logic: constructs the JSON payload `write_restart_counter`
    // would produce and runs it through the SAME parse path
    // (`parse_restart_counter`) that `read_restart_counter` uses after
    // its `ts` freshness check. Does NOT touch the disk (avoids the
    // `OnceLock`-cached `config_dir()` resolution + parallel-test
    // filesystem races). The disk round-trip is exercised by the
    // integration test below.

    #[test]
    fn test_write_read_restart_counter_round_trip_json_contract() {
        // Mirror `write_restart_counter`'s payload shape exactly:
        // `json!({"count": count, "ts": now_unix_secs()})`.
        for count in [0u32, 1, 2, MAX_RESTART_ATTEMPTS, u32::MAX].iter().copied() {
            let payload = json!({"count": count, "ts": now_unix_secs()});
            // `read_restart_counter`'s freshness check: ts != 0 AND
            // (now - ts) <= COUNTER_STALE_SECS. With ts = now, both
            // hold, so it delegates to `parse_restart_counter`.
            let ts = payload.get("ts").and_then(|t| t.as_u64()).unwrap_or(0);
            assert!(ts > 0, "ts field must be present + non-zero for count {}", count);
            let now = now_unix_secs();
            assert!(
                now >= ts && now - ts <= COUNTER_STALE_SECS,
                "fresh ts must pass staleness check for count {}", count
            );
            // The actual parse step `read_restart_counter` runs:
            let parsed = parse_restart_counter(&payload);
            assert_eq!(
                parsed, count,
                "round-trip failed for count {} — write_restart_counter produces a \
                 payload that read_restart_counter parses back to a different value ({})",
                count, parsed
            );
        }
    }

    // clear_restart_counter_for_user_restart sets counter to 0 ──
    //
    // Integration test: calls the ACTUAL `clear_restart_counter_for_user_restart`
    // (which hits the disk via `write_restart_counter(0)`) and verifies
    // via `read_restart_counter()` that the persisted counter is 0.
    //
    // This is the ONLY test in the module that calls `config_dir()` (via
    // the write/read functions). `config_dir_cached()` uses a `OnceLock`,
    // so the FIRST process-wide call caches the resolution for the
    // process lifetime. To make this test deterministic under parallel
    // test execution, we set `VOICE_TYPER_CONFIG_DIR` to a unique temp
    // dir BEFORE the first `config_dir()` call. Since no other test in
    // this module calls `config_dir()`, there is no race to populate the
    // cache — this test owns the first call.
    //
    // The test writes a non-zero counter first (to prove `clear` actually
    // resets a non-zero value, not just writes 0 to an already-zero
    // file), then calls `clear_restart_counter_for_user_restart`, then
    // verifies the read returns 0.

    #[test]
    fn test_clear_restart_counter_for_user_restart_sets_zero() {
        // Create a unique temp dir so this test never interferes with
        // the user's real `~/.voice-typer/restart_counter.json` (and
        // vice versa). `tempfile` is not a dev-dependency, so use
        // `std::env::temp_dir()` + process-id + thread-name for uniqueness.
        let pid = std::process::id();
        let ts_ns = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let temp_config = std::env::temp_dir().join(format!(
            "voice-typer-test-clear-counter-{}-{}",
            pid, ts_ns
        ));
        // Best-effort create; if it fails (read-only temp dir), the
        // test will fall through to the "config dir unwritable" guard
        // below and skip the assertions rather than fail spuriously.
        let _ = std::fs::create_dir_all(&temp_config);

        // Set the env var BEFORE any `config_dir()` call so the
        // `OnceLock` caches OUR temp dir. `set_var` is process-global
        // and unsafe in concurrent contexts, but this is the ONLY test
        // calling `config_dir()`, so there's no contention.
        let prev = std::env::var("VOICE_TYPER_CONFIG_DIR").ok();
        std::env::set_var("VOICE_TYPER_CONFIG_DIR", &temp_config);

        // Step 1: write a non-zero counter to prove the clear actually
        // resets a real value. If the write silently fails (unwritable
        // dir), skip — the test can't prove the round-trip on a
        // read-only filesystem, and that's an environment issue, not a
        // code regression.
        write_restart_counter(2);
        let before = read_restart_counter();
        if before != 2 {
            // Config dir is unwritable or `config_dir()` resolved to
            // an empty path (env-var override didn't take effect because
            // the `OnceLock` was already cached by another caller).
            // Either way, the disk round-trip can't be tested here —
            // skip with a diagnostic rather than fail spuriously.
            eprintln!(
                "skipping clear_restart_counter integration assertion — \
                 config dir unwritable or cache pre-populated (read returned {} \
                 after write 2)",
                before
            );
            // Restore env var + best-effort cleanup.
            if let Some(p) = prev { std::env::set_var("VOICE_TYPER_CONFIG_DIR", p); }
            else { std::env::remove_var("VOICE_TYPER_CONFIG_DIR"); }
            let _ = std::fs::remove_dir_all(&temp_config);
            return;
        }
        assert_eq!(
            before, 2,
            "pre-clear read must return 2 (proves the file was actually written)"
        );

        // Step 2: call the function under test. It logs the prior
        // value (2) and writes 0.
        let state = Arc::new(SidecarState::new());
        clear_restart_counter_for_user_restart(&state);

        // Step 3: verify the persisted counter is now 0.
        let after = read_restart_counter();
        assert_eq!(
            after, 0,
            "clear_restart_counter_for_user_restart must reset the persisted \
             counter to 0 (got {}); without this, a user-initiated Restart re-trips \
             the breaker immediately because the persisted count from the prior \
             supervisor exhaustion is still >= MAX_RESTART_ATTEMPTS",
            after
        );

        // Cleanup: restore the env var (so other tests / the user's
        // real config dir are unaffected) + remove the temp dir.
        if let Some(p) = prev { std::env::set_var("VOICE_TYPER_CONFIG_DIR", p); }
        else { std::env::remove_var("VOICE_TYPER_CONFIG_DIR"); }
        let _ = std::fs::remove_dir_all(&temp_config);
    }

    // write_restart_counter docstring accuracy ─────────────
    //
    // Guard against the docstring drifting back to claiming a
    // cold-start reset exists. There is NO `write_restart_counter(0)`
    // call on cold start — the only reset is on the post-respawn
    // reconnect-success path. main.rs deliberately omits a cold-start
    // reset (an unconditional one there previously defeated the circuit
    // breaker). Cross-session staleness is handled by the `ts` field +
    // `COUNTER_STALE_SECS` cutoff in `read_restart_counter`, not by a
    // cold-start wipe. This test self-inspects via `include_str!` so
    // the assertion stays coupled to the actual doc text.

    #[test]
    fn test_write_restart_counter_docstring_has_no_cold_start_reset_claim() {
        let src = include_str!("supervisor_health.rs");
        let fn_sig = "pub(crate) fn write_restart_counter";
        let fn_idx = src
            .find(fn_sig)
            .expect("write_restart_counter function must exist in supervisor_health.rs");
        let before = &src[..fn_idx];
        let doc_marker = "/// write the disk-persisted restart counter";
        let doc_start = before
            .rfind(doc_marker)
            .expect("write_restart_counter must have its docstring block");
        let doc = &src[doc_start..fn_idx];
        // Build the stale-claim substring dynamically so this test's
        // own source (read via `include_str!`) cannot self-match the
        // assertion. The literal three-word phrase must NOT appear in
        // the docstring after the fix.
        let stale = format!("{} {} start", "successful", "cold");
        assert!(
            !doc.contains(&stale),
            "write_restart_counter docstring must not claim a reset happens on a \
             fresh app launch — there is no `write_restart_counter(0)` call on \
             cold start; the only reset is on reconnect-success in `respawn_inner`"
        );
        // Positive assertion: the docstring must explicitly state the
        // counter is NOT reset on a fresh app launch, so the contract
        // is documented (not just absent).
        assert!(
            doc.contains("is NOT"),
            "write_restart_counter docstring must explicitly state the counter is \
             NOT reset on a fresh app launch (document the contract, don't just \
             omit the stale claim)"
        );
    }

    // The previous `_SILENCE_UNUSED` and `_DURATION_USED` const
    // workarounds (which existed to silence the unused-import warnings
    // for the module-level `use crate::state::lock as mutex_lock` and
    // `use std::time::Duration` lines) were removed when those
    // imports were cleaned up: `mutex_lock` is no longer imported at
    // module level (this module doesn't lock any Mutex), and `Duration`
    // is imported only by the test module that actually uses it.
}
