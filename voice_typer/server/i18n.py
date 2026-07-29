"""Server-side i18n for tray notifications and tooltip state messages.

NF-R16-1 / NF-R16-2: centralized registry so server-side notifications
and ``set_state`` messages can be localized. The renderer pushes the
full label dict via the ``set_tray_locale`` IPC on locale change. Until
then, we use the English fallback defined here in ``_INITIAL_LABELS``.

Naming convention (kept stable so the renderer can override keys
verbatim):

* ``notify.<module>.<context>`` — bodies/titles passed to
  ``tray.notify`` / ``tray.notify_safety``. May contain ``{name}``
  format placeholders.
* ``state.<value>`` — AppState value labels surfaced in the tray
  tooltip (``state.recording`` etc.). Match the lowercase enum values
  in :class:`voice_typer.server.tray_types.AppState`.
* ``state.<module>.<context>`` — per-call ``set_state`` messages
  (e.g. ``state.recording_controller.loading_queued``).

The ``tray.py`` module keeps its own ``_TRAY_LABELS_EN`` / ``_TRAY_LABELS_ES``
dicts for the tray *menu item* labels (``open_app``, ``toggle_dictation``,
``models`` …). Those keys are looked up via the ``_()`` helper in
``tray.py``; the dict here covers everything *else* (notification
bodies, state messages) so non-tray modules don't need to import tray
internals.
"""

from __future__ import annotations

import threading
from typing import Any

# DR-38: single source of truth for the server-side default locale.
# Previously the literal ``"en"`` was duplicated as the fallback locale
# in i18n.py (4 sites), tray_i18n.py (3 sites), and as the default
# ``language`` argument to every ASR engine constructor
# (transcription.py, parakeet_engine.py, cloud_engines.py).
# Centralising the literal here means the parity test
# ``tests/test_default_locale_sync.py`` can assert the TS renderer
# side uses the same value by extracting it from
# ``client/src/renderer/src/i18n/i18n.ts`` via regex.
DEFAULT_LOCALE: str = "en"

_LOCK = threading.Lock()
_CURRENT_LOCALE: str = DEFAULT_LOCALE
_REGISTRY: dict[str, dict[str, str]] = {DEFAULT_LOCALE: {}}


