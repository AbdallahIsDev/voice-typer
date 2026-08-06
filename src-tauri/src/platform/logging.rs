//! Rotating file logger (ADR-0020 §11): 5 MB × 5 files, excludes bubble_level.
//!
//! Log files + the parent `<config_dir>/logs/` dir
//! are created with restricted POSIX permissions (`0o600` for files,
//! `0o700` for the dir) so dictated-text fragments and any PII the
//! Rust code emits are NOT world-readable on multi-user POSIX systems.
//! Mirrors the Python side's `os.umask(0o077)` + `os.chmod(log_file,
//! 0o600)` pattern in `voice_typer/server/log.py`.
//!
//! deferral: proposed split (NOT done this session)
//!
//! This file is a 2161-line monolith mixing 6 concerns: init
//! orchestration, `CombinedLogger` multi-sink dispatch, a 515-LOC PII
//! redaction engine (`redact_pii` + 5+ `try_match_*` state machines),
//! `install_panic_hook`, `EarlyLogger` + `EARLY_LOGGER_HANDLE`, and
//! `RotatingFileWriter`.
//! proposes decomposing into:
//!
//! ```text
//! src/platform/logging/
//!   mod.rs          // re-exports the public API
//!   init.rs         // init_file_logger
//!   combined.rs     // CombinedLogger
//!   redact.rs       // redact_pii + try_match_* + SECRET_KEYWORDS
//!   panic_hook.rs   // install_panic_hook
//!   early.rs        // EarlyLogger + EARLY_LOGGER_HANDLE + install_early_logger
//!   rotating.rs     // RotatingFileWriter
//!   tests/          // co-located per sub-module
//! ```


use crate::util::{ROTATE_MAX_BYTES, ROTATE_MAX_FILES, now_timestamp};
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};

// POSIX-only `OpenOptions::mode` + `Permissions::from_mode`
// trait imports. On Windows these are no-ops (the OS uses ACLs, not
// mode bits) — the `#[cfg(unix)]` blocks below gate every call site.
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

/// ADR-0020 §11: initialize a rotating file logger writing to
/// `<config_dir>/logs/voice-typer.log` (5 MB × 5 files ≈ 25 MB cap).
///
/// **Excludes `bubble_level` events** from the file log: at ~60 Hz
/// they would fill disk fast even with rotation. The Rust WS-reader
/// already coalesces them to ≤30 Hz for the UI (§9); the file path
/// drops them entirely so file logs capture events/errors, not the
/// level stream.
///
/// Replaces the prior `env_logger::Builder::init()` call — this
/// logger writes to BOTH stderr (matching the prior env_logger
/// output) AND the rotating file. If file init fails, the caller
/// should fall back to `env_logger` for stderr-only output.
///
/// # Implementation choice: hand-rolled, not `log4rs`
///
/// `log4rs` is a heavy dep (~30 transitive crates) for a feature
/// that just needs "rotate at N bytes, keep N files". This
/// hand-rolled `RotatingFileWriter` is ~80 lines and has no deps
/// beyond `log` (already required) + `std::fs`. The rotation is
/// triggered lazily on the write that crosses the size threshold
/// (not on a timer), which is fine for our write volume.
pub(crate) fn init_file_logger(config_dir: &std::path::Path) -> Result<(), String> {
    let logs_dir = config_dir.join("logs");
    std::fs::create_dir_all(&logs_dir)
        .map_err(|e| format!("create logs dir failed: {e}"))?;
    // Tighten the parent `<config_dir>/logs/` dir to
    // `0o700` on POSIX (owner rwx only — no group/other access). Mirrors
    // the Python side's `os.chmod(config_dir, 0o700)` at
    // `voice_typer/server/log.py:891-893`. Best-effort: a `chmod` failure
    // is logged but does NOT block logger init (a too-permissive dir is
    // a softening of the security posture, not a hard failure — the
    // individual log files inside still get `0o600` via `OpenOptionsExt`).
    #[cfg(unix)]
    {
        let _ = std::fs::set_permissions(
            &logs_dir,
            std::fs::Permissions::from_mode(0o700),
        );
    }
    // rename Rust's log basename to `voice-typer-rust` so the
    // final path is `<config_dir>/logs/voice-typer-rust.log`. Pre-fix
    // the basename was `voice-typer`, producing
    // `<config_dir>/logs/voice-typer.log` — the SAME basename as the
    // Python sidecar's `<config_dir>/voice-typer.log`. The two paths
    // were different (Python wrote to the config_dir root, Rust to
    // `logs/`) so they didn't actually collide, BUT the basename
    // parity was a fragile contract: a future Python change moving
    // its log into `logs/` (a reasonable cleanup) would silently
    // cause both layers to append to the same file → rotation races
    // + interleaved lines with different timestamp formats. Renaming
    // Rust's file makes the contract explicit and survives a Python
    // layout change. Mirrors the Python side's
    // `RotatingFileHandler(filename=...)` at log.py:891-893.
    let writer = RotatingFileWriter::new(logs_dir, "voice-typer-rust");
    // honor `RUST_LOG` runtime log-level override. Parsed
    // as a `log::LevelFilter` (e.g. "debug", "trace", "warn", "off").
    // Default to `Info` if the var is unset OR unparseable so a typo
    // (e.g. `RUST_LOG=debog`) doesn't silently disable all logging.
    // Both the global `log::set_max_level` AND the per-logger
    // `level_filter` are set to this value — `set_max_level` is the
    // fast-path short-circuit at the macro call site, while
    // `level_filter` is consulted inside `CombinedLogger::enabled`
    // (which `log::log!` calls as a second filter).
    //
    // fallback: if `RUST_LOG` is unset, also honor
    // the Voice Typer-specific `VOICE_TYPER_DEBUG` env var. When
    // truthy ("1", "true", "yes", case-insensitive), set the level to
    // Debug so developers get verbose logs in the file + stderr. This
    // mirrors the Python side's `env_validation.py` boolean-var
    // pattern so the Rust + Python hosts respond identically to the
    // same env var. `RUST_LOG` (the standard Rust convention) wins if
    // set; `VOICE_TYPER_DEBUG` is a fallback for users who don't know
    // about `RUST_LOG`.
    let max_level = std::env::var("RUST_LOG")
        .ok()
        .and_then(|s| s.parse::<log::LevelFilter>().ok())
        .or_else(|| {
            // RUST_LOG unset/unparseable — try VOICE_TYPER_DEBUG.
            if is_debug_env_truthy(std::env::var("VOICE_TYPER_DEBUG").ok().as_deref()) {
                Some(log::LevelFilter::Debug)
            } else {
                None
            }
        })
        .unwrap_or(log::LevelFilter::Info);
    // gate stderr output on debug builds OR `RUST_LOG_STDERR=1`.
    // Release builds with no env var skip the per-line `eprintln!`
    // syscall (saves 1 `write(2)` per log line). The env var is the
    // release-build escape hatch for operators who want stderr tailing
    // (`journalctl -u voice-typer` etc.).
    //
    // use the shared `is_truthy_env_var` helper so the truthy
    // contract ("1" / "true" / "yes", case-insensitive, trimmed) is
    // defined in exactly one place. The same helper is used by
    // `install_early_logger` and `is_debug_env_truthy`.
    let stderr_verbose_init = cfg!(debug_assertions) || is_truthy_env_var("RUST_LOG_STDERR");
    let combined = CombinedLogger {
        file_writer: Some(writer),
        level_filter: max_level,
        // `AtomicBool` so future code (e.g. a Tauri command)
        // can toggle stderr verbosity at runtime. The per-line cost
        // is a single `AtomicBool::load(Relaxed)` — same as a `bool`
        // load on x86/ARM (Relaxed loads compile to a plain MOV).
        stderr_verbose: AtomicBool::new(stderr_verbose_init),
    };

    // prefer the swap pattern when an `EarlyLogger` is already
    // installed as the process-global `log` sink (the standard path —
    // `install_early_logger` runs as the first line of `main()`).
    // `log::set_logger` can only be called ONCE per process, so we
    // can't replace the global logger; instead, we swap the
    // `CombinedLogger` into the `EarlyLogger`'s `OnceLock` so all
    // subsequent `log::*!` records delegate to the combined file+stderr
    // sink. `OnceLock::get` is a single atomic load on the hot path —
    // no mutex acquisition per log call.
    if let Some(early) = EarlyLogger::instance() {
        if early.inner.set(combined).is_err() {
            return Err(
                "init_file_logger called twice (EarlyLogger already upgraded to file sink)"
                    .to_string(),
            );
        }
        // Bump the global max-level to the resolved value (the
        // EarlyLogger was installed with `Info` as a safe default; the
        // file-logger init may have parsed `RUST_LOG=debug` etc.).
        // `set_max_level` can be called multiple times safely.
        log::set_max_level(max_level);
        return Ok(());
    }

    // Fallback: EarlyLogger was NOT installed (e.g. tests, or a host
    // entrypoint that skipped `install_early_logger`). Install the
    // `CombinedLogger` directly via `log::set_logger`. This path
    // preserves the behavior so existing tests that depend
    // on `init_file_logger` calling `set_logger` continue to compile
    // and run.
    log::set_logger(Box::leak(Box::new(combined)))
        .map_err(|_| "failed to set logger (already set?)".to_string())?;
    log::set_max_level(max_level);
    Ok(())
}

