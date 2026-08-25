//! Process panic hook: writes the panic payload + source location to
//! BOTH stderr and the file log (via `log::error!`), with a
//! re-entrancy guard.

use super::redact::redact_pii;
use std::sync::atomic::{AtomicBool, Ordering};

//panic hook ─────────────────────────────────────────────
//
// Install a panic hook that writes the panic payload + source location
// to BOTH stderr (via `eprintln!`) and the file log (via `log::error!`).
// Without this, a panic in a Tauri command handler or sidecar WS reader
// would unwind without any breadcrumb in the file log — operators
// debugging from logs alone would have no signal that a panic occurred
// (only the React UI's generic "something went wrong" toast would
// fire). The hook chains to the previous hook (if any) so existing
// panic behavior is preserved.

/// re-entrancy guard for `install_panic_hook`'s closure.
///
/// The panic hook calls `redact_pii` (which itself may panic — e.g. on
/// a malformed state-machine transition, or via a poisoned mutex
/// inside `RotatingFileWriter`). Without this guard, a panic DURING
/// `redact_pii` would re-enter the hook → call `redact_pii` again →
/// panic again → infinite recursion → the runtime's own panic-in-hook
/// detector aborts the process with no useful breadcrumb.
///
/// The guard is `swap(true, SeqCst)` at hook entry. If the swap
/// returns `true`, we're already inside the hook — bail out (skip
/// `redact_pii` + `log::error!`) and chain directly to the previous
/// hook so the default abort path still fires. On normal hook exit we
/// reset to `false` so a LATER unrelated panic in the same process
/// still gets the full redact+log treatment (matters under
/// `panic=unwind`; under `panic=abort` the reset is moot — the process
/// is going down anyway).
pub(crate) static PANIC_HOOK_REENTRY: AtomicBool = AtomicBool::new(false);

//Install the Voice Typer panic hook ().
///
/// Writes the panic payload + `file:line:col` location to BOTH:
/// - stderr (via `eprintln!`) — so `cargo tauri dev` / `journalctl`
///   captures it even when the file logger isn't installed yet, AND
/// - the file log (via `log::error!`) — so `voice-typer.log` has the
///   same breadcrumb for post-mortem debugging.
///
/// `pub` (NOT `pub(crate)`) so `main.rs` (in the FA3a-retry follow-up
/// that wires this up) can call it from outside the `platform::logging`
/// module. Calling more than once is safe — each call replaces the
/// previous hook (chained via `take_hook` so prior behavior is not
/// lost).
///
/// # When to call
///
/// Call this AFTER `init_file_logger` so `log::error!` actually lands
/// in the rotating file (otherwise the log record is silently dropped
/// by the `log` crate's default no-op logger). Calling before
/// `init_file_logger` is still safe — the `eprintln!` half still fires.
///
//if `install_early_logger` has already been called (the new
/// standard path — `install_early_logger` is the FIRST line of
/// `main()`), then the global `log` sink is the `EarlyLogger` (a
/// stderr-only fallback) and `log::error!` from the panic hook will
/// land on stderr even before `init_file_logger` upgrades the
/// EarlyLogger to the combined file+stderr sink.
///
/// the closure installed here is guarded by
/// `PANIC_HOOK_REENTRY` (see its doc comment for the re-entrancy
/// contract). If `redact_pii` panics, the re-entered hook bails out
/// at the `swap` and chains to `prev` — no infinite recursion.
pub fn install_panic_hook() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // re-entrancy guard. If we're already inside the hook
        // (a prior frame is mid-`redact_pii` and panicked), bail out
        // immediately — do NOT call `redact_pii` or `log::error!`
        // (either could re-panic and recurse). Chain to `prev` so the
        // default abort path still fires.
        if PANIC_HOOK_REENTRY.swap(true, Ordering::SeqCst) {
            prev(info);
            return;
        }
        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "<unknown location>".to_string());
        // The payload is `&dyn Any` — try the two common shapes
        // (`&str` from `panic!("literal")` and `String` from
        // `panic!(format!(...))`). Fall back to a generic placeholder
        // for non-string payloads (e.g. `panic!(42)`).
        let payload = info
            .payload()
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| info.payload().downcast_ref::<String>().map(|s| s.as_str()))
            .unwrap_or("<non-string panic payload>");
        //redact the payload before emitting — panic
        // messages can carry arbitrary user-supplied strings (e.g. a
        // serde_json error containing a fragment of the request body,
        // which can include an email / API key) and we don't want
        // those to land in `voice-typer.log` unredacted.
        //
        // if `redact_pii` panics here, the runtime unwinds
        // (or aborts under `panic=abort`). Under unwind, the
        // `PANIC_HOOK_REENTRY` flag is still `true`, so the
        // re-entered hook bails out at the `swap` above — no
        // infinite recursion.
        let payload_redacted = redact_pii(payload);
        eprintln!("[PANIC] {} -- {}", location, payload_redacted);
        log::error!("panic at {} -- {}", location, payload_redacted);
        // Reset the guard so a later unrelated panic in the same
        // process still gets the full redact+log treatment.
        PANIC_HOOK_REENTRY.store(false, Ordering::SeqCst);
        // Chain to the previous hook so any prior behavior (e.g. the
        // default "print panic message + abort" path under
        // `panic=abort`) is preserved.
        prev(info);
    }));
}
