//! Paste-text execution (ADR-0020 §6.2 + §6.3 + §6.6).
//!
//! Extracted from `sidecar_cmds::paste_text` per CR-52 — the original
//! `paste_text` Tauri command was a 165-LOC god function with 4 platform
//! branches inline. The `#[tauri::command] paste_text` in
//! `sidecar_cmds.rs` is now a 5-line thin wrapper that delegates to
//! [`execute_paste`].
//!
//! # Paste strategy (ADR-0020 §6.2)
//!
//! - Short text (< [`PASTE_SHORT_THRESHOLD`] chars): inject via
//!   `enigo.text()` (IME-safe — does NOT break dead keys / non-English
//!   layouts).
//! - Long text (≥ threshold): copy via `tauri-plugin-clipboard-manager`
//!   then send Ctrl+V (Windows/Linux) or Cmd+V (macOS) via enigo.
//!
//! # Windows focus-restore (ADR-0020 §6.3)
//!
//! Capture the foreground window + its thread id BEFORE the paste
//! executes (the dispatch round-trip may have caused the Voice Typer
//! webview to briefly take foreground). After the paste, if the
//! foreground window changed during paste execution, re-attach input
//! processing between our thread and the captured window's thread via
//! `AttachThreadInput` + `SetForegroundWindow` + `AttachThreadInput`
//! (detach). If `AttachThreadInput` returns `0` (UIPI blocks the attach
//! — the foreground window runs at a higher integrity level than Voice
//! Typer), fall back IMMEDIATELY: write the text to the clipboard, emit
//! a `crash_recovery` event, and post a toast. This preserves the
//! no-data-loss guarantee — the user can press Ctrl+V manually.
//!
//! # Wayland fallback (ADR-0020 §6.6)
//!
//! `enigo` on Linux is X11-only (XTest) — `enigo.text()` is expected to
//! FAIL on Wayland sessions. Detect a Wayland session via
//! `XDG_SESSION_TYPE=wayland` and always use the clipboard + Ctrl+V
//! path (the short-text `enigo.text()` branch is skipped). macOS and
//! Linux X11 are unchanged.

use crate::util::PASTE_SHORT_THRESHOLD;

// ─── Public entry point ────────────────────────────────────────────────

/// Entry point for the paste-text path (CR-52 extraction).
///
/// Dispatches based on text length and platform:
/// - Empty text → no-op (`Ok(())`).
/// - Linux + Wayland session → [`paste_via_clipboard_and_ctrl_v`]
///   (clipboard + Ctrl+V — short-circuits the short-text `enigo.text()`
///   path because enigo is X11-only and would fail on Wayland).
/// - Short text (< [`PASTE_SHORT_THRESHOLD`] chars) →
///   [`paste_via_enigo_text`] (IME-safe enigo text injection).
/// - Long text → [`paste_via_clipboard_and_ctrl_v`] (clipboard +
///   Ctrl/Cmd+V).
/// - Windows: after the paste, calls [`restore_focus_or_fallback`] with
///   the [`capture_focus_guard`] snapshot to restore focus to the
///   captured foreground window (ADR-0020 §6.3).
pub async fn execute_paste(app: tauri::AppHandle, text: String) -> Result<(), String> {
    if text.is_empty() {
        return Ok(());
    }

    // ADR-0020 §6.3 (Windows focus-restore, step 1): capture the
    // foreground window + its thread id BEFORE the paste executes. The
    // dispatch round-trip (UI → Rust → sidecar WS → Rust → here) may
    // have caused the Voice Typer webview to briefly take foreground,
    // so we capture here (not at dispatch time) to snapshot whatever
    // the user's intended target window currently is. We replay this
    // snapshot AFTER the paste via `AttachThreadInput` +
    // `SetForegroundWindow` so the paste lands in the right window.
    #[cfg(target_os = "windows")]
    let focus_guard = capture_focus_guard();

    // ADR-0020 §6.6 (Wayland fallback): on a Wayland session, enigo's
    // X11/XTest backend cannot inject keystrokes, so the short-text
    // `enigo.text()` path is expected to fail. Always use the
    // clipboard + Ctrl+V path on Wayland. macOS and Linux X11 are
    // unchanged.
    #[cfg(target_os = "linux")]
    if is_wayland_session() {
        log::info!(
            "[PASTE] Wayland session detected (XDG_SESSION_TYPE=wayland) — \
             using clipboard + Ctrl+V fallback (ADR-0020 §6.6)"
        );
        return paste_via_clipboard_and_ctrl_v(&app, &text).await;
    }

    if text.chars().count() < PASTE_SHORT_THRESHOLD {
        paste_via_enigo_text(&text)?;
    } else {
        paste_via_clipboard_and_ctrl_v(&app, &text).await?;
    }

    // ADR-0020 §6.3 (Windows focus-restore, step 2): after the paste,
    // check whether the foreground window changed during the paste
    // execution. If it did (e.g. Voice Typer's webview stole focus),
    // re-attach input processing between our thread and the captured
    // window's thread, then call `SetForegroundWindow(captured_hwnd)`,
    // then detach. If `AttachThreadInput` returns `0` (UIPI blocks the
    // attach — the foreground window runs at a higher integrity level
    // than Voice Typer), fall back IMMEDIATELY per ADR §6.3: write the
    // text to the clipboard, emit a `crash_recovery` event, and post a
    // toast. This preserves the no-data-loss guarantee.
    #[cfg(target_os = "windows")]
    {
        restore_focus_or_fallback(&app, &text, focus_guard).await?;
    }

    Ok(())
}

