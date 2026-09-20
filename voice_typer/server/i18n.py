"""full label dict via the ``set_tray_locale`` IPC on locale change. Until"""

from __future__ import annotations

import threading
from typing import Any

# single source of truth for the server-side default locale.
DEFAULT_LOCALE: str = "en"

_LOCK = threading.Lock()
_CURRENT_LOCALE: str = DEFAULT_LOCALE
_REGISTRY: dict[str, dict[str, str]] = {DEFAULT_LOCALE: {}}


# server-side notification strings (kept verbatim from the original
_INITIAL_LABELS: dict[str, str] = {
    # Lowercase to match AppState.<X>.value and preserve existing tooltip
    "state.idle": "idle",
    "state.recording": "recording",
    "state.transcribing": "transcribing",
    "state.loading": "loading",
    "state.error": "error",
    "state.cancelling": "cancelling",
    "state.recording_controller.loading_queued": ("Loading model | your dictation will start automatically…"),
    "state.recording_controller.starting_up": "Starting up | please wait...",
    "state.recording_controller.consent_required": "Voice biometric consent required",
    "state.recording_controller.model_failed_retry": "Model failed to load | press your hotkey to retry",
    "state.recording_controller.recording": "Recording...",
    "state.recording_controller.recording_failed": "Recording failed",
    "state.recording_controller.recording_failed_permission": (
        "Recording failed | microphone permission denied. Allow mic access in system settings."
    ),
    "state.recording_controller.recording_failed_no_device": (
        "Recording failed | no microphone found. Connect a microphone and try again."
    ),
    "state.recording_controller.stop_failed": "Stop failed",
    "state.recording_controller.too_short": "Too short | ignored",
    "state.recording_controller.transcribing": "Transcribing...",
    "state.recording_controller.cancelling": "Cancelling...",
    "state.recording_controller.cancelled": "Cancelled",
    "state.recording_controller.recovered": "Recovered | transcription timed out",
    "state.recording_controller.still_transcribing": "Still transcribing...",
    "state.model_manager.loading": "Loading model | press your hotkey to queue...",
    "state.model_manager.ready_whisper": "Ready | {device_info}",
    "state.model_manager.ready_other": "Ready | {name} ASR",
    "state.model_manager.load_failed_retry": "Model load failed | press your hotkey to retry",
    "state.model_manager.backend_failed": "{backend} model failed to load",
    "state.model_manager.model_failed": "Model failed | {error}",
    "state.model_manager.model_not_downloaded": ("No speech model is selected. Open Models to choose one."),
    # no_model_selected: genuine "no model selected" state
    "state.model_manager.no_model_selected": ("No model selected. Go to the models page to select a model."),
    "state.model_manager.model_integrity_failed": (
        "{backend} model failed integrity verification. Delete and re-download it from the Models page."
    ),
    # Momentary tooltip states published by paste_step / transcribe_step.
    "state.dictation_pipeline.clipboard_unavailable": "Done | clipboard unavailable",
    "state.dictation_pipeline.no_speech_detected": "No speech detected",
    "state.dictation_pipeline.no_speech_check_mic": "No speech | check microphone",
    "state.dictation_pipeline.transcription_empty": "Transcription returned empty",
    # paste_step "Done | N chars (mode)" statuses, the character count
    "state.dictation_pipeline.done_pasted": "Done | {count} chars (pasted)",
    "state.dictation_pipeline.done_in_db": ("Done | {count} chars (in DB, use repaste hotkey)"),
    "state.dictation_pipeline.done_in_clipboard": ("Done | {count} chars (in clipboard)"),
    "notify.app.repaste_no_previous": "No previous transcription to re-paste.",
    "notify.app.repaste_copy_failed": (
        "Could not copy the transcription to the clipboard. Another app may be holding the clipboard lock."
    ),
    "notify.app.repaste_done": "Last transcription re-pasted",
    "notify.app.repaste_blocked": (
        "Re-paste was blocked (unsafe target or rate-limited). "
        "Your previous clipboard was preserved. "
        "Use the repaste hotkey again to try pasting."
    ),
    "notify.app.undo_nothing": "Nothing to undo.",
    "notify.app.undo_done": "Undid last transcription ({char_count} chars)",
    "notify.app.undo_no_pynput": "Undo not available (pynput missing)",
    "notify.app.undo_failed": "Could not undo the last transcription. See logs for details.",
    "notify.app.config_open_failed": "Config file:\n{path}",
    "notify.settings_controller.autostart_failed": "Could not change autostart setting.\n{error}",
    "notify.settings_controller.mic_save_failed": (
        "Failed to save microphone selection. Check disk space or permissions."
    ),
    "notify.settings_controller.mic_next_recording": "Microphone next recording: {label}",
    "notify.settings_controller.mic_changed": "Microphone: {label}",
    "notify.settings_controller.system_default_device": "System Default",
    "notify.startup_tasks.accessibility_granted": ("Accessibility permission granted. Hotkeys are now active."),
    "notify.startup_tasks.accessibility_revoked_title": "{app} | Accessibility Revoked",
    "notify.startup_tasks.accessibility_revoked_body": (
        "Global hotkeys have been disabled. "
        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility to re-grant."
    ),
    "notify.hotkey_dispatcher.register_failed": (
        "Hotkey {hotkey} could not be registered. "
        "It may be in use by another app. "
        "Use the tray menu to toggle dictation, or pick a different hotkey in Settings."
    ),
    "notify.hotkey_dispatcher.save_failed": ("Failed to save hotkey to disk. Check disk space or permissions."),
    "notify.hotkey_dispatcher.wayland_caps_lock": (
        "On Wayland, Caps Lock cannot be suppressed, "
        "your text will be capitalized. Bind Alt or a "
        "function key instead, or remap Caps Lock via "
        "your compositor's settings."
    ),
    "notify.hotkey_dispatcher.ptt_release_missed": (
        "PTT release event missed, recording auto-stopped after 60s safety timeout."
    ),
    "notify.hotkey_dispatcher.esc_register_failed": (
        "ESC cancel hotkey could not be registered. Another app may have claimed it."
    ),
    "notify.hotkey_dispatcher.repaste_register_failed": (
        "Repaste hotkey could not be registered. Another app may have claimed it."
    ),
    "notify.hotkey_dispatcher.invalid_hotkey": (
        "Hotkey {hotkey} is not valid: {validation_error}. Keeping the previous hotkey."
    ),
    "notify.hotkey_dispatcher.restore_failed": (
        "Could not restore the previous hotkey {hotkey}. Open Settings to rebind a hotkey."
    ),
    # Title/body pairs for the native→legacy fallback chain. Titles use
    "notify.native_adapter.warn_title": "{app}: Native hotkey warning",
    "notify.native_adapter.fallback_title": "{app}: Compatibility mode",
    "notify.native_adapter.fallback_body": (
        "Hotkey is running in compatibility mode (reduced features). Restart the app for full functionality."
    ),
    "notify.native_adapter.recovery_title": "{app}: Full mode restored",
    "notify.native_adapter.recovery_body": "Hotkey is running in full mode.",
    "notify.native_adapter.failure_title": "{app}: Hotkey error",
    "notify.native_adapter.failure_body": "Hotkey is not working. Click to troubleshoot.",
    # Failure paths name a cause; success paths omit ``message`` so the
    "notify.model.delete.unknown_model": "Unknown model: {model}",
    "notify.model.delete.not_downloaded": "Model '{model}' is not downloaded.",
    "notify.model.delete.stale_cleared_no_model": (
        "Model '{model}' was not on disk, no model selected. Pick a model on the Models page."
    ),
    "notify.model.delete.stale_cleared_switched": ("Model '{model}' was not on disk, switched to '{replacement}'."),
    "notify.model.delete.stale_nothing_to_delete": ("Model '{model}' was not on disk, nothing to delete."),
    "notify.model.delete.active_refused_recording": ("Stop the current dictation before deleting the active model."),
    "notify.model.delete.unload_failed": (
        "Could not unload '{model}' for deletion. Try again after stopping any dictation."
    ),
    # Distinct from the short ``state.dictation_pipeline.clipboard_unavailable``
    "notify.app.clipboard_unavailable_body": (
        "Transcription complete, but the clipboard was unavailable.\n"
        "Your text was saved to the crash-recovery file so it is not lost."
    ),
    "notify.app.clipboard_unavailable_recovery_path": "Recovery file: {path}",
    "notify.volume_controller.crash_restored": ("System volume was restored after a crash (to {percent}%)."),
    "notify.permissions.macos_title": "{app} needs permission",
    "notify.permissions.macos_body": (
        "Click to open System Settings \u2192 Accessibility. Add {app} (and its key-listener helper) to the list."
    ),
    "notify.permissions.macos_body_with_command": (
        "Click to open System Settings \u2192 Accessibility. Add {app} (and its key-listener helper) to the list. "
        "Then run: {command}"
    ),
    "notify.permissions.linux_title": "{app} needs keyboard permission",
    "notify.permissions.linux_body": (
        "Click to grant access. Your system will ask for your password "
        "to install the keyboard permission (udev rule + input group). "
        "After granting, log out and back in for the change to take effect."
    ),
    # CRASH-NOTIFY: the crash toast is calm, user-facing copy, NO
    "notify.startup_sequence.crash_title": "{app}",
    "notify.startup_sequence.crash_body": (
        "{app} didn't close properly last time. "
        "We've restarted it and recovered your app.\n\n"
        "If this happens often, open Settings \u2192 Privacy \u2192 "
        "Diagnostics for details and help."
    ),
    "notify.startup_sequence.onboarding_failed_critical": (
        "Onboarding setup kept failing. The app will start with default settings. Open Settings to configure manually."
    ),
    "notify.startup_sequence.onboarding_failed_transient": ("Onboarding setup failed; will retry on next start."),
    "notify.startup_sequence.corrections_error_title": "{app} | Corrections Error",
    "notify.startup_sequence.corrections_error_body": (
        "{error}\nCorrections will use built-in defaults. Fix the file and restart."
    ),
    "notify.startup_sequence.wayland_hotkeys_title": "{app} | Wayland Hotkeys",
    "notify.startup_sequence.wayland_hotkeys_body": (
        "Global hotkeys may not work on Wayland. "
        "Install 'wtype' or 'ydotool' for hotkey support, "
        "or use the tray menu's Start Dictation option."
    ),
    "notify.startup_sequence.accessibility_title": "{app} | Accessibility Permission",
    "notify.startup_sequence.accessibility_body": (
        "Global hotkeys require Accessibility permission. "
        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility "
        "and add {app} (or Terminal)."
    ),
    "notify.recording_controller.consent_required": (
        "Voice biometric consent is required to start recording.\n"
        "Enable it in Settings > Privacy > Voice Biometric Consent."
    ),
    # consent_check_failed: distinct from consent_required, this fires
    "notify.recording_controller.consent_check_failed": (
        "Could not verify voice biometric consent.\nRecording refused. Check Settings > Privacy."
    ),
    # mic_disconnected: recorder device-lost callback (slow path —
    "notify.recording_controller.mic_disconnected": (
        "Microphone disconnected. Recording stopped. Reconnect the microphone to resume."
    ),
    # mic_unplugged: fast-path active-mic-lost callback (OS device-list
    "notify.recording_controller.mic_unplugged": "Microphone was unplugged. Recording stopped.",
    # mic_permission_revoked: mid-recording OS-level permission
    "notify.recording_controller.mic_permission_revoked": (
        "Microphone permission was revoked. Recording stopped. "
        "Re-grant microphone access in your OS privacy settings to resume."
    ),
    # The start_failed notification no longer
    "notify.recording_controller.start_failed": (
        "Could not start recording.\nCheck logs/voice-typer.log for traceback."
    ),
    # start_failed_with_reason: the typed-failure branch of the start
    "notify.recording_controller.start_failed_with_reason": "Could not start recording.\n{reason}",
    "notify.recording_controller.stop_failed": "Could not stop recording.",
    "notify.recording_controller.silence_warning": (
        "No audio detected. Check your microphone is connected and working."
    ),
    "notify.recording_controller.silence_auto_stop": ("Recording stopped: no audio detected for an extended period."),
    "notify.recording_controller.max_duration_auto_stop": ("Recording stopped: maximum recording duration reached."),
    "notify.recording_controller.xrun_title": "{app} | Audio Issues",
    "notify.recording_controller.xrun_body": (
        "Detected {count} audio buffer underruns. Try closing other audio apps or reducing CPU load."
    ),
    "notify.recording_controller.still_running": (
        "Transcription is still running.\nLong recordings or CPU fallback can take extra time."
    ),
    # ``{hotkey}`` is the user's CONFIGURED hotkey, formatted via
    "notify.recording_controller.cancelled_timeout": (
        "Transcription took too long and was cancelled.\nPress {hotkey} to try again."
    ),
    "notify.model_manager.backend_init_failed": "Could not initialize the {backend} backend.{hint}",
    "notify.model_manager.load_failed_critical": (
        "Could not load the speech model.\nThe app will keep running. Press {hotkey} to retry loading."
    ),
    "notify.model_manager.load_failed": (
        "Could not load the speech model.\n{error}\n\nThe app will keep running. Press {hotkey} to retry loading."
    ),
    "notify.model_manager.change_deferred": "Model will change to {model} after current recording",
    "notify.model_manager.backend_change_deferred": "Backend will change to {backend} after current recording.",
    "notify.model_manager.model_not_downloaded": ("No speech model is selected. Open Models to choose one."),
    # no_model_selected: notification twin of the state message above —
    "notify.model_manager.no_model_selected": ("No model selected.\nGo to the models page to select a model."),
    # last_resort_unloaded: fired by get_active()'s last-resort branch
    "notify.model_manager.last_resort_unloaded": (
        "The model is not loaded.\nOpen the models page to download a model."
    ),
    "notify.update_available_body": "{app} {version} is available (you have {current})",
}


