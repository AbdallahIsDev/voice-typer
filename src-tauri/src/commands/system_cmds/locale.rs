//! Host-locale storage: the `set_host_locale` Tauri command + its pure
//! decision core. Mirrors Electron's `i18n:set-locale` IPC handler,
//! which keeps the pushed locale in the main process so the host can
//! localize its native surfaces. The stored value is consumed by the
//! native dialog title lookup in `super::dialog_titles` (folder
//! picker + export save dialogs) and broadcast to the bubble webview
//! (MO-119: Electron's `bubble:locale-changed` push, which keeps the
//! dictation pill's language + RTL direction in sync with the main
//! window without an app reload).

use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, Manager};

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::state::{lock, SidecarState};

/// Pure decision core for [`set_host_locale`], extracted so unit tests
/// can pin the envelope contract without constructing a Tauri runtime
/// or window. Validates the payload, stores it into
/// `SidecarState::host_locale` (via the poison-safe [`lock`] helper),
/// and returns the Electron-parity `{ok, error?}` envelope:
/// - whitespace/empty locale → `{"ok": false, "error": "empty locale"}`
///   (resolves instead of rejecting: byte-mirrors the Electron
///   `i18n:set-locale` handler's resolve-not-reject behavior)
/// - otherwise → stores `Some(locale)` and returns `{"ok": true}`
pub(crate) fn set_host_locale_core(locale: String, state: &Arc<SidecarState>) -> Value {
    if locale.trim().is_empty() {
        return json!({"ok": false, "error": "empty locale"});
    }
    *lock(&state.host_locale) = Some(locale);
    json!({"ok": true})
}

/// Broadcast a locale change to the bubble webview (MO-119).
///
/// Electron's main process pushed `bubble:locale-changed` on every
/// language switch (`windows/bubble/lifecycle.ts::
/// notifyBubbleLocaleChanged`); the Tauri bubble renderer's
/// `onLocaleChanged` listener has been wired for parity but the host
/// never emitted the event, so the pill kept the OLD locale / RTL
/// direction until a full app reload. This closes the gap: the same
/// event name, the same bare-locale-code payload ("en" / "ar" / …).
///
/// Emitted TO the bubble window only (`emit_to("bubble", ...)`), the
/// main window already knows the locale (it pushed it). Best-effort:
/// a missing bubble window (not yet created, or already closed) logs
/// at debug and is a no-op.
fn broadcast_locale_to_bubble(app: &tauri::AppHandle, locale: &str) {
    if app.get_webview_window("bubble").is_none() {
        log::debug!(
            "[LOCALE] bubble window not present: locale broadcast skipped (locale={})",
            locale
        );
        return;
    }
    if let Err(e) = app.emit_to("bubble", "bubble:locale-changed", locale.to_string()) {
        log::warn!("[LOCALE] bubble locale broadcast failed: {}", e);
    }
}

/// Store the main-window renderer's current locale so the host can
/// localize its native surfaces, and mirror the change to the bubble
/// webview. The stored value feeds the locale→title lookup in
/// `super::dialog_titles`, which localizes the native folder-picker and
/// export save-dialog titles. Returns the same
/// `{ok: boolean; error?: string}` promise shape as the Electron
/// preload's `window.window_.setLocale`, and never rejects for
/// domain-level failures (an empty locale resolves with
/// `ok: false`, see [`set_host_locale_core`]).
///
/// The `window` parameter is auto-injected by Tauri at runtime;
/// `require_main_window(&window)?` runs FIRST so the sandboxed bubble
/// renderer cannot write host state.
#[tauri::command]
pub async fn set_host_locale(
    app: tauri::AppHandle,
    locale: String,
    window: tauri::Window,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    let result = set_host_locale_core(locale.clone(), state.inner());
    // Only broadcast on a REAL change: the Settings page re-pushes the
    // current locale on mount, and re-emitting the same value would make
    // the bubble needlessly re-render its i18n tree.
    if result.get("ok").and_then(|ok| ok.as_bool()) == Some(true)
        && lock(&state.host_locale).as_deref() == Some(locale.as_str())
    {
        broadcast_locale_to_bubble(&app, &locale);
    }
    Ok(result)
}

// Test-support shim: `broadcast_locale_to_bubble` needs a live Tauri
// runtime (it walks the app's webview windows), so the sibling test
// module pins only the CHANGE-DETECTION predicate the command gates the
// broadcast on. Declared under `#[cfg(test)]` so production builds carry
// no extra surface.
#[cfg(test)]
pub(crate) mod tests_support {
    /// The exact condition the production command uses to decide whether
    /// a successful locale push should be broadcast to the bubble: only
    /// a push that (a) resolved `ok: true` and (b) actually CHANGED the
    /// stored value (same value → no broadcast, mirrors the production
    /// re-push suppression).
    pub(crate) fn locale_changed_for_test(
        ok: bool,
        stored: Option<&str>,
        pushed: &str,
    ) -> bool {
        ok && stored == Some(pushed)
    }
}
