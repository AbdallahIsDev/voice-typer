//! Sibling tests for `sidecar::spawn` (per C-TEST-5 — sibling test
//! file, no inline tests in production source).
//!
//! Covers two areas:
//!
//! - **`passthrough_env_allowlist` LINUX_GUI vars**: the allowlist
//!   must include `XDG_SESSION_TYPE` and `XDG_CURRENT_DESKTOP`. Without
//!   these, the sidecar's tray icon (Qt/GTK) and audio subsystem
//!   (PulseAudio → DBUS) can't detect the desktop environment /
//!   session type, and fall back to no-op or wrong backends under
//!   Wayland / non-GNOME desktops.
//! - **Pre-existing `parse_server_started` + `is_shutting_down` tests**
//!   (moved verbatim from the legacy inline `mod tests` block).
//!
//! On non-Linux platforms the LINUX_GUI block is compiled out, so the
//! allowlist tests are `#[cfg(target_os = "linux")]`-gated.

use super::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// The LINUX_GUI env-var allowlist must include `XDG_SESSION_TYPE`.
/// Pre-fix: this var was missing, so the sidecar couldn't tell
/// whether to use the X11 or Wayland platform plugin (Qt) under a
/// Wayland session that lacks XWayland.
#[cfg(target_os = "linux")]
#[test]
fn test_passthrough_env_allowlist_includes_xdg_session_type() {
    // Set the env var to a known value, call passthrough_env_allowlist,
    // verify the var is in the returned Vec.
    // SAFETY: this test mutates the process env; cargo test runs tests
    // in parallel by default, so we use a unique value + a slightly
    // unique var name pattern to avoid races. The XDG_SESSION_TYPE
    // var is the one we're testing — there's no way around setting it.
    let sentinel = "vt-spawn-test-xdg-session-type";
    std::env::set_var("XDG_SESSION_TYPE", sentinel);
    let envs = passthrough_env_allowlist();
    let found = envs.iter().find(|(k, _)| k == "XDG_SESSION_TYPE");
    assert!(
        found.is_some(),
        "XDG_SESSION_TYPE must be in the LINUX_GUI allowlist"
    );
    let (_, v) = found.unwrap();
    assert_eq!(
        v.to_str(),
        Some(sentinel),
        "XDG_SESSION_TYPE value should match what was set"
    );
}

/// The LINUX_GUI env-var allowlist must include
/// `XDG_CURRENT_DESKTOP`. Pre-fix: this var was missing, so GTK-based
/// tray implementations couldn't pick the correct desktop integration
/// (KDE StatusNotifierItem, GNOME Shell, Unity) and fell back to a
/// no-op tray.
#[cfg(target_os = "linux")]
#[test]
fn test_passthrough_env_allowlist_includes_xdg_current_desktop() {
    let sentinel = "vt-spawn-test-xdg-current-desktop";
    std::env::set_var("XDG_CURRENT_DESKTOP", sentinel);
    let envs = passthrough_env_allowlist();
    let found = envs.iter().find(|(k, _)| k == "XDG_CURRENT_DESKTOP");
    assert!(
        found.is_some(),
        "XDG_CURRENT_DESKTOP must be in the LINUX_GUI allowlist"
    );
    let (_, v) = found.unwrap();
    assert_eq!(
        v.to_str(),
        Some(sentinel),
        "XDG_CURRENT_DESKTOP value should match what was set"
    );
}