/// Host entrypoint convenience wrapper: try the rotating file logger
/// first, and if that fails (e.g. config-dir not writable), fall back
/// to a stderr-only `env_logger` sink so early startup diagnostics
/// still land somewhere visible. Both failures are surfaced to stderr
/// via `eprintln!` (the global `log` sink may not be installed yet).
///
/// Extracted from `main.rs` so the host entrypoint stays wiring-only
//(C-) — no `env_logger::Builder` plumbing inline.
///
/// # Error handling
///
/// This function NEVER panics:
/// - `init_file_logger` failure -> log to stderr, try env_logger.
/// - env_logger `try_init` failure (e.g. another logger already
///   installed) -> log to stderr, return. The host continues with NO
///   logger; all `log::*!` calls become no-ops (the `log` crate's
///   default sink is a no-op until `set_logger` is called).
pub(crate) fn init_file_logger_or_stderr_fallback(config_dir: &std::path::Path) {
    if let Err(e) = init_file_logger(config_dir) {
        eprintln!(
            "[MAIN] file logger init failed (falling back to stderr-only env_logger): {}",
            e
        );
        // Best-effort: env_logger for stderr only (no file sink).
        // `try_init` avoids panic if `log::set_logger` was already
        // called (e.g. by the EarlyLogger swap path above).
        if let Err(e2) = env_logger::Builder::from_env(
            env_logger::Env::default().default_filter_or("info"),
        )
        .format_timestamp_millis()
        .try_init()
        {
            eprintln!(
                "[MAIN] env_logger fallback ALSO failed: {} — running with NO logger; all log::*! calls will be dropped",
                e2
            );
        }
    }
}

/// shared truthy matcher for boolean environment variables.
/// Truthy values: `"1"`, `"true"`, `"yes"` (case-insensitive, after
/// trimming leading/trailing whitespace). Anything else — including
/// unset, empty, `"0"`, `"false"`, `"no"`, or a typo like `"yess"` —
/// is falsy.
///
/// This is the single source of truth for the truthy contract across
/// the Voice Typer Rust host. It is wrapped by two thin callers:
/// - `is_truthy_env_var(name)` — looks up an env var by name and
///   applies this matcher (used by both `stderr_verbose` sites).
/// - `is_debug_env_truthy(value)` — applies this matcher to a
///   caller-supplied value (kept for unit-testability without env
///   mutation).
///
/// Mirrors the Python side's `env_validation.py` boolean-var pattern
/// (pattern: `^(1|0|true|false|yes|no)$`, case-insensitive) so the
/// Rust + Python hosts respond identically to the same env var.
pub(crate) fn is_truthy_value(value: Option<&str>) -> bool {
    match value {
        Some(v) => matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes"
        ),
        None => false,
    }
}

/// env-var form of the truthy predicate. Looks up `name` in
/// the process environment and applies `is_truthy_value` to the
/// result. Unset vars are falsy (not an error). Used by both
/// `stderr_verbose` computation sites (`init_file_logger` +
/// `install_early_logger`) so the truthy contract lives in exactly
/// one place.
pub(crate) fn is_truthy_env_var(name: &str) -> bool {
    is_truthy_value(std::env::var(name).ok().as_deref())
}

//predicate form of the VOICE_TYPER_DEBUG env-var check,
/// extracted for unit testing. now delegates to the shared
/// `is_truthy_value` matcher (the same one `is_truthy_env_var` uses)
/// so the truthy contract is defined in exactly one place. Truthy
/// values: "1", "true", "yes" (case-insensitive). Anything else
/// (including unset / empty) is falsy → production Info-level logging.
///
/// Mirrors the Python side's `env_validation.py` boolean-var pattern
/// (pattern: `^(1|0|true|false|yes|no)$`, case-insensitive) so the
/// Rust + Python hosts respond identically to the same env var.
pub(crate) fn is_debug_env_truthy(value: Option<&str>) -> bool {
    is_truthy_value(value)
}

/// Combined stderr + rotating-file logger. Replaces `env_logger` so
/// we can add the file sink without a multiplexer crate.
pub(crate) struct CombinedLogger {
    pub(crate) file_writer: Option<RotatingFileWriter>,
    pub(crate) level_filter: log::LevelFilter,
    //cached predicate — `true` if log lines should ALSO be
    /// written to stderr. Computed ONCE at logger init from
    /// `cfg!(debug_assertions)` (always true in debug builds) OR the
    /// `RUST_LOG_STDERR=1` env var (opt-in for release builds via
    /// `RUST_LOG_STDERR=1 cargo tauri dev`). The prior code called
    /// `eprintln!` unconditionally — a wasted `write(2)` syscall per
    /// log line in release builds where stderr is typically
    /// `/dev/null` (the Tauri app's stdout/stderr are not connected
    /// to a terminal in `cargo tauri build` release binaries).
    /// Caching the predicate here means the per-line cost is a single
    /// bool load, not an `env::var` lookup.
    ///
    // changed from `bool` to `AtomicBool` so the predicate
    /// becomes runtime-toggleable. Future code (e.g. a Tauri command
    /// that flips stderr verbosity without restarting the host) can
    /// `store(true/false, Ordering::Relaxed)` at any time. The per-
    /// line `log()` path uses `load(Ordering::Relaxed)`, which on
    /// x86/ARM compiles to a plain MOV — same cost as a `bool` load.
    /// `Relaxed` is correct: we don't need cross-thread ordering for
    /// a boolean flag whose only consumer is the same thread that
    /// calls `eprintln!`.
    pub(crate) stderr_verbose: AtomicBool,
}

impl log::Log for CombinedLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        // redact PII from the log message before writing
        // to file or stderr. The redactor is intentionally MINIMAL —
        // it covers the highest-signal patterns (Bearer/Token/sk- prefix
        // tokens, user:pass@host URL credentials, basic email addresses)
        // using std-only substring scanning. The Python side's
        // `_FAST_TRIGGER` shortcut (skip if no trigger char present) is
        // mirrored here so the common-case cost is a single
        // `str::contains('@')` / `str::contains('+')` / etc. scan.
        let raw_msg = record.args().to_string();
        let msg = redact_pii(&raw_msg);
        let ts = now_timestamp();
        // include `file:line` so operators can jump
        // directly to the source location from a log line. Both
        // `record.file()` and `record.line()` return `Option` (they
        // are `None` for log records emitted from non-`#[track_caller]`
        // paths or release builds with debuginfo stripped); fall back
        // to "?" / 0 so the format string still renders cleanly.
        let line = format!(
            "{} {:5} {} {}:{} -- {}",
            ts,
            record.level(),
            record.target(),
            record.file().unwrap_or("?"),
            record.line().unwrap_or(0),
            msg
        );
        // gate the per-line `eprintln!` on the cached
        // `stderr_verbose` flag (computed once at logger init from
        // `cfg!(debug_assertions)` OR `RUST_LOG_STDERR=1`). The prior
        // unconditional `eprintln!` was a wasted `write(2)` syscall
        // per log line in release builds where stderr is /dev/null.
        // Always emit in debug builds so `cargo tauri dev` shows live
        // logs in the launching terminal; opt-in for release builds.
        //
        // `AtomicBool::load(Relaxed)` — runtime-toggleable
        // without restart. Same per-line cost as a `bool` load.
        if self.stderr_verbose.load(Ordering::Relaxed) {
            eprintln!("{}", line);
        }
        // ADR-0020 §11: exclude `bubble_level` from the file log
        // (60 Hz would fill disk fast even with rotation). Match by a
        // SPECIFIC message prefix (`[WS-READER] bubble_level event`)
        // rather than a broad `msg.contains("bubble_level")` substring
        // — the old substring filter risked false-positives on unrelated
        // log lines that happened to mention "bubble_level".
        // the WS reader doesn't currently log bubble_level
        // events to the file (they go via `app.emit()` to the webview,
        // not `log::*!`), so this filter is defensive.
        //
        // preserve WARNING+ records even when they start with
        // the bubble_level prefix. Pre-fix this dropped ANY record
        // matching the prefix regardless of level — a future
        // `log::error!("[WS-READER] bubble_level event handler
        // crashed: ...")` would be SILENTLY LOST from the file log.
        // Mirrors Python's `_BubbleLevelExclusionFilter` short-circuit
        // at `log.py:216-219`:
        // `if record.levelno >= logging.WARNING: return True`
        // (filter returning True = "do NOT filter out" in Python's
        // logging API). The Rust equivalent is the level-guarded
        // early-skip below.
        if let Some(writer) = &self.file_writer {
            // The `log` crate orders levels by severity: Error(1) <
            // Warn(2) < Info(3) < Debug(4) < Trace(5). So "WARNING+"
            // (preserve Error/Warn) is `record.level() <= Warn`.
            // `record.level() >= Info` therefore matches ONLY the
            // low-severity records (Info/Debug/Trace) that the ADR
            // wanted excluded from the file log. Pre-fix this used
            // `<=` which inverted the guard — it dropped ERROR and
            // WARN bubble_level records too (the exact records the
            // FR-33 level-guard was added to preserve).
            let is_filtered_bubble = record.level() >= log::Level::Info
                && msg.starts_with("[WS-READER] bubble_level event");
            if !is_filtered_bubble {
                let _ = writer.write_line(&line);
                // Flush the BufWriter immediately for Warn+ records so
                // an impending crash (the most likely producer of
                // `log::error!`) does NOT leave the diagnostic line
                // stranded in the 8 KB in-memory buffer when the
                // process aborts. Info/Debug/Trace records stay
                // buffered — they're high-volume and the periodic /
                // drop-flush paths are sufficient. Mirrors the
                // Python side's `logging.Handler.flush()` call inside
                // `emit()` for WARNING+ records (log.py:216-219).
                //
                // The `log` crate orders levels by severity:
                // Error(1) < Warn(2) < Info(3) < Debug(4) < Trace(5).
                // `<= Warn` matches Error + Warn.
                if record.level() <= log::Level::Warn {
                    let _ = writer.flush();
                }
            }
        }
    }

    fn flush(&self) {
        if let Some(writer) = &self.file_writer {
            let _ = writer.flush();
        }
    }
}

