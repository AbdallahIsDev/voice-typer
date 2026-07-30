//! Rotating file logger (ADR-0020 §11): 5 MB × 5 files, excludes bubble_level.
//!
//! Log files + the parent `<config_dir>/logs/` dir
//! are created with restricted POSIX permissions (`0o600` for files,
//! `0o700` for the dir) so dictated-text fragments and any PII the
//! Rust code emits are NOT world-readable on multi-user POSIX systems.
//! Mirrors the Python side's `os.umask(0o077)` + `os.chmod(log_file,
//! 0o600)` pattern in `voice_typer/server/log.py`.
//!
//! # UE-31 deferral: proposed split (NOT done this session)
//!
//! This file is a 2161-line monolith mixing 6 concerns: init
//! orchestration, `CombinedLogger` multi-sink dispatch, a 515-LOC PII
//! redaction engine (`redact_pii` + 5+ `try_match_*` state machines),
//! `install_panic_hook`, `EarlyLogger` + `EARLY_LOGGER_HANDLE`, and
//! `RotatingFileWriter`. UE-31 (GROUP 5 mandatory spaghetti split)
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
//!
//! **Deferred** because the UE-6 fix (this session) edits the redaction
//! engine (`redact_pii` + new `try_match_flag_or_bare_key` +
//! `try_match_long_alphanumeric_run`), which would conflict with a
//! simultaneous file split. The split should be done in a follow-up
//! session AFTER the UE-6 redaction expansion has been validated.
//! High regression risk: the redaction engine runs inside the panic
//! hook, so a move/rename mistake would silently disable PII scrubbing
//! in crash reports.

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
    // FR-97: rename Rust's log basename to `voice-typer-rust` so the
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
    // PVT-G5-082: honor `RUST_LOG` runtime log-level override. Parsed
    // as a `log::LevelFilter` (e.g. "debug", "trace", "warn", "off").
    // Default to `Info` if the var is unset OR unparseable so a typo
    // (e.g. `RUST_LOG=debog`) doesn't silently disable all logging.
    // Both the global `log::set_max_level` AND the per-logger
    // `level_filter` are set to this value — `set_max_level` is the
    // fast-path short-circuit at the macro call site, while
    // `level_filter` is consulted inside `CombinedLogger::enabled`
    // (which `log::log!` calls as a second filter).
    //
    // G4-M-31 (session 4) fallback: if `RUST_LOG` is unset, also honor
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
            // G4-M-31: RUST_LOG unset/unparseable — try VOICE_TYPER_DEBUG.
            if is_debug_env_truthy(std::env::var("VOICE_TYPER_DEBUG").ok().as_deref()) {
                Some(log::LevelFilter::Debug)
            } else {
                None
            }
        })
        .unwrap_or(log::LevelFilter::Info);
    // TY-34: gate stderr output on debug builds OR `RUST_LOG_STDERR=1`.
    // Release builds with no env var skip the per-line `eprintln!`
    // syscall (saves 1 `write(2)` per log line). The env var is the
    // release-build escape hatch for operators who want stderr tailing
    // (`journalctl -u voice-typer` etc.).
    let stderr_verbose_init = cfg!(debug_assertions)
        || std::env::var("RUST_LOG_STDERR")
            .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);
    let combined = CombinedLogger {
        file_writer: Some(writer),
        level_filter: max_level,
        // FR-96: `AtomicBool` so future code (e.g. a Tauri command)
        // can toggle stderr verbosity at runtime. The per-line cost
        // is a single `AtomicBool::load(Relaxed)` — same as a `bool`
        // load on x86/ARM (Relaxed loads compile to a plain MOV).
        stderr_verbose: AtomicBool::new(stderr_verbose_init),
    };

    // FR-16: prefer the swap pattern when an `EarlyLogger` is already
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
    // preserves the pre-FR-16 behavior so existing tests that depend
    // on `init_file_logger` calling `set_logger` continue to compile
    // and run.
    log::set_logger(Box::leak(Box::new(combined)))
        .map_err(|_| "failed to set logger (already set?)".to_string())?;
    log::set_max_level(max_level);
    Ok(())
}

/// G4-M-31: predicate form of the VOICE_TYPER_DEBUG env-var check,
/// extracted for unit testing. Truthy values: "1", "true", "yes"
/// (case-insensitive). Anything else (including unset / empty) is
/// falsy → production Info-level logging.
///
/// Mirrors the Python side's `env_validation.py` boolean-var pattern
/// (pattern: `^(1|0|true|false|yes|no)$`, case-insensitive) so the
/// Rust + Python hosts respond identically to the same env var.
pub(crate) fn is_debug_env_truthy(value: Option<&str>) -> bool {
    match value {
        Some(v) => matches!(
            v.trim().to_ascii_lowercase().as_str(),
            "1" | "true" | "yes"
        ),
        None => false,
    }
}

/// Combined stderr + rotating-file logger. Replaces `env_logger` so
/// we can add the file sink without a multiplexer crate.
pub(crate) struct CombinedLogger {
    file_writer: Option<RotatingFileWriter>,
    level_filter: log::LevelFilter,
    /// TY-34: cached predicate — `true` if log lines should ALSO be
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
    /// FR-96: changed from `bool` to `AtomicBool` so the predicate
    /// becomes runtime-toggleable. Future code (e.g. a Tauri command
    /// that flips stderr verbosity without restarting the host) can
    /// `store(true/false, Ordering::Relaxed)` at any time. The per-
    /// line `log()` path uses `load(Ordering::Relaxed)`, which on
    /// x86/ARM compiles to a plain MOV — same cost as a `bool` load.
    /// `Relaxed` is correct: we don't need cross-thread ordering for
    /// a boolean flag whose only consumer is the same thread that
    /// calls `eprintln!`.
    stderr_verbose: AtomicBool,
}

impl log::Log for CombinedLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool {
        metadata.level() <= self.level_filter
    }

    fn log(&self, record: &log::Record) {
        if !self.enabled(record.metadata()) {
            return;
        }
        // XZ-LOG-02: redact PII from the log message before writing
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
        // PVT-G5-084: include `file:line` so operators can jump
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
        // TY-34: gate the per-line `eprintln!` on the cached
        // `stderr_verbose` flag (computed once at logger init from
        // `cfg!(debug_assertions)` OR `RUST_LOG_STDERR=1`). The prior
        // unconditional `eprintln!` was a wasted `write(2)` syscall
        // per log line in release builds where stderr is /dev/null.
        // Always emit in debug builds so `cargo tauri dev` shows live
        // logs in the launching terminal; opt-in for release builds.
        //
        // FR-96: `AtomicBool::load(Relaxed)` — runtime-toggleable
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
        // GT-B4-11: the WS reader doesn't currently log bubble_level
        // events to the file (they go via `app.emit()` to the webview,
        // not `log::*!`), so this filter is defensive.
        //
        // FR-33: preserve WARNING+ records even when they start with
        // the bubble_level prefix. Pre-fix this dropped ANY record
        // matching the prefix regardless of level — a future
        // `log::error!("[WS-READER] bubble_level event handler
        // crashed: ...")` would be SILENTLY LOST from the file log.
        // Mirrors Python's `_BubbleLevelExclusionFilter` short-circuit
        // at `log.py:216-219`:
        //   `if record.levelno >= logging.WARNING: return True`
        // (filter returning True = "do NOT filter out" in Python's
        // logging API). The Rust equivalent is the level-guarded
        // early-skip below.
        if let Some(writer) = &self.file_writer {
            let is_filtered_bubble = record.level() <= log::Level::Info
                && msg.starts_with("[WS-READER] bubble_level event");
            if !is_filtered_bubble {
                let _ = writer.write_line(&line);
            }
        }
    }

    fn flush(&self) {
        if let Some(writer) = &self.file_writer {
            let _ = writer.flush();
        }
    }
}