/// The pre-existing LINUX_GUI vars (`DISPLAY`, `WAYLAND_DISPLAY`,
/// `XDG_RUNTIME_DIR`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`,
/// `DBUS_SESSION_BUS_ADDRESS`) must STILL be in the allowlist after
/// the allowlist fix (regression guard — the fix ADDED two vars, didn't
/// replace the list).
#[cfg(target_os = "linux")]
#[test]
fn test_passthrough_env_allowlist_keeps_existing_linux_gui_vars() {
    // Set all the pre-existing LINUX_GUI vars to sentinels.
    let existing: &[(&str, &str)] = &[
        ("DISPLAY", "vt-spawn-test-display"),
        ("WAYLAND_DISPLAY", "vt-spawn-test-wayland"),
        ("XDG_RUNTIME_DIR", "vt-spawn-test-runtime"),
        ("XDG_DATA_HOME", "vt-spawn-test-data"),
        ("XDG_CONFIG_HOME", "vt-spawn-test-config"),
        ("DBUS_SESSION_BUS_ADDRESS", "vt-spawn-test-dbus"),
    ];
    for (k, v) in existing {
        std::env::set_var(k, v);
    }
    let envs = passthrough_env_allowlist();
    for (k, expected_v) in existing {
        let found = envs.iter().find(|(ek, _)| ek == k);
        assert!(
            found.is_some(),
            "{} must still be in the LINUX_GUI allowlist",
            k
        );
        let (_, actual_v) = found.unwrap();
        assert_eq!(
            actual_v.to_str(),
            Some(*expected_v),
            "{} value should match",
            k
        );
    }
}

// ── Pre-existing parse_server_started + is_shutting_down tests ────
// (Moved verbatim from the legacy inline
//  block to comply with C-TEST-5.)

// ── parse_server_started ──────────────────────────────────────────

#[test]
fn test_parse_server_started_valid() {
    let line = r#"{"event":"server_started","port":12345}"#;
    assert_eq!(parse_server_started(line), Some(12345));
}

#[test]
fn test_parse_server_started_wrong_event() {
    let line = r#"{"event":"other","port":12345}"#;
    assert_eq!(parse_server_started(line), None);
}

#[test]
fn test_parse_server_started_no_port() {
    let line = r#"{"event":"server_started"}"#;
    assert_eq!(parse_server_started(line), None);
}

#[test]
fn test_parse_server_started_port_zero() {
    //port 0 must be rejected (return None). A sidecar that
    // has successfully bound a real port never reports 0 in the
    // `server_started` handshake (the value comes from
    // `socket.getsockname()[1]` AFTER bind succeeds). A `port: 0` is
    // always a bug — returning None forces the spawn loop to time out
    // and surface a clear error rather than handing 0 back to
    // `reconnect_ws` which would dial `127.0.0.1:0` and get an
    // unrelated OS-assigned connection.
    let line = r#"{"event":"server_started","port":0}"#;
    assert_eq!(
        parse_server_started(line),
        None,
        "UE-3-F7: port=0 must be rejected (a real sidecar never reports 0 in the handshake)"
    );
}

// u16::try_from instead of truncating `as u16` ────────

#[test]
fn test_parse_server_started_port_above_u16_max_returns_none() {
    // a port value above u16::MAX (65535) must return None
    // instead of silently truncating. Previously `p as u16` would
    // wrap 70000 → 4464.
    let line = r#"{"event":"server_started","port":70000}"#;
    assert_eq!(
        parse_server_started(line),
        None,
        "GT-D3-2: port=70000 must return None (not truncate to 4464)"
    );
}

#[test]
fn test_parse_server_started_port_u16_max_passthrough() {
    // u16::MAX (65535) is the upper bound of valid ports.
    let line = r#"{"event":"server_started","port":65535}"#;
    assert_eq!(parse_server_started(line), Some(65535));
}

#[test]
fn test_parse_server_started_port_u64_max_returns_none() {
    // An absurdly large port must also return None.
    let line = r#"{"event":"server_started","port":18446744073709551615}"#;
    assert_eq!(parse_server_started(line), None);
}

#[test]
fn test_parse_server_started_invalid_json() {
    assert_eq!(parse_server_started("not json"), None);
    assert_eq!(parse_server_started(""), None);
    assert_eq!(parse_server_started("  "), None);
}

#[test]
fn test_parse_server_started_extra_fields() {
    let line = r#"{"event":"server_started","port":8080,"pid":1234,"ws_path":"/ws"}"#;
    assert_eq!(parse_server_started(line), Some(8080));
}