// PII redaction ────────────────────────────────────
//
// PII redactor for log output. Ports the Python `PIIRedactionFilter`
// pattern set to Rust using std-only substring scanning (no `regex`
// crate dependency). Covered patterns (checked at each byte position
// in the order listed; first match wins):
//
// Prefix / keyword patterns (Python `_KEY_PATTERNS` + `_FLAG_KEY_PATTERNS`):
// - Bearer <token>           → `Bearer ***`   (Python `_KEY_PATTERNS[0]`)
// - Token <token>            → `Token ***`    (Python `_KEY_PATTERNS[1]`)
// - sk-<token> (8+ chars)    → `sk-***`       (Python `_KEY_PATTERNS[2]`)
// - gsk_<token> (8+ chars)   → `gsk_***`      (Python `_KEY_PATTERNS[3]`)
// keyword=value          → `--keyword=***` (Python `_FLAG_VALUE_PATTERN`, )
// keyword value          → `--keyword ***` (Python `_FLAG_VALUE_PATTERN`, )
// keyword=value            → `keyword=***`   (Python `_BARE_KEY_VALUE_PATTERN`, )
//
// PII patterns (Python `_PATTERNS`):
// - user:pass@host      → `***@host`     (Python `redact_url`)
// - <local>@<dom>.<tld> → `[EMAIL]`      (Python `_PATTERNS[0]`)
// - IBAN                → `[IBAN]`       (Python `_PATTERNS[1]`)
// - Intl phone (+cc…)   → `[PHONE]`      (Python `_PATTERNS[3]`)
// - US phone (3-3-4)    → `[PHONE]`      (Python `_PATTERNS[2]`)
// - SSN (3-2-4)         → `[SSN]`        (Python `_PATTERNS[4]`)
// - Credit card (4-4-4-4) → `[CC]`      (Python `_PATTERNS[5]`)
//
//Catch-all (Python `_KEY_PATTERNS[4]`, ):
// - 20+ char alphanumeric run → `***`   (`\b[A-Za-z0-9_\-]{20,}\b`)
//
// The fast path mirrors Python's `_FAST_TRIGGER` (security.py:63):
// `[@+]|\d{3,}|Bearer|Token|sk-|key=|[A-Za-z0-9_\-]{20,}`
// We check the substrings directly (no regex), a 3+ consecutive
// digit scan for the numeric patterns, and a 20+ char alphanumeric
// run scan for the catch-all. A miss on ALL triggers lets us return
// the input unchanged without the per-byte scan loop.

/// Redact PII patterns from the input string. Returns the input unchanged
/// (but as a newly-allocated `String`) if no trigger character is
/// present. Otherwise returns a new `String` with each matched pattern
/// replaced by its redaction placeholder.
pub(crate) fn redact_pii(input: &str) -> String {
    // Fast path: skip the whole pass if no trigger is present. Mirrors
    // the Python side's `_FAST_TRIGGER` shortcut (security.py:63). Each
    // trigger is a *necessary* condition for at least one downstream
    // pattern:
    // `@`      — email / URL credentials
    // `+`      — international phone (`+<cc>…`)
    // `Bearer` — bearer token
    // `Token`  — token keyword
    // `sk-`    — OpenAI-style key
    // `gsk_`   — Groq-style key
    // `://`    — URL credentials (`https://user:pass@host`)
    // 3+ consecutive ASCII digits — US phone, SSN, CC, IBAN (BBAN
    // portion always contains 3+ consecutive digits)
    // `key=`   — bare `key=value` flag form (Python `_FAST_TRIGGER`);
    // also a substring of `--key=`, `--api_key=`, `--api-key=`, and
    // any `--<keyword>=` flag whose keyword ends in `key`. Case-
    //sensitive (mirrors Python).
    // 20+ char alphanumeric run — the generic 20+ char bare-token
    // pattern (`\b[A-Za-z0-9_\-]{20,}\b`, Python `_KEY_PATTERNS[4]`).
    //
    if !input.contains('@')
        && !input.contains('+')
        && !input.contains("Bearer")
        && !input.contains("Token")
        && !input.contains("sk-")
        && !input.contains("gsk_")
        && !input.contains("://")
        && !input.contains("key=")
        && !has_3plus_consecutive_ascii_digits(input)
        && !has_20plus_alphanumeric_run(input)
    {
        return input.to_string();
    }

    let mut out = String::with_capacity(input.len());
    let mut i = 0;
    while i < input.len() {
        let rest = &input[i..];

        // 1. `Bearer <token>` — token runs until a char that's NOT in
        // the Python `_KEY_PATTERNS[0]` charset `[A-Za-z0-9_\-\.=]`.
        // Pre-fix the token ran until whitespace, which consumed
        // trailing punctuation (e.g. the comma in `Bearer abc123,`)
        // and broke `test_redact_pii_multiple_patterns_in_one_line`.
        if let Some(stripped) = rest.strip_prefix("Bearer ") {
            let token_len = stripped
                .find(|c: char| !is_api_token_char(c))
                .unwrap_or(stripped.len());
            out.push_str("Bearer ***");
            i += "Bearer ".len() + token_len;
            continue;
        }
        // 2. `Token <token>` — same charset as Bearer.
        if let Some(stripped) = rest.strip_prefix("Token ") {
            let token_len = stripped
                .find(|c: char| !is_api_token_char(c))
                .unwrap_or(stripped.len());
            out.push_str("Token ***");
            i += "Token ".len() + token_len;
            continue;
        }
        // 3. `sk-<token>` — token runs until non-alphanumeric / non
        // dash / non underscore (the typical API-key charset).
        if let Some(stripped) = rest.strip_prefix("sk-") {
            let token_len = stripped
                .find(|c: char| !c.is_ascii_alphanumeric() && c != '-' && c != '_')
                .unwrap_or(stripped.len());
            if token_len >= 8 {
                // Only redact if the token looks like a real key
                // (>= 8 chars after the prefix); otherwise leave it
                // alone (e.g. `sk-1234` in a model name).
                out.push_str("sk-***");
                i += "sk-".len() + token_len;
                continue;
            }
        }
        // 4. `gsk_<token>` — Groq-style API key. Same charset and
        // length threshold as `sk-` (8+ chars after the prefix).
        if let Some(stripped) = rest.strip_prefix("gsk_") {
            let token_len = stripped
                .find(|c: char| !c.is_ascii_alphanumeric() && c != '-' && c != '_')
                .unwrap_or(stripped.len());
            if token_len >= 8 {
                out.push_str("gsk_***");
                i += "gsk_".len() + token_len;
                continue;
            }
        }

        //5. Flag-form and bare-keyword secret patterns (). Mirrors
        // Python's `_FLAG_VALUE_PATTERN` (Pattern A: `--keyword=value`
        // or `--keyword value`) and `_BARE_KEY_VALUE_PATTERN`
        // (Pattern B: `keyword=value`). Checked AFTER the prefix
        // patterns (Bearer/Token/sk-/gsk_) so those specific prefixes
        // win, but BEFORE the PII patterns (email/IBAN/phone/SSN/CC)
        // so a secret-bearing flag value containing `@` or digits
        // (e.g. `token=alice@example.com`) is redacted as a secret
        // (`token=***`) rather than as PII (`token=[EMAIL]`).
        //
        // See `SECRET_KEYWORDS` below for the keyword list and
        // `try_match_flag_or_bare_key` for the matching logic.
        if let Some((total_len, prefix_len)) = try_match_flag_or_bare_key(rest, input, i) {
            out.push_str(&rest[..prefix_len]);
            out.push_str("***");
            i += total_len;
            continue;
        }

        // 6. `user:pass@host` — strip everything up to and including
        // the `@` IF the prefix contains a `:` (the URL-credential
        // marker). The host part is preserved.
        if rest.contains('@') {
            if let Some(at_pos) = rest.find('@') {
                let prefix = &rest[..at_pos];
                if prefix.contains(':') && !prefix.contains(' ') && !prefix.is_empty() {
                    out.push_str("***@");
                    i += at_pos + 1;
                    continue;
                }
                // 7. Basic email: `<name>@<domain>.<tld>`. We require
                // a `.` in the domain part (after the `@`) to avoid
                // false-positives on `user@host` (no TLD).
                let after_at = &rest[at_pos + 1..];
                let domain_end = after_at
                    .find(|c: char| c.is_whitespace() || c == ',' || c == ';')
                    .unwrap_or(after_at.len());
                let domain = &after_at[..domain_end];
                if domain.contains('.') && !domain.starts_with('.') {
                    let local_valid = !prefix.is_empty()
                        && prefix
                            .chars()
                            .all(|c| c.is_alphanumeric() || c == '.' || c == '-' || c == '_' || c == '+');
                    if local_valid {
                        out.push_str("[EMAIL]");
                        i += at_pos + 1 + domain_end;
                        continue;
                    }
                }
            }
        }

        // 8. IBAN: 2 uppercase ASCII letters + 2 digits + 10-30 BBAN
        // chars (uppercase letters or digits). MUST be checked
        // before phone/SSN/CC so the digit portion of an IBAN
        // (e.g. `GB82WEST12345698765432`) isn't partially matched
        // as a phone number. Mirrors Python `_PATTERNS[1]`:
        // `\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b`.
        if let Some(iban_len) = try_match_iban(rest, input, i) {
            out.push_str("[IBAN]");
            i += iban_len;
            continue;
        }

        // 9. International phone: `+` followed by country code (1-3
        // digits) and subscriber number. Mirrors Python
        // `_PATTERNS[3]`: `\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b`.
        // Checked before US phone so `+1 (415) 555-2671` matches
        // the international pattern (not the US pattern on the
        // `415 555 2671` tail).
        if rest.starts_with('+') {
            if let Some(phone_len) = try_match_intl_phone(rest, input, i) {
                out.push_str("[PHONE]");
                i += phone_len;
                continue;
            }
        }

        // 10. US phone: 3-3-4 digits with optional `-` or `.`
        // separators. Mirrors Python `_PATTERNS[2]`:
        // `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`.
        if let Some(phone_len) = try_match_us_phone(rest, input, i) {
            out.push_str("[PHONE]");
            i += phone_len;
            continue;
        }

        // 11. SSN: 3-2-4 digits with `-` separators (the canonical
        // `123-45-6789` form). Mirrors Python `_PATTERNS[4]`:
        // `\b\d{3}-\d{2}-\d{4}\b`. The dashes are REQUIRED (not
        // optional) so a 9-digit run like `123456789` is NOT
        // matched as an SSN (matches Python behaviour).
        if let Some(ssn_len) = try_match_ssn(rest, input, i) {
            out.push_str("[SSN]");
            i += ssn_len;
            continue;
        }

        // 12. Credit card: 4-4-4-4 digits with optional `-` or space
        // separators. Mirrors Python `_PATTERNS[5]`:
        // `\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b`.
        if let Some(cc_len) = try_match_credit_card(rest, input, i) {
            out.push_str("[CC]");
            i += cc_len;
            continue;
        }

        //13. 20+ char alphanumeric catch-all (). Mirrors Python's
        // `_KEY_PATTERNS[4]`: `\b[A-Za-z0-9_\-]{20,}\b`. Catches
        // bare GitLab/GitHub/Slack PATs with no prefix. Checked LAST
        // (after all PII patterns) so an IBAN like
        // `GB82WEST12345698765432` (20 chars) is redacted as `[IBAN]`
        // rather than `***`.
        if let Some(run_len) = try_match_long_alphanumeric_run(rest, input, i) {
            out.push_str("***");
            i += run_len;
            continue;
        }

        // No pattern matched at this position — copy the char and
        //advance by its UTF-8 length. : use `if let Some` instead
        // of `unwrap()` — defense-in-depth. The loop invariant
        // (`i < input.len()`) guarantees `rest` is non-empty, so the
        // `else { break; }` branch is unreachable today. But a future
        // refactor that changes the loop bound (e.g. off-by-one) would
        // turn `unwrap()` into a panic inside the logger — and logger
        // panics are self-reinforcing (the panic hook calls `log::error!`
        // which calls `redact_pii` which panics again → abort). The
        // `if let Some` form degrades gracefully instead.
        if let Some(ch) = rest.chars().next() {
            out.push(ch);
            i += ch.len_utf8();
        } else {
            break;
        }
    }
    out
}

