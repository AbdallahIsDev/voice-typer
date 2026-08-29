//! Host-locale storage: the `set_host_locale` Tauri command + its pure
//! decision core. Mirrors Electron's `i18n:set-locale` IPC handler,
//! which keeps the pushed locale in the main process so the host can
//! localize its native surfaces.

use serde_json::{json, Value};
use std::sync::Arc;

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::state::{lock, SidecarState};

/// Pure decision core for [`set_host_locale`], extracted so unit tests
/// can pin the envelope contract without constructing a Tauri runtime
/// or window. Validates the payload, stores it into
/// `SidecarState::host_locale` (via the poison-safe [`lock`] helper),
/// and returns the Electron-parity `{ok, error?}` envelope:
/// - whitespace/empty locale → `{"ok": false, "error": "empty locale"}`
///   (resolves instead of rejecting — byte-mirrors the Electron
///   `i18n:set-locale` handler's resolve-not-reject behavior)
/// - otherwise → stores `Some(locale)` and returns `{"ok": true}`
pub(crate) fn set_host_locale_core(locale: String, state: &Arc<SidecarState>) -> Value {
    if locale.trim().is_empty() {
        return json!({"ok": false, "error": "empty locale"});
    }
    *lock(&state.host_locale) = Some(locale);
    json!({"ok": true})
}

/// Store the main-window renderer's current locale so the host can
/// localize its native surfaces (today a parity sink — mirrors
/// Electron's `i18n:set-locale` IPC handler which keeps the pushed
/// locale in the main process). Returns the same
/// `{ok: boolean; error?: string}` promise shape as the Electron
/// preload's `window.window_.setLocale`, and never rejects for
/// domain-level failures (an empty locale resolves with
/// `ok: false` — see [`set_host_locale_core`]).
///
/// The `window` parameter is auto-injected by Tauri at runtime;
/// `require_main_window(&window)?` runs FIRST so the sandboxed bubble
/// renderer cannot write host state.
#[tauri::command]
pub async fn set_host_locale(
    locale: String,
    window: tauri::Window,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    Ok(set_host_locale_core(locale, state.inner()))
}