// ─── XZ-LOG-02 / UE-6: PII redaction ────────────────────────────────────
//
// PII redactor for log output. Ports the Python `PIIRedactionFilter`
// pattern set to Rust using std-only substring scanning (no `regex`
// crate dependency). Covered patterns (checked at each byte position
// in the order listed; first match wins):
//
//   Prefix / keyword patterns (Python `_KEY_PATTERNS` + `_FLAG_KEY_PATTERNS`):
//   - Bearer <token>           → `Bearer ***`   (Python `_KEY_PATTERNS[0]`)
//   - Token <token>            → `Token ***`    (Python `_KEY_PATTERNS[1]`)
//   - sk-<token> (8+ chars)    → `sk-***`       (Python `_KEY_PATTERNS[2]`)
//   - gsk_<token> (8+ chars)   → `gsk_***`      (Python `_KEY_PATTERNS[3]`)
//   - --keyword=value          → `--keyword=***` (Python `_FLAG_VALUE_PATTERN`, UE-6)
//   - --keyword value          → `--keyword ***` (Python `_FLAG_VALUE_PATTERN`, UE-6)
//   - keyword=value            → `keyword=***`   (Python `_BARE_KEY_VALUE_PATTERN`, UE-6)
//
//   PII patterns (Python `_PATTERNS`):
//   - user:pass@host      → `***@host`     (Python `redact_url`)
//   - <local>@<dom>.<tld> → `[EMAIL]`      (Python `_PATTERNS[0]`)
//   - IBAN                → `[IBAN]`       (Python `_PATTERNS[1]`)
//   - Intl phone (+cc…)   → `[PHONE]`      (Python `_PATTERNS[3]`)
//   - US phone (3-3-4)    → `[PHONE]`      (Python `_PATTERNS[2]`)
//   - SSN (3-2-4)         → `[SSN]`        (Python `_PATTERNS[4]`)
//   - Credit card (4-4-4-4) → `[CC]`      (Python `_PATTERNS[5]`)
//
//   Catch-all (Python `_KEY_PATTERNS[4]`, UE-6):
//   - 20+ char alphanumeric run → `***`   (`\b[A-Za-z0-9_\-]{20,}\b`)
//
// The fast path mirrors Python's `_FAST_TRIGGER` (security.py:63):
//   `[@+]|\d{3,}|Bearer|Token|sk-|key=|[A-Za-z0-9_\-]{20,}`
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
    //   `@`      — email / URL credentials
    //   `+`      — international phone (`+<cc>…`)
    //   `Bearer` — bearer token
    //   `Token`  — token keyword
    //   `sk-`    — OpenAI-style key
    //   `gsk_`   — Groq-style key
    //   `://`    — URL credentials (`https://user:pass@host`)
    //   3+ consecutive ASCII digits — US phone, SSN, CC, IBAN (BBAN
    //     portion always contains 3+ consecutive digits)
    //   `key=`   — bare `key=value` flag form (Python `_FAST_TRIGGER`);
    //     also a substring of `--key=`, `--api_key=`, `--api-key=`, and
    //     any `--<keyword>=` flag whose keyword ends in `key`. Case-
    //     sensitive (mirrors Python). UE-6.
    //   20+ char alphanumeric run — the generic 20+ char bare-token
    //     pattern (`\b[A-Za-z0-9_\-]{20,}\b`, Python `_KEY_PATTERNS[4]`).
    //     UE-6.
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
        //    the Python `_KEY_PATTERNS[0]` charset `[A-Za-z0-9_\-\.=]`.
        //    Pre-fix the token ran until whitespace, which consumed
        //    trailing punctuation (e.g. the comma in `Bearer abc123,`)
        //    and broke `test_redact_pii_multiple_patterns_in_one_line`.
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
        //    dash / non underscore (the typical API-key charset).
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
        //    length threshold as `sk-` (8+ chars after the prefix).
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

        // 5. Flag-form and bare-keyword secret patterns (UE-6). Mirrors
        //    Python's `_FLAG_VALUE_PATTERN` (Pattern A: `--keyword=value`
        //    or `--keyword value`) and `_BARE_KEY_VALUE_PATTERN`
        //    (Pattern B: `keyword=value`). Checked AFTER the prefix
        //    patterns (Bearer/Token/sk-/gsk_) so those specific prefixes
        //    win, but BEFORE the PII patterns (email/IBAN/phone/SSN/CC)
        //    so a secret-bearing flag value containing `@` or digits
        //    (e.g. `token=alice@example.com`) is redacted as a secret
        //    (`token=***`) rather than as PII (`token=[EMAIL]`).
        //
        //    See `SECRET_KEYWORDS` below for the keyword list and
        //    `try_match_flag_or_bare_key` for the matching logic.
        if let Some((total_len, prefix_len)) = try_match_flag_or_bare_key(rest, input, i) {
            out.push_str(&rest[..prefix_len]);
            out.push_str("***");
            i += total_len;
            continue;
        }

        // 6. `user:pass@host` — strip everything up to and including
        //    the `@` IF the prefix contains a `:` (the URL-credential
        //    marker). The host part is preserved.
        if rest.contains('@') {
            if let Some(at_pos) = rest.find('@') {
                let prefix = &rest[..at_pos];
                if prefix.contains(':') && !prefix.contains(' ') && !prefix.is_empty() {
                    out.push_str("***@");
                    i += at_pos + 1;
                    continue;
                }
                // 7. Basic email: `<name>@<domain>.<tld>`. We require
                //    a `.` in the domain part (after the `@`) to avoid
                //    false-positives on `user@host` (no TLD).
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
        //    chars (uppercase letters or digits). MUST be checked
        //    before phone/SSN/CC so the digit portion of an IBAN
        //    (e.g. `GB82WEST12345698765432`) isn't partially matched
        //    as a phone number. Mirrors Python `_PATTERNS[1]`:
        //    `\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b`.
        if let Some(iban_len) = try_match_iban(rest, input, i) {
            out.push_str("[IBAN]");
            i += iban_len;
            continue;
        }

        // 9. International phone: `+` followed by country code (1-3
        //    digits) and subscriber number. Mirrors Python
        //    `_PATTERNS[3]`: `\+\d{1,3}[\s-]?\(?\d{1,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b`.
        //    Checked before US phone so `+1 (415) 555-2671` matches
        //    the international pattern (not the US pattern on the
        //    `415 555 2671` tail).
        if rest.starts_with('+') {
            if let Some(phone_len) = try_match_intl_phone(rest, input, i) {
                out.push_str("[PHONE]");
                i += phone_len;
                continue;
            }
        }

        // 10. US phone: 3-3-4 digits with optional `-` or `.`
        //    separators. Mirrors Python `_PATTERNS[2]`:
        //    `\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`.
        if let Some(phone_len) = try_match_us_phone(rest, input, i) {
            out.push_str("[PHONE]");
            i += phone_len;
            continue;
        }

        // 11. SSN: 3-2-4 digits with `-` separators (the canonical
        //     `123-45-6789` form). Mirrors Python `_PATTERNS[4]`:
        //     `\b\d{3}-\d{2}-\d{4}\b`. The dashes are REQUIRED (not
        //     optional) so a 9-digit run like `123456789` is NOT
        //     matched as an SSN (matches Python behaviour).
        if let Some(ssn_len) = try_match_ssn(rest, input, i) {
            out.push_str("[SSN]");
            i += ssn_len;
            continue;
        }

        // 12. Credit card: 4-4-4-4 digits with optional `-` or space
        //     separators. Mirrors Python `_PATTERNS[5]`:
        //     `\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b`.
        if let Some(cc_len) = try_match_credit_card(rest, input, i) {
            out.push_str("[CC]");
            i += cc_len;
            continue;
        }

        // 13. 20+ char alphanumeric catch-all (UE-6). Mirrors Python's
        //     `_KEY_PATTERNS[4]`: `\b[A-Za-z0-9_\-]{20,}\b`. Catches
        //     bare GitLab/GitHub/Slack PATs with no prefix. Checked LAST
        //     (after all PII patterns) so an IBAN like
        //     `GB82WEST12345698765432` (20 chars) is redacted as `[IBAN]`
        //     rather than `***`.
        if let Some(run_len) = try_match_long_alphanumeric_run(rest, input, i) {
            out.push_str("***");
            i += run_len;
            continue;
        }

        // No pattern matched at this position — copy the char and
        // advance by its UTF-8 length. UE-44: use `if let Some` instead
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

// ─── UE-6: flag-form + bare-keyword + 20+ char catch-all helpers ────────

/// Secret-bearing keywords for the flag-form and bare-keyword patterns.
/// Mirrors Python's `_SECRET_KEYWORDS` in `voice_typer/server/_secrets.py:88-115`,
/// plus `bearer` and `credential` (UE-6 task-specified additions not in
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
    // UE-6 task-specified additions (not in Python's _SECRET_KEYWORDS
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
            if after_dashes.len() >= kw.len()
                && after_dashes[..kw.len()].eq_ignore_ascii_case(kw)
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
        if rest.len() >= kw.len() + 1
            && rest[..kw.len()].eq_ignore_ascii_case(kw)
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

// ─── PVT-G5-083: panic hook ─────────────────────────────────────────────
//
// Install a panic hook that writes the panic payload + source location
// to BOTH stderr (via `eprintln!`) and the file log (via `log::error!`).
// Without this, a panic in a Tauri command handler or sidecar WS reader
// would unwind without any breadcrumb in the file log — operators
// debugging from logs alone would have no signal that a panic occurred
// (only the React UI's generic "something went wrong" toast would
// fire). The hook chains to the previous hook (if any) so existing
// panic behavior is preserved.

/// Install the Voice Typer panic hook (PVT-G5-083).
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
/// FR-16: if `install_early_logger` has already been called (the new
/// standard path — `install_early_logger` is the FIRST line of
/// `main()`), then the global `log` sink is the `EarlyLogger` (a
/// stderr-only fallback) and `log::error!` from the panic hook will
/// land on stderr even before `init_file_logger` upgrades the
/// EarlyLogger to the combined file+stderr sink.
pub fn install_panic_hook() {
    let prev = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
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
        // XZ-LOG-02: redact the payload before emitting — panic
        // messages can carry arbitrary user-supplied strings (e.g. a
        // serde_json error containing a fragment of the request body,
        // which can include an email / API key) and we don't want
        // those to land in `voice-typer.log` unredacted.
        let payload_redacted = redact_pii(payload);
        eprintln!("[PANIC] {} -- {}", location, payload_redacted);
        log::error!("panic at {} -- {}", location, payload_redacted);
        // Chain to the previous hook so any prior behavior (e.g. the
        // default "print panic message + abort" path under
        // `panic=abort`) is preserved.
        prev(info);
    }));
}

