//! Process panic hook: payload + source location to BOTH stderr and the
//! file log (`log::error!`), with a re-entrancy guard and a host-only
//! crash-loop breaker.

use super::redact::redact_pii;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

// ── Crash-loop breaker (HOST panics only) ──────────────────────────────
//
// 5 uncaught panics inside 60s → deliberate exit(1) so autostart can
// restart a dead process instead of leaving a looping half-alive host.
// Sidecar crashes use a different counter (`restart_counter.json` +
// supervisor circuit breaker) and must NOT be merged here.

/// Panics inside [`PANIC_BREAKER_WINDOW_SECS`] that trip the breaker.
pub(crate) const PANIC_BREAKER_MAX: usize = 5;

/// Rolling window the breaker counts panics over.
pub(crate) const PANIC_BREAKER_WINDOW_SECS: u64 = 60;

/// Rolling timestamps of recent host panics (hook-serialized via
/// [`PANIC_HOOK_REENTRY`]).
pub(crate) static RECENT_PANICS: Mutex<Vec<Instant>> = Mutex::new(Vec::new());

/// Pure breaker policy (unit-testable without firing real panics).
pub(crate) fn breaker_should_exit(recent: &mut Vec<Instant>, now: Instant) -> bool {
    recent.retain(|t| now.duration_since(*t) <= Duration::from_secs(PANIC_BREAKER_WINDOW_SECS));
    recent.push(now);
    recent.len() >= PANIC_BREAKER_MAX
}

// Panic hook writes payload + location to stderr AND the file log so
// operators see a breadcrumb (not only the UI toast). Chains to the
// previous hook.

/// Re-entrancy guard: a panic inside `redact_pii` would re-enter the
/// hook → infinite recursion. `swap(true)` at entry; already-set bails
/// and chains to the previous hook. Reset to false on normal exit so a
/// later unrelated panic still gets the full treatment (unwind builds).
pub(crate) static PANIC_HOOK_REENTRY: AtomicBool = AtomicBool::new(false);

/// Install the panic hook: payload + location to stderr AND the file log.
/// Idempotent (replaces/chains the previous hook). Call after the early
/// logger so `log::error!` has a sink. Re-entrancy: see
/// `PANIC_HOOK_REENTRY`. Crash-loop breaker: after
/// [`PANIC_BREAKER_MAX`] panics in [`PANIC_BREAKER_WINDOW_SECS`], exit(1)
/// (`#[cfg(not(test))]`; policy stays unit-tested).
pub fn install_panic_hook() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        // Already inside the hook (redact/log panicked): chain to prev,
        // never recurse.
        if PANIC_HOOK_REENTRY.swap(true, Ordering::SeqCst) {
            prev(info);
            return;
        }
        let location = info
            .location()
            .map(|l| format!("{}:{}:{}", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "<unknown location>".to_string());
        // Payload is `&dyn Any`: try `&str` / `String`, else placeholder.
        let payload = info
            .payload()
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| info.payload().downcast_ref::<String>().map(|s| s.as_str()))
            .unwrap_or("<non-string panic payload>");
        // Redact: panic messages can carry user strings (emails, tokens).
        let payload_redacted = redact_pii(payload);
        eprintln!("[PANIC] {} -- {}", location, payload_redacted);
        log::error!("panic at {} -- {}", location, payload_redacted);
        // Host-only crash-loop breaker; poisoned lock just disables it.
        let breaker_tripped = match RECENT_PANICS.lock() {
            Ok(mut recent) => breaker_should_exit(&mut recent, Instant::now()),
            Err(_) => false,
        };
        // Reset so a later unrelated panic still gets the full treatment.
        PANIC_HOOK_REENTRY.store(false, Ordering::SeqCst);
        if breaker_tripped {
            let msg = format!(
                "[PANIC-BREAKER] {} panics within {}s: exiting with code 1 (crash-loop breaker)",
                PANIC_BREAKER_MAX, PANIC_BREAKER_WINDOW_SECS
            );
            log::error!("{}", msg);
            eprintln!("{}", msg);
            log::logger().flush();
            // Deliberate exit from the hook: a crash-looping host is worse
            // than a dead one. Compiled out under test (suite fires real
            // panics through this hook).
            #[cfg(not(test))]
            std::process::exit(1);
        }
        // Preserve prior hook behavior (default abort path, etc.).
        prev(info);
    }));
}