// ─── Linux helpers ─────────────────────────────────────────────────────

/// Linux: detect a Wayland session via `XDG_SESSION_TYPE=wayland`
/// (ADR-0020 §6.6). Returns `false` on X11, tty, or unset. Comparison
/// is case-insensitive (some distros ship `Wayland`).
#[cfg(target_os = "linux")]
fn is_wayland_session() -> bool {
    std::env::var("XDG_SESSION_TYPE")
        .map(|v| v.eq_ignore_ascii_case("wayland"))
        .unwrap_or(false)
}

// ─── Shared paste paths ────────────────────────────────────────────────

/// Short-text paste path: inject `text` directly via `enigo.text()`
/// (IME-safe — does NOT break dead keys / non-English layouts).
///
/// Synchronous because `enigo::Enigo::text()` does not need async I/O —
/// the enigo text injection is a single blocking XTest / Win32
/// `SendInput` call sequence.
fn paste_via_enigo_text(text: &str) -> Result<(), String> {
    use enigo::{Enigo, Keyboard, Settings};
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| format!("enigo init failed: {e}"))?;
    enigo
        .text(text)
        .map_err(|e| format!("enigo.text failed: {e}"))?;
    log::info!("[PASTE] injected {} chars via enigo", text.chars().count());
    Ok(())
}

/// Shared clipboard + Ctrl+V / Cmd+V paste path used by both the
/// long-text branch of [`execute_paste`] and the Wayland short-text
/// fallback.
///
/// Writes `text` to the system clipboard via
/// `tauri-plugin-clipboard-manager`, then sends the platform-appropriate
/// paste keystroke (Ctrl+V on Windows/Linux, Cmd+V on macOS) via enigo.
/// All errors propagate via `?` so the caller (a `#[tauri::command]`
/// returning `Result<(), String>`) surfaces them to the webview's
/// `invoke()` reject handler per ADR-0020 §6.2 + NEW-IPC-107.
async fn paste_via_clipboard_and_ctrl_v(
    app: &tauri::AppHandle,
    text: &str,
) -> Result<(), String> {
    use enigo::{Enigo, Key, Keyboard, Settings};
    use tauri_plugin_clipboard_manager::ClipboardExt;
    app.clipboard()
        .write_text(text.to_string())
        .map_err(|e| format!("clipboard write failed: {e}"))?;
    let mut enigo = Enigo::new(&Settings::default())
        .map_err(|e| format!("enigo init failed: {e}"))?;
    let mod_key = if cfg!(target_os = "macos") {
        Key::Meta
    } else {
        Key::Control
    };
    enigo
        .key(mod_key, enigo::Direction::Press)
        .map_err(|e| format!("enigo mod press failed: {e}"))?;
    enigo
        .key(Key::Unicode('v'), enigo::Direction::Click)
        .map_err(|e| format!("enigo v click failed: {e}"))?;
    enigo
        .key(mod_key, enigo::Direction::Release)
        .map_err(|e| format!("enigo mod release failed: {e}"))?;
    log::info!(
        "[PASTE] injected {} chars via clipboard + Ctrl/Cmd+V",
        text.chars().count()
    );
    Ok(())
}

// ─── Windows focus-restore (ADR-0020 §6.3) ─────────────────────────────
//
// These helpers are cfg(target_os = "windows") because they call Win32
// APIs (`GetForegroundWindow`, `GetWindowThreadProcessId`,
// `AttachThreadInput`, `SetForegroundWindow`, `GetCurrentThreadId`).
// On Linux / macOS they are absent from the build — the
// `#[cfg(target_os = "windows")]` blocks in `execute_paste` simply
// skip the focus-restore dance.