/// Return true if `input` contains 3+ consecutive ASCII digit bytes.
/// Mirrors the `\d{3,}` alternative in Python's `_FAST_TRIGGER`. Used
/// only as a fast-path gate — the actual numeric patterns (phone, SSN,
/// CC, IBAN) require specific digit groupings, so a hit here does NOT
/// mean a redaction will occur.
fn has_3plus_consecutive_ascii_digits(input: &str) -> bool {
    let mut run = 0u8;
    for b in input.bytes() {
        if b.is_ascii_digit() {
            run += 1;
            if run >= 3 {
                return true;
            }
        } else {
            run = 0;
        }
    }
    false
}

/// Predicate matching the Python `_KEY_PATTERNS` charset
/// `[A-Za-z0-9_\-\.=]` used by the `Bearer` / `Token` prefix patterns.
/// Used by `redact_pii` to find the end of a bearer/token value without
/// consuming trailing punctuation (commas, semicolons, quotes) that
/// Python's regex would leave alone.
fn is_api_token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.' || c == '='
}

//flag-form + bare-keyword + 20+ char catch-all helpers ────────

/// Secret-bearing keywords for the flag-form and bare-keyword patterns.
/// Mirrors Python's `_SECRET_KEYWORDS` in `voice_typer/server/_secrets.py:88-115`,
//plus `bearer` and `credential` ( task-specified additions not in
/// Python's list but unambiguously secret-bearing).
///
/// **Order matters**: most-specific first, `key` last — so `api_key=`
/// wins over `key=` when both could match at the same position. Python's
/// regex alternation is leftmost-first (tries each alternative in order),
/// so we iterate in the same order. The `\b` word boundary (checked in
/// `try_match_flag_or_bare_key` for the bare-keyword form) prevents
/// matching inside larger words like `monkey=` or `hotkey=`.
const SECRET_KEYWORDS: &[&str] = &[
    "token",
    "apikey",
    "api_key",
    "api-key",
    "secret",
    "password",
    "passwd",
    "pwd",
    "auth",
    "authorization",
    "authentication",
    "access_token",
    "access-token",
    "refreshtoken",
    "refresh_token",
    "refresh-token",
    "client_secret",
    "client-secret",
    "private_key",
    "private-key",
    //task-specified additions (not in Python's _SECRET_KEYWORDS
    // but unambiguously secret-bearing).
    "bearer",
    "credential",
    // `key` MUST be last — Python orders from most-specific to least-
    // specific so `api_key=` / `access_token=` / etc. win over `key=`.
    "key",
];

/// Return true if `input` contains a run of 20+ consecutive chars from
/// `[A-Za-z0-9_\-]`. Mirrors the `[A-Za-z0-9_\-]{20,}` alternative in
/// Python's `_FAST_TRIGGER`. Used only as a fast-path gate — the actual
/// 20+ char catch-all pattern (`try_match_long_alphanumeric_run`) also
/// requires word boundaries (`\b`), so a hit here does NOT mean a
/// redaction will occur.
fn has_20plus_alphanumeric_run(input: &str) -> bool {
    let mut run: u32 = 0;
    for b in input.bytes() {
        if b.is_ascii_alphanumeric() || b == b'_' || b == b'-' {
            run += 1;
            if run >= 20 {
                return true;
            }
        } else {
            run = 0;
        }
    }
    false
}

/// Predicate for the 20+ char run charset `[A-Za-z0-9_\-]` (includes
/// `-`, unlike `is_python_word_char`).
fn is_long_run_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'-'
}

/// Predicate for Python's word-char set `[A-Za-z0-9_]` (excludes `-`).
/// Used for `\b` word-boundary checks mirroring Python's regex semantics.
fn is_python_word_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