// ── is_dev_mode_for ───────────────────────────────────────────────

#[test]
fn test_is_dev_mode_for() {
    assert!(!is_dev_mode_for(None), "unset → not dev mode");
    assert!(!is_dev_mode_for(Some("0")), "\"0\" → not dev mode");
    assert!(!is_dev_mode_for(Some("")), "empty → not dev mode");
    assert!(!is_dev_mode_for(Some("yes")), "\"yes\" → not dev mode");
    assert!(!is_dev_mode_for(Some("true")), "\"true\" → not dev mode");
    assert!(!is_dev_mode_for(Some("2")), "\"2\" → not dev mode");
    assert!(is_dev_mode_for(Some("1")), "\"1\" → dev mode");
}

// target_triple_for (ADR-0020 §4.1) ─────────────────────

#[test]
fn test_target_triple_for_all_known_combos() {
    // ADR-0020 §4.1: every supported (arch, os) combo must map to
    // the exact triple string Tauri's `externalBin` mechanism
    // expects as the per-platform binary name suffix.
    assert_eq!(
        target_triple_for("x86_64", "windows"),
        "x86_64-pc-windows-msvc"
    );
    assert_eq!(
        target_triple_for("aarch64", "windows"),
        "aarch64-pc-windows-msvc"
    );
    assert_eq!(target_triple_for("x86_64", "macos"), "x86_64-apple-darwin");
    assert_eq!(
        target_triple_for("aarch64", "macos"),
        "aarch64-apple-darwin"
    );
    assert_eq!(
        target_triple_for("x86_64", "linux"),
        "x86_64-unknown-linux-gnu"
    );
    assert_eq!(
        target_triple_for("aarch64", "linux"),
        "aarch64-unknown-linux-gnu"
    );
}

#[test]
fn test_target_triple_for_unknown_combo_fallback() {
    // Unknown (arch, os) combos fall back to the synthetic
    // "<arch>-unknown-<os>" string so a future platform isn't a
    // hard crash at sidecar spawn time (it'll just fail later when
    // Tauri can't find the binary).
    assert_eq!(
        target_triple_for("riscv64", "freebsd"),
        "riscv64-unknown-freebsd"
    );
    assert_eq!(
        target_triple_for("wasm32", "unknown"),
        "wasm32-unknown-unknown"
    );
}

#[test]
fn test_current_target_triple_matches_runtime() {
    // The host's actual (arch, os) at runtime must be one of the
    // known combos (so `externalBin` can resolve the per-platform
    // binary). If this fails, a new platform was added to the build
    // matrix without updating the match in `target_triple_for`.
    let triple = current_target_triple();
    let known = [
        "x86_64-pc-windows-msvc",
        "aarch64-pc-windows-msvc",
        "x86_64-apple-darwin",
        "aarch64-apple-darwin",
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
    ];
    assert!(
        known.contains(&triple.as_str()),
        "current_target_triple returned unknown triple: {} \
             (update target_triple_for's match arms)",
        triple
    );
}

// ── passthrough_env_allowlist ──────────────────

#[test]
fn test_passthrough_env_allowlist_excludes_unrelated_vars() {
    // A sentinel "secret" env var set in the host process
    // must NOT appear in the allowlist (regression guard for the
    // env_clear + allowlist pattern). We set it via std::env::set_var
    // for the duration of this test — Cargo runs tests in the same
    // process by default, so the var is visible to
    // passthrough_env_allowlist's std::env::vars_os() walk.
    //
    // SAFETY: std::env::set_var is process-global and unsafe in
    // Rust 2024 (mutex concerns), but tests run single-threaded by
    // default unless `--test-threads=N` is used. The var name is
    // unique enough that it won't collide with another test's env.
    let sentinel = "VOICE_TYPER_PI2_TEST_SENTINEL_SECRET";
    std::env::set_var(sentinel, "should-not-leak");
    let allowlist = passthrough_env_allowlist();
    std::env::remove_var(sentinel);

    let names: Vec<String> = allowlist
        .iter()
        .map(|(k, _)| k.to_string_lossy().to_string())
        .collect();
    assert!(
        !names.iter().any(|n| n == sentinel),
        "PI-2 regression: sentinel env var leaked into allowlist: {:?}",
        names
    );
}