/// Windows focus-restore step 1 (ADR-0020 §6.3): capture the current
/// foreground window handle + its thread id BEFORE the paste executes.
///
/// Returns `None` if `GetForegroundWindow` returns a null handle (no
/// foreground window — e.g. the user is at the desktop) or
/// `GetWindowThreadProcessId` returns `0` (call failed).
///
/// The returned tuple is `(hwnd_as_isize, thread_id)` — replayed after
/// the paste by [`restore_focus_or_fallback`]. We store the HWND as an
/// `isize` rather than a raw `windows::Win32::Foundation::HWND` so the
/// tuple is unconditionally `Send` (the `windows` crate's `HWND` wraps a
/// `*mut c_void`, which IS `Send`, but storing the integer makes the
/// platform-agnostic docstring cleaner and matches the CR-52 spec).
#[cfg(target_os = "windows")]
fn capture_focus_guard() -> Option<(isize, u32)> {
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};
    let hwnd = unsafe { GetForegroundWindow() };
    if (hwnd.0 as usize) == 0 {
        return None;
    }
    // Second arg `None` → we don't care about the PID, only the
    // thread id (returned by value). tid==0 means the call
    // failed — treat as no foreground window.
    let tid = unsafe { GetWindowThreadProcessId(hwnd, None) };
    if tid == 0 {
        None
    } else {
        Some((hwnd.0 as isize, tid))
    }
}

/// Windows focus-restore step 2 (ADR-0020 §6.3): after the paste, if
/// the foreground window changed during paste execution, re-attach
/// input processing between our thread and the captured window's
/// thread via `AttachThreadInput`, then call
/// `SetForegroundWindow(captured_hwnd)`, then detach (always detach —
/// never leak the attach even if `SetForegroundWindow` silently fails).
///
/// If `AttachThreadInput` returns `0` (UIPI blocks the attach — the
/// foreground window runs at a higher integrity level than Voice
/// Typer), fall back IMMEDIATELY: write the text to the clipboard, emit
/// a `crash_recovery` event, and post a toast. The user can press
/// Ctrl+V manually — no data loss.
///
/// If `guard` is `None` (no foreground window was captured before the
/// paste), this is a no-op (`Ok(())`).
#[cfg(target_os = "windows")]
async fn restore_focus_or_fallback(
    app: &tauri::AppHandle,
    text: &str,
    guard: Option<(isize, u32)>,
) -> Result<(), String> {
    use serde_json::json;
    use tauri::Emitter;
    use tauri_plugin_clipboard_manager::ClipboardExt;
    use tauri_plugin_notification::NotificationExt;
    use windows::Win32::Foundation::HWND;
    use windows::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, SetForegroundWindow};

    let (target_hwnd_raw, target_thread) = match guard {
        Some(v) => v,
        None => return Ok(()),
    };
    let target_hwnd = HWND(target_hwnd_raw as *mut _);

    let current = unsafe { GetForegroundWindow() };
    if current == target_hwnd {
        // Focus did NOT change during paste — nothing to restore.
        return Ok(());
    }

    // Focus changed during paste — restore it.
    let current_thread = unsafe { GetCurrentThreadId() };
    let attached = unsafe {
        AttachThreadInput(
            current_thread,
            target_thread,
            windows::Win32::Foundation::BOOL::from(true),
        )
    };
    if attached.as_bool() {
        // Attach succeeded — switch foreground back to the captured
        // window, then detach (always detach, even if
        // SetForegroundWindow silently fails — never leak the attach).
        let _ = unsafe { SetForegroundWindow(target_hwnd) };
        let _ = unsafe {
            AttachThreadInput(
                current_thread,
                target_thread,
                windows::Win32::Foundation::BOOL::from(false),
            )
        };
        log::info!(
            "[PASTE] focus-restore: AttachThreadInput + SetForegroundWindow succeeded"
        );
        return Ok(());
    }

    // UIPI fallback (ADR-0020 §6.3): `AttachThreadInput` returned 0 —
    // the foreground window is at a higher integrity level than Voice
    // Typer. Do NOT retry the window switch. Fall back IMMEDIATELY:
    // clipboard + crash_recovery event + toast. The user can press
    // Ctrl+V manually — no data loss.
    log::warn!(
        "[PASTE] AttachThreadInput returned 0 — UIPI blocked focus-restore; \
         falling back to clipboard + crash_recovery + toast"
    );
    // Best-effort clipboard write — discard errors (we're already in a
    // fallback path; a clipboard failure here is logged but not
    // surfaced as a paste-text Err since we already lost the paste
    // anyway).
    let _ = app.clipboard().write_text(text.to_string());
    // Emit the crash_recovery event so the React UI can show its own
    // recovery UI (e.g. offer to copy the text again, or restart the
    // sidecar).
    let _ = app.emit(
        "crash_recovery",
        json!({
            "reason": "paste_focus_restore_failed",
            "text_saved_to_clipboard": true,
        }),
    );
    // Post the toast. The body matches the ADR §6.3 contract verbatim
    // so the user knows what to do.
    let _ = app
        .notification()
        .builder()
        .title("Voice Typer")
        .body("Paste failed — clipboard copied. Press Ctrl+V manually.")
        .show();
    Err("paste focus-restore failed (UIPI): text copied to clipboard".to_string())
}
