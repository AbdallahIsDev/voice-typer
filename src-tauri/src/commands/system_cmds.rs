//! System-level window commands: open_logs, open_model_import_dialog,
//! export_templates, export_config ( +  + MODEL-IMPORT +
//! ).
//!
//! : these 4 commands were missing from the Tauri host — the
//! renderer's `window.window_` bridge declared them as optional
//! (`openLogs?`, `openModelImportDialog?`, `exportTemplates?`,
//! `exportConfig?`) so the type system allowed them to be undefined
//! at runtime, and the Tauri bridge never wired them. The Models page
//! (`Models.tsx`) printed "import not available outside Electron"
//! under Tauri; the Settings page's "View Logs" button no-op'd; the
//! Templates page's export button silently failed. This module ports
//! the 4 Electron IPC handlers from
//! `voice_typer/client/src/main/ipc/{window-handlers,export-handlers}.ts`
//! to Tauri commands so the renderer is unchanged on both runtimes.
//!
//! Module is `pub(crate)` — only `commands::mod` and
//! `tauri::generate_handler!` in `main.rs` reference its items.

use serde_json::{json, Value};
use tauri_plugin_dialog::DialogExt;
use tokio::sync::oneshot;

use crate::commands::export::export_data;
use crate::commands::require_main_window;
use crate::platform::open_path::open_path_in_file_manager;
use crate::platform::paths::config_dir;

//defense-in-depth config redaction ─────────────────────────
//
// The Python sidecar is contractually responsible for redacting API
// keys / secrets / tokens BEFORE the config payload reaches the Rust
// `export_config` command (see `voice_typer/server/credential_store.py`
//`_redact_sensitive`).  adds a Rust-side redaction pass as
// defense-in-depth: if a future sidecar refactor or a custom build of
// the renderer forgets to redact, the Rust host still scrubs obvious
// secret-shaped keys before writing the JSON to disk. Without this,
// any regression in the Python redaction path silently leaks API keys
// into the user's chosen export file (a GDPR / security incident).
//
// The key-matching regex is `(?i)(api[_-]?key|secret|token|password|passwd|pwd|credential|auth)`
// — same shape used by the Python `_secrets._KEY_PATTERNS` allowlist,
// applied case-insensitively as a substring match (the regex is
// unanchored, so `my_api_key_v2` and `X-Auth-Token` both match). We
// implement it without the `regex` crate (substring check on the
// lowercased key) to avoid pulling a new dependency into the Tauri
// host — the pattern is simple enough that hand-rolling the matcher
// is cleaner than adding `regex` to `Cargo.toml`.
//
// When a redaction fires, we log at `warn` so it lands in the
// rotating log file for post-mortem diagnosis (a redaction firing at
// this layer means the Python sidecar's redaction FAILED — that's a
// bug worth investigating).

/// Marker value substituted in place of redacted secrets. Matches the
/// Python sidecar's `_REDACTED` literal shape so the exported JSON is
/// consistent regardless of which layer did the redaction.
pub(crate) const REDACTED_MARKER: &str = "***REDACTED***";

//return `true` if `key` matches the redaction pattern
/// `(?i)(api[_-]?key|secret|token|password|passwd|pwd|credential|auth)`
/// (case-insensitive substring match — the regex is unanchored).
pub(crate) fn is_sensitive_key(key: &str) -> bool {
    let k = key.to_ascii_lowercase();
    // `api[_-]?key` → "api_key", "api-key", "apikey" (and any
    // key containing those as a substring, e.g. "openai_api_key").
    // We check all three spellings because the regex `[_-]?` makes
    // the separator optional.
    if k.contains("api_key") || k.contains("api-key") || k.contains("apikey") {
        return true;
    }
    // The remaining alternatives are plain substring checks. Using
    // `contains` (not `==`) so keys like "auth_token" or
    // "client_secret_v2" still match — same semantics as the
    // unanchored regex.
    k.contains("secret")
        || k.contains("token")
        || k.contains("password")
        || k.contains("passwd")
        || k.contains("pwd")
        || k.contains("credential")
        || k.contains("auth")
}

//walk a JSON `Value` recursively and replace every value
/// whose key matches [`is_sensitive_key`] with the
/// [`REDACTED_MARKER`] literal. Mutates `value` in place. Returns the
/// count of redactions performed so the caller can log a single
/// summary line (and so tests can assert the count).
///
/// Recurses into objects and arrays. Non-container values (strings,
/// numbers, bools, null) are leaves — they're only redacted if their
/// PARENT key is sensitive (handled by the parent's iteration).
pub(crate) fn redact_config_secrets(value: &mut Value) -> usize {
    let mut count = 0usize;
    redact_config_secrets_inner(value, None, &mut count);
    count
}

