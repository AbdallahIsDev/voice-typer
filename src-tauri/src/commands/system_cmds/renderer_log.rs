//! Renderer error-log sink: the `renderer_log_error` Tauri command plus
//! its bounded-payload serialization core.
//!
//! The React UI's `__tauriLog.error(...)` invokes this command so
//! uncaught UI errors land in the host-side rotating log file
//! (`<config_dir>/logs/voice-typer-rust.log` via the existing
//! `log::error!` global logger) for operator triage without requiring
//! DevTools to be open.
//!
//! The payload is an opaque JSON value — the renderer sends
//! `{message, stack?, componentStack?, location?}`. We serialize it to
//! a single line and emit via `log::error!` with a `[RENDERER_ERROR]`
//! prefix. Returns `Ok(())` unconditionally — the renderer's promise
//! resolves so its `__tauriLog.error` call doesn't itself become an
//! unhandled rejection.
//!
//! Payload size is capped at 8 KiB DURING serialization (not after):
//! the React UI's `__tauriLog.error(...)` is called with arbitrary
//! `Value` payloads — a runaway renderer could pass a multi-MB object
//! (e.g. an entire Redux state dump, a circular-ref retry, or a stack
//! trace from a deeply-recursive crash). The former
//! serialize-then-truncate approach materialized the FULL multi-MB
//! string before throwing away all but 8 KiB; the bounded writer below
//! caps the heap at ~8 KiB AND aborts the serializer at the first byte
//! past the cap, so an oversized payload costs neither the memory nor
//! the CPU of a full serialization pass. The 8 KiB cap matches the
//! typical size of a rich error report (message + stack +
//! componentStack + location) while preventing the log from being
//! dominated by a single pathological payload. Truncation is marked
//! with `...[truncated]` so operators can see the cap was hit.

use serde::Serialize;
use serde_json::Value;

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;

/// Cap (in bytes) on the serialized renderer error payload. Larger
/// payloads are truncated with a visible marker so operators know the
/// cap fired.
const MAX_RENDERER_ERROR_PAYLOAD_BYTES: usize = 8 * 1024;

/// `io::Write` sink that collects at most `cap` bytes, then aborts the
/// stream. Once the buffer is full (or a write would overflow it), the
/// writer records `overflowed = true` and returns an error, which
/// stops `serde_json` mid-serialization — no further allocation or CPU
/// is spent on bytes that would only be discarded. The buffered prefix
/// is exactly the first `cap` bytes of the full serialization (when
/// the payload exceeds the cap), i.e. byte-identical to the former
/// serialize-then-`truncate(cap)` output, minus the panic risk (see
/// `cap_and_serialize_renderer_payload` for the UTF-8 boundary detail).
struct CappedWriter {
    buf: Vec<u8>,
    cap: usize,
    overflowed: bool,
}

impl CappedWriter {
    fn new(cap: usize) -> Self {
        Self {
            buf: Vec::with_capacity(cap),
            cap,
            overflowed: false,
        }
    }

    fn cap_error() -> std::io::Error {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            "renderer error payload exceeded the 8 KiB cap (intentional abort)",
        )
    }
}

impl std::io::Write for CappedWriter {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        let remaining = self.cap - self.buf.len();
        if remaining == 0 {
            // Buffer already exactly full — any further byte proves the
            // payload exceeds the cap. Fail fast.
            self.overflowed = true;
            return Err(Self::cap_error());
        }
        let take = remaining.min(data.len());
        self.buf.extend_from_slice(&data[..take]);
        if take < data.len() {
            // Partial fit — the payload continues past the cap. The
            // accepted prefix is already buffered; abort the rest.
            self.overflowed = true;
            return Err(Self::cap_error());
        }
        Ok(take)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        // All bytes are already in `buf`; nothing to flush.
        Ok(())
    }
}