def register_locale(locale: str, labels: dict[str, str]) -> None:
    """Replace the registry for a locale (called by set_tray_locale IPC)."""
    with _LOCK:
        _REGISTRY[locale] = dict(labels)


def merge_labels(locale: str, labels: dict[str, str]) -> None:
    """by the ``set_tray_locale`` IPC (HU-17) to push the renderer-side"""
    with _LOCK:
        merged = _REGISTRY.setdefault(locale, {})
        for key, value in labels.items():
            merged.setdefault(key, value)


def set_locale(locale: str) -> None:
    """Switch the active locale. Falls back to English if not registered."""
    global _CURRENT_LOCALE
    with _LOCK:
        _CURRENT_LOCALE = locale if locale in _REGISTRY else DEFAULT_LOCALE


def t(key: str, **fmt: Any) -> str:
    """Translate a key, with optional ``{name}`` format interpolation."""
    with _LOCK:
        locale = _CURRENT_LOCALE
        registry = _REGISTRY
        text = registry.get(locale, {}).get(key)
        if text is None:
            text = registry.get(DEFAULT_LOCALE, {}).get(key, key)
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            # also catch ValueError, str.format() raises it for
            return text
    return text


# Convenience alias matching the tray.py ``_()`` convention.
_ = t


# register the English fallback at import time so the first
register_locale(DEFAULT_LOCALE, _INITIAL_LABELS)

# Non-English "System Default" device labels, mirroring the renderer's
# ``microphone.systemDefault`` strings so tray notifications stay fully
# localized before the first ``set_tray_locale`` push arrives.
_SYSTEM_DEFAULT_DEVICE_LABELS: dict[str, str] = {
    "ar": "افتراضي للنظام",
    "de": "Systemstandard",
    "es": "Predeterminado del sistema",
    "fr": "Valeur système par défaut",
    "hi": "सिस्टम डिफ़ॉल्ट",
    "ru": "Системный по умолчанию",
    "zh": "系统默认",
}

with _LOCK:
    for _locale, _label in _SYSTEM_DEFAULT_DEVICE_LABELS.items():
        _REGISTRY.setdefault(_locale, {}).setdefault("notify.settings_controller.system_default_device", _label)