/// Recursive helper. `parent_key` is the key under which `value`
/// lives (None at the root) — used to decide whether to replace
/// `value` wholesale (if the parent key is sensitive) or to recurse
/// into it.
fn redact_config_secrets_inner(value: &mut Value, parent_key: Option<&str>, count: &mut usize) {
    // If the parent key is sensitive, replace this value wholesale
    // with the redaction marker (regardless of type — a secret could
    // be a string, number, bool, or even a nested object the sidecar
    // failed to scrub).
    if let Some(key) = parent_key {
        if is_sensitive_key(key) {
            // Only redact non-null values — redacting `null` would
            // be a false positive (a sensitive key with no value is
            // not a leak). This also avoids logging a `warn` line
            // for empty secrets, which would be noise.
            if !value.is_null() {
                log::warn!(
                    "[REDACT-DEFENSE] redacted sensitive config key {:?} (value type: {}) — \
                     Python-side redaction may have missed this",
                    key,
                    match value {
                        Value::String(_) => "string",
                        Value::Number(_) => "number",
                        Value::Bool(_) => "bool",
                        Value::Array(_) => "array",
                        Value::Object(_) => "object",
                        Value::Null => "null",
                    }
                );
                *value = Value::String(REDACTED_MARKER.to_string());
                *count += 1;
            }
            return;
        }
    }

    // Otherwise recurse into containers.
    match value {
        Value::Object(map) => {
            // Collect keys first to avoid borrow issues while mutating.
            let keys: Vec<String> = map.keys().cloned().collect();
            for key in keys {
                if let Some(child) = map.get_mut(&key) {
                    redact_config_secrets_inner(child, Some(&key), count);
                }
            }
        }
        Value::Array(arr) => {
            // Array elements have no key — pass None so they're only
            // redacted if their value happens to be an object whose
            // OWN keys are sensitive (handled by the Object arm above
            // on the next recursion).
            for child in arr.iter_mut() {
                redact_config_secrets_inner(child, None, count);
            }
        }
        // Leaves: nothing to do (the parent-key check above already
        // handled redaction).
        _ => {}
    }
}

//Tauri command: open_logs () ────────────────────────────────

//open the Voice Typer log directory in the OS file manager.
///
/// Mirrors the Electron `window:open-logs` IPC handler in
/// `voice_typer/client/src/main/ipc/window-handlers.ts:57-76` which
/// calls `shell.openPath(logDir)`. The Tauri path uses the OS-native
/// open command via `std::process::Command`:
/// - Windows: `explorer.exe <path>`
/// - macOS:   `open <path>`
/// - Linux:   `xdg-open <path>`
///
/// The path is the same `config_dir` used by the Python sidecar's
/// `_paths.config_dir()` resolution (see `platform::paths::config_dir`)
/// — under Tauri the logs live under `<config_dir>/voice-typer/` (NOT
/// the Electron `~/.voice-typer/` path the old handler used, because
/// the Tauri host writes to the platform-canonical config dir per
/// ADR-0020 §8).
///
//the response no longer includes the `path` field — the
/// absolute path can contain the user's home directory / username
/// (PII leak in shared logs / crash reports), and no renderer call
/// site consumes it (`window-namespace.ts::openLogs` strips `path`
/// before returning to the React layer). Returns
/// `{"success": true}` on success or `{"success": false, "error":
/// "<msg>"}` on failure.
///
//`window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('open_logs')` call is unchanged.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger OS file-manager opens.
#[tauri::command]
pub async fn open_logs(_app: tauri::AppHandle, window: tauri::Window) -> Result<Value, String> {
    require_main_window(&window)?;
    let log_dir = config_dir();
    // Offload the synchronous fs mkdir + the OS file-manager spawn to
    // the dedicated blocking-thread pool so this `async fn` does not
    // hold a Tauri async-runtime worker thread for the duration of the
    // mkdir syscall (which can stall >100ms under a contended disk /
    // antivirus scan on Windows). Mirrors the pattern already used by
    // `migrate::mod::migrate_electron_userdata` (line ~143) and
    // `sidecar::supervisor::restart_loop` (line ~277). `spawn_blocking`
    // moves the closure to the cached blocking pool; `.await` yields
    // the calling task until the closure completes.
    //
    // `open_path_in_file_manager` is also moved into the closure
    // because it does its own `path.exists()` syscall + spawns an
    // OS-binary child (explorer.exe / open / xdg-open) — both are
    // blocking work that should NOT run on the async worker pool.
    // The closure returns a single `Result<(), String>` so we can
    // uniformly shape both the mkdir failure and the open failure into
    // the same `{"success": false, "error": "<msg>"}` envelope.
    let blocking_result = tauri::async_runtime::spawn_blocking(move || {
        // Capture mkdir failure rather than silently discarding it
        // with `let _ = ...`. If the config_dir is unwritable
        // (permission denied, read-only mount, etc.), returning
        // `Ok(())` based solely on whether `Command::spawn()` later
        // succeeded would let the OS file manager pop a "path not
        // found" dialog to the user while the UI showed "logs opened".
        // We surface the mkdir failure as a structured error string so
        // the renderer can display it.
        if let Err(e) = std::fs::create_dir_all(&log_dir) {
            return Err(format!(
                "create_dir_all({}) failed: {}",
                log_dir.display(),
                e
            ));
        }
        open_path_in_file_manager(&log_dir)
    })
    .await;
    match blocking_result {
        Ok(Ok(())) => Ok(json!({"success": true})),
        Ok(Err(e)) => Ok(json!({"success": false, "error": e})),
        Err(join_err) => Ok(json!({
            "success": false,
            "error": format!("open_logs blocking task failed: {join_err}")
        })),
    }
}