/// Try to match a flag-form or bare-keyword secret at the start of
/// `rest`. Mirrors Python's `_FLAG_VALUE_PATTERN` (Pattern A:
/// `--keyword=value` or `--keyword value`) and `_BARE_KEY_VALUE_PATTERN`
/// (Pattern B: `keyword=value`) from `voice_typer/server/_secrets.py`.
///
/// Returns `Some((total_len, prefix_len))` on success:
/// - `total_len` — total byte length of the match (to advance the main
///   loop index `i`).
/// - `prefix_len` — byte length of the prefix to preserve in the output
///   (e.g. `--token=`, `--token ` with whitespace, or `token=`). The
///   caller outputs `&rest[..prefix_len]` + `"***"`.
///
/// Matching is case-insensitive (mirrors Python's `(?i)` flag on both
/// patterns). The value (`[^\s=]+` in Python) runs until the next
/// whitespace or `=` char and must be at least 1 char (empty values are
/// NOT redacted — `--token=` with nothing after the `=` is left alone).
///
/// Pattern A (flag form) requires `--` prefix; no word boundary is
/// required before `--` (mirrors Python's `_FLAG_VALUE_PATTERN` which
/// has no `\b`). Pattern B (bare-keyword) requires `\b` before the
/// keyword (mirrors Python's `_BARE_KEY_VALUE_PATTERN` which starts
/// with `\b`) so `monkey=abc` does NOT match `key=abc`.
fn try_match_flag_or_bare_key(rest: &str, input: &str, pos: usize) -> Option<(usize, usize)> {
    // Pattern A: `--keyword=value` or `--keyword value`.
    // No `\b` required before `--` (Python's _FLAG_VALUE_PATTERN has none).
    if let Some(after_dashes) = rest.strip_prefix("--") {
        for &kw in SECRET_KEYWORDS {
            // Compare bytes (not str slices) to avoid UTF-8 panic when
            // ``kw.len()`` lands inside a multi-byte char in ``after_dashes``.
            // The redaction engine runs inside the panic hook, so a
            // str-slice panic here would be self-reinforcing.
            if after_dashes.len() >= kw.len()
                && after_dashes.as_bytes()[..kw.len()].eq_ignore_ascii_case(kw.as_bytes())
            {
                let after_kw = &after_dashes[kw.len()..];
                // Delimiter: `=` (equals form) or one-or-more whitespace
                // chars (space form). Mirrors Python's `(?:=|\s+)`.
                if let Some(value_rest) = after_kw.strip_prefix('=') {
                    // `--keyword=value` form.
                    let value_len = value_rest
                        .find(|c: char| c.is_whitespace() || c == '=')
                        .unwrap_or(value_rest.len());
                    if value_len > 0 {
                        let prefix_len = 2 + kw.len() + 1; // `--` + kw + `=`
                        let total = prefix_len + value_len;
                        return Some((total, prefix_len));
                    }
                } else if let Some(first_non_ws) =
                    after_kw.bytes().position(|b| !b.is_ascii_whitespace())
                {
                    // `--keyword value` form. `\s+` requires at least one
                    // whitespace char before the value.
                    if first_non_ws > 0 {
                        let value_rest = &after_kw[first_non_ws..];
                        let value_len = value_rest
                            .find(|c: char| c.is_whitespace() || c == '=')
                            .unwrap_or(value_rest.len());
                        if value_len > 0 {
                            // `--` + kw + whitespace separator
                            let prefix_len = 2 + kw.len() + first_non_ws;
                            let total = prefix_len + value_len;
                            return Some((total, prefix_len));
                        }
                    }
                }
            }
        }
    }
    // Pattern B: `keyword=value` (no `--` prefix). Requires `\b` before
    // the keyword (mirrors Python's `\b` in `_BARE_KEY_VALUE_PATTERN`).
    if !word_boundary_before(input, pos) {
        return None;
    }
    for &kw in SECRET_KEYWORDS {
        // Compare bytes (not str slices) to avoid UTF-8 panic when
        // ``kw.len()`` lands inside a multi-byte char in ``rest``.
        // The redaction engine runs inside the panic hook, so a
        // str-slice panic here would be self-reinforcing
        // (panic → log::error! → redact_pii → panic → abort).
        if rest.len() >= kw.len() + 1
            && rest.as_bytes()[..kw.len()].eq_ignore_ascii_case(kw.as_bytes())
            && rest.as_bytes()[kw.len()] == b'='
        {
            let value_rest = &rest[kw.len() + 1..];
            let value_len = value_rest
                .find(|c: char| c.is_whitespace() || c == '=')
                .unwrap_or(value_rest.len());
            if value_len > 0 {
                let prefix_len = kw.len() + 1; // kw + `=`
                let total = prefix_len + value_len;
                return Some((total, prefix_len));
            }
        }
    }
    None
}

/// Try to match a 20+ char alphanumeric run at the start of `rest`.
/// Mirrors Python's `_KEY_PATTERNS[4]`: `\b[A-Za-z0-9_\-]{20,}\b`.
/// Catches bare GitLab/GitHub/Slack PATs with no prefix.
///
/// Returns the match length (in bytes) if the pattern matches, or
/// `None` otherwise. Word boundaries (`\b`) are checked at both ends
/// using Python's exact `\b` semantics: a boundary holds iff the
/// word-ness (`[A-Za-z0-9_]`, excluding `-`) differs between the
/// adjacent chars (or start/end of string).
///
/// The match starts at a word char (`[A-Za-z0-9_]`, not `-`): if the
/// first char of the run were `-`, `\b` at the start would require the
/// prev char to be a word char — but then the run could have started
/// earlier (at that prev word char) and we'd have already matched at
/// the earlier position in the single-pass loop. So restricting the
/// start to word chars is correct and avoids the `\b` edge case.
///
/// If the greedy run ends with `-` (non-word), `\b` at the end does NOT
/// hold (both sides non-word). We backtrack (shrink M) until we find a
/// match length where `\b` holds at `pos + M`. This mirrors Python's
/// regex backtracking on `\b[A-Za-z0-9_\-]{20,}\b`.
fn try_match_long_alphanumeric_run(rest: &str, input: &str, pos: usize) -> Option<usize> {
    let bytes = rest.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    // The match must start at a word char ([A-Za-z0-9_]) for \b to hold
    // at `pos` (when the prev char is non-word or start of string — the
    // common case handled by the single-pass loop).
    if !is_python_word_char(bytes[0]) {
        return None;
    }
    // Count the greedy run of [A-Za-z0-9_\-] chars starting at pos.
    let mut greedy_len: usize = 1; // bytes[0] is already a word char
    while greedy_len < bytes.len() && is_long_run_char(bytes[greedy_len]) {
        greedy_len += 1;
    }
    if greedy_len < 20 {
        return None;
    }
    // Check \b at pos: prev char must be non-word (or start of string).
    // Combined with the `is_python_word_char(bytes[0])` check above,
    // this gives the correct Python \b semantics at the start.
    if !word_boundary_before(input, pos) {
        return None;
    }
    // Find the largest M with 20 <= M <= greedy_len such that \b holds
    // at pos+M. \b at pos+M holds iff word-ness differs between
    // input[pos+M-1] and input[pos+M] (or end of string). We scan
    // backwards from greedy_len (the greedy match) — for typical inputs
    // the run ends with a word char, so the first check succeeds and we
    // return immediately (O(1)).
    let mut m = greedy_len;
    while m >= 20 {
        let prev_word = is_python_word_char(input.as_bytes()[pos + m - 1]);
        let cur_word = pos + m < input.len() && is_python_word_char(input.as_bytes()[pos + m]);
        if prev_word != cur_word {
            return Some(m);
        }
        if m == 20 {
            break;
        }
        m -= 1;
    }
    None
}

/// Word-boundary check mirroring Python's `\b`. Returns true if the
/// byte at `pos - 1` is NOT an ASCII word char (`[A-Za-z0-9_]`), or if
/// `pos == 0`. We use byte indexing (not `chars()`) because all the
/// patterns that call this are ASCII-only — a multi-byte UTF-8 lead
/// byte (>= 0x80) is never an ASCII word char, so the check is sound
/// even when `pos` lands just after a non-ASCII character.
fn word_boundary_before(input: &str, pos: usize) -> bool {
    if pos == 0 {
        return true;
    }
    let prev = input.as_bytes()[pos - 1];
    !prev.is_ascii_alphanumeric() && prev != b'_'
}

/// Word-boundary check after a match. Returns true if the byte at
/// `pos` is NOT an ASCII word char, or if `pos >= input.len()`.
fn word_boundary_after(input: &str, pos: usize) -> bool {
    if pos >= input.len() {
        return true;
    }
    let next = input.as_bytes()[pos];
    !next.is_ascii_alphanumeric() && next != b'_'
}

/// Try to match an IBAN at the start of `rest`. Returns the match
/// length (in bytes) if the pattern matches and word boundaries are
/// satisfied, or `None` otherwise. The pattern is `[A-Z]{2}\d{2}[A-Z0-9]{10,30}`
/// (2 country letters + 2 check digits + 10-30 BBAN chars).
fn try_match_iban(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    // Need at least 2 letters + 2 digits + 10 BBAN = 14 bytes.
    if bytes.len() < 14 {
        return None;
    }
    if !bytes[0].is_ascii_uppercase() || !bytes[1].is_ascii_uppercase() {
        return None;
    }
    if !bytes[2].is_ascii_digit() || !bytes[3].is_ascii_digit() {
        return None;
    }
    let mut bban_len = 0usize;
    let max_bban = (bytes.len() - 4).min(30);
    for i in 4..4 + max_bban {
        if bytes[i].is_ascii_uppercase() || bytes[i].is_ascii_digit() {
            bban_len += 1;
        } else {
            break;
        }
    }
    if bban_len < 10 {
        return None;
    }
    let total = 4 + bban_len;
    if !word_boundary_after(input, pos + total) {
        return None;
    }
    Some(total)
}

/// Try to match a US phone number at the start of `rest`: 3-3-4 digits
/// with optional `-` or `.` separators between groups. Pattern:
/// `\d{3}[-.]?\d{3}[-.]?\d{4}`. Word boundaries required on both ends.
fn try_match_us_phone(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    let mut idx = 0usize;
    let group_sizes = [3usize, 3, 4];
    for (g, &expected) in group_sizes.iter().enumerate() {
        // Optional separator before groups 1 and 2 (not group 0).
        if g > 0 {
            if idx < bytes.len() && (bytes[idx] == b'-' || bytes[idx] == b'.') {
                idx += 1;
            }
        }
        let mut digits = 0usize;
        while idx < bytes.len() && bytes[idx].is_ascii_digit() && digits < expected {
            idx += 1;
            digits += 1;
        }
        if digits != expected {
            return None;
        }
    }
    if !word_boundary_after(input, pos + idx) {
        return None;
    }
    Some(idx)
}