# ─── English fallback ───────────────────────────────────────────────────────
# NF-R16-1: server-side notification strings (kept verbatim from the original
# hard-coded literals so source-level regression tests that grep for the
# English text continue to find the substring — see test_notifications.py).
# Long lines use implicit string concatenation ("foo" "bar") which Python
# joins at compile time; ruff's line-length rule still flags each fragment
# individually so we keep every source line ≤ 120 chars.
_INITIAL_LABELS: dict[str, str] = {
    # ── AppState value labels (NF-R16-2) ────────────────────────────────
    # Lowercase to match AppState.<X>.value and preserve existing tooltip
    # behavior (``title += f" — {state.value}"`` →
    # ``i18n.t(f"state.{state.value}")``).
    "state.idle": "idle",
    "state.recording": "recording",
    "state.transcribing": "transcribing",
    "state.loading": "loading",
    "state.error": "error",
    "state.cancelling": "cancelling",
    # ── recording_controller set_state messages ─────────────────────────
    "state.recording_controller.loading_queued": ("Loading model -- your dictation will start automatically\u2026"),
    "state.recording_controller.starting_up": "Starting up -- please wait...",
    "state.recording_controller.consent_required": "Voice biometric consent required",
    "state.recording_controller.model_failed_retry": "Model failed to load -- press F2 to retry",
    "state.recording_controller.recording": "Recording...",
    "state.recording_controller.recording_failed": "Recording failed",
    "state.recording_controller.stop_failed": "Stop failed",
    "state.recording_controller.too_short": "Too short -- ignored",
    "state.recording_controller.transcribing": "Transcribing...",
    "state.recording_controller.cancelling": "Cancelling...",
    "state.recording_controller.cancelled": "Cancelled",
    "state.recording_controller.recovered": "Recovered -- transcription timed out",
    "state.recording_controller.still_transcribing": "Still transcribing...",
    # ── model_manager set_state messages ────────────────────────────────
    "state.model_manager.loading": "Loading model -- press F2 to queue...",
    "state.model_manager.ready_whisper": "Ready -- {device_info}",
    "state.model_manager.ready_other": "Ready -- {name} ASR",
    "state.model_manager.load_failed_retry": "Model load failed -- press F2 to retry",
    "state.model_manager.backend_failed": "{backend} model failed to load",
    "state.model_manager.model_failed": "Model failed: {error}",
    # ── app.py notifications ────────────────────────────────────────────
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
    # ── settings_controller.py notifications ────────────────────────────
    "notify.settings_controller.autostart_failed": "Could not change autostart setting.\n{error}",
    "notify.settings_controller.mic_save_failed": (
        "Failed to save microphone selection. Check disk space or permissions."
    ),
    "notify.settings_controller.mic_next_recording": "Microphone next recording: {label}",
    "notify.settings_controller.mic_changed": "Microphone: {label}",
    # ── startup_tasks.py notifications ──────────────────────────────────
    "notify.startup_tasks.accessibility_granted": ("Accessibility permission granted. Hotkeys are now active."),
    "notify.startup_tasks.accessibility_revoked_title": "{app} — Accessibility Revoked",
    "notify.startup_tasks.accessibility_revoked_body": (
        "Global hotkeys have been disabled. "
        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility to re-grant."
    ),
    # ── hotkey_dispatcher.py notifications ──────────────────────────────
    "notify.hotkey_dispatcher.register_failed": (
        "Hotkey {hotkey} could not be registered. "
        "It may be in use by another app. "
        "Use the tray menu to toggle dictation, or pick a different hotkey in Settings."
    ),
    "notify.hotkey_dispatcher.save_failed": ("Failed to save hotkey to disk. Check disk space or permissions."),
    # ── volume_controller.py notifications ──────────────────────────────
    "notify.volume_controller.crash_restored": ("System volume was restored after a crash (to {percent}%)."),
    # ── permissions.py notifications ────────────────────────────────────
    "notify.permissions.macos_title": "{app} needs permission",
    "notify.permissions.macos_body": (
        "Click to open System Settings \u2192 Accessibility. Add Voice Typer (and its key-listener helper) to the list."
    ),
    "notify.permissions.linux_title": "Voice Typer needs keyboard permission",
    "notify.permissions.linux_body": (
        "Click to grant access. Your system will ask for your password "
        "to install the keyboard permission (udev rule + input group). "
        "After granting, log out and back in for the change to take effect."
    ),
    # ── startup_sequence.py notifications ───────────────────────────────
    "notify.startup_sequence.crash_title": "{app} — Previous Session Crashed",
    "notify.startup_sequence.crash_body": (
        "The app was restarted automatically after an unexpected shutdown."
        "\n\n{summary}\n\n"
        "To prevent this: free up RAM/disk space, or try a smaller model "
        "in Settings. See voice-typer.log for full diagnostics."
    ),
    "notify.startup_sequence.onboarding_failed_critical": (
        "Onboarding setup kept failing. The app will start with default settings. Open Settings to configure manually."
    ),
    "notify.startup_sequence.onboarding_failed_transient": ("Onboarding setup failed; will retry on next start."),
    "notify.startup_sequence.corrections_error_title": "{app} — Corrections Error",
    "notify.startup_sequence.corrections_error_body": (
        "{error}\nCorrections will use built-in defaults. Fix the file and restart."
    ),
    "notify.startup_sequence.crash_recovery_body": (
        "Recovered {count} transcription(s) from last session. Open History to view."
    ),
    "notify.startup_sequence.wayland_hotkeys_title": "{app} — Wayland Hotkeys",
    "notify.startup_sequence.wayland_hotkeys_body": (
        "Global hotkeys may not work on Wayland. "
        "Install 'wtype' or 'ydotool' for hotkey support, "
        "or use the tray menu's Toggle Dictation option."
    ),
    "notify.startup_sequence.accessibility_title": "{app} — Accessibility Permission",
    "notify.startup_sequence.accessibility_body": (
        "Global hotkeys require Accessibility permission. "
        "Open System Settings \u2192 Privacy & Security \u2192 Accessibility "
        "and add {app} (or Terminal)."
    ),
    # ── recording_controller.py notifications ───────────────────────────
    "notify.recording_controller.consent_required": (
        "Voice biometric consent is required to start recording.\n"
        "Enable it in Settings > Privacy > Voice Biometric Consent."
    ),
    # NH-5 / DE-51 (session NH): the start_failed notification no longer
    # interpolates {error} into the user-facing message — exception text
    # can leak absolute paths, device names, and hostnames. The full
    # exception is still logged via log.exception() above; the tray
    # notification now shows only a generic message + a pointer to the
    # log file.
    "notify.recording_controller.start_failed": ("Could not start recording.\nCheck voice-typer.log for traceback."),
    "notify.recording_controller.stop_failed": "Could not stop recording.",
    "notify.recording_controller.silence_warning": (
        "No audio detected. Check your microphone is connected and working."
    ),
    "notify.recording_controller.silence_auto_stop": ("Recording stopped: no audio detected for an extended period."),
    "notify.recording_controller.max_duration_auto_stop": ("Recording stopped: maximum recording duration reached."),
    "notify.recording_controller.xrun_title": "{app} — Audio Issues",
    "notify.recording_controller.xrun_body": (
        "Detected {count} audio buffer underruns. Try closing other audio apps or reducing CPU load."
    ),
    "notify.recording_controller.still_running": (
        "Transcription is still running.\nLong recordings or CPU fallback can take extra time."
    ),
    "notify.recording_controller.cancelled_timeout": (
        "Transcription took too long and was cancelled.\nPress F2 to try again."
    ),
    # ── model_manager.py notifications ──────────────────────────────────
    "notify.model_manager.backend_init_failed": "Could not initialize the {backend} backend.{hint}",
    "notify.model_manager.load_failed_critical": (
        "Could not load the speech model.\nThe app will keep running. Press F2 to retry loading."
    ),
    "notify.model_manager.load_failed": (
        "Could not load the speech model.\n{error}\n\nThe app will keep running. Press F2 to retry loading."
    ),
    "notify.model_manager.change_deferred": "Model will change to {model} after current recording",
    # ── tray.py update-check notification (NF-R16-4) ─────────────────────
    "notify.update_available_body": "{app} {version} is available (you have {current})",
}