//sink for renderer-side error logs. The React UI's
/// `__tauriLog.error(...)` invokes this command so uncaught UI errors
/// land in the host-side rotating log file (`<config_dir>/logs/voice-typer-rust.log`
/// via the existing `log::error!` global logger) for operator triage
/// without requiring DevTools to be open.
///
/// The payload is an opaque JSON value — the renderer sends
/// `{message, stack?, componentStack?, location?}`. We serialize it
/// to a single line and emit via `log::error!` with a `[RENDERER_ERROR]`
/// prefix. Returns `Ok(())` unconditionally — the renderer's promise
/// resolves so its `__tauriLog.error` call doesn't itself become an
/// unhandled rejection.
///
//the doc path above was updated from `voice-typer.log` to
//`voice-typer-rust.log` to match the  rename in
/// `platform::logging`.
///
//payload size is capped at 8 KiB after serialization. The
/// React UI's `__tauriLog.error(...)` is called with arbitrary
/// `Value` payloads — a runaway renderer could pass a multi-MB
/// object (e.g. an entire Redux state dump, a circular-ref retry,
/// or a stack trace from a deeply-recursive crash) that would bloat
/// the rotating file log + block the `eprintln!` path on every
/// `log::error!` call. The 8 KiB cap matches the typical size of a
/// rich error report (message + stack + componentStack + location)
/// while preventing the log from being dominated by a single
/// pathological payload. Truncation is marked with `...[truncated]`
/// so operators can see the cap was hit.
#[tauri::command]
pub async fn renderer_log_error(
    payload: Value,
    window: tauri::Window,
    _app: tauri::AppHandle,
) -> Result<(), String> {
    //main-window-origin guard. Without this, a compromised
    // bubble renderer (withGlobalTauri: true) could invoke
    // `invoke('renderer_log_error', payload)` directly and flood the
    // 25 MiB rotating log at 60 Hz × 8 KiB ≈ 480 KiB/s, evicting real
    // diagnostic logs in ~52 s. The `window` parameter is auto-injected
    // by Tauri at runtime — the renderer's invoke() call is unchanged.
    require_main_window(&window)?;
    let mut serialized =
        serde_json::to_string(&payload).unwrap_or_else(|_| "<unserializable>".to_string());
    //cap serialized payload at 8 KiB so a runaway renderer
    // can't bloat the rotating file log with a multi-MB error report.
    // The cap matches the typical rich-error-report size (message +
    // stack + componentStack + location); larger payloads are
    // truncated with a visible marker so operators know the cap fired.
    const MAX_RENDERER_ERROR_PAYLOAD_BYTES: usize = 8 * 1024;
    if serialized.len() > MAX_RENDERER_ERROR_PAYLOAD_BYTES {
        serialized.truncate(MAX_RENDERER_ERROR_PAYLOAD_BYTES);
        serialized.push_str("...[truncated]");
    }
    log::error!("[RENDERER_ERROR] {}", serialized);
    Ok(())
}

// ─── Tauri command: open_model_import_dialog (MODEL-IMPORT) ───────────