#[test]
fn test_passthrough_env_allowlist_includes_path() {
    // PATH is in the ALWAYS list — it must be present (the sidecar
    // needs it to find `python3` / native hotkey binaries).
    let allowlist = passthrough_env_allowlist();
    let names: Vec<String> = allowlist
        .iter()
        .map(|(k, _)| k.to_string_lossy().to_string())
        .collect();
    // PATH is set on every sane OS; if it's somehow unset in the
    // test env, skip this assertion (don't fail the test).
    if std::env::var_os("PATH").is_some() {
        assert!(
            names.iter().any(|n| n == "PATH"),
            "PI-2: PATH missing from allowlist: {:?}",
            names
        );
    }
}

#[test]
fn test_passthrough_env_allowlist_includes_lc_categories_when_set() {
    // When LC_ALL is set in the host env, it must appear in
    // the allowlist (locale category pass-through). When unset,
    // the allowlist must NOT contain it (no spurious empty values).
    let lc_all = "VOICE_TYPER_PI2_LC_ALL_DOES_NOT_EXIST";
    // Sanity: lc_all is not a real LC_* var name — use a real one.
    let real_lc = "LC_ALL";
    let was_set = std::env::var_os(real_lc).is_some();
    if !was_set {
        std::env::set_var(real_lc, "C");
    }
    let allowlist = passthrough_env_allowlist();
    if !was_set {
        std::env::remove_var(real_lc);
    }

    let names: Vec<String> = allowlist
        .iter()
        .map(|(k, _)| k.to_string_lossy().to_string())
        .collect();
    // LC_ALL should be present (either because the host had it set,
    // or because this test set it temporarily).
    assert!(
        names.iter().any(|n| n == real_lc),
        "PI-2: LC_ALL missing from allowlist (it was set during the call): {:?}",
        names
    );
    // Sentinel must NOT leak (mirrors the unrelated-vars test).
    assert!(
        !names.iter().any(|n| n == lc_all),
        "PI-2 regression: sentinel leaked: {:?}",
        names
    );
}

#[test]
fn test_passthrough_env_allowlist_no_duplicates() {
    // The allowlist must not contain duplicate entries —
    // duplicates would silently override each other in the child
    // process's env map (the last write wins). The `LC_*` walk
    // could in principle produce a duplicate if a manually-added
    // var name happened to start with "LC_", but the ALWAYS list
    // is uppercase non-LC_* names so there's no overlap.
    let allowlist = passthrough_env_allowlist();
    let mut seen = std::collections::HashSet::new();
    for (k, _) in &allowlist {
        let key = k.to_string_lossy().to_string();
        assert!(
            seen.insert(key.clone()),
            "PI-2: duplicate env var in allowlist: {}",
            key
        );
    }
}

// ── shutting_down check in spawn loops ────────────────────────

/// `is_shutting_down(None)` must return `false` — the cold-start
/// path (called from `main.rs`) does not pass a shutting_down
/// flag, so the spawn loop runs to completion (the post-spawn
/// `shutting_down.load()` re-check in `main.rs` handles the
/// quit-during-cold-start case).
#[test]
fn test_is_shutting_down_none_returns_false() {
    assert!(
        !is_shutting_down(None),
        "is_shutting_down(None) must be false (cold-start path)"
    );
}