def register_locale(locale: str, labels: dict[str, str]) -> None:
    """Replace the registry for a locale (called by set_tray_locale IPC)."""
    with _LOCK:
        _REGISTRY[locale] = dict(labels)


def set_locale(locale: str) -> None:
    """Switch the active locale. Falls back to English if not registered."""
    global _CURRENT_LOCALE
    with _LOCK:
        _CURRENT_LOCALE = locale if locale in _REGISTRY else DEFAULT_LOCALE


def get_locale() -> str:
    """Return the currently active locale code (e.g. ``"en"``)."""
    with _LOCK:
        return _CURRENT_LOCALE


def t(key: str, **fmt: Any) -> str:
    """Translate a key, with optional ``{name}`` format interpolation.

    Falls back to the English registry, then to the key itself (so a
    missing key is loudly visible in the UI rather than silently
    empty). Format interpolation failures (e.g. a missing placeholder)
    return the unformatted text so a bad translation never crashes a
    notification path.
    """
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
            # AC-22: also catch ValueError — str.format() raises it for
            # bad format specs (e.g. "{name:bad}" in a translation).
            # The docstring promises "a bad translation never crashes a
            # notification path"; broaden the catch so it actually holds.
            return text
    return text


# Convenience alias matching the tray.py ``_()`` convention.
_ = t


# NF-R16-1: register the English fallback at import time so the first
# notification emits real text instead of the raw key.
register_locale(DEFAULT_LOCALE, _INITIAL_LABELS)
