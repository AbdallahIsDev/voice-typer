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
    // SAFETY: `GetForegroundWindow` is a pure Win32 query with no
    // pointer arguments — it returns the HWND of the window currently
    // in the foreground, or a null HWND (`.0 == 0`) if there is no
    // foreground window (e.g. the user is at the desktop). The null
    // case is handled by the `if (hwnd.0 as usize) == 0` check below.
    // No thread-safety concerns: the function is reentrant and reads
    // kernel-side state that is always consistent.
    let hwnd = unsafe { GetForegroundWindow() };
    if (hwnd.0 as usize) == 0 {
        return None;
    }
    // SAFETY: `GetWindowThreadProcessId(hwnd, None)` reads the thread
    // id (and optionally the process id) of the window identified by
    // `hwnd`. We pass `None` for the second arg so the function does
    // NOT write a PID anywhere (the lpdwProcessId out-param is
    // optional per the Win32 contract). `hwnd` is non-null here (we
    // checked above) and was just returned by `GetForegroundWindow`,
    // so it's a valid window handle for the duration of this call
    // (foreground window changes don't invalidate existing HWNDs —
    // they remain valid until the window is destroyed, which can't
    // happen synchronously between these two calls). Returns 0 on
    // failure — handled by the `tid == 0` check below.
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
    // HWND wraps *mut c_void in windows-rs 0.61.3+ (NOT isize).
    // Casting a captured isize back requires `as *mut c_void`.
    let target_hwnd = HWND(target_hwnd_raw as *mut core::ffi::c_void);

    // SAFETY: `GetForegroundWindow` — same rationale as in
    // `capture_focus_guard` above: pure query, no pointer args, null
    // HWND handled by the equality check below (target_hwnd was
    // captured before the paste and is also non-null by construction
    // — `capture_focus_guard` returned `None` for null HWNDs, so the
    // `Some((hwnd, tid))` arm that produced `target_hwnd_raw` only
    // fires for non-null foreground windows).
    let current = unsafe { GetForegroundWindow() };
    if current == target_hwnd {
        // Focus did NOT change during paste — nothing to restore.
        return Ok(());
    }

    // Focus changed during paste — restore it.
    // SAFETY: `GetCurrentThreadId` is a pure Win32 query with no
    // arguments — it returns the thread id of the calling thread,
    // which is always valid (we're a real OS thread spawned by Tokio).
    // Cannot fail, cannot return 0. No soundness concerns.
    let current_thread = unsafe { GetCurrentThreadId() };
    // SAFETY: `AttachThreadInput(current_thread, target_thread, true)`
    // attaches the input-processing state of the two threads so focus
    // operations (SetForegroundWindow) are not blocked by the
    // foreground-lock-timeout. Both thread ids are valid:
    // `current_thread` was just returned by `GetCurrentThreadId`, and
    // `target_thread` was captured by `GetWindowThreadProcessId` for a
    // foreground window before the paste (and is non-zero —
    // `capture_focus_guard` returns `None` for tid==0). The Win32
    // contract requires both ids to refer to threads in the SAME
    // desktop; if not, the call returns 0 (UIPI / cross-desktop) and
    // we fall through to the fallback path — no UB. Passing `true`
    // attaches the input queues; passing `false` (detach) is done
    // below in a separate call.
    // NOTE: AttachThreadInput takes `bool` in windows-rs 0.61.3+
    // (NOT `windows_core::BOOL`). Do NOT change `true` to
    // `BOOL::from(true)` — that type is not re-exported from
    // `Win32_Foundation` in this version. The wrapper handles
    // bool→BOOL conversion internally with `.into()`.
    let attached = unsafe {
        AttachThreadInput(current_thread, target_thread, true)
    };
    if attached.as_bool() {
        // Attach succeeded — switch foreground back to the captured
        // window, then detach (always detach, even if
        // SetForegroundWindow silently fails — never leak the attach).
        // SAFETY: `SetForegroundWindow(target_hwnd)` switches the
        // foreground window to `target_hwnd`. `target_hwnd` is a
        // valid HWND (same rationale as above) — even if the window
        // was destroyed between capture and now, the Win32 contract
        // is that SetForegroundWindow on an invalid HWND returns 0
        // (no UB). The attach we just performed ensures the call is
        // not blocked by the foreground-lock-timeout. The return value
        // is discarded (best-effort restore — we still detach below).
        let _ = unsafe { SetForegroundWindow(target_hwnd) };
        // SAFETY: `AttachThreadInput(current_thread, target_thread,
        // false)` is the detach call — it MUST be called with the
        // SAME pair of thread ids as the attach above. We hold no
        // lock between the two calls (the attach state is per-thread
        // pair, kernel-tracked), so the detach always succeeds if the
        // attach succeeded. Passing `false` sets the detach flag
        // (windows-rs 0.61.3+: AttachThreadInput takes `bool`, not
        // `BOOL` — see attach call above for details).
        // Even if this call were to fail (it shouldn't), the worst
        // case is a leaked attach — which the OS cleans up when one
        // of the threads exits. No UB.
        let _ = unsafe {
            AttachThreadInput(current_thread, target_thread, false)
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
    // Best-effort clipboard write — capture the success flag so the
    // crash_recovery event + toast body can reflect whether the text
    // was actually saved. G4-M-59: previously the clipboard write
    // result was discarded with `let _ = ...` and the event/toast
    // unconditionally claimed "text_saved_to_clipboard: true" — a
    // lie if the clipboard was locked by another process or the
    // clipboard plugin returned an error. The user would then press
    // Ctrl+V, paste whatever was previously on the clipboard, and
    // the transcribed text would be permanently lost without any
    // indication.
    let clipboard_ok = app
        .clipboard()
        .write_text(text.to_string())
        .map_err(|e| {
            // G4-M-59: log each discarded clipboard error at warn
            // level so it lands in the rotating log file for
            // post-mortem diagnosis (the user-visible toast below
            // is generic — "Text lost." — to avoid leaking clipboard
            // contents or implementation details).
            log::warn!(
                "[PASTE] clipboard write failed in UIPI fallback: {}",
                e
            );
            e
        })
        .is_ok();
    // Emit the crash_recovery event so the React UI can show its own
    // recovery UI (e.g. offer to copy the text again, or restart the
    // sidecar). G4-M-59: include `clipboard_ok` so the UI can branch
    // on whether the text is recoverable via Ctrl+V.
    let _ = app.emit(
        "crash_recovery",
        json!({
            "reason": "paste_focus_restore_failed",
            "text_saved_to_clipboard": clipboard_ok,
        }),
    );
    // Post the toast. G4-M-59: the body now branches on
    // `clipboard_ok` — if the clipboard write also failed, tell the
    // user the text is lost (rather than the old "clipboard copied"
    // message which would mislead them into expecting Ctrl+V to
    // work).
    let toast_body = if clipboard_ok {
        "Paste failed — clipboard copied. Press Ctrl+V manually."
    } else {
        "Paste failed — clipboard write also failed. Text lost."
    };
    let _ = app
        .notification()
        .builder()
        .title("Voice Typer")
        .body(toast_body)
        .show();
    if clipboard_ok {
        Err("paste focus-restore failed (UIPI): text copied to clipboard".to_string())
    } else {
        Err("paste focus-restore failed (UIPI): clipboard write also failed, text lost".to_string())
    }
}