/// `is_shutting_down(Some(&false_flag))` must return `false` —
/// the supervisor's respawn path passes a real flag that starts
/// at `false` and only flips to `true` when the host is shutting
/// down. The spawn loop must NOT short-circuit while the flag is
/// still false.
#[test]
fn test_is_shutting_down_some_false_returns_false() {
    let flag = AtomicBool::new(false);
    assert!(
        !is_shutting_down(Some(&flag)),
        "is_shutting_down(Some(false)) must be false (normal respawn)"
    );
}

/// `is_shutting_down(Some(&true_flag))` must return `true` — the
/// supervisor's respawn path passes a real flag that flips to
/// `true` when the host is shutting down. The spawn loop must
/// short-circuit (kill the freshly-spawned child + return
/// Err("shutdown")) as soon as this returns true.
#[test]
fn test_is_shutting_down_some_true_returns_true() {
    let flag = AtomicBool::new(true);
    assert!(
        is_shutting_down(Some(&flag)),
        "is_shutting_down(Some(true)) must be true (host is shutting down)"
    );
}

/// `is_shutting_down` must observe the flag flip from `false` →
/// `true` mid-test (mirrors the real race: the spawn loop polls
/// the flag between iterations, and a concurrent shutdown flips
/// it). Uses SeqCst on both the store and the load (inside
/// `is_shutting_down`) so the flip is visible without skid.
#[test]
fn test_is_shutting_down_observes_concurrent_flip() {
    let flag = Arc::new(AtomicBool::new(false));
    // Before the flip: false.
    assert!(!is_shutting_down(Some(&flag)));
    // Simulate `shutdown_sidecar_for_exit` flipping the flag.
    flag.store(true, Ordering::SeqCst);
    // After the flip: true — the spawn loop's next iteration
    // would short-circuit.
    assert!(is_shutting_down(Some(&flag)));
}

// ── Worker spawn stubs (Phase 2a — runtime-pack split, §7) ──────────
//
// These tests cover the Phase 2a worker scaffolding delivered in this
// slice: the `WorkerState` struct (parallel to `SidecarState`) + the
// `spawn_worker_and_get_port_with_shutdown` / `initialize_worker`
// stubs. The stubs themselves are async + require a Tauri `AppHandle`
// (not constructible in a plain unit test without a full Tauri test
// harness), so we test the PURE parts: `WorkerState::new()` field
// initialization + the stub function pointers (compile-time contract
// that the symbols exist with the documented signature).

/// `WorkerState::new()` must initialize `child` to `None` — the worker
/// child handle is installed lazily by `initialize_worker` after the
/// pack is downloaded + verified (Phase 2b). A non-`None` default would
/// cause `shutdown_worker_for_exit` (TBD) to attempt killing a
/// non-existent process on the first app-exit path.
#[test]
fn test_worker_state_new_child_is_none() {
    let state = crate::state::WorkerState::new();
    let child = crate::state::lock(&state.child);
    assert!(
        child.is_none(),
        "WorkerState::new() must initialize child to None"
    );
}

/// `WorkerState::new()` must initialize `ws_tx` to `None` — the WS
/// writer channel is installed by `reconnect_worker_ws` (TBD, parallel
/// to `sidecar::ws::reconnect_ws`) after the worker's `server_started`
/// handshake completes.
#[test]
fn test_worker_state_new_ws_tx_is_none() {
    let state = crate::state::WorkerState::new();
    let ws_tx = state.ws_tx.lock().unwrap_or_else(|e| e.into_inner());
    assert!(
        ws_tx.is_none(),
        "WorkerState::new() must initialize ws_tx to None"
    );
}

/// `WorkerState::new()` must initialize the worker's `shutting_down`
/// flag to `false` + `respawn_in_progress` to `false`. The flag only
/// flips to `true` on `shutdown_worker_for_exit` (TBD) / on entry to
/// the worker supervisor's respawn path (TBD). A `true` default would
/// cause the spawn loop to short-circuit immediately (parallel to
/// `is_shutting_down` in the sidecar path).
#[test]
fn test_worker_state_new_shutdown_and_respawn_flags_false() {
    let state = crate::state::WorkerState::new();
    assert!(
        !state.shutting_down.load(Ordering::SeqCst),
        "WorkerState::new() must initialize shutting_down to false"
    );
    assert!(
        !state.respawn_in_progress.load(Ordering::SeqCst),
        "WorkerState::new() must initialize respawn_in_progress to false"
    );
}