/// Serialize `payload` to a single JSON string capped at
/// `MAX_RENDERER_ERROR_PAYLOAD_BYTES` bytes. Oversized payloads are
/// truncated to the cap and suffixed with `...[truncated]`.
///
/// Output is byte-identical to the former serialize-then-truncate
/// implementation for every input that did not panic, with one
/// deliberate improvement: when the 8 KiB boundary lands inside a
/// multi-byte UTF-8 char, the old `String::truncate` PANICKED
/// (truncate requires a char boundary; serde_json emits raw non-ASCII
/// unescaped, so a payload with e.g. CJK text straddling the boundary
/// was reachable from a compromised renderer). The truncated prefix is
/// now floored to the nearest char boundary, keeping the logging path
/// panic-free.
///
/// The `<unserializable>` fallback mirrors the former
/// `serde_json::to_string(..).unwrap_or_else(..)` arm — unreachable for
/// `serde_json::Value` in practice, kept so the contract is unchanged.
pub(crate) fn cap_and_serialize_renderer_payload(payload: &Value) -> String {
    let mut writer = CappedWriter::new(MAX_RENDERER_ERROR_PAYLOAD_BYTES);
    {
        let mut serializer = serde_json::Serializer::new(&mut writer);
        if let Err(_ser_err) = payload.serialize(&mut serializer) {
            if !writer.overflowed {
                // A genuine serialization failure (not the cap abort).
                return "<unserializable>".to_string();
            }
            // Else: the error IS the cap abort — fall through with the
            // truncated prefix in `writer.buf`.
        }
        // `serializer` borrows `writer` mutably; it is dropped at the
        // end of this block. All emitted bytes are already inside
        // `writer.buf` (our `flush` is a no-op), so no flush is needed.
    }

    if writer.overflowed {
        // Floor the truncated prefix to a UTF-8 char boundary so the
        // log line stays valid UTF-8 (see doc above for the panic this
        // replaces). `from_utf8` reports the longest valid prefix via
        // `valid_up_to` — no allocation, no replacement characters.
        let valid_len = match std::str::from_utf8(&writer.buf) {
            Ok(_) => writer.buf.len(),
            Err(e) => e.valid_up_to(),
        };
        // The prefix is valid UTF-8 by construction, so the lossy
        // conversion is byte-exact (no U+FFFD replacement possible).
        let mut out = String::from_utf8_lossy(&writer.buf[..valid_len]).into_owned();
        out.push_str("...[truncated]");
        out
    } else {
        // Complete serialization — serde_json only ever emits valid
        // UTF-8, so the lossy conversion is byte-exact here too.
        String::from_utf8_lossy(&writer.buf).into_owned()
    }
}

/// Tauri command: sink for renderer-side error logs. See the module
/// doc for the payload contract + cap rationale.
///
/// Main-window-origin guard: without this, a compromised bubble
/// renderer (withGlobalTauri: true) could invoke
/// `invoke('renderer_log_error', payload)` directly and flood the
/// 25 MiB rotating log at 60 Hz × 8 KiB ≈ 480 KiB/s, evicting real
/// diagnostic logs in ~52 s. The `window` parameter is auto-injected
/// by Tauri at runtime — the renderer's invoke() call is unchanged.
#[tauri::command]
pub async fn renderer_log_error(
    payload: Value,
    window: tauri::Window,
    _app: tauri::AppHandle,
) -> Result<(), VoiceTyperError> {
    require_main_window(&window)?;
    let serialized = cap_and_serialize_renderer_payload(&payload);
    log::error!("[RENDERER_ERROR] {}", serialized);
    Ok(())
}

// Unit tests for `cap_and_serialize_renderer_payload` (cap boundary,
// truncation marker, UTF-8-boundary safety) live in the sibling
// `renderer_log_tests.rs` file (C-TEST-5 — no inline test code in
// production source, matching the `window_close_tests.rs` pattern).
#[cfg(test)]
#[path = "renderer_log_tests.rs"]
mod renderer_log_tests;