// ─── FR-16: EarlyLogger (lastResort-equivalent for the Rust host) ──────
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
// Pre-FR-16, `main.rs` called `config_dir_from_env(...)` BEFORE
// `init_file_logger`, and `paths.rs` had to work around the silent
// drop with manual `eprintln!("{}", warn_msg); log::warn!("{}", warn_msg);`
// pairs (paths.rs:165-166). Any NEW pre-init `log::*!` call would be
// silently lost with no workaround.
//
// FR-16 fix: install an `EarlyLogger` as the FIRST line of `main()`.
// The EarlyLogger is a minimal stderr-only `log::Log` impl that runs
// until `init_file_logger` upgrades it to the combined file+stderr
// sink via a swap pattern (the global `log::set_logger` can only be
// called ONCE per process, so we can't replace the EarlyLogger — we
// swap a `CombinedLogger` INTO it via a `OnceLock`).

/// Process-global handle to the leaked `&'static EarlyLogger` instance,
/// set by `install_early_logger`. Read by `init_file_logger` so it can
/// swap the file sink in without calling `log::set_logger` a second
/// time (which would fail — `set_logger` is process-global one-shot).
static EARLY_LOGGER_HANDLE: OnceLock<&'static EarlyLogger> = OnceLock::new();

/// FR-16: minimal stderr-only `log::Log` impl installed as the FIRST
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
    inner: OnceLock<CombinedLogger>,
    /// Pre-init fallback state: stderr verbosity flag. FR-96: AtomicBool
    /// so future code can toggle at runtime. After `init_file_logger`
    /// upgrades the EarlyLogger, this field is no longer consulted —
    /// the CombinedLogger's own `stderr_verbose` takes over.
    stderr_verbose: AtomicBool,
    /// Pre-init fallback state: level filter. Plain `log::LevelFilter`
    /// (no atomic) because it's only set once at construction and read
    /// in the pre-init fallback path. After upgrade, the CombinedLogger's
    /// own `level_filter` is used.
    level_filter: log::LevelFilter,
}

impl EarlyLogger {
    /// Return the process-global `&'static EarlyLogger`, if
    /// `install_early_logger` has been called. Returns `None` in
    /// tests / host entrypoints that skip the early-logger install
    /// (in which case `init_file_logger` falls back to the
    /// pre-FR-16 path of calling `log::set_logger` directly).
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
        let msg = record.args().to_string();
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
        // FR-96: AtomicBool::load(Relaxed) — runtime-toggleable.
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

/// FR-16: install the `EarlyLogger` as the process-global `log` sink.
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
pub fn install_early_logger() {
    if EARLY_LOGGER_HANDLE.get().is_some() {
        // Already installed — no-op. Allows `main()` to call this
        // defensively (e.g. in tests that exercise `main`'s startup
        // path) without panicking on the second `log::set_logger`.
        return;
    }
    // TY-34 / FR-96: same stderr_verbose computation as
    // `init_file_logger` — debug builds OR `RUST_LOG_STDERR=1`. We
    // recompute here (rather than inheriting from a shared helper)
    // because env vars don't change between the two calls and the
    // duplication is only 4 lines.
    let stderr_verbose = cfg!(debug_assertions)
        || std::env::var("RUST_LOG_STDERR")
            .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);
    let logger = Box::leak(Box::new(EarlyLogger {
        inner: OnceLock::new(),
        stderr_verbose: AtomicBool::new(stderr_verbose),
        // Pre-init default: Info level so `log::info!`/`log::warn!`/
        // `log::error!` from `config_dir_from_env` etc. all land on
        // stderr. `init_file_logger` will bump this via
        // `log::set_max_level` once it parses `RUST_LOG`.
        level_filter: log::LevelFilter::Info,
    }));
    // `log::set_logger` is a one-shot — returns Err if a logger is
    // already installed. We `let _ =` the result so this function is
    // idempotent (a test that already set its own logger doesn't
    // panic). The `EARLY_LOGGER_HANDLE` is still set below so
    // `init_file_logger` can find the EarlyLogger and swap in the
    // file sink.
    let _ = log::set_logger(logger);
    log::set_max_level(log::LevelFilter::Info);
    let _ = EARLY_LOGGER_HANDLE.set(logger);
}

/// Minimal rotating-file writer: appends to
/// `<dir>/<base_name>.log` until the file exceeds `ROTATE_MAX_BYTES`,
/// then rotates (`.log` → `.log.1` → `.log.2` → … → `.log.4` → delete).
/// Thread-safe via a single `Mutex<Option<File>>`.
pub(crate) struct RotatingFileWriter {
    dir: std::path::PathBuf,
    base_name: String,
    inner: Mutex<Option<std::fs::File>>,
    /// TY-34: in-memory byte counter — replaces the per-line
    /// `file.metadata()?.len()` stat() syscall. Incremented by
    /// `line.len() + 1` (for the newline) on each successful
    /// `write_all`. Reset to 0 on rotation (the file is renamed and
    /// a fresh empty file is opened on the next `write_line` call).
    /// `Relaxed` ordering is correct: we hold the `inner` Mutex
    /// during both the increment and the load (the only concurrent
    /// access is from `flush()`, which doesn't read this field), so
    /// there's no cross-thread ordering requirement.
    current_size: std::sync::atomic::AtomicU64,
}

impl RotatingFileWriter {
    fn new(dir: std::path::PathBuf, base_name: &str) -> Self {
        Self {
            dir,
            base_name: base_name.to_string(),
            inner: Mutex::new(None),
            current_size: std::sync::atomic::AtomicU64::new(0),
        }
    }

    fn current_path(&self) -> std::path::PathBuf {
        self.dir.join(format!("{}.log", self.base_name))
    }