/// Try to match an international phone at the start of `rest` (which
/// must begin with `+`). Pattern: `\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}`.
/// Word boundary required AFTER the match (the `+` prefix already
/// guarantees a non-word char before).
fn try_match_intl_phone(rest: &str, input: &str, pos: usize) -> Option<usize> {
    debug_assert!(rest.starts_with('+'));
    // The `+` is a non-word char, so word_boundary_before is implied
    // (the previous char, if any, can't be a word char that joins to `+`).
    // `pos` is used below in the `word_boundary_after(input, pos + idx)`
    // check at the end of this function.
    let bytes = rest.as_bytes();
    let mut idx = 1usize; // skip the `+`

    // Country code: 1-3 digits.
    let cc_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - cc_start < 3 {
        idx += 1;
    }
    if idx == cc_start {
        return None;
    }

    // Optional separator (` ` or `-`).
    if idx < bytes.len() && (bytes[idx] == b' ' || bytes[idx] == b'-') {
        idx += 1;
    }
    // Optional `(`.
    if idx < bytes.len() && bytes[idx] == b'(' {
        idx += 1;
    }
    // Subscriber group 1: 1-4 digits.
    let g1_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - g1_start < 4 {
        idx += 1;
    }
    if idx == g1_start {
        return None;
    }
    // Optional `)`.
    if idx < bytes.len() && bytes[idx] == b')' {
        idx += 1;
    }
    // Optional separator.
    if idx < bytes.len() && (bytes[idx] == b' ' || bytes[idx] == b'-') {
        idx += 1;
    }
    // Subscriber group 2: 3-4 digits.
    let g2_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - g2_start < 4 {
        idx += 1;
    }
    if idx - g2_start < 3 {
        return None;
    }
    // Optional separator.
    if idx < bytes.len() && (bytes[idx] == b' ' || bytes[idx] == b'-') {
        idx += 1;
    }
    // Subscriber group 3: 3-4 digits.
    let g3_start = idx;
    while idx < bytes.len() && bytes[idx].is_ascii_digit() && idx - g3_start < 4 {
        idx += 1;
    }
    if idx - g3_start < 3 {
        return None;
    }
    if !word_boundary_after(input, pos + idx) {
        return None;
    }
    Some(idx)
}

/// Try to match an SSN at the start of `rest`: 3-2-4 digits with
/// REQUIRED `-` separators (the canonical `123-45-6789` form). Pattern:
/// `\d{3}-\d{2}-\d{4}`. Word boundaries required on both ends.
fn try_match_ssn(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    if bytes.len() < 11 {
        return None;
    }
    // 3 digits
    if !(bytes[0].is_ascii_digit() && bytes[1].is_ascii_digit() && bytes[2].is_ascii_digit()) {
        return None;
    }
    if bytes[3] != b'-' {
        return None;
    }
    // 2 digits
    if !(bytes[4].is_ascii_digit() && bytes[5].is_ascii_digit()) {
        return None;
    }
    if bytes[6] != b'-' {
        return None;
    }
    // 4 digits
    if !(bytes[7].is_ascii_digit()
        && bytes[8].is_ascii_digit()
        && bytes[9].is_ascii_digit()
        && bytes[10].is_ascii_digit())
    {
        return None;
    }
    if !word_boundary_after(input, pos + 11) {
        return None;
    }
    Some(11)
}

/// Try to match a credit-card number at the start of `rest`: 4-4-4-4
/// digits with optional `-` or space separators. Pattern:
/// `\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}`. Word boundaries required
/// on both ends.
fn try_match_credit_card(rest: &str, input: &str, pos: usize) -> Option<usize> {
    if !word_boundary_before(input, pos) {
        return None;
    }
    let bytes = rest.as_bytes();
    let mut idx = 0usize;
    for group in 0..4usize {
        // Optional separator before groups 1, 2, 3.
        if group > 0 {
            if idx < bytes.len() && (bytes[idx] == b'-' || bytes[idx] == b' ') {
                idx += 1;
            }
        }
        // 4 digits.
        if idx + 4 > bytes.len() {
            return None;
        }
        for k in 0..4 {
            if !bytes[idx + k].is_ascii_digit() {
                return None;
            }
        }
        idx += 4;
    }
    if !word_boundary_after(input, pos + idx) {
        return None;
    }
    Some(idx)
}

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

//EarlyLogger (lastResort-equivalent for the Rust host) ──────
//
// Python's `logging` module ships with `logging.lastResort` — a
// stderr-only handler of level WARNING that fires when no other
// handlers are configured, so `log.warning(...)`/`log.error(...)`
// calls during early startup (before `logging.basicConfig` runs) are
// NOT silently lost. The TS side has `console.*` which always fires.
// The Rust `log` crate has NO equivalent: before `log::set_logger`
// returns, every `log::*!` call is silently dropped by the default
// no-op logger.
//
//Pre-, `main.rs` called `config_dir_from_env(...)` BEFORE
// `init_file_logger`, and `paths.rs` had to work around the silent
// drop with manual `eprintln!("{}", warn_msg); log::warn!("{}", warn_msg);`
// pairs (paths.rs:165-166). Any NEW pre-init `log::*!` call would be
// silently lost with no workaround.
//
//fix: install an `EarlyLogger` as the FIRST line of `main()`.
// The EarlyLogger is a minimal stderr-only `log::Log` impl that runs
// until `init_file_logger` upgrades it to the combined file+stderr
// sink via a swap pattern (the global `log::set_logger` can only be
// called ONCE per process, so we can't replace the EarlyLogger — we
// swap a `CombinedLogger` INTO it via a `OnceLock`).

/// Process-global handle to the leaked `&'static EarlyLogger` instance,
/// set by `install_early_logger`. Read by `init_file_logger` so it can
/// swap the file sink in without calling `log::set_logger` a second
/// time (which would fail — `set_logger` is process-global one-shot).
pub(crate) static EARLY_LOGGER_HANDLE: OnceLock<&'static EarlyLogger> = OnceLock::new();

//minimal stderr-only `log::Log` impl installed as the FIRST
/// line of `main()` (before `install_panic_hook`, before
/// `config_dir_from_env`, before `init_file_logger`). Until
/// `init_file_logger` runs, all `log::*!` records go to stderr only
/// (subject to the `stderr_verbose` flag + `level_filter`). Once
/// `init_file_logger` runs, a `CombinedLogger` is swapped into
/// `inner` and all subsequent records delegate to it (file + stderr).
///
/// The hot path (`log()`) is a single `OnceLock::get` (one atomic
/// load) — same cost as a `bool` load. The pre-init fallback path
/// (rare — only runs between `install_early_logger` and
/// `init_file_logger`, a window of microseconds in `main()`) does
/// the format + `eprintln!` inline.
pub(crate) struct EarlyLogger {
    /// The `CombinedLogger` installed by `init_file_logger`. `None`
    /// (via `OnceLock::get()` returning `None`) until that call.
    /// `OnceLock::get` is a single atomic load — no mutex on the hot
    /// path. `OnceLock::set` is called exactly once (init_file_logger
    /// returns Err if called twice).
    pub(crate) inner: OnceLock<CombinedLogger>,
    //Pre-init fallback state: stderr verbosity flag. : AtomicBool
    /// so future code can toggle at runtime. After `init_file_logger`
    /// upgrades the EarlyLogger, this field is no longer consulted —
    /// the CombinedLogger's own `stderr_verbose` takes over.
    pub(crate) stderr_verbose: AtomicBool,
    /// Pre-init fallback state: level filter. Plain `log::LevelFilter`
    /// (no atomic) because it's only set once at construction and read
    /// in the pre-init fallback path. After upgrade, the CombinedLogger's
    /// own `level_filter` is used.
    pub(crate) level_filter: log::LevelFilter,
}

impl EarlyLogger {
    /// Return the process-global `&'static EarlyLogger`, if
    /// `install_early_logger` has been called. Returns `None` in
    /// tests / host entrypoints that skip the early-logger install
    /// (in which case `init_file_logger` falls back to the
    //pre- path of calling `log::set_logger` directly).
    fn instance() -> Option<&'static EarlyLogger> {
        EARLY_LOGGER_HANDLE.get().copied()
    }
}

