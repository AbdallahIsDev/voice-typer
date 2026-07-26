//! System-level window commands: open_logs, open_model_import_dialog,
//! export_templates, export_config (CR-33 + UX-008 + MODEL-IMPORT +
//! NEW-PRIV-007).
//!
//! CR-33: these 4 commands were missing from the Tauri host — the
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

use crate::commands::export::{export_data, require_main_window};
use crate::platform::paths::config_dir;

// ─── DE-73: defense-in-depth config redaction ─────────────────────────
//
// The Python sidecar is contractually responsible for redacting API
// keys / secrets / tokens BEFORE the config payload reaches the Rust
// `export_config` command (see `voice_typer/server/credential_store.py`
// `_redact_sensitive`). DE-73 adds a Rust-side redaction pass as
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

/// DE-73: return `true` if `key` matches the redaction pattern
/// `(?i)(api[_-]?key|secret|token|password|passwd|pwd|credential|auth)`
/// (case-insensitive substring match — the regex is unanchored).
pub(crate) fn is_sensitive_key(key: &str) -> bool {
    let k = key.to_ascii_lowercase();
    // `api[_-]?key` → "api_key", "api-key", "apikey" (and any
    // key containing those as a substring, e.g. "openai_api_key").
    // We check all three spellings because the regex `[_-]?` makes
    // the separator optional.
    if k.contains("api_key")
        || k.contains("api-key")
        || k.contains("apikey")
    {
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

/// DE-73: walk a JSON `Value` recursively and replace every value
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
fn redact_config_secrets_inner(
    value: &mut Value,
    parent_key: Option<&str>,
    count: &mut usize,
) {
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
                    "[DE-73] redacted sensitive config key {:?} (value type: {}) — \
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

// ─── Tauri command: open_logs (UX-008) ────────────────────────────────

/// UX-008: open the Voice Typer log directory in the OS file manager.
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
/// DE-72: the response no longer includes the `path` field — the
/// absolute path can contain the user's home directory / username
/// (PII leak in shared logs / crash reports), and no renderer call
/// site consumes it (`window-namespace.ts::openLogs` strips `path`
/// before returning to the React layer). Returns
/// `{"success": true}` on success or `{"success": false, "error":
/// "<msg>"}` on failure.
///
/// DE-18: `window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('open_logs')` call is unchanged.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger OS file-manager opens.
#[tauri::command]
pub async fn open_logs(
    _app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    let log_dir = config_dir();
    // Best-effort mkdir — if it fails (e.g. permission denied), the
    // open command below will surface the error to the user.
    let _ = std::fs::create_dir_all(&log_dir);

    let open_result = open_path_in_file_manager(&log_dir);
    match open_result {
        Ok(()) => Ok(json!({"success": true})),
        Err(e) => Ok(json!({"success": false, "error": e})),
    }
}

/// GT-83: open the Rust host log directory (`<config_dir>/logs/`) in
/// the OS file manager. Distinct from `open_logs` which opens the
/// parent `<config_dir>/` root.
///
/// The actual Rust host log files (`voice-typer.log`, `.log.1`, ...)
/// live in `<config_dir>/logs/` per `platform::logging::init_file_logger`
/// (which does `config_dir.join("logs")`). The previous `open_logs`
/// command opened `<config_dir>/` — operators clicking "View Logs"
/// landed in the config root and had to navigate into `logs/` manually.
/// This command opens `logs/` directly.
///
/// DE-18: `window` is auto-injected by Tauri at runtime.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger OS file-manager opens.
#[tauri::command]
pub async fn open_host_logs(
    _app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    let log_dir = config_dir().join("logs");
    let _ = std::fs::create_dir_all(&log_dir);

    let open_result = open_path_in_file_manager(&log_dir);
    match open_result {
        Ok(()) => Ok(json!({"success": true})),
        Err(e) => Ok(json!({"success": false, "error": e})),
    }
}

/// GT-35: sink for renderer-side error logs. The React UI's
/// `__tauriLog.error(...)` invokes this command so uncaught UI errors
/// land in the host-side rotating log file (`<config_dir>/logs/voice-typer.log`
/// via the existing `log::error!` global logger) for operator triage
/// without requiring DevTools to be open.
///
/// The payload is an opaque JSON value — the renderer sends
/// `{message, stack?, componentStack?, location?}`. We serialize it
/// to a single line and emit via `log::error!` with a `[RENDERER_ERROR]`
/// prefix. Returns `Ok(())` unconditionally — the renderer's promise
/// resolves so its `__tauriLog.error` call doesn't itself become an
/// unhandled rejection.
#[tauri::command]
pub async fn renderer_log_error(payload: Value, _app: tauri::AppHandle) -> Result<(), String> {
    let serialized = serde_json::to_string(&payload).unwrap_or_else(|_| "<unserializable>".to_string());
    log::error!("[RENDERER_ERROR] {}", serialized);
    Ok(())
}

/// Open a filesystem path in the OS-native file manager. Best-effort:
/// returns an error string on failure (the caller surfaces it to the
/// UI). Mirrors Electron's `shell.openPath()` semantics.
fn open_path_in_file_manager(path: &std::path::Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer.exe")
            .arg(path)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("open spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("xdg-open spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = path;
        Err("unsupported platform: open_logs is only implemented for Windows / macOS / Linux".to_string())
    }
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
/// DE-18: `window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('open_model_import_dialog')` call is unchanged.
/// `require_main_window(&window)?` runs FIRST so a compromised bubble
/// renderer cannot trigger a folder-picker dialog.
#[tauri::command]
pub async fn open_model_import_dialog(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    // PVT-048: use the async folder-pick pattern instead of blocking.
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

// ─── Tauri commands: export_templates / export_config (NEW-PRIV-007) ──

/// NEW-PRIV-007: GDPR right-to-export for templates. Opens a save-file
/// dialog (JSON only — no CSV shape for templates) and writes the data
/// as pretty-printed JSON. Mirrors the Electron `templates:export` IPC
/// handler in `voice_typer/client/src/main/ipc/export-handlers.ts`.
///
/// Returns the same `{success, path?, canceled?, error?}` shape as
/// `export_history` / `export_vocabulary` so the renderer's mapping
/// (Tauri `canceled:true` → Electron `{success:false}` parity) works
/// identically.
///
/// DE-18: `window` is auto-injected by Tauri at runtime — the
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
    export_data(data, "json".to_string(), app, "voice-typer-templates", "Export Templates").await
}

/// NEW-PRIV-007: GDPR right-to-export for the full app config. The
/// Python sidecar is contractually responsible for redacting API keys
/// BEFORE the data reaches this command. DE-73 adds a Rust-side
/// defense-in-depth redaction pass via [`redact_config_secrets`] — if
/// the Python path regresses, the Rust host still scrubs obvious
/// secret-shaped keys (api_key / secret / token / password / passwd /
/// pwd / credential / auth, case-insensitive substring match) before
/// writing the JSON to disk. Mirrors the Electron `config:export` IPC
/// handler.
///
/// Same return shape as `export_templates`.
///
/// DE-18: `window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('export_config', { data })` call is unchanged.
/// `require_main_window(&window)?` runs FIRST.
#[tauri::command]
pub async fn export_config(
    mut data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    // DE-73: defense-in-depth redaction. Walk the JSON tree and
    // replace any value whose key matches the sensitive-key pattern
    // with `***REDACTED***`. Logs a `warn` per redaction so a
    // regression in the Python-side redaction is visible in the
    // rotating log file.
    let redaction_count = redact_config_secrets(&mut data);
    if redaction_count > 0 {
        log::warn!(
            "[DE-73] export_config: redacted {} sensitive field(s) at the \
             Rust host (Python-side redaction should have caught these)",
            redaction_count
        );
    }
    export_data(data, "json".to_string(), app, "voice-typer-config", "Export Config").await
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── open_path_in_file_manager (pure spawn, no side effects on the
    //    test process — spawn fires-and-forgets, the child explorer /
    //    open / xdg-open may or may not actually open in a headless
    //    sandbox). We only assert the spawn doesn't panic. ──────────

    #[cfg(target_os = "linux")]
    #[test]
    fn test_open_path_in_file_manager_does_not_panic_on_existing_dir() {
        // /tmp always exists on Linux — xdg-open may or may not be
        // installed in the sandbox, but `spawn()` only fails if the
        // binary itself can't be launched (it doesn't wait for exit).
        // We accept either Ok or Err (sandbox may lack xdg-open) — the
        // test only asserts the function doesn't panic.
        let _ = open_path_in_file_manager(std::path::Path::new("/tmp"));
    }

    #[test]
    fn test_open_path_in_file_manager_handles_nonexistent_path_gracefully() {
        // A nonexistent path doesn't make `spawn()` fail (the file
        // manager would open and then show an error, but that's the
        // user's problem, not the host's). We just assert no panic.
        let _ = open_path_in_file_manager(std::path::Path::new("/nonexistent/path/that/does/not/exist/voice-typer-test"));
    }

    // ── DE-73: is_sensitive_key ───────────────────────────────────────
    //
    // Pins the key-matching pattern: `(?i)(api[_-]?key|secret|token|
    // password|passwd|pwd|credential|auth)` as a case-insensitive
    // substring match.

    #[test]
    fn test_is_sensitive_key_api_key_variants() {
        // `api[_-]?key` matches all three spellings (and any key
        // containing them as a substring).
        assert!(is_sensitive_key("api_key"));
        assert!(is_sensitive_key("api-key"));
        assert!(is_sensitive_key("apikey"));
        assert!(is_sensitive_key("openai_api_key"));
        assert!(is_sensitive_key("OPENAI_API_KEY"));
        assert!(is_sensitive_key("X-Api-Key"));
        assert!(is_sensitive_key("provider_apikey_v2"));
    }

    #[test]
    fn test_is_sensitive_key_secret_token_password() {
        assert!(is_sensitive_key("secret"));
        assert!(is_sensitive_key("client_secret"));
        assert!(is_sensitive_key("CLIENT_SECRET"));
        assert!(is_sensitive_key("token"));
        assert!(is_sensitive_key("auth_token"));
        assert!(is_sensitive_key("bearer_token"));
        assert!(is_sensitive_key("password"));
        assert!(is_sensitive_key("PASSWORD"));
        assert!(is_sensitive_key("user_password"));
        assert!(is_sensitive_key("passwd"));
        assert!(is_sensitive_key("pwd"));
        assert!(is_sensitive_key("credential"));
        assert!(is_sensitive_key("auth"));
        assert!(is_sensitive_key("authorization"));
        assert!(is_sensitive_key("X-Auth-Token"));
    }

    #[test]
    fn test_is_sensitive_key_negatives() {
        // Keys that look sensitive but aren't (no substring match).
        assert!(!is_sensitive_key("api"));
        assert!(!is_sensitive_key("model"));
        assert!(!is_sensitive_key("language"));
        assert!(!is_sensitive_key("vocabulary"));
        assert!(!is_sensitive_key("backend"));
        assert!(!is_sensitive_key("history"));
        assert!(!is_sensitive_key("auto_punctuate"));
        assert!(!is_sensitive_key("tray_icon"));
        assert!(!is_sensitive_key(""));
    }

    // ── DE-73: redact_config_secrets ──────────────────────────────────

    #[test]
    fn test_redact_config_secrets_flat_object() {
        let mut v = json!({
            "model": "small.en",
            "api_key": "sk-abc123",
            "language": "en",
            "password": "hunter2"
        });
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 2);
        assert_eq!(v["model"], "small.en");
        assert_eq!(v["api_key"], REDACTED_MARKER);
        assert_eq!(v["language"], "en");
        assert_eq!(v["password"], REDACTED_MARKER);
    }

    #[test]
    fn test_redact_config_secrets_nested_object() {
        let mut v = json!({
            "providers": {
                "openai": {
                    "api_key": "sk-abc123",
                    "model": "gpt-4"
                },
                "anthropic": {
                    "api_key": "sk-ant-xyz",
                    "auth_token": "Bearer abc"
                }
            },
            "version": 2
        });
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 3);
        assert_eq!(v["providers"]["openai"]["api_key"], REDACTED_MARKER);
        assert_eq!(v["providers"]["openai"]["model"], "gpt-4");
        assert_eq!(v["providers"]["anthropic"]["api_key"], REDACTED_MARKER);
        assert_eq!(v["providers"]["anthropic"]["auth_token"], REDACTED_MARKER);
        assert_eq!(v["version"], 2);
    }

    #[test]
    fn test_redact_config_secrets_array_of_objects() {
        let mut v = json!([
            {"id": 1, "api_key": "sk-1"},
            {"id": 2, "token": "tok-2"},
            {"id": 3, "name": "no secret here"}
        ]);
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 2);
        assert_eq!(v[0]["id"], 1);
        assert_eq!(v[0]["api_key"], REDACTED_MARKER);
        assert_eq!(v[1]["id"], 2);
        assert_eq!(v[1]["token"], REDACTED_MARKER);
        assert_eq!(v[2]["name"], "no secret here");
    }

    #[test]
    fn test_redact_config_secrets_skips_null_values() {
        // A sensitive key with a null value is not a leak — don't
        // redact (and don't log a warn).
        let mut v = json!({
            "api_key": null,
            "secret": null,
            "model": "small.en"
        });
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 0, "null values should not be redacted");
        assert!(v["api_key"].is_null());
        assert!(v["secret"].is_null());
    }

    #[test]
    fn test_redact_config_secrets_redacts_non_string_values() {
        // A secret could be a number (e.g. a numeric PIN) or bool —
        // redact regardless of type.
        let mut v = json!({
            "password": 12345,
            "pwd": true,
            "credential": ["nested", "array"]
        });
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 3);
        assert_eq!(v["password"], REDACTED_MARKER);
        assert_eq!(v["pwd"], REDACTED_MARKER);
        assert_eq!(v["credential"], REDACTED_MARKER);
    }

    #[test]
    fn test_redact_config_secrets_case_insensitive_keys() {
        let mut v = json!({
            "API_KEY": "sk-1",
            "ApiKey": "sk-2",
            "PASSWORD": "pw"
        });
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 3);
        assert_eq!(v["API_KEY"], REDACTED_MARKER);
        assert_eq!(v["ApiKey"], REDACTED_MARKER);
        assert_eq!(v["PASSWORD"], REDACTED_MARKER);
    }

    #[test]
    fn test_redact_config_secrets_empty_object() {
        let mut v = json!({});
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 0);
    }

    #[test]
    fn test_redact_config_secrets_no_secrets() {
        let mut v = json!({
            "model": "small.en",
            "language": "en",
            "vocabulary": ["hello", "world"]
        });
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 0);
        // Verify the data is untouched.
        assert_eq!(v["model"], "small.en");
        assert_eq!(v["vocabulary"][0], "hello");
    }

    #[test]
    fn test_redact_config_secrets_non_object_root() {
        // The root itself is not under any key — redaction only
        // applies to values whose parent KEY is sensitive. A bare
        // scalar root has nothing to redact.
        let mut v = json!("just a string");
        let count = redact_config_secrets(&mut v);
        assert_eq!(count, 0);
        assert_eq!(v, "just a string");
    }
}
