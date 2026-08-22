"""Server-side i18n for tray notifications and tooltip state messages.

centralized registry so server-side notifications
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

# single source of truth for the server-side default locale.
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
# server-side notification strings (kept verbatim from the original
# hard-coded literals so source-level regression tests that grep for the
# English text continue to find the substring — see test_notifications.py).
# Long lines use implicit string concatenation ("foo" "bar") which Python
# joins at compile time; ruff's line-length rule still flags each fragment
# individually so we keep every source line ≤ 120 chars.
_INITIAL_LABELS: dict[str, str] = {
    # AppState value labels () ────────────────────────────────
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
    "state.recording_controller.model_failed_retry": "Model failed to load -- press your hotkey to retry",
    "state.recording_controller.recording": "Recording...",
    "state.recording_controller.recording_failed": "Recording failed",
    "state.recording_controller.recording_failed_permission": (
        "Recording failed -- microphone permission denied. Allow mic access in system settings."
    ),
    "state.recording_controller.recording_failed_no_device": (
        "Recording failed -- no microphone found. Connect a microphone and try again."
    ),
    "state.recording_controller.stop_failed": "Stop failed",
    "state.recording_controller.too_short": "Too short -- ignored",
    "state.recording_controller.transcribing": "Transcribing...",
    "state.recording_controller.cancelling": "Cancelling...",
    "state.recording_controller.cancelled": "Cancelled",
    "state.recording_controller.recovered": "Recovered -- transcription timed out",
    "state.recording_controller.still_transcribing": "Still transcribing...",
    # ── model_manager set_state messages ────────────────────────────────
    "state.model_manager.loading": "Loading model -- press your hotkey to queue...",
    "state.model_manager.ready_whisper": "Ready -- {device_info}",
    "state.model_manager.ready_other": "Ready -- {name} ASR",
    "state.model_manager.load_failed_retry": "Model load failed -- press your hotkey to retry",
    "state.model_manager.backend_failed": "{backend} model failed to load",
    "state.model_manager.model_failed": "Model failed: {error}",
    "state.model_manager.model_not_downloaded": ("No speech model is selected. Open Models to choose one."),
    # no_model_selected: genuine "no model selected" state
    # (``model_size == NO_MODEL_SIZE``) — distinct from
    # model_not_downloaded (a concrete model is missing from disk). The
    # text MUST match the renderer's ``home.noModelSelectedHint``
    # translation so the tray tooltip and the Home status pill agree;
    # the renderer pushes its localized value into this key at runtime
    # via ``set_tray_locale`` (``trayLabelsForLocale`` maps
    # ``state.model_manager.no_model_selected`` →
    # ``home.noModelSelectedHint``).
    "state.model_manager.no_model_selected": ("No model selected. Go to the models page to select a model."),
    "state.model_manager.model_integrity_failed": (
        "{backend} model failed integrity verification. Delete and re-download it from the Models page."
    ),
    # ── dictation pipeline set_state messages ──────────────────────────
    # Momentary tooltip states published by paste_step / transcribe_step.
    # The renderer pushes localized values for these keys via
    # ``set_tray_locale`` (``trayState.pipeline.*``) so the tooltip
    # follows the renderer locale like every other state message.
    "state.dictation_pipeline.clipboard_unavailable": "Done -- clipboard unavailable",
    "state.dictation_pipeline.no_speech_detected": "No speech detected",
    "state.dictation_pipeline.no_speech_check_mic": "No speech -- check microphone",
    "state.dictation_pipeline.transcription_empty": "Transcription returned empty",
    # paste_step "Done -- N chars (mode)" statuses — the character count
    # is dynamic, so the templates use the ``{count}`` placeholder and
    # the renderer pushes localized versions via ``set_tray_locale``
    # (``trayState.pipeline.donePasted`` etc.; ``trayLabelsForLocale``
    # maps the keys 1:1). The English text here is byte-identical to
    # the former f-strings so tests that grep for the literal still
    # match, and ``i18n.t(key, count=N)`` formats it at call time.
    "state.dictation_pipeline.done_pasted": "Done -- {count} chars (pasted)",
    "state.dictation_pipeline.done_in_db": ("Done -- {count} chars (in DB, use repaste hotkey)"),
    "state.dictation_pipeline.done_in_clipboard": ("Done -- {count} chars (in clipboard)"),
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
    "notify.permissions.macos_body_with_command": (
        "Click to open System Settings \u2192 Accessibility. Add {app} (and its key-listener helper) to the list. "
        "Then run: {command}"
    ),
    "notify.permissions.linux_title": "Voice Typer needs keyboard permission",
    "notify.permissions.linux_body": (
        "Click to grant access. Your system will ask for your password "
        "to install the keyboard permission (udev rule + input group). "
        "After granting, log out and back in for the change to take effect."
    ),
    # ── startup_sequence.py notifications ───────────────────────────────
    # CRASH-NOTIFY: the crash toast is calm, user-facing copy — NO
    # technical details (crash summary, stack traces, python commands)
    # in the notification. The crash summary stays in the log /
    # diagnostics surface only.
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
    "notify.startup_sequence.corrections_error_title": "{app} — Corrections Error",
    "notify.startup_sequence.corrections_error_body": (
        "{error}\nCorrections will use built-in defaults. Fix the file and restart."
    ),
    "notify.startup_sequence.crash_recovery_body": (
        "Recovered {count} transcriptions from last session. Open History to view."
    ),
    "notify.startup_sequence.wayland_hotkeys_title": "{app} — Wayland Hotkeys",
    "notify.startup_sequence.wayland_hotkeys_body": (
        "Global hotkeys may not work on Wayland. "
        "Install 'wtype' or 'ydotool' for hotkey support, "
        "or use the tray menu's Start Dictation option."
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
    # consent_check_failed: distinct from consent_required — this fires
    # when the consent CHECK itself raised (corrupted config read, etc.)
    # rather than the consent being False. The user is told the check
    # failed and recording was refused, so they know to investigate the
    # config / re-grant consent rather than just toggling the setting.
    "notify.recording_controller.consent_check_failed": (
        "Could not verify voice biometric consent.\nRecording refused — check Settings > Privacy."
    ),
    # mic_disconnected: recorder device-lost callback (slow path —
    # zero-fill-chunk retry exhausted). Distinct from mic_unplugged
    # (fast path — OS device-list change) so the user sees an accurate
    # "disconnected after retries" message rather than "unplugged".
    "notify.recording_controller.mic_disconnected": (
        "Microphone disconnected. Recording stopped. Reconnect the microphone to resume."
    ),
    # mic_unplugged: fast-path active-mic-lost callback (OS device-list
    # change while recording). Distinct from mic_disconnected (slow
    # path) so the user sees an accurate "unplugged" message rather
    # than the misleading "disconnected after retries".
    "notify.recording_controller.mic_unplugged": "Microphone was unplugged. Recording stopped.",
    # mic_permission_revoked: mid-recording OS-level permission
    # revocation (e.g. user toggled mic access off in System Settings).
    # Distinct from silence_auto_stop so the user sees an accurate
    # "permission revoked" message rather than the misleading
    # "silence detected" auto-stop after 30-60s of zero-filled buffers.
    "notify.recording_controller.mic_permission_revoked": (
        "Microphone permission was revoked. Recording stopped. "
        "Re-grant microphone access in your OS privacy settings to resume."
    ),
    # (session NH): the start_failed notification no longer
    # interpolates {error} into the user-facing message — exception text
    # can leak absolute paths, device names, and hostnames. The full
    # exception is still logged via log.exception() above; the tray
    # notification now shows only a generic message + a pointer to the
    # log file.
    "notify.recording_controller.start_failed": (
        "Could not start recording.\nCheck logs/voice-typer.log for traceback."
    ),
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
    # ``{hotkey}`` is the user's CONFIGURED hotkey, formatted via
    # ``format_hotkey_label`` at the call site (e.g. "Caps Lock" or
    # "Ctrl+Shift+F2") — never a hardcoded key, so a remapped hotkey is
    # reflected in the notification.
    "notify.recording_controller.cancelled_timeout": (
        "Transcription took too long and was cancelled.\nPress {hotkey} to try again."
    ),
    # ── model_manager.py notifications ──────────────────────────────────
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
    # same wording as the renderer's Home hint so the tray notification
    # and the Home status pill stay in sync (user request: tooltip /
    # notification / status pill must agree on the no-model-selected
    # error state).
    "notify.model_manager.no_model_selected": ("No model selected.\nGo to the models page to select a model."),
    # last_resort_unloaded: fired by get_active()'s last-resort branch
    # when NO ready backend exists and the configured backend is returned
    # unloaded (transcription would silently return empty). Always points
    # the user at the Models page with the download instruction — the app
    # never auto-downloads models, so the user must go there to install /
    # repair the model.
    "notify.model_manager.last_resort_unloaded": (
        "The model is not loaded.\nOpen the models page to download a model."
    ),
    # tray.py update-check notification () ─────────────────────
    "notify.update_available_body": "{app} {version} is available (you have {current})",
}


def register_locale(locale: str, labels: dict[str, str]) -> None:
    """Replace the registry for a locale (called by set_tray_locale IPC)."""
    with _LOCK:
        _REGISTRY[locale] = dict(labels)


def merge_labels(locale: str, labels: dict[str, str]) -> None:
    """Merge labels INTO an existing registry entry without wiping it.

    Unlike :func:`register_locale` (which REPLACES the entry and would
    drop the English fallbacks registered at import time / by app.py),
    this extends a locale's label dict per-key (existing keys win). Used
    by the ``set_tray_locale`` IPC (HU-17) to push the renderer-side
    translations for the server-notification keys
    (``error.config_load_failed.*`` / ``state.app.starting``) into the
    global registry so ``i18n.t`` resolves them for non-English locales.
    """
    with _LOCK:
        merged = _REGISTRY.setdefault(locale, {})
        for key, value in labels.items():
            merged.setdefault(key, value)


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
            # also catch ValueError — str.format() raises it for
            # bad format specs (e.g. "{name:bad}" in a translation).
            # The docstring promises "a bad translation never crashes a
            # notification path"; broaden the catch so it actually holds.
            return text
    return text


# Convenience alias matching the tray.py ``_()`` convention.
_ = t


# register the English fallback at import time so the first
# notification emits real text instead of the raw key.
register_locale(DEFAULT_LOCALE, _INITIAL_LABELS)
