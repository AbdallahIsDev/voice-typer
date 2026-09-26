use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{Emitter, Manager};

use crate::commands::require_main_window;
use crate::error::LausuError;
use crate::state::{lock, SidecarState};

pub(crate) fn set_host_locale_core(locale: String, state: &Arc<SidecarState>) -> Value {
    if locale.trim().is_empty() {
        return json!({"ok": false, "error": "empty locale"});
    }
    *lock(&state.host_locale) = Some(locale);
    json!({"ok": true})
}

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

#[tauri::command]
pub async fn set_host_locale(
    app: tauri::AppHandle,
    locale: String,
    window: tauri::Window,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<Value, LausuError> {
    require_main_window(&window)?;
    let result = set_host_locale_core(locale.clone(), state.inner());
    if result.get("ok").and_then(|ok| ok.as_bool()) == Some(true)
        && lock(&state.host_locale).as_deref() == Some(locale.as_str())
    {
        broadcast_locale_to_bubble(&app, &locale);
    }
    Ok(result)
}

#[cfg(test)]
pub(crate) mod tests_support {
    pub(crate) fn locale_changed_for_test(ok: bool, stored: Option<&str>, pushed: &str) -> bool {
        ok && stored == Some(pushed)
    }
}