impl log::Log for EarlyLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        // If the CombinedLogger has been swapped in, delegate to its
        // `enabled` check (which consults the upgraded level filter).
        if let Some(combined) = self.inner.get() {
            return combined.enabled(metadata);
        }
        // Pre-init fallback: use the EarlyLogger's own level filter.
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        // Hot path: delegate to the CombinedLogger if installed.
        if let Some(combined) = self.inner.get() {
            combined.log(record);
            return;
        }
        // Pre-init fallback (only runs between `install_early_logger`
        // and `init_file_logger` — a window of microseconds in
        // `main()`). Format the line and emit to stderr only.
        if !self.enabled(record.metadata()) {
            return;
        }
        let raw_msg = record.args().to_string();
        // Apply PII redaction in the pre-init fallback too — the
        // CombinedLogger path calls redact_pii at line 325, but this
        // pre-init path (between install_early_logger and
        // init_file_logger) previously emitted the raw message to
        // stderr without redaction. If init_file_logger fails (config
        // dir not writable, disk full), the EarlyLogger stays in
        // pre-init mode for the entire process lifetime, and every
        // log::*! call would land on stderr unredacted. Mirror the
        // CombinedLogger's redaction here.
        let msg = redact_pii(&raw_msg);
        let ts = now_timestamp();
        let line = format!(
            "{} {:5} {} {}:{} -- {}",
            ts,
            record.level(),
            record.target(),
            record.file().unwrap_or("?"),
            record.line().unwrap_or(0),
            msg
        );
        //AtomicBool::load(Relaxed) — runtime-toggleable.
        if self.stderr_verbose.load(Ordering::Relaxed) {
            eprintln!("{}", line);
        }
        // No file sink in the pre-init fallback — `init_file_logger`
        // hasn't run yet, so there's no `RotatingFileWriter` to write
        // to. The record is preserved on stderr, which is the
        // Python `lastResort` equivalent.
    }

    fn flush(&self) {
        if let Some(combined) = self.inner.get() {
            combined.flush();
        }
        // Pre-init fallback: no buffered state to flush (eprintln! is
        // unbuffered on POSIX — writes go straight to the fd via
        // `write(2)`).
    }
}

//install the `EarlyLogger` as the process-global `log` sink.
/// MUST be the FIRST line of `main()` — before `install_panic_hook`,
/// before `config_dir_from_env`, before any other code that might
/// call `log::*!`. Mirrors Python's `logging.lastResort` pattern.
///
/// After this call returns, ALL `log::*!` records (subject to the
/// level filter) land on stderr (if `stderr_verbose` is true) until
/// `init_file_logger` upgrades the EarlyLogger to the combined
/// file+stderr sink.
///
/// `pub` (NOT `pub(crate)`) so `main.rs` can call it from outside
/// the `platform::logging` module. Calling more than once is safe —
/// the second call is a no-op (the EarlyLogger is already installed
/// in `EARLY_LOGGER_HANDLE` and `log::set_logger` was already called).
///
/// if `log::set_logger` returns `Err` (another logger is
/// already installed as the process-global sink — e.g. a test that
/// called `log::set_logger` before `install_early_logger`), this
/// function does NOT set `EARLY_LOGGER_HANDLE`. Pre-fix it set the
/// handle unconditionally, which orphaned the EarlyLogger:
/// `init_file_logger` would later swap a `CombinedLogger` into the
/// handle's `inner`, but the global `log` dispatch still routed to
/// the OTHER logger → silent log loss. Now `init_file_logger` takes
/// its fallback path (direct `log::set_logger`) which propagates the
/// failure to the caller.
pub fn install_early_logger() {
    if EARLY_LOGGER_HANDLE.get().is_some() {
        // Already installed — no-op. Allows `main()` to call this
        // defensively (e.g. in tests that exercise `main`'s startup
        // path) without panicking on the second `log::set_logger`.
        return;
    }
    //same stderr_verbose computation as
    // `init_file_logger` — debug builds OR `RUST_LOG_STDERR=1`. Now
    // delegates to the shared `is_truthy_env_var` helper so the
    // truthy contract is defined in exactly one place (the prior
    // 4-line `matches!` block was duplicated at lines 143-146 of
    // `init_file_logger`; both sites now go through the same helper).
    let stderr_verbose = cfg!(debug_assertions) || is_truthy_env_var("RUST_LOG_STDERR");
    let logger = Box::leak(Box::new(EarlyLogger {
        inner: OnceLock::new(),
        stderr_verbose: AtomicBool::new(stderr_verbose),
        // Pre-init default: Info level so `log::info!`/`log::warn!`/
        // `log::error!` from `config_dir_from_env` etc. all land on
        // stderr. `init_file_logger` will bump this via
        // `log::set_max_level` once it parses `RUST_LOG`.
        level_filter: log::LevelFilter::Info,
    }));
    // `log::set_logger` is a one-shot — returns Err if a
    // logger is already installed. Pre-fix this code did `let _ =` and
    // unconditionally set `EARLY_LOGGER_HANDLE` below, which ORPHANED
    // the EarlyLogger: `init_file_logger` would later find the handle,
    // swap a `CombinedLogger` into the EarlyLogger's `inner`, but the
    // global `log` dispatch still routed to the OTHER (pre-installed)
    // logger → silent log loss (the swapped-in `CombinedLogger` never
    // received records).
    //
    // Fix: if `set_logger` failed, do NOT set `EARLY_LOGGER_HANDLE`.
    // `init_file_logger` will then take its fallback path (call
    // `log::set_logger` directly with the `CombinedLogger`), which
    // also fails — but that failure is propagated to the caller as an
    // `Err`, which is the correct outcome (the caller can fall back
    // to `env_logger` for stderr-only output). Emit a stderr warning
    // so operators see the orphan in `journalctl` output.
    if log::set_logger(logger).is_err() {
        eprintln!(
            "[EarlyLogger] install_early_logger: log::set_logger failed \
             (another logger is already installed as the process-global \
             log sink). EARLY_LOGGER_HANDLE NOT set — init_file_logger \
             will fall back to direct log::set_logger. Subsequent \
             log::*! records route to the pre-installed logger until \
             init_file_logger runs."
        );
        // Note: the leaked `EarlyLogger` is now orphaned (no handle,
        // no global registration). This is a one-time ~200-byte leak
        // on an error path that should never fire in production
        // (only in tests that install their own logger before calling
        // install_early_logger). Acceptable.
        return;
    }
    log::set_max_level(log::LevelFilter::Info);
    let _ = EARLY_LOGGER_HANDLE.set(logger);
}

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `ROTATE_MAX_BYTES`,
/// then rotates (`.log` → `.log.1` → `.log.2` → … → `.log.4` → delete).
/// Thread-safe via a single `Mutex<Option<BufWriter<File>>>`.
///
/// The `File` is wrapped in a `std::io::BufWriter` (8 KB buffer) so
/// the per-line `write_all` lands in an in-memory buffer instead of
/// issuing one `write(2)` syscall per log line. The buffer is flushed
/// on (a) explicit `flush()` — called by the panic hook + by
/// `CombinedLogger::log` after Warn+ records, (b) buffer fill (BufWriter
/// auto-flushes at 8 KB), and (c) drop — the `*guard = None` path on
/// rotation triggers `BufWriter::drop` which flushes the buffer to the
/// underlying `File` before the fd is closed, so rotation never loses
/// buffered data.
pub(crate) struct RotatingFileWriter {
    dir: std::path::PathBuf,
    base_name: String,
    pub(crate) inner: Mutex<Option<std::io::BufWriter<std::fs::File>>>,
    /// in-memory byte counter — replaces the per-line
    /// `file.metadata()?.len()` stat() syscall. Incremented by
    /// `line.len() + 1` (for the newline) on each successful
    /// `write_all`. Reset to 0 on rotation (the file is renamed and
    /// a fresh empty file is opened on the next `write_line` call).
    /// `Relaxed` ordering is correct: we hold the `inner` Mutex
    /// during both the increment and the load (the only concurrent
    /// access is from `flush()`, which doesn't read this field), so
    /// there's no cross-thread ordering requirement.
    current_size: std::sync::atomic::AtomicU64,
    /// Serializes rotations WITHOUT blocking normal writers. The
    /// `inner` Mutex is dropped BEFORE `rotate()` runs (so concurrent
    /// writers can continue appending to a fresh `.log` while the
    /// rename/remove fs ops execute — those can take 100ms+ on slow
    /// disks / AV-scanned Windows / network filesystems). Multiple
    /// writers may independently detect `size > ROTATE_MAX_BYTES` and
    /// both reach the rotation path; this lock ensures only one
    /// `rotate()` call executes at a time. The losers' `rotate()`
    /// calls are no-ops (rename of nonexistent files fails silently
    /// via `let _ =`).
    rotation_lock: Mutex<()>,
}

impl RotatingFileWriter {
    pub(crate) fn new(dir: std::path::PathBuf, base_name: &str) -> Self {
        Self {
            dir,
            base_name: base_name.to_string(),
            inner: Mutex::new(None),
            current_size: std::sync::atomic::AtomicU64::new(0),
            rotation_lock: Mutex::new(()),
        }
    }

    fn current_path(&self) -> std::path::PathBuf {
        self.dir.join(format!("{}.log", self.base_name))
    }