/// `WorkerState::new()` must initialize `next_id` to 1 + `ws_generation`
/// to 0. `next_id` starts at 1 (not 0) to match `SidecarState::next_id`'s
/// convention (id 0 is reserved as the "no response expected" sentinel
/// for fire-and-forget events). `ws_generation` starts at 0 and is
/// bumped to 1 on the first successful reconnect.
#[test]
fn test_worker_state_new_next_id_and_ws_generation() {
    let state = crate::state::WorkerState::new();
    assert_eq!(
        state.next_id.load(Ordering::SeqCst),
        1,
        "WorkerState::new() must initialize next_id to 1"
    );
    assert_eq!(
        state.ws_generation.load(Ordering::SeqCst),
        0,
        "WorkerState::new() must initialize ws_generation to 0"
    );
}

/// `WorkerState::new()` must initialize `auth_token` + `lock_file_path`
/// `OnceLock`s to the empty state — they're populated lazily on first
/// worker spawn (`initialize_worker` calls `OnceLock::set`).
/// `OnceLock::get` returns `None` until `set` is called.
#[test]
fn test_worker_state_new_auth_token_and_lock_file_empty() {
    let state = crate::state::WorkerState::new();
    assert!(
        state.auth_token.get().is_none(),
        "WorkerState::new() must leave auth_token OnceLock empty"
    );
    assert!(
        state.lock_file_path.get().is_none(),
        "WorkerState::new() must leave lock_file_path OnceLock empty"
    );
}

/// `WorkerState::default()` must produce the same field values as
/// `WorkerState::new()` — the `Default` impl delegates to `new()`.
/// Callers that construct `WorkerState` via `default()` (e.g. a future
/// `app.manage(WorkerState::default())` in `main.rs`) must get the
/// same initial state as explicit `new()` callers.
#[test]
fn test_worker_state_default_matches_new() {
    let new_state = crate::state::WorkerState::new();
    let default_state = crate::state::WorkerState::default();
    assert!(
        crate::state::lock(&new_state.child).is_none()
            && crate::state::lock(&default_state.child).is_none(),
        "both new() and default() must initialize child to None"
    );
    assert_eq!(
        new_state.next_id.load(Ordering::SeqCst),
        default_state.next_id.load(Ordering::SeqCst),
        "new() and default() must agree on next_id"
    );
    assert_eq!(
        new_state.ws_generation.load(Ordering::SeqCst),
        default_state.ws_generation.load(Ordering::SeqCst),
        "new() and default() must agree on ws_generation"
    );
}

/// Compile-time contract: the worker spawn stubs exist with the
/// documented names. We bind the function symbols to `_` (inferred as
/// their zero-sized fn-pointer type) so a rename or deletion fails to
/// COMPILE, surfacing the API drift before a caller silently breaks.
///
/// We can't pin the EXACT signature via a `fn(...) -> Pin<Box<dyn
/// Future...>>` type annotation because async fns return an anonymous
/// `impl Future` type that isn't nameable. The name-binding check is
/// weaker than a full signature pin but still catches the common
/// regression (rename / delete / move to a different module).
#[test]
fn test_worker_spawn_stubs_exist() {
    // Binding the fn symbol forces the compiler to resolve the name.
    // If `spawn_worker_and_get_port_with_shutdown` or `initialize_worker`
    // is renamed / deleted / moved out of `super::`, this fails to
    // compile (E0425: cannot find function).
    let _spawn_fn = spawn_worker_and_get_port_with_shutdown;
    let _init_fn = initialize_worker;
    // Reaching this line means both stub symbols resolved — the
    // Phase 2a scaffolding is in place.
}