/// MODEL-IMPORT: open a native folder picker for importing HuggingFace
/// model cache directories. The user picks a folder; the renderer then
/// passes the path to `dispatch({cmd:'import_model', data:{path}})` to
/// trigger the Python sidecar's import flow.
///
/// Mirrors the Electron `model:import-dialog` IPC handler in
/// `voice_typer/client/src/main/ipc/window-handlers.ts:81-90` which
/// calls `dialog.showOpenDialog({properties: ["openDirectory"]})`.
/// The Tauri path uses `tauri-plugin-dialog`'s folder-picker API.
///
/// Returns `{"canceled": true}` if the user dismissed the dialog, or
/// `{"canceled": false, "path": "<folder>"}` on success. Matches the
/// Electron handler's shape so `Models.tsx`'s import handler is
/// unchanged on both runtimes.
///
//`window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('open_model_import_dialog')` call is unchanged.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger a folder-picker dialog.
#[tauri::command]
pub async fn open_model_import_dialog(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    //use the async folder-pick pattern instead of blocking.
    // tauri-plugin-dialog v2.7.2's ``pick_folder()`` is callback-based
    // (not async), so we bridge it via a oneshot channel.
    let (tx, rx) = oneshot::channel();
    app.dialog()
        .file()
        .set_title("Select Model Folder")
        .pick_folder(move |f| {
            let _ = tx.send(f);
        });
    let file_path = rx.await.unwrap_or(None);
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    Ok(json!({
        "canceled": false,
        "path": path.to_string_lossy().to_string(),
    }))
}

//Tauri commands: export_templates / export_config () ──

//GDPR right-to-export for templates. Opens a save-file
/// dialog (JSON only — no CSV shape for templates) and writes the data
/// as pretty-printed JSON. Mirrors the Electron `templates:export` IPC
/// handler in `voice_typer/client/src/main/ipc/export-handlers.ts`.
///
/// Returns the same `{success, path?, canceled?, error?}` shape as
/// `export_history` / `export_vocabulary` so the renderer's mapping
/// (Tauri `canceled:true` → Electron `{success:false}` parity) works
/// identically.
///
//`window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('export_templates', { data })` call is
/// unchanged. `require_main_window(&window)?` runs FIRST.
#[tauri::command]
pub async fn export_templates(
    data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    // Templates are always JSON (no tabular CSV shape). Pass "json"
    // explicitly so the shared helper's format-validation accepts.
    export_data(
        data,
        "json".to_string(),
        app,
        "voice-typer-templates",
        "Export Templates",
    )
    .await
}

//GDPR right-to-export for the full app config. The
/// Python sidecar is contractually responsible for redacting API keys
//BEFORE the data reaches this command.  adds a Rust-side
/// defense-in-depth redaction pass via [`redact_config_secrets`] — if
/// the Python path regresses, the Rust host still scrubs obvious
/// secret-shaped keys (api_key / secret / token / password / passwd /
/// pwd / credential / auth, case-insensitive substring match) before
/// writing the JSON to disk. Mirrors the Electron `config:export` IPC
/// handler.
///
/// Same return shape as `export_templates`.
///
//`window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('export_config', { data })` call is unchanged.
/// `require_main_window(&window)?` runs FIRST.
#[tauri::command]
pub async fn export_config(
    mut data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    //defense-in-depth redaction. Walk the JSON tree and
    // replace any value whose key matches the sensitive-key pattern
    // with `***REDACTED***`. Logs a `warn` per redaction so a
    // regression in the Python-side redaction is visible in the
    // rotating log file.
    let redaction_count = redact_config_secrets(&mut data);
    if redaction_count > 0 {
        log::warn!(
            "[REDACT-DEFENSE] export_config: redacted {} sensitive field(s) at the \
             Rust host (Python-side redaction should have caught these)",
            redaction_count
        );
    }
    export_data(
        data,
        "json".to_string(),
        app,
        "voice-typer-config",
        "Export Config",
    )
    .await
}

// Unit tests for `is_sensitive_key` + `redact_config_secrets` live in
// the sibling `system_cmds_tests.rs` file (C-TEST-5 — keeps production
// source free of inline test code, matching the `commands/bubble/tests.rs`
// pattern). The module is wired as a child of `system_cmds` so the test
// file can use `use super::{...}` to access `pub(crate)` items
// (`is_sensitive_key`, `redact_config_secrets`, `REDACTED_MARKER`).
#[cfg(test)]
#[path = "system_cmds_tests.rs"]
mod system_cmds_tests;