    pub(crate) fn write_line(&self, line: &str) -> std::io::Result<()> {
        //recover from a poisoned mutex rather than
        // panicking inside the logger. A prior panic while holding
        // this lock would poison it; re-panicking here would recurse
        // through the panic hook (which itself calls `log::error!` →
        // this writer) and abort the process. Use the shared poison-safe
        //`crate::state::lock` helper () for consistency with
        // `state.rs` + `supervisor.rs` + `ws.rs`.
        let mut guard = crate::state::lock(&self.inner);
        // Open the file lazily so we don't create `voice-typer.log`
        // until the first log line is emitted. If the guard was None
        // (first write) OR the previous File handle was torn down by
        // the rotation path (which sets `*guard = None` before
        // renaming), open a fresh File in append mode.
        if guard.is_none() {
            std::fs::create_dir_all(&self.dir)?;
            // Create the log file with `0o600` perms
            // on POSIX so it is NOT world-readable. On Linux/macOS the
            // default `OpenOptions::create(true).append(true).open(...)`
            // inherits the process umask (typically 0o022), producing
            // `0o644` — readable by group + others. The dictation log
            //may contain raw transcription text + PII (),
            // so tighten to owner-only. On Windows `OpenOptionsExt::mode`
            // is unavailable; the OS uses ACLs instead (configured at
            // install time, not per-file).
            let mut opts = OpenOptions::new();
            opts.create(true).append(true);
            #[cfg(unix)]
            opts.mode(0o600);
            let file = opts.open(self.current_path())?;
            // Wrap the raw `File` in a `std::io::BufWriter` (8 KB
            // default capacity) so per-line `write_all` calls land in
            // an in-memory buffer instead of issuing one `write(2)`
            // syscall per log line. The buffer is flushed on (a)
            // explicit `flush()` (called by the panic hook + by
            // `CombinedLogger::log` after Warn+ records), (b) buffer
            // fill (auto-flush at 8 KB), and (c) drop (the `*guard =
            // None` path on rotation triggers `BufWriter::drop` which
            // flushes the buffer to the underlying `File` before the
            // fd is closed — so rotation never loses buffered data).
            let buf_writer = std::io::BufWriter::new(file);
            // Belt-and-suspenders: if the file already existed (created
            // by a prior run with looser perms), explicitly chmod it to
            // 0o600 now. `OpenOptions::mode` only applies to NEW files,
            // not pre-existing ones — so without this chmod a leftover
            // 0o644 log file from a pre-hardening build would stay world-
            // readable indefinitely. Best-effort: a chmod failure does
            // not block logging (a too-permissive file is a security
            // softening, not a hard failure).
            #[cfg(unix)]
            {
                let _ = std::fs::set_permissions(
                    self.current_path(),
                    std::fs::Permissions::from_mode(0o600),
                );
            }
            *guard = Some(buf_writer);
            //seed the in-memory byte counter from the on-disk
            // file size on first open. The file is opened in
            // `create(true).append(true)` mode — if a prior run left a
            // stale `voice-typer.log`, its bytes are still on disk
            // and writes append to them. Without this seed, the
            // counter would start at 0 and rotation would not trigger
            // until the file grows past `ROTATE_MAX_BYTES + <pre-
            // existing size>`. This is one `metadata()` syscall per
            // file OPEN (not per line) — a ~99% reduction vs the
            // prior per-line `metadata()` call.
            //
            // `BufWriter::get_ref` returns `&File` (the underlying
            // handle) so we can stat it without unwrapping the
            // BufWriter.
            let existing_len = guard
                .as_ref()
                .and_then(|f| f.get_ref().metadata().ok())
                .map(|m| m.len())
                .unwrap_or(0);
            self.current_size
                .store(existing_len, std::sync::atomic::Ordering::Relaxed);
        }
        // Borrow the BufWriter<File> from the guard for the write
        // calls below. The match returns early with `Err` if the slot
        // is somehow still None (shouldn't happen — we just initialized
        // it above — but the type system can't prove that, and a
        //panic-free `Option::unwrap` is exactly what  forbids).
        let file = match guard.as_mut() {
            Some(f) => f,
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "logging file slot is None despite just-in-time init",
                ));
            }
        };
        //combine the line payload + the trailing newline into a
        // single `write_all` call. The prior version did two separate
        // `write_all` calls (`line.as_bytes()` then `b"\n"`), which is
        // two `write(2)` syscalls per log line. Coalescing into one
        // buffer halves the syscall count for the file-write path.
        // The `Vec` allocation here is small (typical log line ≈ 200 B)
        // and is dominated by the syscall savings — `write(2)` is
        // ~1–2 µs on Linux, `Vec::push` is ~5 ns.
        let mut buf: Vec<u8> = Vec::with_capacity(line.len() + 1);
        buf.extend_from_slice(line.as_bytes());
        buf.push(b'\n');
        let written = buf.len() as u64;
        file.write_all(&buf)?;
        //in-memory byte counter — increment by the bytes we
        // just wrote. Replaces the per-line `file.metadata()?.len()`
        // stat() syscall. The counter is reset to 0 below when the
        // file rotates.
        self.current_size
            .fetch_add(written, std::sync::atomic::Ordering::Relaxed);
        // The BufWriter<File> accumulates the write in its 8 KB
        // in-memory buffer (no `write(2)` syscall unless the buffer
        // fills). The buffer is flushed by (a) the explicit
        // `RotatingFileWriter::flush` path (called by the panic hook +
        // by `CombinedLogger::log` after Warn+ records), (b) BufWriter
        // auto-flush at 8 KB, or (c) drop (the `*guard = None` path on
        // rotation triggers BufWriter::drop which flushes before the
        // fd is closed).
        // Check size; rotate if we've crossed the threshold.
        let len = self
            .current_size
            .load(std::sync::atomic::Ordering::Relaxed);
        if len > u64::from(ROTATE_MAX_BYTES) {
            // Drop the file handle BEFORE renaming (Windows refuses to
            // rename a file that's open by another handle).
            *guard = None;
            // Reset the in-memory counter — the file is about to be
            // renamed to `.log.1`, and the next `write_line` call
            // opens a fresh empty `.log` whose size starts at 0.
            self.current_size
                .store(0, std::sync::atomic::Ordering::Relaxed);
            // Drop the `inner` Mutex guard BEFORE calling `rotate()`
            // so other loggers aren't blocked during the (potentially
            // slow — 100ms+ on AV-scanned Windows / network filesystems)
            // rename/remove `fs` operations. The rotation path does NOT
            // need the `File` handle: we just set `*guard = None` above
            // (closing the fd), and `rotate()` works purely on
            // filesystem paths. Concurrent writers that arrive during
            // rotation will see `guard.is_none()` and lazily open a
            // fresh `.log` (which `rotate()` may rename out from under
            // them — on POSIX the open fd follows the inode, so their
            // writes land in `.log.1`; an acceptable edge case for a
            // logging path, far better than blocking the entire logger
            // pool during rotation).
            drop(guard);
            // Serialize rotations WITHOUT blocking writers: a separate
            // `Mutex<()>` ensures only one `rotate()` runs at a time
            // (two writers that both crossed the threshold would
            // otherwise race on the rename chain). Losers' `rotate()`
            // calls are no-ops — `rename` of a nonexistent source
            // fails silently via `let _ =`.
            let _rotation_guard = crate::state::lock(&self.rotation_lock);
            self.rotate()?;
        }
        Ok(())
    }

    /// Rotate: `.log.(N-1)` → `.log.N`, …, `.log` → `.log.1`.
    /// Files at index `ROTATE_MAX_FILES - 1` (the oldest) are deleted.
    ///
    //the previous loop bound was `(1..ROTATE_MAX_FILES).rev()`
    /// (= 1,2,3,4) with delete check `i + 1 >= ROTATE_MAX_FILES` (=
    /// `5 >= 5`). That kept 6 files total (`.log`, `.log.1`..`.log.5`),
    /// one MORE than `ROTATE_MAX_FILES=5` — an off-by-one that grew
    /// the disk cap from 25 MB to 30 MB. The fix tightens the loop to
    /// `(1..ROTATE_MAX_FILES - 1).rev()` (= 1,2,3) and the delete check
    /// to `i + 1 >= ROTATE_MAX_FILES - 1` (= `4 >= 4`), so the total
    /// file count is exactly `ROTATE_MAX_FILES=5`.
    fn rotate(&self) -> std::io::Result<()> {
        for i in (1..ROTATE_MAX_FILES - 1).rev() {
            let from = self.dir.join(format!("{}.log.{}", self.base_name, i));
            let to = self
                .dir
                .join(format!("{}.log.{}", self.base_name, i + 1));
            if from.exists() {
                if i + 1 >= ROTATE_MAX_FILES - 1 {
                    // Oldest slot — delete what's there before renaming
                    // (best-effort; ignore errors if the file is gone).
                    let _ = std::fs::remove_file(&to);
                }
                let _ = std::fs::rename(&from, &to);
                // Belt-and-suspenders chmod of the
                // renamed file to 0o600 on POSIX. `rename` preserves
                // the source file's mode, which should already be 0o600
                // (set by `write_line`'s `OpenOptionsExt::mode` call),
                // but a leftover rotated file from a pre-hardening build may
                // still be 0o644. Best-effort: ignore errors (the file
                // may have been moved/deleted between the rename and the
                // chmod — extremely unlikely but defensive).
                #[cfg(unix)]
                {
                    let _ = std::fs::set_permissions(
                        &to,
                        std::fs::Permissions::from_mode(0o600),
                    );
                }
            }
        }
        let from = self.current_path();
        let to = self.dir.join(format!("{}.log.1", self.base_name));
        if from.exists() {
            let _ = std::fs::rename(&from, &to);
            // Same belt-and-suspenders chmod for the
            // `.log` → `.log.1` rename above.
            #[cfg(unix)]
            {
                let _ = std::fs::set_permissions(
                    &to,
                    std::fs::Permissions::from_mode(0o600),
                );
            }
        }
        Ok(())
    }

    pub(crate) fn flush(&self) -> std::io::Result<()> {
        //same poison-recovery rationale as
        // `write_line`, using the shared `crate::state::lock` helper.
        if let Some(f) = crate::state::lock(&self.inner).as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}