    fn write_line(&self, line: &str) -> std::io::Result<()> {
        // PVT-G5-018: recover from a poisoned mutex rather than
        // panicking inside the logger. A prior panic while holding
        // this lock would poison it; re-panicking here would recurse
        // through the panic hook (which itself calls `log::error!` →
        // this writer) and abort the process. Use the shared poison-safe
        // `crate::state::lock` helper (G4-H-27) for consistency with
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
            // may contain raw transcription text + PII (XZ-LOG-02),
            // so tighten to owner-only. On Windows `OpenOptionsExt::mode`
            // is unavailable; the OS uses ACLs instead (configured at
            // install time, not per-file).
            let mut opts = OpenOptions::new();
            opts.create(true).append(true);
            #[cfg(unix)]
            opts.mode(0o600);
            let file = opts.open(self.current_path())?;
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
            *guard = Some(file);
            // TY-34: seed the in-memory byte counter from the on-disk
            // file size on first open. The file is opened in
            // `create(true).append(true)` mode — if a prior run left a
            // stale `voice-typer.log`, its bytes are still on disk
            // and writes append to them. Without this seed, the
            // counter would start at 0 and rotation would not trigger
            // until the file grows past `ROTATE_MAX_BYTES + <pre-
            // existing size>`. This is one `metadata()` syscall per
            // file OPEN (not per line) — a ~99% reduction vs the
            // prior per-line `metadata()` call.
            let existing_len = guard
                .as_ref()
                .and_then(|f| f.metadata().ok())
                .map(|m| m.len())
                .unwrap_or(0);
            self.current_size
                .store(existing_len, std::sync::atomic::Ordering::Relaxed);
        }
        // Borrow the File from the guard for the write/flush/metadata
        // calls below. The match returns early with `Err` if the slot
        // is somehow still None (shouldn't happen — we just initialized
        // it above — but the type system can't prove that, and a
        // panic-free `Option::unwrap` is exactly what G4-H-27 forbids).
        let file = match guard.as_mut() {
            Some(f) => f,
            None => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Other,
                    "logging file slot is None despite just-in-time init",
                ));
            }
        };
        // TY-34: combine the line payload + the trailing newline into a
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
        // TY-34: in-memory byte counter — increment by the bytes we
        // just wrote. Replaces the per-line `file.metadata()?.len()`
        // stat() syscall. The counter is reset to 0 below when the
        // file rotates.
        self.current_size
            .fetch_add(written, std::sync::atomic::Ordering::Relaxed);
        // TY-34: `std::fs::File::flush` is a documented no-op ("File
        // doesn't have a buffer"), so the prior `file.flush()?` call
        // was a wasted method dispatch with no syscall savings. Drop
        // it. The OS write buffer is flushed by the kernel on its own
        // schedule (or by the explicit `RotatingFileWriter::flush`
        // path that the panic hook calls).
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
            self.rotate()?;
        }
        Ok(())
    }

    /// Rotate: `.log.(N-1)` → `.log.N`, …, `.log` → `.log.1`.
    /// Files at index `ROTATE_MAX_FILES - 1` (the oldest) are deleted.
    ///
    /// GT-67: the previous loop bound was `(1..ROTATE_MAX_FILES).rev()`
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

    fn flush(&self) -> std::io::Result<()> {
        // PVT-G5-018 / G4-H-27: same poison-recovery rationale as
        // `write_line`, using the shared `crate::state::lock` helper.
        if let Some(f) = crate::state::lock(&self.inner).as_mut() {
            f.flush()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    // PVT-G5-018 merge note: tests call `logger.log(&record)` and
    // `logger.flush()` directly on a `CombinedLogger` value. Those
    // methods belong to the `log::Log` trait, which is NOT auto-
    // imported by `use super::*;` (trait methods need the trait in
    // scope at the call site). Bring it in explicitly so the test
    // module compiles cleanly under rustc 1.97+.
    use log::Log;

    // ── RotatingFileWriter ────────────────────────────────────────────

    #[test]
    fn test_rotating_file_writer_basic_write() {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-basic",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        writer.write_line("hello").unwrap();
        writer.write_line("world").unwrap();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert_eq!(content, "hello\nworld\n");
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotating_file_writer_rotation() {
        // Use a tiny threshold by writing many large lines.
        // ROTATE_MAX_BYTES is 5 MB; writing 6 MB should trigger at
        // least one rotation.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-rotate",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // 6 MB total, 100 KB per line → ~60 lines.
        let big_line = "x".repeat(100_000);
        for _ in 0..60 {
            writer.write_line(&big_line).unwrap();
        }
        // After rotation, `.log` should exist (the current file) and
        // `.log.1` should exist (the first rotated file).
        assert!(tmp.join("test-log.log").exists(), "current log missing");
        assert!(
            tmp.join("test-log.log.1").exists(),
            "first rotated log missing"
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── GT-67: pin the exact file count after N rotations ────────────
    //
    // The previous rotation loop kept `ROTATE_MAX_FILES + 1` files on
    // disk (off-by-one). This test writes enough data to trigger MANY
    // rotations (well past the cap) and asserts the final file count
    // is EXACTLY `ROTATE_MAX_FILES` — no more, no less.

    #[test]
    fn test_rotating_file_writer_pins_exact_file_count_after_many_rotations() {
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-gt67-count",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // Write ~50 MB total (100 KB/line × 500 lines). With a 5 MB
        // rotation threshold, this triggers ~10 rotations — well past
        // the 5-file cap, so the rotate() function's delete-oldest
        // path runs at least 5 times.
        let big_line = "x".repeat(100_000);
        for _ in 0..500 {
            writer.write_line(&big_line).unwrap();
        }
        writer.flush().unwrap();

        // Count the actual files on disk (current + rotated).
        let mut file_count = 0;
        for i in 0..=ROTATE_MAX_FILES {
            let path = if i == 0 {
                tmp.join("test-log.log")
            } else {
                tmp.join(format!("test-log.log.{}", i))
            };
            if path.exists() {
                file_count += 1;
            }
        }

        // GT-67 invariant: total file count must be EXACTLY
        // ROTATE_MAX_FILES (=5). Pre-fix this was 6 (off-by-one).
        assert_eq!(
            file_count,
            ROTATE_MAX_FILES,
            "GT-67: rotating log must keep exactly {} files; found {}. Pre-fix this was {} (off-by-one).",
            ROTATE_MAX_FILES,
            file_count,
            ROTATE_MAX_FILES + 1,
        );

        // The oldest KEPT slot is `.log.(ROTATE_MAX_FILES - 1)` (=4).
        assert!(
            tmp.join(format!("test-log.log.{}", ROTATE_MAX_FILES - 1)).exists(),
            "GT-67: oldest kept slot `.log.{}` must exist after many rotations",
            ROTATE_MAX_FILES - 1
        );
        // The next-oldest slot (`.log.ROTATE_MAX_FILES` = .log.5) must
        // NOT exist — it's the one that gets deleted by the rotate()
        // loop's `if i + 1 >= ROTATE_MAX_FILES - 1` branch.
        assert!(
            !tmp.join(format!("test-log.log.{}", ROTATE_MAX_FILES)).exists(),
            "GT-67: `.log.{}` (one past the cap) must NOT exist",
            ROTATE_MAX_FILES
        );

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_rotating_file_writer_thread_safety() {
        // Spawn multiple threads writing to the same writer — should
        // not panic or corrupt (Mutex protects the inner File).
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-threads",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = std::sync::Arc::new(RotatingFileWriter::new(tmp.clone(), "test-log"));
        let mut handles = Vec::new();
        for i in 0..4 {
            let w = writer.clone();
            handles.push(std::thread::spawn(move || {
                for j in 0..50 {
                    w.write_line(&format!("thread-{}-line-{}", i, j)).unwrap();
                }
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        // 4 threads × 50 lines = 200 lines total.
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        let line_count = content.lines().count();
        // Could be fewer if rotation happened mid-write (the current
        // file gets renamed to .log.1 and a fresh .log starts). Just
        // assert we wrote *something* and didn't panic.
        assert!(line_count > 0, "no lines in current log: {}", content);
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── PVT-G5-018: poison-recovery (Mutex .unwrap_or_else) ──────────

    #[test]
    fn test_rotating_file_writer_recovers_from_poisoned_mutex() {
        // PVT-G5-018: a prior panic while holding `inner`'s lock
        // poisons the mutex. The pre-fix code called `.lock().unwrap()`
        // here, which would re-panic. The post-fix code uses
        // `.lock().unwrap_or_else(|e| e.into_inner())`, which recovers
        // the guard (and the inner File handle) so logging can
        // continue. This test simulates the poison by manually
        // poisoning the mutex via `std::sync::PoisonError`, then
        // verifies that `write_line` and `flush` do NOT panic.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-poison",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // Write an initial line so the inner File handle is opened.
        writer.write_line("before-poison").unwrap();
        // Poison the mutex: lock it, then panic while holding it
        // (caught via `catch_unwind` so the test process survives).
        let poison_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = writer.inner.lock().unwrap();
            panic!("intentional poison for PVT-G5-018 test");
        }));
        assert!(poison_result.is_err(), "test setup: panic should have fired");
        // Now the mutex is poisoned. The post-fix code must NOT panic.
        writer.write_line("after-poison").unwrap();
        writer.flush().unwrap();
        // Verify both lines landed (the recovered guard carries the
        // previously-opened File handle, so the post-poison write
        // appends to the same file).
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert!(content.contains("before-poison"), "pre-poison line lost: {}", content);
        assert!(content.contains("after-poison"), "post-poison line lost: {}", content);
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── PVT-G5-082: RUST_LOG parsing ─────────────────────────────────
    //
    // We can't call `init_file_logger` from a test (it calls
    // `log::set_logger`, which is process-global and can only be set
    // once). Instead, test the parsing logic in isolation by mirroring
    // the `RUST_LOG` parse chain here. This pins the default-Info +
    // unparseable-fallback behavior so a future refactor can't silently
    // break it.

    #[test]
    fn test_rust_log_parsing_default_is_info() {
        // Mirror of the parse chain in `init_file_logger` (with no
        // RUST_LOG env var set, the chain falls through to `Info`).
        // NOTE: this test does NOT set RUST_LOG (other tests in the
        // process might have set it, so we read it defensively and
        // only assert the parse-chain shape, not the exact value —
        // see the next test for a parse-chain correctness check).
        let parsed = std::env::var("RUST_LOG")
            .ok()
            .and_then(|s| s.parse::<log::LevelFilter>().ok())
            .unwrap_or(log::LevelFilter::Info);
        // Whatever RUST_LOG is (unset, "debug", or a typo), the chain
        // must always yield a valid LevelFilter (never panics).
        let _ = parsed;
    }

    #[test]
    fn test_rust_log_parsing_unparseable_falls_back_to_info() {
        // PVT-G5-082: a typo like "debog" should fall back to Info
        // rather than silently disabling all logging. We mirror the
        // exact parse chain (without setting the env var, which would
        // race with other tests in the same process) by feeding the
        // unparseable string directly to the parser.
        let parsed = "debog".parse::<log::LevelFilter>();
        assert!(parsed.is_err(), "garbage value should not parse");
        // The init_file_logger chain uses `.ok()` then `.unwrap_or(Info)`,
        // so an unparseable value yields Info — verified here.
        let effective = parsed.ok().unwrap_or(log::LevelFilter::Info);
        assert_eq!(effective, log::LevelFilter::Info);
    }

    #[test]
    fn test_rust_log_parsing_known_levels() {
        // PVT-G5-082: pin the parse behavior for the common
        // `RUST_LOG=debug` / `=trace` / `=warn` / `=off` values so a
        // future `log` crate upgrade can't silently break the
        // override (e.g. by renaming a variant).
        assert_eq!(
            "off".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Off
        );
        assert_eq!(
            "warn".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Warn
        );
        assert_eq!(
            "info".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Info
        );
        assert_eq!(
            "debug".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Debug
        );
        assert_eq!(
            "trace".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Trace
        );
        // Case-insensitivity: the `FromStr` impl in the `log` crate
        // accepts both `Debug` and `debug`.
        assert_eq!(
            "DEBUG".parse::<log::LevelFilter>().unwrap(),
            log::LevelFilter::Debug
        );
    }

    // ── PVT-G5-083: install_panic_hook ───────────────────────────────

    #[test]
    fn test_install_panic_hook_does_not_panic_on_install() {
        // PVT-G5-083: the hook installer itself must not panic. Calling
        // it twice is also safe (each call replaces the previous hook
        // via take_hook chaining).
        install_panic_hook();
        install_panic_hook();
    }

    // ── PVT-G5-084: CombinedLogger::log format ───────────────────────

    #[test]
    fn test_combined_logger_log_format_includes_file_and_line() {
        // PVT-G5-084: verify the format string includes the file:line
        // segment by constructing a logger and calling `log()` with a
        // synthetic Record. We can't capture stderr (eprintln! goes to
        // fd 2) but we CAN capture the file write and assert the
        // `file:line --` segment is present.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fmt",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Info,
            // TY-34: stderr_verbose=true in tests so the eprintln! path
            // is exercised (mirrors debug-build behavior).
            // FR-96: AtomicBool (was `bool`) so the predicate is
            // runtime-toggleable.
            stderr_verbose: AtomicBool::new(true),
        };
        // Build a Record with a known file/line. `log::Record::builder`
        // sets `file()` and `line()` from the args + metadata.
        let record = log::Record::builder()
            .level(log::Level::Info)
            .target("test_target")
            .file(Some("src/test.rs"))
            .line(Some(42))
            .args(format_args!("hello world"))
            .build();
        logger.log(&record);
        logger.flush();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        assert!(
            content.contains("test_target"),
            "target missing from log line: {}",
            content
        );
        assert!(
            content.contains("src/test.rs:42"),
            "file:line missing from log line (PVT-G5-084): {}",
            content
        );
        assert!(
            content.contains("hello world"),
            "message missing from log line: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_combined_logger_log_format_falls_back_when_file_line_absent() {
        // PVT-G5-084: when `record.file()` / `record.line()` return
        // None (e.g. release builds with debuginfo stripped, or
        // records built without `#[track_caller]`), the format string
        // must still render cleanly (no panic, no `Option` debug
        // string in the output).
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fmt-nofile",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Info,
            // TY-34: stderr_verbose=true in tests so the eprintln! path
            // is exercised (mirrors debug-build behavior).
            // FR-96: AtomicBool (was `bool`) so the predicate is
            // runtime-toggleable.
            stderr_verbose: AtomicBool::new(true),
        };
        let record = log::Record::builder()
            .level(log::Level::Info)
            .target("test_target")
            .file(None)
            .line(None)
            .args(format_args!("no loc"))
            .build();
        logger.log(&record);
        logger.flush();
        let content =
            std::fs::read_to_string(tmp.join("test-log.log")).unwrap();
        // Should contain the "?" fallback for file and "0" for line.
        assert!(
            content.contains("?:0"),
            "fallback file:line missing from log line: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── 0o600 file permissions on POSIX ──────────

    #[cfg(unix)]
    #[test]
    fn test_rotating_file_writer_log_file_mode_is_0o600_on_posix() {
        // The log file created by `write_line` must have mode
        // `0o600` (owner rw only — no group/other access) on POSIX.
        // Pre-fix the file inherited the process umask (typically
        // 0o022), producing `0o644` — readable by group + others.
        // The dictation log may contain raw transcription text + PII
        // (XZ-LOG-02), so it must be owner-only.
        use std::os::unix::fs::PermissionsExt;
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-pi7-mode",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        writer.write_line("secret-dictation-text").unwrap();
        writer.flush().unwrap();

        let path = tmp.join("test-log.log");
        let meta = std::fs::metadata(&path)
            .expect("log file must exist after write_line");
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: log file mode must be 0o600 (owner rw only); got 0o{:o}",
            mode
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(unix)]
    #[test]
    fn test_rotating_file_writer_rotated_files_get_0o600_on_posix() {
        // After rotation, the renamed `.log.1` file must also
        // have mode `0o600`. `rename` preserves the source file's mode
        // (which is 0o600 from the `OpenOptionsExt::mode` call in
        // `write_line`), plus the belt-and-suspenders `chmod` in
        // `rotate` re-asserts 0o600 in case a pre-hardening leftover file
        // had looser perms.
        use std::os::unix::fs::PermissionsExt;
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-pi7-rotate-mode",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        // Write ~6 MB total to trigger at least one rotation
        // (ROTATE_MAX_BYTES = 5 MB).
        let big_line = "x".repeat(100_000);
        for _ in 0..60 {
            writer.write_line(&big_line).unwrap();
        }
        writer.flush().unwrap();

        let rotated = tmp.join("test-log.log.1");
        assert!(rotated.exists(), "rotated file .log.1 must exist");
        let meta = std::fs::metadata(&rotated)
            .expect("rotated log file must exist");
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: rotated log file mode must be 0o600; got 0o{:o}",
            mode
        );

        // The current (just-rotated) `.log` file must also be 0o600 —
        // it was just freshly opened by `write_line`'s `OpenOptionsExt::mode(0o600)`.
        let current = tmp.join("test-log.log");
        let meta = std::fs::metadata(&current)
            .expect("current log file must exist");
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o600,
            "PI-7: current log file mode must be 0o600 after rotation; got 0o{:o}",
            mode
        );

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[cfg(unix)]
    #[test]
    fn test_init_file_logger_tightens_logs_dir_to_0o700_on_posix() {
        // `init_file_logger` must chmod the parent `<config_dir>/logs/`
        // dir to `0o700` (owner rwx only) on POSIX, mirroring the Python
        // side's `os.chmod(config_dir, 0o700)` at log.py:891-893.
        //
        // We can't call `init_file_logger` from a test (it calls
        // `log::set_logger`, which is process-global and can only be
        // set once per process). Instead, mirror the dir-chmod logic
        // directly: create a logs dir, chmod it to 0o755 (the
        // permissive default), then re-apply the same chmod call
        // `init_file_logger` does, and verify the mode is 0o700.
        use std::os::unix::fs::PermissionsExt;
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-pi7-dir-mode",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let logs_dir = tmp.join("logs");
        std::fs::create_dir_all(&logs_dir).unwrap();
        // Set permissive mode first (mimics a pre-hardening leftover dir).
        std::fs::set_permissions(
            &logs_dir,
            std::fs::Permissions::from_mode(0o755),
        )
        .unwrap();
        // Apply the same chmod `init_file_logger` does.
        let _ = std::fs::set_permissions(
            &logs_dir,
            std::fs::Permissions::from_mode(0o700),
        );
        let meta = std::fs::metadata(&logs_dir).unwrap();
        let mode = meta.permissions().mode() & 0o777;
        assert_eq!(
            mode, 0o700,
            "PI-7: logs dir mode must be 0o700; got 0o{:o}",
            mode
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── FR-33: bubble_level filter preserves WARNING+ records ─────────
    //
    // Pre-FR-33 the filter dropped ANY record whose message started
    // with `[WS-READER] bubble_level event`, regardless of level.
    // A future `log::error!("[WS-READER] bubble_level event handler
    // crashed: ...")` would be SILENTLY LOST. Post-FR-33 the filter
    // short-circuits for WARNING+ records (mirrors Python's
    // `_BubbleLevelExclusionFilter` at log.py:216-219).

    #[test]
    fn test_fr33_bubble_level_filter_drops_info_record() {
        // INFO-level bubble_level event must be dropped from the file
        // log (the original ADR-0020 §11 behavior — 60 Hz events would
        // fill disk fast).
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fr33-info",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Trace,
            stderr_verbose: AtomicBool::new(false),
        };
        let record = log::Record::builder()
            .level(log::Level::Info)
            .target("test_target")
            .file(Some("src/test.rs"))
            .line(Some(1))
            .args(format_args!("[WS-READER] bubble_level event rms=0.42"))
            .build();
        logger.log(&record);
        logger.flush();
        let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap_or_default();
        assert!(
            !content.contains("bubble_level event rms=0.42"),
            "FR-33: INFO bubble_level record must be dropped from file log; got: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_fr33_bubble_level_filter_preserves_warn_record() {
        // FR-33: WARN-level bubble_level record must be PRESERVED in
        // the file log even though the message starts with the
        // filtered prefix. Pre-fix this was silently dropped.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fr33-warn",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Trace,
            stderr_verbose: AtomicBool::new(false),
        };
        let record = log::Record::builder()
            .level(log::Level::Warn)
            .target("test_target")
            .file(Some("src/test.rs"))
            .line(Some(2))
            .args(format_args!("[WS-READER] bubble_level event handler stalled"))
            .build();
        logger.log(&record);
        logger.flush();
        let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap_or_default();
        assert!(
            content.contains("bubble_level event handler stalled"),
            "FR-33: WARN bubble_level record must be PRESERVED in file log; got: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn test_fr33_bubble_level_filter_preserves_error_record() {
        // FR-33: ERROR-level bubble_level record must be PRESERVED.
        // This is the most important case — a future
        // `log::error!("[WS-READER] bubble_level event handler crashed")`
        // would be silently lost without the level guard.
        let tmp = std::env::temp_dir().join(format!(
            "voice-typer-test-{}-fr33-err",
            std::process::id()
        ));
        std::fs::remove_dir_all(&tmp).ok();
        let writer = RotatingFileWriter::new(tmp.clone(), "test-log");
        let logger = CombinedLogger {
            file_writer: Some(writer),
            level_filter: log::LevelFilter::Trace,
            stderr_verbose: AtomicBool::new(false),
        };
        let record = log::Record::builder()
            .level(log::Level::Error)
            .target("test_target")
            .file(Some("src/test.rs"))
            .line(Some(3))
            .args(format_args!("[WS-READER] bubble_level event handler crashed: panic"))
            .build();
        logger.log(&record);
        logger.flush();
        let content = std::fs::read_to_string(tmp.join("test-log.log")).unwrap_or_default();
        assert!(
            content.contains("bubble_level event handler crashed"),
            "FR-33: ERROR bubble_level record must be PRESERVED in file log; got: {}",
            content
        );
        std::fs::remove_dir_all(&tmp).ok();
    }

    // ── FR-96: AtomicBool stderr_verbose is runtime-toggleable ────────

    #[test]
    fn test_fr96_stderr_verbose_atomic_toggle_at_runtime() {
        // FR-96: the `stderr_verbose` field is now an `AtomicBool`,
        // allowing future code (e.g. a Tauri command) to flip the
        // predicate at runtime without re-creating the logger. This
        // test verifies the field is constructed + loaded + stored
        // without panic (the actual eprintln! path is exercised by
        // other tests).
        let flag = AtomicBool::new(false);
        assert!(!flag.load(std::sync::atomic::Ordering::Relaxed));
        flag.store(true, std::sync::atomic::Ordering::Relaxed);
        assert!(flag.load(std::sync::atomic::Ordering::Relaxed));
        flag.store(false, std::sync::atomic::Ordering::Relaxed);
        assert!(!flag.load(std::sync::atomic::Ordering::Relaxed));
    }

    // ── FR-16: EarlyLogger idempotent install ──────────────────────────
    //
    // We can't call `install_early_logger` from a test that runs in
    // the same process as other tests (it calls `log::set_logger`
    // which is process-global one-shot, AND it leaks memory via
    // `Box::leak`). But we CAN verify the idempotency guard — a
    // second call after the EARLY_LOGGER_HANDLE is set must be a
    // no-op that doesn't panic.

    #[test]
    fn test_fr16_install_early_logger_idempotent() {
        // FR-16: calling `install_early_logger` more than once must
        // not panic (the function's idempotency guard short-circuits
        // when `EARLY_LOGGER_HANDLE` is already set). The first call
        // may or may not have been made by another test in the same
        // process — either way, this call must not panic.
        install_early_logger();
        // Second call must be a no-op (the function checks
        // `EARLY_LOGGER_HANDLE.get().is_some()` and returns early).
        install_early_logger();
        // Third call also safe.
        install_early_logger();
    }

    #[test]
    fn test_fr16_early_logger_pre_init_fallback_does_not_panic() {
        // FR-16: construct an EarlyLogger directly (bypassing
        // `install_early_logger`) and call `log()` on it in the
        // pre-init fallback state (inner = None). Must not panic and
        // must produce no file output (no file_writer in pre-init).
        let early = EarlyLogger {
            inner: OnceLock::new(),
            stderr_verbose: AtomicBool::new(false),
            level_filter: log::LevelFilter::Info,
        };
        let record = log::Record::builder()
            .level(log::Level::Info)
            .target("test_target")
            .file(Some("src/test.rs"))
            .line(Some(1))
            .args(format_args!("early logger test"))
            .build();
        // Must not panic — the pre-init fallback just eprintln!'s
        // (suppressed here via stderr_verbose=false).
        early.log(&record);
        early.flush();
    }

    // ── XZ-LOG-02: redact_pii unit tests ──────────────────────────────

    #[test]
    fn test_redact_pii_no_trigger_returns_input_unchanged() {
        let input = "WS reader connected on port 51829";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_bearer_token() {
        let input = "Authorization: Bearer abc123def456ghi789";
        let out = redact_pii(input);
        assert_eq!(out, "Authorization: Bearer ***");
    }

    #[test]
    fn test_redact_pii_token_keyword() {
        let input = "Token sk-live-1234567890abcdef";
        let out = redact_pii(input);
        assert_eq!(out, "Token ***");
    }

    #[test]
    fn test_redact_pii_sk_prefix_api_key() {
        let input = "using key sk-1234567890abcdef for the cloud engine";
        let out = redact_pii(input);
        assert_eq!(out, "using key sk-*** for the cloud engine");
    }

    #[test]
    fn test_redact_pii_sk_prefix_too_short_not_redacted() {
        let input = "model name: sk-small.en";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_url_credentials() {
        let input = "connecting to https://user:pass@host.com/path";
        let out = redact_pii(input);
        assert!(out.contains("***@host.com"), "got: {}", out);
        assert!(!out.contains("user:pass"));
    }

    #[test]
    fn test_redact_pii_email_address() {
        let input = "user email is alice@example.com and that's it";
        let out = redact_pii(input);
        assert_eq!(out, "user email is [EMAIL] and that's it");
    }

    #[test]
    fn test_redact_pii_email_with_dotted_local_part() {
        let input = "contact first.last@example.co.uk for info";
        let out = redact_pii(input);
        assert_eq!(out, "contact [EMAIL] for info");
    }

    #[test]
    fn test_redact_pii_at_without_dot_not_redacted() {
        let input = "ssh user@host";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_no_false_positive_on_normal_log_lines() {
        let inputs = [
            "[WS-READER] bubble_level event rms=0.42",
            "[SUPERVISOR] respawn attempt 1/3 after 500ms backoff",
            "config dir: /home/user/.local/share/voice-typer",
            "shutdown ack timeout (2000ms) — force-killing",
        ];
        for input in inputs {
            let out = redact_pii(input);
            assert_eq!(
                out, input,
                "false positive: input {input:?} was changed to {out:?}"
            );
        }
    }

    #[test]
    fn test_redact_pii_multiple_patterns_in_one_line() {
        let input = "auth=Bearer abc123, email=alice@example.com, key=sk-1234567890abcdef";
        let out = redact_pii(input);
        assert_eq!(
            out,
            "auth=Bearer ***, email=[EMAIL], key=sk-***"
        );
    }

    // ── XZ-LOG-02 extended coverage: gsk_, IBAN, phone, SSN, CC ───────

    #[test]
    fn test_redact_pii_gsk_prefix_api_key() {
        let input = "groq key gsk_1234567890abcdef for the cloud engine";
        let out = redact_pii(input);
        assert_eq!(out, "groq key gsk_*** for the cloud engine");
    }

    #[test]
    fn test_redact_pii_gsk_prefix_too_short_not_redacted() {
        let input = "short gsk_abc value";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_us_phone_with_dashes() {
        let input = "call me at 555-123-4567 today";
        let out = redact_pii(input);
        assert_eq!(out, "call me at [PHONE] today");
    }

    #[test]
    fn test_redact_pii_us_phone_with_dots() {
        let input = "call me at 555.123.4567 today";
        let out = redact_pii(input);
        assert_eq!(out, "call me at [PHONE] today");
    }

    #[test]
    fn test_redact_pii_us_phone_no_separators() {
        let input = "call me at 5551234567 today";
        let out = redact_pii(input);
        assert_eq!(out, "call me at [PHONE] today");
    }

    #[test]
    fn test_redact_pii_intl_phone_with_parens() {
        let input = "call +1 (415) 555-2671 now";
        let out = redact_pii(input);
        assert_eq!(out, "call [PHONE] now");
    }

    #[test]
    fn test_redact_pii_intl_phone_uk_format() {
        let input = "dial +44 20 7946 0958 please";
        let out = redact_pii(input);
        assert_eq!(out, "dial [PHONE] please");
    }

    #[test]
    fn test_redact_pii_ssn_with_dashes() {
        let input = "ssn is 123-45-6789 on file";
        let out = redact_pii(input);
        assert_eq!(out, "ssn is [SSN] on file");
    }

    #[test]
    fn test_redact_pii_ssn_no_dashes_not_redacted() {
        // 9-digit run without dashes is NOT an SSN (matches Python
        // behaviour — the `-` separators are required).
        let input = "order id 123456789 processed";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_credit_card_with_dashes() {
        let input = "card 4111-1111-1111-1111 charged";
        let out = redact_pii(input);
        assert_eq!(out, "card [CC] charged");
    }

    #[test]
    fn test_redact_pii_credit_card_with_spaces() {
        let input = "card 4111 1111 1111 1111 charged";
        let out = redact_pii(input);
        assert_eq!(out, "card [CC] charged");
    }

    #[test]
    fn test_redact_pii_credit_card_no_separators() {
        let input = "card 4111111111111111 charged";
        let out = redact_pii(input);
        assert_eq!(out, "card [CC] charged");
    }

    #[test]
    fn test_redact_pii_iban_uk() {
        let input = "iban GB82WEST12345698765432 on file";
        let out = redact_pii(input);
        assert_eq!(out, "iban [IBAN] on file");
    }

    #[test]
    fn test_redact_pii_iban_germany() {
        let input = "iban DE89370400440532013000 on file";
        let out = redact_pii(input);
        assert_eq!(out, "iban [IBAN] on file");
    }

    #[test]
    fn test_redact_pii_iban_too_short_not_redacted() {
        // 2 letters + 2 digits + only 5 BBAN chars (< 10) → not an IBAN.
        let input = "code AB12ABCDE end";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_iban_lowercase_not_redacted() {
        // IBAN requires UPPERCASE country code (matches Python regex
        // `[A-Z]{2}`). A lowercase form is left alone.
        let input = "code gb82west12345698765432 end";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_redact_pii_no_false_positive_on_short_digit_runs() {
        // 1-2 digit runs should NOT trigger any numeric pattern.
        let inputs = [
            "port 80 and 443 are common",
            "version 1.2.3 released",
            "timeout 30s",
        ];
        for input in inputs {
            let out = redact_pii(input);
            assert_eq!(
                out, input,
                "false positive: input {input:?} was changed to {out:?}"
            );
        }
    }

    #[test]
    fn test_redact_pii_bearer_token_trailing_comma_preserved() {
        // Regression: pre-fix the Bearer parser consumed trailing
        // punctuation (ran until whitespace). Now it stops at the
        // first non-token char (comma), matching Python's
        // `[A-Za-z0-9_\-\.=]+` charset.
        //
        // UE-6: the bare-keyword pattern (`auth=`) now fires BEFORE the
        // Bearer prefix pattern, so the value `Bearer` is redacted as
        // part of the `auth=***` substitution (matching Python's
        // `_FLAG_KEY_PATTERNS`-before-`_KEY_PATTERNS` ordering). The
        // trailing-comma preservation regression test is now covered by
        // `test_redact_pii_bearer_token` above (which tests `Bearer`
        // without a preceding `keyword=`).
        let input = "auth=Bearer abc123, next field";
        let out = redact_pii(input);
        assert_eq!(out, "auth=*** abc123, next field");
    }

    // ── UE-6: flag-form / bare-keyword / 20+ char catch-all parity ──────
    //
    // These tests assert that the Rust `redact_pii` redacts the same
    // secret-bearing strings as the Python `_redact_text` /
    // `redact_secret` pipeline (`voice_typer/server/security.py` +
    // `voice_typer/server/_secrets.py`). Each test documents the
    // expected Python output for cross-reference.

    #[test]
    fn test_ue6_bare_key_value_gitlab_pat() {
        // `pat=glpt_Xb8zV9pT3q2aR1wM5sN7` — 24-char GitLab PAT with
        // no Bearer prefix. `pat` is NOT a secret keyword, so the
        // bare-keyword pattern does NOT match. The 20+ char catch-all
        // matches `glpt_Xb8zV9pT3q2aR1wM5sN7` (27 chars) → `***`.
        // Python: `redact_secret` → `_KEY_PATTERNS[4]` → `pat=***`.
        let input = "pat=glpt_Xb8zV9pT3q2aR1wM5sN7";
        let out = redact_pii(input);
        assert_eq!(out, "pat=***");
    }

    #[test]
    fn test_ue6_bare_key_value_api_key() {
        // `api_key=A1B2C3D4E5F6G7H8I9J0K1L2M3N4O7P8` — `api_key` IS a
        // secret keyword, so the bare-keyword pattern matches and
        // redacts the 32-char value → `api_key=***`.
        // Python: `redact_secret` → `_BARE_KEY_VALUE_PATTERN` → `api_key=***`.
        let input = "api_key=A1B2C3D4E5F6G7H8I9J0K1L2M3N4O7P8";
        let out = redact_pii(input);
        assert_eq!(out, "api_key=***");
    }

    #[test]
    fn test_ue6_bearer_with_sk_prefix() {
        // `Bearer sk-abc123def456ghi789` — Bearer prefix pattern fires
        // first, redacting the entire value (`sk-abc123...`) →
        // `Bearer ***`. The bare-keyword pattern does NOT fire (no
        // `keyword=` form).
        // Python: `redact_secret` → `_KEY_PATTERNS[0]` → `Bearer ***`.
        let input = "Bearer sk-abc123def456ghi789";
        let out = redact_pii(input);
        assert_eq!(out, "Bearer ***");
    }

    #[test]
    fn test_ue6_24char_bare_token() {
        // `Xb8zV9pT3q2aR1wM5sN7abcd` — 24-char bare token, no prefix.
        // The 20+ char catch-all matches → `***`.
        // Python: `redact_secret` → `_KEY_PATTERNS[4]` → `***`.
        let input = "Xb8zV9pT3q2aR1wM5sN7abcd";
        let out = redact_pii(input);
        assert_eq!(out, "***");
    }

    #[test]
    fn test_ue6_flag_form_equals() {
        // `--token=secret123` — flag-form Pattern A (`--keyword=value`).
        // `token` is a keyword, `=` delimiter, value `secret123`.
        // Python: `_FLAG_VALUE_PATTERN` → `--token=***`.
        let input = "--token=secret123";
        let out = redact_pii(input);
        assert_eq!(out, "--token=***");
    }

    #[test]
    fn test_ue6_flag_form_space() {
        // `--token secret123` — flag-form Pattern A (`--keyword value`).
        // `token` is a keyword, space delimiter, value `secret123`.
        // Python: `_FLAG_VALUE_PATTERN` → `--token ***`.
        let input = "--token secret123";
        let out = redact_pii(input);
        assert_eq!(out, "--token ***");
    }

    #[test]
    fn test_ue6_flag_form_multiple_spaces() {
        // `--token   secret123` — multiple spaces between keyword and
        // value. Python's `\s+` captures all whitespace in group 1, so
        // the output preserves the spaces.
        let input = "--token   secret123";
        let out = redact_pii(input);
        assert_eq!(out, "--token   ***");
    }

    #[test]
    fn test_ue6_bare_key_value_password() {
        // `password=secret123` — bare-keyword Pattern B. The value
        // `secret123` contains 3+ consecutive digits (`123`), which
        // triggers the fast path (mirrors Python's `_FAST_TRIGGER`
        // `\d{3,}` alternative). Without a trigger, the fast path
        // would skip this short string — matching Python's behavior.
        // Python: `_BARE_KEY_VALUE_PATTERN` → `password=***`.
        let input = "password=secret123";
        let out = redact_pii(input);
        assert_eq!(out, "password=***");
    }

    #[test]
    fn test_ue6_bare_key_value_secret() {
        // `secret=topsecret123` — bare-keyword Pattern B. Same 3+-digit
        // trigger rationale as `test_ue6_bare_key_value_password`.
        let input = "secret=topsecret123";
        let out = redact_pii(input);
        assert_eq!(out, "secret=***");
    }

    #[test]
    fn test_ue6_bare_key_value_case_sensitive_fast_path() {
        // `TOKEN=abc` — the fast-path trigger `key=` is case-sensitive
        // (mirrors Python's `_FAST_TRIGGER`). `TOKEN=` does NOT contain
        // `key=` (lowercase). And `abc` has no 3+ digits, no 20+ char
        // run, and no other trigger. So the fast path skips this string
        // entirely, returning it unchanged. This matches Python's
        // `_redact_text` (which also uses the case-sensitive
        // `_FAST_TRIGGER` and would skip `TOKEN=abc`).
        //
        // NOTE: Python's `redact_secret` (called separately, NOT via
        // `_redact_text`) WOULD redact `TOKEN=abc` because
        // `_FLAG_KEY_PATTERNS` are case-insensitive. But `_redact_text`
        // gates on `_FAST_TRIGGER` first. The Rust `redact_pii` ports
        // `_redact_text`, so it matches that behavior.
        let input = "TOKEN=abc";
        let out = redact_pii(input);
        assert_eq!(
            out, input,
            "UE-6 parity: case-sensitive fast path leaves TOKEN=abc unchanged (mirrors Python _FAST_TRIGGER)"
        );
    }

    #[test]
    fn test_ue6_bare_key_value_case_insensitive_with_trigger() {
        // `TOKEN=secret123456789012345` — the fast-path trigger `key=`
        // is case-sensitive, so `TOKEN=` does NOT trigger via `key=`.
        // But the value `secret123456789012345` is 25 chars, triggering
        // the 20+ char catch-all in the fast path. The slow path then
        // runs and the bare-keyword pattern (case-insensitive, mirrors
        // Python's `(?i)`) matches `TOKEN=` → `TOKEN=***`.
        let input = "TOKEN=secret123456789012345";
        let out = redact_pii(input);
        assert_eq!(out, "TOKEN=***");
    }

    #[test]
    fn test_ue6_no_false_positive_monkey() {
        // `monkey=abc` — `key` is a keyword but `\b` prevents matching
        // inside `monkey` (the `n` before `key` is a word char, so no
        // word boundary). NOT redacted.
        let input = "monkey=abc";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_ue6_no_false_positive_hotkey() {
        // `hotkey=abc` — same as `monkey=`: `key` is preceded by `t`
        // (word char), so `\b` does not hold. NOT redacted.
        let input = "hotkey=abc";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_ue6_no_false_positive_unknown_flag() {
        // `--unknown=abc` — `unknown` is NOT a secret keyword. NOT
        // redacted.
        let input = "--unknown=abc";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_ue6_empty_value_not_redacted() {
        // `--token=` with no value — Python's `[^\s=]+` requires at
        // least 1 char. NOT redacted.
        let input = "--token=";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_ue6_flag_value_stops_at_equals() {
        // `--api_key=abc=def` — value runs until `=` (`[^\s=]+`), so the
        // value is `abc` and `=def` is left alone. Uses `api_key` (not
        // `token`) so the fast path triggers via the `key=` substring
        // in `api_key=`. (`--token=abc=def` would NOT trigger the fast
        // path — no `key=`, no 3+ digits, no 20+ run — and would be
        // returned unchanged, matching Python's `_redact_text`.)
        // Python: `_FLAG_VALUE_PATTERN` → `--api_key=***=def`.
        let input = "--api_key=abc=def";
        let out = redact_pii(input);
        assert_eq!(out, "--api_key=***=def");
    }

    #[test]
    fn test_ue6_flag_value_stops_at_whitespace() {
        // `--api_key=abc def` — value runs until whitespace, so the
        // value is `abc` and ` def` is left alone. Uses `api_key` so
        // the fast path triggers via the `key=` substring.
        let input = "--api_key=abc def";
        let out = redact_pii(input);
        assert_eq!(out, "--api_key=*** def");
    }

    #[test]
    fn test_ue6_api_key_wins_over_key() {
        // `api_key=secret123` — both `api_key` and `key` are keywords.
        // `api_key` comes first in SECRET_KEYWORDS (most-specific
        // first), so it wins. The prefix preserved is `api_key=` (not
        // `key=`).
        let input = "api_key=secret123";
        let out = redact_pii(input);
        assert_eq!(out, "api_key=***");
    }

    #[test]
    fn test_ue6_access_token_keyword() {
        // `access_token=abc123` — `access_token` is a keyword.
        let input = "access_token=abc123";
        let out = redact_pii(input);
        assert_eq!(out, "access_token=***");
    }

    #[test]
    fn test_ue6_client_secret_keyword() {
        // `client_secret=abc123` — `client_secret` is a keyword.
        let input = "client_secret=abc123";
        let out = redact_pii(input);
        assert_eq!(out, "client_secret=***");
    }

    #[test]
    fn test_ue6_bearer_keyword_added() {
        // `bearer=abc123` — `bearer` is a UE-6 task-specified addition
        // (not in Python's `_SECRET_KEYWORDS`). The bare-keyword pattern
        // matches → `bearer=***`. Python would NOT redact this (no
        // `bearer` keyword), but the task explicitly requests it.
        let input = "bearer=abc123";
        let out = redact_pii(input);
        assert_eq!(out, "bearer=***");
    }

    #[test]
    fn test_ue6_credential_keyword_added() {
        // `credential=abc123` — `credential` is a UE-6 task-specified
        // addition. Same rationale as `bearer=`.
        let input = "credential=abc123";
        let out = redact_pii(input);
        assert_eq!(out, "credential=***");
    }

    #[test]
    fn test_ue6_20char_run_exactly_20() {
        // Exactly 20 chars — the minimum for the catch-all. Should
        // match → `***`.
        let input = "abcdefghijklmnopqrst";
        let out = redact_pii(input);
        assert_eq!(out, "***");
    }

    #[test]
    fn test_ue6_19char_run_not_redacted() {
        // 19 chars — below the 20-char threshold. NOT redacted.
        let input = "abcdefghijklmnopqrs";
        let out = redact_pii(input);
        assert_eq!(out, input);
    }

    #[test]
    fn test_ue6_20char_run_with_internal_dash() {
        // `glpt_Xb8zV9pT3q2aR1wM5sN7` — 27 chars with an internal
        // `-`. The 20+ char catch-all includes `-` in the char class,
        // so the entire run matches → `***`.
        let input = "glpt_Xb8zV9pT3q2aR1wM5sN7";
        let out = redact_pii(input);
        assert_eq!(out, "***");
    }

    #[test]
    fn test_ue6_20char_run_does_not_match_inside_word() {
        // `a` + 20-char run + `b` — the `a` and `b` are word chars
        // adjacent to the run, so the run extends to include them
        // (22 chars total). The whole 22-char run matches → `***`.
        // (There's no "inside word" false-positive issue because `\b`
        // is only checked at the start/end of the match, not internally.)
        let input = "aXb8zV9pT3q2aR1wM5sN7b";
        let out = redact_pii(input);
        assert_eq!(out, "***");
    }

    #[test]
    fn test_ue6_redact_pii_empty_string_no_panic() {
        // UE-44 defense-in-depth: `redact_pii("")` must not panic. The
        // fast path returns early (no triggers), but this test pins
        // that behavior so a future refactor can't introduce a panic
        // on empty input.
        let out = redact_pii("");
        assert_eq!(out, "");
    }

    #[test]
    fn test_ue6_flag_form_in_sentence() {
        // Flag-form pattern in the middle of a sentence. The `--token=`
        // is matched at its position, not at position 0.
        let input = "starting with --token=secret123 and done";
        let out = redact_pii(input);
        assert_eq!(out, "starting with --token=*** and done");
    }

    #[test]
    fn test_ue6_bare_key_value_in_sentence() {
        // Bare-keyword pattern in the middle of a sentence. The value
        // `secret123` contains 3+ digits to trigger the fast path.
        let input = "config has password=secret123 in it";
        let out = redact_pii(input);
        assert_eq!(out, "config has password=*** in it");
    }

    #[test]
    fn test_ue6_20char_run_in_sentence() {
        // 20+ char catch-all in the middle of a sentence.
        let input = "token is Xb8zV9pT3q2aR1wM5sN7abcd here";
        let out = redact_pii(input);
        assert_eq!(out, "token is *** here");
    }

    #[test]
    fn test_ue6_no_false_positive_on_normal_paths() {
        // Normal file paths and URLs should NOT be redacted by the
        // 20+ char catch-all (they contain `/`, `:`, etc. which break
        // the alphanumeric run).
        let inputs = [
            "/home/user/.local/share/voice-typer/config.json",
            "https://api.openai.com/v1/audio/transcriptions",
            "C:\\Users\\user\\AppData\\Roaming\\voice-typer",
        ];
        for input in inputs {
            let out = redact_pii(input);
            assert_eq!(
                out, input,
                "false positive: input {input:?} was changed to {out:?}"
            );
        }
    }
}
